"""Concatenated-stack report page: one site's deployments on one time axis."""

from __future__ import annotations

import logging
import math
import os
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from markupsafe import Markup

from oceanarray import logger, mask_rules, paths
from oceanarray.analysis.derived import derived_fields
from oceanarray.mooring import location
from oceanarray.pipeline.concat import (
    adcp_bins,
    cell_deployment,
    depth_types,
    select_depths,
    shown_depths,
)

from ..config import parameters as params
from ..plotters import current as _current
from ..plotters import helpers as _helpers
from ..plotters import ts as _ts
from ..plotters.helpers import nominal_depths
from . import _captions, _html_helpers, _masthead, _qcflags
from ._breadcrumb import page_crumbs
from ._env import render_template
from ._figdebug import as_panel
from ._html_helpers import (
    DESCRIPTION_ANCHOR,
    _format_dt,
    _parse_dt,
    doi_link,
    duration_cells,
    format_durations,
    span_seconds,
)
from ._navs import function_abbr, function_word
from ._plots import (
    _make_adcp_progressive_vector_b64,
    _make_analog_timeseries_b64,
    _make_flag_strip_b64,
    _make_multi_aquadopp_progressive_vector_b64,
    _make_stack_timeseries_b64,
    _make_stack_ts_diagram_b64,
)
from ._read import _read_nc_metadata
from ._slots import render as render_slot
from ._stack import STACK_CAPTIONS as _STACK_CAPTIONS
from ._stack import (
    STACK_PANELS,
    STACK_SECTIONS,
    StackContext,
    velocity_heights,
    velocity_rows,
)
from ._vendored.page_model import Panel, Profile, Section, resolve, validate_nav

log = logging.getLogger(__name__)

#: The variables a short record is judged on, by figure family: a deployment with
#: values at a depth for less than ``MIN_RECORD_FRACTION`` of its time there is
#: left out of every figure of that family, and the captions name it.
SHORT_RECORD_TEST: dict[str, str] = {
    "velocity": "east_velocity",
    "hydrography": "temperature",
}

#: The panels of each family, for the left-out sentence.
_FAMILY_PANELS: dict[str, tuple[str, ...]] = {
    "velocity": ("vel_uvw", "roses", "direction_profile", "trajectories_aquadopp"),
    "hydrography": (
        "hydro_pts",
        "ts_points",
        "ts_by_deployment",
        "ts_medians",
        "ts_diagram",
        "dissolved_oxygen",
    ),
}


#: Where a site page's mooring descriptions are (the links near the top).
SITE_DESCRIPTION = "of each deployment, linked near the top of this page"


def _boundaries(gap: bool) -> str:
    """Return the caption sentence for the deployment marks (grey bands only with a gap)."""
    marks = (
        "a thin line and a ▼ above the panel mark each deployment's start, and each "
        "deployment is named along the top."
    )
    if gap:
        return f" Grey bands are turnarounds with no instrument in the water; {marks}"
    return f" {marks[0].upper()}{marks[1:]}"


def _sigma(record: dict) -> str:
    """Return the σθ sentence of a T-S caption, when contours were drawn."""
    interval = record.get("sigma_interval")
    if not interval:
        return ""
    return (
        f" Dashed: σθ every {interval:g} {params.vunit('potential_density')}, labelled."
    )


def _left_out(short: dict[str, list[str]], family: str) -> str:
    """Return the sentence naming the records left out of a figure (50 % rule)."""
    if not short.get(family):
        return ""
    return (
        f" Left out, with values for less than {_captions.record_fraction_text()} "
        "their deployment: "
        f"{_captions._join(short[family])}."
    )


def _bins_shown(ds: Any) -> str:
    """Return the sentence naming the ADCP bins the roses and flag figure show, or ``""``."""
    from oceanarray.analysis.spectral import SPECTRAL_MIN_FILLED

    picks = adcp_bins(ds)
    if not picks:
        return ""
    names = _helpers.short_names([str(n) for n in ds["deployment_name"].values])
    depth = ds["nominal_depth"].values
    each = [
        f"{names[k]} at {_captions._join([f'{depth[s]:.0f}' for s in at])} m"
        for (k, _serial), at in sorted(picks.items())
    ]
    return (
        f" ADCP bins shown: {'; '.join(each)} (per ADCP, the bin nearest the "
        "instrument, the farthest with velocity at least "
        f"{_captions.percent_text(SPECTRAL_MIN_FILLED)} of the time, and one midway)."
    )


