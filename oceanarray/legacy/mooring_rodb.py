import numpy as np
import pandas as pd
import xarray as xr
from scipy.interpolate import interp1d
from scipy.signal import butter, filtfilt


def find_time_vars(ds_list, time_key="TIME"):
    """Return all variable names that have (time_key,) dimensions in any dataset."""
    time_vars = set()
    for ds in ds_list:
        time_vars.update([var for var in ds.data_vars if ds[var].dims == (time_key,)])
    return sorted(time_vars)


def find_common_attributes(ds_list):
    """Return attributes that are common across all datasets with the same value."""
    common = {}
    shared_keys = set.intersection(*(set(ds.attrs) for ds in ds_list))
    for key in shared_keys:
        values = [ds.attrs[key] for ds in ds_list]
        if all(val == values[0] for val in values):
            common[key] = values[0]
    return common


def stack_instruments(ds_list, time_key="TIME"):
    ds_list = sorted(ds_list, key=lambda ds: ds.InstrDepth.values)
    time = ds_list[0][time_key]
    n_levels = len(ds_list)
    n_time = len(time)

    # Ensure all datasets have the same TIME dimension & arrays are exactly equal
    for ds in ds_list:
        if not np.array_equal(ds[time_key], time):
            raise ValueError("Not all datasets share the same TIME dimension.")

    # Find all time variables across datasets
    time_vars = find_time_vars(ds_list, time_key=time_key)

    # Stack the time variables across datasets
    data_vars = {}
    for var in time_vars:
        stacked = np.full((n_levels, n_time), np.nan)
        for i, ds in enumerate(ds_list):
            if var in ds:
                stacked[i, :] = ds[var].values
        data_vars[var] = (("N_LEVELS", time_key), stacked)

    # Create a list of instrument depths
    instr_depths = np.array([ds.InstrDepth.values for ds in ds_list])

    # Verify that all datasets have the same latitude and longitude
    lats = np.array([ds.Latitude.values for ds in ds_list])
    lons = np.array([ds.Longitude.values for ds in ds_list])
    if not np.allclose(lats, lats[0]):
        raise ValueError("Latitude values differ between datasets.")
    if not np.allclose(lons, lons[0]):
        raise ValueError("Longitude values differ between datasets.")

    # Collect serial numbers and common attributes
    serial_numbers = [ds.attrs.get("serial_number", "unknown") for ds in ds_list]
    common_attrs = find_common_attributes(ds_list)

    ds_out = xr.Dataset(
        data_vars=data_vars,
        coords={
            time_key: time,
            "N_LEVELS": np.arange(n_levels),
            "InstrDepth": ("N_LEVELS", instr_depths),
        },
        attrs={
            **common_attrs,
            "Latitude": float(lats[0]),
            "Longitude": float(lons[0]),
            "serial_numbers": serial_numbers,
        },
    )

    return ds_out


