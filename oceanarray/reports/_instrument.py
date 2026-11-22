"""Per-instrument HTML report template and page generator."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oceanarray import paths
from oceanarray.paths import should_skip_regeneration

from ..config import parameters as params
from ..paths import safe_serial
from ..pipeline import masking
from . import _captions, _html_helpers, _instrument_text, _qcflags
from ._breadcrumb import page_crumbs
from ._env import (
    render_history,
    render_nc_collapsed,
    render_template,
)
from ._figdebug import as_panel
from ._html_helpers import (
    _file_info,
    _format_seconds,
    _parse_history,
    _resolve_array_report_href,
    _stage_file_details,
    make_fig_panel,
)
from ._masthead import cruise_label
from ._navs import _type_label, mooring_nav
from ._plots import (
    _make_adcp_hodograph_b64,
    _make_adcp_rose_b64,
    _make_adcp_velocity_b64,
    _make_analog_timeseries_b64,
    _make_data_histogram_b64,
    _make_hodograph_b64,
    _make_instrument_b64s,
    _make_instrument_rose_b64,
    _make_speed_boxplot_b64,
    _make_temperature_trajectory_b64,
    _make_ts_diagram_b64,
    _make_windows_b64s,
)
from ._read import _read_nc_metadata, _read_qc_thresholds
from ._vendored.page_model import Panel, PanelGroup, Profile, Section, resolve

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-instrument HTML template
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Section manifest — instrument registry
#
# The context is the per-instrument ``ctx`` dict the generator already builds
# (figures + tables + instrument type), so panels read it by key.  Figure panels
# gate on figure-presence: the generator sets a figure to ``None`` both when the
# instrument type does not have it (a microcat has no velocity) and when the data
# is insufficient, so an absent figure means "not applicable to this instrument"
# — it is omitted and the section is named in the not-applicable footer, matching
# the pre-manifest ``{% if fig %}`` behaviour.  Structured content (the windows
# explainer, QC tables, analog-YAML list, NetCDF tables) renders through html/
# table sub-templates; short figure captions are a plain-text data field.
# ---------------------------------------------------------------------------

#: Instrument figure captions, keyed by panel id — plain text, Unicode notation.
#: The captions of the figures shown on every instrument (time series, T-S, rose,
#: distributions) are generated from the file by ``_instrument_text``; these are the
#: fixed ones.
INSTRUMENT_CAPTIONS: dict[str, str] = {
    "adcp_velocity": (
        "Time–range colour plots of the four velocity components (east, north, "
        "up, error). Y-axis: range from transducer (m). Colour scale: symmetric "
        "about zero; percentile 2/98 of all components combined. Magnetic "
        "declination is applied when the record is checked."
    ),
    "ts_diagram": "",
    "adcp_rose": (
        "Depth-average followed by bins nearest 100, 200, 300 and 400 m from the "
        "transducer. Bins with fewer than two valid samples are omitted. "
        "Direction toward which the current flows; 0° = N, clockwise. Magnetic "
        "declination is applied when the record is checked."
    ),
    "rose": "",
    "trajectory": (
        "Cumulative displacement from adding up the measured velocity (a "
        "progressive vector diagram): how far water passing the instrument would "
        "travel if the flow were the same everywhere. It shows the mean flow "
        "direction and how steady it is, not the path of a water parcel. Coloured "
        "by temperature; axes in metres from the start."
    ),
    "hodograph": (
        f"East vs north velocity ({params.vunit('east_velocity')}). Left: full "
        "record. Right: eddy component — raw minus 4-day low-pass (rolling mean)."
    ),
    "dist": "",
    "analog": (
        "Full-record time series of analog channel variables containing "
        "non-zero, non-NaN data. One panel per channel. Labels and units are read "
        "from the mooring description — change them there to change what appears "
        "here."
    ),
    "steps": (
        "What was done to this instrument's record, entry by entry as the file's "
        "history attribute records it, with the settings read from the file."
    ),
    "nc_file": _captions.netcdf_caption("file", "on the deployment page"),
}


def _declination_unapplied_reason(ds: Any) -> str:
    """Return why a stage-3 file's east/north velocities are not in true north.

    Oceanarray records the declination it applied in the ``magnetic_declination``
    attribute (``"UNK"`` when stage 3 had no mooring position); a reader records
    the originator's in ``originator_magnetic_declination``. Stage-2 files are not
    checked: their velocities are magnetic north by design.

    Parameters
    ----------
    ds : xarray.Dataset or None
        The instrument's stage-3 file, or None when the page shows an earlier stage.

    Returns
    -------
    str
        ``"no mooring position at stage 3"``, ``"the stage-3 file records no
        declination"``, or ``""`` when the velocities are in true north (or the
        file has none).
    """
    if ds is None or not {"east_velocity", "north_velocity"} <= set(ds.data_vars):
        return ""
    if "originator_magnetic_declination" in ds.attrs:
        return ""
    decl = ds.attrs.get("magnetic_declination")
    if decl == "UNK":
        return "no mooring position at stage 3"
    if decl is None:
        return "the stage-3 file records no declination"
    return ""


def _has_fig(key: str) -> Callable:
    """Return an applies_to/`unavailable`-style predicate: figure *key* is present."""
    return lambda c: bool(c.get(key))


#: Instrument figure-panel factory (shared with the mooring page, bound to the
#: instrument caption dict).
_fig_panel = make_fig_panel(INSTRUMENT_CAPTIONS)


def _image_panel(pid: str, b64: str) -> Panel:
    """Build a bare-``.fig`` panel rendering a fixed base64 image (paginated runs)."""
    return Panel(pid, render=lambda _c, _b=b64: _b, slot=None)


def _history_unavailable(c: dict) -> str | None:
    """Reason the history panel cannot render, or None."""
    return None if c.get("history_entries") else _html_helpers.NO_HISTORY


def _empty_note(pid: str, reason: str, when_empty: Callable) -> Panel:
    """Build a panel showing *reason* as a stub only when *when_empty(ctx)* is True."""
    # Keeps a PanelGroup section's specific "nothing to show" message instead of the
    # resolver's generic empty-section stub; adds nothing when there are figures.
    return Panel(
        pid,
        render=lambda _c: None,
        kind="html",
        applies_to=when_empty,
        unavailable_if=lambda _c: reason,
    )


def _images(value: Any) -> list[str]:
    """Return the base64 images of a figure value (one, a list, or a stub)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _collapsed_figure(pid: str, key: str, summary: str, *, anchor: str = "") -> Panel:
    """Build a panel showing figure *key* collapsed, with ``captions[pid]`` above it."""
    return Panel(
        pid,
        render=lambda c: render_template(
            "_collapsed_figure.html",
            summary=summary,
            caption=c["captions"].get(pid, ""),
            images=_images(c.get(key)),
            anchor=anchor,
        ),
        kind="html",
        applies_to=lambda c: bool(_images(c.get(key))),
    )


