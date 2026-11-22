"""The site grid page: a site's gridded record over the years, one mooring per profile."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from oceanarray import logger, paths
from oceanarray.analysis import site

from ..config import parameters as params
from ..pipeline.dataset import canonical_grid_attrs
from ..plotters import helpers as _helpers
from ..plotters import site as site_plots
from ..plotters.site import section_cmap, symmetric_limit
from . import _captions as _captions_mod
from . import _masthead
from ._breadcrumb import page_crumbs
from ._concat import (
    CONCAT_PANELS,
    CONCAT_SECTIONS,
    SITE_DESCRIPTION,
    ConcatContext,
    _deployment_rows,
    _deployment_windows,
    _description_links,
    _drop_short,
    _masthead_facts,
    _nav,
)
from ._env import render_template
from ._figdebug import as_panel
from ._read import _read_nc_metadata
from ._slots import render as render_slot
from ._stack import STACK_SECTIONS
from ._vendored.page_model import Panel, Profile, Section, resolve

log = logging.getLogger(__name__)

#: The sections' and profiles' variables, in order, with their panel titles.
_HYDRO = (("temperature", "Temperature"), ("salinity", "Salinity"))


def _velocity_titles(angle: float | None, given: bool) -> list[tuple[str, str]]:
    """Return the velocity variables shown, with their titles.

    Parameters
    ----------
    angle : float or None
        The rotation, degrees clockwise from true north; None for none.
    given : bool
        Whether the angle was given (else it is the mean flow's direction).

    Returns
    -------
    list of (str, str)
    """
    out = [("east_velocity", "U (east)"), ("north_velocity", "V (north)")]
    if angle is None:
        return out
    if given:
        titles = (
            f"Towards {angle:.0f}°",
            f"Across (towards {(angle + 90) % 360:.0f}°)",
        )
    else:
        titles = (f"Mean direction ({angle:.0f}°)", "Cross direction")
    return out + list(zip(("mean_direction", "cross_direction"), titles, strict=True))


def _captions(
    grid: Any,
    stackcat: Any,
    words: str,
    angle: float | None,
    given: bool,
    positioned: bool,
    left_out: dict[str, list[str]],
) -> dict[str, str]:
    """Return the page's captions: the shared intro, then one short caption per figure."""
    s0 = ", ".join(f"{v:g}" for v in params.SITE_SIGMA0_LEVELS)
    s2 = ", ".join(f"{v:g}" for v in params.SITE_SIGMA2_LEVELS)
    density = params.vunit("potential_density")
    hydro = ["temperature", "salinity"]
    velocity = ["east_velocity", "north_velocity"]
    flags_ts = _captions_mod.flag_sentence(grid.attrs, hydro)
    flags_uv = _captions_mod.flag_sentence(grid.attrs, velocity)
    short = [
        f"{', '.join(names)} ({family})" for family, names in left_out.items() if names
    ]
    fraction = _captions_mod.record_fraction_text()
    intro = (
        f"Shown as {words} means of the hourly grid ({site.display_steps_text()}); "
        "the bins restart at each deployment's start, so none spans two "
        "deployments and a step between deployments shows as a sharp edge. A bin "
        f"with data for less than {params.MIN_DISPLAY_BIN_FRACTION:.0%} of its time is "
        f"left blank, and a deployment with values for less than {fraction} its "
        "time is left out"
        + (f" ({'; '.join(short)})" if short else " (none here)")
        + ". "
        "Grey bands are the turnarounds; a dotted line and a ▼ above the panel mark "
        "each deployment's start; grey lines are the pressures of the instruments "
        "measuring the variable shown (between them the grid is interpolated, so "
        "where the instruments' depths change between deployments, part of a "
        f"difference between deployments is the change in sampling). {flags_ts}"
    )
    contours = (
        f" Black contours: σ₀ {s0} and σ₂ {s2} {density}, drawn within each "
        "deployment and labelled in the right margin."
        if positioned
        else " No isopycnals: no deployment has a position."
    )
    reference = _captions_mod.direction_reference(stackcat)
    if angle is None:
        named = "Eastward and northward velocity (no mean direction: the flow has none)"
    elif given:
        named = (
            f"Eastward and northward velocity, and the velocity toward {angle:.0f}° "
            f"and across it (toward {(angle + 90) % 360:.0f}°)"
        )
    else:
        named = (
            f"Eastward, northward, mean-direction (toward {angle:.0f}°, the direction "
            "of the record-mean flow) and cross-direction "
            f"({(angle + 90) % 360:.0f}°) velocity"
        )
    return {
        "intro": intro,
        "sections_ts": "Temperature and salinity (PSU)." + contours,
        "anomalies_ts": (
            "Temperature and salinity (PSU) minus their median over the whole "
            "record at each level. Red/brown: warmer/saltier than usual; colours "
            "symmetric about zero, to ± the larger of the 2nd and 98th percentiles."
            + contours
        ),
        "sections_uv": (
            named
            + (f", {reference}" if reference else "")
            + ". Red: toward the named direction. Cropped to the pressures with "
            "velocity, the panels made shorter in proportion, so a dbar spans the "
            f"same height as in the sections above. {flags_uv}"
        ),
        "profiles_ts": (
            "Median of the hourly values at each level, one line per deployment, "
            "coloured in time order (see legend). A level is drawn only where the "
            f"deployment has values for at least {fraction} its time: a level reached "
            "only when the mooring is knocked down sees only the strong-flow hours. "
            + flags_ts
        ),
        "profiles_uv": (
            "Median velocity at each level, one line per deployment, coloured in "
            "time order (see legend), where the deployment has velocity for at "
            f"least {fraction} its time"
            + (
                f"; {'rotated' if given else 'mean direction'} toward {angle:.0f}°"
                if angle is not None
                else ""
            )
            + (f"; {reference}" if reference else "")
            + ". Dashed: zero. Cropped to the velocity pressures, the figure made "
            f"shorter in proportion. {flags_uv}"
        ),
        "site_ts": (
            "Potential temperature against salinity (PSU) of each deployment's "
            "median profile, one line per deployment; a median is not moved by a "
            f"spike. Levels with values for less than {fraction} the deployment's "
            "time are left out. Axes span the full range of the medians. Circles: each "
            "deployment's instruments measuring both T and S, at their mean "
            "pressure, so the curve between circles is the grid's interpolation. "
            f"Contours: σ₀ {s0} dashed and σ₂ {s2} solid {density} (they cross on "
            "this diagram, so they are drawn differently)."
            if positioned
            else "Median in-situ temperature and salinity (PSU) at each grid level, "
            "one line per deployment. Circles: each deployment's instruments "
            "measuring both T and S, at their mean pressure. No deployment has a "
            "position, so no potential temperature and no isopycnals."
        )
        + f" {flags_ts}",
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
        "nc_file": replace(
            CONCAT_PANELS["nc_file"],
            caption=_captions_mod.netcdf_caption("site grid file", SITE_DESCRIPTION),
        ),
    }


def _sections(intro: str) -> dict[str, Section]:
    """Return the page's own sections; the first carries the intro shared by the sections."""
    return {
        "sections": Section(
            "sections", "Sections over the years", ("sections_ts",), intro=intro
        ),
        "anomalies": Section(
            "anomalies", "Anomalies from the whole-record median", ("anomalies_ts",)
        ),
        "velocity": Section("velocity", "Velocity", ("sections_uv",)),
        "ts_diagram": Section("ts_diagram", "T-S diagram", ("site_ts",)),
        "profiles": Section(
            "profiles", "Median profiles per deployment", ("profiles_ts", "profiles_uv")
        ),
    }


def _profile(intro: str) -> Profile:
    """Return the site grid page's sections, in order."""
    return Profile(
        numbering="flat",
        entries=(
            *_sections(intro).values(),
            CONCAT_SECTIONS["deployments"],
            CONCAT_SECTIONS["processing_history"],
            STACK_SECTIONS["netcdf"],
        ),
    )


def _figures(
    grid: Any, stackcat: Any, names: list[str], angle: float | None, given: bool
) -> tuple[dict, str]:
    """Return the page's figures and the averaging in words."""
    pressure = grid["pressure"].values.astype(float)
    index = grid["deployment_index"].values
    freq, words = site.display_step(grid["time"].values)
    bins = site.display_bins(grid["time"].values, index, freq)
    shown = site.display_means(grid, bins)
    hourly = {
        v: grid[v] for v in grid.data_vars if grid[v].dims == ("time", "pressure")
    }
    if angle is not None and "east_velocity" in grid and "north_velocity" in grid:
        hourly["mean_direction"], _ = site.rotated_components(
            grid["east_velocity"], grid["north_velocity"], angle
        )
        shown["mean_direction"], shown["cross_direction"] = site.rotated_components(
            shown["east_velocity"], shown["north_velocity"], angle
        )

    # The pressures of the instruments behind each panel, once per variable of
    # the site stack (the rotated components are the velocity instruments').
    measured: dict[str, np.ndarray] = {}

    def instruments(var: str) -> np.ndarray | None:
        source = (
            "east_velocity" if var in ("mean_direction", "cross_direction") else var
        )
        if source not in stackcat:
            return None
        if source not in measured:
            measured[source] = site.instrument_pressures(stackcat, source, bins)
        return measured[source]

    position = _position(grid)
    isopycnals = []
    if position is not None and "temperature" in shown and "salinity" in shown:
        s0, s2 = site.density_fields(
            shown["temperature"].values, shown["salinity"].values, pressure, *position
        )
        isopycnals = [
            (s0, params.SITE_SIGMA0_LEVELS, r"$\sigma_0$"),
            (s2, params.SITE_SIGMA2_LEVELS, r"$\sigma_2$"),
        ]

    short = dict(
        zip([str(n) for n in grid["deployment_name"].values], names, strict=True)
    )
    windows = [(start, end, short[n]) for start, end, n in _deployment_windows(grid)]
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
    velocity = [(v, t) for v, t in _velocity_titles(angle, given) if v in shown]
    limit = symmetric_limit(*(shown[v].values for v, _ in velocity))
    vel_sections = [
        {
            "data": shown[v].values,
            "title": title,
            "units": params.vunit("east_velocity"),
            "cmap": section_cmap("east_velocity"),
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
            min_fraction=params.MIN_RECORD_FRACTION,
        )

    def sections_b64(panels: list[dict], contours: list, crop: bool = False) -> Any:
        return as_panel(
            lambda: render_slot(
                site_plots.draw_site_sections,
                panels,
                bins.centers,
                pressure,
                time_edges=bins.edges,
                owner=bins.owner,
                deployments=windows,
                isopycnals=contours,
                crop=crop,
                optional=True,
            )
        )

    def profiles_b64(columns: list[dict], slot: str, **kwargs: Any) -> Any:
        return as_panel(
            lambda: render_slot(
                site_plots.draw_site_profiles,
                columns,
                pressure,
                names,
                slot=slot,
                optional=True,
                **kwargs,
            )
        )

    hydro_medians = {v: medians(v) for v, _ in hydro}

    def site_ts() -> str | None:
        if "temperature" not in hydro_medians or "salinity" not in hydro_medians:
            return None
        salinity = hydro_medians["salinity"]
        temperature = hydro_medians["temperature"]
        pins = None
        if "temperature" in stackcat and "salinity" in stackcat:
            # Paired by name: the curves are in grid order, the pins in stack order.
            by_name = dict(
                zip(
                    [str(n) for n in stackcat["deployment_name"].values],
                    site.instrument_mean_pressures(stackcat),
                    strict=True,
                )
            )
            pins = [by_name.get(str(n), []) for n in grid["deployment_name"].values]
        if position is not None:
            temperature = site.potential_temperature(
                temperature, salinity, pressure, *position
            )
        return render_slot(
            site_plots.draw_site_ts,
            temperature,
            salinity,
            names,
            *(position or (None, None)),
            sigma0_levels=params.SITE_SIGMA0_LEVELS if position else (),
            sigma2_levels=params.SITE_SIGMA2_LEVELS if position else (),
            pressure=pressure,
            pins=pins,
            slot="half",
            optional=True,
        )

    figs = {
        "sections_ts": sections_b64(sections, isopycnals),
        "anomalies_ts": sections_b64(anomalies, isopycnals),
        "sections_uv": sections_b64(vel_sections, [], crop=True),
        "profiles_ts": profiles_b64(
            [
                {"medians": hydro_medians[v], "title": params.vlabel(v)}
                for v, _ in hydro
            ],
            "twothirds",
        ),
        "profiles_uv": profiles_b64(
            [
                {
                    "medians": medians(v),
                    "title": f"{t} ({params.vunit('east_velocity')})",
                }
                for v, t in velocity
                if v != "cross_direction"
            ],
            "full",
            crop=True,
            zero_line=True,
        ),
        "site_ts": as_panel(site_ts),
    }
    if position is None:
        log.warning(
            "no deployment has a position: no isopycnals, and the T-S diagram is in "
            "in-situ temperature"
        )
    return figs, words


def _rotation(grid: Any, rotate_to: float | None) -> tuple[float | None, bool]:
    """Return the velocity rotation (°) and whether it was given.

    Without *rotate_to*, the direction of the record-mean flow, when it is steady
    enough (:data:`~oceanarray.config.parameters.ROTATION_STEADINESS_MIN`);
    otherwise none, with a warning.

    Parameters
    ----------
    grid : xarray.Dataset
        The site grid.
    rotate_to : float or None
        Degrees clockwise from true north, toward.

    Returns
    -------
    angle : float or None
    given : bool
    """
    if rotate_to is not None:
        angle = float(rotate_to)
        if not np.isfinite(angle):
            raise ValueError(f"rotate_to={rotate_to!r} is not a finite angle")
        return angle % 360.0, True
    if "east_velocity" not in grid or "north_velocity" not in grid:
        return None, False
    angle, steadiness = site.mean_flow(grid["east_velocity"], grid["north_velocity"])
    if not steadiness >= params.ROTATION_STEADINESS_MIN:
        log.warning(
            f"no mean direction: the mean flow is weak against its variability "
            f"(steadiness {steadiness:.2f} < {params.ROTATION_STEADINESS_MIN:g}); "
            "give rotate_to (--rotate-to)."
        )
        return None, False
    return round(angle, 1), False


def _position(grid: Any) -> tuple[float, float] | None:
    """Return the site's (lon, lat): the deployments' median, None when none has one."""
    lat = grid["deployment_latitude"].values.astype(float)
    lon = grid["deployment_longitude"].values.astype(float)
    ok = np.isfinite(lat) & np.isfinite(lon)
    return (float(np.median(lon[ok])), float(np.median(lat[ok]))) if ok.any() else None


def generate_gridcat_page(
    grid_path: Path,
    out_dir: Path,
    *,
    generated: str | None = None,
    report_dir: Path | None = None,
    breadcrumb: list[tuple[str, str | None]] | None = None,
    label: str | None = None,
    rotate_to: float | None = None,
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
    rotate_to : float or None, optional
        Rotate the velocity to this direction, degrees clockwise from true north
        (toward); default the direction of the record-mean flow, when it is steady.

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
        names = _helpers.short_names([str(n) for n in grid["deployment_name"].values])
        grid, left_out = _drop_short(grid, grid["deployment_index"].values, names)
        angle, given = _rotation(grid, rotate_to)
        figs, words = _figures(grid, stackcat, names, angle, given)
        captions = _captions(
            grid, stackcat, words, angle, given, _position(grid) is not None, left_out
        )
        deployment_rows = _deployment_rows(stackcat, stack_path, out_dir, report_dir)
        context = ConcatContext(
            figs=figs,
            present_vars=set(grid.data_vars),
            instr_types={str(t).lower() for t in stackcat["instrument_type"].values},
            n_instr=stackcat.sizes["N_LEVELS"],
            has_analog=False,
            instr_rows=[],
            nc_meta=_read_nc_metadata(grid_path),
            nc_file=grid_path.name,
            rose_note=None,
            rose_unapplied=[],
            deployment_rows=deployment_rows,
            deployment_names=names,
            steps=_captions_mod.stack_history_rows(grid),
            deployment_steps=[
                (
                    str(grid["deployment_name"].values[k]),
                    _captions_mod.history_rows(str(h)),
                )
                for k, h in enumerate(grid["deployment_history"].values)
            ]
            if "deployment_history" in grid
            else [],
        )
        report = resolve(_profile(captions["intro"]), context, _panels(captions))
        f = _masthead._fact
        grid_attrs = canonical_grid_attrs(grid.attrs)
        facts = [
            fact
            for fact in _masthead_facts(stackcat, stack_path)
            if not str(fact[0]).startswith("Source")
        ] + [
            f(
                "Grid",
                f"{float(grid_attrs['pmin_dbar']):g}–"
                f"{float(grid_attrs['pmax_dbar']):g} every "
                f"{float(grid_attrs['dp_dbar']):g}",
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
            description_links=_description_links(deployment_rows),
            meta_facts=facts,
            generated=generated or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        )
        out_path.write_text(html, encoding="utf-8")
        logger.status("file", str(out_path))
        return out_path
    except Exception as exc:  # noqa: BLE001 — a failed page must not abort the concat
        log.warning("site grid report generation failed: %s", exc)
        return None


__all__ = ["generate_gridcat_page", "paths"]
