"""Reader for single-instrument OceanSITES-style netCDF time series and ADCP profiles.

Originator-processed files (OceanSITES 1.3 and the OSNAP variant).  Known
parameter codes (:data:`OCEANSITES_VARIABLES`) are renamed to oceanarray names;
``<CODE>_QC`` companions are kept as ``<name>_qc_originator``; a file with no
``<CODE>_QC`` but a ``<CODE>_flag`` flag variable (the OSNAP egM 2018 files) has that
kept instead.
"""

import logging
from pathlib import Path

import numpy as np
import xarray as xr

from oceanarray import provenance
from oceanarray.config.parameters import ADCP_BIN_DIM

log = logging.getLogger(__name__)

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

#: Codes read only from an ADCP profile file (velocities on ``TIME × BINDEPTH``).
#: There ``PRES`` on the bin dimension is ``bin_pressure`` and ``PREST`` the head's.
PROFILE_VARIABLES: dict[str, str] = {
    "ECUR": "error_velocity",
    "PREST": "pressure",
    "HEADING": "heading",
    "PITCH": "pitch",
    "ROLL": "roll",
    "EAMP": "amplitude",
    "CMAG": "correlation",
    "PERG": "percent_good",
}

_ORIENTATION = {"upward": "up", "up": "up", "downward": "down", "down": "down"}
_ANGLE_UNITS = ("degree", "degrees", "deg")

#: OceanSITES reference table 2, used for a ``_QC`` variable that carries no
#: ``flag_values`` / ``flag_meanings`` of its own.
OCEANSITES_FLAG_VALUES: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9)
OCEANSITES_FLAG_MEANINGS: str = (
    "no_qc_performed good_data probably_good_data bad_data_that_are_potentially_"
    "correctable bad_data value_changed not_used nominal_value interpolated_value "
    "missing_value"
)


#: Variable attributes not kept from an originator file: those written here in our
#: own terms, and those describing the file's layout or packing rather than the data.
_NOT_KEPT: frozenset[str] = frozenset(
    {
        "units",
        "long_name",
        "standard_name",
        "_FillValue",
        "missing_value",
        "scale_factor",
        "add_offset",
        "coordinates",
        "ancillary_variables",
        "axis",
        "_ChunkSizes",
        "DIMENSION_LIST",
    }
)


#: CF attributes given in a packed variable's stored (packed) units.
_PACKED_BOUNDS = ("valid_min", "valid_max", "valid_range")


def _unpacked(da: xr.DataArray) -> dict:
    """Return a variable's attributes with packed bounds in the decoded units.

    CF gives ``valid_min`` / ``valid_max`` / ``valid_range`` of a packed variable
    in its stored units; the values are decoded (``scale_factor``, ``add_offset``),
    so the bounds are too, or they would not describe the values kept.
    """
    attrs = dict(da.attrs)
    scale = da.encoding.get("scale_factor", 1)
    offset = da.encoding.get("add_offset", 0)
    if scale == 1 and offset == 0:
        return attrs
    for key in _PACKED_BOUNDS:
        if key in attrs:
            attrs[key] = np.asarray(attrs[key], dtype=np.float64) * scale + offset
            if attrs[key].size == 1:
                attrs[key] = attrs[key].item()
    return attrs


#: Positional variables: outside the valid range is missing (originators use an
#: undeclared 99999 fill there, and a wrong position puts data in the wrong place).
POSITIONAL_VARIABLES = frozenset({"BINDEPTH", "DEPTH", "PRES", "INSTRDEPTH"})


