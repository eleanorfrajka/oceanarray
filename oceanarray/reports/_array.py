"""Year array page: the moorings of one array YAML, with a map and their instruments.

Reads a ``*.array.yaml`` file, gathers per-mooring metadata from each mooring's
YAML, draws a position map, and writes one HTML page linking every mooring's
pages.

Entry point: :func:`generate_array_report`.
"""

from __future__ import annotations

import json
import logging
import warnings
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from markupsafe import escape

from oceanarray import location, logger
from oceanarray import mooring as _mooring
from oceanarray.exceptions import MooringError
from oceanarray.instruments import instrument_entries

from ..config.unit_tables import unit_label
from ..paths import safe_serial
from ..plotters.helpers import grid_despine
from . import _masthead
from ._breadcrumb import page_crumbs
from ._env import render_template
from ._html_helpers import (
    STOPPED_EARLY_HOURS,
    _parse_dt,
    _stage_files,
    format_durations,
    span_seconds,
)
from ._navs import _TYPE_ORDER, _type_label, type_word
from ._plots import render_b64
from ._read import _read_instrument_info
from ._vendored import design_tokens as report_tokens
from ._vendored.leaflet import coastline_geojson, load_leaflet, safe_json
from .design import ARRAY_MAP_LEAFLET

_log = logging.getLogger(__name__)

#: Depth contours on the map (m) and their line colours, shallow to deep.
CONTOURS: dict[int, str] = {
    500: "#c6dbef",
    1000: "#9ecae1",
    2000: "#6baed6",
    2500: "#2c7fb8",
}
#: Bathymetry is averaged to about this resolution (degrees, 4 arc-minutes) before
#: contouring.
CONTOUR_RESOLUTION_DEG: float = 4.0 / 60.0
#: Contour pieces with fewer points than this (seamount specks) are dropped.
CONTOUR_MIN_POINTS: int = 30
#: Contours are drawn this far (degrees) beyond the moorings, so panning shows them.
CONTOUR_PAD_DEG: float = 3.0
#: The line of the vendored map script after which the contours are added, so
#: they are drawn once the map exists and before the markers.
CONTOUR_HOOK = "map.addControl(new ResetControl());"
CONTOUR_JS = """
  (function () {
    var CONTOURS = __CONTOURS__, COLORS = __COLORS__;
    L.geoJSON(CONTOURS, {
      interactive: false,
      style: function (f) { return { color: COLORS[f.properties.depth], weight: 0.9, opacity: 0.9 }; }
    }).addTo(map);
  })();"""

#: Why an instrument the description skips has no pages: not processed here, not
#: missing at the originator.
NOT_HERE = "not processed here"

#: A mooring's pages the year page links: key and file suffix.
_PAGES: tuple[tuple[str, str], ...] = (
    ("report", "_report.html"),
    ("stack", "_stack_report.html"),
    ("grid", "_grid_report.html"),
)


def _resolve_lat_lon(cfg: dict[str, Any]) -> tuple[float | None, float | None]:
    """Return (lat, lon) decimal degrees from a mooring YAML dict, (None, None) if unknown."""
    lat, lon, source = location.parse_latlon_with_source(cfg)
    if location.is_unknown_latlon_source(source):
        return None, None
    return lat, lon


def _square_map_limits(
    yl0: float, yl1: float, xl0: float, xl1: float, cos_lat: float
) -> tuple[float, float, float, float]:
    """Widen longitude so the extent is roughly square in Mercator proportion."""
    lon_span = xl1 - xl0
    lat_span = yl1 - yl0
    if lat_span > lon_span * cos_lat:
        extra = (lat_span / cos_lat - lon_span) / 2
        xl0, xl1 = xl0 - extra, xl1 + extra
    return yl0, yl1, xl0, xl1


