"""Hydrographic analysis utilities for oceanographic mooring data.

Salinity computation, isopycnal tracking, dataset differencing and
cold-regime detection.

Pairs with :mod:`oceanarray.plotters.hydrography` for figure output.
"""

from __future__ import annotations

import gsw
import numpy as np
import pandas as pd
import xarray as xr


def calc_psal(ds: xr.Dataset) -> xr.Dataset:
    """Compute Practical Salinity from conductivity, temperature, and pressure.

    Uses the Gibbs SeaWater (GSW) toolbox: ``gsw.SP_from_C`` applies the
    PSS-78 equation to derive Practical Salinity (dimensionless, roughly PSU)
    from conductivity (mS cm⁻¹), temperature (°C), and pressure (dbar).

    If ``PSAL`` is already present in *ds* the function is a no-op.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset containing ``CNDC`` (conductivity, mS cm⁻¹), ``TEMP``
        (temperature, °C), and ``PRES`` (pressure, dbar).

    Returns
    -------
    xarray.Dataset
        Input dataset with ``PSAL`` added (same dimensions as ``CNDC``),
        or unchanged if ``PSAL`` was already present.

    """
    if "PSAL" not in ds:
        SP = gsw.SP_from_C(ds["CNDC"], ds["TEMP"], ds["PRES"])
        ds["PSAL"] = (ds["CNDC"].dims, SP.data)
    else:
        print("ds already contains variable PSAL")
    return ds


def find_cold_entry_exit(
    time: np.ndarray,
    temp: np.ndarray,
    quantile: float = 0.95,
    dwell_seconds: int = 1800,
    smooth_window: int = 5,
) -> tuple[pd.Timestamp | None, pd.Timestamp | None, float]:
    """Identify first sustained entry into 'cold' regime and last sustained exit.

    Parameters
    ----------
    time : array-like of datetime64
        Time coordinate array.
    temp : array-like of float
        Temperature values aligned with *time*.
    quantile : float
        Percentile for threshold (e.g. 0.1 ~ 10th percentile).
    dwell_seconds : int
        Minimum time in cold regime for it to count (seconds).
    smooth_window : int
        Rolling median window length (samples).

    Returns
    -------
    t_start, t_end, threshold : tuple of (Timestamp or None, Timestamp or None, float)

    """
    time = pd.to_datetime(time)
    temp = np.asarray(temp, dtype=float)
    thr = np.nanquantile(temp, quantile)

    # smooth with rolling median
    if smooth_window > 1:
        temp = (
            pd.Series(temp)
            .rolling(smooth_window, center=True, min_periods=1)
            .median()
            .values
        )

    below = temp <= thr
    if not below.any():
        return None, None, thr

    # sampling interval (assume near-regular)
    dt = np.nanmedian(np.diff(time.values).astype("timedelta64[s]").astype(float))
    min_len = int(np.ceil(dwell_seconds / dt))

    # contiguous runs
    idx = np.where(below)[0]
    gaps = np.diff(idx) > 1
    starts = np.r_[idx[0], idx[1:][gaps]]
    ends = np.r_[idx[:-1][gaps], idx[-1]]

    runs = [
        (s, e) for s, e in zip(starts, ends, strict=False) if (e - s + 1) >= min_len
    ]
    if not runs:
        return None, None, thr

    s0, _ = runs[0]
    _, eL = runs[-1]
    return time[s0], time[eL], thr


def calc_ds_difference(ds1: xr.Dataset, ds2: xr.Dataset) -> xr.Dataset:
    """Compute the variable-by-variable difference between two time-matched datasets."""
    if not np.array_equal(ds1["TIME"].values, ds2["TIME"].values):
        raise ValueError("TIME grids do not match between datasets.")

    # Variables to exclude from differencing
    exclude_vars = {"YY", "MM", "DD", "HH"}

    # Prepare a dict for new data variables
    diff_data = {}

    for var in ds1.data_vars:
        # Only difference variables with dimension TIME and not in exclude list
        if "TIME" in ds1[var].dims and var not in exclude_vars:
            diff_data[var] = ds1[var] - ds2[var]
        else:
            # Copy over variables that are not differenced
            diff_data[var] = ds1[var]

    # Create a new dataset with the same coordinates and attributes
    ds_diff = ds1.copy()
    for var in diff_data:
        ds_diff[var] = diff_data[var]

    return ds_diff


