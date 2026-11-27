"""The standard chain for one mooring: stages 1–3, then stack and grid."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from os import PathLike
from pathlib import Path
from typing import Any

from oceanarray import mask_rules, paths
from oceanarray.config import parameters as params

log = logging.getLogger(__name__)


def run(
    mooring: str | PathLike | Mapping[str, Any],
    *,
    stages: int | str | Iterable[int] | None = None,
    from_stage: int | None = None,
    serials: Iterable[str] | None = None,
    force: bool = False,
    keep_going: bool = True,
    mooring_dir: PathLike | None = None,
    raw_dir: PathLike | None = None,
    caldip_dir: PathLike | None = None,
    dt_seconds: int | None = None,
    pmin: float | None = None,
    pmax: float | None = None,
    dp: float | None = None,
    mask_flags: Iterable[int] | Mapping[str, Iterable[int]] | None = None,
    stack_stage: int = 3,
    report: bool = False,
) -> list[Path]:
    """Run stages 1–3 for one mooring, then stack and grid it (and build its report).

    Parameters
    ----------
    mooring : str or os.PathLike or Mapping
        A ``*.mooring.yaml``, a directory holding exactly one, or a loaded mooring YAML
        (then *mooring_dir* is required).
    stages : int or "all" or iterable of int or None, optional
        Stages to run before the stack, from the first given through the stage the stack
        reads: ``(2, 3)`` needs no *raw_dir*; ``2`` alone is refused. Default: all.
    from_stage : int or None, optional
        Start here instead of listing *stages*: ``from_stage=2`` is ``stages=(2, 3)``.
    serials : iterable of str or None, optional
        Run stages 1–3 for these instruments only; the stack still takes every one.
    force : bool, optional
        Rewrite existing stage files and rebuild a current stack and grid (needed after
        updating oceanarray from git without a new version).
    keep_going : bool, optional
        True: carry on past failures; the stack takes stage 3, else stage 2 (``source_stage``,
        ``stage2_instruments``), names what it left out (``instruments_omitted``,
        ``keep_going_failures``) and products are ``data_mode = "P"``. False: any failure
        raises.
    mooring_dir, raw_dir, caldip_dir : os.PathLike or None, optional
        As for :func:`oceanarray.process`.
    dt_seconds : int or None, optional
        Stack time step (s); default ``stack: dt_seconds``, else 3600.
    pmin, pmax, dp : float or None, optional
        Grid pressure levels (dbar); default the ``grid:`` block, else
        ``parameters.GRID_PMIN_DBAR``, ``GRID_PMAX_DBAR``, ``GRID_DP_DBAR``.
    mask_flags : iterable of int, or Mapping of str to iterable of int, optional
        Flags the stack pages hide and the grid leaves out (``stack_mask_flags``; see
        :func:`~oceanarray.mask_rules.resolve_mask_flags`); default
        ``stack: mask_flags``, else 4.
    stack_stage : {3, 2}, optional
        Stage files the stack reads; 2 is a quick look without QC, interpolated pressure
        or declination.
    report : bool, optional
        Also build the report pages (no PDF) with :func:`oceanarray.report`.

    Returns
    -------
    list of pathlib.Path
        The stage files, the stack and grid, then (with *report*) the summary page.

    Raises
    ------
    oceanarray.exceptions.MooringError
        Nothing can be stacked or a setting is invalid; with ``keep_going=False`` also any
        failure.

    Notes
    -----
    A stack is kept when the same stage files (SHA-256), settings, mooring fields and
    oceanarray release built it; a grid when built from that stack with the same levels
    and ``mask_flags``. Otherwise each is rebuilt, and its history says why.

    Examples
    --------
    Not run as tests: they need a mooring folder on disk.

    >>> import oceanarray as oa
    >>> oa.run("proc/dune2_1_2026/dune2_1_2026.mooring.yaml", raw_dir="raw/")  # doctest: +SKIP
    >>> oa.run(yaml_path, stages=3, dp=10)  # re-QC, regrid finer  # doctest: +SKIP
    """
    from oceanarray.exceptions import MooringError
    from oceanarray.pipeline import (
        _Outcome,
        _process_stages,
        resolve_mooring,
    )
    from oceanarray.pipeline.grid import _grid_file
    from oceanarray.pipeline.stack import _stack_file
    from oceanarray.pipeline.summary import RunSummary

    if stack_stage not in (2, 3):
        raise ValueError(f"stack_stage={stack_stage!r}: expected 3 or 2")
    wanted = _resolve_stages(stages, from_stage, stack_stage)
    serial_list = _as_serial_list(serials)
    mooring_dict, moor_dir = resolve_mooring(mooring, mooring_dir, "run")
    settings = _resolve_settings(
        mooring_dict, dt_seconds=dt_seconds, pmin=pmin, pmax=pmax, dp=dp
    )
    grid_flags = resolve_grid_mask_flags(mooring_dict)
    if mask_flags is not None and grid_flags is not None:
        # --mask-flags sets the stack's rule; the mooring YAML's grid: mask_flags
        # then adds to it rather than clash with it.

        try:
            grid_flags = mask_rules.union(
                mask_rules.stack_rule(mooring_dict, mask_flags), grid_flags
            )
        except ValueError as exc:
            raise MooringError(
                f"{mooring_dict.get('name', 'mooring')}: {exc} (the call)"
            ) from exc
    name = mooring_dict["name"]
    products = [paths.product_file(moor_dir, name, p) for p in ("stack", "grid")]
    outcome = _Outcome()
    # Collect the run's warnings on the package logger; the summary is logged and
    # written to processing_logs/<name>_summary.json at the end, whatever happens.
    summary = RunSummary()
    logging.getLogger("oceanarray").addHandler(summary)
    try:
        try:
            _process_stages(
                mooring_dict,
                moor_dir,
                wanted,
                outcome,
                serial_list=serial_list,
                force=force,
                keep_going=keep_going,
                raw_dir=raw_dir,
                caldip_dir=caldip_dir,
            )
            log.info("Stack for mooring %s (dt = %s s)", name, settings["dt_seconds"])
            stack_path, ds_stack = _stack_file(
                mooring_dict,
                moor_dir,
                dt_seconds=int(settings["dt_seconds"]),
                keep_going=keep_going,
                failed=outcome.failed_serials,
                from_stage2=outcome.stage3_failed or stack_stage == 2,
                stage2_reason=(
                    "stage 2 requested with stack_stage=2"
                    if stack_stage == 2
                    else "stage 3 failed in this run"
                    if outcome.stage3_failed
                    else None
                ),
                failures=outcome.failures,
                force=force,
                mask_flags=mask_flags,
            )
        except Exception:
            # Whatever stopped the run, a product built from the old stage files no
            # longer matches the ones it rewrote.
            if outcome.written:
                _set_aside_stale(
                    products, "the stage files changed but the run stopped"
                )
            raise
        if not keep_going and "instruments_omitted" in ds_stack.attrs:
            _set_aside_stale(products[1:], "the new stack left instruments out")
            raise MooringError(
                f"{name}: the stack left instruments out "
                f"({ds_stack.attrs['instruments_omitted']}); {stack_path.name} is "
                "written but not gridded. Fix them, or use keep_going=True."
            )
        log.info(
            "Grid for mooring %s (%g-%g dbar every %g dbar)",
            name,
            settings["pmin"],
            settings["pmax"],
            settings["dp"],
        )
        try:
            grid_path = _grid_file(
                ds_stack,
                mooring_dict,
                moor_dir,
                pmin=settings["pmin"],
                pmax=settings["pmax"],
                dp=settings["dp"],
                mask_flags=grid_flags,
                stack_path=stack_path,
                force=force,
            )
        except ValueError as e:
            if not keep_going:
                _set_aside_stale(
                    products[1:], "the stack changed but could not be gridded"
                )
                raise MooringError(f"{name}: cannot grid the stack: {e}") from e
            log.warning("keep_going: no grid — %s", e)
            _set_aside_stale(products[1:], "the stack changed but could not be gridded")
            files = [*outcome.files, stack_path]
        except Exception:
            _set_aside_stale(products[1:], "the stack changed but could not be gridded")
            raise
        else:
            files = [*outcome.files, stack_path, grid_path]
        if report:
            from oceanarray.reports import report as _report

            files.append(
                _report(
                    mooring_dict,
                    mooring_dir=moor_dir,
                    raw_dir=raw_dir,
                    serials=serial_list,
                    force=force,
                )
            )
        return files
    finally:
        logging.getLogger("oceanarray").removeHandler(summary)
        # A summary-write failure (e.g. a read-only processing_logs) must not mask
        # the run's own outcome — whether it returned files or is raising.
        try:
            summary.finish(name, moor_dir)
        except OSError as exc:
            log.warning("could not write the run summary for %s: %s", name, exc)


def _resolve_stages(
    stages: int | str | Iterable[int] | None,
    from_stage: int | None,
    stack_stage: int,
    *,
    cli: bool = False,
) -> list[int]:
    """Return the stages ``run`` runs, refusing any that would stack older files."""
    from oceanarray.pipeline import _canonical_stages

    if cli:

        def show(n: int | str | Iterable[int] | None) -> str:
            return " ".join(str(v) for v in _canonical_stages(n))

        stage_kw, from_kw, stack_kw = "--stage ", "--from ", "--stack-stage "
        one_stage = "'oceanarray process --stage N'"
    else:

        def show(n: int | str | Iterable[int] | None) -> str:
            return repr(n)

        stage_kw, from_kw, stack_kw = "stages=", "from_stage=", "stack_stage="
        one_stage = "oceanarray.process"
    if isinstance(stages, str) and stages.strip().lower() == "all":
        stages = None
    if stages is not None and from_stage is not None:
        raise ValueError(f"give {stage_kw.strip()} or {from_kw.strip()}, not both")
    if from_stage is not None:
        if from_stage not in range(1, stack_stage + 1):
            raise ValueError(
                f"{from_kw}{from_stage}: expected 1–{stack_stage} with "
                f"{stack_kw}{stack_stage}"
            )
        return list(range(from_stage, stack_stage + 1))
    if stages is None:
        return list(range(1, stack_stage + 1))
    wanted = _canonical_stages(stages)
    if wanted[0] > stack_stage:
        raise ValueError(
            f"{stage_kw}{show(stages)} with {stack_kw}{stack_stage}: the stack is "
            f"built from the stage-{stack_stage} files, so start at stage "
            f"{stack_stage} or earlier"
        )
    through = list(range(wanted[0], stack_stage + 1))
    if wanted != through:
        listed = " ".join(map(str, through)) if cli else repr(tuple(through))
        raise ValueError(
            f"{stage_kw}{show(stages)}: run rebuilds the stack from the "
            f"stage-{stack_stage} files, so the stages must run from stage "
            f"{wanted[0]} through {stack_stage} ({stage_kw}{listed}, or "
            f"{from_kw}{wanted[0]}); otherwise the stack would take files older "
            f"than the ones just written. To run one stage on its own, use "
            f"{one_stage}."
        )
    return wanted


def _as_serial_list(serials: Iterable[str] | None) -> list[str] | None:
    """Return *serials* as a list read once; a bare string is one serial."""
    if serials is None:
        return None
    return [serials] if isinstance(serials, str) else list(serials)


def _set_aside_stale(paths_: list[Path], why: str) -> None:
    """Rename product files that no longer match the stage files to ``*.stale.nc``."""
    for path in paths_:
        if path.exists():
            stale = paths.stale_file(path)
            # One stale copy per product: a later rename replaces it.
            path.replace(stale)
            log.warning(
                "renamed %s to %s: %s, so it no longer matched.",
                path.name,
                stale.name,
                why,
            )


def _resolve_settings(
    mooring_dict: Mapping[str, Any], **given: float | None
) -> dict[str, float]:
    """Return checked stack/grid settings: call, else mooring YAML, else the defaults."""
    from oceanarray.exceptions import MooringError

    defaults = {
        "stack": {"dt_seconds": params.STACK_DT_SECONDS},
        "grid": {
            "pmin": params.GRID_PMIN_DBAR,
            "pmax": params.GRID_PMAX_DBAR,
            "dp": params.GRID_DP_DBAR,
        },
    }
    out: dict[str, float] = {}
    for block, keys in defaults.items():
        from_yaml = mooring_dict.get(block) or {}
        for key, default in keys.items():
            value = given.get(key)
            out[key] = value if value is not None else from_yaml.get(key, default)
    problems = []
    dt_problem = _dt_seconds_problem(out["dt_seconds"])
    if dt_problem:
        problems.append(dt_problem)
    for key, value in out.items():
        if key == "dt_seconds":
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            problems.append(f"{key}={value!r} is not a number")
    if not problems:
        if out["dp"] <= 0:
            problems.append(f"dp={out['dp']} must be > 0")
        if out["pmax"] <= out["pmin"]:
            problems.append(f"pmax={out['pmax']} must be above pmin={out['pmin']}")
    if problems:
        raise MooringError(
            "stack/grid settings (from the call, else the mooring YAML's stack:/grid: "
            "blocks, else the defaults): " + "; ".join(problems)
        )
    return out


def resolve_grid_mask_flags(
    mooring_dict: Mapping[str, Any],
) -> dict[str, tuple[int, ...]] | None:
    """Return the mooring YAML's ``grid: mask_flags``, or None when it gives none.

    Parameters
    ----------
    mooring_dict : Mapping
        The mooring YAML.

    Returns
    -------
    dict of str to tuple of int, or None
        As :func:`oceanarray.mask_rules.resolve_mask_flags` returns it; the
        grid adds it to the stack's rule.

    Raises
    ------
    oceanarray.exceptions.MooringError
        The value is invalid.
    """
    from oceanarray.exceptions import MooringError

    value = (mooring_dict.get("grid") or {}).get("mask_flags")
    if value is None:
        return None
    try:
        return mask_rules.resolve_mask_flags(value)
    except ValueError as exc:
        raise MooringError(
            f"{mooring_dict.get('name', 'mooring')}: {exc} (the mooring YAML's grid: block)"
        ) from exc


def _dt_seconds_problem(value: Any) -> str | None:
    """Return why *value* is not a valid stack time step, or None if it is."""
    if isinstance(value, bool) or not isinstance(value, int):
        return f"dt_seconds={value!r} is not a whole number"
    if value <= 0:
        return f"dt_seconds={value} must be > 0"
    return None


def resolve_dt_seconds(mooring: Mapping[str, Any], given: int | None = None) -> int:
    """Return the stack's time step: the call, else the mooring YAML, else the default.

    Parameters
    ----------
    mooring : Mapping
        The loaded mooring YAML; its ``stack: dt_seconds`` is used when
        *given* is None.
    given : int or None, optional
        The time step the caller (or ``--dt-seconds``) gave, in seconds.

    Returns
    -------
    int
        The time step in seconds: *given*, else the mooring YAML's
        ``stack: dt_seconds``, else ``parameters.STACK_DT_SECONDS``.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If the chosen value is not a whole number greater than 0.
    """
    from oceanarray.exceptions import MooringError

    if given is not None:
        value, where = given, "the call"
    else:
        from_yaml = (mooring.get("stack") or {}).get("dt_seconds")
        if from_yaml is not None:
            value, where = from_yaml, "the mooring YAML's stack: block"
        else:
            value, where = params.STACK_DT_SECONDS, "the default"
    problem = _dt_seconds_problem(value)
    if problem:
        raise MooringError(f"stack time step (from {where}): {problem}")
    return int(value)