def _page_captions(
    ds: Any,
    view: Any,
    records: dict,
    short: dict[str, list[str]],
    gap: bool,
    depth_rows: list[dict],
) -> dict[str, str]:
    """Return the page's captions, built from the view and what each figure drew.

    Parameters
    ----------
    ds : xarray.Dataset
        The site stack, for the direction reference (the view has no file names).
    view : xarray.Dataset
        The report view (:func:`_report_view`).
    records : dict
        What each figure drew, by panel id.
    short : dict of str to list of str
        The records left out by the 50 % rule, by family (:func:`_drop_short`).
    gap : bool
        Whether a turnaround leaves time with no deployment in the water.
    depth_rows : list of dict
        The Instruments table's rows (:func:`_depth_rows`).

    Returns
    -------
    dict of str to str
        Captions by panel id.
    """
    flags = _captions.stack_flag_sentences(view)
    stack = _captions.stack_captions(view, records, ds_stack=ds)
    reference = _captions.direction_reference(ds)
    toward = "the direction the water flows toward" + (
        f", {reference}" if reference else ""
    )
    by_dep = records.get("ts_by_deployment", {})
    n_levels = by_dep.get("levels", 0)
    outlines = (
        f"For each instrument level, the outline encloses the densest "
        f"{_ts.TS_OUTLINE_FRACTION:.0%} of its values (a 2-D histogram smoothed "
        "over 3 × 3 bins). "
        if by_dep.get("outlines", True)
        else f"Outlines not drawn: {n_levels} levels (more than {_ts.TS_MAX_OUTLINES}). "
    )
    shown = _bins_shown(ds)
    out = {
        "qc_strip": _STACK_CAPTIONS["qc_strip"]
        + (
            " Each ADCP is one panel at its head: the head's series (pressure) once, "
            "their flags filled from its bins where the head has none; then each "
            "per-bin variable flagged, a row per bin shown."
            if shown
            else ""
        )
        + shown,
        "hydro_pts": stack["hydro_pts"].replace(
            "over the deployment", "over every deployment"
        )
        + _boundaries(gap),
        "vel_uvw": stack["vel_uvw"].replace(
            "over the deployment", "over every deployment"
        )
        + _boundaries(gap),
        "turbidity": stack["turbidity"] + _boundaries(gap),
        "dissolved_oxygen": stack["dissolved_oxygen"] + _boundaries(gap),
        "ts_diagram": stack["ts_diagram"].replace(
            "for all samples", "for all samples of all deployments"
        ),
        "ts_points": (
            "Potential temperature against salinity (PSU), one colour per "
            f"deployment: at most {_ts.TS_MAX_POINTS:,} values per deployment, "
            "picked at random (the same ones each time the page is drawn), drawn in "
            "random order so no deployment covers the others."
            + _sigma(records.get("ts_points", {}))
            + f" {flags['ts_points']}"
        ),
        "ts_by_deployment": (
            "Potential temperature against salinity (PSU), one colour per "
            f"deployment. {outlines}The dot is each level's median; lines join each "
            "deployment's medians in order of pressure (a guide to the eye, not "
            f"interpolated data; levels less than {_ts.TS_COLOCATED_DBAR:g} dbar apart "
            "are averaged for the line)."
            + _sigma(by_dep)
            + f" {flags['ts_by_deployment']} A shift between deployments at the same "
            "depth is a sensor offset or a change in the water."
        ),
        "ts_medians": (
            "Median potential temperature and salinity (PSU) at each depth in each "
            "deployment; whiskers: the middle 50 %."
            + _sigma(records.get("ts_medians", {}))
            + f" {flags['ts_medians']}"
        ),
        "roses": (
            f"One rose per depth, one outline per deployment: the percentage of that "
            f"deployment's record flowing in each direction ({toward}); rings share "
            f"one scale.{shown} {flags['roses']}"
        ),
        "speed_profile": (
            "Current speed at each current-meter depth, one box per deployment "
            "(median, middle 50 %, whiskers to 1.5 × that range); an ADCP, one line "
            "per deployment through its bins' medians, the middle 50 % shaded. "
            "Deployments with "
            f"velocity for less than {_captions.record_fraction_text()} their time "
            "are left out ("
            + (_captions._join(short["velocity"]) if short.get("velocity") else "none")
            + f"). {flags['speed_profile']}"
        ),
        "direction_profile": (
            "Current direction at each current-meter depth, one marker per "
            f"deployment: {toward}, of the deployment's mean velocity, from daily "
            "means so tides do not count; the bar is the circular standard deviation "
            "of the daily-mean directions, with an arrow where it runs past the axis "
            "edge; an ADCP, one line per deployment through its bins, the deviation "
            "shaded. The axis is centred on the mean direction "
            f"of all depths, ±180°. {flags['direction_profile']}"
        ),
        "trajectories_aquadopp": (
            "Cumulative displacement from adding up the measured velocity (a "
            "progressive vector diagram), one line per depth, running through every "
            "deployment in the depth (a turnaround adds no displacement). Start at "
            "the origin; the label marks the end."
        ),
    }
    for family, pids in _FAMILY_PANELS.items():
        for pid in pids:
            out[pid] += _left_out(short, family)
    # The types written in the cells: only on rows whose depth holds more than one.
    mixed = sorted(
        {
            e["type"]: e["abbr"]
            for r in depth_rows
            if r["mixed"]
            for c in r["cells"]
            for e in c
        }.items()
    )
    key = ", ".join(abbr if abbr == word else f"{abbr} {word}" for word, abbr in mixed)
    tolerance = view.attrs.get("depth_tolerance_m")
    out["instr_table"] = (
        "Which instrument measured at each depth in each deployment, deepest first; "
        "the Type column names the depth's instruments. Each cell: serial (linked "
        "to its page) and nominal depth"
        + (
            f", with the type in front where a depth holds more than one ({key})"
            if key
            else ""
        )
        + "."
        + (
            f" Rows group instruments within {float(tolerance):g} m of the same depth."
            if tolerance not in (None, "")
            else ""
        )
    )
    return out


@dataclass(frozen=True)
class ConcatContext(StackContext):
    """The stack page's context plus the deployment record of a concatenated stack.

    Parameters
    ----------
    deployment_rows : list of dict
        One row per deployment for the Deployments table.
    deployment_names : list of str
        Deployment names in time order (the instrument table's columns).
    depth_rows : list of dict
        One row per depth for the Instruments table.
    deployment_steps : list of (str, list)
        Each deployment's name and its stack's processing table rows.
    """

    deployment_rows: list = field(default_factory=list)
    deployment_names: list = field(default_factory=list)
    depth_rows: list = field(default_factory=list)
    deployment_steps: list = field(default_factory=list)


def _always(_c: Any) -> bool:
    """Return True: the panel always applies."""
    return True


def _figure_panel(pid: str, applies_to: Any = None, slot: str | None = None) -> Panel:
    """Build a figure panel; its caption is filled in per page."""
    base = STACK_PANELS.get(pid)
    return Panel(
        pid,
        render=lambda c: c.figs.get(pid),
        caption="",
        slot=slot if slot is not None else (base.slot if base else None),
        applies_to=applies_to or (base.applies_to if base else _always),
    )


