"""Array-level HTML summary report.

Reads a ``*.array.yaml`` file, gathers per-mooring metadata from each
mooring's YAML, draws a simple position map, and writes a single HTML
index page with clickable links to every mooring's report.

Entry point: :func:`generate_array_report`.
"""

from __future__ import annotations

import logging
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from markupsafe import escape

from oceanarray import logger

from ..config.unit_tables import unit_label
from ..paths import safe_serial
from ..plotters.helpers import grid_despine
from ._env import render_template
from ._html_helpers import _format_duration, _parse_dt, _stage_files
from ._plots import render_b64
from ._read import _read_instrument_info
from ._vendored import design_tokens as report_tokens
from ._vendored.leaflet import coastline_geojson, load_leaflet, safe_json
from .design import ARRAY_MAP_LEAFLET

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lat/lon parsing
# ---------------------------------------------------------------------------


def _parse_decdeg(val: Any) -> float | None:
    """Parse a latitude or longitude value to decimal degrees."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    try:
        return float(s)
    except ValueError:
        pass
    # Degrees + decimal minutes: "65 43.913" (sign gives hemisphere) or
    # "029 25.878 W" (hemisphere letter).
    parts = s.split()
    if len(parts) >= 2:
        try:
            deg = float(parts[0])
            minutes = float(parts[1])
            dd = abs(deg) + minutes / 60.0
            if deg < 0:
                dd = -dd
            if len(parts) >= 3:
                hemi = parts[2].strip(".,").upper()
                if hemi in ("S", "W"):
                    dd = -abs(dd)
        except (ValueError, IndexError):
            pass
        else:
            return dd
    return None


# ---------------------------------------------------------------------------
# Mooring metadata extraction
# ---------------------------------------------------------------------------


def _resolve_lat_lon(cfg: dict[str, Any]) -> tuple:
    """Return (lat_dd, lon_dd) decimal degrees from a mooring YAML dict."""
    lat_raw = (
        cfg.get("seabed_latitude")
        or cfg.get("deployment_latitude")
        or cfg.get("planned_latitude")
        or cfg.get("latitude")
    )
    lon_raw = (
        cfg.get("seabed_longitude")
        or cfg.get("deployment_longitude")
        or cfg.get("planned_longitude")
        or cfg.get("longitude")
    )
    return _parse_decdeg(lat_raw), _parse_decdeg(lon_raw)


def _count_instruments(cfg: dict[str, Any]) -> int:
    """Count instruments in a mooring YAML (clamp or instruments key)."""
    entries = cfg.get("clamp") or cfg.get("instruments") or []
    return sum(1 for e in entries if isinstance(e, dict) and not e.get("skip"))


# ---------------------------------------------------------------------------
# Map figure
# ---------------------------------------------------------------------------


def _square_map_limits(
    yl0: float, yl1: float, xl0: float, xl1: float, cos_lat: float
) -> tuple[float, float, float, float]:
    """Widen longitude so the extent is roughly square in Mercator proportion."""
    lon_span = xl1 - xl0
    lat_span = yl1 - yl0
    # Mercator-equivalent width (as ctdcast's cruise maps); widen symmetrically
    # so moorings strung along a line do not render as a sliver.
    x_width = lon_span * cos_lat
    if lat_span > x_width:
        lon_span_sq = lat_span / cos_lat
        extra = (lon_span_sq - lon_span) / 2
        xl0, xl1 = xl0 - extra, xl1 + extra
    return yl0, yl1, xl0, xl1


def _format_deg_min(dd: float | None, *, is_lat: bool) -> str:
    """Format decimal degrees as degrees + decimal minutes with a hemisphere letter."""
    if dd is None:
        return "—"
    hemi = ("N" if dd >= 0 else "S") if is_lat else ("E" if dd >= 0 else "W")
    a = abs(dd)
    deg = int(a)
    return f"{deg:0{2 if is_lat else 3}d}° {(a - deg) * 60:05.2f}′ {hemi}"


def _build_array_masthead(
    array_cfg: dict[str, Any],
    rows: list[dict[str, Any]],
    type_summary: list[dict[str, Any]],
    deploy_dt_arr: datetime | None,
    recover_dt_arr: datetime | None,
    array_yaml_path: Path,
) -> dict[str, str]:
    """Return the array masthead metadata from the YAMLs (missing → em-dash)."""
    # Nothing is invented: values come from the YAMLs or the per-mooring rows.
    lats = [r["lat"] for r in rows if r["lat"] is not None]
    lons = [r["lon"] for r in rows if r["lon"] is not None]
    wds = [float(r["waterdepth"]) for r in rows if r["waterdepth"] is not None]
    deps = [r["_deploy_dt"] for r in rows if r["_deploy_dt"] is not None]
    recs = [r["_recover_dt"] for r in rows if r["_recover_dt"] is not None]
    dep_dt = deploy_dt_arr or (min(deps) if deps else None)
    rec_dt = recover_dt_arr or (max(recs) if recs else None)

    vocab = {
        str(v.get("instrument_in", "")).lower(): v.get("instrument_out")
        for v in array_cfg.get("vocab", [])
        if isinstance(v, dict)
    }
    by_type: list[str] = []
    skipped = 0
    for t in type_summary:
        skipped += t.get("skipped", 0)
        active = t["deployed"] - t.get("skipped", 0)
        if active > 0:
            by_type.append(f"{vocab.get(t['itype'].lower(), t['itype'])} {active}")
    total = sum(r["n_instruments"] for r in rows)
    recovered = sum(1 for r in rows if r["_recover_dt"] is not None)
    deployed_only = len(rows) - recovered

    lat_dd = _parse_decdeg(array_cfg.get("latitude"))
    lon_dd = _parse_decdeg(array_cfg.get("longitude"))
    wd_min, wd_max = array_cfg.get("waterdepth_min"), array_cfg.get("waterdepth_max")

    instruments = str(total)
    if by_type:
        instruments += " (" + " · ".join(by_type) + ")"
    if skipped:
        instruments += f" · {skipped} skipped"

    return {
        "deployed": dep_dt.strftime("%Y-%m-%d") if dep_dt else "—",
        "recovered": rec_dt.strftime("%Y-%m-%d") if rec_dt else "—",
        "duration": _format_duration(dep_dt, rec_dt),
        "position": (
            f"{_format_deg_min(lat_dd, is_lat=True)} / {_format_deg_min(lon_dd, is_lat=False)}"
            if lat_dd is not None and lon_dd is not None
            else "—"
        ),
        "extent": (
            f"{_format_deg_min(min(lats), is_lat=True)}–{_format_deg_min(max(lats), is_lat=True)} / "
            f"{_format_deg_min(min(lons), is_lat=False)}–{_format_deg_min(max(lons), is_lat=False)}"
            if lats and lons
            else "—"
        ),
        "waterdepth": (
            f"{wd_min}–{wd_max} m"
            if wd_min is not None and wd_max is not None
            else (f"{min(wds):.0f}–{max(wds):.0f} m" if wds else "—")
        ),
        "instruments": instruments if total else "—",
        "status": (
            f"{recovered} recovered"
            + (f" · {deployed_only} deployed" if deployed_only else "")
        ),
        "processed": "stage 3: {} · stack: {} · grid: {}".format(
            sum(1 for r in rows if r["report_exists"]),
            sum(1 for r in rows if r["stack_exists"]),
            sum(1 for r in rows if r["grid_exists"]),
        ),
        "source": array_yaml_path.name,
    }


def _make_array_map_b64(rows: list[dict[str, Any]]) -> str | None:
    """Return a base64-encoded PNG of mooring positions, or None on failure."""
    import math

    import matplotlib.pyplot as plt

    pts = [
        (r["lat"], r["lon"])
        for r in rows
        if r["lat"] is not None and r["lon"] is not None
    ]
    if not pts:
        return None

    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    cos_lat = math.cos(math.radians(sum(lats) / len(lats)))
    # Framed extent: data bounds + a margin, widened to a Mercator-square aspect so a
    # line of moorings frames sensibly rather than as a sliver.
    lat_margin = max(0.05, (max(lats) - min(lats)) * 0.1)
    lon_margin = max(0.05, (max(lons) - min(lons)) * 0.1)
    yl0, yl1 = min(lats) - lat_margin, max(lats) + lat_margin
    xl0, xl1 = min(lons) - lon_margin, max(lons) + lon_margin
    yl0, yl1, xl0, xl1 = _square_map_limits(yl0, yl1, xl0, xl1, cos_lat)
    # Size the figure from the framed extent so the map is undistorted.
    width_in = report_tokens.W_HALF
    lon_span, lat_span = (xl1 - xl0), (yl1 - yl0)
    height_in = width_in * lat_span / (lon_span * cos_lat) if lon_span > 0 else 5.0
    height_in = min(8.0, max(2.5, height_in))
    # Family TRACK_COLORS so markers match the Leaflet map; no in-figure title
    # (the section heading names the map).
    colors = report_tokens.TRACK_COLORS

    def _draw() -> plt.Figure:
        fig, ax = plt.subplots(figsize=(width_in, height_in))
        ax.set_aspect(1.0 / cos_lat)
        for idx, r in enumerate(rows):
            if r["lat"] is None or r["lon"] is None:
                continue
            color = colors[idx % len(colors)]
            r["color_hex"] = color
            ax.scatter(r["lon"], r["lat"], s=60, color=color, zorder=3)
            if r.get("_recover_dt") is not None:
                ax.annotate(
                    r["mooring"],
                    (r["lon"], r["lat"]),
                    textcoords="offset points",
                    xytext=(5, 3),
                    fontsize=report_tokens.ANNOT_FS,
                    color=color,
                )
        ax.set_xlim(xl0, xl1)
        ax.set_ylim(yl0, yl1)
        ax.set_xlabel(f"Longitude ({unit_label('degrees_east')})")
        ax.set_ylabel(f"Latitude ({unit_label('degrees_north')})")
        grid_despine(ax)
        plt.tight_layout()
        return fig

    return render_b64(
        _draw, source="oceanarray/reports/_array.py:_make_array_map_b64", optional=True
    )


# ---------------------------------------------------------------------------
# Instrument status helpers
# ---------------------------------------------------------------------------

_TYPE_ORDER = [
    "microcat",
    "aquadopp",
    "rbrsolo",
    "tr1050",
    "seapoint",
    "adcp",
    "ADCP",
]


def _build_mooring_instruments(
    proc_dir: Path,
    mooring_id: str,
    mcfg: dict[str, Any],
    recover_dt: Any,
) -> list[dict[str, Any]]:
    """Return a lightweight instrument status list for array-level tables."""
    # proc_dir is the cruise-level processed dir; recover_dt is the YAML recovery time.
    from oceanarray.instruments import instrument_entries

    mooring_proc = proc_dir / mooring_id
    waterdepth = mcfg.get("waterdepth")
    entries = instrument_entries(mcfg)

    results = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        serial = safe_serial(entry.get("serial", ""))
        itype = entry.get("instrument", "unknown")
        hab = entry.get("hab") or entry.get("hab_bottom") or entry.get("hab_top")
        try:
            hab = float(hab) if hab is not None else None
        except (TypeError, ValueError):
            hab = None
        depth = (
            float(waterdepth) - hab
            if (waterdepth is not None and hab is not None)
            else None
        )

        skipped = bool(entry.get("skip"))
        skip_reason = entry.get("skip_reason") or ("skip: true" if skipped else "")

        stages = _stage_files(mooring_proc, itype, mooring_id, serial)
        stage1_exists = bool(stages.get("stage1"))
        stage3_exists = bool(stages.get("stage3"))

        no_raw = not skipped and not stage1_exists

        nc_info = _read_instrument_info(mooring_proc, itype, mooring_id, serial)
        t_end = nc_info.get("t_end") if nc_info and not nc_info.get("error") else None
        t_end_raw = (
            nc_info.get("t_end_raw") if nc_info and not nc_info.get("error") else None
        )
        n_records = (
            nc_info.get("n_records") if nc_info and not nc_info.get("error") else None
        )

        stopped_early = False
        if recover_dt and t_end_raw is not None:
            rec_np = np.datetime64(recover_dt.replace(tzinfo=None).isoformat(), "ns")
            gap_s = float((rec_np - t_end_raw) / np.timedelta64(1, "s"))
            stopped_early = gap_s > 12 * 3600

        complete = stage3_exists and not skipped and not stopped_early and not no_raw

        results.append(
            {
                "serial": serial,
                "itype": itype,
                "hab": hab,
                "depth": depth,
                "skipped": skipped or no_raw,
                "skip_reason": skip_reason
                if skipped
                else ("no raw file" if no_raw else ""),
                "stopped_early": stopped_early,
                "complete": complete,
                "n_records": n_records,
                "t_end": t_end,
            }
        )
    return results


def _build_type_summary(
    mooring_instruments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate per-instrument records into a per-type summary table."""
    from collections import defaultdict

    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"deployed": 0, "complete": 0, "skipped": 0, "stopped_early": 0}
    )
    for instr in mooring_instruments:
        itype = instr["itype"].lower()
        counts[itype]["deployed"] += 1
        if instr["complete"]:
            counts[itype]["complete"] += 1
        if instr["skipped"]:
            counts[itype]["skipped"] += 1
        if instr["stopped_early"]:
            counts[itype]["stopped_early"] += 1

    # Sort: canonical order first, then alphabetical remainder
    canonical = [t.lower() for t in _TYPE_ORDER]
    all_types = list(counts.keys())
    ordered = [t for t in canonical if t in all_types] + sorted(
        t for t in all_types if t not in canonical
    )

    return [
        {
            "itype": itype,
            "deployed": counts[itype]["deployed"],
            "complete": counts[itype]["complete"],
            "skipped": counts[itype]["skipped"],
            "stopped_early": counts[itype]["stopped_early"],
            "notes": "",
        }
        for itype in ordered
    ]


