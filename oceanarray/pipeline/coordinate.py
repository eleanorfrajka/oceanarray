"""BEAM→ENU coordinate transforms and ADCP-specific QC for stage 3."""

from __future__ import annotations

from typing import Any

import numpy as np
import xarray as xr


def xyz_to_enu(
    vx: np.ndarray,
    vy: np.ndarray,
    vz: np.ndarray,
    heading_deg: np.ndarray,
    pitch_deg: np.ndarray,
    roll_deg: np.ndarray,
    declination_deg: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised XYZ → ENU rotation per Nortek Support reference script.

    R = H @ P where (with hdg = heading - 90 + declination)::

        H = [[cos(hdg), sin(hdg), 0], [-sin(hdg), cos(hdg), 0], [0, 0, 1]]
        P = [[cos(p), -sin(p)*sin(r), -cos(r)*sin(p)],
             [0,       cos(r),        -sin(r)],
             [sin(p),  sin(r)*cos(p),  cos(p)*cos(r)]]

    The -90 offset accounts for the Nortek Aquadopp instrument frame where
    heading=90° aligns X→East, Y→North (standard geography at zero tilt).
    Magnetic declination is added to convert magnetic heading to true north.

    Returns (east, north, up) arrays of the same shape as the inputs.
    """
    h = np.radians(heading_deg - 90.0 + declination_deg)
    p = np.radians(pitch_deg)
    r = np.radians(roll_deg)

    ch = np.cos(h)
    sh = np.sin(h)
    cp = np.cos(p)
    sp = np.sin(p)
    cr = np.cos(r)
    sr = np.sin(r)

    # Expanded R = H @ P (verified against Nortek Support reference script)
    east = (
        ch * cp * vx + (-ch * sp * sr + sh * cr) * vy + (-ch * sp * cr - sh * sr) * vz
    )
    north = (
        -sh * cp * vx + (sh * sp * sr + ch * cr) * vy + (sh * sp * cr - ch * sr) * vz
    )
    up = sp * vx + sr * cp * vy + cp * cr * vz
    return east, north, up


def apply_beam_to_enu(
    ds: xr.Dataset,
    entry: dict[str, Any],
    lat: float,
    lon: float,
    latlon_source: str = "unknown",
    log_fn: Any = None,
) -> xr.Dataset:
    """Transform BEAM or XYZ Nortek velocities to ENU geographic coordinates.

    Adds east_velocity, north_velocity, up_velocity, current_speed,
    current_direction.  Updates coordinate_system attr to 'ENU'.
    A quiet no-op for instruments already in ENU.  For an unknown ('UNK')
    coordinate system it refuses loudly and does not rotate — the velocities
    are in an undetermined frame and must not be treated as geographic.
    Requires normalized variable names (heading, pitch, roll) — re-run stage1
    if these are absent.

    This rotates the velocity *vectors* only (and applies magnetic declination).
    It does not re-select range cells per beam (bin mapping) and does not change
    bin depths, so a knocked-down profiler's bins stay at their nominal range here;
    the per-bin *depth* tilt correction is applied separately by
    :func:`oceanarray.pipeline.pressure.compute_adcp_bin_pressure`.
    """

    def _warn(msg: str) -> None:
        if log_fn:
            log_fn(msg)
        else:
            print(msg)

    coord_sys = ds.attrs.get("coordinate_system", "ENU")
    if coord_sys == "UNK":
        _warn(
            f"  WARNING: coordinate_system is UNK for serial "
            f"{entry.get('serial', '?')} — the Nortek header did not declare "
            "BEAM/XYZ/ENU, so the velocities are in an undetermined frame and are "
            "NOT rotated to ENU. Do not treat east/north/up as geographic; re-run "
            "stage1 with the instrument header so the coordinate system is known."
        )
        ds.attrs["coordinate_system_source"] = (
            "unknown (header did not declare it); velocities not rotated to ENU"
        )
        return ds
    if coord_sys not in ("BEAM", "XYZ"):
        return ds

    # Require normalized heading/pitch/roll (stage1 normalization must have run)
    for vname in ("heading", "pitch", "roll"):
        if vname not in ds.data_vars:
            _warn(
                f"  WARNING: BEAM→ENU skipped — '{vname}' not in dataset. "
                "Re-run stage1 to get normalized variable names."
            )
            return ds

    heading = ds["heading"].values.astype(float)
    pitch = ds["pitch"].values.astype(float)
    roll = ds["roll"].values.astype(float)

    # Magnetic declination via ppigrf
    declination = 0.0
    if lat == 0.0 and lon == 0.0 and "unknown" in latlon_source.lower():
        _warn(
            f"  WARNING: BEAM→ENU — lat/lon unknown for serial {entry.get('serial', '?')}. "
            "Skipping magnetic declination to avoid silently applying the Gulf of Guinea value. "
            "Set lat/lon in the mooring YAML to get a correct declination correction."
        )
        ds.attrs["magnetic_declination"] = "UNK"
        ds.attrs["magnetic_declination_latlon_source"] = (
            f"mooring YAML ({latlon_source})"
        )
    else:
        try:
            import datetime as _dt

            import ppigrf

            time_vals = ds["time"].values
            t_mid = time_vals[len(time_vals) // 2]
            t_mid_s = int(t_mid.astype("datetime64[s]").astype("int64"))
            t_mid_dt = _dt.datetime.utcfromtimestamp(t_mid_s)
            Be, Bn, _ = ppigrf.igrf(float(lon), float(lat), 0.0, t_mid_dt)
            declination = float(
                np.degrees(
                    np.arctan2(float(np.atleast_1d(Be)[0]), float(np.atleast_1d(Bn)[0]))
                )
            )
            ds.attrs["magnetic_declination"] = declination
            # Declination is a plane angle in degrees (sign convention: positive
            # east).  NOT "degrees_east" — that is the CF unit for longitude.
            ds.attrs["magnetic_declination_units"] = "degree"
            ds.attrs["magnetic_declination_sign_convention"] = "positive_east"
            ds.attrs["magnetic_declination_method"] = (
                "ppigrf IGRF at deployment midpoint"
            )
            ds.attrs["magnetic_declination_lat"] = lat
            ds.attrs["magnetic_declination_lon"] = lon
            ds.attrs["magnetic_declination_latlon_source"] = (
                f"mooring YAML ({latlon_source})"
            )
            _warn(f"  BEAM→ENU: magnetic declination = {declination:.2f}°")
        except Exception as e:  # noqa: BLE001  — ppigrf optional; proceed with 0° declination
            _warn(f"  WARNING: magnetic declination unavailable ({e}) — using 0°")

    if coord_sys == "BEAM":
        # Stage1 could not apply the T matrix (header file missing or unparseable).
        # Re-run stage1 with the correct header file to produce velocity_x/y/z.
        _warn(
            f"  SKIPPING BEAM→ENU for serial {entry.get('serial', '?')}: "
            "data are still in BEAM coordinates — re-run stage1 with the instrument "
            "header file so the T matrix can be extracted and velocity_x/y/z produced."
        )
        return ds

    # XYZ — stage3 only needs to do XYZ→ENU
    if "velocity_x" in ds.data_vars:
        # stage1 applied the T matrix (BEAM→XYZ) and stored instrument-frame XYZ
        T_source = "applied in stage1 (BEAM→XYZ)"
        vx = ds["velocity_x"].values.astype(float)
        vy = ds["velocity_y"].values.astype(float)
        vz = ds["velocity_z"].values.astype(float)
    elif "x_velocity" in ds.data_vars:
        # Instrument natively reports XYZ; T matrix applied in firmware
        T_source = "not applicable (instrument-native XYZ)"
        vx = ds["x_velocity"].values.astype(float)
        vy = ds["y_velocity"].values.astype(float)
        vz = ds["z_velocity"].values.astype(float)
    else:
        # Legacy fallback: XYZ stored in beam variable slots
        T_source = "not applicable (legacy XYZ fallback)"
        vx = ds["velocity_beam1"].values.astype(float)
        vy = ds["velocity_beam2"].values.astype(float)
        vz = ds["velocity_beam3"].values.astype(float)

    # XYZ → ENU
    valid_all = (
        np.isfinite(vx)
        & np.isfinite(vy)
        & np.isfinite(vz)
        & np.isfinite(heading)
        & np.isfinite(pitch)
        & np.isfinite(roll)
    )
    east = np.full_like(vx, np.nan)
    north = np.full_like(vx, np.nan)
    up = np.full_like(vx, np.nan)
    if valid_all.any():
        east[valid_all], north[valid_all], up[valid_all] = xyz_to_enu(
            vx[valid_all],
            vy[valid_all],
            vz[valid_all],
            heading[valid_all],
            pitch[valid_all],
            roll[valid_all],
            declination,
        )

    speed = np.where(
        np.isfinite(east) & np.isfinite(north), np.sqrt(east**2 + north**2), np.nan
    )
    direction = np.where(
        np.isfinite(east) & np.isfinite(north),
        np.degrees(np.arctan2(east, north)) % 360.0,
        np.nan,
    )

    time_dim = ds["heading"].dims[0]
    for name, arr, cf, units, long_name in [
        (
            "east_velocity",
            east,
            "eastward_sea_water_velocity",
            "m s-1",
            "Eastward sea water velocity",
        ),
        (
            "north_velocity",
            north,
            "northward_sea_water_velocity",
            "m s-1",
            "Northward sea water velocity",
        ),
        (
            "up_velocity",
            up,
            "upward_sea_water_velocity",
            "m s-1",
            "Upward sea water velocity",
        ),
        (
            "current_speed",
            speed,
            "sea_water_speed",
            "m s-1",
            "Horizontal current speed",
        ),
        (
            "current_direction",
            direction,
            "direction_of_sea_water_velocity",
            "degrees",
            "Current direction (0=N, clockwise)",
        ),
    ]:
        ds[name] = xr.Variable(
            time_dim,
            arr,
            attrs={"units": units, "standard_name": cf, "long_name": long_name},
        )

    ds.attrs["coordinate_system"] = "ENU"
    ds.attrs["coordinate_system_source"] = (
        f"rotated from {coord_sys} by oceanarray stage3"
    )
    _warn(
        f"  BEAM→ENU: produced east/north/up_velocity, current_speed, current_direction "
        f"(coord_sys was {coord_sys}, T matrix: {T_source})"
    )
    return ds


def apply_declination_to_enu(
    ds: xr.Dataset,
    lat: float,
    lon: float,
    latlon_source: str = "unknown",
    log_fn: Any = None,
) -> xr.Dataset:
    """Apply magnetic declination rotation to velocities already in ENU frame.

    When a Nortek instrument is configured to output ENU coordinates internally,
    the heading reference used is magnetic north.  This function rotates
    east_velocity and north_velocity by the declination angle so that north
    aligns with true (geographic) north.

    Rotation (D = declination, positive = east):
        u_true = u_mag * cos(D) + v_mag * sin(D)
        v_true = -u_mag * sin(D) + v_mag * cos(D)

    No-ops if east_velocity or north_velocity are absent, or if magnetic
    declination has already been applied — by oceanarray (``magnetic_declination``
    attr present) or by the originator of an imported file
    (``originator_magnetic_declination`` attr present).
    """

    def _warn(msg: str) -> None:
        if log_fn:
            log_fn(msg)
        else:
            print(msg)

    if "magnetic_declination" in ds.attrs:
        return ds  # already applied
    if "originator_magnetic_declination" in ds.attrs:
        return ds  # applied by the file's originator (imported OceanSITES / RODB)

    if "east_velocity" not in ds.data_vars or "north_velocity" not in ds.data_vars:
        return ds

    if lat == 0.0 and lon == 0.0 and "unknown" in latlon_source.lower():
        _warn(
            "  WARNING: ENU declination correction skipped — lat/lon unknown. "
            "Applying declination from (0°N, 0°E) would silently rotate velocities by "
            "the Gulf of Guinea value. Set lat/lon in the mooring YAML."
        )
        ds.attrs["magnetic_declination"] = "UNK"
        ds.attrs["magnetic_declination_latlon_source"] = (
            f"mooring YAML ({latlon_source})"
        )
        return ds

    try:
        import datetime as _dt

        import ppigrf

        time_vals = ds["time"].values
        t_mid = time_vals[len(time_vals) // 2]
        t_mid_s = int(t_mid.astype("datetime64[s]").astype("int64"))
        t_mid_dt = _dt.datetime.utcfromtimestamp(t_mid_s)
        Be, Bn, _ = ppigrf.igrf(float(lon), float(lat), 0.0, t_mid_dt)
        declination = float(
            np.degrees(
                np.arctan2(float(np.atleast_1d(Be)[0]), float(np.atleast_1d(Bn)[0]))
            )
        )

        D = np.radians(declination)
        u = ds["east_velocity"].values.astype(float)
        v = ds["north_velocity"].values.astype(float)
        ds["east_velocity"].values[:] = u * np.cos(D) + v * np.sin(D)
        ds["north_velocity"].values[:] = -u * np.sin(D) + v * np.cos(D)

        ds.attrs["magnetic_declination"] = declination
        # Declination is a plane angle in degrees (sign convention: positive east).
        # NOT "degrees_east" — that is the CF unit for longitude.
        ds.attrs["magnetic_declination_units"] = "degree"
        ds.attrs["magnetic_declination_sign_convention"] = "positive_east"
        ds.attrs["magnetic_declination_method"] = "ppigrf IGRF at deployment midpoint"
        ds.attrs["magnetic_declination_lat"] = lat
        ds.attrs["magnetic_declination_lon"] = lon
        ds.attrs["magnetic_declination_latlon_source"] = (
            f"mooring YAML ({latlon_source})"
        )
        ds.attrs["coordinate_system_source"] = (
            "ENU from instrument; declination-corrected by oceanarray stage3"
        )
        _warn(
            f"  ENU declination correction: {declination:+.2f}° applied to "
            "east_velocity / north_velocity"
        )
    except Exception as e:  # noqa: BLE001  — ppigrf optional
        _warn(f"  WARNING: declination correction unavailable ({e}) — ENU unchanged")

    return ds


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
