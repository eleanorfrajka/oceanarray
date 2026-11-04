"""Clock offset analysis functions for oceanographic instrument data.

This module provides functions to analyze and detect clock offsets between
different instruments on the same mooring by comparing deployment timing
and performing lag correlation analysis.

Naming conventions updated from the original module:
- YAML key ``instruments`` → ``clamp``
- file suffix ``_raw`` → ``_stage2``
"""

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from scipy.signal import find_peaks

from oceanarray import mooring as _mooring
from oceanarray import paths
from oceanarray.instruments import instrument_entries
from oceanarray.pipeline.clock import deployment as find_deployment

_STAGE2_TAG = paths.stage_suffix(2).removesuffix(".nc")


def load_mooring_instruments(
    mooring_name,
    base_dir,  # noqa: ARG001
    output_path,
    file_suffix=_STAGE2_TAG,
):
    """Load all instruments for a mooring from netCDF files and enrich with YAML metadata.

    Parameters
    ----------
    mooring_name : str
        Name of the mooring (e.g., 'dsE_1_2018')
    base_dir : str
        Base directory path (currently unused — reserved for future use)
    output_path : str
        Path to processed data directory
    file_suffix : str, optional
        File suffix to use (e.g. '_stage2', '_stage3'), default '_stage2'

    Returns
    -------
    list of xarray.Dataset
        List of datasets for each instrument
    dict
        YAML metadata for the mooring

    """
    proc_dir = output_path + mooring_name
    moor_yaml = proc_dir + "/" + mooring_name + ".mooring.yaml"

    moor_yaml_data = _mooring.load(moor_yaml)

    instrument_list = instrument_entries(moor_yaml_data)
    if not instrument_list:
        raise KeyError(
            f"Mooring YAML {moor_yaml} has neither 'clamp' nor 'instruments' key."
        )

    datasets = []
    for i in instrument_list:
        fname = mooring_name + "_" + str(i["serial"]) + file_suffix + ".nc"
        rawfile = proc_dir + "/" + i["instrument"] + "/" + fname

        if Path(rawfile).exists():
            print(rawfile)
            ds1 = xr.open_dataset(rawfile)

            # Add metadata from YAML
            if "InstrDepth" not in ds1.variables and "depth" in i:
                ds1["InstrDepth"] = i["depth"]
            if "instrument" not in ds1.variables and "instrument" in i:
                ds1["instrument"] = i["instrument"]
            if "serial_number" not in ds1.variables and "serial" in i:
                ds1["serial_number"] = i["serial"]
            if "timeS" in ds1.variables:
                ds1 = ds1.drop_vars("timeS")

            datasets.append(ds1)

    return datasets, moor_yaml_data


def create_common_time_grid(datasets):
    """Create a common time grid for interpolation based on all datasets.

    Parameters
    ----------
    datasets : list of xarray.Dataset
        List of instrument datasets

    Returns
    -------
    numpy.ndarray
        Common time grid as datetime64 array

    """
    intervals_min = []
    start_times = []
    end_times = []

    for ds in datasets:
        time = ds["time"]
        time_interval = np.nanmedian(np.diff(time.values) / np.timedelta64(1, "m"))
        intervals_min.append(time_interval)
        start_times.append(time.values[0])
        end_times.append(time.values[-1])

    earliest_start = pd.to_datetime(start_times).min().to_datetime64()

    end_arr = np.array(end_times, dtype="datetime64[ns]")
    mask = ~np.isnat(end_arr)
    if mask.any():
        med_ns = np.median(end_arr[mask].astype("int64"))
        latest_end = np.datetime64(int(med_ns), "ns")
    else:
        latest_end = np.datetime64("NaT", "ns")

    dt_sec = int(np.nanmedian(intervals_min) * 60)
    return np.arange(earliest_start, latest_end, np.timedelta64(dt_sec, "s"))


