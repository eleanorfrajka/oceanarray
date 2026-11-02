"""``oceanarray stamp`` — mark stage files written before the processing_stage marker."""

from __future__ import annotations

import argparse
from pathlib import Path


def cmd_stamp(args: argparse.Namespace) -> int:
    """Write the processing_stage marker into stage files written before it existed."""
    from ..pipeline._stage import StageError
    from ..pipeline.stamp import stamp_file

    failed = False
    for path in args.files:
        try:
            written = stamp_file(path, args.stage)
        except (StageError, OSError) as e:
            print(f"ERROR: {e}")
            failed = True
            continue
        state = "stamped" if written else "already marked"
        print(f"{state}: {Path(path).name} (stage {args.stage})")
    return 1 if failed else 0


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``stamp`` subcommand to *sub*."""
    p_stamp = sub.add_parser(
        "stamp",
        help="Mark stage files written before the processing_stage marker existed.",
        description=(
            "Write processing_stage = N into each stage file, declaring the stage its\n"
            "filename (<name>_<serial>_stageN.nc) already states, so later reads do not\n"
            "warn. Refuses a file whose name or existing marker says another stage.\n"
            "Exits 0 if every file is marked, 1 if any was refused."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_stamp.add_argument("files", nargs="+", help="Stage file(s) to mark")
    p_stamp.add_argument(
        "--stage",
        type=int,
        required=True,
        choices=[1, 2, 3],
        help="The stage the files hold (must match their filenames).",
    )
    p_stamp.set_defaults(func=cmd_stamp)
