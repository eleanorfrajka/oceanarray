"""Read one raw instrument file to a canonical dataset.

:func:`read` is the single entry point: given a path it sniffs the file's
content (never its suffix) to decide the format, finds the Nortek ``.hdr``
beside a data file, and dispatches to the per-format module. An oceanarray
netCDF is returned unchanged; an OceanSITES netCDF is read to oceanarray names.
The format can be forced with ``file_type=`` when the content is not one the
sniffer recognises.

One module per format: :mod:`~oceanarray.readers.sbe`,
:mod:`~oceanarray.readers.nortek`, :mod:`~oceanarray.readers.rodb`,
:mod:`~oceanarray.readers.oceansites`, :mod:`~oceanarray.readers.netcdf`.
"""

import logging
import re
import warnings
from pathlib import Path

import numpy as np
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.readers import nortek
from oceanarray.readers.netcdf import read_netcdf
from oceanarray.readers.nortek import load_nortek_csv
from oceanarray.readers.oceansites import read_oceansites
from oceanarray.readers.rodb import read_rodb, read_rodb_bins

log = logging.getLogger(__name__)

__all__ = [
    "load_nortek_csv",
    "read",
    "read_netcdf",
    "read_oceansites",
    "read_rodb",
    "read_rodb_bins",
]

#: OceanSITES parameter codes used to recognise an OceanSITES netCDF by content.
_OCEANSITES_CODES = frozenset({"TEMP", "PSAL", "CNDC", "PRES", "UCUR", "VCUR", "WCUR"})


#: Global attribute recording how many records the reader dropped for having no time.
N_RECORDS_WITHOUT_TIME_ATTR = "n_records_without_time"


def _drop_missing_time(dataset: xr.Dataset, source: str) -> xr.Dataset:
    """Drop records whose ``time`` coordinate is ``NaT``, warning with the count.

    Some originators pad a record that ended early with missing timestamps, and a
    fill in a time column leaves a record with no time.  Left in, the ``NaT``
    entries leave the ``time`` index non-monotonic, which later breaks the
    deployment-window ``sel(time=slice(...))`` in stage 2, so they are removed
    here, for every format, and the count is recorded in the global attribute
    :data:`N_RECORDS_WITHOUT_TIME_ATTR`.

    Parameters
    ----------
    dataset : xarray.Dataset
        A dataset on a ``time`` dimension.
    source : str
        File name, named in the warning so the operator sees which file was padded.

    Returns
    -------
    xarray.Dataset
        The input unchanged when every timestamp is valid (or the dataset has no
        datetime ``time`` coordinate), otherwise the subset of records with a valid
        timestamp, carrying :data:`N_RECORDS_WITHOUT_TIME_ATTR`.

    Raises
    ------
    ValueError
        If no record has a valid timestamp.
    """
    if "time" not in dataset.coords:
        return dataset
    time = dataset["time"].values
    if not np.issubdtype(time.dtype, np.datetime64):
        # A non-standard calendar decodes to cftime objects (object dtype), which
        # np.isnat cannot take and which cannot hold NaT; nothing to drop.
        return dataset
    if not time.size:
        raise ValueError(f"{source}: no record has a timestamp (file has no records).")
    missing = np.isnat(time)
    n = int(missing.sum())
    if not n:
        return dataset
    if n == missing.size:
        raise ValueError(f"{source}: no record has a timestamp (all {n} missing).")
    log.warning("%s: dropped %d record(s) with no timestamp (missing time).", source, n)
    out = dataset.isel(time=np.where(~missing)[0])
    out.attrs[N_RECORDS_WITHOUT_TIME_ATTR] = n
    return out


def _read_typed(
    file_type: str, file_path: str, header_path: str | None = None
) -> xr.Dataset:
    """Read a raw instrument file of a known *file_type*, dropping records with no time."""
    ds = _dispatch(file_type, file_path, header_path)
    return _drop_missing_time(ds, Path(file_path).name)


def _dispatch(
    file_type: str, file_path: str, header_path: str | None = None
) -> xr.Dataset:
    """Read a raw instrument file of a known *file_type* with its format's reader."""
    # Remap deprecated file_type aliases to their current equivalent before
    # the membership check — aliases are intentionally excluded from
    # ALL_FILE_TYPES so they no longer appear in any public list.
    if file_type in params.DEPRECATED_FILE_TYPE_ALIASES:
        canonical = params.DEPRECATED_FILE_TYPE_ALIASES[file_type]
        warnings.warn(
            f"file_type '{file_type}' is deprecated and remapped to "
            f"'{canonical}' automatically; update your mooring YAML to "
            f"'{canonical}'.",
            DeprecationWarning,
            stacklevel=5,
        )
        file_type = canonical

    if file_type not in params.ALL_FILE_TYPES:
        msg = f"Unknown file type: {file_type}"
        raise ValueError(msg)

    if file_type in ("oceansites", "rodb"):
        reader = read_oceansites if file_type == "oceansites" else read_rodb
        return reader(file_path)

    if file_type == "nortek-csv-oa":
        warnings.warn(
            "file_type 'nortek-csv-oa' is deprecated. "
            "Use 'nortek-csv' once seasenselib supports Nortek CSV export files.",
            DeprecationWarning,
            stacklevel=5,
        )
        ds = load_nortek_csv(file_path, header_file=header_path)
        coord_system = ds.attrs.get("coordinate_system")
        if coord_system is None:
            log.warning(
                "nortek-csv-oa file %s has no coordinate_system attribute — "
                "coordinate system unknown (UNK); velocities not renamed.",
                file_path,
            )
            coord_system = "UNK"
        return nortek.to_canonical_nortek(ds, coord_system=coord_system)

    # Nortek formats need the companion header for the coordinate system and
    # transformation matrix.
    kwargs = {}
    if file_type in ("nortek-raw", "nortek-ascii") and header_path:
        kwargs["header_file"] = header_path

    # Imported lazily: the netCDF / OceanSITES / RODB readers do not need
    # seasenselib, so importing oceanarray.readers must not pull it (and its
    # ~0.4 s / pycnv side effects) in for those consumers.
    import seasenselib

    ds = seasenselib.read(
        file_path,
        file_format=file_type,
        pipeline_skip_stages=["derivation"],
        **kwargs,
    )
    if file_type in ("nortek-ascii", "nortek-raw"):
        if header_path:
            coord_system = nortek._parse_nortek_coord_system(Path(header_path))
        else:
            log.warning(
                "no header file for %s — coordinate system unknown (UNK); "
                "velocities not renamed.",
                file_path,
            )
            coord_system = "UNK"
        ds = nortek.to_canonical_nortek(ds, coord_system=coord_system)
    if (
        file_type == "sbe-hex"
        and "press" in ds.data_vars
        and "pressure" not in ds.data_vars
    ):
        ds = ds.rename_vars({"press": "pressure"})
    return ds