def _page_panels(captions: dict[str, str]) -> dict[str, Panel]:
    """Return the panels with this page's captions."""
    captions = {
        **captions,
        "nc_file": _captions.netcdf_caption("site stack file", SITE_DESCRIPTION),
    }
    out = {
        pid: replace(panel, caption=captions[pid]) if pid in captions else panel
        for pid, panel in CONCAT_PANELS.items()
    }
    out["qc_strip"] = Panel(
        "qc_strip",
        render=lambda c: render_template(
            "_collapsed_figure.html",
            summary="Flags through time",
            caption=captions["qc_strip"],
            images=[c.figs["qc_strip"]],
        ),
        kind="html",
        applies_to=lambda c: (
            bool(c.qc_rule and c.qc_rows) and isinstance(c.figs.get("qc_strip"), str)
        ),
    )
    return out


def _has_velocity(c: Any) -> bool:
    """Return whether the page has east and north velocity."""
    return "east_velocity" in c.present_vars and "north_velocity" in c.present_vars


def _has_ts(c: Any) -> bool:
    """Return whether the page has temperature and salinity."""
    return "temperature" in c.present_vars and "salinity" in c.present_vars


CONCAT_PANELS: dict[str, Panel] = {
    **STACK_PANELS,
    "steps": Panel(
        "steps",
        render=lambda c: render_template(
            "_concat_history.html", rows=c.steps, deployments=c.deployment_steps
        ),
        kind="html",
        caption=(
            "What was done to produce this record across the deployments, entry by "
            "entry as the file's history attribute records it, with the settings "
            "read from the file. Each deployment's own record is collapsed below."
        ),
        unavailable_if=lambda c: None if c.steps else _html_helpers.NO_HISTORY,
    ),
    "deployments": Panel(
        "deployments",
        render=lambda c: render_template(
            "_concat_deployments.html",
            rows=c.deployment_rows,
            show_doi=any(r["doi"] for r in c.deployment_rows),
        ),
        kind="table",
        caption=(
            "One row per deployment; positions and water depth as deployed; each "
            "deployment links to its own stack page. Overlap: both moorings in the "
            "water during the turnaround; gap: neither. Where two deployments "
            "overlap, only the one filling the depth is drawn on this page; the "
            "other's values are in the file's overlap block."
        ),
    ),
    "instr_table": Panel(
        "instr_table",
        render=lambda c: render_template(
            "_concat_instr_table.html",
            rows=c.depth_rows,
            deployments=c.deployment_names,
        ),
        kind="table",
    ),
    "hydro_pts": _figure_panel("hydro_pts"),
    "vel_uvw": _figure_panel("vel_uvw"),
    "turbidity": _figure_panel("turbidity"),
    "roses": _figure_panel("roses"),
    "speed_profile": _figure_panel("speed_profile", _has_velocity, slot="half"),
    "direction_profile": _figure_panel("direction_profile", _has_velocity, slot="half"),
    "trajectories_aquadopp": _figure_panel("trajectories_aquadopp"),
    "ts_points": _figure_panel("ts_points", _has_ts, slot="half"),
    "ts_medians": _figure_panel("ts_medians", _has_ts, slot="half"),
    "ts_diagram": _figure_panel("ts_diagram", slot="full"),
    "ts_by_deployment": _figure_panel("ts_by_deployment", _has_ts, slot="half"),
}

CONCAT_SECTIONS: dict[str, Section] = {
    "deployments": Section("deployments", "Deployments", ("deployments",)),
    "processing_history": Section(
        "processing_history", "Processing history", ("steps",)
    ),
    "instruments": Section("instruments", "Instruments", ("instr_table",)),
    "speed_profile": Section(
        "speed_profile",
        "Current speed and direction by depth",
        ("speed_profile", "direction_profile"),
        layout="row",
    ),
    "ts_diagram": Section(
        "ts_diagram",
        "T-S diagram",
        ("ts_points", "ts_by_deployment", "ts_medians", "ts_diagram"),
        layout="row",
    ),
}

#: The concatenated page: the stack page without the per-deployment diagnostics
#: (tilt, spacing, clock check), which stay in each deployment's report.
CONCAT_DEFAULT = Profile(
    numbering="flat",
    entries=(
        STACK_SECTIONS["hydrography"],
        STACK_SECTIONS["velocity"],
        CONCAT_SECTIONS["speed_profile"],
        CONCAT_SECTIONS["ts_diagram"],
        STACK_SECTIONS["roses"],
        STACK_SECTIONS["turbidity"],
        STACK_SECTIONS["trajectories"],
        STACK_SECTIONS["analog"],
        CONCAT_SECTIONS["deployments"],
        CONCAT_SECTIONS["processing_history"],
        CONCAT_SECTIONS["instruments"],
        STACK_SECTIONS["quality_flags"],
        STACK_SECTIONS["netcdf"],
    ),
)


def _text(values: Any, k: int) -> str:
    """Return element *k* of a DEPLOYMENT string variable, or ``""``."""
    if values is None:
        return ""
    v = values[k]
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)


def _physical_instruments(ds: Any) -> list[tuple[int, str]]:
    """Return ``(deployment, instrument_type)`` once per physical instrument (ADCP bins folded)."""
    seen: dict[tuple[int, str], str] = {}
    for dep, serial, itype in zip(
        ds["deployment"].values,
        ds["serial"].values,
        ds["instrument_type"].values,
        strict=True,
    ):
        base = (
            paths.adcp_parent_serial(serial)
            if str(itype).lower() == "adcp"
            else str(serial)
        )
        seen.setdefault((int(dep), base), str(itype))
    return [(dep, itype) for (dep, _), itype in seen.items()]


