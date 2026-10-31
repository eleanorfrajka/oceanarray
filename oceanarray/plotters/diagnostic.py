"""Tier-2 domain wrappers for diagnostic plots (T-S, histograms, spectra, QC).

Knockdown plots
---------------
- :func:`plot_knockdown_pressure` — IQR of measured pressure vs. nominal design
  depth, equal aspect ratio with 1:1 reference line.
- :func:`plot_knockdown_hab` — IQR of measured pressure vs. nominal HAB, equal
  aspect ratio with expected-pressure reference line.
- :func:`plot_knockdown_anomaly` — IQR of pressure anomaly (measured minus nominal)
  per instrument, colour-coded by knockdown severity.
- :func:`plot_knockdown_displacement` — scatter and 2-D heatmap of estimated
  horizontal displacement vs. measured pressure.

Clock-alignment check
---------------------
- :func:`plot_clock_offset_check` — overlaid temperature time series zoomed to
  deployment start and end.

Deployment window figures (moved from report._plots)
-----------------------------------------------------
- :func:`draw_windows` — combined start+end window figure for a single instrument.
- :func:`draw_data_histogram` — histogram of data values with QC thresholds.
- :func:`draw_velocity_iqr_profile` — percentile-profile figure for gridded ADCP
  velocity data.

Post-OdB: migrate the following from report/_plots.py:
  plot_ts_diagram (was _make_ts_diagram), plot_stack_ts_diagram,
  plot_grid_ts_diagram, plot_data_histogram (was _make_data_histogram),
  plot_spectrum (was _make_spectrum_fig_b64).

Tier-1 primitives: plot_vector_heatmap (for T-S, U-V, any pair),
plot_spectrum (any 1D time series), plot_polar_histogram (current rose).
"""

from __future__ import annotations

import re as _re
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from oceanarray.reports._vendored import design_tokens as report_tokens

from .. import parameters as params
from ..config.unit_tables import unit_label, var_unit_label
from .helpers import distinct_line_styles, grid_despine
from .primitives import (
    date_offset_left,
    figure_title,
    plot_title,
    square_axes_grid,
    ytick_reserve_in,
)

if TYPE_CHECKING:
    from datetime import datetime

    import matplotlib.figure
    import matplotlib.pyplot as plt
    import xarray as xr


# ---------------------------------------------------------------------------
# Canonical panel order — single definition; imported by report._plots.
# Labels for physical variables come from params.vlabel() so that VARIABLES is the
# single source of truth.  Instrument-state variables (tilt, battery, etc.)
# have no VARIABLES entry and are kept hardcoded.
# ---------------------------------------------------------------------------

_CANONICAL_PANELS: list[tuple] = [
    ("pressure", params.vlabel("pressure"), "tab:green", True),
    (
        "pressure_1",
        f"Pressure 1 ({params.vunit('pressure')})",
        "tab:green",
        True,
    ),
    ("temperature", params.vlabel("temperature"), "tab:red", False),
    ("conductivity", params.vlabel("conductivity"), "tab:blue", False),
    ("salinity", params.vlabel("salinity"), "tab:cyan", False),
    ("east_velocity", params.vlabel("east_velocity"), "tab:blue", False),
    ("north_velocity", params.vlabel("north_velocity"), "tab:orange", False),
    ("up_velocity", params.vlabel("up_velocity"), "tab:cyan", False),
    ("velocity_beam1", f"Beam 1 velocity ({unit_label('m s-1')})", "tab:blue", False),
    (
        "velocity_beam2",
        f"Beam 2 velocity ({unit_label('m s-1')})",
        "tab:orange",
        False,
    ),
    ("velocity_beam3", f"Beam 3 velocity ({unit_label('m s-1')})", "tab:cyan", False),
    ("tilt", "Tilt (°)", "tab:red", False),
    ("pitch", "Pitch (°)", "tab:purple", False),
    ("roll", "Roll (°)", "#8B4513", False),
    ("heading", "Heading (°)", "tab:gray", False),
    ("speed_of_sound", f"Sound speed ({unit_label('m s-1')})", "tab:olive", False),
    ("turbidity", params.vlabel("turbidity"), "tab:brown", False),
    ("dissolved_oxygen", params.vlabel("dissolved_oxygen"), "steelblue", False),
    ("battery_voltage", "Battery (V)", "tab:pink", False),
]

_COMPACT_PANEL_VARS: frozenset = frozenset({"battery_voltage", "speed_of_sound"})
_COMPACT_PANEL_HEIGHT: float = 1.5
#: Height (relative units) of a normal, non-compact instrument panel row.  Shared
#: by ``draw_windows`` here and ``_build_fig_from_ds`` in ``report/_plots.py`` so
#: the full time-series and start/end-window figures use the same row height.
_PANEL_HEIGHT: float = 2.0

# QC overlay marker styles (OceanSITES flag codes 3, 4, 8).
_QC_COLORS: dict[int, str] = {
    3: "#f39c12",
    4: "#e74c3c",
    8: "#3498db",
}
_QC_MARKER: dict[int, dict] = {
    3: {
        "marker": "+",
        "c": _QC_COLORS[3],
        "s": 15,
        "linewidths": 0.8,
        "zorder": 3,
        "rasterized": True,
    },
    4: {
        "marker": "+",
        "c": _QC_COLORS[4],
        "s": 15,
        "linewidths": 0.8,
        "zorder": 4,
        "rasterized": True,
    },
    8: {
        "marker": ".",
        "c": _QC_COLORS[8],
        "s": 8,
        "linewidths": 0.5,
        "zorder": 2,
        "rasterized": True,
    },
}


def _instrument_panels(ds: xr.Dataset, combine_pitch_roll: bool = False) -> list[tuple]:
    """Return panel list ``(varname, ylabel, line_color, invert_y)`` in canonical order.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset whose ``data_vars`` are inspected.
    combine_pitch_roll : bool
        When True, replace separate pitch and roll rows with a single
        ``_pitch_roll_combo`` panel.

    Returns
    -------
    list of tuple

    """
    time_vars = {v for v in ds.data_vars if ds[v].dims == ("time",)}

    has_enu = any(
        v in time_vars for v in ("east_velocity", "north_velocity", "up_velocity")
    )
    beam_vars = {"velocity_beam1", "velocity_beam2", "velocity_beam3"}
    do_combo = combine_pitch_roll and "pitch" in time_vars and "roll" in time_vars

    out = []
    is_velocity_instrument = has_enu or bool(beam_vars & time_vars)
    for vname, label, color, invert in _CANONICAL_PANELS:
        if vname not in time_vars:
            continue
        if has_enu and vname in beam_vars:
            continue
        # Aquadopps/ADCPs record speed_of_sound internally for the velocity
        # solution, but it is not a science output — drop the panel for them.
        if vname == "speed_of_sound" and is_velocity_instrument:
            continue
        if do_combo:
            if vname == "pitch":
                pr_u = var_unit_label(ds["pitch"])
                combo = f"Pitch & Roll ({pr_u})" if pr_u else "Pitch & Roll"
                out.append(("_pitch_roll_combo", combo, None, False))
                continue
            if vname == "roll":
                continue
        # Relabel the parenthesised unit from the variable's stored `units` attribute,
        # formatted through unit_label, so the axis tracks the data: a file written in
        # radians (angles) or kPa (pressure) relabels rather than silently keeping the
        # canonical spelling the label was written with.
        u = var_unit_label(ds[vname])
        if u:
            label = (
                _re.sub(r"\([^()]*\)\s*$", f"({u})", label)
                if _re.search(r"\([^()]*\)\s*$", label)
                else f"{label} ({u})"
            )
        out.append((vname, label, params.VAR_COLORS.get(vname, color), invert))
    return out


# ---------------------------------------------------------------------------
# Shared helper for stack knockdown plots
# ---------------------------------------------------------------------------

_KD_COLORS = [
    (100.0, "mediumseagreen"),
    (200.0, "gold"),
    (300.0, "darkorange"),
    (np.inf, "firebrick"),
]