def _is_netcdf(path: Path) -> bool:
    """Return True if *path* begins with a classic-netCDF or HDF5 magic number."""
    try:
        with path.open("rb") as fh:
            magic = fh.read(4)
    except OSError:
        return False
    return magic[:3] == b"CDF" or magic == b"\x89HDF"


def _is_nortek_header(hdr_path: Path) -> bool:
    """Return True if *hdr_path* looks like a Nortek .hdr header file."""
    try:
        head = hdr_path.read_text(errors="replace")[:2048]
    except OSError:
        return False
    return "Number of measurements" in head or bool(
        re.search(r"\.aqd\]", head, re.IGNORECASE)
    )


def _sniff_text(path: Path) -> str | None:
    """Return the file_type of a text instrument file from its content, or None."""
    # Content only: the suffix is never consulted.
    try:
        head = path.read_text(errors="replace")[:4096]
    except OSError:
        return None
    lines = [ln for ln in head.splitlines() if ln.strip()]
    if not lines:
        return None
    first = lines[0].lstrip()

    # SBE: header lines begin with "* Sea-Bird" / "* SBE".
    if re.match(r"\*\s*(Sea-Bird|SBE)", first, re.IGNORECASE):
        return (
            "sbe-cnv" if re.search(r"^#\s*nquan", head, re.MULTILINE) else "sbe-ascii"
        )

    # RODB: "Key = value" header lines, one of which is "Columns = ...".
    if re.match(r"^\w[\w ]*=", first) and re.search(
        r"^\s*Columns\s*=", head, re.MULTILINE
    ):
        return "rodb"

    # Nortek: an ASCII data file with a sibling .hdr that is a Nortek header.
    hdr = path.with_suffix(".hdr")
    if hdr.exists() and _is_nortek_header(hdr):
        return "nortek-ascii"

    return None


def read(path: str | Path, *, file_type: str | None = None) -> xr.Dataset:
    """Read one raw instrument file and return a canonical dataset.

    The format is taken from *file_type* when given, otherwise sniffed from the
    file's content (the suffix is never used). A netCDF is read as an OceanSITES
    time series when it carries a ``TIME`` dimension and an OceanSITES parameter
    code, and returned unchanged otherwise. For Nortek data the companion
    ``.hdr`` beside the data file supplies the coordinate system. Records with no
    time (``NaT``) are dropped from any raw file with a warning, and the count is
    recorded in the ``n_records_without_time`` global attribute.

    Parameters
    ----------
    path : str or pathlib.Path
        The raw instrument file.
    file_type : str or None, optional
        Force a format from ``parameters.ALL_FILE_TYPES`` instead of sniffing.

    Returns
    -------
    xarray.Dataset
        The canonical dataset.

    Raises
    ------
    ValueError
        If *file_type* is not a known type, no *file_type* is given and the
        content is not one the sniffer recognises, or no record has a time.
    """
    path = Path(path)

    if _is_netcdf(path):
        if file_type not in (None, "oceansites"):
            return _read_typed(file_type, str(path))
        # Open once.  An OceanSITES file (a TIME dimension and an OceanSITES code)
        # goes to read_oceansites (through the missing-time guard), which re-opens
        # with its own decode; our own netCDF is already canonical, so the open
        # dataset is the result and read_netcdf is not re-opened.
        src = xr.open_dataset(path, engine="netcdf4")
        is_oceansites = "TIME" in src.dims and bool(
            _OCEANSITES_CODES & set(src.data_vars)
        )
        if file_type == "oceansites" or is_oceansites:
            src.close()
            return _read_typed("oceansites", str(path))
        return src

    if file_type is None:
        file_type = _sniff_text(path)
        if file_type is None:
            raise ValueError(
                f"{path.name}: cannot determine the file type from its content; "
                f"pass file_type= (one of {sorted(params.ALL_FILE_TYPES)})."
            )

    header_path = None
    if file_type in ("nortek-raw", "nortek-ascii"):
        hdr = path.with_suffix(".hdr")
        if hdr.exists():
            header_path = str(hdr)

    return _read_typed(file_type, str(path), header_path)
