"""BEAM→ENU coordinate transforms for stage 3."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import xarray as xr

from oceanarray import logger
from oceanarray.config import parameters as params
from oceanarray.physics import flow_direction

log = logging.getLogger(__name__)


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
    """Rotate BEAM or XYZ Nortek velocities to geographic ENU, in place, with magnetic declination.

    Adds ``east/north/up_velocity``, ``current_speed`` and ``current_direction`` and sets
    ``coordinate_system = "ENU"``; no-op for ENU input; refuses (no rotation) for ``"UNK"``.
    Needs ``heading``, ``pitch``, ``roll``. Rotates vectors only: bins keep their nominal
    range (the depth tilt correction is
    :func:`oceanarray.pipeline.pressure.compute_adcp_bin_pressure`).

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset to rotate (also returned).
    entry : dict
        The instrument's YAML entry (its ``serial`` names the messages).
    lat : float
        Deployment latitude, for the magnetic declination.
    lon : float
        Deployment longitude, for the magnetic declination.
    latlon_source : str, optional
        Where lat/lon came from (an unknown position is detected and recorded).
    log_fn : callable, optional
        Deprecated (removed in 0.7.0): configure logging instead.

    Returns
    -------
    xarray.Dataset
    """
    with logger.deprecated_log_fn(log_fn):
        return _beam_to_enu(ds, entry, lat, lon, latlon_source)


def _beam_to_enu(
    ds: xr.Dataset,
    entry: dict[str, Any],
    lat: float,
    lon: float,
    latlon_source: str = "unknown",
) -> xr.Dataset:
    """Implement :func:`apply_beam_to_enu` (without ``log_fn``)."""
    coord_sys = ds.attrs.get("coordinate_system", "ENU")
    if coord_sys == "UNK":
        log.warning(
            f"coordinate_system is UNK for serial "
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
            log.warning(
                f"BEAM→ENU skipped — '{vname}' not in dataset. "
                "Re-run stage1 to get normalized variable names."
            )
            return ds

    heading = ds["heading"].values.astype(float)
    pitch = ds["pitch"].values.astype(float)
    roll = ds["roll"].values.astype(float)

    # Magnetic declination via ppigrf
    declination = 0.0
    if lat == 0.0 and lon == 0.0 and "unknown" in latlon_source.lower():
        log.warning(
            f"BEAM→ENU — lat/lon unknown for serial {entry.get('serial', '?')}. "
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
            log.info(f"  BEAM→ENU: magnetic declination = {declination:.2f}°")
        except Exception as e:  # noqa: BLE001  — ppigrf optional; proceed with 0° declination
            log.warning(f"magnetic declination unavailable ({e}) — using 0°")

    if coord_sys == "BEAM":
        # Stage1 could not apply the T matrix (header file missing or unparseable).
        # Re-run stage1 with the correct header file to produce velocity_x/y/z.
        log.warning(
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

    # Speed as sqrt(e² + n²), not hypot: hypot differs from it in the last bit, and
    # stage-3 speeds stay as earlier versions of oceanarray wrote them.
    both = np.isfinite(east) & np.isfinite(north)
    speed = np.where(both, np.sqrt(east**2 + north**2), np.nan)
    direction = np.where(both, flow_direction(east, north), np.nan)

    time_dim = ds["heading"].dims[0]
    for name, arr in (
        ("east_velocity", east),
        ("north_velocity", north),
        ("up_velocity", up),
        ("current_speed", speed),
        ("current_direction", direction),
    ):
        ds[name] = xr.Variable(time_dim, arr, attrs=params.cf_attrs(name))

    ds.attrs["coordinate_system"] = "ENU"
    ds.attrs["coordinate_system_source"] = (
        f"rotated from {coord_sys} by oceanarray stage3; transformation matrix "
        f"{T_source}"
    )
    log.info(
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
    """Rotate ENU velocities from magnetic to true north, in place.

    ``u_true = u cos D + v sin D``, ``v_true = -u sin D + v cos D`` (D positive east). No-op
    without east/north velocity, or when ``magnetic_declination`` or
    ``originator_magnetic_declination`` shows it was applied.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset to rotate (also returned).
    lat : float
        Deployment latitude, for the magnetic declination.
    lon : float
        Deployment longitude, for the magnetic declination.
    latlon_source : str, optional
        Where lat/lon came from (an unknown position is detected and recorded).
    log_fn : callable, optional
        Deprecated (removed in 0.7.0): configure logging instead.

    Returns
    -------
    xarray.Dataset
    """
    with logger.deprecated_log_fn(log_fn):
        return _declination_to_enu(ds, lat, lon, latlon_source)


def _declination_to_enu(
    ds: xr.Dataset,
    lat: float,
    lon: float,
    latlon_source: str = "unknown",
) -> xr.Dataset:
    """Implement :func:`apply_declination_to_enu` (without ``log_fn``)."""
    if "magnetic_declination" in ds.attrs:
        return ds  # already applied
    if "originator_magnetic_declination" in ds.attrs:
        return ds  # applied by the file's originator (imported OceanSITES / RODB)

    if "east_velocity" not in ds.data_vars or "north_velocity" not in ds.data_vars:
        return ds

    if lat == 0.0 and lon == 0.0 and "unknown" in latlon_source.lower():
        log.warning(
            "ENU declination correction skipped — lat/lon unknown. "
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
        log.info(
            f"  ENU declination correction: {declination:+.2f}° applied to "
            "east_velocity / north_velocity"
        )
    except Exception as e:  # noqa: BLE001  — ppigrf optional
        log.warning(f"declination correction unavailable ({e}) — ENU unchanged")

    return ds