def interpolate_datasets_to_grid(datasets, time_grid):
    """Interpolate all datasets to a common time grid.

    Parameters
    ----------
    datasets : list of xarray.Dataset
        List of instrument datasets
    time_grid : numpy.ndarray
        Common time grid for interpolation

    Returns
    -------
    list of xarray.Dataset
        List of interpolated datasets

    """
    datasets_interp = []

    for idx, ds in enumerate(datasets):
        if "time" not in ds.sizes:
            print(
                f"  WARNING: Dataset {idx} has no time dimension, skipping interpolation"
            )
            continue

        if ds.sizes["time"] <= 1:
            print(
                f"  WARNING: Dataset {idx} has only {ds.sizes['time']} time point(s), skipping interpolation"
            )
            continue

        try:
            interp_vars = {}
            for var in ds.data_vars:
                if "time" in ds[var].dims:
                    interp_vars[var] = ds[var].interp(time=time_grid)
                else:
                    interp_vars[var] = ds[var]

            if interp_vars:
                ds_interp = xr.Dataset(interp_vars, coords={"time": time_grid})

                if "InstrDepth" in ds:
                    ds_interp = ds_interp.assign_coords(depth=ds["InstrDepth"])
                if "clock_offset" in ds:
                    ds_interp = ds_interp.assign_coords(
                        seconds_offset=ds["clock_offset"]
                    )

                datasets_interp.append(ds_interp)
            else:
                print(f"  No time-dependent variables found in dataset {idx}")

        except Exception as e:  # noqa: BLE001  — log and continue; one bad dataset must not abort mooring
            print(f"  ERROR interpolating dataset {idx}: {e}")
            continue

    return datasets_interp


def combine_interpolated_datasets(datasets_interp):
    """Combine interpolated datasets into a single multi-level dataset.

    Parameters
    ----------
    datasets_interp : list of xarray.Dataset
        List of interpolated datasets

    Returns
    -------
    xarray.Dataset
        Combined dataset with N_LEVELS dimension

    """
    vars_to_keep = [
        "temperature",
        "salinity",
        "conductivity",
        "pressure",
        "u_velocity",
        "v_velocity",
    ]

    datasets_clean = []
    for ds in datasets_interp:
        ds_sel = ds.drop_vars(
            ["density", "potential_temperature", "julian_days_offset", "timeS"],
            errors="ignore",
        )
        datasets_clean.append(ds_sel)

    time_coord = datasets_interp[0]["time"]
    combined_data = {}
    N_LEVELS = len(datasets_clean)

    for var in vars_to_keep:
        arrs = []
        for ds in datasets_clean:
            if var in ds:
                arrs.append(ds[var].values)
            else:
                arrs.append(np.full(time_coord.shape, np.nan))
        combined_data[var] = (("time", "N_LEVELS"), np.stack(arrs, axis=-1))

    # Gather metadata for each level
    depths = []
    clock_offsets = []
    serial = []
    instrtype = []

    for ds in datasets_clean:
        depths.append(float(ds["InstrDepth"].item()) if "InstrDepth" in ds else np.nan)
        serial.append(ds["serial_number"].item() if "serial_number" in ds else np.nan)
        instrtype.append(ds["instrument"].item() if "instrument" in ds else "unknown")

        if "clock_offset" in ds:
            co = ds["clock_offset"].item()
        elif "seconds_offset" in ds:
            co = ds["seconds_offset"].item()
        else:
            co = 0
        clock_offsets.append(int(np.rint(co)) if np.isfinite(co) else 0)

    return xr.Dataset(
        data_vars=combined_data,
        coords={
            "time": time_coord,
            "N_LEVELS": np.arange(N_LEVELS),
            "clock_offset": ("N_LEVELS", np.array(clock_offsets)),
            "serial_number": ("N_LEVELS", np.array(serial)),
            "nominal_depth": ("N_LEVELS", np.array(depths)),
            "instrument": ("N_LEVELS", np.asarray(instrtype)),
        },
    )


def deployment_timing(combined_ds):
    """Detect the deployment period using temperature-based detection of deployment periods.

    Parameters
    ----------
    combined_ds : xarray.Dataset
        Combined dataset with all instruments

    Returns
    -------
    xarray.Dataset
        Dataset enriched with deployment timing information

    """
    return find_deployment.find_deployment(
        combined_ds, bottom_strategy="deployment_bounds"
    )


