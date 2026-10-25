"""Command-line interface for oceanarray processing."""

import argparse
import sys
from pathlib import Path

from . import paths
from ._version import __version__
from .pipeline import STAGES, resolve_stage
from .utilities import _status


def _parse_dirs(
    args: "argparse.Namespace",
) -> "tuple[Path | None, Path]":
    """Resolve raw and proc directories from CLI args.

    Returns
    -------
    raw_dir : Path or None
        Cruise-level raw directory (``--raw-dir``). None when a command does not
        read raw files (stack, grid, report).
    proc_root : Path
        Cruise-level processed output directory.

    Raises
    ------
    SystemExit
        If ``--basedir`` is supplied (removed; prints a migration message), or if
        ``--proc-dir`` is not given.

    """
    basedir = getattr(args, "basedir", None)
    raw_dir = getattr(args, "raw_dir", None)
    proc_dir = getattr(args, "proc_dir", None)

    if basedir:
        raise SystemExit(
            "ERROR: --basedir has been removed. Use --raw-dir + --proc-dir "
            "(the mooring directory is now <proc-dir>/<mooring>, not "
            "<basedir>/moor/proc/<mooring>). See the migration guide: "
            f"docs/source/migration.rst or {paths.DOCS_URL}migration.html"
        )

    if not proc_dir:
        raise SystemExit("ERROR: --proc-dir is required (and --raw-dir for stage 1).")
    return Path(raw_dir) if raw_dir else None, Path(proc_dir)


def _parse_dirs_checked(
    args: "argparse.Namespace",
) -> "tuple[Path | None, Path]":
    """Parse dirs and reject an old-layout ``--proc-dir`` for a mooring command.

    Like :func:`_parse_dirs`, but also runs :func:`paths.require_current_layout`
    on ``<proc-dir>/<args.mooring>`` so a legacy ``moor/proc`` (or ``proc``)
    directory raises the migration error up front rather than failing later with
    "no files found".  Use for every mooring-scoped subcommand; ``report --array``
    is the exception (its positional is a YAML path, not a mooring name).
    """
    raw_dir, proc_root = _parse_dirs(args)
    paths.require_current_layout(proc_root, args.mooring)
    return raw_dir, proc_root


def _print_report(proc_dir: Path) -> None:
    """Print a per-instrument summary of stage1/stage2/stage3 NetCDF files.

    *proc_dir* is the mooring-level processed directory (``<proc-dir>/<mooring>``).
    For each instrument, reports: serial number, record count (raw from stage1 vs
    processed from stage2/stage3), time span, nominal depth, sampling interval, and
    which geophysical variables are present (temperature, salinity, velocity, etc.).
    """
    import datetime

    import numpy as np
    import xarray as xr

    # Collect all processed files; prefer _stage3 > _use as the "best" file per serial
    use_files = sorted(proc_dir.rglob(paths.stage_glob(2)))
    stage3_files = {
        f.name.replace(paths.stage_suffix(3), ""): f
        for f in proc_dir.rglob(paths.stage_glob(3))
    }

    if not use_files and not stage3_files:
        print("  No processed files found.")
        return

    by_instrument: dict = {}
    for nc in use_files:
        instrument = nc.parent.name
        by_instrument.setdefault(instrument, []).append(nc)

    for instrument, files in sorted(by_instrument.items()):
        print(f"\n  {instrument}")
        for nc in sorted(files):
            # Use _stage3.nc for reporting if it exists
            stem = nc.name.replace(paths.stage_suffix(2), "")
            best_nc = stage3_files.get(stem, nc)
            stage_label = "stage3" if best_nc != nc else "use"
            try:
                ds = xr.open_dataset(best_nc, decode_timedelta=False)
                n_rec = len(ds.time)
                t0 = str(ds.time.values[0])[:16].replace("T", " ")
                t1 = str(ds.time.values[-1])[:16].replace("T", " ")
                serial = ds["serial_number"].item() if "serial_number" in ds else "?"
                depth = (
                    f"{ds['InstrDepth'].item():.0f} m" if "InstrDepth" in ds else "?"
                )
                vars_present = [
                    v
                    for v in (
                        "temperature",
                        "conductivity",
                        "pressure",
                        "east_velocity",
                        "north_velocity",
                        "up_velocity",
                        "velocity_beam1",
                    )
                    if v in ds.data_vars
                ]
                if len(ds.time) > 1:
                    dt_s = np.median(
                        np.diff(ds.time.values).astype("timedelta64[s]").astype(float)
                    )
                    interval = f"{dt_s:.0f} s"
                else:
                    interval = "? s"
                ds.close()

                raw_nc = nc.with_name(
                    nc.name.replace(paths.stage_suffix(2), paths.stage_suffix(1))
                )
                try:
                    if raw_nc.exists():
                        ds_raw = xr.open_dataset(raw_nc, decode_timedelta=False)
                        n_raw = len(ds_raw.time)
                        ds_raw.close()
                        counts = f"{n_raw:>7} raw → {n_rec:>7} {stage_label}"
                    else:
                        counts = f"{'?':>7} raw → {n_rec:>7} {stage_label}"
                except Exception:  # noqa: BLE001  — intentional broad catch at I/O boundary
                    counts = f"{'HDF ERR':>7} raw → {n_rec:>7} {stage_label}"

                mtime = datetime.datetime.fromtimestamp(
                    best_nc.stat().st_mtime
                ).strftime("%Y-%m-%d %H:%M")
                print(
                    f"    s/n {serial:<8}  {counts}  {t0} → {t1}  depth: {depth}  dt: {interval}  processed: {mtime}  vars: {', '.join(vars_present)}"
                )
            except Exception as e:  # noqa: BLE001  — display helper; must not crash status output
                print(f"    {nc.name}: ERROR — {e}")


