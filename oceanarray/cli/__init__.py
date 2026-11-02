"""Command-line interface for oceanarray processing: one module per subcommand."""

from __future__ import annotations

import argparse
import sys

from oceanarray import paths
from oceanarray._version import __version__
from oceanarray.cli import animate as _animate
from oceanarray.cli import init as _init
from oceanarray.cli import list as _list
from oceanarray.cli import plot as _plot
from oceanarray.cli import process as _process
from oceanarray.cli import report as _report
from oceanarray.cli import run as _run
from oceanarray.cli import stamp as _stamp
from oceanarray.cli import validate as _validate
from oceanarray.cli._common import _parse_stage_token  # noqa: F401  — used by the tests
from oceanarray.cli.animate import cmd_animate
from oceanarray.cli.init import cmd_init
from oceanarray.cli.list import cmd_list
from oceanarray.cli.plot import cmd_plot
from oceanarray.cli.process import cmd_process
from oceanarray.cli.report import cmd_report
from oceanarray.cli.run import cmd_run
from oceanarray.cli.stamp import cmd_stamp
from oceanarray.cli.validate import cmd_validate

__all__ = [
    "build_parser",
    "cmd_animate",
    "cmd_init",
    "cmd_list",
    "cmd_plot",
    "cmd_process",
    "cmd_report",
    "cmd_run",
    "cmd_stamp",
    "cmd_validate",
    "main",
]


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser for the oceanarray CLI."""
    epilog = (
        "Typical workflow (MOORING is the *.mooring.yaml, its folder, or a name with --proc-dir):\n"
        "  oceanarray init     dsG3_1_2026 --proc-dir /data/proc          write a skeleton description\n"
        "  oceanarray validate /data/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml\n"
        "  oceanarray process  MOORING --raw-dir /data/raw --stage 1      one stage on its own\n"
        "  oceanarray report   MOORING --instruments                     look at the stage files\n"
        "  oceanarray run      MOORING --raw-dir /data/raw               stages 1-3, stack, grid, report\n"
        "  oceanarray run      MOORING --stage 3 --dp 10                 re-run stage 3, regrid finer\n"
        "\n"
        "Run 'oceanarray <command> --help' for command-specific options.\n"
    )
    parser = argparse.ArgumentParser(
        prog="oceanarray",
        description="Oceanographic mooring data processing.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Also print the processing stages' debug messages.",
    )
    sub = parser.add_subparsers(
        dest="command", title="commands", metavar="<command>", required=True
    )

    for verb in (
        _init,
        _process,
        _report,
        _run,
        _validate,
        _stamp,
        _plot,
        _animate,
        _list,
    ):
        verb.build_parser(sub)
    return parser


def main() -> None:
    """Entry point for the ``oceanarray`` command-line tool."""
    parser = build_parser()
    args, _unknown = parser.parse_known_args()
    if _unknown:
        _raw_dir_unknowns = []
        _other_unknowns = []
        _skip_next = False
        for _i, _tok in enumerate(_unknown):
            if _skip_next:
                _skip_next = False
                continue
            if _tok == "--raw-dir":
                _raw_dir_unknowns.append(_tok)
                if _i + 1 < len(_unknown) and not _unknown[_i + 1].startswith("--"):
                    _raw_dir_unknowns.append(_unknown[_i + 1])
                    _skip_next = True
            elif _tok.startswith("--raw-dir="):
                _raw_dir_unknowns.append(_tok)
            else:
                _other_unknowns.append(_tok)
        if _raw_dir_unknowns:
            print(
                f"oceanarray: WARNING: --raw-dir is not used by this subcommand "
                f"and will be ignored ({' '.join(_raw_dir_unknowns)})",
                file=sys.stderr,
            )
        if _other_unknowns:
            parser.error(f"unrecognized arguments: {' '.join(_other_unknowns)}")
    if args.verbose:
        from oceanarray import logger

        logger.CONSOLE_LEVEL = logger.logging.DEBUG
    try:
        sys.exit(args.func(args))
    except paths.LegacyLayoutError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
