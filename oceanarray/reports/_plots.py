"""All figure-generating functions for the mooring report package."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import matplotlib.pyplot as plt
    import xarray as xr

import numpy as np

from ..config.unit_tables import var_unit_label
from ..plotters.current import (
    draw_adcp_hodograph,
    draw_adcp_rose,
    draw_adcp_velocity,
    draw_grid_hodograph,
    draw_grid_rose,
    draw_grid_trajectory,
    draw_instrument_rose,
    draw_rose_grid,
)
from ..plotters.diagnostic import (
    _CANONICAL_PANELS,  # noqa: F401  (re-exported for callers/tests)
    _COMPACT_PANEL_HEIGHT,
    _COMPACT_PANEL_VARS,
    _PANEL_HEIGHT,
    _instrument_panels,
    draw_data_histogram,
    draw_instrument_spacing,
    draw_velocity_iqr_profile,
    draw_windows,
)
from ..plotters.helpers import QC_MARKER as _QC_MARKER
from ..plotters.helpers import (  # noqa: F401
    _rose_ax,
    grid_despine,
)
from ..plotters.hydrography import (
    draw_isopycnal_coverage,
    draw_isopycnal_ts_fig,
    draw_overflow_temperature_fig,
)
from ..plotters.primitives import (
    date_axis,
    hodograph_panel,
)
from ..plotters.spectrum import (
    draw_grid_rotary_spectrum,
    draw_spectrum,
    draw_wavelet,
)
from ..plotters.timeseries import (
    draw_analog_timeseries,
    draw_grid_fig,
    draw_grid_hydro,
    draw_grid_n2,
    draw_grid_sigma,
    draw_grid_timeseries,
    draw_grid_velocity_stacked,
    draw_stack_timeseries,
)
from ..plotters.ts import (
    draw_grid_ts_diagram,
    draw_stack_ts_diagram,
    draw_ts_diagram,
)
from ._figdebug import render_b64
from ._html_helpers import _QC_LABELS
from ._slots import render as render_slot
from ._vendored import design_tokens as report_tokens
from ._vendored.figure_png import source_for

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Figure encoder + silent-failure guard
# ---------------------------------------------------------------------------

#: ``render_b64`` / ``_fig_to_base64`` live in the shared encoder
#: (:mod:`._encode`) — the single choke point applying the report mplstyle,
#: dpi, palette quantization and error policy.  Error policy is
#: :data:`report_tokens.RAISE_ON_PLOT_ERROR`; honour the legacy
#: ``OCEANARRAY_RAISE_ON_PLOT_ERROR`` env var by setting that flag here at import
#: (``OCEANARRAY_RAISE_ON_PLOT_ERROR=1 oceanarray report ...``).
if os.environ.get("OCEANARRAY_RAISE_ON_PLOT_ERROR", "").lower() in ("1", "true", "yes"):
    report_tokens.RAISE_ON_PLOT_ERROR = True


# ---------------------------------------------------------------------------
# Aquadopp quick-look
# ---------------------------------------------------------------------------


def _plot_aquadopp_quick(ds: xr.Dataset) -> plt.Figure:
    """Quick-look figure for Aquadopp; handles beam and ENU naming, lowercase attitude."""
    import matplotlib.pyplot as plt

    from .. import parameters as params

    panels: list[tuple] = []

    enu = [
        v
        for v in ("east_velocity", "north_velocity", "up_velocity")
        if v in ds.data_vars
    ]
    if enu:
        for vname, color in zip(
            enu, ["tab:blue", "tab:orange", "tab:cyan"], strict=False
        ):
            label = vname.replace("_velocity", " vel.").replace("_", " ").title()
            panels.append((vname, label, color, False))
    else:
        for i, color in enumerate(["tab:blue", "tab:orange", "tab:cyan"], 1):
            vname = f"velocity_beam{i}"
            if vname in ds.data_vars:
                panels.append((vname, f"Beam {i} vel.", color, False))

    pvar = next((v for v in ("pressure", "pressure_1") if v in ds.data_vars), None)
    if pvar:
        panels.append((pvar, "Pressure", "tab:green", True))

    for vname, label in (("pitch", "Pitch"), ("roll", "Roll")):
        if vname in ds.data_vars:
            panels.append((vname, label, "tab:purple", False))

    nrows = max(len(panels), 1)
    with plt.style.context(str(params.MPLSTYLE)):
        fig, axs = plt.subplots(
            nrows, 1, figsize=(report_tokens.W_FULL, 3 * nrows), sharex=True
        )
        if nrows == 1:
            axs = [axs]

        for ax, (vname, label, color, invert) in zip(axs, panels, strict=False):
            ax.plot(ds["time"], ds[vname], color=color, linewidth=0.5)
            if "velocity" in vname:
                ax.axhline(0, color="k", linewidth=0.5, linestyle="--")
            u = var_unit_label(ds[vname])
            ax.set_ylabel(f"{label} ({u})" if u else label)
            grid_despine(ax)
            if invert:
                vmin = float(ds[vname].min())
                vmax = float(ds[vname].max())
                pad = max((vmax - vmin) * 0.1, 0.5)
                ax.set_ylim(vmax + pad, vmin - pad)

        serial = (
            ds["serial_number"].item()
            if "serial_number" in ds
            else ds.attrs.get("serial_number", "?")
        )
        depth = f"{ds['InstrDepth'].item():.0f} m" if "InstrDepth" in ds else "?"
        axs[0].set_title(f"Aquadopp s/n: {serial}  |  Target depth: {depth}")
        axs[-1].set_xlabel("Date")
        date_axis(axs[-1])
        plt.tight_layout()
        return fig


# Variables plotted with both a line and individual dots so that sparse or
# near-zero samples (e.g. turbidity = 0 NTU between events) are visible.
_DOT_LINE_VARS: frozenset = frozenset({"turbidity"})


# _instrument_panels is imported from plotters.diagnostic (single definition) —
# it was previously duplicated here (U8).


# ---------------------------------------------------------------------------
# Full time-series figure
# ---------------------------------------------------------------------------


# Max panels per instrument time-series figure.  A tall figure cannot split
# across PDF pages, so we paginate into several figures of at most this many
# panels each (see _build_figs_from_ds).
_MAX_TS_PANELS = 5


def _augment_tilt(ds: xr.Dataset) -> xr.Dataset:
    """Return *ds* with a derived ``tilt`` variable added from pitch/roll.

    ``tilt = arccos(cos(pitch)·cos(roll))`` in degrees from vertical.  A no-op
    when neither pitch nor roll is present.  Idempotent (re-running overwrites
    the derived variable).
    """
    _has_pitch = "pitch" in ds.data_vars
    _has_roll = "roll" in ds.data_vars
    if not (_has_pitch or _has_roll):
        return ds
    _n = ds.sizes["time"]
    _pitch_r = (
        np.radians(ds["pitch"].values.astype(float)) if _has_pitch else np.zeros(_n)
    )
    _roll_r = np.radians(ds["roll"].values.astype(float)) if _has_roll else np.zeros(_n)
    _cos_t = np.cos(_pitch_r) * np.cos(_roll_r)
    _tilt = np.degrees(np.arccos(np.clip(_cos_t, -1.0, 1.0)))
    if _has_pitch:
        _tilt[~np.isfinite(ds["pitch"].values.astype(float))] = np.nan
    if _has_roll:
        _tilt[~np.isfinite(ds["roll"].values.astype(float))] = np.nan
    import xarray as _xr

    return ds.assign(
        tilt=_xr.Variable(
            "time",
            _tilt,
            {"units": "degrees", "long_name": "Instrument tilt from vertical"},
        )
    )


def _build_fig_from_ds(
    ds: xr.Dataset,
    instr_type: str,
    show_qc: bool = True,
    title_suffix: str = "",
    panels: list | None = None,
) -> plt.Figure | None:
    """Render instrument panels from an already-loaded xarray Dataset.

    When *panels* is given, only those panels are drawn (used to paginate a tall
    instrument figure via :func:`_build_figs_from_ds`); otherwise every panel for
    the instrument is drawn on one figure.
    """
    import matplotlib.pyplot as plt

    from .. import parameters as params

    ds = _augment_tilt(ds)
    if panels is None:
        panels = _instrument_panels(ds, combine_pitch_roll=True)
    if not panels:
        return None

    with plt.style.context(str(params.MPLSTYLE)):
        nrows = len(panels)
        height_ratios = [
            _COMPACT_PANEL_HEIGHT if vname in _COMPACT_PANEL_VARS else _PANEL_HEIGHT
            for vname, *_ in panels
        ]
        fig, axs = plt.subplots(
            nrows,
            1,
            figsize=(report_tokens.W_FULL, sum(height_ratios)),
            gridspec_kw={"height_ratios": height_ratios},
            sharex=True,
        )
        if nrows == 1:
            axs = [axs]

        time = ds["time"].values

        for ax, (vname, label, color, invert) in zip(axs, panels, strict=False):
            grid_despine(ax)
            if vname == "_pitch_roll_combo":
                _suspect_t = float(ds.attrs.get("tilt_suspect_threshold", 20.0))
                _fail_t = float(ds.attrs.get("tilt_fail_threshold", 30.0))
                if "pitch" in ds.data_vars:
                    ax.plot(
                        time,
                        ds["pitch"].values.astype(float),
                        color="tab:purple",
                        lw=0.6,
                        label="pitch",
                        zorder=1,
                    )
                if "roll" in ds.data_vars:
                    ax.plot(
                        time,
                        ds["roll"].values.astype(float),
                        color="#8B4513",
                        lw=0.6,
                        label="roll",
                        zorder=1,
                    )
                for _val, _c, _ls in [
                    (_suspect_t, "tab:orange", "--"),
                    (-_suspect_t, "tab:orange", "--"),
                    (_fail_t, "tab:red", ":"),
                    (-_fail_t, "tab:red", ":"),
                ]:
                    ax.axhline(_val, color=_c, lw=0.8, ls=_ls, zorder=0)
                ax.set_ylabel(label)
                ax.legend(loc="upper right", framealpha=0.8)
                continue

            data = ds[vname].values.astype(float)
            ax.plot(time, data, color=color, linewidth=0.6, zorder=1)
            if vname in _DOT_LINE_VARS:
                ax.plot(
                    time, data, ".", color=color, markersize=2, linewidth=0, zorder=2
                )
            if "velocity" in vname and not invert:
                ax.axhline(0, color="k", linewidth=0.4, linestyle="--", zorder=0)
            if vname == "tilt":
                _suspect_t = float(ds.attrs.get("tilt_suspect_threshold", 20.0))
                _fail_t = float(ds.attrs.get("tilt_fail_threshold", 30.0))
                ax.axhline(
                    _suspect_t,
                    color="tab:orange",
                    lw=0.9,
                    ls="--",
                    label=f"suspect {_suspect_t:.0f}°",
                    zorder=2,
                )
                ax.axhline(
                    _fail_t,
                    color="tab:red",
                    lw=0.9,
                    ls="--",
                    label=f"fail {_fail_t:.0f}°",
                    zorder=2,
                )
                ax.legend(loc="upper right", framealpha=0.8)
            ax.set_ylabel(label)
            if invert:
                vmin, vmax = float(np.nanmin(data)), float(np.nanmax(data))
                pad = max((vmax - vmin) * 0.1, 0.5)
                ax.set_ylim(vmax + pad, vmin - pad)
            elif vname == "heading":
                ax.set_ylim(0.0, 360.0)
            elif "velocity" in vname:
                _half = max(
                    abs(float(np.nanmax(data))), abs(float(np.nanmin(data))), 1e-6
                )
                ax.set_ylim(-_half, _half)

            qc_var = f"{vname}_qc"
            if show_qc and qc_var in ds.data_vars:
                flags = ds[qc_var].values.astype(int)
                for fval, mkw in _QC_MARKER.items():
                    mask = flags == fval
                    if mask.any():
                        ax.scatter(
                            time[mask], data[mask], label=_QC_LABELS[fval], **mkw
                        )
                handles, labels_list = ax.get_legend_handles_labels()
                if handles:
                    ax.legend(
                        handles,
                        labels_list,
                        loc="upper right",
                        ncol=3,
                        framealpha=0.8,
                    )

            # Twin right y-axis: O2 % saturation alongside dissolved oxygen concentration.
            # Uses in-situ seawater density for the µmol/L → µmol/kg conversion (see
            # _derive_oxygen_saturation in stage3.py); error vs freshwater density ~2.5%.
            if vname == "dissolved_oxygen" and "oxygen_saturation_pct" in ds.data_vars:
                ax2 = ax.twinx()
                sat = ds["oxygen_saturation_pct"].values.astype(float)
                ax2.plot(
                    time, sat, color="darkorange", linewidth=0.6, alpha=0.75, zorder=0
                )
                ax2.set_ylabel("O₂ sat. (%)", color="darkorange")
                ax2.tick_params(axis="y", labelcolor="darkorange")
                ax2.axhline(100.0, color="darkorange", lw=0.5, ls="--", alpha=0.4)

        serial = (
            ds["serial_number"].item()
            if "serial_number" in ds
            else ds.attrs.get("serial_number", "?")
        )
        depth = f"{ds['InstrDepth'].item():.0f} m" if "InstrDepth" in ds else "?"
        title = f"{instr_type.title()} s/n: {serial}  |  Target depth: {depth}"
        if title_suffix:
            title += f"  [{title_suffix}]"
        axs[0].set_title(title)

        axs[-1].set_xlabel("Date")
        date_axis(axs[-1])
        plt.tight_layout()
        return fig


def _make_instrument_fig(
    nc_path: Path, instr_type: str, show_qc: bool = True
) -> list[str]:
    """Instrument data time series with optional QC markers, paginated.

    Returns a *list* of base64 PNGs: the instrument's panels are split into
    figures of at most ``_MAX_TS_PANELS`` panels each, so a tall instrument time
    series paginates into successive images instead of overflowing one PDF page.
    Empty list if the instrument has no plottable panels.
    """
    import xarray as xr

    ds = xr.open_dataset(nc_path, decode_timedelta=False).load()
    try:
        ds = _augment_tilt(ds)
        panels = _instrument_panels(ds, combine_pitch_roll=True)
        if not panels:
            return []
        images: list[str] = []
        for i in range(0, len(panels), _MAX_TS_PANELS):
            chunk = panels[i : i + _MAX_TS_PANELS]
            b64 = render_b64(
                lambda c=chunk: _build_fig_from_ds(
                    ds, instr_type, show_qc=show_qc, panels=c
                ),
                optional=True,
                source=source_for(_build_fig_from_ds),
            )
            if b64:
                images.append(b64)
        return images
    finally:
        ds.close()


def _make_windows_fig(
    nc_path: Path,
    instr_type: str,
    hours: int = 6,
    show_qc: bool = True,
    vlines: list | None = None,
    stage1_nc: Path | None = None,
) -> list[str]:
    """Return base64 PNGs: combined start + end window figure, paginated.

    Returns a *list* of base64 PNGs: the instrument's panels are split into
    figures of at most ``_MAX_TS_PANELS`` rows each, so a tall start/end window
    figure paginates into successive images instead of overflowing one PDF page.
    Each row is a half-width start panel beside a half-width end panel.  Empty
    list if the instrument has no plottable panels.
    """
    import xarray as xr

    ds = xr.open_dataset(nc_path, decode_timedelta=False).load()
    try:
        panels = _instrument_panels(ds, combine_pitch_roll=True)
    finally:
        ds.close()
    if not panels:
        return []
    images: list[str] = []
    for i in range(0, len(panels), _MAX_TS_PANELS):
        chunk = panels[i : i + _MAX_TS_PANELS]
        b64 = render_b64(
            draw_windows,
            nc_path,
            instr_type,
            hours,
            show_qc,
            vlines,
            stage1_nc,
            panels=chunk,
            optional=True,
        )
        if b64:
            images.append(b64)
    return images


def _make_data_histogram(nc_path: Path) -> str | None:
    """Return base64 PNG: histogram of data values with QC range threshold lines."""
    return render_b64(draw_data_histogram, nc_path, optional=True)


def _make_ts_diagram(nc_path: Path) -> str | None:
    """Return base64 PNG: T-S diagram (scatter by pressure, heatmap, optional O2 panel)."""
    return render_b64(draw_ts_diagram, nc_path, optional=True)


def _make_grid_fig_b64(
    da: xr.DataArray,
    title: str,
    units: str,
    cmap: str,
    style: str = "pcolormesh",
    contour_levels: list | None = None,
    symmetric: bool = False,
    vmin: float | None = None,
    vmax: float | None = None,
) -> str | None:
    """Render a grid figure from *da* (dims time × pressure); return base64 PNG or None."""
    return render_slot(
        draw_grid_fig,
        da,
        title,
        units,
        cmap,
        style=style,
        contour_levels=contour_levels,
        symmetric=symmetric,
        vmin=vmin,
        vmax=vmax,
    )


def _make_grid_sigma_b64(ds: xr.Dataset) -> str | None:
    """Stacked sigma0 pcolormesh panel(s) for the stratification section."""
    return render_slot(draw_grid_sigma, ds, optional=True)


def _make_grid_hydro_b64(
    ds: xr.Dataset,
    var_bounds: dict | None = None,
) -> str | None:
    """Return base64 PNG: stacked temperature / salinity pcolormesh panels."""
    return render_slot(draw_grid_hydro, ds, var_bounds, optional=True)


def _make_grid_velocity_stacked_b64(ds: xr.Dataset) -> str | None:
    """Stacked east / north / up velocity pcolormesh panels for the grid report."""
    return render_slot(draw_grid_velocity_stacked, ds, optional=True)


def _make_spectrum_fig_b64(
    da_temp: xr.DataArray,
    dt_seconds: float,
    lat: float = 0.0,
    hf_segment_days: float = 1.0,
    hf_x_max_days: float = 3.0,
) -> str | None:
    """Return base64 PNG: two-panel Welch PSD of gridded temperature."""
    return render_b64(
        draw_spectrum,
        da_temp,
        dt_seconds,
        lat,
        hf_segment_days,
        hf_x_max_days,
        optional=True,
    )


def _make_wavelet_fig_b64(
    da_temp: xr.DataArray,
    dt_seconds: float,
    wavelet: str = "morlet",
) -> str | None:
    """Return base64 PNG: continuous wavelet transform scalogram for gridded temperature."""
    return render_b64(draw_wavelet, da_temp, dt_seconds, wavelet, optional=True)


def _make_grid_rotary_spectrum_b64(
    ds: xr.Dataset,
    lat: float = 0.0,
) -> str | None:
    """Return base64 PNG: two-panel rotary velocity spectrum for the grid report."""
    return render_b64(draw_grid_rotary_spectrum, ds, lat, optional=True)


def _make_stack_ts_diagram(ds: xr.Dataset) -> str | None:
    """Return base64 PNG: T-S diagram for a stacked dataset."""
    return render_b64(draw_stack_ts_diagram, ds, optional=True)


# ---------------------------------------------------------------------------
# Rose diagrams
# ---------------------------------------------------------------------------


def _make_instrument_rose_b64(nc_path: Path) -> str | None:
    """Rose diagram grid for a single Aquadopp instrument."""
    return render_b64(draw_instrument_rose, nc_path, optional=True)


def _make_grid_ts_diagram(ds: xr.Dataset, n_bins: int = 60) -> tuple[str | None, dict]:
    """Return (b64_str_or_None, bounds_dict): T-S diagram for gridded mooring data."""
    ts_bounds: dict = {}

    def _draw(*, width_in: float = report_tokens.W_FULL) -> plt.Figure | None:
        result = draw_grid_ts_diagram(ds, n_bins, width_in=width_in)
        if result is None:
            return None
        nonlocal ts_bounds
        fig, ts_bounds = result
        return fig

    # Displayed at half width (template slot-half); rendering at that width keeps
    # the PNG px == display px.  With O₂ the diagram gains a panel but the page
    # still shows it at half — same as before U0.2, now without the oversample.
    return (
        render_slot(
            _draw, slot="half", optional=True, source=source_for(draw_grid_ts_diagram)
        ),
        ts_bounds,
    )


def _make_velocity_iqr_profile_b64(ds: xr.Dataset) -> str | None:
    """Return base64 PNG: percentile-profile figure for gridded ADCP velocity data."""
    return render_b64(draw_velocity_iqr_profile, ds, optional=True)


def _make_grid_n2_b64(ds: xr.Dataset, lat: float = 0.0) -> str | None:
    """Compute and plot buoyancy frequency squared N² on the pressure-time grid."""
    return render_slot(draw_grid_n2, ds, lat, optional=True)


def _make_rose_grid_b64(
    ds: xr.Dataset,
    serial_list: list,
) -> tuple[str | None, int]:
    """Return (b64_png, n_panels): grid of current roses for ENU velocity data."""
    n_panels: int = 0

    def _draw() -> plt.Figure | None:
        result = draw_rose_grid(ds, serial_list)
        if result is None:
            return None
        nonlocal n_panels
        fig, n_panels = result
        return fig

    return render_b64(_draw, optional=True, source=source_for(draw_rose_grid)), n_panels


def _make_grid_rose_b64(ds: xr.Dataset, max_roses: int = 4) -> str | None:
    """Grid of current roses, one per pressure level, for the grid report."""
    return render_b64(draw_grid_rose, ds, max_roses, optional=True)


def _make_grid_trajectory_b64(ds: xr.Dataset) -> str | None:
    """Pseudo-Lagrangian trajectory by pressure level for the grid report."""
    return render_slot(draw_grid_trajectory, ds, slot="half", optional=True)


def _make_grid_timeseries_b64(ds: xr.Dataset) -> str | None:
    """Return base64 PNG: velocity time series at depth of maximum mean speed."""
    return render_b64(draw_grid_timeseries, ds, optional=True)


def _make_isopycnal_ts_fig_b64(ds_iso: xr.Dataset) -> str | None:
    """Return base64 PNG: isopycnal height-above-seabed time series."""
    return render_b64(draw_isopycnal_ts_fig, ds_iso, optional=True)


def _make_isopycnal_coverage_fig_b64(ds: xr.Dataset) -> str | None:
    """Return base64 PNG: three-panel isopycnal diagnostic."""
    return render_b64(draw_isopycnal_coverage, ds, optional=True)


def _make_overflow_temperature_fig_b64(ds: xr.Dataset) -> str | None:
    """Return base64 PNG: temperature time series at ~100 m above the seabed."""
    return render_b64(draw_overflow_temperature_fig, ds, optional=True)


# ---------------------------------------------------------------------------
# Aquadopp trajectory and speed distribution (Tier-3 wrappers)
# Delegates to oceanarray.plotters.current (Tier-2 domain wrappers).
# ---------------------------------------------------------------------------


def _make_temperature_trajectory(nc_path: str) -> str | None:
    """Lagrangian trajectory coloured by temperature, for Aquadopp instrument page."""
    import xarray as xr

    from oceanarray.plotters.current import plot_temperature_trajectory

    def _draw() -> plt.Figure:
        with xr.open_dataset(nc_path) as ds:
            return plot_temperature_trajectory(ds)

    return render_b64(
        _draw, optional=True, source=source_for(plot_temperature_trajectory)
    )


def _make_speed_boxplot(nc_path: str) -> str | None:
    """Speed boxplot with percentile statistics, for Aquadopp instrument page."""
    import xarray as xr

    from oceanarray.plotters.current import plot_speed_boxplot

    def _draw(*, width_in: float = report_tokens.W_QUARTER) -> plt.Figure:
        with xr.open_dataset(nc_path) as ds:
            return plot_speed_boxplot(ds, width_in=width_in)

    return render_slot(
        _draw, slot="quarter", optional=True, source=source_for(plot_speed_boxplot)
    )


def _make_hodograph_b64(nc_path: str) -> str | None:
    """Two-panel hodograph (raw + eddy-only) for Aquadopp instrument page.

    Always returns a base64 PNG — a placeholder image is rendered when
    east/north velocities are absent so the section is never silently omitted.
    Returns None only on unrecoverable file errors.
    """
    import xarray as xr

    from oceanarray.plotters.current import plot_hodograph

    def _draw() -> plt.Figure:
        with xr.open_dataset(nc_path) as ds:
            return plot_hodograph(ds)

    return render_b64(_draw, source=source_for(plot_hodograph))


def _make_multi_aquadopp_trajectories(ds: xr.Dataset) -> str | None:
    """Multi-instrument Aquadopp trajectory plot coloured by temperature, for stack page.

    Takes an already-loaded xarray.Dataset (not a path) since the stack report
    has the dataset in memory when it calls this function.
    """
    from oceanarray.plotters.current import plot_multi_aquadopp_trajectories

    return render_slot(plot_multi_aquadopp_trajectories, ds, slot="half", optional=True)


def _make_stack_timeseries_b64(
    ds: xr.Dataset,
    rows: list[tuple[str, str, bool, set[str] | None]],
    *,
    color_var: str,
    dot_overlay: bool = False,
) -> str | None:
    """Stack time series (one row per variable, one line per level), full width, for the stack page."""
    return render_slot(
        draw_stack_timeseries,
        ds,
        rows,
        color_var=color_var,
        dot_overlay=dot_overlay,
        optional=True,
    )


def _make_aquadopp_tilt_panels(ds: xr.Dataset, step: int = 1) -> str | None:
    """|pitch|, |roll| and pressure tilt per Aquadopp, full width, for the stack page."""
    from oceanarray.plotters.current import draw_aquadopp_tilt_panels

    return render_slot(draw_aquadopp_tilt_panels, ds, step=step, optional=True)


def _make_instrument_spacing_b64(ds: xr.Dataset) -> str | None:
    """Adjacent-instrument pressure spacing histogram, a third wide, for the stack page."""
    return render_slot(draw_instrument_spacing, ds, slot="third", optional=True)


def _make_aquadopp_speed_profile(ds: xr.Dataset) -> str | None:
    """Horizontal speed boxplots per Aquadopp positioned by HAB, for stack page.

    Takes an already-loaded xarray.Dataset (not a path).
    """
    from oceanarray.plotters.current import plot_aquadopp_speed_profile

    return render_slot(plot_aquadopp_speed_profile, ds, slot="half", optional=True)


def _make_adcp_trajectories_b64(ds: xr.Dataset) -> str | None:
    """Per-bin ADCP particle trajectories coloured by HAB, for stack page.

    Takes an already-loaded xarray.Dataset (not a path).
    """
    from oceanarray.plotters.current import plot_adcp_trajectories

    return render_slot(plot_adcp_trajectories, ds, slot="half", optional=True)


def _make_adcp_velocity_b64(nc_path: str) -> str | None:
    """Return base64 PNG: stacked colour panels for ADCP per-instrument report."""
    return render_b64(draw_adcp_velocity, nc_path, optional=True)


def _draw_hodograph_pair(
    ax_raw: Any,
    ax_eddy: Any,
    east_1d: np.ndarray,
    north_1d: np.ndarray,
    label: str,
    smooth_hours: float,
    lp_days: float,
    units: str,
    dt_s: float,
) -> Any:
    """Draw one row (raw + eddy panels) of a hodograph figure onto two Axes.

    Computes a 2-D density heatmap (hexbin) of east vs north velocity with a
    thin downsampled trajectory line overlay for temporal context.  Start and
    end positions are marked with distinct markers.

    Parameters
    ----------
    ax_raw : matplotlib.axes.Axes
        Left panel — Tukey-smoothed raw east vs north velocity density.
    ax_eddy : matplotlib.axes.Axes
        Right panel — eddy component (LP mean removed, then Tukey smoothed).
    east_1d : np.ndarray
        1-D eastward velocity timeseries (may contain NaN).
    north_1d : np.ndarray
        1-D northward velocity timeseries (may contain NaN).
    label : str
        Short bin description used in the subplot title, e.g. ``"430 m range"``.
    smooth_hours : float
        Tukey window half-width in hours for the raw panel.
    lp_days : float
        Rolling-mean low-pass window in days for the eddy panel.
    units : str
        Velocity units string, e.g. ``"m s⁻¹"``.
    dt_s : float
        Sample interval in seconds.

    Returns
    -------
    matplotlib.cm.ScalarMappable or None
        The shared 0->1 plasma time mapping (for one shared colorbar), or None if
        neither panel had enough finite data to draw.

    """
    import pandas as pd

    from oceanarray.plotters.helpers import tukey_smooth

    smooth_n = max(3, int(round(smooth_hours * 3600.0 / dt_s)))
    lp_n = max(3, int(round(lp_days * 86400.0 / dt_s)))

    e_sm = tukey_smooth(east_1d, smooth_n)
    n_sm = tukey_smooth(north_1d, smooth_n)

    e_lp = (
        pd.Series(east_1d)
        .rolling(window=lp_n, min_periods=1, center=True)
        .mean()
        .values
    )
    n_lp = (
        pd.Series(north_1d)
        .rolling(window=lp_n, min_periods=1, center=True)
        .mean()
        .values
    )
    e_eddy = tukey_smooth(east_1d - e_lp, smooth_n)
    n_eddy = tukey_smooth(north_1d - n_lp, smooth_n)

    # Fractional time 0→1 for colour mapping; matches east_1d length
    t_frac = np.linspace(0.0, 1.0, len(east_1d))

    def _draw(ax: Any, e: np.ndarray, n: np.ndarray, title: str) -> Any:
        mask = np.isfinite(e) & np.isfinite(n)
        if mask.sum() < 2:
            ax.text(
                0.5, 0.5, "No data", transform=ax.transAxes, ha="center", va="center"
            )
            ax.set_title(title)
            return None
        return hodograph_panel(ax, e[mask], n[mask], t_frac[mask], title, units)

    sm_raw = _draw(ax_raw, e_sm, n_sm, f"{label} — raw ({smooth_hours:.0f}-h smoothed)")
    sm_eddy = _draw(
        ax_eddy, e_eddy, n_eddy, f"{label} — eddy ({lp_days:.0f}-day LP removed)"
    )
    return sm_raw or sm_eddy


def _make_adcp_rose_b64(nc_path: str) -> str | None:
    """Return base64 PNG: ADCP current rose (depth-average + percentile bins)."""
    return render_b64(draw_adcp_rose, nc_path, optional=True)


def _make_adcp_hodograph_b64(
    nc_path: str, lp_days: float = 4.0, smooth_hours: float = 24.0
) -> str | None:
    """Return base64 PNG: two-depth hodograph for an ADCP per-instrument report."""
    return render_b64(
        draw_adcp_hodograph, nc_path, lp_days, smooth_hours, optional=True
    )


def _make_grid_hodograph_b64(ds: xr.Dataset, smooth_hours: float = 24.0) -> str | None:
    """Return base64 PNG: two-depth hodograph for the grid report."""
    return render_b64(draw_grid_hodograph, ds, smooth_hours, optional=True)


def _make_analog_timeseries(nc_path: Path, analog_vars: list[str]) -> str | None:
    """Full-record time series for analog channel variables, one panel per variable.

    Only generates a plot when *analog_vars* is non-empty (caller should check
    via nc_meta['analog_vars'] before calling).  Returns None on any error or if
    the dataset lacks a time dimension.
    """
    if not analog_vars:
        return None
    return render_b64(draw_analog_timeseries, nc_path, analog_vars, optional=True)


def _make_knockdown_pressure_b64(ds: xr.Dataset) -> str | None:
    """IQR of measured pressure vs. nominal pressure, for the stack report.

    Thin Tier-3 wrapper around
    ``plotters.diagnostic.plot_knockdown_pressure``.  Takes an already-loaded
    xarray.Dataset (not a path).

    Returns a base64-encoded PNG string or None when no data are available.
    """
    from oceanarray.plotters.diagnostic import plot_knockdown_pressure

    return render_b64(plot_knockdown_pressure, ds, optional=True)


def _make_knockdown_hab_b64(ds: xr.Dataset) -> str | None:
    """IQR of measured HAB vs. nominal HAB, for the stack report.

    Thin Tier-3 wrapper around
    ``plotters.diagnostic.plot_knockdown_hab``.  Takes an already-loaded
    xarray.Dataset (not a path).

    Returns a base64-encoded PNG string or None when no data are available.
    """
    from oceanarray.plotters.diagnostic import plot_knockdown_hab

    return render_slot(plot_knockdown_hab, ds, slot="half", optional=True)


def _make_knockdown_displacement_b64(ds: xr.Dataset) -> str | None:
    """Scatter of estimated horizontal displacement vs. measured pressure.

    Thin Tier-3 wrapper around
    ``plotters.diagnostic.plot_knockdown_displacement``.

    Returns a base64-encoded PNG string or None when no data are available.
    """
    from oceanarray.plotters.diagnostic import plot_knockdown_displacement

    return render_slot(plot_knockdown_displacement, ds, slot="full", optional=True)


def _make_knockdown_anomaly_b64(ds: xr.Dataset) -> str | None:
    """IQR of pressure anomaly (measured − nominal) per instrument, for the stack report.

    Thin Tier-3 wrapper around
    ``plotters.diagnostic.plot_knockdown_anomaly``.  Takes an already-loaded
    xarray.Dataset (not a path).

    Returns a base64-encoded PNG string or None when no data are available.
    """
    from oceanarray.plotters.diagnostic import plot_knockdown_anomaly

    return render_slot(plot_knockdown_anomaly, ds, slot="half", optional=True)


def _make_clock_check_b64(
    nc_paths: dict[str, Any],
    deploy_dt: Any,
    recover_dt: Any,
    window_minutes: int = 30,
) -> str | None:
    """Overlaid normalised-temperature comparison ±window around deploy/recover.

    Thin Tier-3 wrapper around
    ``plotters.diagnostic.plot_clock_offset_check``.

    Parameters
    ----------
    nc_paths : dict of {serial: Path}
        Paths to stage-2 or stage-3 NetCDF files keyed by serial number.
    deploy_dt : datetime or None
        Deployment start time (UTC).
    recover_dt : datetime or None
        Recovery time (UTC).
    window_minutes : int
        Duration of each zoom window in minutes (default 10).

    Returns
    -------
    str or None
        Base64-encoded PNG for HTML embedding, or None.

    """
    from oceanarray.plotters.diagnostic import plot_clock_offset_check

    return render_b64(
        plot_clock_offset_check,
        nc_paths,
        deploy_dt,
        recover_dt,
        window_minutes,
        optional=True,
    )
