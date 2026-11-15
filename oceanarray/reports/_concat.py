"""Concatenated-stack report page: one site's deployments on one time axis."""

from __future__ import annotations

import math
import os
import warnings
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from markupsafe import Markup

from oceanarray import location, logger, paths
from oceanarray.pipeline.concat import cell_deployment

from ..config import parameters as params
from . import _masthead
from ._env import render_history, render_template
from ._figdebug import as_panel
from ._html_helpers import _format_dt, _format_duration, _parse_dt, _parse_history
from ._plots import (
    _make_adcp_trajectories_b64,
    _make_analog_timeseries_b64,
    _make_multi_aquadopp_trajectories_b64,
    _make_rose_outlines_b64,
    _make_speed_profile_by_deployment_b64,
    _make_stack_timeseries_b64,
    _make_stack_ts_diagram_b64,
    _make_ts_by_deployment_b64,
    _make_ts_medians_b64,
)
from ._read import _read_nc_metadata
from ._stack import STACK_CAPTIONS, STACK_PANELS, STACK_SECTIONS, StackContext
from ._vendored.page_model import Panel, Profile, Section, resolve, validate_nav

#: Shading note added to the time-series captions on the concatenated page.
_TURNAROUND = (
    " Grey bands are the turnarounds (no instrument in the water); a thin line marks "
    "each deployment's start, and each deployment is named along the top."
)

#: Added to the captions of the figures drawn per deployment.
_OVERLAP_NOTE = (
    " Where two deployments overlap, only the one filling the depth is drawn; the "
    "other's values are in the file's overlap block."
)

CONCAT_CAPTIONS: dict[str, str] = {
    "hydro_pts": STACK_CAPTIONS["hydro_pts"].replace(
        "for the full deployment", "for every deployment"
    )
    + _TURNAROUND,
    "vel_uvw": STACK_CAPTIONS["vel_uvw"] + _TURNAROUND,
    "turbidity": STACK_CAPTIONS["turbidity"] + _TURNAROUND,
    "ts_by_deployment": (
        "Temperature against salinity, one color per deployment; values flagged "
        "3, 4 or 9 left out. A shift between the clouds at the same depth is a "
        "difference between deployments (sensor offset or a change in the water)."
        + _OVERLAP_NOTE
    ),
    "ts_medians": (
        "Draft. Median salinity and temperature of each depth in each "
        "deployment, with interquartile whiskers, one color per deployment. Values "
        "flagged 3, 4 or 9 left out; the scatter below carries the pressure."
        + _OVERLAP_NOTE
    ),
    "roses": (
        "One rose per depth, one outline per "
        "deployment: the share of that deployment's samples flowing toward each "
        "direction (oceanographic convention, 0° = N). Values flagged 3, 4 or 9 "
        "left out." + _OVERLAP_NOTE
    ),
    "speed_profile": (
        "Current speed per depth, one box per deployment in its color "
        "(median, interquartile range, whiskers to 1.5 × IQR), overlaid at the "
        "depth's nominal depth. Values flagged 3, 4 or 9 left out." + _OVERLAP_NOTE
    ),
    "trajectories_aquadopp": (
        "Pseudo-Lagrangian trajectories integrated from east/north velocity, one per "
        "depth, running through every deployment in the depth (a turnaround "
        "adds no displacement; where two deployments overlap, the one filling the "
        "depth counts). Start at the origin; the label marks the end."
    ),
}


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
        One row per depth (and per level without a depth) for the Instruments table.
    concat_history : list
        Parsed history entries of the concat step.
    n_overlap : int
        Length of the overlap block (``N_OVERLAP``); 0 when no deployments overlap.
    deployment_histories : list of (str, list)
        Each deployment's name and parsed stack history.
    """

    deployment_rows: list = field(default_factory=list)
    deployment_names: list = field(default_factory=list)
    depth_rows: list = field(default_factory=list)
    concat_history: list = field(default_factory=list)
    deployment_histories: list = field(default_factory=list)
    n_overlap: int = 0


def _render_histories(c: ConcatContext) -> str:
    """Render the concat history, then each deployment's history folded under its name."""
    return render_template(
        "_concat_history.html",
        concat_html=Markup(render_history(c.concat_history)),
        deployments=[
            (name, Markup(render_history(entries)))
            for name, entries in c.deployment_histories
        ],
    )


def _always(_c: Any) -> bool:
    """Return True: the panel always applies."""
    return True


