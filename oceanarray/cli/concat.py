"""``oceanarray concat`` — join one site's deployment stacks end to end."""

from __future__ import annotations

import argparse
from pathlib import Path

from oceanarray.cli._common import _add_dir_args
from oceanarray.cli.validate import _description_path


def cmd_concat(args: argparse.Namespace) -> int:
    """Concatenate the named moorings' stacks; write the site stack and its report."""
    from oceanarray.exceptions import MooringError
    from oceanarray.pipeline.concat import concat_moorings

    descriptions = [_description_path(m, args.proc_dir) for m in args.mooring]
    try:
        out = concat_moorings(
            descriptions,
            name=args.name,
            out_dir=args.output_dir,
            overwrite=args.force,
            dry_run=args.dry_run,
        )
    except MooringError as exc:
        print(f"ERROR: {exc}")
        return 1
    if not args.dry_run and not args.no_report:
        from oceanarray.reports._concat import generate_concat_page

        generate_concat_page(out, out.parent / "report")
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``concat`` subcommand to *sub*."""
    p = sub.add_parser(
        "concat",
        help="Join one site's deployment stacks end to end in time.",
        description=(
            "Read each named mooring's stack (<name>_stack.nc beside its description)\n"
            "and join them on one time axis: every level of every deployment, each\n"
            "empty outside its deployment; nothing is filled across a turnaround. The\n"
            "stacks must share stack: dt_seconds. Writes <NAME>_stackcat.nc and its report\n"
            "page (report/<NAME>_stackcat_report.html). Exits 1 when the stacks cannot be\n"
            "joined."
        ),
        epilog=(
            "Examples:\n"
            "  # two deployments of one site\n"
            "  oceanarray concat egM2_2016 egM2_2018 --name egM2 --proc-dir /data/proc\n"
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
        "--force", action="store_true", help="Replace an existing site stack."
    )
    p.add_argument("--no-report", action="store_true", help="Write the stack only.")
    _add_dir_args(p, raw_needed=False)
    p.set_defaults(func=cmd_concat)
