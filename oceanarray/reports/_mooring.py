"""Mooring summary HTML template and MooringReport orchestrator class."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from oceanarray import mooring as _mooring
from oceanarray import paths

from ..utilities import instrument_entries
from . import _figdebug
from ._env import render_template
from ._grid import generate_grid_page
from ._html_helpers import (
    _check_readable,
    _duration_str,
    _find_array_report_href,
    _fmt_dt,
    _instrument_report_exists,
    _load_pdf_b64,
    _parse_dt,
    _read_instrument_info,
    _read_qc_summary,
    _read_sensor_info,
    _read_timing_info,
    _resolve_clock,
    _resolve_diagram_pdf,
    _safe_serial,
    _should_skip,
    _stage_files,
    _status,
    make_fig_panel,
)
from ._instrument import generate_instrument_pages
from ._navs import mooring_nav
from ._plots import (
    _make_clock_check_b64,
    _make_knockdown_anomaly_b64,
    _make_knockdown_displacement_b64,
    _make_knockdown_hab_b64,
)
from ._stack import generate_stack_page
from ._vendored.page_model import Panel, Profile, Section, resolve

# ---------------------------------------------------------------------------
# Section manifest — mooring registry (rep/07)
#
# The context is the ``ctx`` dict ``_build_context`` already returns (instrument
# records + figures + timing/QC fields), so panels read it by key — the same
# dict-context pattern the instrument page uses.  Bespoke tables/prose render
# through html sub-templates (emitted ``|safe``); the four figure panels
# (clock-check + three knockdown) are bare-``.fig`` panels whose payload is the
# base64 PNG and whose caption is a plain-text data field.  The hand-typed
# section numbers ("2 —", "3 —", …) are dropped: the resolver numbers the
# rendered subset ``1..N`` automatically, which fixes the old off-by-one start.
# ---------------------------------------------------------------------------


#: Mooring figure captions, keyed by panel id — plain text, Unicode notation.
MOORING_CAPTIONS: dict[str, str] = {
    "clock_check": (
        "Clock alignment check. Overlaid temperature records zoomed to the first "
        "and last 30 minutes of the deployment. If instrument clocks are "
        "misaligned the temperature curves will appear shifted in time. Data are "
        "from stage 3 (or stage 2 if stage 3 is not yet available). Instruments "
        "with no temperature data are omitted."
    ),
    "knockdown_hab": (
        "Nominal HAB (x) vs. measured pressure (y). The dashed line shows "
        "expected pressure (water depth − HAB). Instruments below the line were "
        "knocked down. Interpolated pressure (QC flag 8) excluded."
    ),
    "knockdown_anomaly": (
        "IQR of pressure anomaly (measured − nominal) per instrument. Positive = "
        "deeper than design depth. Green < 100 dbar, yellow 100–200, amber "
        "200–300, red > 300 dbar."
    ),
    "knockdown_displacement": (
        "Estimated horizontal displacement (m) vs. measured pressure. Left: "
        "scatter per instrument; right: normalised 2-D density across all "
        "instruments. Displacement derived from the rigid-pendulum "
        "approximation: x = √(hab_nom² − hab_meas²)."
    ),
}


#: Mooring figure-panel factory (shared with the instrument page, bound to the
#: mooring caption dict).  The knockdown HAB and anomaly plots are drawn at
#: ``"half"``, so they carry ``slot="half"``.
_fig_panel = make_fig_panel(MOORING_CAPTIONS)


#: Mooring panel registry (dict-context: panels read ctx by key).
MOORING_PANELS: dict[str, Panel] = {
    "pipeline": Panel(
        "pipeline",
        render=lambda c: render_template(
            "_mooring_pipeline.html",
            instruments=c["instruments"],
            mooring_name=c["mooring_name"],
        ),
        kind="html",
    ),
    "instruments": Panel(
        "instruments",
        render=lambda c: render_template(
            "_mooring_instruments.html",
            instruments=c["instruments"],
            mooring_name=c["mooring_name"],
            grid_p_start=c["grid_p_start"],
            grid_p_end=c["grid_p_end"],
        ),
        kind="html",
    ),
    "timing": Panel(
        "timing",
        render=lambda c: render_template(
            "_mooring_timing.html",
            instruments=c["instruments"],
            mooring_name=c["mooring_name"],
            rec_deploy=c["rec_deploy"],
            rec_recover=c["rec_recover"],
            rec_deploy_sec=c["rec_deploy_sec"],
            rec_recover_sec=c["rec_recover_sec"],
            rec_differs=c["rec_differs"],
            yaml_deploy_time=c["yaml_deploy_time"],
            yaml_recover_time=c["yaml_recover_time"],
        ),
        kind="html",
    ),
    "clock_table": Panel(
        "clock_table",
        render=lambda c: render_template(
            "_mooring_clock_table.html", instruments=c["instruments"]
        ),
        kind="html",
    ),
    "clock_check": _fig_panel("clock_check", "fig_clock_check_b64"),
    "calibration": Panel(
        "calibration",
        render=lambda c: render_template(
            "_mooring_calibration.html", instruments=c["instruments"]
        ),
        kind="html",
    ),
    "qc": Panel(
        "qc",
        render=lambda c: render_template(
            "_mooring_qc.html", instruments=c["instruments"]
        ),
        kind="html",
    ),
    "knockdown_hab": _fig_panel("knockdown_hab", "fig_knockdown_hab_b64", slot="half"),
    "knockdown_anomaly": _fig_panel(
        "knockdown_anomaly", "fig_knockdown_anomaly_b64", slot="half"
    ),
    "knockdown_displacement": _fig_panel(
        "knockdown_displacement", "fig_knockdown_displacement_b64", slot="full"
    ),
    "diagram": Panel(
        "diagram",
        render=lambda c: render_template(
            "_mooring_diagram.html", diagram_b64=c["diagram_b64"]
        ),
        kind="html",
        # A mooring diagram is always *applicable*; it is just *not available*
        # when no PDF was produced — so it stubs ("not available") rather than
        # dropping to the "not applicable to this deployment" footer.
        unavailable_if=lambda c: (
            None
            if c.get("diagram_b64")
            else "Mooring diagram not available (no _diagram.pdf, _single.pdf, or _hardware.pdf found)."
        ),
    ),
    "issues": Panel(
        "issues",
        render=lambda c: render_template("_mooring_issues.html", issues=c["issues"]),
        kind="html",
        applies_to=lambda c: bool(c.get("issues", {}).get("any")),
    ),
}


#: Mooring sections, in display order.  Numbering is automatic (``1..N`` over the
#: rendered subset), replacing the hand-typed "2 —"/"3 —"/… headings.
MOORING_SECTIONS: dict[str, Section] = {
    "pipeline": Section("pipeline", "Processing pipeline", ("pipeline",)),
    "instruments": Section("instruments", "Instrument summary", ("instruments",)),
    "timing": Section("timing", "Deployment timing", ("timing",)),
    "clock": Section("clock", "Clock corrections", ("clock_table", "clock_check")),
    "calibration": Section("calibration", "Sensor calibration", ("calibration",)),
    "qc": Section("qc", "QC flag summary", ("qc",)),
    "knockdown": Section(
        "knockdown",
        "Mooring knockdown",
        ("knockdown_hab", "knockdown_anomaly", "knockdown_displacement"),
        layout="row",
    ),
    "diagram": Section("diagram", "Mooring diagram", ("diagram",)),
    "issues": Section("issues", "Issues for cruise report", ("issues",)),
}

#: Default mooring profile.
MOORING_DEFAULT = Profile(
    numbering="flat",
    entries=(
        MOORING_SECTIONS["pipeline"],
        MOORING_SECTIONS["instruments"],
        MOORING_SECTIONS["timing"],
        MOORING_SECTIONS["clock"],
        MOORING_SECTIONS["calibration"],
        MOORING_SECTIONS["qc"],
        MOORING_SECTIONS["knockdown"],
        MOORING_SECTIONS["diagram"],
        MOORING_SECTIONS["issues"],
    ),
)


# ---------------------------------------------------------------------------
# Orchestrator class
# ---------------------------------------------------------------------------


def _build_issues(
    instruments: list[dict[str, Any]],
    recover_dt: Any,
) -> dict[str, Any]:
    """Build the issues dict for the 'Issues for cruise report' section.

    Parameters
    ----------
    instruments : list of dict
        Instrument dicts from ``_build_context`` (already computed).
    recover_dt : datetime or None
        YAML recovery time used to compute hours-short for stopped-early items.

    """
    skipped_list = []
    stopped_list = []
    qc_list = []

    for instr in instruments:
        serial = instr.get("serial", "?")
        itype = instr.get("instr_type", "?")
        depth = instr.get("depth")
        depth_str = f"{int(round(depth))} m" if depth is not None else "? m"

        if instr.get("skipped"):
            reason = instr.get("skip_reason") or "no raw file"
            if not instr.get("raw_exists") and not instr.get("skip_reason"):
                reason = "no raw file"
            skipped_list.append(
                {
                    "serial": serial,
                    "itype": itype,
                    "depth_str": depth_str,
                    "reason": reason,
                }
            )

        if instr.get("stopped_early"):
            nc = instr.get("nc") or {}
            t_end = nc.get("t_end", "?")
            delta_h: str | None = None
            if recover_dt and nc.get("t_end_raw") is not None:
                import numpy as np

                rec_np = np.datetime64(
                    recover_dt.replace(tzinfo=None).isoformat(), "ns"
                )
                gap_s = float((rec_np - nc["t_end_raw"]) / np.timedelta64(1, "s"))
                delta_h = f"{gap_s / 3600:.0f}"
            expected = recover_dt.strftime("%Y-%m-%d %H:%M") if recover_dt else "?"
            stopped_list.append(
                {
                    "serial": serial,
                    "itype": itype,
                    "depth_str": depth_str,
                    "t_end": t_end,
                    "expected": expected,
                    "delta_h": delta_h or "?",
                }
            )

        for row in instr.get("qc_summary") or []:
            for flag in row.get("flags") or []:
                if flag["flag"] in (3, 4) and flag["pct"] > 1.0:
                    qc_list.append(
                        {
                            "serial": serial,
                            "itype": itype,
                            "note": (
                                f"{row['var']}: {flag['pct']:.1f}% "
                                f"{flag['label']} (flag {flag['flag']})"
                            ),
                        }
                    )
                    break  # one entry per variable is enough

    return {
        "skipped": skipped_list,
        "stopped_early": stopped_list,
        "qc_flagged": qc_list,
        "any": bool(skipped_list or stopped_list or qc_list),
    }


def _serials_in_nc(nc_path: Path) -> frozenset:
    """Return the set of serial numbers present in a stack or grid NC file.

    Reads the ``serial`` coordinate on the ``N_LEVELS`` dimension.  Returns an
    empty frozenset if the file is missing, unreadable, or has no ``serial``
    coordinate.

    ADCP entries use suffixed serials (e.g. ``16430_hd``, ``16430_b03``).  The
    returned set also includes the base serial (suffix stripped) so that the YAML
    instrument serial matches correctly.
    """
    import re

    try:
        import xarray as xr

        ds = xr.open_dataset(nc_path, decode_timedelta=False)
        if "serial" in ds.coords:
            raw = frozenset(str(s) for s in ds["serial"].values)
            base = frozenset(re.sub(r"_(hd|b\d+)$", "", s) for s in raw)
            return raw | base
    except Exception:
        pass
    return frozenset()


class MooringReport:
    """Generate a mooring recovery HTML report from YAML and processed files."""

    def __init__(
        self,
        *,
        proc_dir: str,
        raw_dir: str | None = None,
        report_dir: str | None = None,
    ) -> None:
        """Initialize with the cruise-level proc directory and optional raw/report dirs.

        Parameters
        ----------
        proc_dir : str
            Cruise-level processed output directory. Pipeline appends ``/{mooring}/``.
        raw_dir : str, optional
            Cruise-level raw data directory.  When provided, the report checks whether
            each instrument's raw data file still exists on disk and shows a green/red
            status indicator next to the filename.  When absent, the check is skipped.
        report_dir : str, optional
            Central directory for all HTML reports.  When set, each mooring's reports
            are written to ``report_dir/{mooring}/`` instead of
            ``proc_dir/{mooring}/report/``.  This makes the entire report tree
            portable — copy ``report_dir`` to share all reports independently of the
            NetCDF data.  The ``outdir`` argument of :meth:`generate` takes priority
            when both are supplied.

        """
        self._proc_dir = Path(proc_dir) if proc_dir else None
        self._raw_dir = Path(raw_dir) if raw_dir else None
        self._report_dir: Path | None = Path(report_dir) if report_dir else None

    def _resolve_proc_dir(self, mooring_name: str) -> Path:
        """Return the mooring-level proc directory."""
        return paths.mooring_proc_dir(self._proc_dir, mooring_name)

    def _rel(self, path: Path) -> str:
        """Return a short display path relative to proc_dir."""
        if self._proc_dir:
            try:
                return path.relative_to(self._proc_dir).as_posix()
            except ValueError:
                pass
        return path.name

    def generate(
        self,
        mooring_name: str,
        force: bool = False,
        skip_existing: bool = False,
        outdir: str | None = None,
        serials: list[str] | None = None,
        instruments: bool = False,
        grid: bool = False,
        stack: bool = False,
        cfg: Mapping[str, Any] | None = None,
        mooring_dir: str | Path | None = None,
        yaml_path: str | Path | None = None,
    ) -> Path | None:
        """Build the mooring's summary page and, when asked, its other pages.

        Parameters
        ----------
        mooring_name : str
            The description's ``name``; every page and the stage, stack and grid
            files are named from it.
        force, skip_existing : bool, optional
            Rebuild pages newer than their inputs / keep any page that exists.
        outdir : str or None, optional
            Where the pages go; overrides the central report directory.
        serials : list of str or None, optional
            Build instrument pages for these serials only.
        instruments, grid, stack : bool, optional
            Also build these pages.
        cfg : Mapping or None, optional
            The loaded description; read from *yaml_path* when not given. When
            given, the summary page is rebuilt whatever its age (the description
            may differ from the file).
        mooring_dir : str or pathlib.Path or None, optional
            The mooring's folder, whatever it is called; default
            ``<proc_dir>/<mooring_name>``.
        yaml_path : str or pathlib.Path or None, optional
            The description file (for staleness and display); default
            ``<mooring folder>/<mooring_name>.mooring.yaml``.

        Returns
        -------
        pathlib.Path or None
            The summary page, or None when the folder or description is missing.
        """
        # Reset the per-figure debug capture at the start of each report so a
        # long-lived process (a batch of moorings) does not accumulate it
        # unbounded.  (The b64->slot side registry is gone: the slot now travels
        # on the resolved panel.)
        _figdebug.clear()
        # The mooring's folder is where its description is (any name); the pages
        # and the files they read are named from the description's name.
        proc_dir = (
            Path(mooring_dir)
            if mooring_dir is not None
            else self._resolve_proc_dir(mooring_name)
        )
        if not proc_dir.exists():
            print(f"ERROR: Processing directory not found: {proc_dir}")
            return None

        out_dir = paths.resolve_report_dir(
            mooring_name,
            outdir,
            self._report_dir,
            self._proc_dir,
            mooring_dir=mooring_dir,
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{mooring_name}_report.html"
        yaml_path = (
            Path(yaml_path)
            if yaml_path is not None
            else proc_dir / f"{mooring_name}.mooring.yaml"
        )
        given_cfg = cfg
        if cfg is not None:
            cfg = dict(cfg)
        elif yaml_path.exists():
            cfg = _mooring.load(yaml_path)
        else:
            print(f"ERROR: Config not found: {yaml_path}")
            return None
        # The summary's inputs are the description and its instruments' stage files,
        # named from the description (not found by searching the folder).
        source_ncs = [
            path
            for entry in instrument_entries(cfg)
            if isinstance(entry, dict)
            for n in (3, 2)
            if (
                path := paths.stage_file(
                    proc_dir,
                    cfg["name"],
                    entry.get("instrument", "unknown"),
                    paths.safe_serial(entry.get("serial", "")),
                    n,
                )
            ).exists()
        ]
        # The summary also shows which instruments are in the stack and grid.
        products = [
            paths.product_file(proc_dir, mooring_name, p) for p in ("stack", "grid")
        ]
        # A description passed in may differ from the file on disk, whose time is
        # all the freshness check can see: the summary is always rebuilt from it.
        summary_fresh = given_cfg is None and _should_skip(
            output_path, force, skip_existing, yaml_path, *source_ncs, *products
        )
        if summary_fresh and not (instruments or grid or stack):
            _status("skip", self._rel(output_path))
            return output_path

        # Display root for pretty-printing paths in the instrument/grid/stack pages.
        display_root = self._proc_dir or proc_dir.parent

        ctx = self._build_context(mooring_name, cfg, proc_dir, yaml_path, out_dir)
        # A fresh summary is kept, but the stack, grid and instrument pages are
        # still checked against their own inputs below (each skips when fresh).
        if summary_fresh:
            _status("skip", self._rel(output_path))
        else:
            output_path.write_text(self._render(ctx), encoding="utf-8")
            _status("file", self._rel(output_path))

        if instruments:
            generate_instrument_pages(
                mooring_name,
                ctx["instruments"],
                cfg,
                proc_dir,
                out_dir,
                force,
                serials=serials,
                raw_dir=self._raw_dir,
                skip_existing=skip_existing,
            )
            # Instrument pages are now on disk — re-check existence and re-render
            # the mooring summary so serial-number links work on first run.  A
            # fresh summary whose links are unchanged is left as it is.
            if out_dir is not None:
                linked = [i.get("report_exists") for i in ctx["instruments"]]
                for instr in ctx["instruments"]:
                    instr["report_exists"] = _instrument_report_exists(
                        out_dir, mooring_name, instr["serial"]
                    )
                if not summary_fresh or linked != [
                    i["report_exists"] for i in ctx["instruments"]
                ]:
                    output_path.write_text(self._render(ctx), encoding="utf-8")

        if grid:
            grid_path = paths.product_file(proc_dir, mooring_name, "grid")
            if grid_path.exists():
                generate_grid_page(
                    mooring_name,
                    grid_path,
                    ctx,
                    out_dir,
                    force,
                    display_root,
                    skip_existing=skip_existing,
                )
            else:
                print("  NOTE: no grid file found — run 'oceanarray run MOORING' first")

        if stack:
            stack_path = paths.product_file(proc_dir, mooring_name, "stack")
            if stack_path.exists():
                generate_stack_page(
                    mooring_name,
                    stack_path,
                    ctx,
                    out_dir,
                    force,
                    display_root,
                    skip_existing=skip_existing,
                )
            else:
                print(
                    "  NOTE: no stack file found — run 'oceanarray process MOORING --stage stack' first"
                )

        return output_path

    def _build_context(
        self,
        mooring_name: str,
        cfg: dict[str, Any],
        proc_dir: Path,
        yaml_path: Path,
        out_dir: Path | None = None,
    ) -> dict[str, Any]:
        deploy_dt = _parse_dt(cfg.get("deployment_time"))
        recover_dt = _parse_dt(cfg.get("recovery_time"))
        waterdepth = cfg.get("waterdepth")

        instrument_list = instrument_entries(cfg)

        stack_nc = paths.product_file(proc_dir, mooring_name, "stack")
        grid_nc = paths.product_file(proc_dir, mooring_name, "grid")
        stack_exists = stack_nc.exists()
        grid_exists = grid_nc.exists()
        stack_serials = _serials_in_nc(stack_nc) if stack_exists else frozenset()
        # Grid is a pressure-regridded version of the stack with no per-instrument
        # serial coordinate — use stack membership as the proxy for grid inclusion.

        instruments = []
        for entry in instrument_list:
            if not isinstance(entry, dict):
                continue
            serial = _safe_serial(entry.get("serial", ""))
            instr_type = entry.get("instrument", "unknown")
            hab = entry.get("hab")
            if hab is None:
                continue
            hab = float(hab)

            depth = (
                float(entry["depth"])
                if "depth" in entry
                else (float(waterdepth) - hab if waterdepth is not None else None)
            )

            filename = entry.get("filename", "")
            file_type = entry.get("file_type", "")
            yaml_interval_s = entry.get("sample_interval_seconds")

            if filename and self._raw_dir is not None:
                # New layout: {raw_dir}/{mooring}/{instrument}/filename
                raw_path = self._raw_dir / mooring_name / instr_type / filename
                raw_path_str = raw_path.relative_to(self._raw_dir).as_posix()
                raw_exists = raw_path.exists()
                readable, readable_note = (
                    _check_readable(raw_path, file_type)
                    if raw_exists
                    else (False, "file missing")
                )
            elif filename:
                # No raw_dir configured: the raw file's location is unknown.  Do
                # NOT probe the filesystem — a bare filename resolves against the
                # current working directory and would misreport existence.
                raw_path_str = filename
                raw_exists = False
                readable, readable_note = (False, "not checked (no --raw-dir)")
            else:
                # Try auto-guessing filename from standard naming conventions
                # (the stage-1 raw-file finder, raw_files.guess_instrument_filename).
                # Skip guessing for instruments marked skip:true — they won't
                # be processed and there's nothing useful to report.
                _guessed = None
                if self._raw_dir is not None and not entry.get("skip"):
                    from oceanarray.pipeline.raw_files import guess_instrument_filename

                    _raw_mooring = self._raw_dir / mooring_name
                    _guessed = guess_instrument_filename(
                        entry, mooring_name, _raw_mooring, None
                    )
                if _guessed is not None:
                    _gfname, _gftype, _ghdr = _guessed
                    file_type = file_type or _gftype
                    _gpath = self._raw_dir / mooring_name / instr_type / _gfname
                    if not _gpath.exists():
                        _gpath = self._raw_dir / mooring_name / _gfname
                    raw_path_str = (
                        _gpath.relative_to(self._raw_dir).as_posix()
                        if _gpath.exists()
                        else f"(auto) {_gfname}"
                    )
                    raw_exists = _gpath.exists()
                    readable, readable_note = (
                        _check_readable(_gpath, file_type)
                        if raw_exists
                        else (False, "auto-guessed file not found")
                    )
                else:
                    raw_path_str = ""
                    raw_exists = False
                    readable = False
                    readable_note = "no filename in YAML"

            nc_info = _read_instrument_info(proc_dir, instr_type, mooring_name, serial)

            _base_nc = proc_dir / instr_type / f"{mooring_name}_{serial}"
            _stage3_nc = Path(str(_base_nc) + paths.stage_suffix(3))
            _stage3_nc = _stage3_nc if _stage3_nc.exists() else None

            stopped_early = False
            if recover_dt and nc_info and not nc_info.get("error"):
                t_end_raw = nc_info.get("t_end_raw")
                if t_end_raw is not None:
                    rec_np = np.datetime64(
                        recover_dt.replace(tzinfo=None).isoformat(), "ns"
                    )
                    gap_s = float((rec_np - t_end_raw) / np.timedelta64(1, "s"))
                    stopped_early = gap_s > 12 * 3600

            instruments.append(
                {
                    "serial": serial,
                    "instr_type": instr_type,
                    "hab": hab,
                    "depth": depth,
                    "filename": filename,
                    "file_type": file_type,
                    "raw_path": raw_path_str,
                    "raw_exists": raw_exists,
                    "readable": readable,
                    "readable_note": readable_note,
                    "yaml_interval_s": yaml_interval_s,
                    "dt_mismatch": bool(
                        yaml_interval_s is not None
                        and nc_info
                        and not nc_info.get("error")
                        and nc_info.get("dt_s") is not None  # key present
                        and nc_info.get("dt_s") == nc_info.get("dt_s")  # False for NaN
                        and abs(float(yaml_interval_s) - float(nc_info["dt_s"]))
                        > max(5.0, float(yaml_interval_s) * 0.1)
                    ),
                    "stopped_early": stopped_early,
                    "skipped": bool(entry.get("skip")),
                    "skip_reason": entry.get("skip_reason", ""),
                    "report_exists": (
                        out_dir is not None
                        and (
                            out_dir
                            / "instrument"
                            / f"{mooring_name}_{serial}_report.html"
                        ).exists()
                    ),
                    "stages": _stage_files(proc_dir, instr_type, mooring_name, serial),
                    "in_stack": serial in stack_serials,
                    "in_grid": (serial in stack_serials) and grid_exists,
                    "clock": _resolve_clock(entry),
                    "nc": nc_info,
                    "timing": _read_timing_info(
                        proc_dir, instr_type, mooring_name, serial
                    ),
                    "sensors": _read_sensor_info(
                        proc_dir, instr_type, mooring_name, serial
                    ),
                    "qc_summary": _read_qc_summary(_stage3_nc) if _stage3_nc else [],
                }
            )

        instruments.sort(key=lambda x: x["hab"])

        # Compute recommended --pmin / --pmax for `oceanarray run`.
        # Collect finite min/max pressure values from instruments that have real
        # pressure data (exclude stuck-at-zero sensors: p_max must be > 20 dbar).
        _all_pmin: list[float] = []
        _all_pmax: list[float] = []
        for _instr in instruments:
            _nc = _instr.get("nc") or {}
            _pmin = _nc.get("p_min")
            _pmax = _nc.get("p_max")
            if (
                _pmin is not None
                and _pmax is not None
                and np.isfinite(_pmin)
                and np.isfinite(_pmax)
                and _pmax > 20.0  # skip stuck-at-zero sensors
            ):
                _all_pmin.append(_pmin)
                _all_pmax.append(_pmax)

        grid_p_start: int | None = None
        grid_p_end: int | None = None
        if _all_pmin and _all_pmax:
            import math

            grid_p_start = int(math.floor(min(_all_pmin) / 20.0) * 20)
            grid_p_end = int(math.ceil(max(_all_pmax) / 20.0) * 20)

        # Compute recommended YAML deployment/recovery times from all instruments.
        # Start: latest suggested start UTC (most conservative — all instruments
        #        are definitely deployed before this).
        # End: earliest in the main recovery cluster (instruments stopping days/weeks
        #      before the latest are likely battery/memory failures; exclude them by
        #      keeping only instruments whose end time is within 4 h of the latest).
        from datetime import timedelta as _td

        _rec_starts: list[datetime] = []
        _rec_ends: list[datetime] = []
        for _instr in instruments:
            _tm = _instr.get("timing") or {}
            # sugg_* attrs are only written when pressure detection succeeded, so
            # any non-None entry here is already pressure-based.
            _s = _tm.get("sugg_start_utc")
            _e = _tm.get("sugg_end_utc")
            if _s:
                with contextlib.suppress(Exception):
                    _rec_starts.append(datetime.fromisoformat(_s.replace("T", " ")))
            if _e:
                with contextlib.suppress(Exception):
                    _rec_ends.append(datetime.fromisoformat(_e.replace("T", " ")))

        rec_deploy: str | None = None  # minute precision — for YAML textarea
        rec_recover: str | None = None  # minute precision — for YAML textarea
        rec_deploy_sec: str | None = None  # second precision — for table summary row
        rec_recover_sec: str | None = None  # second precision — for table summary row

        if _rec_starts:
            _best_start = max(_rec_starts)
            # Ceil to next whole minute so the YAML start doesn't clip a partial sample
            if _best_start.second or _best_start.microsecond:
                _best_start = _best_start.replace(second=0, microsecond=0) + _td(
                    minutes=1
                )
            rec_deploy = _best_start.strftime("%Y-%m-%dT%H:%M")
            rec_deploy_sec = _best_start.strftime("%Y-%m-%d %H:%M:%S")

        if _rec_ends:
            _ends_sorted = sorted(_rec_ends)
            _latest_end = _ends_sorted[-1]
            _cluster = [t for t in _ends_sorted if _latest_end - t <= _td(hours=4)]
            _best_end = min(_cluster)
            # Floor to whole minute so the YAML end doesn't include a partial sample
            _best_end = _best_end.replace(second=0, microsecond=0)
            rec_recover = _best_end.strftime("%Y-%m-%dT%H:%M")
            rec_recover_sec = _best_end.strftime("%Y-%m-%d %H:%M:%S")

        # Only show the recommended YAML block when it differs from the current YAML.
        # Compare at minute precision (same as rec_deploy / rec_recover).
        _yaml_deploy_min = deploy_dt.strftime("%Y-%m-%dT%H:%M") if deploy_dt else None
        _yaml_recover_min = (
            recover_dt.strftime("%Y-%m-%dT%H:%M") if recover_dt else None
        )
        rec_differs = (rec_deploy != _yaml_deploy_min) or (
            rec_recover != _yaml_recover_min
        )

        any_clock = any(i["clock"]["has_correction"] for i in instruments)

        # Build {serial: nc_path} for the clock-offset comparison figure.
        # Use stage1 (raw, UNtrimmed): stage2/3 are trimmed to the deployment
        # window, which removes exactly the pre-deploy / post-recover data the
        # ±window check needs to show the deployment/recovery temperature
        # transient (the shared timing feature).  Raw clocks also expose the real
        # inter-instrument offsets before correction.  (Note: stage1 times are in
        # the raw instrument clock, so a very large offset could shift the
        # transient out of the ±window; offsets are normally seconds-to-minutes.)
        # Fall back to stage2/3 only if stage1 is absent.
        _clock_nc_paths: dict[str, Path] = {}
        for _instr in instruments:
            _s = _instr["serial"]
            _itype = _instr["instr_type"]
            _base = proc_dir / _itype / f"{mooring_name}_{_s}"
            _s1 = Path(str(_base) + paths.stage_suffix(1))
            _s3 = Path(str(_base) + paths.stage_suffix(3))
            _s2 = Path(str(_base) + paths.stage_suffix(2))
            if _s1.exists():
                _clock_nc_paths[_s] = _s1
            elif _s2.exists():
                _clock_nc_paths[_s] = _s2
            elif _s3.exists():
                _clock_nc_paths[_s] = _s3
        fig_clock_check_b64 = _figdebug.as_panel(
            lambda: _make_clock_check_b64(_clock_nc_paths, deploy_dt, recover_dt)
        )

        fig_knockdown_hab_b64 = None
        fig_knockdown_anomaly_b64 = None
        fig_knockdown_displacement_b64 = None
        if stack_exists:
            try:
                import xarray as _xr

                with _xr.open_dataset(stack_nc) as _ds_kd:
                    fig_knockdown_hab_b64 = _figdebug.as_panel(
                        lambda: _make_knockdown_hab_b64(_ds_kd)
                    )
                    fig_knockdown_anomaly_b64 = _figdebug.as_panel(
                        lambda: _make_knockdown_anomaly_b64(_ds_kd)
                    )
                    fig_knockdown_displacement_b64 = _figdebug.as_panel(
                        lambda: _make_knockdown_displacement_b64(_ds_kd)
                    )
            except Exception:
                pass

        def _combined(deploy_key: str, recover_key: str, legacy_key: str) -> str:
            d = cfg.get(deploy_key) or cfg.get(legacy_key, "—")
            r = cfg.get(recover_key) or cfg.get(legacy_key) or d
            return d if d == r else f"{d} / {r}"

        return {
            "mooring_name": mooring_name,
            "cruise": _combined("deployment_cruise", "recovery_cruise", "cruise"),
            "ship": _combined("deployment_ship", "recovery_ship", "ship"),
            "deploy_time": _fmt_dt(deploy_dt),
            "recover_time": _fmt_dt(recover_dt),
            "duration": _duration_str(deploy_dt, recover_dt),
            "waterdepth": waterdepth if waterdepth is not None else "—",
            "latitude": (
                cfg.get("seabed_latitude")
                or cfg.get("deployment_latitude")
                or cfg.get("planned_latitude")
                or cfg.get("latitude")
                or "—"
            ),
            "longitude": (
                cfg.get("seabed_longitude")
                or cfg.get("deployment_longitude")
                or cfg.get("planned_longitude")
                or cfg.get("longitude")
                or "—"
            ),
            "n_instruments": len(instruments),
            "instruments": instruments,
            "rec_deploy": rec_deploy,
            "rec_recover": rec_recover,
            "rec_deploy_sec": rec_deploy_sec,
            "rec_recover_sec": rec_recover_sec,
            "rec_differs": rec_differs,
            "yaml_deploy_time": (
                deploy_dt.strftime("%Y-%m-%dT%H:%M") if deploy_dt else None
            ),
            "yaml_recover_time": (
                recover_dt.strftime("%Y-%m-%dT%H:%M") if recover_dt else None
            ),
            "grid_p_start": grid_p_start,
            "grid_p_end": grid_p_end,
            "stack_exists": stack_exists,
            "grid_exists": grid_exists,
            "any_clock_correction": any_clock,
            "fig_clock_check_b64": fig_clock_check_b64,
            "fig_knockdown_hab_b64": fig_knockdown_hab_b64,
            "fig_knockdown_anomaly_b64": fig_knockdown_anomaly_b64,
            "fig_knockdown_displacement_b64": fig_knockdown_displacement_b64,
            "nav": mooring_nav(
                mooring_name,
                instruments,
                stack_exists=stack_exists,
                grid_exists=grid_exists,
                current="summary",
                array_href=_find_array_report_href(out_dir),
            ),
            "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "yaml_path": self._rel(yaml_path),
            "diagram_b64": _load_pdf_b64(_resolve_diagram_pdf(proc_dir, mooring_name)),
            "issues": _build_issues(instruments, recover_dt),
        }

    def _render(self, ctx: dict[str, Any]) -> str:
        ctx["report"] = resolve(MOORING_DEFAULT, ctx, MOORING_PANELS)
        return render_template("mooring.html", **ctx)
