"""Tier-2 time-series and gridded-section figures for the report pages.

Grid sections (pressure × time): :func:`draw_grid_hydro`,
:func:`draw_grid_velocity_stacked`, :func:`draw_grid_sigma`, :func:`draw_grid_n2`,
:func:`draw_grid_timeseries` (``draw_grid`` is deprecated, removed in 0.7.0). Stack:
:func:`draw_stack_timeseries`. Instrument: :func:`draw_analog_timeseries`.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

from oceanarray import _deprecated
from oceanarray.analysis.derived import (
    level_of_max_speed,
    mooring_position,
    n_squared,
    speed_direction,
)

if TYPE_CHECKING:
    import matplotlib.pyplot as plt
    import xarray as xr

    from oceanarray.analysis.derived import DerivedFields

from oceanarray.pipeline.masking import masked
from oceanarray.plotters.primitives import nice_colorbar_ticks
from oceanarray.reports._vendored import design_tokens as report_tokens

from ..config import parameters as params
from ..config.unit_tables import unit_label
from .helpers import (
    DEPLOYMENT_BOUNDARY,
    OKABE_ITO,
    as_dataset,
    grid_despine,
    level_line_styles,
    marker_flags,
)
from .primitives import (
    SECTION_AXES_IN,
    SectionScale,
    apply_section_scale,
    colorbar_norm,
    date_axis,
    date_offset_left,
    figure_title,
    instrument_marks,
    pcolormesh_panel,
    plot_title,
    pressure_axis,
    pressure_extent,
    section_layout,
    section_scale,
)

log = logging.getLogger(__name__)


def _draw_grid(
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
        Figure width (inches), the report slot; default the full content width.

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


#: Percentiles of a section's values its colour limits are set at; values beyond
#: them take the end colours (the colorbar has arrows).
SECTION_PERCENTILES: tuple[float, float] = (1.0, 99.0)

#: Panel titles of the grid hydrography sections.
_HYDRO_TITLES: dict[str, str] = {
    "temperature": "Temperature",
    "salinity": "Salinity (PSU)",
    "oxygen_saturation_pct": "Oxygen saturation",
}


def _percentile_limits(
    data: np.ndarray, percentiles: tuple[float, float] = SECTION_PERCENTILES
) -> tuple[float | None, float | None]:
    """Return the colour limits of *data* at *percentiles* (None, None when empty)."""
    finite = np.asarray(data, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return None, None
    lo, hi = (float(np.percentile(finite, q)) for q in percentiles)
    if hi <= lo:
        hi = lo + 1e-6
    return lo, hi


def _marks_for(
    instrument_pressures: Mapping[str, Sequence[float]] | Sequence[float] | None,
    var: str,
) -> Sequence[float]:
    """Return the instrument pressures marked on *var*'s panel."""
    if instrument_pressures is None:
        return ()
    if isinstance(instrument_pressures, Mapping):
        return instrument_pressures.get(var, ())
    return instrument_pressures


def hydro_scale(ds: xr.Dataset) -> SectionScale | None:
    """Return the vertical scale of a grid page: its hydrography panels' dbar per inch.

    Parameters
    ----------
    ds : xarray.Dataset
        A grid with ``temperature`` and/or ``salinity`` on ``(time, pressure)``.

    Returns
    -------
    SectionScale or None
        None without temperature or salinity values.
    """
    fields = [
        ds[v].transpose("time", "pressure").values
        for v in ("temperature", "salinity")
        if v in ds.data_vars
    ]
    if not fields:
        return None
    return section_scale(ds["pressure"].values, fields)