def cmd_process(args: argparse.Namespace) -> int:
    """Convert raw instrument files to processed NetCDF for one mooring.

    By default runs stages 1 (raw → CF-NetCDF), 2 (trim to deployment window +
    clock-drift correction) and 3 (QC flags, pressure interpolation, beam → ENU
    velocity rotation).  Pass ``--stage 1 2`` for the old stage-1-and-2-only
    behaviour.

    Optional side effects:
    - ``--report``: after processing, print a per-instrument record summary to
      the console (record counts, time spans, variables present).
    - ``--plot``: save a PNG overview plot for each instrument alongside the
      stage2 NetCDF file in the proc directory.
    """
    from .pipeline import _process_legacy as _process

    raw_dir, proc_root = _parse_dirs_checked(args)

    # If --stage was not explicitly set and --report is the only action, skip processing
    stages = args.stage if (args.stage is not None or not args.report) else []
    if stages is None:
        stages = [1, 2, 3]

    serials = args.serial or None
    overall_success = True

    if stages:
        if 1 in stages and raw_dir is None:
            raise SystemExit("ERROR: --raw-dir is required for stage 1.")
        overall_success = _process(
            args.mooring,
            stage=stages,
            proc_dir=proc_root,
            raw_dir=raw_dir,
            force=args.force,
            serials=serials,
            dt_seconds=args.dt,
            p_start=args.pmin,
            p_end=args.pmax,
            dp=args.dp,
            dry_run=getattr(args, "dry_run", False),
        )

    if args.report:
        _status("section", f"Record Summary: {args.mooring}")
        _print_report(proc_root / args.mooring)

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import xarray as xr

        from .plotters import plot_aquadopp_raw, plot_microcat_raw

        _status("section", f"Plotting: {args.mooring}")
        proc_dir = proc_root / args.mooring

        for nc in sorted((proc_dir / "microcat").glob(paths.stage_glob(2))):
            ds = xr.open_dataset(nc, decode_timedelta=False)
            plot_microcat_raw(ds, save_path=nc.with_suffix(".png"))
            print(f"Saved: {nc.with_suffix('.png')}")

        for nc in sorted((proc_dir / "aquadopp").glob(paths.stage_glob(2))):
            ds = xr.open_dataset(nc, decode_timedelta=False)
            plot_aquadopp_raw(ds, save_path=nc.with_suffix(".png"))
            print(f"Saved: {nc.with_suffix('.png')}")

    return 0 if overall_success else 1


