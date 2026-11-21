"""ADCP QC steps: bins in the seabed or surface sidelobe zone, and velocity checks."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import xarray as xr

from oceanarray import logger

log = logging.getLogger(__name__)


def apply_adcp_seabed_qc(
    ds: xr.Dataset,
    water_depth_m: float,
    lat: float,
    fail_margin_m: float = 20.0,
    log_fn: Any = None,
) -> xr.Dataset:
    """Add ``seabed_qc(time, N_BINS)``: 3 for bins below the seabed, 4 beyond *fail_margin_m*.

    Parameters
    ----------
    ds : xarray.Dataset
        Stage-3 ADCP dataset with ``bin_pressure(time, N_BINS)`` (dbar).
    water_depth_m : float
        Water depth (m, the YAML ``waterdepth``); no-op if ≤ 0.
    lat : float
        Latitude (decimal degrees), for :func:`~oceanarray.pipeline.pressure.pressure_from_depth`.
    fail_margin_m : float, optional
        Metres below the seabed beyond which a bin is bad (4) rather than suspect (3).
    log_fn : callable, optional
        Deprecated (removed in 0.7.0): configure logging instead.

    Returns
    -------
    xarray.Dataset
        *ds* with ``seabed_qc`` (not merged into the velocity flags); unchanged without
        ``bin_pressure``.
    """
    with logger.deprecated_log_fn(log_fn):
        return _apply_adcp_seabed_qc(ds, water_depth_m, lat, fail_margin_m)


def _apply_adcp_seabed_qc(
    ds: xr.Dataset,
    water_depth_m: float,
    lat: float,
    fail_margin_m: float = 20.0,
) -> xr.Dataset:
    """Flag ADCP bins at or below the seabed; see :func:`apply_adcp_seabed_qc`."""
    if water_depth_m <= 0:
        log.warning("  seabed QC: skipped (waterdepth not set in mooring YAML)")
        return ds
    if "bin_pressure" not in ds.data_vars:
        log.warning("  seabed QC: skipped (bin_pressure not in dataset)")
        return ds

    from oceanarray.config import parameters as params
    from oceanarray.pipeline.pressure import pressure_from_depth

    p_seabed = pressure_from_depth(water_depth_m, lat)
    p_fail = pressure_from_depth(water_depth_m + fail_margin_m, lat)

    bin_p = ds["bin_pressure"].values  # (time, N_BINS)
    seabed_flags = np.ones(bin_p.shape, dtype=np.int8)  # 1 = good
    seabed_flags = np.where(bin_p > p_seabed, np.int8(3), seabed_flags)  # suspect
    seabed_flags = np.where(bin_p > p_fail, np.int8(4), seabed_flags)  # bad
    seabed_flags = seabed_flags.astype(np.int8)

    n_suspect = int(np.sum(seabed_flags == 3))
    n_bad = int(np.sum(seabed_flags == 4))

    ds["seabed_qc"] = xr.Variable(
        ("time", params.ADCP_BIN_DIM),
        seabed_flags,
        {
            "long_name": "Seabed proximity QC flag",
            "comment": (
                f"Bins at or below seabed ({water_depth_m:.0f} m, "
                f"p_seabed={p_seabed:.1f} dbar): flag 3 (suspect); "
                f"bins >{fail_margin_m:.0f} m below seabed "
                f"(p={p_fail:.1f} dbar): flag 4 (bad). "
                "Standalone — not merged into velocity_qc."
            ),
        },
    )

    log.info(
        f"  seabed QC: water_depth={water_depth_m:.0f} m, "
        f"p_seabed={p_seabed:.1f} dbar, "
        f"p_fail={p_fail:.1f} dbar (+{fail_margin_m:.0f} m), "
        f"suspect={n_suspect}, bad={n_bad}"
    )
    return ds


def apply_adcp_surface_qc(
    ds: xr.Dataset,
    lat: float,
    suspect_margin_m: float = 20.0,
    log_fn: Any = None,
) -> xr.Dataset:
    """Add ``surface_qc(time, N_BINS)``: 4 at or above the surface, 3 within *suspect_margin_m* of it.

    Parameters
    ----------
    ds : xarray.Dataset
        Stage-3 ADCP dataset with ``bin_pressure(time, N_BINS)`` (dbar).
    lat : float
        Latitude (decimal degrees), for the depth-to-pressure conversion.
    suspect_margin_m : float, optional
        Depth (m) of the suspect zone below the surface.
    log_fn : callable, optional
        Deprecated (removed in 0.7.0): configure logging instead.

    Returns
    -------
    xarray.Dataset
        *ds* with ``surface_qc`` (not merged into the velocity flags); unchanged without
        ``bin_pressure``.
    """
    with logger.deprecated_log_fn(log_fn):
        return _apply_adcp_surface_qc(ds, lat, suspect_margin_m)


def _apply_adcp_surface_qc(
    ds: xr.Dataset,
    lat: float,
    suspect_margin_m: float = 20.0,
) -> xr.Dataset:
    """Flag ADCP bins at or above the sea surface; see :func:`apply_adcp_surface_qc`."""
    if "bin_pressure" not in ds.data_vars:
        log.warning("  surface QC: skipped (bin_pressure not in dataset)")
        return ds

    from oceanarray.config import parameters as params
    from oceanarray.pipeline.pressure import pressure_from_depth

    p_suspect = pressure_from_depth(suspect_margin_m, lat)

    bin_p = ds["bin_pressure"].values  # (time, N_BINS)
    surface_flags = np.ones(bin_p.shape, dtype=np.int8)  # 1 = good
    surface_flags = np.where(bin_p < p_suspect, np.int8(3), surface_flags)  # suspect
    surface_flags = np.where(bin_p <= 0.0, np.int8(4), surface_flags)  # bad

    n_suspect = int(np.sum(surface_flags == 3))
    n_bad = int(np.sum(surface_flags == 4))

    ds["surface_qc"] = xr.Variable(
        ("time", params.ADCP_BIN_DIM),
        surface_flags,
        {
            "long_name": "Sea surface proximity QC flag",
            "comment": (
                f"Bins within {suspect_margin_m:.0f} m of sea surface "
                f"(bin_pressure < {p_suspect:.1f} dbar): flag 3 (suspect); "
                f"bins at or above surface (bin_pressure ≤ 0 dbar): flag 4 (bad). "
                "Standalone — not merged into velocity_qc."
            ),
        },
    )

    log.info(
        f"  surface QC: p_suspect={p_suspect:.1f} dbar ({suspect_margin_m:.0f} m), "
        f"suspect={n_suspect}, bad={n_bad}"
    )
    return ds


def apply_adcp_velocity_qc(
    ds: xr.Dataset,
    gr_cfg: dict[str, Any],
    prcnt_gd_bad: float,
    prcnt_gd_suspect: float,
    error_vel_threshold: float,
    log_fn: Any = None,
) -> xr.Dataset:
    """Add the standalone ADCP flags on ``(time, N_BINS)``; none is merged into another.

    ``east/north/up_velocity_qc`` are gross range on the value; ``percent_good_qc`` is 4
    below *prcnt_gd_bad* and 3 below *prcnt_gd_suspect*; ``error_velocity_qc`` is 4 where
    ``|error_velocity|`` exceeds *error_vel_threshold*.

    Parameters
    ----------
    ds : xarray.Dataset
        Stage-2 dataset with 2-D ADCP velocities.
    gr_cfg : dict
        Gross-range thresholds, as :func:`~oceanarray.pipeline.qc.load_qc_config` returns.
    prcnt_gd_bad : float
        Percent good (%) below which a bin is bad.
    prcnt_gd_suspect : float
        Percent good (%) below which a bin is suspect.
    error_vel_threshold : float
        Error-velocity magnitude (m s-1) above which a bin is bad.
    log_fn : callable, optional
        Deprecated (removed in 0.7.0): configure logging instead.

    Returns
    -------
    xarray.Dataset

    Notes
    -----
    RDI percent good has four columns; only column 3 (4-beam solutions) measures quality
    (the mean of all four is ~25 % for perfect data). Fewer columns: their mean is used; a
    ``percent_good`` on ``(time, N_BINS)`` is used as it is.
    """
    with logger.deprecated_log_fn(log_fn):
        return _apply_adcp_velocity_qc(
            ds, gr_cfg, prcnt_gd_bad, prcnt_gd_suspect, error_vel_threshold
        )


def _apply_adcp_velocity_qc(
    ds: xr.Dataset,
    gr_cfg: dict[str, Any],
    prcnt_gd_bad: float,
    prcnt_gd_suspect: float,
    error_vel_threshold: float,
) -> xr.Dataset:
    """Apply QC to ADCP 2D velocity variables; see :func:`apply_adcp_velocity_qc`."""
    # 1. Gross-range on each ENU velocity component independently.
    #    Flags only reflect whether that component's value is out of range.
    for varname in ("east_velocity", "north_velocity", "up_velocity"):
        if varname not in ds.data_vars:
            continue
        cfg = gr_cfg.get(varname)
        if not cfg:
            continue
        data = ds[varname].values.astype(float)
        fail_min, fail_max = cfg["fail_span"]
        susp_min, susp_max = cfg.get("suspect_span", cfg["fail_span"])
        flags = np.where(
            ~np.isfinite(data),
            np.int8(9),
            np.where(
                (data < fail_min) | (data > fail_max),
                np.int8(4),
                np.where((data < susp_min) | (data > susp_max), np.int8(3), np.int8(1)),
            ),
        ).astype(np.int8)
        qc_var = f"{varname}_qc"
        threshold_attrs: dict[str, Any] = {
            "qc_gross_range_fail_min": float(fail_min),
            "qc_gross_range_fail_max": float(fail_max),
            "qc_gross_range_suspect_min": float(susp_min),
            "qc_gross_range_suspect_max": float(susp_max),
        }
        ds[qc_var] = xr.Variable(
            ds[varname].dims,
            flags,
            attrs={
                "long_name": f"quality flag for {varname}",
                **threshold_attrs,
            },
        )

    # 2. percent_good QC — standalone variable, NOT merged into velocity_qc.
    #    RDI ADCPs store four percent-good columns per bin:
    #      [0] 3-beam solutions  [1] rejected (error vel)
    #      [2] rejected (low corr/amp)  [3] 4-beam solutions  ← the useful one
    #    Using column 3 only; averaging all columns gives ~25% even for
    #    perfect data (100% 4-beam, 0% others), which would flag everything.
    if "percent_good" in ds.data_vars:
        pg = ds["percent_good"].values.astype(float)  # (time, N_BINS[, beam])
        if pg.ndim == 2:
            pg_col3 = pg  # one value per bin
        elif pg.shape[2] >= 4:
            pg_col3 = pg[:, :, 3]  # 4-beam solutions column
        else:
            pg_col3 = np.nanmean(pg, axis=-1)  # fallback for non-4-beam ADCPs
        pg_flags = np.where(
            ~np.isfinite(pg_col3),
            np.int8(9),
            np.where(
                pg_col3 < prcnt_gd_bad,
                np.int8(4),
                np.where(pg_col3 < prcnt_gd_suspect, np.int8(3), np.int8(1)),
            ),
        ).astype(np.int8)
        n_bad_pg = int(np.sum(pg_flags == 4))
        n_susp_pg = int(np.sum(pg_flags == 3))
        log.info(
            f"  ADCP percent_good QC (col-3 4-beam): bad={n_bad_pg}, suspect={n_susp_pg} "
            f"(thresholds: bad<{prcnt_gd_bad}%, suspect<{prcnt_gd_suspect}%)"
        )
        from oceanarray.config import parameters as params

        ds["percent_good_qc"] = xr.Variable(
            ("time", params.ADCP_BIN_DIM),
            pg_flags,
            attrs={
                "long_name": "QC flag for ADCP percent good (4-beam solutions, column 3)",
                "comment": (
                    f"Based on RDI percent_good column 3 (4-beam solutions). "
                    f"bad<{prcnt_gd_bad}%, suspect<{prcnt_gd_suspect}%. "
                    "Standalone — not merged into velocity_qc."
                ),
            },
        )

    # 3. error_velocity QC — standalone variable, NOT merged into velocity_qc.
    if "error_velocity" in ds.data_vars:
        ev = ds["error_velocity"].values.astype(float)  # (time, N_BINS)
        ev_flags = np.where(
            ~np.isfinite(ev),
            np.int8(9),
            np.where(np.abs(ev) > error_vel_threshold, np.int8(4), np.int8(1)),
        ).astype(np.int8)
        n_ev_bad = int(np.sum(ev_flags == 4))
        log.info(
            f"  ADCP error_velocity QC: bad={n_ev_bad} "
            f"(threshold: |ev|>{error_vel_threshold:.2f} m s-1). Standalone."
        )
        ds["error_velocity_qc"] = xr.Variable(
            ds["error_velocity"].dims,
            ev_flags,
            attrs={
                "long_name": "QC flag for error velocity",
                "comment": (
                    f"|error_velocity| > {error_vel_threshold:.2f} m s-1 → bad (4). "
                    "Standalone — not merged into velocity_qc."
                ),
                "qc_threshold_fail_m_s": float(error_vel_threshold),
            },
        )

    return ds
