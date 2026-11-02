"""Quick dataset inspection helpers for interactive use.

``inspect.variables(ds)`` returns a styled table of an xarray Dataset's (or a netCDF
file's) variables — dims, units, standard name, dtype — and ``inspect.attributes(ds)``
returns its global attributes. Handy in notebooks; no plotting involved.

Examples
--------
>>> from oceanarray import inspect
>>> inspect.variables(ds)   # styled variable table
>>> inspect.attributes(ds)  # global-attribute table

"""

from typing import Any

import xarray as xr
from pandas import DataFrame

from oceanarray import _deprecated

_Data = str | xr.Dataset


def variables(data: _Data) -> Any:
    """Return a styled table of the variables in *data*.

    Parameters
    ----------
    data : str or xarray.Dataset
        A path to a netCDF file, or an xarray Dataset.

    Returns
    -------
    pandas.io.formats.style.Styler
        A styled table with columns ``dims``, ``name``, ``units``, ``comment``,
        ``standard_name``, and ``dtype``, indexed by variable name.

    """
    if isinstance(data, str):
        print(f"information is based on file: {data}")
        dataset = xr.open_dataset(data)
    elif isinstance(data, xr.Dataset):
        print("information is based on xarray Dataset")
        dataset = data
    else:
        raise TypeError("Input data must be a file path (str) or an xarray Dataset")

    try:
        info = {}
        for i, key in enumerate(dataset.variables):
            var = dataset.variables[key]
            dims = var.dims[0] if len(var.dims) == 1 else "string"
            info[i] = {
                "name": str(key),
                "dims": dims,
                "units": var.attrs.get("units", ""),
                "comment": var.attrs.get("comment", ""),
                "standard_name": var.attrs.get("standard_name", ""),
                "dtype": str(var.dtype),
            }
    finally:
        # Close only a dataset we opened here, never the caller's.
        if isinstance(data, str):
            dataset.close()

    columns = ["dims", "name", "units", "comment", "standard_name", "dtype"]
    df = DataFrame(info).T if info else DataFrame(columns=columns)
    dim = df["dims"]
    dim[dim.str.startswith("str")] = "string"
    df["dims"] = dim

    return (
        df.sort_values(["dims", "name"])
        .reset_index(drop=True)
        .loc[:, columns]
        .set_index("name")
        .style
    )


def attributes(data: _Data) -> Any:
    """Return a table of the global attributes of *data*.

    Parameters
    ----------
    data : str or xarray.Dataset
        A path to a netCDF file, or an xarray Dataset.

    Returns
    -------
    pandas.DataFrame
        A table with columns ``Attribute``, ``Value``, and ``DType``.

    """
    if isinstance(data, str):
        print(f"information is based on file: {data}")
        dataset = xr.open_dataset(data)
    elif isinstance(data, xr.Dataset):
        print("information is based on xarray Dataset")
        dataset = data
    else:
        raise TypeError("Input data must be a file path (str) or an xarray Dataset")

    try:
        info = {}
        for i, key in enumerate(dataset.attrs):
            value = dataset.attrs[key]
            info[i] = {
                "Attribute": key,
                "Value": value,
                "DType": type(value).__name__,
            }
    finally:
        # Close only a dataset we opened here, never the caller's.
        if isinstance(data, str):
            dataset.close()

    return DataFrame(info).T


#: Deprecated names (they shadow the builtin ``vars`` when imported by name).
vars = _deprecated.alias("oceanarray.inspect.vars", variables)
attrs = _deprecated.alias("oceanarray.inspect.attrs", attributes)