def cmd_plot(args: argparse.Namespace) -> int:
    """Generate a multi-instrument mooring overview plot.

    Reads the best available processed NetCDF (stage3 preferred, falling back to
    stage2) for all instruments and produces a scatter/line plot with a user-chosen
    y-axis variable (default: pressure) and colour variable (default: temperature).

    When ``--output`` is given the plot is saved to that filename in the proc
    directory (or ``--output-dir`` if specified).  Without ``--output``, the plot
    is displayed interactively.
    """
    from pathlib import Path

    from .config import parameters as params
    from .plotters import plot_mooring_timeseries

    _, proc_root = _parse_dirs_checked(args)

    if args.colormap:
        params.DEFAULT_COLORMAP = args.colormap
    if args.downsample:
        params.DOWNSAMPLE_SECONDS = args.downsample

    save_path = None
    if args.output:
        out_dir = Path(args.output_dir) if args.output_dir else proc_root / args.mooring
        save_path = out_dir / args.output

    try:
        plot_mooring_timeseries(
            proc_dir=proc_root,
            mooring=args.mooring,
            var_y=args.var_y,
            var_color=args.var_color,
            markersize=args.markersize,
            save_path=save_path,
            show=args.show or save_path is None,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}")
        return 1

    if save_path:
        print(f"Saved: {save_path}")
    return 0


def _generate_mooring_report(
    mooring: str,
    *,
    proc_root: "Path",
    raw_dir: "Path | None",
    report_dir: "str | None",
    outdir: "str | None",
    sig_level: "list[float] | None",
    serials: "list[str] | None",
    force: bool,
    instruments: bool,
    grid: bool,
    stack: bool,
    skip_existing: bool = False,
) -> bool:
    """Generate the standard mooring report pages and return True on success.

    Shared by :func:`cmd_report` (single-mooring path) and :func:`cmd_run`.
    Applies the *sig_level* isopycnal-grid override for the duration of the call
    and restores the previous ``SIGMA_GRID`` afterwards, so batch callers in the
    same process do not inherit the mutation (not thread-safe).  Layout
    validation is the caller's responsibility.

    Parameters
    ----------
    mooring : str
        Mooring name.
    proc_root : Path
        Cruise-level processed output directory.
    raw_dir : Path or None
        Cruise-level raw directory, or None when unavailable.
    report_dir : str or None
        Central report directory; when set, pages go to ``report_dir/{mooring}/``.
    outdir : str or None
        Explicit output directory for this mooring's HTML, overriding the default.
    sig_level : list of float or None
        σ₀ targets for the grid report; None leaves the default ``SIGMA_GRID``.
    serials : list of str or None
        Restrict per-instrument pages to these serials (implies *instruments*).
    force : bool
        Overwrite existing output files.
    instruments, grid, stack : bool
        Whether to also build the per-instrument, grid, and stack pages.
    skip_existing : bool, optional
        Skip outputs that already exist regardless of source mtime.

    Returns
    -------
    bool
        True if report generation succeeded.

    """
    from .reports import MooringReport

    _sigma_restore: tuple | None = None
    if sig_level is not None:
        import numpy as _np

        from .config import parameters as params

        _sigma_restore = (params, params.SIGMA_GRID.copy())
        params.SIGMA_GRID = _np.array(sorted(sig_level))
    try:
        _status("section", f"Report: {mooring}")
        reporter = MooringReport(
            proc_dir=str(proc_root),
            raw_dir=str(raw_dir) if raw_dir else None,
            report_dir=report_dir,
        )
        return bool(
            reporter.generate(
                mooring,
                force=force,
                skip_existing=skip_existing,
                outdir=outdir,
                serials=serials,
                instruments=instruments,
                grid=grid,
                stack=stack,
            )
        )
    finally:
        if _sigma_restore is not None:
            _sigma_restore[0].SIGMA_GRID = _sigma_restore[1]


