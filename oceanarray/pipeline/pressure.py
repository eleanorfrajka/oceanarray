"""Pressure interpolation helpers for stage 3."""

from __future__ import annotations

from typing import Any

import numpy as np
import xarray as xr

from oceanarray.pipeline.qc import set_qc_attrs

HAB_THRESHOLD = 2.0  # metres — use near-neighbour below this Δhab


def _times_to_float(time_values: np.ndarray) -> np.ndarray:
    """Convert numpy datetime64 array to float64 nanoseconds (for numpy.interp)."""
    return time_values.astype("datetime64[ns]").astype(np.float64)


def _is_burst_mode(time_values: np.ndarray, burst_ratio: float = 5.0) -> bool:
    """Return True when the time series has a bimodal Δt (burst sampling).

    Burst-mode instruments (e.g. Nortek Aquadopp) take N pings at 1 Hz then
    wait ~120 s.  The within-burst interval (p50) is << the burst interval
    (p90).  The QARTOD spike test compares adjacent samples and generates false
    positives at every burst boundary, so callers skip it when this is True.
    """
    if len(time_values) < 10:
        return False
    dt = np.diff(time_values).astype("datetime64[s]").astype(float)
    dt = dt[dt > 0]
    if len(dt) == 0:
        return False
    p50 = float(np.percentile(dt, 50))
    p90 = float(np.percentile(dt, 90))
    return p50 > 0 and p90 > burst_ratio * p50


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


def interp_pressure_for_hab(
    hab_t: float,
    sorted_sources: list[dict[str, Any]],
    target_time: np.ndarray,
    serial: str,
    log_fn: Any | None = None,
    used: list[dict[str, Any]] | None = None,
) -> tuple[np.ndarray, str]:
    """Interpolate pressure for a single nominal HAB; return (p_array, method_str).

    Parameters
    ----------
    hab_t : float
        Target height above bottom in metres.
    sorted_sources : list of dict
        Pressure source instrument dicts sorted by hab, each with ``'ds'``,
        ``'hab'``, ``'pressure_var'``, ``'instrument'``, and ``'serial'`` keys.
    target_time : np.ndarray
        Target datetime64 time axis.
    serial : str
        Serial number of the target instrument (used in log messages only).
    log_fn : callable, optional
        Logging callback; falls back to print when None.
    used : list of dict, optional
        When given, the source dicts the interpolation drew on are appended to it.

    Returns
    -------
    tuple[np.ndarray, str]
        Interpolated pressure array and a human-readable method string.

    """
    habs = np.array([s["hab"] for s in sorted_sources])
    diffs = np.abs(habs - hab_t)
    nearest_idx = int(np.argmin(diffs))

    if diffs[nearest_idx] <= HAB_THRESHOLD:
        src = sorted_sources[nearest_idx]
        if used is not None:
            used.append(src)
        hab_offset_dbar = src["hab"] - hab_t
        p = (
            interp_pressure(
                src["ds"]["time"].values,
                src["ds"][src["pressure_var"]].values,
                target_time,
            )
            + hab_offset_dbar
        )
        method = (
            f"near-neighbour from {src['instrument']} {src['serial']} "
            f"(Δhab={diffs[nearest_idx]:.1f}m, "
            f"static offset={hab_offset_dbar:+.1f} dbar)"
        )
        if log_fn:
            log_fn(f"  {serial} (hab={hab_t:.1f}m): {method}")
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
            p_below = interp_pressure(
                src_below["ds"]["time"].values,
                src_below["ds"][src_below["pressure_var"]].values,
                target_time,
            )
            p_above = interp_pressure(
                src_above["ds"]["time"].values,
                src_above["ds"][src_above["pressure_var"]].values,
                target_time,
            )
            p = w_below * p_below + w_above * p_above
            method = (
                f"bracketed: {w_below:.2f}×{src_below['instrument']} "
                f"{src_below['serial']} (hab={h_below:.1f}m) + "
                f"{w_above:.2f}×{src_above['instrument']} "
                f"{src_above['serial']} (hab={h_above:.1f}m)"
            )
            if log_fn:
                log_fn(f"  {serial} (hab={hab_t:.1f}m): {method}")
        else:
            src = below[-1][0] if below else above[0][0]
            if used is not None:
                used.append(src)
            hab_offset_dbar = src["hab"] - hab_t
            p = (
                interp_pressure(
                    src["ds"]["time"].values,
                    src["ds"][src["pressure_var"]].values,
                    target_time,
                )
                + hab_offset_dbar
            )
            method = (
                f"extrapolated from {src['instrument']} {src['serial']} "
                f"(hab={src['hab']:.1f}m, "
                f"static offset={hab_offset_dbar:+.1f} dbar) — WARNING: out of range: "
                f"hab {hab_t:.1f} m is {'above' if below else 'below'} every pressure "
                f"source (hab {habs.min():.1f}–{habs.max():.1f} m): not bracketed; the "
                "offset takes 1 m of hab as 1 dbar and assumes the line stays upright"
            )
            if log_fn:
                log_fn(f"  WARNING: {serial} (hab={hab_t:.1f}m): {method}")

    return p, method