def _kd_color(magnitude_dbar: float) -> str:
    """Return a colour string for the given knockdown magnitude in dbar."""
    return next(c for thresh, c in _KD_COLORS if magnitude_dbar < thresh)


def _collect_knockdown_records(
    ds: xr.Dataset,
    waterdepth: float,
) -> list:
    """Extract per-instrument knockdown arrays from a stack dataset.

    Returns a list of ``(p_nom, serial, actual_pressure_array)`` tuples,
    one per non-ADCP instrument with at least 10 valid pressure samples.
    Interpolated pressure (QC flag 8) is excluded.

    Parameters
    ----------
    ds : xr.Dataset
        Stack dataset with dimensions ``(time, N_LEVELS)``.
    waterdepth : float
        Water depth in metres (1 dbar ≈ 1 m seawater).

    Returns
    -------
    list of tuple

    """
    serials = ds["serial"].values
    habs = ds["hab"].values
    instr_types = ds["instrument_type"].values
    n_instr = ds.sizes["N_LEVELS"]
    pressure_arr = ds["pressure"].values  # (time, N_LEVELS)
    pqc_arr = ds["pressure_qc"].values if "pressure_qc" in ds.data_vars else None

    records = []
    for i in range(n_instr):
        if str(instr_types[i]).lower() == "adcp":
            continue
        p_nom = waterdepth - float(habs[i])
        p_vals = pressure_arr[:, i].copy()
        if pqc_arr is not None:
            _qc = pqc_arr[:, i] if pqc_arr.ndim == 2 else pqc_arr
            p_vals[_qc == 8] = np.nan
        valid = p_vals[np.isfinite(p_vals)]
        if len(valid) < 10:
            continue
        records.append((p_nom, str(serials[i]), valid))
    return records


# ---------------------------------------------------------------------------
# Knockdown figure 1: measured pressure vs. nominal (equal aspect, 1:1 line)
# ---------------------------------------------------------------------------


def plot_knockdown_pressure(
    ds: xr.Dataset,
) -> matplotlib.figure.Figure | None:
    """IQR of measured pressure vs. nominal design depth, equal aspect ratio.

    Each non-ADCP instrument is shown as a vertical box-and-whisker positioned
    at its nominal pressure on the x-axis; the box spans the IQR of the actual
    measured pressure distribution on the y-axis (increasing downward).

    The axes have equal aspect (``ax.set_aspect("equal")``) and both run from
    0 to the maximum value, so the 1:1 reference line appears at 45°.
    Instruments on the diagonal are at their design depth; boxes that drop
    below the diagonal experienced knockdown (measured deeper than nominal).

    Boxes are coloured blue; interpolated pressure (QC flag 8) is excluded.
    Rendered at half-width in the stack report.

    Parameters
    ----------
    ds : xr.Dataset
        Stack dataset with dimensions ``(time, N_LEVELS)`` containing
        ``pressure``, ``hab``, ``serial``, ``instrument_type``, and
        optionally ``pressure_qc``.  The ``waterdepth`` global attribute
        must be present and non-zero.

    Returns
    -------
    matplotlib.figure.Figure or None

    """
    if "pressure" not in ds.data_vars or "hab" not in ds:
        return None

    import matplotlib.pyplot as plt

    from oceanarray import parameters as params

    try:
        waterdepth = float(ds.attrs.get("waterdepth", 0) or 0)
    except (ValueError, TypeError):
        return None
    if waterdepth <= 0:
        return None

    records = _collect_knockdown_records(ds, waterdepth)
    if not records:
        return None

    p_nom_vals = [r[0] for r in records]
    p_range = max(p_nom_vals) - min(p_nom_vals) if len(p_nom_vals) > 1 else 50.0
    box_width = max(5.0, p_range / (len(p_nom_vals) * 2))

    with plt.style.context(str(params.MPLSTYLE)):
        fig, ax = plt.subplots(figsize=(5, 5))

        p_max_all = 0.0
        for p_nom, _serial, actual_p in records:
            bp = ax.boxplot(
                actual_p,
                vert=True,
                positions=[p_nom],
                widths=box_width,
                patch_artist=True,
                flierprops={
                    "marker": ".",
                    "markersize": 2,
                    "alpha": 0.25,
                    "color": "grey",
                },
                medianprops={"color": "black", "linewidth": 1.5},
                manage_ticks=False,
            )
            bp["boxes"][0].set_facecolor("steelblue")
            bp["boxes"][0].set_alpha(0.6)
            p_max_all = max(p_max_all, float(np.nanmax(actual_p)))

        ax_max = max(p_max_all, max(p_nom_vals)) * 1.05
        ax.set_xlim(ax_max, 0)  # deeper nominal pressure on the left
        ax.set_ylim(0, ax_max)
        ax.set_aspect("equal")
        ax.invert_yaxis()  # deeper measured pressure at bottom

        ax.plot(
            [0, ax_max],
            [0, ax_max],
            color="dimgrey",
            lw=0.8,
            ls="--",
            zorder=0,
            label="actual = nominal",
        )
        ax.legend(loc="upper left")
        ax.set_xlabel("Nominal pressure (dbar)")
        ax.set_ylabel("Measured pressure (dbar)")
        grid_despine(ax)

        plt.tight_layout()
        return fig


# ---------------------------------------------------------------------------
# Knockdown figure 1b: measured HAB vs. nominal HAB (horizontal displacement)
# ---------------------------------------------------------------------------


def plot_knockdown_hab(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_HALF,
) -> matplotlib.figure.Figure | None:
    """IQR of measured pressure vs. nominal HAB, equal aspect ratio.

    Companion to :func:`plot_knockdown_pressure`.  The y-axis is identical
    (measured pressure, dbar, increasing downward); only the x-axis changes
    from nominal pressure to nominal height-above-bottom (m).

    Each non-ADCP instrument is shown as a vertical box-and-whisker positioned
    at its nominal HAB on the x-axis; the box spans the IQR of the measured
    pressure distribution.  The dashed reference line shows the expected
    pressure for each HAB (``pressure = waterdepth − hab``).  Boxes below
    the line were pulled deeper than their design height by current drag.

    With 1 dbar ≈ 1 m, equal aspect keeps the reference line near 45° and
    the departure from it is directly related to horizontal displacement.

    Parameters
    ----------
    ds : xr.Dataset
        Stack dataset with dimensions ``(time, N_LEVELS)`` containing
        ``pressure``, ``hab``, ``serial``, ``instrument_type``, and
        optionally ``pressure_qc``.  The ``waterdepth`` global attribute
        must be present and non-zero.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure or None

    """
    if "pressure" not in ds.data_vars or "hab" not in ds:
        return None

    import matplotlib.pyplot as plt

    from oceanarray import parameters as params

    try:
        waterdepth = float(ds.attrs.get("waterdepth", 0) or 0)
    except (ValueError, TypeError):
        return None
    if waterdepth <= 0:
        return None

    records = _collect_knockdown_records(ds, waterdepth)
    if not records:
        return None

    # hab_nom is stored as p_nom = waterdepth - hab, so recover hab
    hab_records = [
        (waterdepth - p_nom, serial, actual_p) for p_nom, serial, actual_p in records
    ]

    hab_nom_vals = [r[0] for r in hab_records]
    hab_range = max(hab_nom_vals) - min(hab_nom_vals) if len(hab_nom_vals) > 1 else 10.0
    box_width = max(2.0, hab_range / (len(hab_nom_vals) * 2))

    with plt.style.context(str(params.MPLSTYLE)):
        # Half width — displayed side-by-side with the anomaly panel in a two-column
        # flex row (see mooring.html), so the slot is ~half the page, not full.
        fig, ax = plt.subplots(figsize=(width_in, width_in))

        p_max_all = 0.0
        for hab_nom, _serial, actual_p in hab_records:
            bp = ax.boxplot(
                actual_p,
                vert=True,
                positions=[hab_nom],
                widths=box_width,
                patch_artist=True,
                flierprops={
                    "marker": ".",
                    "markersize": 2,
                    "alpha": 0.25,
                    "color": "grey",
                },
                medianprops={"color": "black", "linewidth": 1.5},
                manage_ticks=False,
            )
            bp["boxes"][0].set_facecolor("steelblue")
            bp["boxes"][0].set_alpha(0.6)
            p_max_all = max(p_max_all, float(np.nanmax(actual_p)))

        ax_max = max(p_max_all, waterdepth) * 1.05
        ax.set_xlim(0, ax_max)  # low HAB (deep) on the left, surface on the right
        ax.set_ylim(0, ax_max)
        ax.set_aspect("equal")
        ax.invert_yaxis()  # deeper (larger pressure) at bottom

        # Reference line: expected pressure = waterdepth - hab
        ax.plot(
            [0, ax_max],
            [waterdepth, waterdepth - ax_max],
            color="dimgrey",
            lw=0.8,
            ls="--",
            zorder=0,
            label="expected pressure",
        )
        ax.legend(loc="upper right")
        ax.set_xlabel("Nominal HAB (m)")
        ax.set_ylabel("Measured pressure (dbar)")

        plt.tight_layout()
        return fig


