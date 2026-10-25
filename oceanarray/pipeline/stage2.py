"""Stage 2 processing for mooring data: apply clock corrections and trim to deployment.

Processing order per instrument
--------------------------------
1. Load Stage 1 ``_stage1.nc`` file.
2. Resolve clock offset and drift from YAML.
3. If either is non-zero, save ``time_orig`` (original instrument time) before correcting.
4. Apply a linear correction that ramps from ``clock_offset`` at deployment to
   ``clock_drift_seconds`` at recovery.  Both default to 0, so:
   - Only ``clock_offset`` set: uniform constant shift (same correction throughout).
   - Only ``clock_drift_seconds`` set: ramps from 0 at deployment to drift at recovery.
   - Both set: ramps from ``clock_offset`` at deployment to ``clock_drift_seconds`` at recovery.
5. Trim record to ``deployment_time`` … ``recovery_time`` from the mooring YAML.
6. Write ``_stage2.nc``.  ``time_orig`` is only present when a correction was applied.

Clock correction YAML keys (per instrument)
--------------------------------------------
``clock_offset`` : float, seconds
    Total correction to apply at the start of the deployment (instrument clock error
    at deployment time).  Positive = instrument was slow (behind UTC).

``clock_drift_seconds`` : float, seconds  [Option A]
    Total correction to apply at the end of the deployment (instrument clock error
    at recovery time).  Positive = instrument was slow (behind UTC) at recovery.
    The correction ramps linearly from ``clock_offset`` at deployment to this value at recovery.

``computer_clock_at_recovery`` / ``instrument_clock_at_recovery`` : ISO-8601 str  [Option B]
    Two timestamps read off at recovery.  drift = computer − instrument = total correction
    at recovery.  Equivalent to setting ``clock_drift_seconds``.
    Option B takes priority over Option A if both are present.

Sign convention
---------------
All clock values are the amounts **added** to instrument time to obtain corrected time.

  - Positive value → instrument was *slow* (behind real time); times shifted later.
  - Negative value → instrument was *fast* (ahead of real time); times shifted earlier.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray import parameters as params
from oceanarray import paths
from oceanarray.exceptions import MooringError
from oceanarray.logger import log_info, with_stage_handlers
from oceanarray.pipeline import _stage
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.pipeline.units import to_canonical_units
from oceanarray.pipeline.write_mooring import write_mooring
from oceanarray.utilities import (
    drop_all_zero_vars,
    instrument_entries,
)
from oceanarray.writers.netcdf import as_stored


def _parse_clock_str(s: str) -> pd.Timestamp | None:
    """Parse a clock timestamp in multiple formats; return None if unparseable.

    Accepts:
      - ``HH:MM:SS``             (time only — date is arbitrary; only differences matter)
      - ``YYYYMMDDTHH:MM:SS``    (compact ISO, no dashes in date)
      - ``YYYY-MM-DDTHH:MM:SS``  (standard ISO)
      - ``unknown`` or any non-parseable string → None (no correction applied)
    """
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


def _append_history(dataset: xr.Dataset, note: str) -> None:
    """Append a timestamped note to dataset.attrs['history'] in place.

    Delegates to :func:`oceanarray.pipeline._stage.append_history` so the
    ``history`` timestamp format lives in one place (and uses the non-deprecated
    UTC clock).
    """
    from oceanarray.pipeline._stage import append_history

    append_history(dataset, note)


def detect_deployment_window(
    ds: xr.Dataset,
) -> tuple[np.datetime64 | None, np.datetime64 | None, str]:
    """Estimate the deployed in-water window from a stage1 pressure record.

    .. important:: **Returns ``(None, None, ...)`` when no pressure data are
       available.**  The caller must check for ``None`` before using the result.
       Suggested times are **not** written to the output file when pressure is
       absent — instruments without pressure receive no ``suggested_*`` attrs.

    .. important:: **All non-None timestamps are in the raw instrument clock.**

       The input *ds* is a stage 1 dataset whose ``"time"`` coordinate carries
       the uncorrected instrument clock.  Add the YAML ``clock_offset`` to
       convert to UTC for copy-pasting into the YAML.

    **Pressure-based algorithm** (middle-50 % pmin + 10 dbar):

    1. **Middle-50 % reference window**: skip the first and last 25 % of the
       record by time.  This excludes any bench / surface period at either end
       regardless of its length, while avoiding a ``pmax``-based threshold that
       would be biased by knockdown events (which push instruments *deeper* than
       the nominal deployment depth).
    2. ``pmin_deployed`` = 1st-percentile of pressure within that middle window
       (robust to brief sensor-zero artefacts that would drag the absolute
       minimum to ~0 dbar and make the threshold negative).
    3. ``threshold`` = ``pmin_deployed − 10`` dbar.
    4. **Opening search window**: the first 25 % of the record by time (mirrors
       the middle window, long enough to cover multi-day bench periods).
    5. **Closing search window**: the last 25 % of the record by time.
    6. **Start**: last sample at or below the threshold in the opening window
       → the *next* sample is the suggested deployment start.
    7. **End**: first sample at or below the threshold in the closing window
       → the *preceding* sample is the suggested recovery end.

    This approach avoids two known failure modes of the original "skip
    first/last 12 h" middle window:

    * **Bench data in the middle window**: if an instrument recorded for days on
      deck before deployment, the 12 h skip was too short and ``pmin`` would
      equal the bench pressure (≈ 0 dbar), driving ``threshold`` negative so
      that nothing ever satisfied it.
    * **``pmax``-based threshold biased by knockdown**: using ``0.5 × pmax``
      as a conservative-window threshold is biased because knockdowns push
      instruments *deeper* than their nominal position, inflating ``pmax``
      above the nominal deployment pressure.  ``0.5 × pmax`` therefore sits
      *deeper* than intended, delaying the start of the conservative window.
      The middle-50 % approach uses the typical deployed pressure (median
      region of the time series), not the occasional extreme.

    Returns ``(None, None, "no_pressure")`` when the dataset has no
    ``"pressure"`` variable, fewer than 10 records, all-NaN pressure, the
    middle window is empty, or the algorithm cannot produce a valid window.

    Parameters
    ----------
    ds : xr.Dataset
        Stage 1 dataset.  Must contain a ``"time"`` coordinate in the raw
        instrument clock.  The ``"pressure"`` variable (dbar) is required.

    Returns
    -------
    tuple[Optional[np.datetime64], Optional[np.datetime64], str]
        ``(sug_start, sug_end, source)`` where *source* is
        ``"pressure_pmin10dbar"`` on success or ``"no_pressure"`` when no
        valid pressure data are available.

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