def interpolate_pressure(
    ds: xr.Dataset,
    target_info: dict[str, Any],
    sources: list[dict[str, Any]],
    target_time: np.ndarray,
    pressure_bad_flag: bool,
    log_fn: Any | None = None,
) -> tuple[xr.Dataset, str]:
    """Interpolate pressure from sources onto target; return (ds, method_str).

    If *target_info* contains ``hab_segments`` (a sorted list of
    ``(breakpoint_datetime64, hab_float)`` pairs), the record is split into
    time segments and each segment is interpolated with its own HAB, allowing
    for instruments that physically moved during the deployment.

    HAB convention for segmented deployments
    -----------------------------------------
    ``target_info["hab"]`` is the **initial** HAB — the height above bottom
    at which the instrument was **deployed**.  Segment 0 runs from the start
    of the record up to (but not including) the first breakpoint, using this
    value.  Each entry in ``hab_segments`` gives the new HAB **at and after**
    the breakpoint timestamp (i.e. records with ``T >= breakpoint`` use the
    new HAB; the breakpoint itself belongs to the new segment).  This matches
    the YAML convention where ``hab:`` records the as-deployed position and
    ``hab_segments:`` records subsequent slides, with ``from:`` marking the
    first timestamp that belongs to the post-slide position.

    Parameters
    ----------
    ds : xr.Dataset
        Stage 2 dataset for the target instrument.
    target_info : dict
        Instrument metadata dict with ``'hab'``, ``'hab_segments'``,
        ``'serial'``, and ``'qc_flags'`` keys.
    sources : list of dict
        Pressure source instrument dicts (each must have ``'ds'`` loaded).
    target_time : np.ndarray
        Target datetime64 time axis (must match ``ds["time"].values``).
    pressure_bad_flag : bool
        When True, the original pressure is preserved as ``pressure_orig``
        and ``pressure_orig_qc`` before being replaced.
    log_fn : callable, optional
        Logging callback.

    Returns
    -------
    tuple[xr.Dataset, str]
        Updated dataset and a human-readable method string.

    """
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
            p_seg, meth = interp_pressure_for_hab(
                hab_t, sorted_sources, T[idx], serial, log_fn=log_fn, used=used
            )
            p_interp[idx] = p_seg
            bp_str = str(seg_end_ns)[:16]
            method_parts.append(f"hab={hab_t:.1f}m until {bp_str}: {meth}")
        method = "; ".join(method_parts)
    else:
        p_interp, method = interp_pressure_for_hab(
            target_info["hab"],
            sorted_sources,
            target_time,
            serial,
            log_fn=log_fn,
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
            "comment": "interpolated from neighbouring instrument(s)",
        },
    )
    ds["pressure_qc"] = xr.Variable(
        "time",
        np.full(len(ds["time"]), 8, dtype=np.int8),
        attrs={"long_name": "quality flag for pressure"},
    )
    return ds, method