def _spread_km(lats: np.ndarray, lons: np.ndarray) -> float:
    """Return the largest distance (km) of a deployment position from their mean."""
    ok = np.isfinite(lats) & np.isfinite(lons)
    if ok.sum() < 2:
        return float("nan")
    lat0, lon0 = float(np.mean(lats[ok])), float(np.mean(lons[ok]))
    dy = (lats[ok] - lat0) * 111.2
    dx = (lons[ok] - lon0) * 111.2 * math.cos(math.radians(lat0))
    return float(np.max(np.hypot(dx, dy)))


def _ddm(value: float, *, is_lat: bool) -> str:
    """Return degrees and decimal minutes, or ``—`` when not finite."""
    return (
        location.format_ddm(value, is_lat=is_lat)
        if np.isfinite(value)
        else _masthead.MISSING
    )


def _masthead_facts(ds: Any, stack_path: Path) -> list:
    """Return the site masthead: site, deployments, span, position, depth, instruments."""
    lats = ds["deployment_latitude"].values.astype(float)
    lons = ds["deployment_longitude"].values.astype(float)
    depths = ds["deployment_waterdepth"].values.astype(float)
    start = _parse_dt(str(ds["deployment_time"].values[0]))
    end = _parse_dt(str(ds["recovery_time"].values[-1]))
    spread = _spread_km(lats, lons)
    finite_depths = depths[np.isfinite(depths)]
    depth_range = (
        f"{finite_depths.min():.0f}–{finite_depths.max():.0f}"
        if finite_depths.size
        else None
    )
    f = _masthead._fact
    return [
        f("Site", ds.attrs.get("site_name")),
        f("Deployments", ds.sizes["DEPLOYMENT"]),
        f("Span", f"{_format_dt(start, '%Y-%m-%d')} → {_format_dt(end, '%Y-%m-%d')}"),
        f("Duration", format_durations([span_seconds(start, end)])[0]),
        f(
            "Latitude",
            _ddm(
                float(np.nanmean(lats)) if np.isfinite(lats).any() else np.nan,
                is_lat=True,
            ),
        ),
        f(
            "Longitude",
            _ddm(
                float(np.nanmean(lons)) if np.isfinite(lons).any() else np.nan,
                is_lat=False,
            ),
        ),
        f(
            Markup("Position&nbsp;spread"),
            f"{spread:.1f}" if np.isfinite(spread) else None,
            "km",
        ),
        f("Water depth", depth_range, "m"),
        f(
            "Instruments (avg)",
            _masthead.format_instrument_counts(
                ({"instrument": t} for _, t in _physical_instruments(ds)),
                per=ds.sizes["DEPLOYMENT"],
            ),
        ),
        f(Markup("&Delta;t"), ds.attrs.get("dt_seconds"), "s"),
        f("Records", ds.sizes["time"]),
        f(Markup("Source&nbsp;file"), stack_path.name),
    ]


def _report_folder(
    source: str, site_dir: Path, name: str, report_dir: Path | None
) -> Path | None:
    """Return the folder of a deployment's own reports.

    ``<report_dir>/<deployment>`` when *report_dir* is given, else the ``report``
    folder beside the deployment's stack.
    """
    if report_dir is not None:
        return Path(report_dir) / name
    if not source:
        return None
    return (site_dir / source).parent / "report"


def _deployment_href(
    source: str,
    site_dir: Path,
    out_dir: Path,
    name: str,
    report_dir: Path | None = None,
    page_suffix: str = "_stack_report.html",
    anchor: str = "",
) -> str | None:
    """Return the relative link from this page to one of a deployment's own pages.

    Parameters
    ----------
    source : str
        The deployment's stack file as recorded in the Stack cat (``stack_file``).
    site_dir : Path
        Folder of the Stack cat file, against which *source* is resolved.
    out_dir : Path
        Folder this page is written to.
    name : str
        Deployment name.
    report_dir : Path or None, optional
        Reports root, when the deployment pages live under one.
    page_suffix : str, optional
        The page after the deployment name: ``"_stack_report.html"`` (default) or
        ``"_report.html"`` (the deployment page).
    anchor : str, optional
        Section id to link to on that page (``"description"``); none by default.

    Returns
    -------
    str or None
        The relative href, or None when the page does not exist.
    """
    folder = _report_folder(source, site_dir, name, report_dir)
    if folder is None:
        return None
    page = folder / f"{name}{page_suffix}"
    if not page.exists():
        return None
    href = Path(os.path.relpath(page.resolve(), out_dir.resolve())).as_posix()
    return f"{href}#{anchor}" if anchor else href


def _description_links(rows: list[dict]) -> list[tuple[str, str]]:
    """Return one (deployment name, href) pair per deployment page found.

    Parameters
    ----------
    rows : list of dict
        Deployments-table rows (:func:`_deployment_rows`), each with ``name`` and
        ``description_href`` (None when the deployment page was not found).

    Returns
    -------
    list of (str, str)
        Links to the "Mooring description" section of each deployment page.
    """
    return [
        (r["name"], r["description_href"]) for r in rows if r.get("description_href")
    ]


