"""Stage 2: correct instrument clocks and trim each record to the deployment.

The correction, seconds **added** to instrument time (positive: the clock was slow),
ramps linearly from ``clock_offset`` at deployment to ``clock_drift_seconds`` at
recovery (both default 0). ``computer_clock_at_recovery`` /
``instrument_clock_at_recovery`` give the drift as computer − instrument and take
priority over ``clock_drift_seconds``. A corrected file keeps ``time_orig``. The record
is then trimmed to ``deployment_time`` … ``recovery_time``.
"""

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray import paths
from oceanarray.config import parameters as params
from oceanarray.exceptions import MooringError
from oceanarray.instruments import instrument_entries
from oceanarray.logger import format_time, with_stage_handlers
from oceanarray.pipeline import _stage
from oceanarray.pipeline.dataset import drop_all_zero_vars
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.pipeline.units import to_canonical_units
from oceanarray.pipeline.write_mooring import write_mooring
from oceanarray.writers.netcdf import as_stored

log = logging.getLogger(__name__)

#: A record that starts late or ends early by more than this fraction of the
#: planned deployment window is a short record (WARNING): usually an instrument
#: that failed before recovery, not a processing error.
_SHORT_RECORD_FRACTION = 0.05


def _parse_clock_str(s: str) -> pd.Timestamp | None:
    """Parse a clock timestamp in multiple formats; return None if unparseable."""
    # Accepts HH:MM:SS, YYYYMMDDTHH:MM:SS and ISO; "unknown" or anything unparseable
    # gives None, so no correction is applied.
    s = s.strip()
    if re.match(r"^\d{2}:\d{2}:\d{2}", s) and "T" not in s:
        # Time-only: anchor to an arbitrary date (only the difference is used)
        s = f"2000-01-01T{s}"
    elif re.match(r"^\d{8}T", s):
        # Compact YYYYMMDDTHH:MM:SS — s[8] is 'T', s[9:] is the time part
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}T{s[9:]}"
    try:
        return pd.Timestamp(s)
    except Exception:  # noqa: BLE001  — returns None for any unparseable timestamp string
        return None


def detect_deployment_window(
    ds: xr.Dataset,
) -> tuple[np.datetime64 | None, np.datetime64 | None, str]:
    """Estimate the in-water window from a stage-1 pressure record, in the raw instrument clock.

    Parameters
    ----------
    ds : xarray.Dataset
        Stage-1 dataset with ``time`` (instrument clock) and ``pressure`` (dbar).

    Returns
    -------
    tuple of (numpy.datetime64 or None, numpy.datetime64 or None, str)
        ``(start, end, "pressure_pmin10dbar")``, or ``(None, None, "no_pressure")``
        without pressure, with under 10 records, or with no valid window. Add the YAML
        ``clock_offset`` for UTC.

    Notes
    -----
    Threshold = 1st percentile of pressure in the middle 50 % of the records, minus
    10 dbar: the middle excludes bench periods at either end, and a low percentile is not
    biased by knockdown (which inflates ``pmax``). The start is the sample after the last
    one shallower than the threshold in the first 25 %; the end is the sample before the
    first one shallower in the last 25 % (else the last sample).
    """
    time = ds["time"].values
    n = len(time)

    if "pressure" not in ds.data_vars or n < 10:
        return None, None, "no_pressure"

    p = ds["pressure"].values.astype(float)

    # ── Middle-50 % reference window (skip first/last 25 % by index) ─────────
    i_lo = int(0.25 * n)
    i_hi = int(0.75 * n)
    mid_mask = np.zeros(n, dtype=bool)
    mid_mask[i_lo:i_hi] = True

    p_mid_finite = p[mid_mask & np.isfinite(p)]
    if p_mid_finite.size == 0:
        return None, None, "no_pressure"

    # 1st percentile: robust to brief sensor-zero artefacts in the deployed
    # record that would drag the absolute minimum to ~0 dbar.
    pmin_deployed = float(np.nanpercentile(p_mid_finite, 1))
    threshold = pmin_deployed - 10.0

    # ── Search windows: first/last 25 % of record ────────────────────────────
    # Proportional windows cover multi-day bench periods that a fixed 12 h
    # window would miss.
    start_window = np.zeros(n, dtype=bool)
    start_window[:i_lo] = True  # first 25 %
    end_window = np.zeros(n, dtype=bool)
    end_window[i_hi:] = True  # last 25 %

    below = np.isfinite(p) & (p <= threshold)

    # Start: last below-threshold sample in opening window → next sample
    idx_start = np.where(below & start_window)[0]
    start_idx = min(int(idx_start[-1]) + 1, n - 1) if idx_start.size > 0 else 0

    # End: first below-threshold sample in closing window → previous sample
    idx_end = np.where(below & end_window)[0]
    if idx_end.size > 0:
        end_idx = max(int(idx_end[0]) - 1, 0)
    else:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "detect_deployment_window: no recovery pressure transition detected "
            "in last 25 %% of record — suggested end = last stage1 sample (%s). "
            "If the YAML recovery_time is set earlier than this, the orange vline "
            "will fall outside the 6 h end-window and be invisible.",
            time[n - 1],
        )
        end_idx = n - 1

    if start_idx >= end_idx:
        return None, None, "no_pressure"

    return time[start_idx], time[end_idx], "pressure_pmin10dbar"


