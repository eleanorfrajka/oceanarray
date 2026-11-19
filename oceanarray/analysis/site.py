"""A site's gridded record over the years: display averaging, anomalies, medians, density.

The calculations behind the site grid page (:mod:`oceanarray.reports._gridcat`),
kept apart from the figures (:mod:`oceanarray.plotters.site`). They read a site
grid (``oceanarray.grid`` of a site stack) and, for the instrument pressures, its
site stack.
"""

from __future__ import annotations

import warnings
from typing import NamedTuple

import numpy as np
import xarray as xr

#: Display averaging by record length: ``(longest span in years, pandas
#: frequency, words)``; the first whose span exceeds the record's is used.
DISPLAY_STEPS: tuple[tuple[float, str, str], ...] = (
    (1.0, "1D", "daily"),
    (3.0, "7D", "weekly"),
    (20.0, "14D", "2-weekly"),
    (float("inf"), "1MS", "monthly"),
)


def display_step(time: np.ndarray) -> tuple[str, str]:
    """Return the averaging used to show a record of this length.

    Parameters
    ----------
    time : numpy.ndarray of datetime64
        The record's time axis.

    Returns
    -------
    tuple of str
        ``(frequency, words)``: a pandas frequency and its name, from
        :data:`DISPLAY_STEPS` (daily under 1 year, weekly under 3, 2-weekly
        under 20, monthly beyond).
    """
    years = (time[-1] - time[0]) / np.timedelta64(365, "D") if len(time) else 0.0
    return next((f, w) for longest, f, w in DISPLAY_STEPS if years < longest)


def display_steps_text() -> str:
    """Return :data:`DISPLAY_STEPS` in words, e.g. ``"daily under 1 year, ..., monthly beyond"``."""
    parts = [
        f"{words} under {longest:g} year{'s' if longest != 1 else ''}"
        if np.isfinite(longest)
        else f"{words} beyond"
        for longest, _, words in DISPLAY_STEPS
    ]
    return ", ".join(parts)


class DisplayBins(NamedTuple):
    """Averaging bins of a site grid's time axis (see :func:`display_bins`)."""

    #: The bins' edges in time order, one more than the bins.
    edges: np.ndarray
    #: The bins' centers.
    centers: np.ndarray
    #: Each time step's bin.
    step_bin: np.ndarray
    #: Each bin's deployment (-1 for a turnaround).
    owner: np.ndarray


def display_bins(
    time: np.ndarray, deployment_index: np.ndarray, freq: str
) -> DisplayBins:
    """Return averaging bins that never span two deployments.

    Each deployment's bins start at its first step and run every *freq* to its
    end, where the last bin is cut short; a turnaround is one bin of its own. So a
    difference between deployments shows as a sharp edge at the change.

    Parameters
    ----------
    time : numpy.ndarray of datetime64
        The grid's time axis (regular steps).
    deployment_index : numpy.ndarray
        The deployment counted at each step (-1 none).
    freq : str
        A pandas frequency (see :func:`display_step`).

    Returns
    -------
    DisplayBins
        The bins' edges and centers, each step's bin, and each bin's deployment.
    """
    import pandas as pd

    if not len(time):
        empty = np.array([], dtype=np.int64)
        none = np.array([], dtype="datetime64[ns]")
        return DisplayBins(none, none, empty, empty)
    time = time.astype("datetime64[ns]")
    step = time[1] - time[0] if len(time) > 1 else np.timedelta64(1, "h")
    change = np.flatnonzero(np.diff(deployment_index)) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(time)]])
    edges = [time[0]]
    for a, b in zip(starts, ends, strict=True):
        start, stop = time[a], time[b - 1] + step
        if deployment_index[a] >= 0:
            inside = pd.date_range(start, stop, freq=freq).values
            edges.extend(t for t in inside if start < t < stop)
        edges.append(stop)
    edges = np.array(edges, dtype="datetime64[ns]")
    bins = np.searchsorted(edges, time, side="right") - 1
    owner = np.full(len(edges) - 1, -1, dtype=np.int64)
    owner[bins] = deployment_index
    centers = edges[:-1] + (edges[1:] - edges[:-1]) / 2
    return DisplayBins(edges, centers, bins, owner)


#: A display bin of a section is shown only when its cell has finite values for at
#: least this fraction of the bin's time steps; an edge level that the grid fills
#: only part of the time (an instrument riding across it) is left blank instead of
#: showing a chequer of partial means.
DISPLAY_MIN_FRACTION: float = 0.5


