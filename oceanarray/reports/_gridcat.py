"""The site grid page: a site's gridded record over the years, one mooring per profile."""

from __future__ import annotations

import json
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from oceanarray import logger, paths
from oceanarray.analysis import site

from ..config import parameters as params
from . import _masthead
from ._breadcrumb import page_crumbs
from ._concat import (
    CONCAT_PANELS,
    CONCAT_SECTIONS,
    ConcatContext,
    _deployment_rows,
    _deployment_windows,
    _masthead_facts,
    _nav,
)
from ._env import render_template
from ._figdebug import as_panel
from ._html_helpers import _parse_history
from ._plots import _make_site_profiles_b64, _make_site_sections_b64, _make_site_ts_b64
from ._read import _read_nc_metadata
from ._stack import STACK_SECTIONS
from ._vendored.page_model import Panel, Profile, Section, resolve

#: The sections' and profiles' variables, in order, with their panel titles.
_HYDRO = (("temperature", "Temperature"), ("salinity", "Salinity"))


def _velocity_titles(angle: float | None) -> list[tuple[str, str]]:
    """Return the velocity variables shown, with their titles (along/across with an angle)."""
    out = [("east_velocity", "U (east)"), ("north_velocity", "V (north)")]
    if angle is not None:
        out += [
            ("along_slope", f"Along-slope ({angle:.0f}°)"),
            ("across_slope", f"Across-slope ({(angle + 90) % 360:.0f}°)"),
        ]
    return out


def _flags_text(ds: Any) -> str:
    """Return the masked flags in words, from ``grid_mask_flags``."""
    try:
        flags = json.loads(str(ds.attrs.get("grid_mask_flags", "")))
    except ValueError:
        return ""

    def flagged(values: list) -> str:
        if not values:
            return "nothing"
        listed = ", ".join(map(str, values[:-1]))
        return f"values flagged {listed + ' or ' if listed else ''}{values[-1]}"

    words = [f"{flagged(flags.pop('default', []))} left out before gridding"]
    words += [f"{key}: {flagged(values)}" for key, values in flags.items()]
    return "; ".join(words).capitalize() + "."


def _captions(
    ds: Any, words: str, angle_source: str, positioned: bool
) -> dict[str, str]:
    """Return the panel captions, which name the averaging, references and rules."""
    s0 = ", ".join(f"{v:g}" for v in params.SITE_SIGMA0_LEVELS)
    s2 = ", ".join(f"{v:g}" for v in params.SITE_SIGMA2_LEVELS)
    density = params.vunit("potential_density")
    shown = (
        f"Shown as {words} means of the hourly grid ({site.display_steps_text()}); "
        "the bins restart at each deployment's start, so none spans two "
        "deployments and a step between deployments shows as a sharp edge. A bin "
        f"with data for less than {site.DISPLAY_MIN_FRACTION:.0%} of its time "
        "is left blank. Gray bands are the turnarounds; a dotted line and a ▼ above "
        "the panel mark each deployment's start; gray lines are the pressures of "
        "the instruments measuring the variable shown (between them the grid is "
        "interpolated, so where the instruments' depths change between deployments, "
        "part of a difference between deployments is the change in sampling). "
    )
    isopycnals = (
        f"Black contours: σ0 {s0} and σ2 {s2} {density} (labeled with the value "
        "only), each over the whole depth, drawn within each deployment. "
        if positioned
        else "No isopycnals: no deployment has a position. "
    )
    flags = _flags_text(ds)
    return {
        "sections_ts": shown + isopycnals + "20 discrete color levels. " + flags,
        "anomalies_ts": (
            "Temperature and salinity less their whole-record median at each level "
            "(the median of the hourly values, which a spike or one bad instrument "
            "does not move). Colors symmetric about zero, to ±the larger of the 2nd "
            "and 98th percentiles. " + shown + isopycnals
        ),
        "profiles_ts": (
            "Median of the hourly values at each level, one line per deployment "
            "(red to purple in time order; with more than seven deployments each "
            "color twice, solid then dashed). A level is drawn only where the "
            f"deployment has values for at least {site.DISPLAY_MIN_FRACTION:.0%} of its "
            "time: a level reached only when the mooring is knocked down sees only "
            "the strong-flow hours. " + flags
        ),
        "profiles_uv": (
            "Median velocity at each level, one line per deployment, where the "
            f"deployment has velocity for at least {site.DISPLAY_MIN_FRACTION:.0%} of its "
            "time. Along-slope "
            f"direction: {angle_source}. " + flags
        ),
        "sections_uv": (
            shown
            + "Cropped to the pressures with velocity data, the panels made shorter "
            "in proportion, so a dbar spans the same height as in the temperature "
            "and salinity sections. Colors symmetric about zero on one scale for "
            "every panel, to ±the "
            "larger of the 2nd and 98th percentiles. Along-slope direction: "
            + angle_source
            + ". "
            + flags
        ),
        "site_ts": (
            "Potential temperature and salinity of the median temperature and "
            "salinity at each grid level, one line per deployment; a median is not "
            "moved by a spike. Levels with values for less than "
            f"{site.DISPLAY_MIN_FRACTION:.0%} of the deployment's time are left out. Axes span the 1–99 % range of the medians. Circles: "
            "each deployment's instruments measuring both T and S, at their mean "
            "pressure, so the curve between circles is the grid's interpolation. Gray "
            f"contours: σ0 {s0} and σ2 {s2} {density} (labeled with the value only)."
            if positioned
            else "Median in-situ temperature and salinity at each grid level, one "
            "line per deployment. Circles: each deployment's instruments measuring "
            "both T and S, at their mean pressure. No deployment has a position, so "
            "no potential temperature and no isopycnals."
        ),
    }


