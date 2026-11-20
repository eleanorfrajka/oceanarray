"""Stack all instruments on a mooring onto a common time axis (nearest sample per step)."""

import logging
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from oceanarray import catalog, logger, paths, provenance
from oceanarray import mooring as _mooring
from oceanarray.config import parameters as params
from oceanarray.exceptions import MooringError
from oceanarray.instruments import instrument_entries
from oceanarray.location import (
    is_unknown_latlon_source,
    parse_latlon,
    parse_latlon_with_source,
)
from oceanarray.paths import safe_serial
from oceanarray.physics import potential_density_anomaly, speed_direction
from oceanarray.pipeline import _stage, attribution, masking, sensors
from oceanarray.pipeline.dataset import drop_all_zero_vars, time_axis
from oceanarray.pipeline.pressure import _times_to_float
from oceanarray.pipeline.qc import _worst_flag
from oceanarray.pipeline.qc.originator import MAPPED_SUFFIX
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.pipeline.units import UnitsError, to_canonical_units
from oceanarray.writers import write
from oceanarray.writers.netcdf import as_stored

log = logging.getLogger(__name__)

STACK_VARS = [
    "temperature",
    "temperature_qc",
    "salinity",
    "salinity_qc",
    "conductivity",
    "pressure",
    "pressure_qc",
    "east_velocity",
    "north_velocity",
    "up_velocity",
    "current_speed",
    "current_direction",
    "east_velocity_qc",
    "north_velocity_qc",
    "up_velocity_qc",
    "pitch_qc",
    "roll_qc",
    "velocity_beam1",
    "velocity_beam2",
    "velocity_beam3",
    "heading",
    "pitch",
    "roll",
    "analog_input_1",
    "analog_input_2",
    "percent_good_qc",
    "error_velocity_qc",
    "seabed_qc",
    "turbidity",
    "turbidity_qc",
    "dissolved_oxygen",
    "dissolved_oxygen_qc",
    "oxygen_saturation_pct",
    "apparent_oxygen_utilization",
]


#: The originator's flags, translated to our table at stage 3, carried for each
#: variable an originator file can hold (``<var>_qc_originator_mapped``).
STACK_VARS += [f"{v}{MAPPED_SUFFIX}" for v in masking.FLAGGED_VARIABLES]


def _is_flag(name: str) -> bool:
    """Return whether a stack variable is a flag (``_qc`` or translated originator)."""
    return name.endswith(("_qc", MAPPED_SUFFIX))


def _flag_parent(name: str) -> str:
    """Return the data variable a flag belongs to."""
    return name.removesuffix(MAPPED_SUFFIX).removesuffix("_qc")


def _sample_interval_s(time_vals: np.ndarray) -> float:
    """Return the median sample interval in seconds; NaN for fewer than two samples."""
    if len(time_vals) < 2:
        return float("nan")
    dt = np.diff(time_vals.astype("datetime64[s]").astype(np.float64))
    return float(np.median(dt))


# Variables measured at the ADCP transducer head (point measurements, not per-bin).
# These must NOT appear in per-bin datasets — they belong only in the head entry.
_ADCP_HEAD_VARS: frozenset = frozenset(
    {
        "temperature",
        "temperature_qc",
        "heading",
        "pitch",
        "roll",
        "battery_voltage",
        "speed_of_sound",
        "analog_input_1",
        "analog_input_2",
    }
)


def _build_adcp_head_ds(ds_parent: xr.Dataset) -> xr.Dataset:
    """Create a 1-D (time-only) view of the ADCP transducer head for stack insertion."""
    # The head is its own stack position carrying sensor diagnostics, not water-column
    # data: its temperature is electronics temperature (warm-up drift), not water
    # temperature. Per-bin velocity and bin_pressure are excluded; each bin gets its
    # own entry at its own pressure (_build_adcp_bin_ds).
    keep = [
        v
        for v in ds_parent.data_vars
        if v in _ADCP_HEAD_VARS and ds_parent[v].dims == ("time",)
    ]
    # Also keep the instrument-head pressure if present (1-D, not bin_pressure):
    # pressure at the transducer face, i.e. the instrument depth.
    if (
        "pressure" in ds_parent.data_vars
        and ds_parent["pressure"].dims == ("time",)
        and "pressure" not in keep
    ):
        keep.append("pressure")
    return ds_parent[keep]


def _build_adcp_bin_ds(ds_parent: xr.Dataset, bin_idx: int) -> xr.Dataset:
    """Create a 1-D (time-only) point-instrument view of one ADCP range bin."""
    # bin_idx is zero-based; 0 is the bin nearest the transducer face.
    bin_dim = params.ADCP_BIN_DIM
    ds_bin = ds_parent.isel({bin_dim: bin_idx}, drop=True)

    # Drop head-only variables — these belong in the separate head entry
    head_to_drop = [v for v in _ADCP_HEAD_VARS if v in ds_bin.data_vars]
    if head_to_drop:
        ds_bin = ds_bin.drop_vars(head_to_drop)

    # The bin's own pressure replaces the transducer pressure so the entry sits at
    # the correct depth in the stack.
    if "bin_pressure" in ds_bin.data_vars:
        ds_bin = ds_bin.assign(
            pressure=xr.Variable(
                "time",
                ds_bin["bin_pressure"].values.astype(np.float32),
                {"units": "dbar", "long_name": "Pressure at ADCP bin"},
            )
        ).drop_vars("bin_pressure")

    if "east_velocity" in ds_bin.data_vars and "north_velocity" in ds_bin.data_vars:
        e = ds_bin["east_velocity"].values.astype(float)
        n = ds_bin["north_velocity"].values.astype(float)
        speed, direction = speed_direction(e, n)
        # Direction is oceanographic: toward which the water flows, clockwise from true
        # north once stage 3 applied declination (magnetic north in a stage-2 stack);
        # 0 = northward, 90 = eastward, the opposite of meteorological "from".
        ds_bin = ds_bin.assign(
            current_speed=xr.Variable(
                "time", speed.astype(np.float32), params.cf_attrs("current_speed")
            ),
            current_direction=xr.Variable(
                "time",
                direction.astype(np.float32),
                params.cf_attrs("current_direction"),
            ),
        )

    # Beam-dimension arrays (amplitude, correlation, percent_good) cannot be a point
    # time series without choosing a beam; dropping them leaves only ("time",) or
    # scalar variables, as _nearest_subsample needs.
    to_drop = [v for v in ds_bin.data_vars if any(d != "time" for d in ds_bin[v].dims)]
    if to_drop:
        ds_bin = ds_bin.drop_vars(to_drop)

    return ds_bin


def _nearest_index(
    src_t: np.ndarray, tgt_t: np.ndarray, half_window_ns: float
) -> np.ndarray:
    """Return the index of the nearest source time within the window per target, else -1."""
    # The window is half-open, [t - half, t + half): a sample exactly half a step
    # from two targets lands on the later one only. On equal distance the earlier
    # sample wins.
    n_src = len(src_t)
    if n_src == 0:
        return np.full(len(tgt_t), -1, dtype=np.intp)
    k = np.searchsorted(src_t, tgt_t)  # src_t[k - 1] < t <= src_t[k]
    before = np.clip(k - 1, 0, n_src - 1)
    after = np.clip(k, 0, n_src - 1)
    d_before = np.where(k > 0, tgt_t - src_t[before], np.inf)
    d_after = np.where(k < n_src, src_t[after] - tgt_t, np.inf)
    ok_before = d_before <= half_window_ns
    ok_after = d_after < half_window_ns
    use_after = ok_after & (~ok_before | (d_after < d_before))
    return np.where(use_after, after, np.where(ok_before, before, -1))


def _nearest_subsample(
    ds: xr.Dataset,
    common_time: np.ndarray,
    half_window_s: float,
) -> dict[str, np.ndarray]:
    """Return each stack variable's nearest sample on common_time within ±half_window_s."""
    # NaN where no sample falls within the window; no interpolation, no averaging.
    idx = _nearest_index(
        _times_to_float(ds["time"].values),
        _times_to_float(common_time),
        half_window_s * 1e9,
    )
    hit = idx >= 0
    n = len(common_time)
    result: dict[str, np.ndarray] = {}
    for vname in STACK_VARS:
        out = np.full(n, np.nan)
        if vname in ds.data_vars:
            # Every value is kept; its flags ride beside it, from the same sample.
            src_v = ds[vname].values.astype(np.float64)
            out[hit] = src_v[idx[hit]]
        result[vname] = out
    return result


KNOWN_INSTRUMENT_TYPES: frozenset = params.KNOWN_INSTRUMENT_TYPES


class _LowPassedError(ValueError):
    """A low-passed instrument file reached the stack without ``stack: false``."""


