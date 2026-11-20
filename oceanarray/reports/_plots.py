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

from .. import physics
from ..plotters.current import (
    draw_adcp_hodograph,
    draw_adcp_rose,
    draw_adcp_velocity,
    draw_grid_hodograph,
    draw_grid_progressive_vector,
    draw_grid_rose,
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
    as_dataset,
    grid_despine,
    nominal_depth_text,
)
from ..plotters.hydrography import (
    draw_isopycnal_coverage,
    draw_isopycnal_ts,
    draw_overflow_temperature,
)
from ..plotters.primitives import (
    date_axis,
)
from ..plotters.spectrum import (
    draw_grid_rotary_spectrum,
    draw_spectrum,
    draw_wavelet,
)
from ..plotters.timeseries import (
    draw_analog_timeseries,
    draw_grid_hydro,
    draw_grid_n2,
    draw_grid_potential_density,
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
from ._read import _QC_LABELS
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


# Variables plotted with both a line and individual dots so that sparse or
# near-zero samples (e.g. turbidity = 0 NTU between events) are visible.
_DOT_LINE_VARS: frozenset = frozenset({"turbidity"})


# _instrument_panels is imported from plotters.diagnostic (single definition) —
# it was previously duplicated here.


# ---------------------------------------------------------------------------
# Full time-series figure
# ---------------------------------------------------------------------------


# Max panels per instrument time-series figure.  A tall figure cannot split
# across PDF pages, so we paginate into several figures of at most this many
# panels each (see _make_instrument_b64s).
_MAX_TS_PANELS = 5


def _add_tilt(ds: xr.Dataset) -> xr.Dataset:
    """Return *ds* with the derived ``tilt`` (:func:`oceanarray.physics.tilt`) added."""
    if "pitch" not in ds.data_vars and "roll" not in ds.data_vars:
        return ds
    zeros = np.zeros(ds.sizes["time"])
    pitch = ds["pitch"].values.astype(float) if "pitch" in ds.data_vars else zeros
    roll = ds["roll"].values.astype(float) if "roll" in ds.data_vars else zeros
    attrs = {"units": "degree", "long_name": "Instrument tilt from vertical"}
    return ds.assign(tilt=("time", physics.tilt(pitch, roll), attrs))


def _draw_instrument_panels(
    ds: xr.Dataset,
    instr_type: str,
    show_qc: bool = True,
    title_suffix: str = "",
    panels: list | None = None,
    *,
    title: str | None = None,
    flag_sources: tuple[str, ...] | None = None,
    record: dict | None = None,
) -> plt.Figure | None:
    """Render instrument panels from an already-loaded xarray Dataset.

    Parameters
    ----------
    ds : xarray.Dataset
        The instrument's stage file, loaded.
    instr_type : str
        The instrument type (for the default title).
    show_qc : bool, optional
        Mark flagged values: dots suspect, crosses bad, blue points interpolated.
    title_suffix : str, optional
        Appended to the title in brackets.
    panels : list, optional
        The ``(varname, label, colour, invert)`` panels to draw (a page of them).
    title : str or None, optional
        The figure title (the page's ``"{Type} {serial} · {depth} m"``).
    flag_sources : tuple of str or None, optional
        Flag-variable suffixes the markers read (default oceanarray's and the
        originator's: the worse of the two is drawn).
    record : dict or None, optional
        Filled with what was drawn: ``variables`` (list) and ``counts`` (values
        marked per flag, added to what it holds).

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    from ..plotters.helpers import FLAG_SOURCES, marker_flags

    sources = flag_sources if flag_sources is not None else FLAG_SOURCES
    import matplotlib.pyplot as plt

    from ..config import parameters as params

    ds = _add_tilt(ds)
    # A *panels* subset paginates a tall figure (see _make_instrument_b64s).
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

            if record is not None:
                record.setdefault("variables", []).append(vname)
            flags = marker_flags(ds, vname, sources) if show_qc else None
            if flags is not None:
                counts = record.setdefault("counts", {}) if record is not None else {}
                for fval, mkw in _QC_MARKER.items():
                    mask = (flags == fval) & np.isfinite(data)
                    if mask.any():
                        counts[fval] = counts.get(fval, 0) + int(mask.sum())
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

        if title is None:
            serial = ds.attrs.get("serial", "?")
            depth = nominal_depth_text(ds)
            title = f"{instr_type.title()} {serial} · {depth}"
        if title_suffix:
            title += f"  [{title_suffix}]"
        axs[0].set_title(title)

        axs[-1].set_xlabel("Date")
        date_axis(axs[-1])
        plt.tight_layout()
        return fig


def _make_instrument_b64s(
    nc_path: Path | xr.Dataset,
    instr_type: str,
    show_qc: bool = True,
    *,
    title: str | None = None,
    record: dict | None = None,
) -> list[str]:
    """Instrument data time series with flag markers, paginated.

    Parameters
    ----------
    nc_path : pathlib.Path or xarray.Dataset
        The instrument's stage file.
    instr_type : str
        The instrument type.
    show_qc : bool, optional
        Mark flagged values.
    title : str or None, optional
        Figure title (the page's).
    record : dict or None, optional
        Filled with what the figures drew (see :func:`_draw_instrument_panels`).

    Returns
    -------
    list of str
        One base64 PNG per page of panels.
    """
    ds = as_dataset(nc_path)
    try:
        ds = _add_tilt(ds)
        panels = _instrument_panels(ds, combine_pitch_roll=True)
        if not panels:
            return []
        images: list[str] = []
        # Paginate into figures of <= _MAX_TS_PANELS panels so a tall time series
        # does not overflow one PDF page.
        for i in range(0, len(panels), _MAX_TS_PANELS):
            chunk = panels[i : i + _MAX_TS_PANELS]
            b64 = render_b64(
                lambda c=chunk: _draw_instrument_panels(
                    ds,
                    instr_type,
                    show_qc=show_qc,
                    panels=c,
                    title=title,
                    record=record,
                ),
                optional=True,
                source=source_for(_draw_instrument_panels),
            )
            if b64:
                images.append(b64)
        return images
    finally:
        ds.close()


def _make_windows_b64s(
    nc_path: Path | xr.Dataset,
    instr_type: str,
    hours: int = 6,
    show_qc: bool = True,
    vlines: list | None = None,
    stage1_nc: Path | None = None,
    *,
    title: str | None = None,
) -> list[str]:
    """Return base64 PNGs: combined start + end window figure, paginated (*title*: the page's)."""
    ds = as_dataset(nc_path)
    try:
        panels = _instrument_panels(ds, combine_pitch_roll=True)
    finally:
        ds.close()
    if not panels:
        return []
    images: list[str] = []
    # Paginate into figures of <= _MAX_TS_PANELS rows (each a half-width start
    # panel beside a half-width end panel) so none overflows one PDF page.
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
            title=title,
            optional=True,
        )
        if b64:
            images.append(b64)
    return images


def _make_data_histogram_b64(
    nc_path: Path | xr.Dataset, *, rule: dict | None = None
) -> str | None:
    """Return base64 PNG: histogram of data values with QC range threshold lines.

    *rule*: the flag rule deciding which values are kept (see
    ``plotters.diagnostic.draw_data_histogram``).
    """
    return render_b64(draw_data_histogram, nc_path, rule=rule, optional=True)


def _make_ts_diagram_b64(
    nc_path: Path | xr.Dataset, *, record: dict | None = None
) -> str | None:
    """Return base64 PNG: T-S diagram (scatter by pressure, heatmap, optional O2 panel).

    Parameters
    ----------
    nc_path : Path
        The instrument's stage file.
    record : dict or None, optional
        Filled with what the figure drew (see ``plotters.ts.draw_ts_diagram``).
    """
    return render_b64(draw_ts_diagram, nc_path, record=record, optional=True)


def _make_grid_potential_density_b64(
    ds: xr.Dataset,
    reference: str = "sigma0",
    derived: Any = None,
    scale: Any = None,
    density: Any = None,
) -> str | None:
    """Return base64 PNG: one potential-density section (σ0 or σ2) over the column."""
    return render_slot(
        draw_grid_potential_density,
        ds,
        reference,
        derived=derived,
        density=density,
        scale=scale,
        optional=True,
    )


def _make_grid_hydro_b64(
    ds: xr.Dataset,
    var_bounds: dict | None = None,
    instrument_pressures: Any = None,
    scale: Any = None,
) -> str | None:
    """Return base64 PNG: stacked temperature / practical-salinity sections."""
    return render_slot(
        draw_grid_hydro,
        ds,
        var_bounds,
        instrument_pressures=instrument_pressures,
        scale=scale,
        optional=True,
    )


def _make_grid_velocity_stacked_b64(
    ds: xr.Dataset, instrument_pressures: Any = None, scale: Any = None
) -> str | None:
    """Eastward / northward / upward velocity sections for the grid report."""
    return render_slot(
        draw_grid_velocity_stacked,
        ds,
        instrument_pressures=instrument_pressures,
        scale=scale,
        optional=True,
    )


def _make_spectrum_b64(
    da_temp: xr.DataArray,
    dt_seconds: float,
    lat: float = 0.0,
    hf_segment_days: float = 7.0,
    hf_x_max_days: float = 2.0,
    levels: list[int] | None = None,
    markers: Any = (),
) -> str | None:
    """Return base64 PNG: two-panel Welch PSD of gridded temperature."""
    return render_b64(
        draw_spectrum,
        da_temp,
        dt_seconds,
        lat,
        hf_segment_days,
        hf_x_max_days,
        levels=levels,
        markers=markers,
        optional=True,
    )


def _make_wavelet_b64(
    da_temp: xr.DataArray,
    dt_seconds: float,
    wavelet: str = "morlet",
    levels: list[int] | None = None,
) -> str | None:
    """Return base64 PNG: continuous wavelet transform scalogram for gridded temperature."""
    return render_b64(
        draw_wavelet, da_temp, dt_seconds, wavelet, levels=levels, optional=True
    )


def _make_grid_rotary_spectrum_b64(
    ds: xr.Dataset,
    lat: float = 0.0,
    levels: list[int] | None = None,
    markers: Any = (),
) -> str | None:
    """Return base64 PNG: two-panel rotary velocity spectrum for the grid report."""
    return render_b64(
        draw_grid_rotary_spectrum,
        ds,
        lat,
        levels=levels,
        markers=markers,
        optional=True,
    )


def _make_flag_strip_b64(ds: xr.Dataset, labels: list[str] | None = None) -> str | None:
    """Return base64 PNG: the quality-flag strip of a stack or Stack cat (*labels*: the page's)."""
    from oceanarray.plotters.flags import draw_flag_strip

    return render_slot(draw_flag_strip, ds, labels=labels, slot="full", optional=True)


def _make_stack_ts_diagram_b64(
    ds: xr.Dataset, derived: Any = None, **kwargs: Any
) -> str | None:
    """Return base64 PNG: T-S diagram for a stacked dataset (*derived*: the page's).

    Keywords (``record``, ``full_range``) go to ``plotters.ts.draw_stack_ts_diagram``.
    """
    return render_b64(
        draw_stack_ts_diagram, ds, derived=derived, optional=True, **kwargs
    )


# ---------------------------------------------------------------------------
# Rose diagrams
# ---------------------------------------------------------------------------


def _make_instrument_rose_b64(
    nc_path: Path | xr.Dataset, *, title: str | None = None, rule: dict | None = None
) -> str | None:
    """Return base64 PNG: one instrument's current rose (*title*, *rule*: the page's)."""
    return render_b64(
        draw_instrument_rose, nc_path, title=title, rule=rule, optional=True
    )


def _make_grid_ts_diagram_b64_and_limits(
    ds: xr.Dataset, n_bins: int = 60, derived: Any = None
) -> tuple[str | None, dict]:
    """Return (b64_str_or_None, bounds_dict): T-S diagram for gridded mooring data."""
    ts_bounds: dict = {}

    def _draw(*, width_in: float = report_tokens.W_FULL) -> plt.Figure | None:
        result = draw_grid_ts_diagram(ds, n_bins, width_in=width_in, derived=derived)
        if result is None:
            return None
        nonlocal ts_bounds
        fig, ts_bounds = result
        return fig

    # Displayed at half width (template slot-half); rendering at that width keeps
    # the PNG px == display px.  With O₂ the diagram gains a panel but the page
    # still shows it at half — now without the oversample.
    return (
        render_slot(
            _draw, slot="half", optional=True, source=source_for(draw_grid_ts_diagram)
        ),
        ts_bounds,
    )


def _make_velocity_iqr_profile_b64(ds: xr.Dataset, scale: Any = None) -> str | None:
    """Return base64 PNG: percentile profiles of gridded velocity, at the page's scale."""
    return render_b64(draw_velocity_iqr_profile, ds, scale=scale, optional=True)


def _make_grid_n2_b64(
    ds: xr.Dataset, lat: float = 0.0, derived: Any = None
) -> str | None:
    """Plot buoyancy frequency squared N² on the pressure-time grid."""
    return render_slot(draw_grid_n2, ds, lat, derived=derived, optional=True)


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


def _make_grid_rose_b64(
    ds: xr.Dataset, max_roses: int = 4, levels: list[int] | None = None
) -> str | None:
    """Return base64 PNG: current roses at grid levels (the stack page's design)."""
    return render_b64(draw_grid_rose, ds, max_roses, levels=levels, optional=True)


def _make_grid_progressive_vector_b64(ds: xr.Dataset) -> str | None:
    """Progressive vector diagram by pressure level for the grid report."""
    return render_slot(draw_grid_progressive_vector, ds, slot="half", optional=True)


def _make_grid_timeseries_b64(ds: xr.Dataset, derived: Any = None) -> str | None:
    """Return base64 PNG: velocity time series at depth of maximum mean speed."""
    return render_b64(draw_grid_timeseries, ds, derived=derived, optional=True)


def _make_isopycnal_ts_b64(ds_iso: xr.Dataset) -> str | None:
    """Return base64 PNG: isopycnal height-above-seabed time series."""
    return render_b64(draw_isopycnal_ts, ds_iso, optional=True)


def _make_isopycnal_coverage_b64(ds: xr.Dataset) -> str | None:
    """Return base64 PNG: three-panel isopycnal diagnostic."""
    return render_b64(draw_isopycnal_coverage, ds, optional=True)


def _make_overflow_temperature_b64(ds: xr.Dataset) -> str | None:
    """Return base64 PNG: temperature time series at ~100 m above the seabed."""
    return render_b64(draw_overflow_temperature, ds, optional=True)


# ---------------------------------------------------------------------------
# Aquadopp trajectory and speed distribution (Tier-3 wrappers)
# Delegates to oceanarray.plotters.current (Tier-2 domain wrappers).
# ---------------------------------------------------------------------------


def _make_temperature_trajectory_b64(nc_path: Path | xr.Dataset) -> str | None:
    """Lagrangian trajectory coloured by temperature, for Aquadopp instrument page."""
    from oceanarray.plotters.current import draw_temperature_trajectory

    def _draw() -> plt.Figure:
        with as_dataset(nc_path) as ds:
            return draw_temperature_trajectory(ds)

    return render_b64(
        _draw, optional=True, source=source_for(draw_temperature_trajectory)
    )


def _make_speed_boxplot_b64(nc_path: Path | xr.Dataset) -> str | None:
    """Speed boxplot with percentile statistics, for Aquadopp instrument page."""
    from oceanarray.plotters.current import draw_speed_boxplot

    def _draw(*, width_in: float = report_tokens.W_QUARTER) -> plt.Figure:
        with as_dataset(nc_path) as ds:
            return draw_speed_boxplot(ds, width_in=width_in)

    return render_slot(
        _draw, slot="quarter", optional=True, source=source_for(draw_speed_boxplot)
    )


def _make_hodograph_b64(nc_path: Path | xr.Dataset) -> str | None:
    """Two-panel hodograph (raw + eddy-only) for Aquadopp instrument page."""
    # Not optional: a placeholder is drawn when east/north velocities are absent, so
    # the section is never silently omitted; None only on unrecoverable file errors.

    from oceanarray.plotters.current import draw_hodograph

    def _draw() -> plt.Figure:
        with as_dataset(nc_path) as ds:
            return draw_hodograph(ds)

    return render_b64(_draw, source=source_for(draw_hodograph))


def _make_multi_aquadopp_progressive_vector_b64(ds: xr.Dataset) -> str | None:
    """Return base64 PNG: the Aquadopps' progressive vector diagram, stack page."""
    from oceanarray.plotters.current import draw_multi_aquadopp_progressive_vector

    return render_slot(
        draw_multi_aquadopp_progressive_vector, ds, slot="half", optional=True
    )


def _make_stack_timeseries_b64(
    ds: xr.Dataset,
    rows: list[tuple[str, str, bool, set[str] | None]],
    *,
    color_var: str,
    dot_overlay: bool = False,
    deployments: list | None = None,
    legend_shallow_first: bool = False,
    **kwargs: Any,
) -> str | None:
    """Stack time series (one row per variable, one line per level), full width, for the stack page.

    Further keywords (``label_lines``, ``row_heights``, ``mark_suspect``,
    ``record``) go to ``plotters.timeseries.draw_stack_timeseries``.
    """
    return render_slot(
        draw_stack_timeseries,
        ds,
        rows,
        color_var=color_var,
        dot_overlay=dot_overlay,
        deployments=deployments,
        legend_shallow_first=legend_shallow_first,
        optional=True,
        **kwargs,
    )


def _make_aquadopp_tilt_panels_b64(ds: xr.Dataset, step: int = 1) -> str | None:
    """|pitch|, |roll| and pressure tilt per Aquadopp, full width, for the stack page."""
    from oceanarray.plotters.current import draw_aquadopp_tilt_panels

    return render_slot(draw_aquadopp_tilt_panels, ds, step=step, optional=True)


def _make_instrument_spacing_b64(ds: xr.Dataset) -> str | None:
    """Adjacent-instrument pressure spacing histogram, a third wide, for the stack page."""
    return render_slot(draw_instrument_spacing, ds, slot="third", optional=True)


def _make_aquadopp_speed_profile_b64(ds: xr.Dataset) -> str | None:
    """Horizontal speed boxplots per Aquadopp positioned by HAB, for stack page."""
    from oceanarray.plotters.current import draw_aquadopp_speed_profile

    return render_slot(draw_aquadopp_speed_profile, ds, slot="half", optional=True)


def _make_adcp_progressive_vector_b64(ds: xr.Dataset) -> str | None:
    """Return base64 PNG: the ADCP bins' progressive vector diagram, stack page."""
    from oceanarray.plotters.current import draw_adcp_progressive_vector

    return render_slot(draw_adcp_progressive_vector, ds, slot="half", optional=True)


def _make_adcp_velocity_b64(nc_path: Path | xr.Dataset) -> str | None:
    """Return base64 PNG: stacked colour panels for ADCP per-instrument report."""
    return render_b64(draw_adcp_velocity, nc_path, optional=True)


def _make_adcp_rose_b64(nc_path: Path | xr.Dataset) -> str | None:
    """Return base64 PNG: ADCP current rose (depth-average + percentile bins)."""
    return render_b64(draw_adcp_rose, nc_path, optional=True)


def _make_adcp_hodograph_b64(
    nc_path: Path | xr.Dataset, lp_days: float = 4.0, smooth_hours: float = 24.0
) -> str | None:
    """Return base64 PNG: two-depth hodograph for an ADCP per-instrument report."""
    return render_b64(
        draw_adcp_hodograph, nc_path, lp_days, smooth_hours, optional=True
    )


def _make_grid_hodograph_b64(
    ds: xr.Dataset, smooth_hours: float = 24.0, levels: list[int] | None = None
) -> str | None:
    """Return base64 PNG: hodographs at the current roses' grid levels."""
    return render_b64(
        draw_grid_hodograph, ds, smooth_hours, levels=levels, optional=True
    )


def _make_analog_timeseries_b64(
    nc_path: Path | xr.Dataset, analog_vars: list[str]
) -> str | None:
    """Full-record time series for analog channel variables, one panel each."""
    if not analog_vars:
        return None
    return render_b64(draw_analog_timeseries, nc_path, analog_vars, optional=True)


def _make_knockdown_hab_b64(ds: xr.Dataset, knockdown: Any = None) -> str | None:
    """IQR of measured HAB vs. nominal HAB, for the stack report; *knockdown* is the report's shared input."""
    from oceanarray.plotters.diagnostic import draw_knockdown_hab

    return render_slot(
        draw_knockdown_hab, ds, knockdown=knockdown, slot="half", optional=True
    )


def _make_knockdown_displacement_b64(
    ds: xr.Dataset, knockdown: Any = None
) -> str | None:
    """Scatter of estimated horizontal displacement vs. measured pressure; *knockdown* is the report's shared input."""
    from oceanarray.plotters.diagnostic import draw_knockdown_displacement

    return render_slot(
        draw_knockdown_displacement, ds, knockdown=knockdown, slot="full", optional=True
    )


def _make_knockdown_anomaly_b64(ds: xr.Dataset, knockdown: Any = None) -> str | None:
    """Per-instrument IQR of pressure anomaly (measured − nominal), stack report; *knockdown* is the report's shared input."""
    from oceanarray.plotters.diagnostic import draw_knockdown_anomaly

    return render_slot(
        draw_knockdown_anomaly, ds, knockdown=knockdown, slot="half", optional=True
    )


def _make_clock_check_b64(
    nc_paths: dict[str, Any],
    deploy_dt: Any,
    recover_dt: Any,
    window_minutes: int = 30,
) -> str | None:
    """Overlaid normalised-temperature comparison ±window around deploy/recover."""
    # nc_paths maps serial -> stage-2 or stage-3 NetCDF path; times are UTC.
    from oceanarray.plotters.diagnostic import draw_clock_offset_check

    return render_b64(
        draw_clock_offset_check,
        nc_paths,
        deploy_dt,
        recover_dt,
        window_minutes,
        optional=True,
    )