def _figure(pid: str, caption: str, slot: str, needs: tuple[str, ...]) -> Panel:
    """Return a figure panel shown when the grid holds every variable in *needs*."""
    return Panel(
        pid,
        render=lambda c: c.figs.get(pid),
        caption=caption,
        slot=slot,
        applies_to=lambda c: all(v in c.present_vars for v in needs),
    )


def _panels(captions: dict[str, str]) -> dict[str, Panel]:
    """Return the page's panels: the site stack page's tables, and the grid's figures."""
    vel = ("east_velocity", "north_velocity")
    ts = ("temperature", "salinity")
    return {
        **CONCAT_PANELS,
        "sections_ts": _figure("sections_ts", captions["sections_ts"], "full", ts[:1]),
        "anomalies_ts": _figure(
            "anomalies_ts", captions["anomalies_ts"], "full", ts[:1]
        ),
        "profiles_ts": _figure(
            "profiles_ts", captions["profiles_ts"], "twothirds", ts[:1]
        ),
        "profiles_uv": _figure("profiles_uv", captions["profiles_uv"], "full", vel),
        "sections_uv": _figure("sections_uv", captions["sections_uv"], "full", vel),
        "site_ts": _figure("site_ts", captions["site_ts"], "half", ts),
    }


GRIDCAT_SECTIONS: dict[str, Section] = {
    "sections": Section("sections", "Sections over the years", ("sections_ts",)),
    "anomalies": Section(
        "anomalies", "Anomalies from the whole-record median", ("anomalies_ts",)
    ),
    "profiles": Section(
        "profiles", "Median profiles per deployment", ("profiles_ts", "profiles_uv")
    ),
    "velocity": Section("velocity", "Velocity", ("sections_uv",)),
    "ts_diagram": Section("ts_diagram", "T-S diagram", ("site_ts",)),
}

#: The site grid page.
GRIDCAT_DEFAULT = Profile(
    numbering="flat",
    entries=(
        GRIDCAT_SECTIONS["sections"],
        GRIDCAT_SECTIONS["anomalies"],
        GRIDCAT_SECTIONS["velocity"],
        GRIDCAT_SECTIONS["ts_diagram"],
        GRIDCAT_SECTIONS["profiles"],
        CONCAT_SECTIONS["deployments"],
        STACK_SECTIONS["processing_history"],
        STACK_SECTIONS["netcdf_variables"],
        STACK_SECTIONS["netcdf_attributes"],
    ),
)