def _outside_valid_range(
    src: xr.Dataset, fname: str
) -> tuple[xr.Dataset, dict[str, int], dict[str, int]]:
    """Set positions outside ``valid_min``/``valid_max`` missing; count the rest.

    Only :data:`POSITIONAL_VARIABLES` are masked: xarray does not apply the CF
    valid range, and some files mark a missing position only this way
    (``BINDEPTH`` 99999 with no ``_FillValue``).  Other variables keep their
    values, since an originator's range can be wrong (``ROLL`` ``valid_min`` 0;
    ``CNDC`` ``valid_max`` in S/m with units mS/cm).  Integer positions are
    checked too (as float once masked); other integer variables (flags) are not.
    A ``valid_range`` that is not two values is ignored.

    Parameters
    ----------
    src : xarray.Dataset
        The open originator file.
    fname : str
        The file name, for messages.

    Returns
    -------
    xarray.Dataset
        *src* with positions outside the range missing.
    dict of str to int
        Per positional variable, the number of values set missing.
    dict of str to int
        Per other variable, the number of values outside the range and kept.
    """
    missing, kept = {}, {}
    for name in list(src.variables):
        da = src[name]
        kinds = "iuf" if name in POSITIONAL_VARIABLES else "f"
        if da.dtype.kind not in kinds:
            continue
        attrs = _unpacked(da)
        lo = hi = None
        if "valid_range" in attrs:
            vr = np.atleast_1d(attrs["valid_range"])
            if vr.size == 2:
                lo, hi = vr
            else:
                log.warning(
                    f"  {fname}: {name} valid_range {vr.tolist()} is not two values; "
                    "ignored"
                )
        lo = attrs.get("valid_min", lo)
        hi = attrs.get("valid_max", hi)
        if lo is None and hi is None:
            continue
        ok = (da >= (-np.inf if lo is None else lo)) & (
            da <= (np.inf if hi is None else hi)
        )
        n = int((da.notnull() & ~ok).sum())
        if not n:
            continue
        if name not in POSITIONAL_VARIABLES:
            kept[name] = n
            continue
        missing[name] = n
        if name in src.coords:
            src = src.assign_coords({name: da.where(ok)})
        else:
            src[name] = da.where(ok)
    return src, missing, kept


def _usable_flag_variable(da: xr.DataArray, data: xr.DataArray) -> str | None:
    """Return why a ``<CODE>_flag`` variable cannot be used as flags, or None."""
    from oceanarray.pipeline.qc.originator import vocabulary

    if da.dtype.kind not in "iuf":
        return "not numeric"
    if da.shape != data.shape:
        return f"shape {da.shape} is not the data's {data.shape}"
    if "flag_values" not in da.attrs or "flag_meanings" not in da.attrs:
        return "no flag_values / flag_meanings"
    meanings, unmapped = vocabulary()
    unknown = [
        w
        for w in str(da.attrs["flag_meanings"]).lower().split()
        if w not in meanings and w not in unmapped
    ]
    if unknown:
        return f"flag words not in the meaning vocabulary: {' '.join(unknown)}"
    return None


def _originator_flags(da: xr.DataArray) -> xr.DataArray:
    """Return an OceanSITES ``_QC`` (or ``_flag``) variable as originator flags."""
    vals = da.values
    flags = np.where(np.isfinite(vals), vals, 9).astype("int8")
    attrs = {
        "long_name": f"originator QC flag ({da.name})",
        "originator_flag_variable": str(da.name),
    }
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