def bin_means(
    values: np.ndarray, bins: np.ndarray, n_bins: int, *, min_fraction: float = 0.0
) -> np.ndarray:
    """Return the mean of the finite values of each bin.

    Sums are accumulated in float64 whatever the input type.

    Parameters
    ----------
    values : numpy.ndarray
        ``(time, n)``.
    bins : numpy.ndarray
        Each step's bin (``DisplayBins.step_bin``).
    n_bins : int
        Number of bins.
    min_fraction : float, optional
        Leave a bin NaN unless its finite values cover at least this fraction of
        the bin's time steps (default 0: any finite value counts).

    Returns
    -------
    numpy.ndarray
        ``(n_bins, n)``; NaN for a bin with no finite value, or with too few.
    """
    finite = np.isfinite(values)
    total = np.zeros((n_bins, values.shape[1]), dtype=np.float64)
    count = np.zeros((n_bins, values.shape[1]))
    np.add.at(total, bins, np.where(finite, values, 0.0).astype(np.float64))
    np.add.at(count, bins, finite)
    steps = np.bincount(bins, minlength=n_bins)[:, None]
    keep = (count > 0) & (count >= min_fraction * steps)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(keep, total / count, np.nan)


def display_means(ds: xr.Dataset, bins: DisplayBins) -> xr.Dataset:
    """Return the ``(time, pressure)`` variables of a grid averaged in deployment-bound bins.

    A cell is shown only where it has data for at least
    :data:`DISPLAY_MIN_FRACTION` of its bin.

    Parameters
    ----------
    ds : xarray.Dataset
        A grid on ``(time, pressure)``.
    bins : DisplayBins
        Bins of its time axis (:func:`display_bins`).

    Returns
    -------
    xarray.Dataset
        The means on ``(time, pressure)``, with ``time`` the bins' centers.
    """
    fields = [v for v in ds.data_vars if ds[v].dims == ("time", "pressure")]
    return xr.Dataset(
        {
            v: (
                ("time", "pressure"),
                bin_means(
                    ds[v].values,
                    bins.step_bin,
                    len(bins.centers),
                    min_fraction=DISPLAY_MIN_FRACTION,
                ),
                dict(ds[v].attrs),
            )
            for v in fields
        },
        coords={"time": bins.centers, "pressure": ds["pressure"].values},
    )


def median_anomaly(hourly: xr.DataArray, shown: xr.DataArray) -> xr.DataArray:
    """Return *shown* less the whole-record median at each level.

    The median is taken over the full-resolution values (*hourly*), so a
    spike or one bad instrument does not move the reference.

    Parameters
    ----------
    hourly : xarray.DataArray
        The variable on ``(time, pressure)``, at the grid's own time step.
    shown : xarray.DataArray
        The same variable as displayed (e.g. :func:`display_means`).

    Returns
    -------
    xarray.DataArray
        ``shown - median(hourly, time)``, with the attribute ``reference`` saying so.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a level with no data
        reference = hourly.median("time", skipna=True)
    out = shown - reference
    out.attrs = {
        **hourly.attrs,
        "reference": "whole-record median at each level",
    }
    return out


def slope_components(
    east: xr.DataArray, north: xr.DataArray, angle_deg: float
) -> tuple[xr.DataArray, xr.DataArray]:
    """Return the along- and across-slope velocity for an along-slope direction.

    Parameters
    ----------
    east, north : xarray.DataArray
        Velocity components.
    angle_deg : float
        The along-slope direction, degrees clockwise from north.

    Returns
    -------
    tuple of xarray.DataArray
        ``(along, across)``: along positive toward *angle_deg*, across positive
        toward *angle_deg* + 90°.
    """
    theta = np.radians(angle_deg)
    along = east * np.sin(theta) + north * np.cos(theta)
    across = east * np.cos(theta) - north * np.sin(theta)
    along.attrs = {"units": east.attrs.get("units", "m s-1")}
    across.attrs = {"units": east.attrs.get("units", "m s-1")}
    return along, across


def deployment_medians(
    values: np.ndarray,
    deployment_index: np.ndarray,
    n_deployments: int,
    *,
    min_fraction: float = 0.0,
) -> np.ndarray:
    """Return each deployment's median at each level.

    A level the deployment fills only part of the time is left out when it has
    values for less than *min_fraction* of the deployment's steps: at the edge of
    an instrument's range, a level is reached only when the mooring is knocked
    down, i.e. in strong flow, and its median would describe those hours only.

    Parameters
    ----------
    values : numpy.ndarray
        A variable on ``(time, pressure)``.
    deployment_index : numpy.ndarray
        The deployment counted at each step (-1 none).
    n_deployments : int
        Number of deployments.
    min_fraction : float, optional
        The share of a deployment's steps a level needs values for (0 to 1);
        0 keeps every level with a value.

    Returns
    -------
    numpy.ndarray
        ``(n_deployments, pressure)``; NaN where a deployment has no value, or
        too few.
    """
    out = np.full((n_deployments, values.shape[1]), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a level with no data
        for k in range(n_deployments):
            rows = values[deployment_index == k]
            if rows.size:
                med = np.nanmedian(rows, axis=0)
                covered = np.isfinite(rows).sum(axis=0) >= min_fraction * rows.shape[0]
                out[k] = np.where(covered, med, np.nan)
    return out


def density_fields(
    temperature: np.ndarray,
    salinity: np.ndarray,
    pressure: np.ndarray,
    longitude: float,
    latitude: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return potential density anomaly referenced to 0 and to 2000 dbar.

    Parameters
    ----------
    temperature, salinity : numpy.ndarray
        In-situ temperature (°C) and Practical Salinity, on ``(time, pressure)``.
    pressure : numpy.ndarray
        The pressure levels (dbar).
    longitude, latitude : float
        The site's position, for Absolute Salinity.

    Returns
    -------
    tuple of numpy.ndarray
        ``(sigma0, sigma2)`` in kg m⁻³, by TEOS-10.
    """
    import gsw

    from oceanarray.physics import potential_density_anomaly

    p = np.broadcast_to(pressure[None, :], temperature.shape)
    sa = gsw.SA_from_SP(salinity, p, longitude, latitude)
    ct = gsw.CT_from_t(sa, temperature, p)
    return tuple(potential_density_anomaly(sa, ct, ref) for ref in (0, 2000))


