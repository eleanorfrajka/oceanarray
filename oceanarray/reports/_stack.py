"""Stack report HTML template, tilt panels helper, and page generator."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from oceanarray import logger, paths
from oceanarray.analysis.derived import derived_fields
from oceanarray.paths import should_skip_regeneration

from ..config import parameters as params
from ..pipeline import masking
from ..plotters.helpers import nominal_depths
from . import _captions, _html_helpers, _masthead, _qcflags
from ._breadcrumb import crumbs_from_ctx
from ._env import (
    render_nc_collapsed,
    render_nc_coords,
    render_nc_globals,
    render_nc_scalars,
    render_nc_variables,
    render_template,
)
from ._figdebug import as_panel
from ._html_helpers import (
    _instrument_report_exists,
    _instrument_report_href,
    _parse_dt,
    _resolve_array_report_href,
    description_links,
)
from ._navs import _type_label, mooring_nav
from ._plots import (
    _make_adcp_progressive_vector_b64,
    _make_analog_timeseries_b64,
    _make_aquadopp_speed_profile_b64,
    _make_aquadopp_tilt_panels_b64,
    _make_clock_check_b64,
    _make_flag_strip_b64,
    _make_instrument_spacing_b64,
    _make_multi_aquadopp_progressive_vector_b64,
    _make_rose_grid_b64,
    _make_stack_timeseries_b64,
    _make_stack_ts_diagram_b64,
)
from ._read import _read_nc_metadata
from ._vendored.page_model import Panel, Profile, Section, resolve

# ---------------------------------------------------------------------------
# Aquadopp tilt helper (was @staticmethod on MooringReport)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Page generator
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Section manifest — stack registry
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
    # The figure captions with a flag sentence are generated per page
    # (``_captions.stack_captions``); these are the fallbacks.
    "hydro_pts": (
        "Pressure, temperature and salinity from every instrument over the "
        "deployment, on a shared time axis. Each instrument keeps one colour and "
        "line style in all three panels, from the deepest (blue) to the shallowest "
        "(red)."
    ),
    "dissolved_oxygen": (
        "Dissolved oxygen from each instrument with an oxygen sensor "
        f"({params.vunit('dissolved_oxygen')})."
    ),
    "vel_uvw": (
        "Eastward, northward and upward velocity from each current meter over the "
        "deployment."
    ),
    "turbidity": "Turbidity from each instrument with a turbidity sensor.",
    "trajectories_aquadopp": (
        "Cumulative displacement from adding up each Aquadopp's measured velocity "
        "(a progressive vector diagram): how far water passing the instrument would "
        "travel if the flow were the same everywhere. It shows the mean flow "
        "direction and how steady it is, not the path of a water parcel. Coloured "
        "by temperature; the serial number marks each line's end."
    ),
    "trajectories_adcp": (
        "Cumulative displacement from adding up the measured velocity in each ADCP "
        "bin (a progressive vector diagram): how far water passing the bin would "
        "travel if the flow were the same everywhere. It shows the mean flow "
        "direction and how steady it is, not the path of a water parcel. Coloured "
        "by height above bottom."
    ),
    "speed_profile": (
        "Aquadopp current-speed profile: median and interquartile range of speed "
        "against height above bottom across the deployment."
    ),
    "analog": (
        "Full-record time series of analog channel variables containing "
        "non-zero, non-NaN data. One panel per channel."
    ),
    "ts_diagram": "Potential temperature against salinity (PSU) for all samples.",
    "steps": (
        "What was done to produce this combined record, entry by entry as the stack "
        "file's history attribute records it, with the settings read from the file."
    ),
    "roses": (
        "Direction toward which the current flows and speed for each current meter. "
        "Wedge length is the percentage of the record in that direction; colours "
        "are speed classes."
    ),
    "tilt": (
        "|pitch| / |roll| / pressure-estimate tilt per Aquadopp, sharing a "
        "y-axis. Horizontal lines mark the suspect and fail limits stored with the "
        "flags."
    ),
    "spacing": (
        "Vertical distance between neighbouring instruments, from their measured "
        "pressures. This sets how finely the gridded product can resolve the water "
        "column; pairs closer than 2 dbar count as one level."
    ),
    "clock_check": (
        "One temperature trace per instrument, zoomed to the first and last "
        "30 min of the deployment, to check clock alignment across instruments."
    ),
    "clock_imported": (
        "Not applicable: clocks were aligned by the originator, and these records "
        "start and end where the originator's did."
    ),
    "qc_counts": (
        "Share of each variable's record flagged by the originator and by oceanarray "
        "(its automatic tests), and which values the plots and the gridded "
        "product leave out. Variables with nothing flagged are not listed."
    ),
    "qc_strip": (
        "Flags through time for each instrument with anything flagged: the "
        "originator's above oceanarray's for each variable. A flag set the file "
        "does not have gets no bar."
    ),
    "instr_table": (
        "Deepest first. HAB: height above bottom. A serial links to the instrument's "
        "own page."
    ),
    "nc_file": _captions.netcdf_caption("stack file"),
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
    instr_rows : list
        Per-instrument summary rows for the deep-first instruments table.
    nc_meta : dict
        NetCDF metadata (dims/variables/scalars/globals) for the appendix.
    nc_file : str
        The stack NetCDF file's name.
    rose_note : str or None
        Magnetic-declination note for the current-roses section, if any.
    rose_unapplied : list of tuple of (str, str)
        Instruments whose east/north velocities neither oceanarray nor the
        originator rotated to true north, each with why (from
        :func:`declination_unapplied`); empty for none.
    stage2_serials : list
        Serials stacked from their stage-2 files (``stage2_instruments``).
    stage2_velocity : bool
        Whether any of them carries velocities (then without magnetic declination).
    stage2_current_meters : list
        Those of them that are current meters without east/north velocities
        (``stage2_no_velocity``: their velocities are in instrument coordinates).
    stage2_reason : str
        Why they were stacked from stage 2 (``stage2_reason``).
    stage2_magnetic_north : list
        Those of them with east/north velocities, which lack magnetic declination.
    qc_sentence : str
        The flag sentence of the Quality flags table, from the stack's rule.

    """

    figs: dict
    present_vars: set
    instr_types: set
    n_instr: int
    has_analog: bool
    instr_rows: list
    nc_meta: dict
    nc_file: str
    rose_note: str | None
    rose_unapplied: list
    stage2_serials: list = field(default_factory=list)
    stage2_velocity: bool = False
    stage2_current_meters: list = field(default_factory=list)
    stage2_magnetic_north: list = field(default_factory=list)
    stage2_reason: str = ""
    qc_rows: list = field(default_factory=list)
    qc_rule: str = ""
    qc_note: str | None = None  # None: not known (a Stack cat)
    qc_tables: list = field(default_factory=list)
    qc_unflagged: list = field(default_factory=list)
    qc_quiet: int = 0
    qc_sentence: str = ""
    all_imported: bool = False
    steps: list = field(default_factory=list)


