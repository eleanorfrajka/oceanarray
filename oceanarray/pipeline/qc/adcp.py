"""ADCP QC steps: bins in the seabed or surface sidelobe zone, and velocity checks."""

from __future__ import annotations

from typing import Any

import numpy as np
import xarray as xr


def apply_adcp_seabed_qc(
    ds: xr.Dataset,
    water_depth_m: float,
    lat: float,
    fail_margin_m: float = 20.0,
    log_fn: Any = None,
) -> xr.Dataset:
    """Flag ADCP bins that are at or below the seabed.

    Bins whose ``bin_pressure`` exceeds the estimated seabed pressure are flagged
    suspect (3); bins more than *fail_margin_m* metres below the seabed are flagged
    bad (4).  The flag is stored as the standalone variable ``seabed_qc(time, N_BINS)``.

    **Flag scale** (OceanSITES / QARTOD convention):
    1 = good, 3 = suspect, 4 = bad, 9 = missing.

    **Standalone flag**: ``seabed_qc`` is *not* merged into ``east_velocity_qc``,
    ``north_velocity_qc``, or ``up_velocity_qc``.  Downstream code that needs clean
    velocity data must explicitly include all relevant flags, for example::

        clean = (ds.east_velocity_qc == 1) & (ds.seabed_qc == 1)

    The seabed pressure threshold is computed via ``gsw.p_from_z(-water_depth_m, lat)``,
    consistent with ``compute_adcp_bin_pressure``.

    Parameters
    ----------
    ds : xr.Dataset
        Stage 3 ADCP dataset containing ``bin_pressure(time, N_BINS)`` in dbar.
    water_depth_m : float
        Water depth at the mooring site in metres (from YAML ``waterdepth`` key).
        If ≤ 0 the function is a no-op.
    lat : float
        Mooring latitude in decimal degrees, used by ``gsw.p_from_z``.
    fail_margin_m : float
        Margin below the seabed (in metres) that separates suspect (3) from bad (4).
        Default 20 m.  Bins between 0 and *fail_margin_m* below the seabed receive
        flag 3; bins more than *fail_margin_m* below receive flag 4.
    log_fn : callable, optional
        Logging callback.

    Returns
    -------
    xr.Dataset
        Input dataset with ``seabed_qc(time, N_BINS)`` added.
        Returns *ds* unchanged if ``water_depth_m <= 0`` or ``bin_pressure`` is absent.

    """
    if water_depth_m <= 0:
        if log_fn:
            log_fn("  seabed QC: skipped (waterdepth not set in mooring YAML)")
        return ds
    if "bin_pressure" not in ds.data_vars:
        if log_fn:
            log_fn("  seabed QC: skipped (bin_pressure not in dataset)")
        return ds

    import gsw

    from oceanarray.config import parameters as params

    p_seabed = float(gsw.p_from_z(-water_depth_m, lat))
    p_fail = float(gsw.p_from_z(-(water_depth_m + fail_margin_m), lat))

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

    if log_fn:
        log_fn(
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
    """Flag ADCP bins that are at or above the sea surface.

    Bins whose ``bin_pressure`` is ≤ 0 dbar are flagged bad (4); bins within
    *suspect_margin_m* metres of the surface (0 < bin_pressure < p_suspect) are
    flagged suspect (3).  The result is stored as the standalone variable
    ``surface_qc(time, N_BINS)``.

    This catches upward-looking ADCP bins that extend above the water surface
    when the instrument range exceeds the distance to the surface.  Symmetric
    counterpart to ``apply_adcp_seabed_qc``.

    **Flag scale** (OceanSITES / QARTOD convention):
    1 = good, 3 = suspect, 4 = bad.

    **Standalone flag**: ``surface_qc`` is *not* merged into the velocity QC
    variables.  Downstream consumers must combine flags explicitly::

        clean = (ds.east_velocity_qc == 1) & (ds.surface_qc == 1) & (ds.seabed_qc == 1)

    Parameters
    ----------
    ds : xr.Dataset
        Stage 3 ADCP dataset containing ``bin_pressure(time, N_BINS)`` in dbar.
    lat : float
        Mooring latitude in decimal degrees, used by ``gsw.p_from_z``.
    suspect_margin_m : float
        Depth (m) below the surface that defines the suspect zone.  Bins with
        0 < bin_pressure < p_from_z(-suspect_margin_m) receive flag 3; bins
        with bin_pressure ≤ 0 receive flag 4.  Default 20 m.
    log_fn : callable, optional
        Logging callback.

    Returns
    -------
    xr.Dataset
        Input dataset with ``surface_qc(time, N_BINS)`` added.
        Returns *ds* unchanged if ``bin_pressure`` is absent.

    """
    if "bin_pressure" not in ds.data_vars:
        if log_fn:
            log_fn("  surface QC: skipped (bin_pressure not in dataset)")
        return ds

    import gsw

    from oceanarray.config import parameters as params

    p_suspect = float(gsw.p_from_z(-suspect_margin_m, lat))

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

    if log_fn:
        log_fn(
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
    """Apply QC to ADCP 2D velocity variables (time × N_BINS).

    Each QC criterion produces its **own standalone variable**; flags are
    **not** merged across variables.  Downstream users combine them to mask
    data, e.g.::

        good = (
            (ds.east_velocity_qc == 1)
            & (ds.percent_good_qc == 1)
            & (ds.error_velocity_qc == 1)
            & (ds.seabed_qc == 1)
            & (ds.surface_qc == 1)
        )

    QC variables produced
    ---------------------
    ``east/north/up_velocity_qc``
        Gross-range flag on the velocity component value itself (same
        fail_span / suspect_span thresholds as point instruments).  Flag 1
        means the velocity value is within the accepted range; it says nothing
        about acoustic quality.

    ``percent_good_qc(time, N_BINS)``
        RDI ADCPs write four percent-good columns per ensemble per bin:

        =========  =============================================================
        Col 0      % pings accepted as 3-beam solutions (one beam rejected)
        Col 1      % pings rejected by the error-velocity threshold
        Col 2      % pings rejected by low correlation or low amplitude
        **Col 3**  **% pings accepted as 4-beam solutions ← quality metric**
        =========  =============================================================

        Column 3 (4-beam solutions) is the relevant quality indicator.
        Averaging all four columns is wrong: when data is perfect, col 3 ≈
        100 % and cols 0–2 ≈ 0 %, giving a mean of ~25 %, which falls below
        any reasonable suspect threshold and flags everything.  This function
        therefore uses column 3 alone (falling back to the column mean for
        non-4-beam ADCPs that store fewer than 4 columns).

        Flags: col3 < *prcnt_gd_bad* → bad (4);
        col3 < *prcnt_gd_suspect* → suspect (3); otherwise good (1).

    ``error_velocity_qc(time, N_BINS)``
        For a 4-beam ADCP the error velocity is the difference between two
        independent estimates of vertical velocity from opposite beam pairs.
        It is zero for a perfect measurement; large values indicate beam
        decorrelation (e.g. fish, bubbles, mooring motion).
        ``|error_velocity|`` > *error_vel_threshold* → bad (4).

    Parameters
    ----------
    ds : xr.Dataset
        Stage 2 dataset with 2-D ADCP velocity variables.
    gr_cfg : dict
        Gross-range config (same format as ``load_qc_config`` returns).
    prcnt_gd_bad : float
        4-beam percent good below this → flag 4 (bad). Percent.
    prcnt_gd_suspect : float
        4-beam percent good below this (but above prcnt_gd_bad) → flag 3
        (suspect). Percent.
    error_vel_threshold : float
        ``|error_velocity|`` above this → flag 4 (bad). m s⁻¹.
    log_fn : callable, optional
        Logging callback.

    Returns
    -------
    xr.Dataset
        Input dataset with the following standalone QC variables added (where their
        parent data variables are present):

        - ``east_velocity_qc(time, N_BINS)`` — gross-range flag on east velocity
        - ``north_velocity_qc(time, N_BINS)`` — gross-range flag on north velocity
        - ``up_velocity_qc(time, N_BINS)`` — gross-range flag on up velocity
        - ``percent_good_qc(time, N_BINS)`` — 4-beam percent-good flag
        - ``error_velocity_qc(time, N_BINS)`` — error velocity magnitude flag

        All flags use the OceanSITES scale: 1 = good, 3 = suspect, 4 = bad, 9 = missing.
        Variables are standalone; no merging across criteria is performed.

    """
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
        pg = ds["percent_good"].values.astype(float)  # (time, N_BINS, beam)
        if pg.ndim == 3 and pg.shape[2] >= 4:
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
        if log_fn:
            log_fn(
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
        if log_fn:
            log_fn(
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