def _nonnull_time_bounds(
    dataset: xr.Dataset,
) -> tuple[np.datetime64, np.datetime64]:
    """Return the first and last non-null times of the highest-priority variable."""
    # Times are in whatever clock the dataset carries: for stage-1 input, the raw
    # (uncorrected) instrument clock.
    time = dataset["time"].values
    n = len(time)

    # Choose the representative variable using the priority hierarchy
    var: str | None = None
    if "pressure" in dataset.data_vars and dataset["pressure"].dims == ("time",):
        var = "pressure"
    elif "temperature" in dataset.data_vars and dataset["temperature"].dims == (
        "time",
    ):
        var = "temperature"

    if var is None:
        for v in dataset.data_vars:
            if dataset[v].dims == ("time",) and any(
                k in v for k in ("velocity", "turbidity")
            ):
                var = v
                break

    if var is None:
        for v in dataset.data_vars:
            if dataset[v].dims == ("time",):
                var = v
                break

    if var is None:
        return time[0], time[-1]

    vals = np.asarray(dataset[var].values, dtype=float)
    finite_mask = np.isfinite(vals)
    if not finite_mask.any():
        return time[0], time[-1]

    first_idx = int(np.argmax(finite_mask))
    last_idx = int(n - 1 - np.argmax(finite_mask[::-1]))
    return time[first_idx], time[last_idx]


def _parse_yaml_time(data: dict[str, Any], key: str) -> np.datetime64:
    """Return ``datetime64[ns]`` for *key*, ``NaT`` if absent, raising if unparseable."""
    val = data.get(key)
    if val is None or (isinstance(val, str) and not val.strip()):
        return np.datetime64("NaT", "ns")
    try:
        return pd.to_datetime(val).to_datetime64()
    except Exception as e:  # any parse failure — re-raised, not swallowed
        raise MooringError(f"{key}={val!r} is not a valid timestamp: {e}") from e


def _preserve_time_orig(dataset: xr.Dataset) -> xr.Dataset:
    """Save original (uncorrected) time as time_orig before any clock corrections."""
    if "time_orig" not in dataset.coords:
        dataset = dataset.assign_coords(time_orig=dataset["time"])
        dataset["time_orig"].attrs = {
            "long_name": "original instrument time before clock correction",
            "standard_name": "time",
        }
    return dataset


