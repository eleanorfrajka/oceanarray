"""``oceanarray export`` — write a mooring's stage-3 files and grid as OceanSITES 1.4 files."""

from __future__ import annotations

import argparse
from pathlib import Path

from oceanarray.cli._common import _add_dir_args, _folder, _mooring_location


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
            overwrite=args.overwrite,
        )
    except (MooringError, OceanSitesError, FileExistsError) as e:
        print(f"ERROR: {e}")
        return 1
    for path in written:
        print(f"wrote {path}")
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
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "mooring",
        help="The *.mooring.yaml, its folder, or a mooring name with --proc-dir.",
    )
    _add_dir_args(p, raw_needed=False)
    p.add_argument(
        "--array",
        type=Path,
        default=None,
        metavar="YAML",
        help="The *.array.yaml; supplies site_code and project.",
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
    p.add_argument("--overwrite", action="store_true", help="Replace files that exist.")
    p.set_defaults(func=cmd_export)