def cmd_report(args: argparse.Namespace) -> int:
    """Generate HTML quality-control reports for a mooring or an array.

    Without ``--array``: generates the mooring summary page (and optionally
    ``--instruments``, ``--stack``, ``--grid`` pages) for a single mooring.

    With ``--array``: the positional argument is treated as a path to an
    ``*.array.yaml`` file and an array-level HTML index is generated linking
    all mooring reports.  Other report flags are ignored in array mode.
    """
    from pathlib import Path

    raw_dir, proc_root = _parse_dirs(args)
    # Array mode treats the positional as a YAML path, not a mooring name, so the
    # per-mooring layout check does not apply there.
    if not getattr(args, "array", False):
        paths.require_current_layout(proc_root, args.mooring)

    report_dir = getattr(args, "report_dir", None)
    sig_level = getattr(args, "sig_level", None)

    if getattr(args, "dry_run", False):
        from oceanarray import mooring as _mooring
        from oceanarray.utilities import instrument_entries

        _status("section", f"Report (dry run): {args.mooring}")
        proc_dir = proc_root / args.mooring
        yaml_path = proc_dir / f"{args.mooring}.mooring.yaml"
        all_reports = getattr(args, "all_reports", False)
        do_instruments = all_reports or getattr(args, "instruments", False)
        do_stack = all_reports or getattr(args, "stack", False)
        do_grid = all_reports or getattr(args, "grid", False)
        mooring_html = proc_dir / f"{args.mooring}_report.html"
        print(
            f"Summary:  {mooring_html}  ({'exists' if mooring_html.exists() else 'new'})"
        )
        if do_stack:
            p = proc_dir / f"{args.mooring}_stack_report.html"
            print(f"Stack:    {p}  ({'exists' if p.exists() else 'new'})")
        if do_grid:
            p = proc_dir / f"{args.mooring}_grid_report.html"
            print(f"Grid:     {p}  ({'exists' if p.exists() else 'new'})")
        if do_instruments and yaml_path.exists():
            # A dry run is a preview — never raise on a half-filled config. Show
            # the validation issues (as `validate` would), then list whatever the
            # file parses to; only a file that is not YAML at all stops the listing.
            cfg = _mooring._parse(yaml_path)
            if cfg is None:
                print(f"  (cannot preview instruments: {yaml_path} is not valid YAML)")
            else:
                for issue in _mooring.check(yaml_path):
                    print(f"  {issue.level.lower()}: {issue.message}")
                for entry in instrument_entries(cfg):
                    if not isinstance(entry, dict):
                        continue
                    serial = str(entry.get("serial", "")).split(",")[0].strip()
                    instr_type = entry.get("instrument", "unknown")
                    p = proc_dir / "instrument" / f"{args.mooring}_{serial}_report.html"
                    print(
                        f"  Instrument {instr_type:12s} s/n {serial:8s}  {p.name}  ({'exists' if p.exists() else 'new'})"
                    )
        if getattr(args, "pdf", False) or all_reports:
            _html_dir = paths.resolve_report_dir(
                args.mooring, getattr(args, "outdir", None), report_dir, proc_root
            )
            pdf_path = paths.resolve_pdf_path(
                args.mooring, getattr(args, "pdf_dir", None), _html_dir
            )
            print(f"PDF:      {pdf_path}  (combined from the HTML pages above)")
        return 0

    if getattr(args, "array", False):
        from .reports._array import generate_array_report

        if getattr(args, "pdf", False) or getattr(args, "all_reports", False):
            _status(
                "error",
                "--pdf is not supported in --array mode; the array index is "
                "HTML-only. Run 'report MOORING --pdf' per mooring instead.",
            )
        if getattr(args, "outdir", None):
            _status(
                "error",
                "-o/--output-dir is ignored in --array mode; the array index "
                "goes to the tree root. Use --report-dir to set it.",
            )
        # Resolve the YAML path: try as-given first, then relative to proc_dir.
        _yaml_path = Path(args.mooring)
        if not _yaml_path.exists() and not _yaml_path.is_absolute():
            _yaml_path = proc_root / args.mooring
        _status("section", f"Array report: {_yaml_path.name}")
        result = generate_array_report(
            array_yaml_path=_yaml_path,
            proc_dir=proc_root,
            force=args.force,
            report_dir=Path(report_dir) if report_dir else None,
        )
        return 0 if result else 1

    serials = getattr(args, "serial", None)
    all_reports = getattr(args, "all_reports", False)
    result = _generate_mooring_report(
        args.mooring,
        proc_root=proc_root,
        raw_dir=raw_dir,
        report_dir=report_dir,
        outdir=getattr(args, "outdir", None),
        sig_level=sig_level,
        serials=serials,
        force=args.force,
        instruments=all_reports or args.instruments or bool(serials),
        grid=all_reports or args.grid,
        stack=all_reports or args.stack,
        skip_existing=getattr(args, "skip_existing", False),
    )
    if getattr(args, "cruise_table", False):
        from .reports._recovery_table import generate_recovery_table

        mooring_proc = proc_root / args.mooring
        out_dir = Path(
            getattr(args, "outdir", None)
            or (Path(report_dir) / args.mooring if report_dir else mooring_proc)
        )
        generate_recovery_table(
            mooring_name=args.mooring,
            proc_dir=mooring_proc,
            out_path=out_dir / f"{args.mooring}_recovery_table.html",
            force=args.force,
        )
    if getattr(args, "pdf", False) or all_reports:
        from .reports import combine_mooring_pdf

        # Combine reads the same directory generate() wrote to; both resolve it
        # through paths.resolve_report_dir so they can never drift.
        html_dir = paths.resolve_report_dir(
            args.mooring, getattr(args, "outdir", None), report_dir, proc_root
        )
        # --pdf-dir redirects the PDF to a shared directory; when unset it stays
        # beside the HTML.  Resolved identically to the dry-run preview above.
        pdf_path = paths.resolve_pdf_path(
            args.mooring, getattr(args, "pdf_dir", None), html_dir
        )
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            pdf_path = combine_mooring_pdf(html_dir, args.mooring, output_path=pdf_path)
            _status("file", str(pdf_path))
        except (ImportError, FileNotFoundError) as exc:
            # An explicit --pdf request that cannot be honoured is a failure.
            # When the PDF was only implied by --all, treat it as best-effort:
            # warn but keep the (successful) HTML result and do not fail, so
            # `report --all` still works on machines without the pdf extra.
            _status("error", str(exc))
            if getattr(args, "pdf", False):
                return 1
    return 0 if result else 1