def _apply_clock_offset(dataset: xr.Dataset, clock_offset: float) -> xr.Dataset:
    """Apply a constant clock offset (seconds added to instrument time)."""
    if clock_offset == 0:
        return dataset

    log.debug("Applying clock offset: %+.1f s", clock_offset)
    result = dataset.copy()
    result["clock_offset"] = clock_offset
    result["clock_offset"].attrs = {
        "units": "s",
        "long_name": "constant clock offset added to time",
    }
    result = result.assign_coords(
        time=result["time"].values + np.timedelta64(int(clock_offset * 1e9), "ns")
    )
    sign = "+" if clock_offset >= 0 else ""
    _stage.append_history(result, f"clock_offset={sign}{clock_offset:.1f}s applied")
    return result


def _resolve_clock_drift(instrument_config: dict[str, Any]) -> tuple:
    """Return (drift_seconds, history_note) from the YAML config."""
    # Drift is ADDED to instrument time at recovery to get corrected time (positive =
    # instrument was slow). The two recovery timestamps take priority over
    # clock_drift_seconds.
    comp_str = instrument_config.get("computer_clock_at_recovery")
    inst_str = instrument_config.get("instrument_clock_at_recovery")
    if comp_str and inst_str:
        comp_t = _parse_clock_str(str(comp_str))
        inst_t = _parse_clock_str(str(inst_str))
        if comp_t is None or inst_t is None:
            drift_s = float(instrument_config.get("clock_drift_seconds", 0))
            note = f"clock_drift={drift_s:+.1f}s over deployment"
            return drift_s, note
        drift_s = (comp_t - inst_t).total_seconds()  # +ve when instrument was slow
        note = (
            f"clock_drift={drift_s:+.1f}s over deployment "
            f"(computer={comp_str}, instrument={inst_str})"
        )
        return drift_s, note

    drift_s = float(instrument_config.get("clock_drift_seconds", 0))
    note = f"clock_drift={drift_s:+.1f}s over deployment"
    return drift_s, note


def _apply_clock_drift(
    dataset: xr.Dataset,
    clock_drift_seconds: float,
    deploy_time: np.datetime64,
    recover_time: np.datetime64,
    history_note: str = "",
) -> xr.Dataset:
    """Apply a linear clock-drift ramp (0 at deployment → *clock_drift_seconds* at recovery)."""
    if clock_drift_seconds == 0:
        return dataset

    log.debug("Applying clock drift: %+.1f s over deployment", clock_drift_seconds)

    total_duration_s = (recover_time - deploy_time) / np.timedelta64(1, "s")
    if total_duration_s <= 0:
        log.warning("deploy_time >= recover_time; skipping clock drift ramp")
        return dataset
    time_since_deploy_s = np.clip(
        (dataset["time"].values - deploy_time) / np.timedelta64(1, "s"),
        0.0,
        total_duration_s,
    )
    correction_ns = (
        clock_drift_seconds * time_since_deploy_s / total_duration_s * 1e9
    ).astype("int64")

    result = dataset.copy()
    result["clock_drift_seconds"] = clock_drift_seconds
    result["clock_drift_seconds"].attrs = {
        "units": "s",
        "long_name": "total linear clock drift applied",
    }
    corrected_times = dataset["time"].values + correction_ns.astype("timedelta64[ns]")
    result = result.assign_coords(time=corrected_times)
    _stage.append_history(
        result, history_note or f"clock_drift={clock_drift_seconds:+.1f}s applied"
    )
    return result