#: Registry entry of the σ at each reference pressure (dbar).
_SIGMA_ENTRY = {0: "potential_density"} | {
    ref: f"sigma{ref // 1000}" for ref in (1000, 2000, 3000, 4000)
}


def stack(
    datasets: dict[str, xr.Dataset],
    mooring: dict,
    *,
    dt_seconds: int | None = None,
    omitted: list[str] | None = None,
    stage2_reason: str = "no stage-3 file",
    mask_flags: Iterable[int] | Mapping[str, Iterable[int]] | None = None,
) -> xr.Dataset:
    """Stack a mooring's instruments onto a common time axis.

    Pure function over datasets: no file I/O. The time axis runs through the
    deployment window in steps of *dt_seconds*, starting at the first whole
    multiple of the step (UTC, counted from 1970-01-01; on the hour at 3600 s).
    Each step takes every instrument's nearest sample within ±dt/2 (no
    interpolation or averaging in time); a step with no sample in that window is
    NaN, so an instrument sampling slower than the step leaves the steps between
    its samples empty (with a warning). ADCPs expand into one level per bin plus a
    head level. Instruments marked ``stack: false`` are left out and named in
    ``instruments_excluded``; instruments that cannot be stacked (no ``hab`` in
    the description, non-canonical units, low-passed data) are left out and named
    in ``instruments_omitted``. A level whose stage-3 ``magnetic_declination`` is
    ``"UNK"`` (no mooring position) gets NaN in the per-level
    ``magnetic_declination``, whose ``comment`` names those levels.

    Every value is kept, flagged or not, with two flag sets beside it from the same
    sample: ours (``<var>_qc``) and the originator's translated to our table
    (``<var>_qc_originator_mapped``). A level with a value but no flag variable gets
    0 (not QC'd); a step with no value gets 9 (missing). Each level records its
    originator dictionary (``<var>_flag_translation``, ``_counts``; ``"none"``
    without originator flags). The rule for what the pages hide and the grid leaves
    out (*mask_flags*) is recorded as ``stack_mask_flags``; the potential density
    and ``tilt_from_pressure`` use only values it does not hide.

    Parameters
    ----------
    datasets : dict of str to xarray.Dataset
        Stage-3 (or stage-2) datasets keyed by cleaned serial.
    mooring : dict
        The loaded mooring description: the deployment window is the time axis, and
        each entry's ``hab`` and ``stack`` flag are used. Each instrument's sampling
        interval is detected from its time axis.
    dt_seconds : int or None, optional
        Spacing of the common time axis, in seconds. ``None`` takes
        ``stack: dt_seconds`` from *mooring*, else
        ``parameters.STACK_DT_SECONDS`` (3600).
    omitted : list of str or None, optional
        Instruments the caller already dropped (e.g. unreadable files), recorded
        in ``instruments_omitted`` with the ones dropped here.
    stage2_reason : str, optional
        Why any stage-2 datasets were stacked, recorded in ``stage2_reason`` and
        shown in the report's amber note (e.g. ``"stage 3 failed in this run"``).
    mask_flags : iterable of int, or Mapping of str to iterable of int, optional
        The flags hidden from the pages and left out of the grid
        (:func:`oceanarray.pipeline.masking.resolve_mask_flags`); ``None`` takes
        ``stack: mask_flags`` from *mooring*, else ``parameters.MASK_FLAGS`` (4).

    Returns
    -------
    xarray.Dataset
        The stack on ``(time, N_LEVELS)``, deepest level first.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If the description has no deployment window, the window holds no whole
        step, the time step is not a whole number > 0, or no instrument can be
        stacked.
    """
    from oceanarray.pipeline._run import resolve_dt_seconds

    mooring_config = mooring
    mooring_name = mooring["name"]
    omitted = list(omitted or [])
    try:
        rule = masking.stack_rule(mooring, mask_flags)
    except ValueError as exc:
        raise MooringError(f"{mooring_name}: {exc}") from exc

    # The common time axis IS the deployment window, so stacking needs it.
    # Stage 2 writing a window detected from the records (when the YAML omits
    # it) is later work; until then a window-less config fails loud here
    # rather than with a bare KeyError.
    if "deployment_time" not in mooring_config or (
        "recovery_time" not in mooring_config
    ):
        raise MooringError(
            f"Cannot stack {mooring_name}: the mooring config has no "
            f"'deployment_time'/'recovery_time' (the common time axis is the "
            f"deployment window). A window detected from the records is not yet "
            f"implemented — add both to the YAML."
        )
    deploy_time = np.datetime64(mooring_config["deployment_time"], "ns")
    recover_time = np.datetime64(mooring_config["recovery_time"], "ns")

    dt_seconds = resolve_dt_seconds(mooring_config, dt_seconds)
    common_time = time_axis(deploy_time, recover_time, dt_seconds)
    n_time = len(common_time)
    if n_time == 0:
        raise MooringError(
            f"Cannot stack {mooring_name}: no whole multiple of the {dt_seconds} s "
            f"step falls between the deployment and recovery times."
        )

    instrument_list = instrument_entries(mooring_config)

    # Collect instruments with known hab and an available stage2/stage3 file.
    # An instrument marked ``stack: false`` in the mooring YAML is processed
    # through stages 1-3 (reference data) but kept out of the stack, and named
    # in ``instruments_excluded`` — distinct from an error omission.
    instruments = []
    excluded: list[str] = []
    for entry in instrument_list:
        if not isinstance(entry, dict):
            continue
        serial = safe_serial(entry.get("serial", ""))
        instr_type = entry.get("instrument", "unknown")
        if not params.is_known_instrument_type(instr_type):
            log.warning(
                f"  instrument '{instr_type}' (s/n {serial}) is not in "
                f"KNOWN_INSTRUMENT_TYPES {sorted(KNOWN_INSTRUMENT_TYPES)}; "
                "it will be stacked as a generic instrument (Aquadopp/ADCP-specific "
                "handling skipped)."
            )
        if entry.get("stack", True) is False:
            excluded.append(f"{instr_type} s/n {serial}")
            log.debug(f"  EXCLUDE {instr_type} s/n {serial}: stack: false in the YAML")
            continue
        hab = entry.get("hab")
        if hab is None:
            # No height above bottom: the level cannot be placed. Named, not silent
            # (a skipped entry is not processed, so it is not reported).
            if not entry.get("skip"):
                reason = "no hab in the mooring YAML; set hab, or stack: false"
                omitted.append(f"{instr_type} s/n {serial} ({reason})")
                log.warning(f"  omitting {instr_type} s/n {serial} — {reason}")
            continue
        if serial not in datasets:
            why = next(
                (o for o in omitted or () if f"s/n {serial} " in f"{o} "),
                None,
            )
            log.debug(
                f"  SKIP {instr_type} s/n {serial}: "
                + ("left out, see below" if why else "no stage2/stage3 file")
            )
            continue
        ds_in = datasets[serial]
        instruments.append(
            {
                "serial": serial,
                "instrument": instr_type,
                "hab": float(hab),
                "ds": ds_in,
                "stage_label": f"stage{ds_in.attrs.get('processing_stage', '')}",
            }
        )

    if not instruments:
        raise MooringError(
            f"{mooring_name}: no instruments with a stage-2/stage-3 dataset and a hab value"
        )

    # Sort deep-first (ascending hab: smallest hab = nearest bottom = deepest)
    instruments.sort(key=lambda x: x["hab"])

    # ── Expand ADCP instruments into per-bin pseudo-instruments ───────
    # Each ADCP bin becomes a separate row in the stack, with its own
    # bin_pressure, velocity, and QC time series sliced from the 2-D
    # (time, N_BINS) arrays in the parent stage3 file.
    # The parent file is loaded once and cached; _build_adcp_bin_ds slices it.
    _adcp_parent_datasets: dict[str, xr.Dataset] = {}
    expanded: list[dict] = []
    for info in instruments:
        if info["instrument"].lower() != "adcp":
            expanded.append(info)
            continue
        nc_key = info["serial"]
        _adcp_parent_datasets[nc_key] = info["ds"]
        ds_adcp = info["ds"]
        bin_dim = params.ADCP_BIN_DIM
        if bin_dim not in ds_adcp.dims:
            log.warning(
                f"  ADCP s/n {info['serial']} has no {bin_dim} dim "
                "— treating as point instrument"
            )
            expanded.append(info)
            continue
        n_bins = ds_adcp.sizes[bin_dim]
        range_vals = (
            ds_adcp["range"].values
            if "range" in ds_adcp.coords
            else np.arange(n_bins, dtype=float)
        )
        orientation = ds_adcp.attrs.get("orientation_yaml") or ds_adcp.attrs.get(
            "orientation_instrument"
        )
        if not orientation:
            orientation = "down"
            log.warning(
                f"  ADCP {info['serial']} has no orientation attr — "
                "assuming downward-looking. HAB offsets may be wrong for "
                "upward-looking instruments."
            )
        looking_down = str(orientation).lower() == "down"
        # Pre-compute seabed mask so always-submerged bins can be skipped.
        # seabed_qc has dims (time, N_BINS); a bin is permanently below the
        # seabed when every time step is flagged suspect or worse (>= 3).
        _seabed_qc_vals = (
            ds_adcp["seabed_qc"].values if "seabed_qc" in ds_adcp.data_vars else None
        )

        n_skipped = 0
        valid_bins = []
        for i in range(n_bins):
            if _seabed_qc_vals is not None and np.all(_seabed_qc_vals[:, i] >= 3):
                n_skipped += 1
            else:
                valid_bins.append(i)

        log.debug(
            f"  ADCP s/n {info['serial']}: expanding {len(valid_bins)} of {n_bins} bins "
            f"({orientation}-looking) into stack + head entry"
            + (f" [{n_skipped} bins always below seabed, skipped]" if n_skipped else "")
        )

        # Head entry — transducer location carries temperature and orientation.
        # Uses the instrument HAB directly (range = 0 from the transducer).
        expanded.append(
            {
                **info,
                "serial": f"{info['serial']}_hd",
                "hab": float(info["hab"]),
                "_adcp_head": True,
                "_adcp_nc_key": nc_key,
            }
        )
        for i in valid_bins:
            r = float(range_vals[i])
            bin_hab = float(info["hab"]) - r if looking_down else float(info["hab"]) + r
            expanded.append(
                {
                    **info,
                    "serial": f"{info['serial']}_b{i:02d}",
                    "hab": bin_hab,
                    "_adcp_bin_idx": i,
                    "_adcp_nc_key": nc_key,
                }
            )
    instruments = expanded
    # Re-sort after expansion so ADCP bins interleave with other instruments by depth
    instruments.sort(key=lambda x: x["hab"])

    n_instr = len(instruments)

    log.info(
        f"Merging {n_instr} instruments onto {n_time}-point {dt_seconds}s time grid "
        f"({mooring_name})"
    )

    stacked: dict[str, np.ndarray] = {
        v: np.full((n_instr, n_time), np.nan) for v in STACK_VARS
    }
    var_attrs: dict[str, dict] = {v: {} for v in STACK_VARS}
    serials: list[str] = []
    habs: list[float] = []
    instr_types: list[str] = []
    stage_labels: list[str] = []
    # Per-instrument scalar metadata: {varname: [value_for_instr0, value_for_instr1, ...]}
    scalar_meta: dict[str, list] = {}  # populated during loop
    scalar_attrs: dict[str, dict] = {}
    # Each instrument's sensor record (dimensionless, kept as is) and, per level,
    # the record it came from (ADCP bins share their instrument's).
    sensor_records: dict[str, dict] = {}
    level_sensor: list[str] = []
    # Per level: the originator's flag dictionary and its counts, for each variable.
    mapped_vars = [v for v in STACK_VARS if v.endswith(MAPPED_SUFFIX)]
    level_translation: dict[str, list[tuple[str, str]]] = {v: [] for v in mapped_vars}
    # Stage-3 time-series variables not in STACK_VARS are dropped here; collect
    # them so the operator is told rather than losing data silently (D2).
    dropped_ts_vars: set = set()
    # Instruments dropped from the stack (bad units or unreadable file).  The
    # stack still builds (partial stacks are useful operationally), but each
    # omission is recorded loudly: in the summary, in an `instruments_omitted`
    # global attribute the grid inherits, and in the exit code.
    omitted_idx: list[int] = []  # enumerate positions of omitted instruments
    decl_unknown: list[str] = []  # levels whose stage-3 declination is "UNK"
    slow_warned: set[str] = set()  # instruments already warned as slower than dt
    slow: list[str] = []  # recorded in instruments_slower_than_step

    for i, info in enumerate(instruments):
        serials.append(info["serial"])
        habs.append(info["hab"])
        instr_types.append(info["instrument"])
        stage_labels.append(info["stage_label"])

        try:
            if "_adcp_bin_idx" in info:
                ds_parent = _adcp_parent_datasets[info["_adcp_nc_key"]]
                ds = _build_adcp_bin_ds(ds_parent, info["_adcp_bin_idx"])
            elif "_adcp_head" in info:
                ds_parent = _adcp_parent_datasets[info["_adcp_nc_key"]]
                ds = _build_adcp_head_ds(ds_parent)
            else:
                ds = info["ds"]
            # Canonicalise each instrument's units and identity names before
            # stacking, so an older input (degC, S/m, InstrDepth, …) joins canonical.
            ds = to_canonical_units(_stage.normalize_identity_names(ds), stage="stack")
            # A low-passed file (e.g. RODB .lowedt) would be sampled onto the
            # stack's steps as if it were full-rate data.
            if "originator_low_passed" in ds.attrs:
                raise _LowPassedError(
                    f"low-passed reference data "
                    f"({ds.attrs['originator_low_passed']}); set stack: false "
                    f"on this instrument in the mooring YAML"
                )
        except Exception as e:  # noqa: BLE001  — one bad instrument must not abort the stack
            if isinstance(e, UnitsError):
                reason = f"units error: {e}"
            elif isinstance(e, _LowPassedError):
                reason = str(e)
            else:
                reason = f"could not stack it: {e}"
            log.warning(
                f"  omitting {info['instrument']} s/n {info['serial']} — {reason}"
            )
            omitted.append(f"{info['instrument']} s/n {info['serial']} ({reason})")
            omitted_idx.append(i)
            # Ensure scalar_meta lists stay length-consistent
            for lst in scalar_meta.values():
                lst.append(None)
            level_sensor.append("")
            for mv in mapped_vars:
                level_translation[mv].append(("none", "{}"))
            continue

        interval_s = _sample_interval_s(ds["time"].values)
        interval_txt = f"{interval_s:.0f}s" if np.isfinite(interval_s) else "?"
        log.debug(
            f"  [{i:2d}] {info['instrument']:10s} s/n {info['serial']:<8}  "
            f"hab={info['hab']:6.1f} m  dt={interval_txt}  "
            f"({stage_labels[-1]})"
        )
        base = info["serial"]
        if "_adcp_bin_idx" in info or "_adcp_head" in info:
            base = base.rsplit("_", 1)[0]
        if interval_s > dt_seconds and base not in slow_warned:
            # An ADCP warns once, not once per bin.
            slow_warned.add(base)
            slow.append(f"{info['instrument']} s/n {base} ({interval_s:.0f} s)")
            log.warning(
                f"  {info['instrument']} s/n {base} samples every "
                f"{interval_s:.0f} s, slower than the {dt_seconds} s step: the steps "
                f"between its samples are empty in the stack and the grid (set "
                f"stack: dt_seconds to at least {interval_s:.0f} to avoid them)."
            )

        values = _nearest_subsample(ds, common_time, dt_seconds / 2.0)

        for vname in STACK_VARS:
            arr = values[vname]
            n = min(len(arr), n_time)
            stacked[vname][i, :n] = arr[:n]
            if not var_attrs[vname] and vname in ds.data_vars:
                # One instrument's record name is not every level's: the stack
                # names each level's record in sensor(N_LEVELS) instead.
                # (processing_level and the pressure sources are per instrument too)
                var_attrs[vname] = {
                    k: v
                    for k, v in ds[vname].attrs.items()
                    if k
                    not in (
                        "sensor",
                        "processing_level",
                        "pressure_source",
                        "pressure_source_serials",
                        "pressure_source_flags",
                    )
                }

        # Track any time-series stage-3 variables that STACK_VARS omits.
        dropped_ts_vars.update(
            v
            for v, da in ds.data_vars.items()
            if "time" in da.dims and v not in STACK_VARS
        )

        # From the instrument's own file (an ADCP's bins and head share its record).
        rec_name, rec_attrs = sensors.sensor_record(
            _adcp_parent_datasets[info["_adcp_nc_key"]]
            if "_adcp_nc_key" in info
            else info["ds"],
            info["instrument"],
            paths.adcp_parent_serial(info["serial"])
            if "_adcp_nc_key" in info
            else info["serial"],
        )
        sensor_records[rec_name] = rec_attrs
        level_sensor.append(rec_name)
        for mv in mapped_vars:
            attrs = ds[mv].attrs if mv in ds.data_vars else {}
            level_translation[mv].append(
                (
                    str(attrs.get("flag_translation", "none")),
                    str(attrs.get("flag_translation_counts", "{}")),
                )
            )

        # Collect scalar (0-D) metadata variables — keep all instruments consistent
        for vname, da in ds.data_vars.items():
            if da.dims or sensors.is_record(vname):  # time series; sensor records
                continue
            if vname not in scalar_meta:
                # Back-fill with None for instruments processed before this variable appeared
                scalar_meta[vname] = [None] * i
                scalar_attrs[vname] = dict(da.attrs)
            scalar_meta[vname].append(da.values.item())

        # Collect magnetic_declination from global attrs (set by stage3 BEAM→ENU).
        # Must come BEFORE the fill-None loop so the length is already i+1 when
        # the loop checks.
        if "magnetic_declination" not in scalar_meta:
            scalar_meta["magnetic_declination"] = [np.nan] * i
            scalar_attrs["magnetic_declination"] = {
                "units": "degrees_east",
                "long_name": "Magnetic declination (IGRF)",
            }
        decl_val = ds.attrs.get("magnetic_declination")
        if decl_val == "UNK":
            # Stage 3 had no position, so no declination was applied. Name each
            # physical instrument once (an ADCP expands into _hd/_b## levels that
            # all share the one declination).
            base = info["serial"]
            if "_adcp_bin_idx" in info or "_adcp_head" in info:
                base = base.rsplit("_", 1)[0]
            label = f"{info['instrument']} s/n {base}"
            if label not in decl_unknown:
                decl_unknown.append(label)
            decl_val = None
        scalar_meta["magnetic_declination"].append(
            float(decl_val) if decl_val is not None else np.nan
        )

        # Fill None for variables not present in this instrument
        for vname in scalar_meta:
            if len(scalar_meta[vname]) < i + 1:
                scalar_meta[vname].append(None)

    if dropped_ts_vars:
        log.info(
            f"  NOTE: {len(dropped_ts_vars)} stage-3 time-series variable(s) not in "
            f"STACK_VARS were dropped from the stack: "
            f"{', '.join(sorted(dropped_ts_vars))}. "
            f"Add them to oceanarray.pipeline.stack.STACK_VARS to retain them."
        )

    # Drop omitted instruments from every per-level array/list so the written
    # stack has no phantom all-NaN level — N_LEVELS and `instruments_omitted`
    # must agree.  The loop indexes `stacked`/`scalar_meta` by enumerate position,
    # so slice all of them to the kept positions here rather than restructuring it.
    _omitted_set = set(omitted_idx)
    kept = [i for i in range(n_instr) if i not in _omitted_set]
    if not kept:
        raise MooringError(
            f"every instrument was omitted — no stack ({mooring_name}): "
            + "; ".join(omitted)
        )
    if len(kept) < n_instr:
        serials = [serials[i] for i in kept]
        habs = [habs[i] for i in kept]
        instr_types = [instr_types[i] for i in kept]
        stage_labels = [stage_labels[i] for i in kept]
        for _v in stacked:
            stacked[_v] = stacked[_v][kept, :]
        for _k in scalar_meta:
            scalar_meta[_k] = [scalar_meta[_k][i] for i in kept]
        level_sensor = [level_sensor[i] for i in kept]
        for mv in mapped_vars:
            level_translation[mv] = [level_translation[mv][i] for i in kept]
        n_instr = len(kept)

    # Build output dataset; skip variables that are entirely NaN. A flag is
    # written as int8 with the fill rule: a present value with no flag is 0 (not
    # QC'd), a step with no value is 9 (missing); a present value is never 9.
    data_vars: dict = {}
    for vname in STACK_VARS:
        if np.all(np.isnan(stacked[vname])):
            continue
        values = stacked[vname]
        if _is_flag(vname):
            parent = stacked.get(_flag_parent(vname))
            if parent is not None and np.all(np.isnan(parent)):
                continue  # the variable itself is not written
            present = np.isfinite(parent) if parent is not None else np.isfinite(values)
            values = np.where(
                np.isfinite(values),
                values,
                np.where(present, params.QC_NOT_EVALUATED, params.QC_MISSING),
            ).astype(np.int8)
            if parent is not None:
                values[~present] = params.QC_MISSING
                # 9 means missing: a value that is there is at most unknown.
                wrong = present & (values == params.QC_MISSING)
                if wrong.any():
                    log.warning(
                        f"  {mooring_name}: {int(wrong.sum())} {vname} values that "
                        "are there were flagged missing (9); written 0 (unknown)"
                    )
                    values[wrong] = params.QC_NOT_EVALUATED
        data_vars[vname] = xr.Variable(
            ("N_LEVELS", "time"), values, attrs=var_attrs[vname]
        )
    for mv in mapped_vars:
        if mv not in data_vars:
            continue
        var = mv.removesuffix(MAPPED_SUFFIX)
        data_vars[f"{var}_flag_translation"] = xr.Variable(
            "N_LEVELS",
            np.array([t for t, _ in level_translation[mv]]),
            {
                "long_name": f"originator flag dictionary for {var} (number word -> "
                "ours), per level",
                "comment": "translated by meaning into the nearest severity bucket; "
                "none: the instrument has no originator flags",
            },
        )
        data_vars[f"{var}_flag_translation_counts"] = xr.Variable(
            "N_LEVELS",
            np.array([c for _, c in level_translation[mv]]),
            {"long_name": f"originator flag samples per number for {var} (JSON)"},
        )

    # Physics derived here use only the values the rule does not hide.
    def _usable(name: str) -> np.ndarray:
        values = stacked[name].copy()
        hide = masking.flags_for(rule, name)
        for flag in (f"{name}_qc", f"{name}{MAPPED_SUFFIX}"):
            if flag in data_vars:
                values[np.isin(data_vars[flag].values, hide)] = np.nan
        return values

    # Compute potential density from stacked T, S, P
    ref_p = int(mooring_config.get("density_reference", params.DENSITY_REFERENCE))
    if (
        "temperature" in data_vars
        and "salinity" in data_vars
        and "pressure" in data_vars
    ):
        try:
            import gsw

            lat, lon = parse_latlon(mooring_config)
            T_arr = _usable("temperature")
            SP_arr = _usable("salinity")
            P_arr = _usable("pressure")
            SA = gsw.SA_from_SP(SP_arr, P_arr, lon, lat)
            CT = gsw.CT_from_t(SA, T_arr, P_arr)
            sigma_vals = potential_density_anomaly(
                SA, CT, ref_p, temperature=T_arr, pressure=P_arr
            )
            if not np.all(np.isnan(sigma_vals)):
                entry = _SIGMA_ENTRY.get(ref_p)
                vname = f"sigma{ref_p // 1000}" if entry else f"sigma_{ref_p}"
                attrs = (
                    params.cf_attrs(entry)
                    if entry
                    else {
                        "units": "kg m-3",
                        "long_name": f"Potential density anomaly referenced to {ref_p} dbar",
                    }
                )
                attrs["reference_pressure_dbar"] = ref_p
                data_vars[vname] = xr.Variable(("N_LEVELS", "time"), sigma_vals, attrs)
        except Exception as exc:  # noqa: BLE001  — gsw failure must not abort stack
            log.warning(f"  could not compute potential density: {exc}")

    # velocity_flag: element-wise worst QC flag across east/north/up velocity.
    # Velocity is stored unmasked in the stack; this combined flag is what the
    # grid step (and users) should apply before using velocity values.
    _vel_qc_keys = [
        v
        for v in ("east_velocity_qc", "north_velocity_qc", "up_velocity_qc")
        if v in data_vars
    ]
    if _vel_qc_keys:
        n_lev_v, n_t_v = (
            stacked["east_velocity_qc"].shape
            if "east_velocity_qc" in stacked
            else (0, 0)
        )
        if n_lev_v > 0:
            # int8 so flag_values (also int8) matches the variable dtype (CF).
            vel_flag = np.zeros((n_lev_v, n_t_v), dtype=np.int8)  # 0 = weakest
            for _qk in _vel_qc_keys:
                vel_flag = _worst_flag(vel_flag, data_vars[_qk].values).astype(np.int8)
            data_vars["velocity_flag"] = xr.Variable(
                ("N_LEVELS", "time"),
                vel_flag,
                {
                    "long_name": "Combined velocity QC flag (worst of east/north/up)",
                    "comment": (
                        "Worst OceanSITES flag across east/north/up_velocity; "
                        "apply to the velocity components before use."
                    ),
                    "flag_values": params.QC_FLAG_VALUES_I8,
                    "flag_meanings": params.QC_FLAG_MEANINGS,
                    "conventions": params.QC_CONVENTION,
                },
            )

    # Tilt estimated from pressure difference between two instrument levels.
    # For each level i, find the nearest level j above it that is ≥10 m away
    # in hab AND has at least some finite pressure data.  Instruments close
    # together (e.g. a microcat strapped to an Aquadopp frame) are skipped.
    #   rope_length = hab[j] - hab[i]            (m, from YAML)
    #   ΔP          = pressure[i,:] - pressure[j,:]   (dbar ≈ m, >0 when upright)
    #   tilt        = arccos(ΔP / rope_length)   (degrees from vertical; 0 = upright)
    _MIN_HAB_SEP = 10.0  # minimum hab separation (m) to use as reference
    if "pressure" in data_vars and len(habs) > 1:
        try:
            p_arr = _usable("pressure")  # (N_LEVELS, time) numpy array
            n_lev = len(habs)
            tilt_p = np.full_like(p_arr, np.nan)
            ref_hab_arr = np.full(n_lev, np.nan)  # hab of the reference level
            ref_serial_arr = np.array([""] * n_lev, dtype=object)
            # pre-compute which levels have any valid pressure
            _has_p = np.array([np.any(np.isfinite(p_arr[k, :])) for k in range(n_lev)])
            _is_adcp = [t.lower() == "adcp" for t in instr_types]
            for i in range(n_lev):
                if not _has_p[i]:
                    continue
                if _is_adcp[i]:
                    continue  # ADCP transducer pressure is not suitable for tilt
                # Find nearest level above i that is ≥_MIN_HAB_SEP away with pressure,
                # excluding ADCP levels as reference (their bin pressures shift with tilt).
                ref_j = None
                for j in range(i + 1, n_lev):
                    if float(habs[j]) - float(habs[i]) < _MIN_HAB_SEP:
                        continue
                    if not _has_p[j]:
                        continue
                    if _is_adcp[j]:
                        continue
                    ref_j = j
                    break  # first valid j = nearest ≥10 m above
                if ref_j is None:
                    continue
                rope = float(habs[ref_j]) - float(habs[i])
                ref_hab_arr[i] = float(habs[ref_j])
                ref_serial_arr[i] = str(serials[ref_j])
                delta_p = p_arr[i, :] - p_arr[ref_j, :]
                ratio = np.clip(delta_p / rope, 0.0, 1.0)
                tilt_p[i, :] = np.degrees(np.arccos(ratio))
                tilt_p[i, ~np.isfinite(delta_p)] = np.nan
                log.debug(
                    f"  tilt_from_pressure[{i}] s/n {serials[i]} ({habs[i]:.0f} m): "
                    f"ref s/n {serials[ref_j]} ({habs[ref_j]:.0f} m, "
                    f"rope={rope:.0f} m)"
                )
            data_vars["tilt_from_pressure"] = xr.Variable(
                ("N_LEVELS", "time"),
                tilt_p,
                {
                    "units": "degree",
                    "long_name": "Tilt estimated from pressure difference",
                    "comment": (
                        "arccos(ΔP / rope_length) where ΔP = pressure[i] - pressure[ref] "
                        "and rope_length = hab[ref] - hab[i] from mooring YAML; "
                        "ref is the nearest instrument ≥10 m above i with valid pressure."
                    ),
                },
            )
            data_vars["tilt_pressure_ref_hab"] = xr.Variable(
                ("N_LEVELS",),
                ref_hab_arr,
                {
                    "units": "m",
                    "long_name": "Height above bottom of the pressure reference instrument used for tilt",
                },
            )
            data_vars["tilt_pressure_ref_serial"] = xr.Variable(
                ("N_LEVELS",),
                ref_serial_arr,
                {
                    "long_name": "Serial number of the pressure reference instrument used for tilt"
                },
            )
        except Exception as exc:  # noqa: BLE001  — optional derived var; must not abort stack
            log.warning(f"  could not compute tilt_from_pressure: {exc}")

    # The instruments' sensor records, and which one each level came from.
    for name, attrs in sensor_records.items():
        if name in level_sensor:
            data_vars[name] = xr.Variable((), np.int8(0), attrs=attrs)
    if any(level_sensor):
        data_vars["sensor"] = xr.Variable(
            ("N_LEVELS",),
            np.array(level_sensor, dtype=object),
            {
                "long_name": "the sensor record (SENSOR_* variable) of the level's instrument"
            },
        )

    # Coordinate names — exclude these from scalar metadata to avoid name conflicts
    _coord_names = {"serial", "hab", "instrument_type", "instrument", "time"}

    # Add scalar metadata — collapse to 0-D when all non-NaN values are identical,
    # otherwise store as (N_LEVELS,) with one entry per instrument.
    for vname, values_list in scalar_meta.items():
        if vname in _coord_names:
            continue
        try:
            if any(isinstance(v, str) for v in values_list):
                raise TypeError("text, kept as text")  # e.g. "8080" serials
            arr = np.array(
                [v if v is not None else np.nan for v in values_list],
                dtype=np.float64,
            )
            unique_finite = np.unique(arr[np.isfinite(arr)])
            if vname == "magnetic_declination" and decl_unknown:
                # Keep one value per level so the unknown ones stay NaN, and name them.
                attrs = dict(scalar_attrs[vname])
                attrs["comment"] = (
                    "declination unknown (no mooring position at stage 3; not "
                    "applied) for: " + "; ".join(decl_unknown)
                )
                data_vars[vname] = xr.Variable(("N_LEVELS",), arr, attrs=attrs)
            elif len(unique_finite) <= 1:
                val = float(unique_finite[0]) if len(unique_finite) == 1 else np.nan
                data_vars[vname] = xr.Variable((), val, attrs=scalar_attrs[vname])
            else:
                data_vars[vname] = xr.Variable(
                    ("N_LEVELS",), arr, attrs=scalar_attrs[vname]
                )
        except (ValueError, TypeError):
            arr = np.array([str(v) if v is not None else "" for v in values_list])
            unique_vals = np.unique(arr)
            if len(unique_vals) == 1:
                data_vars[vname] = xr.Variable(
                    (), unique_vals[0], attrs=scalar_attrs[vname]
                )
            else:
                data_vars[vname] = xr.Variable(
                    ("N_LEVELS",), arr, attrs=scalar_attrs[vname]
                )

    ds_out = xr.Dataset(
        data_vars,
        coords={
            "time": xr.Variable(
                "time",
                common_time,
                {"long_name": "time", "axis": "T", "standard_name": "time"},
            ),
            "serial": xr.Variable(
                "N_LEVELS",
                np.array(serials),
                {
                    "long_name": "level key: the instrument serial, with _b01… for "
                    "an ADCP's bins and _hd for its head data"
                },
            ),
            "hab": xr.Variable(
                "N_LEVELS",
                np.array(habs),
                {"units": "m", "long_name": "height above bottom"},
            ),
            "instrument_type": xr.Variable(
                "N_LEVELS",
                np.array(instr_types),
                {"long_name": "instrument type"},
            ),
            "source_stage": xr.Variable(
                "N_LEVELS",
                np.array(
                    [int(label.removeprefix("stage") or 0) for label in stage_labels],
                    dtype=np.int8,
                ),
                {
                    "long_name": "processing stage of the instrument file the level "
                    "came from",
                    "comment": "3 normally; 2 only in a keep-going run, where an "
                    "instrument without a stage-3 file is stacked from its stage-2 "
                    "file; 0 if unknown",
                },
            ),
        },
    )
    ds_out.attrs.update(
        {
            **mooring_attrs(mooring_config),
            "deployment_time": _stage.iso_time(deploy_time),
            "recovery_time": _stage.iso_time(recover_time),
            "dt_seconds": dt_seconds,
            masking.STACK_ATTR: masking.to_json(rule),
            "stack_mask_flag_sources": masking.FLAG_SOURCES,
            # What built this stack, as YAML a description can take back.
            "deployment_settings": _mooring.settings_yaml(
                mooring_config, stack={"dt_seconds": dt_seconds}
            ),
            "Conventions": "CF-1.13",
            "history": "",
        }
    )
    # Two dated entries, one per step, so the page's processing table reads them
    # back as written (no entry separator inside a note).
    _stage.append_history(
        ds_out,
        f"stack: {n_instr} instruments placed on a {dt_seconds} s time axis (whole "
        f"multiples of {dt_seconds} s), each step taking the instrument's nearest "
        f"sample within ±{dt_seconds / 2:g} s, with no interpolation or averaging in "
        "time, steps with no sample left empty",
    )
    _stage.append_history(
        ds_out,
        f"flags: values flagged {masking.rule_words(rule)} by oceanarray or the "
        "originator hidden on the stack pages and left out of the grid "
        f"({masking.STACK_ATTR})",
    )

    # The people stage 1 cleaned for each instrument, joined without repeats. A
    # stage file whose stage 1 predates people cleaning contributes none: it is
    # named in people_not_cleaned, and concat refuses the stack.
    kept_infos = [
        info
        for i, info in enumerate(instruments)
        if i not in _omitted_set and info.get("ds") is not None
    ]
    not_cleaned = sorted(
        {
            f"{info['instrument']} s/n {paths.adcp_parent_serial(info['serial'])}"
            for info in kept_infos
            if not attribution.cleaned_at_stage1(info["ds"].attrs)
        }
    )
    ds_out.attrs.update(
        attribution.to_attrs(
            attribution.union(
                attribution.read_own(info["ds"].attrs)
                for info in kept_infos
                if attribution.cleaned_at_stage1(info["ds"].attrs)
            )
        )
    )
    # Where the data came from (stage 1's source_*): one package per mooring, so one
    # value each; a second value is kept beside the first, with a warning.
    sources = attribution.join_sources(
        [info["ds"].attrs for info in kept_infos],
        what="the stage files (one mooring is expected to come from one data package)",
    )
    ds_out.attrs.update(sources)
    attribution.apply_product_citation(
        ds_out, f"{mooring_name} stack", [sources] if sources else []
    )
    if not_cleaned:
        ds_out.attrs["people_not_cleaned"] = "; ".join(not_cleaned)
        log.warning(
            f"  {len(not_cleaned)} stage file(s) were written before stage 1 "
            f"cleaned people; their people are left out (rerun from stage 1): "
            f"{'; '.join(not_cleaned)}"
        )

    # What each imported level's originator file was, and what its originators say
    # of the deployment (a file with no originator adds nothing).
    _add_originator_levels(ds_out, kept_infos)
    ds_out.attrs.update(_originator_globals(kept_infos))

    # Beam velocities are superseded by ENU components in the stacked file.
    beam_vel_vars = [v for v in ds_out.data_vars if v.startswith("velocity_beam")]
    if beam_vel_vars:
        ds_out = ds_out.drop_vars(beam_vel_vars)
    ds_out = drop_all_zero_vars(ds_out, ["amplitude_beam", "analog_input_"])
    # OceanSITES convention: time is the unlimited (first) dimension.
    ds_out = ds_out.transpose("time", "N_LEVELS")
    ds_out = to_canonical_units(ds_out, stage="stack")
    ds_out.attrs["data_mode"] = "P"
    at_stage2 = sorted(
        {s for s, label in zip(serials, stage_labels, strict=True) if label == "stage2"}
    )
    if at_stage2:
        ds_out.attrs["stage2_instruments"] = " ".join(at_stage2)
        ds_out.attrs["stage2_reason"] = stage2_reason
        # Stage 2 has no magnetic declination: name the levels whose east/north
        # velocities are therefore referenced to magnetic north.
        if "east_velocity" in ds_out:
            magnetic = sorted(
                {
                    s
                    for i, (s, label) in enumerate(
                        zip(serials, stage_labels, strict=True)
                    )
                    if label == "stage2"
                    and np.isfinite(ds_out["east_velocity"].isel(N_LEVELS=i)).any()
                }
            )
            if magnetic:
                ds_out.attrs["stage2_magnetic_north"] = " ".join(magnetic)
        # Current meters whose stage-2 velocities are in instrument coordinates
        # (beam or XYZ): stage 3 rotates them to east/north, so the stack has none.
        if "instrument_type" in ds_out:
            has_enu = "east_velocity" in ds_out
            no_velocity = sorted(
                {
                    s
                    for i, (s, label, itype) in enumerate(
                        zip(
                            serials,
                            stage_labels,
                            ds_out["instrument_type"].values,
                            strict=True,
                        )
                    )
                    if label == "stage2"
                    and str(itype).lower() in ("aquadopp", "adcp")
                    and not (
                        has_enu
                        and np.isfinite(ds_out["east_velocity"].isel(N_LEVELS=i)).any()
                    )
                }
            )
            if no_velocity:
                ds_out.attrs["stage2_no_velocity"] = " ".join(no_velocity)
        log.warning(f"  stacked from stage 2 ({stage2_reason}): {' '.join(at_stage2)}")
    if excluded:
        ds_out.attrs["instruments_excluded"] = "; ".join(excluded)
    if slow:
        # Recorded once here, so the grid (which reads this file) names the same
        # instruments without re-detecting them.
        ds_out.attrs["instruments_slower_than_step"] = "; ".join(slow)
    if omitted:
        # Record the omission in the file so the stack report and the grid
        # (which reads this file) inherit it, and summarise it loudly.
        ds_out.attrs["instruments_omitted"] = "; ".join(omitted)
        log.warning(
            f"  {len(omitted)} instrument(s) omitted from the stack "
            f"(recorded in instruments_omitted):\n"
            + "\n".join(f"    - {line}" for line in omitted)
        )
    return as_stored(ds_out)


