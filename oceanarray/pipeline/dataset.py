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