def bathymetry_contours(
    path: Path, extent: tuple[float, float, float, float], cache: Path
) -> str | None:
    """Return depth contours over *extent* as GeoJSON, cached between runs.

    Parameters
    ----------
    path : Path
        A bathymetry file (GEBCO, ETOPO): one elevation variable (m, negative
        below sea level) on latitude and longitude.
    extent : tuple of float
        West, south, east, north, in degrees.
    cache : Path
        JSON file the contours are kept in, reused while the file, extent and
        depths are the same.

    Returns
    -------
    str or None
        A GeoJSON FeatureCollection, one MultiLineString per depth in
        :data:`CONTOURS` (property ``depth``, m); None, with a warning, when *path*
        does not exist or does not cover *extent*.
    """
    import matplotlib.pyplot as plt
    import xarray as xr

    key = {
        "extent": [round(v, 2) for v in extent],
        "depths": list(CONTOURS),
        "file": path.name,
    }
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("key") == key:
            return json.dumps(cached["geojson"], separators=(",", ":"))
    if not path.exists():
        _log.warning("no depth contours on the map: %s not found", path)
        return None
    west, south, east, north = extent
    with xr.open_dataset(path, engine="netcdf4") as ds:
        z = next(ds[v] for v in ds.data_vars if ds[v].ndim == 2)
        lat_dim, lon_dim = z.dims
        # Some files run north to south; sorted, the selection works either way.
        z = z.sortby([lat_dim, lon_dim]).sel(
            {lat_dim: slice(south, north), lon_dim: slice(west, east)}
        )
        if min(z.shape) >= 2:
            step = float(z[lon_dim].values[1] - z[lon_dim].values[0])
            n = max(1, round(CONTOUR_RESOLUTION_DEG / step))
            z = z.coarsen({lat_dim: n, lon_dim: n}, boundary="trim").mean().load()
    if min(z.shape) < 2:
        _log.warning(
            "no depth contours on the map: %s does not cover %.1f–%.1f°E, %.1f–%.1f°N",
            path.name,
            west,
            east,
            south,
            north,
        )
        return None
    depths = sorted(CONTOURS)
    fig, ax = plt.subplots()
    lines = ax.contour(z[lon_dim].values, z[lat_dim].values, -z.values, levels=depths)
    features = []
    for depth, segments in zip(depths, lines.allsegs, strict=True):
        coords = []
        for segment in segments:
            if len(segment) < CONTOUR_MIN_POINTS:
                continue
            points = np.round(segment, 2).tolist()
            coords.append(
                [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
            )
        features.append(
            {
                "type": "Feature",
                "properties": {"depth": depth},
                "geometry": {"type": "MultiLineString", "coordinates": coords},
            }
        )
    plt.close(fig)
    geojson = {"type": "FeatureCollection", "features": features}
    cache.write_text(
        json.dumps({"key": key, "geojson": geojson}, separators=(",", ":")),
        encoding="utf-8",
    )
    return json.dumps(geojson, separators=(",", ":"))


def _with_contours(html: str, contours: str) -> str:
    """Return the page with the depth contours added to its map script."""
    if html.count(CONTOUR_HOOK) != 1:
        _log.warning("no depth contours: the map script has changed (CONTOUR_HOOK)")
        return html
    js = CONTOUR_JS.replace("__CONTOURS__", contours).replace(
        "__COLORS__", json.dumps({str(k): v for k, v in CONTOURS.items()})
    )
    return html.replace(CONTOUR_HOOK, CONTOUR_HOOK + js)


def _make_array_map_b64(rows: list[dict[str, Any]]) -> str | None:
    """Return a base64 PNG of the mooring positions (the printed map), or None."""
    import math

    import matplotlib.pyplot as plt

    placed = [r for r in rows if r["lat"] is not None and r["lon"] is not None]
    if not placed:
        return None
    lats = [r["lat"] for r in placed]
    lons = [r["lon"] for r in placed]
    cos_lat = math.cos(math.radians(sum(lats) / len(lats)))
    lat_margin = max(0.05, (max(lats) - min(lats)) * 0.1)
    lon_margin = max(0.05, (max(lons) - min(lons)) * 0.1)
    yl0, yl1, xl0, xl1 = _square_map_limits(
        min(lats) - lat_margin,
        max(lats) + lat_margin,
        min(lons) - lon_margin,
        max(lons) + lon_margin,
        cos_lat,
    )
    width_in = report_tokens.W_HALF
    lon_span, lat_span = (xl1 - xl0), (yl1 - yl0)
    height_in = width_in * lat_span / (lon_span * cos_lat) if lon_span > 0 else 5.0
    height_in = min(8.0, max(2.5, height_in))

    def _draw() -> plt.Figure:
        fig, ax = plt.subplots(figsize=(width_in, height_in))
        ax.set_aspect(1.0 / cos_lat)
        for r in placed:
            ax.scatter(r["lon"], r["lat"], s=60, color=r["color_hex"], zorder=3)
            ax.annotate(
                r["mooring"],
                (r["lon"], r["lat"]),
                textcoords="offset points",
                xytext=(5, 3),
                fontsize=report_tokens.ANNOT_FS,
                color=r["color_hex"],
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


def _build_mooring_instruments(
    proc_dir: Path,
    mooring_id: str,
    mcfg: dict[str, Any],
    recover_dt: Any,
) -> list[dict[str, Any]]:
    """Return one status record per instrument of a mooring, for the array tables."""
    mooring_proc = proc_dir / mooring_id
    results = []
    for entry in instrument_entries(mcfg):
        if not isinstance(entry, dict):
            continue
        serial = safe_serial(entry.get("serial", ""))
        # The stage files are in a folder named as the YAML writes the type.
        folder = str(entry.get("instrument", "unknown"))
        itype = folder.strip().lower()
        skipped = bool(entry.get("skip"))
        stages = _stage_files(mooring_proc, folder, mooring_id, serial)
        no_file = not skipped and not stages.get("stage1")
        info = _read_instrument_info(mooring_proc, folder, mooring_id, serial)
        t_end_raw = info.get("t_end_raw") if info and not info.get("error") else None
        stopped_early = False
        if recover_dt and t_end_raw is not None:
            rec_np = np.datetime64(recover_dt.replace(tzinfo=None).isoformat(), "ns")
            gap_s = float((rec_np - t_end_raw) / np.timedelta64(1, "s"))
            stopped_early = gap_s > STOPPED_EARLY_HOURS * 3600
        processed = bool(stages.get("stage3")) and not skipped
        results.append(
            {
                "serial": serial,
                "itype": itype,
                "skipped": skipped or no_file,
                "skip_reason": NOT_HERE
                if skipped
                else ("no file found" if no_file else ""),
                "yaml_reason": str(entry.get("skip_reason") or ""),
                "stopped_early": stopped_early,
                "processed": processed,
            }
        )
    return results


def _build_type_summary(
    mooring_instruments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the instrument-type table: one row per type, deployed and processed."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for instr in mooring_instruments:
        by_type.setdefault(instr["itype"], []).append(instr)
    canonical = [t.lower() for t in _TYPE_ORDER]
    order = [t for t in canonical if t in by_type] + sorted(
        t for t in by_type if t not in canonical
    )
    rows = []
    for itype in order:
        group = by_type[itype]
        rows.append(
            {
                "itype": itype,
                "label": _type_label(itype),
                "deployed": len(group),
                "processed": sum(i["processed"] for i in group),
                "skipped": sum(i["skipped"] for i in group),
                "not_here": sum(i.get("skip_reason") == NOT_HERE for i in group),
                "stopped_early": sum(i["stopped_early"] for i in group),
                "notes": "; ".join(
                    sorted(
                        {
                            i.get("yaml_reason") or i.get("skip_reason", "")
                            for i in group
                            if i["skipped"]
                        }
                    )
                ),
            }
        )
    return rows


def _instruments_cell(instrs: list[dict[str, Any]]) -> str:
    """Return ``"14 of 15 (1 ADCP not processed here)"`` for one mooring."""
    processed = sum(i["processed"] for i in instrs)
    missing = Counter(
        (type_word(i["itype"]), i["skip_reason"]) for i in instrs if i["skipped"]
    )
    notes = ", ".join(f"{n} {t} {why}" for (t, why), n in missing.items())
    return f"{processed} of {len(instrs)}" + (f" ({notes})" if notes else "")


def _build_array_masthead(
    array_cfg: dict[str, Any],
    rows: list[dict[str, Any]],
    type_summary: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[_masthead.Fact]:
    """Return the header: year deployed, project, moorings, status, water depth, instruments.

    Parameters
    ----------
    array_cfg : dict
        The array YAML.
    rows : list of dict
        One row per mooring (``_deploy_dt``, ``_recover_dt``, ``waterdepth``).
    type_summary : list of dict
        :func:`_build_type_summary`.
    now : datetime, optional
        When the status is judged; default now (UTC).

    Returns
    -------
    list of Fact
        In the page's order; each type with instruments not processed adds a fact.
    """
    now = now or datetime.now(UTC)
    years = sorted({r["_deploy_dt"].year for r in rows if r["_deploy_dt"]})
    recovered = sum(
        1
        for r in rows
        if r["_recover_dt"] is not None
        and r["_recover_dt"].replace(tzinfo=r["_recover_dt"].tzinfo or UTC) <= now
    )
    if recovered == len(rows):
        status = "recovered"
    else:
        status = (
            f"{recovered} of {len(rows)} recovered" if recovered else "in the water"
        )
    depths = [r["waterdepth"] for r in rows if r["waterdepth"] is not None]
    by_type = " · ".join(
        f"{type_word(t['itype'])} {t['deployed']}" for t in type_summary
    )
    total = sum(t["deployed"] for t in type_summary)
    f = _masthead._fact
    return [
        f("Year deployed", array_cfg.get("year") or (years[0] if years else None)),
        f("Project", array_cfg.get("project")),
        f("Number of moorings", len(rows)),
        f("Status", status if rows else None),
        f(
            "Water depth",
            f"{min(depths):.0f}–{max(depths):.0f}" if depths else None,
            "m",
        ),
        f("Instruments", f"{total} ({by_type})" if total else None),
    ] + [
        f(f"{_type_label(t['itype'])}s {NOT_HERE}", t["not_here"])
        for t in type_summary
        if t["not_here"]
    ]


def _array_map_payload(rows: list[dict[str, Any]]) -> tuple[str, str, str]:
    """Return (payload, coastline, bounds) JSON strings for the Leaflet array map."""
    marks = [r for r in rows if r.get("lat") is not None and r.get("lon") is not None]
    if not marks:
        return safe_json([]), "null", "null"
    payload = []
    for r in marks:
        # Per-marker info HTML is pre-escaped here, then embedded via safe_json.
        info = [
            f"<strong>{escape(r['mooring'])}</strong>",
            f"{location.format_ddm(r['lat'], is_lat=True)}, "
            f"{location.format_ddm(r['lon'], is_lat=False)}",
        ]
        if r.get("waterdepth") is not None:
            info.append(f"{r['waterdepth']:.0f} m")
        info.append(f"{escape(r['deploy_time'])} &ndash; {escape(r['recover_time'])}")
        if r.get("report_exists"):
            info.append(f'<a href="{escape(r["report_href"])}">&rarr; Overview</a>')
        payload.append(
            {
                "id": r["mooring"],
                "track": [[r["lat"], r["lon"]]],
                "color": r.get("color_hex") or "#1a3a5c",
                "href": r["report_href"] if r.get("report_exists") else "",
                "info": "<br>".join(info),
            }
        )
    lats = [r["lat"] for r in marks]
    lons = [r["lon"] for r in marks]
    # Padded, so one marker (or a tight cluster) frames with context.
    lat_pad = max((max(lats) - min(lats)) * 0.15, 0.5)
    lon_pad = max((max(lons) - min(lons)) * 0.15, 0.5)
    bounds_json = safe_json(
        [
            [min(lats) - lat_pad, min(lons) - lon_pad],
            [max(lats) + lat_pad, max(lons) + lon_pad],
        ]
    )
    coastline_json = coastline_geojson(
        (min(lons), min(lats), max(lons), max(lats)), pad_deg=1.0
    )
    if coastline_json == "null":
        _log.warning(
            "array map: coastline unavailable (cartopy/shapely missing, or no land "
            "in the clip extent); map shows markers only"
        )
    return safe_json(payload), coastline_json, bounds_json


def _mooring_yaml(config_path: Path) -> dict[str, Any]:
    """Return a mooring YAML, unvalidated when it has errors (its own page names them)."""
    if not config_path.exists():
        warnings.warn(f"mooring YAML not found: {config_path}", stacklevel=3)
        return {}
    try:
        return _mooring.load(config_path)
    except MooringError as exc:
        warnings.warn(
            f"{config_path} has validation errors ({exc}); shown unvalidated.",
            stacklevel=3,
        )
        with config_path.open() as fh:
            return yaml.safe_load(fh) or {}


def _mooring_row(
    entry: dict[str, Any],
    array_dir: Path,
    proc_dir: Path,
    report_dir: Path | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return one mooring's table row and its instruments' status records."""
    mooring_id = entry.get("mooring", "")
    # A config path is relative to the array YAML (or absolute); the per-mooring
    # proc-dir convention is the fallback.
    config_path = Path(entry.get("config", f"{mooring_id}.mooring.yaml"))
    if not config_path.is_absolute():
        beside = array_dir / config_path
        config_path = beside if beside.exists() else proc_dir / mooring_id / config_path
    mcfg = _mooring_yaml(config_path)
    lat, lon = _resolve_lat_lon(mcfg)
    deploy_dt = _parse_dt(mcfg.get("deployment_time"))
    recover_dt = _parse_dt(mcfg.get("recovery_time"))
    rpt_dir, prefix = (
        (report_dir / mooring_id, mooring_id)
        if report_dir is not None
        else (proc_dir / mooring_id / "report", f"{mooring_id}/report")
    )
    instrs = _build_mooring_instruments(proc_dir, mooring_id, mcfg, recover_dt)
    # An unvalidated YAML may give the depth as text ("unknown", "~2000"): the
    # table shows that text and the header and map leave it out.
    depth = mcfg.get("waterdepth")
    try:
        depth_m = float(depth)
    except (TypeError, ValueError):
        depth_m = None
    row = {
        "mooring": mooring_id,
        "line": entry.get("line"),
        "lat": lat,
        "lon": lon,
        "position": (
            f"{location.format_ddm(lat, is_lat=True)}, "
            f"{location.format_ddm(lon, is_lat=False)}"
            if lat is not None and lon is not None
            else _masthead.MISSING
        ),
        "waterdepth": depth_m,
        "waterdepth_text": f"{depth_m:.0f}" if depth_m is not None else depth or "—",
        "deploy_time": deploy_dt.strftime("%Y-%m-%d %H:%M") if deploy_dt else "—",
        "recover_time": recover_dt.strftime("%Y-%m-%d %H:%M") if recover_dt else "—",
        "_deploy_dt": deploy_dt,
        "_recover_dt": recover_dt,
        "instruments": _instruments_cell(instrs),
    }
    for page, suffix in _PAGES:
        row[f"{page}_exists"] = (rpt_dir / f"{mooring_id}{suffix}").exists()
        row[f"{page}_href"] = f"{prefix}/{mooring_id}{suffix}"
    return row, instrs


def generate_array_report(
    array_yaml_path: Path,
    proc_dir: Path,
    out_path: Path | None = None,
    force: bool = False,
    report_dir: Path | None = None,
    breadcrumb: list[tuple[str, str | None]] | None = None,
    bathymetry: Path | None = None,
) -> Path | None:
    """Generate the year array page from an array YAML file.

    Parameters
    ----------
    array_yaml_path : Path
        Path to the ``*.array.yaml`` file. A mooring entry may name its ``line``
        (moorings are coloured by line); ``bathymetry:`` names a bathymetry file
        for the map's depth contours, relative to the YAML.
    proc_dir : Path
        Root processing directory; each mooring is ``proc_dir / mooring_id``.
    out_path : Path, optional
        Output HTML path. Default ``{array_name}_array_report.html`` in
        *report_dir*, else in *proc_dir*.
    force : bool
        Overwrite an existing page.
    report_dir : Path, optional
        Central report directory: each mooring's pages are expected under
        ``report_dir / mooring_id /``; default ``{mooring}/report/`` beside each
        mooring.
    breadcrumb : list of (str, str or None) or None, optional
        Ancestor crumbs shown above the title; hrefs are used as given.
    bathymetry : Path, optional
        A bathymetry file (GEBCO or ETOPO) for the depth contours, in place of
        the YAML's ``bathymetry:``. Without either, the map has no contours.

    Returns
    -------
    Path or None
        The page written, or None when the array YAML is not found.
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
        out_path = (report_dir or proc_dir) / f"{array_name}_array_report.html"
    if out_path.exists() and not force:
        logger.status("skip", str(out_path))
        return out_path

    entries = array_cfg.get("moorings", [])
    rows, instruments = [], []
    for entry in entries:
        row, instrs = _mooring_row(entry, array_yaml_path.parent, proc_dir, report_dir)
        rows.append(row)
        instruments.extend(instrs)
    lines = list(dict.fromkeys(r["line"] for r in rows if r["line"]))
    colors = report_tokens.TRACK_COLORS
    durations = format_durations(
        [span_seconds(r["_deploy_dt"], r["_recover_dt"]) for r in rows]
    )
    for k, (row, duration) in enumerate(zip(rows, durations, strict=True)):
        row["duration"] = duration
        # One colour per line; without lines, one per mooring.
        at = lines.index(row["line"]) if row["line"] in lines else k
        row["color_hex"] = colors[at % len(colors)]

    type_summary = _build_type_summary(instruments)
    fig_map_b64 = _make_array_map_b64(rows)
    leaflet_js, leaflet_css = load_leaflet()
    payload_json, coastline_json, bounds_json = (
        _array_map_payload(rows) if ARRAY_MAP_LEAFLET else ("[]", "null", "null")
    )
    gebco = bathymetry or (
        array_yaml_path.parent / array_cfg["bathymetry"]
        if array_cfg.get("bathymetry")
        else None
    )
    placed = [r for r in rows if r["lat"] is not None and r["lon"] is not None]
    contours = None
    if gebco and placed and payload_json != "[]":
        contours = bathymetry_contours(
            Path(gebco),
            (
                min(r["lon"] for r in placed) - CONTOUR_PAD_DEG,
                min(r["lat"] for r in placed) - CONTOUR_PAD_DEG,
                max(r["lon"] for r in placed) + CONTOUR_PAD_DEG,
                max(r["lat"] for r in placed) + CONTOUR_PAD_DEG,
            ),
            out_path.parent / f"_{array_name}_contours.json",
        )
    html = render_template(
        "array.html",
        array_name=array_name,
        year=array_cfg.get("year")
        or next((r["_deploy_dt"].year for r in rows if r["_deploy_dt"]), ""),
        moorings=rows,
        lines=[(name, colors[i % len(colors)]) for i, name in enumerate(lines)],
        fig_map_b64=fig_map_b64,
        payload_json=payload_json,
        coastline_json=coastline_json,
        bounds_json=bounds_json,
        leaflet_js=leaflet_js if ARRAY_MAP_LEAFLET else "",
        leaflet_css=leaflet_css,
        map_height=460,
        contours=CONTOURS if contours else {},
        contour_file=Path(gebco).name if contours else "",
        type_summary=type_summary,
        stopped_hours=STOPPED_EARLY_HOURS,
        meta_facts=_build_array_masthead(array_cfg, rows, type_summary),
        processing={
            "pages": {p: sum(r[f"{p}_exists"] for r in rows) for p, _ in _PAGES},
            "source": array_yaml_path.name,
        },
        generated=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        breadcrumb=page_crumbs(breadcrumb, "", []),
    )
    if contours:
        html = _with_contours(html, contours)
    out_path.write_text(html, encoding="utf-8")
    logger.status("file", str(out_path))
    return out_path
