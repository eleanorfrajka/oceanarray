"""``oceanarray report`` — build a mooring's report pages, an array index or a recovery table."""

from __future__ import annotations

import argparse
from pathlib import Path

from oceanarray import paths
from oceanarray.cli._common import (
    _MOORING_HELP,
    _add_dir_args,
    _add_only,
    _add_shared_report_args,
    _folder,
    _mooring_location,
    _OldFlag,
    _parse_dirs,
)
from oceanarray.utilities import _status


def cmd_report(args: argparse.Namespace) -> int:
    """Generate HTML reports for a mooring or an array, through :func:`oceanarray.report`.

    Without ``--array``: the mooring summary page, plus ``--instruments``,
    ``--stack`` and ``--grid`` pages (``--all``: all of them and the PDF).
    ``--cruise-table`` adds the recovery table; ``--pdf`` the combined PDF.

    With ``--array``: the positional argument is an ``*.array.yaml`` path and the
    array index linking each mooring's report is built; the other report flags
    are ignored.
    """
    from ..exceptions import MooringError
    from ..reports import report

    raw_dir, proc_root = _parse_dirs(args, proc_required=False)
    report_dir = getattr(args, "report_dir", None)
    outdir = getattr(args, "outdir", None)

    if getattr(args, "array", False):
        if getattr(args, "pdf", False) or getattr(args, "all_reports", False):
            _status(
                "error",
                "--pdf is not supported in --array mode; the array index is "
                "HTML-only. Run 'report MOORING --pdf' per mooring instead.",
            )
        if outdir:
            _status(
                "error",
                "-o/--output-dir is ignored in --array mode; the array index "
                "goes to the tree root. Use --report-dir to set it.",
            )
        try:
            report(
                args.mooring,
                kind="array",
                proc_dir=proc_root,
                report_dir=report_dir,
                force=args.force,
            )
        except MooringError as e:
            _status("error", str(e))
            return 1
        return 0

    # Array mode treats the positional as a YAML path, not a mooring name, so the
    # per-mooring layout check applies only here.
    moor_dir = _mooring_location(args)
    all_reports = getattr(args, "all_reports", False)
    serials = args.serials
    do_instruments = all_reports or getattr(args, "instruments", False) or bool(serials)
    do_stack = all_reports or getattr(args, "stack", False)
    do_grid = all_reports or getattr(args, "grid", False)
    want_pdf = getattr(args, "pdf", False) or all_reports

    if getattr(args, "dry_run", False):
        return _report_dry_run(
            args,
            moor_dir,
            outdir,
            report_dir,
            do_instruments,
            do_stack,
            do_grid,
            want_pdf,
        )

    try:
        page = report(
            moor_dir,
            raw_dir=raw_dir,
            out_dir=outdir,
            report_dir=report_dir,
            instruments=do_instruments,
            stack=do_stack,
            grid=do_grid,
            serials=serials,
            sig_level=getattr(args, "sig_level", None),
            force=args.force,
            skip_existing=getattr(args, "skip_existing", False),
        )
        if getattr(args, "cruise_table", False):
            report(
                moor_dir,
                kind="recovery",
                out_dir=outdir,
                report_dir=report_dir,
                force=args.force,
            )
    except MooringError as e:
        _status("error", str(e))
        return 1
    if want_pdf:
        from ..reports._verb import _combine_pdf, _name_and_dir

        name, _ = _name_and_dir(moor_dir, None)
        try:
            _combine_pdf(name, page.parent, getattr(args, "pdf_dir", None))
        except MooringError as e:
            # An explicit --pdf that cannot be honoured is a failure; a PDF only
            # implied by --all is best-effort, so the HTML result stands.
            _status("error", str(e))
            if getattr(args, "pdf", False):
                return 1
    return 0