def _figures(grid: Any, stackcat: Any) -> tuple[dict, str, str]:
    """Return the page's figures, the averaging in words and where the slope angle came from."""
    from oceanarray.plotters.helpers import short_names

    # Legends name each deployment without the site's prefix (egM4_2014 → 2014).
    names = short_names([str(n) for n in grid["deployment_name"].values])
    pressure = grid["pressure"].values.astype(float)
    index = grid["deployment_index"].values
    freq, words = site.display_step(grid["time"].values)
    bins = site.display_bins(grid["time"].values, index, freq)
    shown = site.display_means(grid, bins)
    angle = grid.attrs.get("slope_angle_deg")
    angle = float(angle) if angle not in (None, "") else None
    source = str(grid.attrs.get("slope_angle_source", ""))
    angle_source = (
        f"{angle:.0f}° clockwise from north, {source}"
        if angle is not None
        else source or "none (no velocity)"
    )
    hourly = {
        v: grid[v] for v in grid.data_vars if grid[v].dims == ("time", "pressure")
    }
    if angle is not None and "east_velocity" in grid and "north_velocity" in grid:
        hourly["along_slope"], _ = site.slope_components(
            grid["east_velocity"], grid["north_velocity"], angle
        )
        shown["along_slope"], shown["across_slope"] = site.slope_components(
            shown["east_velocity"], shown["north_velocity"], angle
        )

    # The pressures of the instruments behind each panel, once per variable of
    # the site stack (along and across slope are the velocity instruments').
    measured: dict[str, np.ndarray] = {}

    def instruments(var: str) -> np.ndarray | None:
        source = "east_velocity" if var in ("along_slope", "across_slope") else var
        if source not in stackcat:
            return None
        if source not in measured:
            measured[source] = site.instrument_pressures(stackcat, source, bins)
        return measured[source]

    # Absolute salinity needs the position; without one, no density is drawn
    # (rather than one computed at 0° N, 0° E).
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # no deployment has one
        lat = float(np.nanmedian(grid["deployment_latitude"].values))
        lon = float(np.nanmedian(grid["deployment_longitude"].values))
    position = (lon, lat) if np.isfinite(lat) and np.isfinite(lon) else None
    isopycnals = []
    if position is not None and "temperature" in shown and "salinity" in shown:
        s0, s2 = site.density_fields(
            shown["temperature"].values, shown["salinity"].values, pressure, *position
        )
        isopycnals = [
            (s0, params.SITE_SIGMA0_LEVELS, "%g"),
            (s2, params.SITE_SIGMA2_LEVELS, "%g"),
        ]

    from oceanarray.plotters.site import section_cmap, symmetric_limit

    # Each deployment named without the site's prefix (FLMA_2014 → 2014).
    site_name = str(grid.attrs.get("site_name", ""))
    windows = [
        (start, end, label.removeprefix(f"{site_name}_"))
        for start, end, label in _deployment_windows(grid)
    ]
    hydro = [(v, t) for v, t in _HYDRO if v in shown]
    sections = [
        {
            "data": shown[v].values,
            "title": title,
            "units": params.vunit(v),
            "cmap": section_cmap(v),
            "instruments": instruments(v),
            "isopycnals": True,
        }
        for v, title in hydro
    ]
    anomalies = []
    for v, title in hydro:
        data = site.median_anomaly(grid[v], shown[v]).values
        anomalies.append(
            {
                "data": data,
                "title": f"{title} anomaly",
                "units": params.vunit(v),
                "cmap": section_cmap(v, anomaly=True),
                "limit": symmetric_limit(data),
                "instruments": instruments(v),
                "isopycnals": True,
            }
        )
    velocity = [(v, t) for v, t in _velocity_titles(angle) if v in shown]
    limit = symmetric_limit(*(shown[v].values for v, _ in velocity))
    vel_sections = [
        {
            "data": shown[v].values,
            "title": title,
            "units": params.vunit("east_velocity"),
            "cmap": section_cmap(v),
            "limit": limit,
            "instruments": instruments(v),
        }
        for v, title in velocity
    ]

    def medians(var: str) -> np.ndarray:
        return site.deployment_medians(
            hourly[var].values,
            index,
            len(names),
            min_fraction=site.DISPLAY_MIN_FRACTION,
        )

    def sections_b64(panels: list[dict], contours: list, crop: bool = False) -> Any:
        return as_panel(
            lambda: _make_site_sections_b64(
                panels,
                bins.centers,
                pressure,
                bins.edges,
                bins.owner,
                windows,
                contours,
                crop=crop,
            )
        )

    def site_ts() -> str | None:
        if "temperature" not in hourly or "salinity" not in hourly:
            return None
        salinity = medians("salinity")
        pins = None
        if "temperature" in stackcat and "salinity" in stackcat:
            # pins (stackcat deployment order) line up positionally with the curves
            # (grid order) because the grid carries the stack's whole deployment
            # record unchanged; guard that invariant rather than assume it.
            assert list(grid["deployment_name"].values) == list(
                stackcat["deployment_name"].values
            ), "grid and stackcat deployments differ; T-S pins would misalign"
            pins = site.instrument_mean_pressures(stackcat)
        if position is None:
            return _make_site_ts_b64(
                medians("temperature"),
                salinity,
                names,
                None,
                (),
                (),
                pressure=pressure,
                pins=pins,
            )
        theta = site.potential_temperature(
            medians("temperature"), salinity, pressure, *position
        )
        return _make_site_ts_b64(
            theta,
            salinity,
            names,
            position,
            params.SITE_SIGMA0_LEVELS,
            params.SITE_SIGMA2_LEVELS,
            pressure=pressure,
            pins=pins,
        )

    figs = {
        "sections_ts": sections_b64(sections, isopycnals),
        "anomalies_ts": sections_b64(anomalies, isopycnals),
        "sections_uv": sections_b64(vel_sections, [], crop=True),
        "profiles_ts": as_panel(
            lambda: _make_site_profiles_b64(
                [{"medians": medians(v), "title": params.vlabel(v)} for v, _ in hydro],
                pressure,
                names,
                "twothirds",
            )
        ),
        "profiles_uv": as_panel(
            lambda: _make_site_profiles_b64(
                [
                    {
                        "medians": medians(v),
                        "title": f"{t} ({params.vunit('east_velocity')})",
                    }
                    for v, t in velocity
                    if v != "across_slope"
                ],
                pressure,
                names,
                "full",
            )
        ),
        "site_ts": as_panel(site_ts),
    }
    if position is None:
        print(
            "  WARNING: no deployment has a position: no isopycnals, and the T-S "
            "diagram is in in-situ temperature."
        )
    return figs, words, angle_source, position is not None