def _add_bin_geometry(
    out: xr.Dataset, src: xr.Dataset, prof: str, name: str
) -> xr.Dataset:
    """Add an ADCP profile's ``range`` coordinate, bin depths and orientation.

    The originator's per-time bin depths (``DEPTH`` or a 2-D ``BINDEPTH``) are kept
    as ``bin_depth`` and, when the file has no per-bin ``PRES``, converted to
    ``bin_pressure`` (TEOS-10, file latitude). A file with only static bin depths
    gets ``range`` alone; stage 3 then places the bins from the head pressure.
    ``range`` is the median distance from the head, for the stack row and plots.

    Parameters
    ----------
    out : xarray.Dataset
        The dataset being built, with ``time`` and ``N_BINS``.
    src : xarray.Dataset
        The open originator file.
    prof : str
        The file's bin dimension.
    name : str
        The file name, for messages.

    Returns
    -------
    xarray.Dataset
        *out* with ``range``, ``orientation_instrument`` and, when per-time bin depths
        exist, ``bin_depth`` / ``bin_pressure``.

    Raises
    ------
    ValueError
        If the file has no bin depths, or its stated orientation contradicts them.
    """
    from oceanarray.pipeline.pressure import (
        ORIGINATOR_BIN_PRESSURE_ATTRS,
        depth_from_pressure,
        originator_bin_geometry,
        pressure_from_depth,
    )

    lat = float(np.ravel(src["LATITUDE"].values)[0])
    head = float(np.ravel(src["INSTRDEPTH"].values)[0]) if "INSTRDEPTH" in src else None
    depth_var = next(
        (v for v in ("DEPTH", prof) if v in src and src[v].dims == ("TIME", prof)),
        None,
    )
    if depth_var is not None:
        bin_depth = src[depth_var].values.astype(float)
        out["bin_depth"] = xr.DataArray(
            bin_depth,
            dims=("time", ADCP_BIN_DIM),
            attrs={
                "units": "m",
                "positive": "down",
                "long_name": "bin depth",
                "comment": f"originator's per-time bin depth ({depth_var}, {name})",
            },
        )
        if "pressure" in out:
            head = depth_from_pressure(out["pressure"].values, lat)[:, None]
        if "bin_pressure" not in out:
            out["bin_pressure"] = xr.DataArray(
                pressure_from_depth(bin_depth, lat),
                dims=("time", ADCP_BIN_DIM),
                attrs={"comment": f"gsw.p_from_z(-{depth_var}, {lat:.4f})"},
            )
        else:
            out["bin_pressure"].attrs["comment"] = f"PRES on {prof} ({name})"
        out["bin_pressure"].attrs.update(ORIGINATOR_BIN_PRESSURE_ATTRS)
        if head is None:
            raise ValueError(
                f"{name}: no head pressure (PREST) or INSTRDEPTH; the bins cannot be "
                "placed relative to the instrument. Give the file one of them."
            )
        offset = bin_depth - head
    elif prof in src and src[prof].ndim == 1 and head is not None:
        offset = src[prof].values.astype(float) - head
    else:
        raise ValueError(f"{name}: no bin depths on {prof}; cannot place the bins")

    geometry, rng = originator_bin_geometry(offset)
    stated = _ORIENTATION.get(str(src["UCUR"].attrs.get("sensor_orientation", "")))
    if stated and stated != geometry:
        raise ValueError(
            f"{name}: sensor_orientation says '{stated}' but the bin depths are "
            f"{'above' if geometry == 'up' else 'below'} the instrument"
        )
    out.attrs["orientation_instrument"] = geometry
    out.attrs["orientation_instrument_source"] = (
        "file: sensor_orientation, checked against the bin depths"
        if stated
        else "file: bin depths relative to the instrument"
    )
    out = out.assign_coords(
        range=(
            ADCP_BIN_DIM,
            rng,
            {
                "units": "m",
                "long_name": "distance from the transducer to the bin centre",
                "comment": "nominal, from the originator's bin depths; places the "
                "stack row only, not bin_pressure",
            },
        )
    )
    # Bins never given a depth cannot be placed; the rest go in order of distance
    # from the head, as an instrument's own bins do.
    if not np.isfinite(rng).all():
        log.warning(
            f"  {name}: {int((~np.isfinite(rng)).sum())} bin(s) with no depth dropped"
        )
    return out.isel({ADCP_BIN_DIM: np.flatnonzero(np.isfinite(rng))}).sortby("range")