def draw_grid_hydro(
    ds: xr.Dataset,
    var_bounds: dict | None = None,
    *,
    width_in: float = report_tokens.W_FULL,
    instrument_pressures: Mapping[str, Sequence[float]] | Sequence[float] | None = None,
    scale: SectionScale | None = None,
) -> plt.Figure | None:
    """Stacked temperature / salinity (PSU) sections for the grid report.

    Each panel shows the variable on the pressure grid against time (surface up),
    over the levels with values, at the page's vertical scale (*scale*).  Colour
    limits are the 1st and 99th percentiles of the values unless *var_bounds*
    gives them; values beyond take the end colours (arrows on the colorbar).
    Instrument pressures are marked by triangles at the right edge.

    **Salinity**: the ``salinity`` variable, or, when absent but ``conductivity``
    and ``temperature`` are present, practical salinity from ``gsw.SP_from_C``.

    Parameters
    ----------
    ds : xarray.Dataset
        Gridded mooring dataset with dimensions ``(time, pressure)``.
    var_bounds : dict, optional
        Colour limits keyed ``"t_lim"``, ``"s_lim"``, ``"o2_lim"`` (each
        ``(vmin, vmax)``), used instead of the percentiles.
    width_in : float, optional
        Figure width in inches (the display slot).
    instrument_pressures : Mapping of str to sequence of float, or sequence of float, optional
        Pressures (dbar) of the instruments behind each panel, keyed by variable
        (or one sequence for every panel); none marked when omitted.
    scale : SectionScale, optional
        Vertical scale; default: the panels fill :data:`SECTION_AXES_IN`.

    Returns
    -------
    matplotlib.figure.Figure or None
        Figure, or None if no hydrographic data are present.
    """
    import xarray as _xr

    var_bounds = var_bounds or {}
    panels = [
        v
        for v in ("temperature", "salinity", "oxygen_saturation_pct")
        if v in ds.data_vars
    ]
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
            ds = ds.assign(
                salinity=_xr.DataArray(
                    gsw.SP_from_C(C, T, p_1d[np.newaxis, :]),
                    dims=("time", "pressure"),
                    coords={"time": ds["time"], "pressure": ds["pressure"]},
                    attrs={"units": "1", "long_name": "Practical Salinity"},
                )
            )
            panels.insert(1, "salinity")
        except (KeyError, ValueError) as e:
            log.warning("salinity not computed from conductivity: %s", e)
    if not panels:
        return None

    pressure = ds["pressure"].values
    time = ds["time"].values
    fields = {v: ds[v].transpose("time", "pressure").values for v in panels}
    extent = pressure_extent(pressure, list(fields.values()))
    if extent is None:
        return None
    if scale is None:
        scale = section_scale(pressure, list(fields.values()))
    h = scale.height_in(extent) if scale is not None else SECTION_AXES_IN
    fig, axes, caxes = section_layout(width_in, [h] * len(panels))
    for ax, cax, var in zip(axes, caxes, panels, strict=True):
        data = fields[var].T
        key = {"temperature": "t_lim", "salinity": "s_lim"}.get(var, "o2_lim")
        passed = var_bounds.get(key)
        vmin, vmax = passed if passed is not None else _percentile_limits(data)
        units = "" if var == "salinity" else params.vunit(var) or ""
        pcolormesh_panel(
            fig,
            ax,
            data,
            time,
            pressure,
            title=_HYDRO_TITLES[var],
            cmap=params.SECTION_CMAPS.get(var, "viridis"),
            vmin=vmin,
            vmax=vmax,
            n=11 if var == "oxygen_saturation_pct" else 20,
            units=units,
            cb_label=None if units else "",
            date_fmt=False,
            cax=cax,
            extend="both",
        )
        apply_section_scale(ax, extent, scale)
        instrument_marks(ax, _marks_for(instrument_pressures, var))
    for ax in axes[:-1]:
        ax.tick_params(labelbottom=False)
    date_axis(axes[-1])
    return fig


#: The velocity sections of a grid page, with their panel titles.
_VELOCITY_TITLES: dict[str, str] = {
    "east_velocity": "Eastward velocity",
    "north_velocity": "Northward velocity",
    "up_velocity": "Upward velocity",
    "current_speed": "Current speed",
    "current_direction": "Direction toward which the current flows",
}