def _array_map_payload(rows: list[dict[str, Any]]) -> tuple[str, str, str]:
    """Return (payload, coastline, bounds) JSON strings for the Leaflet array map."""
    marks = [r for r in rows if r.get("lat") is not None and r.get("lon") is not None]
    payload = []
    for r in marks:
        # Per-marker info HTML is pre-escaped here, then embedded via safe_json.
        info = [
            f"<strong>{escape(r['mooring'])}</strong>",
            f"{r['lat']:.4f}, {r['lon']:.4f}",
        ]
        if r.get("waterdepth"):
            info.append(f"{escape(str(r['waterdepth']))} m")
        info.append(f"{escape(r['deploy_time'])} &ndash; {escape(r['recover_time'])}")
        info.append(f"{escape(str(r['n_instruments']))} instruments")
        if r.get("report_exists"):
            info.append(
                f'<a href="{escape(r["report_href"])}">&rarr; Mooring report</a>'
            )
        payload.append(
            {
                "id": r["mooring"],
                "track": [[r["lat"], r["lon"]]],
                "color": r.get("color_hex") or "#1a3a5c",
                "href": r["report_href"] if r.get("report_exists") else "",
                "info": "<br>".join(info),
            }
        )
    # No positioned mooring: payload "[]", coastline and bounds "null".
    if not marks:
        return safe_json([]), "null", "null"
    lats = [r["lat"] for r in marks]
    lons = [r["lon"] for r in marks]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    # Pad the fit-bounds so a single marker (or a tight cluster) frames with context
    # instead of fit-to-bounds collapsing onto a zero-size box and over-zooming.
    lat_pad = max((lat_max - lat_min) * 0.15, 0.5)
    lon_pad = max((lon_max - lon_min) * 0.15, 0.5)
    bounds_json = safe_json(
        [[lat_min - lat_pad, lon_min - lon_pad], [lat_max + lat_pad, lon_max + lon_pad]]
    )
    coastline_json = coastline_geojson(
        (lon_min, lat_min, lon_max, lat_max), pad_deg=1.0
    )
    if coastline_json == "null":
        _log.warning(
            "array map: coastline unavailable (cartopy/shapely missing, or no land "
            "in the clip extent); map shows markers only"
        )
    return safe_json(payload), coastline_json, bounds_json


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_array_report(
    array_yaml_path: Path,
    proc_dir: Path,
    out_path: Path | None = None,
    force: bool = False,
    report_dir: Path | None = None,
) -> Path | None:
    """Generate an HTML array-level summary report from an array YAML file.

    Parameters
    ----------
    array_yaml_path : Path
        Path to the ``*.array.yaml`` file.
    proc_dir : Path
        Root processing directory.  Each mooring directory is expected to be
        ``proc_dir / mooring_id``.
    out_path : Path, optional
        Output HTML path.  Defaults to
        ``proc_dir / "{array_name}_array_report.html"`` (or
        ``report_dir / "{array_name}_array_report.html"`` when *report_dir* is
        set).
    force : bool
        Overwrite existing output file.
    report_dir : Path, optional
        Central report directory.  When provided, each mooring's reports are
        expected under ``report_dir / mooring_id /`` (the layout produced by
        ``oceanarray report --report-dir``).  The array report itself is
        written to ``report_dir / "{array_name}_array_report.html"`` and the
        per-mooring links use the shorter relative path
        ``{mooring}/{mooring}_report.html``.  When absent (default), the
        legacy layout ``{mooring}/report/{mooring}_report.html`` is assumed.

    Returns
    -------
    Path or None
        Path to the written HTML file, or None on failure.

    """
    array_yaml_path = Path(array_yaml_path)
    proc_dir = Path(proc_dir)

    if not array_yaml_path.exists():
        warnings.warn(f"array YAML not found: {array_yaml_path}", stacklevel=2)
        return None

    with array_yaml_path.open() as fh:
        array_cfg = yaml.safe_load(fh)

    array_name = array_cfg.get("name", array_yaml_path.stem)

    if out_path is None:
        _out_root = report_dir if report_dir is not None else proc_dir
        out_path = _out_root / f"{array_name}_array_report.html"

    if out_path.exists() and not force:
        logger.status("skip", str(out_path))
        return out_path

    mooring_entries = array_cfg.get("moorings", [])
    rows: list[dict[str, Any]] = []
    all_instruments: list[dict[str, Any]] = []  # flat list across all moorings
    mooring_completeness: list[dict[str, Any]] = []

    for pos_idx, entry in enumerate(mooring_entries, start=1):
        mooring_id = entry.get("mooring", "")
        config_name = entry.get("config", f"{mooring_id}.mooring.yaml")
        mooring_yaml = proc_dir / mooring_id / config_name

        mcfg: dict[str, Any] = {}
        if mooring_yaml.exists():
            try:
                with mooring_yaml.open() as fh:
                    mcfg = yaml.safe_load(fh) or {}
            except Exception as exc:  # noqa: BLE001
                warnings.warn(f"could not read {mooring_yaml}: {exc}", stacklevel=2)
        else:
            warnings.warn(f"mooring YAML not found: {mooring_yaml}", stacklevel=2)

        lat, lon = _resolve_lat_lon(mcfg)
        deploy_dt = _parse_dt(mcfg.get("deployment_time"))
        recover_dt = _parse_dt(mcfg.get("recovery_time"))

        if report_dir is not None:
            # Centralised layout: report_dir/{mooring}/{mooring}_report.html
            mooring_rpt_dir = report_dir / mooring_id
            _href_prefix = mooring_id
        else:
            # Legacy layout: proc_dir/{mooring}/report/{mooring}_report.html
            mooring_rpt_dir = proc_dir / mooring_id / "report"
            _href_prefix = f"{mooring_id}/report"

        rows.append(
            {
                "position": entry.get("position", str(pos_idx)),
                "mooring": mooring_id,
                "lat": lat,
                "lon": lon,
                "color_hex": None,
                "waterdepth": mcfg.get("waterdepth"),
                "deploy_time": deploy_dt.strftime("%Y-%m-%d %H:%M")
                if deploy_dt
                else "—",
                "recover_time": recover_dt.strftime("%Y-%m-%d %H:%M")
                if recover_dt
                else "—",
                "duration": _format_duration(deploy_dt, recover_dt),
                "_deploy_dt": deploy_dt,
                "_recover_dt": recover_dt,
                "n_instruments": _count_instruments(mcfg),
                "report_exists": (
                    mooring_rpt_dir / f"{mooring_id}_report.html"
                ).exists(),
                "stack_exists": (
                    mooring_rpt_dir / f"{mooring_id}_stack_report.html"
                ).exists(),
                "grid_exists": (
                    mooring_rpt_dir / f"{mooring_id}_grid_report.html"
                ).exists(),
                "report_href": f"{_href_prefix}/{mooring_id}_report.html",
                "stack_href": f"{_href_prefix}/{mooring_id}_stack_report.html",
                "grid_href": f"{_href_prefix}/{mooring_id}_grid_report.html",
            }
        )

        # Collect per-instrument status for summary tables
        instrs = _build_mooring_instruments(proc_dir, mooring_id, mcfg, recover_dt)
        all_instruments.extend(instrs)
        n_deployed = len(instrs)
        n_complete = sum(1 for i in instrs if i["complete"])
        n_skipped = sum(1 for i in instrs if i["skipped"])
        n_early = sum(1 for i in instrs if i["stopped_early"])
        mooring_completeness.append(
            {
                "mooring": mooring_id,
                "waterdepth": mcfg.get("waterdepth"),
                "deployed": n_deployed,
                "complete": n_complete,
                "skipped": n_skipped,
                "stopped_early": n_early,
                "deploy_time": deploy_dt.strftime("%Y-%m-%d %H:%M")
                if deploy_dt
                else "—",
                "recover_time": recover_dt.strftime("%Y-%m-%d %H:%M")
                if recover_dt
                else "—",
                "duration": _format_duration(deploy_dt, recover_dt),
            }
        )

    fig_map_b64 = _make_array_map_b64(rows)
    # Array map: the vendored Leaflet track_map when design.ARRAY_MAP_LEAFLET is on;
    # while off (the macro can't yet draw a single-point mooring — see F8), an empty
    # payload makes track_map show the matplotlib PNG fallback, so the map is never blank.
    _, leaflet_css = load_leaflet()
    if ARRAY_MAP_LEAFLET:
        payload_json, coastline_json, bounds_json = _array_map_payload(rows)
        leaflet_js, _ = load_leaflet()
    else:
        payload_json, coastline_json, bounds_json = "[]", "null", "null"
        leaflet_js = ""

    deploy_dt_arr = _parse_dt(array_cfg.get("deployment_time"))
    recover_dt_arr = _parse_dt(array_cfg.get("recovery_time"))
    type_summary = _build_type_summary(all_instruments)
    masthead = _build_array_masthead(
        array_cfg, rows, type_summary, deploy_dt_arr, recover_dt_arr, array_yaml_path
    )

    ctx = {
        "array_name": array_name,
        "year": array_cfg.get("year"),
        "cruise": array_cfg.get("cruise"),
        "ship": array_cfg.get("ship"),
        "project": array_cfg.get("project"),
        "deploy_time": deploy_dt_arr.strftime("%Y-%m-%d") if deploy_dt_arr else "",
        "recover_time": recover_dt_arr.strftime("%Y-%m-%d") if recover_dt_arr else "",
        "moorings": rows,
        "fig_map_b64": fig_map_b64,
        "payload_json": payload_json,
        "coastline_json": coastline_json,
        "bounds_json": bounds_json,
        "leaflet_js": leaflet_js,
        "leaflet_css": leaflet_css,
        "map_height": 460,
        "type_summary": type_summary,
        "masthead": masthead,
        "mooring_completeness": mooring_completeness,
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }

    html = render_template("array.html", **ctx)
    out_path.write_text(html, encoding="utf-8")
    logger.status("file", str(out_path))
    return out_path