def add_serial_and_sensor_info(ds_combined, ds_list):
    """Add serial number/depth mapping and sensor variable info to the combined dataset.

    Parameters
    ----------
    ds_combined : xarray.Dataset
        The combined mooring-level dataset (with DEPTH coordinate).
    ds_list : list of xarray.Dataset
        List of instrument-level datasets used to create ds_combined.

    Returns
    -------
    xarray.Dataset
        The dataset with added attributes and SENSOR_VARS variable.
    dict
        The updated attributes dictionary.

    """
    # Get the serial numbers and depths
    serial_numbers = []
    depths = []
    for ds in ds_list:
        serial_number = ds.attrs.get("serial_number", "unknown")
        if serial_number not in serial_numbers:
            serial_numbers.append(serial_number)
        depth_val = (
            float(ds.InstrDepth.values)
            if np.ndim(ds.InstrDepth.values) == 0
            else float(ds.InstrDepth.values[0])
        )
        depths.append(depth_val)
    serial_depth_dict = dict(zip(serial_numbers, depths))
    attrs = ds_combined.attrs.copy()
    attrs["serial_depth_dict"] = str(serial_depth_dict)

    # Collect all variable names that match "SENSOR_" prefix
    sensor_vars = [var for var in ds_combined.data_vars if var.startswith("SENSOR_")]
    sensor_depths = []
    for var in sensor_vars:
        vals = ds_combined[var].values
        if vals.ndim == 1:
            idx = np.where(~np.isnan(vals))[0]
            depth = (
                float(ds_combined["DEPTH"].values[idx[0]]) if idx.size > 0 else np.nan
            )
        elif vals.ndim > 1:
            idx = np.where(~np.isnan(vals.any(axis=tuple(range(1, vals.ndim)))))
            depth = (
                float(ds_combined["DEPTH"].values[idx[0][0]])
                if idx[0].size > 0
                else np.nan
            )
        else:
            depth = np.nan
        sensor_depths.append(depth)
    sensor_vars = np.array(sensor_vars)
    sensor_depths = np.array(sensor_depths)
    sensor_vars_at_depth = []
    for depth in ds_combined["DEPTH"].values:
        matching_vars = sensor_vars[sensor_depths == depth]
        if matching_vars.size > 0:
            sensor_vars_at_depth.append(", ".join(matching_vars))
        else:
            sensor_vars_at_depth.append("")
    sensor_vars_arr = np.array(sensor_vars_at_depth, dtype="object")
    ds_combined["SENSOR_VARS"] = xr.DataArray(
        sensor_vars_arr,
        dims=("DEPTH",),
        coords={"DEPTH": ds_combined["DEPTH"]},
        attrs={"long_name": "Sensor variable names", "units": "string"},
    )
    return ds_combined, attrs


def combine_mooring_OS(ds_list):
    """Combine a list of OceanSITES instrument-level datasets into a mooring-level dataset.

    Parameters
    ----------
    ds_list : list of xarray.Dataset
        List of instrument-level datasets to concatenate along DEPTH.

    Returns
    -------
    xarray.Dataset
        Combined dataset with cleaned and updated global attributes.

    """
    if not ds_list:
        raise ValueError("Input list is empty. At least one dataset is required.")

    ds_combined = concat_with_scalar_vars(ds_list, dim="DEPTH")
    ds_combined = ds_combined.sortby("DEPTH")  # Ensure DEPTH is sorted

    # Start with a copy of attributes from the first dataset
    attrs = ds_combined.attrs.copy()

    # Remove instrument-specific attribute
    attrs.pop("serial_number", None)

    # Merge all unique source_file entries
    source_files = []
    for ds in ds_list:
        sf = ds.attrs.get("source_file")
        if isinstance(sf, list):
            source_files.extend(sf)
        elif isinstance(sf, str):
            # Split comma-separated strings if needed
            source_files.extend([s.strip() for s in sf.split(",")])
    unique_sorted = sorted(set(source_files))
    attrs["source_file"] = ", ".join(unique_sorted)

    # Simplify id and title
    platform_code = attrs.get("platform_code", "unknown")
    deployment_code = attrs.get("deployment_code", "unknown")
    attrs["title"] = f"Time series from {platform_code}_{deployment_code}"
    attrs["id"] = f"OS_{platform_code}_{deployment_code}_P"

    # Update geospatial vertical bounds from DEPTH coordinate
    if "DEPTH" in ds_combined.coords:
        depths = ds_combined["DEPTH"].values
        attrs["geospatial_vertical_min"] = float(np.nanmin(depths))
        attrs["geospatial_vertical_max"] = float(np.nanmax(depths))

    # Re-order variables to put any scalar variables at the end, and if they have numeric values, then put them in order of low to high values
    scalar_vars = []
    for var in ds_combined.data_vars:
        if ds_combined[var].ndim == 0:
            scalar_vars.append(var)
    scalar_vars.sort(
        key=lambda v: (
            ds_combined[v].values
            if np.issubdtype(ds_combined[v].dtype, np.number)
            else float("inf")
        )
    )
    non_scalar_vars = [var for var in ds_combined.data_vars if var not in scalar_vars]
    ds_combined = ds_combined[non_scalar_vars + scalar_vars]

    # Assign back updated attributes
    ds_combined.attrs = attrs

    return ds_combined