def draw_grid_velocity_stacked(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_FULL,
    instrument_pressures: Sequence[float] | None = None,
    scale: SectionScale | None = None,
) -> plt.Figure | None:
    """Velocity component, speed and direction sections for the grid report.

    Eastward, northward and upward velocity, then current speed and the direction
    toward which the current flows.  The panels are cropped to the levels with
    velocity and drawn at the page's vertical scale (*scale*: the hydrography
    panels' dbar per inch, so a dbar spans the same height in both figures), with
    the same pressure tick step.  The three components share one colour scale,
    symmetric about zero, to the larger of the magnitudes of the 1st and 99th
    percentiles of all their values (values beyond take the end colours), and one
    colorbar spanning their panels.  Speed (0 to its 98th percentile) and
    direction (a cyclic map, 0–360°) have their own colorbars.

    Parameters
    ----------
    ds : xarray.Dataset
        Gridded dataset with dimensions ``(time, pressure)``.  Speed and direction
        are ``current_speed`` / ``current_direction`` when present, else computed
        from the horizontal components.
    width_in : float, optional
        Figure width in inches (the display slot).
    instrument_pressures : sequence of float, optional
        Pressures (dbar) of the current meters, marked by triangles at the right
        edge of each panel.
    scale : SectionScale, optional
        Vertical scale; default: that of *ds*'s own hydrography
        (:func:`hydro_scale`).

    Returns
    -------
    matplotlib.figure.Figure or None
        None when no velocity component is present or all are empty.
    """
    from oceanarray.analysis.derived import speed_direction

    from .helpers import _velocity_panel_style

    components = [
        v
        for v in ("east_velocity", "north_velocity", "up_velocity")
        if v in ds.data_vars
    ]
    if not components:
        return None
    pressure = ds["pressure"].values
    time = ds["time"].values
    fields = {v: ds[v].transpose("time", "pressure").values for v in components}
    extent = pressure_extent(pressure, list(fields.values()))
    if extent is None:
        return None
    if {"east_velocity", "north_velocity"} <= set(components):
        if {"current_speed", "current_direction"} <= set(ds.data_vars):
            spd = ds["current_speed"].transpose("time", "pressure").values
            dirn = ds["current_direction"].transpose("time", "pressure").values
        else:
            spd, dirn = speed_direction(
                fields["east_velocity"], fields["north_velocity"]
            )
        fields["current_speed"], fields["current_direction"] = spd, dirn
    if scale is None:
        scale = hydro_scale(ds) or section_scale(pressure, list(fields.values()))
    lo, hi = _percentile_limits(np.concatenate([fields[v].ravel() for v in components]))
    vmax = max(abs(lo or 0.0), abs(hi or 0.0), 1e-4)
    bounds, _, cmap, _ = _velocity_panel_style("east_velocity", np.array([]), vmax)
    norm = mcolors.BoundaryNorm(bounds, ncolors=256, extend="both")

    panels = list(fields)
    h = scale.height_in(extent)
    fig, axes, caxes = section_layout(width_in, [h] * len(panels), colorbar="each")
    # One colorbar spans the component panels: replace their own bars by one.
    n_comp = len(components)
    x0, w = caxes[0].get_position().x0, caxes[0].get_position().width
    y_bot = caxes[n_comp - 1].get_position().y0
    y_top = caxes[0].get_position().y1
    for cax in caxes[:n_comp]:
        cax.remove()
    shared = fig.add_axes([x0, y_bot, w, y_top - y_bot])
    pc = None
    for i, (ax, var) in enumerate(zip(axes, panels, strict=True)):
        data = fields[var].T
        if i < n_comp:
            pc = ax.pcolormesh(
                time, pressure, data, shading="nearest", cmap=cmap, norm=norm
            )
        else:
            finite = data[np.isfinite(data)]
            b_own, n_own, c_own, label = _velocity_panel_style(var, finite, vmax)
            extend = "max" if var == "current_speed" else "neither"
            if extend == "max":
                n_own = mcolors.BoundaryNorm(b_own, ncolors=256, extend="max")
            own = ax.pcolormesh(
                time, pressure, data, shading="nearest", cmap=c_own, norm=n_own
            )
            ticks = (
                [0, 90, 180, 270, 360]
                if var == "current_direction"
                else nice_colorbar_ticks(float(b_own[0]), float(b_own[-1]))
            )
            cb = fig.colorbar(own, cax=caxes[i], extend=extend, ticks=ticks)
            cb.ax.set_title(label, fontsize=report_tokens.ANNOT_FS)
        pressure_axis(ax)
        ax.grid(False, axis="y")
        apply_section_scale(ax, extent, scale)
        plot_title(ax, _VELOCITY_TITLES[var])
        instrument_marks(ax, instrument_pressures or ())
    cb = fig.colorbar(
        pc,
        cax=shared,
        extend="both",
        ticks=nice_colorbar_ticks(float(bounds[0]), float(bounds[-1])),
    )
    cb.ax.set_title(params.vunit("east_velocity"), fontsize=report_tokens.ANNOT_FS)
    for ax in axes[:-1]:
        ax.tick_params(labelbottom=False)
    date_axis(axes[-1])
    return fig