# ---------------------------------------------------------------------------
# Knockdown figure 2: pressure anomaly IQR
# ---------------------------------------------------------------------------


def plot_knockdown_anomaly(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_HALF,
) -> matplotlib.figure.Figure | None:
    """IQR of pressure anomaly (measured − nominal) per instrument.

    Each non-ADCP instrument is shown as a horizontal box-and-whisker at its
    nominal pressure on the y-axis; the box spans the IQR of the pressure
    anomaly distribution (actual − nominal, positive = knocked down deeper).

    A vertical reference line at x = 0 marks zero knockdown.  Box colour
    indicates the magnitude of the median knockdown:

    ==========  ========================
    Colour      Knockdown magnitude
    ==========  ========================
    green       < 100 dbar
    gold        100–200 dbar
    darkorange  200–300 dbar
    firebrick   > 300 dbar
    ==========  ========================

    Interpolated pressure (QC flag 8) is excluded.  Rendered at half-width
    in the stack report.

    Parameters
    ----------
    ds : xr.Dataset
        Stack dataset; same requirements as :func:`plot_knockdown_pressure`.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure or None

    """
    if "pressure" not in ds.data_vars or "hab" not in ds:
        return None

    import matplotlib.pyplot as plt

    from oceanarray import parameters as params

    try:
        waterdepth = float(ds.attrs.get("waterdepth", 0) or 0)
    except (ValueError, TypeError):
        return None
    if waterdepth <= 0:
        return None

    records = _collect_knockdown_records(ds, waterdepth)
    if not records:
        return None

    p_nom_vals = [r[0] for r in records]
    p_range = max(p_nom_vals) - min(p_nom_vals) if len(p_nom_vals) > 1 else 50.0
    box_width = max(5.0, p_range / (len(p_nom_vals) * 2))

    with plt.style.context(str(params.MPLSTYLE)):
        # Half width — side-by-side with the HAB panel in the mooring.html flex row.
        fig, ax = plt.subplots(figsize=(width_in, max(3, len(records) * 0.4 + 1)))

        for p_nom, _serial, actual_p in records:
            anomaly = actual_p - p_nom  # positive = knocked down deeper
            median_anom = float(np.median(anomaly))
            color = _kd_color(abs(median_anom))

            bp = ax.boxplot(
                anomaly,
                vert=False,
                positions=[p_nom],
                widths=box_width,
                patch_artist=True,
                flierprops={
                    "marker": ".",
                    "markersize": 2,
                    "alpha": 0.25,
                    "color": "grey",
                },
                medianprops={"color": "black", "linewidth": 1.5},
                manage_ticks=False,
            )
            bp["boxes"][0].set_facecolor(color)
            bp["boxes"][0].set_alpha(0.7)

        ax.invert_yaxis()
        ax.axvline(0, color="k", lw=0.8, zorder=3)
        # Anchor the left edge at exactly 0 when everything is knocked down
        # (positive); keep any genuine negative (shallower-than-nominal) anomalies
        # visible rather than clipping them.
        ax.set_xlim(left=min(0.0, ax.get_xlim()[0]))
        ax.set_xlabel("Pressure anomaly (dbar) — positive = deeper than nominal")
        ax.set_ylabel("Nominal pressure (dbar)")

        plt.tight_layout()
        return fig


# ---------------------------------------------------------------------------
# Knockdown figure 3: horizontal displacement scatter
# ---------------------------------------------------------------------------


