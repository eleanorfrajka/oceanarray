"""RODB-era QC flaggers for datasets with OceanSITES-style uppercase variables (TEMP, CNDC, PSAL)."""

from __future__ import annotations

import logging
import numpy as np
import xarray as xr
_log = logging.getLogger(__name__)


def flag_salinity_outliers(ds: xr.Dataset, n_std: float = 4) -> xr.DataArray:
    """Flag PSAL values more than n_std standard deviations from the mean.

    Computed separately for each depth level.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset containing "PSAL" variable with dimensions including "DEPTH".
    n_std : float, optional
        Number of standard deviations from the mean to define an outlier (default is 4).

    Returns
    -------
    xarray.DataArray (bool)
        Boolean array with True where salinity is flagged as an outlier.

    """
    if "CNDC_QC" in ds:
        mask = ds["CNDC_QC"] == 0
        mean_sal = ds["PSAL"].where(mask).mean(dim="TIME", skipna=True)
        std_sal = ds["PSAL"].where(mask).std(dim="TIME", skipna=True)
    else:
        mean_sal = ds["PSAL"].mean(dim="TIME", skipna=True)
        std_sal = ds["PSAL"].std(dim="TIME", skipna=True)

    # Broadcast mean and std over time
    lower = mean_sal - n_std * std_sal
    upper = mean_sal + n_std * std_sal

    return (ds["PSAL"] < lower) | (ds["PSAL"] > upper)


def flag_temporal_spikes(
    ds: xr.Dataset, var: str = "CNDC", threshold: float = 5
) -> xr.DataArray:
    """Flag large absolute differences in time for each depth.

    threshold: maximum allowed difference in units of the variable.
    """
    diff = np.abs(ds[var].diff("TIME", label="upper"))
    flag = diff > threshold
    # Pad to match original dimensions
    flag = flag.reindex(TIME=ds["TIME"], method="ffill")
    flag = flag.fillna(False)
    return flag.astype(bool)


def flag_vertical_inconsistencies(
    ds: xr.Dataset, var: str = "CNDC", threshold: float = 2
) -> xr.DataArray:
    """Flag points that are very different from vertical neighbors.

    threshold: max allowed difference between vertically adjacent sensors.
    """
    # Central difference approximation in depth
    vert_diff = np.abs(ds[var].diff("DEPTH"))
    # Pad to match original dimensions
    vert_diff = vert_diff.reindex(DEPTH=ds["DEPTH"], method="ffill")
    return vert_diff > threshold


def run_qc(ds: xr.Dataset) -> xr.Dataset:
    """Apply a sequence of QC tests and write combined CNDC_QC flag variable."""
    from oceanarray.analysis.hydrography import calc_psal as _calc_psal

    if "PSAL" not in ds:
        ds = _calc_psal(ds)

    sal_flag = flag_salinity_outliers(ds, 6).astype(bool)
    time_flag = flag_temporal_spikes(ds).astype(bool)
    #    vert_flag = flag_vertical_inconsistencies(ds).astype(bool)

    # Combine all
    combined_flag = sal_flag | time_flag
    ds["CNDC_QC"] = (ds["CNDC"].dims, combined_flag.data.astype(int))

    sal_flag = flag_salinity_outliers(ds, 3).astype(bool)
    combined_flag = sal_flag | time_flag
    ds["CNDC_QC"] = (ds["CNDC"].dims, combined_flag.data.astype(int))

    return ds
