"""Filesystem path and filename conventions for the oceanarray pipeline.

Single source of truth for folder resolution, the stage output-filename
pattern, and turning instrument serial numbers into filename-safe tokens.
Every processing stage derives its read/write locations from here so the
logic is defined once rather than copied per stage.
"""

import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_PathLike = str | Path

#: Base URL of the hosted documentation, used in user-facing upgrade messages.
DOCS_URL = "https://ocean-uhh.github.io/oceanarray/"


class LegacyLayoutError(RuntimeError):
    """Raised when a directory uses the removed ``moor/proc`` (``basedir``) layout."""


def mooring_proc_dir(proc_root: _PathLike, mooring: str) -> Path:
    """Return the mooring's processed-data directory, ``proc_root/<mooring>``.

    Parameters
    ----------
    proc_root : str or Path
        Cruise-level processed-data root (``--proc-dir``).
    mooring : str
        Mooring name.

    Returns
    -------
    Path
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
    """Return the directory a mooring's report pages are written to (shared by the pages and the PDF).

    *outdir* wins; else ``report_dir/<mooring>``; else ``<mooring_dir>/report``
    (``proc_root/<mooring>/report`` without *mooring_dir*).

    Parameters
    ----------
    mooring : str
        Mooring name.
    outdir : str or Path, optional
        Explicit output directory (``--output-dir``).
    report_dir : str or Path, optional
        Central report root (``--report-dir``).
    proc_root : str or Path, optional
        Processed-data root, for the default without *mooring_dir*.
    mooring_dir : str or Path, optional
        The mooring's own folder.

    Returns
    -------
    Path
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
    """Return the path of a mooring's combined report PDF; creates nothing.

    Parameters
    ----------
    mooring : str
        Mooring name (the filename stem).
    pdf_dir : str or Path, optional
        Central PDF directory (``--pdf-dir``); default beside the pages.
    report_html_dir : str or Path
        The mooring's report directory (:func:`resolve_report_dir`).

    Returns
    -------
    Path
        ``<pdf_dir or report_html_dir>/<mooring>_report.pdf``.
    """
    filename = f"{mooring}_report.pdf"
    if pdf_dir:
        return Path(pdf_dir) / filename
    return Path(report_html_dir) / filename


def raw_mooring_dir(raw_root: _PathLike, mooring: str) -> Path:
    """Return the mooring's raw-data directory, ``raw_root/<mooring>``.

    Parameters
    ----------
    raw_root : str or Path
        Cruise-level raw-data root (``--raw-dir``).
    mooring : str
        Mooring name.

    Returns
    -------
    Path
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
    """Return an instrument's stage filename, ``{mooring}_{serial}{tag}_stage{N}.nc``.

    Parameters
    ----------
    mooring : str
        Mooring name.
    serial : Any
        Instrument serial as in the YAML (cleaned by :func:`safe_serial`).
    stage : int
        Stage (1, 2 or 3).
    tag : str, optional
        Extra tag before ``_stage`` (``adcp-matlab`` file index).

    Returns
    -------
    str
    """
    return f"{mooring}_{safe_serial(serial)}{tag}{stage_suffix(stage)}"


#: The multi-instrument products, each named ``{name}_{product}.nc``: a mooring's
#: stack and grid in its directory, a site's stack and grid (``stackcat``,
#: ``gridcat``) in the site's.
PRODUCTS: frozenset[str] = frozenset({"stack", "grid", "stackcat", "gridcat"})


def stage_file(
    mooring_dir: _PathLike,
    name: str,
    instrument: str,
    serial: Any,
    stage: int,
    *,
    tag: str = "",
) -> Path:
    """Return an instrument's stage file, ``<mooring_dir>/<instrument>/<name>_<serial>{tag}_stage<N>.nc``.

    Parameters
    ----------
    mooring_dir : str or pathlib.Path
        The mooring's directory.
    name : str
        The mooring's ``name``.
    instrument : str
        Instrument type (the subdirectory, e.g. ``"microcat"``).
    serial : Any
        Instrument serial as in the YAML.
    stage : int
        Stage (1, 2 or 3).
    tag : str, optional
        Extra tag before ``_stage`` (``adcp-matlab`` file index ``_000``, ...).

    Returns
    -------
    pathlib.Path
    """
    return Path(mooring_dir) / instrument / stage_output_name(name, serial, stage, tag)


def stale_file(path: _PathLike) -> Path:
    """Return where a product that no longer matches its stage files is kept.

    ``<name>_stack.nc`` becomes ``<name>_stack.stale.nc``; the stack and grid
    search patterns (``*_stack.nc``, ``*_grid.nc``) do not match it.

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
    """Return a mooring product's path, ``<mooring_dir>/<name>_<product>.nc``.

    Parameters
    ----------
    mooring_dir : str or pathlib.Path
        The mooring's directory.
    name : str
        The mooring's ``name``.
    product : str
        One of :data:`PRODUCTS`.

    Returns
    -------
    pathlib.Path

    Raises
    ------
    ValueError
        *product* is not in :data:`PRODUCTS`.
    """
    if product not in PRODUCTS:
        raise ValueError(
            f"unknown product {product!r}; expected one of {sorted(PRODUCTS)}."
        )
    return Path(mooring_dir) / f"{name}_{product}.nc"