def instrument_pressures(
    stackcat: xr.Dataset, var: str, bins: DisplayBins
) -> np.ndarray:
    """Return, per depth, the pressure of the instruments measuring *var*, in display bins.

    Only the levels of the deployment counted at each step (as gridded), and only
    where that level has a finite *var*.

    Parameters
    ----------
    stackcat : xarray.Dataset
        The site stack the grid was made from (on the grid's time axis).
    var : str
        A variable of the site stack.
    bins : DisplayBins
        Bins of the time axis (:func:`display_bins`).

    Returns
    -------
    numpy.ndarray
        The pressure on ``(bin, N_DEPTHS)``; NaN where the depth does not measure *var*.
    """
    from oceanarray.pipeline.concat import own_levels

    keep = own_levels(stackcat) & np.isfinite(stackcat[var].values)
    pressure = np.where(keep, stackcat["pressure"].values.astype(float), np.nan)
    return bin_means(pressure, bins.step_bin, len(bins.centers))


def potential_temperature(
    temperature: np.ndarray,
    salinity: np.ndarray,
    pressure: np.ndarray,
    longitude: float,
    latitude: float,
) -> np.ndarray:
    """Return potential temperature referenced to the surface (°C), by TEOS-10.

    Parameters
    ----------
    temperature, salinity : numpy.ndarray
        In-situ temperature (°C) and Practical Salinity, on ``(..., pressure)``.
    pressure : numpy.ndarray
        The pressure levels (dbar), along the last axis.
    longitude, latitude : float
        The site's position, for Absolute Salinity.

    Returns
    -------
    numpy.ndarray
        θ, of the shape of *temperature*.
    """
    import gsw

    p = np.broadcast_to(pressure, temperature.shape)
    sa = gsw.SA_from_SP(salinity, p, longitude, latitude)
    return gsw.pt0_from_t(sa, temperature, p)


def instrument_mean_pressures(stackcat: xr.Dataset) -> list[list[float]]:
    """Return, per deployment, the mean pressure of each level that measures both T and S.

    For marking where a deployment's T-S curve is pinned by an instrument (the
    Grid cat T-S diagram); levels that measure temperature only (thermistors)
    are left out.

    Parameters
    ----------
    stackcat : xarray.Dataset
        A site stack with ``temperature``, ``salinity``, ``pressure`` and
        ``deployment_name``.

    Returns
    -------
    list of list of float
        One list per deployment, in deployment order: the mean pressures (dbar)
        of its T-and-S levels, shallow to deep.
    """
    from oceanarray.pipeline.concat import cell_deployment

    cells = cell_deployment(stackcat)
    t = stackcat["temperature"].values
    s = stackcat["salinity"].values
    p = stackcat["pressure"].values.astype(np.float64)
    both = np.isfinite(t) & np.isfinite(s) & np.isfinite(p)
    out: list[list[float]] = []
    for k in range(stackcat["deployment_name"].size):
        means = []
        for j in range(cells.shape[1]):
            rows = (cells[:, j] == k) & both[:, j]
            if rows.any():
                means.append(float(p[rows, j].mean()))
        out.append(sorted(means))
    return out
