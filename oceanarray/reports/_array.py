"""Year page: the moorings of one array YAML, with a map and their instruments.

Reads a ``*.array.yaml`` file, gathers per-mooring metadata from each mooring's
YAML, draws a position map, and writes one HTML page linking every mooring's
pages.

Entry point: :func:`generate_array_report`.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from markupsafe import Markup, escape

from oceanarray import logger, mask_rules
from oceanarray import mooring as _mooring
from oceanarray.exceptions import MooringError
from oceanarray.mooring import location
from oceanarray.mooring.instruments import instrument_entries

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
    duration_cells,
    span_seconds,
)
from ._navs import FUNCTION_ABBR, function_word
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
#: A bathymetry dataset's name in its title or summary.
DATASET_NAME = re.compile(r"\b(GEBCO[ _]?\d{4}|GEBCO|ETOPO ?\d+|SRTM\w*|IBCAO\S*)\b")
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

#: Why an instrument the mooring YAML skips has no pages: not processed here, not
#: missing at the originator.
NOT_HERE = "not processed here"

#: Array YAML keys the year page no longer reads (each mooring YAML gives these, and
#: the bathymetry file is an argument).
IGNORED_KEYS: tuple[str, ...] = (
    "cruise",
    "ship",
    "deployment_time",
    "recovery_time",
    "latitude",
    "longitude",
    "position",
    "waterdepth_min",
    "waterdepth_max",
    "vocab",
    "bathymetry",
)

#: The group of the moorings an array YAML gives no ``subarray``.
OTHER = "other"

#: A mooring's pages the year page links: key and file suffix.
_PAGES: tuple[tuple[str, str], ...] = (
    ("report", "_report.html"),
    ("stack", "_stack_report.html"),
    ("grid", "_grid_report.html"),
)


def _resolve_lat_lon(mooring_dict: dict[str, Any]) -> tuple[float | None, float | None]:
    """Return (lat, lon) decimal degrees from a mooring YAML dict, (None, None) if unknown."""
    lat, lon, source = location.parse_latlon_with_source(mooring_dict)
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
        JSON file the contours are kept in, reused while the file (path, size,
        modification time), extent and depths are the same.

    Returns
    -------
    str or None
        A GeoJSON FeatureCollection, one MultiLineString per depth in
        :data:`CONTOURS` (property ``depth``, m); None, with a warning, when *path*
        does not exist or does not cover *extent*.
    """
    import matplotlib.pyplot as plt
    import xarray as xr

    if not path.exists():
        _log.warning("no depth contours on the map: %s not found", path)
        return None
    stat = path.stat()
    key = {
        "extent": [round(v, 2) for v in extent],
        "depths": list(CONTOURS),
        "file": [str(path.resolve()), stat.st_mtime_ns, stat.st_size],
    }
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("key") == key:
            return json.dumps(cached["geojson"], separators=(",", ":"))
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


