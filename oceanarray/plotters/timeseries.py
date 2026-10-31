"""Tier-2 domain wrappers for time-series and gridded-section plots.

This module provides the ``draw_*`` functions used by the HTML report pipeline
to render instrument time-series panels and mooring-grid section plots.

Pairs with :mod:`oceanarray.analysis.temporal` for time-series analysis.

**Grid section panels** (data on a pressure × time grid):

- :func:`draw_grid_fig` — generic pcolormesh / contourf wrapper for a single variable.
- :func:`draw_grid_hydro` — stacked temperature / salinity (and optionally oxygen) panels.
- :func:`draw_grid_velocity_stacked` — stacked east / north / up velocity panels.
- :func:`draw_grid_sigma` — potential density (sigma) panels.
- :func:`draw_grid_n2` — buoyancy frequency squared N² computed via ``gsw``.
- :func:`draw_grid_timeseries` — velocity time series at the depth of maximum mean speed.

**Stack panels** (one line per instrument level on a common time axis):

- :func:`draw_stack_timeseries` — one or more variables, one row each, sharing the
  time axis and one instrument legend.

**Instrument time-series panels**:

- :func:`draw_analog_timeseries` — full-record time series for analog channel variables.

Post-OdB: migrate the following from plotter.py and report/_plots.py:
  plot_microcat_raw, plot_aquadopp_raw, plot_mooring_timeseries (the three
  kept plotter.py functions; §11), plot_aquadopp_quick,
  build_instrument_fig (was _build_fig_from_ds), plot_instrument_windows.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    import matplotlib.pyplot as plt
    import xarray as xr

from oceanarray.reports._vendored import design_tokens as report_tokens

from .. import parameters as params
from ..config.unit_tables import unit_label
from ..utilities import nice_colorbar_ticks
from .helpers import OKABE_ITO, grid_despine, level_line_styles
from .primitives import (
    colorbar_norm,
    date_axis,
    date_offset_left,
    figure_title,
    pcolormesh_panel,
    plot_title,
    pressure_axis,
)


def draw_grid_fig(
    da: xr.DataArray,
    title: str,
    units: str,
    cmap: str,
    style: str = "pcolormesh",
    contour_levels: list | None = None,
    symmetric: bool = False,
    vmin: float | None = None,
    vmax: float | None = None,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure:
    """Render a grid figure from *da* (dims time × pressure); return a Figure.

    Parameters
    ----------
    da : xr.DataArray
        Data array with ``time`` and ``pressure`` dimensions.
    title : str
        Panel title and colorbar label prefix.
    units : str
        Unit string appended to the colorbar label.
    cmap : str
        Matplotlib colormap name.
    style : str
        ``"pcolormesh"`` (default) or ``"contourf"``.
    contour_levels : list, optional
        If given, overlay black contour lines at these levels.
    symmetric : bool
        If True, force a symmetric (diverging) color range.
    vmin, vmax : float, optional
        Override the automatic percentile-based color limits.

    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure

    """
    import matplotlib.pyplot as plt

    time = da.coords["time"].values
    pressure = da.coords["pressure"].values
    data = da.transpose("pressure", "time").values

    fig, ax = plt.subplots(
        figsize=(width_in, params.GRID_PANEL_ROW_IN), layout="constrained"
    )
    bounds, norm = colorbar_norm(data, vmin=vmin, vmax=vmax, symmetric=symmetric)
    if style == "contourf":
        pc = ax.contourf(time, pressure, data, levels=bounds, cmap=cmap, extend="both")
    else:
        pc = ax.pcolormesh(
            time, pressure, data, shading="nearest", cmap=cmap, norm=norm
        )
    if contour_levels:
        ct = ax.contour(
            time,
            pressure,
            data,
            levels=contour_levels,
            colors="k",
            linewidths=0.8,
            alpha=0.75,
        )
        ax.clabel(ct, fmt="%.1f", fontsize=report_tokens.CLABEL_FS, inline=True)
    cb = fig.colorbar(pc, ax=ax, pad=0.02, ticks=bounds)
    cb.set_label(f"{title} ({units})" if units else title)
    pressure_axis(ax)
    date_axis(ax)
    ax.set_xlabel("Time")
    plot_title(ax, f"{title} [{style}]")
    return fig


def draw_grid_hydro(
    ds: xr.Dataset,
    var_bounds: dict | None = None,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Stacked temperature / salinity pcolormesh panels for the grid report; return a Figure.

    Both panels have pressure (dbar) on the Y-axis (inverted, surface at top).
    Colorbar bounds are clipped to the ``COLORBAR_PLOW``–``COLORBAR_PHIGH`` percentiles
    of the data to reduce the influence of outliers on color scaling.

    **Temperature** (°C, colormap ``RdYlBu_r``): sea water temperature on the gridded
    pressure–time grid.

    **Salinity** (PSU, colormap ``YlGnBu_r``): taken from the ``salinity`` variable if
    present.  If absent but both ``conductivity`` (mS cm⁻¹) and ``temperature`` (°C) are
    present, Practical Salinity is derived via ``gsw.SP_from_C`` (PSS-78).  Note: this
    is Practical Salinity, not Absolute Salinity (g kg⁻¹); the latter would require
    pressure and longitude via ``gsw.SA_from_SP``.

    Panels are rendered only for variables that are present in *ds* or derivable.

    Parameters
    ----------
    ds : xr.Dataset
        Gridded mooring dataset with dimensions ``(time, pressure)``.
    var_bounds : dict, optional
        Pre-computed colorbar limits keyed by variable name, e.g.
        ``{"t_lim": (vmin, vmax), "s_lim": (vmin, vmax), "o2_lim": (vmin, vmax)}``.
        When a key is present its limits are used instead of computing from the data.
        Intended for passing the T-S diagram axis limits so both figures share scales.

    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None
        Figure, or None if no hydrographic data are present.

    """
    import matplotlib.pyplot as plt
    import xarray as _xr

    if var_bounds is None:
        var_bounds = {}

    panels = []
    for var, cmap in [
        ("temperature", "RdYlBu_r"),
        ("salinity", "YlGnBu_r"),
        ("oxygen_saturation_pct", "BrBG"),
    ]:
        if var not in ds.data_vars:
            continue
        panels.append((var, cmap))

    # Also allow derived salinity from T/C
    if (
        "salinity" not in ds.data_vars
        and "conductivity" in ds.data_vars
        and "temperature" in ds.data_vars
    ):
        try:
            import gsw

            p_1d = ds["pressure"].values
            T = ds["temperature"].transpose("time", "pressure").values
            C = ds["conductivity"].transpose("time", "pressure").values
            SP_vals = gsw.SP_from_C(C, T, p_1d[np.newaxis, :])
            ds = ds.assign(
                salinity=_xr.DataArray(
                    SP_vals,
                    dims=("time", "pressure"),
                    coords={"time": ds["time"], "pressure": ds["pressure"]},
                    attrs={"units": "1", "long_name": "Practical Salinity"},
                )
            )
            panels.append(("salinity", "YlGnBu_r"))
        except Exception:  # noqa: BLE001
            pass

    if not panels:
        return None

    pressure = ds["pressure"].values
    time = ds["time"].values
    n = len(panels)
    fig, axes = plt.subplots(
        n,
        1,
        figsize=(width_in, params.GRID_PANEL_ROW_IN * n),
        sharex=True,
        squeeze=False,
        layout="constrained",
    )

    for ax, (var, cmap) in zip(axes[:, 0], panels, strict=False):
        da = ds[var]
        data = da.transpose("pressure", "time").values
        units = params.vunit(var) or da.attrs.get("units", "")
        # Use the variable name (tidied) as the panel title — long_name often
        # carries the full CF phrase "sea water temperature" which is verbose
        # for a plot heading.  The colorbar shows units only, on top of the bar.
        title = var.replace("_", " ").capitalize()
        _lim_key = {
            "temperature": "t_lim",
            "salinity": "s_lim",
            "oxygen_saturation_pct": "o2_lim",
        }.get(var)
        _passed = var_bounds.get(_lim_key) if _lim_key else None
        _n = 11 if var == "oxygen_saturation_pct" else 20
        _vmin, _vmax = (_passed[0], _passed[1]) if _passed is not None else (None, None)
        pcolormesh_panel(
            fig,
            ax,
            data,
            time,
            pressure,
            title=title,
            cmap=cmap,
            vmin=_vmin,
            vmax=_vmax,
            n=_n,
            units=units,
            title_loc="left",
            date_fmt=False,
        )

    date_axis(axes[-1, 0])
    return fig


