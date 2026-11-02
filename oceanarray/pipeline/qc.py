"""QARTOD QC tests and CTD derivations for stage 3."""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.pipeline.units import UnitsError

# Merge priority for combining QC flags (higher priority wins = worse quality).
# Single source of truth: parameters.QC_MERGE_PRIORITY, shared with
# mooring.helpers._worst_flag so the two merge paths cannot drift.
_QC_PRIORITY: dict[int, int] = params.QC_MERGE_PRIORITY

# Velocity variable names in both beam and ENU coordinate systems.
_VELOCITY_VARS = (
    "velocity_beam1",
    "velocity_beam2",
    "velocity_beam3",
    "east_velocity",
    "north_velocity",
    "up_velocity",
)


def _merge_flags(*flag_arrays: np.ndarray) -> np.ndarray:
    """Merge multiple int8 flag arrays using priority ordering.

    Returns element-wise flag with highest priority (worst quality).
    Works on arrays of any shape (1-D time series or 2-D time×N_BINS).
    """
    # priority[flag] for flags 0-9, from the shared single-source LUT.
    _priority = params.QC_MERGE_PRIORITY_LUT
    result = np.asarray(flag_arrays[0], dtype=np.int8).copy()
    for fa in flag_arrays[1:]:
        fa = np.asarray(fa, dtype=np.int8)
        replace = _priority[fa.clip(0, 9)] > _priority[result.clip(0, 9)]
        result = np.where(replace, fa, result).astype(np.int8)
    return result


def _ingest_qartod(result: Any) -> np.ndarray:
    """Normalise one ioos_qc test result onto the OceanSITES flag table.

    Folds the three steps every QARTOD test needs into one place: fill missing
    entries with 9 (handling both ioos_qc return types — a masked array or a
    plain ndarray, depending on version), cast to int8, and remap QARTOD
    ``UNKNOWN(2)`` — "could not evaluate", e.g. ``spike_test`` at a record edge or
    beside a gap — to OceanSITES ``unknown(0)``. Applied *before* merging, so a
    point a test could not evaluate never asserts ``probably_good_data(2)``.
    """
    if hasattr(result, "filled"):
        flags = result.filled(9)
    else:
        arr = np.asarray(result, dtype=float)
        flags = np.where(np.isnan(arr), 9, arr)
    flags = np.asarray(flags).astype(np.int8)
    # np.where over an int8 array and an int8 scalar is already int8.
    return np.where(flags == 2, np.int8(0), flags)


