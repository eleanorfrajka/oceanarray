"""Filesystem path and filename conventions for the oceanarray pipeline.

Single source of truth for folder resolution, the stage output-filename
pattern, and turning instrument serial numbers into filename-safe tokens.
Every processing stage derives its read/write locations from here so the
logic is defined once rather than copied per stage.
"""

import re
import sys
from pathlib import Path
from typing import Any

_PathLike = str | Path

#: Base URL of the hosted documentation, used in user-facing upgrade messages.
DOCS_URL = "https://ocean-uhh.github.io/oceanarray/"


class LegacyLayoutError(RuntimeError):
    """Raised when a directory uses the removed ``moor/proc`` (``basedir``) layout."""


def mooring_proc_dir(proc_root: _PathLike, mooring: str) -> Path:
    """Return the mooring-level processed-data directory.

    Under the current layout this is ``proc_root/<mooring>``.

    Parameters
    ----------
    proc_root : str or Path
        Cruise-level processed-data root (the ``--proc-dir`` value).
    mooring : str
        Mooring name.

    Returns
    -------
    Path
        ``proc_root/<mooring>``.

    """
    return Path(proc_root) / mooring


def resolve_report_dir(
    mooring: str,
    outdir: _PathLike | None,
    report_dir: _PathLike | None,
    proc_root: _PathLike | None = None,
    *,
    mooring_dir: _PathLike | None = None,
) -> Path:
    """Return the directory a mooring's HTML report pages are written to.

    Single source of truth for report output-dir resolution, mirrored by both
    :meth:`oceanarray.reports.MooringReport.generate` and the PDF combiner so the
    two never drift.  Priority: explicit *outdir* wins; otherwise a central
    *report_dir* nests each mooring under ``report_dir/<mooring>``; otherwise the
    default ``<mooring_dir>/report`` (``proc_root/<mooring>/report`` when
    *mooring_dir* is not given).

    Parameters
    ----------
    mooring : str
        Mooring name.
    outdir : str or Path, optional
        Explicit output directory (``--output-dir``); takes precedence when set.
    report_dir : str or Path, optional
        Central report root (``--report-dir``); each mooring nests below it.
    proc_root : str or Path, optional
        Cruise-level processed-data root, used for the default location when
        *mooring_dir* is not given.
    mooring_dir : str or Path, optional
        The mooring's own folder (wherever its description is); the default
        location is its ``report`` subfolder, whatever the folder is called.

    Returns
    -------
    Path
        The resolved report directory.

    """
    if outdir:
        return Path(outdir)
    if report_dir:
        return Path(report_dir) / mooring
    if mooring_dir is not None:
        return Path(mooring_dir) / "report"
    return mooring_proc_dir(proc_root, mooring) / "report"


def resolve_pdf_path(
    mooring: str,
    pdf_dir: _PathLike | None,
    report_html_dir: _PathLike,
) -> Path:
    """Return the path a mooring's combined report PDF is written to.

    Single source of truth for PDF output-path resolution, mirrored by both
    ``cmd_report``'s dry-run preview and its real PDF branch so the two never
    drift.  When *pdf_dir* is set, every mooring's PDF collects in that one
    shareable directory as ``pdf_dir/<mooring>_report.pdf``; when it is ``None``
    the PDF stays beside the HTML pages at
    ``report_html_dir/<mooring>_report.pdf``.

    This function is pure: it does not create any directory (so the dry-run
    preview stays side-effect-free).  The caller creates the parent before
    writing.

    Parameters
    ----------
    mooring : str
        Mooring name, used as the filename stem.
    pdf_dir : str or Path, optional
        Central directory collecting every mooring's PDF (``--pdf-dir``); when
        set it takes precedence over the beside-the-HTML default.
    report_html_dir : str or Path
        The mooring's resolved HTML report directory (from
        :func:`resolve_report_dir`); the default PDF location when *pdf_dir* is
        unset.

    Returns
    -------
    Path
        The resolved ``<mooring>_report.pdf`` path.

    """
    filename = f"{mooring}_report.pdf"
    if pdf_dir:
        return Path(pdf_dir) / filename
    return Path(report_html_dir) / filename


def raw_mooring_dir(raw_root: _PathLike, mooring: str) -> Path:
    """Return the mooring-level raw-data directory.

    Under the current layout this is ``raw_root/<mooring>``.

    Parameters
    ----------
    raw_root : str or Path
        Cruise-level raw-data root (the ``--raw-dir`` value).
    mooring : str
        Mooring name.

    Returns
    -------
    Path
        ``raw_root/<mooring>``.

    """
    return Path(raw_root) / mooring


def stage_suffix(stage: int) -> str:
    """Return the filename ending of a stage file, ``"_stage<N>.nc"``.

    Parameters
    ----------
    stage : int
        Processing stage number (1, 2, or 3).

    Returns
    -------
    str
        The ending, e.g. ``"_stage2.nc"``.
    """
    return f"_stage{int(stage)}.nc"