def draw_grid_velocity_stacked(
    ds: xr.Dataset, *, width_in: float = report_tokens.W_FULL
) -> plt.Figure | None:
    """Stacked east / north / up velocity pcolormesh panels for the grid report.

    All three panels share the time axis and show pressure (dbar) on the Y-axis
    (inverted, surface at top).  East and north share symmetric diverging bounds;
    up uses its own symmetric bounds (open-ocean vertical velocities are typically
    1–2 cm s⁻¹ vs. tens of cm s⁻¹ horizontal).  Returns ``None`` when no
    velocity variables are present.

    Parameters
    ----------
    ds : xr.Dataset
        Gridded dataset with dimensions ``(time, pressure)``.

    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None

    """
    import matplotlib.pyplot as plt

    from .helpers import _velocity_panel_style

    vel_vars = [
        "east_velocity",
        "north_velocity",
        "up_velocity",
        "current_speed",
        "current_direction",
    ]
    _LABELS = {
        "east_velocity": "East velocity",
        "north_velocity": "North velocity",
        "up_velocity": "Up velocity",
        "current_speed": "Current speed",
        "current_direction": "Current direction",
    }
    present = [v for v in vel_vars if v in ds.data_vars]
    if not present:
        return None

    pressure = ds["pressure"].values
    time = ds["time"].values

    # Shared diverging bound from all ENU components (2nd/98th pctile magnitude)
    div_vars = [
        v for v in present if v in ("east_velocity", "north_velocity", "up_velocity")
    ]
    if div_vars:
        div_vals = np.concatenate([ds[v].values.ravel() for v in div_vars])
        finite_div = div_vals[np.isfinite(div_vals)]
        div_abs_max = (
            max(
                abs(float(np.percentile(finite_div, 2))),
                abs(float(np.percentile(finite_div, 98))),
                1e-4,
            )
            if len(finite_div)
            else 1.0
        )
    else:
        div_abs_max = 1.0

    n = len(present)
    fig, axes = plt.subplots(
        n,
        1,
        figsize=(width_in, params.GRID_PANEL_ROW_IN * n),
        sharex=True,
        squeeze=False,
        layout="constrained",
    )

    for ax, var in zip(axes[:, 0], present, strict=False):
        data = ds[var].transpose("pressure", "time").values
        # Apply QC mask via the variable's own QC flag, or fall back to east_velocity_qc
        for _qv in (f"{var}_qc", "east_velocity_qc"):
            if _qv in ds.data_vars:
                data = data.copy()
                data[
                    np.isin(
                        ds[_qv].transpose("pressure", "time").values,
                        params.QC_BAD_FLAGS,
                    )
                ] = np.nan
                break
        fv = data.ravel()
        fv = fv[np.isfinite(fv)]
        bounds, norm, cmap, cb_label = _velocity_panel_style(var, fv, div_abs_max)
        pc = ax.pcolormesh(
            time, pressure, data, shading="nearest", cmap=cmap, norm=norm
        )
        # Current direction is cyclic 0–360°: tick at the compass points rather
        # than the generic nice_colorbar_ticks (which gives 0/100/200/300).
        if var == "current_direction":
            cb_ticks = [0, 90, 180, 270, 360]
        else:
            cb_ticks = nice_colorbar_ticks(float(bounds[0]), float(bounds[-1]))
        cb = fig.colorbar(pc, ax=ax, pad=0.02, ticks=cb_ticks)
        # Use the per-variable colorbar label from _velocity_panel_style (° for
        # direction, m s⁻¹ for velocity/speed) — not a hardcoded east-velocity unit.
        cb.ax.set_title(cb_label, fontsize=report_tokens.ANNOT_FS)
        pressure_axis(ax)
        plot_title(ax, _LABELS[var])

    date_axis(axes[-1, 0])
    return fig


