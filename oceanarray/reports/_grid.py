"""Grid report HTML template and page generator."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from oceanarray import paths

from ..config import parameters as params
from ..utilities import (
    is_unknown_latlon_source,
    parse_latlon_with_source,
    should_skip_regeneration,
)
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
    _format_rel_path,
    _parse_history,
    _read_nc_metadata,
    _resolve_array_report_href,
    _status,
)
from ._navs import mooring_nav
from ._plots import (
    _make_grid_hodograph_b64,
    _make_grid_hydro_b64,
    _make_grid_n2_b64,
    _make_grid_rose_b64,
    _make_grid_rotary_spectrum_b64,
    _make_grid_sigma_b64,
    _make_grid_timeseries_b64,
    _make_grid_trajectory_b64,
    _make_grid_ts_diagram_b64,
    _make_grid_velocity_stacked_b64,
    _make_isopycnal_coverage_b64,
    _make_isopycnal_ts_b64,
    _make_overflow_temperature_b64,
    _make_spectrum_b64,
    _make_velocity_iqr_profile_b64,
    _make_wavelet_b64,
)
from ._vendored.page_model import Panel, PanelGroup, Profile, Section, resolve

# ---------------------------------------------------------------------------
# Section manifest — grid registry (rep/06, design §9.2–§9.3, §7)
#
# Declared here per design §9.7: each page's registry lives in its own module.
# Panels wrap the unchanged ``_make_*`` adapters; sections/profiles are the
# declarative layer.  ``GRID_CAPTIONS`` is kept as a panel-id-keyed dict so it
# maps one-to-one onto the future ``oceanarray/config/default.yaml`` ``captions:``
# block (the render adapters and ``applies_to`` predicates are code and stay
# here; only the declarative data — order, titles, captions — migrates to YAML).
# Captions are plain text (Unicode notation, no markup): they become a
# user-editable data field, so they are rendered *escaped*, never ``|safe``.
# ---------------------------------------------------------------------------


#: Panel captions, keyed by panel id (design §6.1: caption strings are a data
#: field, not template prose).  Plain text with Unicode scientific notation — no
#: HTML markup — because a future override layer makes these user-editable YAML,
#: where trusted markup would be an injection vector.  This dict is the in-Python
#: form of ``default.yaml``'s ``captions:`` block; when that lands it is replaced
#: by a YAML load and nothing else here changes.
GRID_CAPTIONS: dict[str, str] = {
    "hydro": (
        "Temperature, salinity, dissolved oxygen, and O₂ saturation (when "
        "present). Vertically interpolated to regular pressure grid • 20 "
        "discrete colour levels."
    ),
    "ts_diagram": (
        "Left: log₁₀(count+1) per T-S bin. Right (when O₂ present): median O₂ "
        "saturation per T-S bin — bins with <5 samples masked. Colour scale: "
        "BrBG (brown=low, teal=high)."
    ),
    "vel_stacked": (
        "East, north, and up velocity. QC-flagged samples excluded before "
        "interpolation. Shared symmetric Spectral_r colormap for east/north; "
        "separate bounds for up. No temporal gap fill — NaN where no data."
    ),
    "vel_iqr": (
        "Velocity IQR profiles. Median (solid line) and interquartile range "
        "(shaded, 25–75 %) across the full deployment at each pressure level. "
        "2.5–97.5 % outer envelope for speed. Right panel: count of non-NaN "
        "values per depth."
    ),
    "vel_timeseries": (
        "Velocity time series at depth of maximum mean speed. Depth chosen as "
        "the pressure level with the highest time-mean current speed. Speed, "
        "east, and north velocity at that level."
    ),
    "roses": (
        "Current roses. One rose per pressure level (up to 12, evenly "
        "subsampled). Suspect/bad QC excluded. Each petal shows the fraction of "
        "time current flowed in that direction; petal length encodes speed "
        f"({params.vunit('east_velocity')})."
    ),
    "hodograph": (
        "Hodograph. Current hodographs at two pressure levels (25th and 75th "
        "percentile of the valid range). Top row = shallower level; bottom row "
        "= deeper level. Left = Tukey-smoothed raw; right = eddy component (LP "
        "mean removed). Colour indicates fractional time through the deployment "
        "(lime circle = start, red square = end). QC-bad data excluded."
    ),
    "trajectory": (
        "Particle trajectory. Pseudo-Lagrangian trajectories at each gridded "
        "pressure level, integrated from east and north velocity (Euler "
        "forward). All start at the origin. Coloured by pressure (dbar)."
    ),
    "sigma0": "Potential density. 20 discrete colour levels.",
    "n2": (
        f"Buoyancy frequency squared (N²). log₁₀(N²) in {params.vunit('n2')}. "
        "Purple = strongly stratified; yellow = weakly stratified. Computed from "
        "T and S via GSW."
    ),
    "isopycnal_coverage": (
        "Isopycnal coverage diagnostic. Percentage of time steps during which "
        f"each σ₀ surface (0.1 {params.vunit('potential_density')} spacing) lies "
        "within the measured column "
        "range. Green ≥ 80 %; amber 50–80 %; red < 50 %. Orange diamond = "
        "current --sig-level target. Dashed line = 80 % threshold. NaN gaps in "
        "the tracking figure above correspond to times when the isopycnal is "
        "outside the measured range (pycnocline above the shallowest grid "
        "level, or below the deepest)."
    ),
    # Per-isopycnal panel — one per sigma variable, so the caption is a template
    # with a {sigma_var} field the panel builder formats in.  It is the one
    # GRID_CAPTIONS entry carrying a substitution; in the future YAML it is a
    # caption string with the same field.
    "isopycnal_ts": (
        "Isopycnal height above seabed — {sigma_var}. Height above seabed (m) "
        "of each target σ₀ surface through time. Tracked by linear "
        "interpolation in density space from the gridded {sigma_var} field; "
        "1-hour running median applied. NaN where the surface outcrops or is "
        "outside the observed range. Light blue = lighter water (shallower); "
        "dark blue = denser water (deeper). Targets set by --sig-level "
        "(default σ₀ = 27.70)."
    ),
    "overflow_temp": (
        "Temperature ~100 m above seabed. 1-hour running median temperature at "
        "the grid pressure level nearest to 100 m above the seabed. Level and "
        "height-above-seabed shown in figure title."
    ),
    "temp_spectrum": (
        "Temperature power spectrum. Left: low-frequency overview — 14-day Hann "
        "windows, 50 % overlap. Right: high-frequency zoom — 1-day windows "
        "(~14× more windows, smoother tidal/inertial estimate); x-axis in "
        "hours. LF markers: M2, 1.8 d, 4 d, f. HF markers: M2 (12.4 h), f "
        "(inertial). Dashed black line: −2 spectral slope. Colour encodes "
        "pressure (light blue = shallow, dark blue = deep). Window length "
        "controllable via hf_segment_days in _make_spectrum_b64."
    ),
    "wavelet": (
        "Temperature wavelet scalogram. Continuous wavelet transform (Morlet, "
        "ω₀ = 6; Torrence & Compo 1998). Three depth levels: 2nd from top, "
        "middle, 2nd from bottom of the 100-dbar-multiple valid levels. Colour "
        "shows log₁₀(power) (°C² d); hatched grey region = cone of influence "
        "(edge-affected); hatched cross region = gap-filled data; black contour "
        "= 95 % significance against red noise."
    ),
    "rotary_spectrum": (
        "Rotary velocity spectrum. CW (clockwise, anticyclonic, solid red "
        "lines) and CCW (counter-clockwise, cyclonic, dashed blue lines) power "
        "spectra and rotary coefficient r = (CCW−CW)/(CCW+CW). Welch PSD, Hann "
        "window, 14-day segments, 50 % overlap. Up to 4 pressure levels (at "
        "most 1/5th of valid levels); colour encodes pressure depth. Vertical "
        "lines: M2, K1, 1.8 d, 4 d, and inertial period (f). r > 0 = CCW "
        "dominant; r < 0 = CW dominant. Physical interpretation (NH): inertial "
        "oscillations are inherently CW; for internal waves (f < ω), CW "
        "dominance indicates upward energy propagation / downward phase "
        "propagation (Leaman & Sanford 1975)."
    ),
}


@dataclass(frozen=True)
class GridContext:
    """Shared inputs for the grid page's panels, computed once (design §9.3).

    The T-S diagram is drawn once, eagerly, by :func:`build_grid_context`: it
    both produces ``ts_bounds`` (consumed by Hydrography, independent of whether
    the T-S *section* is in the profile) and yields ``ts_b64``, which the
    ``ts_diagram`` panel returns directly rather than re-rendering the figure.

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
        Shared T-S axis limits, from :func:`_make_grid_ts_diagram_b64`.
    ts_b64 : str or None
        The rendered T-S diagram (base64 PNG), or ``None`` if it could not be
        drawn.  Cached here so the figure is drawn once, not once for the bounds
        and again for the panel.
    dt_s : float
        Sample interval in seconds.
    history_entries : list
        Parsed processing-history entries (for the history panel).
    nc_meta : dict
        NetCDF metadata (variables/scalars/globals) for the appendix tables.
    nc_file : str
        The gridded NetCDF file's name, for the appendix table heading.

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


