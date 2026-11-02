"""``oceanarray list`` — the instrument types and file types a description can use."""

from __future__ import annotations

import argparse


def cmd_list(args: argparse.Namespace) -> int:
    """Print allowed instrument types and file_type values for mooring YAML files."""
    from ..config import parameters as params

    topic = getattr(args, "topic", None)

    instr_col = max(len(k) for k in params.INSTRUMENT_FILE_TYPES) + 2
    file_col = max(len(", ".join(v)) for v in params.INSTRUMENT_FILE_TYPES.values()) + 2

    if topic in (None, "instruments", "file-types"):
        print()
        print("  Mooring YAML — allowed values for instrument: and file_type:")
        print()
        print(f"  {'instrument':<{instr_col}}  {'file_type (seasenselib reader)'}")
        print(f"  {'-' * instr_col}  {'-' * file_col}")
        for name in sorted(params.INSTRUMENT_FILE_TYPES):
            readers = ", ".join(params.INSTRUMENT_FILE_TYPES[name])
            print(f"  {name:<{instr_col}}  {readers}")

        if params.EXTRA_FILE_TYPES:
            print()
            print("  Additional file_type values (specialist / deprecated):")
            print(f"  {'-' * instr_col}  {'-' * file_col}")
            for ft, note in sorted(params.EXTRA_FILE_TYPES.items()):
                print(f"  {ft:<{instr_col}}  {note}")

        print()
        print("  Mooring YAML fields for each instrument entry:")
        print("    instrument:  name from the table above; stored as instrument_type")
        print("                 in the NetCDF output.  Drives Aquadopp-specific plots,")
        print("                 declination correction, and tilt QC.")
        print("    file_type:   selects the seasenselib reader for stage 1.")
        print("    header:      path to the Nortek .hdr file (aquadopp only); required")
        print("                 for beam→XYZ coordinate transformation.")
        print()

    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``list`` subcommand to *sub*."""
    p_list = sub.add_parser(
        "list",
        help="List allowed instrument types and file_type values for mooring YAML files.",
    )
    p_list.add_argument(
        "topic",
        nargs="?",
        choices=["instruments", "file-types"],
        default=None,
        help="Filter output: 'instruments' or 'file-types' (default: show both).",
    )
    p_list.set_defaults(func=cmd_list)