def draw_grid_sigma(
    ds: xr.Dataset, *, width_in: float = report_tokens.W_FULL
) -> plt.Figure | None:
    """Stacked sigma0 pcolormesh panel(s) for the stratification section.

    Returns ``None`` when no sigma variables are present.

    """
    import matplotlib.pyplot as plt

    from .. import parameters as params

    sigma_vars = [
        v for v in ds.data_vars if v.startswith("sigma") and "pressure" in ds[v].dims
    ]
    if not sigma_vars:
        return None

    pressure = ds["pressure"].values
    time = ds["time"].values
    n = len(sigma_vars)
    fig, axes = plt.subplots(
        n,
        1,
        figsize=(width_in, params.GRID_PANEL_ROW_IN * n),
        sharex=True,
        squeeze=False,
        layout="constrained",
    )

    for ax, sv in zip(axes[:, 0], sigma_vars, strict=False):
        da = ds[sv]
        data = da.transpose("pressure", "time").values
        units = params.vunit("potential_density") or unit_label(
            da.attrs.get("units", "kg m-3")
        )
        label = da.attrs.get("long_name", sv)
        pcolormesh_panel(
            fig,
            ax,
            data,
            time,
            pressure,
            title=label,
            cmap=params.DENSITY_COLORMAP,
            units=units,
            title_loc="left",
            date_fmt=False,
        )

    date_axis(axes[-1, 0])
    return fig