def build_grid_context(ds: Any, grid_path: Path) -> GridContext:
    """Return a :class:`GridContext` for *ds*, computing ``ts_bounds`` eagerly.

    Parameters
    ----------
    ds : xarray.Dataset
        The opened gridded dataset.
    grid_path : Path
        Path to the gridded NetCDF file (for the variable-metadata tables).

    Returns
    -------
    GridContext
        The shared render context for the grid page's panels.

    """
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
    ts_b64, ts_bounds = _make_grid_ts_diagram_b64(ds)
    dt_s = float(ds.attrs.get("dt_seconds", 3600))
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


def _has_hydro(ctx: GridContext) -> bool:
    """Return True when temperature and salinity (or conductivity) are present."""
    dv = ctx.ds.data_vars
    # Conductivity counts: salinity is derivable via gsw.SP_from_C.
    return "temperature" in dv and ("salinity" in dv or "conductivity" in dv)


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
    return "No processing history recorded in the file attributes."


def _has_global_attrs(ctx: GridContext) -> bool:
    """Return True when the NetCDF file exposes global attributes."""
    return bool(ctx.nc_meta.get("global_groups"))


def _isopycnal_ts_panel(sigma_var: str) -> Panel:
    """Build the per-isopycnal height-above-seabed panel for one ``sigma`` variable."""

    def _render(ctx: GridContext, _sv: str = sigma_var) -> str | None:
        try:
            from ..tools import isopycnal_dataset as _iso_ds

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