def _figure_panel(pid: str, applies_to: Any = None, slot: str | None = None) -> Panel:
    """Build a figure panel with the concat caption (or the stack's)."""
    base = STACK_PANELS.get(pid)
    caption = CONCAT_CAPTIONS.get(pid, STACK_CAPTIONS.get(pid, ""))
    return Panel(
        pid,
        render=lambda c: c.figs.get(pid),
        caption=caption,
        slot=slot if slot is not None else (base.slot if base else None),
        applies_to=applies_to or (base.applies_to if base else _always),
    )


CONCAT_PANELS: dict[str, Panel] = {
    **STACK_PANELS,
    "history": Panel(
        "history",
        render=_render_histories,
        kind="html",
        unavailable_if=lambda c: (
            None
            if c.concat_history
            else "No processing history recorded in the file attributes."
        ),
    ),
    "nc_overlap_note": Panel(
        "nc_overlap_note",
        render=lambda _c: Markup(
            '<p class="none-note">No deployments overlap: the overlap block '
            "(<code>N_OVERLAP</code>) is empty, so its variables are not listed.</p>"
        ),
        kind="html",
        applies_to=lambda c: c.n_overlap == 0,
    ),
    "deployments": Panel(
        "deployments",
        render=lambda c: render_template(
            "_concat_deployments.html", rows=c.deployment_rows
        ),
        kind="table",
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
    "speed_profile": _figure_panel(
        "speed_profile",
        lambda c: (
            "east_velocity" in c.present_vars and "north_velocity" in c.present_vars
        ),
        slot="half",
    ),
    "trajectories_aquadopp": _figure_panel("trajectories_aquadopp"),
    "ts_medians": _figure_panel(
        "ts_medians",
        lambda c: "temperature" in c.present_vars and "salinity" in c.present_vars,
        slot="half",
    ),
    "ts_diagram": _figure_panel("ts_diagram", slot="full"),
    "ts_by_deployment": _figure_panel(
        "ts_by_deployment",
        lambda c: "temperature" in c.present_vars and "salinity" in c.present_vars,
        slot="half",
    ),
}

CONCAT_SECTIONS: dict[str, Section] = {
    "netcdf_variables": Section(
        "netcdf_variables",
        "NetCDF variables",
        ("nc_coords", "nc_variables", "nc_overlap_note", "nc_scalars"),
        role="appendix",
    ),
    "deployments": Section(
        "deployments",
        "Deployments",
        ("deployments",),
        intro="Each deployment links to its own stack report.",
    ),
    "processing_history": STACK_SECTIONS["processing_history"],
    "instruments": Section(
        "instruments",
        "Instruments by depth (deep-first)",
        ("instr_table",),
        intro=(
            "A depth holds the instruments of one family (hydrography, point "
            "velocity, or ADCP bins) whose nominal depth (water depth − HAB) is "
            "within the depth tolerance (half the bin size for ADCP bins), one per "
            "deployment. Depths are named by their depth: their numbers change when "
            "a deployment is added."
        ),
    ),
    "speed_profile": Section(
        "speed_profile", "Current speed by depth", ("speed_profile",)
    ),
    "ts_diagram": Section(
        "ts_diagram",
        "T-S diagram",
        ("ts_by_deployment", "ts_medians", "ts_diagram"),
        layout="row",
    ),
}

#: The concatenated page: the stack page without the per-deployment diagnostics
#: (tilt, spacing, clock check), which stay in each deployment's report.
CONCAT_DEFAULT = Profile(
    numbering="flat",
    entries=(
        CONCAT_SECTIONS["deployments"],
        CONCAT_SECTIONS["processing_history"],
        CONCAT_SECTIONS["instruments"],
        STACK_SECTIONS["hydrography"],
        STACK_SECTIONS["velocity"],
        STACK_SECTIONS["turbidity"],
        STACK_SECTIONS["trajectories"],
        CONCAT_SECTIONS["speed_profile"],
        STACK_SECTIONS["analog"],
        CONCAT_SECTIONS["ts_diagram"],
        STACK_SECTIONS["roses"],
        CONCAT_SECTIONS["netcdf_variables"],
        STACK_SECTIONS["netcdf_attributes"],
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
        f("Duration", _format_duration(start, end)),
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
) -> str | None:
    """Return the relative link from this page to a deployment's own stack report."""
    folder = _report_folder(source, site_dir, name, report_dir)
    if folder is None:
        return None
    page = folder / f"{name}_stack_report.html"
    if not page.exists():
        return None
    return Path(os.path.relpath(page.resolve(), out_dir.resolve())).as_posix()


def _deployment_rows(
    ds: Any, stack_path: Path, out_dir: Path, report_dir: Path | None = None
) -> list[dict]:
    """Return one Deployments-table row per deployment."""
    names = [str(n) for n in ds["deployment_name"].values]
    get = {
        v: ds[v].values if v in ds else None
        for v in ("deployment_cruise", "recovery_cruise", "source_file")
    }
    instruments = Counter(dep for dep, _ in _physical_instruments(ds))
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
                    _text(get["source_file"], k),
                    stack_path.parent,
                    out_dir,
                    name,
                    report_dir,
                ),
                "deployed": _format_dt(start),
                "recovered": _format_dt(end),
                "duration": _format_duration(start, end),
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
            }
        )
    return rows