def read_oceansites(path: str | Path) -> xr.Dataset:
    """Read a single-instrument OceanSITES-style netCDF time series.

    Accepts the OceanSITES 1.3 layout (variables on ``(TIME, DEPTH)`` with
    ``LATITUDE`` / ``LONGITUDE`` dimensions) and the OSNAP variant with variables
    on ``(TIME,)`` and position on ``DEPTH``.  Known parameter codes
    (:data:`OCEANSITES_VARIABLES`) are renamed to oceanarray names on a ``time``
    dimension, keeping the file's ``units`` string for the units pass to resolve.
    ``<CODE>_QC`` variables become ``<name>_qc_originator`` (or, with no
    ``<CODE>_QC``, a ``<CODE>_flag`` that is a flag variable whose words are in the
    meaning vocabulary; the source name is kept as ``originator_flag_variable``); each
    variable's
    own attributes (``comment``, ``uncertainty``, ``accuracy``, ``sensor_*`` ...) are
    kept as ``originator_<attribute>``.  The originator's
    global attributes are kept with an ``originator_`` prefix, and because CF
    ``eastward``/``northward_sea_water_velocity`` are geographic, a file with
    velocities is marked ``coordinate_system = "ENU"`` with
    ``originator_magnetic_declination`` recorded so the declination is not applied
    again.  Records whose ``TIME`` is ``NaT`` (originator padding of a record that
    ended early) are kept here; :func:`oceanarray.readers.read` and stage 1 drop
    them, with the count, for every format.

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
    src, n_missing, n_kept = _outside_valid_range(
        xr.open_dataset(path, engine="netcdf4", mask_and_scale=True), path.name
    )
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

    ucur = _squeeze_non_time(src["UCUR"]) if "UCUR" in src else None
    prof = ucur.dims[1] if ucur is not None and ucur.ndim == 2 else None
    codes = OCEANSITES_VARIABLES | (PROFILE_VARIABLES if prof else {})
    dims_map = {"TIME": "time"} | ({prof: ADCP_BIN_DIM, "BEAM": "beam"} if prof else {})

    out = xr.Dataset(coords={"time": src["TIME"].values})
    for code in [c for c in codes if c in src.data_vars]:
        da = _squeeze_non_time(src[code])
        extra = [d for d in da.dims if d not in dims_map]
        if extra:
            raise ValueError(
                f"{path.name}: {code} is on {extra[0]} ({da.sizes[extra[0]]} levels); "
                "one instrument per entry — this reader takes one depth"
            )
        da = da.transpose(*[d for d in dims_map if d in da.dims])
        name = codes[code]
        if prof in da.dims and name == "pressure":
            name = "bin_pressure"
        attrs = {
            k: v
            for k, v in da.attrs.items()
            if k in ("units", "long_name", "standard_name") and v != ""
        }
        attrs["original_name"] = code
        if name in ("heading", "pitch", "roll") and attrs.get("units") not in (
            _ANGLE_UNITS
        ):
            # One originator file gives HEADING the attributes of PRES.
            stated = attrs.get("units")
            log.warning(f"  {path.name}: {code} units {stated!r} → degree")
            attrs |= {
                "units": "degree",
                "comment": f"units corrected to degree from the file's {stated!r}",
            }
            if stated is not None:
                attrs["originator_units"] = stated
        # The originator's own description of the variable and its sensor, verbatim
        # (stage 1 moves them to the sensor record).
        attrs.update(
            {
                f"originator_{k}": v if np.isscalar(v) else str(v)
                for k, v in _unpacked(da).items()
                if k not in _NOT_KEPT
            }
        )
        out[name] = xr.DataArray(
            da.values, dims=[dims_map[d] for d in da.dims], attrs=attrs
        )
        qc = f"{code}_QC"
        if qc not in src.data_vars and f"{code}_flag" in src.data_vars:
            # Non-standard name (OSNAP egM 2018): used only when it is a real flag
            # variable whose words we know.
            why = _usable_flag_variable(src[f"{code}_flag"], src[code])
            if why is None:
                qc = f"{code}_flag"
            else:
                log.warning(f"  {path.name}: {code}_flag not used as flags: {why}")
        if qc in src.data_vars:
            qc_da = _squeeze_non_time(src[qc]).transpose(*da.dims)
            out[f"{name}_qc_originator"] = _originator_flags(qc_da.rename(qc)).rename(
                dims_map
            )
    if prof:
        out = _add_bin_geometry(out, src, prof, path.name)

    for key in ("LATITUDE", "LONGITUDE", "DEPTH", "INSTRDEPTH"):
        if key in src.variables and src[key].size == 1:
            out.attrs[f"originator_{key.lower()}"] = float(np.ravel(src[key].values)[0])
    for key, val in src.attrs.items():
        out.attrs[f"originator_{key}"] = val if np.isscalar(val) else str(val)
    out.attrs["originator_file"] = path.name
    out.attrs["originator_sha256"] = provenance.sha256(path)
    if {"east_velocity", "north_velocity"} & set(out.data_vars):
        out.attrs["coordinate_system"] = "ENU"
        magvar = np.ravel(src["MAGVAR"].values) if "MAGVAR" in src else np.array([])
        magvar = magvar[np.abs(magvar) <= 360]
        out.attrs["originator_magnetic_declination"] = (
            "applied by the originator (file declares geographic eastward/northward "
            "velocity); "
            + (
                f"MAGVAR median {np.median(magvar):.2f} degree"
                if magvar.size
                else "value not recorded"
            )
        )
        if prof:
            out.attrs["coordinate_system_source"] = (
                "originator: geographic east/north velocity in the file"
            )
    from oceanarray.pipeline._stage import append_history

    for counts, what, say in (
        (n_missing, "set missing", log.info),
        (n_kept, "kept", log.warning),
    ):
        if counts:
            note = f"outside the originator's valid range, {what}: " + ", ".join(
                f"{v} {n}" for v, n in counts.items()
            )
            say(f"  {path.name}: {note}")
            append_history(out, note)
    src.close()
    return out
