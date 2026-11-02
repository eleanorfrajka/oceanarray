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

import re
import warnings
from pathlib import Path

import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.readers import nortek
from oceanarray.readers.netcdf import read_netcdf
from oceanarray.readers.nortek import load_nortek_csv
from oceanarray.readers.oceansites import read_oceansites
from oceanarray.readers.rodb import read_rodb

__all__ = [
    "load_nortek_csv",
    "read",
    "read_netcdf",
    "read_oceansites",
    "read_rodb",
]

#: OceanSITES parameter codes used to recognise an OceanSITES netCDF by content.
_OCEANSITES_CODES = frozenset({"TEMP", "PSAL", "CNDC", "PRES", "UCUR", "VCUR", "WCUR"})


def _read_typed(
    file_type: str, file_path: str, header_path: str | None = None
) -> xr.Dataset:
    """Read a raw instrument file of a known *file_type* and return a Dataset.

    Deprecated aliases in ``parameters.DEPRECATED_FILE_TYPE_ALIASES`` (e.g.
    ``rbr-hex-oa`` → ``rbr-hex``) are remapped to their current equivalent
    first, with a deprecation warning. ``nortek-csv-oa`` then routes to the
    deprecated internal reader (also with a warning); all other types go
    straight to ``seasenselib.read``.
    For Nortek formats the coordinate system is parsed from *header_path* and
    ``nortek.to_canonical_nortek`` is called.  For ``sbe-hex``, the
    seasenselib output variable ``press`` is renamed to ``pressure``.

    Parameters
    ----------
    file_type : str
        One of ``parameters.ALL_FILE_TYPES`` (e.g. ``"nortek-raw"``, ``"sbe-cnv"``).
    file_path : str
        Path to the raw instrument file.
    header_path : str or None, optional
        Path to the companion header file (required for Nortek formats to read
        the coordinate system and transformation matrix).

    Returns
    -------
    xarray.Dataset
        The normalised dataset.
    """
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
            stacklevel=4,
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
            stacklevel=4,
        )
        ds = load_nortek_csv(file_path, header_file=header_path)
        coord_system = ds.attrs.get("coordinate_system")
        if coord_system is None:
            print(
                f"WARNING: nortek-csv-oa file {file_path} has no coordinate_system "
                "attribute — coordinate system unknown (UNK); velocities not renamed."
            )
            coord_system = "UNK"
        return nortek.to_canonical_nortek(ds, coord_system=coord_system)

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
            print(
                f"WARNING: no header file for {file_path} — "
                "coordinate system unknown (UNK); velocities not renamed."
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
    """Return the file_type of a text instrument file from its content, or None.

    Recognises SBE (``sbe-cnv`` when the CNV ``# nquan`` marker is present, else
    ``sbe-ascii``), RODB (``Key = value`` header with a ``Columns`` line), and
    Nortek (an ASCII data file with a sibling ``.hdr`` that looks like a Nortek
    header). The suffix is never consulted.
    """
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
    ``.hdr`` beside the data file supplies the coordinate system.

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
        If *file_type* is not a known type, or no *file_type* is given and the
        content is not one the sniffer recognises.
    """
    path = Path(path)

    if _is_netcdf(path):
        if file_type not in (None, "oceansites"):
            return _read_typed(file_type, str(path))
        # Open once.  An OceanSITES file (a TIME dimension and an OceanSITES code)
        # goes to read_oceansites, which re-opens with its own decode; our own
        # netCDF is already canonical, so the open dataset is the result and
        # read_netcdf is not re-opened.
        src = xr.open_dataset(path, engine="netcdf4")
        is_oceansites = "TIME" in src.dims and bool(
            _OCEANSITES_CODES & set(src.data_vars)
        )
        if file_type == "oceansites" or is_oceansites:
            src.close()
            return read_oceansites(path)
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
