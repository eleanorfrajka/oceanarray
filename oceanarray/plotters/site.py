"""Figures of a site's gridded record: sections over the years, profiles and T-S per deployment.

Tier-2 plotters for the site grid page; the numbers come from
:mod:`oceanarray.analysis.site`. Each ``draw_*`` takes plain arrays and returns a
Figure (or None when there is nothing to draw).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

from oceanarray.reports._vendored import design_tokens as report_tokens

from ..config import parameters as params
from .helpers import (
    _velocity_panel_style,
    deployment_color,
    deployment_line,
    legend_below,
)
from .primitives import date_axis, pcolormesh_panel
from .timeseries import _label_line_ends, _mark_deployments

if TYPE_CHECKING:
    import matplotlib.pyplot as plt

#: The part of a section panel's height (inches) that is not plotting area: its
#: title and the gap to the next panel.
_SECTION_FRAME_IN: float = 0.45

#: The velocity variables, drawn on one shared symmetric scale.
_VELOCITY = ("east_velocity", "north_velocity")


def section_cmap(var: str, *, anomaly: bool = False) -> Any:
    """Return the colormap of a pressure × time section of *var* (or of its anomaly).

    Velocity takes the mooring grid page's velocity map; temperature, salinity
    and their anomalies take :data:`parameters.SECTION_CMAPS`; any other
    variable its registry map.
    """
    if var in _VELOCITY:
        return _velocity_panel_style("east_velocity", np.array([]), 1.0)[2]
    key = f"{var}_anomaly" if anomaly else var
    return params.SECTION_CMAPS.get(
        key,
        "RdBu_r"
        if anomaly
        else params.CMAPS_BY_VARIABLE.get(var, params.DEFAULT_COLORMAP),
    )


def symmetric_limit(*fields: np.ndarray) -> float:
    """Return the shared ± limit of diverging fields: the larger of |2nd| and |98th| percentile."""
    values = np.concatenate([np.ravel(f) for f in fields]) if fields else np.array([])
    values = values[np.isfinite(values)]
    if not values.size:
        return 1.0
    lo, hi = np.percentile(values, [2, 98])
    return max(abs(float(lo)), abs(float(hi)), 1e-6)


#: The lines marking each instrument's pressure on a section: grey, a little
#: darker and wider than a hairline so they read over both ends of a colormap.
INSTRUMENT_LINE: dict = {"color": "0.55", "lw": 0.6}


def _edges(centers: np.ndarray) -> np.ndarray:
    """Return cell edges for regularly spaced pressure *centers*: midway between, half a step beyond."""
    if len(centers) == 1:  # one level: a cell 1 dbar thick
        return np.array([centers[0] - 0.5, centers[0] + 0.5])
    step = np.diff(centers)
    half = np.concatenate([[step[0]], step, [step[-1]]]) / 2
    return np.concatenate([[centers[0] - half[0]], centers + half[1:]])


def _contour_by_deployment(
    ax: Any,
    time: np.ndarray,
    pressure: np.ndarray,
    field: np.ndarray,
    levels: Sequence[float],
    owner: np.ndarray,
) -> None:
    """Contour *field* within each deployment's bins, so no line runs across a change.

    Black lines with a thin white edge, so they read on dark colours.
    """
    from matplotlib import patheffects

    edge = [patheffects.withStroke(linewidth=1.5, foreground="w")]
    for k in np.unique(owner[owner >= 0]):
        cols = np.flatnonzero(owner == k)
        if cols.size < 2 or not np.isfinite(field[cols]).any():
            continue
        cs = ax.contour(
            time[cols],
            pressure,
            field[cols].T,
            levels=list(levels),
            colors="k",
            linewidths=0.7,
        )
        cs.set(path_effects=edge)


def _isopycnal_ends(
    pressure: np.ndarray, field: np.ndarray, levels: Sequence[float], name: str
) -> dict[str, float]:
    """Return each level's pressure in the last time bin with values, labelled by *name*."""
    cols = np.flatnonzero(np.isfinite(field).any(axis=1))
    if not cols.size:
        return {}
    profile = field[cols[-1]]
    ok = np.isfinite(profile)
    p, v = pressure[ok], profile[ok]
    out = {}
    for level in levels:
        cross = np.flatnonzero(np.diff(np.sign(v - level)) != 0)
        if cross.size:
            # Linear between the pair, which may be in either order (an inversion).
            a = cross[0]
            out[f"{name} {level:g}"] = float(
                p[a] + (level - v[a]) * (p[a + 1] - p[a]) / (v[a + 1] - v[a])
            )
    return out