#: Instrument panel registry (dict-context: panels read ctx by key).
INSTRUMENT_PANELS: dict[str, Panel] = {
    "steps": Panel(
        "steps",
        render=lambda c: render_template("_history_steps.html", rows=c["steps"]),
        kind="html",
        caption=INSTRUMENT_CAPTIONS["steps"],
        applies_to=lambda c: bool(c.get("steps")),
    ),
    "files": Panel(
        "files",
        render=lambda c: render_template(
            "_instrument_files.html",
            raw_file=c["raw_file"],
            stage_files=c["stage_files"],
            originator=c.get("originator"),
        ),
        kind="html",
    ),
    "history": Panel(
        "history",
        render=lambda c: render_template(
            "_instrument_history.html",
            history=render_history(c["history_entries"]),
            levels=c.get("processing_levels", []),
        ),
        kind="html",
        unavailable_if=_history_unavailable,
    ),
    "timeseries_empty": _empty_note(
        "timeseries_empty",
        "No plottable variables found.",
        lambda c: not c.get("fig_ts_b64"),
    ),
    "adcp_velocity": _fig_panel("adcp_velocity", "fig_adcp_velocity_b64"),
    "windows": Panel(
        "windows",
        render=lambda c: render_template(
            "_instrument_windows_note.html",
            hours=c.get("window_hours", 6),
            kinds={label for _t, _c, label in c.get("window_vlines", [])},
            end_suggested=c.get("end_suggested", False),
            disturbed=c.get("disturbed_end", ""),
            images=_images(c.get("fig_windows_b64")),
        ),
        kind="html",
        applies_to=_has_fig("fig_windows_b64"),
    ),
    "ts_diagram": _fig_panel("ts_diagram", "fig_tsd_b64"),
    "adcp_rose": _fig_panel("adcp_rose", "fig_adcp_rose_b64"),
    "rose_declination": Panel(
        "rose_declination",
        render=lambda c: render_template(
            "_instrument_rose_note.html", reason=c.get("declination_reason", "")
        ),
        kind="html",
        applies_to=lambda c: bool(c.get("declination_warn")),
    ),
    "rose": _fig_panel("rose", "fig_rose_b64"),
    "trajectory": _fig_panel("trajectory", "fig_trajectory_b64", slot="half"),
    "hodograph": _fig_panel("hodograph", "fig_hodograph_b64"),
    "speed": _fig_panel("speed", "fig_speed_boxplot_b64", slot="quarter"),
    "analog": _fig_panel("analog", "fig_analog_b64"),
    "analog_yaml": Panel(
        "analog_yaml",
        render=lambda c: render_template(
            "_instrument_analog_yaml.html", analog_yaml_info=c["analog_yaml_info"]
        ),
        kind="html",
        applies_to=lambda c: bool(c.get("analog_yaml_info")),
    ),
    "dist": _collapsed_figure("dist", "fig_dt_b64", "Data value distributions"),
    "qc": Panel(
        "qc",
        render=lambda c: render_template(
            "_instrument_qc.html",
            table=c["flag_table"],
            qc_thresholds=c["qc_thresholds"],
            good_sentence=_qcflags.good_sentence(),
        ),
        kind="html",
        applies_to=lambda c: c.get("flag_table") is not None,
    ),
    "nc_file": Panel(
        "nc_file",
        render=lambda c: render_nc_collapsed(c["nc_meta"]),
        kind="html",
        caption=INSTRUMENT_CAPTIONS["nc_file"],
        applies_to=lambda c: bool(c.get("nc_meta")),
    ),
}