def _deployment_rows(
    ds: Any, stack_path: Path, out_dir: Path, report_dir: Path | None = None
) -> list[dict]:
    """Return one Deployments-table row per deployment."""
    names = [str(n) for n in ds["deployment_name"].values]
    get = {
        v: ds[v].values if v in ds else None
        for v in (
            "deployment_cruise",
            "recovery_cruise",
            "stack_file",
            "deployment_source_doi",
        )
    }
    instruments = Counter(dep for dep, _ in _physical_instruments(ds))
    unit, durations = duration_cells(
        [
            span_seconds(
                _parse_dt(str(ds["deployment_time"].values[k])),
                _parse_dt(str(ds["recovery_time"].values[k])),
            )
            for k in range(len(names))
        ]
    )
    rows = []
    for k, name in enumerate(names):
        start = _parse_dt(str(ds["deployment_time"].values[k]))
        end = _parse_dt(str(ds["recovery_time"].values[k]))
        to_next = "—"
        if k + 1 < len(names):
            nxt = _parse_dt(str(ds["deployment_time"].values[k + 1]))
            if end and nxt:
                days = (nxt - end).total_seconds() / 86400
                to_next = f"gap {days:.1f} d" if days >= 0 else f"overlap {-days:.1f} d"
        cruises = [
            c
            for c in (
                _text(get["deployment_cruise"], k),
                _text(get["recovery_cruise"], k),
            )
            if c
        ]
        years = f"{start.year}–{end.year}" if start and end else name
        rows.append(
            {
                "name": name,
                "years": years,
                "href": _deployment_href(
                    _text(get["stack_file"], k),
                    stack_path.parent,
                    out_dir,
                    name,
                    report_dir,
                ),
                "description_href": _deployment_href(
                    _text(get["stack_file"], k),
                    stack_path.parent,
                    out_dir,
                    name,
                    report_dir,
                    page_suffix="_report.html",
                    anchor=DESCRIPTION_ANCHOR,
                ),
                "deployed": _format_dt(start),
                "recovered": _format_dt(end),
                "duration": durations[k],
                "duration_unit": unit,
                "lat": _ddm(float(ds["deployment_latitude"].values[k]), is_lat=True),
                "lon": _ddm(float(ds["deployment_longitude"].values[k]), is_lat=False),
                "waterdepth": (
                    f"{float(ds['deployment_waterdepth'].values[k]):.0f}"
                    if np.isfinite(float(ds["deployment_waterdepth"].values[k]))
                    else _masthead.MISSING
                ),
                "n_instr": instruments.get(k, 0),
                "cruises": " → ".join(cruises) if cruises else _masthead.MISSING,
                "to_next": to_next,
                "doi": Markup(doi_link(_text(get["deployment_source_doi"], k))),
            }
        )
    return rows


def _instrument_href(
    ds: Any, i: int, stack_path: Path, out_dir: Path, report_dir: Path | None = None
) -> str | None:
    """Return the link from this page to level *i*'s own instrument page, if it exists."""
    k = int(ds["deployment"].values[i])
    source = _text(ds["stack_file"].values if "stack_file" in ds else None, k)
    name = str(ds["deployment_name"].values[k])
    folder = _report_folder(source, stack_path.parent, name, report_dir)
    if folder is None:
        return None
    serial = str(ds["serial"].values[i])
    if str(ds["instrument_type"].values[i]).lower() == "adcp":
        serial = paths.adcp_parent_serial(serial)
    page = folder / "instrument" / f"{name}_{paths.safe_serial(serial)}_report.html"
    if not page.exists():
        return None
    return Path(os.path.relpath(page.resolve(), out_dir.resolve())).as_posix()


def _depth_rows(
    ds: Any, stack_path: Path, out_dir: Path, report_dir: Path | None = None
) -> list[dict]:
    """Return the site inventory: one row per depth, deep-first by depth.

    Each cell lists that deployment's instruments in the depth, each linked to its
    own instrument page in that deployment's report when the page exists.
    """
    names = [str(n) for n in ds["deployment_name"].values]
    depth_index = ds["depth_index"].values
    dep = ds["deployment"].values

    def entry(i: int) -> dict:
        itype = str(ds["instrument_type"].values[i]).lower()
        return {
            "type": function_word(itype),
            "abbr": function_abbr(itype),
            "serial": str(ds["serial"].values[i]),
            "depth": f"{float(ds['depth'].values[i]):.0f}",
            "href": _instrument_href(ds, i, stack_path, out_dir, report_dir),
        }

    rows = []
    depths = nominal_depths(ds)
    for k in _depth_order(ds):
        cells = [
            [entry(i) for i in np.flatnonzero((depth_index == k) & (dep == d))]
            for d in range(len(names))
        ]
        types = list(dict.fromkeys(e["type"] for cell in cells for e in cell))
        rows.append(
            {
                # The instrument type heads the row; a cell names it only when
                # the depth holds more than one type.
                "types": " / ".join(types),
                "mixed": len(types) > 1,
                "depth": f"{depths[k]:.0f}" if np.isfinite(depths[k]) else "—",
                "cells": cells,
            }
        )
    return rows


def _nav(
    rows: list[dict], *, current: str = "stackcat", other: str | None = None
) -> dict:
    """Return the masthead nav: the site pages, then each deployment's stack report.

    *current* is this page (``"stackcat"`` or ``"gridcat"``); *other* the
    other site page's link, None when it is not there.
    """
    # The roles of a mooring's Stack and Grid pills (blue, purple).
    pills = []
    for page, label, role in (
        ("stackcat", "Stack cat", "landing"),
        ("gridcat", "Grid cat", "aggregate-a"),
    ):
        if page == current:
            pills.append(
                {"label": label, "href": "#top", "role": role, "state": "current"}
            )
        elif other is not None:
            pills.append({"label": label, "href": other, "role": role, "state": "link"})
    dep_pills = [
        {
            "label": row["years"],
            "href": row["href"] or "#",
            "role": "aggregate-b",
            "state": "link" if row["href"] else "missing",
        }
        for row in rows
    ]
    nav = {
        "rows": [
            {"label": "Reports:", "pills": pills},
            {"label": "Deployments:", "pills": dep_pills},
        ],
        "back": None,
        "inventory": [],
    }
    validate_nav(nav)
    return nav


def _depth_order(ds: Any) -> np.ndarray:
    """Return the depths deep-first by depth; a depth with no known depth goes last."""
    depths = nominal_depths(ds)
    return np.argsort(np.where(np.isfinite(depths), -depths, np.inf), kind="stable")


