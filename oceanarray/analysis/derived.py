"""Quantities derived from a product for its report figures, computed once per page.

Physics lives here and drawing in :mod:`oceanarray.plotters`: a report page builds
these quantities once from the product it shows and hands them to every figure that
needs them.  Nothing here is written back to a product file.

Functions
---------
mooring_position : the mooring's latitude and longitude from a product's attributes.
speed_direction : current speed and the direction the water flows toward
    (from :mod:`oceanarray.physics`, shared with the pipeline).
level_of_max_speed : the level with the highest time-mean speed.
potential_temperature_of : potential temperature θ of a product's records.
potential_density_grid : σθ (or σ2) on a salinity × θ grid, for T-S contours.
potential_density_fields : σ0 and σ2 from θ and practical salinity.
potential_density : σ0 and σ2 of a product, for the grid page's sections.
n_squared : buoyancy frequency squared on a pressure grid.
seabed_pressure : the sea pressure at the seabed, from the water depth.
instrument_levels : each instrument of a stack with its median pressure.
velocity_coverage : the share of the record each level has velocity for.
velocity_levels : the grid levels the current roses and hodographs are drawn at.
sparse_velocity_levels : the levels left out of the roses for low coverage.
knockdown : per-instrument pressure records and design pressures of a stack.
derived_fields : the per-page bundle (position, θ, speed, direction).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import numpy as np

from oceanarray.physics import sigma_function, speed_direction

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)

#: Attribute pairs that may carry a product's position, in the order they are tried.
_POSITION_ATTRS: tuple[tuple[str, str], ...] = (
    ("seabed_latitude", "seabed_longitude"),
    ("deployment_latitude", "deployment_longitude"),
    ("latitude", "longitude"),
)


def mooring_position(
    ds: xr.Dataset, *, warn: bool = True
) -> tuple[float | None, float | None]:
    """Return the mooring's latitude and longitude from a product's attributes.

    Parameters
    ----------
    ds : xarray.Dataset
        A stage-3, stack or grid product.  Its global attributes are searched for
        ``seabed_*``, ``deployment_*`` and plain ``latitude`` / ``longitude``, in
        that order; a pair at exactly (0, 0) is an unfilled placeholder and skipped.
    warn : bool, optional
        Log a warning when the latitude or the longitude is unknown (default True).

    Returns
    -------
    tuple of (float or None, float or None)
        ``(latitude, longitude)`` in decimal degrees; None for a coordinate that is
        not recorded.  Never substitutes 0.
    """
    from oceanarray.location import parse_coordinate

    attrs = ds.attrs
    lat = lon = None
    for lat_key, lon_key in _POSITION_ATTRS:
        la = parse_coordinate(attrs.get(lat_key))
        lo = parse_coordinate(attrs.get(lon_key))
        if la == 0.0 and lo == 0.0:
            continue
        if lat is None and la is not None:
            lat = la
        if lon is None and lo is not None:
            lon = lo
        if lat is not None and lon is not None:
            break
    if warn and (lat is None or lon is None):
        missing = " and ".join(
            n for n, v in (("latitude", lat), ("longitude", lon)) if v is None
        )
        logger.warning(
            "%s: mooring %s unknown; quantities that need it are left out",
            attrs.get("mooring_name", "product"),
            missing,
        )
    return lat, lon


def level_of_max_speed(speed: np.ndarray, min_coverage: float = 0.70) -> int | None:
    """Return the column of *speed* with the highest time-mean speed.

    Parameters
    ----------
    speed : numpy.ndarray
        Current speed on ``(time, level)``.
    min_coverage : float, optional
        Only levels with at least this fraction of finite values compete
        (default 0.70), so a briefly covered level does not win on a few strong
        values; when no level qualifies, all do.

    Returns
    -------
    int or None
        The level index; None when every value is missing.
    """
    import warnings

    speed = np.asarray(speed, dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean_spd = np.nanmean(speed, axis=0, dtype=np.float64)
    if not np.any(np.isfinite(mean_spd)):
        return None
    coverage = np.isfinite(speed).sum(axis=0) / speed.shape[0]
    eligible = coverage >= min_coverage
    if not np.any(eligible & np.isfinite(mean_spd)):
        eligible = np.ones(speed.shape[1], dtype=bool)
    return int(np.nanargmax(np.where(eligible, mean_spd, np.nan)))


def potential_temperature_of(
    temperature: np.ndarray,
    salinity: np.ndarray,
    pressure: np.ndarray,
    latitude: float | None,
    longitude: float | None,
) -> np.ndarray | None:
    """Return potential temperature θ referenced to the surface (°C), by TEOS-10.

    Parameters
    ----------
    temperature, salinity : numpy.ndarray
        In-situ temperature (°C) and practical salinity, of one shape.
    pressure : numpy.ndarray
        Sea pressure (dbar), broadcastable to *temperature* (one value per sample,
        or the pressure levels along the last axis).
    latitude, longitude : float or None
        The mooring position, for Absolute Salinity.

    Returns
    -------
    numpy.ndarray or None
        θ of the shape of *temperature*; None when the position is unknown.
    """
    if latitude is None or longitude is None:
        return None
    from oceanarray.analysis.site import potential_temperature

    return potential_temperature(
        np.asarray(temperature, dtype=float),
        np.asarray(salinity, dtype=float),
        np.asarray(pressure, dtype=float),
        longitude,
        latitude,
    )


@lru_cache(maxsize=32)
def _density_grid(
    s_lo: float,
    s_hi: float,
    t_lo: float,
    t_hi: float,
    n_grid: int,
    longitude: float,
    latitude: float,
    reference_dbar: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    import gsw

    sg, tg = np.meshgrid(
        np.linspace(s_lo, s_hi, n_grid), np.linspace(t_lo, t_hi, n_grid)
    )
    sa = gsw.SA_from_SP(sg, 0.0, longitude, latitude)
    ct = gsw.CT_from_pt(sa, tg)
    sigma = sigma_function(reference_dbar)(sa, ct)
    return sg, tg, sigma


def potential_density_grid(
    s_lim: tuple[float, float],
    theta_lim: tuple[float, float],
    latitude: float,
    longitude: float,
    *,
    n_grid: int = 200,
    reference_dbar: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return potential density on a practical-salinity × θ grid, for T-S contours.

    Parameters
    ----------
    s_lim : tuple of float
        Practical-salinity range ``(low, high)``.
    theta_lim : tuple of float
        Potential-temperature range ``(low, high)`` (°C).
    latitude, longitude : float
        The mooring position, for Absolute Salinity (taken at the surface).
    n_grid : int, optional
        Grid points along each axis (default 200).
    reference_dbar : int, optional
        Reference pressure: 0 (σθ, default) or 2000 (σ2).

    Returns
    -------
    tuple of numpy.ndarray
        ``(S, θ, σ)`` on ``(n_grid, n_grid)``; σ is the potential density anomaly
        (kg m⁻³) at the reference pressure, from Conservative Temperature computed
        from θ.  Results are cached on the limits rounded to 0.001, so panels of one
        figure share a grid.
    """
    if reference_dbar not in (0, 2000):
        raise ValueError(f"reference_dbar must be 0 or 2000, not {reference_dbar}")
    return _density_grid(
        round(float(s_lim[0]), 3),
        round(float(s_lim[1]), 3),
        round(float(theta_lim[0]), 3),
        round(float(theta_lim[1]), 3),
        int(n_grid),
        float(longitude),
        float(latitude),
        int(reference_dbar),
    )


