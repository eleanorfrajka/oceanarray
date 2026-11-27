"""Mooring summary HTML template and MooringReport orchestrator class."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from oceanarray import _deprecated, logger, mask_rules, paths
from oceanarray import mooring as _mooring
from oceanarray.analysis.derived import knockdown as build_knockdown
from oceanarray.exceptions import MooringError
from oceanarray.mooring.instruments import instrument_entries
from oceanarray.paths import should_skip_regeneration

from ..config import parameters as params
from ..paths import safe_serial
from . import _figdebug, _masthead, _qcflags
from ._breadcrumb import crumbs_from_ctx
from ._env import render_template
from ._grid import generate_grid_page
from ._html_helpers import (
    STOPPED_EARLY_HOURS,
    _check_readable,
    _format_dt,
    _format_duration,
    _format_seconds,
    _instrument_report_exists,
    _parse_dt,
    _read_pdf_b64,
    _resolve_array_report_href,
    _resolve_clock,
    _resolve_diagram_pdf,
    _stage_files,
    make_fig_panel,
)
from ._instrument import generate_instrument_pages
from ._navs import _type_label, mooring_nav
from ._plots import (
    _make_clock_check_b64,
    _make_knockdown_b64,
)
from ._read import (
    _read_instrument_info,
    _read_qc_summary,
    _read_sensor_info,
    _read_sensor_record,
    _read_timing_info,
)
from ._stack import generate_stack_page
from ._vendored.page_model import Panel, Profile, Section, resolve

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section manifest — mooring registry
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
        "Measured pressure against height above bottom (HAB) for each instrument "
        "with a pressure sensor: median and range of the record. The dashed line "
        "is the pressure expected at the design depth. Points below it were pushed "
        "deeper by currents (knockdown). Interpolated pressure is excluded."
    ),
    "knockdown_anomaly": (
        "Pressure anomaly (measured minus design pressure) for each instrument: "
        "the box is the middle 50 % of the record (interquartile range); the "
        "whiskers reach the furthest values within 1.5 × that range, and the dots "
        "are the values beyond. Positive means deeper than design. Box colour marks "
        "the median anomaly: green < 100 dbar, yellow 100–200, amber 200–300, "
        "red > 300."
    ),
    "knockdown_displacement": (
        "Horizontal distance each instrument was pushed from its position above "
        "the anchor, estimated from how far it was pulled down: treating the "
        "mooring as a straight line pivoting at the anchor, a drop from design "
        "height H to measured height h gives a displacement of √(H² − h²), so "
        "small drops mean large displacements; the estimate is rough for moorings "
        "that bend. Left: each point is one sample — how far down and sideways "
        "each instrument went, and whether the deep instruments moved with the "
        "top ones. Right: where each instrument spent its time — the colour of "
        "each cell is the fraction of that instrument's record at that pressure "
        "and displacement (each instrument's cells sum to 1)."
    ),
}


#: Mooring figure-panel factory (shared with the instrument page, bound to the
#: mooring caption dict).  The knockdown HAB and anomaly plots are drawn at
#: ``"half"``, so they carry ``slot="half"``.
_fig_panel = make_fig_panel(MOORING_CAPTIONS)


#: Shown for an instrument marked ``skip: true`` without a ``skip_reason``.
SKIP_NO_REASON = "skipped (no reason provided in YAML)"

#: Table note for an entry without a hab; the stack omits it for the same reason.
NO_HAB_NOTE = "not stacked: no hab in the mooring YAML"

#: File types whose records the originator already trimmed (no raw instrument clock).
_IMPORTED_FILE_TYPES = frozenset({"oceansites", "rodb"})


def _trim_note(timing: dict[str, Any]) -> str:
    """Return how much of an originator's record was cut to the deployment window.

    Parameters
    ----------
    timing : dict
        The instrument's deployment-timing record (:func:`._read._read_timing_info`):
        ``stage1_start`` / ``stage1_end`` (the originator's record) and
        ``stage2_start`` / ``stage2_end`` (what was kept), as ``"YYYY-MM-DD HH:MM"``.

    Returns
    -------
    str
        For example ``"12 h at start"`` or ``"30 min at start, 1 h at end"``; ``""``
        when nothing was cut or the times are missing.
    """
    parts = []
    for first, kept, where, sign in (
        ("stage1_start", "stage2_start", "start", 1),
        ("stage1_end", "stage2_end", "end", -1),
    ):
        a, b = timing.get(first), timing.get(kept)
        if not a or not b:
            continue
        try:
            cut = (
                sign
                * (
                    datetime.fromisoformat(b) - datetime.fromisoformat(a)
                ).total_seconds()
            )
        except ValueError:
            continue
        if cut > 0:
            parts.append(f"{_format_seconds(cut)} at {where}")
    return ", ".join(parts)


#: Variables whose every sample carries one of these flags are listed in one line
#: each under the QC table instead of a row: a row would only repeat "100 %".  Flag
#: 0 (not checked) is never counted as good; interpolated (8) is
#: (``parameters.QC_GOOD_FLAGS``), so an all-8 variable is all good.
_QC_SINGLE_FLAG_LINES: dict[int, str] = {
    0: "Not checked",
    7: "Nominal values only",
}


def _qc_table(instruments: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the deployment's QC summary: only variables with anything not good.

    Parameters
    ----------
    instruments : list of dict
        The instrument records of the deployment report, each with ``qc_summary``
        (per-variable flag counts from its stage-3 file), ``depth``, ``hab``,
        ``type_label`` and ``serial``.

    Returns
    -------
    dict
        ``rows`` (one per instrument and variable with a mix of flags or any flag
        other than good, deepest first), ``flags`` (the flag values present in
        those rows, each with its word and colour, for the columns and the
        legend), ``lines`` (variables whose every sample is not checked (0) or
        nominal (7): one entry per such flag, with ``label``, ``n`` and
        ``names``, the variable names each followed by the serials that carry
        it), ``n_good`` (variables left out because every sample is good or
        interpolated, ``parameters.QC_GOOD_FLAGS``) and
        ``any`` (whether any stage-3 QC file was read).
    """
    rows, n_good, present = [], 0, set()
    single: dict[int, dict[str, list[str]]] = {k: {} for k in _QC_SINGLE_FLAG_LINES}
    for instr in instruments:
        for row in instr.get("qc_summary") or []:
            counts = {f["flag"]: f for f in row["flags"] if f["n"] > 0}
            if set(counts) <= set(params.QC_GOOD_FLAGS):
                n_good += 1
                continue
            if len(counts) == 1 and next(iter(counts)) in single:
                serials = single[next(iter(counts))].setdefault(row["var"], [])
                by = (
                    ""
                    if row.get("by", "oceanarray") == "oceanarray"
                    else ", originator"
                )
                serials.append(f"{instr.get('serial', '')}{by}")
                continue
            present.update(counts)
            rows.append(
                {
                    "depth": instr.get("depth"),
                    "hab": instr.get("hab"),
                    "type_label": instr.get("type_label", ""),
                    "serial": instr.get("serial", ""),
                    "var": row["var"],
                    "by": row.get("by", "oceanarray"),
                    "total": row["total"],
                    # Share as text (_qcflags.share_text); a flag below 0.1 % reads "<0.1".
                    "pct": {k: f["text"] for k, f in counts.items()},
                    "good": row["good"],
                    "bar": [f for f in row["flags"] if f["n"] > 0],
                }
            )
    words = {
        f["flag"]: f
        for i in instruments
        for r in i.get("qc_summary") or []
        for f in r["flags"]
    }
    flags = [
        {"flag": k, "label": words[k]["label"], "color": words[k]["color"]}
        for k in sorted(present)
    ]
    lines = [
        {
            "flag": k,
            "label": _QC_SINGLE_FLAG_LINES[k],
            "n": sum(len(v) for v in by_var.values()),
            "names": [f"{var} ({', '.join(sers)})" for var, sers in by_var.items()],
        }
        for k, by_var in single.items()
        if by_var
    ]
    return {
        "rows": rows,
        "flags": flags,
        "lines": lines,
        "n_good": n_good,
        "any": any(i.get("qc_summary") for i in instruments),
    }


def _read_text(path: Path | None) -> str:
    """Return a text file's contents, or ``""`` when it cannot be read."""
    try:
        return Path(path).read_text(encoding="utf-8") if path else ""
    except OSError:
        return ""


def _timing_mode(instruments: list[dict]) -> str:
    """Return ``"raw"``, ``"imported"`` or ``"mixed"`` for the deployment-timing table.

    ``imported`` rows (``oceansites``/``rodb``) carry the originator's trimmed record, not
    a raw instrument clock, so the timing table's header and suggestion columns differ
    from a raw mooring's.  The mode drives which header the one shared ``<th>`` row shows.
    """
    kinds = {
        "imported" if i.get("file_type") in _IMPORTED_FILE_TYPES else "raw"
        for i in instruments
        if not i.get("skipped")
    }
    if kinds == {"imported"}:
        return "imported"
    return "mixed" if "imported" in kinds else "raw"


#: Mooring panel registry (dict-context: panels read ctx by key).
MOORING_PANELS: dict[str, Panel] = {
    "pipeline": Panel(
        "pipeline",
        render=lambda c: render_template(
            "_mooring_pipeline.html",
            instruments=c["instruments"],
            mooring_name=c["mooring_name"],
            grid_p_start=c["grid_p_start"],
            grid_p_end=c["grid_p_end"],
            yaml_name=c["yaml_name"] or f"{c['mooring_name']}.mooring.yaml",
        ),
        kind="html",
    ),
    "instruments": Panel(
        "instruments",
        render=lambda c: render_template(
            "_mooring_instruments.html",
            instruments=c["instruments"],
            mooring_name=c["mooring_name"],
            stopped_early_hours=STOPPED_EARLY_HOURS,
        ),
        kind="html",
    ),
    "timing": Panel(
        "timing",
        render=lambda c: render_template(
            # All-imported moorings get a trimmed table (no raw-clock suggestion
            # columns); raw and mixed moorings keep the full table. ``timing_mode`` is
            # computed once in the context.
            "_mooring_timing_imported.html"
            if c["timing_mode"] == "imported"
            else "_mooring_timing.html",
            instruments=c["instruments"],
            timing_mode=c["timing_mode"],
            mooring_name=c["mooring_name"],
            rec_deploy=c["rec_deploy"],
            rec_recover=c["rec_recover"],
            rec_deploy_sec=c["rec_deploy_sec"],
            rec_recover_sec=c["rec_recover_sec"],
            rec_differs=c["rec_differs"],
            yaml_deploy_time=c["yaml_deploy_time"],
            yaml_recover_time=c["yaml_recover_time"],
            deploy_time=c["deploy_time"],
            recover_time=c["recover_time"],
            any_trimmed=c["any_trimmed"],
        ),
        kind="html",
    ),
    "clock_table": Panel(
        "clock_table",
        render=lambda c: render_template(
            "_mooring_clock_table.html",
            instruments=c["instruments"],
            any_clock_correction=c["any_clock_correction"],
            any_correction_applied=c["any_correction_applied"],
            timing_mode=c["timing_mode"],
        ),
        kind="html",
    ),
    "clock_check": _fig_panel("clock_check", "fig_clock_check_b64"),
    "calibration": Panel(
        "calibration",
        render=lambda c: render_template(
            "_mooring_calibration.html",
            instruments=c["instruments"],
            timing_mode=c["timing_mode"],
        ),
        kind="html",
    ),
    "sensor_records": Panel(
        "sensor_records",
        render=lambda c: render_template(
            "_mooring_sensor_records.html", instruments=c["instruments"]
        ),
        kind="html",
        applies_to=lambda c: any(i.get("record") for i in c["instruments"]),
    ),
    "qc": Panel(
        "qc",
        render=lambda c: render_template(
            "_mooring_qc.html", qc=c["qc_table"], good_sentence=_qcflags.good_sentence()
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
    "description": Panel(
        "description",
        render=lambda c: render_template(
            "_mooring_description.html",
            yaml_text=c["yaml_text"],
            yaml_name=c["yaml_name"],
            mooring_name=c["mooring_name"],
        ),
        kind="html",
        applies_to=lambda c: bool(c.get("yaml_text")),
    ),
}


#: Mooring sections, in display order: the one place the order is written.  The
#: instrument summary leads; how the records were processed (pipeline, timing,
#: clocks) follows it; the mooring description closes the page.  Numbering is
#: automatic (``1..N`` over the rendered subset).
MOORING_SECTIONS: dict[str, Section] = {
    "instruments": Section("instruments", "Instrument summary", ("instruments",)),
    "pipeline": Section("pipeline", "Processing pipeline", ("pipeline",)),
    "timing": Section("timing", "Deployment timing", ("timing",)),
    "clock": Section("clock", "Clock corrections", ("clock_table", "clock_check")),
    "calibration": Section(
        "calibration", "Sensor calibration", ("calibration", "sensor_records")
    ),
    "qc": Section("qc", "QC flag summary", ("qc",)),
    "knockdown": Section(
        "knockdown",
        "Mooring knockdown",
        ("knockdown_hab", "knockdown_anomaly", "knockdown_displacement"),
        layout="row",
    ),
    "diagram": Section("diagram", "Mooring diagram", ("diagram",)),
    "issues": Section("issues", "Issues for cruise report", ("issues",)),
    "description": Section(
        "description", "Mooring description", ("description",), role="appendix"
    ),
}

#: Default mooring profile: every section, in the order of :data:`MOORING_SECTIONS`.
MOORING_DEFAULT = Profile(numbering="flat", entries=tuple(MOORING_SECTIONS.values()))


# ---------------------------------------------------------------------------
# Orchestrator class
# ---------------------------------------------------------------------------


def _build_issues(
    instruments: list[dict[str, Any]],
    recover_dt: Any,
) -> dict[str, Any]:
    """Build the issues dict for the 'Issues for cruise report' section."""
    # recover_dt is the YAML recovery time, used for hours-short on stopped-early items.
    skipped_list = []
    stopped_list = []
    qc_list = []

    for instr in instruments:
        serial = instr.get("serial", "?")
        itype = instr.get("type_label") or instr.get("instr_type", "?")
        depth = instr.get("depth")
        depth_str = f"{int(round(depth))} m" if depth is not None else "? m"

        if instr.get("skipped"):
            reason = instr.get("skip_reason") or SKIP_NO_REASON
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
    """Return the serials on the ``N_LEVELS`` dim of a stack/grid file, or empty."""
    try:
        import xarray as xr

        ds = xr.open_dataset(nc_path, decode_timedelta=False)
        if "serial" in ds.coords:
            raw = frozenset(str(s) for s in ds["serial"].values)
            # ADCP serials carry suffixes (16430_hd, 16430_b03); add the base serial
            # so the YAML instrument serial matches.
            base = frozenset(paths.adcp_parent_serial(s) for s in raw)
            return raw | base
    except (OSError, ValueError) as e:
        log.warning("%s: serials not read (%s)", nc_path.name, e)
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

    def _rel(self, path: Path) -> str:
        """Return a short display path relative to proc_dir."""
        if self._proc_dir:
            return logger.format_rel_path(path, self._proc_dir)
        return path.name

    def generate(
        self,
        mooring_name: str,
        force: bool = False,
        skip_existing: bool = False,
        outdir: str | None = None,
        breadcrumb: list[tuple[str, str | None]] | None = None,
        serials: list[str] | None = None,
        instruments: bool = False,
        grid: bool = False,
        stack: bool = False,
        mooring_dict: Mapping[str, Any] | None = None,
        mooring_dir: str | Path | None = None,
        yaml_path: str | Path | None = None,
        *,
        cfg: Mapping[str, Any] | None = None,
    ) -> Path:
        """Build the mooring's summary page and, when asked, its other pages.

        Parameters
        ----------
        mooring_name : str
            The mooring YAML's ``name``; every page and the stage, stack and grid
            files are named from it.
        force, skip_existing : bool, optional
            Rebuild pages newer than their inputs / keep any page that exists.
        outdir : str or None, optional
            Where the pages go; overrides the central report directory.
        breadcrumb : list of (str, str or None) or None, optional
            An optional trail of ancestor crumbs (hrefs relative to the page's own
            report directory); each page always shows its own position with the
            ancestors in front. ``None`` shows just each page's own position.
        serials : list of str or None, optional
            Build instrument pages for these serials only.
        instruments, grid, stack : bool, optional
            Also build these pages.
        mooring_dict : Mapping or None, optional
            The loaded mooring YAML; read from *yaml_path* when not given. When
            given, the summary page is rebuilt whatever its age (the mooring YAML
            may differ from the file).
        mooring_dir : str or pathlib.Path or None, optional
            The mooring's folder, whatever it is called; default
            ``<proc_dir>/<mooring_name>``.
        yaml_path : str or pathlib.Path or None, optional
            The mooring YAML file (for staleness and display); default
            ``<mooring folder>/<mooring_name>.mooring.yaml``.
        cfg : Mapping or None, optional
            Deprecated name of *mooring_dict*; removed in 0.8.0.

        Returns
        -------
        pathlib.Path
            The summary page.

        Raises
        ------
        MooringError
            When the processing folder or the mooring YAML is missing.
        """
        # Reset the per-figure debug capture at the start of each report so a
        # long-lived process (a batch of moorings) does not accumulate it
        # unbounded.  (The b64->slot side registry is gone: the slot now travels
        # on the resolved panel.)
        given = _deprecated.resolve_keyword(
            "oceanarray.reports.MooringReport.generate(cfg=)", mooring_dict, cfg
        )
        _figdebug.clear()
        # The mooring's folder is where its mooring YAML is (any name); the pages
        # and the files they read are named from the mooring YAML's name.
        proc_dir = (
            Path(mooring_dir)
            if mooring_dir is not None
            else paths.mooring_proc_dir(self._proc_dir, mooring_name)
        )
        if not proc_dir.exists():
            raise MooringError(
                f"{mooring_name}: processing directory not found: {proc_dir}"
            )

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
        if given is not None:
            mooring_dict = dict(given)
        elif yaml_path.exists():
            mooring_dict = _mooring.load(yaml_path)
        else:
            raise MooringError(f"{mooring_name}: mooring YAML not found: {yaml_path}")
        # The summary's inputs are the mooring YAML and its instruments' stage files,
        # named from the mooring YAML (not found by searching the folder).
        source_ncs = [
            path
            for entry in instrument_entries(mooring_dict)
            if isinstance(entry, dict)
            for n in (3, 2)
            if (
                path := paths.stage_file(
                    proc_dir,
                    mooring_dict["name"],
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
        # A mooring YAML passed in may differ from the file on disk, whose time is
        # all the freshness check can see: the summary is always rebuilt from it.
        summary_fresh = given is None and should_skip_regeneration(
            output_path, force, skip_existing, yaml_path, *source_ncs, *products
        )
        if summary_fresh and not (instruments or grid or stack):
            logger.status("skip", self._rel(output_path))
            return output_path

        # Display root for pretty-printing paths in the instrument/grid/stack pages.
        display_root = self._proc_dir or proc_dir.parent

        ctx = self._build_context(
            mooring_name, mooring_dict, proc_dir, yaml_path, out_dir
        )
        # The caller's breadcrumb ancestors ride in the context so every page (the
        # summary here and the stack/grid/instrument pages below) builds its own
        # trail from the same ancestors with page_breadcrumb().
        ctx["_breadcrumb_ancestors"] = breadcrumb
        # A fresh summary is kept, but the stack, grid and instrument pages are
        # still checked against their own inputs below (each skips when fresh).
        if summary_fresh:
            logger.status("skip", self._rel(output_path))
        else:
            output_path.write_text(self._render(ctx), encoding="utf-8")
            logger.status("file", self._rel(output_path))

        if instruments:
            generate_instrument_pages(
                mooring_name,
                ctx["instruments"],
                mooring_dict,
                proc_dir,
                out_dir,
                force,
                serials=serials,
                raw_dir=self._raw_dir,
                skip_existing=skip_existing,
                breadcrumb=breadcrumb,
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
                log.info(
                    "no grid file found — run 'oceanarray run %s' first", mooring_name
                )

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
                log.info(
                    "no stack file found — run 'oceanarray process %s --stage stack' first",
                    mooring_name,
                )

        return output_path

    def _build_context(
        self,
        mooring_name: str,
        mooring_dict: dict[str, Any],
        proc_dir: Path,
        yaml_path: Path,
        out_dir: Path | None = None,
    ) -> dict[str, Any]:
        deploy_dt = _parse_dt(mooring_dict.get("deployment_time"))
        recover_dt = _parse_dt(mooring_dict.get("recovery_time"))
        waterdepth = mooring_dict.get("waterdepth")

        instrument_list = instrument_entries(mooring_dict)
        # The pressure ranges leave out what the figures hide: the mooring's rule.
        rule = mask_rules.stack_rule(mooring_dict)

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
            serial = safe_serial(entry.get("serial", ""))
            instr_type = entry.get("instrument", "unknown")
            # An entry without a hab stays in the table with a note, as the masthead
            # counts it (the stack omits it); a skipped one without a hab is left out.
            hab = entry.get("hab")
            if hab is None and entry.get("skip"):
                continue
            hab = float(hab) if hab is not None else None

            depth = (
                float(entry["depth"])
                if "depth" in entry
                else (
                    float(waterdepth) - hab
                    if waterdepth is not None and hab is not None
                    else None
                )
            )

            filename = entry.get("filename", "")
            file_type = entry.get("file_type", "")
            yaml_interval_s = entry.get("sample_interval_seconds")

            if filename and self._raw_dir is not None:
                # New layout: {raw_dir}/{mooring}/{instrument}/filename
                raw_path = self._raw_dir / mooring_name / instr_type / filename
                raw_path_str = raw_path.relative_to(self._raw_dir).as_posix()
                # A wildcard (RODB per-bin ADCP files): count the matches, check one.
                matches = sorted(raw_path.parent.glob(filename))
                if matches and matches[0] != raw_path:
                    raw_path_str += f" ({len(matches)} files)"
                    raw_path = matches[0]
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

            nc_info = _read_instrument_info(
                proc_dir, instr_type, mooring_name, serial, rule=rule
            )

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
                    stopped_early = gap_s > STOPPED_EARLY_HOURS * 3600

            instruments.append(
                {
                    "serial": serial,
                    "instr_type": instr_type,
                    "type_label": _type_label(instr_type),
                    "hab": hab,
                    "no_hab_note": NO_HAB_NOTE if hab is None else "",
                    "depth": depth,
                    "nominal_depth": (
                        float(entry["nominal_depth"])
                        if entry.get("nominal_depth") is not None
                        else depth
                    ),
                    "filename": filename,
                    "file_type": file_type,
                    "imported": file_type in _IMPORTED_FILE_TYPES,
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
                    "skip_reason": entry.get("skip_reason", "") or SKIP_NO_REASON,
                    # Where stage files are looked for; named in the table when none is
                    # found, so the reader sees what was searched rather than a guess at
                    # why (not processed yet, wrong proc_dir, in-memory run).
                    "stage_dir": f"{mooring_name}/{instr_type}/",
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
                    "record": _read_sensor_record(
                        proc_dir, instr_type, mooring_name, serial
                    ),
                    "qc_summary": _read_qc_summary(_stage3_nc) if _stage3_nc else [],
                }
            )

        # Entries without a hab sort last.
        instruments.sort(key=lambda x: (x["hab"] is None, x["hab"] or 0.0))
        for _instr in instruments:
            _nc = _instr.get("nc") or {}
            _instr["interval_label"] = _format_seconds(_nc.get("dt_s"))
            _instr["trim_note"] = _trim_note(_instr.get("timing") or {})
        any_trimmed = any(i["trim_note"] for i in instruments)

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

        any_clock = any(
            i["clock"]["has_correction"] or i["clock"]["not_read"] for i in instruments
        )
        any_correction_applied = any(i["clock"]["has_correction"] for i in instruments)

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
            import xarray as _xr

            with _xr.open_dataset(stack_nc) as _ds_kd:
                # Design pressures and measured records, built once for the
                # three knockdown figures.
                _kd = build_knockdown(_ds_kd)
                fig_knockdown_hab_b64 = _figdebug.as_panel(
                    lambda: _make_knockdown_b64("hab", _ds_kd, _kd)
                )
                fig_knockdown_anomaly_b64 = _figdebug.as_panel(
                    lambda: _make_knockdown_b64("anomaly", _ds_kd, _kd)
                )
                fig_knockdown_displacement_b64 = _figdebug.as_panel(
                    lambda: _make_knockdown_b64("displacement", _ds_kd, _kd)
                )

        common = _masthead.common_facts(mooring_dict, instrument_list)
        return {
            "mooring_name": mooring_name,
            "cruise": _masthead.cruise_label(mooring_dict),
            "masthead_common": common,
            "meta_facts": _masthead.masthead_facts("mooring", common),
            "deploy_time": _format_dt(deploy_dt),
            "recover_time": _format_dt(recover_dt),
            "duration": _format_duration(deploy_dt, recover_dt),
            "waterdepth": waterdepth if waterdepth is not None else "—",
            "instruments": instruments,
            "qc_threshold_note": _qcflags.threshold_note(mooring_dict),
            "qc_table": _qc_table(instruments),
            "any_trimmed": any_trimmed,
            "yaml_text": _read_text(yaml_path),
            "yaml_name": yaml_path.name if yaml_path else "",
            "timing_mode": _timing_mode(instruments),
            # Optional report sections turned on in the config. A standalone mooring
            # report resolves from the mooring YAML (the strongest level reachable here).
            "report_sections_grid": _mooring.resolve_report_sections(
                "grid", mooring_dict
            ),
            # Extra periods marked on the grid page's spectra.
            "spectrum_markers": _mooring.resolve_spectrum_markers(mooring_dict),
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
            "any_correction_applied": any_correction_applied,
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
                array_href=_resolve_array_report_href(out_dir),
            ),
            "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "yaml_path": self._rel(yaml_path),
            "diagram_b64": _read_pdf_b64(_resolve_diagram_pdf(proc_dir, mooring_name)),
            "issues": _build_issues(instruments, recover_dt),
        }

    def _render(self, ctx: dict[str, Any]) -> str:
        ctx["report"] = resolve(MOORING_DEFAULT, ctx, MOORING_PANELS)
        # The summary is the mooring's own page, so the mooring is the current crumb.
        ctx["breadcrumb"] = crumbs_from_ctx(ctx, [(ctx["mooring_name"], None)])
        return render_template("mooring.html", **ctx)
