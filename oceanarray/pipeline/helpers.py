"""Internal helper functions for mooring-level stack and grid operations."""

from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.paths import safe_serial, stage_file

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

# Variables passed through without QC masking at the stack step.
# Velocity and orientation are kept at full precision so downstream users
# (grid step, analysis) can apply their own masking via velocity_flag.
# QC flag arrays never need masking (there is no companion *_qc_qc* variable).
_STACK_RAW: frozenset = frozenset(
    {
        "east_velocity",
        "north_velocity",
        "up_velocity",
        "current_speed",
        "current_direction",
        "velocity_beam1",
        "velocity_beam2",
        "velocity_beam3",
        "heading",
        "pitch",
        "roll",
        "east_velocity_qc",
        "north_velocity_qc",
        "up_velocity_qc",
        "pitch_qc",
        "roll_qc",
        "percent_good_qc",
        "error_velocity_qc",
        "seabed_qc",
    }
)


def _safe_serial(serial: Any) -> str:
    """Return a filename-safe serial token (see :func:`oceanarray.paths.safe_serial`)."""
    return safe_serial(serial)


def _apply_qc_mask(src_v: np.ndarray, ds: "xr.Dataset", vname: str) -> np.ndarray:
    """Return src_v with flagged values replaced by NaN.

    Flags kept as valid:
      0 (no QC performed), 1 (good), 2 (probably good)
      + flag 8 (interpolated) for pressure only.
    Flags masked (→ NaN):
      3 (suspect), 4 (bad), 9 (missing value).
    If no companion *_qc* variable exists the array is returned unchanged.
    """
    qc_name = f"{vname}_qc"
    if qc_name not in ds.data_vars:
        return src_v
    qc = ds[qc_name].values
    keep = np.isin(qc, [0, 1, 2, 8]) if vname == "pressure" else np.isin(qc, [0, 1, 2])
    out = src_v.copy()
    out[~keep] = np.nan
    return out


