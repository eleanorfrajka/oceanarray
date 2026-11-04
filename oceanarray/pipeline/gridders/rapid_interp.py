"""Physics-informed vertical interpolation scheme for sparse ocean profiles.

This module implements the core algorithm used in the RAPID array, which
reconstructs full-depth temperature and salinity profiles using climatological
vertical gradients.

Functions
---------
- spacing : Generate evenly spaced pressure levels from start to end.
- save_climatology : Save climatology fields as a NetCDF dataset.
- smooth_climatology : Apply running mean smoothing to climatology fields.
- interpolate_internal : Interpolate between two sparse profile points using gradient fields.
- extrapolate_boundary : Extrapolate into unsampled regions near profile boundaries.

References
----------
- Vertical interpolation based on climatological gradients (Johns et al., 2001).
- Adapted and translated from Matlab routines by T. Kanzow (2000).

"""

import pathlib
from datetime import datetime
from typing import Any

import gsw
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr
from matplotlib.colors import LinearSegmentedColormap
from scipy.integrate import cumulative_trapezoid
from scipy.interpolate import interp1d

from oceanarray import logger
from oceanarray.config import parameters as params
from oceanarray.logger import log_info


def spacing(p_start: float, p_end: float, step: float) -> np.ndarray:
    """Create an array of pressures from `p_start` to `p_end` using approximately uniform spacing.

    This helper function generates a 1D pressure array using either increasing or decreasing
    steps depending on the ordering of `p_start` and `p_end`. It ensures that the end point
    is included.

    Parameters
    ----------
    p_start : float
        Starting pressure [dbar].
    p_end : float
        Ending pressure [dbar].
    step : float
        Approximate pressure increment [dbar].

    Returns
    -------
    np.ndarray
        1D array of pressure values from `p_start` to `p_end` (inclusive).

    Notes
    -----
    This function is used internally by the vertical interpolation routines to construct
    pressure grids between adjacent observations.

    See Also
    --------
    verticalnn.rapid_interp.interpolate_internal : Uses this function to build vertical grids.

    """
    if p_start < p_end:
        return np.arange(p_start, p_end + step, step)
    return np.arange(p_start, p_end - step, -step)


def save_climatology(clim_ds: xr.Dataset, output_path: str | pathlib.Path) -> None:
    """Save a climatology dataset containing vertical gradients to a NetCDF file.

    This function writes the input dataset—typically containing monthly fields
    of dT/dP and dS/dP as a function of temperature—to disk, adding metadata.

    Parameters
    ----------
    clim_ds : xr.Dataset
        Dataset containing climatological temperature and salinity gradients.
        Should include variables like 'dTdp', 'dSdp', and coordinate 'TEMP', 'month'.
    output_path : str or pathlib.Path
        Destination file path for the NetCDF file.

    Notes
    -----
    Adds metadata to the saved dataset:
    - `description`: Short description of contents.
    - `generated_by`: Marks source module (`verticalnn.rapid_interp`).
    - `created_on`: ISO-formatted timestamp of when the file was written.

    See Also
    --------
    verticalnn.rapid_interp.smooth_climatology : Smooths this dataset along the temperature axis.
    verticalnn.rapid_interp.build_climatology : Creates the dataset to be saved.

    """
    output_path = pathlib.Path(output_path)

    # Make sure parent folder exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Add some metadata
    clim_ds.attrs.update(
        {
            "description": "Monthly climatology of dT/dP and dS/dP vs temperature.",
            "generated_by": "verticalnn rapid_interp module",
            "created_on": datetime.now().isoformat(timespec="seconds"),
        }
    )

    # Save
    clim_ds.to_netcdf(output_path)

    log_info(f"✅ Saved climatology to {output_path}")


