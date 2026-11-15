"""xarray conveniences the pipeline stages share."""

from __future__ import annotations

import numpy as np
import xarray as xr


def drop_all_zero_vars(ds: xr.Dataset, prefixes: list[str]) -> xr.Dataset:
    """Drop variables whose finite values are all zero.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset to filter.
    prefixes : list of str
        Variable name prefixes to check (e.g. ``["amplitude_beam", "analog_input_"]``).
        Any variable whose name starts with one of these prefixes and whose
        finite values are all zero (or has no finite values) is dropped.

    Returns
    -------
    xr.Dataset
        Dataset with all-zero prefix-matched variables removed.

    """
    to_drop = []
    for vname in ds.data_vars:
        if not any(vname.startswith(p) for p in prefixes):
            continue
        arr = ds[vname].values
        finite = arr[np.isfinite(arr)] if np.issubdtype(arr.dtype, np.floating) else arr
        if finite.size == 0 or np.all(finite == 0):
            to_drop.append(vname)
    return ds.drop_vars(to_drop) if to_drop else ds


def time_axis(start: np.datetime64, end: np.datetime64, dt_seconds: int) -> np.ndarray:
    """Return the steps of *dt_seconds* that are whole multiples of it within a window.

    Whole multiples of the step since the Unix epoch (on the hour at 3600 s), so a
    mooring's stack and the stacks of every deployment of one site share their
    steps; ``concat`` joins them on this axis.

    Parameters
    ----------
    start, end : numpy.datetime64
        The window, both ends included.
    dt_seconds : int
        The step in seconds.

    Returns
    -------
    numpy.ndarray
        ``datetime64[ns]`` steps; empty when no whole step falls in the window.
    """
    step_ns = int(dt_seconds) * 1_000_000_000
    start_ns = int(np.datetime64(start, "ns").astype(np.int64))
    end_ns = int(np.datetime64(end, "ns").astype(np.int64))
    first_ns = -(-start_ns // step_ns) * step_ns
    # Counted in integers: np.arange sizes its result in floating point, which at
    # nanosecond magnitudes can drop a last step that falls exactly on *end*.
    n = max(0, (end_ns - first_ns) // step_ns + 1)
    return (first_ns + step_ns * np.arange(n, dtype=np.int64)).astype("datetime64[ns]")


def time_step_seconds(ds: xr.Dataset) -> float:
    """Return a stack or grid file's time step in seconds.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack or grid dataset with a ``time`` axis.

    Returns
    -------
    float
        The ``dt_seconds`` attribute when the file has a numeric one, else the median step
        of its time axis; NaN when it has neither (fewer than two times).
    """
    try:
        return float(ds.attrs["dt_seconds"])
    except (KeyError, TypeError, ValueError):
        pass  # absent or not a number: use the time axis
    if "time" not in ds or ds.sizes.get("time", 0) < 2:
        return float("nan")
    steps = np.diff(ds["time"].values) / np.timedelta64(1, "s")
    return float(np.median(steps))
