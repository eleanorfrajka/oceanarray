"""``oceanarray plot`` — a multi-instrument overview plot."""

from __future__ import annotations

import argparse
from pathlib import Path

from oceanarray.cli._common import (
    _MOORING_HELP,
    _add_dir_args,
    _folder,
    _mooring_location,
)
from oceanarray.cli._deprecate import DeprecatedAlias


def cmd_plot(args: argparse.Namespace) -> int:
    """Generate a multi-instrument mooring overview plot.

    Reads the best available processed NetCDF (stage3 preferred, falling back to
    stage2) for all instruments and produces a scatter/line plot with a user-chosen
    y-axis variable (default: temperature) and an optional colour variable.

    When ``--output`` is given the plot is saved to that filename in the proc
    directory (or ``--output-dir`` if specified).  Without ``--output``, the plot
    is displayed interactively.
    """
    from ..plotters import draw_mooring_timeseries

    folder = _folder(_mooring_location(args))

    save_path = None
    if args.output:
        out_dir = Path(args.output_dir) if args.output_dir else folder
        save_path = out_dir / args.output

    try:
        draw_mooring_timeseries(
            proc_dir=folder.parent,
            mooring=folder.name,
            var_y=args.var_y,
            var_color=args.var_color,
            markersize=args.markersize,
            colormap=args.colormap,
            downsample_seconds=args.downsample,
            save_path=save_path,
            show=args.show or save_path is None,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}")
        return 1

    if save_path:
        print(f"Saved: {save_path}")
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``plot`` subcommand to *sub*."""
    p_plot = sub.add_parser(
        "plot",
        help="Plot one variable of every instrument on a mooring in one figure.",
        description=(
            "Read each instrument's stage-3 file (stage 2 where there is none) and plot\n"
            "one variable against time for all of them, as lines or, with --var-color,\n"
            "as points coloured by a second variable. Shows the figure (--show) or saves\n"
            "it (--output / -o)."
        ),
        epilog=(
            "Examples:\n"
            "  # temperature of every instrument, saved next to the stage files\n"
            "  oceanarray plot /data/proc/dsG3_1_2026 --output overview.png\n"
            "  # pressure coloured by temperature, on screen\n"
            "  oceanarray plot dsG3_1_2026 --proc-dir /data/proc --var-y pressure "
            "--var-color temperature --show"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_plot.add_argument("mooring", help=_MOORING_HELP)
    _add_dir_args(p_plot, raw_needed=False)
    p_plot.add_argument(
        "--var-y",
        dest="var_y",
        default="temperature",
        help="Variable on y-axis (default: temperature)",
    )
    p_plot.add_argument(
        "--var-color",
        dest="var_color",
        default=None,
        help="Variable for scatter colour; omit for line plot",
    )
    for old, new in (("--var_y", "--var-y"), ("--var_color", "--var-color")):
        p_plot.add_argument(
            old,
            dest=new[2:].replace("-", "_"),
            action=DeprecatedAlias,
            new=new,
            help=argparse.SUPPRESS,
        )
    p_plot.add_argument(
        "--colormap",
        default=None,
        help=f"Matplotlib colormap (default: {__import__('oceanarray.config.parameters', fromlist=['DEFAULT_COLORMAP']).DEFAULT_COLORMAP})",
    )
    p_plot.add_argument(
        "--markersize",
        type=float,
        default=None,
        metavar="PTS2",
        help="Scatter marker size in points² (default 4). Only used in scatter mode.",
    )
    p_plot.add_argument(
        "--downsample-seconds",
        type=int,
        default=None,
        dest="downsample",
        metavar="SECONDS",
        help="Resample interval in seconds (default: 120)",
    )
    p_plot.add_argument(
        "--downsample",
        type=int,
        dest="downsample",
        action=DeprecatedAlias,
        new="--downsample-seconds",
        help=argparse.SUPPRESS,
    )
    p_plot.add_argument(
        "--output",
        default=None,
        metavar="FILENAME",
        help="Base filename for saved figure (e.g. overview.png); "
        "combined with --output-dir if given",
    )
    p_plot.add_argument(
        "-o",
        "--output-dir",
        default=None,
        metavar="DIR",
        help="Directory for saved figure (default: mooring proc dir)",
    )
    p_plot.add_argument(
        "--show",
        action="store_true",
        help="Display the figure interactively (works alongside --output)",
    )
    p_plot.set_defaults(func=cmd_plot)