#: Grid panel registry (design §9.2).  Figure adapters are wrapped unchanged;
#: captions come from :data:`GRID_CAPTIONS` (keyed by id).  ``ts_diagram`` and
#: ``trajectory`` are the only non-full slots (both ``"half"``).  ``applies_to``
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
    # non-figure appendix / history — payloads are markup from sub-templates
    "history": Panel(
        "history",
        render=lambda c: render_history(c.history_entries),
        kind="html",
        unavailable_if=_history_unavailable,
    ),
    "nc_coords": Panel(
        "nc_coords",
        render=lambda c: render_nc_coords(c.nc_meta),
        kind="table",
        applies_to=lambda c: bool(c.nc_meta.get("coords_rows")),
    ),
    "nc_variables": Panel(
        "nc_variables",
        render=lambda c: render_nc_variables(c.nc_meta),
        kind="table",
    ),
    "nc_scalars": Panel(
        "nc_scalars",
        render=lambda c: render_nc_scalars(c.nc_meta),
        kind="table",
        applies_to=lambda c: bool(c.nc_meta.get("scalar_rows")),
    ),
    "nc_globals": Panel(
        "nc_globals",
        render=lambda c: render_nc_globals(c.nc_meta),
        kind="table",
        applies_to=_has_global_attrs,
    ),
    # hydrography
    "hydro": Panel(
        "hydro",
        render=lambda c: as_panel(
            lambda: _make_grid_hydro_b64(c.ds, var_bounds=c.ts_bounds)
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
        render=lambda c: as_panel(lambda: _make_grid_velocity_stacked_b64(c.ds)),
        caption=GRID_CAPTIONS["vel_stacked"],
        applies_to=_has_velocity,
    ),
    "vel_iqr": Panel(
        "vel_iqr",
        render=lambda c: as_panel(lambda: _make_velocity_iqr_profile_b64(c.ds)),
        caption=GRID_CAPTIONS["vel_iqr"],
        applies_to=_has_velocity,
    ),
    "vel_timeseries": Panel(
        "vel_timeseries",
        render=lambda c: as_panel(lambda: _make_grid_timeseries_b64(c.ds)),
        caption=GRID_CAPTIONS["vel_timeseries"],
        applies_to=_has_velocity,
    ),
    # velocity structure
    "roses": Panel(
        "roses",
        render=lambda c: as_panel(lambda: _make_grid_rose_b64(c.ds)),
        caption=GRID_CAPTIONS["roses"],
        applies_to=_has_velocity,
    ),
    "hodograph": Panel(
        "hodograph",
        render=lambda c: as_panel(lambda: _make_grid_hodograph_b64(c.ds)),
        caption=GRID_CAPTIONS["hodograph"],
        applies_to=_has_velocity,
    ),
    "trajectory": Panel(
        "trajectory",
        render=lambda c: as_panel(lambda: _make_grid_trajectory_b64(c.ds)),
        slot="half",
        caption=GRID_CAPTIONS["trajectory"],
        applies_to=_has_velocity,
    ),
    # stratification
    "sigma0": Panel(
        "sigma0",
        render=lambda c: as_panel(lambda: _make_grid_sigma_b64(c.ds)),
        caption=GRID_CAPTIONS["sigma0"],
        applies_to=_has_sigma,
    ),
    "n2": Panel(
        "n2",
        render=lambda c: as_panel(lambda: _make_grid_n2_b64(c.ds, lat=c.lat)),
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
            lambda: _make_spectrum_b64(c.ds["temperature"], c.dt_s, lat=c.lat)
        ),
        caption=GRID_CAPTIONS["temp_spectrum"],
        applies_to=_has_temperature,
    ),
    "wavelet": Panel(
        "wavelet",
        render=lambda c: as_panel(
            lambda: _make_wavelet_b64(c.ds["temperature"], c.dt_s)
        ),
        caption=GRID_CAPTIONS["wavelet"],
        applies_to=_has_temperature,
    ),
    "rotary_spectrum": Panel(
        "rotary_spectrum",
        render=lambda c: as_panel(
            lambda: _make_grid_rotary_spectrum_b64(c.ds, lat=c.lat)
        ),
        caption=GRID_CAPTIONS["rotary_spectrum"],
        applies_to=_has_velocity,
    ),
}