def _find_nonnull_bounds(
    dataset: xr.Dataset,
) -> tuple[np.datetime64, np.datetime64]:
    """Return (first_nonnull_time, last_nonnull_time) using a variable priority hierarchy.

    Priority: pressure > temperature > any variable with 'velocity' or 'turbidity' in
    its name > any other 1-D time-dimension variable.  Falls back to the first/last
    timestamps if no finite values are found.

    .. important:: Returns times in whatever clock the dataset carries — for stage 1
       input this is the **raw instrument clock** (uncorrected).

    Parameters
    ----------
    dataset : xr.Dataset
        Dataset whose ``"time"`` coordinate is used for indexing.

    Returns
    -------
    tuple[np.datetime64, np.datetime64]
        ``(first_nonnull, last_nonnull)`` timestamps from the chosen variable.

    """
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


def _read_yaml_time(data: dict[str, Any], key: str) -> np.datetime64:
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

    log_info("Applying clock offset: %+.1f s", clock_offset)
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
    _append_history(result, f"clock_offset={sign}{clock_offset:.1f}s applied")
    return result


def _resolve_clock_drift(instrument_config: dict[str, Any]) -> tuple:
    """Return (drift_seconds, history_note) from the YAML config.

    Convention: drift is the amount ADDED to instrument time at recovery to get
    corrected time (positive = instrument was slow/behind).  Option B (two recovery
    timestamps) takes priority over Option A (``clock_drift_seconds``).
    """
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

    log_info("Applying clock drift: %+.1f s over deployment", clock_drift_seconds)

    total_duration_s = (recover_time - deploy_time) / np.timedelta64(1, "s")
    if total_duration_s <= 0:
        log_info("WARNING: deploy_time >= recover_time; skipping clock drift ramp")
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
    _append_history(
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
        log_info(line)


def _trim_to_deployment_window(
    dataset: xr.Dataset,
    deploy_time: np.datetime64,
    recover_time: np.datetime64,
) -> xr.Dataset:
    """Trim to the deployment window, pre-filtering stray (clock-wrap) records first.

    A stray-record pre-filter (boolean indexing, non-monotonic safe) drops any record
    more than 31 days outside the window; the remainder is sliced to
    ``[deploy_time, recover_time]`` (which requires a monotonic time index).
    """
    original_size = len(dataset.time)

    _one_month = np.timedelta64(31, "D")
    t_vals = dataset.time.values
    mask = np.ones(len(t_vals), dtype=bool)
    if np.isfinite(deploy_time):
        early_cutoff = deploy_time - _one_month
        n_early = int(np.sum(t_vals < early_cutoff))
        if n_early:
            log_info(
                "WARNING: dropping %d record(s) with timestamps more than 31 days "
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
            log_info(
                "WARNING: dropping %d record(s) with timestamps more than 31 days "
                "after recovery (%s): %s",
                n_late,
                late_cutoff,
                t_vals[t_vals > late_cutoff],
            )
            mask &= t_vals <= late_cutoff
    if not mask.all():
        dataset = dataset.isel(time=np.where(mask)[0])

    if np.isfinite(deploy_time):
        log_info("Trimming start to deployment time: %s", deploy_time)
        dataset = dataset.sel(time=slice(deploy_time, None))

    if np.isfinite(recover_time):
        log_info("Trimming end to recovery time: %s", recover_time)
        dataset = dataset.sel(time=slice(None, recover_time))

    final_size = len(dataset.time)
    log_info("Trimmed from %d to %d records", original_size, final_size)
    if final_size == 0:
        log_info("WARNING: No data remains after trimming!")
    return dataset


def _add_missing_metadata(
    dataset: xr.Dataset, instrument_config: dict[str, Any]
) -> xr.Dataset:
    """Add the InstrDepth / instrument / serial_number scalar variables from the YAML.

    The identity *attributes* are stamped by :func:`read_mooring`; this adds the scalar
    *variables* the downstream report and stack expect, from the description entry.
    """
    if "InstrDepth" not in dataset.variables:
        dataset["InstrDepth"] = instrument_config.get("depth", 0)
    instrument = instrument_config.get("instrument")
    serial = instrument_config.get("serial")
    if "instrument" not in dataset.variables and instrument is not None:
        dataset["instrument"] = instrument
    if "serial_number" not in dataset.variables and serial is not None:
        dataset["serial_number"] = serial
    return dataset


def _clean_unnecessary_variables(dataset: xr.Dataset) -> xr.Dataset:
    """Drop redundant SeaBird CNV columns (timeS/timeQ; flag when all-zero)."""
    for var in ("timeS", "timeQ"):
        if var in dataset.variables:
            log_info("Removing redundant SeaBird time variable: %s", var)
            dataset = dataset.drop_vars(var)

    if "flag" in dataset.variables:
        flag_vals = np.asarray(dataset["flag"].values, dtype="float64")
        if np.all((flag_vals == 0) | np.isnan(flag_vals)):
            log_info("Removing 'flag': all values are zero (no SeaBird scan flags set)")
            dataset = dataset.drop_vars("flag")
            _append_history(
                dataset, "dropped SeaBird 'flag' column: all values were 0 (good data)"
            )
        else:
            n_flagged = int(np.sum(np.isfinite(flag_vals) & (flag_vals != 0)))
            log_info(
                "Keeping 'flag': %d non-zero scan flag(s) from SeaBird CNV", n_flagged
            )
    return dataset


def _stage2_instrument(
    ds: xr.Dataset,
    entry: dict[str, Any],
    mooring_name: str,
    deploy_time: np.datetime64,
    recover_time: np.datetime64,
) -> xr.Dataset:
    """Apply clock corrections and deployment trimming to one instrument dataset.

    The body of the old ``_process_instrument`` with the file I/O removed: canonicalise
    units, resolve/apply the clock offset and linear drift, trim to the window, and
    stamp the stage-1 bounds, the suggested window and the YAML window as attributes.
    """
    dataset = to_canonical_units(ds.copy(deep=True), log_fn=log_info, stage=2)

    _stage1_t0 = dataset["time"].values[0]
    _stage1_t1 = dataset["time"].values[-1]

    clock_offset = entry.get("clock_offset", 0)
    drift_s, drift_note = _resolve_clock_drift(entry)
    drift_ramp = (drift_s - clock_offset) if drift_s != 0 else 0

    sug_start, sug_end, sug_source = detect_deployment_window(dataset)
    _nonnull_start, _nonnull_end = _find_nonnull_bounds(dataset)

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
    dataset = _clean_unnecessary_variables(dataset)

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
    log_info("Final time range: %s to %s", start_time, end_time)

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
    """
    deploy_time = _read_yaml_time(mooring, "deployment_time")
    recover_time = _read_yaml_time(mooring, "recovery_time")
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
    """Handles Stage 2 processing: clock correction and temporal trimming."""

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
    log: Callable[..., None] = print,
    rel: Callable[[Path], str] = lambda p: p.name,
) -> _stage.StageRun:
    """Run stage 2 over one mooring's stage-1 files, one instrument at a time.

    read_mooring joins the stage-1 file with the description (identity + facts +
    marker), stage2 is the pure transform, write_mooring lays the stage-2 file out
    from the dataset's stamped facts. One instrument's failure is logged and
    recorded; the rest of the mooring is still written.
    """
    wanted = {paths.safe_serial(s) for s in serials} if serials else None
    run = _stage.StageRun()
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

    log(
        f"Stage 2 completed: {run.matched - len(run.failed)}/{run.matched} "
        "instruments successful"
    )
    if run.matched == 0:
        log("No instruments matched — nothing processed.")
    return run
