"""``oceanarray init`` — write a skeleton mooring description."""

from __future__ import annotations

import argparse
from pathlib import Path


def cmd_init(args: argparse.Namespace) -> int:
    """Create a skeleton mooring YAML in {proc_dir}/{mooring}/{mooring}.mooring.yaml."""
    from oceanarray import mooring as _mooring

    out_path = Path(args.proc_dir) / args.mooring / f"{args.mooring}.mooring.yaml"
    if out_path.exists():
        print(f"ERROR: {out_path} already exists.")
        print("Rename or delete the existing file before generating a new stub.")
        return 1

    _mooring.save(_mooring.template(args.mooring), out_path, optional_blocks=True)
    print(f"Wrote stub: {out_path}")
    print(
        "Edit the file to fill in metadata and instrument details, then delete unused clamp entries."
    )
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``init`` subcommand to *sub*."""
    p_init = sub.add_parser(
        "init",
        help="Create a skeleton mooring YAML file.",
        description=(
            "Create {proc_dir}/{mooring}/{mooring}.mooring.yaml with commented template\n"
            "fields for all mandatory metadata and one example entry per instrument type.\n"
            "Edit the file to fill in real values and delete unused instrument blocks."
        ),
        epilog=(
            "Examples:\n"
            "  oceanarray init dsG3_1_2026 --proc-dir /data/proc\n"
            "  oceanarray validate /data/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_init.add_argument("mooring", help="Mooring name, e.g. dsG3_1_2026")
    p_init.add_argument(
        "--proc-dir",
        dest="proc_dir",
        metavar="DIR",
        required=True,
        help="Cruise-level processed output directory (stub is written to {proc_dir}/{mooring}/).",
    )
    p_init.set_defaults(func=cmd_init)