#: Paginated time-series images — one bare-``.fig`` panel per PNG in the list.
_TIMESERIES_IMGS = PanelGroup(
    over=lambda c: list(enumerate(c.get("fig_ts_b64") or [])),
    panel=lambda pair: _image_panel(f"timeseries_{pair[0]}", pair[1]),
)


#: Instrument sections.  The start and end windows keep the ``start`` anchor
#: (mooring.html deep-links to it) on their collapsed box in the processing section.
#: Sections whose only figures come from a PanelGroup pair an ``*_empty`` note
#: panel (a specific "nothing to show" message) with the image run, so the
#: section is always kept via an applicable panel.
INSTRUMENT_SECTIONS: dict[str, Section] = {
    "adcp_velocity": Section("adcp_velocity", "Velocity", ("adcp_velocity",)),
    "timeseries": Section(
        "timeseries", "Time series", ("timeseries_empty", _TIMESERIES_IMGS)
    ),
    "ts_diagram": Section("ts_diagram", "T-S diagram", ("ts_diagram",)),
    "adcp_rose": Section("adcp_rose", "Current roses", ("adcp_rose",)),
    "rose": Section("rose", "Current rose", ("rose_declination", "rose")),
    "trajectory": Section("trajectory", "Progressive vector diagram", ("trajectory",)),
    "hodograph": Section("hodograph", "Hodograph", ("hodograph",)),
    "speed": Section("speed", "Current speed distribution", ("speed",)),
    "analog": Section("analog", "Analog channels", ("analog", "analog_yaml")),
    "qc": Section("qc", "Quality flags", ("qc",)),
    "processing": Section(
        "processing",
        "Processing",
        ("steps", "files", "history", "windows", "dist"),
    ),
    "netcdf": Section("netcdf", "NetCDF file", ("nc_file",), role="appendix"),
}