def _warn_missing_deployment_window(
    serial: str,
    instrument_type: str,
    mooring_name: str,
    deploy_time: np.datetime64,
    recover_time: np.datetime64,
    sug_start: np.datetime64,
    sug_end: np.datetime64,
    clock_offset: float = 0,
) -> None:
    """Warn (console + log) when YAML deployment times are absent, suggesting a window."""
    missing_keys = []
    if not np.isfinite(deploy_time):
        missing_keys.append("deployment_time")
    if not np.isfinite(recover_time):
        missing_keys.append("recovery_time")
    if not missing_keys:
        return

    border = "─" * 60
    lines = [
        "",
        f"  ┌─ DEPLOYMENT WINDOW WARNING {'─' * 32}",
        f"  │  Mooring : {mooring_name}",
        f"  │  Instrument: {instrument_type} {serial}",
        f"  │  Missing YAML key(s): {', '.join(missing_keys)}",
        "  │  No deployment-window trimming applied for this instrument.",
    ]
    if sug_start is not None:
        offset_td = (
            np.timedelta64(int(clock_offset * 1e9), "ns")
            if clock_offset
            else np.timedelta64(0, "ns")
        )
        offset_note = (
            f" (clock_offset={clock_offset:+.1f}s applied)" if clock_offset else ""
        )
        lines += [
            "  │",
            f"  │  Auto-detected window from pressure{offset_note} (copy-paste into YAML):",
            f"  │    deployment_time: {pd.Timestamp(sug_start + offset_td).isoformat()}",
            f"  │    recovery_time:   {pd.Timestamp(sug_end + offset_td).isoformat()}",
        ]
    else:
        lines += [
            "  │",
            "  │  No pressure variable — cannot auto-detect deployment window.",
            "  │  Set deployment_time and recovery_time manually in the YAML.",
        ]
    lines += [
        "  │",
        f"  │  Update {mooring_name}.mooring.yaml then re-run stage 2.",
        f"  └{border}",
        "",
    ]
    for line in lines:
        log.info(line)


def _trim_to_deployment_window(
    dataset: xr.Dataset,
    deploy_time: np.datetime64,
    recover_time: np.datetime64,
) -> xr.Dataset:
    """Trim to the deployment window, pre-filtering stray (clock-wrap) records first."""
    original_size = len(dataset.time)

    # The pre-filter is a boolean mask, safe on a non-monotonic index; the slices
    # below need a monotonic time index.
    _one_month = np.timedelta64(31, "D")
    t_vals = dataset.time.values
    mask = np.ones(len(t_vals), dtype=bool)
    if np.isfinite(deploy_time):
        early_cutoff = deploy_time - _one_month
        n_early = int(np.sum(t_vals < early_cutoff))
        if n_early:
            log.warning(
                "dropping %d record(s) with timestamps more than 31 days "
                "before deployment (%s): %s",
                n_early,
                early_cutoff,
                t_vals[t_vals < early_cutoff],
            )
            mask &= t_vals >= early_cutoff
    if np.isfinite(recover_time):
        late_cutoff = recover_time + _one_month
        n_late = int(np.sum(t_vals > late_cutoff))
        if n_late:
            log.warning(
                "dropping %d record(s) with timestamps more than 31 days "
                "after recovery (%s): %s",
                n_late,
                late_cutoff,
                t_vals[t_vals > late_cutoff],
            )
            mask &= t_vals <= late_cutoff
    if not mask.all():
        dataset = dataset.isel(time=np.where(mask)[0])

    if np.isfinite(deploy_time):
        log.debug("Trimming start to deployment time: %s", format_time(deploy_time))
        dataset = dataset.sel(time=slice(deploy_time, None))

    if np.isfinite(recover_time):
        log.debug("Trimming end to recovery time: %s", format_time(recover_time))
        dataset = dataset.sel(time=slice(None, recover_time))

    final_size = len(dataset.time)
    log.debug("Trimmed from %d to %d records", original_size, final_size)
    if final_size == 0:
        log.warning("No data remains after trimming!")
    return dataset


def _gap_text(seconds: float) -> str:
    """Render a gap of *seconds* as whole days, adding approximate months when large."""
    days = seconds / 86400
    if days >= 60:
        return f"{days:.0f} d (~{days / 30.44:.0f} months)"
    return f"{days:.0f} d"