def potential_density_fields(
    theta: np.ndarray,
    salinity: np.ndarray,
    pressure: np.ndarray,
    latitude: float,
    longitude: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return σ0 and σ2 from potential temperature and practical salinity.

    Parameters
    ----------
    theta : numpy.ndarray
        Potential temperature referenced to the surface (°C).
    salinity : numpy.ndarray
        Practical salinity, of the shape of *theta*.
    pressure : numpy.ndarray
        Sea pressure (dbar), broadcastable to *theta*; used only for Absolute
        Salinity.
    latitude, longitude : float
        The mooring position.

    Returns
    -------
    tuple of numpy.ndarray
        ``(sigma0, sigma2)``: potential density anomaly referenced to the surface
        and to 2000 dbar (kg m⁻³), each over the whole array.
    """
    import gsw

    p = np.broadcast_to(np.asarray(pressure, dtype=float), np.shape(theta))
    sa = gsw.SA_from_SP(salinity, p, longitude, latitude)
    ct = gsw.CT_from_pt(sa, theta)
    return sigma_function(0)(sa, ct), sigma_function(2000)(sa, ct)


def n_squared(
    temperature: np.ndarray,
    salinity: np.ndarray,
    pressure: np.ndarray,
    latitude: float,
    longitude: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return buoyancy frequency squared N² between neighbouring pressure levels.

    Parameters
    ----------
    temperature, salinity : numpy.ndarray
        In-situ temperature (°C) and practical salinity on ``(pressure, time)``.
    pressure : numpy.ndarray
        The pressure levels (dbar), one per row.
    latitude : float
        Mooring latitude (gravity).
    longitude : float or None
        Mooring longitude, for Absolute Salinity.  When None, a warning is logged
        and 0 is used: Absolute Salinity depends on it only weakly.

    Returns
    -------
    n2 : numpy.ndarray
        N² (s⁻²) on ``(pressure − 1, time)``, by ``gsw.Nsquared``.
    p_mid : numpy.ndarray
        The mid-point pressure of each pair of levels (dbar), averaged over time.
    """
    import gsw

    if longitude is None:
        logger.warning(
            "mooring longitude unknown; N² uses longitude 0 for Absolute Salinity "
            "(a small effect)"
        )
        longitude = 0.0
    n_p, n_t = temperature.shape
    p_2d = np.broadcast_to(
        np.asarray(pressure, float)[:, np.newaxis], (n_p, n_t)
    ).copy()
    sa = gsw.SA_from_SP(salinity, p_2d, longitude, latitude)
    ct = gsw.CT_from_t(sa, temperature, p_2d)
    n2, p_mid = gsw.Nsquared(sa, ct, p_2d, lat=latitude)
    return n2, np.nanmean(p_mid, axis=1)


def seabed_pressure(ds: xr.Dataset) -> float | None:
    """Return the sea pressure (dbar) at the seabed, from the water depth.

    Parameters
    ----------
    ds : xarray.Dataset
        A product with the ``waterdepth`` (m) and position attributes.

    Returns
    -------
    float or None
        ``pressure_from_depth(waterdepth, latitude)``; None (with a warning when
        the depth is known but the latitude is not) when it cannot be computed.
    """
    try:
        waterdepth = float(ds.attrs.get("waterdepth", ""))
    except (TypeError, ValueError):
        return None
    if not np.isfinite(waterdepth) or waterdepth <= 0:
        return None
    lat, _lon = mooring_position(ds, warn=False)
    if lat is None:
        logger.warning(
            "%s: mooring latitude unknown; the water depth is not converted to a "
            "seabed pressure",
            ds.attrs.get("mooring_name", "product"),
        )
        return None
    from oceanarray.pipeline.pressure import pressure_from_depth

    return float(pressure_from_depth(waterdepth, lat))


@dataclass(frozen=True)
class InstrumentLevel:
    """One instrument of a stack: what it measured and where.

    Attributes
    ----------
    serial : str
        The instrument's serial number.
    instrument_type : str
        Its type (``microcat``, ``aquadopp``, ...).
    pressure : float
        Median of its pressure record (dbar).
    variables : frozenset of str
        The variables it has values for (``temperature``, ``salinity``,
        ``east_velocity``, ...).
    """

    serial: str
    instrument_type: str
    pressure: float
    variables: frozenset = field(default_factory=frozenset)


#: The variables :func:`instrument_levels` records an instrument as measuring.
_LEVEL_VARIABLES: tuple[str, ...] = (
    "temperature",
    "salinity",
    "conductivity",
    "dissolved_oxygen",
    "east_velocity",
    "north_velocity",
)


def instrument_levels(ds_stack: xr.Dataset | None) -> list[InstrumentLevel]:
    """Return each instrument of a stack with its median pressure, shallowest first.

    Parameters
    ----------
    ds_stack : xarray.Dataset or None
        A stack (``time`` × ``N_LEVELS``) with ``pressure``, ``serial`` and
        ``instrument_type``.  None (no stack beside the grid) gives no levels.

    Returns
    -------
    list of InstrumentLevel
        Instruments with a pressure record, sorted by median pressure.  Values
        are counted as the stack holds them (its flags are not applied).
    """
    import warnings

    if ds_stack is None or "pressure" not in ds_stack.data_vars:
        return []
    dim = "N_LEVELS"
    if dim not in ds_stack.dims:
        return []
    p = ds_stack["pressure"].transpose("time", dim).values.astype(float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(p, axis=0)
    serials = (
        [str(s) for s in ds_stack["serial"].values]
        if "serial" in ds_stack.variables
        else [str(i) for i in range(ds_stack.sizes[dim])]
    )
    types = (
        [str(t) for t in ds_stack["instrument_type"].values]
        if "instrument_type" in ds_stack.variables
        else [""] * len(serials)
    )
    present = {
        v: np.isfinite(ds_stack[v].transpose("time", dim).values).any(axis=0)
        for v in _LEVEL_VARIABLES
        if v in ds_stack.data_vars and ds_stack[v].dims == ("time", dim)
    }
    out = [
        InstrumentLevel(
            serial=serials[i],
            instrument_type=types[i],
            pressure=float(med[i]),
            variables=frozenset(v for v, has in present.items() if has[i]),
        )
        for i in range(len(serials))
        if np.isfinite(med[i])
    ]
    return sorted(out, key=lambda r: r.pressure)


#: Share of the record a grid level must have velocity for to get a current rose.
ROSE_MIN_FILLED: float = 0.5
#: Most current roses (and hodographs) drawn on a grid page.
ROSE_MAX_LEVELS: int = 4


def velocity_coverage(east: np.ndarray, north: np.ndarray) -> np.ndarray | None:
    """Return the share of the record each level has both velocity components for.

    Parameters
    ----------
    east, north : numpy.ndarray
        Velocity components on ``(time, level)``.

    Returns
    -------
    numpy.ndarray or None
        One share in [0, 1] per level; None when the arrays are not
        ``(time, level)`` or hold no time.
    """
    both = np.isfinite(np.asarray(east, float)) & np.isfinite(np.asarray(north, float))
    if both.ndim != 2 or both.shape[0] == 0:
        return None
    return both.sum(axis=0) / both.shape[0]


def sparse_velocity_levels(
    east: np.ndarray, north: np.ndarray, *, min_filled: float = ROSE_MIN_FILLED
) -> list[int]:
    """Return the levels left out of the roses and hodographs for low coverage.

    Parameters
    ----------
    east, north : numpy.ndarray
        Velocity components on ``(time, level)``, levels shallowest first.
    min_filled : float, optional
        The share :func:`velocity_levels` requires (default
        :data:`ROSE_MIN_FILLED`).

    Returns
    -------
    list of int
        Levels with some velocity but less than *min_filled* of the record,
        shallowest first.  Levels with no velocity at all (outside the current
        meters) are not listed.
    """
    filled = velocity_coverage(east, north)
    if filled is None:
        return []
    return np.flatnonzero((filled > 0) & (filled < min_filled)).tolist()


def velocity_levels(
    east: np.ndarray,
    north: np.ndarray,
    *,
    min_filled: float = ROSE_MIN_FILLED,
    n: int = ROSE_MAX_LEVELS,
) -> list[int]:
    """Return the grid levels the current roses and hodographs are drawn at.

    Parameters
    ----------
    east, north : numpy.ndarray
        Velocity components on ``(time, level)``, levels shallowest first.
    min_filled : float, optional
        Share of the record a level must have both components for (default
        :data:`ROSE_MIN_FILLED`).
    n : int, optional
        Most levels taken (default :data:`ROSE_MAX_LEVELS`), evenly from the shallowest to the deepest
        qualifying level, both always included.

    Returns
    -------
    list of int
        Level indices, shallowest first; empty when no level qualifies.
    """
    filled = velocity_coverage(east, north)
    if filled is None:
        return []
    ok = np.flatnonzero(filled >= min_filled).tolist()
    if len(ok) <= n:
        return ok
    picks = np.round(np.linspace(0, len(ok) - 1, n)).astype(int)
    return [ok[i] for i in dict.fromkeys(picks.tolist())]


@dataclass(frozen=True)
class KnockdownRecord:
    """One instrument's pressure record against its design position.

    Attributes
    ----------
    p_nom : float
        Design depth as sea pressure (dbar); NaN when the latitude is unknown.
    serial : str
        Instrument serial.
    pressure : numpy.ndarray
        Measured pressure samples (dbar), finite, interpolated values excluded.
    hab : float
        Design height above bottom (m).
    """

    p_nom: float
    serial: str
    pressure: np.ndarray
    hab: float

    def __iter__(self) -> Iterator[Any]:
        """Unpack as ``(p_nom, serial, pressure, hab)``."""
        return iter((self.p_nom, self.serial, self.pressure, self.hab))


@dataclass(frozen=True)
class Knockdown:
    """A stack's knockdown inputs, built once per report.

    Attributes
    ----------
    waterdepth : float
        Water depth at the mooring (m).
    latitude : float or None
        Mooring latitude; None when unknown (design pressures are then NaN).
    records : list of KnockdownRecord
        One per non-ADCP instrument with at least 10 measured pressure samples.
    """

    waterdepth: float
    latitude: float | None
    records: list[KnockdownRecord] = field(default_factory=list)

    def design_pressure(self, hab: float | np.ndarray) -> float | np.ndarray:
        """Return the sea pressure (dbar) at height *hab* (m) above the seabed.

        Parameters
        ----------
        hab : float or numpy.ndarray
            Height above bottom (m).

        Returns
        -------
        float or numpy.ndarray
            Pressure at depth ``waterdepth − hab``; NaN when the latitude is unknown.
        """
        if self.latitude is None:
            return np.full(np.shape(hab), np.nan) if np.ndim(hab) else float("nan")
        from oceanarray.pipeline.pressure import pressure_from_depth

        return pressure_from_depth(
            self.waterdepth - np.asarray(hab, float), self.latitude
        )


def knockdown(ds: xr.Dataset) -> Knockdown | None:
    """Return a stack's knockdown inputs: design pressures and measured pressures.

    Parameters
    ----------
    ds : xarray.Dataset
        Stack dataset with ``pressure`` (time, N_LEVELS), ``hab``, ``serial``,
        ``instrument_type``, optionally ``pressure_qc``, and the ``waterdepth`` and
        position attributes.

    Returns
    -------
    Knockdown or None
        None when the stack has no pressure or HAB, or no positive water depth.
        When the latitude is unknown a warning is logged once and every design
        pressure is NaN.
    """
    if "pressure" not in ds.data_vars or "hab" not in ds:
        return None
    try:
        waterdepth = float(ds.attrs.get("waterdepth", 0) or 0)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(waterdepth) or waterdepth <= 0:
        return None
    lat, _lon = mooring_position(ds, warn=False)
    if lat is None:
        logger.warning(
            "%s: mooring latitude unknown; design depths are not converted to "
            "pressure, so the knockdown reference is left out",
            ds.attrs.get("mooring_name", "stack"),
        )
    kd = Knockdown(waterdepth=waterdepth, latitude=lat)
    serials = ds["serial"].values
    habs = ds["hab"].values
    types = ds["instrument_type"].values
    pressure = ds["pressure"].values
    pqc = ds["pressure_qc"].values if "pressure_qc" in ds.data_vars else None
    for i in range(ds.sizes["N_LEVELS"]):
        if str(types[i]).lower() == "adcp":
            continue
        p_vals = pressure[:, i].astype(float, copy=True)
        if pqc is not None:
            qc = pqc[:, i] if pqc.ndim == 2 else pqc
            p_vals[qc == 8] = np.nan  # interpolated pressure is not a measurement
        valid = p_vals[np.isfinite(p_vals)]
        if len(valid) < 10:
            continue
        hab = float(habs[i])
        kd.records.append(
            KnockdownRecord(
                p_nom=float(kd.design_pressure(hab)),
                serial=str(serials[i]),
                pressure=valid,
                hab=hab,
            )
        )
    return kd


@dataclass(frozen=True)
class DerivedFields:
    """Quantities a report page derives once from its product.

    Attributes
    ----------
    latitude, longitude : float or None
        Mooring position; None when not recorded.
    theta : xarray.DataArray or None
        Potential temperature (°C), on the dimensions of ``temperature``; None
        without temperature, salinity, pressure or position.
    speed, direction : xarray.DataArray or None
        Current speed (units of the components) and direction toward which the
        water flows (degrees clockwise from north); None without both horizontal
        velocity components.

    Notes
    -----
    Potential density is not part of the bundle: only the grid page draws it, so
    it is computed there, once, by :func:`potential_density`.
    """

    latitude: float | None = None
    longitude: float | None = None
    theta: Any = None
    speed: Any = None
    direction: Any = None


def _pressure_like(ds: xr.Dataset) -> Any:
    """Return pressure broadcast to the temperature's dimensions, or None."""
    if "pressure" not in ds.data_vars and "pressure" not in ds.coords:
        return None
    p = ds["pressure"]
    return p.broadcast_like(ds["temperature"]).transpose(*ds["temperature"].dims)


def derived_fields(ds: xr.Dataset) -> DerivedFields:
    """Return the quantities a report page derives from its product, once.

    Parameters
    ----------
    ds : xarray.Dataset
        A stage-3 file, stack or grid.  θ needs ``temperature``, ``salinity``,
        ``pressure`` (variable or coordinate) and the position; speed and direction
        need ``east_velocity`` and ``north_velocity``.

    Returns
    -------
    DerivedFields
        The bundle; a field is None when its inputs are missing.  An unknown
        position is warned about once, here.
    """
    import xarray as xr

    lat, lon = mooring_position(ds)
    theta = None
    if {"temperature", "salinity"} <= set(ds.data_vars):
        p = _pressure_like(ds)
        if p is not None:
            values = potential_temperature_of(
                ds["temperature"].values, ds["salinity"].values, p.values, lat, lon
            )
            if values is not None:
                theta = xr.DataArray(
                    values,
                    dims=ds["temperature"].dims,
                    coords=ds["temperature"].coords,
                    name="potential_temperature",
                    attrs={
                        "units": "degree_Celsius",
                        "long_name": "potential temperature referenced to 0 dbar",
                        "comment": (
                            f"TEOS-10 pt0_from_t at {lat:.4f} N, {lon:.4f} E; "
                            "computed for the report, not stored in the product"
                        ),
                    },
                )
    speed = direction = None
    if {"east_velocity", "north_velocity"} <= set(ds.data_vars):
        speed, direction = speed_direction(ds["east_velocity"], ds["north_velocity"])
    return DerivedFields(
        latitude=lat,
        longitude=lon,
        theta=theta,
        speed=speed,
        direction=direction,
    )


def potential_density(
    ds: xr.Dataset, derived: DerivedFields
) -> dict[str, xr.DataArray] | None:
    """Return σ0 and σ2 on the dimensions of *ds*'s temperature, from θ.

    Parameters
    ----------
    ds : xarray.Dataset
        The product *derived* was computed from, with ``salinity`` and
        ``pressure`` (variable or coordinate).
    derived : DerivedFields
        Its derived quantities (θ and the position).

    Returns
    -------
    dict of str to xarray.DataArray, or None
        ``{"sigma0": …, "sigma2": …}``: potential density anomaly (kg m⁻³)
        referenced to the surface and to 2000 dbar, from θ and practical salinity
        (:func:`potential_density_fields`); None when θ is None.
    """
    import xarray as xr

    theta = derived.theta
    p = _pressure_like(ds) if "temperature" in ds.data_vars else None
    if theta is None or p is None or "salinity" not in ds.data_vars:
        return None
    s0, s2 = potential_density_fields(
        theta.values,
        ds["salinity"].values,
        p.values,
        derived.latitude,
        derived.longitude,
    )
    return {
        name: xr.DataArray(
            values,
            dims=theta.dims,
            coords=theta.coords,
            name=name,
            attrs={
                "units": "kg m-3",
                "long_name": f"potential density anomaly referenced to {ref}",
                "comment": "TEOS-10, from potential temperature and practical "
                "salinity; computed for the report, not stored in the product",
            },
        )
        for values, name, ref in ((s0, "sigma0", "0 dbar"), (s2, "sigma2", "2000 dbar"))
    }


__all__ = [
    "DerivedFields",
    "InstrumentLevel",
    "Knockdown",
    "KnockdownRecord",
    "derived_fields",
    "instrument_levels",
    "knockdown",
    "level_of_max_speed",
    "mooring_position",
    "n_squared",
    "potential_density",
    "potential_density_fields",
    "potential_density_grid",
    "potential_temperature_of",
    "seabed_pressure",
    "speed_direction",
    "sparse_velocity_levels",
    "velocity_coverage",
    "velocity_levels",
]