def _depth_names(ds: Any, holds: np.ndarray | None = None) -> list[str]:
    """Return each depth's label, ``"1690 m"``, with its type where two round alike.

    Labels never carry the depth index.

    Parameters
    ----------
    ds : xarray.Dataset
        The site stack.
    holds : numpy.ndarray of bool, optional
        The depths a figure draws; only those that round alike take their type
        (``"2724 m CTD"``). Default every depth, so the labels are unique.

    Returns
    -------
    list of str
        In depth-index order.
    """
    depths = nominal_depths(ds)
    base = [f"{d:.0f} m" if np.isfinite(d) else "depth ?" for d in depths]
    holds = np.ones(len(base), bool) if holds is None else holds
    clash = Counter(b for b, h in zip(base, holds, strict=True) if h)
    return [
        f"{b} {function_abbr(t.lower())}" if h and clash[b] > 1 else b
        for b, t, h in zip(base, depth_types(ds), holds, strict=True)
    ]


def _report_view(ds: Any) -> Any:
    """Return the site stack with one column per depth, named by depth, deep-first.

    The figures read it like a stack (``time, N_LEVELS``): ``serial`` is the depth's
    label (``level_id`` the same, with the type where two depths round alike),
    ``instrument_type`` its most common type, ``hab`` and ``depth`` its
    members' means, and ``cell_deployment`` the deployment behind each value (-1
    none), so a figure can color by deployment.
    """
    import xarray as xr

    level_depth_index = ds["depth_index"].values
    n = ds.sizes["N_DEPTHS"]
    types, habs = depth_types(ds), []
    for k in range(n):
        hab = ds["hab"].values.astype(float)[level_depth_index == k]
        habs.append(float(np.nanmean(hab)) if np.isfinite(hab).any() else np.nan)
    cell = cell_deployment(ds).astype(np.int16)
    data = {
        v: (("time", "N_LEVELS"), ds[v].values, dict(ds[v].attrs))
        for v in ds.data_vars
        if ds[v].dims == ("time", "N_DEPTHS") and v != "level_index"
    }
    data["cell_deployment"] = (("time", "N_LEVELS"), cell)
    for v in ds.data_vars:
        if ds[v].dims in (("DEPLOYMENT",), ("time",)):
            data[v] = ds[v].variable
    depths = nominal_depths(ds)
    view = xr.Dataset(
        data,
        coords={
            "time": ds["time"],
            "serial": ("N_LEVELS", np.array(_depth_names(ds, np.zeros(n, bool)))),
            "level_id": ("N_LEVELS", np.array(_depth_names(ds))),
            "instrument_type": ("N_LEVELS", np.array(types)),
            "hab": ("N_LEVELS", np.array(habs), {"units": "m"}),
            "depth": ("N_LEVELS", depths, {"units": "m"}),
        },
        attrs=dict(ds.attrs),
    )
    return view.isel(N_LEVELS=_depth_order(ds))


#: The variables left out with each family's short records.
_FAMILY_VARIABLES: dict[str, frozenset[str]] = {
    "velocity": mask_rules.VELOCITY_GROUP,
    "hydrography": frozenset(
        {"temperature", "salinity", "conductivity", "pressure", "dissolved_oxygen"}
    ),
}


def _drop_short(
    view: Any,
    member: np.ndarray,
    names: list[str],
    at: np.ndarray | None = None,
    bins: np.ndarray | None = None,
) -> tuple[Any, dict[str, list[str]]]:
    """Return *view* without the short records, and who was left out, by family.

    A deployment whose values (by :data:`SHORT_RECORD_TEST`) cover less than
    :data:`~oceanarray.config.parameters.MIN_RECORD_FRACTION` of its time is left
    out of every figure of that family.

    Parameters
    ----------
    view : xarray.Dataset
        The report view (:func:`_report_view`) or a site grid.
    member : numpy.ndarray of int
        Each value's deployment index (-1 for none): ``(time, level)`` to judge each
        level on its own, or ``(time,)`` to judge the steps with a value at any level.
    names : list of str
        Short deployment names, in deployment order.
    at : numpy.ndarray of str, optional
        Each level's label, for a per-level *member*.
    bins : numpy.ndarray of bool, optional
        Which levels are ADCP bins: named together, per deployment.

    Returns
    -------
    view : xarray.Dataset
        A copy with those values set to NaN.
    left_out : dict of str to list of str
        ``"2016 at 1820 m"`` per record left out (``"2016"`` per step), and
        ``"2014 ADCP bins at 9–29 m"`` per deployment for ADCP bins, by family.
    """
    cells = member if member.ndim == 2 else member[:, None]
    view = view.copy(deep=True)
    left_out: dict[str, list[str]] = {}
    for family, test in SHORT_RECORD_TEST.items():
        if test not in view:
            continue
        finite = np.isfinite(view[test].values)
        if member.ndim == 1:
            finite = finite.any(axis=1)[:, None]
        drop = np.zeros_like(finite)
        short: dict = {}  # label, or (deployment, "bins") → bin depths
        for j in range(cells.shape[1]):
            for k in np.unique(cells[:, j][cells[:, j] >= 0]):
                here = cells[:, j] == k
                if 0 < finite[here, j].mean() < params.MIN_RECORD_FRACTION:
                    drop[here, j] = True
                    if bins is not None and bins[j]:
                        short.setdefault((int(k), "bins"), []).append(
                            float(view["depth"].values[j])
                        )
                    else:
                        where = f" at {at[j]}" if member.ndim == 2 else ""
                        short[f"{names[k]}{where}"] = None
        for key, depths in short.items():
            if depths is None:
                left_out.setdefault(family, []).append(key)
                continue
            lo, hi = min(depths), max(depths)
            where = f"bin at {lo:.0f} m" if lo == hi else f"bins at {lo:.0f}–{hi:.0f} m"
            left_out.setdefault(family, []).append(f"{names[key[0]]} ADCP {where}")
        if drop.any():
            for v in _FAMILY_VARIABLES[family] & set(view.data_vars):
                if view[v].dims == view[test].dims:
                    view[v] = view[v].where(~drop)
    return view, left_out