def draw_grid_sigma(
    ds: xr.Dataset, *, width_in: float = report_tokens.W_FULL
) -> plt.Figure | None:
    """Stacked sigma0 pcolormesh panel(s) for the stratification section.

    Returns ``None`` when no sigma variables are present.

    """
    import matplotlib.pyplot as plt

    from ..config import parameters as params

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


#: Panel titles of the potential-density sections, by reference.
_SIGMA_TITLES: dict[str, str] = {
    "sigma0": "Potential density anomaly σ0 (referenced to the surface)",
    "sigma2": "Potential density anomaly σ2 (referenced to 2000 dbar)",
}


def draw_grid_potential_density(
    ds: xr.Dataset,
    reference: str = "sigma0",
    *,
    width_in: float = report_tokens.W_FULL,
    derived: DerivedFields | None = None,
    density: Mapping[str, xr.DataArray] | None = None,
    scale: SectionScale | None = None,
) -> plt.Figure | None:
    """One potential-density section (σ0 or σ2) over the whole grid column.

    σ is computed from potential temperature and practical salinity on the grid
    (:func:`oceanarray.analysis.derived.potential_density`), over the full column
    at one reference pressure.  Colour limits are the 1st and 99th percentiles,
    with values beyond in the end colours.

    Parameters
    ----------
    ds : xarray.Dataset
        A grid with ``temperature``, ``salinity`` and a position.
    reference : {"sigma0", "sigma2"}, optional
        The reference pressure: the surface (default) or 2000 dbar.
    width_in : float, optional
        Figure width in inches (the display slot).
    derived : DerivedFields or None, optional
        The page's derived quantities (θ and the position), used when *density*
        is not given; computed from *ds* when neither is given.
    density : Mapping of str to xarray.DataArray, optional
        σ0 and σ2 computed once for the page
        (:func:`oceanarray.analysis.derived.potential_density`), so the two
        sections share one computation.
    scale : SectionScale, optional
        Vertical scale; default: that of *ds*'s hydrography.

    Returns
    -------
    matplotlib.figure.Figure or None
        None when σ cannot be computed (no temperature, salinity or position).
    """
    from oceanarray.analysis.derived import derived_fields, potential_density

    if reference not in _SIGMA_TITLES:
        raise ValueError(f"reference must be 'sigma0' or 'sigma2', not {reference!r}")
    if density is None:
        density = potential_density(ds, derived or derived_fields(ds))
    sigma = (density or {}).get(reference)
    if sigma is None:
        return None
    data = sigma.transpose("time", "pressure").values
    pressure = ds["pressure"].values
    extent = pressure_extent(pressure, [data])
    if extent is None:
        return None
    if scale is None:
        scale = hydro_scale(ds)
    h = scale.height_in(extent) if scale is not None else SECTION_AXES_IN
    fig, axes, caxes = section_layout(width_in, [h])
    vmin, vmax = _percentile_limits(data)
    pcolormesh_panel(
        fig,
        axes[0],
        data.T,
        ds["time"].values,
        pressure,
        title=_SIGMA_TITLES[reference],
        cmap=params.DENSITY_COLORMAP,
        vmin=vmin,
        vmax=vmax,
        units=params.vunit("potential_density"),
        date_fmt=True,
        cax=caxes[0],
        extend="both",
    )
    apply_section_scale(axes[0], extent, scale)
    return fig