#: Default instrument profile.
INSTRUMENT_DEFAULT = Profile(
    numbering="flat",
    entries=tuple(
        INSTRUMENT_SECTIONS[k]
        for k in (
            "adcp_velocity",
            "timeseries",
            "ts_diagram",
            "adcp_rose",
            "rose",
            "trajectory",
            "hodograph",
            "speed",
            "analog",
            "qc",
            "processing",
            "netcdf",
        )
    ),
)


def _profile(captions: dict[str, str]) -> Profile:
    """Return the instrument profile with the page's generated section captions."""
    timeseries = replace(
        INSTRUMENT_SECTIONS["timeseries"], intro=captions.get("timeseries") or None
    )
    return replace(
        INSTRUMENT_DEFAULT,
        entries=tuple(
            timeseries if s.id == "timeseries" else s
            for s in INSTRUMENT_DEFAULT.entries
        ),
    )


def _panels(captions: dict[str, str]) -> dict[str, Panel]:
    """Return the panel registry with the page's generated figure captions."""
    out = dict(INSTRUMENT_PANELS)
    for pid in ("ts_diagram", "rose"):
        if captions.get(pid):
            out[pid] = replace(out[pid], caption=captions[pid])
    return out


def _title(type_label: str, serial: str, depth: float | None) -> str:
    """Return the page's title: ``"MicroCAT 14644 · 2190 m"``."""
    return f"{type_label} {serial}" + (f" · {depth:.0f} m" if depth is not None else "")


# ---------------------------------------------------------------------------
# Page generator
# ---------------------------------------------------------------------------


