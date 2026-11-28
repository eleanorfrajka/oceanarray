"""The report verb: build a mooring's HTML report (and PDF), an array index, or a recovery table."""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Iterator, Mapping
from os import PathLike
from pathlib import Path
from typing import Any, Literal

from oceanarray import _deprecated, logger
from oceanarray.exceptions import MooringError
from oceanarray.paths import Paths

Kind = Literal["mooring", "array", "recovery"]


def report(
    yaml_path: str | PathLike | None = None,
    paths: Paths | None = None,
    *,
    kind: Kind = "mooring",
    breadcrumb: Iterable[tuple[str, str | None]] | None = None,
    instruments: bool = True,
    stack: bool = True,
    grid: bool = True,
    serials: Iterable[str] | None = None,
    sig_level: Iterable[float] | None = None,
    pdf: bool = False,
    force: bool = False,
    skip_existing: bool = False,
    bathymetry: PathLike | None = None,
    mooring: str | PathLike | Mapping[str, Any] | None = None,
    mooring_dir: PathLike | None = None,
    proc_dir: PathLike | None = None,
    raw_dir: PathLike | None = None,
    out_dir: PathLike | None = None,
    report_dir: PathLike | None = None,
    pdf_dir: PathLike | None = None,
) -> Path:
    """Build a report and return the path of its main page.

    Parameters
    ----------
    yaml_path : str or os.PathLike
        ``"mooring"`` / ``"recovery"``: the ``*.mooring.yaml``; ``"array"``: the
        ``*.array.yaml``.
    paths : oceanarray.Paths
        Where the files are: the stage, stack and grid files in
        ``paths.mooring_dir(name)``, the raw files (named on the instrument pages) in
        ``paths.raw_mooring_dir(name)``, the pages, recovery table and PDF written to
        ``paths.report_mooring_dir(name)``. ``"array"``: the moorings in
        ``paths.proc_dir``, the index in ``paths.report_dir`` (default
        ``paths.proc_dir``) and each mooring's pages under it.
    kind : {"mooring", "array", "recovery"}, optional
        The mooring's summary page (and its instrument, stack and grid pages), the array
        index, or the cruise-report recovery table.
    breadcrumb : iterable of (str, str or None), optional
        Ancestor crumbs ``[(text, href), ...]`` (hrefs relative to the report directory)
        shown before each page's own position.
    instruments, stack, grid : bool, optional
        ``"mooring"``: also build these pages.
    serials : iterable of str or None, optional
        Instrument pages for these serials only.
    sig_level : iterable of float or None, optional
        σ₀ targets of the grid page's isopycnal panels (default
        :data:`~oceanarray.config.parameters.SIGMA_GRID`).
    pdf : bool, optional
        ``"mooring"``: also combine the pages into one A4 PDF, beside them.
    force : bool, optional
        Rebuild pages newer than their inputs.
    skip_existing : bool, optional
        Keep any existing page, however old.
    bathymetry : os.PathLike or None, optional
        ``"array"``: a GEBCO or ETOPO file for the map's depth contours.
    mooring : str or os.PathLike or Mapping, optional
        Deprecated name of *yaml_path*; removed in 0.8.0.
    mooring_dir, proc_dir, raw_dir, out_dir, report_dir, pdf_dir : os.PathLike, optional
        Deprecated folders, used when *paths* is not given; removed in 0.8.0.

    Returns
    -------
    pathlib.Path

    Raises
    ------
    oceanarray.exceptions.MooringError
        Invalid or missing mooring YAML, a folder missing from *paths*, a page that
        cannot be built, or a PDF that cannot be made (``pdf`` extra not installed).
    ValueError
        Unknown *kind*.
    TypeError
        *paths* is not an :class:`oceanarray.Paths`.

    Examples
    --------
    Not run as tests: they need a mooring YAML and its files on disk.

    >>> import oceanarray as oa
    >>> paths = oa.Paths.from_file("paths.yaml")  # doctest: +SKIP
    >>> oa.report(paths.yaml_path("M2_2016"), paths, pdf=True)  # doctest: +SKIP
    >>> oa.report("OSNAPeast_2016.array.yaml", paths, kind="array")  # doctest: +SKIP
    """
    from oceanarray.pipeline import resolve_verb_call

    renamed = "oceanarray.report(mooring=)"
    yaml_path = _deprecated.resolve_keyword(
        renamed, yaml_path, mooring, required="yaml_path", quiet=True
    )
    renamed_given = [renamed] if mooring is not None else []
    crumbs = list(breadcrumb) if breadcrumb is not None else None
    if kind == "array":
        if paths is not None and not isinstance(paths, Paths):
            raise TypeError(
                f"report() paths must be an oceanarray.Paths, got {type(paths).__name__}"
            )
        old = {"proc_dir": proc_dir, "report_dir": report_dir}
        given = [k for k, v in old.items() if v is not None]
        if paths is not None and given:
            raise TypeError(
                f"report() got paths and {', '.join(k + '=' for k in given)}; "
                "give every folder in paths"
            )
        keys = renamed_given + [f"oceanarray.report({k}=)" for k in given]
        if paths is None or keys:
            # Points at the caller of report.
            _deprecated.warn((keys or [_deprecated.NO_PATHS])[0], stacklevel=3)
        if paths is None:
            paths = Paths(
                proc_dir=proc_dir or Path(yaml_path).parent, report_dir=report_dir
            )
        return _array_report(
            Path(yaml_path),
            paths,
            breadcrumb=crumbs,
            force=force,
            bathymetry=bathymetry,
        )
    if kind not in ("mooring", "recovery"):
        raise ValueError(f"kind={kind!r}: expected 'mooring', 'array' or 'recovery'")

    def _old(name: str, folder: Path) -> Paths:
        """Return the Paths of the old folder keywords."""
        pages = (
            None if report_dir else folder / "report" if kind == "mooring" else folder
        )
        return Paths.from_mooring_dir(
            name,
            folder,
            raw_dir=raw_dir,
            report_dir=report_dir,
            mooring_report_dir=out_dir or pages,
        )

    mooring_dict, yaml_path, paths, given_dict = resolve_verb_call(
        "report",
        yaml_path,
        paths,
        {
            "mooring_dir": mooring_dir,
            "proc_dir": proc_dir,
            "raw_dir": raw_dir,
            "out_dir": out_dir,
            "report_dir": report_dir,
            "pdf_dir": pdf_dir,
        },
        _old,
        renamed=renamed_given,
    )
    name = mooring_dict["name"]
    moor_dir, pages = paths.mooring_dir(name), paths.report_mooring_dir(name)
    if not moor_dir.is_dir():
        raise MooringError(f"Mooring directory not found: {moor_dir}")
    if kind == "recovery":
        from oceanarray.reports._recovery_table import generate_recovery_table

        pages.mkdir(parents=True, exist_ok=True)
        return Path(
            generate_recovery_table(
                mooring_name=name,
                proc_dir=moor_dir,
                yaml_path=yaml_path,
                out_path=pages / f"{name}_recovery_table.html",
                force=force,
            )
        )

    from oceanarray.pipeline._run import _as_serial_list
    from oceanarray.reports import MooringReport

    try:
        # The folder stage 1 reads, so the pages find the files it read.
        raw_folder = paths.raw_mooring_dir(name)
    except MooringError:
        raw_folder = None  # the instrument pages then name no raw files
    serial_list = _as_serial_list(serials)
    with _sigma_grid(sig_level):
        logger.status("section", f"Report: {name}")
        page = MooringReport(proc_dir=str(moor_dir.parent)).generate(
            name,
            force=force,
            skip_existing=skip_existing,
            outdir=str(pages),
            breadcrumb=crumbs,
            serials=serial_list,
            instruments=instruments or serial_list is not None,
            grid=grid,
            stack=stack,
            # A loaded mooring YAML may differ from the file on disk, so it is
            # passed on (and rebuilds the summary); a path is read from disk.
            mooring_dict=mooring_dict if given_dict else None,
            mooring_dir=moor_dir,
            yaml_path=yaml_path,
            raw_mooring_dir=raw_folder,
        )
    if pdf:
        where = report_paths(
            mooring_dict, mooring_dir=moor_dir, out_dir=pages, pdf_dir=pdf_dir
        )
        _combine_pdf(name, pages, where["pdf"])
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

    The mooring YAML is only read for its ``name``, not checked, so this works
    on a half-filled one.

    Parameters
    ----------
    mooring : str or os.PathLike or Mapping
        As for :func:`report`.
    mooring_dir : os.PathLike or None, optional
        The mooring's own folder, when *mooring* is a loaded mooring YAML.
    out_dir, report_dir, pdf_dir : os.PathLike or None, optional
        As for :func:`report`.

    Returns
    -------
    dict of str to pathlib.Path
        ``"summary"``, ``"stack"``, ``"grid"`` and ``"pdf"`` pages, and
        ``"instrument_dir"`` (where ``<name>_<serial>_report.html`` go).
    """
    from oceanarray.reports._html_helpers import _pages_dir

    name, moor_dir = _resolve_name_and_dir(mooring, mooring_dir)
    html = _pages_dir(name, out_dir, report_dir, moor_dir)
    return {
        "summary": html / f"{name}_report.html",
        "stack": html / f"{name}_stack_report.html",
        "grid": html / f"{name}_grid_report.html",
        "instrument_dir": html / "instrument",
        "pdf": Path(pdf_dir or html) / f"{name}_report.pdf",
    }


def _resolve_name_and_dir(
    mooring: str | PathLike | Mapping[str, Any], mooring_dir: PathLike | None
) -> tuple[str, Path]:
    """Return the mooring YAML's ``name`` and the mooring's folder, without checking it."""
    from oceanarray.mooring import check_yaml as _mooring

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
    mooring_dict = _mooring._parse(path)
    if not isinstance(mooring_dict, Mapping) or not mooring_dict.get("name"):
        raise MooringError(f"{path}: no 'name:' to name the report pages from.")
    return str(mooring_dict["name"]), moor_dir or path.parent


def _resolve_description_file(
    mooring: str | PathLike | Mapping[str, Any], moor_dir: Path, name: str
) -> Path:
    """Return the mooring YAML file: the path given, the one in the folder given, or ``<name>.mooring.yaml``."""
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


def _combine_pdf(name: str, html_dir: Path, pdf_path: Path) -> Path:
    """Combine the mooring's HTML pages into the PDF *pdf_path*; raise MooringError if it cannot."""
    from oceanarray.reports import combine_mooring_pdf

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        pdf_path = combine_mooring_pdf(html_dir, name, output_path=pdf_path)
    except (ImportError, FileNotFoundError) as exc:
        raise MooringError(f"{name}: no PDF — {exc}") from exc
    logger.status("file", str(pdf_path))
    return pdf_path


def _array_report(
    array_yaml: Path,
    paths: Paths,
    *,
    breadcrumb: list[tuple[str, str | None]] | None = None,
    force: bool,
    bathymetry: PathLike | None = None,
) -> Path:
    """Build the year page; raise MooringError if it cannot be built."""
    from oceanarray.reports._array import generate_array_report

    root = paths.proc_dir
    if root is None:
        raise MooringError(
            f"{array_yaml.name}: no proc_dir given; set proc_dir in the paths file "
            "(paths.yaml) or Paths(proc_dir=...)."
        )
    if not array_yaml.exists() and not array_yaml.is_absolute():
        array_yaml = root / array_yaml
    logger.status("section", f"Array report: {array_yaml.name}")
    page = generate_array_report(
        array_yaml_path=array_yaml,
        proc_dir=root,
        force=force,
        report_dir=paths.report_dir,
        breadcrumb=breadcrumb,
        bathymetry=Path(bathymetry) if bathymetry is not None else None,
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
