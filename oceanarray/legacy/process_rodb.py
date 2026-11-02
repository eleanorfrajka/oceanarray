import re
from datetime import datetime
from pathlib import Path

import numpy as np
import xarray as xr

from oceanarray import _deprecated
from oceanarray.logger import log_debug, log_info, log_warning

from . import rodb

DUMMY_VALUE = -9.99e-29  # adjust if needed


def middle_percent(values, percent=95):
    """Return the lower and upper bounds for the central `percent` of the data.

    Parameters
    ----------
    values : array-like
        Input data (1D array). NaNs will be ignored.
    percent : float
        Desired central percentage (e.g., 95 for middle 95%).

    Returns
    -------
    tuple
        (lower_bound, upper_bound)

    """
    values = np.asarray(values)
    values = values[~np.isnan(values)]

    if not 0 < percent < 100:
        raise ValueError("percent must be between 0 and 100 (exclusive)")

    tail = (100 - percent) / 2
    lower = np.nanpercentile(values, tail)
    upper = np.nanpercentile(values, 100 - tail)
    return lower, upper


def mean_of_middle_percent(values, percent=95):
    """Compute the mean of values within the central `percent` of the data.

    Parameters
    ----------
    values : array-like
        Input data (1D array). NaNs will be ignored.
    percent : float
        Desired central percentage (e.g., 95 for middle 95%).

    Returns
    -------
    float
        Mean of values within the specified middle percentage.

    """
    values = np.asarray(values)
    values = values[~np.isnan(values)]
    lower, upper = middle_percent(values, percent)
    filtered = values[(values >= lower) & (values <= upper)]
    return np.mean(filtered)


def std_of_middle_percent(values, percent=95):
    """Compute the standard deviation of values within the central `percent` of the data.

    Parameters
    ----------
    values : array-like
        Input data (1D array). NaNs will be ignored.
    percent : float
        Desired central percentage (e.g., 95 for middle 95%).

    Returns
    -------
    float
        Standard deviation of values within the specified middle percentage.

    """
    values = np.asarray(values)
    values = values[~np.isnan(values)]
    lower, upper = middle_percent(values, percent)
    filtered = values[(values >= lower) & (values <= upper)]
    return np.std(filtered)


def to_zscore(values, percent=95):
    """Return the z-score of a data series, using the mean and standard deviation
    of its central `percent` range.

    Parameters
    ----------
    values : array-like
        Input data (1D array). NaNs are ignored.
    percent : float
        Central percentage to define the 'middle' of the distribution (e.g., 95).

    Returns
    -------
    array
        z-scores, with the same shape as the input.

    """
    values = np.asarray(values)
    mask = ~np.isnan(values)
    valid_values = values[mask]

    if valid_values.size == 0:
        return values  # return original if all NaNs

    lower, upper = middle_percent(valid_values, percent)
    middle_vals = valid_values[(valid_values >= lower) & (valid_values <= upper)]

    mean_mid = np.mean(middle_vals)
    std_mid = np.std(middle_vals)

    if std_mid == 0:
        raise ValueError(
            "Standard deviation of middle percent is zero — normalization not possible."
        )

    normalized = (values - mean_mid) / std_mid
    return normalized


def to_zscore_dataset(ds, percent=95):
    """Return the z-score of every 1D data variable in an xarray Dataset that matches the
    length of TIME, using the mean and std over the central `percent` of each variable.

    Parameters
    ----------
    ds : xarray.Dataset
        Input dataset with a 'TIME' coordinate.
    percent : float
        Percentage of central values to define the middle (e.g., 95 for middle 95%).

    Returns
    -------
    xarray.Dataset
        New dataset holding the z-scores.

    """
    ds_norm = xr.Dataset(attrs=ds.attrs)
    time_shape = ds["TIME"].shape

    for var in ds.data_vars:
        if ds[var].shape == time_shape:
            norm_values = to_zscore(ds[var].values, percent)
            ds_norm[var] = xr.DataArray(
                norm_values,
                coords=ds[var].coords,
                dims=ds[var].dims,
                attrs=ds[var].attrs,
            )

    # Retain TIME coordinate
    ds_norm = ds_norm.assign_coords({"TIME": ds["TIME"]})
    return ds_norm



normalize_by_middle_percent = _deprecated.alias(
    "oceanarray.legacy.process_rodb.normalize_by_middle_percent", to_zscore
)
normalize_dataset_by_middle_percent = _deprecated.alias(
    "oceanarray.legacy.process_rodb.normalize_dataset_by_middle_percent",
    to_zscore_dataset,
)

def trim_suggestion(ds, percent=95, threshold=6, vars_to_check=["T", "C", "P"]):
    """Normalize dataset variables using the middle percentile and determine suggested
    deployment start and end times where the normalized values are below a given threshold.

    Parameters
    ----------
    ds : xarray.Dataset
        Input dataset with a 'TIME' coordinate and 1D data variables.
    percent : float
        Percentage for middle-percent normalization (e.g., 95).
    threshold : float
        Absolute threshold on normalized values to consider as stable.
    vars_to_check : list of str
        List of variable names to consider for start/end detection.

    Returns
    -------
    start_time : np.datetime64 or None
        Suggested deployment start time.
    end_time : np.datetime64 or None
        Suggested deployment end time.

    """
    ds_norm = to_zscore_dataset(ds, percent=percent)

    start_candidates = []
    end_candidates = []

    for var in vars_to_check:
        if var not in ds_norm:
            continue

        arr = np.abs(ds_norm[var].values)
        time_vals = ds_norm["TIME"].values

        # First time index where normalized value drops below threshold
        below_thresh = np.where(arr < threshold)[0]
        if len(below_thresh) > 0:
            first_below = time_vals[below_thresh[0]]
            last_below = time_vals[below_thresh[-1]]
            start_candidates.append(first_below)
            end_candidates.append(last_below)
        else:
            print(f"Warning: {var} never drops below threshold {threshold}")

    if not start_candidates or not end_candidates:
        print("Could not determine suggested deployment period.")
        return None, None

    start_time = max(start_candidates)
    end_time = min(end_candidates)

    print(f"Suggested deployment start: {start_time}")
    print(f"Suggested deployment end: {end_time}")
    return start_time, end_time