def cmd_run(args: argparse.Namespace) -> int:
    """Run the complete processing pipeline for one mooring.

    Executes in order: stage 1 (raw → CF-NetCDF), stage 2 (trim + clock
    correction), stage 3 (QC + velocity rotation), stack (common time axis),
    grid (pressure interpolation), and all reports (summary, per-instrument,
    stack, grid).

    Stage failures are non-fatal: each subsequent stage runs regardless, except
    that a stack failure skips grid (grid reads stack.nc as its input).  The
    command returns exit code 1 if any step fails.

    The report step honours ``--output-dir``/``--report-dir`` (to redirect the
    HTML tree) and ``--sig-level`` (grid isopycnal targets); when unset it writes
    to the default ``{proc-dir}/{mooring}/report/`` with the default σ₀ grid.
    """
    from .pipeline import _process_legacy as _process

    raw_dir, proc_root = _parse_dirs_checked(args)

    if raw_dir is None:
        raise SystemExit("ERROR: --raw-dir is required for stage 1.")

    serials = args.serial or None

    overall_ok = _process(
        args.mooring,
        stage=None,
        proc_dir=proc_root,
        raw_dir=raw_dir,
        force=args.force,
        serials=serials,
        dt_seconds=args.dt,
        p_start=args.pmin,
        p_end=args.pmax,
        dp=args.dp,
    )

    paths.require_current_layout(proc_root, args.mooring)
    report_ok = _generate_mooring_report(
        args.mooring,
        proc_root=proc_root,
        raw_dir=raw_dir,
        report_dir=args.report_dir,
        outdir=args.outdir,
        sig_level=args.sig_level,
        serials=serials,
        force=args.force,
        instruments=True,
        grid=True,
        stack=True,
    )
    if not report_ok:
        overall_ok = False

    return 0 if overall_ok else 1


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate one or more mooring YAML files."""
    from .config.validation import print_validation_report

    all_ok = True
    for yaml_path in args.yaml:
        ok = print_validation_report(yaml_path)
        if not ok:
            all_ok = False
        print()

    return 0 if all_ok else 1


def cmd_stamp(args: argparse.Namespace) -> int:
    """Write the processing_stage marker into stage files written before it existed."""
    from .pipeline._stage import StageError
    from .pipeline.stamp import stamp_file

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

    from .plotters import animate_hodograph

    _, proc_root = _parse_dirs_checked(args)
    proc_dir = proc_root / args.mooring
    serials = args.serial or []

    # Build {stem: best_nc} — stage3 preferred over stage2
    stage3_map = {
        f.name.replace(paths.stage_suffix(3), ""): f
        for f in sorted(proc_dir.rglob(paths.stage_glob(3)))
    }

    if serials:
        candidates: list[tuple[str, Path]] = []
        for sn in serials:
            matches = [stem for stem in stage3_map if str(sn) in stem]
            if matches:
                candidates.extend((m, stage3_map[m]) for m in matches)
            else:
                stage2 = sorted(proc_dir.rglob(f"*{sn}*{paths.stage_suffix(2)}"))
                if stage2:
                    stem = stage2[0].name.replace(paths.stage_suffix(2), "")
                    candidates.append((stem, stage2[0]))
                else:
                    print(
                        f"  WARNING: no stage2/3 NC found for serial {sn} under {proc_dir}"
                    )
    else:
        # All stage3 files; fill in with stage2 where stage3 is absent
        seen: set[str] = set(stage3_map)
        candidates = list(stage3_map.items())
        for nc in sorted(proc_dir.rglob(paths.stage_glob(2))):
            stem = nc.name.replace(paths.stage_suffix(2), "")
            if stem not in seen:
                seen.add(stem)
                candidates.append((stem, nc))

    if not candidates:
        print(f"  No processed files found under {proc_dir}")
        return 1

    n_ok = 0
    for stem, nc in candidates:
        if args.output and len(candidates) == 1:
            out = Path(args.output)
        else:
            out = nc.parent / f"{stem}_hodograph.gif"

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


def cmd_list(args: argparse.Namespace) -> int:
    """Print allowed instrument types and file_type values for mooring YAML files."""
    from .config import parameters as params

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


def _stage_token(value: str) -> "int | str":
    """Convert a ``--stage`` CLI token to ``int`` or canonical ``str`` via :func:`resolve_stage`.

    Parameters
    ----------
    value : str
        Raw token from the command line.

    Returns
    -------
    int | str
        Integer stage number or the literal string ``'stack'`` / ``'grid'``.

    Raises
    ------
    argparse.ArgumentTypeError
        If *value* is not recognised by :func:`~oceanarray.pipeline.resolve_stage`.

    """
    coerced: int | str = int(value) if value.isdigit() else value
    try:
        resolve_stage(coerced)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return coerced


def _add_dir_args(p: "argparse.ArgumentParser", raw_needed: bool = True) -> None:
    """Add directory arguments to a subparser.

    Adds ``--raw-dir`` / ``--proc-dir``, plus a hidden ``--basedir`` flag that
    only exists to emit a migration message (see :func:`_parse_dirs`).  When
    *raw_needed* is False (e.g. for ``plot``, ``animate`` which operate on
    already-processed NetCDF files) ``--raw-dir`` is silently omitted and the
    argument defaults to None.  Stage 1 processing requires raw instrument files
    and therefore sets *raw_needed=True*.

    Parameters
    ----------
    p : argparse.ArgumentParser
        The subparser to add arguments to.
    raw_needed : bool
        Whether to add ``--raw-dir``.  Pass False for commands that do not
        read raw instrument data files (plot, animate).

    """
    # --basedir is removed: it is accepted (undiscoverable in --help) only so we
    # can print a migration message instead of an argparse "unrecognized argument"
    # error.  See _parse_dirs.  It is a plain suppressed argument -- a
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
            help="Cruise-level raw data directory (pipeline appends /{mooring}/). "
            "Required for stage 1.",
        )
    else:
        p.set_defaults(raw_dir=None)
    p.add_argument(
        "--proc-dir",
        default=None,
        dest="proc_dir",
        metavar="DIR",
        help="Cruise-level processed output directory (pipeline appends /{mooring}/).",
    )


def _add_stack_grid_args(p: "argparse.ArgumentParser") -> None:
    """Add the shared stack/grid tuning arguments (--dt, --dp, --pmin, --pmax).

    Used by ``process`` and ``run``, which drive the mooring-level stack and grid
    steps with identical defaults.

    Parameters
    ----------
    p : argparse.ArgumentParser
        The subparser to add arguments to.

    """
    p.add_argument(
        "--dt",
        type=int,
        default=60,
        metavar="SECONDS",
        help="Stack time-grid interval in seconds (default: 60)",
    )
    p.add_argument(
        "--dp",
        type=float,
        default=20.0,
        metavar="DBAR",
        help="Pressure grid spacing in dbar (default: 20)",
    )
    p.add_argument(
        "--pmin",
        type=float,
        default=200.0,
        metavar="DBAR",
        help="Shallowest pressure level for grid (default: 200)",
    )
    p.add_argument(
        "--pmax",
        type=float,
        default=1000.0,
        metavar="DBAR",
        help="Deepest pressure level for grid (default: 1000)",
    )


def _add_shared_report_args(p: "argparse.ArgumentParser") -> None:
    """Add the report-location and grid-isopycnal arguments shared by report/run.

    ``-o/--output-dir``, ``--report-dir``, and ``--sig-level`` are honoured by
    both the ``report`` and ``run`` subcommands to redirect the HTML report tree
    and set the grid isopycnal targets.

    Parameters
    ----------
    p : argparse.ArgumentParser
        The subparser to add arguments to.

    """
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


def cmd_init(args: argparse.Namespace) -> int:
    """Create a skeleton mooring YAML in {proc_dir}/{mooring}/{mooring}.mooring.yaml."""
    from oceanarray import mooring as _mooring

    out_path = Path(args.proc_dir) / args.mooring / f"{args.mooring}.mooring.yaml"
    if out_path.exists():
        print(f"ERROR: {out_path} already exists.")
        print("Rename or delete the existing file before generating a new stub.")
        return 1

    _mooring.save(_mooring.template(args.mooring), out_path)
    print(f"Wrote stub: {out_path}")
    print(
        "Edit the file to fill in metadata and instrument details, then delete unused clamp entries."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser for the oceanarray CLI."""
    epilog = (
        "Typical workflow:\n"
        "  oceanarray process MOORING --raw-dir /data/raw --proc-dir /data/proc --stage 1 2 3 stack grid\n"
        "  oceanarray report  MOORING --raw-dir /data/raw --proc-dir /data/proc --instruments --stack --grid\n"
        "\n"
        "Or in a single command:\n"
        "  oceanarray run MOORING --raw-dir /data/raw --proc-dir /data/proc --dp 20\n"
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
        help="Enable verbose output.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "init",
        help="Create a skeleton mooring YAML file.",
        description=(
            "Create {proc_dir}/{mooring}/{mooring}.mooring.yaml with commented template\n"
            "fields for all mandatory metadata and one example entry per instrument type.\n"
            "Edit the file to fill in real values and delete unused instrument blocks."
        ),
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

    p_process = sub.add_parser(
        "process",
        help="Run per-instrument processing stages 1-3 (raw→NetCDF, trim+clock, QC+ENU).",
        description=(
            "Run one or more per-instrument processing stages for a mooring.\n"
            "  Stage 1: read raw files → CF-NetCDF (_stage1.nc)\n"
            "  Stage 2: trim to deployment window + apply clock-drift correction (_stage2.nc)\n"
            "  Stage 3: QC flags, BEAM→ENU rotation, magnetic declination, derived vars (_stage3.nc)\n"
            "Default when --stage is omitted: stages 1, 2 and 3."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_process.add_argument("mooring", help="Mooring name, e.g. dsG3_1_2026")
    _add_dir_args(p_process, raw_needed=True)
    p_process.add_argument(
        "--stage",
        type=_stage_token,
        nargs="+",
        choices=[s.number if s.number is not None else s.name for s in STAGES],
        default=None,
        metavar="{1,2,3,stack,grid}",
        help="Stage(s) to run (default: 1 2 3). "
        "Use 'stack' and 'grid' for mooring-level steps.",
    )
    _add_stack_grid_args(p_process)
    p_process.add_argument(
        "--plot",
        action="store_true",
        help="Generate plots of processed microcat data after stage 2",
    )
    p_process.add_argument(
        "--report",
        action="store_true",
        help="Print a summary of processed records per instrument and serial number",
    )
    p_process.add_argument(
        "--serial",
        nargs="+",
        metavar="SERIAL",
        default=[],
        help="Process only instrument(s) with these serial number(s), e.g. --serial 14321 400118",
    )
    p_process.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output files (both stages skip existing files by default)",
    )
    p_process.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Do not actually modify any files; show what would be done (stage 3 only)",
    )
    p_process.set_defaults(func=cmd_process)

    p_report = sub.add_parser(
        "report",
        help="Generate a mooring recovery HTML report.",
    )
    p_report.add_argument("mooring", help="Mooring name, e.g. dsG3_1_2026")
    _add_dir_args(p_report, raw_needed=True)
    _add_shared_report_args(p_report)
    p_report.add_argument(
        "--force", action="store_true", help="Overwrite existing report"
    )
    p_report.add_argument(
        "--instruments",
        "-i",
        action="store_true",
        default=False,
        help="Also generate per-instrument HTML pages (slow; skipped by default)",
    )
    p_report.add_argument(
        "--grid",
        "-g",
        action="store_true",
        default=False,
        help="Also generate the gridded-data report page (requires _grid.nc)",
    )
    p_report.add_argument(
        "--stack",
        "-s",
        action="store_true",
        default=False,
        help="Also generate the stacked-data report page with pressure and T time series (requires _stack.nc)",
    )
    p_report.add_argument(
        "--serial",
        nargs="+",
        metavar="SN",
        default=None,
        help="Generate per-instrument page(s) for these serial number(s) only "
        "(implies --instruments for the listed serials)",
    )
    p_report.add_argument(
        "--all",
        "-A",
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
    p_report.set_defaults(func=cmd_report)

    p_run = sub.add_parser(
        "run",
        help="Full pipeline: stages 1-3, stack, grid, and all reports in one command.",
    )
    p_run.add_argument("mooring", help="Mooring name, e.g. dsG3_1_2026")
    _add_dir_args(p_run, raw_needed=True)
    p_run.add_argument(
        "--force", action="store_true", help="Overwrite existing files at every stage"
    )
    p_run.add_argument(
        "--serial",
        nargs="+",
        metavar="SN",
        default=[],
        help="Restrict to these serial number(s) for processing and per-instrument reports",
    )
    _add_stack_grid_args(p_run)
    _add_shared_report_args(p_run)
    p_run.set_defaults(func=cmd_run)

    p_validate = sub.add_parser(
        "validate",
        help="Validate mooring YAML configuration file(s).",
        description=(
            "Parse and validate one or more .mooring.yaml files.\n"
            "Checks instrument types, serial numbers, file_type values, HAB fields,\n"
            "deployment dates, and inline instrument conventions.\n"
            "Exits 0 if all files are valid, 1 if any file has errors or warnings."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_validate.add_argument("yaml", nargs="+", help="Path(s) to .mooring.yaml file(s)")
    p_validate.set_defaults(func=cmd_validate)

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

    p_plot = sub.add_parser("plot", help="Plot multi-instrument mooring overview.")
    p_plot.add_argument("mooring", help="Mooring name, e.g. dsG3_1_2026")
    _add_dir_args(p_plot, raw_needed=False)
    p_plot.add_argument(
        "--var_y",
        default="temperature",
        help="Variable on y-axis (default: temperature)",
    )
    p_plot.add_argument(
        "--var_color",
        default=None,
        help="Variable for scatter colour; omit for line plot",
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
        "--downsample",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Resample interval in seconds (default: 120)",
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

    p_animate = sub.add_parser(
        "animate",
        help="Write an animated hodograph GIF for Aquadopp instrument(s).",
    )
    p_animate.add_argument("mooring", help="Mooring name, e.g. dsG2_1_2026")
    _add_dir_args(p_animate, raw_needed=False)
    p_animate.add_argument(
        "--serial",
        nargs="+",
        metavar="SN",
        default=[],
        help="Serial number(s) to animate (default: all instruments with velocity data)",
    )
    p_animate.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="FILE",
        help="Output GIF path (only used when a single --serial is given; "
        "default: {stem}_hodograph.gif next to the NC file)",
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
    try:
        sys.exit(args.func(args))
    except paths.LegacyLayoutError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
