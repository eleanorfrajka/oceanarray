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
