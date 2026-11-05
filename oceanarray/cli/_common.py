"""Shared pieces of the oceanarray CLI: locating the mooring, the directory and shared flags."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from oceanarray import paths
from oceanarray.cli._deprecate import DeprecatedAlias
from oceanarray.pipeline import resolve_stage


def _usage_error(message: str) -> NoReturn:
    """Print *message* as an ERROR and exit 2, the exit status of a usage error."""
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(2)


def _resolve_dirs(
    args: argparse.Namespace,
    *,
    proc_required: bool = True,
) -> tuple[Path | None, Path | None]:
    """Resolve raw and proc directories from CLI args."""
    basedir = getattr(args, "basedir", None)
    raw_dir = getattr(args, "raw_dir", None)
    proc_dir = getattr(args, "proc_dir", None)

    if basedir:
        _usage_error(
            "--basedir has been removed. Use --raw-dir + --proc-dir "
            "(the mooring directory is now <proc-dir>/<mooring>, not "
            "<basedir>/moor/proc/<mooring>). See the migration guide: "
            f"docs/source/migration.rst or {paths.DOCS_URL}migration.html"
        )

    if not proc_dir:
        if proc_required:
            _usage_error("--proc-dir is required (and --raw-dir for stage 1).")
        return Path(raw_dir) if raw_dir else None, None
    return Path(raw_dir) if raw_dir else None, Path(proc_dir)


def _mooring_location(args: argparse.Namespace) -> Path:
    """Return the mooring's folder or description from the positional argument."""
    _resolve_dirs(args, proc_required=False)
    if getattr(args, "proc_dir", None):
        proc_root = Path(args.proc_dir)
        paths.require_current_layout(proc_root, args.mooring)
        folder = paths.mooring_proc_dir(proc_root, args.mooring)
        if not folder.is_dir():
            raise SystemExit(
                f"ERROR: no mooring folder {folder}: {args.mooring} is not a mooring name "
                "under --proc-dir."
            )
        return folder
    # Without --proc-dir the positional is a path: a *.mooring.yaml or the folder
    # holding one, under any name.
    given = Path(args.mooring)
    if not given.exists():
        raise SystemExit(
            f"ERROR: {args.mooring} is not a *.mooring.yaml or a mooring folder. Give "
            "its path, or a mooring name with --proc-dir."
        )
    return given


def _raw_dir(args: argparse.Namespace) -> Path | None:
    """Return ``--raw-dir`` as a path, or None."""
    raw = getattr(args, "raw_dir", None)
    return Path(raw) if raw else None


def _folder(location: Path) -> Path:
    """Return the mooring folder for a location from :func:`_mooring_location`."""
    return location if location.is_dir() else location.parent


def _parse_stage_token(value: str) -> int | str:
    """Convert a ``--stage`` CLI token to ``int`` or canonical ``str``."""
    coerced: int | str = int(value) if value.isdigit() else value
    try:
        resolve_stage(coerced)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return coerced


def _add_dir_args(p: argparse.ArgumentParser, raw_needed: bool = True) -> None:
    """Add ``--raw-dir``, ``--proc-dir`` and the hidden ``--basedir`` to *p*."""
    # --basedir is removed: it is accepted (undiscoverable in --help) only so we
    # can print a migration message instead of an argparse "unrecognized argument"
    # error.  See _resolve_dirs.  It is a plain suppressed argument -- a
    # single-member mutually-exclusive group added nothing and tripped a
    # usage-formatting assertion in the Python 3.11 argparse.
    p.add_argument(
        "--basedir",
        default=None,
        metavar="DIR",
        help=argparse.SUPPRESS,
    )
    if raw_needed:
        p.add_argument(
            "--raw-dir",
            default=None,
            dest="raw_dir",
            metavar="DIR",
            help="The cruise's raw-data folder; a mooring's raw files are in "
            "<raw-dir>/<name>/. Needed only when stage 1 runs.",
        )
    else:
        # Commands that read only processed NetCDF (plot, animate) take no --raw-dir.
        p.set_defaults(raw_dir=None)
    p.add_argument(
        "--proc-dir",
        default=None,
        dest="proc_dir",
        metavar="DIR",
        help="The cruise's processed-data folder; a mooring's files are in "
        "<proc-dir>/<name>/. With it, MOORING may be just the name.",
    )


def _add_only(p: argparse.ArgumentParser, text: str) -> None:
    """Add ``--only SERIAL...`` (and the old ``--serial`` spelling, hidden)."""
    p.add_argument(
        "--only", nargs="+", metavar="SERIAL", dest="serials", default=None, help=text
    )
    p.add_argument(
        "--serial",
        nargs="+",
        dest="serials",
        action=DeprecatedAlias,
        new="--only",
        help=argparse.SUPPRESS,
    )