def smooth_climatology(clim_ds: xr.Dataset, window: int = 3) -> xr.Dataset:
    """Apply rolling smoothing to vertical gradient climatology along the temperature axis.

    This function smooths the dT/dP and dS/dP climatology fields using a centered
    moving average along the temperature bins, applied independently for each month.

    Parameters
    ----------
    clim_ds : xr.Dataset
        Input climatology dataset with variables 'dTdp' and 'dSdp' on (month, TEMP) grid.
    window : int, optional
        Rolling window size (number of temperature bins), by default 3.

    Returns
    -------
    xr.Dataset
        Smoothed climatology dataset with the same coordinates and attributes.

    Notes
    -----
    Rolling averages are computed with `center=True` and `min_periods=1`, preserving edge values.
    The smoothing is purely horizontal (along TEMP), not temporal.

    See Also
    --------
    verticalnn.rapid_interp.save_climatology : saves smoothed output to disk.

    """
    dTdp_smoothed = (
        clim_ds["dTdp"].rolling(TEMP=window, center=True, min_periods=1).mean()
    )
    dSdp_smoothed = (
        clim_ds["dSdp"].rolling(TEMP=window, center=True, min_periods=1).mean()
    )

    return xr.Dataset(
        {
            "dTdp": dTdp_smoothed,
            "dSdp": dSdp_smoothed,
        },
        coords=clim_ds.coords,
        attrs=clim_ds.attrs,
    )


def build_climatology(
    ds: xr.Dataset,
    standard_pressures: np.ndarray,
    temp_key: str = "CT",
    salt_key: str = "SA",
    pres_key: str = "PRESSURE",
    time_key: str = "TIME",
    min_profiles_per_bin: int = 5,
    temp_bins: np.ndarray | None = None,
) -> xr.Dataset:
    """Build a seasonal climatology of dT/dP and dS/dP as a function of temperature.

    This function processes hydrographic profiles to compute monthly climatologies
    of temperature and salinity vertical gradients. It bins gradients by temperature
    and month, and returns an xarray.Dataset with `dTdp` and `dSdp` fields indexed by
    temperature and time of year.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset containing temperature, salinity, pressure, and time variables.
    standard_pressures : np.ndarray
        Vertical pressure grid [dbar] to interpolate profiles before gradient calculation.
    temp_key : str, optional
        Variable name for Conservative Temperature, by default "CT".
    salt_key : str, optional
        Variable name for Absolute Salinity, by default "SA".
    pres_key : str, optional
        Variable name for Pressure, by default "PRESSURE".
    time_key : str, optional
        Variable name for time coordinate, by default "TIME".
    min_profiles_per_bin : int, optional
        Minimum number of valid profiles per (month, temp) bin to accept, by default 5.
    temp_bins : np.ndarray, optional
        Temperature bin edges for grouping (°C). If None, defaults to -2 to 35.5°C in 0.5°C bins.

    Returns
    -------
    xr.Dataset
        Climatology dataset with 'dTdp' and 'dSdp' indexed by ('month', 'TEMP').

    Notes
    -----
    - Interpolates all input profiles to `standard_pressures` before calculating gradients.
    - Based on the physics-informed interpolation approach described in Johns et al. (2001).
    - Original implementation adapted from Matlab code by T. Kanzow (2000).

    See Also
    --------
    verticalnn.rapid_interp.smooth_climatology : Applies smoothing to output climatology.
    verticalnn.rapid_interp.save_climatology : Saves climatology dataset to disk.
    verticalnn.rapid_interp.interpolate_profiles : Uses this climatology for profile interpolation.

    """
    if temp_bins is None:
        temp_bins = np.arange(-2, 35.5, 0.5)  # typical ocean range

    # Step 1: Downsample each profile to standard pressures
    pres_key, time_key, pres_dim, time_dim = get_dims(ds)
    profile_dim = time_dim
    ntime = ds.sizes[profile_dim]

    # Reshape pressure array to match the number of profiles
    if ds[pres_key].ndim == 2:
        pressure_array = ds[pres_key].values
    else:
        pressure_array = np.tile(ds[pres_key].values, (ntime, 1))

    temp_std = np.full((ntime, len(standard_pressures)), np.nan)
    salt_std = np.full((ntime, len(standard_pressures)), np.nan)

    for i in range(ntime):
        t_prof = ds[temp_key].isel({profile_dim: i}).values
        s_prof = ds[salt_key].isel({profile_dim: i}).values
        p_prof = pressure_array[i, :]

        mask = ~np.isnan(t_prof) & ~np.isnan(p_prof)

        if np.sum(mask) < 2:
            continue  # skip bad profiles

        temp_std[i, :] = np.interp(
            standard_pressures, p_prof[mask], t_prof[mask], left=np.nan, right=np.nan
        )
        salt_std[i, :] = np.interp(
            standard_pressures, p_prof[mask], s_prof[mask], left=np.nan, right=np.nan
        )

    # Step 2: Assign months
    months = pd.to_datetime(ds[time_key].values).month  # array of months (1–12)

    # Step 3: Compute gradients dT/dP and dS/dP
    dTdp = np.gradient(temp_std, standard_pressures, axis=1)
    dSdp = np.gradient(salt_std, standard_pressures, axis=1)

    # Step 4: Flatten arrays
    flat_temp = temp_std.flatten()
    flat_dTdp = dTdp.flatten()
    flat_dSdp = dSdp.flatten()
    flat_month = np.repeat(months, len(standard_pressures))

    # Step 5: Bin by temperature and month
    temp_bin_centers = 0.5 * (temp_bins[:-1] + temp_bins[1:])
    n_temp_bins = len(temp_bin_centers)

    dTdp_clim = np.full((12, n_temp_bins), np.nan)
    dSdp_clim = np.full((12, n_temp_bins), np.nan)

    for month in range(1, 13):
        mask_month = flat_month == month

        for j, (tmin, tmax) in enumerate(
            zip(temp_bins[:-1], temp_bins[1:], strict=False)
        ):
            mask_temp = (flat_temp >= tmin) & (flat_temp < tmax)

            combined_mask = mask_month & mask_temp

            if np.sum(combined_mask) >= min_profiles_per_bin:
                dTdp_clim[month - 1, j] = np.nanmean(flat_dTdp[combined_mask])
                dSdp_clim[month - 1, j] = np.nanmean(flat_dSdp[combined_mask])

    # Step 6: Build climatology xarray.Dataset
    return xr.Dataset(
        {
            "dTdp": (("month", "TEMP"), dTdp_clim),
            "dSdp": (("month", "TEMP"), dSdp_clim),
        },
        coords={
            "month": np.arange(1, 13),
            "TEMP": temp_bin_centers,
        },
        attrs={
            "method": "seasonal climatology built from downsampled CTD profiles",
            "temp_bin_width": temp_bins[1] - temp_bins[0],
        },
    )