def auto_filt(y, sr, co, typ="low", fo=6):
    """Apply a Butterworth digital filter to a data array.

    Parameters
    ----------
    y : array_like
        Input data array (1D).
    sr : float
        Sampling rate (Hz or 1/time units of your data).
    co : float or tuple of float
        Cutoff frequency/frequencies. A scalar for 'low' or 'high', a 2-tuple for 'bandstop'.
    typ : str, optional
        Filter type: 'low', 'high', or 'bandstop'. Default is 'low'.
    fo : int, optional
        Filter order. Default is 6.

    Returns
    -------
    yf : ndarray
        Filtered data array.

    """
    # Normalize cutoff frequency to the Nyquist rate
    nyquist = 0.5 * sr
    if isinstance(co, (list, tuple, np.ndarray)):
        wh = [c / nyquist for c in co]
    else:
        wh = co / nyquist

    b, a = butter(fo, wh, btype=typ)
    yf = filtfilt(b, a, y)
    return yf


def filter_all_time_vars(ds, cutoff_days=2, fo=6):
    """Apply a lowpass Butterworth filter to all data variables that depend on TIME.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset containing time series variables.
    cutoff_days : float, optional
        Lowpass filter cutoff in days. Default is 2 days.
    fo : int, optional
        Filter order. Default is 6.

    Returns
    -------
    xarray.Dataset
        Dataset with filtered variables.

    """
    time = ds["TIME"]
    dt = (time.isel(TIME=1) - time.isel(TIME=0)) / np.timedelta64(1, "s")  # seconds
    sr = 1.0 / dt  # Hz
    co = 1.0 / (cutoff_days * 24 * 3600)  # Hz

    ds_filt = ds.copy(deep=True)
    padlen = 3 * fo

    for var in ds.data_vars:
        da = ds[var]
        if "TIME" in da.dims and da.sizes["TIME"] == ds["TIME"].size:
            y = da.values

            if y.shape[0] <= padlen:
                continue  # skip too-short series

            # Reshape for filtering along TIME
            y_flat = y.reshape((y.shape[0], -1))  # (TIME, N)
            y_filt = np.empty_like(y_flat)

            for i in range(y_flat.shape[1]):
                y1d = y_flat[:, i]
                if np.isnan(y1d).all():
                    y_filt[:, i] = np.nan
                else:
                    try:
                        y_filt[:, i] = auto_filt(y1d, sr, co, typ="low", fo=fo)
                    except ValueError:
                        y_filt[:, i] = np.nan  # fallback for rare failures

            # Reshape back to original
            ds_filt[var].values = y_filt.reshape(y.shape)

    return ds_filt


def get_12hourly_time_grid(
    time_or_ds,
    freq="12h",
    start_offset=pd.Timedelta(days=1),
    end_offset=pd.Timedelta(0),
    time_var="TIME",
):
    """Given a pandas.DatetimeIndex, array of datetimes, or xarray.Dataset,
    return a regular time grid (default: 12-hourly) from the first full day after the start
    to the last full day before the end.

    Parameters
    ----------
    time_or_ds : array-like of datetime64, pandas.DatetimeIndex, or xarray.Dataset
        Input time array or dataset containing a time variable.
    freq : str, optional
        Frequency string for the output grid (default '12h').
    start_offset : pd.Timedelta, optional
        Offset to add to the start time before normalizing (default 1 day).
    end_offset : pd.Timedelta, optional
        Offset to subtract from the end time after normalizing (default 0).
    time_var : str, optional
        Name of the time variable in the dataset (default 'TIME').

    Returns
    -------
    pandas.DatetimeIndex
        Regular time grid at the specified frequency.

    """
    if hasattr(time_or_ds, "dims") and time_var in time_or_ds:
        # Assume xarray.Dataset or DataArray
        time = pd.to_datetime(time_or_ds[time_var].values)
    else:
        time = pd.to_datetime(time_or_ds)
    start = time[0]
    stop = time[-1]
    start_day = (
        (start + start_offset).normalize()
        if start.time() != pd.Timestamp("00:00").time()
        else start.normalize()
    )
    stop_day = (stop - end_offset).normalize()
    jd_grid = pd.date_range(start=start_day, end=stop_day, freq=freq)
    return jd_grid