def calculate_timing_offsets(combined_ds, bin_width_sec=60):
    """Calculate timing offsets between instruments based on deployment start/end times.

    Parameters
    ----------
    combined_ds : xarray.Dataset
        Dataset with deployment timing information
    bin_width_sec : float, optional
        Bin width in seconds for consensus grouping, default 60

    Returns
    -------
    dict
        Dictionary containing offset analysis results

    """
    start_times = pd.to_datetime(combined_ds["start_time"].values)
    end_times = pd.to_datetime(combined_ds["end_time"].values)

    f_start = np.isfinite(start_times)
    f_end = np.isfinite(end_times)

    # Initial reference times for clustering
    ref_start0 = start_times[f_start].min()
    ref_end0 = end_times[f_end].max()

    start_off0 = np.full(start_times.shape, np.nan, float)
    end_off0 = np.full(end_times.shape, np.nan, float)
    start_off0[f_start] = (start_times[f_start] - ref_start0) / np.timedelta64(1, "s")
    end_off0[f_end] = (end_times[f_end] - ref_end0) / np.timedelta64(1, "s")

    # Find consensus group
    vals = start_off0[np.isfinite(start_off0)]
    if vals.size == 0:
        raise RuntimeError("No finite start offsets to form consensus.")

    vmin, vmax = vals.min(), vals.max()
    bins = np.arange(vmin - bin_width_sec, vmax + 2 * bin_width_sec, bin_width_sec)
    hist, edges = np.histogram(vals, bins=bins)
    k = np.argmax(hist)
    lo, hi = edges[k], edges[k + 1]
    in_consensus = (start_off0 >= lo) & (start_off0 < hi)

    idx_consensus = np.where(in_consensus & f_start & f_end)[0]
    if idx_consensus.size == 0:
        idx_consensus = np.where(in_consensus & f_start)[0]

    # Redefine references from consensus group
    ref_start = start_times[idx_consensus].min()
    ref_end = end_times[idx_consensus].max()

    # Recompute offsets
    start_off = (start_times - ref_start) / np.timedelta64(1, "s")
    end_off = (end_times - ref_end) / np.timedelta64(1, "s")
    avg_off = (start_off + end_off) / 2.0
    diff_off = start_off - end_off

    # Calculate drift rates
    dur = (end_times - start_times) / np.timedelta64(1, "s")
    drift_rate_per_day = np.full_like(avg_off, np.nan, dtype=float)
    ok = np.isfinite(start_off) & np.isfinite(end_off) & np.isfinite(dur) & (dur > 0)
    drift_rate_per_day[ok] = (end_off[ok] - start_off[ok]) / dur[ok] * 86400.0

    return {
        "start_offsets": start_off,
        "end_offsets": end_off,
        "avg_offsets": avg_off,
        "diff_offsets": diff_off,
        "drift_rates": drift_rate_per_day,
        "consensus_indices": idx_consensus,
        "ref_start": ref_start,
        "ref_end": ref_end,
    }


def perform_lag_correlation_analysis(combined_ds, ref_index=0, sub_sample=5):
    """Perform lag correlation analysis between instruments to detect clock offsets.

    Parameters
    ----------
    combined_ds : xarray.Dataset
        Combined dataset with all instruments
    ref_index : int, optional
        Index of reference instrument, default 0
    sub_sample : int, optional
        Subsampling factor to speed up correlation, default 5

    Returns
    -------
    dict
        Dictionary containing correlation analysis results

    """
    time_interval = np.nanmedian(
        np.diff(combined_ds["time"].values) / np.timedelta64(1, "s")
    )

    ref_temp_sub = combined_ds["temperature"][:, ref_index].values[::sub_sample]
    n_sub = len(ref_temp_sub)

    max_lag_sub = n_sub // 5
    lags_sub = np.arange(-max_lag_sub, max_lag_sub + 1)

    results = {
        "ref_index": ref_index,
        "sub_sample": sub_sample,
        "time_interval": time_interval,
        "lags": [],
        "correlations": [],
        "max_correlations": [],
        "clock_offsets": [],
    }

    N_LEVELS = combined_ds.sizes["N_LEVELS"]

    for i in range(N_LEVELS):
        temp_i_sub = combined_ds["temperature"][:, i].values[::sub_sample]
        coff = combined_ds["clock_offset"][i].values

        corrs_sub = lag_correlation(ref_temp_sub, temp_i_sub, max_lag_sub)

        max_corr_idx = np.nanargmax(corrs_sub)
        max_corr = corrs_sub[max_corr_idx]
        max_lag = lags_sub[max_corr_idx]
        dt_sub = sub_sample * time_interval
        clock_offset_total = max_lag * dt_sub + coff

        results["lags"].append(max_lag)
        results["correlations"].append(corrs_sub)
        results["max_correlations"].append(max_corr)
        results["clock_offsets"].append(clock_offset_total)

    return results


