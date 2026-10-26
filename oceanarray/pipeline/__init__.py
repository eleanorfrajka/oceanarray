"""Pipeline stage registry and public ``process()`` entry point.

:func:`process` runs stages 1–3 for one mooring from its description. The
:data:`STAGES` tuple orders and dispatches all five stages for the form
``process(name, stage, proc_dir=…)``, deprecated and removed in 0.7.0.

Example usage::

    import oceanarray

    yaml_path = "/data/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml"
    oceanarray.process(yaml_path, raw_dir="/data/raw")            # stages 1-3
    oceanarray.process(yaml_path, stages=3, serials=["2941"])      # stage 3, one instrument

"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "Stage",
    "STAGES",
    "resolve_stage",
    "process",
    "run",
]

Scope = Literal["instrument", "mooring"]


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
    """Run stage 1 (raw → CF-NetCDF) for *mooring*.

    Parameters
    ----------
    mooring : str
        Mooring name.
    proc_dir : Path
        Processing root directory (parent that contains per-mooring subdirectories).
    raw_dir : Path, optional
        Raw-data root.  Required for stage 1.
    force : bool
        Re-run even when output is newer than inputs.
    serials : list of str, optional
        Restrict to these serial numbers.

    """
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
    """Run stage 2 (deployment trim + clock-drift correction) for *mooring*.

    Parameters
    ----------
    mooring : str
        Mooring name.
    proc_dir : Path
        Processing root directory.
    force : bool
        Re-run even when output is newer than inputs.
    serials : list of str, optional
        Restrict to these serial numbers.

    """
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
    """Run stage 3 (QC, coordinate rotation, derived vars) for *mooring*.

    Parameters
    ----------
    mooring : str
        Mooring name.
    proc_dir : Path
        Processing root directory.
    force : bool
        Re-run even when output is newer than inputs.
    serials : list of str, optional
        Restrict to these serial numbers.
    dry_run : bool
        If True, log what would be done without writing output files.

    """
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
    dt_seconds: int = 60,
    **_kw: Any,
) -> bool:
    """Run the stack stage (multi-instrument → common time grid) for *mooring*.

    Parameters
    ----------
    mooring : str
        Mooring name.
    proc_dir : Path
        Processing root directory.
    force : bool
        Re-run even when output is newer than inputs.
    dt_seconds : int
        Target sampling interval in seconds (default 60).

    """
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
    p_start: float = 200.0,
    p_end: float = 1000.0,
    dp: float = 20.0,
    **_kw: Any,
) -> bool:
    """Run the grid stage (pressure-level interpolation) for *mooring*.

    Parameters
    ----------
    mooring : str
        Mooring name.
    proc_dir : Path
        Processing root directory.
    force : bool
        Re-run even when output is newer than inputs.
    p_start : float
        Shallowest pressure level in dbar.
    p_end : float
        Deepest pressure level in dbar.
    dp : float
        Pressure step in dbar.

    """
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
    """Run stages by name under *proc_dir*: the form :func:`process` had before 0.6.0.

    Parameters
    ----------
    mooring : str
        Mooring name (the subdirectory under *proc_dir*).
    stage : int, str, list of int/str, or None
        Which stage(s) to run — ``1``, ``2``, ``3``, ``"stage1"``, ``"stack"``,
        ``"grid"``, etc.  Pass a list to run a specific subset; the subset is
        always executed in :data:`STAGES` order regardless of the order of
        elements in the list.  ``None`` (the default) runs all five stages in
        :data:`STAGES` order.
    proc_dir : path-like
        Processing root directory (parent containing per-mooring subdirectories).
    raw_dir : path-like, optional
        Raw-data root.  Required only when *stage* includes stage 1.
    force : bool, default False
        Re-run even when the output is newer than its inputs.
    **kw
        Extra keyword arguments forwarded to the stage's ``run`` callable
        (e.g. ``serials``, ``dt_seconds``, ``p_start``, ``p_end``, ``dp``).

    Returns
    -------
    bool
        ``True`` if every requested stage succeeded, ``False`` if any failed.

    """
    proc_dir = Path(proc_dir)
    raw_dir_path = Path(raw_dir) if raw_dir is not None else None

    # Data-integrity guard: refuse a mooring whose serials would make two
    # instruments' stage/report files silently overwrite each other.  Runs before
    # any stage writes, and `force` does not bypass it (force re-runs stale outputs,
    # it does not license data loss).
    from oceanarray.config.validation import serial_collision_errors

    _collisions = serial_collision_errors(
        proc_dir / mooring / f"{mooring}.mooring.yaml"
    )
    if _collisions:
        raise ValueError(
            f"Refusing to process '{mooring}': serial collisions would silently "
            f"overwrite output files. Fix the mooring YAML:\n  - "
            + "\n  - ".join(_collisions)
        )

    if stage is None:
        stages_to_run: tuple[Stage, ...] = STAGES
    elif isinstance(stage, (list, tuple)):
        _requested = {resolve_stage(s) for s in stage}
        stages_to_run = tuple(s for s in STAGES if s in _requested)
    else:
        stages_to_run = (resolve_stage(stage),)

    from oceanarray import paths

    stack_file = paths.product_file(
        paths.mooring_proc_dir(proc_dir, mooring), mooring, "stack"
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


def process(
    mooring: str | PathLike | Mapping[str, Any],
    stage: Any = _UNSET,
    *,
    stages: int | str | Iterable[int] = (1, 2, 3),
    serials: Iterable[str] | None = None,
    force: bool = False,
    keep_going: bool | None = None,
    mooring_dir: PathLike | None = None,
    raw_dir: PathLike | None = None,
    caldip_dir: PathLike | None = None,
    proc_dir: PathLike | None = None,
    **legacy_kw: Any,
) -> Any:
    """Run stages 1–3 for one mooring and write each stage's files.

    Each stage reads the previous stage's files, applies the pure stage function
    (:func:`~oceanarray.pipeline.stage1.stage1`, :func:`~oceanarray.pipeline.stage2.stage2`,
    :func:`~oceanarray.pipeline.stage3.stage3`) and writes its own files beside
    them, with a log per stage. Stacking and gridding are :func:`oceanarray.run`.

    Parameters
    ----------
    mooring : str or os.PathLike or Mapping
        A ``*.mooring.yaml`` path, a directory holding exactly one, or a loaded
        description (then *mooring_dir* is required).
    stages : int or "all" or iterable of int, optional
        The stages to run, from ``1``, ``2`` and ``3``; always run in that order.
        Default ``(1, 2, 3)``.
    serials : iterable of str or None, optional
        Process only these instruments.
    force : bool, optional
        Rewrite stage files that already exist (stage 1: that are newer than
        their raw file). Default False keeps them.
    keep_going : bool or None, optional
        ``None`` (the default) means True: carry on past failures — a failed instrument is logged and
        left out, the next stage runs on the instruments that have files, and a
        stage-3 failure is logged instead of raised. Every stage-3 file is
        ``data_mode = "P"`` either way. False (strict) raises once a stage has
        written what worked, and runs nothing after it.
    mooring_dir : os.PathLike or None, optional
        The mooring's own directory; taken from *mooring* when it is a path.
    raw_dir : os.PathLike or None, optional
        The cruise-level raw-data root, holding the mooring's folder (named as
        *mooring_dir* is). Required for stage 1 unless the description gives an
        absolute ``directory:``.
    caldip_dir : os.PathLike or None, optional
        Calibration-dip results for stage 3 (not yet applied: the stub records
        ``caldip_applied``); its presence rewrites existing stage-3 files.
    stage, proc_dir, **legacy_kw
        The form before 0.6.0, ``process(name, stage, proc_dir=…)``, which also
        runs stack and grid and returns a bool. Deprecated (a ``DeprecationWarning``)
        and removed in 0.7.0; use :func:`oceanarray.run` for stages 1–3, stack and
        grid.

    Returns
    -------
    list of pathlib.Path
        Every stage file of the stages run, in stage order: those written and
        those kept because they were already there (each stage's log says which).
        A failed or ``skip`` instrument has none.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If the description is invalid, a directory is missing or no instrument
        matched; with ``keep_going=False`` also if any instrument failed (a stage
        writes the instruments that succeeded before raising, and the later stages
        do not run).
    ValueError
        If *stages* names anything but 1, 2 and 3.

    Examples
    --------
    >>> import oceanarray as oa
    >>> oa.process("proc/dune2_1_2026/dune2_1_2026.mooring.yaml", raw_dir="raw/")
    >>> oa.process(mooring, stages=3, serials=["2941"], force=True)
    """
    if stage is not _UNSET or proc_dir is not None or legacy_kw:
        if keep_going is not None:
            raise TypeError("keep_going= needs the new form: process(yaml_path, ...)")
        warnings.warn(
            "process(name, stage, proc_dir=...) is deprecated and will be removed in "
            "0.7.0. Pass the mooring's *.mooring.yaml instead: "
            "oceanarray.process(yaml_path, raw_dir=...) runs stages 1-3, and "
            "oceanarray.run(yaml_path, ...) runs stages 1-3, stack and grid.",
            DeprecationWarning,
            stacklevel=2,
        )
        if serials is not None:
            legacy_kw["serials"] = serials
        if caldip_dir is not None:
            legacy_kw["caldip_dir"] = caldip_dir
        return _process_legacy(
            mooring,
            None if stage is _UNSET else stage,
            proc_dir=proc_dir,
            raw_dir=raw_dir,
            force=force,
            **legacy_kw,
        )

    wanted = _stage_numbers(stages)
    cfg, moor_dir = _resolve_mooring(mooring, mooring_dir, "process")
    outcome = _Outcome()
    _process_stages(
        cfg,
        moor_dir,
        wanted,
        outcome,
        serial_list=list(serials) if serials is not None else None,
        force=force,
        keep_going=keep_going is not False,
        raw_dir=raw_dir,
        caldip_dir=caldip_dir,
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


def _resolve_mooring(
    mooring: str | PathLike | Mapping[str, Any],
    mooring_dir: PathLike | None,
    caller: str,
) -> tuple[dict, Path]:
    """Return the loaded, validated description and the mooring's directory."""
    from oceanarray import mooring as _mooring
    from oceanarray.exceptions import MooringError
    from oceanarray.pipeline.read_mooring import _resolve

    if isinstance(mooring, Mapping):
        if mooring_dir is None:
            raise MooringError(
                f"{caller} was given a mooring dict but no mooring_dir=; a dict has "
                "no location for the stage files — pass mooring_dir=."
            )
        cfg, moor_dir = _mooring.load(mooring), Path(mooring_dir)
    else:
        cfg, moor_dir = _resolve(mooring, mooring_dir, 1, caller=caller)
    if not moor_dir.is_dir():
        raise MooringError(f"Mooring directory not found: {moor_dir}")
    return cfg, moor_dir


def _process_stages(
    cfg: dict,
    moor_dir: Path,
    wanted: list[int],
    outcome: _Outcome,
    *,
    serial_list: list[str] | None,
    force: bool,
    keep_going: bool,
    raw_dir: PathLike | None,
    caldip_dir: PathLike | None,
) -> None:
    """Run *wanted* of stages 1–3, filling *outcome*; raise on failure unless *keep_going*."""
    from oceanarray.exceptions import MooringError
    from oceanarray.logger import StageLog
    from oceanarray.pipeline.stage1 import (
        _attach_reader_logs,
        _detach_reader_logs,
        _stage1_files,
    )
    from oceanarray.pipeline.stage2 import _stage2_files
    from oceanarray.pipeline.stage3 import _stage3_files

    name = cfg["name"]
    for n in wanted:
        with StageLog(name, f"stage{n}", moor_dir) as log:
            log(f"Stage {n} for mooring: {name}")
            if n == 1:
                readers = _attach_reader_logs(log.file)
                try:
                    run = _stage1_files(
                        cfg,
                        moor_dir,
                        Path(raw_dir) if raw_dir is not None else None,
                        folder=moor_dir.name,
                        serials=serial_list,
                        force=force,
                        log=log,
                    )
                finally:
                    _detach_reader_logs(readers)
            elif n == 2:
                run = _stage2_files(
                    cfg, moor_dir, serials=serial_list, force=force, log=log
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
                        cfg,
                        moor_dir,
                        serials=serial_list,
                        force=force,
                        caldip_dir=caldip_dir,
                        log=log,
                    )
                except Exception as e:
                    log(
                        f"ERROR: {e}"
                        if isinstance(e, MooringError)
                        else f"ERROR: Stage 3 failed: {e}"
                    )
                    if keep_going:
                        log("  keep_going: no stage-3 files written; carrying on.")
                        outcome.failures.append(f"stage 3: {e}")
                        outcome.stage3_failed = True
                        continue
                    if isinstance(e, MooringError):
                        raise
                    raise MooringError(
                        f"{name}: stage 3 failed (nothing written; see {log.file}): {e}"
                    ) from e
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
        print(
            f"WARNING: {name}: {len(outcome.failures)} failure(s) left behind by "
            "keep_going (see the stage logs):\n  - " + "\n  - ".join(outcome.failures)
        )


def _stage_numbers(stages: int | str | Iterable[int]) -> list[int]:
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