def _worst_flag(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Element-wise worst QC flag on the OceanSITES priority ordering.

    Worst-wins using ``parameters.QC_MERGE_PRIORITY`` (weakest to strongest:
    ``unknown(0) < good(1) < probably_good(2) < nominal(7) < interpolated(8)
    < potentially_correctable_bad(3) < bad(4) < missing(9)``) — the same table
    ``instrument.qc._merge_flags`` uses, so the two merge paths cannot drift.
    NaN inputs are treated as flag 9 (missing value) so that levels with no
    data are never silently promoted to flag 0 ("unknown").
    """
    # rank[flag_value] gives priority; higher rank = worse flag (shared LUT).
    _rank = params.QC_MERGE_PRIORITY_LUT
    a = np.where(np.isfinite(a), a, 9.0)
    b = np.where(np.isfinite(b), b, 9.0)
    ai = np.clip(np.round(a).astype(np.int8), 0, 9)
    bi = np.clip(np.round(b).astype(np.int8), 0, 9)
    take_b = _rank[bi] > _rank[ai]
    out = ai.copy()
    out[take_b] = bi[take_b]
    return out


def _times_to_float(t: np.ndarray) -> np.ndarray:
    return t.astype("datetime64[ns]").astype(np.float64)


def _best_nc(
    proc_dir: Path, instr_type: str, mooring_name: str, serial: str
) -> Path | None:
    """Return best available stage3 or stage2 file for one instrument; None if only stage1."""
    for stage in (3, 2):
        p = stage_file(proc_dir, mooring_name, instr_type, serial, stage)
        if p.exists():
            return p
    return None


def _detect_interval_s(time_vals: np.ndarray) -> float:
    if len(time_vals) < 2:
        return 60.0
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


def _make_adcp_head_ds(ds_parent: xr.Dataset) -> xr.Dataset:
    """Create a 1-D (time-only) view of the ADCP transducer head for stack insertion.

    The ADCP transducer head is treated as a distinct instrument position in the mooring
    stack.  It carries instrument-diagnostic variables that describe the sensor, not the
    water column:

    - ``temperature`` (°C) — internal electronics temperature; useful for diagnosing
      warm-up drift but not a reliable water temperature measurement.
    - ``heading``, ``pitch``, ``roll`` (°) — instrument orientation.
    - ``pressure`` (dbar) — pressure at the transducer face, i.e. the instrument depth.

    Per-bin velocity and ``bin_pressure`` are deliberately excluded.  Each velocity bin
    gets its own stack entry at the pressure appropriate to that bin
    (see ``_make_adcp_bin_ds``), rather than the transducer pressure.

    Parameters
    ----------
    ds_parent : xr.Dataset
        Full ADCP stage-3 dataset, including 2-D ``(time, N_BINS)`` variables.

    Returns
    -------
    xr.Dataset
        Dataset containing only time-series (1-D) variables from ``_ADCP_HEAD_VARS``
        plus the transducer ``pressure``.  Suitable for merging into a point-instrument
        stack.

    """
    keep = [
        v
        for v in ds_parent.data_vars
        if v in _ADCP_HEAD_VARS and ds_parent[v].dims == ("time",)
    ]
    # Also keep the instrument-head pressure if present (1-D, not bin_pressure)
    if (
        "pressure" in ds_parent.data_vars
        and ds_parent["pressure"].dims == ("time",)
        and "pressure" not in keep
    ):
        keep.append("pressure")
    return ds_parent[keep]


def _make_adcp_bin_ds(ds_parent: xr.Dataset, bin_idx: int) -> xr.Dataset:
    """Create a 1-D (time-only) point-instrument view of one ADCP range bin.

    Slices the parent dataset at *bin_idx* along ``params.ADCP_BIN_DIM`` and replaces the
    transducer ``pressure`` with ``bin_pressure[:, bin_idx]`` so that this entry sits at
    the correct depth in the stack.  Computes derived scalar velocity quantities::

        current_speed      = sqrt(east_velocity² + north_velocity²)  [m s⁻¹, always ≥ 0]
        current_direction  = atan2(east, north) mod 360               [degrees, 0 = N CW]

    **Direction convention**: the direction *toward which* the water flows, clockwise
    from True North.  This is the oceanographic convention (opposite to meteorological
    "wind from").  0° = northward flow, 90° = eastward flow.

    Instrument-head variables (``temperature``, ``heading``, ``pitch``, ``roll`` — see
    ``_ADCP_HEAD_VARS``) are dropped because they belong to the transducer location, not
    to an individual velocity bin.  Variables retaining non-time dimensions (e.g.
    beam-dimension arrays ``amplitude``, ``correlation``, ``percent_good``) are also
    dropped; they cannot be represented as point-instrument time series without
    ambiguity about which beam to keep.

    The result is compatible with ``_nearest_subsample`` and ``_linear_interp`` because
    every remaining data variable has dimension ``("time",)`` or is scalar.

    Parameters
    ----------
    ds_parent : xr.Dataset
        Full ADCP stage-3 dataset.
    bin_idx : int
        Zero-based index along the bin dimension (``params.ADCP_BIN_DIM``).  Index 0 is
        the bin nearest the transducer face.

    Returns
    -------
    xr.Dataset
        Single-bin time-series dataset with ``pressure`` set to ``bin_pressure``
        at this bin and ``current_speed`` / ``current_direction`` added.

    """
    bin_dim = params.ADCP_BIN_DIM
    ds_bin = ds_parent.isel({bin_dim: bin_idx}, drop=True)

    # Drop head-only variables — these belong in the separate head entry
    head_to_drop = [v for v in _ADCP_HEAD_VARS if v in ds_bin.data_vars]
    if head_to_drop:
        ds_bin = ds_bin.drop_vars(head_to_drop)

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
        ds_bin = ds_bin.assign(
            current_speed=xr.Variable(
                "time",
                np.hypot(e, n).astype(np.float32),
                {"units": "m s-1", "long_name": "Current speed"},
            ),
            current_direction=xr.Variable(
                "time",
                (np.degrees(np.arctan2(e, n)) % 360.0).astype(np.float32),
                {
                    "units": "degrees",
                    "long_name": "Current direction (oceanographic, 0=N clockwise)",
                },
            ),
        )

    to_drop = [v for v in ds_bin.data_vars if any(d != "time" for d in ds_bin[v].dims)]
    if to_drop:
        ds_bin = ds_bin.drop_vars(to_drop)

    return ds_bin


def _nearest_subsample(
    ds: xr.Dataset,
    common_time: np.ndarray,
    half_window_s: float,
) -> dict[str, np.ndarray]:
    """Return nearest-neighbour values on common_time within ±half_window_s seconds.

    Returns a dict {varname: array} with NaN where no sample falls within the window.
    """
    src_t = _times_to_float(ds["time"].values)
    tgt_t = _times_to_float(common_time)
    n = len(common_time)
    half_ns = half_window_s * 1e9  # ns

    idx = np.searchsorted(src_t, tgt_t)
    result: dict[str, np.ndarray] = {}
    for vname in STACK_VARS:
        if vname not in ds.data_vars:
            result[vname] = np.full(n, np.nan)
            continue
        src_v = ds[vname].values.astype(np.float64)
        if vname not in _STACK_RAW:
            src_v = _apply_qc_mask(src_v, ds, vname)
        out = np.full(n, np.nan)
        for i, (t_tgt, k) in enumerate(zip(tgt_t, idx, strict=False)):
            # Check candidates at k-1 and k
            best_dt = np.inf
            best_v = np.nan
            for j in [k - 1, k]:
                if 0 <= j < len(src_t):
                    dt = abs(src_t[j] - t_tgt)
                    if dt < best_dt and dt <= half_ns:
                        best_dt = dt
                        best_v = src_v[j]
            out[i] = best_v
        result[vname] = out
    return result


def _linear_interp(
    ds: xr.Dataset,
    common_time: np.ndarray,
) -> dict[str, np.ndarray]:
    """Linearly interpolate all stack variables onto common_time; NaN outside data range."""
    src_t = _times_to_float(ds["time"].values)
    tgt_t = _times_to_float(common_time)
    result: dict[str, np.ndarray] = {}
    for vname in STACK_VARS:
        if vname not in ds.data_vars:
            result[vname] = np.full(len(common_time), np.nan)
            continue
        src_v = ds[vname].values.astype(np.float64)
        if vname not in _STACK_RAW:
            src_v = _apply_qc_mask(src_v, ds, vname)
        valid = np.isfinite(src_v)
        if valid.sum() < 2:
            result[vname] = np.full(len(common_time), np.nan)
            continue
        if vname.endswith("_qc"):
            # QC flag arrays must not be linearly interpolated (a flag of 2.5 is
            # meaningless). Resample conservatively: each target inherits the
            # *worst* flag of its two bracketing source samples on the OceanSITES
            # priority (see _worst_flag), so a bad flag adjacent to a target is
            # never laundered into a good one. A target coinciding exactly with a
            # source sample takes that sample's flag alone (left == right);
            # out-of-range targets take the nearest edge flag.
            src_t_v = src_t[valid]
            src_v_v = src_v[valid]
            n = len(src_t_v)
            left = np.clip(np.searchsorted(src_t_v, tgt_t, side="right") - 1, 0, n - 1)
            right = np.clip(np.searchsorted(src_t_v, tgt_t, side="left"), 0, n - 1)
            result[vname] = _worst_flag(src_v_v[left], src_v_v[right]).astype(
                np.float64
            )
        else:
            result[vname] = np.interp(
                tgt_t, src_t[valid], src_v[valid], left=np.nan, right=np.nan
            )
    return result
