"""The report verb: build a mooring's HTML report (and PDF), an array index, or a recovery table."""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Iterator, Mapping
from os import PathLike
from pathlib import Path
from typing import Any, Literal

from oceanarray import paths
from oceanarray.exceptions import MooringError
from oceanarray.utilities import _status

Kind = Literal["mooring", "array", "recovery"]


def report(
    mooring: str | PathLike | Mapping[str, Any],
    *,
    kind: Kind = "mooring",
    mooring_dir: PathLike | None = None,
    proc_dir: PathLike | None = None,
    raw_dir: PathLike | None = None,
    out_dir: PathLike | None = None,
    report_dir: PathLike | None = None,
    instruments: bool = True,
    stack: bool = True,
    grid: bool = True,
    serials: Iterable[str] | None = None,
    sig_level: Iterable[float] | None = None,
    pdf: bool = False,
    pdf_dir: PathLike | None = None,
    force: bool = False,
    skip_existing: bool = False,
) -> Path:
    """Build a report and return the path of its main page.

    Parameters
    ----------
    mooring : str or os.PathLike or Mapping
        For ``kind="mooring"`` and ``"recovery"``: a ``*.mooring.yaml`` path, a
        directory holding exactly one, or a loaded description (then
        *mooring_dir* is required). For ``kind="array"``: an ``*.array.yaml``
        path.
    kind : {"mooring", "array", "recovery"}, optional
        ``"mooring"`` (default) builds the mooring's summary page and, unless
        switched off, its per-instrument, stack and grid pages; ``"array"`` builds
        the array index linking each mooring's report; ``"recovery"`` builds the
        cruise-report recovery table.
    mooring_dir : os.PathLike or None, optional
        The mooring's own directory, when *mooring* is a loaded description.
    proc_dir : os.PathLike or None, optional
        ``kind="array"`` only: the processed-data root holding the moorings;
        defaults to the array description's directory.
    raw_dir : os.PathLike or None, optional
        The cruise-level raw-data root, for the raw-file details on the
        instrument pages.
    out_dir : os.PathLike or None, optional
        Where this mooring's pages go; overrides *report_dir*.
    report_dir : os.PathLike or None, optional
        A central report root: each mooring's pages go to ``report_dir/<mooring>``
        (and the array index to *report_dir*). Default: ``<mooring_dir>/report``.
    instruments, stack, grid : bool, optional
        ``kind="mooring"``: also build these pages (default all three).
    serials : iterable of str or None, optional
        Build instrument pages for these serials only.
    sig_level : iterable of float or None, optional
        σ₀ targets for the grid page's isopycnal panels (default
        :data:`~oceanarray.config.parameters.SIGMA_GRID`).
    pdf : bool, optional
        ``kind="mooring"``: also combine the pages into one A4 PDF.
    pdf_dir : os.PathLike or None, optional
        Where the PDF goes; default beside the HTML.
    force : bool, optional
        Rebuild pages that are newer than their inputs.
    skip_existing : bool, optional
        Keep any page that exists, however old.

    Returns
    -------
    pathlib.Path
        The mooring summary page, the array index or the recovery table.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If the description is invalid or missing, a page cannot be built, or a
        requested PDF cannot be made (e.g. the ``pdf`` extra is not installed).
    ValueError
        If *kind* is not one of the three.

    Examples
    --------
    >>> import oceanarray as oa
    >>> oa.report("proc/dune2_1_2026/dune2_1_2026.mooring.yaml", pdf=True)
    >>> oa.report("proc/odb2026.array.yaml", kind="array")
    """
    if kind == "array":
        return _array_report(
            Path(mooring),  # type: ignore[arg-type]
            proc_dir=proc_dir,
            report_dir=report_dir,
            force=force,
        )
    if kind not in ("mooring", "recovery"):
        raise ValueError(f"kind={kind!r}: expected 'mooring', 'array' or 'recovery'")

    from oceanarray.pipeline import _resolve_mooring

    cfg, moor_dir = _resolve_mooring(mooring, mooring_dir, "report")
    name, proc_root = cfg["name"], moor_dir.parent
    yaml_path = _description_file(mooring, moor_dir, name)
    if kind == "recovery":
        return _recovery_table(
            name, moor_dir, yaml_path, out_dir, report_dir, force=force
        )

    from oceanarray.pipeline._run import _serial_list
    from oceanarray.reports import MooringReport

    serial_list = _serial_list(serials)
    with _sigma_grid(sig_level):
        _status("section", f"Report: {name}")
        page = MooringReport(
            proc_dir=str(proc_root),
            raw_dir=str(raw_dir) if raw_dir is not None else None,
            report_dir=str(report_dir) if report_dir is not None else None,
        ).generate(
            name,
            force=force,
            skip_existing=skip_existing,
            outdir=str(out_dir) if out_dir is not None else None,
            serials=serial_list,
            instruments=instruments or serial_list is not None,
            grid=grid,
            stack=stack,
            # A loaded description may differ from the file on disk, so it is
            # passed on (and rebuilds the summary); a path is read from disk.
            cfg=cfg if isinstance(mooring, Mapping) else None,
            mooring_dir=moor_dir,
            yaml_path=yaml_path,
        )
    if not page:
        raise MooringError(f"{name}: the report could not be built (see above).")
    if pdf:
        _combine_pdf(name, Path(page).parent, pdf_dir)
    return Path(page)


