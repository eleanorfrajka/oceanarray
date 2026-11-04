"""Hydrographic profiles prepared as a climatology for :mod:`~oceanarray.pipeline.gridders.rapid_interp`."""

from __future__ import annotations

import logging

import numpy as np
import xarray as xr

from oceanarray.pipeline.gridders.rapid_interp import get_dims

_log = logging.getLogger(__name__)


def downsample_to_sparse(
    temp_profiles: np.ndarray,
    salt_profiles: np.ndarray,
    full_pressures: np.ndarray,
    sparse_pressures: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Downsample full T/S profiles to sparse pressure levels.

    Parameters
    ----------
    temp_profiles : np.ndarray
        Full temperature profiles, shape (n_profiles, n_pressures_full).
    salt_profiles : np.ndarray
        Full salinity profiles, shape (n_profiles, n_pressures_full).
    full_pressures : np.ndarray
        Full pressure levels corresponding to temp_profiles and salt_profiles, shape (n_pressures_full,).
    sparse_pressures : np.ndarray
        Target sparse pressure levels to sample, shape (n_pressures_sparse,).

    Returns
    -------
    temp_sparse : np.ndarray
        Sparse temperature profiles, shape ``(n_profiles, n_pressures_sparse)``.
        NaN where the target pressure is outside ``full_pressures``.
    salt_sparse : np.ndarray
        Sparse salinity profiles, same shape.  NaN at the same out-of-range
        levels.

    """
    n_profiles = temp_profiles.shape[0]
    all_temp = []
    all_salt = []

    for i in range(n_profiles):
        temp_sparse = np.interp(
            sparse_pressures,
            full_pressures,
            temp_profiles[i],
            left=np.nan,
            right=np.nan,
        )
        salt_sparse = np.interp(
            sparse_pressures,
            full_pressures,
            salt_profiles[i],
            left=np.nan,
            right=np.nan,
        )
        all_temp.append(temp_sparse)
        all_salt.append(salt_sparse)

    return np.array(all_temp), np.array(all_salt)


def process_dataset(
    ds: xr.Dataset,
    latlim: tuple[float, float] = (26.0, 27.0),
    lonlim: tuple[float, float] = (-77.0, -76.5),
    pgrid: np.ndarray = None,
) -> tuple[xr.Dataset, xr.Dataset]:
    """Filter and process a hydrographic dataset for use in training.

    This function selects a region of interest, extracts and downsamples profiles of
    temperature and salinity onto both standard and sparse pressure grids. It also computes
    potential density anomaly for both resolutions.

    Parameters
    ----------
    ds : xr.Dataset
        Input dataset containing hydrographic data including CT, SA, PRES, and metadata.
    latlim : tuple of float, optional
        Latitude limits for filtering, by default (26.0, 27.0).
    lonlim : tuple of float, optional
        Longitude limits for filtering, by default (-77.0, -76.5).
    pgrid : np.ndarray, optional
        Target pressure levels (dbar) for vertical interpolation.  When None,
        a 20 dbar grid is constructed automatically from 0 to the maximum
        observed pressure.

    Returns
    -------
    ds_standard : xr.Dataset
        Dataset downsampled to standard pressure levels.
    ds_sparse : xr.Dataset
        Dataset downsampled to sparse pressure levels.

    See Also
    --------
    verticalnn.data_utils.downsample_to_sparse : Used to interpolate to target pressure levels.
    verticalnn.config.STANDARD_PRESSURES : Standard pressure grid.
    verticalnn.config.SPARSE_PRESSURES : Sparse pressure grid.

    """
    from oceanarray.pipeline.gridders.climatology import (
        downsample_to_sparse as _downsample,
    )

    pres_key, time_key, pres_dim, time_dim = get_dims(ds)

    if pgrid is None:
        max_pres = np.nanmax(ds[pres_key].values)
        pgrid = np.arange(0, max_pres + 1, 20)

    # Extract variables
    TEMP_profiles = ds["TEMP"].values
    PSAL_profiles = ds["PSAL"].values
    CT_profiles = ds["CT"].values
    SA_profiles = ds["SA"].values
    PRES = ds[pres_key].values
    LAT = ds["LATITUDE"].values
    LON = ds["LONGITUDE"].values
    time = ds[time_key].values

    # Apply region of interest mask
    mask = (
        (latlim[0] <= LAT)
        & (latlim[1] >= LAT)
        & (lonlim[0] <= LON)
        & (lonlim[1] >= LON)
    )
    TEMP_profiles = TEMP_profiles[mask]
    PSAL_profiles = PSAL_profiles[mask]
    CT_profiles = CT_profiles[mask]
    SA_profiles = SA_profiles[mask]
    time = time[mask]
    LAT = LAT[mask]
    LON = LON[mask]

    # Downsample to standard pressures
    CT_standard, SA_standard = _downsample(CT_profiles, SA_profiles, PRES, pgrid)
    # Downsample to standard pressures
    temp_standard, salt_standard = _downsample(
        TEMP_profiles, PSAL_profiles, PRES, pgrid
    )
    standard_pressures = pgrid.flatten()

    # Create ds_standard
    return xr.Dataset(
        {
            "CT": ((time_dim, pres_dim), CT_standard),
            "SA": ((time_dim, pres_dim), SA_standard),
            "TEMP": ((time_dim, pres_dim), temp_standard),
            "PSAL": ((time_dim, pres_dim), salt_standard),
        },
        coords={
            time_key: (time_dim, time),
            pres_key: (pres_dim, standard_pressures),
            "LATITUDE": (time_dim, LAT),
            "LONGITUDE": (time_dim, LON),
        },
    )
