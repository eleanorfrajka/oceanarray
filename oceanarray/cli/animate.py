"""``oceanarray animate`` — animated hodograph GIFs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from oceanarray import paths
from oceanarray.cli._common import (
    _MOORING_HELP,
    _add_dir_args,
    _add_only,
    _mooring_location,
)
from oceanarray.utilities import _status


def cmd_animate(args: argparse.Namespace) -> int:
    """Write animated hodograph GIF(s) for Aquadopp current-meter instruments.

    A hodograph plots the tip of the horizontal velocity vector (east vs north
    component) over time, showing how current speed and direction evolve.  The
    animation highlights rotary motion such as tidal ellipses or eddy passages.

    Requires ``east_velocity`` and ``north_velocity`` in the NetCDF file, which
    means stage 3 (BEAM → ENU coordinate rotation) must have completed for the
    target instrument.  A low-pass filter can be applied via ``--lp-days`` to
    separate sub-inertial (eddy/mean) flow from tidal and near-inertial signals.

    Requires the Pillow package (``pip install Pillow``); will fail at import time
    if Pillow is not installed.
    """
    import matplotlib

    matplotlib.use("Agg")
    import xarray as xr

    from ..exceptions import MooringError
    from ..pipeline import _resolve_mooring
    from ..plotters import animate_hodograph
    from ..utilities import instrument_entries

    location = _mooring_location(args)
    try:
        cfg, proc_dir = _resolve_mooring(location, None, "animate")
    except MooringError as e:
        _status("error", str(e))
        return 1
    wanted = {paths.safe_serial(s) for s in args.serials} if args.serials else None

    # Each listed instrument's stage-3 file, else its stage-2 file, from the description.
    candidates: list[tuple[str, Path]] = []
    for entry in instrument_entries(cfg):
        if not isinstance(entry, dict) or entry.get("skip"):
            continue
        serial = paths.safe_serial(entry.get("serial", ""))
        if wanted is not None and serial not in wanted:
            continue
        instr = entry.get("instrument", "unknown")
        found = [
            p
            for n in (3, 2)
            if (p := paths.stage_file(proc_dir, cfg["name"], instr, serial, n)).exists()
        ]
        if found:
            candidates.append((f"{cfg['name']}_{serial}", found[0]))
        elif wanted is not None:
            print(
                f"  WARNING: no stage-2 or stage-3 file for serial {serial} in {proc_dir}"
            )
    listed = {
        paths.safe_serial(e.get("serial", ""))
        for e in instrument_entries(cfg)
        if isinstance(e, dict)
    }
    for serial in sorted((wanted or set()) - listed):
        print(f"  WARNING: serial {serial} is not in {cfg['name']}'s description")
    if not candidates:
        print(f"  No processed files found under {proc_dir}")
        return 1

    if args.output_dir and args.output_dir.endswith(".gif") and not args.output:
        # Before 0.6, -o was the GIF file; it is the output directory now.
        print(
            "oceanarray: -o FILE.gif is now --output FILE.gif (-o is the output "
            "directory, as on report, run and plot).",
            file=sys.stderr,
        )
        args.output, args.output_dir = args.output_dir, None
    n_ok = 0
    for stem, nc in candidates:
        if args.output and len(candidates) == 1:
            out = Path(args.output)
        else:
            where = Path(args.output_dir) if args.output_dir else nc.parent
            where.mkdir(parents=True, exist_ok=True)
            out = where / f"{stem}_hodograph.gif"

        _status("section", f"Animate: {nc.name}")
        try:
            import numpy as np
            import pandas as pd

            ds = xr.open_dataset(nc, decode_timedelta=False)
            t = ds["time"].values
            t0_str = pd.Timestamp(t[0]).strftime("%Y-%m-%d %H:%M")
            t1_str = pd.Timestamp(t[-1]).strftime("%Y-%m-%d %H:%M")
            n_days = float((t[-1] - t[0]) / np.timedelta64(1, "D"))
            if len(t) > 1:
                dt_s = float(np.median(np.diff(t) / np.timedelta64(1, "s")))
                step_n = max(1, int(round(args.frame_hours * 3600.0 / dt_s)))
                n_exp = int(np.ceil(len(t) / step_n))
            else:
                dt_s = float("nan")
                n_exp = 1
            print(
                f"  Data:   {t0_str} → {t1_str}  ({n_days:.1f} days, "
                f"{len(t)} records, dt={dt_s:.0f} s)"
            )
            print(f"  Frames: ~{n_exp}  ({args.frame_hours:.0f}-h steps)")

            result = animate_hodograph(
                ds,
                out,
                u_var=args.u_var,
                v_var=args.v_var,
                lp_days=args.lp_days,
                smooth_hours=args.smooth_hours,
                frame_hours=args.frame_hours,
                fps=args.fps,
                dpi=args.dpi,
            )
            ds.close()
        except Exception as exc:  # noqa: BLE001  — I/O boundary; report and continue
            print(f"  ERROR: {exc}")
            continue

        if result is None:
            print(
                f"  Skipped: {nc.name} — no {args.u_var}/{args.v_var} variables "
                "or pillow writer unavailable"
            )
        else:
            print(f"  Saved: {result}")
            n_ok += 1

    return 0 if n_ok > 0 else 1


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``animate`` subcommand to *sub*."""
    p_animate = sub.add_parser(
        "animate",
        help="Write an animated hodograph GIF for Aquadopp instrument(s).",
    )
    p_animate.add_argument("mooring", help=_MOORING_HELP)
    _add_dir_args(p_animate, raw_needed=False)
    _add_only(
        p_animate,
        "Serial number(s) to animate (default: all instruments with velocity data)",
    )
    p_animate.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Output GIF path when one instrument is animated "
        "(default: <name>_<serial>_hodograph.gif in --output-dir)",
    )
    p_animate.add_argument(
        "-o",
        "--output-dir",
        dest="output_dir",
        default=None,
        metavar="DIR",
        help="Directory for the GIFs (default: beside each instrument's stage file)",
    )
    p_animate.add_argument(
        "--u-var",
        default="east_velocity",
        dest="u_var",
        metavar="VAR",
        help="Eastward velocity variable name (default: east_velocity)",
    )
    p_animate.add_argument(
        "--v-var",
        default="north_velocity",
        dest="v_var",
        metavar="VAR",
        help="Northward velocity variable name (default: north_velocity)",
    )
    p_animate.add_argument(
        "--lp-days",
        type=float,
        default=4.0,
        dest="lp_days",
        metavar="DAYS",
        help="Low-pass window for eddy-component removal in days (default: 4.0)",
    )
    p_animate.add_argument(
        "--smooth-hours",
        type=float,
        default=3.0,
        dest="smooth_hours",
        metavar="HOURS",
        help="Tukey smoothing window in hours applied to both panels (default: 3.0)",
    )
    p_animate.add_argument(
        "--frame-hours",
        type=float,
        default=6.0,
        dest="frame_hours",
        metavar="HOURS",
        help="Time step between frames in hours (default: 6.0 → one frame per "
        "quarter-day of deployment)",
    )
    p_animate.add_argument(
        "--fps",
        type=int,
        default=20,
        metavar="FPS",
        help="Frames per second in the output GIF (default: 20)",
    )
    p_animate.add_argument(
        "--dpi",
        type=int,
        default=100,
        metavar="DPI",
        help="Resolution of each frame in dots per inch (default: 100)",
    )
    p_animate.set_defaults(func=cmd_animate)
