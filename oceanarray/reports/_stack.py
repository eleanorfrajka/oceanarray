"""Stack report HTML template, tilt panels helper, and page generator."""

from __future__ import annotations

import contextlib
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from oceanarray import paths

from .. import parameters as params
from ..config.unit_tables import var_unit_label, warn_if_not_canonical
from ..plotters.helpers import grid_despine, ordered_line_colors
from ..plotters.primitives import date_offset_left, plot_title
from . import _figdebug
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
    _fig_to_base64,
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
    _make_clock_check_b64,
    _make_multi_aquadopp_trajectories,
    _make_rose_grid_b64,
    _make_stack_ts_diagram,
    render_b64,
)
from ._slots import render as render_slot
from ._vendored import design_tokens as report_tokens
from ._vendored.figure_png import record_source, source_for
from ._vendored.page_model import Panel, Profile, Section, resolve

# ---------------------------------------------------------------------------
# Aquadopp tilt helper (was @staticmethod on MooringReport)
# ---------------------------------------------------------------------------


def _make_aquadopp_tilt_panels(ds: Any, step: int = 1) -> str | None:
    """One subplot per Aquadopp showing pitch, roll, and tilt_from_pressure.

    Within each row the time-series panel and its paired scatter panel share one
    0–90° y-axis, so the pitch/roll time series and the pressure-tilt scatter read
    on the same scale.  Gridlines are drawn on both panels.  Horizontal reference
    lines are drawn at the suspect and fail thresholds read from ds.attrs (falling
    back to 20° / 30° if absent).
    Returns None if no Aquadopp levels are found or none of the relevant
    variables exist.
    """
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    tilt_suspect = float(ds.attrs.get("tilt_suspect_threshold", 20.0))
    tilt_fail = float(ds.attrs.get("tilt_fail_threshold", 30.0))

    instr_types = ds["instrument_type"].values
    serials = ds["serial"].values
    habs = ds["hab"].values
    ref_habs = (
        ds["tilt_pressure_ref_hab"].values
        if "tilt_pressure_ref_hab" in ds.data_vars
        else None
    )
    ref_serials = (
        ds["tilt_pressure_ref_serial"].values
        if "tilt_pressure_ref_serial" in ds.data_vars
        else None
    )

    aq_indices = [i for i, t in enumerate(instr_types) if str(t).lower() == "aquadopp"]
    if not aq_indices:
        return None

    # Cap the number of stacked panels so the figure stays a sane height for PDF
    # pagination (one panel is ~2.24 in; 5 → ~11 in).  Deep-first order is kept, so
    # the deepest Aquadopps are shown; a note flags any that were dropped.
    _MAX_TILT_ROWS = 5
    _n_dropped = max(0, len(aq_indices) - _MAX_TILT_ROWS)
    aq_indices = aq_indices[:_MAX_TILT_ROWS]

    has_pitch = "pitch" in ds.data_vars
    has_roll = "roll" in ds.data_vars
    has_tilt_p = "tilt_from_pressure" in ds.data_vars
    if not has_pitch and not has_roll and not has_tilt_p:
        return None

    # The angle unit for every axis on this figure, read from whichever stored angle
    # variable is present (pitch/roll/tilt share it) so the label tracks the data: a
    # file written in radians would relabel, not silently claim degrees.
    _angle_var = (
        "pitch" if has_pitch else ("roll" if has_roll else "tilt_from_pressure")
    )
    deg_u = var_unit_label(ds[_angle_var])

    time_ds = ds["time"].values[::step]
    n_panels = len(aq_indices)

    def _draw() -> plt.Figure:
        fig = plt.figure(
            figsize=(report_tokens.W_FULL, 2.24 * n_panels), constrained_layout=True
        )
        # Tighten the inter-row gap; upper rows drop their x tick labels/label so
        # the rows can pack closer.
        fig.get_layout_engine().set(h_pad=0.03, hspace=0.0)
        gs = fig.add_gridspec(n_panels, 3, width_ratios=[2, 2, 1])

        ax_ts_first = None
        for row, i in enumerate(aq_indices):
            serial = serials[i]
            hab = habs[i]

            ax_ts = fig.add_subplot(gs[row, :2], sharex=ax_ts_first)
            if ax_ts_first is None:
                ax_ts_first = ax_ts
            # Scatter shares this row's y-axis so pitch/roll read on the same
            # 0–90° scale as the time series.
            ax_sc = fig.add_subplot(gs[row, 2], sharey=ax_ts)

            p_data = r_data = tp_data = None
            if has_pitch:
                p_data = np.abs(ds["pitch"].values[::step, i].astype(float))
                if np.any(np.isfinite(p_data)):
                    ax_ts.plot(
                        time_ds, p_data, lw=0.7, color="#2980b9", label="|pitch|"
                    )
            if has_roll:
                r_data = np.abs(ds["roll"].values[::step, i].astype(float))
                if np.any(np.isfinite(r_data)):
                    ax_ts.plot(time_ds, r_data, lw=0.7, color="#27ae60", label="|roll|")
            if has_tilt_p:
                tp_data = ds["tilt_from_pressure"].values[::step, i].astype(float)
                if np.any(np.isfinite(tp_data)):
                    ax_ts.plot(
                        time_ds,
                        tp_data,
                        lw=0.9,
                        color="#e67e22",
                        ls="--",
                        label="tilt (pressure)",
                    )

            ax_ts.axhline(tilt_suspect, color="tab:orange", lw=0.8, ls="--", zorder=0)
            ax_ts.axhline(tilt_fail, color="tab:red", lw=0.8, ls=":", zorder=0)
            ax_ts.set_ylim(0.0, 90.0)
            ax_ts.set_ylabel(f"Degrees ({deg_u})" if deg_u else "Degrees")
            grid_despine(ax_ts)

            _ref_note = ""
            if ref_habs is not None and np.isfinite(ref_habs[i]):
                _ref_s = str(ref_serials[i]) if ref_serials is not None else "?"
                _ref_note = f"  [ref: s/n {_ref_s} @ {ref_habs[i]:.0f} m]"
            ax_ts.set_title(f"s/n {serial}  ({hab:.0f} m hab){_ref_note}")
            if ax_ts.get_legend_handles_labels()[0]:
                # Legend inside the time-series panel (was anchored outside at
                # 1.01, which landed over the neighbouring scatter panel).
                ax_ts.legend(loc="best", framealpha=0.8)

            if row < n_panels - 1:
                ax_ts.tick_params(labelbottom=False)
            else:
                loc = mdates.AutoDateLocator()
                ax_ts.xaxis.set_major_locator(loc)
                ax_ts.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
                date_offset_left(ax_ts)
                ax_ts.tick_params(axis="x")

            if tp_data is not None and np.any(np.isfinite(tp_data)):
                sc_kw = {"s": 3, "alpha": 0.25, "rasterized": True, "linewidths": 0}
                if p_data is not None and np.any(np.isfinite(p_data)):
                    fin = np.isfinite(tp_data) & np.isfinite(p_data)
                    ax_sc.scatter(
                        tp_data[fin],
                        p_data[fin],
                        color="#2980b9",
                        label="|pitch|",
                        **sc_kw,
                    )
                if r_data is not None and np.any(np.isfinite(r_data)):
                    fin = np.isfinite(tp_data) & np.isfinite(r_data)
                    ax_sc.scatter(
                        tp_data[fin],
                        r_data[fin],
                        color="#27ae60",
                        label="|roll|",
                        **sc_kw,
                    )
                ax_sc.plot(
                    [0, 90],
                    [0, 90],
                    color="0.4",
                    lw=0.8,
                    ls="--",
                    label="1:1",
                    zorder=2,
                )
                ax_sc.axvline(
                    tilt_suspect, color="tab:orange", lw=0.7, ls="--", zorder=0
                )
                ax_sc.axvline(tilt_fail, color="tab:red", lw=0.7, ls=":", zorder=0)
                ax_sc.axhline(
                    tilt_suspect, color="tab:orange", lw=0.7, ls="--", zorder=0
                )
                ax_sc.axhline(tilt_fail, color="tab:red", lw=0.7, ls=":", zorder=0)
                ax_sc.set_xlim(0.0, 90.0)
                ax_sc.set_ylim(0.0, 90.0)  # re-assert after scatter autoscale
                # Match x/y ticks (both are 0–90° tilt); the shared y also sets the
                # time-series panel's y-ticks.
                ax_sc.set_xticks([0, 30, 60, 90])
                ax_sc.set_yticks([0, 30, 60, 90])
                ax_sc.set_ylabel(
                    f"|pitch|, |roll| [{deg_u}]" if deg_u else "|pitch|, |roll|"
                )
                if row == n_panels - 1:
                    ax_sc.set_xlabel(
                        f"tilt (pressure) [{deg_u}]" if deg_u else "tilt (pressure)"
                    )
                else:
                    ax_sc.tick_params(labelbottom=False)
                grid_despine(ax_sc)
                # No scatter legend — the time-series panel legend already names
                # pitch/roll; a second legend here is a redundant repeat.
            else:
                ax_sc.text(
                    0.5,
                    0.5,
                    "no tilt data",
                    transform=ax_sc.transAxes,
                    ha="center",
                    va="center",
                    color="gray",
                )
                ax_sc.set_axis_off()

        if _n_dropped:
            # No explicit y: constrained_layout reserves space for the suptitle
            # above the panels, so it no longer overprints the top panel's title.
            fig.suptitle(
                f"Showing {_MAX_TILT_ROWS} deepest Aquadopps "
                f"({_n_dropped} more not shown)",
                fontsize=report_tokens.ANNOT_FS,
            )
        return fig

    return render_b64(_draw, optional=True)


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
        "panels; the single legend spans the union of instruments. Values with "
        "QC flag ≥ 3 (suspect/bad) masked to NaN; ADCP excluded from pressure."
    ),
    "dissolved_oxygen": (
        "One line per instrument with dissolved oxygen data (SBE ODO sensor); "
        f"QC flags ≥ 3 masked. Units: {params.vunit('dissolved_oxygen')}. "
        "% saturation available in per-instrument reports."
    ),
    "vel_uvw": (
        "Eastward (U), northward (V) and upward (W) velocity in the ENU frame — "
        "one row each on a shared time axis, one colour per instrument (shared "
        "across rows, ordered by height) with a single legend over the union of "
        "instruments. Values with velocity_flag ≥ 3 masked to NaN; instruments "
        "without velocity data omitted."
    ),
    "turbidity": (
        "One line per instrument with turbidity data; QC flags ≥ 3 masked. Dots "
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
    """Generate a stack report HTML page with pressure and T time series."""
    out_path = out_dir / f"{mooring_name}_stack_report.html"
    if _should_skip(out_path, force, skip_existing, stack_path):
        _status("skip", _safe_rel(out_path, display_root))
        return

    try:
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        import xarray as xr

        ds = xr.open_dataset(stack_path).load()
        n_time = ds.sizes["time"]
        n_instr = ds.sizes["N_LEVELS"]
        dt_seconds = ds.attrs.get("dt_seconds", "?")
        waterdepth = float(ds.attrs.get("waterdepth", 0) or 0)
        stack_history = _parse_history(ds.attrs.get("history", ""))
        _t_cov_start = ds.attrs.get("time_coverage_start")
        _t_cov_end = ds.attrs.get("time_coverage_end")

        step = max(1, n_time // 5000)
        time_ds = ds["time"].values[::step]

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

        def _var_line_styling(varname: str) -> tuple[dict, dict]:
            """Per-serial (colour, linestyle) for one variable, deep-first ordered.

            Instruments are ordered deep-first and mapped onto the variable's
            **line** colormap (:data:`params.LINE_CMAPS_BY_VARIABLE`, falling back
            to the field map then viridis) so, e.g., temperature runs from the
            cold/deep (blue) end to the warm/shallow (red) end and pressure from
            dark to lighter blue.  Each colour is shared by a consecutive **pair**
            of instruments distinguished by linestyle (solid then dashed), which
            halves the number of distinct colours so the ramp stays legible for
            many instruments.  Washed-out colours are skipped by luminance (see
            :func:`ordered_line_colors`) rather than a fixed trim, so a diverging
            map's pale midpoint does not swallow the mid-depth lines.
            """
            _cmap_name = (
                params.LINE_CMAPS_BY_VARIABLE.get(varname)
                or params.CMAPS_BY_VARIABLE.get(varname)
                or "viridis"
            )
            _styles_ladder = ["-", "--"]
            _n_pairs = max(1, (len(_serial_list) + 1) // 2)  # 2 linestyles per colour
            _pair_colors = ordered_line_colors(_cmap_name, _n_pairs)
            colors, styles = {}, {}
            for _i, _s in enumerate(_serial_list):
                colors[_s] = _pair_colors[_i // len(_styles_ladder)]
                styles[_s] = _styles_ladder[_i % len(_styles_ladder)]
            return colors, styles

        def _ts_fig(
            varname: str,
            ylabel: str,
            invert: bool = False,
            hlines: list[tuple] | None = None,
            exclude_types: set | None = None,
            dot_overlay: bool = False,
        ) -> str | None:
            if varname not in ds.data_vars:
                return None
            arr = ds[varname].values.copy()
            qc_varname = f"{varname}_qc"
            if qc_varname in ds.data_vars:
                qc = ds[qc_varname].values
                arr[np.isin(qc, params.QC_BAD_FLAGS)] = np.nan
            _serial_colors, _serial_styles = _var_line_styling(varname)
            with plt.style.context(str(params.MPLSTYLE)):
                fig, ax = plt.subplots(figsize=(report_tokens.W_FULL, 2.56))
                plotted = False
                for i in range(n_instr):
                    if exclude_types and instr_types[i].lower() in exclude_types:
                        continue
                    serial = _serial_list[i]
                    color = _serial_colors[serial]
                    style = _serial_styles[serial]
                    y = arr[::step, i]
                    if not np.any(np.isfinite(y)):
                        continue
                    plotted = True
                    ax.plot(
                        time_ds,
                        y,
                        color=color,
                        ls=style,
                        lw=0.7,
                        alpha=0.85,
                        label=f"{serial}",
                    )
                    if dot_overlay:
                        ax.plot(
                            time_ds,
                            y,
                            ".",
                            color=color,
                            markersize=2,
                            linewidth=0,
                            alpha=0.85,
                        )
                if not plotted:
                    plt.close(fig)
                    return None
                if hlines:
                    for val, col, ls, lbl in hlines:
                        ax.axhline(val, color=col, lw=0.9, ls=ls, label=lbl, zorder=0)
                if invert:
                    ax.invert_yaxis()
                locator = mdates.AutoDateLocator()
                ax.xaxis.set_major_locator(locator)
                ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
                date_offset_left(ax)
                ax.set_ylabel(ylabel)
                ax.set_xlabel("Time")
                grid_despine(ax)
                if _t_cov_start and _t_cov_end:
                    with contextlib.suppress(Exception):
                        ax.set_xlim(
                            np.datetime64(_t_cov_start), np.datetime64(_t_cov_end)
                        )
                n_plotted = sum(
                    1 for i in range(n_instr) if np.any(np.isfinite(arr[::step, i]))
                )
                ax.legend(
                    loc="upper left",
                    bbox_to_anchor=(1.01, 1.0),
                    borderaxespad=0,
                    framealpha=0.8,
                    ncol=1,
                )
                # No tight_layout: it shrinks the axes to fit a tall outside
                # legend within the figsize.  _fig_to_base64 saves with
                # bbox_inches="tight", so the PNG expands to include the legend
                # while the plot keeps its full height.
                b64 = _fig_to_base64(fig)
                _figdebug.record(b64, f"_ts_fig[{varname}]", fig)
                record_source(b64, source_for(_ts_fig))
                plt.close(fig)
                return b64

        def _combined_ts_fig(_rows: list[tuple], _color_var: str) -> str | None:
            """Stacked multi-variable time series with one shared instrument legend.

            Each entry in *_rows* is ``(varname, ylabel, invert, exclude_types)``.
            The rows share one time axis and one instrument colour scheme (that of
            *_color_var*, height-ordered), so a colour tracks an instrument through
            every panel, and a single legend spans the union of instruments over
            the full figure height.  Used for the P/T/S and U/V/W groups so their
            many instruments share one tall legend instead of one per variable.
            """
            _rows = [r for r in _rows if r[0] in ds.data_vars]
            if not _rows:
                return None
            # One colour/linestyle per instrument, shared across rows (colour
            # tracks an instrument through every panel).
            _s_colors, _s_styles = _var_line_styling(_color_var)
            with plt.style.context(str(params.MPLSTYLE)):
                fig, _axs = plt.subplots(
                    len(_rows),
                    1,
                    figsize=(report_tokens.W_FULL, 2.56 * len(_rows)),
                    sharex=True,
                    squeeze=False,
                )
                _handles: dict = {}  # serial -> Line2D (union, height order)
                for _ax, (_var, _ylabel, _invert, _excl) in zip(
                    _axs[:, 0], _rows, strict=False
                ):
                    _a = ds[_var].values.copy()
                    _qcv = f"{_var}_qc"
                    if _qcv in ds.data_vars:
                        _a[np.isin(ds[_qcv].values, params.QC_BAD_FLAGS)] = np.nan
                    for _i in range(n_instr):
                        if _excl and instr_types[_i].lower() in _excl:
                            continue
                        _serial = _serial_list[_i]
                        _y = _a[::step, _i]
                        if not np.any(np.isfinite(_y)):
                            continue
                        (_ln,) = _ax.plot(
                            time_ds,
                            _y,
                            color=_s_colors[_serial],
                            ls=_s_styles[_serial],
                            lw=0.7,
                            alpha=0.85,
                            label=f"{_serial}",
                        )
                        _handles.setdefault(_serial, _ln)
                    if _invert:
                        _ax.invert_yaxis()
                    _ax.set_ylabel(_ylabel)
                    grid_despine(_ax)
                    if _t_cov_start and _t_cov_end:
                        with contextlib.suppress(Exception):
                            _ax.set_xlim(
                                np.datetime64(_t_cov_start),
                                np.datetime64(_t_cov_end),
                            )
                # No instrument had finite data in any row: drop the figure so the
                # panel stubs as "not available" rather than publishing empty axes
                # (matches the old per-variable _ts_fig behaviour).
                if not _handles:
                    plt.close(fig)
                    return None
                _ax_last = _axs[-1, 0]
                _loc = mdates.AutoDateLocator()
                _ax_last.xaxis.set_major_locator(_loc)
                _ax_last.xaxis.set_major_formatter(mdates.ConciseDateFormatter(_loc))
                date_offset_left(_ax_last)
                _ax_last.set_xlabel("Time")
                # Single legend over the union of instruments in height order,
                # spanning the full figure height on the right.
                _ordered = [s for s in _serial_list if s in _handles]
                if _ordered:
                    # Anchor just right of the panels' actual right edge (not the
                    # figure edge, which leaves a gap), centred vertically.
                    _right = max(_ax.get_position().x1 for _ax in _axs[:, 0])
                    fig.legend(
                        handles=[_handles[s] for s in _ordered],
                        labels=_ordered,
                        loc="center left",
                        bbox_to_anchor=(_right + 0.01, 0.5),
                        bbox_transform=fig.transFigure,
                        borderaxespad=0,
                        framealpha=0.8,
                        ncol=1,
                    )
                b64 = _fig_to_base64(fig)
                _figdebug.record(b64, f"_combined[{_color_var}]", fig)
                record_source(b64, source_for(_combined_ts_fig))
                plt.close(fig)
                return b64

        fig_hydro_pts_b64 = _combined_ts_fig(
            [
                ("pressure", params.vlabel("pressure"), True, {"adcp"}),
                ("temperature", params.vlabel("temperature"), False, None),
                ("salinity", params.vlabel("salinity"), False, None),
            ],
            "temperature",
        )
        fig_dissolved_oxygen_b64 = (
            _ts_fig("dissolved_oxygen", params.vlabel("dissolved_oxygen"))
            if "dissolved_oxygen" in ds
            else None
        )
        fig_vel_uvw_b64 = _combined_ts_fig(
            [
                (
                    "east_velocity",
                    params.vlabel("east_velocity", prefix="U — "),
                    False,
                    None,
                ),
                (
                    "north_velocity",
                    params.vlabel("north_velocity", prefix="V — "),
                    False,
                    None,
                ),
                (
                    "up_velocity",
                    params.vlabel("up_velocity", prefix="W — "),
                    False,
                    None,
                ),
            ],
            "east_velocity",
        )
        fig_turbidity_b64 = (
            _ts_fig("turbidity", params.vlabel("turbidity"), dot_overlay=True)
            if "turbidity" in ds
            else None
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

        fig_spacing_b64: str | None = None
        if "pressure" in ds.data_vars and n_instr > 1:
            try:
                # The spacing axis is labelled dbar; it is a difference of pressures.
                warn_if_not_canonical(
                    ds["pressure"], "dbar", quantity="instrument spacing (dbar)"
                )
                pres_arr = ds["pressure"].values  # (time, N_LEVELS)
                with np.errstate(all="ignore"):
                    med_p = np.nanmedian(
                        pres_arr, axis=0
                    )  # one value per N_LEVELS; NaN when level fully gapped
                sort_idx = np.argsort(med_p)
                pres_sorted = pres_arr[:, sort_idx]
                all_spacings: list = []
                for i in range(1, n_instr):
                    spacing = pres_sorted[:, i] - pres_sorted[:, i - 1]
                    valid = spacing[np.isfinite(spacing) & (spacing >= 2.0)]
                    all_spacings.extend(valid.tolist())
                if all_spacings:

                    def _draw_spacing(
                        *, width_in: float = report_tokens.W_THIRD
                    ) -> Any:
                        with plt.style.context(str(params.MPLSTYLE)):
                            fig_sp, ax_sp = plt.subplots(figsize=(width_in, 3))
                            ax_sp.hist(
                                all_spacings,
                                bins=60,
                                color="steelblue",
                                edgecolor="white",
                            )
                            ax_sp.set_xlabel("Instrument spacing (dbar)")
                            ax_sp.set_ylabel("Count (instrument pair × time step)")
                            plot_title(ax_sp, "Instrument spacing")
                            return fig_sp

                    # Render at the "third" slot so the PNG width matches the
                    # displayed .slot-third class (crisp, not a CSS downscale).
                    fig_spacing_b64 = render_slot(
                        _draw_spacing, slot="third", optional=True
                    )
            except Exception as _exc_sp:
                warnings.warn(
                    f"pressure spacing figure failed: {_exc_sp}", stacklevel=2
                )

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