def mooring_attrs(mooring_config: Mapping) -> dict[str, Any]:
    """Return the mooring-level attributes a stack copies from its description.

    Parameters
    ----------
    mooring_config : Mapping
        The loaded mooring description.

    Returns
    -------
    dict of str to Any
        ``mooring_name``, ``waterdepth`` (m), ``latitude`` and ``longitude`` (decimal
        degrees; the best position the description gives: seabed, deployment,
        planned, then the bare pair), each when the description gives it, and each
        ``<field>_source`` it states.
    """
    lat, lon, source = parse_latlon_with_source(mooring_config)
    position = (
        {} if is_unknown_latlon_source(source) else {"latitude": lat, "longitude": lon}
    )
    waterdepth = mooring_config.get("waterdepth")
    return {
        "mooring_name": str(mooring_config.get("name", "")),
        **({} if waterdepth is None else {"waterdepth": float(waterdepth)}),
        **position,
        **{
            f"{field}_source": str(mooring_config[f"{field}_source"])
            for field in _mooring.SOURCE_FIELDS
            if mooring_config.get(f"{field}_source")
        },
    }


def _read_best_stage(
    mooring: dict, proc_dir: Path
) -> tuple[dict[str, xr.Dataset], list[str]]:
    """Read each stackable instrument at stage 3, falling back to stage 2."""
    name = mooring["name"]
    datasets: dict[str, xr.Dataset] = {}
    omitted: list[str] = []
    for entry in instrument_entries(mooring):
        if not isinstance(entry, dict):
            continue
        if entry.get("stack", True) is False or entry.get("hab") is None:
            continue
        serial = safe_serial(entry.get("serial", ""))
        instr_type = entry.get("instrument", "unknown")
        for stage_n in (3, 2):
            path = paths.stage_file(proc_dir, name, instr_type, serial, stage_n)
            if not path.exists():
                continue
            try:
                ds = read_mooring(
                    mooring, stage=stage_n, serials=[serial], mooring_dir=proc_dir
                )[serial]
                datasets[serial] = ds.load()
            except Exception as e:  # noqa: BLE001  — one bad file must not abort the stack
                reason = f"could not load {path.name}: {e}"
                log.warning(f"  omitting {instr_type} s/n {serial} — {reason}")
                omitted.append(f"{instr_type} s/n {serial} ({reason})")
            break
    return datasets, omitted


