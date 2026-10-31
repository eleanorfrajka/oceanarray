"""Stack report HTML template, tilt panels helper, and page generator."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

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
    _find_array_report_href,
    _instrument_report_exists,
    _instrument_report_href,
    _parse_dt,
    _parse_history,
    _read_nc_metadata,
    _safe_rel,
    _should_skip,
    _status,
)
from ._navs import mooring_nav
from ._plots import (
    _make_adcp_trajectories_b64,
    _make_analog_timeseries,
    _make_aquadopp_speed_profile,
    _make_aquadopp_tilt_panels,
    _make_clock_check_b64,
    _make_instrument_spacing_b64,
    _make_multi_aquadopp_trajectories,
    _make_rose_grid_b64,
    _make_stack_timeseries_b64,
    _make_stack_ts_diagram,
)
from ._vendored.page_model import Panel, Profile, Section, resolve

# ---------------------------------------------------------------------------
# Aquadopp tilt helper (was @staticmethod on MooringReport)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Page generator
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Section manifest — stack registry (rep/07)
#
# Unlike grid (lazy adapters rendered during resolve), the stack figures are
# built by closures over the open dataset in ``generate_stack_page``; the port
# keeps that unchanged and collects the results into ``StackContext`` (a bag of
# pre-built base64 figures plus the variable/instrument sets the ``applies_to``
# predicates need).  Panels return the cached b64; the manifest drives structure
# (grouped Hydrography/Velocity sections, generated jump-nav, numbering, stubs).
# ---------------------------------------------------------------------------

#: Stack panel captions, keyed by panel id — plain text, Unicode notation, no
#: markup (a future ``config/report.yaml`` makes these user-editable; see grid).
STACK_CAPTIONS: dict[str, str] = {
    "hydro_pts": (
        "Pressure, temperature and salinity for the full deployment — one row "
        "each on a shared time axis. One colour per instrument (shared across "
        "rows, ordered by height) so a line can be followed through all three "
        "panels; the single legend spans the union of instruments. Values flagged "
        "3, 4 or 9 (correctable bad, bad, missing) are left out; interpolated (8) "
        "and nominal (7) values are drawn. ADCP excluded from pressure."
    ),
    "dissolved_oxygen": (
        "One line per instrument with dissolved oxygen data (SBE ODO sensor); "
        f"values flagged 3, 4 or 9 left out. Units: {params.vunit('dissolved_oxygen')}. "
        "% saturation available in per-instrument reports."
    ),
    "vel_uvw": (
        "Eastward (U), northward (V) and upward (W) velocity in the ENU frame — "
        "one row each on a shared time axis, one colour per instrument (shared "
        "across rows, ordered by height) with a single legend over the union of "
        "instruments. Values flagged 3, 4 or 9 left out; instruments without "
        "velocity data omitted."
    ),
    "turbidity": (
        "One line per instrument with turbidity data; values flagged 3, 4 or 9 "
        "left out. Dots "
        "overlaid to reveal individual samples near zero. Units from file attrs "
        "(verify: NTU, FTU, or V depending on sensor)."
    ),
    "trajectories_aquadopp": (
        "Pseudo-Lagrangian particle trajectories integrated from each Aquadopp's "
        "east/north velocity. Start at the origin; coloured by time through the "
        "deployment."
    ),
    "trajectories_adcp": (
        "Pseudo-Lagrangian particle trajectories integrated from ADCP bin "
        "east/north velocity, one path per bin."
    ),
    "speed_profile": (
        "Aquadopp current-speed profile: median and interquartile range of speed "
        "against height above bottom across the deployment."
    ),
    "analog": (
        "Full-record time series of analog channel variables containing "
        "non-zero, non-NaN data. One panel per channel."
    ),
    "ts_diagram": (
        "Left: scatter coloured by pressure. Middle: 2-D count heatmap. Right "
        "(when oxygen data present): scatter coloured by O₂ saturation (%). Bad "
        "(flag 4) and missing (flag 9) excluded; interpolated pressure (flag 8) "
        "retained."
    ),
    "roses": (
        "Direction the current flows toward (oceanographic convention, 0°=N). "
        "Speed coloured light→dark blue (slow→fast). QC-flagged samples "
        "excluded. Title shows serial number and height above bottom (m)."
    ),
    "tilt": (
        "|pitch| / |roll| / pressure-estimate tilt per Aquadopp, sharing a "
        "y-axis. Horizontal lines mark the suspect and fail thresholds from the "
        "file attributes."
    ),
    "spacing": (
        "Distribution of pressure differences between adjacent instrument pairs "
        "(pairs < 2 dbar apart excluded as co-located)."
    ),
    "clock_check": (
        "One temperature trace per instrument, zoomed to the first and last "
        "30 min of the deployment, to check clock alignment across instruments."
    ),
}


@dataclass(frozen=True)
class StackContext:
    """Pre-built figures and applies_to inputs for the stack page's panels.

    Parameters
    ----------
    figs : dict
        ``panel id -> base64 PNG`` (or ``None``) for every figure panel, built
        eagerly by :func:`generate_stack_page`.
    present_vars : set
        Data-variable names present in the stack dataset (for ``applies_to``).
    instr_types : set
        Lower-cased instrument types present (aquadopp / adcp / …).
    n_instr : int
        Number of instrument levels.
    has_analog : bool
        Whether any analog channels are present.
    history_entries : list
        Parsed processing-history entries.
    instr_rows : list
        Per-instrument summary rows for the deep-first instruments table.
    nc_meta : dict
        NetCDF metadata (dims/variables/scalars/globals) for the appendix.
    nc_file : str
        The stack NetCDF file's name.
    rose_note : str or None
        Magnetic-declination note for the current-roses section, if any.
    rose_warn : bool
        Whether an Aquadopp is missing its magnetic declination.
    rose_missing_serials : list
        Serials of Aquadopps missing declination (for the warning).

    """

    figs: dict
    present_vars: set
    instr_types: set
    n_instr: int
    has_analog: bool
    history_entries: list
    instr_rows: list
    nc_meta: dict
    nc_file: str
    rose_note: str | None
    rose_warn: bool
    rose_missing_serials: list


def _fig(panel_id: str) -> Callable[[StackContext], str | None]:
    """Return a render callable that yields the pre-built figure for *panel_id*."""
    return lambda c: c.figs.get(panel_id)


def _has(var: str) -> Callable[[StackContext], bool]:
    """Return an applies_to predicate: the named variable is present."""
    return lambda c: var in c.present_vars


def _has_stack_velocity(c: StackContext) -> bool:
    """Return True when eastward or northward velocity is present."""
    return "east_velocity" in c.present_vars or "north_velocity" in c.present_vars


def _has_aquadopp(c: StackContext) -> bool:
    """Return True when an Aquadopp instrument is present."""
    return "aquadopp" in c.instr_types


def _stack_history_unavailable(c: StackContext) -> str | None:
    """Reason the history panel cannot render, or None (provenance always applies)."""
    if c.history_entries:
        return None
    return "No processing history recorded in the file attributes."


def _figure_panel(
    pid: str,
    applies_to: Callable | None = None,
    slot: str | None = None,
) -> Panel:
    """Build a stack figure panel from the registry.

    The stack figures are pre-built by the ad-hoc closures in
    :func:`generate_stack_page`.  Most fill the content column (``slot=None`` →
    bare ``.fig``); the trajectory panels carry ``slot="half"`` so the row-layout
    section places them side-by-side.
    """
    kwargs: dict = {
        "render": _fig(pid),
        "caption": STACK_CAPTIONS[pid],
        "slot": slot,
    }
    if applies_to is not None:
        kwargs["applies_to"] = applies_to
    return Panel(pid, **kwargs)


#: Stack panel registry.  Figure panels return the pre-built b64 from the
#: context; html/table panels render sub-templates (shared where identical to
#: grid).  ``speed_profile`` is half-width, ``spacing`` a third; trajectories are
#: full-width and stacked (the old side-by-side flex is a later CSS enhancement).
STACK_PANELS: dict[str, Panel] = {
    "history": Panel(
        "history",
        render=lambda c: render_history(c.history_entries),
        kind="html",
        unavailable_if=_stack_history_unavailable,
    ),
    "instr_table": Panel(
        "instr_table",
        render=lambda c: render_template(
            "_stack_instr_table.html", instr_rows=c.instr_rows
        ),
        kind="table",
    ),
    "hydro_pts": _figure_panel(
        "hydro_pts",
        lambda c: any(
            v in c.present_vars for v in ("pressure", "temperature", "salinity")
        ),
    ),
    "dissolved_oxygen": _figure_panel("dissolved_oxygen", _has("dissolved_oxygen")),
    "vel_uvw": _figure_panel("vel_uvw", _has_stack_velocity),
    "turbidity": _figure_panel("turbidity", _has("turbidity")),
    "trajectories_aquadopp": _figure_panel(
        "trajectories_aquadopp", _has_aquadopp, slot="half"
    ),
    "trajectories_adcp": _figure_panel(
        "trajectories_adcp", lambda c: "adcp" in c.instr_types, slot="half"
    ),
    "speed_profile": Panel(
        "speed_profile",
        render=_fig("speed_profile"),
        slot="half",
        caption=STACK_CAPTIONS["speed_profile"],
        applies_to=_has_aquadopp,
    ),
    "analog": _figure_panel("analog", lambda c: c.has_analog),
    "ts_diagram": _figure_panel(
        "ts_diagram",
        lambda c: "temperature" in c.present_vars and "salinity" in c.present_vars,
    ),
    "roses_declination": Panel(
        "roses_declination",
        render=lambda c: render_template(
            "_stack_roses_note.html",
            note=c.rose_note,
            warn=c.rose_warn,
            missing=c.rose_missing_serials,
        ),
        kind="html",
        applies_to=lambda c: bool(c.rose_warn or c.rose_note),
    ),
    "roses": _figure_panel("roses", _has_stack_velocity),
    "tilt": _figure_panel("tilt", _has_aquadopp),
    "spacing": Panel(
        "spacing",
        render=_fig("spacing"),
        slot="third",
        caption=STACK_CAPTIONS["spacing"],
        applies_to=lambda c: c.n_instr > 1 and "pressure" in c.present_vars,
    ),
    "clock_check": _figure_panel("clock_check"),
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
        applies_to=lambda c: bool(c.nc_meta.get("global_groups")),
    ),
}

#: Stack sections (design §7 grouping — Hydrography and Velocity folded).
STACK_SECTIONS: dict[str, Section] = {
    "processing_history": Section(
        "processing_history", "Processing history", ("history",)
    ),
    "instruments": Section(
        "instruments",
        "Instruments (deep-first)",
        ("instr_table",),
        intro="HAB = height above bottom (m).",
    ),
    "hydrography": Section(
        "hydrography",
        "Hydrography",
        ("hydro_pts", "dissolved_oxygen"),
    ),
    "velocity": Section("velocity", "Velocity", ("vel_uvw",)),
    "turbidity": Section("turbidity", "Turbidity", ("turbidity",)),
    "trajectories": Section(
        "trajectories",
        "Particle trajectories",
        ("trajectories_aquadopp", "trajectories_adcp"),
        layout="row",
    ),
    "speed_profile": Section(
        "speed_profile", "Aquadopp speed profile", ("speed_profile",)
    ),
    "analog": Section("analog", "Analog channels", ("analog",)),
    "ts_diagram": Section("ts_diagram", "T-S diagram", ("ts_diagram",)),
    "roses": Section("roses", "Current rose diagrams", ("roses_declination", "roses")),
    "tilt": Section(
        "tilt", "Aquadopp tilt (|pitch| / |roll| / pressure estimate)", ("tilt",)
    ),
    "spacing": Section("spacing", "Adjacent instrument spacing", ("spacing",)),
    "clock_check": Section("clock_check", "Clock alignment check", ("clock_check",)),
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

#: Default stack profile.
STACK_DEFAULT = Profile(
    numbering="flat",
    entries=(
        STACK_SECTIONS["processing_history"],
        STACK_SECTIONS["instruments"],
        STACK_SECTIONS["hydrography"],
        STACK_SECTIONS["velocity"],
        STACK_SECTIONS["turbidity"],
        STACK_SECTIONS["trajectories"],
        STACK_SECTIONS["speed_profile"],
        STACK_SECTIONS["analog"],
        STACK_SECTIONS["ts_diagram"],
        STACK_SECTIONS["roses"],
        STACK_SECTIONS["tilt"],
        STACK_SECTIONS["spacing"],
        STACK_SECTIONS["clock_check"],
        STACK_SECTIONS["netcdf_variables"],
        STACK_SECTIONS["netcdf_attributes"],
    ),
)


def generate_stack_page(
    mooring_name: str,
    stack_path: Path,
    ctx: dict[str, Any],
    out_dir: Path,
    force: bool,
    display_root: Path,
    skip_existing: bool = False,
) -> None:
    """Generate the stack report HTML page: lay out the figures and tables; the figures are drawn in ``plotters``."""
    out_path = out_dir / f"{mooring_name}_stack_report.html"
    if _should_skip(out_path, force, skip_existing, stack_path):
        _status("skip", _safe_rel(out_path, display_root))
        return

    try:
        import xarray as xr

        ds = xr.open_dataset(stack_path).load()
        n_time = ds.sizes["time"]
        n_instr = ds.sizes["N_LEVELS"]
        dt_seconds = ds.attrs.get("dt_seconds", "?")
        waterdepth = float(ds.attrs.get("waterdepth", 0) or 0)
        stack_history = _parse_history(ds.attrs.get("history", ""))
        step = max(1, n_time // 5000)

        serials = ds["serial"].values
        instr_types = ds["instrument_type"].values
        habs = ds["hab"].values

        instr_rows = []
        for i in range(n_instr):
            depth = f"{waterdepth - habs[i]:.0f}" if waterdepth else "—"
            _ser = str(serials[i])
            instr_rows.append(
                {
                    "serial": _ser,
                    "instr_type": instr_types[i],
                    "hab": f"{habs[i]:.0f}",
                    "depth": depth,
                    "stage": "",
                    "report_href": _instrument_report_href(mooring_name, _ser),
                    "report_exists": _instrument_report_exists(
                        out_dir, mooring_name, _ser
                    ),
                }
            )

        _serial_list = list(serials)

        fig_hydro_pts_b64 = _make_stack_timeseries_b64(
            ds,
            [
                ("pressure", params.vlabel("pressure"), True, {"adcp"}),
                ("temperature", params.vlabel("temperature"), False, None),
                ("salinity", params.vlabel("salinity"), False, None),
            ],
            color_var="temperature",
        )
        fig_dissolved_oxygen_b64 = _make_stack_timeseries_b64(
            ds,
            [("dissolved_oxygen", params.vlabel("dissolved_oxygen"), False, None)],
            color_var="dissolved_oxygen",
        )
        fig_vel_uvw_b64 = _make_stack_timeseries_b64(
            ds,
            [
                (v, params.vlabel(v, prefix=f"{c} — "), False, None)
                for v, c in (
                    ("east_velocity", "U"),
                    ("north_velocity", "V"),
                    ("up_velocity", "W"),
                )
            ],
            color_var="east_velocity",
        )
        fig_turbidity_b64 = _make_stack_timeseries_b64(
            ds,
            [("turbidity", params.vlabel("turbidity"), False, None)],
            color_var="turbidity",
            dot_overlay=True,
        )

        fig_rose_grid_b64, _n_rose = _make_rose_grid_b64(ds, _serial_list)

        _decl_vals: list = []
        _decl_missing = False
        _decl_missing_serials: list = []
        if "magnetic_declination" in ds.data_vars:
            _dv = ds["magnetic_declination"].values
            # _dv may be 0-D (scalar) if mooring_level collapsed identical values
            _dv_flat = _dv.ravel()
            _decl_vals = sorted(
                {round(float(v), 2) for v in _dv_flat if np.isfinite(float(v))}
            )
            if "instrument_type" in ds:
                _aqd_mask = np.array(
                    [str(t).lower() == "aquadopp" for t in ds["instrument_type"].values]
                )
                if _dv.ndim == 0:
                    # Scalar → same value for all instruments
                    _decl_missing = bool(
                        _aqd_mask.any() and not np.isfinite(float(_dv))
                    )
                else:
                    _aqd_bad = _aqd_mask & ~np.isfinite(_dv)
                    _decl_missing = bool(_aqd_mask.any() and _aqd_bad.any())
                    if _decl_missing:
                        _svar = next(
                            (v for v in ("serial_number", "serial") if v in ds), None
                        )
                        if _svar is not None:
                            _svals = [str(s) for s in ds[_svar].values]
                            _decl_missing_serials = [
                                _svals[i] for i, bad in enumerate(_aqd_bad) if bad
                            ]
        elif "instrument_type" in ds:
            _aqd_mask = np.array(
                [str(t).lower() == "aquadopp" for t in ds["instrument_type"].values]
            )
            _decl_missing = bool(_aqd_mask.any())
        rose_declination_note = (
            f"Magnetic declination applied: {', '.join(f'{v:+.2f}°' for v in _decl_vals)}"
            if _decl_vals
            else None
        )
        rose_declination_warn = _decl_missing
        rose_declination_missing_serials = _decl_missing_serials

        fig_spacing_b64 = _make_instrument_spacing_b64(ds)

        # as_panel: a figure whose draw raises becomes a Stub naming the cause
        # (shown as a "warn" note) instead of silently vanishing.
        fig_ts_stack_b64 = as_panel(lambda: _make_stack_ts_diagram(ds))
        fig_aquadopp_tilt_b64 = as_panel(
            lambda: _make_aquadopp_tilt_panels(ds, step=step)
        )
        fig_trajectories_b64 = as_panel(lambda: _make_multi_aquadopp_trajectories(ds))
        fig_adcp_trajectories_b64 = as_panel(lambda: _make_adcp_trajectories_b64(ds))
        fig_speed_profile_b64 = as_panel(lambda: _make_aquadopp_speed_profile(ds))
        # Clock alignment check: one temperature trace per instrument zoomed to
        # first/last 30 min.  Build {serial: path} preferring stage3 over stage2.
        proc_dir = stack_path.parent
        _clock_nc_paths: dict[str, Path] = {}
        for i in range(n_instr):
            _s = str(serials[i])
            _itype = str(instr_types[i])
            _base = proc_dir / _itype / f"{mooring_name}_{_s}"
            _s3 = Path(str(_base) + paths.stage_suffix(3))
            _s2 = Path(str(_base) + paths.stage_suffix(2))
            if _s3.exists():
                _clock_nc_paths[_s] = _s3
            elif _s2.exists():
                _clock_nc_paths[_s] = _s2
        _deploy_dt = _parse_dt(ctx.get("deploy_time"))
        _recover_dt = _parse_dt(ctx.get("recover_time"))
        fig_clock_check_b64 = as_panel(
            lambda: _make_clock_check_b64(_clock_nc_paths, _deploy_dt, _recover_dt)
        )

        # Capture the applies_to inputs before closing the dataset.
        present_vars = set(ds.data_vars)
        instr_types_set = {str(t).lower() for t in instr_types}

        ds.close()

        nc_meta = _read_nc_metadata(stack_path)
        analog_vars = nc_meta.get("analog_vars", [])
        fig_analog_b64 = (
            as_panel(lambda: _make_analog_timeseries(stack_path, analog_vars))
            if analog_vars
            else None
        )
        grid_exists = paths.product_file(
            stack_path.parent, mooring_name, "grid"
        ).exists()

        report = resolve(
            STACK_DEFAULT,
            StackContext(
                figs={
                    "hydro_pts": fig_hydro_pts_b64,
                    "dissolved_oxygen": fig_dissolved_oxygen_b64,
                    "vel_uvw": fig_vel_uvw_b64,
                    "turbidity": fig_turbidity_b64,
                    "trajectories_aquadopp": fig_trajectories_b64,
                    "trajectories_adcp": fig_adcp_trajectories_b64,
                    "speed_profile": fig_speed_profile_b64,
                    "analog": fig_analog_b64,
                    "ts_diagram": fig_ts_stack_b64,
                    "roses": fig_rose_grid_b64,
                    "tilt": fig_aquadopp_tilt_b64,
                    "spacing": fig_spacing_b64,
                    "clock_check": fig_clock_check_b64,
                },
                present_vars=present_vars,
                instr_types=instr_types_set,
                n_instr=n_instr,
                has_analog=bool(analog_vars),
                history_entries=stack_history,
                instr_rows=instr_rows,
                nc_meta=nc_meta,
                nc_file=stack_path.name,
                rose_note=rose_declination_note,
                rose_warn=rose_declination_warn,
                rose_missing_serials=rose_declination_missing_serials,
            ),
            STACK_PANELS,
        )

        html = render_template(
            "stack.html",
            mooring_name=mooring_name,
            nav=mooring_nav(
                mooring_name,
                ctx.get("instruments", []),
                stack_exists=True,
                grid_exists=grid_exists,
                current="stack",
                array_href=_find_array_report_href(out_dir),
            ),
            report=report,
            cruise=ctx.get("cruise", "—"),
            ship=ctx.get("ship", "—"),
            deploy_time=ctx["deploy_time"],
            recover_time=ctx["recover_time"],
            duration=ctx.get("duration", "—"),
            waterdepth=ctx.get("waterdepth", "—"),
            n_instr=n_instr,
            dt_seconds=dt_seconds,
            n_time=n_time,
            grid_exists=grid_exists,
            latitude=ctx.get("latitude", "—"),
            longitude=ctx.get("longitude", "—"),
            nc_file=stack_path.name,
            generated=ctx["generated"],
        )
        out_path.write_text(html, encoding="utf-8")
        _status("file", _safe_rel(out_path, display_root))
    except Exception as exc:
        warnings.warn(f"stack report generation failed: {exc}", stacklevel=2)
