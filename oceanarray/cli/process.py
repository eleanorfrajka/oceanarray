"""``oceanarray process`` — run stages 1-3 for one mooring."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from oceanarray import logger, paths
from oceanarray.cli._common import (
    _MOORING_HELP,
    _add_dir_args,
    _add_only,
    _add_stack_grid_args,
    _folder,
    _mooring_location,
    _parse_stage_token,
    _raw_dir,
    _usage_error,
)
from oceanarray.cli._deprecate import DeprecatedAlias
from oceanarray.pipeline import STAGES


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
        _usage_error("--raw-dir is required for stage 1.")

    if products and not args.keep_going:
        _usage_error(
            "--strict does not apply to --stage stack/grid; use "
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
            logger.status("error", str(e))
            return 1

    if args.report:
        from ..reports import summary

        logger.status("section", f"Record Summary: {args.mooring}")
        try:
            print(summary(location))
        except MooringError as e:
            logger.status("error", str(e))
            return 1

    if args.plot:
        _save_stage2_plots(_folder(location))
    return 0 if ok else 1


def _process_with_classes(
    args: argparse.Namespace, stages: list, raw_dir: Path | None
) -> bool:
    """Run the stack/grid *stages* the pre-0.6 way (by name under ``--proc-dir``)."""
    from ..pipeline import _process_legacy

    if not getattr(args, "proc_dir", None):
        _usage_error(
            "--stage stack/grid needs a mooring name and --proc-dir; or use "
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


def _save_stage2_plots(moor_dir: Path) -> None:
    """Save a PNG quick-look beside each microcat and aquadopp stage-2 file."""
    import matplotlib

    matplotlib.use("Agg")
    import xarray as xr

    from ..plotters import draw_aquadopp_raw, draw_microcat_raw

    logger.status("section", f"Plotting: {moor_dir.name}")
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
        help="Run the per-instrument stages 1-3 (raw files to QC'd netCDF).",
        description=(
            "Run one or more per-instrument processing stages for a mooring. Reads the\n"
            "mooring description and the raw files (stage 1) or the previous stage's files;\n"
            "writes <proc-dir>/<name>/<instrument>/<name>_<serial>_stage<N>.nc.\n"
            "  Stage 1: read raw files into CF-NetCDF\n"
            "  Stage 2: trim to the deployment window, correct clock drift\n"
            "  Stage 3: QC flags, BEAM to ENU rotation, magnetic declination, derived variables\n"
            "Default when --stage is omitted: stages 1, 2 and 3. For the stack, grid and\n"
            "report as well, use 'oceanarray run'."
        ),
        epilog=(
            "Examples:\n"
            "  # all three stages from the raw files\n"
            "  oceanarray process dsG3_1_2026 --proc-dir /data/proc --raw-dir /data/raw\n"
            "  # re-run stage 3 for two instruments\n"
            "  oceanarray process dsG3_1_2026 --proc-dir /data/proc --stage 3 --only 14321 400118\n"
            "  # see what stage 2 would write\n"
            "  oceanarray process /data/proc/dsG3_1_2026 --stage 2 -n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_process.add_argument("mooring", help=_MOORING_HELP)
    _add_dir_args(p_process, raw_needed=True)
    p_process.add_argument(
        "--stage",
        type=_parse_stage_token,
        nargs="+",
        choices=[s.number if s.number is not None else s.name for s in STAGES],
        default=None,
        metavar="{1,2,3,stack,grid}",
        help="Stage(s) to run (default: 1 2 3). 'stack' and 'grid' are deprecated and "
        "removed in 0.7.0: 'oceanarray run' builds them.",
    )
    _add_stack_grid_args(p_process, hidden=True)
    p_process.add_argument(
        "--plot",
        action="store_true",
        help="After stage 2, plot the MicroCAT records (temperature, conductivity, pressure)",
    )
    p_process.add_argument(
        "--summary",
        action="store_true",
        dest="report",
        help="Print the number of records per instrument and serial number",
    )
    p_process.add_argument(
        "--report",
        nargs=0,
        dest="report",
        action=DeprecatedAlias,
        new="--summary",
        help=argparse.SUPPRESS,
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
        help="Rewrite stage files that already exist (default: keep them; stage 1 "
        "rewrites only a file older than its raw file)",
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