def _instrument_href(
    ds: Any, i: int, stack_path: Path, out_dir: Path, report_dir: Path | None = None
) -> str | None:
    """Return the link from this page to level *i*'s own instrument page, if it exists."""
    k = int(ds["deployment"].values[i])
    source = _text(ds["source_file"].values if "source_file" in ds else None, k)
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
        return {
            "type": str(ds["instrument_type"].values[i]),
            "serial": str(ds["serial"].values[i]),
            "depth": f"{float(ds['depth'].values[i]):.0f}",
            "href": _instrument_href(ds, i, stack_path, out_dir, report_dir),
        }

    rows = []
    depths = nominal_depths(ds)
    for k in _depth_order(ds):
        rows.append(
            {
                "family": str(ds["depth_family"].values[k]).replace("_", " "),
                "depth": f"{depths[k]:.0f}" if np.isfinite(depths[k]) else "—",
                "cells": [
                    [entry(i) for i in np.flatnonzero((depth_index == k) & (dep == d))]
                    for d in range(len(names))
                ],
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


#: Short family names for the depth labels in figure legends.
_FAMILY_SHORT = {
    "hydrography": "T/S",
    "point_velocity": "vel",
    "adcp": "ADCP",
    "adcp_head": "ADCP head",
}


def nominal_depths(ds: Any) -> np.ndarray:
    """Return each depth's value: the mean nominal depth of its levels (``nominal_depth``).

    Whatever the depth reference (depth or hab), the page orders and labels depths by
    depth, never by their number.
    """
    return ds["nominal_depth"].values.astype(float)


def _depth_order(ds: Any) -> np.ndarray:
    """Return the depths deep-first by depth; a depth with no known depth goes last."""
    depths = nominal_depths(ds)
    return np.argsort(np.where(np.isfinite(depths), -depths, np.inf), kind="stable")


def _depth_names(ds: Any) -> list[str]:
    """Return each depth's label, ``≈1500 m T/S``; two depths that round alike are told apart.

    The second and later of a clash get ``(2)``, ``(3)`` in depth order, so the
    label never carries the depth index.
    """
    depths = nominal_depths(ds)
    base = [
        (f"≈{round(d, -1):.0f} m" if np.isfinite(d) else "depth ?")
        + f" {_FAMILY_SHORT.get(str(ds['depth_family'].values[k]), str(ds['depth_family'].values[k]))}"
        for k, d in enumerate(depths)
    ]
    seen: Counter = Counter()
    out = []
    for k in _depth_order(ds):
        seen[base[k]] += 1
        out.append(
            (k, base[k] if seen[base[k]] == 1 else f"{base[k]} ({seen[base[k]]})")
        )
    return [label for _, label in sorted(out)]


def _report_view(ds: Any) -> Any:
    """Return the site stack with one column per depth, named by depth, deep-first.

    The figures read it like a stack (``time, N_LEVELS``): ``serial`` is the depth's
    label, ``instrument_type`` its most common type, ``hab`` and ``depth`` its
    members' means, and ``cell_deployment`` the deployment behind each value (-1
    none), so a figure can color by deployment.
    """
    import xarray as xr

    labels = _depth_names(ds)
    level_depth_index = ds["depth_index"].values
    types, habs = [], []
    for k in range(ds.sizes["N_DEPTHS"]):
        members = np.flatnonzero(level_depth_index == k)
        kinds = Counter(str(x) for x in ds["instrument_type"].values[members])
        types.append(kinds.most_common(1)[0][0] if kinds else "")
        hab = ds["hab"].values.astype(float)[members]
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
            "serial": ("N_LEVELS", np.array(labels)),
            "level_id": ("N_LEVELS", np.array(labels)),
            "instrument_type": ("N_LEVELS", np.array(types)),
            "hab": ("N_LEVELS", np.array(habs), {"units": "m"}),
            "depth": ("N_LEVELS", depths, {"units": "m"}),
        },
        attrs=dict(ds.attrs),
    )
    return view.isel(N_LEVELS=_depth_order(ds))


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

    from oceanarray.pipeline.concat import sha256

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
        name = str(ds.attrs.get("site_name", stack_path.stem))
        out_path = out_dir / f"{name}_stackcat_report.html"
        view = _report_view(ds)
        depth_view = _depth_view(view)
        names = [str(n) for n in ds["deployment_name"].values]
        windows = _deployment_windows(ds)
        figs = {
            "hydro_pts": _make_stack_timeseries_b64(
                view,
                [
                    ("pressure", params.vlabel("pressure"), True, {"adcp"}),
                    ("temperature", params.vlabel("temperature"), False, None),
                    ("salinity", params.vlabel("salinity"), False, None),
                ],
                color_var="temperature",
                deployments=windows,
                legend_shallow_first=True,
            ),
            "dissolved_oxygen": _make_stack_timeseries_b64(
                view,
                [("dissolved_oxygen", params.vlabel("dissolved_oxygen"), False, None)],
                color_var="dissolved_oxygen",
                deployments=windows,
                legend_shallow_first=True,
            ),
            "vel_uvw": _make_stack_timeseries_b64(
                view,
                [
                    (v, params.vlabel(v, prefix=f"{c} — "), False, None)
                    for v, c in (
                        ("east_velocity", "U"),
                        ("north_velocity", "V"),
                        ("up_velocity", "W"),
                    )
                ],
                color_var="east_velocity",
                deployments=windows,
                legend_shallow_first=True,
            ),
            "turbidity": _make_stack_timeseries_b64(
                view,
                [("turbidity", params.vlabel("turbidity"), False, None)],
                color_var="turbidity",
                dot_overlay=True,
                deployments=windows,
                legend_shallow_first=True,
            ),
            "roses": as_panel(lambda: _make_rose_outlines_b64(view)),
            "ts_diagram": as_panel(lambda: _make_stack_ts_diagram_b64(view)),
            "ts_by_deployment": as_panel(lambda: _make_ts_by_deployment_b64(view)),
            "ts_medians": as_panel(lambda: _make_ts_medians_b64(view)),
            "trajectories_aquadopp": as_panel(
                lambda: (
                    _make_multi_aquadopp_trajectories_b64(depth_view)
                    if depth_view is not None
                    else None
                )
            ),
            "trajectories_adcp": as_panel(lambda: _make_adcp_trajectories_b64(view)),
            "speed_profile": as_panel(
                lambda: _make_speed_profile_by_deployment_b64(view)
            ),
        }
        deployment_rows = _deployment_rows(ds, stack_path, out_dir, report_dir)
        nc_meta = _read_nc_metadata(stack_path)
        n_overlap = int(ds.sizes.get("N_OVERLAP", 0))
        if n_overlap == 0 and nc_meta.get("var_groups"):
            # An empty overlap block is said in one line, not listed as a table.
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
            as_panel(lambda: _make_analog_timeseries_b64(stack_path, analog_vars))
            if analog_vars
            else None
        )
        context = ConcatContext(
            figs=figs,
            present_vars=set(ds.data_vars),
            instr_types={str(t).lower() for t in ds["instrument_type"].values},
            n_instr=ds.sizes["N_LEVELS"],
            has_analog=bool(analog_vars),
            history_entries=_parse_history(ds.attrs.get("history", "")),
            instr_rows=[],
            nc_meta=nc_meta,
            nc_file=stack_path.name,
            rose_note=None,
            rose_warn=False,
            rose_missing_serials=[],
            deployment_rows=deployment_rows,
            deployment_names=names,
            depth_rows=_depth_rows(ds, stack_path, out_dir, report_dir),
            concat_history=_parse_history(ds.attrs.get("history", "")),
            n_overlap=n_overlap,
            deployment_histories=[
                (names[k], _parse_history(str(ds["deployment_history"].values[k])))
                for k in range(len(names))
            ],
        )
        report = resolve(CONCAT_DEFAULT, context, CONCAT_PANELS)
        html = render_template(
            "concat.html",
            mooring_name=name,
            nav=_nav(
                deployment_rows,
                other=(
                    f"{name}_gridcat_report.html"
                    if _gridcat_matches(stack_path, name)
                    else None
                ),
            ),
            report=report,
            meta_facts=_masthead_facts(ds, stack_path),
            generated=generated or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        )
        out_path.write_text(html, encoding="utf-8")
        logger.status("file", str(out_path))
        return out_path
    except Exception as exc:  # noqa: BLE001 — a failed page must not abort the concat
        warnings.warn(f"concat report generation failed: {exc}", stacklevel=2)
        return None
