"""Pressure helpers: stage-3 interpolation of pressure onto instruments and ADCP bins."""

from __future__ import annotations

import logging
import warnings
from types import ModuleType
from typing import Any

import numpy as np
import xarray as xr

from oceanarray import logger, physics
from oceanarray.exceptions import MooringError
from oceanarray.pipeline.qc import set_qc_attrs
from oceanarray.readers import adcp_geometry

log = logging.getLogger(__name__)

HAB_THRESHOLD = 2.0  # metres — use near-neighbour below this Δhab

#: Released names that moved out of this module: old name -> the module now holding
#: it. Each still imports from here, with a DeprecationWarning, until 0.8.0.
_MOVED: dict[str, ModuleType] = {
    "pressure_from_depth": physics,
    "depth_from_pressure": physics,
    "ORIGINATOR_BIN_PRESSURE_ATTRS": adcp_geometry,
    "originator_bin_geometry": adcp_geometry,
}


def __getattr__(name: str) -> object:
    """Return a moved name from its new module, warning that this path is deprecated."""
    if name not in _MOVED:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    new = _MOVED[name]
    warnings.warn(
        f"{__name__}.{name} is deprecated and is removed in 0.8.0; "
        f"use {new.__name__}.{name}.",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(new, name)


def _times_to_float(time_values: np.ndarray) -> np.ndarray:
    """Convert numpy datetime64 array to float64 nanoseconds (for numpy.interp)."""
    return time_values.astype("datetime64[ns]").astype(np.float64)


def interp_pressure(
    source_time: np.ndarray,
    source_pressure: np.ndarray,
    target_time: np.ndarray,
) -> np.ndarray:
    """Linearly interpolate source pressure onto target time axis.

    Nearest-point extrapolation at the edges (no NaN fill outside range).
    """
    src_t = _times_to_float(source_time)
    tgt_t = _times_to_float(target_time)
    valid = np.isfinite(source_pressure)
    if valid.sum() < 2:
        return np.full(len(target_time), np.nan)
    return np.interp(
        tgt_t,
        src_t[valid],
        source_pressure[valid],
        left=source_pressure[valid][0],
        right=source_pressure[valid][-1],
    )


#: Source pressure with one of these flags (ours or the originator's translated)
#: is never interpolated from: a value made from bad or missing data is not right,
#: whatever ``mask_flags`` says.
UNUSABLE_SOURCE_FLAGS: tuple[int, ...] = (4, 9)


def _source_flags(ds: xr.Dataset, var: str) -> list[np.ndarray]:
    """Return the flag arrays of a source's pressure: ours, and the originator's."""
    from oceanarray.originator_flags import MAPPED_SUFFIX
    from oceanarray.pipeline.qc.originator import translate

    out = []
    if f"{var}_qc" in ds.data_vars:
        out.append(np.asarray(ds[f"{var}_qc"].values))
    if f"{var}{MAPPED_SUFFIX}" in ds.data_vars:
        out.append(np.asarray(ds[f"{var}{MAPPED_SUFFIX}"].values))
    elif f"{var}_qc_originator" in ds.data_vars:
        # A stage-2 source: translate its originator flags here.
        out.append(translate(ds[f"{var}_qc_originator"])[0])
    return out


def _source_pressure(src: dict[str, Any], target_time: np.ndarray) -> np.ndarray:
    """Return a source's pressure on *target_time*, never from an unusable sample.

    The source's samples flagged 4 or 9 (ours or the originator's) are left out; a
    target time whose nearest source sample is such a sample is NaN (missing, 9),
    not filled from the samples around it.
    """
    ds = src["ds"]
    var = src["pressure_var"]
    values = np.asarray(ds[var].values, dtype=np.float64).copy()
    unusable = np.zeros(values.shape, dtype=bool)
    for flags in _source_flags(ds, var):
        unusable |= np.isin(flags, UNUSABLE_SOURCE_FLAGS) & np.isfinite(values)
    values[unusable] = np.nan
    p = interp_pressure(ds["time"].values, values, target_time)
    if unusable.any():
        src_t = _times_to_float(ds["time"].values)
        nearest = np.clip(
            np.searchsorted(src_t, _times_to_float(target_time)), 0, len(src_t) - 1
        )
        prev = np.clip(nearest - 1, 0, len(src_t) - 1)
        tgt = _times_to_float(target_time)
        pick = np.where(
            np.abs(src_t[prev] - tgt) <= np.abs(src_t[nearest] - tgt), prev, nearest
        )
        p[unusable[pick]] = np.nan
    return p


def _flag_summary(src: dict[str, Any]) -> str:
    """Return ``"<serial>: 1 (n), 3 (n), 4 (n, not used)"`` for a pressure source."""
    ds = src["ds"]
    var = src["pressure_var"]
    finite = np.isfinite(np.asarray(ds[var].values, dtype=np.float64))
    worst = np.zeros(finite.shape, dtype=np.int8)
    from oceanarray.pipeline.qc import _worst_flag

    for flags in _source_flags(ds, var):
        worst = _worst_flag(worst, flags).astype(np.int8)
    numbers, counts = np.unique(worst[finite], return_counts=True)
    parts = [
        f"{int(k)} ({int(c)}{', not used' if int(k) in UNUSABLE_SOURCE_FLAGS else ''})"
        for k, c in zip(numbers, counts, strict=True)
    ]
    return f"{src['serial']}: " + (", ".join(parts) if parts else "no flags")


def interp_pressure_for_hab(
    hab_t: float,
    sorted_sources: list[dict[str, Any]],
    target_time: np.ndarray,
    serial: str,
    log_fn: Any | None = None,
    used: list[dict[str, Any]] | None = None,
) -> tuple[np.ndarray, str]:
    """Interpolate pressure for one nominal HAB; return (pressure, method).

    Parameters
    ----------
    hab_t : float
        Target height above bottom (m).
    sorted_sources : list of dict
        Pressure sources sorted by HAB, each with ``ds``, ``hab``, ``pressure_var``,
        ``instrument`` and ``serial``.
    target_time : numpy.ndarray
        Target datetime64 times.
    serial : str
        Target serial, for the log.
    log_fn : callable, optional
        Deprecated (removed in 0.7.0): configure logging instead.
    used : list of dict, optional
        Receives the sources the interpolation drew on.

    Returns
    -------
    tuple of (numpy.ndarray, str)
    """
    with logger.deprecated_log_fn(log_fn):
        return _interp_pressure_for_hab(
            hab_t, sorted_sources, target_time, serial, used=used
        )


def _interp_pressure_for_hab(
    hab_t: float,
    sorted_sources: list[dict[str, Any]],
    target_time: np.ndarray,
    serial: str,
    used: list[dict[str, Any]] | None = None,
) -> tuple[np.ndarray, str]:
    """Implement :func:`interp_pressure_for_hab` (without ``log_fn``)."""
    habs = np.array([s["hab"] for s in sorted_sources])
    diffs = np.abs(habs - hab_t)
    nearest_idx = int(np.argmin(diffs))

    if diffs[nearest_idx] <= HAB_THRESHOLD:
        src = sorted_sources[nearest_idx]
        if used is not None:
            used.append(src)
        hab_offset_dbar = src["hab"] - hab_t
        p = _source_pressure(src, target_time) + hab_offset_dbar
        method = (
            f"near-neighbour from {src['instrument']} {src['serial']} "
            f"(Δhab={diffs[nearest_idx]:.1f}m, "
            f"static offset={hab_offset_dbar:+.1f} dbar)"
        )
        log.debug(f"  {serial} (hab={hab_t:.1f}m): {method}")
    else:
        below = [
            (s, h) for s, h in zip(sorted_sources, habs, strict=False) if h < hab_t
        ]
        above = [
            (s, h) for s, h in zip(sorted_sources, habs, strict=False) if h > hab_t
        ]

        if below and above:
            src_below, h_below = below[-1]
            if used is not None:
                used += [src_below, above[0][0]]
            src_above, h_above = above[0]
            w_above = (hab_t - h_below) / (h_above - h_below)
            w_below = 1.0 - w_above
            p_below = _source_pressure(src_below, target_time)
            p_above = _source_pressure(src_above, target_time)
            p = w_below * p_below + w_above * p_above
            method = (
                f"bracketed: {w_below:.2f}×{src_below['instrument']} "
                f"{src_below['serial']} (hab={h_below:.1f}m) + "
                f"{w_above:.2f}×{src_above['instrument']} "
                f"{src_above['serial']} (hab={h_above:.1f}m)"
            )
            log.debug(f"  {serial} (hab={hab_t:.1f}m): {method}")
        else:
            src = below[-1][0] if below else above[0][0]
            if used is not None:
                used.append(src)
            hab_offset_dbar = src["hab"] - hab_t
            p = _source_pressure(src, target_time) + hab_offset_dbar
            method = (
                f"extrapolated from {src['instrument']} {src['serial']} "
                f"(hab={src['hab']:.1f}m, "
                f"static offset={hab_offset_dbar:+.1f} dbar) — WARNING: out of range: "
                f"hab {hab_t:.1f} m is {'above' if below else 'below'} every pressure "
                f"source (hab {habs.min():.1f}–{habs.max():.1f} m): not bracketed; the "
                "offset takes 1 m of hab as 1 dbar and assumes the line stays upright"
            )
            log.warning(f"{serial} (hab={hab_t:.1f}m): {method}")

    return p, method


def interpolate_pressure(
    ds: xr.Dataset,
    target_info: dict[str, Any],
    sources: list[dict[str, Any]],
    target_time: np.ndarray,
    pressure_bad_flag: bool,
    log_fn: Any | None = None,
) -> tuple[xr.Dataset, str]:
    """Interpolate pressure from *sources* onto the target; return (ds, method).

    Parameters
    ----------
    ds : xarray.Dataset
        Stage-2 dataset of the target instrument.
    target_info : dict
        ``hab``, ``hab_segments``, ``serial`` and ``qc_flags`` of the target.
    sources : list of dict
        Pressure sources, each with ``ds`` loaded.
    target_time : numpy.ndarray
        ``ds["time"].values``.
    pressure_bad_flag : bool
        Keep the original pressure as ``pressure_orig`` / ``pressure_orig_qc``.
    log_fn : callable, optional
        Deprecated (removed in 0.7.0): configure logging instead.

    Returns
    -------
    tuple of (xarray.Dataset, str)

    Notes
    -----
    ``hab`` is the as-deployed HAB; each ``hab_segments`` entry ``(time, hab)`` holds from
    that time on (``T >= time``), as ``hab_segments: from:`` in the mooring YAML, so an
    instrument that slid is interpolated per segment.
    """
    with logger.deprecated_log_fn(log_fn):
        return _interpolate_pressure(
            ds, target_info, sources, target_time, pressure_bad_flag
        )


def _interpolate_pressure(
    ds: xr.Dataset,
    target_info: dict[str, Any],
    sources: list[dict[str, Any]],
    target_time: np.ndarray,
    pressure_bad_flag: bool,
) -> tuple[xr.Dataset, str]:
    """Implement :func:`interpolate_pressure` (without ``log_fn``)."""
    if len(target_time) == 0:
        return ds, "no data"

    pressure_qc_val = target_info["qc_flags"].get("pressure", 0)
    serial = target_info["serial"]

    sorted_sources = sorted(
        [s for s in sources if s.get("ds") is not None],
        key=lambda s: s["hab"],
    )
    if not sorted_sources:
        return ds, "no sources available"

    hab_segments = target_info.get("hab_segments", [])
    used: list[dict[str, Any]] = []

    if hab_segments:
        # Build list of (start_ns, end_ns, hab) covering the full record.
        # Segment 0: record start → first breakpoint, using nominal HAB.
        # Segment k: breakpoint[k-1] → breakpoint[k] (or record end), using
        #            the HAB specified at breakpoint[k-1].
        T = target_time
        seg_bounds: list[tuple] = []
        seg_hab = target_info["hab"]
        seg_start = T[0]
        for bp_ns, next_hab in hab_segments:
            seg_bounds.append((seg_start, bp_ns, seg_hab))
            seg_start = bp_ns
            seg_hab = next_hab
        seg_bounds.append((seg_start, T[-1] + np.timedelta64(1, "ns"), seg_hab))

        p_interp = np.full(len(T), np.nan)
        method_parts = []
        for seg_start_ns, seg_end_ns, hab_t in seg_bounds:
            idx = np.where((seg_start_ns <= T) & (seg_end_ns > T))[0]
            if len(idx) == 0:
                continue
            p_seg, meth = _interp_pressure_for_hab(
                hab_t, sorted_sources, T[idx], serial, used=used
            )
            p_interp[idx] = p_seg
            bp_str = str(seg_end_ns)[:16]
            method_parts.append(f"hab={hab_t:.1f}m until {bp_str}: {meth}")
        method = "; ".join(method_parts)
    else:
        p_interp, method = _interp_pressure_for_hab(
            target_info["hab"],
            sorted_sources,
            target_time,
            serial,
            used=used,
        )

    # Preserve bad original pressure
    if pressure_bad_flag and "pressure" in ds.data_vars:
        orig_attrs = dict(ds["pressure"].attrs)
        orig_attrs["comment"] = (
            orig_attrs.get("comment", "") + " [original; flagged bad in YAML]"
        ).strip()
        ds["pressure_orig"] = ds["pressure"].copy()
        ds["pressure_orig"].attrs = orig_attrs
        ds["pressure_orig_qc"] = xr.Variable(
            "time",
            np.full(len(ds["time"]), pressure_qc_val, dtype=np.int8),
            attrs={"long_name": "quality flag for pressure_orig"},
        )
        # pressure_orig_qc is excluded from the stage3 flag-attr sweep (it keeps the
        # pre-interpolation flag, kept distinct from the interpolated pressure_qc),
        # so attach its OceanSITES flag attributes here.
        set_qc_attrs(ds, "pressure_orig")
        ds = ds.drop_vars("pressure")

    ds["pressure"] = xr.Variable(
        "time",
        p_interp,
        attrs={
            "units": "dbar",
            "long_name": "sea water pressure",
            "standard_name": "sea_water_pressure",
            "pressure_source": method,
            "pressure_source_serials": " ".join(
                sorted({str(s["serial"]) for s in used})
            ),
            # The inputs' flags (worst of ours and the originator's) over the
            # record; inputs flagged 4 or 9 are never used.
            "pressure_source_flags": "; ".join(
                _flag_summary(s)
                for s in sorted(
                    {id(s): s for s in used}.values(), key=lambda s: str(s["serial"])
                )
            ),
            "comment": "interpolated from neighbouring instrument(s)",
        },
    )
    ds["pressure_qc"] = xr.Variable(
        "time",
        # 8 where interpolated; 9 where no usable input left it missing.
        np.where(np.isfinite(p_interp), 8, 9).astype(np.int8),
        attrs={"long_name": "quality flag for pressure"},
    )
    return ds, method


def compute_adcp_bin_pressure(
    ds: xr.Dataset,
    lat: float,
    log_fn: Any = None,
) -> xr.Dataset:
    """Add ``bin_pressure(time, N_BINS)`` (dbar): transducer pressure plus the bins' tilt-reduced range.

    Parameters
    ----------
    ds : xarray.Dataset
        Stage-3 ADCP dataset with ``pressure(time)`` (dbar, at the head), ``range(N_BINS)``
        (m, from the transducer face) and orientation in ``orientation_yaml`` or
        ``orientation_instrument``.
    lat : float
        Latitude (decimal degrees north), for ``gsw.p_from_z``.
    log_fn : callable, optional
        Deprecated (removed in 0.7.0): configure logging instead.

    Returns
    -------
    xarray.Dataset
        *ds* with ``bin_pressure``; its ``tilt_correction`` is ``"applied"``,
        ``"none (firmware bin mapping)"``, ``"none (firmware config unknown)"`` or
        ``"none (pitch/roll unavailable)"``. Unchanged without ``pressure`` or ``range``,
        or with an originator ``bin_pressure``.

    Raises
    ------
    MooringError
        Orientation is not ``"up"`` or ``"down"``.

    Notes
    -----
    Offset per bin is ``gsw.p_from_z(-range_eff, lat)``, added for ``"down"`` and
    subtracted for ``"up"``. ``range_eff = range * cos(θ)``, with θ =
    ``arccos(cos(P_true) cos(roll))`` and ``P_true = arctan(tan(tilt1) cos(roll))``
    (RDI Coordinate Transformation manual §4.2, Eq. 8): the beam-mean, first-order
    correction. It is applied only when the firmware config is known and is not earth
    coordinates with bin mapping (which already place the bins); NaN pitch/roll gives NaN
    ``bin_pressure``.
    ``p_from_z`` is measured from the sea surface, not from the head: ~2–3 dbar at 300 m
    range from a 500 m transducer.
    """
    with logger.deprecated_log_fn(log_fn):
        return _compute_adcp_bin_pressure(ds, lat)


def _compute_adcp_bin_pressure(
    ds: xr.Dataset,
    lat: float,
) -> xr.Dataset:
    """Implement :func:`compute_adcp_bin_pressure` (without ``log_fn``)."""
    import gsw

    from oceanarray.config import parameters as params

    if (
        ds.get("bin_pressure", xr.DataArray()).attrs.get("bin_pressure_source")
        == adcp_geometry.ORIGINATOR_BIN_PRESSURE_ATTRS["bin_pressure_source"]
    ):
        log.info("  ADCP bin_pressure: kept from the originator (not re-derived)")
        return ds
    if "pressure" not in ds.data_vars or "range" not in ds.coords:
        log.warning("  ADCP bin_pressure: skipped (no pressure or range coord)")
        return ds

    orientation = ds.attrs.get("orientation_yaml") or ds.attrs.get(
        "orientation_instrument"
    )
    if str(orientation).lower() not in ("up", "down"):
        raise MooringError(
            f"ADCP orientation {orientation!r} is not 'up' or 'down'; set "
            "orientation in the mooring YAML"
        )
    sign = 1.0 if str(orientation).lower() == "down" else -1.0

    p_trans = ds["pressure"].values.astype(float)  # (time,)
    range_m = ds["range"].values.astype(float)  # (N_BINS,)

    # Decide whether to apply the knock-down tilt correction to the bin ranges, from the
    # firmware recording config Stage 1 surfaced (firmware_coordinate_system /
    # firmware_bin_mapping, from the RDI EX command):
    #   - earth + bin mapping on: the firmware already re-selected cells per beam for tilt,
    #     so each bin's nominal range *is* its true vertical distance — applying cos(tilt)
    #     again would double-correct.  Skip.
    #   - a known non-mapped recording (beam/instrument coords, or earth without bin
    #     mapping): the nominal range is the untilted vertical distance — apply cos(tilt).
    #   - unknown (attrs absent: a non-RDI ADCP, or a file predating this surfacing): we
    #     cannot tell, so DO NOT apply (fail closed) — this keeps the pre-correction values
    #     and avoids double-correcting a firmware-mapped file whose attrs were lost.  The
    #     correction only fires on positive evidence that the bins are *not* tilt-placed.
    firmware_cs = ds.attrs.get("firmware_coordinate_system")
    firmware_binmap = str(ds.attrs.get("firmware_bin_mapping", "")).lower()
    firmware_known = firmware_cs is not None
    firmware_mapped = str(firmware_cs).lower() == "earth" and firmware_binmap == "yes"
    has_tilt = "pitch" in ds.data_vars and "roll" in ds.data_vars

    tilt: np.ndarray | None = (
        None  # instrument-axis angle from vertical, radians (time,)
    )
    tilt_definition = "n/a"
    tilt_source = "n/a"
    if not firmware_known:
        tilt_correction = "none (firmware config unknown)"
        _warn = (
            "ADCP bin_pressure: firmware coordinate/bin-mapping config unknown "
            "(not an RDI raw file, or attrs lost) — cos(tilt) NOT applied; bins at nominal "
            "depth."
        )
    elif firmware_mapped:
        tilt_correction = "none (firmware bin mapping)"
        _warn = (
            "  ADCP bin_pressure: firmware earth + bin mapping — bins already tilt-placed; "
            "cos(tilt) NOT applied (would double-correct)."
        )
    elif has_tilt:
        # Angle of the instrument axis from vertical, with the RDI true-pitch conversion.
        tilt = np.radians(physics.instrument_tilt(ds))
        tilt_correction = "applied"
        tilt_definition = "arccos(cos(true_pitch)·cos(roll)); true_pitch = arctan(tan(tilt1)·cos(roll))"
        tilt_source = "pitch,roll"
        _n_nan = int(np.isnan(tilt).sum())
        _warn = (
            f"ADCP bin_pressure: {_n_nan} of {tilt.size} timesteps have missing "
            "pitch/roll — bin_pressure is NaN there (tilt could not be computed)."
            if _n_nan
            else ""
        )
    else:
        tilt_correction = "none (pitch/roll unavailable)"
        _warn = (
            "ADCP bin_pressure: coordinates are not firmware-mapped but pitch/"
            "roll are absent — cos(tilt) NOT applied; knocked-down bins sit at nominal depth."
        )
    if _warn:
        log.warning(_warn)

    # Tilt-reduced vertical range (2-D when the correction is applied, else the untilted 1-D
    # range broadcast over time).  The correction is the first-order, ensemble-mean form:
    # opposing beams average out the first-order asymmetry, so the mean vertical bin range is
    # range × cos(tilt).  gsw.p_from_z(z, lat): z is depth in m (negative = below surface).
    range_vert = range_m[np.newaxis, :]
    if tilt is not None:
        range_vert = range_vert * np.cos(tilt)[:, np.newaxis]
    dp = gsw.p_from_z(-range_vert, lat)
    bin_p = p_trans[:, np.newaxis] + sign * dp  # (time, N_BINS)

    ds["bin_pressure"] = xr.Variable(
        ("time", params.ADCP_BIN_DIM),
        bin_p.astype(np.float32),
        attrs={
            "long_name": "pressure at ADCP bin",
            "units": "dbar",
            "comment": (
                f"transducer pressure + sign({orientation}-looking) × "
                f"gsw.p_from_z(-range×cos(tilt), lat={lat:.2f}°); "
                f"tilt_correction={tilt_correction}"
            ),
            "tilt_correction": tilt_correction,
            "tilt_definition": tilt_definition,
            "tilt_source": tilt_source,
        },
    )
    _extra = ""
    if tilt is not None and np.isfinite(tilt).any():
        _td = np.rad2deg(tilt)
        _extra = f", tilt median={np.nanmedian(_td):.1f}° max={np.nanmax(_td):.1f}°"
    log.info(
        f"  ADCP bin_pressure: {ds['range'].size} bins, "
        f"orientation={orientation}, lat={lat:.2f}°, {tilt_correction}{_extra}"
    )
    return ds