def _add_stack_grid_args(
    p: argparse.ArgumentParser, *, from_description: bool = False, hidden: bool = False
) -> None:
    """Add the shared stack/grid tuning flags (--dt-seconds, --dp, --pmin, --pmax).

    *hidden* keeps them working but out of ``--help`` (``process``, where they only
    serve the deprecated ``--stage stack|grid``).
    """
    from ..pipeline._run import DEFAULTS

    # The flags spell the Python keywords; the time step carries its unit.
    flags = (
        ("--dt-seconds", "dt_seconds", int, "SECONDS", "Stack time step, in seconds"),
        ("--dp", "dp", float, "DBAR", "Grid spacing, in dbar"),
        ("--pmin", "pmin", float, "DBAR", "Shallowest grid level, in dbar"),
        ("--pmax", "pmax", float, "DBAR", "Deepest grid level, in dbar"),
    )
    for flag, key, kind, metavar, text in flags:
        default = DEFAULTS["stack" if key == "dt_seconds" else "grid"][key]
        where = (
            f"the description's {'stack' if key == 'dt_seconds' else 'grid'}: "
            f"block, else {default:g}"
            if from_description
            else f"{default:g}"
        )
        p.add_argument(
            flag,
            type=kind,
            dest=key,
            # None (run) lets the description's stack:/grid: blocks apply; process
            # keeps the fixed defaults.
            default=None if from_description else kind(default),
            metavar=metavar,
            help=argparse.SUPPRESS if hidden else f"{text} (default: {where})",
        )
    p.add_argument(
        "--dt",
        type=int,
        dest="dt_seconds",
        action=DeprecatedAlias,
        new="--dt-seconds",
        help=argparse.SUPPRESS,
    )


def _add_shared_report_args(p: argparse.ArgumentParser) -> None:
    """Add the report-location and grid-isopycnal arguments shared by report/run."""
    p.add_argument(
        "-o",
        "--output-dir",
        default=None,
        dest="outdir",
        metavar="DIR",
        help="Directory for the HTML report (default: {proc-dir}/{mooring}/report/)",
    )
    p.add_argument(
        "--report-dir",
        default=None,
        dest="report_dir",
        metavar="DIR",
        help="Central directory for all mooring reports.  Each mooring's pages are "
        "written to DIR/{mooring}/ instead of proc/{mooring}/report/, making the "
        "whole report tree portable.",
    )
    p.add_argument(
        "--sig-level",
        nargs="+",
        type=float,
        default=None,
        metavar="SIG",
        dest="sig_level",
        help="σ₀ target values (kg m⁻³, referenced to 0 dbar) for isopycnal "
        "height-above-seabed tracking in the grid report.  Pass one or more "
        "values; they are sorted before use.  Example: --sig-level 27.5 27.7 27.9  "
        "(default: 27.7).",
    )


_MOORING_HELP = (
    "The mooring: its <name>.mooring.yaml file or its folder (any name), or, with "
    "--proc-dir, its name, e.g. dsG3_1_2026 (the folder <proc-dir>/<name>)"
)


def _print_stage_plan(
    args: argparse.Namespace, location: Path, wanted: list[int], title: str
) -> tuple[str, Path] | None:
    """Print each stage file *wanted* stages would write; write nothing."""
    from oceanarray import logger
    from oceanarray import mooring as _mooring
    from oceanarray.exceptions import MooringError
    from oceanarray.instruments import instrument_entries
    from oceanarray.reports._verb import (
        _resolve_description_file,
        _resolve_name_and_dir,
    )

    try:
        name, moor_dir = _resolve_name_and_dir(location, None)
    except MooringError as e:
        logger.status("error", str(e))
        return None
    cfg = (
        _mooring._parse(_resolve_description_file(location, _folder(location), name))
        or {}
    )
    only = {paths.safe_serial(s) for s in getattr(args, "serials", None) or []}

    def _state(path: Path, n: int) -> str:
        """Return what the stage would do with *path*."""
        if not path.exists():
            return "new"
        if args.force:
            return "rewrite"
        return "keep, unless older than its raw file" if n == 1 else "keep"

    logger.status("section", f"{title} (dry run): {name}")
    # Interpolated-pressure targets can be named only when the stage-2 files exist.
    targets = _pressure_targets(cfg, moor_dir) if 3 in wanted else set()
    for n in wanted:
        print(f"Stage {n}:")
        for entry in instrument_entries(cfg):
            if not isinstance(entry, dict) or entry.get("skip"):
                continue
            serial = paths.safe_serial(entry.get("serial", ""))
            if only and serial not in only:
                continue
            f = paths.stage_file(
                moor_dir, name, entry.get("instrument", "unknown"), serial, n
            )
            note = "; pressure interpolated" if n == 3 and serial in targets else ""
            print(f"  {f}  ({_state(f, n)}{note})")
    return name, moor_dir


def _pressure_targets(cfg: dict, moor_dir: Path) -> set[str]:
    """Return the serials stage 3 would give interpolated pressure, from the stage-2 files on disk."""
    from oceanarray.pipeline.read_mooring import read_mooring
    from oceanarray.pipeline.stage3 import (
        _build_instrument_table,
        _split_pressure_sources,
    )

    try:
        datasets = read_mooring(cfg, stage=2, allow_missing=True, mooring_dir=moor_dir)
        _, targets = _split_pressure_sources(_build_instrument_table(datasets, cfg))
    except Exception:  # noqa: BLE001  — a preview: no stage-2 files, or unreadable ones
        return set()
    return {str(t["serial"]) for t in targets}
