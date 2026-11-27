"""Quality control: QARTOD tests, flag merging and CTD derivations for stage 3."""

from __future__ import annotations

import copy
import logging
from typing import Any

import numpy as np
import xarray as xr

from oceanarray import _deprecated, logger, physics
from oceanarray.config import parameters as params
from oceanarray.config.unit_tables import UnitsError

log = logging.getLogger(__name__)

#: ``qc_ranges`` keys not run as gross-range tests: tilt QC is geometric only.
TILT_KEYS = ("tilt", "pitch", "roll")


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
    """Merge int8 flag arrays element-wise, keeping the highest-priority (worst)."""
    # priority[flag] for flags 0-9, from the shared single-source LUT.
    _priority = params.QC_MERGE_PRIORITY_LUT
    result = np.asarray(flag_arrays[0], dtype=np.int8).copy()
    for fa in flag_arrays[1:]:
        fa = np.asarray(fa, dtype=np.int8)
        replace = _priority[fa.clip(0, 9)] > _priority[result.clip(0, 9)]
        result = np.where(replace, fa, result).astype(np.int8)
    return result


def _qartod_flags(result: Any) -> np.ndarray:
    """Normalize one ioos_qc test result onto the OceanSITES flag table."""
    # ioos_qc returns a masked array or a plain ndarray, depending on version;
    # missing entries become 9 either way.
    if hasattr(result, "filled"):
        flags = result.filled(9)
    else:
        arr = np.asarray(result, dtype=float)
        flags = np.where(np.isnan(arr), 9, arr)
    flags = np.asarray(flags).astype(np.int8)
    # QARTOD UNKNOWN(2) means "could not evaluate" (e.g. spike_test at a record edge
    # or beside a gap): remap to OceanSITES unknown(0) before merging, so such a
    # point never asserts probably_good_data(2).
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
    """Extract tilt thresholds from a qc_ranges block if 'tilt' is present."""
    # Spans are symmetric like every other variable's, but tilt is non-negative,
    # so the upper bound is the threshold: suspect_span [-20, 20] -> 20.
    out: dict[str, float] = {}
    tilt_cfg = qc_ranges.get("tilt", {})
    if "suspect_span" in tilt_cfg:
        out["suspect_threshold"] = float(tilt_cfg["suspect_span"][1])
    if "fail_span" in tilt_cfg:
        out["fail_threshold"] = float(tilt_cfg["fail_span"][1])
    return out


def load_qc_config(
    mooring_dict: dict[str, Any] | None = None,
    entry: dict[str, Any] | None = None,
    *,
    mooring_cfg: dict[str, Any] | None = None,
) -> tuple[dict, dict, dict, dict]:
    """Return ``(gross_range, spike, tilt, flat_line)`` thresholds for one instrument.

    Package defaults, overridden by the mooring's and then the instrument's ``qc_ranges``,
    ``qc_spike``, ``tilt_qc`` (over ``qc_ranges.tilt``) and ``qc_flat_line``. ``tilt``,
    ``pitch`` and ``roll`` are dropped from the gross range: tilt QC is geometric only
    (:func:`apply_tilt_qc`).

    Parameters
    ----------
    mooring_dict : dict
        The loaded mooring YAML.
    entry : dict
        The instrument's entry.
    mooring_cfg : dict, optional
        Deprecated name of *mooring_dict*; removed in 0.8.0.

    Returns
    -------
    tuple of dict
    """
    mooring_dict = _deprecated.resolve_keyword(
        "oceanarray.pipeline.qc.load_qc_config(mooring_cfg=)",
        mooring_dict,
        mooring_cfg,
        required="mooring_dict",
    )
    if entry is None:
        raise TypeError("load_qc_config() missing required argument: 'entry'")
    gr = copy.deepcopy(params.QC_GROSS_RANGE)
    sp = copy.deepcopy(params.QC_SPIKE)
    tilt = copy.deepcopy(params.QC_TILT)
    fl = copy.deepcopy(params.QC_FLAT_LINE)

    # Mooring-level overrides
    if "qc_ranges" in mooring_dict:
        gr = _deep_merge(gr, mooring_dict["qc_ranges"])
        tilt.update(_tilt_from_span(mooring_dict["qc_ranges"]))
    if "qc_spike" in mooring_dict:
        sp = _deep_merge(sp, mooring_dict["qc_spike"])
    if "tilt_qc" in mooring_dict:
        tilt.update(mooring_dict["tilt_qc"])
    if "qc_flat_line" in mooring_dict:
        fl = _deep_merge(fl, mooring_dict["qc_flat_line"])

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

    for key in TILT_KEYS:
        gr.pop(key, None)

    return gr, sp, tilt, fl