def interp_to_12hour_grid(ds1):
    """Interpolate all variables with a TIME dimension to a regular 12-hour grid.

    Handles both 1D and multidimensional variables with TIME as the first dimension.
    """
    jd_grid = get_12hourly_time_grid(ds1)
    time1 = ds1["TIME"].values
    time1_float = time1.astype("datetime64[s]").astype(float)
    grid_float = jd_grid.values.astype("datetime64[s]").astype(float)

    interp_vars = {}

    for var in ds1.data_vars:
        da = ds1[var]
        if "TIME" not in da.dims:
            continue  # skip non-time-dependent variables

        axis_time = da.get_axis_num("TIME")
        other_dims = [d for d in da.dims if d != "TIME"]

        # Reshape to (TIME, -1) to interpolate each column
        reshaped = da.transpose("TIME", *other_dims).data.reshape(len(time1), -1)

        interpolated = np.empty((len(grid_float), reshaped.shape[1]))

        for i in range(reshaped.shape[1]):
            interp_func = interp1d(
                time1_float,
                reshaped[:, i],
                bounds_error=False,
                fill_value="extrapolate",
                assume_sorted=True,
            )
            interpolated[:, i] = interp_func(grid_float)

        # Reshape back to (TIME, *other_dims)
        new_shape = (len(grid_float),) + tuple(
            da.transpose("TIME", *other_dims).shape[1:]
        )
        interp_data = interpolated.reshape(new_shape)

        # Create new DataArray
        coords = {"TIME": jd_grid}
        for dim in other_dims:
            coords[dim] = ds1[dim]

        interp_vars[var] = xr.DataArray(
            interp_data, dims=("TIME", *other_dims), coords=coords, attrs=da.attrs
        )

    # Build new dataset
    ds_interp = xr.Dataset(interp_vars, coords={"TIME": jd_grid})
    ds_interp.attrs = ds1.attrs.copy()

    # Copy non-time-dependent variables
    for var in ds1.data_vars:
        if "TIME" not in ds1[var].dims:
            ds_interp[var] = ds1[var]

    # Also copy over coordinates that are not 'TIME'
    for coord in ds1.coords:
        if coord != "TIME" and coord not in ds_interp.coords:
            ds_interp = ds_interp.assign_coords({coord: ds1[coord]})

    return ds_interp


def concat_with_scalar_vars(
    datasets: list[xr.Dataset],
    dim: str,
    scalar_vars: list[str] | None = None,
) -> xr.Dataset:
    """Concatenate datasets along a dimension, preserving scalar variables.

    Scalar (0-D) variables would normally be broadcast to the concatenation
    dimension by ``xr.concat``; this helper strips them beforehand and
    re-attaches the first occurrence after the concat so they remain 0-D.

    Parameters
    ----------
    datasets : list of xarray.Dataset
        Datasets to concatenate.
    dim : str
        Dimension along which to concatenate.
    scalar_vars : list of str, optional
        List of variable names to treat as scalars. If None, auto-detect
        scalar variables (those with ndim == 0 in any dataset).

    Returns
    -------
    xarray.Dataset
        Concatenated dataset with scalar variables re-attached as 0-D DataArrays.

    """
    scalar_storage = {}

    # Auto-detect scalar variables if not provided
    if scalar_vars is None:
        scalar_vars = set()
        for ds in datasets:
            scalar_vars.update([v for v in ds.data_vars if ds[v].ndim == 0])
        scalar_vars = list(scalar_vars)

    # Strip scalar variables and store them
    cleaned = []
    for _i, ds in enumerate(datasets):
        ds_copy = ds.copy()
        for var in scalar_vars:
            if var in ds_copy and ds_copy[var].ndim == 0:
                scalar_storage[(_i, var)] = ds_copy[var]
                del ds_copy[var]
        cleaned.append(ds_copy)

    # Concatenate non-scalar parts
    combined = xr.concat(cleaned, dim=dim)

    # Re-attach scalar variables as 0-D DataArrays
    for (_i, var), da in scalar_storage.items():
        combined[var] = da  # safe: 0-D, no coords

    return combined
