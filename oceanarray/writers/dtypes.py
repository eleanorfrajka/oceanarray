"""Storage dtypes for written files: the smallest dtype that holds each variable."""

from __future__ import annotations

from typing import Any

import numpy as np
import xarray as xr

_INT32 = np.iinfo(np.int32)


def is_position(name: str) -> bool:
    """Return whether *name* holds a position, kept at double precision.

    One rule for variables and attributes: ``latitude`` / ``longitude`` anywhere in
    the name, or ACDD's ``geospatial_lat_*`` / ``geospatial_lon_*``.

    Parameters
    ----------
    name : str
        A variable or attribute name.

    Returns
    -------
    bool
        True for a position.
    """
    low = name.lower()
    return (
        "latitude" in low
        or "longitude" in low
        or low.startswith(("geospatial_lat", "geospatial_lon"))
    )


def find_best_dtype(var_name: str, da: xr.DataArray) -> type:
    """Determine the optimal storage dtype for a variable.

    Parameters
    ----------
    var_name : str
        Variable name.
    da : xr.DataArray
        Data array to inspect.

    Returns
    -------
    type
        Recommended numpy dtype.

    Notes
    -----
    Rules applied in order:

    - String / datetime / timedelta / object variables: unchanged.
    - ``time`` in name: unchanged (preserve datetime64 / float encoding).
    - ``*_qc`` suffix or ``flag`` in name: ``int8``.
    - ``serial_number`` or ``serial``: ``int32``.
    - ``latitude`` / ``longitude`` in name: ``float64``.
    - Integer input: downsize to ``int32`` if stored as ``int64``, else unchanged.
    - ``float64`` input: ``float32``.
    - Anything else: unchanged.

    """
    input_dtype = da.dtype.type
    if da.dtype.kind in ("U", "S", "O", "M", "m"):
        return input_dtype
    if "time" in var_name.lower():
        return input_dtype
    if var_name.endswith("_qc") or "flag" in var_name:
        return np.int8
    if var_name in ("serial_number", "serial"):
        return np.int32
    if is_position(var_name):
        return np.float64
    if da.dtype.kind in ("i", "u") and da.dtype.itemsize == 8:
        return np.int32
    if input_dtype == np.float64:
        return np.float32
    return input_dtype


def cast_output_dtypes(ds: xr.Dataset) -> xr.Dataset:
    """Cast every variable in *ds* to its optimal storage dtype.

    Calls :func:`find_best_dtype` per variable and rebuilds only those that
    change.  Attributes are preserved.  The input dataset is not modified.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset to cast.

    Returns
    -------
    xr.Dataset
        New dataset with optimised dtypes, ready for NetCDF output.

    """
    updates: dict[str, xr.Variable] = {}
    for vname in ds.data_vars:
        var = ds[vname]
        target = find_best_dtype(vname, var)
        if np.dtype(target) != var.dtype:
            if np.issubdtype(np.dtype(target), np.integer) and np.issubdtype(
                var.dtype, np.floating
            ):
                # NaN cannot be represented as an integer; replace before cast.
                # QC variables use 9 (CF "missing value"); other integer vars use 0.
                fill_val = 9 if (vname.endswith("_qc") or "flag" in vname) else 0
                safe_vals = np.where(np.isfinite(var.values), var.values, fill_val)
                updates[vname] = xr.Variable(
                    var.dims, safe_vals.astype(target), attrs=var.attrs
                )
            else:
                updates[vname] = xr.Variable(
                    var.dims, var.values.astype(target), attrs=var.attrs
                )
    if not updates:
        return ds
    return ds.assign(updates)


def cast_attr(name: str, value: Any) -> Any:  # any attribute value
    """Return an attribute value at its storage precision, as variables are stored.

    A double becomes ``float32`` and a 64-bit integer ``int32`` (when it fits), so a
    global attribute such as ``dp_dbar`` is not written at a precision its variable
    counterparts are not. Positions (``latitude``, ``geospatial_lat_min`` ...) and
    anything not numeric are returned unchanged.

    Parameters
    ----------
    name : str
        The attribute name.
    value : Any
        The attribute value: a number, a numeric array, a string ...

    Returns
    -------
    Any
        The value cast, or *value* itself when no cast applies.
    """
    if isinstance(value, (bool, np.bool_)) or is_position(name):
        return value
    if "time" in name.lower():
        return value
    arr = np.asarray(value)
    if arr.dtype.kind == "f" and arr.dtype.itemsize == 8:
        out = arr.astype(np.float32)
    elif arr.dtype.kind in "iu" and arr.dtype.itemsize == 8:
        if arr.size == 0 or arr.min() < _INT32.min or arr.max() > _INT32.max:
            return value
        out = arr.astype(np.int32)
    else:
        return value
    return out[()] if out.ndim == 0 else out


def cast_attr_dtypes(attrs: dict[str, Any]) -> dict[str, Any]:
    """Return *attrs* with each value cast by :func:`cast_attr`.

    Parameters
    ----------
    attrs : dict of str to Any
        Global attributes.

    Returns
    -------
    dict of str to Any
        A new dict; *attrs* is not modified.
    """
    return {key: cast_attr(key, value) for key, value in attrs.items()}


def same_attr(name: str, stored: Any, expected: Any) -> bool:
    """Return whether a stored attribute equals the value that would be written now.

    *expected* is cast as the writer casts it before the two are compared, so a
    ``dp_dbar`` of 0.1 read back as ``float32`` still matches 0.1.

    Parameters
    ----------
    name : str
        The attribute name.
    stored : Any
        The value read from the file (None when absent).
    expected : Any
        The value the current run would write.

    Returns
    -------
    bool
        True when the two agree at storage precision.
    """
    if stored is None or expected is None:
        return stored is None and expected is None
    want = cast_attr(name, expected)
    try:
        # equal_nan: a NaN position or depth recorded as NaN is unchanged.
        if np.array_equal(np.asarray(stored), np.asarray(want), equal_nan=True):
            return True
    except (TypeError, ValueError):
        pass  # text: compared as written below
    return str(stored) == str(want)
