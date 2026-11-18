"""Seawater and current formulas shared by the pipeline writers and the report pages.

A low-level module: it imports nothing from oceanarray, so both
:mod:`oceanarray.pipeline` (which writes these quantities into the products) and
:mod:`oceanarray.analysis` (which derives them for the figures) call the same
code, and neither has to import the other.

Functions
---------
flow_direction : direction the water flows toward, degrees clockwise from north.
speed_direction : current speed and flow direction.
sigma_function : the TEOS-10 potential-density function for a reference pressure.
potential_density_anomaly : σ at a reference pressure from Absolute Salinity and Conservative Temperature.
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


__all__ = [
    "SIGMA_REFERENCES_DBAR",
    "flow_direction",
    "potential_density_anomaly",
    "sigma_function",
    "speed_direction",
]