def draw_grid_n2(
    ds: xr.Dataset,
    lat: float = 0.0,
    *,
    width_in: float = report_tokens.W_FULL,
    derived: DerivedFields | None = None,
) -> plt.Figure | None:
    """Plot buoyancy frequency squared N² on the pressure-time grid.

    N² is computed by :func:`oceanarray.analysis.derived.n_squared` (TEOS-10
    ``Nsquared`` between neighbouring grid levels).

    Parameters
    ----------
    ds : xr.Dataset
        Gridded dataset with ``temperature`` and ``salinity`` on
        ``(time, pressure)``.
    lat : float, optional
        Mooring latitude (decimal degrees).
    width_in : float, optional
        Figure width in inches.
    derived : DerivedFields or None, optional
        The page's derived quantities; its longitude is used for Absolute
        Salinity (read from *ds* when not given; a warning when unknown).

    Returns
    -------
    plt.Figure or None
        Figure, or None when temperature or salinity are absent.
    """
    import matplotlib.pyplot as plt

    if "temperature" not in ds.data_vars or "salinity" not in ds.data_vars:
        return None

    time_vals = ds["time"].values
    lon = (
        derived.longitude
        if derived is not None
        else mooring_position(ds, warn=False)[1]
    )
    N2, p_mid_1d = n_squared(
        ds["temperature"].transpose("pressure", "time").values.astype(float),
        ds["salinity"].transpose("pressure", "time").values.astype(float),
        ds["pressure"].values.astype(float),
        lat,
        lon,
    )
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
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_FULL,
    derived: DerivedFields | None = None,
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
        Figure width (inches), the report slot; default the full content width.
    derived : DerivedFields or None, optional
        The page's derived quantities; its ``speed`` is used instead of being
        computed again.

    Returns
    -------
    plt.Figure or None
        Figure, or None if horizontal velocity data are absent or all-NaN.

    """
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    has_horiz = all(v in ds.data_vars for v in ("east_velocity", "north_velocity"))
    if not has_horiz:
        return None

    east = ds["east_velocity"].values  # (time, pressure)
    north = ds["north_velocity"].values
    pressure = ds["pressure"].values  # 1-D
    time_vals = ds["time"].values

    if derived is not None and derived.speed is not None:
        spd = np.asarray(derived.speed.transpose(*ds["east_velocity"].dims), float)
    else:
        spd, _ = speed_direction(east, north)
    k_max = level_of_max_speed(spd)
    if k_max is None:
        return None
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
    axs[0].set_ylabel(params.vlabel("current_speed"))
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
    nc_path: Path | xr.Dataset,
    analog_vars: list[str],
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Full-record time series for analog channel variables, one panel per variable.

    Returns ``None`` when the dataset lacks a ``time`` dimension.

    Parameters
    ----------
    nc_path : Path or xarray.Dataset
        The stage-3 or stack NetCDF file, or the dataset already read from it.
    analog_vars : list of str
        Variable names to plot (caller must ensure the list is non-empty).

    width_in : float, optional
        Figure width (inches), the report slot; default the full content width.

    Returns
    -------
    plt.Figure or None

    """
    import matplotlib.pyplot as plt

    ds = as_dataset(nc_path)
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


def _as_time_level(values: np.ndarray) -> np.ndarray:
    """Return *values* as ``(time, level)``: a 1-D record becomes one column.

    Parameters
    ----------
    values : numpy.ndarray
        ``(time,)`` or ``(time, level)``.

    Returns
    -------
    numpy.ndarray
        ``(time, level)``.
    """
    values = np.asarray(values)
    return values[:, np.newaxis] if values.ndim == 1 else values