def compute_adcp_bin_pressure(
    ds: xr.Dataset,
    lat: float,
    log_fn: Any = None,
) -> xr.Dataset:
    """Compute pressure at each ADCP bin from transducer pressure and tilt-reduced range.

    For each time step, adds a pressure offset per bin, ``gsw.p_from_z(-range_eff, lat)``,
    the hydrostatic pressure of *range_eff* metres of seawater at the given latitude, where
    *range_eff* is the bin's vertical distance from the transducer reduced for instrument
    tilt (knock-down).

    **Tilt correction.** The RDI firmware converts slant range to vertical distance using
    the *nominal* beam angle (RDI Coordinate Transformation manual §4.2, Eq. 8), i.e.
    assuming the instrument is vertical.  When the mooring is knocked down by an angle θ the
    true vertical distance of each bin shrinks to ``range × cos(θ)``.  θ is the angle of the
    instrument axis from vertical, ``arccos(cos(P_true)·cos(roll))``, where ``P_true =
    arctan(tan(tilt1)·cos(roll))`` converts the raw tilt-1 pitch the RDI variable leader
    stores into the true pitch.  This is the first-order, ensemble-mean correction: the ADCP
    averages opposing beams, so the mean vertical bin range is ``range × cos(θ)`` (individual
    beams differ, consistent with velocity being a beam-averaged quantity); it is not a full
    3-beam geometry solution.  oceanarray's own BEAM→ENU
    (:func:`oceanarray.pipeline.coordinate.apply_beam_to_enu`) rotates velocity vectors only
    and does not bin-map, so beam/XYZ-coordinate bins reach this function at their nominal
    depth and need this correction; the firmware's earth + bin mapping is the only step in the
    pipeline that tilt-places the bins (and is the skip case below).

    **Guard — fail closed.** The correction is applied *only* on positive evidence that the
    bins are not already tilt-placed:

    - earth + bin mapping (``firmware_coordinate_system == "earth"`` and
      ``firmware_bin_mapping == "yes"``, surfaced by Stage 1 from the RDI ``EX`` command): the
      firmware already re-selected cells per beam for tilt, so each bin's nominal range is its
      true vertical distance → **skip** (``cos(tilt)`` would double-correct),
      ``tilt_correction = "none (firmware bin mapping)"``.
    - firmware config **unknown** (attrs absent — a non-RDI ADCP, or a file predating this
      surfacing) → **skip** (``"none (firmware config unknown)"``): absence must not be read as
      beam input, or a firmware-mapped file whose attrs were lost would be double-corrected.
    - no pitch/roll → **skip** (``"none (pitch/roll unavailable)"``).

    Every current UHH deployment is earth + bin-mapping, so in practice the apply path is
    exercised only by beam-coordinate input (none in the current UHH data); it is correct for
    such input and for future deployments.  Timesteps whose pitch/roll are NaN get a NaN
    ``bin_pressure`` (the depth is unknown) and a warning naming the count — the correction is
    never silently applied over missing attitude data.

    **Approximation** (unchanged, separate from tilt): ``gsw.p_from_z`` measures the column
    pressure from the *sea surface*, not the increment at the ADCP's depth — ~2–3 dbar at
    300 m range from a 500 m transducer, within the seabed-QC margin.

    The sign follows instrument orientation:

    - Downward-looking (``orientation == "down"``): bins are deeper than the
      transducer → pressure increases with bin index.
    - Upward-looking (``orientation == "up"``): bins are shallower than the
      transducer → pressure decreases with bin index.

    If orientation cannot be determined from dataset attributes a WARNING is emitted
    and ``"down"`` is assumed.

    Parameters
    ----------
    ds : xr.Dataset
        Stage 3 ADCP dataset.  Must contain ``pressure(time)`` in dbar (pressure at
        the transducer head) and ``range(N_BINS)`` in metres (bin centre distance from
        the transducer face).  Orientation is read from the ``orientation_yaml`` global
        attribute (preferred) or ``orientation_instrument`` (fallback).
    lat : float
        Mooring latitude in decimal degrees (positive North), used by
        ``gsw.p_from_z`` for the gravitational/centrifugal correction.
    log_fn : callable, optional
        Logging callback (e.g. ``logger.info``).

    Returns
    -------
    xr.Dataset
        Input dataset with ``bin_pressure(time, N_BINS)`` added in dbar.  The variable's
        ``comment`` records the formula and orientation; ``tilt_correction`` is ``"applied"``,
        ``"none (firmware bin mapping)"`` or ``"none (pitch/roll unavailable)"``, and
        ``tilt_definition`` records the tilt formula when applied.
        Returns *ds* unchanged if ``pressure`` or ``range`` are absent.

    """
    import gsw

    from oceanarray import parameters as params

    if "pressure" not in ds.data_vars or "range" not in ds.coords:
        if log_fn:
            log_fn("  ADCP bin_pressure: skipped (no pressure or range coord)")
        return ds

    orientation = ds.attrs.get("orientation_yaml") or ds.attrs.get(
        "orientation_instrument"
    )
    if not orientation:
        orientation = "down"
        if log_fn:
            log_fn(
                "  WARNING: ADCP orientation unknown (no orientation_yaml or "
                "orientation_instrument attr) — assuming downward-looking. "
                "bin_pressure signs may be wrong for upward-looking instruments."
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
            "  WARNING: ADCP bin_pressure: firmware coordinate/bin-mapping config unknown "
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
        # Angle of the instrument axis from vertical.  RDI stores pitch as the raw tilt-1
        # reading; the true pitch is arctan(tan(tilt1)·cos(roll)) (RDI Coordinate
        # Transformation manual), and the angle from vertical is arccos(cos P_true·cos R)
        # (roll is already the true roll, tilt-2).
        roll_r = np.deg2rad(ds["roll"].values.astype(float))  # (time,)
        p_true = np.arctan(
            np.tan(np.deg2rad(ds["pitch"].values.astype(float))) * np.cos(roll_r)
        )
        tilt = np.arccos(np.clip(np.cos(p_true) * np.cos(roll_r), -1.0, 1.0))
        tilt_correction = "applied"
        tilt_definition = "arccos(cos(true_pitch)·cos(roll)); true_pitch = arctan(tan(tilt1)·cos(roll))"
        tilt_source = "pitch,roll"
        _n_nan = int(np.isnan(tilt).sum())
        _warn = (
            f"  WARNING: ADCP bin_pressure: {_n_nan} of {tilt.size} timesteps have missing "
            "pitch/roll — bin_pressure is NaN there (tilt could not be computed)."
            if _n_nan
            else ""
        )
    else:
        tilt_correction = "none (pitch/roll unavailable)"
        _warn = (
            "  WARNING: ADCP bin_pressure: coordinates are not firmware-mapped but pitch/"
            "roll are absent — cos(tilt) NOT applied; knocked-down bins sit at nominal depth."
        )
    if log_fn and _warn:
        log_fn(_warn)

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
    if log_fn:
        _extra = ""
        if tilt is not None and np.isfinite(tilt).any():
            _td = np.rad2deg(tilt)
            _extra = f", tilt median={np.nanmedian(_td):.1f}° max={np.nanmax(_td):.1f}°"
        log_fn(
            f"  ADCP bin_pressure: {ds['range'].size} bins, "
            f"orientation={orientation}, lat={lat:.2f}°, {tilt_correction}{_extra}"
        )
    return ds