def _warn_short_record(
    serial: str,
    instrument_type: str,
    deploy_time: np.datetime64,
    recover_time: np.datetime64,
    start: np.datetime64,
    end: np.datetime64,
) -> None:
    """Warn when the trimmed record misses more than 5 % of the planned window.

    The planned window is the mooring YAML's ``deployment_time`` → ``recovery_time``
    span. A record that begins well after deployment or ends well before recovery
    is usually an instrument that failed early, not a processing error, so it earns
    a WARNING naming the end, the gap, and where the window came from. Applies to
    imported (originator-trimmed) records too — that is how an instrument that
    stopped mid-deployment shows up.

    Parameters
    ----------
    serial : str
        The instrument serial, for the message.
    instrument_type : str
        The instrument type, for the message.
    deploy_time, recover_time : numpy.datetime64
        The planned window from the YAML; ``NaT`` (unset) skips the check.
    start, end : numpy.datetime64
        The first and last time of the trimmed record.
    """
    if not (np.isfinite(deploy_time) and np.isfinite(recover_time)):
        return
    window_s = (recover_time - deploy_time) / np.timedelta64(1, "s")
    if window_s <= 0:
        return
    threshold_s = _SHORT_RECORD_FRACTION * window_s
    start_gap_s = (np.datetime64(start) - deploy_time) / np.timedelta64(1, "s")
    end_gap_s = (recover_time - np.datetime64(end)) / np.timedelta64(1, "s")
    if start_gap_s > threshold_s:
        log.warning(
            "%s %s starts %s after deployment (%s, from the mooring YAML)",
            instrument_type,
            serial,
            _gap_text(start_gap_s),
            format_time(deploy_time),
        )
    if end_gap_s > threshold_s:
        log.warning(
            "%s %s ends %s before recovery (%s, from the mooring YAML)",
            instrument_type,
            serial,
            _gap_text(end_gap_s),
            format_time(recover_time),
        )


def _add_missing_metadata(
    dataset: xr.Dataset, instrument_config: dict[str, Any]
) -> xr.Dataset:
    """Add the identity scalars and ``instrument`` a stage-1 file lacks, from the YAML entry."""
    # read_mooring stamps the identity attributes; the report and stack also expect
    # these scalar variables.
    identity = _stage.identity_variables(
        instrument_config, dataset.attrs.get("waterdepth")
    )
    # Stage-1 files before 0.6.1 hold these as InstrDepth and serial_number.
    dataset = dataset.drop_vars(list(_stage.OLD_IDENTITY_NAMES), errors="ignore")
    dataset = dataset.assign(
        {k: v for k, v in identity.items() if k not in dataset.variables}
    )
    instrument = instrument_config.get("instrument")
    if "instrument" not in dataset.variables and instrument is not None:
        dataset["instrument"] = instrument
    return dataset


def _drop_redundant_sbe_vars(dataset: xr.Dataset) -> xr.Dataset:
    """Drop redundant SeaBird CNV columns (timeS/timeQ; flag when all-zero)."""
    for var in ("timeS", "timeQ"):
        if var in dataset.variables:
            log.debug("Removing redundant SeaBird time variable: %s", var)
            dataset = dataset.drop_vars(var)

    if "flag" in dataset.variables:
        flag_vals = np.asarray(dataset["flag"].values, dtype="float64")
        if np.all((flag_vals == 0) | np.isnan(flag_vals)):
            log.debug(
                "Removing 'flag': all values are zero (no SeaBird scan flags set)"
            )
            dataset = dataset.drop_vars("flag")
            _stage.append_history(
                dataset, "dropped SeaBird 'flag' column: all values were 0 (good data)"
            )
        else:
            n_flagged = int(np.sum(np.isfinite(flag_vals) & (flag_vals != 0)))
            log.debug(
                "Keeping 'flag': %d non-zero scan flag(s) from SeaBird CNV", n_flagged
            )
    return dataset


def _refuse_missing_time(ds: xr.Dataset, entry: dict[str, Any]) -> None:
    """Raise MooringError when a stage-1 dataset holds records with no time (NaT)."""
    time = ds["time"].values
    if not np.issubdtype(time.dtype, np.datetime64):
        return
    n = int(np.isnat(time).sum())
    if n:
        raise MooringError(
            f"{entry.get('instrument', 'unknown')} s/n "
            f"{paths.safe_serial(entry.get('serial', 'unknown'))}: {n} record(s) "
            "with no time in the stage-1 file; re-run stage 1 (the reader now "
            "drops them)"
        )