def print_timing_offset_summary(combined_ds, offset_results):
    """Print a summary table of timing offsets for all instruments.

    Parameters
    ----------
    combined_ds : xarray.Dataset
        Combined dataset with instrument metadata
    offset_results : dict
        Results from calculate_timing_offsets()

    """
    N = combined_ds.sizes["N_LEVELS"]
    labels = combined_ds["instrument"].values
    serial = combined_ds["serial_number"].values

    start_times = pd.to_datetime(combined_ds["start_time"].values)
    end_times = pd.to_datetime(combined_ds["end_time"].values)

    print(f"Consensus group size: {offset_results['consensus_indices'].size}")
    print(
        f"Consensus-derived refs -> ref_start={offset_results['ref_start']}, ref_end={offset_results['ref_end']}\n"
    )

    for i in range(N):
        tag = "REF" if i in offset_results["consensus_indices"] else "-"
        s = offset_results["start_offsets"][i]
        e = offset_results["end_offsets"][i]
        a = offset_results["avg_offsets"][i]
        d = offset_results["diff_offsets"][i]
        dr = offset_results["drift_rates"][i]

        print(
            f"{i:02d}: {str(labels[i]):8s}/{str(serial[i]):6s} | "
            f"start={pd.to_datetime(start_times[i])} ({s:+8.0f}s) | "
            f"end={pd.to_datetime(end_times[i])} ({e:+8.0f}s) | "
            f"avg={a:+8.0f}s | diff={d:+6.0f}s | drift={'nan' if not np.isfinite(dr) else f'{dr:+.2f} s/day'} | {tag}"
        )


def suggest_reference_instrument(combined_ds, offset_results):
    """Suggest the best reference instrument for lag correlation analysis.

    This function recommends the instrument with the smallest average timing offset
    from the temperature threshold method, making it the most likely to have
    accurate timing for use as a correlation reference.

    Parameters
    ----------
    combined_ds : xarray.Dataset
        Combined dataset with instrument metadata
    offset_results : dict
        Results from calculate_timing_offsets()

    Returns
    -------
    dict
        Dictionary with recommendation details including suggested index

    """
    avg_offsets = offset_results["avg_offsets"]
    abs_offsets = np.abs(avg_offsets)

    # Find instruments with finite offsets
    finite_mask = np.isfinite(abs_offsets)
    if not finite_mask.any():
        print("Warning: No instruments have finite timing offsets")
        return {"suggested_index": 0, "reason": "fallback - no finite offsets"}

    # Find the instrument with smallest absolute average offset
    finite_indices = np.where(finite_mask)[0]
    min_abs_idx = finite_indices[np.argmin(abs_offsets[finite_mask])]

    instruments = combined_ds["instrument"].values
    serial = combined_ds["serial_number"].values
    depths = combined_ds["nominal_depth"].values

    suggestion = {
        "suggested_index": min_abs_idx,
        "offset_seconds": avg_offsets[min_abs_idx],
        "abs_offset_seconds": abs_offsets[min_abs_idx],
        "instrument": instruments[min_abs_idx],
        "serial": serial[min_abs_idx],
        "depth": depths[min_abs_idx],
        "reason": "smallest absolute average timing offset",
    }

    print("Suggested reference instrument for lag correlation:")
    print(
        f"  Index {min_abs_idx}: {instruments[min_abs_idx]} #{serial[min_abs_idx]} at {depths[min_abs_idx]:.0f}m"
    )
    print(f"  Average timing offset: {avg_offsets[min_abs_idx]:+.1f}s")
    print(f"  Reason: {suggestion['reason']}")
    print()

    return suggestion


