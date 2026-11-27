"""Filesystem path and filename conventions for the oceanarray pipeline.

Single source of truth for folder resolution (:class:`Paths`, read from a
``paths.yaml``), the stage output-filename pattern, and turning instrument serial
numbers into filename-safe tokens. Every processing stage derives its read/write
locations from here so the logic is defined once rather than copied per stage.
"""

import dataclasses
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from oceanarray import _deprecated
from oceanarray.exceptions import MooringError, OceanarrayError

log = logging.getLogger(__name__)

_PathLike = str | Path

#: Base URL of the hosted documentation, used in user-facing upgrade messages.
DOCS_URL = "https://ocean-uhh.github.io/oceanarray/"


class LegacyLayoutError(OceanarrayError, RuntimeError):
    """Raised when a directory uses the removed ``moor/proc`` (``basedir``) layout."""


#: The folders a :class:`Paths` holds: the keys of a ``paths.yaml`` and of each
#: mooring's entry under its ``moorings:`` section.
PATHS_FIELDS: tuple[str, ...] = (
    "yaml_dir",
    "raw_dir",
    "proc_dir",
    "report_dir",
    "export_dir",
    "caldip_dir",
)

_MOORING_YAML_SUFFIX = ".mooring.yaml"

#: The deprecation key for a mooring YAML found in its processed-data folder.
_YAML_IN_PROC_DIR = "a mooring YAML inside proc_dir/<mooring_name>/"


def _as_path(value: _PathLike | None) -> Path | None:
    """Return *value* as a Path with ``~`` expanded, or None."""
    return None if value is None else Path(value).expanduser()