def stage2_trim(
    ds: xr.Dataset,
    deployment_start: datetime = None,
    deployment_end: datetime = None,
) -> xr.Dataset:
    """Trim dataset to deployment period and fill time gaps with NaN.

    Parameters
    ----------
    ds : xarray.Dataset
        Input dataset with 'TIME' coordinate and variables like 'TEMP', 'CNDC', optionally 'PRES'.
    deployment_start : datetime, optional
        Start of the valid deployment period. If None, uses first timestamp in ds.
    deployment_end : datetime, optional
        End of the valid deployment period. If None, uses last timestamp in ds.

    Returns
    -------
    xr.Dataset
        Trimmed and gap-filled dataset.

    """
    if "TIME" not in ds:
        raise ValueError("Dataset must contain 'TIME' coordinate")

    if deployment_start is None:
        deployment_start = ds.TIME.values[0]
    if deployment_end is None:
        deployment_end = ds.TIME.values[-1]

    log_info(
        "Trimming dataset to deployment period %s – %s",
        deployment_start,
        deployment_end,
    )

    # Trim to deployment period
    ds_trimmed = ds.sel(TIME=slice(deployment_start, deployment_end))

    # Infer sampling interval
    time_diff = np.diff(ds_trimmed.TIME.values).astype("timedelta64[s]").astype(int)
    if len(time_diff) == 0:
        log_warning("Only one sample in dataset — no trimming or gap filling possible.")
        return ds_trimmed

    dt_seconds = int(np.median(time_diff))
    sampling_interval = np.timedelta64(dt_seconds, "s")
    log_debug("Inferred sampling interval: %d seconds", dt_seconds)

    # Create regular time axis and reindex
    time_start = ds_trimmed.TIME.values[0]
    time_end = ds_trimmed.TIME.values[-1]
    full_time = np.arange(time_start, time_end + sampling_interval, sampling_interval)

    log_info("Interpolating to regular time base with %d steps", len(full_time))
    ds_uniform = ds_trimmed.reindex(TIME=full_time, method=None)

    # Log stats
    for var in ["TEMP", "CNDC", "PRES"]:
        if var in ds_uniform:
            data = ds_uniform[var]
            valid = np.isfinite(data.values)
            log_info(
                "%s: %d samples, mean = %.3f, std = %.3f, min = %.3f, max = %.3f",
                var,
                valid.sum(),
                np.nanmean(data),
                np.nanstd(data),
                np.nanmin(data),
                np.nanmax(data),
            )

    return ds_uniform, deployment_start, deployment_end


def apply_microcat_calibration_from_txt(txt_file: str, use_file: str) -> xr.Dataset:
    """Apply calibration offsets from a *.microcat.txt file to the original .use data file.

    Parameters
    ----------
    txt_file : str
        Path to the calibration log file (e.g., 'wb1_12_2015_005.microcat').
    use_file : str
        Path to the original '.use' file.

    Returns
    -------
    ds : xr.Dataset
        Dataset with calibrated temperature, conductivity, and pressure.

    """
    txt_file = Path(txt_file)
    use_file = Path(use_file)

    with open(txt_file, "r") as f:
        text = f.read()

    def get_offsets(variable):
        pattern = rf"{variable}.*?:\s+([-0-9.]+)\s+([-0-9.]+)"
        match = re.search(pattern, text)
        if match:
            return float(match.group(1)), float(match.group(2))
        return None, None

    def is_applied(var):
        match = re.search(rf"Average {var} applied\?\s+([yn])", text, re.IGNORECASE)
        return match and match.group(1).lower() == "y"

    # Extract offsets and flags
    c_pre, c_post = get_offsets("Conductivity")
    t_pre, t_post = get_offsets("Temperature")
    p_pre, p_post = get_offsets("Pressure")

    apply_c = is_applied("conductivity")
    apply_t = is_applied("temperature")
    apply_p = is_applied("pressure")

    # Load data using rodbload
    ds = rodb.rodbload(use_file)

    # Rename to standard names
    # rename_map = {"T": "TEMP", "C": "CNDC", "P": "PRES"}
    # ds = ds.rename({k: v for k, v in rename_map.items() if k in ds})

    def apply_avg_offset(var, pre, post, flag):
        if var not in ds or not flag or pre is None or post is None:
            return ds.get(var, None)
        offset = (pre + post) / 2
        log_info(f"Applying average offset {offset:+.4f} to {var}")
        return ds[var] + offset

    # Apply calibration offsets
    for var, pre, post, flag in [
        ("T", t_pre, t_post, apply_t),
        ("C", c_pre, c_post, apply_c),
        ("P", p_pre, p_post, apply_p),
    ]:
        if var in ds:
            ds[var] = apply_avg_offset(var, pre, post, flag)

    # Replace dummy values
    for var in ["T", "C", "P"]:
        if var in ds:
            ds[var] = ds[var].where(ds[var] != DUMMY_VALUE, np.nan)

    ds.attrs["source_file"] = str(use_file)
    ds.attrs["calibration_log"] = str(txt_file)
    ds.attrs["comment"] = "Calibration offsets applied from microcat log file."

    return ds