def report_paths(
    mooring: str | PathLike | Mapping[str, Any],
    *,
    mooring_dir: PathLike | None = None,
    out_dir: PathLike | None = None,
    report_dir: PathLike | None = None,
    pdf_dir: PathLike | None = None,
) -> dict[str, Path]:
    """Return where :func:`report` writes a mooring's pages, without building any.

    The description is only read for its ``name``, not checked, so this works
    on a half-filled one.

    Parameters
    ----------
    mooring : str or os.PathLike or Mapping
        As for :func:`report`.
    mooring_dir : os.PathLike or None, optional
        The mooring's own folder, when *mooring* is a loaded description.
    out_dir, report_dir, pdf_dir : os.PathLike or None, optional
        As for :func:`report`.

    Returns
    -------
    dict of str to pathlib.Path
        ``"summary"``, ``"stack"``, ``"grid"`` and ``"pdf"`` pages, and
        ``"instrument_dir"`` (where ``<name>_<serial>_report.html`` go).
    """
    name, moor_dir = _name_and_dir(mooring, mooring_dir)
    html = paths.resolve_report_dir(name, out_dir, report_dir, mooring_dir=moor_dir)
    return {
        "summary": html / f"{name}_report.html",
        "stack": html / f"{name}_stack_report.html",
        "grid": html / f"{name}_grid_report.html",
        "instrument_dir": html / "instrument",
        "pdf": paths.resolve_pdf_path(name, pdf_dir, html),
    }


def _name_and_dir(
    mooring: str | PathLike | Mapping[str, Any], mooring_dir: PathLike | None
) -> tuple[str, Path]:
    """Return the description's ``name`` and the mooring's folder, without checking it."""
    from oceanarray import mooring as _mooring

    if isinstance(mooring, Mapping):
        if mooring_dir is None:
            raise MooringError("a mooring dict needs mooring_dir= to locate its pages.")
        return str(mooring["name"]), Path(mooring_dir)
    path = Path(mooring)
    moor_dir = Path(mooring_dir) if mooring_dir is not None else None
    if path.is_dir():
        found = sorted(path.glob("*.mooring.yaml"))
        if len(found) != 1:
            raise MooringError(
                f"{path} holds {len(found)} '*.mooring.yaml' files; need exactly one."
            )
        path, moor_dir = found[0], moor_dir or path
    cfg = _mooring._parse(path)
    if not isinstance(cfg, Mapping) or not cfg.get("name"):
        raise MooringError(f"{path}: no 'name:' to name the report pages from.")
    return str(cfg["name"]), moor_dir or path.parent


def _description_file(
    mooring: str | PathLike | Mapping[str, Any], moor_dir: Path, name: str
) -> Path:
    """Return the description file: the path given, the one in the folder given, or ``<name>.mooring.yaml``."""
    if not isinstance(mooring, Mapping):
        path = Path(mooring)
        if path.is_file():
            return path
        found = sorted(path.glob("*.mooring.yaml")) if path.is_dir() else []
        if len(found) == 1:
            return found[0]
    return moor_dir / f"{name}.mooring.yaml"


@contextlib.contextmanager
def _sigma_grid(sig_level: Iterable[float] | None) -> Iterator[None]:
    """Use *sig_level* as the grid page's σ₀ targets for the block, then restore them."""
    if sig_level is None:
        yield
        return
    import numpy as np

    from oceanarray.config import parameters as params

    saved = params.SIGMA_GRID.copy()
    params.SIGMA_GRID = np.array(sorted(sig_level))
    try:
        yield
    finally:
        params.SIGMA_GRID = saved


def _combine_pdf(name: str, html_dir: Path, pdf_dir: PathLike | None) -> Path:
    """Combine the mooring's HTML pages into one PDF; raise MooringError if it cannot."""
    from oceanarray.reports import combine_mooring_pdf

    pdf_path = paths.resolve_pdf_path(name, pdf_dir, html_dir)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        pdf_path = combine_mooring_pdf(html_dir, name, output_path=pdf_path)
    except (ImportError, FileNotFoundError) as exc:
        raise MooringError(f"{name}: no PDF — {exc}") from exc
    _status("file", str(pdf_path))
    return pdf_path


def _array_report(
    array_yaml: Path,
    *,
    proc_dir: PathLike | None,
    report_dir: PathLike | None,
    force: bool,
) -> Path:
    """Build the array index; raise MooringError if it cannot be built."""
    from oceanarray.reports._array import generate_array_report

    root = Path(proc_dir) if proc_dir is not None else array_yaml.parent
    if not array_yaml.exists() and not array_yaml.is_absolute():
        array_yaml = root / array_yaml
    _status("section", f"Array report: {array_yaml.name}")
    page = generate_array_report(
        array_yaml_path=array_yaml,
        proc_dir=root,
        force=force,
        report_dir=Path(report_dir) if report_dir is not None else None,
    )
    if not page:
        raise MooringError(f"{array_yaml.name}: the array report could not be built.")
    return Path(page)


def _recovery_path(
    name: str, moor_dir: Path, out_dir: PathLike | None, report_dir: PathLike | None
) -> Path:
    """Return the recovery table's path: in *out_dir*, else ``report_dir/<name>``, else the mooring directory."""
    where = Path(
        out_dir or (Path(report_dir) / name if report_dir is not None else moor_dir)
    )
    return where / f"{name}_recovery_table.html"


def _recovery_table(
    name: str,
    moor_dir: Path,
    yaml_path: Path,
    out_dir: PathLike | None,
    report_dir: PathLike | None,
    *,
    force: bool,
) -> Path:
    """Build the recovery table: in *out_dir*, else ``report_dir/<name>``, else the mooring directory."""
    from oceanarray.reports._recovery_table import generate_recovery_table

    page = generate_recovery_table(
        mooring_name=name,
        proc_dir=moor_dir,
        yaml_path=yaml_path,
        out_path=_recovery_path(name, moor_dir, out_dir, report_dir),
        force=force,
    )
    if not page:
        raise MooringError(f"{name}: the recovery table could not be built.")
    return Path(page)
