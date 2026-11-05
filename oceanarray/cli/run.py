"""``oceanarray run`` — stages 1-3, the stack, the grid and the report."""

from __future__ import annotations

import argparse
from pathlib import Path

from oceanarray.cli._common import (
    _MOORING_HELP,
    _add_dir_args,
    _add_only,
    _add_shared_report_args,
    _add_stack_grid_args,
    _mooring_location,
    _raw_dir,
    _usage_error,
)


def cmd_run(args: argparse.Namespace) -> int:
    """Run stages 1-3, the stack, the grid and all reports for one mooring.

    Calls :func:`oceanarray.run`, then builds the reports (summary,
    per-instrument, stack, grid). It carries on past failed instruments and a
    failed stage 3, stacks what exists (stage 3, else stage 2, recorded in the
    stack) and builds the reports; the failures are printed as warnings and
    recorded in the products, which are provisional (``data_mode = "P"``).
    ``--strict`` stops at the first failing stage with exit code 1 and builds no
    report.

    The report step honours ``--output-dir``/``--report-dir`` (to redirect the
    HTML tree) and ``--sig-level`` (grid isopycnal targets); when unset it writes
    to the default ``{proc-dir}/{mooring}/report/`` with the default σ₀ grid.
    """
    from ..exceptions import MooringError
    from ..pipeline import run as _run
    from ..pipeline._run import _resolve_stages

    yaml_path = _mooring_location(args)
    raw_dir = _raw_dir(args)
    try:
        wanted = _resolve_stages(
            args.stage, args.from_stage, args.stack_stage, cli=True
        )
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1
    if args.dry_run:
        return _run_dry_run(args, yaml_path, wanted)
    if wanted and wanted[0] == 1 and raw_dir is None:
        _usage_error(
            "--raw-dir is required for stage 1 (or pass --from 2 to start "
            "from the stage-1 files)."
        )

    serials = args.serials
    try:
        _run(
            yaml_path,
            stages=wanted,
            serials=serials,
            force=args.force,
            keep_going=args.keep_going,
            raw_dir=raw_dir,
            stack_stage=args.stack_stage,
            dt_seconds=args.dt_seconds,
            pmin=args.pmin,
            pmax=args.pmax,
            dp=args.dp,
        )
    except (MooringError, ValueError) as e:
        print(f"ERROR: {e}")
        return 1

    from ..reports import report

    try:
        report(
            yaml_path,
            raw_dir=raw_dir,
            out_dir=args.outdir,
            report_dir=args.report_dir,
            serials=serials,
            sig_level=args.sig_level,
            force=args.force,
            skip_existing=args.skip_existing,
        )
    except MooringError as e:
        print(f"ERROR: {e}")
        return 1
    return 0


def _run_dry_run(args: argparse.Namespace, location: Path, wanted: list[int]) -> int:
    """Print the files a run would write, from the description; write nothing."""
    from oceanarray import paths
    from oceanarray.reports import report_paths

    from ._common import _print_stage_plan

    found = _print_stage_plan(args, location, wanted, "Run")
    if found is None:
        return 1
    name, moor_dir = found
    for product in ("stack", "grid"):
        f = paths.product_file(moor_dir, name, product)
        print(
            f"{product.capitalize()}:  {f}  (rebuilt from the stage-{args.stack_stage} files)"
        )
    where = report_paths(location, out_dir=args.outdir, report_dir=args.report_dir)
    print(f"Report:  {where['summary']}  (and the stack, grid and instrument pages)")
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``run`` subcommand to *sub*."""
    p_run = sub.add_parser(
        "run",
        help="Run stages 1-3, the stack, the grid and the report pages in one go.",
        description=(
            "Process a mooring from its raw files to its products and report: stages 1-3\n"
            "for every instrument, then the stack (instruments on one time base), the grid\n"
            "(time x pressure levels) and the HTML pages. Writes the stage files, \n"
            "<name>_stack.nc and <name>_grid.nc in the mooring folder, and the pages in\n"
            "<mooring folder>/report/ (or -o / --report-dir). Instruments that fail are\n"
            "left out and named in the products, unless --strict."
        ),
        epilog=(
            "Examples:\n"
            "  # everything, from the raw files\n"
            "  oceanarray run dsG3_1_2026 --proc-dir /data/proc --raw-dir /data/raw\n"
            "  # from the stage-1 files on, regridded at 10 dbar\n"
            "  oceanarray run /data/proc/dsG3_1_2026 --from 2 --dp 10\n"
            "  # list what would be written\n"
            "  oceanarray run /data/proc/dsG3_1_2026 --from 2 -n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_run.add_argument("mooring", help=_MOORING_HELP)
    _add_dir_args(p_run, raw_needed=True)
    p_run.add_argument(
        "--force", action="store_true", help="Overwrite existing files at every stage"
    )
    _add_only(
        p_run,
        "Restrict to these serial number(s) for processing and per-instrument reports",
    )
    p_run.add_argument(
        "--stage",
        type=int,
        nargs="+",
        choices=[1, 2, 3],
        default=None,
        metavar="N",
        help="Stages to run before the stack (default: 1 2 3). They must run through "
        "stage 3 (stage 2 with --stack-stage 2), so the stack never takes stage files "
        "older than the ones just written: --stage 2 3 re-runs stages 2 and 3 without "
        "the raw files, --stage 3 re-runs stage 3. To run one stage on its own, use "
        "'oceanarray process --stage N'.",
    )
    p_run.add_argument(
        "--from",
        type=int,
        choices=[1, 2, 3],
        default=None,
        dest="from_stage",
        metavar="N",
        help="Start at stage N and run through stage 3 (2 with --stack-stage 2): "
        "--from 2 is --stage 2 3.",
    )
    p_run.add_argument(
        "--stack-stage",
        type=int,
        choices=[3, 2],
        default=3,
        dest="stack_stage",
        metavar="N",
        help="Build the stack and grid from the stage-N files (default 3). 2 is a "
        "quick look at sea without stage 3: no QC flags, no interpolated pressure, "
        "velocities without magnetic declination; the report says so.",
    )
    p_run.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="List the stage, stack, grid and report files the run would write; "
        "write nothing.",
    )
    p_run.add_argument(
        "--skip-existing",
        action="store_true",
        dest="skip_existing",
        help="Keep any report page that exists, however old (the stage files, stack "
        "and grid are not affected).",
    )
    p_run.add_argument(
        "--strict",
        action="store_false",
        dest="keep_going",
        help="Stop at the first failing stage (exit 1, no report). Without it the "
        "run carries on past failed instruments and a failed stage 3, stacks "
        "stage-2 files where stage 3 is missing, and records what was left out; "
        "the products are provisional (data_mode P) either way.",
    )
    _add_stack_grid_args(p_run, from_description=True)
    _add_shared_report_args(p_run)
    p_run.set_defaults(func=cmd_run)