#: The per-isopycnal run inside Overflow — a numeric expansion, so panels (not
#: sections), per design §9.7 / the ``PanelGroup`` rule.
_OVERFLOW_ISOPYCNALS = PanelGroup(over=_sigma_vars, panel=_isopycnal_ts_panel)

#: Grid sections (design §7).  8 content sections + one appendix.
GRID_SECTIONS: dict[str, Section] = {
    "processing_history": Section(
        "processing_history", "Processing history", ("stage2_note", "history")
    ),
    "hydrography": Section("hydrography", "Hydrography", ("hydro",)),
    "ts_diagram": Section("ts_diagram", "T-S diagram", ("ts_diagram",)),
    "velocity": Section(
        "velocity",
        "Velocity",
        ("stage2_velocity_note", "vel_stacked", "vel_iqr", "vel_timeseries"),
    ),
    "velocity_structure": Section(
        "velocity_structure", "Velocity structure", ("roses", "hodograph", "trajectory")
    ),
    "stratification": Section("stratification", "Stratification", ("sigma0", "n2")),
    "overflow": Section(
        "overflow",
        "Overflow",
        ("isopycnal_coverage", _OVERFLOW_ISOPYCNALS, "overflow_temp"),
    ),
    "frequency_analysis": Section(
        "frequency_analysis",
        "Frequency analysis",
        ("temp_spectrum", "wavelet", "rotary_spectrum"),
    ),
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

#: Default grid profile: T-S its own §3, Velocity split 3+3 (design §7).
GRID_DEFAULT = Profile(
    numbering="flat",
    entries=(
        GRID_SECTIONS["processing_history"],
        GRID_SECTIONS["hydrography"],
        GRID_SECTIONS["ts_diagram"],
        GRID_SECTIONS["velocity"],
        GRID_SECTIONS["velocity_structure"],
        GRID_SECTIONS["stratification"],
        GRID_SECTIONS["overflow"],
        GRID_SECTIONS["frequency_analysis"],
        GRID_SECTIONS["netcdf_variables"],
        GRID_SECTIONS["netcdf_attributes"],
    ),
)


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
        _status("skip", _format_rel_path(out_path, display_root))
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
        n_instr = ctx.get("n_instruments", "—")

        # Build the shared context (latitude, ts_bounds, history, nc_meta) once,
        # then resolve the profile against it.  ``resolve`` renders every panel
        # from the open dataset, so it must run before ``ds.close()``.
        gctx = build_grid_context(ds, grid_path)
        report = resolve(GRID_DEFAULT, gctx, GRID_PANELS)
        ds.close()

        stack_exists = paths.product_file(
            grid_path.parent, mooring_name, "stack"
        ).exists()

        html = render_template(
            "grid.html",
            mooring_name=mooring_name,
            nav=mooring_nav(
                mooring_name,
                ctx.get("instruments", []),
                stack_exists=stack_exists,
                grid_exists=True,
                current="grid",
                array_href=_resolve_array_report_href(out_dir),
            ),
            report=report,
            cruise=ctx.get("cruise", "—"),
            ship=ctx.get("ship", "—"),
            deploy_time=ctx["deploy_time"],
            recover_time=ctx["recover_time"],
            duration=ctx.get("duration", "—"),
            waterdepth=ctx.get("waterdepth", "—"),
            n_levels=n_levels,
            n_time=n_time,
            p_range=p_range,
            nc_file=grid_path.name,
            latitude=ctx.get("latitude", "—"),
            longitude=ctx.get("longitude", "—"),
            n_instr=n_instr,
            grid_dt_s=grid_dt_s,
            grid_dp=grid_dp,
            generated=ctx["generated"],
        )
        out_path.write_text(html, encoding="utf-8")
        _status("file", _format_rel_path(out_path, display_root))
    except Exception as exc:
        print(f"  ERROR generating grid report: {exc}")