def _stage2_instrument(
    ds: xr.Dataset,
    entry: dict[str, Any],
    mooring_name: str,
    deploy_time: np.datetime64,
    recover_time: np.datetime64,
) -> xr.Dataset:
    """Apply clock corrections and deployment trimming to one instrument dataset."""
    _refuse_missing_time(ds, entry)
    dataset = to_canonical_units(ds.copy(deep=True), stage=2)

    _stage1_t0 = dataset["time"].values[0]
    _stage1_t1 = dataset["time"].values[-1]

    clock_offset = entry.get("clock_offset", 0)
    drift_s, drift_note = _resolve_clock_drift(entry)
    drift_ramp = (drift_s - clock_offset) if drift_s != 0 else 0

    sug_start, sug_end, sug_source = detect_deployment_window(dataset)
    _nonnull_start, _nonnull_end = _nonnull_time_bounds(dataset)

    serial = paths.safe_serial(entry.get("serial", "unknown"))
    instrument_type = entry.get("instrument", "unknown")
    if not np.isfinite(deploy_time) or not np.isfinite(recover_time):
        _warn_missing_deployment_window(
            serial,
            instrument_type,
            mooring_name,
            deploy_time,
            recover_time,
            sug_start,
            sug_end,
            clock_offset,
        )

    dataset = _add_missing_metadata(dataset, entry)
    dataset = _drop_redundant_sbe_vars(dataset)

    if clock_offset != 0 or drift_ramp != 0:
        dataset = _preserve_time_orig(dataset)
    dataset = _apply_clock_offset(dataset, clock_offset)
    dataset = _apply_clock_drift(
        dataset, drift_ramp, deploy_time, recover_time, history_note=drift_note
    )
    dataset = _trim_to_deployment_window(dataset, deploy_time, recover_time)

    if len(dataset.time) == 0:
        raise MooringError(
            f"No data remains after processing {instrument_type} {serial}"
        )

    start_time = dataset["time"].values.min()
    end_time = dataset["time"].values.max()
    log.debug(
        "Final time range: %s to %s",
        format_time(start_time),
        format_time(end_time),
    )
    _warn_short_record(
        serial, instrument_type, deploy_time, recover_time, start_time, end_time
    )

    dataset.attrs.setdefault("qc_convention", params.QC_CONVENTION)

    if sug_start is not None:
        dataset.attrs["suggested_deployment_time"] = pd.Timestamp(sug_start).isoformat()
        dataset.attrs["suggested_recovery_time"] = pd.Timestamp(sug_end).isoformat()
        dataset.attrs["suggested_window_source"] = sug_source
        dataset.attrs["suggested_window_clock"] = "raw_instrument_clock"
        _sugg_start_utc = pd.Timestamp(sug_start) + pd.Timedelta(seconds=clock_offset)
        dataset.attrs["suggested_deployment_time_utc"] = _sugg_start_utc.isoformat()
        _end_correction = drift_s if drift_s else clock_offset
        _sugg_end_utc = pd.Timestamp(sug_end) + pd.Timedelta(seconds=_end_correction)
        dataset.attrs["suggested_recovery_time_utc"] = _sugg_end_utc.isoformat()
    dataset.attrs["stage1_first_nonnull"] = pd.Timestamp(_nonnull_start).isoformat()
    dataset.attrs["stage1_last_nonnull"] = pd.Timestamp(_nonnull_end).isoformat()
    dataset.attrs["stage1_time_start"] = pd.Timestamp(_stage1_t0).isoformat()
    dataset.attrs["stage1_time_end"] = pd.Timestamp(_stage1_t1).isoformat()
    if np.isfinite(deploy_time):
        dataset.attrs["yaml_deployment_time"] = pd.Timestamp(deploy_time).isoformat()
    if np.isfinite(recover_time):
        dataset.attrs["yaml_recovery_time"] = pd.Timestamp(recover_time).isoformat()

    return drop_all_zero_vars(dataset, ["amplitude_beam", "analog_input_"])