def require_current_layout(proc_root: _PathLike, mooring: str) -> None:
    """Raise when only the removed ``basedir`` layout holds the mooring, naming the folder to use.

    Parameters
    ----------
    proc_root : str or Path
        Cruise-level processed-data root (``--proc-dir``).
    mooring : str
        Mooring name.

    Raises
    ------
    LegacyLayoutError
        ``<proc_root>/moor/proc/<mooring>`` or ``<proc_root>/proc/<mooring>`` exists but
        ``<proc_root>/<mooring>`` does not.
    """
    proc_root = Path(proc_root)
    legacy_roots = (proc_root / "moor" / "proc", proc_root / "proc")
    if (proc_root / mooring).is_dir():
        # Current layout is present.  Warn (do not fail) if a stale legacy copy
        # also exists — a half-finished migration leaves two competing trees and
        # the legacy one is silently ignored.
        for legacy_root in legacy_roots:
            if (legacy_root / mooring).is_dir():
                log.warning(
                    "both the current '%s' and a legacy '%s' directory exist; the "
                    "legacy copy is ignored. Remove it to avoid confusion.",
                    proc_root / mooring,
                    legacy_root / mooring,
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
    """Return whether *name* is one folder or file name that cannot leave its parent.

    Parameters
    ----------
    name : Any
        A name from a description (a cruise, a cast).

    Returns
    -------
    bool
        False for empty, absolute, ``.``, ``..``, or a name with a separator or drive colon.

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
    """Return a filename-safe serial: the first comma-separated token, word characters and hyphens only.

    Parameters
    ----------
    serial : Any
        Serial as in the YAML (``"16430, R01-024"`` gives ``"16430"``).

    Returns
    -------
    str
    """
    primary = str(serial).split(",")[0]
    return re.sub(r"[^\w\-]", "", primary)


def adcp_parent_serial(serial: Any) -> str:
    """Return the ADCP serial a stack level came from, without its ``_hd`` / ``_bNN`` suffix.

    A stack splits an ADCP into a head level (``<serial>_hd``) and one level per
    bin (``<serial>_b00``, ``_b01``, ...); any other serial is returned unchanged.

    Parameters
    ----------
    serial : Any
        A stack level's serial.

    Returns
    -------
    str
        The instrument's own serial.
    """
    return re.sub(r"_(hd|b\d+)$", "", str(serial))


def should_skip_regeneration(
    output: Path,
    force: bool,
    skip_existing: bool,
    *sources: Path,
) -> bool:
    """Return whether rebuilding *output* can be skipped.

    Never with *force*; whenever *output* exists with *skip_existing*; otherwise when it
    exists and is newer than every existing source.

    Parameters
    ----------
    output : Path
        The file that would be rebuilt.
    force : bool
        Never skip.
    skip_existing : bool
        Skip whenever *output* exists.
    *sources : Path
        Files *output* is made from; missing ones are ignored.

    Returns
    -------
    bool
    """
    if force:
        return False
    if not output.exists():
        return False
    if skip_existing:
        return True
    out_mtime = output.stat().st_mtime
    return not any(s.exists() and s.stat().st_mtime > out_mtime for s in sources)