def _report_dry_run(
    args: argparse.Namespace,
    moor_dir: Path,
    outdir: str | None,
    report_dir: str | None,
    do_instruments: bool,
    do_stack: bool,
    do_grid: bool,
    want_pdf: bool,
) -> int:
    """Print the pages a report run would write, at the paths it would write them to."""
    from oceanarray import mooring as _mooring
    from oceanarray.reports import report_paths
    from oceanarray.reports._verb import (
        _description_file,
        _name_and_dir,
        _recovery_path,
    )
    from oceanarray.utilities import instrument_entries

    from ..exceptions import MooringError

    _status("section", f"Report (dry run): {args.mooring}")
    try:
        name, _ = _name_and_dir(moor_dir, None)
    except MooringError as e:
        _status("error", str(e))
        return 1
    where = report_paths(
        moor_dir,
        out_dir=outdir,
        report_dir=report_dir,
        pdf_dir=getattr(args, "pdf_dir", None),
    )

    def _state(p: Path) -> str:
        """Return whether *p* already exists, for the listing."""
        return "exists" if p.exists() else "new"

    print(f"Summary:  {where['summary']}  ({_state(where['summary'])})")
    if do_stack:
        print(f"Stack:    {where['stack']}  ({_state(where['stack'])})")
    if do_grid:
        print(f"Grid:     {where['grid']}  ({_state(where['grid'])})")
    yaml_path = _description_file(moor_dir, _folder(moor_dir), name)
    if do_instruments and yaml_path.exists():
        # A dry run is a preview — never raise on a half-filled config. Show the
        # validation issues (as `validate` would), then list whatever the file
        # parses to; only a file that is not YAML at all stops the listing.
        cfg = _mooring._parse(yaml_path)
        if cfg is None:
            print(f"  (cannot preview instruments: {yaml_path} is not valid YAML)")
        else:
            for issue in _mooring.check(yaml_path):
                print(f"  {issue.level.lower()}: {issue.message}")
            only = {paths.safe_serial(s) for s in args.serials or []}
            for entry in instrument_entries(cfg):
                if not isinstance(entry, dict):
                    continue
                serial = paths.safe_serial(entry.get("serial", ""))
                if only and serial not in only:
                    continue
                instr_type = entry.get("instrument", "unknown")
                p = where["instrument_dir"] / f"{name}_{serial}_report.html"
                print(
                    f"  Instrument {instr_type:12s} s/n {serial:8s}  {p}  ({_state(p)})"
                )
    if getattr(args, "cruise_table", False):
        table = _recovery_path(name, _folder(moor_dir), outdir, report_dir)
        print(f"Recovery: {table}  ({_state(table)})")
    if want_pdf:
        print(f"PDF:      {where['pdf']}  (combined from the HTML pages above)")
    return 0


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``report`` subcommand to *sub*."""
    p_report = sub.add_parser(
        "report",
        help="Generate a mooring recovery HTML report.",
    )
    p_report.add_argument("mooring", help=_MOORING_HELP)
    _add_dir_args(p_report, raw_needed=True)
    _add_shared_report_args(p_report)
    p_report.add_argument(
        "--force", action="store_true", help="Overwrite existing report"
    )
    p_report.add_argument(
        "--instruments",
        action="store_true",
        default=False,
        help="Also generate per-instrument HTML pages (slow; skipped by default)",
    )
    p_report.add_argument(
        "--grid",
        action="store_true",
        default=False,
        help="Also generate the gridded-data report page (requires _grid.nc)",
    )
    p_report.add_argument(
        "--stack",
        action="store_true",
        default=False,
        help="Also generate the stacked-data report page with pressure and T time series (requires _stack.nc)",
    )
    _add_only(
        p_report,
        "Generate per-instrument page(s) for these serial number(s) only "
        "(implies --instruments for the listed serials)",
    )
    p_report.add_argument(
        "--all",
        action="store_true",
        default=False,
        dest="all_reports",
        help="Generate all report pages: equivalent to --stack --grid --instruments "
        "--pdf (also builds the combined PDF)",
    )
    p_report.add_argument(
        "--pdf",
        action="store_true",
        default=False,
        help="Combine the generated HTML report pages into a single A4 PDF "
        "({mooring}_report.pdf), in reading order summary → instruments → stack "
        "→ grid.  Requires the 'pdf' extra: pip install oceanarray[pdf].  "
        "Implied by --all.",
    )
    p_report.add_argument(
        "--pdf-dir",
        dest="pdf_dir",
        default=None,
        metavar="DIR",
        help="Write the combined PDF to DIR/{mooring}_report.pdf instead of beside "
        "the HTML pages, so every mooring's PDF collects in one shareable directory "
        "(created if needed).  Only affects PDF placement; still needs --pdf or --all "
        "to build the PDF at all.",
    )
    p_report.add_argument(
        "--array",
        action="store_true",
        default=False,
        help="Treat the positional argument as an array YAML path (name ending "
        ".array.yaml) and generate an array-level HTML index linking all mooring "
        "reports.  --report-dir is the output root for the index.",
    )
    p_report.add_argument(
        "--cruise-table",
        action="store_true",
        default=False,
        dest="cruise_table",
        help="Generate a standalone, print-optimised HTML recovery table for use in "
        "cruise reports (one per mooring: {mooring}_recovery_table.html).",
    )
    p_report.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        dest="dry_run",
        default=False,
        help="Show which report files would be generated (no files written).",
    )
    p_report.add_argument(
        "--skip-existing",
        action="store_true",
        dest="skip_existing",
        default=False,
        help="Skip any output file that already exists, regardless of source mtime "
        "(old behaviour; use --force to always regenerate).",
    )
    # The short spellings went in 0.6 (family rule: only -o, -n and -v are short);
    # accepted until 0.7.0 with a note.
    for short, dest, new in (
        ("-i", "instruments", "--instruments"),
        ("-g", "grid", "--grid"),
        ("-s", "stack", "--stack"),
        ("-A", "all_reports", "--all"),
    ):
        p_report.add_argument(
            short, dest=dest, nargs=0, action=_OldFlag, new=new, help=argparse.SUPPRESS
        )
    p_report.set_defaults(func=cmd_report)