def _pressure_crop(
    data: Sequence[np.ndarray], p_edges: np.ndarray, axis: int
) -> tuple[tuple[float, float], float]:
    """Return the pressure range with values in any of *data*, and its share of the grid.

    Parameters
    ----------
    data : sequence of numpy.ndarray
        Fields with pressure along *axis*.
    p_edges : numpy.ndarray
        The grid's pressure cell edges.
    axis : int
        The pressure axis of *data*.

    Returns
    -------
    range : tuple of float
        Shallowest and deepest edge with values.
    share : float
        That range over the whole grid's, to shrink a panel by (same dbar per inch).
    """
    other = 1 - axis
    rows = np.flatnonzero(
        np.any([np.isfinite(d).any(axis=other) for d in data], axis=0)
    )
    cropped = (float(p_edges[rows[0]]), float(p_edges[rows[-1] + 1]))
    return cropped, (cropped[1] - cropped[0]) / float(p_edges[-1] - p_edges[0])


def draw_site_sections(
    panels: Sequence[dict],
    time: np.ndarray,
    pressure: np.ndarray,
    time_edges: np.ndarray,
    owner: np.ndarray,
    *,
    deployments: Sequence[tuple] = (),
    isopycnals: Sequence[tuple[np.ndarray, Sequence[float], str]] = (),
    crop: bool = False,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Stacked pressure × time sections of a site grid, sharing the time axis; return a Figure.

    Each panel is drawn with 20 discrete color levels, each cell between its
    time edges so a deployment's last, shorter bin ends exactly at the change.
    Turnarounds are shaded, each deployment's start is a thin dotted line
    marked by a ▼ above the top panel, and each deployment is named along the
    top; there are no horizontal gridlines, and the instruments measuring the
    panel's variable are grey lines at their pressures (:data:`INSTRUMENT_LINE`);
    isopycnals are black contours drawn within each deployment (no line runs
    across a change), labelled in the right margin at each level's last depth.

    Parameters
    ----------
    panels : sequence of dict
        One per panel: ``data`` (``(time, pressure)``), ``title``, ``units``,
        ``cmap``, and optionally ``limit`` (draw symmetric about zero, ±limit),
        ``instruments`` (the pressures of the instruments measuring the
        variable, on ``(time, n)``) and ``isopycnals`` (bool: overlay
        *isopycnals*).
    time, pressure : numpy.ndarray
        The sections' axes (the bins' centers).
    time_edges : numpy.ndarray
        The bins' edges, one more than *time*.
    owner : numpy.ndarray
        Each bin's deployment (-1 none), for the contours.
    deployments : sequence of tuple, optional
        ``(start, end, name)`` per deployment.
    isopycnals : sequence of tuple, optional
        ``(field on (time, pressure), levels, name)`` per density reference, e.g.
        ``(sigma0, [27.6, 27.7], "σ₀")``; each label is the name and value.
    crop : bool, optional
        Show only the pressures the panels have data at, in panels made shorter
        in proportion, so a dbar spans the same height as in an uncropped
        section of the same grid.
    width_in : float, optional
        Figure width in inches (the report slot's).

    Returns
    -------
    matplotlib.figure.Figure or None
        None when *panels* is empty.
    """
    import matplotlib.pyplot as plt

    panels = [p for p in panels if np.isfinite(p["data"]).any()]
    if not panels:
        return None
    p_edges = _edges(pressure.astype(float))
    p_range = (float(p_edges[0]), float(p_edges[-1]))
    row_in = params.GRID_PANEL_ROW_IN
    if crop:
        cropped, share = _pressure_crop([p["data"] for p in panels], p_edges, 1)
        # The plotting area shrinks with the pressure span; the title and the
        # margins (about _SECTION_FRAME_IN) do not.
        row_in = _SECTION_FRAME_IN + max(
            (params.GRID_PANEL_ROW_IN - _SECTION_FRAME_IN) * share, 0.5
        )
        p_range = cropped
    fig, axes = plt.subplots(
        len(panels),
        1,
        figsize=(width_in, row_in * len(panels)),
        sharex=True,
        squeeze=False,
        layout="constrained",
    )
    axes = list(axes[:, 0])
    edges = (time_edges, p_edges)
    for i, (ax, panel) in enumerate(zip(axes, panels, strict=True)):
        limit = panel.get("limit")
        # The deployment names sit above the top panel: lift its title over them.
        lift = {"axes.titlepad": 16} if i == 0 and deployments else {}
        with plt.rc_context(lift):
            pcolormesh_panel(
                fig,
                ax,
                np.asarray(panel["data"]).T,
                time,
                pressure,
                title=panel["title"],
                units=panel.get("units", ""),
                cmap=panel["cmap"],
                vmin=-limit if limit is not None else None,
                vmax=limit if limit is not None else None,
                date_fmt=False,
                edges=edges,
            )
        if panel.get("isopycnals") and isopycnals:
            ends: dict[str, float] = {}
            for field, levels, name in isopycnals:
                _contour_by_deployment(ax, time, pressure, field, levels, owner)
                ends |= _isopycnal_ends(pressure, field, levels, name)
            _label_line_ends(ax, ends, dict.fromkeys(ends, "k"))
        if panel.get("instruments") is not None:
            ax.plot(
                time,
                panel["instruments"],
                color=INSTRUMENT_LINE["color"],
                lw=INSTRUMENT_LINE["lw"],
                zorder=3,
            )
        ax.set_ylim(p_range[1], p_range[0])
    if deployments:
        _mark_deployments(axes, list(deployments))
    date_axis(axes[-1])
    return fig


def draw_site_profiles(
    columns: Sequence[dict],
    pressure: np.ndarray,
    names: Sequence[str],
    *,
    crop: bool = False,
    zero_line: bool = False,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Median profile of each deployment, one panel per variable; return a Figure.

    The panels are as tall as a section panel, so a dbar spans the same height
    on the page.

    Parameters
    ----------
    columns : sequence of dict
        One per panel: ``medians`` (``(deployment, pressure)``), ``title``.
    pressure : numpy.ndarray
        The grid's pressure levels.
    names : sequence of str
        The deployments' names, in time order.
    crop : bool, optional
        Show only the pressures with values, the figure made shorter in proportion.
    zero_line : bool, optional
        A thin black dashed line at zero over the whole height (velocity).
    width_in : float, optional
        Figure width in inches (the report slot's).

    Returns
    -------
    matplotlib.figure.Figure or None
        None when no column has a value.
    """
    import matplotlib.pyplot as plt

    columns = [c for c in columns if np.isfinite(c["medians"]).any()]
    if not columns:
        return None
    p_edges = _edges(pressure.astype(float))
    p_range, share = (float(p_edges[0]), float(p_edges[-1])), 1.0
    if crop:
        p_range, share = _pressure_crop([c["medians"] for c in columns], p_edges, 1)
    area = max((params.GRID_PANEL_ROW_IN - _SECTION_FRAME_IN) * share, 0.5)
    fig, axes = plt.subplots(
        1,
        len(columns),
        # A section panel's plotting height plus room for the labels, so a dbar
        # spans the same height on the page as in a section.
        figsize=(width_in, area + _SECTION_FRAME_IN + 0.5),
        sharey=True,
        squeeze=False,
        layout="constrained",
    )
    axes = list(axes[0])
    for ax, column in zip(axes, columns, strict=True):
        medians = column["medians"]
        for k, row in enumerate(medians):
            if np.isfinite(row).any():
                ax.plot(
                    row,
                    pressure,
                    lw=1.2,
                    label=names[k],
                    **deployment_line(k, len(names)),
                )
        finite = medians[np.isfinite(medians)]
        lo, hi = np.percentile(finite, [0.5, 99.5])
        pad = 0.05 * (hi - lo) if hi > lo else 0.1
        ax.set_xlim(lo - pad, hi + pad)
        ax.set_xlabel(column["title"])
        ax.grid(alpha=0.3)
        if zero_line:
            ax.axvline(0.0, color="k", lw=0.6, ls="--", zorder=1)
    axes[0].set_ylim(p_range[1], p_range[0])
    axes[0].set_ylabel(params.vlabel("pressure"))
    # Below the axes, so no deployment's line is hidden behind it.
    handles, _ = axes[-1].get_legend_handles_labels()
    legend_below(fig, handles)
    return fig


def draw_site_ts(
    theta: np.ndarray,
    salinity: np.ndarray,
    names: Sequence[str],
    longitude: float | None,
    latitude: float | None,
    *,
    sigma0_levels: Sequence[float] = (),
    sigma2_levels: Sequence[float] = (),
    pressure: np.ndarray | None = None,
    pins: Sequence[Sequence[float]] | None = None,
    width_in: float = report_tokens.W_HALF,
) -> plt.Figure | None:
    """θ-S of each deployment's median profile, one line per deployment; return a Figure.

    Potential temperature, so the σ₀ (dashed) and σ₂ (solid) contours, computed
    from it at the site's position, hold at every depth. The axes span the full
    range of the medians. With *pressure* and *pins*, a small circle in the
    deployment's color marks the curve at each T-and-S instrument's mean
    pressure: where the curve is pinned by data rather than filled by the grid.
    An instrument up to half a grid step beyond the curve's shallowest or deepest
    level (its own level's median sits there) is marked at that end.

    Parameters
    ----------
    theta, salinity : numpy.ndarray
        Median potential temperature (°C) and Practical Salinity per deployment
        and level, ``(deployment, pressure)``.
    names : sequence of str
        The deployments' names, in time order.
    longitude, latitude : float or None
        The site's position, for Absolute Salinity. None when unknown: *theta* is
        then in-situ temperature, labeled so, and no isopycnals are drawn.
    sigma0_levels, sigma2_levels : sequence of float, optional
        The isopycnals to draw (kg m⁻³).
    pressure : numpy.ndarray, optional
        The grid's pressure levels (dbar), the second axis of *theta*.
    pins : sequence of sequence of float, optional
        Per deployment, the mean pressures of its T-and-S instruments (see
        :func:`oceanarray.analysis.site.instrument_mean_pressures`).
    width_in : float, optional
        Figure width in inches (the report slot's).

    Returns
    -------
    matplotlib.figure.Figure or None
        None when no deployment has both θ and S.
    """
    import matplotlib.pyplot as plt

    from oceanarray.analysis.derived import potential_density_grid

    ok = np.isfinite(theta) & np.isfinite(salinity)
    if not ok.any():
        return None
    fig, ax = plt.subplots(figsize=(width_in, width_in * 0.9), layout="constrained")
    for k in range(theta.shape[0]):
        if ok[k].any():
            ax.plot(
                np.where(ok[k], salinity[k], np.nan),
                np.where(ok[k], theta[k], np.nan),
                lw=1.3,
                label=names[k],
                **deployment_line(k, len(names)),
            )
            if pins is not None and pressure is not None and k < len(pins):
                p_all = np.asarray(pressure, dtype=float)
                p_ok = p_all[ok[k]]
                half = 0.5 * float(np.median(np.diff(p_all))) if p_all.size > 1 else 0.0
                at = [
                    float(np.clip(x, p_ok.min(), p_ok.max()))
                    for x in pins[k]
                    if p_ok.size and p_ok.min() - half <= x <= p_ok.max() + half
                ]
                if at:
                    ax.plot(
                        np.interp(at, p_ok, salinity[k][ok[k]]),
                        np.interp(at, p_ok, theta[k][ok[k]]),
                        ls="none",
                        marker="o",
                        ms=4,
                        color=deployment_color(k, len(names)),
                        mec="black",
                        mew=0.4,
                        zorder=4,
                    )
    # The full range of the medians, so a bad record stays in view.
    s_lo, s_hi = float(salinity[ok].min()), float(salinity[ok].max())
    t_lo, t_hi = float(theta[ok].min()), float(theta[ok].max())
    s_pad = max(0.1 * (s_hi - s_lo), 0.01)
    t_pad = max(0.1 * (t_hi - t_lo), 0.05)
    s_lim = (s_lo - s_pad, s_hi + s_pad)
    t_lim = (t_lo - t_pad, t_hi + t_pad)
    known = longitude is not None and latitude is not None
    # σ0 dashed and σ2 solid: on a T-S diagram they cross, so they must differ.
    for levels, reference, style, name in (
        (sigma2_levels, 2000, "solid", r"$\sigma_2$"),
        (sigma0_levels, 0, "dashed", r"$\sigma_0$"),
    ):
        if levels and known:
            sg, tg, sigma = potential_density_grid(
                s_lim, t_lim, latitude, longitude, n_grid=100, reference_dbar=reference
            )
            cs = ax.contour(
                sg,
                tg,
                sigma,
                levels=list(levels),
                colors="0.4",
                linewidths=0.6,
                linestyles=style,
            )
            ax.clabel(cs, fontsize="small", fmt=lambda v, n=name: f"{n} {v:g}")
    ax.set_xlim(*s_lim)
    ax.set_ylim(*t_lim)
    ax.set_xlabel(params.vlabel("salinity"))
    ax.set_ylabel(params.vlabel("potential_temperature" if known else "temperature"))
    ax.legend(fontsize=report_tokens.ANNOT_FS, ncols=2)
    return fig