def stage_glob(stage: int) -> str:
    """Return the glob pattern matching every stage-*N* file, ``"*_stage<N>.nc"``.

    For discovery in a directory tree where no mooring description is at hand.

    Parameters
    ----------
    stage : int
        Processing stage number (1, 2, or 3).

    Returns
    -------
    str
        The pattern, e.g. ``"*_stage3.nc"``.
    """
    return f"*{stage_suffix(stage)}"


def stage_output_name(mooring: str, serial: Any, stage: int, tag: str = "") -> str:
    """Return the stage output filename for one instrument.

    The pattern is ``{mooring}_{serial}{tag}_stage{N}.nc``.  The serial is
    cleaned with :func:`safe_serial`, so the raw YAML value may be passed
    directly.

    Parameters
    ----------
    mooring : str
        Mooring name.
    serial : Any
        Raw instrument serial (cleaned internally via :func:`safe_serial`).
    stage : int
        Processing stage number (1, 2, or 3).
    tag : str, optional
        Extra filename tag (e.g. the ADCP-matlab file tag). Default ``""``.

    Returns
    -------
    str
        The output filename, e.g. ``"dsG3_16430_stage1.nc"``.

    """
    return f"{mooring}_{safe_serial(serial)}{tag}{stage_suffix(stage)}"


#: The multi-instrument products a mooring directory holds, each named
#: ``{name}_{product}.nc`` at the top of the mooring directory.
PRODUCTS: frozenset[str] = frozenset({"stack", "grid"})


def stage_file(
    mooring_dir: _PathLike,
    name: str,
    instrument: str,
    serial: Any,
    stage: int,
    *,
    tag: str = "",
) -> Path:
    """Return the full path of one instrument's stage file.

    ``<mooring_dir>/<instrument>/<name>_<serial>{tag}_stage<N>.nc`` — the mooring's
    identity is *name* (every file is named from it), its location is *mooring_dir*,
    and the instrument type is the subdirectory. Thin wrapper over
    :func:`stage_output_name`.

    Parameters
    ----------
    mooring_dir : str or pathlib.Path
        The mooring's own directory, holding the per-instrument subdirectories.
    name : str
        The mooring's ``name`` (its identity).
    instrument : str
        The instrument type (the subdirectory name, e.g. ``"microcat"``).
    serial : Any
        Raw instrument serial (cleaned internally via :func:`safe_serial`).
    stage : int
        Processing stage number (1, 2, or 3).
    tag : str, optional
        Extra filename tag before ``_stage``. Used only for ``adcp-matlab`` files,
        where it carries the per-file index (``_000``, ``_001``, …) from
        ``stage1._find_file_tag``; ``""`` for every other instrument.

    Returns
    -------
    pathlib.Path
        The stage file path.
    """
    return Path(mooring_dir) / instrument / stage_output_name(name, serial, stage, tag)


def product_glob(product: str) -> str:
    """Return the glob pattern matching every *product* file, ``"*_<product>.nc"``.

    Parameters
    ----------
    product : str
        The product, one of :data:`PRODUCTS`.

    Returns
    -------
    str
        The pattern, e.g. ``"*_grid.nc"``.
    """
    if product not in PRODUCTS:
        raise ValueError(
            f"unknown product {product!r}; expected one of {sorted(PRODUCTS)}."
        )
    return f"*_{product}.nc"


def stale_file(path: _PathLike) -> Path:
    """Return where a product that no longer matches its stage files is kept.

    ``<name>_stack.nc`` becomes ``<name>_stack.stale.nc``; the stack and grid
    search patterns (:func:`product_glob`) do not match it.

    Parameters
    ----------
    path : str or Path
        A product file.

    Returns
    -------
    Path
        The stale copy's path, beside the product.
    """
    path = Path(path)
    return path.with_suffix(".stale" + path.suffix)


def product_file(mooring_dir: _PathLike, name: str, product: str) -> Path:
    """Return the full path of a mooring-level product file.

    ``<mooring_dir>/<name>_<product>.nc`` for *product* in :data:`PRODUCTS`
    (``"stack"`` or ``"grid"``).

    Parameters
    ----------
    mooring_dir : str or pathlib.Path
        The mooring's own directory.
    name : str
        The mooring's ``name`` (its identity).
    product : str
        The product, one of :data:`PRODUCTS`.

    Returns
    -------
    pathlib.Path
        The product file path.

    Raises
    ------
    ValueError
        If *product* is not in :data:`PRODUCTS`.
    """
    if product not in PRODUCTS:
        raise ValueError(
            f"unknown product {product!r}; expected one of {sorted(PRODUCTS)}."
        )
    return Path(mooring_dir) / f"{name}_{product}.nc"