def extrapolate_boundary(
    T: float,
    S: float,
    P: float,
    p_bound: float,
    dtdp_func,
    dsdp_func,
    int_step: float = 20.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Extrapolate temperature and salinity profiles from a boundary point using climatological gradients.

    This function integrates vertical gradients of temperature (dT/dP) and salinity (dS/dP)
    downward or upward from a single observed point to reach a specified pressure boundary.

    Parameters
    ----------
    T : float
        Starting temperature [°C].
    S : float
        Starting salinity [g/kg].
    P : float
        Starting pressure [dbar].
    p_bound : float
        Target pressure to extrapolate to [dbar].
    dtdp_func : function
        Callable that returns dT/dP as a function of T.
    dsdp_func : function
        Callable that returns dS/dP as a function of T.
    int_step : float, optional
        Integration step size [dbar], by default 20.0.

    Returns
    -------
    T_profile : np.ndarray
        Extrapolated temperature profile.
    S_profile : np.ndarray
        Extrapolated salinity profile.
    P_profile : np.ndarray
        Corresponding pressure levels.

    Notes
    -----
    This function integrates stepwise from the boundary point toward the pressure bound.
    The step direction is automatically inferred from the sign of `(p_bound - P)`.

    Based on the original Matlab routine `t_bound0.m` by T. Kanzow (2000).
    Method described in Johns et al. (2001).

    See Also
    --------
    verticalnn.rapid_interp.interpolate_internal : Used for interpolation between data points.
    verticalnn.rapid_interp.interpolate_profiles : Combines extrapolation and interpolation.

    """
    # Handle degenerate case early
    if p_bound == P:
        return np.array([T]), np.array([S]), np.array([P])

    # Ensure consistent stepping direction
    step = np.sign(p_bound - P) * abs(int_step)

    # Create pressure steps from P to p_bound (inclusive)
    inc = np.arange(P, p_bound, step)
    if inc.size == 0 or inc[-1] != p_bound:
        inc = np.append(inc, p_bound)

    # Evaluate gradients at each pressure step
    T_values = np.full_like(inc, np.nan)
    T_values[0] = T

    for i in range(1, len(inc)):
        T_values[i] = T_values[i - 1] + dtdp_func(T_values[i - 1]) * (
            inc[i] - inc[i - 1]
        )

    # Now compute gradients based on T_values
    dTdp_vals = dtdp_func(T_values)
    dSdp_vals = dsdp_func(T_values)

    # Integrate gradients
    T_profile = T + cumulative_trapezoid(dTdp_vals, inc, initial=0)
    S_profile = S + cumulative_trapezoid(dSdp_vals, inc, initial=0)
    P_profile = inc

    # Flatten to 1D arrays
    T_profile = T_profile.flatten()
    S_profile = S_profile.flatten()
    P_profile = P_profile.flatten()

    # Ensure output is sorted increasing in pressure
    if P_profile[0] > P_profile[-1]:
        T_profile = np.flip(T_profile)
        S_profile = np.flip(S_profile)
        P_profile = np.flip(P_profile)

    return T_profile, S_profile, P_profile


def interpolate_internal(
    T: np.ndarray,
    S: np.ndarray,
    P: np.ndarray,
    dtdp_func,
    dsdp_func,
    int_step: float = 20.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Interpolate between observed data points using climatological vertical gradients.

    This function fills gaps between observed pressure levels by integrating temperature
    and salinity gradients from both the upper and lower sensors and blending the results.
    It implements the logic of the original `t_int0.m` routine by Kanzow (2000).

    Parameters
    ----------
    T : np.ndarray
        Observed temperature values [°C].
    S : np.ndarray
        Observed salinity values [g/kg].
    P : np.ndarray
        Observed pressure values [dbar].
    dtdp_func : callable
        Function that returns dT/dP given temperature.
    dsdp_func : callable
        Function that returns dS/dP given temperature.
    int_step : float, optional
        Pressure increment for integration [dbar], by default 20.0.

    Returns
    -------
    np.ndarray
        Interpolated temperature values between observations.
    np.ndarray
        Interpolated salinity values between observations.
    np.ndarray
        Corresponding pressure values.

    Notes
    -----
    This function performs dual integration (from above and below) and blends the two
    estimates using linear weighting. Duplicate pressure values are removed in the final output.

    - Adapted from original Matlab function `t_int0.m`.
    - Author: T. Kanzow, 4 April 2000.
    - Part of the vertical interpolation scheme described in Johns et al. (2001):
      "The Kuroshio east of Taiwan: Moored transport observations from the WOCE PCM-1 Array."
      This version translated to Python and adapted for TEOS-10 Conservative Temperature and Absolute Salinity.

    See Also
    --------
    verticalnn.rapid_interp.extrapolate_boundary : For extrapolation above or below the data range.
    verticalnn.rapid_interp.interpolate_profiles : Applies this interpolation to all profiles in a dataset.

    """
    innerT = []
    innerS = []
    innerP = []

    ni = len(T) - 1  # number of separate gaps to fill

    for i in range(ni):
        p_start = P[i]
        p_end = P[i + 1]
        t_start = T[i]
        t_end = T[i + 1]
        s_start = S[i]
        s_end = S[i + 1]

        # Build pressure grid between points
        inc = np.arange(p_start, p_end, np.sign(p_end - p_start) * int_step)
        inc = np.append(inc, p_end)

        if len(inc) < 2:
            continue

        dinc_up = np.diff(inc)
        dinc_down = np.diff(inc[::-1])

        # Upward integration (from top sensor)
        tgrad1 = (
            dtdp_func(t_start)
            if np.isscalar(t_start)
            else dtdp_func(np.array([t_start]))[0]
        )
        sgrad1 = (
            dsdp_func(t_start)
            if np.isscalar(t_start)
            else dsdp_func(np.array([t_start]))[0]
        )
        T1 = t_start + cumulative_trapezoid(np.full_like(inc, tgrad1), inc, initial=0)
        S1 = s_start + cumulative_trapezoid(np.full_like(inc, sgrad1), inc, initial=0)

        # Downward integration (from bottom sensor)
        tgrad2 = (
            dtdp_func(t_end) if np.isscalar(t_end) else dtdp_func(np.array([t_end]))[0]
        )
        sgrad2 = (
            dsdp_func(t_end) if np.isscalar(t_end) else dsdp_func(np.array([t_end]))[0]
        )
        T2 = t_end + cumulative_trapezoid(
            np.full_like(inc[::-1], tgrad2), inc[::-1], initial=0
        )
        S2 = s_end + cumulative_trapezoid(
            np.full_like(inc[::-1], sgrad2), inc[::-1], initial=0
        )

        T2 = T2[::-1]
        S2 = S2[::-1]

        # Weights
        w1 = 1 - np.abs(inc - p_start) / np.abs(p_end - p_start)
        w2 = 1 - np.abs(inc - p_end) / np.abs(p_end - p_start)

        # Weighted average
        T_comb = w1 * T1 + w2 * T2
        S_comb = w1 * S1 + w2 * S2

        innerT.append(T_comb)
        innerS.append(S_comb)
        innerP.append(inc)

    if not innerT:
        return np.array([]), np.array([]), np.array([])

    innerT = np.concatenate(innerT)
    innerS = np.concatenate(innerS)
    innerP = np.concatenate(innerP)

    # Remove duplicate pressures
    _, unique_idx = np.unique(innerP, return_index=True)
    innerT = innerT[unique_idx]
    innerS = innerS[unique_idx]
    innerP = innerP[unique_idx]

    return innerT, innerS, innerP


def interpolate_profiles(
    ds: xr.Dataset,
    clim_ds: xr.Dataset,
    temp_key: str = "CT",
    salt_key: str = "SA",
    pres_key: str = "PRES",
    time_key: str = "TIME",
    p_grid: np.ndarray | None = None,
    int_step: float = 20.0,
    extrapolate: bool = True,
) -> xr.Dataset:
    """Interpolate sparse moored profiles onto a regular vertical grid using climatological gradients.

    This function performs both interpolation between observed levels and extrapolation
    beyond observed bounds using seasonal climatologies of dT/dP and dS/dP, following
    the physics-informed method described by Johns et al. (2001) and originally implemented
    in Matlab by T. Kanzow (2000).

    Parameters
    ----------
    ds : xr.Dataset
        Input dataset containing sparse temperature, salinity, and pressure profiles.
    clim_ds : xr.Dataset
        Climatology dataset with vertical gradients (variables: 'dTdp', 'dSdp') indexed by TEMP and month.
    temp_key : str, optional
        Variable name for Conservative Temperature, by default "CT".
    salt_key : str, optional
        Variable name for Absolute Salinity, by default "SA".
    pres_key : str, optional
        Variable name for pressure, by default "PRES".
    time_key : str, optional
        Variable name for profile time coordinate, by default "TIME".
    p_grid : np.ndarray, optional
        Target vertical grid for output profiles [dbar], by default `np.arange(0, 5000, 20)`.
    int_step : float, optional
        Step size for vertical integration [dbar], by default 20.
    extrapolate : bool, optional
        Whether to extrapolate above and below observed pressures, by default True.

    Returns
    -------
    xr.Dataset
        Interpolated dataset with gridded CT, SA, and SIGMA0 on the pressure grid.

    Notes
    -----
    - Uses temperature-dependent vertical gradients from `clim_ds`.
    - Combines logic of `con_tprof0.m`, `t_int0.m`, and `t_bound0.m` (Kanzow 2000).
    - Final dataset includes derived potential density (SIGMA0) using TEOS-10.

    See Also
    --------
    verticalnn.rapid_interp.build_climatology : Generates the dT/dP and dS/dP climatology.
    verticalnn.rapid_interp.interpolate_internal : Fills gaps between sensors.
    verticalnn.rapid_interp.extrapolate_boundary : Fills above/below observed range.

    """
    # Check if the climatology dataset has the required variables
    check_necessary_variables(clim_ds, vars=["dTdp", "dSdp"])
    check_necessary_variables(ds, vars=[temp_key, salt_key, "LATITUDE", "LONGITUDE"])

    pres_key, time_key1, pres_dim, time_dim = get_dims(ds)
    if time_key1 != time_key:
        raise ValueError(f"Time key mismatch: {time_key1} != {time_key}")

    if p_grid is None:
        p_grid = np.arange(0, 5000, 20)

    profile_dim = time_dim
    ntime = ds.sizes[profile_dim]

    t_con = np.full((ntime, len(p_grid)), np.nan)
    s_con = np.full((ntime, len(p_grid)), np.nan)

    TEMP = clim_ds["TEMP"].values

    # Reshape pressure array to match the number of profiles
    if ds[pres_key].ndim == 2:
        pressure_array = ds[pres_key].values
    else:
        pressure_array = np.tile(ds[pres_key].values, (ntime, 1))

    for ti in range(ntime):
        t_prof = ds[temp_key].isel({time_dim: ti}).values
        s_prof = ds[salt_key].isel({time_dim: ti}).values
        p_prof = pressure_array[ti, :]

        mask = ~np.isnan(t_prof) & ~np.isnan(p_prof)

        if np.sum(mask) < 2:
            continue  # Not enough data to interpolate

        t_good = t_prof[mask]
        s_good = s_prof[mask]
        p_good = p_prof[mask]

        # --- Select climatology for the month ---
        month_i = pd.to_datetime(ds[time_key].isel({time_dim: ti}).values).month

        cTg_month = clim_ds["dTdp"].sel(month=month_i).values
        cSg_month = clim_ds["dSdp"].sel(month=month_i).values

        dtdp_func = interp1d(
            TEMP,
            cTg_month,
            bounds_error=False,
            fill_value=(cTg_month[0], cTg_month[-1]),
        )
        dsdp_func = interp1d(
            TEMP,
            cSg_month,
            bounds_error=False,
            fill_value=(cSg_month[0], cSg_month[-1]),
        )

        segments = []

        # --- Extrapolate upward if needed
        if extrapolate and p_good[0] > p_grid.min():
            upT, upS, upP = extrapolate_boundary(
                T=t_good[0],
                S=s_good[0],
                P=p_good[0],
                p_bound=p_grid.min(),
                dtdp_func=dtdp_func,
                dsdp_func=dsdp_func,
                int_step=int_step,
            )
            mask_up = upP < p_good[0]
            segments.append((upT[mask_up], upS[mask_up], upP[mask_up]))

        # --- Interpolate internal points
        innerT, innerS, innerP = interpolate_internal(
            t_good,
            s_good,
            p_good,
            dtdp_func=dtdp_func,
            dsdp_func=dsdp_func,
            int_step=int_step,
        )
        segments.append((innerT, innerS, innerP))

        # --- Extrapolate downward if needed
        if extrapolate and p_good[-1] < p_grid.max():
            lowT, lowS, lowP = extrapolate_boundary(
                T=t_good[-1],
                S=s_good[-1],
                P=p_good[-1],
                p_bound=p_grid.max(),
                dtdp_func=dtdp_func,
                dsdp_func=dsdp_func,
                int_step=int_step,
            )
            mask_low = lowP > p_good[-1]
            segments.append((lowT[mask_low], lowS[mask_low], lowP[mask_low]))

        # --- Stitch segments together
        full_T = np.concatenate([seg[0] for seg in segments])
        full_S = np.concatenate([seg[1] for seg in segments])
        full_P = np.concatenate([seg[2] for seg in segments])

        # Sort by pressure
        sort_idx = np.argsort(full_P)
        full_T = full_T[sort_idx]
        full_S = full_S[sort_idx]
        full_P = full_P[sort_idx]

        # Interpolate onto regular p_grid
        t_interp = np.interp(p_grid, full_P, full_T, left=np.nan, right=np.nan)
        s_interp = np.interp(p_grid, full_P, full_S, left=np.nan, right=np.nan)

        t_con[ti, :] = t_interp
        s_con[ti, :] = s_interp

    sigma_con = gsw.sigma0(s_con, t_con)

    return xr.Dataset(
        {
            temp_key: ((time_dim, pres_dim), t_con),
            salt_key: ((time_dim, pres_dim), s_con),
            "SIGMA0": ((time_dim, pres_dim), sigma_con),
        },
        coords={
            pres_key: (pres_dim, p_grid),
            time_key: (time_dim, ds[time_key].values),
            "LATITUDE": (time_dim, ds["LATITUDE"].values),
            "LONGITUDE": (time_dim, ds["LONGITUDE"].values),
        },
        attrs={
            "method": "vertical interpolation using climatological gradients with seasonal adjustment"
        },
    )


def plot_climatology(
    clim_ds: xr.Dataset,
    var: str = "dTdp",
    clim_ds_smoothed: "xr.Dataset | None" = None,
    fig: Any = None,
    ax: Any = None,
) -> tuple[Any, Any]:
    """Plot the seasonal climatology of dT/dP or dS/dP, optionally with a smoothed overlay.

    Lives here because it visualises the climatological gradient field this module
    builds. Follows the composable ``fig=None, ax=None`` convention.

    Parameters
    ----------
    clim_ds : xarray.Dataset
        Raw climatology dataset with ``dTdp`` and/or ``dSdp``.
    var : str, optional
        Variable to plot (``"dTdp"`` or ``"dSdp"``). Default ``"dTdp"``.
    clim_ds_smoothed : xarray.Dataset, optional
        Smoothed climatology dataset to overlay. When given, the raw climatology
        is drawn in grey behind it. Default None.
    fig : matplotlib.figure.Figure, optional
        Existing figure to draw on; a new one is created when None.
    ax : matplotlib.axes.Axes, optional
        Existing axes to draw on; new axes are created when None.

    Returns
    -------
    tuple of (Figure, Axes)
        The figure and axes drawn on.

    """
    if var not in clim_ds:
        raise ValueError(f"{var} not found in climatology dataset.")

    with plt.style.context(str(params.MPLSTYLE)):
        if fig is None or ax is None:
            fig, ax = plt.subplots(figsize=(10, 6))

        months = np.arange(1, 13)
        # Seasonal colour cycle: cold → warm → cold.
        colors = [
            (0, 0, 1),  # Blue (January, cold)
            (0, 0.5, 1),  # Light blue (Spring)
            (0, 1, 0),  # Green (Early summer)
            (1, 1, 0),  # Yellow (Mid-summer, warm)
            (1, 0.5, 0),  # Orange (Late summer)
            (1, 0, 0),  # Red (July, peak warmth)
            (0.5, 0, 0.5),  # Purple (Autumn)
            (0, 0, 1),  # Blue (December, cold again)
        ]
        seasonal_colormap = LinearSegmentedColormap.from_list(
            "seasonal_cycle", colors, N=12
        )

        for month in months:
            TEMP = clim_ds["TEMP"]
            if clim_ds_smoothed is not None:
                ax.plot(
                    TEMP,
                    clim_ds[var].sel(month=month),
                    color="grey",
                    alpha=0.4,
                    linewidth=1,
                )
                ax.plot(
                    TEMP,
                    clim_ds_smoothed[var].sel(month=month),
                    linewidth=1.5,
                    label=f"Month {month}",
                    color=seasonal_colormap(month - 1),
                )
            else:
                ax.plot(
                    TEMP,
                    clim_ds[var].sel(month=month),
                    label=f"Month {month}",
                    color=seasonal_colormap(month - 1),
                    linewidth=1.5,
                )

        ax.set_xlabel("Temperature (°C)")
        ax.set_ylabel(f"{var} (per dbar)")
        ax.set_title(f"Monthly {var} Climatology")
        ax.legend(ncol=3, fontsize=8)
        ax.grid(True)
        plt.tight_layout()

        return fig, ax


def check_necessary_variables(ds: xr.Dataset, vars: list) -> None:
    """Check that all required variables are present in a dataset.

    Parameters
    ----------
    ds: xarray.Dataset
        Dataset that should be checked
    vars: list
        List of variables

    Raises
    ------
    KeyError:
        Raises an error if all vars not present in ds

    Notes
    -----
    Original Author: Callum Rollo

    """
    missing_vars = set(vars).difference(set(ds.variables))
    if missing_vars:
        msg = f"Required variables {list(missing_vars)} do not exist in the supplied dataset."
        raise KeyError(msg)


def get_dims(
    ds_gridded: xr.Dataset,
) -> tuple:
    """Extract pressure key, time key, and their dimensions from a dataset.

    Parameters
    ----------
    ds_gridded : xarray.Dataset
        Dataset containing the variables and dimensions.

    Returns
    -------
    pres_key : str or None
        Key for the pressure (or depth) variable; None if not found.
    time_key : str
        Key for the time variable.
    pres_dim : str or None
        Dimension associated with the pressure variable; None if not found.
    time_dim : str or None
        Dimension associated with the time variable.

    """
    if isinstance(ds_gridded, xr.DataArray):
        ds_gridded = ds_gridded.to_dataset()

    # Determine pressure key
    pressure_candidates = [
        "PRES_ADJUSTED",
        "PRESSURE",
        "pressure",
        "PRES",
        "PRES_INTERPOLATED",
    ]
    pres_key = None
    for name in pressure_candidates:
        if name in ds_gridded.coords or name in ds_gridded.variables:
            pres_key = name
            break

    # Fall back to depth if no pressure variable found
    dpth_key = None
    if pres_key is None:
        depth_candidates = [
            "DEPTH_ADJUSTED",
            "DEPTH",
            "depth",
            "Depth",
            "DEPTH_INTERPOLATED",
        ]
        for name in depth_candidates:
            if name in ds_gridded.coords or name in ds_gridded.variables:
                dpth_key = name
                break

    # Get time key using helper
    time_key = get_time_key(ds_gridded)

    # Determine dimensions
    time_dim = ds_gridded[time_key].dims[0] if ds_gridded[time_key].dims else None
    pres_dim = None
    if pres_key is None and dpth_key is not None:
        pres_dims = ds_gridded[dpth_key].dims
        if len(pres_dims) > 1:
            pres_dim = next(dim for dim in pres_dims if dim != time_dim)
        else:
            pres_dim = pres_dims[0]

    elif pres_key is not None:
        pres_dims = ds_gridded[pres_key].dims
        if len(pres_dims) > 1:
            pres_dim = next(dim for dim in pres_dims if dim != time_dim)
        else:
            pres_dim = pres_dims[0]

    return pres_key or dpth_key, time_key, pres_dim, time_dim


def get_time_key(ds: xr.Dataset) -> str:
    """Return the name of the time coordinate or variable in an xarray.Dataset.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset to inspect.

    Returns
    -------
    str
        The name of the time coordinate or variable.

    Raises
    ------
    ValueError
        If no time dimension or coordinate is found.

    """
    # Common time names to check
    time_candidates = ["TIME", "time", "Time", "DATETIME", "datetime", "date"]
    for name in time_candidates:
        if name in ds.coords or name in ds.variables:
            return name

    # Fallback: look for any variable or coordinate with datetime dtype
    for name in ds.coords:
        if np.issubdtype(ds.coords[name].dtype, np.datetime64):
            logger.log_warning(
                "No standard time coordinate found. Using first datetime coordinate: %s",
                name,
            )
            return name

    for name in ds.variables:
        if np.issubdtype(ds.variables[name].dtype, np.datetime64):
            logger.log_warning(
                "No standard time coordinate found. Using first datetime coordinate: %s",
                name,
            )
            return name

    raise ValueError("No valid time coordinate found in dataset.")