@dataclasses.dataclass(frozen=True)
class Paths:
    """Where a project's files are: mooring YAMLs, raw files, processed files, pages, exports.

    A mooring's folder is ``<dir>/<mooring_name>``, unless *moorings* gives that
    mooring an exact folder. A field left None is not given; a method that needs it
    raises :class:`~oceanarray.exceptions.MooringError` naming the field. Paths are
    kept as given: a relative path stays relative to the current folder.

    Parameters
    ----------
    yaml_dir : str or Path, optional
        The folder holding the ``<mooring_name>.mooring.yaml`` files.
    raw_dir : str or Path, optional
        The raw instrument files, one folder per mooring.
    proc_dir : str or Path, optional
        The stage, stack and grid files, one folder per mooring.
    report_dir : str or Path, optional
        The report pages, one folder per mooring.
    export_dir : str or Path, optional
        The OceanSITES files (not per mooring).
    caldip_dir : str or Path, optional
        The caldip outputs.
    moorings : Mapping, optional
        Exact folders for moorings that differ, ``{mooring_name: {field: folder}}``
        with fields from :data:`PATHS_FIELDS`. Stored read-only.
    sources : Mapping, optional
        Where each field set came from: ``"given"`` (the default), ``"standard"``
        or ``"file"``. Not compared.

    Raises
    ------
    ValueError
        A *moorings* entry names a field not in :data:`PATHS_FIELDS`.

    Examples
    --------
    >>> paths = Paths(raw_dir="raw", proc_dir="proc")
    >>> paths.raw_mooring_dir("M2_2016").as_posix()
    'raw/M2_2016'
    >>> moved = paths.with_mooring("M2_2016", raw_dir="raw/M2_recovered")
    >>> moved.raw_mooring_dir("M2_2016").as_posix(), moved.mooring_dir("M2_2016").as_posix()
    ('raw/M2_recovered', 'proc/M2_2016')
    >>> Paths.standard("/data/OSNAP").mooring_dir("M2_2016").as_posix()
    '/data/OSNAP/proc/M2_2016'
    """

    yaml_dir: Path | None = None
    raw_dir: Path | None = None
    proc_dir: Path | None = None
    report_dir: Path | None = None
    export_dir: Path | None = None
    caldip_dir: Path | None = None
    moorings: Mapping[str, Mapping[str, Path]] = dataclasses.field(
        default_factory=dict, hash=False
    )
    sources: Mapping[str, str] | None = dataclasses.field(
        default=None, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        """Store each folder as a Path and the per-mooring folders read-only."""
        # A frozen dataclass sets its own normalized fields through object.__setattr__.
        for name in PATHS_FIELDS:
            object.__setattr__(self, name, _as_path(getattr(self, name)))
        moorings = {}
        for mooring_name, fields in self.moorings.items():
            unknown = sorted(set(fields) - set(PATHS_FIELDS))
            if unknown:
                raise ValueError(
                    f"moorings[{mooring_name!r}]: unknown field(s) {unknown}; "
                    f"expected some of {list(PATHS_FIELDS)}."
                )
            moorings[mooring_name] = MappingProxyType(
                {k: _as_path(v) for k, v in fields.items() if v is not None}
            )
        object.__setattr__(self, "moorings", MappingProxyType(moorings))
        if self.sources is None:
            sources = {k: "given" for k in PATHS_FIELDS if getattr(self, k) is not None}
        else:
            sources = dict(self.sources)
        object.__setattr__(self, "sources", MappingProxyType(sources))

    @classmethod
    def standard(cls, root: _PathLike) -> "Paths":
        """Return *root*'s ``yamls``, ``raw``, ``proc``, ``reports``, ``exports``; no caldip_dir.

        Parameters
        ----------
        root : str or Path
            The project folder.
        """
        root = Path(root).expanduser()
        dirs = {
            "yaml_dir": root / "yamls",
            "raw_dir": root / "raw",
            "proc_dir": root / "proc",
            "report_dir": root / "reports",
            "export_dir": root / "exports",
        }
        return cls(**dirs, sources=dict.fromkeys(dirs, "standard"))

    @classmethod
    def from_file(cls, path: _PathLike) -> "Paths":
        """Read a paths file (``paths.yaml``).

        The keys are those of :data:`PATHS_FIELDS`, each a folder, and an optional
        ``moorings:`` section giving exact folders for moorings that differ. A
        relative folder is relative to the paths file's folder; ``~`` is expanded.

        Parameters
        ----------
        path : str or Path
            The paths file.

        Returns
        -------
        Paths

        Raises
        ------
        MooringError
            The file is missing or not YAML, or a key is not one of
            :data:`PATHS_FIELDS` (or ``moorings``), or a value is not a folder.

        Examples
        --------
        Not run as a test: it needs a paths file on disk.

        >>> paths = Paths.from_file("paths.yaml")  # doctest: +SKIP
        >>> paths.yaml_path("M2_2016")  # doctest: +SKIP
        PosixPath('config/moorings/M2_2016.mooring.yaml')
        """
        path = Path(path).expanduser()
        if not path.is_file():
            raise MooringError(f"Paths file not found: {path}")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise MooringError(f"YAML parse error in {path}: {e}") from e
        data = {} if data is None else data
        allowed = [*PATHS_FIELDS, "moorings"]
        if not isinstance(data, dict):
            raise MooringError(
                f"{path}: a paths file is a mapping with keys {allowed}."
            )
        unknown = sorted(str(k) for k in set(data) - set(allowed))
        if unknown:
            raise MooringError(
                f"{path}: unknown key(s) {unknown}; a paths file takes {allowed}."
            )

        def _parse_folder(value: Any, key: str) -> Path | None:
            """Return a folder from the file, relative to its folder, or None."""
            if value is None:
                return None
            if not isinstance(value, str):
                raise MooringError(f"{path}: {key} must be a folder, not {value!r}.")
            given = Path(value).expanduser()
            return given if given.is_absolute() else path.parent / given

        moorings = data.get("moorings") or {}
        if not isinstance(moorings, dict) or not all(
            isinstance(fields, dict) for fields in moorings.values()
        ):
            raise MooringError(
                f"{path}: moorings: maps each mooring name to its folders, "
                f"with keys from {list(PATHS_FIELDS)}."
            )
        overrides = {}
        for mooring_name, fields in moorings.items():
            unknown = sorted(str(k) for k in set(fields) - set(PATHS_FIELDS))
            if unknown:
                raise MooringError(
                    f"{path}: moorings: {mooring_name}: unknown key(s) {unknown}; "
                    f"expected some of {list(PATHS_FIELDS)}."
                )
            overrides[str(mooring_name)] = {
                k: _parse_folder(v, f"moorings: {mooring_name}: {k}")
                for k, v in fields.items()
            }
        dirs = {k: _parse_folder(data.get(k), k) for k in PATHS_FIELDS}
        return cls(
            **dirs,
            moorings=overrides,
            sources={k: "file" for k, v in dirs.items() if v is not None},
        )

    @classmethod
    def from_mooring_dir(
        cls,
        mooring_name: str,
        mooring_dir: _PathLike,
        *,
        raw_dir: _PathLike | None = None,
        caldip_dir: _PathLike | None = None,
        report_dir: _PathLike | None = None,
        export_dir: _PathLike | None = None,
        mooring_report_dir: _PathLike | None = None,
    ) -> "Paths":
        """Return the Paths the verbs' old folder keywords meant, for one mooring's folder.

        The raw files are in ``<raw_dir>/<name of mooring_dir>``; the CLI builds its
        Paths from its folder flags this way.

        Parameters
        ----------
        mooring_name, mooring_dir : str or Path
            The mooring's name and its processed-data folder (its exact ``proc_dir``).
        raw_dir, caldip_dir, report_dir, export_dir : str or Path, optional
            As for :class:`Paths`.
        mooring_report_dir : str or Path, optional
            The mooring's exact report folder.
        """
        exact: dict[str, _PathLike] = {"proc_dir": mooring_dir}
        if raw_dir is not None:
            folder = Path(mooring_dir)
            # A folder given as "." or ".." is named by the folder it stands for.
            name = (
                folder.name if folder.name not in ("", "..") else folder.resolve().name
            )
            exact["raw_dir"] = Path(raw_dir) / name
        if mooring_report_dir is not None:
            exact["report_dir"] = mooring_report_dir
        return cls(
            raw_dir=raw_dir,
            caldip_dir=caldip_dir,
            report_dir=report_dir,
            export_dir=export_dir,
        ).with_mooring(mooring_name, **exact)

    def with_mooring(self, mooring_name: str, **fields: _PathLike) -> "Paths":
        """Return a copy with exact folders for one mooring, added to any it has.

        Parameters
        ----------
        mooring_name : str
            The mooring's name.
        **fields : str or Path
            Folders by field (:data:`PATHS_FIELDS`); another name is a TypeError.
        """
        unknown = sorted(set(fields) - set(PATHS_FIELDS))
        if unknown:
            raise TypeError(
                f"with_mooring() got unknown field(s) {unknown}; "
                f"expected some of {list(PATHS_FIELDS)}."
            )
        moorings = {name: dict(f) for name, f in self.moorings.items()}
        moorings[mooring_name] = {**moorings.get(mooring_name, {}), **fields}
        return dataclasses.replace(self, moorings=moorings)

    def _folder(self, field: str, mooring_name: str) -> Path:
        """Return the mooring's exact folder for *field*, else ``<field>/<mooring_name>``."""
        override = self.moorings.get(mooring_name, {}).get(field)
        if override is not None:
            return override
        root = getattr(self, field)
        if root is None:
            raise MooringError(
                f"{mooring_name}: no {field} given; set {field} in the paths file "
                f"(paths.yaml) or Paths({field}=...)."
            )
        return root / mooring_name

    def yaml_path(self, mooring_name: str) -> Path:
        """Return the path of a mooring's YAML, ``<yaml_dir>/<mooring_name>.mooring.yaml``.

        One still in ``<proc_dir>/<mooring_name>/`` is returned with a
        ``DeprecationWarning``, until 0.8.0; with neither file, the *yaml_dir* one.

        Parameters
        ----------
        mooring_name : str
            The mooring's name.

        Returns
        -------
        Path

        Raises
        ------
        MooringError
            No *yaml_dir* is given and no YAML is in the processed-data folder.
        """
        file_name = f"{mooring_name}{_MOORING_YAML_SUFFIX}"
        yaml_dir = self.moorings.get(mooring_name, {}).get("yaml_dir", self.yaml_dir)
        if yaml_dir is not None and (yaml_dir / file_name).is_file():
            return yaml_dir / file_name
        if self.proc_dir is not None or "proc_dir" in self.moorings.get(
            mooring_name, {}
        ):
            old = self._folder("proc_dir", mooring_name) / file_name
            if old.is_file():
                _deprecated.warn(_YAML_IN_PROC_DIR, stacklevel=3)
                return old
        if yaml_dir is None:
            raise MooringError(
                f"{mooring_name}: no yaml_dir given and no {file_name} in the "
                "processed-data folder; set yaml_dir in the paths file (paths.yaml) "
                "or Paths(yaml_dir=...)."
            )
        return yaml_dir / file_name

    def yaml_paths(self) -> list[Path]:
        """Return every mooring YAML the paths know, sorted.

        The ``*.mooring.yaml`` files in *yaml_dir* and in the exact ``yaml_dir`` of
        each mooring in *moorings*. When there are none, those still in
        ``<proc_dir>/*/`` are returned with a ``DeprecationWarning``, until 0.8.0.

        Returns
        -------
        list of Path

        Raises
        ------
        MooringError
            Neither *yaml_dir* nor *proc_dir* is given.
        """
        named = {
            name: fields["yaml_dir"] / f"{name}{_MOORING_YAML_SUFFIX}"
            for name, fields in self.moorings.items()
            if "yaml_dir" in fields
        }
        if self.yaml_dir is None and self.proc_dir is None and not named:
            raise MooringError(
                "no yaml_dir given; set yaml_dir in the paths file (paths.yaml) or "
                "Paths(yaml_dir=...)."
            )
        found = {p for p in named.values() if p.is_file()}
        if self.yaml_dir is not None:
            found.update(
                p for p in self.yaml_dir.glob(f"*{_MOORING_YAML_SUFFIX}") if p.is_file()
            )
        if not found and self.proc_dir is not None:
            found = {
                p
                for p in self.proc_dir.glob(f"*/*{_MOORING_YAML_SUFFIX}")
                if p.is_file()
            }
            if found:
                _deprecated.warn(_YAML_IN_PROC_DIR, stacklevel=3)
        return sorted(found)

    def raw_mooring_dir(self, mooring_name: str) -> Path:
        """Return a mooring's raw-data folder, ``<raw_dir>/<mooring_name>`` or its exact folder.

        Parameters
        ----------
        mooring_name : str
            The mooring's name; without *raw_dir* or an exact folder, a MooringError.
        """
        return self._folder("raw_dir", mooring_name)

    def mooring_dir(self, mooring_name: str) -> Path:
        """Return a mooring's processed-data folder, ``<proc_dir>/<mooring_name>`` or its exact folder.

        Parameters
        ----------
        mooring_name : str
            The mooring's name; without *proc_dir* or an exact folder, a MooringError.
        """
        return self._folder("proc_dir", mooring_name)

    def report_mooring_dir(self, mooring_name: str) -> Path:
        """Return a mooring's report folder, ``<report_dir>/<mooring_name>`` or its exact folder.

        Parameters
        ----------
        mooring_name : str
            The mooring's name; without *report_dir* or an exact folder, a MooringError.
        """
        return self._folder("report_dir", mooring_name)


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

    For discovery in a directory tree where no mooring YAML is at hand.

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
        A name from a mooring YAML (a cruise, a cast).

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