def require_current_layout(proc_root: _PathLike, mooring: str) -> None:
    """Raise if *proc_root* points at a removed legacy layout, naming the fix.

    Under the current layout the mooring directory is ``proc_root/<mooring>``.
    The removed ``basedir`` mode nested it one level down — under either
    ``proc_root/moor/proc/<mooring>`` or ``proc_root/proc/<mooring>`` (both
    shapes the old ``cli._get_proc_root`` accepted).  When the current-layout
    directory is absent but a legacy one is present, raise an error that names
    the directory to use instead, rather than letting processing fail later with
    "no files found".  If neither exists this returns normally — a genuinely
    empty run is not this function's concern.

    Parameters
    ----------
    proc_root : str or Path
        Cruise-level processed-data root (the ``--proc-dir`` value).
    mooring : str
        Mooring name.

    Raises
    ------
    LegacyLayoutError
        If a legacy ``<proc_root>/moor/proc/<mooring>`` or
        ``<proc_root>/proc/<mooring>`` directory exists but the current
        ``<proc_root>/<mooring>`` directory does not.

    """
    proc_root = Path(proc_root)
    legacy_roots = (proc_root / "moor" / "proc", proc_root / "proc")
    if (proc_root / mooring).is_dir():
        # Current layout is present.  Warn (do not fail) if a stale legacy copy
        # also exists — a half-finished migration leaves two competing trees and
        # the legacy one is silently ignored.
        for legacy_root in legacy_roots:
            if (legacy_root / mooring).is_dir():
                print(
                    f"WARNING: both the current '{proc_root / mooring}' and a "
                    f"legacy '{legacy_root / mooring}' directory exist; the legacy "
                    f"copy is ignored. Remove it to avoid confusion.",
                    file=sys.stderr,
                )
                break
        return
    for legacy_root in legacy_roots:
        if (legacy_root / mooring).is_dir():
            raise LegacyLayoutError(
                f"--proc-dir points at an old-layout directory (found "
                f"'{legacy_root / mooring}'). Use --proc-dir {legacy_root} "
                "instead (see the migration guide: docs/source/migration.rst "
                f"or {DOCS_URL}migration.html)."
            )


def is_path_component(name: Any) -> bool:
    """Return whether a name is one folder or file name, with no way out of its parent.

    Names taken from a description (a cruise, a cast) are joined onto a root; a name that is
    absolute, empty, ``.`` or ``..``, or that holds a path separator or a Windows drive colon,
    would point somewhere else.

    Parameters
    ----------
    name : Any
        The name to check.

    Returns
    -------
    bool
        ``True`` when ``name`` is non-empty text naming one entry beneath a folder.

    Examples
    --------
    >>> is_path_component("msm142_2026"), is_path_component("../other")
    (True, False)

    """
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and not any(sep in name for sep in ("/", "\\", ":"))
    )


def safe_serial(serial: Any) -> str:
    """Return a filename-safe token for an instrument serial number.

    If the raw value contains a comma (e.g. ``"16430, R01-024"``), only the
    first comma-separated token is the primary serial used in filenames and
    output; the remainder is a beacon id or annotation and is dropped.  Any
    remaining characters that are illegal in filenames (e.g. ``*`` used as a
    YAML marker) are stripped.

    Parameters
    ----------
    serial : Any
        Raw serial value from the mooring YAML (``str``, ``int``, or other).

    Returns
    -------
    str
        Sanitised serial token containing only word characters and hyphens.

    """
    primary = str(serial).split(",")[0]
    return re.sub(r"[^\w\-]", "", primary)


def should_skip_regeneration(
    output: Path,
    force: bool,
    skip_existing: bool,
    *sources: Path,
) -> bool:
    """Decide whether regenerating *output* can be skipped.

    Three-mode logic (matching the report layer / ctd_report CLI design):

    - ``force=True``         → never skip (always regenerate).
    - ``skip_existing=True`` → skip whenever *output* exists, regardless of
      source modification times (the fast, mtime-agnostic behaviour).
    - default                → skip only if *output* exists and is newer than
      every path in *sources* (mtime-based staleness).  A source newer than
      *output* — for example an edited mooring YAML — forces regeneration.

    Parameters
    ----------
    output : Path
        The file that would be (re)generated.
    force : bool
        If True, never skip.
    skip_existing : bool
        If True, skip whenever *output* exists regardless of source mtimes.
    *sources : Path
        Input files *output* is derived from (raw data, previous-stage NetCDF,
        the mooring YAML).  Non-existent sources are ignored.

    Returns
    -------
    bool
        True if regeneration can be skipped; False if *output* must be rebuilt.

    """
    if force:
        return False
    if not output.exists():
        return False
    if skip_existing:
        return True
    out_mtime = output.stat().st_mtime
    return not any(s.exists() and s.stat().st_mtime > out_mtime for s in sources)