def _depth_view(view: Any) -> Any:
    """Return the point-velocity depths of the report view, for the trajectories.

    One path per depth runs through every deployment in it; ADCP bins have their own
    trajectory panel.
    """
    if "east_velocity" not in view or "north_velocity" not in view:
        return None
    u = view["east_velocity"].values
    point = np.array(
        [
            params.INSTRUMENT_FAMILIES.get(str(t).lower()) == "point_velocity"
            for t in view["instrument_type"].values
        ]
    )
    keep = np.flatnonzero(np.isfinite(u).any(axis=0) & point)
    if not keep.size:
        return None
    # Temperature is left out: a depth whose instruments have none would draw an
    # invisible path when the others are colored by it.
    out = view[["east_velocity", "north_velocity"]].isel(N_LEVELS=keep)
    return out.assign_coords(
        instrument_type=("N_LEVELS", np.array(["aquadopp"] * keep.size))
    )


def _gridcat_matches(stack_path: Path, name: str) -> bool:
    """Return whether the site grid beside *stack_path* was made from this site stack.

    By its ``stackcat_sha256``: a grid left from an earlier stack is not linked.
    """
    import xarray as xr

    from oceanarray.provenance import sha256

    gridcat = paths.product_file(stack_path.parent, name, "gridcat")
    if not gridcat.exists():
        return False
    try:
        with xr.open_dataset(gridcat, engine="netcdf4") as ds:
            recorded = str(ds.attrs.get("stackcat_sha256", ""))
    except (OSError, ValueError):
        return False
    return recorded == sha256(stack_path)


def _deployment_windows(ds: Any) -> list[tuple[np.datetime64, np.datetime64, str]]:
    """Return each deployment's ``(start, end, name)`` for the time-series figures.

    From its deployment and recovery times; a time that is missing or does not
    parse falls back to the deployment's first or last step in ``deployment_index``.
    """
    names = [str(n) for n in ds["deployment_name"].values]
    time = ds["time"].values
    index = ds["deployment_index"].values
    out = []
    for k, name in enumerate(names):
        steps = time[index == k]
        bounds = []
        for var, fallback in (("deployment_time", 0), ("recovery_time", -1)):
            parsed = _parse_dt(str(ds[var].values[k]))
            if parsed is not None:
                bounds.append(np.datetime64(parsed.replace(tzinfo=None)))
            elif steps.size:
                bounds.append(steps[fallback])
        if len(bounds) == 2:
            out.append((bounds[0], bounds[1], name))
    return out


