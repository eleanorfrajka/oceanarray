"""Grid report HTML template and page generator."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from oceanarray import logger, paths
from oceanarray import mooring as _mooring
from oceanarray.analysis.derived import derived_fields
from oceanarray.location import is_unknown_latlon_source, parse_latlon_with_source
from oceanarray.paths import should_skip_regeneration
from oceanarray.pipeline.dataset import time_step_seconds

from ..config import parameters as params
from ..plotters.hydrography import COVERAGE_FAIR_PCT, COVERAGE_GOOD_PCT
from . import _captions, _html_helpers, _masthead
from ._breadcrumb import crumbs_from_ctx
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
    _parse_history,
    _resolve_array_report_href,
    description_links,
)
from ._navs import mooring_nav
from ._plots import (
    _make_grid_hodograph_b64,
    _make_grid_hydro_b64,
    _make_grid_n2_b64,
    _make_grid_potential_density_b64,
    _make_grid_progressive_vector_b64,
    _make_grid_rose_b64,
    _make_grid_rotary_spectrum_b64,
    _make_grid_timeseries_b64,
    _make_grid_ts_diagram_b64_and_limits,
    _make_grid_velocity_stacked_b64,
    _make_isopycnal_coverage_b64,
    _make_isopycnal_ts_b64,
    _make_overflow_temperature_b64,
    _make_spectrum_b64,
    _make_velocity_iqr_profile_b64,
    _make_wavelet_b64,
)
from ._read import _read_nc_metadata
from ._vendored.page_model import Panel, PanelGroup, Profile, Section, resolve

# ---------------------------------------------------------------------------
# Section manifest — grid registry
#
# Each page's registry lives in its own module.
# Panels wrap the unchanged ``_make_*`` adapters; sections/profiles are the
# declarative layer.  ``GRID_CAPTIONS`` is kept as a panel-id-keyed dict so it
# maps one-to-one onto the future ``oceanarray/config/default.yaml`` ``captions:``
# block (the render adapters and ``applies_to`` predicates are code and stay
# here; only the declarative data — order, titles, captions — migrates to YAML).
# Captions are plain text (Unicode notation, no markup): they become a
# user-editable data field, so they are rendered *escaped*, never ``|safe``.
# ---------------------------------------------------------------------------


#: Panel captions, keyed by panel id (caption strings are a data field, not
#: template prose).  Plain text with Unicode scientific notation — no
#: HTML markup — because a future override layer makes these user-editable YAML,
#: where trusted markup would be an injection vector.  This dict is the in-Python
#: form of ``default.yaml``'s ``captions:`` block; when that lands it is replaced
#: by a YAML load and nothing else here changes.
GRID_CAPTIONS: dict[str, str] = {
    "steps": "Processing steps that produced this gridded record, with the settings used.",
    "hydro": (
        "Temperature and salinity (PSU) on the pressure grid, interpolated "
        "in pressure between instruments; nothing is extrapolated beyond the "
        "shallowest or deepest instrument at each time."
    ),
    "ts_diagram": (
        "Potential temperature against salinity (PSU) for every grid cell "
        "(interpolated values included), as the number of values in each bin (log "
        "scale). Dashed lines: potential density σθ."
    ),
    "vel_stacked": (
        "Eastward, northward and upward velocity, current speed, and the direction "
        "toward which the current flows, interpolated in pressure between the "
        "current meters; no values above the shallowest or below the deepest."
    ),
    "vel_iqr": (
        "Distribution of current speed (left) and eastward/northward velocity "
        "(middle) at each grid level over the deployment: line = median, dark "
        "shading = middle 50 % (interquartile range), light shading = 2.5–97.5 % "
        "(speed only). Right: number of values at each level. Black line: seabed, "
        "from the water depth."
    ),
    "vel_timeseries": (
        "Current speed and eastward/northward velocity at the grid level with the "
        "highest mean speed."
    ),
    "roses": (
        "Direction toward which the current flows and speed at grid levels "
        "(interpolated between current meters). Wedge length is the percentage of "
        "the record in that direction; colours are speed classes."
    ),
    "hodograph": (
        "Velocity vectors (24-hour smoothed) at the levels of the current roses, "
        "traced through the deployment, coloured from start (dark) to end (yellow); "
        "green circle = start, red square = end."
    ),
    "progressive_vector": (
        "Cumulative displacement from adding up the hourly velocity at each level "
        "(a progressive vector diagram): how far water passing the mooring would "
        "travel if the flow were the same everywhere. It shows the mean flow "
        "direction and how steady it is, not the path of a water parcel."
    ),
    "sigma0": (
        f"Potential density anomaly σ0 (referenced to the surface; {params.vunit('potential_density')}), from "
        "potential temperature and salinity (PSU) on the grid."
    ),
    "sigma2": (
        f"Potential density anomaly σ2 (referenced to 2000 dbar; {params.vunit('potential_density')}), from "
        "potential temperature and salinity (PSU) on the grid."
    ),
    "n2": (
        f"Buoyancy frequency squared (log₁₀ N², {params.vunit('n2')}), calculated "
        "simply between neighbouring levels of the gridded temperature and salinity "
        "(GSW, no adiabatic levelling); zero or negative values are drawn in the "
        "lowest colour. For analysis, calculate N² from the measurements yourself."
    ),
    "isopycnal_coverage": (
        "Isopycnal coverage diagnostic. Percentage of time steps during which "
        f"each σ₀ surface (0.1 {params.vunit('potential_density')} spacing) lies "
        f"within the measured column range. Green ≥ {COVERAGE_GOOD_PCT:g} %; amber "
        f"{COVERAGE_FAIR_PCT:g}–{COVERAGE_GOOD_PCT:g} %; red < {COVERAGE_FAIR_PCT:g} %. "
        f"Orange diamond: the target σ₀. Dashed line: {COVERAGE_GOOD_PCT:g} %. Gaps in "
        "the tracking "
        "figure correspond to times when the isopycnal is outside the measured "
        "range (above the shallowest grid level, or below the deepest)."
    ),
    # Per-isopycnal panel — one per sigma variable, so the caption is a template
    # with a {sigma_var} field the panel builder formats in.
    "isopycnal_ts": (
        "Isopycnal height above seabed — {sigma_var}. Height above seabed (m) "
        "of each target σ₀ surface through time. Tracked by linear "
        "interpolation in density space from the gridded {sigma_var} field; "
        "1-hour running median applied. Blank where the surface outcrops or is "
        "outside the observed range. Light blue = lighter water (shallower); "
        "dark blue = denser water (deeper). Default target σ₀ = "
        + _captions._join([f"{s:.2f}" for s in params.SIGMA_GRID])
        + "."
    ),
    "overflow_temp": (
        "Temperature ~100 m above seabed. 1-hour running median temperature at "
        "the grid pressure level nearest to 100 m above the seabed. Level and "
        "height-above-seabed shown in figure title."
    ),
    "temp_spectrum": (
        "Power spectral density of temperature at a few levels (Welch estimate). "
        "Left: 14-day Hann windows; right: 7-day Hann windows, zoomed to periods "
        "under 2 days; 50 % overlap. Vertical lines: M2, K1 and the inertial "
        "period. Dashed: a −2 slope for reference."
    ),
    "wavelet": (
        "Wavelet power of temperature (Morlet, ω₀ = 6; Torrence & Compo 1998), "
        "log₁₀ °C² d. Hatched: edge-affected (cone of influence); cross-hatched: "
        "gaps filled before the transform. Black contours: 95 % significance "
        "against red noise."
    ),
    "rotary_spectrum": (
        "Rotary spectra of velocity: clockwise (red, solid) and counter-clockwise "
        "(blue, dashed) components, and the rotary coefficient r = (CCW − CW)/(CCW + "
        "CW) (right; r < 0: clockwise dominant). Welch estimate, 14-day Hann "
        "windows, 50 % overlap. Vertical lines: M2, K1 and the inertial period f."
    ),
    "nc_file": "The grid file: its coordinates, variables and global attributes.",
}


@dataclass(frozen=True)
class GridContext:
    """Shared inputs for the grid page's panels, computed once.

    The T-S diagram is drawn once, eagerly, by :func:`build_grid_context`: it
    yields ``ts_b64``, which the ``ts_diagram`` panel returns directly rather than
    re-rendering the figure, and the σθ contour interval its caption names.

    Parameters
    ----------
    ds : xarray.Dataset
        The opened gridded dataset.
    lat : float
        Mooring latitude in degrees; ``0.0`` (with a warning) if unresolved.
    lat_resolved : bool
        Whether ``lat`` came from the file attributes.  ``False`` is a metadata
        defect: the N² panel stubs on it (via ``unavailable_if``), while the
        spectra still compute at ``lat=0`` as before.
    ts_bounds : dict
        T-S axis limits and the σθ contour interval, from
        :func:`_make_grid_ts_diagram_b64_and_limits`.
    ts_b64 : str or None
        The rendered T-S diagram (base64 PNG), or ``None`` if it could not be
        drawn.
    dt_s : float
        The grid's time step in seconds: its ``dt_seconds`` attribute, else the
        median step of its time axis.
    history_entries : list
        Parsed processing-history entries (for the collapsed file record).
    nc_meta : dict
        NetCDF metadata (variables/scalars/globals) for the appendix tables.
    nc_file : str
        The gridded NetCDF file's name, for the appendix table heading.
    derived : DerivedFields or None
        Quantities the figures derive from the grid (position, θ, speed,
        direction), computed once for the page.
    density : dict of str to xarray.DataArray, or None
        σ0 and σ2 for the Stratification sections, computed once for the page;
        None without θ.
    ds_stack : xarray.Dataset or None
        The stack beside the grid (its instruments and velocity reference), or
        None when it is not there.
    instruments : tuple of InstrumentLevel
        The stack's instruments with their median pressures, shallowest first.
    scale : SectionScale or None
        The page's vertical scale: the hydrography panels' dbar per inch.
    seabed_dbar : float or None
        Seabed pressure from the water depth.
    max_speed_level : int or None
        Grid level with the highest mean speed.
    rose_levels : tuple of int
        Grid levels of the current roses and the hodographs.
    rose_left_out : tuple of int
        Grid levels with velocity for less than
        :data:`~oceanarray.analysis.derived.ROSE_MIN_FILLED` of the record, left
        out of the roses and hodographs (named in their captions).
    temp_levels, wavelet_levels, rotary_levels : tuple of int
        Grid levels of the temperature spectrum, wavelet and rotary spectrum.
    spectrum_markers : tuple of (str, float)
        Extra spectrum markers from the mooring description (label, period in days).
    stack_href : str or None
        Link to the stack page.
    """

    ds: Any
    lat: float
    lat_resolved: bool
    ts_bounds: dict
    ts_b64: str | None
    dt_s: float
    history_entries: list
    nc_meta: dict
    nc_file: str
    derived: Any = None
    density: Any = None
    ds_stack: Any = None
    instruments: tuple = ()
    scale: Any = None
    seabed_dbar: float | None = None
    max_speed_level: int | None = None
    rose_levels: tuple = ()
    rose_left_out: tuple = ()
    temp_levels: tuple = ()
    wavelet_levels: tuple = ()
    rotary_levels: tuple = ()
    spectrum_markers: tuple = ()
    stack_href: str | None = None


def _instrument_pressures(levels: tuple, variables: tuple[str, ...]) -> list[float]:
    """Return the median pressures of the instruments measuring any of *variables*."""
    return [r.pressure for r in levels if r.variables & set(variables)]


def build_grid_context(
    ds: Any,
    grid_path: Path,
    ds_stack: Any = None,
    *,
    spectrum_markers: tuple = (),
    stack_href: str | None = None,
) -> GridContext:
    """Return a :class:`GridContext` for *ds*: what its panels and captions share.

    Parameters
    ----------
    ds : xarray.Dataset
        The opened gridded dataset.
    grid_path : Path
        Path to the gridded NetCDF file (for the variable-metadata tables).
    ds_stack : xarray.Dataset or None, optional
        The stack the grid was made from, for the instrument pressures, the
        velocity reference and the instrument count; without it those are left out.
    spectrum_markers : tuple of (str, float), optional
        Extra spectrum markers (label, period in days).
    stack_href : str or None, optional
        Link to the stack page.

    Returns
    -------
    GridContext
        The shared render context for the grid page's panels.

    """
    from oceanarray.analysis.derived import (
        instrument_levels,
        level_of_max_speed,
        potential_density,
        seabed_pressure,
        sparse_velocity_levels,
        velocity_levels,
    )
    from oceanarray.analysis.spectral import select_levels
    from oceanarray.plotters.timeseries import hydro_scale

    lat_v, _, source = parse_latlon_with_source(dict(ds.attrs))
    lat_resolved = not is_unknown_latlon_source(source)
    if not lat_resolved:
        warnings.warn(
            f"grid report: mooring latitude unresolved from attrs "
            f"({ds.attrs.get('mooring_name', '?')}); N² stubs and spectra "
            f"compute at lat=0.",
            stacklevel=2,
        )
        lat_v = 0.0
    derived = derived_fields(ds)
    ts_b64, ts_bounds = _make_grid_ts_diagram_b64_and_limits(ds, derived=derived)
    dt_s = time_step_seconds(ds)
    instruments = tuple(instrument_levels(ds_stack))
    pressure = ds["pressure"].values
    rose = speed_level = None
    left_out: list[int] = []
    rotary: list[int] = []
    if {"east_velocity", "north_velocity"} <= set(ds.data_vars):
        east = ds["east_velocity"].transpose("time", "pressure").values
        north = ds["north_velocity"].transpose("time", "pressure").values
        rose = velocity_levels(east, north)
        left_out = sparse_velocity_levels(east, north)
        speed = derived.speed.transpose("time", "pressure").values
        speed_level = level_of_max_speed(speed)
        both = np.where(np.isfinite(east) & np.isfinite(north), east, np.nan).T
        rotary = select_levels(
            both,
            pressure,
            at=_instrument_pressures(instruments, ("east_velocity",)) or None,
            n=3,
        )
    temp_levels: list[int] = []
    wavelet: list[int] = []
    if "temperature" in ds.data_vars:
        t = ds["temperature"].transpose("pressure", "time").values
        at = _instrument_pressures(instruments, ("temperature",)) or None
        temp_levels = select_levels(t, pressure, at=at)
        wavelet = select_levels(t, pressure, n=3)
    return GridContext(
        ds=ds,
        lat=lat_v,
        lat_resolved=lat_resolved,
        ts_bounds=ts_bounds,
        ts_b64=ts_b64,
        dt_s=dt_s,
        history_entries=_parse_history(ds.attrs.get("history", "")),
        nc_meta=_read_nc_metadata(grid_path),
        nc_file=grid_path.name,
        derived=derived,
        density=potential_density(ds, derived) if _has_hydro_ds(ds) else None,
        ds_stack=ds_stack,
        instruments=instruments,
        scale=hydro_scale(ds),
        seabed_dbar=seabed_pressure(ds),
        max_speed_level=speed_level,
        rose_levels=tuple(rose or ()),
        rose_left_out=tuple(left_out),
        temp_levels=tuple(temp_levels),
        wavelet_levels=tuple(wavelet),
        rotary_levels=tuple(rotary),
        spectrum_markers=tuple(spectrum_markers),
        stack_href=stack_href,
    )


def _has_temperature(ctx: GridContext) -> bool:
    """Return True when the dataset carries a ``temperature`` variable."""
    # Gates Hydrography and temperature spectra: draw_grid_hydro renders whichever of
    # T/S/O2 is present, so temperature alone suffices. The T-S diagram needs both
    # T and S (see _has_hydro).
    return "temperature" in ctx.ds


def _sigma_vars(ctx: GridContext) -> list:
    """Return the pressure-dimensioned ``sigma*`` variables present, in order."""
    return [
        v
        for v in ctx.ds.data_vars
        if v.startswith("sigma") and "pressure" in ctx.ds[v].dims
    ]


def _has_velocity(ctx: GridContext) -> bool:
    """Return True when eastward or northward velocity is present."""
    # Without horizontal velocity the Velocity sections are named in the
    # not-applicable footer.
    return "east_velocity" in ctx.ds.data_vars or "north_velocity" in ctx.ds.data_vars


def _has_hydro_ds(ds: Any) -> bool:
    """Return True when *ds* has temperature and salinity (or conductivity)."""
    dv = ds.data_vars
    # Conductivity counts: salinity is derivable via gsw.SP_from_C.
    return "temperature" in dv and ("salinity" in dv or "conductivity" in dv)


def _has_hydro(ctx: GridContext) -> bool:
    """Return True when temperature and salinity (or conductivity) are present."""
    return _has_hydro_ds(ctx.ds)


def _has_sigma(ctx: GridContext) -> bool:
    """Return True when at least one pressure-dimensioned ``sigma*`` field is present."""
    return bool(_sigma_vars(ctx))


def _history_unavailable(ctx: GridContext) -> str | None:
    """Reason the processing-history panel cannot render, or ``None`` to proceed."""
    # Provenance always belongs on the report, so missing history is a visible stub
    # (unavailable_if), not a not-applicable footer entry (applies_to), which would
    # misstate a missing-metadata gap as a deployment characteristic.
    if ctx.history_entries:
        return None
    return _html_helpers.NO_HISTORY


def _theta_unavailable(ctx: GridContext) -> str | None:
    """Reason σ0/σ2 cannot be drawn (no position for θ), or None to proceed."""
    if ctx.density is not None:
        return None
    return (
        "Potential density unavailable: potential temperature needs the mooring "
        "position, which the file does not record."
    )


def _isopycnal_ts_panel(sigma_var: str) -> Panel:
    """Build the per-isopycnal height-above-seabed panel for one ``sigma`` variable."""

    def _render(ctx: GridContext, _sv: str = sigma_var) -> str | None:
        try:
            from oceanarray.analysis.hydrography import isopycnal_dataset as _iso_ds

            ds_iso = _iso_ds(
                ctx.ds, sigma_var=_sv, sigma_grid=getattr(params, "SIGMA_GRID", None)
            )
            return _make_isopycnal_ts_b64(ds_iso)
        except Exception as exc:
            # Report stays partial, but say why instead of swallowing silently.
            warnings.warn(
                f"isopycnal T/S panel for {_sv} unavailable: {exc!r}",
                stacklevel=2,
            )
            return None

    caption = GRID_CAPTIONS["isopycnal_ts"].format(sigma_var=sigma_var)
    return Panel(id=f"isopycnal_ts_{sigma_var}", render=_render, caption=caption)


#: Grid panel registry.  Figure adapters are wrapped unchanged;
#: captions come from :data:`GRID_CAPTIONS` (keyed by id), replaced on a built page
#: by the generated ones (:func:`oceanarray.reports._captions.grid_captions`).
#: ``ts_diagram`` and ``progressive_vector`` are the only non-full slots (both
#: ``"half"``).  ``applies_to``
#: answers "could this exist for this deployment?" — a false predicate omits the
#: panel (and, when a whole section drops out, names it in the not-applicable
#: footer); ``n2``'s ``unavailable_if`` is the separate "applicable but a
#: metadata precondition failed" channel (unresolved latitude → visible stub).
def _stage2_serials(ctx: GridContext) -> list[str]:
    """Return the instruments the stack took from their stage-2 files."""
    return _attr(ctx, "stage2_instruments").split()


def _attr(ctx: GridContext, key: str) -> str:
    """Return a global attribute of the grid dataset, or "" (also with no dataset)."""
    return str(getattr(ctx.ds, "attrs", {}).get(key, ""))


GRID_PANELS: dict[str, Panel] = {
    "stage2_note": Panel(
        "stage2_note",
        render=lambda c: render_template(
            "_quicklook_note.html",
            serials=_stage2_serials(c),
            velocity=bool(_attr(c, "stage2_magnetic_north")),
            current_meters=_attr(c, "stage2_no_velocity").split(),
            reason=_attr(c, "stage2_reason"),
        ),
        kind="html",
        applies_to=lambda c: bool(_stage2_serials(c)),
    ),
    "stage2_velocity_note": Panel(
        "stage2_velocity_note",
        render=lambda c: render_template(
            "_magnetic_north_note.html",
            serials=_attr(c, "stage2_magnetic_north").split(),
        ),
        kind="html",
        applies_to=lambda c: bool(_attr(c, "stage2_magnetic_north")),
    ),
    # processing steps, then the file's own record (collapsed); the appendix
    "steps": Panel(
        "steps",
        render=lambda c: render_template(
            "_processing_steps.html",
            steps=_captions.grid_steps(
                c.ds, len(c.instruments) or None, stack_href=c.stack_href
            ),
        ),
        kind="html",
        caption=GRID_CAPTIONS["steps"],
        applies_to=lambda c: c.ds is not None,
    ),
    "history": Panel(
        "history",
        render=lambda c: render_template(
            "_collapsed.html",
            summary="show the file's processing record (dated events)",
            body=render_history(c.history_entries),
        ),
        kind="html",
        unavailable_if=_history_unavailable,
    ),
    "nc_file": Panel(
        "nc_file",
        render=lambda c: render_template(
            "_collapsed.html",
            summary="show the coordinates, variables and global attributes",
            body="".join(
                render(c.nc_meta)
                for render in (
                    render_nc_coords,
                    render_nc_variables,
                    render_nc_scalars,
                    render_nc_globals,
                )
            ),
        ),
        kind="html",
        caption=GRID_CAPTIONS["nc_file"],
    ),
    # hydrography
    "hydro": Panel(
        "hydro",
        render=lambda c: as_panel(
            lambda: _make_grid_hydro_b64(
                c.ds,
                instrument_pressures={
                    "temperature": _instrument_pressures(
                        c.instruments, ("temperature",)
                    ),
                    "salinity": _instrument_pressures(
                        c.instruments, ("salinity", "conductivity")
                    ),
                },
                scale=c.scale,
            )
        ),
        caption=GRID_CAPTIONS["hydro"],
        applies_to=_has_temperature,
    ),
    "ts_diagram": Panel(
        "ts_diagram",
        render=lambda c: c.ts_b64,
        slot="half",
        caption=GRID_CAPTIONS["ts_diagram"],
        applies_to=_has_hydro,
    ),
    # velocity
    "vel_stacked": Panel(
        "vel_stacked",
        render=lambda c: as_panel(
            lambda: _make_grid_velocity_stacked_b64(
                c.ds,
                instrument_pressures=_instrument_pressures(
                    c.instruments, ("east_velocity", "north_velocity")
                ),
                scale=c.scale,
            )
        ),
        caption=GRID_CAPTIONS["vel_stacked"],
        applies_to=_has_velocity,
    ),
    "vel_iqr": Panel(
        "vel_iqr",
        render=lambda c: as_panel(
            lambda: _make_velocity_iqr_profile_b64(c.ds, scale=c.scale)
        ),
        caption=GRID_CAPTIONS["vel_iqr"],
        applies_to=_has_velocity,
    ),
    "vel_timeseries": Panel(
        "vel_timeseries",
        render=lambda c: as_panel(lambda: _make_grid_timeseries_b64(c.ds, c.derived)),
        caption=GRID_CAPTIONS["vel_timeseries"],
        applies_to=_has_velocity,
    ),
    # velocity structure
    "roses": Panel(
        "roses",
        render=lambda c: as_panel(
            lambda: _make_grid_rose_b64(c.ds, levels=list(c.rose_levels))
        ),
        caption=GRID_CAPTIONS["roses"],
        applies_to=_has_velocity,
    ),
    "hodograph": Panel(
        "hodograph",
        render=lambda c: as_panel(
            lambda: _make_grid_hodograph_b64(c.ds, levels=list(c.rose_levels))
        ),
        caption=GRID_CAPTIONS["hodograph"],
        applies_to=_has_velocity,
    ),
    "progressive_vector": Panel(
        "progressive_vector",
        render=lambda c: as_panel(lambda: _make_grid_progressive_vector_b64(c.ds)),
        slot="half",
        caption=GRID_CAPTIONS["progressive_vector"],
        applies_to=_has_velocity,
    ),
    # stratification
    **{
        ref: Panel(
            ref,
            render=lambda c, _ref=ref: as_panel(
                lambda: _make_grid_potential_density_b64(
                    c.ds, _ref, density=c.density, scale=c.scale
                )
            ),
            caption=GRID_CAPTIONS[ref],
            applies_to=_has_hydro,
            unavailable_if=_theta_unavailable,
        )
        for ref in ("sigma0", "sigma2")
    },
    "n2": Panel(
        "n2",
        render=lambda c: as_panel(
            lambda: _make_grid_n2_b64(c.ds, lat=c.lat, derived=c.derived)
        ),
        caption=GRID_CAPTIONS["n2"],
        applies_to=_has_sigma,
        unavailable_if=lambda c: (
            None
            if c.lat_resolved
            else "Buoyancy frequency unavailable: mooring latitude could not be "
            "resolved from the file attributes."
        ),
    ),
    # overflow
    "isopycnal_coverage": Panel(
        "isopycnal_coverage",
        render=lambda c: as_panel(lambda: _make_isopycnal_coverage_b64(c.ds)),
        caption=GRID_CAPTIONS["isopycnal_coverage"],
        applies_to=_has_sigma,
    ),
    "overflow_temp": Panel(
        "overflow_temp",
        render=lambda c: as_panel(lambda: _make_overflow_temperature_b64(c.ds)),
        caption=GRID_CAPTIONS["overflow_temp"],
        applies_to=_has_sigma,
    ),
    # frequency analysis
    "temp_spectrum": Panel(
        "temp_spectrum",
        render=lambda c: as_panel(
            lambda: _make_spectrum_b64(
                c.ds["temperature"],
                c.dt_s,
                lat=c.lat,
                levels=list(c.temp_levels),
                markers=c.spectrum_markers,
            )
        ),
        caption=GRID_CAPTIONS["temp_spectrum"],
        applies_to=_has_temperature,
    ),
    "wavelet": Panel(
        "wavelet",
        render=lambda c: as_panel(
            lambda: _make_wavelet_b64(
                c.ds["temperature"], c.dt_s, levels=list(c.wavelet_levels)
            )
        ),
        caption=GRID_CAPTIONS["wavelet"],
        applies_to=_has_temperature,
    ),
    "rotary_spectrum": Panel(
        "rotary_spectrum",
        render=lambda c: as_panel(
            lambda: _make_grid_rotary_spectrum_b64(
                c.ds,
                lat=c.lat,
                levels=list(c.rotary_levels),
                markers=c.spectrum_markers,
            )
        ),
        caption=GRID_CAPTIONS["rotary_spectrum"],
        applies_to=_has_velocity,
    ),
}

#: The per-isopycnal run inside Overflow — a numeric expansion, so panels (not
#: sections), per the ``PanelGroup`` rule.
_OVERFLOW_ISOPYCNALS = PanelGroup(over=_sigma_vars, panel=_isopycnal_ts_panel)

#: Grid sections, in display order: the one place the order is written, and each
#: id written once (the key is the section's own id).  8 content sections + one
#: appendix (the NetCDF file, collapsed); T-S is its own section, Velocity split 3+3.
GRID_SECTIONS: dict[str, Section] = {
    s.id: s
    for s in (
        Section(
            "processing_history",
            "Processing history",
            ("stage2_note", "steps", "history"),
        ),
        Section("hydrography", "Hydrography", ("hydro",)),
        Section("ts_diagram", "T-S diagram", ("ts_diagram",)),
        Section(
            "velocity",
            "Velocity",
            ("stage2_velocity_note", "vel_stacked", "vel_iqr", "vel_timeseries"),
        ),
        Section(
            "velocity_structure",
            "Velocity structure",
            ("roses", "hodograph", "progressive_vector"),
        ),
        Section("stratification", "Stratification", ("sigma0", "sigma2", "n2")),
        Section(
            "overflow",
            "Overflow",
            ("isopycnal_coverage", _OVERFLOW_ISOPYCNALS, "overflow_temp"),
        ),
        Section(
            "frequency_analysis",
            "Frequency analysis",
            ("temp_spectrum", "wavelet", "rotary_spectrum"),
        ),
        Section("netcdf", "NetCDF file", ("nc_file",), role="appendix"),
    )
}

#: Default grid profile: every section, in the order of :data:`GRID_SECTIONS`.
GRID_DEFAULT = Profile(numbering="flat", entries=tuple(GRID_SECTIONS.values()))


#: Grid sections that are off by default and turned on via ``report_sections`` — read
#: from the config validator's registry (one source), not restated here.
_OPTIONAL_GRID_SECTIONS: tuple[str, ...] = tuple(
    sorted(_mooring.OPTIONAL_REPORT_SECTIONS.get("grid", ()))
)


def grid_panels(ctx: GridContext) -> dict[str, Panel]:
    """Return :data:`GRID_PANELS` with the captions generated for *ctx*.

    Parameters
    ----------
    ctx : GridContext
        The page's context.

    Returns
    -------
    dict of str to Panel
        The registry, each panel with its generated caption where there is one.
    """
    captions = _captions.grid_captions(ctx)
    return {
        pid: replace(panel, caption=captions[pid]) if pid in captions else panel
        for pid, panel in GRID_PANELS.items()
    }


# ---------------------------------------------------------------------------
# Page generator
# ---------------------------------------------------------------------------


def generate_grid_page(
    mooring_name: str,
    grid_path: Path,
    ctx: dict[str, Any],
    out_dir: Path,
    force: bool,
    display_root: Path,
    skip_existing: bool = False,
) -> None:
    """Generate a grid report HTML page with T/S pcolormesh figures."""
    out_path = out_dir / f"{mooring_name}_grid_report.html"
    if should_skip_regeneration(out_path, force, skip_existing, grid_path):
        logger.status("skip", logger.format_rel_path(out_path, display_root))
        return

    try:
        import xarray as xr

        ds = xr.open_dataset(grid_path).load()
        pressure = ds["pressure"].values
        n_levels = len(pressure)
        n_time = ds.sizes["time"]
        p_min, p_max = int(pressure.min()), int(pressure.max())
        p_range = f"{p_min}–{p_max} dbar"
        grid_dp = (
            f"{int(round(float(np.median(np.diff(pressure)))))} dbar"
            if len(pressure) > 1
            else "—"
        )
        if n_time > 1:
            dt_arr = np.diff(ds["time"].values) / np.timedelta64(1, "s")
            grid_dt_s = str(int(np.median(dt_arr)))
        else:
            grid_dt_s = "—"

        # The stack beside the grid: its instruments' pressures, the velocity
        # reference and the instrument count, for the figures and captions.
        stack_path = paths.product_file(grid_path.parent, mooring_name, "stack")
        stack_exists = stack_path.exists()
        ds_stack = None
        if stack_exists:
            try:
                with xr.open_dataset(stack_path) as _f:
                    ds_stack = _f.load()
            except Exception as exc:
                warnings.warn(
                    f"grid report: could not read the stack ({exc}); instrument "
                    "pressures and counts are left out",
                    stacklevel=2,
                )
        # Build the shared context (latitude, levels, scale, history, nc_meta)
        # once, then resolve the profile against it.  ``resolve`` renders every
        # panel from the open dataset, so it must run before ``ds.close()``.
        gctx = build_grid_context(
            ds,
            grid_path,
            ds_stack,
            spectrum_markers=tuple(ctx.get("spectrum_markers", ())),
            stack_href=f"{mooring_name}_stack_report.html" if stack_exists else None,
        )
        # Optional sections are off unless the config turns them on: drop them from the
        # profile entirely (not via applies_to, which would name them in the
        # not-applicable footer — they were simply not asked for).
        report_sections = ctx.get("report_sections_grid", ())
        drop = [
            GRID_SECTIONS[name]
            for name in _OPTIONAL_GRID_SECTIONS
            if name not in report_sections
        ]
        profile = GRID_DEFAULT
        if drop:
            profile = replace(
                GRID_DEFAULT,
                entries=tuple(
                    e for e in GRID_DEFAULT.entries if not any(e is d for d in drop)
                ),
            )
        report = resolve(profile, gctx, grid_panels(gctx))
        ds.close()

        html = render_template(
            "grid.html",
            mooring_name=mooring_name,
            breadcrumb=crumbs_from_ctx(
                ctx, [(mooring_name, f"{mooring_name}_report.html"), ("Grid", None)]
            ),
            nav=mooring_nav(
                mooring_name,
                ctx.get("instruments", []),
                stack_exists=stack_exists,
                grid_exists=True,
                current="grid",
                array_href=_resolve_array_report_href(out_dir),
            ),
            report=report,
            description_links=description_links(ctx),
            cruise=ctx.get("cruise", "—"),
            meta_facts=_masthead.masthead_facts(
                "grid",
                ctx.get("masthead_common", []),
                {
                    _masthead.DT_S: grid_dt_s,
                    _masthead.N_TIME: n_time,
                    _masthead.GRID_DP: grid_dp,
                    _masthead.N_LEVELS: n_levels,
                    _masthead.P_RANGE: p_range,
                    _masthead.NC_FILE: grid_path.name,
                },
            ),
            generated=ctx["generated"],
        )
        out_path.write_text(html, encoding="utf-8")
        logger.status("file", logger.format_rel_path(out_path, display_root))
    except Exception as exc:
        print(f"  ERROR generating grid report: {exc}")