def set_qc_attrs(
    ds: xr.Dataset, var: str, extra: dict[str, Any] | None = None
) -> xr.Dataset:
    """Attach the OceanSITES flag attributes to ``{var}_qc`` in place.

    The single owner of QC-flag metadata: call this wherever a ``_qc`` variable
    is created or updated so the CF flag attributes cannot be forgotten. The flag
    table is sourced from :mod:`oceanarray.config.parameters` (one source of truth) and
    the full legal set is declared, not only the values present. The status-flag
    ``standard_name`` (``"{parent} status_flag"``) is attached only when the parent
    variable has a ``standard_name`` — it is skipped, never fabricated, when the
    parent has none (``standard_name`` is an optional CF attribute).

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset containing both ``var`` and its ``{var}_qc`` companion.
    var : str
        Name of the data variable whose ``_qc`` companion is annotated.
    extra : dict of str to Any, optional
        Additional attributes to attach (e.g. QC threshold provenance).

    Returns
    -------
    xarray.Dataset
        *ds*, modified in place.

    """
    attrs: dict[str, Any] = {
        "flag_values": params.QC_FLAG_VALUES_I8,
        "flag_meanings": params.QC_FLAG_MEANINGS,
        "conventions": params.QC_CONVENTION,
    }
    std = ds[var].attrs.get("standard_name") if var in ds.variables else None
    if std:
        attrs["standard_name"] = f"{std} status_flag"
    if extra:
        attrs.update(extra)
    qc = ds[f"{var}_qc"]
    qc.attrs.update(attrs)
    # Preserve an author-supplied long_name (e.g. a standalone diagnostic flag
    # like seabed_qc); only fill it when absent.
    qc.attrs.setdefault("long_name", f"quality flag for {var}")
    return ds


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge two nested dicts; override values win at the variable level."""
    merged = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = copy.deepcopy(v)
    return merged


def _tilt_from_span(qc_ranges: dict[str, Any]) -> dict[str, float]:
    """Extract tilt thresholds from a qc_ranges block if 'tilt' is present.

    Converts the symmetric span format (matching all other variables) into the
    threshold format used internally by apply_tilt_qc.  The upper bound of
    each span is the threshold — tilt is always non-negative so the lower bound
    is typically negative and only the upper bound matters:
        suspect_span: [-20, 20]  →  suspect_threshold: 20
        fail_span:    [-30, 30]  →  fail_threshold:    30
    """
    out: dict[str, float] = {}
    tilt_cfg = qc_ranges.get("tilt", {})
    if "suspect_span" in tilt_cfg:
        out["suspect_threshold"] = float(tilt_cfg["suspect_span"][1])
    if "fail_span" in tilt_cfg:
        out["fail_threshold"] = float(tilt_cfg["fail_span"][1])
    return out


def load_qc_config(
    mooring_cfg: dict[str, Any],
    entry: dict[str, Any],
) -> tuple[dict, dict, dict, dict]:
    """Return QC threshold dicts for one instrument.

    Returns a 4-tuple ``(gross_range, spike, tilt, flat_line)`` built from
    package defaults with mooring-level and then instrument-level YAML
    overrides applied (later values win).

    ``gross_range`` and ``spike`` map variable names (e.g. ``"temperature"``,
    ``"pressure"``) to threshold dicts understood by ``apply_qc_tests``.
    ``tilt`` holds ``suspect_threshold`` / ``fail_threshold`` in degrees,
    used to flag Aquadopp velocity data when the instrument is tilted beyond
    acceptable limits.  ``flat_line`` maps variable names to
    ``{suspect_n, fail_n, tolerance}`` dicts for the stuck-sensor test.

    YAML override keys:

    * ``qc_ranges`` (mooring or instrument level) — gross-range and tilt spans
    * ``qc_spike`` — spike thresholds
    * ``tilt_qc`` — tilt thresholds (takes precedence over ``qc_ranges.tilt``)
    * ``qc_flat_line`` — stuck-sensor thresholds

    ``tilt`` is stripped from the gross-range dict before returning so it is
    not passed to the QARTOD gross-range test runner.
    """
    gr = copy.deepcopy(params.QC_GROSS_RANGE)
    sp = copy.deepcopy(params.QC_SPIKE)
    tilt = copy.deepcopy(params.QC_TILT)
    fl = copy.deepcopy(params.QC_FLAT_LINE)

    # Mooring-level overrides
    if "qc_ranges" in mooring_cfg:
        gr = _deep_merge(gr, mooring_cfg["qc_ranges"])
        tilt.update(_tilt_from_span(mooring_cfg["qc_ranges"]))
    if "qc_spike" in mooring_cfg:
        sp = _deep_merge(sp, mooring_cfg["qc_spike"])
    if "tilt_qc" in mooring_cfg:
        tilt.update(mooring_cfg["tilt_qc"])
    if "qc_flat_line" in mooring_cfg:
        fl = _deep_merge(fl, mooring_cfg["qc_flat_line"])

    # Instrument-level overrides
    if "qc_ranges" in entry:
        gr = _deep_merge(gr, entry["qc_ranges"])
        tilt.update(_tilt_from_span(entry["qc_ranges"]))
    if "qc_spike" in entry:
        sp = _deep_merge(sp, entry["qc_spike"])
    if "tilt_qc" in entry:
        tilt.update(entry["tilt_qc"])
    if "qc_flat_line" in entry:
        fl = _deep_merge(fl, entry["qc_flat_line"])

    # Remove tilt from the gross-range dict — it is handled separately and
    # is not a valid QARTOD gross-range variable.
    gr.pop("tilt", None)

    return gr, sp, tilt, fl


def apply_tilt_qc(
    ds: xr.Dataset,
    tilt_cfg: dict[str, Any],
) -> tuple[xr.Dataset, int, int]:
    """Flag velocity variables when pitch or roll exceeds QC thresholds.

    Primary path — pitch_qc / roll_qc already exist (created by the gross-range
    QC step when pitch and/or roll appear in ``qc_ranges`` in the YAML):

        The two flag arrays are merged element-wise (worst flag wins) and the
        result is propagated to every velocity variable.  Any time step where
        pitch OR roll is flagged suspect (3) or bad (4) will flag the velocities
        with the same severity.

    Fallback path — neither pitch_qc nor roll_qc exist:

        tilt is computed as max(``|pitch|``, ``|roll|``) and compared against
        tilt_cfg thresholds (``suspect_threshold`` / ``fail_threshold``).

    In both paths ``tilt_suspect_threshold`` and ``tilt_fail_threshold`` are
    written to global attrs so the report can draw reference lines on the tilt
    time-series panel.

    Returns (ds, n_suspect, n_bad).  No-ops when both pitch and roll are absent.
    """
    has_pitch = "pitch" in ds.data_vars
    has_roll = "roll" in ds.data_vars
    if not has_pitch and not has_roll:
        return ds, 0, 0

    has_pitch_qc = "pitch_qc" in ds.data_vars
    has_roll_qc = "roll_qc" in ds.data_vars

    if has_pitch_qc or has_roll_qc:
        # Primary path: merge already-computed pitch/roll QC flags.
        n_time = ds["time"].size
        combined = np.ones(n_time, dtype=np.int8)
        if has_pitch_qc:
            combined = _merge_flags(combined, ds["pitch_qc"].values.astype(np.int8))
        if has_roll_qc:
            combined = _merge_flags(combined, ds["roll_qc"].values.astype(np.int8))
        tilt_flags = combined

        # Read thresholds from whichever QC variable is available so the
        # report can draw consistent reference lines on the tilt panel.
        ref_attrs = ds["pitch_qc" if has_pitch_qc else "roll_qc"].attrs
        suspect_thresh = float(
            ref_attrs.get(
                "qc_gross_range_suspect_max", tilt_cfg.get("suspect_threshold", 20.0)
            )
        )
        fail_thresh = float(
            ref_attrs.get(
                "qc_gross_range_fail_max", tilt_cfg.get("fail_threshold", 30.0)
            )
        )
    else:
        # Fallback: compute from raw pitch/roll values.
        n_time = ds["time"].size
        pitch = ds["pitch"].values.astype(float) if has_pitch else np.zeros(n_time)
        roll = ds["roll"].values.astype(float) if has_roll else np.zeros(n_time)

        tilt = np.maximum(np.abs(pitch), np.abs(roll))
        tilt[~np.isfinite(pitch) | ~np.isfinite(roll)] = np.nan

        suspect_thresh = float(tilt_cfg.get("suspect_threshold", 20.0))
        fail_thresh = float(tilt_cfg.get("fail_threshold", 30.0))

        tilt_flags = np.where(
            ~np.isfinite(tilt),
            np.int8(9),
            np.where(
                tilt >= fail_thresh,
                np.int8(4),
                np.where(tilt >= suspect_thresh, np.int8(3), np.int8(1)),
            ),
        ).astype(np.int8)

    n_suspect = int(np.sum(tilt_flags == 3))
    n_bad = int(np.sum(tilt_flags == 4))

    # Persist thresholds so the report can draw reference lines on the tilt panel.
    ds.attrs["tilt_suspect_threshold"] = suspect_thresh
    ds.attrs["tilt_fail_threshold"] = fail_thresh

    if n_suspect == 0 and n_bad == 0:
        return ds, 0, 0

    vel_vars = [v for v in _VELOCITY_VARS if v in ds.data_vars]
    for varname in vel_vars:
        qc_varname = f"{varname}_qc"
        # For ADCP, velocity is 2-D (time, N_BINS); broadcast (time,) → (time, N_BINS).
        if ds[varname].values.ndim > 1:
            flags_for_var = np.broadcast_to(
                tilt_flags[:, np.newaxis], ds[varname].shape
            ).copy()
        else:
            flags_for_var = tilt_flags.copy()
        if qc_varname in ds:
            existing = ds[qc_varname].values.astype(np.int8)
            new_flags = _merge_flags(existing, flags_for_var)
        else:
            new_flags = flags_for_var
        ds[qc_varname] = xr.Variable(
            ds[varname].dims,
            new_flags,
            attrs={"long_name": f"quality flag for {varname}"},
        )

    return ds, n_suspect, n_bad


def compute_salinity_data(
    ds: xr.Dataset,
    log_fn: Any = None,
) -> xr.Dataset:
    """Compute Practical Salinity (SP) data values only — no QC flags yet.

    Call this BEFORE ``apply_qc_tests`` so that salinity participates in the
    gross-range QC pass and gets its threshold attrs written to ``salinity_qc``.
    Call ``merge_salinity_parent_qc`` afterward to fold in T/C/P parent flags.

    Conductivity must already be canonical ``mS cm-1`` — stage 3 runs
    :func:`oceanarray.pipeline.units.to_canonical_units` first, which converts any
    S/m input.  A non-canonical unit here means that pass was skipped and the ×10 is
    missing, so this raises rather than compute salinity 10× too small from S/m.
    """
    required = {"temperature", "conductivity", "pressure"}
    if not required.issubset(ds.data_vars):
        return ds
    cond_units = str(ds["conductivity"].attrs.get("units", "")).strip()
    canonical = params.VARIABLES["conductivity"]["units"]
    if cond_units != canonical:
        raise UnitsError(
            f"conductivity units are {cond_units!r}, not the canonical {canonical!r}; "
            f"run to_canonical_units before computing salinity (salinity from S/m "
            f"would be 10x too small)."
        )
    try:
        import gsw
    except ImportError:
        if log_fn:
            log_fn("  WARNING: gsw not installed — salinity not computed")
        return ds

    C = ds["conductivity"].values.astype(float)  # mS/cm
    t = ds["temperature"].values.astype(float)  # °C ITS-90
    p = ds["pressure"].values.astype(float)  # dbar

    SP = gsw.SP_from_C(C, t, p)
    ds["salinity"] = xr.Variable(
        "time",
        SP,
        attrs={
            "units": "1",
            "long_name": "Practical Salinity",
            "standard_name": "sea_water_practical_salinity",
            "coverage_content_type": "physicalMeasurement",
            "comment": "Derived from conductivity, temperature, pressure via gsw.SP_from_C",
        },
    )
    return ds


def merge_salinity_parent_qc(
    ds: xr.Dataset,
) -> xr.Dataset:
    """Merge parent (T/C/P) QC flags into salinity_qc after QC tests have run.

    ``apply_qc_tests`` sets salinity_qc from the gross-range test and stores
    the threshold attrs needed by the report histogram.  Here we additionally
    fold in the worst flag from temperature_qc, conductivity_qc, and
    pressure_qc so that a bad/suspect input propagates to salinity.
    """
    if "salinity" not in ds.data_vars:
        return ds
    parent_flags = []
    for varname in ("temperature", "conductivity", "pressure"):
        qv = f"{varname}_qc"
        if qv not in ds.data_vars:
            continue
        f = ds[qv].values.astype(np.int8).copy()
        if varname == "pressure":
            # Interpolated pressure (flag 8) does not degrade salinity: T and C
            # are still directly measured, so salinity is valid.  Treat flag 8
            # as good (1) when propagating from pressure to salinity.
            f[f == 8] = np.int8(1)
        parent_flags.append(f)
    if not parent_flags:
        return ds

    parent_merged = _merge_flags(*parent_flags)
    if "salinity_qc" in ds:
        existing_attrs = dict(ds["salinity_qc"].attrs)
        new_flags = _merge_flags(
            ds["salinity_qc"].values.astype(np.int8), parent_merged
        )
    else:
        existing_attrs = {}
        new_flags = parent_merged

    ds["salinity_qc"] = xr.Variable(
        "time",
        new_flags,
        attrs={
            **existing_attrs,
            "long_name": "quality flag for salinity",
            "comment": "QARTOD gross-range + worst of T/C/P parent flags",
        },
    )
    return ds


def apply_qc_tests(
    ds: xr.Dataset,
    gross_range: dict[str, Any],
    spike: dict[str, Any],
    flat_line: dict[str, Any] | None = None,
) -> xr.Dataset:
    """Apply QARTOD gross-range, spike, and flat-line tests, writing ``*_qc`` variables.

    Three QARTOD tests are applied in sequence; the worst flag across all
    tests wins for each sample:

    * **Gross-range**: flags values outside physically plausible bounds as
      SUSPECT (3) or BAD (4) — e.g. temperature below -2.5 °C or above 40 °C.
    * **Spike**: flags isolated outliers that deviate from the surrounding
      record by more than a threshold — e.g. a brief salinity glitch from
      biofouling or a pressure transient.
    * **Flat-line** (stuck-sensor): flags runs of consecutive samples where
      the value does not change by more than ``tolerance`` — e.g. a pressure
      sensor frozen at 0 dbar after a failure.  Threshold is expressed in
      sample counts (``suspect_n``, ``fail_n``) and converted to seconds
      using the median sample interval.  Applied by default to pressure only
      (see ``parameters.QC_FLAT_LINE``).

    Pre-existing ``*_qc`` values (e.g. ``pressure_qc = 8`` set by the
    pressure-interpolation step in the stack) are preserved: the worst flag
    across the incoming value and the new test flags is written to the output.

    Provenance: the thresholds actually applied are stored as attributes on
    each ``{var}_qc`` variable so the treatment can be reconstructed from
    the NetCDF file alone, without re-reading the YAML:

    * ``qc_gross_range_fail_min`` / ``qc_gross_range_fail_max``
    * ``qc_gross_range_suspect_min`` / ``qc_gross_range_suspect_max``
    * ``qc_spike_suspect_threshold`` / ``qc_spike_fail_threshold``
    * ``qc_flat_line_suspect_count`` / ``qc_flat_line_fail_count``
    """
    from ioos_qc import qartod

    _flat = flat_line or {}
    test_vars = [
        v
        for v in ds.data_vars
        if (v in gross_range or v in spike or v in _flat) and not v.endswith("_qc")
    ]

    for varname in test_vars:
        data = ds[varname].values.copy().astype(float)
        # Start with flag 1 (good) for all non-NaN, 9 (missing) for NaN
        base_flags = np.where(np.isfinite(data), np.int8(1), np.int8(9))
        flags_list = [base_flags]

        if varname in gross_range:
            cfg = gross_range[varname]
            flags_list.append(
                _ingest_qartod(
                    qartod.gross_range_test(
                        inp=data,
                        fail_span=tuple(cfg["fail_span"]),
                        suspect_span=tuple(cfg.get("suspect_span", cfg["fail_span"])),
                    )
                )
            )

        if varname in spike:
            cfg = spike[varname]
            flags_list.append(
                _ingest_qartod(
                    qartod.spike_test(
                        inp=data,
                        suspect_threshold=cfg.get("suspect_threshold"),
                        fail_threshold=cfg.get("fail_threshold"),
                    )
                )
            )

        if varname in _flat and "time" in ds:
            fl_cfg = _flat[varname]
            time_vals = ds["time"].values
            # Convert datetime64 → float seconds since epoch for ioos_qc
            time_s = time_vals.astype("datetime64[s]").astype(float)
            dt_s = float(np.nanmedian(np.diff(time_s))) if len(time_s) > 1 else 60.0
            if dt_s <= 0:
                # All timestamps are identical (or single record); ioos_qc would
                # divide by zero inside flat_line_test — skip the test.
                import warnings

                warnings.warn(
                    f"flat_line_test skipped for '{varname}': median time interval is "
                    f"{dt_s:.1f} s (all timestamps identical?)",
                    RuntimeWarning,
                    stacklevel=2,
                )
                continue
            suspect_n = int(fl_cfg.get("suspect_n", 3))
            fail_n = int(fl_cfg.get("fail_n", 10))
            try:
                _fl_result = qartod.flat_line_test(
                    inp=data,
                    tinp=time_s,
                    suspect_threshold=int(suspect_n * dt_s),
                    fail_threshold=int(fail_n * dt_s),
                    tolerance=float(fl_cfg.get("tolerance", 0.0)),
                )
            except (ValueError, OverflowError) as _fl_err:
                import warnings

                warnings.warn(
                    f"flat_line_test skipped for '{varname}': {_fl_err}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                continue
            flags_list.append(_ingest_qartod(_fl_result))

        new_flags = _merge_flags(*flags_list)

        qc_varname = f"{varname}_qc"
        if qc_varname in ds:
            existing = ds[qc_varname].values.astype(np.int8)
            new_flags = _merge_flags(existing, new_flags)

        # Store the actual thresholds applied so downstream tools (e.g. the
        # report histogram) can show exactly what was used without re-reading YAML.
        threshold_attrs: dict[str, Any] = {}
        if varname in gross_range:
            gcfg = gross_range[varname]
            if "fail_span" in gcfg:
                threshold_attrs["qc_gross_range_fail_min"] = float(gcfg["fail_span"][0])
                threshold_attrs["qc_gross_range_fail_max"] = float(gcfg["fail_span"][1])
            if "suspect_span" in gcfg:
                threshold_attrs["qc_gross_range_suspect_min"] = float(
                    gcfg["suspect_span"][0]
                )
                threshold_attrs["qc_gross_range_suspect_max"] = float(
                    gcfg["suspect_span"][1]
                )
        if varname in spike:
            scfg = spike[varname]
            if "suspect_threshold" in scfg:
                threshold_attrs["qc_spike_suspect_threshold"] = float(
                    scfg["suspect_threshold"]
                )
            if "fail_threshold" in scfg:
                threshold_attrs["qc_spike_fail_threshold"] = float(
                    scfg["fail_threshold"]
                )
        if varname in _flat:
            fl_cfg = _flat[varname]
            threshold_attrs["qc_flat_line_suspect_count"] = int(
                fl_cfg.get("suspect_n", 3)
            )
            threshold_attrs["qc_flat_line_fail_count"] = int(fl_cfg.get("fail_n", 10))

        ds[qc_varname] = xr.Variable(
            ds[varname].dims,
            new_flags,
            attrs={
                "long_name": f"quality flag for {varname}",
                **threshold_attrs,
            },
        )

    return ds


def apply_enu_velocity_qc(
    ds: xr.Dataset,
    gr_cfg: dict[str, Any],
) -> xr.Dataset:
    """Apply QARTOD gross-range QC to ENU velocity vars and propagate w flags.

    Must be called after apply_beam_to_enu (east/north/up_velocity must exist).
    No spike test is applied to velocity (burst-mode Aquadopps generate false
    positives at every burst boundary).

    Propagates up_velocity_qc (flag 3 or 4) to east_velocity_qc and
    north_velocity_qc: if vertical velocity is implausibly large the whole
    3-D velocity measurement is suspect/bad.  Full bidirectional unification
    (large east/north flags → up_velocity_qc) is done by unify_velocity_qc,
    which must be called after apply_tilt_qc.
    """
    enu_gr = {
        k: v
        for k, v in gr_cfg.items()
        if k in ("east_velocity", "north_velocity", "up_velocity")
    }
    if enu_gr:
        ds = apply_qc_tests(ds, enu_gr, {})

    if "up_velocity_qc" in ds.data_vars:
        up_flags = ds["up_velocity_qc"].values.astype(np.int8)
        for vel_var in ("east_velocity", "north_velocity"):
            qc_varname = f"{vel_var}_qc"
            if qc_varname in ds.data_vars:
                existing = ds[qc_varname].values.astype(np.int8)
                merged = _merge_flags(existing, up_flags)
                ds[qc_varname] = xr.Variable(
                    ds[vel_var].dims, merged, attrs=dict(ds[qc_varname].attrs)
                )
    return ds


def unify_velocity_qc(ds: xr.Dataset) -> xr.Dataset:
    """Unify ENU velocity QC flags so all three components share the worst flag.

    After gross-range, up-velocity propagation, and tilt QC, each component may
    carry different flags.  A large east/north velocity flags only that component;
    tilt flags all three; up-velocity flags east and north but not itself from the
    east/north gross-range test.  This function computes the element-wise worst flag
    across east_velocity_qc, north_velocity_qc, and up_velocity_qc, then writes
    that combined flag back to all three.  The result: masking on any single
    component produces an identical, consistent velocity mask.
    """
    keys = [
        v
        for v in ("east_velocity_qc", "north_velocity_qc", "up_velocity_qc")
        if v in ds.data_vars
    ]
    if not keys:
        return ds
    combined = np.ones(ds[keys[0]].shape, dtype=np.int8)
    for k in keys:
        combined = _merge_flags(combined, ds[k].values.astype(np.int8))
    for k in keys:
        ds[k] = xr.Variable(ds[k].dims, combined.copy(), attrs=dict(ds[k].attrs))
    return ds


def derive_oxygen_saturation(ds: xr.Dataset) -> xr.Dataset:
    """Compute O2 % saturation and AOU from dissolved_oxygen, temperature, salinity, pressure.

    Requires dissolved_oxygen, temperature (°C), salinity (PSU), and pressure (dbar)
    all present in *ds*.  Returns *ds* unchanged when any variable is missing or when
    gsw is unavailable.

    The saturation reference ``gsw.O2sol_SP_pt`` is per mass (µmol kg⁻¹), so the
    measured oxygen is compared in µmol kg⁻¹.  The measured value is taken by its
    ``units`` attribute:

    - ``umol kg-1`` (the canonical, per mass): used directly.
    - ``umol L-1`` (a per-volume variable, if ever read): divided by in-situ
      **seawater** density ``gsw.rho(SA, CT, p)/1000`` (kg L⁻¹) to convert to per mass.
      Seawater density (~1025–1028 kg m⁻³) gives a ~2.5 % correction relative to
      freshwater — oceanographically significant, not skippable.
    - anything else: :class:`~oceanarray.pipeline.units.UnitsError` — the basis must
      be known, never guessed.

    lon/lat for ``gsw.SA_from_SP`` are taken from global attrs (default 0.0 if absent;
    the SA error from a wrong position is typically < 0.05 g kg⁻¹ at open-ocean sites).

    Derived variables stored
    ------------------------
    oxygen_saturation_pct : %
        100 × O2_measured(µmol kg⁻¹) / O2sol(µmol kg⁻¹)
        where O2sol is from ``gsw.O2sol_SP_pt(SP, pt)``.
    apparent_oxygen_utilization : µmol kg⁻¹
        O2sol − O2_measured (positive = oxygen-depleted water).
    """
    required = {"dissolved_oxygen", "temperature", "salinity", "pressure"}
    if not required.issubset(ds.data_vars):
        return ds
    try:
        import gsw
    except ImportError:
        return ds

    O2 = ds["dissolved_oxygen"].values.astype(float)
    o2_units = str(ds["dissolved_oxygen"].attrs.get("units", "")).strip()
    t = ds["temperature"].values.astype(float)
    SP = ds["salinity"].values.astype(float)
    p = ds["pressure"].values.astype(float)
    try:
        lon = float(ds.attrs.get("longitude", 0.0))
    except (ValueError, TypeError):
        lon = 0.0
    try:
        lat = float(ds.attrs.get("latitude", 0.0))
    except (ValueError, TypeError):
        lat = 0.0

    # Mask already-flagged dissolved oxygen so bad values produce NaN saturation
    # rather than spurious negative percentages that would then back-propagate.
    if "dissolved_oxygen_qc" in ds.data_vars:
        _do_qc = ds["dissolved_oxygen_qc"].values.astype(np.int8)
        O2 = O2.copy()
        O2[np.isin(_do_qc, [4, 9])] = np.nan

    SA = gsw.SA_from_SP(SP, p, lon, lat)
    CT = gsw.CT_from_t(SA, t, p)
    pt = gsw.pt0_from_t(SA, t, p)

    O2sol = gsw.O2sol_SP_pt(SP, pt)  # µmol/kg at equilibrium

    # Bring the measured oxygen to per mass (µmol/kg) to match O2sol, by its units.
    if o2_units == "umol kg-1":
        O2_kg = O2  # already per mass
    elif o2_units == "umol L-1":
        rho_sw = gsw.rho(SA, CT, p)  # kg/m³ in-situ seawater density
        O2_kg = O2 / (rho_sw / 1000.0)  # per volume -> per mass (divide by kg/L)
    else:
        raise UnitsError(
            f"dissolved_oxygen units {o2_units!r} is neither 'umol kg-1' nor "
            f"'umol L-1'; cannot derive saturation without a known mass/volume basis."
        )

    pct_sat = 100.0 * O2_kg / O2sol
    aou = O2sol - O2_kg

    # Back-flag: pct_sat < 0 is physically impossible (negative dissolved oxygen).
    # Set saturation to NaN and flag the underlying dissolved_oxygen as bad (4).
    neg_sat = np.isfinite(pct_sat) & (pct_sat < 0.0)
    n_neg = int(np.sum(neg_sat))
    if n_neg > 0:
        pct_sat[neg_sat] = np.nan
        aou[neg_sat] = np.nan
        if "dissolved_oxygen_qc" in ds.data_vars:
            do_qc = ds["dissolved_oxygen_qc"].values.astype(np.int8).copy()
            do_qc[neg_sat] = np.int8(4)
            ds["dissolved_oxygen_qc"] = xr.Variable(
                ds["dissolved_oxygen_qc"].dims,
                do_qc,
                attrs=dict(ds["dissolved_oxygen_qc"].attrs),
            )

    return ds.assign(
        oxygen_saturation_pct=xr.Variable(
            "time",
            pct_sat.astype(np.float32),
            {
                "long_name": "Dissolved Oxygen Percent Saturation",
                "units": "%",
                "comment": (
                    "100 × O2_meas / O2sol. O2_meas converted from µmol/L to µmol/kg "
                    "using in-situ seawater density gsw.rho(SA,CT,p); "
                    "O2sol = gsw.O2sol_SP_pt(SP, pt0). "
                    f"Back-flagged {n_neg} sample(s) where pct_sat < 0 as bad (flag 4)."
                    if n_neg
                    else (
                        "100 × O2_meas / O2sol. O2_meas converted from µmol/L to µmol/kg "
                        "using in-situ seawater density gsw.rho(SA,CT,p); "
                        "O2sol = gsw.O2sol_SP_pt(SP, pt0)."
                    )
                ),
            },
        ),
        apparent_oxygen_utilization=xr.Variable(
            "time",
            aou.astype(np.float32),
            {
                "long_name": "Apparent Oxygen Utilization",
                "standard_name": "apparent_oxygen_utilization",
                "units": "umol kg-1",
                "comment": (
                    "AOU = O2sol - O2_meas (µmol/kg); positive = undersaturated "
                    "(oxygen consumed). Uses in-situ seawater density for µmol/L → µmol/kg."
                ),
            },
        ),
    )