def draw_grid_n2(
    ds: xr.Dataset, lat: float = 0.0, *, width_in: float = report_tokens.W_FULL
) -> plt.Figure | None:
    """Compute and plot buoyancy frequency squared N² on the pressure-time grid.

    Returns ``None`` when temperature or salinity are absent.

    """
    import gsw
    import matplotlib.pyplot as plt

    if "temperature" not in ds.data_vars or "salinity" not in ds.data_vars:
        return None

    p_1d = ds["pressure"].values.astype(float)
    T_pt = ds["temperature"].transpose("pressure", "time").values.astype(float)
    SP_pt = ds["salinity"].transpose("pressure", "time").values.astype(float)
    time_vals = ds["time"].values
    n_p, n_t = T_pt.shape

    lon = 0.0
    for key in ("deployment_longitude", "seabed_longitude", "longitude"):
        v = ds.attrs.get(key)
        if v is not None:
            try:
                lon = float(v)
                break
            except Exception:  # noqa: BLE001  — attribute may be non-numeric; skip
                pass

    p_2d = np.broadcast_to(p_1d[:, np.newaxis], (n_p, n_t)).copy()
    SA = gsw.SA_from_SP(SP_pt, p_2d, lon, lat)
    CT = gsw.CT_from_t(SA, T_pt, p_2d)
    N2, p_mid = gsw.Nsquared(SA, CT, p_2d, lat=lat)
    p_mid_1d = np.nanmean(p_mid, axis=1)
    # Floor non-positive/unstable N² to 1e-12 so it renders as the lowest colour
    # (weakly stratified / off-scale low) rather than blank.
    N2_log = np.log10(np.maximum(N2, 1e-12))

    fig, ax = plt.subplots(
        figsize=(width_in, params.GRID_PANEL_ROW_IN), layout="constrained"
    )
    # Colour limits from the 1st-99th percentile of log10(N²) over *positive*
    # (statically stable) cells only.  Non-positive N² is floored to 1e-12 for the
    # plot, but including that floor in the percentile would peg the low limit at
    # log10(1e-12) = -12 and wash out the stratification structure whenever more
    # than ~1% of cells are unstable (Eleanor 2026-08-18).
    _pos = N2[np.isfinite(N2) & (N2 > 0)]
    if _pos.size:
        _logpos = np.log10(_pos)
        _lo = float(np.nanpercentile(_logpos, 1))
        _hi = float(np.nanpercentile(_logpos, 99))
    else:
        _lo, _hi = -12.0, 0.0
    bounds, norm = colorbar_norm(vmin=_lo, vmax=_hi)
    pc = ax.pcolormesh(
        time_vals, p_mid_1d, N2_log, shading="nearest", cmap="plasma_r", norm=norm
    )
    cb = fig.colorbar(
        pc,
        ax=ax,
        pad=0.02,
        ticks=nice_colorbar_ticks(float(bounds[0]), float(bounds[-1])),
    )
    cb.ax.set_title(f"log({unit_label('s-2')})", fontsize=report_tokens.ANNOT_FS)
    pressure_axis(ax)
    date_axis(ax)
    ax.set_xlabel("Time")
    plot_title(
        ax, r"Buoyancy frequency squared N² ($\log_{10}$ scale; purple = stratified)"
    )
    return fig