def apply_tilt_qc(
    ds: xr.Dataset,
    tilt_cfg: dict[str, Any],
) -> tuple[xr.Dataset, int, int]:
    """Flag velocity variables where the instrument tilt exceeds the QC thresholds.

    The geometric tilt (:func:`oceanarray.physics.instrument_tilt`) is compared against
    ``suspect_threshold`` / ``fail_threshold`` (``QC_TILT`` updated by *tilt_cfg*); the
    flags are merged into each velocity ``_qc`` variable.  The thresholds are written to the
    global attrs ``tilt_suspect_threshold`` / ``tilt_fail_threshold`` for the report's tilt
    panel.

    Returns (ds, n_suspect, n_bad).  No-ops when both pitch and roll are absent.
    """
    tilt = physics.instrument_tilt(ds)
    if tilt is None:
        return ds, 0, 0
    tilt_cfg = {**params.QC_TILT, **tilt_cfg}
    suspect_thresh = float(tilt_cfg["suspect_threshold"])
    fail_thresh = float(tilt_cfg["fail_threshold"])
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
    """Add Practical Salinity (``salinity``) from T, C and P, in place; no flags yet.

    Run before ``apply_qc_tests`` (so salinity gets gross-range QC) and before
    ``merge_salinity_parent_qc``.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset with temperature, conductivity (``mS cm-1``) and pressure.
    log_fn : callable, optional
        Deprecated (removed in 0.7.0): configure logging instead.

    Returns
    -------
    xarray.Dataset

    Raises
    ------
    UnitsError
        Conductivity is not ``mS cm-1`` (the units pass was skipped).
    """
    with logger.deprecated_log_fn(log_fn):
        return _compute_salinity_data(ds)


def _compute_salinity_data(
    ds: xr.Dataset,
) -> xr.Dataset:
    """Implement :func:`compute_salinity_data` (without ``log_fn``)."""
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
        log.warning("gsw not installed — salinity not computed")
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
    """Apply the QARTOD gross-range, spike and flat-line tests, writing ``<var>_qc``.

    The worst flag wins, including an existing ``<var>_qc`` (e.g. interpolated pressure,
    8). Flat-line counts (``suspect_n``, ``fail_n``) become seconds by the median step;
    by default only pressure (``parameters.QC_FLAT_LINE``). The thresholds applied are
    recorded on each flag variable: ``qc_gross_range_{fail,suspect}_{min,max}``,
    ``qc_spike_{suspect,fail}_threshold``, ``qc_flat_line_{suspect,fail}_count``.

    Parameters
    ----------
    ds : xarray.Dataset
        The instrument's dataset.
    gross_range : dict
        Variable → ``fail_span`` / ``suspect_span``.
    spike : dict
        Variable → ``suspect_threshold`` / ``fail_threshold``.
    flat_line : dict, optional
        Variable → ``suspect_n`` / ``fail_n`` / ``tolerance``.

    Returns
    -------
    xarray.Dataset
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
                _qartod_flags(
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
                _qartod_flags(
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
                log.warning(
                    "flat_line_test skipped for '%s': median time interval is "
                    "%.1f s (all timestamps identical?)",
                    varname,
                    dt_s,
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
                log.warning("flat_line_test skipped for '%s': %s", varname, _fl_err)
                continue
            flags_list.append(_qartod_flags(_fl_result))

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
    """Add ``oxygen_saturation_pct`` (%) and ``apparent_oxygen_utilization`` (µmol kg-1).

    Saturation is ``100 × O2 / O2sol`` and AOU ``O2sol − O2`` with ``O2sol =
    gsw.O2sol_SP_pt(SP, pt)`` (µmol kg-1). Oxygen in ``umol kg-1`` is used as it is; in
    ``umol L-1`` it is divided by in-situ seawater density (``gsw.rho / 1000``, ~2.5 % from
    fresh water). Position for ``SA_from_SP`` from the global attributes, else 0.

    Parameters
    ----------
    ds : xarray.Dataset
        With ``dissolved_oxygen``, ``temperature``, ``salinity`` and ``pressure``.

    Returns
    -------
    xarray.Dataset
        Unchanged when an input or gsw is missing.

    Raises
    ------
    UnitsError
        Oxygen in any other units.
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


def _worst_flag(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Element-wise worst QC flag on the OceanSITES priority ordering."""
    # rank[flag_value] gives priority; higher rank = worse flag. Weakest to strongest:
    # unknown 0 < good 1 < probably_good 2 < nominal 7 < interpolated 8
    # < potentially_correctable_bad 3 < bad 4 < missing 9. The LUT is shared with the
    # qc module's flag merge so the two merge paths cannot drift.
    _rank = params.QC_MERGE_PRIORITY_LUT
    # NaN -> 9 (missing) so levels with no data are never promoted to 0 (unknown).
    a = np.where(np.isfinite(a), a, 9.0)
    b = np.where(np.isfinite(b), b, 9.0)
    ai = np.clip(np.round(a).astype(np.int8), 0, 9)
    bi = np.clip(np.round(b).astype(np.int8), 0, 9)
    take_b = _rank[bi] > _rank[ai]
    out = ai.copy()
    out[take_b] = bi[take_b]
    return out