#: Settings blocks that do not change the stack: the grid's own, and which report
#: sections are drawn. A change there leaves the stack current.
_NOT_STACK_SETTINGS = frozenset({"grid", "report_sections"})


def _stack_settings(text: str) -> dict:
    """Return the recorded settings that the stack depends on (no grid, no mask flags)."""
    out = {
        k: v
        for k, v in _mooring.settings_from_yaml(text).items()
        if k not in _NOT_STACK_SETTINGS
    }
    if isinstance(out.get("stack"), Mapping):
        out["stack"] = {k: v for k, v in out["stack"].items() if k != "mask_flags"}
    return out


def _relative(path: Path, mooring_dir: Path) -> str:
    """Return *path* relative to *mooring_dir* (POSIX), or its name when outside it."""
    try:
        return Path(path).resolve().relative_to(Path(mooring_dir).resolve()).as_posix()
    except ValueError:
        return Path(path).name


def stack_stale_reason(
    stack_path: Path,
    inputs: Mapping[str, Path],
    mooring: Mapping,
    mooring_dir: Path,
    *,
    dt_seconds: int,
    mask_flags: Iterable[int] | Mapping[str, Iterable[int]] | None = None,
) -> str | None:
    """Return why the stack at *stack_path* must be rebuilt, or None when it is current.

    The stack is current when the stage files it would be built from are the ones it
    records (same files, same SHA-256), the description's settings that shape it are
    the ones it records (``deployment_settings`` without the grid's settings and
    ``mask_flags``), its mooring-level attributes and deployment window match the
    description, and the same oceanarray release built it (the commit is ignored).

    Parameters
    ----------
    stack_path : pathlib.Path
        The existing ``<name>_stack.nc``.
    inputs : Mapping of str to pathlib.Path
        The stage file each instrument would be stacked from, keyed by serial.
    mooring : Mapping
        The loaded mooring description.
    mooring_dir : pathlib.Path
        The mooring's processing directory (stage-file paths are recorded relative
        to it).
    dt_seconds : int
        The time step the new stack would use.
    mask_flags : iterable of int, or Mapping, optional
        The rule the call gives (None: the description's or the default); a
        different rule from the one recorded rebuilds the stack.

    Returns
    -------
    str or None
        The first reason found, in words, or None.
    """
    if not stack_path.exists():
        return "no stack yet"
    with xr.open_dataset(stack_path, engine="netcdf4") as old:
        attrs = dict(old.attrs)
        old_records = list(sensors.records(old).values())
        recorded = (
            {
                str(f): str(h)
                for f, h in zip(
                    old["stage_file"].values, old["stage_sha256"].values, strict=True
                )
                if str(f)
            }
            if "stage_sha256" in old.variables and "stage_file" in old.variables
            else None
        )
        if recorded is not None:
            for item in str(attrs.get("stage_files_not_stacked", "")).split("; "):
                if " " in item:
                    file, digest = item.rsplit(" ", 1)
                    recorded[file] = digest
    if (reason := provenance.release_change_reason(attrs)) is not None:
        return reason
    if recorded is None:
        return "it predates recorded stage-file hashes"
    current = {
        _relative(path, mooring_dir): provenance.sha256(path)
        for path in inputs.values()
    }
    added = sorted(set(current) - set(recorded))
    gone = sorted(set(recorded) - set(current))
    changed = sorted(f for f in current if f in recorded and current[f] != recorded[f])
    for label, files in (
        ("stage file changed", changed),
        ("new stage file", added),
        ("stage file no longer used", gone),
    ):
        if files:
            return f"{label}{'s' if len(files) > 1 else ''}: {', '.join(files)}"
    expected = {
        **mooring_attrs(mooring),
        "deployment_time": _stage.iso_time(mooring["deployment_time"]),
        "recovery_time": _stage.iso_time(mooring["recovery_time"]),
    }
    for k in attrs:
        # A <field>_source the stack holds but the description no longer states.
        if k.endswith("_source") and k[:-7] in _mooring.SOURCE_FIELDS:
            expected.setdefault(k, "")
    if (key := provenance.changed_attr(attrs, expected)) is not None:
        return f"the description's {key} changed"
    want = _stack_settings(
        _mooring.settings_yaml(mooring, stack={"dt_seconds": dt_seconds})
    )
    if _stack_settings(str(attrs.get("deployment_settings", ""))) != want:
        return "the description's settings for the stack changed"
    if attrs.get(masking.STACK_ATTR) != masking.to_json(
        masking.stack_rule(mooring, mask_flags)
    ):
        return "mask_flags changed"
    # Stage 1 applies people:, source: and the sensor catalog, so a new stack from
    # the same stage files would carry the old values; only rerunning stage 1
    # brings them in.
    changed_inputs = []
    if attrs.get(PEOPLE_SOURCE_ATTR, "") != people_source_sha256(mooring):
        changed_inputs.append("people:/source:")
    if attrs.get(CATALOG_ATTR, "") != catalog.digest(mooring, old_records):
        changed_inputs.append("what its sensor catalog says of these instruments")
    if changed_inputs:
        log.warning(
            f"{mooring.get('name', '')}: the description's "
            f"{' and '.join(changed_inputs)} changed after stage 1 wrote the stage "
            "files; rerun with --force to bring them into the products"
        )
    return None


