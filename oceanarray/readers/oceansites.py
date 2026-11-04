"""Reader for single-instrument OceanSITES-style netCDF time series.

Originator-processed files (OceanSITES 1.3 and the OSNAP variant).  Known
parameter codes (:data:`OCEANSITES_VARIABLES`) are renamed to oceanarray names;
``<CODE>_QC`` companions are kept as ``<name>_qc_originator``.
"""

from pathlib import Path

import numpy as np
import xarray as xr

#: OceanSITES parameter codes → oceanarray registry names.  The QC companion
#: ``<CODE>_QC`` is carried as ``<name>_qc_originator``.
OCEANSITES_VARIABLES: dict[str, str] = {
    "TEMP": "temperature",
    "PSAL": "salinity",
    "CNDC": "conductivity",
    "PRES": "pressure",
    "UCUR": "east_velocity",
    "VCUR": "north_velocity",
    "WCUR": "up_velocity",
    "CSPD": "current_speed",
    "CDIR": "current_direction",
    "DOX2": "dissolved_oxygen",
    "TURB": "turbidity",
}

#: OceanSITES reference table 2, used for a ``_QC`` variable that carries no
#: ``flag_values`` / ``flag_meanings`` of its own.
OCEANSITES_FLAG_VALUES: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9)
OCEANSITES_FLAG_MEANINGS: str = (
    "no_qc_performed good_data probably_good_data bad_data_that_are_potentially_"
    "correctable bad_data value_changed not_used nominal_value interpolated_value "
    "missing_value"
)


def _originator_flags(da: xr.DataArray) -> xr.DataArray:
    """Return an OceanSITES ``_QC`` variable as an originator flag variable."""
    vals = da.values
    flags = np.where(np.isfinite(vals), vals, 9).astype("int8")
    attrs = {"long_name": f"originator QC flag ({da.name})"}
    if "flag_values" in da.attrs and "flag_meanings" in da.attrs:
        attrs["flag_values"] = np.asarray(da.attrs["flag_values"]).astype("int8")
        attrs["flag_meanings"] = str(da.attrs["flag_meanings"])
    else:
        # No flag meanings in the file: attach OceanSITES reference table 2 and
        # record in flag_meanings_source that they were assumed, not read.
        attrs["flag_values"] = np.array(OCEANSITES_FLAG_VALUES, dtype="int8")
        attrs["flag_meanings"] = OCEANSITES_FLAG_MEANINGS
        attrs["flag_meanings_source"] = (
            "assumed: OceanSITES reference table 2 (the file has no flag_meanings)"
        )
    return xr.DataArray(flags, dims=da.dims, attrs=attrs)


def read_oceansites(path: str | Path) -> xr.Dataset:
    """Read a single-instrument OceanSITES-style netCDF time series.

    Accepts the OceanSITES 1.3 layout (variables on ``(TIME, DEPTH)`` with
    ``LATITUDE`` / ``LONGITUDE`` dimensions) and the OSNAP variant with variables
    on ``(TIME,)`` and position on ``DEPTH``.  Known parameter codes
    (:data:`OCEANSITES_VARIABLES`) are renamed to oceanarray names on a ``time``
    dimension, keeping the file's ``units`` string for the units pass to resolve.
    ``<CODE>_QC`` variables become ``<name>_qc_originator``.  The originator's
    global attributes are kept with an ``originator_`` prefix, and because CF
    ``eastward``/``northward_sea_water_velocity`` are geographic, a file with
    velocities is marked ``coordinate_system = "ENU"`` with
    ``originator_magnetic_declination`` recorded so the declination is not applied
    again.

    Parameters
    ----------
    path : str or pathlib.Path
        The netCDF file.

    Returns
    -------
    xarray.Dataset
        One ``time`` dimension; renamed variables, originator flags and
        ``originator_*`` attributes.

    Raises
    ------
    ValueError
        If the file holds none of the known parameter codes.
    """
    path = Path(path)
    src = xr.open_dataset(path, engine="netcdf4", mask_and_scale=True)
    if "TIME" not in src.dims:
        raise ValueError(
            f"{path.name}: no TIME dimension; not an OceanSITES time series"
        )
    found = [code for code in OCEANSITES_VARIABLES if code in src.data_vars]
    if not found:
        raise ValueError(
            f"{path.name}: none of {sorted(OCEANSITES_VARIABLES)} present; not a "
            f"supported OceanSITES file"
        )

    def _squeeze_non_time(v: xr.DataArray) -> xr.DataArray:
        """Drop size-1 dims except TIME, so a one-record file keeps its time axis."""
        # A plain squeeze(drop=True) would also drop a length-1 TIME, leaving a scalar
        # that cannot take dims="time" (data path) and has no dims[0] (QC path).
        extra = [d for d in v.dims if d != "TIME" and v.sizes[d] == 1]
        return v.squeeze(dim=extra, drop=True) if extra else v

    out = xr.Dataset(coords={"time": src["TIME"].values})
    for code in found:
        da = _squeeze_non_time(src[code])
        name = OCEANSITES_VARIABLES[code]
        attrs = {k: v for k, v in da.attrs.items() if k in ("units", "long_name")}
        attrs["standard_name"] = da.attrs.get("standard_name", "")
        attrs["original_name"] = code
        out[name] = xr.DataArray(da.values, dims="time", attrs=attrs)
        qc = f"{code}_QC"
        if qc in src.data_vars:
            qc_da = _squeeze_non_time(src[qc])
            out[f"{name}_qc_originator"] = _originator_flags(qc_da.rename(qc)).rename(
                {qc_da.dims[0]: "time"}
            )

    for key in ("LATITUDE", "LONGITUDE", "DEPTH"):
        if key in src.variables:
            out.attrs[f"originator_{key.lower()}"] = float(np.ravel(src[key].values)[0])
    for key, val in src.attrs.items():
        out.attrs[f"originator_{key}"] = val if np.isscalar(val) else str(val)
    out.attrs["originator_file"] = path.name
    if {"east_velocity", "north_velocity"} & set(out.data_vars):
        out.attrs["coordinate_system"] = "ENU"
        out.attrs["originator_magnetic_declination"] = (
            "applied by the originator (file declares geographic eastward/northward "
            "velocity); value not recorded"
        )
    src.close()
    return out