def generate_gridcat_page(
    grid_path: Path,
    out_dir: Path,
    *,
    generated: str | None = None,
    report_dir: Path | None = None,
    breadcrumb: list[tuple[str, str | None]] | None = None,
    label: str | None = None,
) -> Path | None:
    """Write the site grid page for a site grid.

    Parameters
    ----------
    grid_path : Path
        The site grid, ``<site>_gridcat.nc``; its site stack (``stackcat_file``)
        is read from the same folder for the instrument pressures and the
        Deployments table.
    out_dir : Path
        Where the page goes (``<site_dir>/report`` from ``oceanarray concat``).
    generated : str, optional
        The page's "generated" timestamp; default now (UTC).
    report_dir : Path, optional
        Where the deployments' own reports are (see
        :func:`oceanarray.reports._concat.generate_concat_page`).
    breadcrumb : list of (str, str or None) or None, optional
        An optional trail of ancestor crumbs shown above the title; the page always
        shows the site (linking to its Stack cat page) and ``Grid cat``, with the
        ancestors in front.
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
        with xr.open_dataset(grid_path, engine="netcdf4") as opened:
            grid = opened.load()
        name = str(grid.attrs.get("site_name", grid_path.stem))
        stack_path = grid_path.parent / str(
            grid.attrs.get("stackcat_file", f"{name}_stackcat.nc")
        )
        with xr.open_dataset(stack_path, engine="netcdf4") as opened:
            stackcat = opened.load()
        out_path = out_dir / f"{name}_gridcat_report.html"
        figs, words, angle_source, positioned = _figures(grid, stackcat)
        names = [str(n) for n in grid["deployment_name"].values]
        deployment_rows = _deployment_rows(stackcat, stack_path, out_dir, report_dir)
        history = _parse_history(grid.attrs.get("history", ""))
        context = ConcatContext(
            figs=figs,
            present_vars=set(grid.data_vars),
            instr_types={str(t).lower() for t in stackcat["instrument_type"].values},
            n_instr=stackcat.sizes["N_LEVELS"],
            has_analog=False,
            history_entries=history,
            instr_rows=[],
            nc_meta=_read_nc_metadata(grid_path),
            nc_file=grid_path.name,
            rose_note=None,
            rose_warn=False,
            rose_missing_serials=[],
            deployment_rows=deployment_rows,
            deployment_names=names,
            concat_history=history,
            deployment_histories=[
                (names[k], _parse_history(str(grid["deployment_history"].values[k])))
                for k in range(len(names))
            ]
            if "deployment_history" in grid
            else [],
        )
        panels = _panels(_captions(grid, words, angle_source, positioned))
        report = resolve(GRIDCAT_DEFAULT, context, panels)
        f = _masthead._fact
        facts = [
            fact
            for fact in _masthead_facts(stackcat, stack_path)
            if not str(fact[0]).startswith("Source")
        ] + [
            f(
                "Grid",
                f"{float(grid.attrs['p_start_dbar']):g}–"
                f"{float(grid.attrs['p_end_dbar']):g} every "
                f"{float(grid.attrs['dp_dbar']):g}",
                "dbar",
            ),
            f("Source file", grid_path.name),
        ]
        stack_page = out_dir / f"{name}_stackcat_report.html"
        html = render_template(
            "gridcat.html",
            mooring_name=label or name,
            breadcrumb=page_crumbs(
                breadcrumb,
                "",
                [
                    (label or name, f"{name}_stackcat_report.html"),
                    ("Grid cat", None),
                ],
            ),
            nav=_nav(
                deployment_rows,
                current="gridcat",
                other=stack_page.name if stack_page.exists() else None,
            ),
            report=report,
            meta_facts=facts,
            generated=generated or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        )
        out_path.write_text(html, encoding="utf-8")
        logger.status("file", str(out_path))
        return out_path
    except Exception as exc:  # noqa: BLE001 — a failed page must not abort the concat
        warnings.warn(f"site grid report generation failed: {exc}", stacklevel=2)
        return None


__all__ = ["generate_gridcat_page", "paths"]