#: The velocity figure's rows: variable and axis label.
_VELOCITY_ROWS: tuple[tuple[str, str], ...] = (
    ("east_velocity", "Eastward"),
    ("north_velocity", "Northward"),
    ("up_velocity", "Upward"),
)


def velocity_rows(ds: Any) -> list[tuple[str, str, bool, None]]:
    """Return the stack velocity figure's rows (``draw_stack_timeseries`` form)."""
    unit = params.vunit("east_velocity")
    return [(v, f"{w} ({unit})", False, None) for v, w in _VELOCITY_ROWS if v in ds]


def velocity_heights(ds: Any) -> list[float]:
    """Return the rows' heights: upward velocity in a shorter row (its spread is small)."""
    return [0.5 if v == "up_velocity" else 1.0 for v, *_ in velocity_rows(ds)]


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


def declination_unapplied(ds: Any) -> list[tuple[str, str]]:
    """Return the instruments whose east/north velocities nobody rotated to true north.

    A level is left out when oceanarray applied the magnetic declination (a finite
    ``magnetic_declination``), when the originator did (a non-empty
    ``originator_magnetic_declination``; a stack written before that variable
    falls back to ``originator_file``, as the readers record the originator's
    declination for every imported file with east/north velocities), or when it
    was stacked from stage 2 (the stage-2 note covers it).

    Parameters
    ----------
    ds : xarray.Dataset
        The stack, with ``N_LEVELS`` levels and ``(time, N_LEVELS)`` velocities.

    Returns
    -------
    list of tuple of (str, str)
        ``(instrument, reason)`` once per physical instrument (an ADCP's bins and
        head are one), e.g. ``("Aquadopp s/n 9920", "no mooring position at
        stage 3")``; empty when every level with velocity is in true north.
    """
    if "east_velocity" not in ds.data_vars or "N_LEVELS" not in ds.dims:
        return []
    n = ds.sizes["N_LEVELS"]
    east = ds["east_velocity"]
    has_vel = (
        np.isfinite(east.transpose(..., "N_LEVELS").values).reshape(-1, n).any(axis=0)
    )

    def per_level(name: str, fill: Any) -> list:
        if name not in ds.variables:
            return [fill] * n
        vals = np.asarray(ds[name].values)
        return [vals.item()] * n if vals.ndim == 0 else list(vals.ravel())

    decl = np.array(per_level("magnetic_declination", np.nan), dtype=float)
    if "originator_magnetic_declination" in ds.variables:
        by_originator = [
            bool(str(v)) for v in per_level("originator_magnetic_declination", "")
        ]
    else:
        by_originator = [bool(str(v)) for v in per_level("originator_file", "")]
    serials = [str(s) for s in per_level("serial", "")]
    types = [str(t) for t in per_level("instrument_type", "")]
    stage2 = set(str(ds.attrs.get("stage2_instruments", "")).split())
    unknown = str(
        ds["magnetic_declination"].attrs.get("comment", "")
        if "magnetic_declination" in ds.variables
        else ""
    )
    out: list[tuple[str, str]] = []
    for i in np.flatnonzero(has_vel):
        if np.isfinite(decl[i]) or by_originator[i]:
            continue
        if paths.safe_serial(serials[i]) in stage2 or serials[i] in stage2:
            continue
        base = paths.adcp_parent_serial(serials[i])
        if f"{types[i]} s/n {base}" in unknown:
            reason = "no mooring position at stage 3"
        else:
            reason = "its stage-3 file records no declination"
        entry = (f"{_type_label(types[i])} s/n {base}", reason)
        if entry not in out:
            out.append(entry)
    return out