def dataset_name(path: Path) -> str:
    """Return the bathymetry dataset's name ("ETOPO1", "GEBCO 2025") from its attributes.

    Parameters
    ----------
    path : Path
        The bathymetry file.

    Returns
    -------
    str
        The first dataset name in its ``title`` or ``summary``; else the title
        itself; else "the bathymetry file".
    """
    import xarray as xr

    with xr.open_dataset(path, engine="netcdf4") as ds:
        attrs = ds.attrs
    for key in ("title", "summary"):
        found = DATASET_NAME.search(str(attrs.get(key, "")))
        if found:
            return found.group(0).replace("_", " ")
    return str(attrs.get("title") or "the bathymetry file")


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
    mooring_dict: dict[str, Any],
    recover_dt: Any,
) -> list[dict[str, Any]]:
    """Return one status record per instrument of a mooring, for the array tables."""
    mooring_proc = proc_dir / mooring_id
    results = []
    # The pressure ranges leave out what the figures hide: the mooring's rule. The
    # array page reads YAMLs unchecked, so a rule it cannot read falls back to the
    # file's own.
    try:
        rule = mask_rules.stack_rule(mooring_dict)
    except ValueError:
        rule = None
    for entry in instrument_entries(mooring_dict):
        if not isinstance(entry, dict):
            continue
        serial = safe_serial(entry.get("serial", ""))
        # The stage files are in a folder named as the YAML writes the type.
        folder = str(entry.get("instrument", "unknown"))
        itype = folder.strip().lower()
        skipped = bool(entry.get("skip"))
        stages = _stage_files(mooring_proc, folder, mooring_id, serial)
        no_file = not skipped and not stages.get("stage1")
        info = _read_instrument_info(
            mooring_proc, folder, mooring_id, serial, rule=rule
        )
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
    """Return the instrument-type table: one row per function, deployed and processed."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for instr in mooring_instruments:
        by_type.setdefault(function_word(instr["itype"]), []).append(instr)
    order = [t for t in FUNCTION_ABBR if t in by_type] + sorted(
        t for t in by_type if t not in FUNCTION_ABBR
    )
    rows = []
    for label in order:
        group = by_type[label]
        rows.append(
            {
                "label": label,
                "deployed": len(group),
                "processed": sum(i["processed"] for i in group),
                "skipped": sum(i["skipped"] for i in group),
                "not_here": sum(i["skip_reason"] == NOT_HERE for i in group),
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
        (function_word(i["itype"]), i["skip_reason"]) for i in instrs if i["skipped"]
    )
    notes = ", ".join(f"{n} {t} {why}" for (t, why), n in missing.items())
    return f"{processed} of {len(instrs)}" + (f" ({notes})" if notes else "")


def _date_range(dates: list[datetime]) -> str | None:
    """Return ``"10 Jul – 16 Aug 2016"`` (the year once when shared), None when empty."""
    if not dates:
        return None
    first, last = min(dates), max(dates)
    end = f"{last.day} {last:%b %Y}"
    if first.date() == last.date():
        return end
    start = f"{first.day} {first:%b}" + (
        "" if first.year == last.year else f" {first:%Y}"
    )
    return f"{start} – {end}"


def _cruises(rows: list[dict[str, Any]], key: str) -> str:
    """Return the cruises of *key* (deployment or recovery) grouped by ship.

    ``"DY053, DY054 (RRS Discovery) · AR07-01 (RV Neil Armstrong)"``.
    """
    by_ship: dict[str, list[str]] = {}
    for r in rows:
        cruise, ship = r[key]
        if cruise:
            names = by_ship.setdefault(ship or "", [])
            if cruise not in names:
                names.append(cruise)
    return " · ".join(
        ", ".join(sorted(c)) + (f" ({ship})" if ship else "")
        for ship, c in by_ship.items()
    )


def _with_note(value: Any, note: str) -> Any:
    """Return a header value with a second, lighter line."""
    if value is None or not note:
        return value
    return Markup("{}<small>{}</small>").format(value, note)


def _build_array_masthead(
    array_dict: dict[str, Any],
    rows: list[dict[str, Any]],
    type_summary: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[_masthead.Fact]:
    """Return the header facts of the year page.

    Parameters
    ----------
    array_dict : dict
        The parsed array YAML.
    rows : list of dict
        One row per mooring (``_deploy_dt``, ``_recover_dt``, ``waterdepth``,
        ``deployed_by``, ``recovered_by``, ``group``).
    type_summary : list of dict
        :func:`_build_type_summary`.
    now : datetime, optional
        When the status is judged; default now (UTC).

    Returns
    -------
    list of Fact
        Year deployed, project, moorings, status, deployed, recovered, water
        depth, instruments, and the instruments the mooring YAMLs skip, when any.
    """
    now = now or datetime.now(UTC)
    deployed = [r["_deploy_dt"] for r in rows if r["_deploy_dt"]]
    ended = [r["_recover_dt"] for r in rows if r["_recover_dt"]]
    recovered = sum(1 for t in ended if t.replace(tzinfo=t.tzinfo or UTC) <= now)
    if recovered == len(rows):
        status = "recovered"
    else:
        status = (
            f"{recovered} of {len(rows)} recovered" if recovered else "in the water"
        )
    depths = [r["waterdepth"] for r in rows if r["waterdepth"] is not None]
    groups = {r["group"] for r in rows if r["group"] != OTHER}
    by_type = " · ".join(f"{t['label']} {t['deployed']}" for t in type_summary)
    total = sum(t["deployed"] for t in type_summary)
    not_here = sum(t["not_here"] for t in type_summary)
    f = _masthead._fact
    return [
        f(
            "Year deployed",
            array_dict.get("year") or (min(deployed).year if deployed else None),
        ),
        f("Project", array_dict.get("project")),
        f(
            "Moorings",
            _with_note(
                len(rows), f"in {len(groups)} arrays" if len(groups) > 1 else ""
            ),
        ),
        f("Status", status if rows else None),
        f("Deployed", _with_note(_date_range(deployed), _cruises(rows, "deployed_by"))),
        f("Recovered", _with_note(_date_range(ended), _cruises(rows, "recovered_by"))),
        f(
            "Water depth",
            f"{min(depths):.0f}–{max(depths):.0f}" if depths else None,
            "m",
        ),
        f("Instruments", _with_note(total, by_type) if total else None),
    ] + ([f("Instruments not processed", not_here)] if not_here else [])


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


def _mooring_yaml(mooring_path: Path) -> dict[str, Any]:
    """Return a mooring YAML, unvalidated when it has errors (its own page names them)."""
    if not mooring_path.exists():
        _log.warning("mooring YAML not found: %s", mooring_path)
        return {}
    try:
        return _mooring.load(mooring_path)
    except MooringError as exc:
        _log.warning(
            "%s has validation errors (%s); shown unvalidated.", mooring_path, exc
        )
        with mooring_path.open() as fh:
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
    mooring_path = Path(entry.get("config", f"{mooring_id}.mooring.yaml"))
    if not mooring_path.is_absolute():
        beside = array_dir / mooring_path
        mooring_path = (
            beside if beside.exists() else proc_dir / mooring_id / mooring_path
        )
    mooring_dict = _mooring_yaml(mooring_path)
    lat, lon = _resolve_lat_lon(mooring_dict)
    deploy_dt = _parse_dt(mooring_dict.get("deployment_time"))
    recover_dt = _parse_dt(mooring_dict.get("recovery_time"))
    rpt_dir, prefix = (
        (report_dir / mooring_id, mooring_id)
        if report_dir is not None
        else (proc_dir / mooring_id / "report", f"{mooring_id}/report")
    )
    instrs = _build_mooring_instruments(proc_dir, mooring_id, mooring_dict, recover_dt)
    # An unvalidated YAML may give the depth as text ("unknown", "~2000"): the
    # table shows that text and the header and map leave it out.
    depth = mooring_dict.get("waterdepth")
    try:
        depth_m = float(depth)
    except (TypeError, ValueError):
        depth_m = None
    row = {
        "mooring": mooring_id,
        "group": entry.get("subarray") or OTHER,
        "deployed_by": (
            _masthead._first(mooring_dict, "deployment_cruise", "cruise"),
            _masthead._first(mooring_dict, "deployment_ship", "ship"),
        ),
        "recovered_by": (
            _masthead._first(mooring_dict, "recovery_cruise"),
            _masthead._first(mooring_dict, "recovery_ship"),
        ),
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


def generate_array_report(  # noqa: C901
    array_path: Path,
    proc_dir: Path,
    out_path: Path | None = None,
    force: bool = False,
    report_dir: Path | None = None,
    breadcrumb: list[tuple[str, str | None]] | None = None,
    bathymetry: Path | None = None,
) -> Path | None:
    """Generate the year page from an array YAML file.

    Parameters
    ----------
    array_path : Path
        Path to the ``*.array.yaml`` file. A mooring entry may name its
        ``subarray``, a key of the top-level ``subarrays`` table (``{key: {name:
        ..., short: ...}}``); moorings are coloured by subarray.
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
        A bathymetry file (GEBCO or ETOPO) for the map's depth contours; without
        it the map has none.

    Returns
    -------
    Path or None
        The page written, or None when the array YAML is not found.
    """
    array_path = Path(array_path)
    proc_dir = Path(proc_dir)
    if not array_path.exists():
        _log.warning("array YAML not found: %s", array_path)
        return None
    with array_path.open() as fh:
        array_dict = yaml.safe_load(fh)
    array_name = array_dict.get("name", array_path.stem)
    if out_path is None:
        out_path = (report_dir or proc_dir) / f"{array_name}_array_report.html"
    if out_path.exists() and not force:
        logger.status("skip", str(out_path))
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    entries = array_dict.get("moorings", [])
    ignored = [k for k in IGNORED_KEYS if k in array_dict] + (
        ["moorings: line (now subarray)"] if any("line" in e for e in entries) else []
    )
    if ignored:
        _log.warning(
            "%s: keys not used by the year page: %s",
            array_path.name,
            ", ".join(ignored),
        )
    rows, instruments = [], []
    for entry in entries:
        row, instrs = _mooring_row(entry, array_path.parent, proc_dir, report_dir)
        rows.append(row)
        instruments.extend(instrs)
    # One colour per subarray, "other" last; without subarrays, one per mooring.
    names = array_dict.get("subarrays") or {}
    groups = sorted(dict.fromkeys(r["group"] for r in rows), key=lambda g: g == OTHER)
    colors = report_tokens.TRACK_COLORS
    by_group = len(groups) > 1 or groups != [OTHER]
    legend = []
    for k, group in enumerate(groups):
        named = names.get(group) or {}
        legend.append(
            {
                "key": group,
                "name": named.get("name") or group,
                "short": named.get("short") or group,
                "color": colors[k % len(colors)],
            }
        )
    duration_unit, durations = duration_cells(
        [span_seconds(r["_deploy_dt"], r["_recover_dt"]) for r in rows]
    )
    for k, (row, duration) in enumerate(zip(rows, durations, strict=True)):
        row["duration"] = duration
        if by_group:
            row["array"] = legend[groups.index(row["group"])]
            row["color_hex"] = row["array"]["color"]
        else:
            row["color_hex"] = colors[k % len(colors)]

    type_summary = _build_type_summary(instruments)
    fig_map_b64 = _make_array_map_b64(rows)
    leaflet_js, leaflet_css = load_leaflet()
    payload_json, coastline_json, bounds_json = (
        _array_map_payload(rows) if ARRAY_MAP_LEAFLET else ("[]", "null", "null")
    )
    placed = [r for r in rows if r["lat"] is not None and r["lon"] is not None]
    contours = None
    if bathymetry and placed and payload_json != "[]":
        contours = bathymetry_contours(
            Path(bathymetry),
            (
                min(r["lon"] for r in placed) - CONTOUR_PAD_DEG,
                min(r["lat"] for r in placed) - CONTOUR_PAD_DEG,
                max(r["lon"] for r in placed) + CONTOUR_PAD_DEG,
                max(r["lat"] for r in placed) + CONTOUR_PAD_DEG,
            ),
            out_path.parent / f"_{array_name}_contours.json",
        )
    year = array_dict.get("year") or next(
        (r["_deploy_dt"].year for r in rows if r["_deploy_dt"]), ""
    )
    html = render_template(
        "array.html",
        title=f"{array_dict.get('project') or array_name} {year}".strip(),
        year=year,
        moorings=rows,
        duration_unit=duration_unit,
        legend=legend if by_group else [],
        fig_map_b64=fig_map_b64,
        payload_json=payload_json,
        coastline_json=coastline_json,
        bounds_json=bounds_json,
        leaflet_js=leaflet_js if ARRAY_MAP_LEAFLET else "",
        leaflet_css=leaflet_css,
        map_height=460,
        contours=CONTOURS if contours else {},
        contour_source=dataset_name(Path(bathymetry)) if contours else "",
        type_summary=type_summary,
        stopped_hours=STOPPED_EARLY_HOURS,
        meta_facts=_build_array_masthead(array_dict, rows, type_summary),
        processing={
            "pages": {p: sum(r[f"{p}_exists"] for r in rows) for p, _ in _PAGES},
            "source": array_path.name,
        },
        generated=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        breadcrumb=page_crumbs(breadcrumb, "", []),
    )
    if contours:
        html = _with_contours(html, contours)
    out_path.write_text(html, encoding="utf-8")
    logger.status("file", str(out_path))
    return out_path