def plot_knockdown_displacement(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_FULL,
) -> matplotlib.figure.Figure | None:
    """Scatter and heatmap of estimated horizontal displacement vs. measured pressure.

    For each non-ADCP instrument the horizontal displacement is estimated at
    every time step as:

        x_horiz = sqrt(max(0, hab_nom² − hab_meas²))

    where ``hab_nom`` is the nominal height-above-bottom (m) and
    ``hab_meas = waterdepth − pressure`` is the measured HAB.

    **Left panel** — scatter of ``(x_horiz, measured_pressure)`` per
    instrument, coloured by serial number.  y-axis is measured pressure
    (dbar, increasing downward); x-axis is horizontal displacement (m).
    Square axes (equal aspect, adjustable data limits).

    **Right panel** — 2-D count heatmap of the same points across all
    instruments combined.  Discrete colorbar (up to 15 levels).

    Parameters
    ----------
    ds : xr.Dataset
        Stack dataset; same requirements as :func:`plot_knockdown_pressure`.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure or None

    """
    if "pressure" not in ds.data_vars or "hab" not in ds:
        return None

    import matplotlib.colors as mcolors
    import matplotlib.pyplot as plt

    from oceanarray import parameters as params
    from oceanarray.utilities import _nice_colorbar_bounds

    try:
        waterdepth = float(ds.attrs.get("waterdepth", 0) or 0)
    except (ValueError, TypeError):
        return None
    if waterdepth <= 0:
        return None

    records = _collect_knockdown_records(ds, waterdepth)
    if not records:
        return None

    # --- collect data for both panels ---
    _tab20 = plt.get_cmap("tab20")
    scatter_data = []  # (serial, x_horiz_thinned, p_thinned, color)
    all_x_list = []
    all_p_list = []

    for idx, (p_nom, serial, actual_p) in enumerate(records):
        hab_nom = waterdepth - p_nom
        hab_meas = waterdepth - actual_p
        x_horiz = np.sqrt(np.maximum(0.0, hab_nom**2 - hab_meas**2))
        all_x_list.append(x_horiz)
        all_p_list.append(actual_p)
        step = max(1, len(actual_p) // 2000)
        scatter_data.append(
            (serial, x_horiz[::step], actual_p[::step], _tab20(idx % 20))
        )

    all_x = np.concatenate(all_x_list)
    all_p = np.concatenate(all_p_list)
    valid = np.isfinite(all_x) & np.isfinite(all_p)
    all_x, all_p = all_x[valid], all_p[valid]

    x_max = max(float(np.nanmax(all_x)) if len(all_x) else 1.0, 1.0)
    p_max = float(np.nanmax(all_p)) * 1.05 if len(all_p) else 1.0
    with plt.style.context(str(params.MPLSTYLE)):
        # Deterministic square panels with the colorbar in its OWN reserved column.
        # (plt.subplots + fig.colorbar(ax=ax2) took the colorbar's width out of the
        # right panel, so set_box_aspect(1) then shrank its height and it no longer
        # matched the left panel — square_axes_grid places both by construction.)
        # Tight inter-panel gap (the heatmap shares the scatter's y-axis and hides
        # its own y-labels) and a wider colorbar-text reserve for the long label.
        # Reserve left margin for the pressure y-labels — square_axes_grid bypasses
        # tight_layout, so a deep mooring's 4-digit dbar labels clip without this.
        fig, axs, cax = square_axes_grid(
            width_in,
            1,
            2,
            colorbar=True,
            wgap_in=0.3,
            cbar_txt_in=1.0,
            left_in=ytick_reserve_in(all_p),
        )
        ax1, ax2 = axs[0, 0], axs[0, 1]

        # The panels are square (square_axes_grid), but each axis spans its OWN
        # data range — displacement on x (0..x_max), pressure on y (0..p_max) — so
        # the displacement structure fills the panel instead of being squeezed into
        # a thin strip against the full depth range.  This is NOT a 1:1 physical
        # scale (true-scale, a mooring barely tilts and reads as an empty vertical
        # sliver); readability wins for a QC diagnostic.
        for _ax in (ax1, ax2):
            _ax.set_xlim(0, x_max)
            _ax.set_ylim(0, p_max)
            _ax.invert_yaxis()
            _ax.set_xlabel("Horizontal displacement (m)")
            grid_despine(_ax)

        # --- left panel: scatter ---
        for serial, x_thin, p_thin, color in scatter_data:
            ax1.scatter(
                x_thin,
                p_thin,
                s=4,
                alpha=0.3,
                color=color,
                linewidths=0,
                label=str(serial),
                rasterized=True,
            )
        ax1.set_ylabel("Measured pressure (dbar)")
        ax1.legend(loc="lower right", markerscale=3)

        # --- right panel: per-instrument normalised heatmap ---
        # Each instrument's 2-D histogram is divided by its own total before
        # summing, so all instruments contribute equally regardless of record length.
        ax2.tick_params(labelleft=False)  # same y-axis as the scatter panel
        n_bins = 40
        x_edges = np.linspace(0, x_max, n_bins + 1)
        p_edges = np.linspace(0, p_max, n_bins + 1)

        H_norm = np.zeros((n_bins, n_bins))
        for x_arr, p_arr in zip(all_x_list, all_p_list, strict=False):
            valid_i = np.isfinite(x_arr) & np.isfinite(p_arr)
            if not valid_i.sum():
                continue
            H_i, _, _ = np.histogram2d(
                x_arr[valid_i], p_arr[valid_i], bins=[x_edges, p_edges]
            )
            total = H_i.sum()
            if total > 0:
                H_norm += H_i / total
        H_norm[H_norm == 0] = np.nan

        h_vals = H_norm[np.isfinite(H_norm)]
        if len(h_vals):
            # Start the scale at 0 and thin the tick labels to ~6 nice values
            # (was ~15 ticks like 0.015 / 0.045 / 0.075).
            bounds = _nice_colorbar_bounds(
                0.0, float(np.nanpercentile(h_vals, 98)), n=15
            )
            norm = mcolors.BoundaryNorm(bounds, ncolors=256)
            mesh = ax2.pcolormesh(x_edges, p_edges, H_norm.T, norm=norm, cmap="YlOrRd")
            fig.colorbar(
                mesh,
                cax=cax,
                ticks=bounds[:: max(1, len(bounds) // 6)],
                label="Normalised density (sum = 1 per instrument)",
            )
        else:
            cax.set_axis_off()  # nothing to show; don't leave an empty colorbar box
        return fig


# ---------------------------------------------------------------------------
# Clock-offset comparison
# ---------------------------------------------------------------------------


def plot_clock_offset_check(
    nc_paths: dict[str, Path],
    deploy_dt: datetime | None,
    recover_dt: datetime | None,
    window_minutes: int = 30,
    *,
    width_in: float = report_tokens.W_FULL,
) -> matplotlib.figure.Figure | None:
    """Overlaid, per-instrument normalised temperature around deploy and recover.

    Plots a ``±window_minutes`` window centred on deployment and on recovery for
    every instrument with a temperature variable, so clock alignment between
    instruments can be assessed visually.  If an instrument's clock is offset the
    temperature signal appears shifted in time relative to the others.

    Each instrument's trace is **standardised over the plotted window**
    (subtract the window mean, divide by the window standard deviation) so
    instruments with different absolute temperatures and amplitudes overlay on a
    common ``std`` y-axis and their *timing* can be compared directly.

    Two sub-panels are produced side by side:

    - **Left**: ``deploy_dt ± window_minutes``
    - **Right**: ``recover_dt ± window_minutes``

    When ``deploy_dt`` or ``recover_dt`` is ``None``, only the available
    window is produced.  A shared legend below both panels lists all
    instruments.  An instrument with zero variance in a window (flat/constant)
    is skipped for that window (no timing information, and normalisation is
    undefined).

    Parameters
    ----------
    nc_paths : dict of {serial: Path}
        Paths to stage-2 or stage-3 NetCDF files keyed by serial number.
        Only instruments with temperature data are included in the plot.
    deploy_dt : datetime or None
        Deployment time (UTC).
    recover_dt : datetime or None
        Recovery time (UTC).
    window_minutes : int
        Duration of each zoom window in minutes.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure or None
        None when fewer than two instruments have temperature data, or if
        any other error prevents plotting.

    """
    if not nc_paths:
        return None

    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import xarray as xr

    from oceanarray import parameters as params

    _TEMP_VARS = ("temperature", "temp", "TEMP", "sea_water_temperature")

    # Load temperature time series for each instrument
    series: dict = {}  # {serial: (time_array, temp_array)}
    for serial, path in nc_paths.items():
        if not path.exists():
            continue
        try:
            ds = xr.open_dataset(path, decode_timedelta=False).load()
            temp_var = next((v for v in _TEMP_VARS if v in ds.data_vars), None)
            if temp_var is None or "time" not in ds.dims:
                ds.close()
                continue
            t = ds["time"].values
            temp = ds[temp_var].values.astype(float)
            # Apply QC mask if available
            qc_var = f"{temp_var}_qc"
            if qc_var in ds.data_vars:
                temp[ds[qc_var].values >= 4] = np.nan
            ds.close()
            series[serial] = (t, temp)
        except Exception:  # noqa: BLE001
            continue

    if len(series) < 2:
        return None

    # Determine zoom windows
    _td = np.timedelta64(window_minutes * 60, "s")
    windows: list = []
    if deploy_dt is not None:
        t0 = np.datetime64(deploy_dt.replace(tzinfo=None).isoformat())
        windows.append((t0 - _td, t0 + _td, f"Deployment ±{window_minutes} min"))
    if recover_dt is not None:
        t1 = np.datetime64(recover_dt.replace(tzinfo=None).isoformat())
        windows.append((t1 - _td, t1 + _td, f"Recovery ±{window_minutes} min"))
    if not windows:
        return None

    n_panels = len(windows)
    with plt.style.context(str(params.MPLSTYLE)):
        fig, axes = plt.subplots(1, n_panels, figsize=(width_in, 3.5), sharey=False)
        if n_panels == 1:
            axes = [axes]

        # Colourblind-safe styles: colour alone (tab20 = 20 hues) collided once
        # a mooring had >20 instruments, and tab20 is not CVD-safe.  Vary colour
        # (Okabe-Ito) and linestyle so up to 32 lines are each distinct.
        styles = dict(zip(series, distinct_line_styles(len(series)), strict=False))

        plotted_serials: set = set()
        for ax, (t_lo, t_hi, title) in zip(axes, windows, strict=False):
            for serial, (t, temp) in series.items():
                mask = (t >= t_lo) & (t <= t_hi) & np.isfinite(temp)
                if not np.any(mask):
                    continue
                tw = temp[mask]
                sd = np.nanstd(tw)
                if not np.isfinite(sd) or sd == 0:
                    continue  # flat window: no timing info, normalisation undefined
                tw_norm = (tw - np.nanmean(tw)) / sd
                _c, _ls, _lw = styles[serial]
                ax.plot(t[mask], tw_norm, color=_c, linestyle=_ls, lw=_lw)
                plotted_serials.add(serial)

            plot_title(ax, title)
            ax.set_ylabel("Normalised temperature (std)")
            grid_despine(ax)
            locator = mdates.AutoDateLocator()
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            date_offset_left(ax)

        # No instrument fell inside any window (e.g. deploy/recover times outside
        # the data range) — skip the figure rather than emit an empty axis, which
        # matplotlib would date-label at the 1970 epoch.
        if not plotted_serials:
            plt.close(fig)
            return None

        from matplotlib.lines import Line2D

        handles = [
            Line2D(
                [0],
                [0],
                color=styles[s][0],
                linestyle=styles[s][1],
                lw=styles[s][2],
                label=str(s),
            )
            for s in series
            if s in plotted_serials
        ]
        # Shared legend below the axes.  A fixed bottom reserve overlapped the
        # panels once the instrument count was large (~24 → 6 cols × 4 rows), so
        # size a dedicated legend band from the row count and grow the figure
        # height by it — the panels keep their height and the legend's top edge
        # stays below the date labels (Eleanor 2026-08-18).
        ncol = min(len(handles), 6)
        n_rows = int(np.ceil(len(handles) / ncol))
        panel_in = 3.5
        # Padding includes a gap above the legend for the date-offset labels, so
        # the legend's top edge clears the dates (Eleanor 2026-08-18).
        legend_in = 0.55 + n_rows * 0.19  # padding + per-row height (inches)
        fig.set_size_inches(width_in, panel_in + legend_in)
        fig.legend(
            handles=handles,
            loc="lower center",
            ncol=ncol,
            bbox_to_anchor=(0.5, 0.005),
            frameon=True,
        )

        # Reserve the legend band at the bottom and keep it: mark the figure
        # manual so the encoder does not re-run tight_layout and undo the reserve
        # (which clipped the legend and overspilled the slot at full-canvas save).
        _bottom = legend_in / (panel_in + legend_in) + 0.03
        fig.subplots_adjust(
            bottom=_bottom, top=0.92, left=0.08, right=0.97, wspace=0.22
        )
        fig._manual_layout = True
        return fig


# ---------------------------------------------------------------------------
# Deployment window figures (moved from report._plots)
# ---------------------------------------------------------------------------


def draw_windows(
    nc_path: Path,
    instr_type: str,
    hours: int = 6,
    show_qc: bool = True,
    vlines: list | None = None,
    stage1_nc: Path | None = None,
    panels: list | None = None,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Draw the combined start + end window figure: (nrows × 2) — left = first N h, right = last N h.

    Parameters
    ----------
    nc_path : Path
        Path to the processed (stage2 or stage3) NetCDF file.
    instr_type : str
        Instrument type string (used in the figure title).
    hours : int
        Width of each window in hours (default 6).
    show_qc : bool
        Overlay QC flag markers on the data.
    vlines : list of (time_val, color, label), optional
        Vertical marker lines to draw on both panels.  *time_val* may be a
        ``numpy.datetime64``, an ISO-8601 string, or a ``pandas.Timestamp``.
        Lines are only drawn when they fall inside the plotted window.
        Labels appear as small rotated text at the top of the **first row** only
        to avoid excessive clutter.
    stage1_nc : Path, optional
        Path to the stage1 NC file.  When provided, the raw stage1 data is
        plotted as a light-grey background trace in each window panel so the
        full pre/post-deployment record is visible (bench → deployment in the
        left panel; deployment → recovery in the right panel).  The x-axis
        limits are taken from the stage1 window extent so that the recovery
        transition appears even if the stage2/3 YAML trim cut it off.  The
        y-axis limits are taken from the primary (stage2/3) data only so that
        bench-pressure outliers (p ≈ 0) do not squish the deployment-depth view.
    panels : list, optional
        Subset of ``_instrument_panels`` tuples to draw.  When given, only these
        rows are rendered (used to paginate a tall window figure across several
        images); otherwise every panel for the instrument is drawn on one figure.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None
        Figure, or None if the dataset is too short to show windows.

    """
    import matplotlib.pyplot as plt
    import xarray as xr
    from matplotlib.gridspec import GridSpec

    from .primitives import date_axis

    ds = xr.open_dataset(nc_path, decode_timedelta=False).load()
    # Stage1 background data (optional).  Closed in the finally block.
    # Masks are computed later, after the stage2/3 x-axis bounds are known,
    # so the grey trace is selected by the same window as the coloured data.
    ds1 = None
    time1 = None
    start_mask1: np.ndarray | None = None
    end_mask1: np.ndarray | None = None
    if stage1_nc is not None:
        try:
            ds1 = xr.open_dataset(stage1_nc, decode_timedelta=False).load()
            time1 = ds1["time"].values
        except Exception:  # noqa: BLE001
            ds1 = None
    try:
        time = ds["time"].values
        if len(time) < 2:
            return None

        start_mask = time <= time[0] + np.timedelta64(hours * 3600, "s")
        end_mask = time >= time[-1] - np.timedelta64(hours * 3600, "s")
        if start_mask.sum() < 2 and end_mask.sum() < 2:
            return None

        if panels is None:
            panels = _instrument_panels(ds, combine_pitch_roll=True)
        if not panels:
            return None

        height_ratios = [
            _COMPACT_PANEL_HEIGHT if vname in _COMPACT_PANEL_VARS else _PANEL_HEIGHT
            for vname, *_ in panels
        ]
        nrows = len(panels)
        fig = plt.figure(figsize=(width_in, sum(height_ratios)))
        gs = GridSpec(
            nrows,
            2,
            figure=fig,
            height_ratios=height_ratios,
            wspace=0.06,
            hspace=0.18,
        )

        def _plot_panel(
            ax: plt.Axes,
            vname: str,
            label: str,
            color: str,
            invert: bool,
            mask: np.ndarray,
            col: str,
        ) -> None:
            _suspect_t = float(ds.attrs.get("tilt_suspect_threshold", 20.0))
            _fail_t = float(ds.attrs.get("tilt_fail_threshold", 30.0))

            if vname == "_pitch_roll_combo":
                t = time[mask]
                if len(t) < 2:
                    ax.set_visible(False)
                    return
                if "pitch" in ds.data_vars:
                    _p = ds["pitch"].values.astype(float)[mask]
                    ax.plot(t, _p, color="tab:purple", lw=0.6, label="pitch", zorder=1)
                    ax.scatter(t, _p, s=3, color="tab:purple", zorder=2, linewidths=0)
                if "roll" in ds.data_vars:
                    _r = ds["roll"].values.astype(float)[mask]
                    ax.plot(t, _r, color="#8B4513", lw=0.6, label="roll", zorder=1)
                    ax.scatter(t, _r, s=3, color="#8B4513", zorder=2, linewidths=0)
                for _val, _c, _ls in [
                    (_suspect_t, "tab:orange", "--"),
                    (-_suspect_t, "tab:orange", "--"),
                    (_fail_t, "tab:red", ":"),
                    (-_fail_t, "tab:red", ":"),
                ]:
                    ax.axhline(_val, color=_c, lw=0.8, ls=_ls, zorder=0)
                if col == 0:
                    ax.set_ylabel(label)
                    ax.legend(loc="upper right", framealpha=0.8)
                else:
                    ax.tick_params(labelleft=False)
                date_axis(ax)
                ax.tick_params(axis="x")
                return

            data = ds[vname].values.astype(float)
            t, d = time[mask], data[mask]
            if len(t) < 2:
                ax.set_visible(False)
                return
            ax.plot(t, d, color=color, linewidth=0.6, zorder=1)
            ax.scatter(t, d, s=3, color=color, zorder=2, linewidths=0)
            if "velocity" in vname and not invert:
                ax.axhline(0, color="k", linewidth=0.4, linestyle="--", zorder=0)
            if vname == "tilt":
                ax.axhline(_suspect_t, color="tab:orange", lw=0.9, ls="--", zorder=2)
                ax.axhline(_fail_t, color="tab:red", lw=0.9, ls="--", zorder=2)
            # ylim for inverted variables is set from the outer loop using the
            # combined range of both panels, so we do not set it here.
            if show_qc and f"{vname}_qc" in ds.data_vars:
                flags = ds[f"{vname}_qc"].values.astype(int)[mask]
                for fval, mkw in _QC_MARKER.items():
                    m2 = flags == fval
                    if m2.any():
                        ax.scatter(t[m2], d[m2], **mkw)
            if col == 0:
                ax.set_ylabel(label)
            else:
                ax.tick_params(labelleft=False)
            date_axis(ax)
            ax.tick_params(axis="x")

        # Normalise vlines to numpy datetime64[ns] upfront.
        # Use pandas.Timestamp as an intermediate because it handles Python
        # datetime objects, ISO strings, and pandas.Timestamp objects uniformly.
        import pandas as _pd

        _vlines_ns: list = []
        for _vt, _vc, _vl in vlines or []:
            try:
                _ts = _pd.Timestamp(_vt)
                if _ts.tzinfo is not None:
                    _ts = _ts.tz_convert("UTC").tz_localize(None)
                _vlines_ns.append((_ts.to_datetime64(), _vc, _vl))
            except Exception:  # noqa: BLE001
                pass

        def _draw_vlines(
            ax: plt.Axes,
            t_lo: np.datetime64,
            t_hi: np.datetime64,
            first_row: bool,
        ) -> None:
            """Draw vertical marker lines that fall inside [t_lo, t_hi]."""
            for _vt, _vc, _vl in _vlines_ns:
                if _vt < t_lo or _vt > t_hi:
                    continue
                ax.axvline(_vt, color=_vc, lw=1.2, ls="--", zorder=-1)
                if first_row:
                    ax.text(
                        _vt,
                        1.0,
                        f" {_vl}",
                        transform=ax.get_xaxis_transform(),
                        rotation=90,
                        va="bottom",
                        ha="left",
                        color=_vc,
                        zorder=5,
                        clip_on=False,
                    )

        # X-axis bounds: always based on stage2/3 data.  Add 1 h of padding
        # before the stage2/3 start (left panel) and after the stage2/3 end
        # (right panel) so that any stage1 grey data in those margins is visible.
        # This makes each window effectively 7 h wide (1 h pad + 6 h of record).
        _one_hour = np.timedelta64(3600, "s")
        _t_start_lo = time[0] - _one_hour
        _t_start_hi = time[start_mask][-1] if start_mask.any() else time[0]
        _t_end_lo = time[end_mask][0] if end_mask.any() else time[-1]
        _t_end_hi = time[-1] + _one_hour

        # Stage1 masks: select stage1 samples within the expanded x windows so the
        # grey trace fills the 1 h margins but never triggers axes auto-rescaling.
        if time1 is not None:
            start_mask1 = (time1 >= _t_start_lo) & (time1 <= _t_start_hi)
            end_mask1 = (time1 >= _t_end_lo) & (time1 <= _t_end_hi)

        def _plot_grey(
            ax: plt.Axes,
            vname: str,
            mask: np.ndarray,
        ) -> None:
            """Plot stage1 reference data as light-grey background trace.

            Skipped when the physical units differ between stage1 and stage2/3
            (e.g. conductivity in mS/cm in stage1 vs S/m after normalisation)
            because the values would be on an incompatible scale.

            ``_pitch_roll_combo`` is a synthetic panel name; pitch and roll are
            read directly from ds1 by their real variable names.
            """
            if ds1 is None or time1 is None:
                return
            if vname == "_pitch_roll_combo":
                for _v in ("pitch", "roll"):
                    if _v not in ds1.data_vars:
                        continue
                    _t1 = time1[mask]
                    _d1 = ds1[_v].values.astype(float)[mask]
                    if len(_t1) < 2:
                        continue
                    ax.plot(_t1, _d1, color="#cccccc", lw=0.5, zorder=0)
                    ax.scatter(_t1, _d1, s=2, color="#cccccc", zorder=0, linewidths=0)
                return
            if vname not in ds1.data_vars:
                return
            _u1 = ds1[vname].attrs.get("units", "").strip()
            _u2 = ds[vname].attrs.get("units", "").strip()
            if _u1 and _u2 and _u1 != _u2:
                return
            data1 = ds1[vname].values.astype(float)
            t1, d1 = time1[mask], data1[mask]
            if len(t1) < 2:
                return
            ax.plot(t1, d1, color="#cccccc", lw=0.5, zorder=0)
            ax.scatter(t1, d1, s=2, color="#cccccc", zorder=0, linewidths=0)

        for row_i, (vname, label, color, invert) in enumerate(panels):
            ax_l = fig.add_subplot(gs[row_i, 0])
            ax_r = fig.add_subplot(gs[row_i, 1], sharey=ax_l)
            # Grey stage1 background drawn before the coloured primary data.
            if start_mask1 is not None:
                _plot_grey(ax_l, vname, start_mask1)
            if end_mask1 is not None:
                _plot_grey(ax_r, vname, end_mask1)
            _plot_panel(ax_l, vname, label, color, invert, start_mask, 0)
            _plot_panel(ax_r, vname, label, color, invert, end_mask, 1)
            # For inverted variables (pressure), set the shared y-axis from the
            # combined range of both panels so neither panel clips the other's data.
            # Use stage2/3 data only — stage1 bench pressure (≈ 0) must not expand
            # the y-axis and squish the deployment-depth view.
            if invert and vname in ds.data_vars:
                _d_both = np.concatenate(
                    [
                        ds[vname].values.astype(float)[start_mask],
                        ds[vname].values.astype(float)[end_mask],
                    ]
                )
                _d_both = _d_both[np.isfinite(_d_both)]
                if _d_both.size:
                    _lo_c, _hi_c = float(_d_both.min()), float(_d_both.max())
                    _pad_c = max((_hi_c - _lo_c) * 0.1, 0.5)
                    ax_l.set_ylim(_hi_c + _pad_c, _lo_c - _pad_c)
            # Explicit x-axis limits (from stage1 if available, else stage2/3).
            ax_l.set_xlim(_t_start_lo, _t_start_hi)
            ax_r.set_xlim(_t_end_lo, _t_end_hi)
            _draw_vlines(ax_l, _t_start_lo, _t_start_hi, row_i == 0)
            _draw_vlines(ax_r, _t_end_lo, _t_end_hi, row_i == 0)
            ax_r.yaxis.tick_right()
            ax_r.yaxis.set_label_position("right")
            if row_i == 0:
                plot_title(ax_l, f"First {hours} h")
                plot_title(ax_r, f"Last {hours} h")

        serial = (
            ds["serial_number"].item()
            if "serial_number" in ds
            else ds.attrs.get("serial_number", "?")
        )
        figure_title(
            fig,
            f"{instr_type.title()} s/n {serial} — deployment start / end",
        )
        return fig
    finally:
        ds.close()
        if ds1 is not None:
            ds1.close()


def draw_data_histogram(
    nc_path: Path,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Histogram of data values for each main variable; return a Figure.

    Each panel shows grey bars (all finite data) and blue bars (kept, not bad/missing),
    with QC range threshold lines overlaid.

    Parameters
    ----------
    nc_path : Path
        Path to a stage-3 NetCDF file.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None
        Figure, or None if no plottable variables are found.

    """
    import math

    import matplotlib.pyplot as plt
    import xarray as xr

    from .. import parameters as params

    with xr.open_dataset(nc_path, decode_timedelta=False) as ds:
        ds.load()

        panels = _instrument_panels(ds)
        _HIST_EXCLUDE = {"battery_voltage"}
        plot_panels: list = [
            (vn, lbl, False)
            for vn, lbl, *_ in panels
            if ds[vn].dims == ("time",) and vn not in _HIST_EXCLUDE
        ]
        # ADCP: append multi-dim velocity and percent_good panels (flattened).
        _ADCP_FLAT = [
            ("east_velocity", f"East vel. (all bins, {unit_label('m s-1')})"),
            ("north_velocity", f"North vel. (all bins, {unit_label('m s-1')})"),
            ("up_velocity", f"Up vel. (all bins, {unit_label('m s-1')})"),
            ("error_velocity", f"Error vel. (all bins, {unit_label('m s-1')})"),
            ("percent_good", "Pct good (all bins/beams, %)"),
        ]
        for vn, lbl in _ADCP_FLAT:
            if vn in ds.data_vars and ds[vn].values.ndim > 1:
                plot_panels.append((vn, lbl, True))  # True = flatten

        if not plot_panels:
            return None

        ncols = 3
        nrows = math.ceil(len(plot_panels) / ncols)
        fig, axs_grid = plt.subplots(
            nrows,
            ncols,
            figsize=(width_in, 2.5 * nrows),
            squeeze=False,
            sharey=True,
            layout="constrained",
        )
        axs = axs_grid.ravel()
        for k in range(len(plot_panels), len(axs)):
            axs[k].set_visible(False)

        for ax, (vname, ylabel, flatten) in zip(axs, plot_panels, strict=False):
            data = (
                ds[vname].values.astype(float).ravel()
                if flatten
                else ds[vname].values.astype(float)
            )

            qc_var = f"{vname}_qc"
            has_qc = qc_var in ds
            if has_qc:
                flags = (
                    ds[qc_var].values.astype(int).ravel()
                    if flatten
                    else ds[qc_var].values.astype(int)
                )
                kept_mask = np.isfinite(data) & ~np.isin(flags, [4, 9])
            else:
                kept_mask = np.isfinite(data)
            all_mask = np.isfinite(data)

            # percent_good has no _qc variable; annotate with ADCP thresholds
            pg_thresholds = None
            if vname == "percent_good":
                pg_thresholds = (
                    params.QC_ADCP["percent_good_bad"],
                    params.QC_ADCP["percent_good_suspect"],
                )
                # Treat bins below bad threshold as "removed" for the histogram
                kept_mask = all_mask & (data >= params.QC_ADCP["percent_good_bad"])

            all_data = data[all_mask]
            kept_data = data[kept_mask]
            if len(all_data) == 0:
                ax.set_ylabel(ylabel)
                ax.text(
                    0.5,
                    0.5,
                    "no data",
                    transform=ax.transAxes,
                    ha="center",
                    va="center",
                    color="#999",
                )
                continue

            # Compute shared bin edges from ALL data so both histograms are comparable
            if vname == "heading":
                bin_edges = np.linspace(0.0, 360.0, 81)
            elif vname == "percent_good":
                bin_edges = np.linspace(0.0, 100.0, 81)
            elif "velocity" in vname:
                _half = max(
                    abs(float(all_data.min())), abs(float(all_data.max())), 1e-4
                )
                bin_edges = np.linspace(-_half, _half, 81)
            else:
                bin_edges = 80

            # Grey: all finite data.  Kept bars use the variable's own line colour
            # (VAR_COLORS) so the distribution matches its time-series line, falling
            # back to the default blue for variables without a registered colour.
            _kept_color = params.var_color(vname)
            ax.hist(
                all_data,
                bins=bin_edges,
                color="#aaaaaa",
                alpha=0.6,
                edgecolor="none",
                zorder=1,
                label="all",
            )
            if len(kept_data) > 0:
                ax.hist(
                    kept_data,
                    bins=bin_edges,
                    color=_kept_color,
                    alpha=0.85,
                    edgecolor="none",
                    zorder=2,
                    label="kept",
                )
            ax.set_yscale("log")
            ax.set_xlabel(ylabel)
            if ax.get_subplotspec().is_first_col():
                ax.set_ylabel(r"$\log_{10}$(count)")

            s_min = s_max = f_min = f_max = None
            if has_qc:
                qattrs = ds[qc_var].attrs
                s_min = qattrs.get("qc_gross_range_suspect_min")
                s_max = qattrs.get("qc_gross_range_suspect_max")
                f_min = qattrs.get("qc_gross_range_fail_min")
                f_max = qattrs.get("qc_gross_range_fail_max")

            all_lo, all_hi = float(all_data.min()), float(all_data.max())
            pad = max(0.03 * (all_hi - all_lo), 1e-6)
            xlim_lo = (
                max(float(f_min), all_lo - pad) if f_min is not None else all_lo - pad
            )
            xlim_hi = (
                min(float(f_max), all_hi + pad) if f_max is not None else all_hi + pad
            )
            ax.set_xlim(xlim_lo, xlim_hi)

            if vname == "heading":
                ax.set_xlim(0.0, 360.0)
            elif vname == "percent_good":
                ax.set_xlim(0.0, 100.0)
            elif "velocity" in vname:
                _half = max(abs(all_lo), abs(all_hi), 1e-6)
                ax.set_xlim(-_half, _half)

            legend_handles, legend_labels = [], []
            threshold_lines = [
                (s_min, "#f39c12", "--", f"suspect min ({s_min})"),
                (s_max, "#f39c12", "--", f"suspect max ({s_max})"),
                (f_min, "#e74c3c", ":", f"fail min ({f_min})"),
                (f_max, "#e74c3c", ":", f"fail max ({f_max})"),
            ]
            if pg_thresholds is not None:
                pg_bad, pg_susp = pg_thresholds
                threshold_lines += [
                    (pg_bad, "#e74c3c", ":", f"bad < {pg_bad}%"),
                    (pg_susp, "#f39c12", "--", f"suspect < {pg_susp}%"),
                ]
            for xv, col, ls, lbl in threshold_lines:
                if xv is not None and xlim_lo <= float(xv) <= xlim_hi:
                    line = ax.axvline(
                        float(xv), color=col, linewidth=1.2, linestyle=ls, zorder=3
                    )
                    legend_handles.append(line)
                    legend_labels.append(lbl)
            if legend_handles:
                ax.legend(
                    legend_handles,
                    legend_labels,
                    loc="upper right",
                    ncol=2,
                    framealpha=0.8,
                )

            n_removed = int(np.sum(all_mask)) - int(np.sum(kept_mask))
            if n_removed > 0:
                ax.text(
                    0.98,
                    0.96,
                    f"{n_removed} removed (grey)",
                    transform=ax.transAxes,
                    ha="right",
                    va="top",
                    color="#e74c3c",
                )

        return fig


def draw_velocity_iqr_profile(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Percentile-profile figure for gridded ADCP velocity data; return a Figure.

    Three side-by-side panels, all with pressure (dbar) on the Y-axis (inverted,
    surface at top).  All velocity units are m s⁻¹.

    **Left — current speed** (always ≥ 0):
        Shaded percentile profile: outer band p2.5–p97.5 (95% range of the
        distribution); inner band IQR p25–p75; median p50.  Wide IQR at a given
        depth indicates high velocity variability (e.g. an eddy-active layer or a
        strong tidal signal); narrow IQR with a large median indicates a persistent
        mean flow.  ``current_speed`` is computed from ``sqrt(east² + north²)`` if
        not already present in the dataset.

    **Middle — east and north velocity** (can be negative):
        Median and IQR (p25/p50/p75) for each component.  East velocity in
        Okabe-Ito blue (#0072B2); north velocity in Okabe-Ito orange (#E69F00); both
        colours are distinguishable for common colour-vision deficiencies.
        Positive east = rightward facing north (geographic ENU after declination
        correction); positive north = toward True North.  A median near zero with
        large IQR suggests rotary motion (e.g. tides or near-inertial oscillations);
        a non-zero median indicates a mean current.

    **Right — count** (dimensionless):
        Number of non-NaN time steps at each pressure level.  Use this panel to assess
        data coverage before interpreting velocity statistics: pressure levels with very
        few records produce unreliable percentile estimates.

    Parameters
    ----------
    ds : xr.Dataset
        Gridded dataset with dimensions ``(time, pressure)`` containing at minimum one
        of ``current_speed``, ``east_velocity``, or ``north_velocity`` in m s⁻¹.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None
        Figure, or None if no velocity data are present.

    """
    import warnings

    import matplotlib.pyplot as plt
    import xarray as _xr

    # Ensure current_speed exists
    if "current_speed" not in ds.data_vars and all(
        v in ds.data_vars for v in ("east_velocity", "north_velocity")
    ):
        spd = np.sqrt(
            ds["east_velocity"].values ** 2 + ds["north_velocity"].values ** 2
        )
        ds = ds.assign(
            current_speed=_xr.DataArray(
                spd,
                dims=ds["east_velocity"].dims,
                coords=ds["east_velocity"].coords,
                attrs={"units": "m s-1", "long_name": "Current speed"},
            )
        )

    has_speed = "current_speed" in ds.data_vars
    has_horiz = all(v in ds.data_vars for v in ("east_velocity", "north_velocity"))
    if not has_speed and not has_horiz:
        return None

    pressure = ds["pressure"].values  # 1-D (n_levels,)

    # Count non-NaN values at each pressure level (from east_velocity, or speed)
    _count_ref = ds["east_velocity"].values if has_horiz else ds["current_speed"].values
    n_good = np.sum(np.isfinite(_count_ref), axis=0)  # (n_levels,)

    # Suppress levels with < 20 % coverage relative to the best-sampled level
    _max_good = n_good.max() if n_good.max() > 0 else 1
    coverage_mask = n_good >= 0.2 * _max_good

    # Okabe-Ito blue and orange — distinguishable for all common colour-vision deficiencies
    _C_EAST = "#0072B2"
    _C_NORTH = "#E69F00"

    n_panels = int(has_speed) + int(has_horiz) + 1  # +1 for count panel
    fig, axs = plt.subplots(
        1,
        n_panels,
        figsize=(width_in, 4.5),
        sharey=True,
        gridspec_kw={"width_ratios": [2] * (n_panels - 1) + [1]},
    )
    if n_panels == 1:
        axs = [axs]
    for _ax in axs:
        grid_despine(_ax)

    ax_iter = iter(axs[:-1])  # last panel reserved for count

    # ── Panel 1: current speed with 2.5/25/50/75/97.5 ────────────────────────
    if has_speed:
        ax = next(ax_iter)
        data = ds["current_speed"].values  # (time, pressure)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pressure levels
            p025 = np.nanpercentile(data, 2.5, axis=0)
            p25 = np.nanpercentile(data, 25, axis=0)
            p50 = np.nanpercentile(data, 50, axis=0)
            p75 = np.nanpercentile(data, 75, axis=0)
            p975 = np.nanpercentile(data, 97.5, axis=0)
        valid = np.isfinite(p50) & coverage_mask
        if valid.any():
            ax.fill_betweenx(
                pressure[valid],
                p025[valid],
                p975[valid],
                alpha=0.15,
                color="steelblue",
                label="2.5–97.5 %",
            )
            ax.fill_betweenx(
                pressure[valid],
                p25[valid],
                p75[valid],
                alpha=0.35,
                color="steelblue",
                label="IQR (25–75 %)",
            )
            ax.plot(
                p50[valid],
                pressure[valid],
                color="steelblue",
                linewidth=1.5,
                label="Median",
            )
            ax.plot(
                p025[valid],
                pressure[valid],
                color="steelblue",
                linewidth=0.6,
                linestyle=":",
            )
            ax.plot(
                p975[valid],
                pressure[valid],
                color="steelblue",
                linewidth=0.6,
                linestyle=":",
            )
            ax.set_xlim(left=0)
        ax.set_xlabel(f"Current speed ({unit_label('m s-1')})")
        grid_despine(ax)
        ax.legend(loc="best")

    # ── Panel 2: east + north on shared axes ─────────────────────────────────
    if has_horiz:
        ax = next(ax_iter)
        absmax = 0.0
        for varname, color, label in [
            ("east_velocity", _C_EAST, "East"),
            ("north_velocity", _C_NORTH, "North"),
        ]:
            data = ds[varname].values
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                p25 = np.nanpercentile(data, 25, axis=0)
                p50 = np.nanpercentile(data, 50, axis=0)
                p75 = np.nanpercentile(data, 75, axis=0)
            valid = np.isfinite(p50) & coverage_mask
            if not valid.any():
                continue
            ax.fill_betweenx(
                pressure[valid], p25[valid], p75[valid], alpha=0.25, color=color
            )
            ax.plot(
                p50[valid], pressure[valid], color=color, linewidth=1.5, label=label
            )
            ax.plot(
                p25[valid], pressure[valid], color=color, linewidth=0.6, linestyle="--"
            )
            ax.plot(
                p75[valid], pressure[valid], color=color, linewidth=0.6, linestyle="--"
            )
            _abs = np.nanmax(np.abs([p25[valid], p75[valid]]))
            if np.isfinite(_abs):
                absmax = max(absmax, _abs)
        ax.axvline(0, color="k", linewidth=0.5, linestyle="--", alpha=0.4)
        if absmax > 0:
            ax.set_xlim(-absmax * 1.15, absmax * 1.15)
        ax.set_xlabel(f"Velocity ({unit_label('m s-1')})")
        grid_despine(ax)
        ax.legend(loc="best")

    # ── Count panel (rightmost) ───────────────────────────────────────────────
    ax_count = axs[-1]
    ax_count.barh(
        pressure,
        n_good,
        height=np.diff(pressure).mean() * 0.8 if len(pressure) > 1 else 5,
        color="gray",
        alpha=0.6,
    )
    ax_count.set_xlabel("N good")
    grid_despine(ax_count)

    axs[0].set_ylabel(f"Pressure ({unit_label('dbar')})")
    axs[0].invert_yaxis()  # shared y — invert once only

    # Mark bottom depth if available (waterdepth global attr, dbar ≈ m seawater)
    try:
        _wd = float(ds.attrs.get("waterdepth", ""))
        if np.isfinite(_wd) and _wd > 0:
            for _ax in axs:
                _ax.axhline(_wd, color="k", linewidth=2.0, linestyle="-", zorder=5)
    except (ValueError, TypeError):
        pass

    return fig


def draw_instrument_spacing(
    ds: xr.Dataset, *, width_in: float = report_tokens.W_THIRD
) -> plt.Figure | None:
    """Histogram of the pressure spacing between adjacent instrument levels.

    Levels are ordered by their median pressure; at each time step the pressure
    difference between each adjacent pair is counted. Pairs under 2 dbar apart
    are left out as co-located.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack with ``pressure`` on ``(time, N_LEVELS)``.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get a third of the content width.

    Returns
    -------
    matplotlib.figure.Figure or None
        ``None`` when there is no pressure, fewer than two levels, or no pair
        2 dbar or more apart.
    """
    import matplotlib.pyplot as plt

    from ..config.unit_tables import warn_if_not_canonical

    if "pressure" not in ds.data_vars or ds.sizes.get("N_LEVELS", 0) < 2:
        return None
    # The spacing axis is labelled dbar; it is a difference of pressures.
    warn_if_not_canonical(ds["pressure"], "dbar", quantity="instrument spacing (dbar)")
    pressure = ds["pressure"].values  # (time, N_LEVELS)
    with np.errstate(all="ignore"):
        median = np.nanmedian(pressure, axis=0)  # NaN when a level is fully gapped
    ordered = pressure[:, np.argsort(median)]
    spacing = np.diff(ordered, axis=1).ravel()
    spacing = spacing[np.isfinite(spacing) & (spacing >= 2.0)]
    if spacing.size == 0:
        return None
    fig, ax = plt.subplots(figsize=(width_in, 3))
    ax.hist(spacing, bins=60, color="steelblue", edgecolor="white")
    ax.set_xlabel("Instrument spacing (dbar)")
    ax.set_ylabel("Count (instrument pair × time step)")
    plot_title(ax, "Instrument spacing")
    return fig