def draw_grid_timeseries(
    ds: xr.Dataset, *, width_in: float = report_tokens.W_FULL
) -> plt.Figure | None:
    """Velocity time series at the depth of maximum time-mean current speed.

    Two stacked panels (shared time axis):

    - **Speed** (black, m s⁻¹): ``sqrt(east² + north²)``; always ≥ 0.
    - **East and North velocity** (same axes, m s⁻¹): east in Okabe-Ito blue
      (#0072B2), north in Okabe-Ito orange (#E69F00).  Both signed components
      are plotted together so the relationship between along- and cross-stream
      flow is immediately visible.

    The target pressure level is chosen automatically as the level with the
    highest time-mean current speed and at least 70 % non-NaN coverage.  The
    selected pressure is annotated in the figure title.

    Parameters
    ----------
    ds : xr.Dataset
        Gridded dataset with dimensions ``(time, pressure)`` containing at
        minimum ``east_velocity`` and ``north_velocity`` in m s⁻¹.

    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None
        Figure, or None if horizontal velocity data are absent or all-NaN.

    """
    import warnings

    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    has_horiz = all(v in ds.data_vars for v in ("east_velocity", "north_velocity"))
    if not has_horiz:
        return None

    east = ds["east_velocity"].values  # (time, pressure)
    north = ds["north_velocity"].values
    pressure = ds["pressure"].values  # 1-D
    time_vals = ds["time"].values

    spd = np.sqrt(east**2 + north**2)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean_spd = np.nanmean(spd, axis=0)  # (pressure,)
    if not np.any(np.isfinite(mean_spd)):
        return None

    # Only consider levels with at least 70 % non-NaN values so that a
    # depth covered only briefly does not win on spuriously high mean speed.
    n_time = spd.shape[0]
    coverage = np.sum(np.isfinite(spd), axis=0) / n_time  # fraction per level
    eligible = coverage >= 0.70
    if not np.any(eligible):
        eligible = np.ones(len(pressure), dtype=bool)  # fall back: no restriction
    candidate_spd = np.where(eligible, mean_spd, np.nan)
    k_max = int(np.nanargmax(candidate_spd))
    p_target = float(pressure[k_max])

    spd_ts = spd[:, k_max]
    east_ts = east[:, k_max]
    north_ts = north[:, k_max]

    fig, axs = plt.subplots(
        2, 1, figsize=(width_in, 5), sharex=True, layout="constrained"
    )
    _C_EAST = "#0072B2"
    _C_NORTH = "#E69F00"

    # Panel 0: speed
    axs[0].plot(time_vals, spd_ts, color="k", linewidth=0.8)
    axs[0].set_ylabel(params.vlabel("speed"))
    axs[0].set_ylim(bottom=0)

    # Panel 1: east and north on the same axes
    axs[1].plot(time_vals, east_ts, color=_C_EAST, linewidth=0.8, label="East")
    axs[1].plot(time_vals, north_ts, color=_C_NORTH, linewidth=0.8, label="North")
    axs[1].axhline(0, color="k", linewidth=0.4, linestyle="--", alpha=0.4, zorder=0)
    axs[1].set_ylabel(f"Velocity ({unit_label('m s-1')})")
    axs[1].legend(loc="upper right", framealpha=0.8)

    for ax in axs:
        grid_despine(ax)
    axs[-1].xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(axs[-1].xaxis.get_major_locator())
    )
    date_offset_left(axs[-1])
    figure_title(
        fig,
        f"Velocity time series at {p_target:.0f} dbar (depth of maximum mean speed)",
    )
    return fig


