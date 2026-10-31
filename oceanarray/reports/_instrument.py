"""Per-instrument HTML report template and page generator."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oceanarray import paths

from .. import parameters as params
from ._env import (
    render_history,
    render_nc_coords,
    render_nc_globals,
    render_nc_scalars,
    render_nc_variables,
    render_template,
)
from ._figdebug import as_panel
from ._html_helpers import (
    _duration_str,
    _file_info,
    _find_array_report_href,
    _parse_dt,
    _parse_history,
    _read_nc_metadata,
    _read_qc_summary,
    _read_qc_thresholds,
    _safe_serial,
    _should_skip,
    _stage_file_details,
    make_fig_panel,
)
from ._navs import mooring_nav
from ._plots import (
    _make_adcp_hodograph_b64,
    _make_adcp_rose_b64,
    _make_adcp_velocity_b64,
    _make_analog_timeseries,
    _make_data_histogram,
    _make_hodograph_b64,
    _make_instrument_fig,
    _make_instrument_rose_b64,
    _make_speed_boxplot,
    _make_temperature_trajectory,
    _make_ts_diagram,
    _make_windows_fig,
)
from ._vendored.page_model import Panel, PanelGroup, Profile, Section, resolve

# ---------------------------------------------------------------------------
# Per-instrument HTML template
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Section manifest — instrument registry (rep/07)
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
INSTRUMENT_CAPTIONS: dict[str, str] = {
    "adcp_velocity": (
        "Time–range colour plots of the four velocity components (east, north, "
        "up, error). Y-axis: range from transducer (m). Colour scale: symmetric "
        "about zero; percentile 2/98 of all components combined. Magnetic "
        "declination is applied at stage 3 only."
    ),
    "ts_diagram": (
        "Coloured by pressure (or sample index). × = suspect | × = bad (QC flags)."
    ),
    "adcp_rose": (
        "Depth-average followed by bins nearest 100, 200, 300 and 400 m from the "
        "transducer. Bins with fewer than two valid samples are omitted. "
        "Direction toward which the current flows; 0° = N, clockwise. Magnetic "
        "declination is applied at stage 3 only."
    ),
    "rose": (
        "ENU panels split by QC flag: usable (flags 0–2, 7 nominal, 8 "
        "interpolated; Blues), suspect (flag 3, Oranges), fail (flag 4, Reds); "
        "missing (9) not drawn. Direction toward which the "
        "current flows; 0° = N, clockwise."
    ),
    "trajectory": (
        "Pseudo-Lagrangian displacement obtained by integrating east/north "
        "velocity over time (Euler forward; NaN velocities set to zero). Coloured "
        "by temperature. Origin (0, 0) = deployment position; axes in metres."
    ),
    "hodograph": (
        f"East vs north velocity ({params.vunit('east_velocity')}). Left: full "
        "record. Right: eddy component — raw minus 4-day low-pass (rolling mean)."
    ),
    "dist": (
        "Orange dashed = suspect threshold | Red dotted = fail threshold "
        "(gross-range QC). Histogram shows non-bad data only; bad-flagged count "
        "noted in red."
    ),
    "analog": (
        "Full-record time series of analog channel variables containing "
        "non-zero, non-NaN data. One panel per channel. Labels and units are read "
        "from the mooring YAML — update them there to change what appears here."
    ),
}


def _has_fig(key: str) -> Callable:
    """Return an applies_to/`unavailable`-style predicate: figure *key* is present."""
    return lambda c: bool(c.get(key))


#: Instrument figure-panel factory (shared with the mooring page, bound to the
#: instrument caption dict).
_fig_panel = make_fig_panel(INSTRUMENT_CAPTIONS)


def _img_panel(pid: str, b64: str) -> Panel:
    """Build a bare-``.fig`` panel rendering a fixed base64 image (paginated runs)."""
    return Panel(pid, render=lambda _c, _b=b64: _b, slot=None)


def _instrument_history_unavailable(c: dict) -> str | None:
    """Reason the history panel cannot render, or None."""
    return None if c.get("history_entries") else "No history attribute found."


def _empty_note(pid: str, reason: str, when_empty: Callable) -> Panel:
    """Build a panel that shows *reason* as a stub only when *when_empty(ctx)* is True.

    Used so a PanelGroup section (paginated time-series, start/end windows) keeps
    its specific "nothing to show" message — e.g. "No plottable variables found."
    — instead of falling to the resolver's generic empty-section stub.  It applies
    only when the run is empty, so it adds nothing when there are figures.
    """
    return Panel(
        pid,
        render=lambda _c: None,
        kind="html",
        applies_to=when_empty,
        unavailable_if=lambda _c: reason,
    )


#: Instrument panel registry (dict-context: panels read ctx by key).
INSTRUMENT_PANELS: dict[str, Panel] = {
    "files": Panel(
        "files",
        render=lambda c: render_template(
            "_instrument_files.html",
            raw_file=c["raw_file"],
            stage_files=c["stage_files"],
        ),
        kind="table",
    ),
    "history": Panel(
        "history",
        render=lambda c: render_history(c["history_entries"]),
        kind="html",
        unavailable_if=_instrument_history_unavailable,
    ),
    "timeseries_empty": _empty_note(
        "timeseries_empty",
        "No plottable variables found.",
        lambda c: not c.get("fig_ts_b64"),
    ),
    "windows_empty": _empty_note(
        "windows_empty",
        "Insufficient data for start/end windows.",
        lambda c: not c.get("fig_windows_b64"),
    ),
    "adcp_velocity": _fig_panel("adcp_velocity", "fig_adcp_velocity_b64"),
    "windows_note": Panel(
        "windows_note",
        render=lambda _c: render_template("_instrument_windows_note.html"),
        kind="html",
        applies_to=_has_fig("fig_windows_b64"),
    ),
    "ts_diagram": _fig_panel("ts_diagram", "fig_tsd_b64"),
    "adcp_rose": _fig_panel("adcp_rose", "fig_adcp_rose_b64"),
    "rose_declination": Panel(
        "rose_declination",
        render=lambda _c: render_template("_instrument_rose_note.html"),
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
    "dist": Panel(
        "dist",
        render=lambda c: c.get("fig_dt_b64"),
        slot=None,
        caption=INSTRUMENT_CAPTIONS["dist"],
        unavailable_if=lambda c: (
            None if c.get("fig_dt_b64") else "Not enough samples to compute."
        ),
    ),
    "qc": Panel(
        "qc",
        render=lambda c: render_template(
            "_instrument_qc.html",
            qc_summary=c["qc_summary"],
            qc_thresholds=c["qc_thresholds"],
        ),
        kind="table",
        applies_to=lambda c: bool(c.get("qc_summary")),
    ),
    "nc_coords": Panel(
        "nc_coords",
        render=lambda c: render_nc_coords(c["nc_meta"]),
        kind="table",
        applies_to=lambda c: bool(c.get("nc_meta", {}).get("coords_rows")),
    ),
    "nc_variables": Panel(
        "nc_variables",
        render=lambda c: render_nc_variables(c["nc_meta"]),
        kind="table",
    ),
    "nc_scalars": Panel(
        "nc_scalars",
        render=lambda c: render_nc_scalars(c["nc_meta"]),
        kind="table",
        applies_to=lambda c: bool(c.get("nc_meta", {}).get("scalar_rows")),
    ),
    "nc_globals": Panel(
        "nc_globals",
        render=lambda c: render_nc_globals(c["nc_meta"]),
        kind="table",
        applies_to=lambda c: bool(c.get("nc_meta", {}).get("global_groups")),
    ),
}

#: Paginated time-series images — one bare-``.fig`` panel per PNG in the list.
_TIMESERIES_IMGS = PanelGroup(
    over=lambda c: list(enumerate(c.get("fig_ts_b64") or [])),
    panel=lambda pair: _img_panel(f"timeseries_{pair[0]}", pair[1]),
)
#: Paginated start/end-window images.
_WINDOWS_IMGS = PanelGroup(
    over=lambda c: list(enumerate(c.get("fig_windows_b64") or [])),
    panel=lambda pair: _img_panel(f"windows_{pair[0]}", pair[1]),
)


#: Instrument sections.  ``start`` keeps its id (mooring.html deep-links to it).
#: Sections whose only figures come from a PanelGroup pair an ``*_empty`` note
#: panel (a specific "nothing to show" message) with the image run, so the
#: section is always kept via an applicable panel — no explicit section predicate
#: is needed.
INSTRUMENT_SECTIONS: dict[str, Section] = {
    "files": Section("files", "Files", ("files",)),
    "processing_history": Section(
        "processing_history",
        "Processing history",
        ("history",),
    ),
    "adcp_velocity": Section("adcp_velocity", "Velocity", ("adcp_velocity",)),
    "timeseries": Section(
        "timeseries",
        "Time series (full deployment)",
        ("timeseries_empty", _TIMESERIES_IMGS),
        intro="line = data; × = suspect; × = bad; + = interpolated.",
    ),
    "start": Section(
        "start",
        "Start & end windows — first / last 6 h",
        ("windows_note", "windows_empty", _WINDOWS_IMGS),
    ),
    "ts_diagram": Section("ts_diagram", "T-S diagram", ("ts_diagram",)),
    "adcp_rose": Section("adcp_rose", "Current roses", ("adcp_rose",)),
    "rose": Section("rose", "Current rose diagrams", ("rose_declination", "rose")),
    "trajectory": Section("trajectory", "Particle trajectory", ("trajectory",)),
    "hodograph": Section("hodograph", "Hodograph", ("hodograph",)),
    "speed": Section("speed", "Current speed distribution", ("speed",)),
    "analog": Section("analog", "Analog channels", ("analog", "analog_yaml")),
    "distributions": Section(
        "distributions",
        "Data value distributions",
        ("dist",),
    ),
    "qc": Section("qc", "QC flag breakdown", ("qc",)),
    "netcdf_variables": Section(
        "netcdf_variables",
        "NetCDF variables",
        ("nc_coords", "nc_variables", "nc_scalars"),
        role="appendix",
    ),
    "netcdf_attributes": Section(
        "netcdf_attributes",
        "NetCDF attributes",
        ("nc_globals",),
        role="appendix",
    ),
}

#: Default instrument profile.
INSTRUMENT_DEFAULT = Profile(
    numbering="flat",
    entries=(
        INSTRUMENT_SECTIONS["files"],
        INSTRUMENT_SECTIONS["processing_history"],
        INSTRUMENT_SECTIONS["adcp_velocity"],
        INSTRUMENT_SECTIONS["timeseries"],
        INSTRUMENT_SECTIONS["start"],
        INSTRUMENT_SECTIONS["ts_diagram"],
        INSTRUMENT_SECTIONS["adcp_rose"],
        INSTRUMENT_SECTIONS["rose"],
        INSTRUMENT_SECTIONS["trajectory"],
        INSTRUMENT_SECTIONS["hodograph"],
        INSTRUMENT_SECTIONS["speed"],
        INSTRUMENT_SECTIONS["analog"],
        INSTRUMENT_SECTIONS["distributions"],
        INSTRUMENT_SECTIONS["qc"],
        INSTRUMENT_SECTIONS["netcdf_variables"],
        INSTRUMENT_SECTIONS["netcdf_attributes"],
    ),
)


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

    """
    mooring_report_link = f"../{mooring_name}_report.html"
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    stack_exists = paths.product_file(proc_dir, mooring_name, "stack").exists()
    grid_exists = paths.product_file(proc_dir, mooring_name, "grid").exists()
    _d = cfg.get("deployment_cruise") or cfg.get("cruise", "—")
    _r = cfg.get("recovery_cruise") or cfg.get("cruise") or _d
    cruise = _d if _d == _r else f"{_d} / {_r}"
    serial_filter = {_safe_serial(s) for s in serials} if serials else None

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
            print(f"{prefix}  [skipped — no processed files]")
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
        if _should_skip(
            out_path, force, skip_existing, *([_source_nc] if _source_nc else [])
        ):
            print(f"{prefix}  {out_path.name}  [skip]")
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
        nc_file = best_nc.name if best_nc else "—"

        nc_info = instr.get("nc", {}) or {}
        n_records = nc_info.get("n_records", "—")
        t_start = nc_info.get("t_start", "—")
        t_end = nc_info.get("t_end", "—")
        dt_s = nc_info.get("dt_s")
        median_dt = f"{dt_s:.0f} s" if dt_s and dt_s == dt_s else "—"
        duration = _duration_str(_parse_dt(t_start), _parse_dt(t_end))

        history_entries: list[dict[str, str]] = []
        _sugg_deploy_utc: str | None = None
        _sugg_recover_utc: str | None = None
        if best_nc:
            try:
                import xarray as xr

                with xr.open_dataset(best_nc, decode_timedelta=False) as _ds:
                    history_entries = _parse_history(_ds.attrs.get("history", ""))
                    _sugg_deploy_utc = _ds.attrs.get("suggested_deployment_time_utc")
                    _sugg_recover_utc = _ds.attrs.get("suggested_recovery_time_utc")
            except Exception:
                pass

        # Build vertical-line markers for the start/end window plots.
        # Orange = auto-detected from pressure; green = set in the YAML.
        _yaml_deploy_str = cfg.get("deployment_time")
        _yaml_recover_str = cfg.get("recovery_time")
        # PyYAML may parse bare ISO datetime values as datetime objects; normalise to
        # ISO strings so the vlines normalizer in _plots.py can handle them uniformly.
        if hasattr(_yaml_deploy_str, "isoformat"):
            _yaml_deploy_str = _yaml_deploy_str.isoformat()
        if hasattr(_yaml_recover_str, "isoformat"):
            _yaml_recover_str = _yaml_recover_str.isoformat()
        _window_vlines: list[tuple] = []
        if _sugg_deploy_utc:
            _window_vlines.append((_sugg_deploy_utc, "#e67e22", "Suggested"))
        if _sugg_recover_utc:
            _window_vlines.append((_sugg_recover_utc, "#e67e22", "Suggested"))
        if _yaml_deploy_str:
            _window_vlines.append((_yaml_deploy_str, "#27ae60", "YAML"))
        if _yaml_recover_str:
            _window_vlines.append((_yaml_recover_str, "#27ae60", "YAML"))

        # File listing — raw source and stage1/2/3 NC files
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

        ctx = {
            "mooring_name": mooring_name,
            "cruise": cruise,
            "serial": serial,
            "instr_type": instr_type,
            "hab": instr["hab"],
            "depth": instr["depth"],
            "n_records": (
                f"{n_records:,}" if isinstance(n_records, int) else n_records
            ),
            "t_start": t_start,
            "t_end": t_end,
            "duration": duration,
            "median_dt": median_dt,
            "nc_file": nc_file,
            "raw_file": raw_file,
            "stage_files": stage_files,
            "mooring_report_link": mooring_report_link,
            "nav": mooring_nav(
                mooring_name,
                instruments,
                stack_exists=stack_exists,
                grid_exists=grid_exists,
                current=serial,
                in_instrument_subdir=True,
                array_href=_find_array_report_href(out_dir, in_instrument_subdir=True),
            ),
            "generated": generated,
            "history_entries": history_entries,
            # _make_instrument_fig / _make_windows_fig return list[str] (paginated,
            # multiple render_b64 calls) — not as_panel's str|None single-figure
            # contract — so they are not wrapped (a Stub cannot stand in for a list).
            "fig_ts_b64": (
                _make_instrument_fig(best_nc, instr_type) if best_nc else None
            ),
            "fig_windows_b64": (
                _make_windows_fig(
                    best_nc,
                    instr_type,
                    vlines=_window_vlines,
                    stage1_nc=(_stage1_nc if _stage1_nc.exists() else None),
                )
                if best_nc
                else None
            ),
            "fig_tsd_b64": (
                as_panel(lambda bn=best_nc: _make_ts_diagram(bn)) if best_nc else None
            ),
            "fig_rose_b64": (
                as_panel(lambda bn=best_nc: _make_instrument_rose_b64(bn))
                if best_nc and instr_type.lower() != "adcp"
                else None
            ),
            "fig_adcp_velocity_b64": (
                as_panel(lambda bn=best_nc: _make_adcp_velocity_b64(bn))
                if best_nc and instr_type.lower() == "adcp"
                else None
            ),
            "fig_adcp_rose_b64": (
                as_panel(lambda bn=best_nc: _make_adcp_rose_b64(bn))
                if best_nc and instr_type.lower() in ("adcp", "rdi")
                else None
            ),
            "fig_trajectory_b64": (
                as_panel(lambda bn=best_nc: _make_temperature_trajectory(bn))
                if best_nc and instr_type.lower() == "aquadopp"
                else None
            ),
            "fig_speed_boxplot_b64": (
                as_panel(lambda bn=best_nc: _make_speed_boxplot(bn))
                if best_nc and instr_type.lower() == "aquadopp"
                else None
            ),
            "fig_hodograph_b64": (
                as_panel(lambda bn=best_nc: _make_hodograph_b64(bn))
                if best_nc and instr_type.lower() == "aquadopp"
                else (
                    as_panel(lambda bn=best_nc: _make_adcp_hodograph_b64(bn))
                    if best_nc and instr_type.lower() in ("adcp", "rdi")
                    else None
                )
            ),
            "fig_dt_b64": (
                as_panel(lambda bn=best_nc: _make_data_histogram(bn))
                if best_nc
                else None
            ),
            "qc_summary": _read_qc_summary(stage3_nc) if stage3_nc else [],
            "qc_thresholds": _read_qc_thresholds(stage3_nc) if stage3_nc else [],
            "nc_meta": _read_nc_metadata(best_nc) if best_nc else {},
            "data_stage": data_stage,  # "stage1", "stage2", "stage3", or None
        }
        analog_vars = ctx["nc_meta"].get("analog_vars", [])
        ctx["fig_analog_b64"] = (
            as_panel(lambda bn=best_nc, av=analog_vars: _make_analog_timeseries(bn, av))
            if best_nc and analog_vars
            else None
        )
        # Look up the per-instrument YAML entry for analog metadata.
        # Search both 'clamp' and 'inline' lists (the serial in inline entries
        # may contain a comma; _safe_serial strips it to the first token).
        _instr_entries = list(cfg.get("clamp", cfg.get("instruments", []))) + [
            e
            for e in cfg.get("inline", [])
            if isinstance(e, dict) and "instrument" in e
        ]
        _yaml_entry: dict[str, Any] = next(
            (
                e
                for e in _instr_entries
                if _safe_serial(str(e.get("serial", "")).split(",")[0].strip())
                == serial
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
        # Warn if magnetic declination was not applied (lat/lon missing from YAML)
        _global_names = {
            attr
            for _title, rows in ctx["nc_meta"].get("global_groups", [])
            for attr, _type, _val in rows
        }
        ctx["declination_warn"] = (
            instr_type.lower() == "aquadopp"
            and "magnetic_declination" not in _global_names
        )
        # True when the NC contains instrument-frame XYZ velocities (rose has XYZ panel)
        _tvar_names = {
            r["name"]
            for _title, rows in ctx["nc_meta"].get("var_groups", [])
            for r in rows
        }
        ctx["rose_has_xyz"] = "velocity_x" in _tvar_names

        try:
            ctx["report"] = resolve(INSTRUMENT_DEFAULT, ctx, INSTRUMENT_PANELS)
            html = render_template("instrument.html", **ctx)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(html, encoding="utf-8")
            print(f"{prefix}  {out_path.name}")
        except Exception as exc:
            print(f"{prefix}  ERROR: {exc}")