def _figure_panel(
    pid: str,
    applies_to: Callable | None = None,
    slot: str | None = None,
) -> Panel:
    """Build a stack figure panel from the registry."""
    # slot=None fills the content column; trajectory panels pass "half" so the
    # row-layout section places them side by side.
    kwargs: dict = {
        "render": _fig(pid),
        "caption": STACK_CAPTIONS[pid],
        "slot": slot,
        # An applicable figure that draws nothing says why, never a bare stub.
        "unavailable_if": lambda c: (
            None
            if c.figs.get(pid) is not None
            else "No values in this file to draw this figure from."
        ),
    }
    if applies_to is not None:
        kwargs["applies_to"] = applies_to
    return Panel(pid, **kwargs)


#: Stack panel registry.  Figure panels return the pre-built b64 from the
#: context; html/table panels render sub-templates (shared where identical to
#: grid).  ``speed_profile`` is half-width, ``spacing`` a third; trajectories are
#: full-width and stacked (the old side-by-side flex is a later CSS enhancement).
STACK_PANELS: dict[str, Panel] = {
    "steps": Panel(
        "steps",
        render=lambda c: render_template("_history_steps.html", rows=c.steps),
        kind="html",
        caption=STACK_CAPTIONS["steps"],
        unavailable_if=lambda c: None if c.steps else _html_helpers.NO_HISTORY,
    ),
    "stage2_note": Panel(
        "stage2_note",
        render=lambda c: render_template(
            "_quicklook_note.html",
            serials=c.stage2_serials,
            velocity=c.stage2_velocity,
            current_meters=c.stage2_current_meters,
            reason=c.stage2_reason,
        ),
        kind="html",
        applies_to=lambda c: bool(c.stage2_serials),
    ),
    "stage2_velocity_note": Panel(
        "stage2_velocity_note",
        render=lambda c: render_template(
            "_magnetic_north_note.html", serials=c.stage2_magnetic_north
        ),
        kind="html",
        applies_to=lambda c: bool(c.stage2_magnetic_north),
    ),
    "instr_table": Panel(
        "instr_table",
        render=lambda c: render_template(
            "_stack_instr_table.html", instr_rows=c.instr_rows
        ),
        kind="table",
        caption=STACK_CAPTIONS["instr_table"],
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
            unapplied=c.rose_unapplied,
        ),
        kind="html",
        applies_to=lambda c: bool(c.rose_unapplied or c.rose_note),
    ),
    "roses": _figure_panel("roses", _has_stack_velocity),
    "tilt": _figure_panel("tilt", _has_aquadopp),
    "spacing": Panel(
        "spacing",
        render=lambda c: render_template(
            "_collapsed_figure.html",
            summary="Spacing of neighbouring instruments",
            caption=STACK_CAPTIONS["spacing"],
            images=[c.figs["spacing"]]
            if isinstance(c.figs.get("spacing"), str)
            else [],
            slot="third",
        ),
        kind="html",
        applies_to=lambda c: (
            c.n_instr > 1
            and "pressure" in c.present_vars
            and isinstance(c.figs.get("spacing"), str)
        ),
    ),
    "clock_check": _figure_panel(
        "clock_check",
        # A figure only when an instrument was processed from its own file.
        lambda c: not c.all_imported,
    ),
    "clock_imported": Panel(
        "clock_imported",
        render=lambda _c: f'<p class="one-line">{STACK_CAPTIONS["clock_imported"]}</p>',
        kind="html",
        applies_to=lambda c: c.all_imported,
    ),
    "qc_counts": Panel(
        "qc_counts",
        render=lambda c: render_template(
            "_qc_counts.html",
            rows=c.qc_rows,
            rule=c.qc_rule,
            note=c.qc_note,
            unflagged=c.qc_unflagged,
            quiet=c.qc_quiet,
            sentence=c.qc_sentence,
            good_sentence=_qcflags.good_sentence(),
        ),
        kind="table",
        applies_to=lambda c: bool(c.qc_rule),
    ),
    "qc_strip": _figure_panel("qc_strip", lambda c: bool(c.qc_rule and c.qc_rows)),
    "nc_file": Panel(
        "nc_file",
        render=lambda c: render_nc_collapsed(c.nc_meta),
        kind="html",
        caption=STACK_CAPTIONS["nc_file"],
    ),
    "qc_translation": Panel(
        "qc_translation",
        render=lambda c: render_template("_qc_translation.html", tables=c.qc_tables),
        kind="table",
        applies_to=lambda c: bool(c.qc_tables),
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
        applies_to=lambda c: bool(c.nc_meta.get("global_groups")),
    ),
}