def stage2(
    datasets: dict[str, xr.Dataset], mooring: dict[str, Any]
) -> dict[str, xr.Dataset]:
    """Apply clock corrections and deployment trimming to a mooring's stage-1 datasets.

    Pure function over datasets: no file I/O.  *datasets* is keyed by serial (as
    :func:`read_mooring` returns); *mooring* is the loaded description.  Returns the
    stage-2 datasets, each marked ``processing_stage = 2`` and holding the
    values its file will hold, so each equals its file read back.

    Parameters
    ----------
    datasets : dict of str to xarray.Dataset
        Stage-1 datasets keyed by cleaned serial.
    mooring : dict
        The loaded mooring description.

    Returns
    -------
    dict of str to xarray.Dataset
        Stage-2 datasets keyed by serial.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If a stage-1 dataset holds records with no time (written before the
        readers dropped them); re-run stage 1.
    """
    deploy_time = _parse_yaml_time(mooring, "deployment_time")
    recover_time = _parse_yaml_time(mooring, "recovery_time")
    mooring_name = mooring["name"]
    by_serial = {
        paths.safe_serial(e.get("serial", "")): e
        for e in instrument_entries(mooring)
        if isinstance(e, dict)
    }
    out: dict[str, xr.Dataset] = {}
    for serial, ds in datasets.items():
        entry = by_serial.get(serial, {})
        result = _stage2_instrument(ds, entry, mooring_name, deploy_time, recover_time)
        out[serial] = _stage.stamp(as_stored(result), 2)
    return out


class Stage2Processor:
    """Handles Stage 2 processing: clock correction and temporal trimming.

    .. deprecated:: 0.6.0
        Removed in 0.7.0; see the migration page.
    """

    def __init__(self, *, proc_dir: str) -> None:
        """Apply clock-drift correction and trim records to the deployment window.

        Reads stage1 CF-NetCDF files, corrects instrument clock offsets using a
        linear drift model (offset at deployment and recovery specified in the YAML),
        and trims each record to the deployment start/end times.  Writes
        ``{mooring}_{serial}_stage2.nc`` alongside the stage1 file.

        Parameters
        ----------
        proc_dir : str
            Cruise-level processed output directory. The pipeline appends
            ``/{mooring}/`` internally (see :func:`oceanarray.paths.mooring_proc_dir`).

        """
        from oceanarray import _deprecated

        _deprecated.warn("oceanarray.pipeline.stage2.Stage2Processor")
        self._proc_dir = Path(proc_dir)
        self.log_file = None

    def _rel(self, path: Path) -> str:
        """Return a short display path relative to proc_dir."""
        if self._proc_dir:
            try:
                return str(path.relative_to(self._proc_dir))
            except ValueError:
                pass
        return path.name

    def _setup_logging(self, mooring_name: str, output_path: Path) -> None:
        """Set up logging for the processing run using global config."""
        from oceanarray.logger import attach_stage_handlers, setup_stage_logging

        self.log_file = setup_stage_logging(mooring_name, "stage2", output_path)
        self._log_handlers = attach_stage_handlers(self.log_file)

    def _log_print(self, *args: Any, **kwargs: Any) -> None:
        """Print to both console and log file."""
        print(*args, **kwargs)
        if self.log_file:
            with self.log_file.open("a", encoding="utf-8") as f:
                print(*args, **kwargs, file=f)

    @with_stage_handlers
    def process_mooring(
        self,
        mooring_name: str,
        output_path: str | None = None,
        serials: list[str] | None = None,
        force: bool = False,
    ) -> bool:
        """Process Stage 2 for a single mooring.

        Args:
            mooring_name: Name of the mooring to process
            output_path: Optional custom output path
            serials: Optional list of serial numbers to process; if None, process all.
            force: Re-process even if Stage 2 output already exists.

        Returns
        -------
            bool: True if processing completed successfully

        """
        # Set up paths — {proc_dir}/{mooring}/
        if output_path is None:
            proc_dir = paths.mooring_proc_dir(self._proc_dir, mooring_name)
        else:
            proc_dir = Path(output_path) / mooring_name

        if not proc_dir.exists():
            print(f"ERROR: Processing directory not found: {proc_dir}")
            return False

        # Set up logging
        self._setup_logging(mooring_name, proc_dir)
        self._log_print(f"Starting Stage 2 processing for mooring: {mooring_name}")

        # Load configuration
        config_file = proc_dir / f"{mooring_name}.mooring.yaml"
        if not config_file.exists():
            self._log_print(f"ERROR: Configuration file not found: {config_file}")
            return False

        try:
            mooring_config = _mooring.load(config_file)
        except Exception as e:  # noqa: BLE001  — intentional broad catch at I/O boundary
            self._log_print(f"ERROR: Failed to load configuration: {e}")
            return False

        run = _stage2_files(
            mooring_config,
            proc_dir,
            serials=serials,
            force=force,
            log=self._log_print,
            rel=self._rel,
        )
        # Partial success is still success: the instruments that processed are
        # written and the log names the ones that failed.
        return run.matched > len(run.failed)