#: The stack attribute holding the SHA-256 of the description's ``people:`` and
#: ``source:`` blocks (absent when the description has neither).
PEOPLE_SOURCE_ATTR = "people_source_sha256"

#: The stack attribute holding the SHA-256 of what the sensor catalog says of the
#: stack's own instruments (absent when the description names no catalog).
CATALOG_ATTR = "sensor_catalog_sha256"


def people_source_sha256(mooring: Mapping) -> str:
    """Return the SHA-256 of the description's ``people:`` and ``source:`` blocks.

    Parameters
    ----------
    mooring : Mapping
        The loaded mooring description.

    Returns
    -------
    str
        The hex digest of the two blocks as sorted JSON, or ``""`` when the
        description has neither.
    """
    import hashlib
    import json

    blocks = {k: mooring[k] for k in ("people", "source") if mooring.get(k)}
    if not blocks:
        return ""
    text = json.dumps(blocks, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


#: Per level, what an imported level's originator file was: which file, which
#: release of it, its own sampling and processing level, and the magnetic
#: declination its originator applied to the east/north velocities.
ORIGINATOR_LEVEL_ATTRS: tuple[str, ...] = tuple(
    f"originator_{key}"
    for key in (
        "file",
        "sha256",
        "id",
        "date_created",
        "date_modified",
        "time_coverage_resolution",
        "processing_level",
        "QC_indicator",
        "magnetic_declination",
    )
)

#: CF ``comment`` for the per-level variables of :data:`ORIGINATOR_LEVEL_ATTRS`
#: that need one.
_ORIGINATOR_LEVEL_COMMENTS: dict[str, str] = {
    "originator_magnetic_declination": (
        "declination (degrees east) or a note, as the originator's file gives it; "
        "set when the originator rotated the east/north velocities to true north, "
        "so oceanarray did not apply magnetic_declination; empty for other levels"
    ),
}

#: Per deployment, what the originators say of it: one value when every imported
#: instrument agrees, else the distinct values joined.
ORIGINATOR_DEPLOYMENT_ATTRS: tuple[str, ...] = tuple(
    f"originator_{key}"
    for key in (
        "title",
        "summary",
        "institution",
        "project",
        "array",
        "network",
        "platform_code",
        "site_code",
        "publisher_name",
        "publisher_email",
        "publisher_url",
        "data_assembly_center",
    )
)


def _add_originator_levels(ds: xr.Dataset, infos: Sequence[Mapping]) -> None:
    """Write :data:`ORIGINATOR_LEVEL_ATTRS` per level, in place (an ADCP's bins share).

    A level with no originator gets ``""``; a name no level has is not written.

    Parameters
    ----------
    ds : xarray.Dataset
        The stack, with a ``serial`` coordinate on ``N_LEVELS``; changed in place.
    infos : sequence of mapping
        One entry per stacked instrument, each with its ``"serial"`` (str) and its
        stage file as ``"ds"`` (xarray.Dataset), whose attributes are read.
    """
    by_serial = {
        paths.adcp_parent_serial(str(info["serial"])): info["ds"].attrs
        for info in infos
    }
    levels = [
        by_serial.get(paths.adcp_parent_serial(str(s)), {}) for s in ds["serial"].values
    ]
    for key in ORIGINATOR_LEVEL_ATTRS:
        values = [str(attrs.get(key, "") or "") for attrs in levels]
        if any(values):
            attrs = {
                "long_name": f"{key.removeprefix('originator_').replace('_', ' ')} "
                "of the originator's file the level was read from"
            }
            if key in _ORIGINATOR_LEVEL_COMMENTS:
                attrs["comment"] = _ORIGINATOR_LEVEL_COMMENTS[key]
            ds[key] = xr.Variable(("N_LEVELS",), np.array(values, dtype=object), attrs)


def _originator_globals(infos: Sequence[Mapping]) -> dict[str, str]:
    """Return :data:`ORIGINATOR_DEPLOYMENT_ATTRS`: one value each, joined (with a WARNING) if they differ."""
    return attribution.join_values(
        [info["ds"].attrs for info in infos],
        ORIGINATOR_DEPLOYMENT_ATTRS,
        what="the imported files",
        warn=ORIGINATOR_DEPLOYMENT_ATTRS,
    )


def _add_stage_files(
    ds: xr.Dataset, inputs: Mapping[str, Path], mooring_dir: Path
) -> xr.Dataset:
    """Record each level's stage file and its SHA-256 (an ADCP's bins share their file).

    Stage files the stack read but could not stack (an omitted instrument) are
    recorded in ``stage_files_not_stacked``, so an unchanged mooring stays current.
    """
    hashes = {serial: provenance.sha256(path) for serial, path in inputs.items()}
    stacked = {paths.adcp_parent_serial(str(v)) for v in ds["serial"].values} | {
        str(v) for v in ds["serial"].values
    }
    left_out = sorted(
        f"{_relative(path, mooring_dir)} {hashes[serial]}"
        for serial, path in inputs.items()
        if serial not in stacked
    )
    if left_out:
        ds.attrs["stage_files_not_stacked"] = "; ".join(left_out)
    files, digests = [], []
    for serial in ds["serial"].values:
        parent = paths.adcp_parent_serial(str(serial))
        key = str(serial) if str(serial) in inputs else parent
        path = inputs.get(key)
        files.append(_relative(path, mooring_dir) if path is not None else "")
        digests.append(hashes.get(key, ""))
    ds["stage_file"] = xr.Variable(
        ("N_LEVELS",),
        np.array(files, dtype=object),
        {
            "long_name": "stage file the level was stacked from, relative to the mooring directory"
        },
    )
    ds["stage_sha256"] = xr.Variable(
        ("N_LEVELS",),
        np.array(digests, dtype=object),
        {"long_name": "SHA-256 of the stage file"},
    )
    return ds


def _stack_file(
    mooring: dict,
    mooring_dir: Path,
    *,
    dt_seconds: int | None = None,
    keep_going: bool = False,
    failed: Mapping[str, str] | None = None,
    from_stage2: bool = False,
    stage2_reason: str | None = None,
    failures: Sequence[str] = (),
    force: bool = True,
    mask_flags: Iterable[int] | Mapping[str, Iterable[int]] | None = None,
) -> tuple[Path, xr.Dataset]:
    """Stack a mooring's stage-3 files, write the stack; return its path and dataset.

    With ``force=False`` an existing stack that is still current
    (:func:`stack_stale_reason`) is kept and returned as read; otherwise the stack is
    rebuilt and its history says why.
    """
    name = mooring["name"]
    failed = dict(failed or {})
    # from_stage2: stage 3 failed as a whole this run (any stage-3 file on disk is
    # older than it), or a stage-2 quick look (run(stack_stage=2)). keep_going
    # falls back to stage 2 per instrument; source_stage records which was used.
    if from_stage2:
        stages: tuple[int, ...] = (2,)
    else:
        stages = (3, 2) if keep_going else (3,)
    inputs: dict[str, tuple[int, Path]] = {}
    missing: list[str] = []
    omitted: list[str] = []
    for entry in instrument_entries(mooring):
        if not isinstance(entry, dict) or entry.get("skip"):
            continue
        if entry.get("stack", True) is False or entry.get("hab") is None:
            continue
        serial = safe_serial(entry.get("serial", ""))
        instr_type = entry.get("instrument", "unknown")
        # Failed in stages 1-3 this run: its files on disk predate the run.
        if serial in failed:
            omitted.append(
                f"{instr_type} s/n {serial} ({failed[serial]} this run; its older "
                "files are not used)"
            )
            continue
        found = [
            (n, path)
            for n in stages
            if (
                path := paths.stage_file(mooring_dir, name, instr_type, serial, n)
            ).exists()
        ]
        if not found:
            missing.append(f"{instr_type} s/n {serial}")
            continue
        inputs[serial] = found[0]
    if missing and not keep_going:
        raise MooringError(
            f"{name}: no stage-3 file for {', '.join(missing)}. Run stage 3, mark "
            "the instrument 'skip: true', or use keep_going=True to stack what exists."
        )
    out_path = paths.product_file(mooring_dir, name, "stack")
    stage_paths = {serial: path for serial, (_n, path) in inputs.items()}
    if force:
        reason = "rebuild forced"
    else:
        from oceanarray.pipeline._run import resolve_dt_seconds

        reason = stack_stale_reason(
            out_path,
            stage_paths,
            mooring,
            mooring_dir,
            dt_seconds=resolve_dt_seconds(mooring, dt_seconds),
            mask_flags=mask_flags,
        )
        if reason is None and not failures:
            log.info("%s: stack current (%s)", name, out_path.name)
            with xr.open_dataset(out_path, engine="netcdf4") as old:
                return out_path, old.load()
    datasets: dict[str, xr.Dataset] = {}
    for serial, (stage_n, path) in inputs.items():
        try:
            datasets[serial] = read_mooring(
                mooring, stage=stage_n, serials=[serial], mooring_dir=mooring_dir
            )[serial].load()
        except Exception as e:
            if not keep_going:
                raise MooringError(f"{name}: could not read {path.name}: {e}") from e
            omitted.append(
                f"{_instr_type_of(mooring, serial)} s/n {serial} "
                f"(could not load {path.name}: {e})"
            )
    omitted += [
        f"{m} (no stage-{'2' if from_stage2 else '2 or stage-3'} file)" for m in missing
    ]
    ds = stack(
        datasets,
        mooring,
        dt_seconds=dt_seconds,
        omitted=omitted,
        stage2_reason=stage2_reason or "no stage-3 file",
        mask_flags=mask_flags,
    )
    # A file that could not be read is not recorded: the next run tries it again.
    ds = _add_stage_files(
        ds, {s: p for s, p in stage_paths.items() if s in datasets}, mooring_dir
    )
    if digest := people_source_sha256(mooring):
        ds.attrs[PEOPLE_SOURCE_ATTR] = digest
    if digest := catalog.digest(mooring, list(sensors.records(ds).values())):
        ds.attrs[CATALOG_ATTR] = digest
    why = reason or ("stage processing failed" if failures else "unknown")
    _stage.append_history(ds, f"stack rebuilt: {why}")
    if failures:
        # The grid inherits this attribute from the stack.
        ds.attrs["keep_going_failures"] = "; ".join(failures)
    return write(ds, out_path, overwrite=True), ds


def _instr_type_of(mooring: Mapping, serial: str) -> str:
    """Return the ``instrument`` of the entry whose cleaned serial is *serial*."""
    for entry in instrument_entries(mooring):
        if isinstance(entry, dict) and safe_serial(entry.get("serial", "")) == serial:
            return str(entry.get("instrument", "unknown"))
    return "unknown"


class MooringStacker:
    """Step 1: stack all instruments onto a common time axis → ``_stack.nc``.

    .. deprecated:: 0.6.0
        Removed in 0.7.0; see the migration page.
    """

    def __init__(
        self,
        *,
        proc_dir: str,
    ) -> None:
        """Resample all instruments from a mooring onto a common time axis.

        Reads ``{mooring}_{serial}_stage3.nc`` (or ``_stage2.nc`` as fallback) for
        every instrument listed in the mooring YAML, takes each one's nearest
        sample at every step of a common time axis, and writes a single multi-instrument file
        ``{mooring}_stack.nc`` in the mooring proc directory.

        Parameters
        ----------
        proc_dir : str
            Cruise-level processed output directory. The pipeline appends
            ``/{mooring}/`` internally (see :func:`oceanarray.paths.mooring_proc_dir`).

        """
        from oceanarray import _deprecated

        _deprecated.warn("oceanarray.pipeline.stack.MooringStacker")
        self._proc_dir = Path(proc_dir)

    def _resolve_proc_dir(self, mooring_name: str) -> Path:
        """Return the mooring-level proc directory."""
        return paths.mooring_proc_dir(self._proc_dir, mooring_name)

    def _rel(self, path: Path) -> str:
        """Return a short display path relative to proc_dir."""
        if self._proc_dir:
            try:
                return str(path.relative_to(self._proc_dir))
            except ValueError:
                pass
        return path.name

    def stack(
        self,
        mooring_name: str,
        dt_seconds: int | None = None,
        force: bool = False,
    ) -> bool:
        """Stack all processed instruments for *mooring_name* onto a common time grid.

        Reads ``_stage3.nc`` (falling back to ``_stage2.nc``) for every instrument
        listed in the mooring YAML, takes each one's nearest sample at every step of
        *dt_seconds* (``None``: the YAML's ``stack: dt_seconds``, else the default),
        and writes ``{mooring}_stack.nc`` under the mooring proc directory.

        Returns True on success; False if no instruments could be stacked, or if any
        instrument was omitted (the partial stack is still written).
        """
        proc_dir = self._resolve_proc_dir(mooring_name)
        try:
            proc_dir_exists = proc_dir.exists()
        except (TimeoutError, OSError) as exc:
            log.warning(
                f"Cannot access data drive — is it connected?\n"
                f"       Path: {proc_dir}\n"
                f"       ({type(exc).__name__}: {exc})"
            )
            return False

        if not proc_dir_exists:
            log.warning(f"Processing directory not found: {proc_dir}")
            return False

        output_path = paths.product_file(proc_dir, mooring_name, "stack")
        if output_path.exists() and not force:
            logger.status("skip", self._rel(output_path))
            return True

        config_file = proc_dir / f"{mooring_name}.mooring.yaml"
        if not config_file.exists():
            log.warning(f"Config not found: {config_file}")
            return False

        mooring_config = _mooring.load(config_file)
        datasets, read_omitted = _read_best_stage(mooring_config, proc_dir)
        try:
            ds_out = stack(
                datasets, mooring_config, dt_seconds=dt_seconds, omitted=read_omitted
            )
        except MooringError as e:
            if "deployment_time" in str(e):
                raise
            log.warning(f"{e}")
            return False
        write(ds_out, output_path, overwrite=True)
        logger.status("file", self._rel(output_path))
        # Return False when anything was omitted so the CLI exits 1 ("something
        # failed") even though the partial stack file was written; process() only
        # skips the grid stage when no stack file exists, so the grid still runs.
        return "instruments_omitted" not in ds_out.attrs
