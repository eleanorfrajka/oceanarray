"""``oceanarray export`` — write a mooring's stage-3 files and grid as OceanSITES 1.4 files."""

from __future__ import annotations

import argparse
from pathlib import Path

from oceanarray.cli._common import _add_dir_args, _folder, _mooring_location
from oceanarray.cli._deprecate import DeprecatedAlias


def cmd_export(args: argparse.Namespace) -> int:
    """Write the OceanSITES files through :func:`oceanarray.export`; 1 if nothing was written."""
    from oceanarray.exceptions import MooringError
    from oceanarray.pipeline.export import export
    from oceanarray.pipeline.oceansites import OceanSitesError

    location = _mooring_location(args)
    try:
        written = export(
            location,
            mooring_dir=_folder(location),
            out_dir=args.output_dir,
            array=args.array,
            grid=not args.no_grid,
            overwrite=args.force,
            dry_run=args.dry_run,
        )
    except (MooringError, OceanSitesError, FileExistsError) as e:
        print(f"ERROR: {e}")
        return 1
    verb = "would write" if args.dry_run else "wrote"
    for path in written:
        print(f"{verb} {path}")
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``export`` subcommand to *sub*."""
    p = sub.add_parser(
        "export",
        help="Write the stage-3 files and grid as OceanSITES 1.4 files.",
        description=(
            "Write each instrument's stage-3 file as an OceanSITES 1.4 deployment file\n"
            "(OS_<platform>_<YYYYMM>_<mode>_<type>-<serial>-<depth>m.nc) and the grid as a\n"
            "gridded file (..._GRD_<interval>-DP<step>.nc). The mooring description needs\n"
            "platform_code. Instruments that cannot be exported are listed and left out.\n"
            "Exits 0 if any file was written, 1 if none."
        ),
        epilog=(
            "Examples:\n"
            "  # every instrument and the grid, into <mooring folder>/oceansites/\n"
            "  oceanarray export /data/proc/dsG3_1_2026 --array /data/proc/MIXSED2026.array.yaml\n"
            "  # see the file names first\n"
            "  oceanarray export dsG3_1_2026 --proc-dir /data/proc -n\n"
            "  # instruments only, replacing earlier files\n"
            "  oceanarray export dsG3_1_2026 --proc-dir /data/proc --no-grid --force"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "mooring",
        help="The <name>.mooring.yaml, its folder, or a mooring name with --proc-dir.",
    )
    _add_dir_args(p, raw_needed=False)
    p.add_argument(
        "--array",
        type=Path,
        default=None,
        metavar="YAML",
        help="The <array>.array.yaml; supplies site_code and project.",
    )
    p.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        dest="output_dir",
        metavar="DIR",
        help="Where to write the files (default: <mooring folder>/oceansites).",
    )
    p.add_argument("--no-grid", action="store_true", help="Leave the grid out.")
    p.add_argument(
        "--force", action="store_true", help="Replace OceanSITES files that exist."
    )
    p.add_argument(
        "--overwrite",
        dest="force",
        nargs=0,
        action=DeprecatedAlias,
        new="--force",
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Convert and list the files that would be written; write nothing.",
    )
    p.set_defaults(func=cmd_export)
