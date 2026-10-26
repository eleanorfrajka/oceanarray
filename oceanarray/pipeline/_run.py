"""The standard chain for one mooring: stages 1–3, then stack and grid."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from os import PathLike
from pathlib import Path
from typing import Any

from oceanarray import paths

#: Stack and grid settings used when neither the call nor the description's
#: ``stack:`` / ``grid:`` block gives one.
DEFAULTS: dict[str, dict[str, float]] = {
    "stack": {"dt_seconds": 60},
    "grid": {"p_start": 200.0, "p_end": 1000.0, "dp": 20.0},
}


def run(
    mooring: str | PathLike | Mapping[str, Any],
    *,
    stages: int | str | Iterable[int] = (1, 2, 3),
    serials: Iterable[str] | None = None,
    force: bool = False,
    keep_going: bool = True,
    mooring_dir: PathLike | None = None,
    raw_dir: PathLike | None = None,
    caldip_dir: PathLike | None = None,
    dt_seconds: int | None = None,
    p_start: float | None = None,
    p_end: float | None = None,
    dp: float | None = None,
) -> list[Path]:
    """Run stages 1–3 for one mooring, then stack and grid it.

    :func:`oceanarray.process` writes the stage files; the stack is built from
    every instrument's stage-3 file and written as ``<name>_stack.nc``, then
    gridded onto pressure levels as ``<name>_grid.nc``. The stack and grid are
    rebuilt on every call, so they always match the stage files.

    Parameters
    ----------
    mooring : str or os.PathLike or Mapping
        A ``*.mooring.yaml`` path, a directory holding exactly one, or a loaded
        description (then *mooring_dir* is required).
    stages : int or "all" or iterable of int, optional
        Which of stages 1–3 to run first (see :func:`oceanarray.process`); the
        stack and grid always follow. Default all three; ``stages=(2, 3)``
        re-runs from the stage-1 files without needing *raw_dir*.
    serials : iterable of str or None, optional
        Run stages 1–3 for these instruments only; the stack still takes every
        instrument's stage-3 file.
    force : bool, optional
        Rewrite stage files that already exist (see :func:`oceanarray.process`).
    keep_going : bool, optional
        Default True: stages 1–3 carry on past failures, and the stack takes each
        instrument's stage-3 file, else its stage-2 file (recorded per level in
        ``source_stage`` and in ``stage2_instruments``), leaving out and naming in
        ``instruments_omitted`` any with neither or that failed in this run; what
        failed is recorded in ``keep_going_failures``. Every product is
        ``data_mode = "P"``. False (strict): any failure raises, and no grid is
        made from a stack that left an instrument out.
    mooring_dir, raw_dir, caldip_dir : os.PathLike or None, optional
        As for :func:`oceanarray.process`.
    dt_seconds : int or None, optional
        Spacing of the stack's time axis. ``None`` takes ``stack: dt_seconds`` from
        the description, else 60.
    p_start, p_end, dp : float or None, optional
        The grid's pressure levels, in dbar. ``None`` takes the description's
        ``grid:`` block, else 200, 1000 and 20.

    Returns
    -------
    list of pathlib.Path
        The stage files (as :func:`oceanarray.process` returns them), then the
        stack and grid files.

    Raises
    ------
    oceanarray.exceptions.MooringError
        When nothing at all can be stacked, or a setting is invalid; with
        ``keep_going=False`` also on any failure in stages 1–3, an instrument with
        no stage-3 file, or a stack that left an instrument out.

    Examples
    --------
    >>> import oceanarray as oa
    >>> oa.run("proc/dune2_1_2026/dune2_1_2026.mooring.yaml", raw_dir="raw/")
    >>> oa.run(yaml_path, raw_dir="raw/", keep_going=False)  # stop on any failure
    >>> oa.run(yaml_path, stages=3, dp=10)                    # re-QC, regrid finer
    """
    from oceanarray.exceptions import MooringError
    from oceanarray.pipeline import (
        _Outcome,
        _process_stages,
        _resolve_mooring,
        _stage_numbers,
    )
    from oceanarray.pipeline.grid import _grid_file
    from oceanarray.pipeline.stack import _stack_file

    wanted = _stage_numbers(stages)
    cfg, moor_dir = _resolve_mooring(mooring, mooring_dir, "run")
    settings = _settings(
        cfg, dt_seconds=dt_seconds, p_start=p_start, p_end=p_end, dp=dp
    )
    name = cfg["name"]
    products = [paths.product_file(moor_dir, name, p) for p in ("stack", "grid")]
    outcome = _Outcome()
    try:
        _process_stages(
            cfg,
            moor_dir,
            wanted,
            outcome,
            serial_list=list(serials) if serials is not None else None,
            force=force,
            keep_going=keep_going,
            raw_dir=raw_dir,
            caldip_dir=caldip_dir,
        )
        print(f"Stack for mooring {name} (dt = {settings['dt_seconds']} s)")
        stack_path, ds_stack = _stack_file(
            cfg,
            moor_dir,
            dt_seconds=int(settings["dt_seconds"]),
            keep_going=keep_going,
            failed=outcome.failed_serials,
            stage3_failed=outcome.stage3_failed,
            failures=outcome.failures,
        )
    except MooringError:
        if outcome.written:
            _remove_stale(products, "the stage files changed but the run stopped")
        raise
    if not keep_going and "instruments_omitted" in ds_stack.attrs:
        _remove_stale(products[1:], "the new stack left instruments out")
        raise MooringError(
            f"{name}: the stack left instruments out "
            f"({ds_stack.attrs['instruments_omitted']}); {stack_path.name} is "
            "written but not gridded. Fix them, or use keep_going=True."
        )
    print(
        f"Grid for mooring {name} ({settings['p_start']:g}–"
        f"{settings['p_end']:g} dbar every {settings['dp']:g} dbar)"
    )
    try:
        grid_path = _grid_file(
            ds_stack,
            cfg,
            moor_dir,
            p_start=settings["p_start"],
            p_end=settings["p_end"],
            dp=settings["dp"],
        )
    except ValueError as e:
        if not keep_going:
            _remove_stale(products[1:], "the stack changed but could not be gridded")
            raise MooringError(f"{name}: cannot grid the stack: {e}") from e
        print(f"WARNING: keep_going: no grid — {e}")
        _remove_stale(products[1:], "the stack changed but could not be gridded")
        return [*outcome.files, stack_path]
    return [*outcome.files, stack_path, grid_path]


def _remove_stale(paths_: list[Path], why: str) -> None:
    """Delete product files that no longer match the stage files, saying so."""
    for path in paths_:
        if path.exists():
            path.unlink()
            print(f"  removed {path.name}: {why}, so it no longer matched.")


def _settings(cfg: Mapping[str, Any], **given: float | None) -> dict[str, float]:
    """Return the checked stack and grid settings: the call's, else the description's, else :data:`DEFAULTS`.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If a setting is not a finite number (``dt_seconds``: a whole number), is
        not positive (``dt_seconds``, ``dp``), or ``p_end`` is not above ``p_start``.
    """
    from oceanarray.exceptions import MooringError

    out: dict[str, float] = {}
    for block, keys in DEFAULTS.items():
        from_yaml = cfg.get(block) or {}
        for key, default in keys.items():
            value = given.get(key)
            out[key] = value if value is not None else from_yaml.get(key, default)
    problems = []
    for key, value in out.items():
        whole = key == "dt_seconds"
        if (
            isinstance(value, bool)
            or not isinstance(value, int if whole else int | float)
            or not math.isfinite(value)
        ):
            problems.append(
                f"{key}={value!r} is not a {'whole number' if whole else 'number'}"
            )
    if not problems:
        if out["dt_seconds"] <= 0:
            problems.append(f"dt_seconds={out['dt_seconds']} must be > 0")
        if out["dp"] <= 0:
            problems.append(f"dp={out['dp']} must be > 0")
        if out["p_end"] <= out["p_start"]:
            problems.append(
                f"p_end={out['p_end']} must be above p_start={out['p_start']}"
            )
    if problems:
        raise MooringError(
            "stack/grid settings (from the call, else the description's stack:/grid: "
            "blocks, else the defaults): " + "; ".join(problems)
        )
    return out