def generate_instrument_pages(
    mooring_name: str,
    instruments: list[dict[str, Any]],
    cfg: dict[str, Any],
    proc_dir: Path,
    out_dir: Path,
    force: bool,
    serials: list[str] | None = None,
    raw_dir: Path | None = None,
    skip_existing: bool = False,
    breadcrumb: list[tuple[str, str | None]] | None = None,
) -> None:
    """Generate one HTML quality-control report page per instrument.

    For each instrument in *instruments*, reads the best available processed
    NetCDF (stage3 preferred, falling back to stage2 or stage1) and renders an
    HTML page with time-series plots, data-quality flags, a histogram panel,
    hodograph (if velocity data are present), and provenance metadata.  Pages are
    written to ``{out_dir}/instrument/{mooring_name}_{serial}_report.html``.

    Parameters
    ----------
    mooring_name : str
        Mooring identifier (e.g. ``"dsG3_1_2026"``).
    instruments : list of dict
        Instrument records extracted from the mooring YAML, each containing at
        minimum ``serial``, ``instr_type``, and ``stages`` keys.
    cfg : dict
        Full mooring configuration dictionary (from the mooring YAML).
    proc_dir : Path
        Mooring-level processed output directory (e.g. ``proc/dsG3_1_2026/``).
    out_dir : Path
        Report output directory for this mooring (e.g. ``proc/dsG3_1_2026/report/``).
        Per-instrument pages are placed in ``out_dir/instrument/``.
    force : bool
        If True, regenerate pages that already exist.
    serials : list of str, optional
        If provided, only generate pages for instruments whose serial number is in
        this list.  Useful for regenerating a single instrument without reprocessing
        the full mooring.
    raw_dir : Path, optional
        Cruise-level raw data directory for the new directory layout.  When given,
        raw file existence is checked at ``{raw_dir}/{mooring}/{instr_type}/filename``.
        When None, the raw file is not located on disk.
    skip_existing : bool
        If True, skip pages whose output file is newer than the source NetCDF.
    breadcrumb : list of (str, str or None) or None, optional
        An optional trail of ancestor crumbs (hrefs relative to the mooring's report
        directory); each instrument page always shows ``{mooring} › {serial}`` with
        the ancestors in front, rebased by ``"../"`` for the instrument subdirectory.

    """
    mooring_report_link = f"../{mooring_name}_report.html"
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    stack_exists = paths.product_file(proc_dir, mooring_name, "stack").exists()
    grid_exists = paths.product_file(proc_dir, mooring_name, "grid").exists()
    cruise = cruise_label(cfg)
    serial_filter = {safe_serial(s) for s in serials} if serials else None

    idx = 0
    for instr in instruments:
        serial = instr["serial"]
        if serial_filter and serial not in serial_filter:
            continue
        instr_type = instr["instr_type"]
        out_path = out_dir / "instrument" / f"{mooring_name}_{serial}_report.html"
        prefix = f"  [{idx:2d}] {instr_type:<12} s/n {serial:<12}"
        idx += 1

        stages = instr.get("stages", {})
        if not any(stages.get(s) for s in ("stage1", "stage2", "stage3")):
            log.info("%s  [skipped — no processed files]", prefix)
            continue

        _base = proc_dir / instr_type / f"{mooring_name}_{serial}"
        stage3_nc = Path(str(_base) + paths.stage_suffix(3))
        stage3_nc = stage3_nc if stage3_nc.exists() else None
        _stage2_nc = Path(str(_base) + paths.stage_suffix(2))
        _stage1_nc = Path(str(_base) + paths.stage_suffix(1))
        _source_nc = next(
            (
                p
                for p in [stage3_nc, _stage2_nc, _stage1_nc]
                if p is not None and p.exists()
            ),
            None,
        )
        if should_skip_regeneration(
            out_path, force, skip_existing, *([_source_nc] if _source_nc else [])
        ):
            log.info("%s  %s  [skip]", prefix, out_path.name)
            continue
        best_nc = (
            stage3_nc
            or (_stage2_nc if _stage2_nc.exists() else None)
            or (_stage1_nc if _stage1_nc.exists() else None)
        )
        data_stage = (
            "stage3"
            if stage3_nc
            else (
                "stage2"
                if _stage2_nc.exists()
                else ("stage1" if _stage1_nc.exists() else None)
            )
        )

        nc_info = instr.get("nc", {}) or {}
        n_records = nc_info.get("n_records", "—")
        t_start = nc_info.get("t_start", "—")
        t_end = nc_info.get("t_end", "—")
        dt_s = nc_info.get("dt_s")
        median_dt = _format_seconds(dt_s)
        nominal_depth = instr.get("nominal_depth", instr.get("depth"))
        type_label = instr.get("type_label") or _type_label(instr_type)
        title = _title(type_label, serial, nominal_depth)

        # One read of the file the page shows: its attributes, flags and history
        # feed the header, the generated captions and the step table.
        history_entries: list[dict[str, str]] = []
        _sugg_deploy_utc: str | None = None
        _sugg_recover_utc: str | None = None
        who: _instrument_text.Originator | None = None
        steps: list = []
        levels: list = []
        flag_table = None
        disturbed = ""
        captions: dict[str, str] = {}
        # The flag rule the stack and grid apply, chosen once for every figure and
        # caption of the page.
        rule = masking.stack_rule(cfg)
        ds_page = None
        if best_nc:
            try:
                import xarray as xr

                # Loaded and closed at once: the figures open the file again.
                with xr.open_dataset(best_nc, decode_timedelta=False) as _ds:
                    ds_page = _ds.load()
            except (OSError, ValueError) as e:
                log.warning(
                    "%s: not read for the instrument page (%s)", Path(best_nc).name, e
                )
                ds_page = None
        if ds_page is not None:
            history_entries = _parse_history(ds_page.attrs.get("history", ""))
            _sugg_deploy_utc = ds_page.attrs.get("suggested_deployment_time_utc")
            _sugg_recover_utc = ds_page.attrs.get("suggested_recovery_time_utc")
            who = _instrument_text.originator_identity(ds_page.attrs)
            steps = _instrument_text.instrument_history_rows(
                ds_page,
                file_type=str(instr.get("file_type", "")),
                raw_name=str(instr.get("filename", "")),
                rule=rule,
            )
            levels = _instrument_text.processing_levels(ds_page)
            if stage3_nc is not None:
                flag_table = _instrument_text.flag_table(ds_page)
            if who is not None:
                disturbed = _instrument_text.disturbed_end(ds_page)

        # Vertical lines on the start/end windows: one per time, a suggested
        # time (orange, from pressure) and the description's (green) drawn as one
        # line when they agree.
        _window_vlines = _instrument_text.window_lines(
            (_sugg_deploy_utc, _sugg_recover_utc),
            (cfg.get("deployment_time"), cfg.get("recovery_time")),
        )

        # File listing — the data read and the file of each processing step
        raw_filename = instr.get("filename", "")
        if raw_filename and raw_dir is not None:
            # New layout: {raw_dir}/{mooring}/{instrument}/filename
            raw_file = _file_info(raw_dir / mooring_name / instr_type / raw_filename)
        elif raw_filename:
            # No raw_dir configured: the raw file's location is unknown.  Do NOT
            # probe the filesystem — a bare filename resolves against the current
            # working directory and would misreport existence.  Mark it "not
            # checked" rather than guess.
            raw_file = {
                "exists": False,
                "unknown": True,
                "name": raw_filename,
                "size": "",
                "mtime": "",
            }
        else:
            raw_file = {"exists": False, "name": "—", "size": "", "mtime": ""}
        stage_files = _stage_file_details(proc_dir, instr_type, mooring_name, serial)

        # The figures draw from the file the page already read (read again only
        # when that failed).
        src = ds_page if ds_page is not None else best_nc
        ts_record: dict = {}
        tsd_record: dict = {}
        rose_title = _title("", serial, nominal_depth).strip()
        ctx = {
            "mooring_name": mooring_name,
            "cruise": cruise,
            "serial": serial,
            "instr_type": instr_type,
            "type_label": type_label,
            "title": title,
            "hab": instr["hab"],
            "depth": nominal_depth,
            "n_records": (
                f"{n_records:,}" if isinstance(n_records, int) else n_records
            ),
            "t_start": t_start,
            "t_end": t_end,
            "median_dt": median_dt,
            "originator": who,
            "raw_name": raw_filename,
            "raw_file": raw_file,
            "stage_files": stage_files,
            "steps": steps,
            "processing_levels": levels,
            "flag_table": flag_table,
            "disturbed_end": disturbed,
            "window_vlines": _window_vlines,
            "window_hours": 6,
            "end_suggested": bool(_sugg_recover_utc),
            "mooring_report_link": mooring_report_link,
            "nav": mooring_nav(
                mooring_name,
                instruments,
                stack_exists=stack_exists,
                grid_exists=grid_exists,
                current=serial,
                in_instrument_subdir=True,
                array_href=_resolve_array_report_href(
                    out_dir, in_instrument_subdir=True
                ),
            ),
            "generated": generated,
            "history_entries": history_entries,
            # _make_instrument_b64s / _make_windows_b64s return list[str] (paginated,
            # multiple render_b64 calls) — not as_panel's str|None single-figure
            # contract — so they are not wrapped (a Stub cannot stand in for a list).
            "fig_ts_b64": (
                _make_instrument_b64s(src, instr_type, title=title, record=ts_record)
                if best_nc
                else None
            ),
            "fig_windows_b64": (
                _make_windows_b64s(
                    src,
                    instr_type,
                    vlines=_window_vlines,
                    stage1_nc=(_stage1_nc if _stage1_nc.exists() else None),
                    title=title,
                )
                if best_nc
                else None
            ),
            "fig_tsd_b64": (
                as_panel(
                    lambda bn=src, rec=tsd_record: _make_ts_diagram_b64(bn, record=rec)
                )
                if best_nc
                else None
            ),
            "fig_rose_b64": (
                as_panel(
                    lambda bn=src, r=rule, rt=rose_title: _make_instrument_rose_b64(
                        bn, title=rt, rule=r
                    )
                )
                if best_nc and instr_type.lower() != "adcp"
                else None
            ),
            "fig_adcp_velocity_b64": (
                as_panel(lambda bn=src, r=rule: _make_adcp_velocity_b64(bn, rule=r))
                if best_nc and instr_type.lower() == "adcp"
                else None
            ),
            "fig_adcp_rose_b64": (
                as_panel(lambda bn=src: _make_adcp_rose_b64(bn))
                if best_nc and instr_type.lower() in ("adcp", "rdi")
                else None
            ),
            "fig_trajectory_b64": (
                as_panel(lambda bn=src: _make_temperature_trajectory_b64(bn))
                if best_nc and instr_type.lower() == "aquadopp"
                else None
            ),
            "fig_speed_boxplot_b64": (
                as_panel(lambda bn=src: _make_speed_boxplot_b64(bn))
                if best_nc and instr_type.lower() == "aquadopp"
                else None
            ),
            "fig_hodograph_b64": (
                as_panel(lambda bn=src: _make_hodograph_b64(bn))
                if best_nc and instr_type.lower() == "aquadopp"
                else (
                    as_panel(lambda bn=src: _make_adcp_hodograph_b64(bn))
                    if best_nc and instr_type.lower() in ("adcp", "rdi")
                    else None
                )
            ),
            "fig_dt_b64": (
                as_panel(lambda bn=src, r=rule: _make_data_histogram_b64(bn, rule=r))
                if best_nc
                else None
            ),
            "qc_thresholds": _read_qc_thresholds(stage3_nc) if stage3_nc else [],
            "nc_meta": _read_nc_metadata(best_nc) if best_nc else {},
            "data_stage": data_stage,  # "stage1", "stage2", "stage3", or None
        }
        if ds_page is not None:
            drawn = ts_record.get("variables", [])
            captions["timeseries"] = _instrument_text.timeseries_caption(
                ds_page, drawn, ts_record.get("counts", {})
            )
            if tsd_record:
                captions["ts_diagram"] = _instrument_text.ts_caption(
                    ds_page, tsd_record, tsd_record.get("counts", {})
                )
            captions["rose"] = _instrument_text.rose_caption(ds_page, rule)
            captions["dist"] = _instrument_text.distributions_caption(
                ds_page, [v for v in drawn if v in ds_page.data_vars], rule
            )
        ctx["captions"] = captions
        analog_vars = ctx["nc_meta"].get("analog_vars", [])
        ctx["fig_analog_b64"] = (
            as_panel(lambda bn=src, av=analog_vars: _make_analog_timeseries_b64(bn, av))
            if best_nc and analog_vars
            else None
        )
        # Look up the per-instrument YAML entry for analog metadata.
        # Search both 'clamp' and 'inline' lists (the serial in inline entries
        # may contain a comma; safe_serial strips it to the first token).
        _instr_entries = list(cfg.get("clamp", cfg.get("instruments", []))) + [
            e
            for e in cfg.get("inline", [])
            if isinstance(e, dict) and "instrument" in e
        ]
        _yaml_entry: dict[str, Any] = next(
            (
                e
                for e in _instr_entries
                if safe_serial(str(e.get("serial", "")).split(",")[0].strip()) == serial
            ),
            {},
        )
        # Build per-channel YAML-source info for display in the template.
        # Variable name in NC is always "analog_input_{n}" (seasenselib native).
        # YAML key matches: analog_input_{n}, analog_input_{n}_units, etc.
        analog_yaml_info: list[dict[str, str]] = []
        for _av in analog_vars:
            _n = _av.replace("analog_input_", "").replace("analog_", "")
            _yaml_key = f"analog_input_{_n}"
            analog_yaml_info.append(
                {
                    "varname": _av,
                    "yaml_key": _yaml_key,
                    "label": str(_yaml_entry.get(_yaml_key, "")),
                    "units": str(_yaml_entry.get(f"{_yaml_key}_units", "")),
                    "serial": str(_yaml_entry.get(f"{_yaml_key}_serial_number", "")),
                }
            )
        ctx["analog_yaml_info"] = analog_yaml_info
        ctx["declination_reason"] = _declination_unapplied_reason(
            ds_page if best_nc is not None and best_nc == stage3_nc else None
        )
        ctx["declination_warn"] = bool(ctx["declination_reason"])

        try:
            ctx["report"] = resolve(_profile(captions), ctx, _panels(captions))
            # Instrument pages sit a level deeper (instrument/), so ancestors are
            # rebased by "../" back to the mooring directory; the mooring crumb links
            # up to the summary. Always shown, with ancestors in front when given.
            ctx["breadcrumb"] = page_crumbs(
                breadcrumb,
                "../",
                [(mooring_name, mooring_report_link), (serial, None)],
            )
            html = render_template("instrument.html", **ctx)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(html, encoding="utf-8")
            log.info("%s  %s", prefix, out_path.name)
        except Exception as exc:
            log.error("%s  ERROR: %s", prefix, exc)