def print_correlation_summary(combined_ds, correlation_results):
    """Print a summary of lag correlation analysis results.

    Parameters
    ----------
    combined_ds : xarray.Dataset
        Combined dataset with instrument metadata
    correlation_results : dict
        Results from perform_lag_correlation_analysis()

    """
    serial = combined_ds["serial_number"].values

    print("Lag Correlation Analysis Results:")
    print("(Enter the summed value, no sign change, in the yaml as clock_offset)")
    print()

    for i, (lag, corr, offset) in enumerate(
        zip(
            correlation_results["lags"],
            correlation_results["max_correlations"],
            correlation_results["clock_offsets"],
            strict=False,
        )
    ):
        print(
            f"Level {i + 1} (#{serial[i]}): max corr = {corr:.3f} @lag {lag} --> clock_offset: {offset:.0f}s"
        )


def plot_deployment_boundaries(
    original_datasets, combined_ds, n_samples=10, figsize=(12, 4)
):
    """Plot detailed view of deployment boundaries showing individual measurements.

    This function creates plots showing the exact transition points where instruments
    move from surface to deployment conditions, with individual data points highlighted
    around the predicted start and end times.

    Parameters
    ----------
    original_datasets : list of xarray.Dataset
        Original (non-interpolated) datasets for each instrument
    combined_ds : xarray.Dataset
        Combined dataset with deployment timing information
    n_samples : int, optional
        Number of samples to show before/after predicted boundaries, default 10
    figsize : tuple, optional
        Figure size for each plot, default (12, 4)

    """
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    start_times = combined_ds["start_time"].values
    end_times = combined_ds["end_time"].values
    split_vals = combined_ds["split_value"].values
    instruments = combined_ds["instrument"].values
    depths = combined_ds["nominal_depth"].values

    for i, ds in enumerate(original_datasets):
        if i >= len(start_times):
            break

        if "temperature" not in ds.data_vars:
            print(f"Skipping instrument {i}: no temperature data")
            continue

        time_orig = ds["time"].values
        temp_orig = ds["temperature"].values

        # Convert deployment times to numpy datetime64 for comparison
        start_time = start_times[i]
        end_time = end_times[i]
        split_val = split_vals[i]

        # Find indices closest to start and end times
        start_idx = None
        end_idx = None

        if np.isfinite(start_time.astype("datetime64[ns]").astype("int64")):
            start_idx = np.argmin(np.abs(time_orig - start_time))

        if np.isfinite(end_time.astype("datetime64[ns]").astype("int64")):
            end_idx = np.argmin(np.abs(time_orig - end_time))

        # Create subplots for start and end (if both exist)
        if start_idx is not None and end_idx is not None:
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(figsize[0] * 2, figsize[1]))
            axes = [ax1, ax2]
            titles = ["Deployment Start", "Deployment End"]
            indices = [start_idx, end_idx]
            times_ref = [start_time, end_time]
        elif start_idx is not None:
            fig, ax1 = plt.subplots(1, 1, figsize=figsize)
            axes = [ax1]
            titles = ["Deployment Start"]
            indices = [start_idx]
            times_ref = [start_time]
        elif end_idx is not None:
            fig, ax1 = plt.subplots(1, 1, figsize=figsize)
            axes = [ax1]
            titles = ["Deployment End"]
            indices = [end_idx]
            times_ref = [end_time]
        else:
            print(f"Skipping instrument {i}: no valid deployment times")
            continue

        # Plot each boundary
        for ax, title, idx, time_ref in zip(
            axes, titles, indices, times_ref, strict=False
        ):
            # Define range around the boundary
            start_range = max(0, idx - n_samples)
            end_range = min(len(time_orig), idx + n_samples + 1)

            time_window = time_orig[start_range:end_range]
            temp_window = temp_orig[start_range:end_range]

            # Plot connecting line first (so points appear on top)
            ax.plot(
                time_window,
                temp_window,
                "b-",
                linewidth=1.5,
                alpha=0.8,
                label="Temperature",
            )

            # Plot individual measurements as filled red circles
            ax.plot(
                time_window,
                temp_window,
                "ro",
                markersize=6,
                markerfacecolor="red",
                markeredgecolor="darkred",
                markeredgewidth=1,
                label="Measurements",
            )

            # Highlight the exact predicted boundary time
            ax.axvline(
                time_ref,
                color="green",
                linestyle="--",
                linewidth=2,
                alpha=0.8,
                label=f"Predicted {title.split()[1]}",
            )

            # Add horizontal line for split value
            ax.axhline(
                split_val,
                color="orange",
                linestyle=":",
                linewidth=1.5,
                alpha=0.7,
                label=f"Split Value ({split_val:.2f}°C)",
            )

            ax.set_xlabel("Time")
            ax.set_ylabel("Temperature (°C)")
            ax.set_title(f"{title} - {instruments[i]} at {depths[i]:.0f}m")
            ax.legend(loc="best")
            ax.grid(True, alpha=0.3)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
            ax.xaxis.set_major_locator(
                mdates.MinuteLocator(interval=max(1, n_samples // 5))
            )
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        plt.tight_layout()
        plt.show()


def lag_correlation(
    x: np.ndarray,
    y: np.ndarray,
    max_lag: int,
    min_overlap: int = 10,
) -> np.ndarray:
    """Pearson correlation at integer lags in ``[-max_lag, max_lag]``.

    Parameters
    ----------
    x, y : np.ndarray
        1-D arrays of the same length.  NaN values are excluded pairwise
        at each lag.
    max_lag : int
        Maximum lag (in samples) to compute.  Output has length
        ``2 * max_lag + 1``.
    min_overlap : int, optional
        Minimum number of finite pairs required to compute a correlation
        at a given lag.  Lags with fewer pairs return NaN (default 10).

    Returns
    -------
    np.ndarray
        Correlation coefficients, shape ``(2 * max_lag + 1,)``.  Positive
        lags mean *x* leads *y*; NaN where overlap is insufficient.

    Raises
    ------
    ValueError
        If *x* and *y* do not have the same shape.

    """
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if x.shape != y.shape:
        raise ValueError("x and y must have same length (subsample both).")
    corrs = np.full(2 * max_lag + 1, np.nan)
    for k, lag in enumerate(range(-max_lag, max_lag + 1)):
        if lag < 0:
            xs = x[:lag]  # up to last |lag|
            ys = y[-lag:]  # from |lag| to end
        elif lag > 0:
            xs = x[lag:]  # from lag to end
            ys = y[:-lag]  # up to n-lag
        else:
            xs, ys = x, y

        m = ~np.isnan(xs) & ~np.isnan(ys)
        if m.sum() >= min_overlap:
            xc = xs[m] - np.nanmean(xs[m])
            yc = ys[m] - np.nanmean(ys[m])
            denom = np.nanstd(xc) * np.nanstd(yc)
            corrs[k] = (np.nanmean(xc * yc) / denom) if denom > 0 else np.nan
    return corrs


def split_value(data: np.ndarray, nbins: int = 30) -> float:
    """Find the histogram-based threshold between two data modes.

    Computes a ``nbins``-bin histogram, locates the two highest peaks, and
    returns the left edge of the minimum-count bin between them.

    Parameters
    ----------
    data : np.ndarray
        1-D array (NaNs are removed before binning).
    nbins : int, optional
        Number of histogram bins (default 30).  Increase if the two modes
        are not resolved.

    Returns
    -------
    float
        Left edge of the minimum-count bin between the two dominant peaks.

    Raises
    ------
    ValueError
        If fewer than two histogram peaks are detected.  This occurs when
        the data are unimodal or when *nbins* is too coarse to resolve the
        two modes.

    """
    data = data[~np.isnan(data)]  # Remove NaNs for histogram
    # Step 1: Create histogram
    counts, bins = np.histogram(data, bins=nbins)

    # Step 2: Find peaks
    peaks, _ = find_peaks(counts)

    # Step 3: Find minimum between first two major peaks
    if len(peaks) < 2:
        raise ValueError(
            f"split_value: fewer than 2 histogram peaks found in data "
            f"(found {len(peaks)}). Data may be unimodal or nbins={nbins} too coarse."
        )
    i1, i2 = sorted(peaks[:2])
    split_index = np.argmin(counts[i1:i2]) + i1
    return bins[split_index]