def _stage2_files(
    mooring: dict[str, Any],
    mooring_dir: Path,
    *,
    serials: list[str] | None = None,
    force: bool = False,
    exclude: set[str] | frozenset[str] = frozenset(),
    log: Callable[..., None] = print,
    rel: Callable[[Path], str] = lambda p: p.name,
) -> _stage.StageRun:
    """Run stage 2 over one mooring's stage-1 files, one instrument at a time."""
    wanted = {paths.safe_serial(s) for s in serials} if serials else None
    run = _stage.StageRun()
    n_skipped = n_excluded = 0
    for entry in instrument_entries(mooring):
        if not isinstance(entry, dict):
            continue
        serial = paths.safe_serial(entry.get("serial", ""))
        if wanted is not None and serial not in wanted:
            continue
        run.matched += 1
        instr_type = entry.get("instrument", "unknown")
        if entry.get("skip"):
            reason = entry.get("skip_reason", "marked skip:true in YAML")
            log(f"SKIP {instr_type} {serial}: {reason}")
            n_skipped += 1
            continue
        # Failed earlier in this run: its older stage-1 file must not be used.
        if serial in exclude:
            log(f"SKIP {instr_type} {serial}: failed earlier in this run")
            n_excluded += 1
            continue
        out_path = paths.stage_file(mooring_dir, mooring["name"], instr_type, serial, 2)
        if out_path.exists() and not force:
            log(f"SKIP (exists): {rel(out_path)}")
            run.files.append(out_path)
            continue
        try:
            datasets = read_mooring(
                mooring, stage=1, serials=[serial], mooring_dir=mooring_dir
            )
            new = write_mooring(stage2(datasets, mooring), mooring_dir, overwrite=True)
            run.written += new
            run.files += new
        except Exception as e:  # noqa: BLE001  — one instrument must not abort the mooring
            log(f"ERROR: {instr_type} {serial}: {e}")
            run.failed.append(f"{instr_type} {serial}: {e}")
            run.failed_serials.append(serial)

    attempted = run.matched - n_skipped - n_excluded
    extra = "".join(
        f", {n} {what}"
        for n, what in (
            (n_skipped, "skipped (skip: true)"),
            (n_excluded, "left out (failed earlier in this run)"),
        )
        if n
    )
    log(
        f"Stage 2 completed: {attempted - len(run.failed)}/{attempted} "
        f"instruments successful{extra}"
    )
    if run.matched == 0:
        log("No instruments matched — nothing processed.")
    return run