#: Stack sections (Hydrography and Velocity folded).
STACK_SECTIONS: dict[str, Section] = {
    "processing_history": Section(
        "processing_history", "Processing history", ("steps", "spacing")
    ),
    "instruments": Section(
        "instruments",
        "Instruments",
        ("stage2_note", "instr_table"),
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
        "Progressive vector diagram",
        ("trajectories_aquadopp", "trajectories_adcp"),
        layout="row",
    ),
    "speed_profile": Section(
        "speed_profile", "Aquadopp speed profile", ("speed_profile",)
    ),
    "analog": Section("analog", "Analog channels", ("analog",)),
    "ts_diagram": Section("ts_diagram", "T-S diagram", ("ts_diagram",)),
    "roses": Section(
        "roses",
        "Current rose diagrams",
        ("roses_declination", "stage2_velocity_note", "roses"),
    ),
    "tilt": Section(
        "tilt", "Aquadopp tilt (|pitch| / |roll| / pressure estimate)", ("tilt",)
    ),
    "clock_check": Section(
        "clock_check", "Clock alignment check", ("clock_check", "clock_imported")
    ),
    "quality_flags": Section(
        "quality_flags",
        "Quality flags",
        ("qc_counts", "qc_strip", "qc_translation"),
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
    "netcdf": Section("netcdf", "NetCDF file", ("nc_file",), role="appendix"),
}

#: Default stack profile.
STACK_DEFAULT = Profile(
    numbering="flat",
    entries=(
        STACK_SECTIONS["hydrography"],
        STACK_SECTIONS["velocity"],
        STACK_SECTIONS["turbidity"],
        STACK_SECTIONS["trajectories"],
        STACK_SECTIONS["speed_profile"],
        STACK_SECTIONS["analog"],
        STACK_SECTIONS["ts_diagram"],
        STACK_SECTIONS["roses"],
        STACK_SECTIONS["processing_history"],
        STACK_SECTIONS["instruments"],
        STACK_SECTIONS["tilt"],
        STACK_SECTIONS["clock_check"],
        STACK_SECTIONS["quality_flags"],
        STACK_SECTIONS["netcdf"],
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
    if should_skip_regeneration(out_path, force, skip_existing, stack_path):
        logger.status("skip", logger.format_rel_path(out_path, display_root))
        return

    try:
        import xarray as xr

        ds = xr.open_dataset(stack_path).load()
        # Quantities the figures derive (position, θ, speed, direction), once.
        derived = derived_fields(ds)
        n_time = ds.sizes["time"]
        n_instr = ds.sizes["N_LEVELS"]
        dt_seconds = ds.attrs.get("dt_seconds", "?")
        waterdepth = float(ds.attrs.get("waterdepth", 0) or 0)
        step = max(1, n_time // 5000)

        serials = ds["serial"].values
        instr_types = ds["instrument_type"].values
        habs = ds["hab"].values

        instr_rows = []
        nominal = nominal_depths(ds)
        for i in range(n_instr):
            depth = (
                float(nominal[i])
                if nominal is not None and np.isfinite(nominal[i])
                else (waterdepth - float(habs[i]) if waterdepth else None)
            )
            _ser = str(serials[i])
            instr_rows.append(
                {
                    "serial": _ser,
                    "instr_type": instr_types[i],
                    "type_label": _type_label(str(instr_types[i]).lower()),
                    "hab": float(habs[i]) if np.isfinite(habs[i]) else None,
                    "depth": depth,
                    "stage": "",
                    "report_href": _instrument_report_href(mooring_name, _ser),
                    "report_exists": _instrument_report_exists(
                        out_dir, mooring_name, _ser
                    ),
                }
            )

        _as_serial_list = list(serials)

        # One colour and line style per instrument on every time-series figure:
        # the hydrography's (temperature's line colormap, deep-first).
        records: dict[str, dict] = {"hydro_pts": {}, "vel_uvw": {}, "ts_diagram": {}}
        fig_hydro_pts_b64 = _make_stack_timeseries_b64(
            ds,
            [
                ("pressure", params.vlabel("pressure"), True, {"adcp"}),
                ("temperature", params.vlabel("temperature"), False, None),
                ("salinity", params.vlabel("salinity"), False, None),
            ],
            color_var="temperature",
            label_lines=True,
            mark_suspect=True,
            record=records["hydro_pts"],
        )
        fig_dissolved_oxygen_b64 = _make_stack_timeseries_b64(
            ds,
            [("dissolved_oxygen", params.vlabel("dissolved_oxygen"), False, None)],
            color_var="temperature",
        )
        fig_vel_uvw_b64 = _make_stack_timeseries_b64(
            ds,
            velocity_rows(ds),
            color_var="temperature",
            row_heights=velocity_heights(ds),
            mark_suspect=True,
            record=records["vel_uvw"],
            keyed=records["hydro_pts"].get("labelled", []),
        )
        fig_turbidity_b64 = _make_stack_timeseries_b64(
            ds,
            [("turbidity", params.vlabel("turbidity"), False, None)],
            color_var="temperature",
            dot_overlay=True,
        )

        fig_rose_grid_b64, _n_rose = _make_rose_grid_b64(ds, _as_serial_list)

        _decl_vals: list = []
        if "magnetic_declination" in ds.data_vars:
            # 0-D when the stack collapsed one value shared by every level.
            _decl_vals = sorted(
                {
                    round(float(v), 2)
                    for v in ds["magnetic_declination"].values.ravel()
                    if np.isfinite(float(v))
                }
            )
        rose_declination_note = (
            f"Magnetic declination applied: {', '.join(f'{v:+.2f}°' for v in _decl_vals)}"
            if _decl_vals
            else None
        )
        # Instruments stacked from stage 2 lack declination for that reason, not
        # for a missing position: the stage-2 note says so instead.
        stage2_serials = ds.attrs.get("stage2_instruments", "").split()
        stage2_velocity = bool(ds.attrs.get("stage2_magnetic_north"))
        stage2_current_meters = ds.attrs.get("stage2_no_velocity", "").split()
        rose_declination_unapplied = declination_unapplied(ds)

        fig_spacing_b64 = _make_instrument_spacing_b64(ds)

        # as_panel: a figure whose draw raises becomes a Stub naming the cause
        # (shown as a "warn" note) instead of silently vanishing.
        fig_ts_stack_b64 = as_panel(
            lambda: _make_stack_ts_diagram_b64(
                ds, derived, record=records["ts_diagram"]
            )
        )
        fig_aquadopp_tilt_b64 = as_panel(
            lambda: _make_aquadopp_tilt_panels_b64(ds, step=step)
        )
        fig_trajectories_b64 = as_panel(
            lambda: _make_multi_aquadopp_progressive_vector_b64(ds)
        )
        fig_adcp_trajectories_b64 = as_panel(
            lambda: _make_adcp_progressive_vector_b64(ds)
        )
        fig_speed_profile_b64 = as_panel(lambda: _make_aquadopp_speed_profile_b64(ds))
        # Clock alignment check: one temperature trace per instrument zoomed to
        # first/last 30 min.  Build {serial: path} preferring stage3 over stage2.
        proc_dir = stack_path.parent
        _clock_nc_paths: dict[str, Path] = {}
        # An imported instrument's clock was aligned by its originator, and its
        # file starts and ends where theirs did: it has no place in the check.
        imported = (
            [bool(str(f)) for f in ds["originator_file"].values]
            if "originator_file" in ds.variables
            else [False] * n_instr
        )
        for i in range(n_instr):
            if imported[i]:
                continue
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
        fig_clock_check_b64 = (
            as_panel(
                lambda: _make_clock_check_b64(_clock_nc_paths, _deploy_dt, _recover_dt)
            )
            if not all(imported)
            else None
        )

        # The Quality flags section, from the file alone.
        qc_ruled = masking.STACK_ATTR in ds.attrs
        qc_rows, qc_unflagged, qc_quiet = (
            _qcflags.count_rows(ds) if qc_ruled else ([], [], 0)
        )
        qc_tables = _qcflags.translation_tables(ds) if qc_ruled else []
        fig_qc_strip_b64 = (
            as_panel(lambda: _make_flag_strip_b64(ds, _qcflags.instrument_labels(ds)))
            if qc_ruled
            else None
        )
        qc_rule = (
            masking.rule_words(masking.from_attrs(ds.attrs), table=True)
            if qc_ruled
            else ""
        )
        steps = _captions.stack_history_rows(ds)
        rose_caption = _captions.stack_rose_caption(ds)
        captions = _captions.stack_captions(ds, records)

        # Capture the applies_to inputs before closing the dataset.
        present_vars = set(ds.data_vars)
        instr_types_set = {str(t).lower() for t in instr_types}

        ds.close()

        nc_meta = _read_nc_metadata(stack_path)
        analog_vars = nc_meta.get("analog_vars", [])
        fig_analog_b64 = (
            as_panel(lambda: _make_analog_timeseries_b64(stack_path, analog_vars))
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
                    "qc_strip": fig_qc_strip_b64,
                },
                qc_rows=qc_rows,
                qc_rule=qc_rule,
                qc_note=ctx.get("qc_threshold_note", ""),
                qc_tables=qc_tables,
                qc_unflagged=qc_unflagged,
                qc_quiet=qc_quiet,
                qc_sentence=_captions.stack_rule_sentence(
                    ds, list(masking.FLAGGED_VARIABLES)
                )
                if qc_ruled
                else "",
                present_vars=present_vars,
                instr_types=instr_types_set,
                n_instr=n_instr,
                all_imported=all(imported),
                steps=steps,
                has_analog=bool(analog_vars),
                instr_rows=instr_rows,
                nc_meta=nc_meta,
                nc_file=stack_path.name,
                rose_note=rose_declination_note,
                rose_unapplied=rose_declination_unapplied,
                stage2_serials=stage2_serials,
                stage2_velocity=stage2_velocity,
                stage2_current_meters=stage2_current_meters,
                stage2_reason=str(ds.attrs.get("stage2_reason", "")),
                stage2_magnetic_north=ds.attrs.get("stage2_magnetic_north", "").split(),
            ),
            {
                **STACK_PANELS,
                **{
                    pid: replace(STACK_PANELS[pid], caption=text)
                    for pid, text in captions.items()
                    if pid in STACK_PANELS
                },
                "roses": replace(STACK_PANELS["roses"], caption=rose_caption),
            },
        )

        html = render_template(
            "stack.html",
            mooring_name=mooring_name,
            breadcrumb=crumbs_from_ctx(
                ctx, [(mooring_name, f"{mooring_name}_report.html"), ("Stack", None)]
            ),
            nav=mooring_nav(
                mooring_name,
                ctx.get("instruments", []),
                stack_exists=True,
                grid_exists=grid_exists,
                current="stack",
                array_href=_resolve_array_report_href(out_dir),
            ),
            report=report,
            description_links=description_links(ctx),
            cruise=ctx.get("cruise", "—"),
            meta_facts=_masthead.masthead_facts(
                "stack",
                ctx.get("masthead_common", []),
                {
                    _masthead.DT_S: dt_seconds,
                    _masthead.N_TIME: n_time,
                    _masthead.NC_FILE: stack_path.name,
                },
            ),
            grid_exists=grid_exists,
            generated=ctx["generated"],
        )
        out_path.write_text(html, encoding="utf-8")
        logger.status("file", logger.format_rel_path(out_path, display_root))
    except Exception as exc:
        warnings.warn(f"stack report generation failed: {exc}", stacklevel=2)
