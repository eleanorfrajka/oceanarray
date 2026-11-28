"""The pipeline: the verbs' bodies (stages, stack, grid, process, run) and what they compose.

:func:`process` runs stages 1–3 for one mooring from its mooring YAML. The
:data:`STAGES` tuple orders and dispatches all five stages for the form
``process(name, stage, proc_dir=…)``, deprecated and removed in 0.7.0.

Example usage::

    import oceanarray

    paths = oceanarray.Paths.standard("/data/OSNAP")
    mooring_path = paths.mooring_path("dsG3_1_2026")
    oceanarray.process(mooring_path, paths)                           # stages 1-3
    oceanarray.process(mooring_path, paths, stages=3, serials=["2941"])  # stage 3, one instrument

"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Any, Literal

from oceanarray import _deprecated
from oceanarray.exceptions import MooringError, OceanarrayError, ValidationIssue
from oceanarray.paths import Paths

__all__ = [
    "Stage",
    "STAGES",
    "resolve_stage",
    "resolve_mooring",
    "process",
    "run",
]

Scope = Literal["instrument", "mooring"]

_log = logging.getLogger("oceanarray")


@dataclass(frozen=True)
class Stage:
    """One pipeline stage.

    Parameters
    ----------
    name : str
        Canonical stage name used on the CLI and in log messages.  For the
        numbered stages this matches the output filename suffix
        (``*_stage3.nc``); ``stack`` and ``grid`` write ``stack.nc`` and
        ``grid.nc``.
    number : int or None
        Position for the numbered stages; ``None`` for ``stack`` and ``grid``.
        This is what makes ``stage=1`` resolvable and ``stage=4`` a
        ``ValueError`` rather than a silent wrong dispatch.
    scope : Scope
        Whether the stage runs once per instrument (``"instrument"``) or once
        per mooring (``"mooring"``).  Drives which staleness sources the re-run
        rule consults.
    run : Callable
        Normalised entry point: ``run(mooring, proc_dir, *, force, **kw) -> bool``.

    """

    name: str
    number: int | None
    scope: Scope
    run: Callable[..., bool]


# ---------------------------------------------------------------------------
# Normalised wrappers — thin shims over the existing class-based API so that
# STAGES can store a uniform (mooring, proc_dir, *, force, **kw) -> bool signature.
# ---------------------------------------------------------------------------


def _run_stage1(
    mooring: str,
    proc_dir: Path,
    *,
    raw_dir: Path | None = None,
    force: bool = False,
    serials: list[str] | None = None,
    **_kw: Any,
) -> bool:
    """Run stage 1 (raw → CF-NetCDF) for *mooring*."""
    if raw_dir is None:
        raise ValueError("stage1 requires raw_dir")
    from oceanarray.pipeline.stage1 import MooringProcessor

    proc = MooringProcessor(raw_dir=str(raw_dir), proc_dir=str(proc_dir))
    return bool(proc.process_mooring(mooring, serials=serials, force=force))


def _run_stage2(
    mooring: str,
    proc_dir: Path,
    *,
    force: bool = False,
    serials: list[str] | None = None,
    **_kw: Any,
) -> bool:
    """Run stage 2 (deployment trim + clock-drift correction) for *mooring*."""
    from oceanarray.pipeline.stage2 import Stage2Processor

    return bool(
        Stage2Processor(proc_dir=str(proc_dir)).process_mooring(
            mooring, serials=serials, force=force
        )
    )


def _run_stage3(
    mooring: str,
    proc_dir: Path,
    *,
    force: bool = False,
    serials: list[str] | None = None,
    dry_run: bool = False,
    **_kw: Any,
) -> bool:
    """Run stage 3 (QC, coordinate rotation, derived vars) for *mooring*."""
    from oceanarray.pipeline.stage3 import Stage3Processor

    return bool(
        Stage3Processor(proc_dir=str(proc_dir)).process_mooring(
            mooring, serials=serials, force=force, dry_run=dry_run
        )
    )


def _run_stack(
    mooring: str,
    proc_dir: Path,
    *,
    force: bool = False,
    dt_seconds: int | None = None,
    **_kw: Any,
) -> bool:
    """Run the stack stage (multi-instrument → common time grid) for *mooring*."""
    from oceanarray.pipeline.stack import MooringStacker

    return bool(
        MooringStacker(proc_dir=str(proc_dir)).stack(
            mooring, dt_seconds=dt_seconds, force=force
        )
    )


def _run_grid(
    mooring: str,
    proc_dir: Path,
    *,
    force: bool = False,
    p_start: float | None = None,
    p_end: float | None = None,
    dp: float | None = None,
    **_kw: Any,
) -> bool:
    """Run the grid stage (pressure-level interpolation) for *mooring*."""
    # p_start (shallowest), p_end (deepest) and dp are in dbar.
    from oceanarray.pipeline.grid import MooringGridder

    return bool(
        MooringGridder(proc_dir=str(proc_dir)).grid(
            mooring, p_start=p_start, p_end=p_end, dp=dp, force=force
        )
    )


#: Pipeline stages in execution order.  Single source of truth for
#: the deprecated ``process(name, stage, proc_dir=…)`` form and the CLI.
STAGES: tuple[Stage, ...] = (
    Stage("stage1", 1, "instrument", _run_stage1),
    Stage("stage2", 2, "instrument", _run_stage2),
    Stage("stage3", 3, "instrument", _run_stage3),
    Stage("stack", None, "mooring", _run_stack),
    Stage("grid", None, "mooring", _run_grid),
)


def resolve_stage(stage: int | str) -> Stage:
    """Return the :class:`Stage` named or numbered by *stage*.

    Parameters
    ----------
    stage : int or str
        ``1``, ``2``, or ``3``; or a canonical name — ``"stage1"``,
        ``"stage2"``, ``"stage3"``, ``"stack"``, ``"grid"``.  Names are
        matched case-insensitively.  Integer ``4`` is deliberately an error:
        ``stack`` and ``grid`` have no number so there is no valid integer
        shorthand for them.

    Returns
    -------
    Stage
        The matching :class:`Stage` entry from :data:`STAGES`.

    Raises
    ------
    ValueError
        If *stage* matches no entry.  The message lists every valid value.

    """
    for s in STAGES:
        # Guard bool before int: True == 1 and False == 0 in Python, so without
        # the isinstance check, resolve_stage(True) would silently return stage1.
        # Guard None before comparing to s.number: stack/grid have number=None,
        # so None == None would silently return the first un-numbered stage.
        if isinstance(stage, bool) or stage is None:
            break
        if stage == s.number or (isinstance(stage, str) and stage.lower() == s.name):
            return s
    valid_ints = ", ".join(str(s.number) for s in STAGES if s.number is not None)
    valid_names = ", ".join(repr(s.name) for s in STAGES)
    msg = f"unknown stage {stage!r}; expected one of: {valid_ints}, {valid_names}"
    raise ValueError(msg)


def _process_legacy(
    mooring: str,
    stage: int | str | list[int | str] | None = None,
    *,
    proc_dir: PathLike,
    raw_dir: PathLike | None = None,
    force: bool = False,
    **kw: Any,
) -> bool:
    """Run stages by name under *proc_dir*, as :func:`process` did before 0.6.0."""
    proc_dir = Path(proc_dir)
    raw_dir_path = Path(raw_dir) if raw_dir is not None else None

    # Data-integrity guard: refuse a mooring whose serials would make two
    # instruments' stage/report files silently overwrite each other.  Runs before
    # any stage writes, and `force` does not bypass it (force re-runs stale outputs,
    # it does not license data loss).
    from oceanarray.mooring import serial_collision_errors

    _collisions = serial_collision_errors(
        proc_dir / mooring / f"{mooring}.mooring.yaml"
    )
    if _collisions:
        raise MooringError(
            f"{mooring}: serial collisions would silently overwrite output files; "
            f"fix the serials in the mooring YAML: {'; '.join(_collisions)}",
            issues=[ValidationIssue("ERROR", c) for c in _collisions],
        )

    if stage is None:
        stages_to_run: tuple[Stage, ...] = STAGES
    elif isinstance(stage, (list, tuple)):
        # A subset always runs in STAGES order, whatever the order of the list.
        _requested = {resolve_stage(s) for s in stage}
        stages_to_run = tuple(s for s in STAGES if s in _requested)
    else:
        stages_to_run = (resolve_stage(stage),)

    from oceanarray import _deprecated, paths

    stack_file = paths.product_file(
        Paths(proc_dir=proc_dir).mooring_dir(mooring),
        mooring_name=mooring,
        product="stack",
    )

    overall = True
    skip_grid = False
    for s in stages_to_run:
        if s.name == "grid" and skip_grid:
            overall = False
            continue
        # Note the stack file's mtime before running the stack stage so a failure can
        # be told from a stale file left by a previous run.
        stack_mtime_before = (
            stack_file.stat().st_mtime
            if s.name == "stack" and stack_file.exists()
            else None
        )
        # The legacy form already warned once; its stage classes stay quiet.
        with _deprecated.internal():
            ok = s.run(
                mooring,
                proc_dir,
                raw_dir=raw_dir_path,
                force=force,
                **kw,
            )
        if not ok:
            overall = False
            # A stack returns False either because it wrote nothing (true failure →
            # skip grid) or because it wrote a *partial* stack with some instruments
            # omitted (file exists → grid still runs and inherits `instruments_omitted`).
            # Gate on whether THIS run wrote the stack — a stale stack from a previous
            # run (mtime unchanged) must not be gridded.
            if s.name == "stack":
                wrote_this_run = stack_file.exists() and (
                    stack_mtime_before is None
                    or stack_file.stat().st_mtime > stack_mtime_before
                )
                if not wrote_this_run:
                    skip_grid = True
    return overall


_UNSET: Any = object()


def _is_legacy_stage(value: Any) -> bool:
    """Return whether *value* is what the pre-0.6.0 form took second: a stage number or name, or a list of them."""
    names = {s.name for s in STAGES}
    items = value if isinstance(value, (list, tuple)) else [value]
    return bool(items) and all(
        isinstance(v, int) or (isinstance(v, str) and v.lower() in names) for v in items
    )


def process(
    mooring_path: str | PathLike | None = None,
    paths: Paths | None = None,
    *,
    stages: int | str | Iterable[int] = (1, 2, 3),
    serials: Iterable[str] | None = None,
    force: bool = False,
    keep_going: bool | None = None,
    stage: Any = _UNSET,
    mooring: str | PathLike | Mapping[str, Any] | None = None,
    mooring_dir: PathLike | None = None,
    raw_dir: PathLike | None = None,
    caldip_dir: PathLike | None = None,
    proc_dir: PathLike | None = None,
    **legacy_kw: Any,
) -> Any:
    """Run stages 1–3 for one mooring and write each stage's files (stack and grid: :func:`oceanarray.run`).

    Parameters
    ----------
    mooring_path : str or os.PathLike
        The ``*.mooring.yaml``.
    paths : oceanarray.Paths
        Where the files are: the stage files in ``paths.mooring_dir(name)``, the raw
        files (stage 1) in ``paths.raw_mooring_dir(name)``, the caldip results
        (stage 3; recorded as ``caldip_applied``, not yet applied) in
        ``paths.caldip_dir``.
    stages : int or "all" or iterable of int, optional
        Stages to run, always in order 1, 2, 3.
    serials : iterable of str or None, optional
        Process only these instruments.
    force : bool, optional
        Rewrite existing stage files (stage 1: those newer than their raw file).
    keep_going : bool or None, optional
        None or True: a failed instrument is logged and left out, later stages run on the
        rest, and every stage-3 file is ``data_mode = "P"``. False: raise once a stage has
        written what worked, and stop.
    mooring : str or os.PathLike or Mapping, optional
        Deprecated name of *mooring_path*; removed in 0.8.0.
    mooring_dir, raw_dir, caldip_dir : os.PathLike, optional
        Deprecated folders, used when *paths* is not given; removed in 0.8.0.
    stage, proc_dir, **legacy_kw
        The pre-0.6.0 form ``process(name, stage, proc_dir=...)``: deprecated, removed in
        0.7.0; use :func:`oceanarray.run`.

    Returns
    -------
    list of pathlib.Path
        Every stage file of the stages run (written or kept), in stage order.

    Raises
    ------
    oceanarray.exceptions.MooringError
        Invalid mooring YAML, a folder missing from *paths*, no instrument matched, or
        (with ``keep_going=False``) any failed instrument.
    ValueError
        *stages* names anything but 1, 2 and 3.
    TypeError
        *paths* is not an :class:`oceanarray.Paths` (or a pre-0.6.0 stage), or comes
        with the pre-0.6.0 keywords.

    Examples
    --------
    Not run as tests: they need a mooring YAML and its files on disk.

    >>> import oceanarray as oa
    >>> paths = oa.Paths.standard("/data/OSNAP")  # doctest: +SKIP
    >>> oa.process(paths.mooring_path("M2_2016"), paths)  # doctest: +SKIP
    >>> oa.process(mooring_path, paths, stages=3, serials=["2941"], force=True)  # doctest: +SKIP
    """
    if paths is not None and not isinstance(paths, Paths):
        if not _is_legacy_stage(paths):
            raise TypeError(
                "process() paths must be an oceanarray.Paths, "
                f"got {type(paths).__name__}"
            )
        # The pre-0.6.0 form gave the stage second.
        stage, paths = paths, None
    legacy = ["stage="] if stage is not _UNSET else []
    legacy += ["proc_dir="] if proc_dir is not None else []
    legacy += [k + "=" for k in legacy_kw]
    if paths is not None and legacy:
        raise TypeError(
            f"process() got paths and {', '.join(legacy)}, which it does not take"
        )
    if stage is not _UNSET or proc_dir is not None or legacy_kw:
        if keep_going is not None:
            raise TypeError(
                "keep_going= needs the new form: process(mooring_path, ...)"
            )
        warnings.warn(
            "process(name, stage, proc_dir=...) is deprecated and will be removed in "
            "0.7.0. Pass the mooring's *.mooring.yaml instead: "
            "oceanarray.process(mooring_path, paths) runs stages 1-3, and "
            "oceanarray.run(mooring_path, paths) runs stages 1-3, stack and grid.",
            DeprecationWarning,
            stacklevel=2,
        )
        if serials is not None:
            legacy_kw["serials"] = serials
        if caldip_dir is not None:
            legacy_kw["caldip_dir"] = caldip_dir
        return _process_legacy(
            mooring_path if mooring is None else mooring,
            None if stage is _UNSET else stage,
            proc_dir=proc_dir,
            raw_dir=raw_dir,
            force=force,
            **legacy_kw,
        )

    wanted = _canonical_stages(stages)
    renamed = "oceanarray.process(mooring=)"
    mooring_path = _deprecated.resolve_keyword(
        renamed, mooring_path, mooring, required="mooring_path", quiet=True
    )
    mooring_dict, _, paths, _ = resolve_verb_call(
        "process",
        mooring_path,
        paths,
        {"mooring_dir": mooring_dir, "raw_dir": raw_dir, "caldip_dir": caldip_dir},
        renamed=[renamed] if mooring is not None else [],
    )
    outcome = _Outcome()
    _process_stages(
        mooring_dict,
        paths,
        wanted,
        outcome,
        serial_list=list(serials) if serials is not None else None,
        force=force,
        keep_going=keep_going is not False,
    )
    return outcome.files


@dataclass
class _Outcome:
    """What a run of stages 1–3 did, filled in as it goes (so a caller sees it after a raise)."""

    files: list[Path] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    failed_serials: dict[str, str] = field(default_factory=dict)
    stage3_failed: bool = False


def resolve_mooring(
    mooring: str | PathLike | Mapping[str, Any],
    mooring_dir: PathLike | None,
    caller: str = "resolve_mooring",
) -> tuple[dict, Path]:
    """Return a mooring's loaded, validated mooring YAML and its directory.

    Parameters
    ----------
    mooring : str, os.PathLike or Mapping
        The ``*.mooring.yaml`` path, the folder holding it, or an already-loaded
        mooring YAML (then *mooring_dir* is required).
    mooring_dir : os.PathLike or None
        The mooring's processed folder; needed with a dict, optional with a path.
    caller : str, optional
        Name used in error messages (the verb that asked).

    Returns
    -------
    tuple of (dict, pathlib.Path)
        The mooring YAML (checked by :func:`oceanarray.mooring.load`) and the folder its
        stage files live in.

    Raises
    ------
    oceanarray.exceptions.MooringError
        When a dict comes without *mooring_dir*, the mooring YAML fails the checks, or
        the folder does not exist.

    Examples
    --------
    Not run as tests: they need a mooring folder on disk.

    >>> mooring_dict, folder = resolve_mooring("proc/dune2_1_2026/dune2_1_2026.mooring.yaml", None)  # doctest: +SKIP
    """
    from oceanarray import mooring as _mooring
    from oceanarray.pipeline.read_mooring import _resolve_description

    if isinstance(mooring, Mapping):
        if mooring_dir is None:
            raise MooringError(
                f"{caller} was given a mooring dict but no mooring_dir=; a dict has "
                "no location for the stage files — pass mooring_dir=."
            )
        # Loading a dict is deprecated; the caller has warned for it.
        with _deprecated.internal():
            mooring_dict, moor_dir = _mooring.load(mooring), Path(mooring_dir)
        # A dict has no file to be relative to: a relative catalog path is taken
        # from where the mooring YAML lives — the real folder of the mooring
        # directory's mooring YAML when it is a link to a file kept elsewhere, else
        # the mooring directory.
        catalog_path = mooring_dict.get("sensor_catalog")
        if catalog_path and not Path(str(catalog_path)).is_absolute():
            mooring_path = (
                moor_dir / f"{mooring_dict.get('name', moor_dir.name)}.mooring.yaml"
            )
            base = mooring_path.resolve().parent if mooring_path.exists() else moor_dir
            mooring_dict["sensor_catalog"] = str((base / str(catalog_path)).resolve())
    else:
        mooring_dict, moor_dir = _resolve_description(
            mooring, mooring_dir, 1, caller=caller
        )
    if not moor_dir.is_dir():
        raise MooringError(f"Mooring directory not found: {moor_dir}")
    return mooring_dict, moor_dir


def resolve_verb_call(
    verb: str,
    mooring_path: str | PathLike | Mapping[str, Any],
    paths: Paths | None,
    old: Mapping[str, PathLike | None],
    to_paths: Callable[[str, Path], Paths] | None = None,
    *,
    renamed: Iterable[str] = (),
) -> tuple[dict, Path, Paths, bool]:
    """Return a verb's mooring YAML, its path, its Paths and whether a dict was given: the verbs' shared step.

    An old form of the call warns once, for the first it uses: a renamed keyword, a
    mooring dict or folder in place of *mooring_path*, an old folder keyword, no *paths*.

    Parameters
    ----------
    verb : str
        The verb's name, used in messages and deprecation keys.
    mooring_path : str, os.PathLike or Mapping
        The verb's *mooring_path*.
    paths : oceanarray.Paths or None
        The verb's *paths*.
    old : Mapping of str to os.PathLike or None
        The verb's old folder keywords, by name (``mooring_dir`` among them).
    to_paths : callable, optional
        ``to_paths(mooring_name, mooring_dir)``, the Paths of a call without *paths*;
        default :meth:`~oceanarray.Paths.from_mooring_dir` with the keywords of *old*.
    renamed : iterable of str, optional
        Deprecation keys of the renamed keywords the call used.

    Returns
    -------
    tuple of (dict, pathlib.Path, oceanarray.Paths, bool)

    Raises
    ------
    TypeError
        *paths* is not a Paths, or is given with an old folder keyword.
    oceanarray.exceptions.MooringError
        The mooring YAML cannot be used, or the mooring's folder does not exist.
    """
    from oceanarray import mooring as _mooring

    if paths is not None and not isinstance(paths, Paths):
        raise TypeError(
            f"{verb}() paths must be an oceanarray.Paths, got {type(paths).__name__}"
        )
    given = [k for k, v in old.items() if v is not None]
    if paths is not None and given:
        raise TypeError(
            f"{verb}() got paths and {', '.join(k + '=' for k in given)}; "
            "give every folder in paths"
        )
    is_dict = isinstance(mooring_path, Mapping)
    is_folder = not is_dict and Path(mooring_path).is_dir()
    keys = list(renamed)
    keys += (
        [_deprecated.DICT_GIVEN]
        if is_dict
        else [_deprecated.FOLDER_GIVEN]
        if is_folder
        else []
    )
    keys += [f"oceanarray.{verb}({k}=)" for k in given]
    if paths is None and not given:
        keys.append(_deprecated.NO_PATHS)
    if keys:
        # Points at the caller of the verb.
        _deprecated.warn(keys[0], stacklevel=4)
    if is_dict:
        name = str(mooring_path.get("name", ""))
        where = old.get("mooring_dir") if paths is None else paths.mooring_dir(name)
        mooring_dict, moor_dir = resolve_mooring(mooring_path, where, verb)
        mooring_path = moor_dir / f"{mooring_dict['name']}.mooring.yaml"
        if paths is not None:
            try:
                # The call has warned once already.
                with _deprecated.internal():
                    mooring_path = paths.mooring_path(mooring_dict["name"])
            except MooringError:
                pass  # no yaml_dir: the YAML beside the stage files, as before
    else:
        mooring_path = (
            _mooring.folder_mooring_path(mooring_path, verb)
            if is_folder
            else Path(mooring_path)
        )
        mooring_dict = _mooring.load(mooring_path)
        moor_dir = Path(old.get("mooring_dir") or mooring_path.parent)
    if paths is None:
        if not moor_dir.is_dir():
            raise MooringError(f"Mooring directory not found: {moor_dir}")
        if to_paths is None:
            dirs = {k: v for k, v in old.items() if k != "mooring_dir"}
            paths = Paths.from_mooring_dir(mooring_dict["name"], moor_dir, **dirs)
        else:
            paths = to_paths(mooring_dict["name"], moor_dir)
    return mooring_dict, mooring_path, paths, is_dict


def _process_stages(
    mooring_dict: dict,
    paths: Paths,
    wanted: list[int],
    outcome: _Outcome,
    *,
    serial_list: list[str] | None,
    force: bool,
    keep_going: bool,
) -> None:
    """Run *wanted* of stages 1–3, filling *outcome*; raise on failure unless *keep_going*."""
    from oceanarray.logger import StageLog
    from oceanarray.pipeline.stage1 import (
        _attach_reader_logs,
        _detach_reader_logs,
        _stage1_files,
    )
    from oceanarray.pipeline.stage2 import _stage2_files
    from oceanarray.pipeline.stage3 import _stage3_files

    name = mooring_dict["name"]
    moor_dir, caldip_dir = paths.mooring_dir(name), paths.caldip_dir
    for n in wanted:
        with StageLog(name, f"stage{n}", moor_dir) as log:
            log(f"Stage {n} for mooring: {name}")
            if n == 1:
                # An absolute directory: in the mooring YAML stands in for raw_dir.
                override = mooring_dict.get("directory")
                absolute = override is not None and Path(override).is_absolute()
                raw = None if absolute else paths.raw_mooring_dir(name)
                readers = _attach_reader_logs(log.file)
                try:
                    run = _stage1_files(
                        mooring_dict,
                        moor_dir,
                        None if raw is None else raw.parent,
                        folder=moor_dir.name if raw is None else raw.name,
                        serials=serial_list,
                        force=force,
                        log=log,
                    )
                finally:
                    _detach_reader_logs(readers)
            elif n == 2:
                run = _stage2_files(
                    mooring_dict,
                    moor_dir,
                    serials=serial_list,
                    force=force,
                    exclude=set(outcome.failed_serials),
                    log=log,
                )
            else:
                if caldip_dir is not None:
                    log(
                        "  WARNING: caldip correction requested but not implemented; "
                        "Stage 3 science data is unchanged (see the caldip_applied "
                        "attribute)."
                    )
                # Stage 3 runs over the whole mooring: any failure fails it.
                try:
                    run = _stage3_files(
                        mooring_dict,
                        moor_dir,
                        serials=serial_list,
                        force=force,
                        caldip_dir=caldip_dir,
                        exclude=set(outcome.failed_serials),
                        log=log,
                    )
                except Exception as e:
                    log(
                        f"ERROR: {e}"
                        if isinstance(e, OceanarrayError)
                        else f"ERROR: Stage 3 failed: {e}"
                    )
                    if keep_going:
                        log("  keep_going: no stage-3 files written; carrying on.")
                        outcome.failures.append(f"stage 3: {e}")
                        outcome.stage3_failed = True
                        continue
                    if isinstance(e, OceanarrayError):
                        raise
                    raise MooringError(
                        f"{name}: stage 3 failed (nothing written; see {log.file}): {e}"
                    ) from e
            # One line per file, at DEBUG (shown by `oceanarray -v`).
            written = set(run.written)
            for path in run.files:
                _log.debug(
                    "  stage %d %s: %s", n, "wrote" if path in written else "kept", path
                )
        log(
            f"Stage {n}: {len(run.written)} file(s) written, "
            f"{len(run.files) - len(run.written)} kept (already there; force=True "
            "rewrites)"
        )
        outcome.files += run.files
        outcome.written += run.written
        if run.matched == 0:
            which = f" matching {', '.join(serial_list)}" if serial_list else ""
            raise MooringError(f"{name}: stage {n}: no instrument{which} to process.")
        if run.failed and keep_going:
            log(
                f"  keep_going: stage {n} failed for {len(run.failed)} of "
                f"{run.matched} instrument(s); carrying on without them:\n  - "
                + "\n  - ".join(run.failed)
            )
            outcome.failures += [f"stage {n}: {f}" for f in run.failed]
            for serial in run.failed_serials:
                outcome.failed_serials.setdefault(serial, f"failed in stage {n}")
        elif run.failed:
            raise MooringError(
                f"{name}: stage {n} failed for {len(run.failed)} of {run.matched} "
                f"instrument(s) (the rest were written; see {log.file}):\n  - "
                + "\n  - ".join(run.failed)
            )
    if outcome.failures:
        _log.warning(
            "%s: %d failure(s) left behind by keep_going (see the stage logs):\n  - %s",
            name,
            len(outcome.failures),
            "\n  - ".join(outcome.failures),
        )


def _canonical_stages(stages: int | str | Iterable[int]) -> list[int]:
    """Return *stages* as a sorted list drawn from 1, 2, 3, or raise ValueError."""
    if isinstance(stages, str):
        if stages.lower() != "all":
            raise ValueError(
                f"stages={stages!r}: expected 1, 2, 3, a list of them, or 'all'"
            )
        return [1, 2, 3]
    items = [stages] if isinstance(stages, int) else list(stages)
    bad = [s for s in items if isinstance(s, bool) or s not in (1, 2, 3)]
    if bad or not items:
        raise ValueError(
            f"stages={stages!r}: expected 1, 2, 3, a list of them, or 'all' "
            "(stack and grid are oceanarray.run)"
        )
    return sorted(set(items))


from oceanarray.pipeline._run import run  # noqa: E402  — run composes process
