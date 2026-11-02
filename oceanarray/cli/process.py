"""``oceanarray process`` — run stages 1-3 for one mooring."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from oceanarray import paths
from oceanarray.cli._common import (
    _MOORING_HELP,
    _add_dir_args,
    _add_only,
    _add_stack_grid_args,
    _folder,
    _mooring_location,
    _raw_dir,
    _stage_token,
)
from oceanarray.pipeline import STAGES
from oceanarray.utilities import _status


def cmd_process(args: argparse.Namespace) -> int:
    """Run stages 1-3 for one mooring through :func:`oceanarray.process`.

    By default runs stages 1 (raw → CF-NetCDF), 2 (trim to deployment window +
    clock-drift correction) and 3 (QC flags, pressure interpolation, beam → ENU
    velocity rotation), carrying on past a failed instrument (``--strict``: stop
    at the first failure and exit 1).

    ``--dry-run`` lists the file each stage would write, without writing. ``--stage
    stack`` / ``grid`` still run through the pre-0.6 stage classes, with a note
    (``oceanarray run`` builds the stack and grid); ``--strict`` with them is
    refused.

    Optional side effects:
    - ``--report``: after processing, print a per-instrument record summary to
      the console (record counts, time spans, variables present).
    - ``--plot``: save a PNG overview plot for each instrument alongside the
      stage2 NetCDF file in the proc directory.
    """
    from ..exceptions import MooringError

    location = _mooring_location(args)
    raw_dir = _raw_dir(args)
    # --report alone (no --stage) only prints the summary.
    stages = args.stage if (args.stage is not None or not args.report) else []
    if stages is None:
        stages = [1, 2, 3]
    products = [s for s in stages if s in ("stack", "grid")]
    if args.dry_run and not products:
        from ._common import _print_stage_plan

        ints = sorted({int(s) for s in stages})
        return 0 if _print_stage_plan(args, location, ints, "Process") else 1
    if 1 in stages and raw_dir is None:
        raise SystemExit("ERROR: --raw-dir is required for stage 1.")

    if products and not args.keep_going:
        raise SystemExit(
            "ERROR: --strict does not apply to --stage stack/grid; use "
            "'oceanarray run MOORING --strict' to build the stack and grid."
        )
    ok = True
    if products:
        ok = _process_with_classes(args, stages, raw_dir)
    elif stages:
        from ..pipeline import process

        try:
            process(
                location,
                stages=stages,
                serials=args.serials,
                force=args.force,
                keep_going=args.keep_going,
                raw_dir=raw_dir,
            )
        except MooringError as e:
            _status("error", str(e))
            return 1

    if args.report:
        from ..reports import summary

        _status("section", f"Record Summary: {args.mooring}")
        try:
            print(summary(location))
        except MooringError as e:
            _status("error", str(e))
            return 1

    if args.plot:
        _plot_stage2(_folder(location))
    return 0 if ok else 1


def _process_with_classes(
    args: argparse.Namespace, stages: list, raw_dir: Path | None
) -> bool:
    """Run the stack/grid *stages* the pre-0.6 way (by name under ``--proc-dir``)."""
    from ..pipeline import _process_legacy

    if not getattr(args, "proc_dir", None):
        raise SystemExit(
            "ERROR: --stage stack/grid needs a mooring name and --proc-dir; or use "
            "'oceanarray run PATH' for the stack and grid."
        )
    if any(s in ("stack", "grid") for s in stages):
        print(
            "oceanarray: process --stage stack/grid is removed in 0.7.0; "
            "'oceanarray run MOORING --stage 3' rebuilds the stack and grid.",
            file=sys.stderr,
        )
    return _process_legacy(
        args.mooring,
        stage=stages,
        proc_dir=Path(args.proc_dir),
        raw_dir=raw_dir,
        force=args.force,
        serials=args.serials,
        dt_seconds=args.dt_seconds,
        p_start=args.pmin,
        p_end=args.pmax,
        dp=args.dp,
        dry_run=args.dry_run,
    )


def _plot_stage2(moor_dir: Path) -> None:
    """Save a PNG quick-look beside each microcat and aquadopp stage-2 file."""
    import matplotlib

    matplotlib.use("Agg")
    import xarray as xr

    from ..plotters import draw_aquadopp_raw, draw_microcat_raw

    _status("section", f"Plotting: {moor_dir.name}")
    for kind, draw in (
        ("microcat", draw_microcat_raw),
        ("aquadopp", draw_aquadopp_raw),
    ):
        for nc in sorted((moor_dir / kind).glob(paths.stage_glob(2))):
            with xr.open_dataset(nc, decode_timedelta=False) as ds:
                draw(ds, save_path=nc.with_suffix(".png"))
            print(f"Saved: {nc.with_suffix('.png')}")


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``process`` subcommand to *sub*."""
    p_process = sub.add_parser(
        "process",
        help="Run per-instrument processing stages 1-3 (raw→NetCDF, trim+clock, QC+ENU).",
        description=(
            "Run one or more per-instrument processing stages for a mooring.\n"
            "  Stage 1: read raw files → CF-NetCDF (_stage1.nc)\n"
            "  Stage 2: trim to deployment window + apply clock-drift correction (_stage2.nc)\n"
            "  Stage 3: QC flags, BEAM→ENU rotation, magnetic declination, derived vars (_stage3.nc)\n"
            "Default when --stage is omitted: stages 1, 2 and 3."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_process.add_argument("mooring", help=_MOORING_HELP)
    _add_dir_args(p_process, raw_needed=True)
    p_process.add_argument(
        "--stage",
        type=_stage_token,
        nargs="+",
        choices=[s.number if s.number is not None else s.name for s in STAGES],
        default=None,
        metavar="{1,2,3,stack,grid}",
        help="Stage(s) to run (default: 1 2 3). "
        "Use 'stack' and 'grid' for mooring-level steps.",
    )
    _add_stack_grid_args(p_process)
    p_process.add_argument(
        "--plot",
        action="store_true",
        help="Generate plots of processed microcat data after stage 2",
    )
    p_process.add_argument(
        "--report",
        action="store_true",
        help="Print a summary of processed records per instrument and serial number",
    )
    _add_only(
        p_process,
        "Process only instrument(s) with these serial number(s), e.g. --only 14321 400118",
    )
    p_process.add_argument(
        "--strict",
        action="store_false",
        dest="keep_going",
        help="Stop at the first failing instrument (exit 1). Without it, stages 1-3 "
        "carry on past failed instruments, log them, and exit 0.",
    )
    p_process.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output files (both stages skip existing files by default)",
    )
    p_process.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="List the stage files each stage would write (new, keep or rewrite) and, "
        "for stage 3, which instruments get interpolated pressure; write nothing.",
    )
    p_process.set_defaults(func=cmd_process)
