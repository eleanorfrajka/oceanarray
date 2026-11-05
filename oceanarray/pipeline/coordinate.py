"""BEAM→ENU coordinate transforms for stage 3."""

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
            "sea_water_velocity_to_direction",
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