def isopycnal_pressure_series(
    sigma0_tp: np.ndarray,
    pressure: np.ndarray,
    sigma_grid: np.ndarray,
) -> np.ndarray:
    """Find the pressure of each target density surface at every time step.

    Uses a "first crossing from shallow" approach: for each target σ₀ value,
    scans from the shallowest pressure downward and finds the first level where
    sigma0 transitions from below to at-or-above the target, then linearly
    interpolates between those two levels.  This is robust to non-monotonic
    sigma0 columns (which occur in gridded fields during knockdowns) and always
    returns the shallowest crossing — the physically meaningful pycnocline.

    Returns NaN only when the target density is absent from the entire column
    (too light or too dense for any observed level at that time step).

    .. note::
        Operates on σ₀ (potential density referenced to 0 dbar).  If the
        mooring was processed with a different reference pressure (e.g. σ₂ at
        2000 dbar), pass the corresponding sigma variable.  A future option
        ``--sig-ref`` / ``density_reference`` will generalise this; for now
        the variable name in the dataset controls which reference is used.

    Parameters
    ----------
    sigma0_tp:
        Shape ``(time, pressure)``.  NaN where missing.
    pressure:
        1-D array of pressure levels in dbar.  Need not be sorted — the
        function sorts each time step's finite values by pressure before
        scanning for crossings.
    sigma_grid:
        Target sigma0 values (kg m⁻³) at which to find the pressure.

    Returns
    -------
    np.ndarray
        Shape ``(time, len(sigma_grid))``.  NaN where the target isopycnal is
        absent from the observed column at that time step.

    """
    s = np.asarray(sigma0_tp, dtype=float)
    p = np.asarray(pressure, dtype=float)
    sg = np.asarray(sigma_grid, dtype=float)
    n_t = s.shape[0]
    n_sg = len(sg)
    result = np.full((n_t, n_sg), np.nan)

    for t in range(n_t):
        col = s[t, :]
        finite_mask = np.isfinite(col)
        if finite_mask.sum() < 2:
            continue
        s_f = col[finite_mask]
        p_f = p[finite_mask]
        sort_idx = np.argsort(p_f)
        s_f = s_f[sort_idx]
        p_f = p_f[sort_idx]

        # Upward crossings: pairs (j, j+1) where s_f[j] < target and
        # s_f[j+1] >= target.  This handles non-monotonic columns: isolated
        # spikes (level above target then back below) do not produce an upward
        # crossing, while genuine crossings of the pycnocline do.
        # Shape: (n_p-1, n_sg)
        above = s_f[:, None] >= sg[None, :]  # (n_p, n_sg)
        upward = ~above[:-1, :] & above[1:, :]  # first element of each crossing pair

        has_crossing = upward.any(axis=0)  # (n_sg,)
        # argmax on upward gives index j of the first crossing pair (j, j+1).
        # When has_crossing is False argmax returns 0 — use safe fallback indices.
        j0 = np.argmax(upward, axis=0)  # (n_sg,) index of lower bound
        i0 = np.where(has_crossing, j0, 0)
        i1 = np.where(has_crossing, j0 + 1, 1)

        s0 = s_f[i0]
        s1 = s_f[i1]
        p0v = p_f[i0]
        p1v = p_f[i1]
        denom = s1 - s0
        # denom > 0 guaranteed for valid crossings (s0 < target <= s1).
        with np.errstate(divide="ignore", invalid="ignore"):
            frac = np.where(has_crossing & (denom > 0), (sg - s0) / denom, 0.0)
        result[t, :] = np.where(has_crossing, p0v + frac * (p1v - p0v), np.nan)

        # To also track the *last* (deepest) upward crossing of each surface,
        # flip upward along axis=0, argmax, then map back:
        #   j0_last = (len(s_f) - 2) - np.argmax(upward[::-1, :], axis=0)

    return result


def isopycnal_dataset(
    ds: xr.Dataset,
    sigma_var: str = "sigma0",
    sigma_grid: np.ndarray | None = None,
) -> xr.Dataset:
    """Build an isopycnal-tracking dataset from a gridded mooring file.

    Tracks the pressure (and height above seabed) of density surfaces through
    time using the gridded sigma0 field.  Calls
    :func:`isopycnal_pressure_series` column-by-column.

    Parameters
    ----------
    ds:
        Gridded mooring xr.Dataset with ``(pressure, time)`` dimensions and
        a sigma variable.  A ``waterdepth`` global attribute (metres) is used
        for the height-above-seabed conversion; if absent both ``_height``
        variables are all-NaN.
    sigma_var:
        Name of the sigma variable in *ds* (default ``"sigma0"``).
    sigma_grid:
        Target sigma0 values to track.  If ``None``, a 0.1 kg m⁻³ grid is
        computed from the observed data range.

    Returns
    -------
    xr.Dataset
        Dimensions ``(sigma0_level, time)``.  Variables:

        * ``isopycnal_pressure``  — dbar, NaN where absent.
        * ``isopycnal_height``    — m above seabed, NaN where absent.

    """
    if sigma_var not in ds:
        raise ValueError(f"sigma variable '{sigma_var}' not found in dataset")

    da = ds[sigma_var]
    if "time" not in da.dims or "pressure" not in da.dims:
        raise ValueError(
            f"'{sigma_var}' must have both 'time' and 'pressure' dimensions"
        )
    da_tp = da.transpose("time", "pressure")
    pressure = da_tp["pressure"].values
    time = da_tp["time"].values
    sigma0_tp = da_tp.values

    if sigma_grid is None:
        s_finite = sigma0_tp[np.isfinite(sigma0_tp)]
        if len(s_finite) == 0:
            sigma_grid = np.arange(26.5, 28.2, 0.1)
        else:
            smin = np.floor(s_finite.min() * 10) / 10
            smax = np.ceil(s_finite.max() * 10) / 10
            sigma_grid = np.arange(smin, smax + 0.05, 0.1)

    iso_p = isopycnal_pressure_series(sigma0_tp, pressure, sigma_grid)  # (T, N)

    try:
        waterdepth = float(ds.attrs.get("waterdepth", ""))
    except (ValueError, TypeError):
        waterdepth = np.nan
    if np.isfinite(waterdepth) and waterdepth > 0:
        iso_h = waterdepth - iso_p
        iso_h[iso_h < 0] = np.nan
    else:
        iso_h = np.full_like(iso_p, np.nan)

    dim_name = f"{sigma_var}_level"
    return xr.Dataset(
        {
            "isopycnal_pressure": xr.DataArray(
                iso_p.T,
                dims=[dim_name, "time"],
                attrs={"units": "dbar", "long_name": "Isopycnal pressure"},
            ),
            "isopycnal_height": xr.DataArray(
                iso_h.T,
                dims=[dim_name, "time"],
                attrs={
                    "units": "m",
                    "long_name": "Isopycnal height above seabed",
                },
            ),
        },
        coords={
            dim_name: xr.DataArray(
                sigma_grid,
                dims=[dim_name],
                attrs={
                    "units": "kg m-3",
                    "long_name": f"Potential density anomaly ({sigma_var})",
                },
            ),
            "time": time,
        },
    )