def generate_concat_page(
    stack_path: Path,
    out_dir: Path,
    *,
    generated: str | None = None,
    report_dir: Path | None = None,
    breadcrumb: list[tuple[str, str | None]] | None = None,
    label: str | None = None,
) -> Path | None:
    """Write the concatenated-stack report page for a site stack.

    Parameters
    ----------
    stack_path : Path
        The site stack (``oceanarray.concat`` output).
    out_dir : Path
        Where the page goes (``<site_dir>/report`` by default in the file verb).
    generated : str, optional
        The page's "generated" timestamp; default now (UTC).
    report_dir : Path, optional
        Where the deployments' own reports are, one folder per deployment
        (``<report_dir>/<deployment>``); default the ``report`` folder beside each
        deployment's stack. The page links to them.
    breadcrumb : list of (str, str or None) or None, optional
        An optional trail of ancestor crumbs shown above the title (hrefs relative to
        this page's own report directory); the page always shows the site as its own
        crumb, with the ancestors in front.
    label : str or None, optional
        The site's display name for the title and its own crumb; defaults to the
        file stem.

    Returns
    -------
    Path or None
        The page written, or None when it could not be built (a warning says why).
    """
    import xarray as xr

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with xr.open_dataset(stack_path, engine="netcdf4") as opened:
            ds = opened.load()
        # ADCP bins at or above the surface are not levels: no figure, table or caption.
        ds = select_depths(ds, shown_depths(ds))
        name = str(ds.attrs.get("site_name", stack_path.stem))
        out_path = out_dir / f"{name}_stackcat_report.html"
        names = [str(n) for n in ds["deployment_name"].values]
        # Deployments named without the site's prefix on the figures and in the
        # Instruments table (egM4_2014 → 2014); the Deployments table keeps the full name.
        short = dict(zip(names, _helpers.short_names(names), strict=True))
        view = _report_view(ds)
        order = _depth_order(ds)
        view, left_out = _drop_short(
            view,
            view["cell_deployment"].values,
            [short[n] for n in names],
            view["serial"].values,
            ds["depth_family"].values[order].astype(str) == "adcp",
        )
        # Roses and the flag figure: three bins per ADCP (adcp_bins).
        three = shown_depths(ds, bins="three")
        depth_view = _depth_view(view)
        # Position and θ for the T-S figures, derived once for the page.
        derived = derived_fields(view)
        windows = [(start, end, short[n]) for start, end, n in _deployment_windows(ds)]
        spans = sorted(windows, key=lambda w: w[0])
        gap = any(b[0] > a[1] for a, b in zip(spans, spans[1:], strict=False))
        records: dict[str, dict] = {pid: {} for pid in _captions.STACK_FIGURE_VARIABLES}
        series = {"deployments": windows, "legend_shallow_first": True}

        def drawn(draw: Any, pid: str, **kwargs: Any) -> Any:
            return as_panel(
                lambda: render_slot(
                    draw,
                    view,
                    derived=derived,
                    record=records[pid],
                    optional=True,
                    **kwargs,
                )
            )

        # The hydrography legend names the type where two depths with temperature
        # round alike.
        held = (
            np.isfinite(ds["temperature"].values).any(0)
            if "temperature" in ds
            else None
        )
        typed = np.array(_depth_names(ds, held))
        figs = {
            "hydro_pts": _make_stack_timeseries_b64(
                view.assign_coords(serial=("N_LEVELS", typed[_depth_order(ds)])),
                [
                    ("pressure", params.vlabel("pressure"), True, {"adcp"}),
                    ("temperature", params.vlabel("temperature"), False, None),
                    ("salinity", params.vlabel("salinity"), False, None),
                ],
                color_var="temperature",
                mark_suspect=True,
                record=records["hydro_pts"],
                **series,
            ),
            "dissolved_oxygen": _make_stack_timeseries_b64(
                view,
                [("dissolved_oxygen", params.vlabel("dissolved_oxygen"), False, None)],
                color_var="dissolved_oxygen",
                **series,
            ),
            "vel_uvw": _make_stack_timeseries_b64(
                view,
                velocity_rows(view),
                color_var="east_velocity",
                row_heights=velocity_heights(view),
                mark_suspect=True,
                record=records["vel_uvw"],
                **series,
            ),
            "turbidity": _make_stack_timeseries_b64(
                view,
                [("turbidity", params.vlabel("turbidity"), False, None)],
                color_var="turbidity",
                dot_overlay=True,
                **series,
            ),
            "roses": as_panel(
                lambda: render_slot(
                    _current.draw_rose_outlines,
                    view.isel(N_LEVELS=three[order]),
                    optional=True,
                )
            ),
            "ts_diagram": as_panel(
                lambda: _make_stack_ts_diagram_b64(
                    view, derived, record=records["ts_diagram"], full_range=True
                )
            ),
            "ts_by_deployment": drawn(
                _ts.draw_ts_by_deployment, "ts_by_deployment", slot="half"
            ),
            "ts_points": drawn(
                _ts.draw_ts_points_by_deployment, "ts_points", slot="half"
            ),
            "ts_medians": drawn(_ts.draw_ts_medians, "ts_medians", slot="half"),
            "trajectories_aquadopp": as_panel(
                lambda: (
                    _make_multi_aquadopp_progressive_vector_b64(depth_view)
                    if depth_view is not None
                    else None
                )
            ),
            "trajectories_adcp": as_panel(
                lambda: _make_adcp_progressive_vector_b64(view)
            ),
            "speed_profile": as_panel(
                lambda: render_slot(
                    _current.draw_speed_profile_by_deployment,
                    view,
                    slot="half",
                    optional=True,
                )
            ),
            "direction_profile": as_panel(
                lambda: render_slot(
                    _current.draw_direction_profile_by_deployment,
                    view,
                    slot="half",
                    optional=True,
                )
            ),
        }
        deployment_rows = _deployment_rows(ds, stack_path, out_dir, report_dir)
        nc_meta = _read_nc_metadata(stack_path)
        if not ds.sizes.get("N_OVERLAP", 0) and nc_meta.get("var_groups"):
            # An empty overlap block is not listed.
            nc_meta = {
                **nc_meta,
                "var_groups": [
                    g
                    for g in nc_meta["var_groups"]
                    if not str(g[0]).startswith("N_OVERLAP")
                ],
            }
        analog_vars = nc_meta.get("analog_vars", [])
        figs["analog"] = (
            as_panel(lambda: _make_analog_timeseries_b64(ds, analog_vars))
            if analog_vars
            else None
        )
        qc_ruled = mask_rules.STACK_ATTR in ds.attrs
        figs["qc_strip"] = (
            as_panel(
                lambda: _make_flag_strip_b64(
                    select_depths(ds, three),
                    _qcflags.instrument_labels(select_depths(ds, three)),
                )
            )
            if qc_ruled
            else None
        )
        qc_rows, qc_unflagged, qc_quiet = (
            _qcflags.count_rows(ds) if qc_ruled else ([], [], 0)
        )
        types = {str(t).lower() for t in ds["instrument_type"].values}
        depth_rows = _depth_rows(ds, stack_path, out_dir, report_dir)
        context = ConcatContext(
            figs=figs,
            qc_rows=qc_rows,
            qc_rule=mask_rules.rule_words(mask_rules.from_attrs(ds.attrs), table=True)
            if qc_ruled
            else "",
            qc_tables=_qcflags.translation_tables(ds) if qc_ruled else [],
            qc_unflagged=qc_unflagged,
            qc_quiet=qc_quiet,
            qc_sentence=_captions.stack_rule_sentence(ds, mask_rules.FLAGGED_VARIABLES),
            present_vars=set(ds.data_vars),
            instr_types=types,
            n_instr=ds.sizes["N_LEVELS"],
            has_analog=bool(analog_vars),
            instr_rows=[],
            nc_meta=nc_meta,
            nc_file=stack_path.name,
            rose_note=None,
            rose_unapplied=[],
            deployment_rows=deployment_rows,
            deployment_names=[short[n] for n in names],
            depth_rows=depth_rows,
            steps=_captions.stack_history_rows(ds),
            deployment_steps=[
                (
                    names[k],
                    _captions.history_rows(str(ds["deployment_history"].values[k])),
                )
                for k in range(len(names))
            ],
        )
        captions = _page_captions(ds, view, records, left_out, gap, depth_rows)
        report = resolve(CONCAT_DEFAULT, context, _page_panels(captions))
        html = render_template(
            "concat.html",
            mooring_name=label or name,
            breadcrumb=page_crumbs(breadcrumb, "", [(label or name, None)]),
            nav=_nav(
                deployment_rows,
                other=(
                    f"{name}_gridcat_report.html"
                    if _gridcat_matches(stack_path, name)
                    else None
                ),
            ),
            report=report,
            description_links=_description_links(deployment_rows),
            meta_facts=_masthead_facts(ds, stack_path),
            generated=generated or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        )
        out_path.write_text(html, encoding="utf-8")
        logger.status("file", str(out_path))
        return out_path
    except Exception as exc:  # noqa: BLE001 — a failed page must not abort the concat
        log.warning("concat report generation failed: %s", exc)
        return None
