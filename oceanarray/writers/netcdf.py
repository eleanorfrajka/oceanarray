"""Single netCDF writer for oceanarray processing stages.

One :func:`write` seam replaces the six write paths the stages used before
(three via seasenselib's ``NetCdfWriter``, three via bare ``to_netcdf``).  It
reproduces seasenselib's attribute and name handling — dict attributes to JSON,
slash names to underscores — and cleans attributes: a ``None`` value is dropped
with a warning (never written as ``""``).  Every numeric variable with at least
one dimension, coordinates included, is written with zlib level 4 and the shuffle
filter.

Every ``datetime64`` variable is pinned to ``seconds since 1970-01-01T00:00:00``,
float64, ``proleptic_gregorian`` (``_TIME_ENCODING``) rather than xarray's
per-file guess.  Decoded instants are unchanged; raw stored values change.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import xarray as xr
from xarray.coding.times import CFDatetimeCoder

from oceanarray.logger import log_info
from oceanarray.utilities import cast_output_dtypes

_DATETIME_ENCODING_ATTRS = ("units", "calendar")
# zlib level 4 with the shuffle filter.  Turning shuffle off for float64 — as
# ctdcast does — is byte-identical here: re-measured across every dune2 fixture on
# 2026-09-22, after the time pin made every ``time`` axis float64 (the largest
# float64 array in each file), shuffle changes 0 bytes on four fixtures and +21 on
# grid.  The shuffle decision is per package (it hurts ctdcast, helps seagliderOG1,
# is a wash for oceanarray), so do not "fix" this to match another package.
_COMPRESSION = {"zlib": True, "complevel": 4, "shuffle": True}
# dtype kinds that cannot be zlib-compressed (object / unicode / byte strings).
_UNCOMPRESSIBLE_KINDS = ("O", "U", "S")
# Encoding keys that fix stored values or CF interpretation and must survive a
# re-write byte-for-byte.  Everything else in a variable's encoding is storage
# layout (``contiguous``, ``chunksizes``, ``preferred_chunks``, ...) inherited
# when a dataset is read back; it must be dropped, or it collides with the
# compression filter (``contiguous`` + ``zlib``) or with a changed shape
# (stale ``chunksizes`` after a stage trims time).  This is an allowlist on
# purpose: an unrecognised key (a stale ``least_significant_digit`` from an
# externally-quantized input, an unknown future key) is dropped rather than
# re-applied — re-applying quantization would re-round already-rounded data, and
# a denylist would instead let unknown keys reach ``to_netcdf`` and raise.
# Time ``units``/``calendar`` are not listed: datetime64 variables are pinned to
# ``_TIME_ENCODING`` and never consult this list; object-dtype cftime time variables
# keep their ``units``/``calendar`` through a dedicated branch in ``_prepare`` (so a
# non-standard calendar and epoch are preserved, not re-guessed) and likewise never
# reach here; a timedelta64 variable's encoding is already stripped upstream by
# ``cast_output_dtypes`` (it rebuilds the variable), and xarray then re-derives a valid
# CF ``units`` for it on write — so no variable reaching this filter carries
# ``units``/``calendar`` in encoding.
_PRESERVED_ENCODING = (
    "dtype",
    "_FillValue",
    "missing_value",
    "scale_factor",
    "add_offset",
)
# Pinned time encoding: every datetime variable is written as float64 seconds since
# a fixed midnight epoch, not xarray's per-file guess (which varies with the file's
# first timestamp and, in the string form, with the installed pandas).  Decoded
# instants are unchanged; raw stored values change.  xarray normalises the epoch
# string, so this reads back on disk as ``seconds since 1970-01-01``.  No
# ``_FillValue`` is set — xarray writes ``nan`` itself for a float64 datetime and
# NaT round-trips.  Calendar is a deliberate per-package choice:
# ``proleptic_gregorian`` for OceanSITES here; seagliderOG1 uses ``gregorian``
# because the OG1 manual specifies it.  Two calendars across the family is
# intended — do not "harmonise" them.
_TIME_ENCODING = {
    "units": "seconds since 1970-01-01T00:00:00",
    "dtype": "float64",
    "calendar": "proleptic_gregorian",
}


#: Sentinel returned by :func:`_clean_attr_value` for a ``None`` value; the caller
#: drops the attribute instead of writing it.  None-valued attributes are dropped
#: with a warning; nothing here verifies that required globals are present.
_DROP = object()


def _clean_attr_value(value: object) -> object:
    """Return an attribute value in a form netCDF4 can store, or :data:`_DROP`.

    A ``dict`` becomes a JSON string, a non-empty list or tuple is kept when it
    renders as text and JSON-encoded otherwise, and everything else is returned
    unchanged.  A ``None`` value returns :data:`_DROP`: the writer drops the
    attribute (with a warning) rather than substituting ``""``, because an empty
    string is a silent stand-in for a missing value.

    Parameters
    ----------
    value : object
        The raw attribute value.

    Returns
    -------
    object
        A netCDF4-compatible attribute value, or :data:`_DROP` for ``None``.
    """
    if isinstance(value, dict):
        return json.dumps(value)
    if value is None:
        return _DROP
    if isinstance(value, bool):
        # netCDF4 has no boolean attribute type; store the CF-conventional 0/1.
        # (bool is a subclass of int, so this must precede any int handling.)
        return np.int8(value)
    if isinstance(value, (list, tuple)) and len(value) > 0:
        try:
            str(value)
            return value
        except (TypeError, ValueError):
            return json.dumps(list(value))
    return value


def _clean_attr_dict(attrs: dict, scope: str, *, stacklevel: int) -> dict:
    """Clean an attribute mapping, dropping ``None``-valued keys with a warning.

    Parameters
    ----------
    attrs : dict
        Raw attribute mapping (global or per-variable).
    scope : str
        Label used in the warning, e.g. ``"global"`` or ``"variable 'time'"``.
    stacklevel : int
        Frames from this call up to the user's :func:`write` call, so the warning
        points at the caller's line rather than into the writer.

    Returns
    -------
    dict
        Cleaned mapping with every ``None``-valued key removed.
    """
    cleaned: dict = {}
    for key, value in attrs.items():
        result = _clean_attr_value(value)
        if result is _DROP:
            warnings.warn(
                f"{scope}: dropping attribute {key!r} because its value is None",
                stacklevel=stacklevel,
            )
            continue
        cleaned[key] = result
    return cleaned


def _sanitize_names(ds: xr.Dataset) -> xr.Dataset:
    """Replace ``/`` in dimension, coordinate and variable names with ``_``.

    NetCDF/HDF5 treats ``/`` as a group separator, so a name containing one
    cannot be written.  Renamed variables gain an ``original_name`` attribute.
    Reproduces seasenselib's name sanitization; a no-op once names are canonical.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset whose names may contain slashes.

    Returns
    -------
    xarray.Dataset
        Dataset with slash-free names.

    Raises
    ------
    ValueError
        If two distinct names would collapse to the same sanitized name.
    """
    name_map: dict[str, str] = {}
    final: dict[str, str] = {}
    seen: set[str] = set()
    for source in (ds.dims, ds.coords, ds.data_vars):
        for name in source:
            if name in seen:
                continue
            seen.add(name)
            safe = (
                name.replace("/", "_")
                if isinstance(name, str) and "/" in name
                else name
            )
            previous = final.get(safe)
            if previous is not None and previous != name:
                raise ValueError(
                    f"NetCDF name sanitization would map both '{previous}' and "
                    f"'{name}' to '{safe}'. Rename one before writing."
                )
            final[safe] = name
            if safe != name:
                name_map[name] = safe

    if not name_map:
        return ds

    renamed = ds.rename(name_map)
    for original, safe in name_map.items():
        if safe in renamed.data_vars or safe in renamed.coords:
            renamed[safe].attrs.setdefault("original_name", str(original))
    return renamed


def _clean_attrs(ds: xr.Dataset) -> xr.Dataset:
    """Return a shallow copy of *ds* with netCDF-safe attributes and encoding.

    Cleans global and per-variable attributes with :func:`_clean_attr_dict`
    (``None``-valued attributes are dropped with a warning), and moves
    ``units``/``calendar`` from attributes to encoding on datetime variables (so a
    decoded time coordinate that still carries a ``units`` attribute does not
    collide with xarray's time encoding at write time).

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset to clean.

    Returns
    -------
    xarray.Dataset
        Shallow copy with cleaned attributes; the input is not modified.
    """

    # stacklevel to the user's write() call: warn -> _clean_attr_dict ->
    # (clean_variable ->) _clean_attrs -> _prepare -> write -> caller. The
    # per-variable path is one frame deeper than the global path.
    def clean_variable(name: str, var: xr.DataArray) -> tuple[dict, dict]:
        attrs = _clean_attr_dict(var.attrs, f"variable {name!r}", stacklevel=6)
        encoding = dict(var.encoding)
        if _is_datetime(var):
            for attr in _DATETIME_ENCODING_ATTRS:
                if attr in attrs:
                    encoding[attr] = attrs.pop(attr)
        return attrs, encoding

    safe = ds.copy(deep=False)
    safe.attrs = _clean_attr_dict(ds.attrs, "global", stacklevel=5)
    for name, var in ds.variables.items():
        attrs, encoding = clean_variable(name, var)
        safe[name].attrs = attrs
        safe[name].encoding = encoding
    return safe


def _is_datetime(var: xr.Variable | xr.DataArray) -> bool:
    """Return ``True`` if *var* holds numpy ``datetime64`` values.

    Parameters
    ----------
    var : xarray.Variable or xarray.DataArray
        The variable to test.

    Returns
    -------
    bool
        Whether *var* is a numpy ``datetime64`` variable.
    """
    return np.issubdtype(var.dtype, np.datetime64)


def _has_cf_time_encoding(var: xr.Variable | xr.DataArray) -> bool:
    """Return ``True`` for an object-dtype variable carrying CF time encoding.

    A dataset decoded with ``use_cftime=True`` holds its time coordinate as ``object``
    dtype (cftime objects) with ``units``/``calendar`` in encoding rather than numpy
    ``datetime64``.  Such a variable must keep that encoding so its (possibly
    non-standard) calendar and epoch are preserved on write rather than re-guessed by
    xarray from the data, which can change the stored reference.

    Parameters
    ----------
    var : xarray.Variable or xarray.DataArray
        The variable to test.

    Returns
    -------
    bool
        Whether *var* is object dtype with ``units`` or ``calendar`` in encoding.
    """
    return var.dtype.kind == "O" and (
        "units" in var.encoding or "calendar" in var.encoding
    )


def _prepare(ds: xr.Dataset, *, compress: bool = True) -> xr.Dataset:
    """Return a copy of *ds* ready to write: cast, cleaned, per-variable encoding set.

    Casts dtypes, cleans attributes and names, and sets per-variable encoding —
    datetime variables pinned to :data:`_TIME_ENCODING`, others filtered to
    :data:`_PRESERVED_ENCODING` — then applies :data:`_COMPRESSION` to every
    dimensioned numeric variable unless *compress* is ``False``.  A caller passes
    ``compress=False`` to produce an otherwise-identical uncompressed file, so a
    compressed write can be diffed against it and show only the zlib filter.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset to prepare.
    compress : bool, default True
        Apply the zlib + shuffle filter to dimensioned numeric variables.

    Returns
    -------
    xarray.Dataset
        Prepared copy; the input is not modified.
    """
    safe = _clean_attrs(_sanitize_names(cast_output_dtypes(ds)))
    for name, var in safe.variables.items():
        if _is_datetime(var):
            # Pin datetime variables; do not inherit xarray's per-file guess.
            enc = dict(_TIME_ENCODING)
        elif _has_cf_time_encoding(safe[name]):
            # Object-dtype cftime time: keep its CF time encoding so a non-standard
            # calendar and epoch survive rather than being re-guessed by xarray.
            keep = (*_PRESERVED_ENCODING, "units", "calendar")
            enc = {k: v for k, v in safe[name].encoding.items() if k in keep}
        else:
            enc = {
                k: v for k, v in safe[name].encoding.items() if k in _PRESERVED_ENCODING
            }
        if compress and var.ndim >= 1 and var.dtype.kind not in _UNCOMPRESSIBLE_KINDS:
            enc.update(_COMPRESSION)
        safe[name].encoding = enc
    return safe


def as_stored(ds: xr.Dataset) -> xr.Dataset:
    """Return *ds* holding the values its file will hold, so it equals the file read back.

    Casts to the storage dtypes (:func:`~oceanarray.utilities.cast_output_dtypes`:
    float64 to float32 but for time and position) and rounds every datetime to what
    :data:`_TIME_ENCODING` (float64 seconds) can represent, by encoding and decoding
    it with xarray's own time coder. A stage applies it to its output, so a dataset
    passed on in memory and the same dataset written and read back agree exactly.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset a stage is about to return.

    Returns
    -------
    xarray.Dataset
        A copy with storage dtypes and stored time values; attributes kept.
    """
    out = cast_output_dtypes(ds)
    coder = CFDatetimeCoder()
    for name in list(out.variables):
        var = out[name].variable
        if not _is_datetime(var):
            continue
        tmp = var.copy(deep=False)
        tmp.encoding = dict(_TIME_ENCODING)
        stored = np.asarray(coder.decode(coder.encode(tmp, name), name).values)
        new = xr.Variable(var.dims, stored, attrs=var.attrs)
        if name in out.coords:
            out = out.assign_coords({name: new})
        else:
            out[name] = new
    return out


def write(ds: xr.Dataset, path: Path | str, *, overwrite: bool = False) -> Path:
    """Write *ds* to *path* as compressed CF-NetCDF, one seam for every stage.

    Casts variables to their storage dtypes, sanitizes names and attributes to
    match seasenselib, applies zlib level 4 with the shuffle filter to every
    numeric variable that has at least one dimension (coordinates included), and
    writes atomically via a temporary file in the target directory.  Every
    ``datetime64`` variable is pinned to ``seconds since 1970-01-01T00:00:00``
    (float64); decoded instants are unchanged to sub-microsecond, raw stored values
    change.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset to write.
    path : pathlib.Path or str
        Output netCDF path.  The parent directory is created if absent.
    overwrite : bool, optional
        When False (the default), refuse to replace an existing *path*; the
        processing stages pass ``overwrite=True`` so re-running a stage replaces
        its output.

    Returns
    -------
    pathlib.Path
        The path written.

    Raises
    ------
    FileExistsError
        If *path* exists and *overwrite* is False.
    """
    path = Path(path)
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"{path} exists; pass overwrite=True to replace it.")
        # Re-running a stage is routine, but never silent: a replaced file is logged
        # so a re-run is visible in the log and the report's run summary.
        log_info("overwriting %s", path)
    path.parent.mkdir(parents=True, exist_ok=True)

    safe = _prepare(ds)

    # Atomic write: to_netcdf creates a sibling temp file, then rename over the
    # target.  The temp path is deterministic and let netCDF4 create it fresh —
    # pre-creating it (e.g. via NamedTemporaryFile) and clobbering it triggers an
    # HDF5 file-lock error on Windows.
    tmp = path.with_name(path.name + ".tmp")
    try:
        safe.to_netcdf(tmp, engine="netcdf4", format="NETCDF4")
        tmp.replace(path)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    return path
