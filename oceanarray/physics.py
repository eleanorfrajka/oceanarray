"""Seawater and current formulas shared by the readers, the pipeline and the report pages.

Imports nothing from oceanarray, so :mod:`oceanarray.readers`, :mod:`oceanarray.pipeline`
(writing the products) and :mod:`oceanarray.analysis` (the figures) share one
implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

#: Reference pressures (dbar) with a dedicated TEOS-10 function (``gsw.sigma0`` …
#: ``gsw.sigma4``); any other reference goes through ``gsw.pot_rho_t_exact``.
SIGMA_REFERENCES_DBAR: tuple[int, ...] = (0, 1000, 2000, 3000, 4000)


def flow_direction(east: Any, north: Any) -> Any:
    """Return the direction the water flows toward, in degrees clockwise from north.

    Parameters
    ----------
    east, north : numpy.ndarray or xarray.DataArray
        Eastward and northward velocity, of one shape and units.

    Returns
    -------
    numpy.ndarray or xarray.DataArray
        Of the type of *east*: in [0, 360), north 0 and east 90 (oceanographic
        "toward", the opposite of the meteorological "from"); NaN where either
        component is missing.
    """
    return np.mod(np.degrees(np.arctan2(east, north)), 360.0)


def speed_direction(east: Any, north: Any) -> tuple[Any, Any]:
    """Return current speed and the direction the water flows toward.

    Parameters
    ----------
    east, north : numpy.ndarray or xarray.DataArray
        Eastward and northward velocity, of one shape and units.

    Returns
    -------
    speed : numpy.ndarray or xarray.DataArray
        ``hypot(east, north)``, in the units of the components.
    direction : numpy.ndarray or xarray.DataArray
        :func:`flow_direction` of the components.
    """
    return np.hypot(east, north), flow_direction(east, north)


def tilt(pitch: Any, roll: Any) -> Any:
    """Return the angle of an instrument's axis from vertical, in degrees.

    ``arccos(cos(pitch)·cos(roll))``: the geometric tilt, which is larger than
    ``max(|pitch|, |roll|)`` whenever both are non-zero.

    Parameters
    ----------
    pitch, roll : numpy.ndarray
        Pitch and roll in degrees, of one shape.

    Returns
    -------
    numpy.ndarray
        Tilt in degrees; NaN where either angle is missing.
    """
    cos_t = np.cos(np.radians(pitch)) * np.cos(np.radians(roll))
    return np.degrees(np.arccos(np.clip(cos_t, -1.0, 1.0)))


def instrument_tilt(ds: Any) -> Any:
    """Return the geometric tilt of an instrument dataset, in degrees.

    :func:`tilt` of the dataset's pitch and roll (a missing one counts as 0). An RDI
    ADCP (``file_type == "rdi-raw"`` or a ``firmware_coordinate_system`` attr) records
    pitch as the raw tilt-1 reading; its true pitch is ``arctan(tan(tilt1)·cos(roll))``
    (RDI ADCP Coordinate Transformation manual), used here in place of the recorded pitch.
    Other instruments' pitch is used as recorded.

    Parameters
    ----------
    ds : xarray.Dataset
        With ``pitch`` and/or ``roll`` (degrees) on ``time``.

    Returns
    -------
    numpy.ndarray or None
        Tilt on ``time``; None when both pitch and roll are absent.
    """
    if "pitch" not in ds.data_vars and "roll" not in ds.data_vars:
        return None
    zeros = np.zeros(ds.sizes["time"])
    pitch = ds["pitch"].values.astype(float) if "pitch" in ds.data_vars else zeros
    roll = ds["roll"].values.astype(float) if "roll" in ds.data_vars else zeros
    if (
        ds.attrs.get("file_type") == "rdi-raw"
        or "firmware_coordinate_system" in ds.attrs
    ):
        pitch = np.degrees(
            np.arctan(np.tan(np.radians(pitch)) * np.cos(np.radians(roll)))
        )
    return tilt(pitch, roll)


def sigma_function(reference_dbar: int) -> Callable[[Any, Any], Any] | None:
    """Return the TEOS-10 potential-density function for *reference_dbar*.

    Parameters
    ----------
    reference_dbar : int
        Reference pressure in dbar.

    Returns
    -------
    callable or None
        ``gsw.sigma0`` … ``gsw.sigma4`` for a reference in
        :data:`SIGMA_REFERENCES_DBAR`, taking ``(SA, CT)``; None for any other
        reference.
    """
    import gsw

    return {
        0: gsw.sigma0,
        1000: gsw.sigma1,
        2000: gsw.sigma2,
        3000: gsw.sigma3,
        4000: gsw.sigma4,
    }.get(int(reference_dbar))


def potential_density_anomaly(
    absolute_salinity: np.ndarray,
    conservative_temperature: np.ndarray,
    reference_dbar: int,
    *,
    temperature: np.ndarray | None = None,
    pressure: np.ndarray | None = None,
) -> np.ndarray:
    """Return the potential density anomaly σ (kg m⁻³) at *reference_dbar*.

    Parameters
    ----------
    absolute_salinity : numpy.ndarray
        Absolute Salinity SA (g kg⁻¹).
    conservative_temperature : numpy.ndarray
        Conservative Temperature CT (°C), of the shape of *absolute_salinity*.
    reference_dbar : int
        Reference pressure (dbar).
    temperature, pressure : numpy.ndarray or None, optional
        In-situ temperature (°C) and sea pressure (dbar).  Needed only for a
        reference outside :data:`SIGMA_REFERENCES_DBAR`, which is computed as
        ``gsw.pot_rho_t_exact(SA, t, p, reference) − 1000``.

    Returns
    -------
    numpy.ndarray
        σ of the shape of the inputs.

    Raises
    ------
    ValueError
        For a reference outside :data:`SIGMA_REFERENCES_DBAR` without
        *temperature* and *pressure*.
    """
    fn = sigma_function(reference_dbar)
    if fn is not None:
        return fn(absolute_salinity, conservative_temperature)
    if temperature is None or pressure is None:
        raise ValueError(
            f"a {reference_dbar} dbar reference needs in-situ temperature and pressure"
        )
    import gsw

    return (
        gsw.pot_rho_t_exact(absolute_salinity, temperature, pressure, reference_dbar)
        - 1000.0
    )


def pressure_from_depth(depth_m: Any, latitude: float) -> Any:
    """Return sea pressure (dbar) at a depth below the surface, by TEOS-10.

    Parameters
    ----------
    depth_m : float or numpy.ndarray
        Depth below the sea surface (m, positive down).
    latitude : float
        Latitude (decimal degrees).

    Returns
    -------
    float or numpy.ndarray
        ``gsw.p_from_z(-depth_m, latitude)`` (dbar); 1–1.5 % more than the depth in m.
    """
    import gsw

    p = gsw.p_from_z(-np.asarray(depth_m, dtype=float), latitude)
    return float(p) if np.ndim(p) == 0 else p


def depth_from_pressure(pressure_dbar: Any, latitude: float) -> Any:
    """Return depth below the surface (m, positive down) at a sea pressure (dbar), by TEOS-10.

    Parameters
    ----------
    pressure_dbar : float or numpy.ndarray
        Sea pressure (dbar).
    latitude : float
        Latitude (decimal degrees).

    Returns
    -------
    float or numpy.ndarray
        Depth (m, positive down).
    """
    import gsw

    z = -gsw.z_from_p(np.asarray(pressure_dbar, dtype=float), latitude)
    return float(z) if np.ndim(z) == 0 else z