def draw_stack_timeseries(
    ds: xr.Dataset,
    rows: list[tuple[str, str, bool, set[str] | None]],
    *,
    color_var: str,
    dot_overlay: bool = False,
    deployments: list[tuple[np.datetime64, np.datetime64, str]] | None = None,
    legend_shallow_first: bool = False,
    width_in: float = report_tokens.W_FULL,
    label_lines: bool = False,
    row_heights: Sequence[float] | None = None,
    mark_suspect: bool = False,
    record: dict | None = None,
    keyed: Sequence[str] | None = None,
) -> plt.Figure | None:
    """Stack time series: one row per variable, one line per instrument level.

    The rows share the time axis and one instrument colour scheme (that of
    *color_var*, deep-first; see :func:`~oceanarray.plotters.helpers.level_line_styles`),
    so a colour tracks an instrument through every row, and one legend on the
    right lists the instruments plotted. Values the stack's ``mask_flags`` rule
    hides (ours or the originator's flag; default 4) are left out. Long records are
    thinned to about 5000 points per line. The time axis spans
    ``time_coverage_start`` to ``time_coverage_end`` when the stack records them;
    with *deployments* (a site), the data drawn.

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
    deployments : list of (start, end, name), optional
        For a concatenated stack: each deployment's window. The gaps between them
        are shaded, each boundary gets a thin line, and each deployment is named
        along the top.
    legend_shallow_first : bool, optional
        List the levels shallowest first in the legend (the colors keep the
        deep-first order of *ds*).
    width_in : float, optional
        Figure width (inches), the report slot; default the full content width.
    label_lines : bool, optional
        Name each line by its serial at its right end in the first row (spread
        apart so they do not overlap) instead of a legend; instruments with no
        line in the first row get a small legend.
    row_heights : sequence of float, optional
        Relative height of each row (e.g. a shorter upward-velocity row); equal
        rows when not given.
    mark_suspect : bool, optional
        Mark the values flagged suspect (oceanarray's or the originator's flag)
        with small black dots.
    record : dict or None, optional
        Filled with what was drawn: ``serials`` (the instruments with a line),
        ``labelled`` (those named at their line's end), ``suspect`` (values
        marked) and ``legend`` (the serials in a legend).
    keyed : sequence of str, optional
        Instruments already named on another figure of the page (the
        hydrography's line labels): left out of the legend, so the legend lists
        only the others; with one instrument and nothing keyed, no legend (the
        caption names it).

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

    heights = (
        [float(h) for h, r in zip(row_heights, rows, strict=False)]
        if row_heights is not None
        else [1.0] * len(rows)
    )
    fig, axs = plt.subplots(
        len(rows),
        1,
        figsize=(width_in, params.STACK_PANEL_ROW_IN * sum(heights)),
        sharex=True,
        squeeze=False,
        layout="constrained",
        height_ratios=heights,
    )
    handles: dict = {}
    spans: list[tuple[np.datetime64, np.datetime64]] = []
    first_row: dict[str, float] = {}
    n_suspect = 0
    for row_i, (ax, (var, ylabel, invert, exclude)) in enumerate(
        zip(axs[:, 0], rows, strict=True)
    ):
        # (time, level); a one-level variable may come back as (time,).
        values = _as_time_level(masked(ds, var))
        suspect = marker_flags(ds, var) if mark_suspect else None
        if suspect is not None:
            suspect = _as_time_level(suspect)
        for i, serial in enumerate(serials):
            if exclude and types[i] in exclude:
                continue
            y = values[::step, i]
            finite = np.flatnonzero(np.isfinite(y))
            if not finite.size:
                continue
            spans.append((time[finite[0]], time[finite[-1]]))
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
            if row_i == 0:
                first_row[serial] = float(y[finite[-1]])
            if suspect is not None:
                s = (suspect[::step, i] == 3) & np.isfinite(y)
                if s.any():
                    n_suspect += int(s.sum())
                    ax.plot(time[s], y[s], ".", color="k", markersize=2.5, zorder=3)
        if invert:
            ax.invert_yaxis()
        ax.set_ylabel(ylabel)
        grid_despine(ax)
        start, end = (
            ds.attrs.get("time_coverage_start"),
            ds.attrs.get("time_coverage_end"),
        )
        if start and end and not deployments:
            # A coverage attribute that does not parse leaves the data's own limits.
            with contextlib.suppress(ValueError, TypeError):
                ax.set_xlim(
                    pd.Timestamp(start).tz_localize(None).to_datetime64(),
                    pd.Timestamp(end).tz_localize(None).to_datetime64(),
                )
    if not handles:
        plt.close(fig)
        return None
    if deployments:
        _mark_deployments(list(axs[:, 0]), deployments)
        # A site's axis spans its data, not the deployment windows.
        axs[0, 0].set_xlim(min(t for t, _ in spans), max(t for _, t in spans))
    date_axis(axs[-1, 0])
    axs[-1, 0].set_xlabel("Time")
    fig.align_ylabels(axs[:, 0])
    # Levels that share a name (a depth across deployments) share one legend entry.
    ordered = [s for s in dict.fromkeys(serials) if s in handles]
    if legend_shallow_first:
        ordered.reverse()
    in_legend = ordered
    if label_lines and first_row:
        _label_line_ends(axs[0, 0], first_row, colors)
        in_legend = [s for s in ordered if s not in first_row]
    elif keyed is not None:
        in_legend = [s for s in ordered if s not in set(keyed)]
        if len(ordered) == 1 and not keyed:
            in_legend = []
    if in_legend:
        fig.legend(
            handles=[handles[s] for s in in_legend],
            labels=in_legend,
            loc="outside right center",
            framealpha=0.8,
            fontsize="small" if label_lines else None,
        )
    if record is not None:
        record.update(
            serials=ordered,
            labelled=list(first_row) if label_lines else [],
            suspect=n_suspect,
            legend=in_legend,
        )
    return fig


def _label_line_ends(
    ax: plt.Axes, ends: Mapping[str, float], colors: Mapping[str, object]
) -> None:
    """Write each serial just right of the axes at its line's last value, spread apart.

    Labels closer than about one line of text are pushed apart vertically, in
    order, so none overlaps another; each keeps its line's colour.
    """
    fig = ax.figure
    fig.canvas.draw()
    to_px = ax.transData.transform
    height = ax.bbox.height
    gap = 9.0 * fig.dpi / 72.0  # one small label's height, in pixels
    ys = sorted(
        (
            (ax.transAxes.inverted().transform(to_px((0, v)))[1], s)
            for s, v in ends.items()
        ),
        key=lambda p: p[0],
    )
    placed: list[tuple[float, str]] = []
    for frac, serial in ys:
        y = min(max(frac, 0.0), 1.0) * height
        if placed and y - placed[-1][0] < gap:
            y = placed[-1][0] + gap
        placed.append((y, serial))
    overflow = placed[-1][0] - height if placed else 0.0
    if overflow > 0:  # shift down, keeping the spacing, so the top label stays in
        placed = [(y - overflow, s) for y, s in placed]
    for y, serial in placed:
        ax.annotate(
            serial,
            xy=(1.0, y / height),
            xycoords="axes fraction",
            xytext=(3, 0),
            textcoords="offset points",
            va="center",
            ha="left",
            fontsize="small",
            color=colors[serial],
            annotation_clip=False,
        )


def _mark_deployments(
    axes: list, deployments: list[tuple[np.datetime64, np.datetime64, str]]
) -> None:
    """Shade the gaps between deployments, line each boundary, name each deployment on top.

    Each boundary line also gets a ▼ just above the top axes, pointing down at it,
    on the strip where the deployment names are. The names and ▼ stay out of the
    layout (the top panel's title is lifted over them), so a name centred beyond a
    cropped time axis cannot collapse it.
    """
    import matplotlib.dates as mdates
    import matplotlib.transforms as mtransforms

    spans = sorted(deployments, key=lambda d: d[0])
    for ax in axes:
        for (_s0, e0, _n0), (s1, _e1, _n1) in zip(spans, spans[1:], strict=False):
            if s1 > e0:
                ax.axvspan(e0, s1, color="0.85", alpha=0.7, lw=0, zorder=0)
            ax.axvline(s1, **DEPLOYMENT_BOUNDARY, zorder=1)
    top = axes[0]
    trans = mtransforms.blended_transform_factory(top.transData, top.transAxes)
    # The triangle's tip touches the top of the axes: its center sits half a
    # marker above.
    above = mtransforms.offset_copy(trans, fig=top.figure, y=2.5, units="points")
    for start, _end, _label in spans[1:]:
        top.plot(
            mdates.date2num(start),
            1.0,
            marker="v",
            ms=5,
            color=DEPLOYMENT_BOUNDARY["color"],
            transform=above,
            clip_on=False,
            zorder=5,
        )[0].set_in_layout(False)
    for start, end, label in spans:
        mid = mdates.date2num(start + (end - start) / 2)
        top.text(
            mid,
            1.02,
            label,
            transform=trans,
            ha="center",
            va="bottom",
            fontsize="small",
            in_layout=False,
        )


draw_grid = _deprecated.alias("oceanarray.plotters.timeseries.draw_grid", _draw_grid)