def draw_analog_timeseries(
    nc_path: Path,
    analog_vars: list[str],
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Full-record time series for analog channel variables, one panel per variable.

    Returns ``None`` when the dataset lacks a ``time`` dimension.

    Parameters
    ----------
    nc_path : Path
        Path to the stage-3 or stack NetCDF file.
    analog_vars : list of str
        Variable names to plot (caller must ensure the list is non-empty).

    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None

    """
    import matplotlib.pyplot as plt
    import xarray as xr

    ds = xr.open_dataset(nc_path, decode_timedelta=False).load()
    try:
        if "time" not in ds.dims:
            return None

        time = ds["time"].values
        n_vars = len(analog_vars)
        fig, axes = plt.subplots(
            n_vars,
            1,
            figsize=(width_in, max(2.5, n_vars * 2.0)),
            sharex=True,
            squeeze=False,
        )

        colors = OKABE_ITO  # colourblind-safe palette (cycles at 8)
        for row, vname in enumerate(analog_vars):
            ax = axes[row][0]
            raw = ds[vname].values
            units = ds[vname].attrs.get("units", "")
            long_name = ds[vname].attrs.get("long_name", vname)
            ylabel = f"{long_name} ({units})" if units else long_name

            # Stack NC: shape (time, N_LEVELS) — plot each level with finite data
            if raw.ndim == 2:
                n_plotted = 0
                for lvl_i in range(raw.shape[1]):
                    row_data = raw[:, lvl_i]
                    if np.any(np.isfinite(row_data)):
                        label = None
                        if "serial" in ds.coords:
                            label = str(ds["serial"].values[lvl_i])
                        ax.plot(
                            time,
                            row_data,
                            linewidth=0.8,
                            color=colors[n_plotted % len(colors)],
                            label=label,
                        )
                        n_plotted += 1
                if n_plotted > 1 and "serial" in ds.coords:
                    ax.legend(loc="upper right")
            else:
                ax.plot(time, raw, linewidth=0.8, color=OKABE_ITO[0])

            ax.set_ylabel(ylabel)
            grid_despine(ax)

        fig.autofmt_xdate(rotation=30, ha="right")
        return fig
    finally:
        ds.close()


def draw_stack_timeseries(
    ds: xr.Dataset,
    rows: list[tuple[str, str, bool, set[str] | None]],
    *,
    color_var: str,
    dot_overlay: bool = False,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Stack time series: one row per variable, one line per instrument level.

    The rows share the time axis and one instrument colour scheme (that of
    *color_var*, deep-first; see :func:`~oceanarray.plotters.helpers.level_line_styles`),
    so a colour tracks an instrument through every row, and one legend on the
    right lists the instruments plotted. Values whose QC flag is in
    :data:`~oceanarray.config.parameters.QC_BAD_FLAGS` are left out; nominal (7)
    and interpolated (8) values are kept. Long records are thinned to about 5000
    points per line; the time axis spans ``time_coverage_start`` to
    ``time_coverage_end`` when the stack records them.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack on ``(time, N_LEVELS)`` with ``serial`` and ``instrument_type``
        on ``N_LEVELS``.
    rows : list of tuple
        ``(varname, ylabel, invert, exclude_types)`` per row; a variable not in
        *ds* is skipped, and instruments whose type is in *exclude_types* are not
        drawn in that row.
    color_var : str
        The variable whose line colormap colours the instruments.
    dot_overlay : bool, optional
        Also mark each sample with a dot (for sparse, spiky records).
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure or None
        ``None`` when no row has an instrument with finite values.
    """
    import matplotlib.pyplot as plt

    rows = [r for r in rows if r[0] in ds.data_vars]
    if not rows:
        return None
    serials = [str(s) for s in ds["serial"].values]
    types = [str(t).lower() for t in ds["instrument_type"].values]
    step = max(1, ds.sizes["time"] // 5000)
    time = ds["time"].values[::step]
    colors, styles = level_line_styles(serials, color_var)

    fig, axs = plt.subplots(
        len(rows),
        1,
        figsize=(width_in, params.STACK_PANEL_ROW_IN * len(rows)),
        sharex=True,
        squeeze=False,
        layout="constrained",
    )
    handles: dict = {}
    for ax, (var, ylabel, invert, exclude) in zip(axs[:, 0], rows, strict=True):
        values = ds[var].values.copy()
        if f"{var}_qc" in ds.data_vars:
            values[np.isin(ds[f"{var}_qc"].values, params.QC_BAD_FLAGS)] = np.nan
        for i, serial in enumerate(serials):
            if exclude and types[i] in exclude:
                continue
            y = values[::step, i]
            if not np.any(np.isfinite(y)):
                continue
            (line,) = ax.plot(
                time,
                y,
                color=colors[serial],
                ls=styles[serial],
                lw=0.7,
                alpha=0.85,
                label=serial,
            )
            if dot_overlay:
                ax.plot(
                    time,
                    y,
                    ".",
                    color=colors[serial],
                    markersize=2,
                    linewidth=0,
                    alpha=0.85,
                )
            handles.setdefault(serial, line)
        if invert:
            ax.invert_yaxis()
        ax.set_ylabel(ylabel)
        grid_despine(ax)
        start, end = (
            ds.attrs.get("time_coverage_start"),
            ds.attrs.get("time_coverage_end"),
        )
        if start and end:
            # A coverage attribute that does not parse leaves the data's own limits.
            with contextlib.suppress(ValueError, TypeError):
                ax.set_xlim(
                    pd.Timestamp(start).tz_localize(None).to_datetime64(),
                    pd.Timestamp(end).tz_localize(None).to_datetime64(),
                )
    if not handles:
        plt.close(fig)
        return None
    date_axis(axs[-1, 0])
    axs[-1, 0].set_xlabel("Time")
    fig.align_ylabels(axs[:, 0])
    ordered = [s for s in serials if s in handles]
    fig.legend(
        handles=[handles[s] for s in ordered],
        labels=ordered,
        loc="outside right center",
        framealpha=0.8,
    )
    return fig
