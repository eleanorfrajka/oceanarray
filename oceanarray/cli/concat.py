"""``oceanarray concat`` — join one site's deployment stacks end to end, and grid them."""

from __future__ import annotations

import argparse
from pathlib import Path

from oceanarray.cli._common import _add_dir_args, _add_mask_flags_arg, _mask_flags
from oceanarray.cli.validate import _description_path


def cmd_concat(args: argparse.Namespace) -> int:
    """Concatenate the named moorings' stacks and grid them; write both and their reports."""
    from oceanarray.exceptions import MooringError
    from oceanarray.pipeline.concat import concat_moorings
    from oceanarray.pipeline.grid import grid_site

    descriptions = [_description_path(m, args.proc_dir) for m in args.mooring]
    mask_flags = _mask_flags(args)
    try:
        out = concat_moorings(
            descriptions,
            name=args.name,
            out_dir=args.output_dir,
            overwrite=args.force,
            dry_run=args.dry_run,
            mask_flags=mask_flags,
        )
        gridded = None
        if not args.no_grid and not args.dry_run:
            gridded = grid_site(
                out,
                pmin=args.pmin,
                pmax=args.pmax,
                dp=args.dp,
                slope_angle=args.slope_angle,
                overwrite=args.force,
            )
    except MooringError as exc:
        print(f"ERROR: {exc}")
        return 1
    if args.dry_run and not args.no_grid:
        print("would then grid the site stack into <NAME>_gridcat.nc")
    if not args.dry_run and not args.no_report:
        from oceanarray.reports._concat import generate_concat_page

        generate_concat_page(out, out.parent / "report", report_dir=args.report_dir)
        if gridded is not None:
            from oceanarray.reports._gridcat import generate_gridcat_page

            generate_gridcat_page(
                gridded, out.parent / "report", report_dir=args.report_dir
            )
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``concat`` subcommand to *sub*."""
    p = sub.add_parser(
        "concat",
        help="Join one site's deployment stacks end to end in time.",
        description=(
            "Read each named mooring's stack (<name>_stack.nc beside its description)\n"
            "and join them on one time axis by depth: every level of every\n"
            "deployment, each depth empty outside its levels' deployments; nothing is\n"
            "filled across a turnaround, and where two deployments overlap the newer\n"
            "fills the depth and the older is kept in the overlap block. The\n"
            "stacks must share stack: dt_seconds. Writes <NAME>_stackcat.nc, then grids\n"
            "it onto pressure levels, one mooring per profile, as <NAME>_gridcat.nc;\n"
            "and the two report pages (report/<NAME>_stackcat_report.html and\n"
            "report/<NAME>_gridcat_report.html). Exits 1 when the stacks cannot be\n"
            "joined or gridded."
        ),
        epilog=(
            "Examples:\n"
            "  # two deployments of one site\n"
            "  oceanarray concat egM2_2016 egM2_2018 --name egM2 --proc-dir /data/proc\n"
            "  # grid every 50 dbar, keep velocity flagged 3 out too\n"
            "  oceanarray concat egM2_2016 egM2_2018 --name egM2 --proc-dir /data/proc \\\n"
            "      --dp 50 --mask-flags 4,9 velocity=3,4,9\n"
            "  # check only\n"
            "  oceanarray concat /data/proc/egM2_*/egM2_*.mooring.yaml --name egM2 -n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "mooring",
        nargs="+",
        help="Two or more moorings: a <name>.mooring.yaml, its folder, or a name with --proc-dir.",
    )
    p.add_argument(
        "--name", required=True, help="The site's name (the output file stem)."
    )
    p.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write (default: <folder above the first mooring>/<NAME>).",
    )
    p.add_argument(
        "-n", "--dry-run", action="store_true", help="Check only; write nothing."
    )
    p.add_argument(
        "--force", action="store_true", help="Replace an existing site stack and grid."
    )
    p.add_argument(
        "--no-report", action="store_true", help="Write the files, not the pages."
    )
    p.add_argument(
        "--no-grid", action="store_true", help="Write the site stack only, no grid."
    )
    for flag, text in (
        ("--pmin", "Shallowest grid level, in dbar"),
        ("--pmax", "Deepest grid level, in dbar"),
    ):
        p.add_argument(
            flag,
            type=float,
            default=None,
            metavar="DBAR",
            help=(
                f"{text} (default: the 5th percentile of the shallowest depth's "
                "pressure and the 95th of the deepest's, to whole --dp)"
            ),
        )
    p.add_argument(
        "--dp",
        type=float,
        default=None,
        metavar="DBAR",
        help="Grid spacing, in dbar (default: 20)",
    )
    _add_mask_flags_arg(p, from_description=False)
    p.add_argument(
        "--slope-angle",
        type=float,
        default=None,
        metavar="DEG",
        help=(
            "Along-slope direction, degrees clockwise from north (default: the "
            "direction of the mean flow, when it is steady enough)"
        ),
    )
    p.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help=(
            "Where the deployments' own reports are, one folder per deployment "
            "(<DIR>/<deployment>), for the page's links (default: the report folder "
            "beside each deployment's stack)."
        ),
    )
    _add_dir_args(p, raw_needed=False)
    p.set_defaults(func=cmd_concat)
