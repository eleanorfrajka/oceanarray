"""Tier-1 data-agnostic plotting primitives.

These functions accept pre-processed arrays (not xarray Datasets) and have no
knowledge of oceanographic variable naming conventions.  They are the lowest
layer in the three-tier plotters architecture:

  Tier 1: primitives.py    — generic, array-in / Figure-out
  Tier 2: _current.py etc. — domain wrappers (xr.Dataset-in / Figure-out)
  Tier 3: report/_plots.py — thin wrappers (path-in / base64-out)

Post-OdB: add plot_vector_heatmap, plot_section, plot_spectrum,
plot_polar_histogram, plot_timeseries.
"""

from __future__ import annotations

from typing import Any

import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.collections import LineCollection

from oceanarray.reports._vendored import design_tokens as report_tokens

from ..config import parameters as params
from ..utilities import _nice_colorbar_bounds, nice_colorbar_ticks
from .helpers import grid_despine


def plot_title(ax: Any, text: str, *, loc: str = "left", **kwargs: Any) -> Any:
    """Set an axes (panel) title, left-aligned by default.

    The single place panel-title alignment is decided for report figures, so the
    left-vs-centre choice for every panel title can be switched here in one line.
    Figure-level titles that span several panels use :func:`figure_title`
    (centred) instead — do not route those through this helper.  Colorbar unit
    labels (``cb.ax.set_title``) are not panel titles and must not use this
    helper either.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
        Axes whose title is set.
    text : str
        Title text.
    loc : str, optional
        Horizontal alignment passed to ``set_title`` (default ``"left"``).
    **kwargs : Any
        Forwarded to ``matplotlib.axes.Axes.set_title`` (e.g. ``fontsize``).

    Returns
    -------
    matplotlib.text.Text
        The created title artist.

    """
    return ax.set_title(text, loc=loc, **kwargs)


def figure_title(fig: Any, text: str, **kwargs: Any) -> Any:
    """Set a figure-level title (``suptitle``), centred over all panels.

    Use for a title that spans a multi-panel figure; per-panel titles use
    :func:`plot_title` (left-aligned).  Keeping both in one module makes the
    left-vs-centre policy a single switch point.

    Parameters
    ----------
    fig : matplotlib.figure.Figure
        Figure whose suptitle is set.
    text : str
        Title text.
    **kwargs : Any
        Forwarded to ``matplotlib.figure.Figure.suptitle`` (e.g. ``y``, ``fontsize``).

    Returns
    -------
    matplotlib.text.Text
        The created suptitle artist.

    """
    return fig.suptitle(text, **kwargs)


def pcolormesh_panel(
    fig: Any,
    ax: Any,
    data: np.ndarray,
    time: np.ndarray,
    pressure: np.ndarray,
    title: str,
    units: str = "",
    cmap: str = "RdYlBu_r",
    style: str = "pcolormesh",
    vmin: float | None = None,
    vmax: float | None = None,
    n: int = 20,
    cb_label: str | None = None,
    title_loc: str = "left",
    date_fmt: bool = True,
) -> Any:
    """Draw one (pressure × time) field as a discrete-colorbar panel on *ax*.

    A generic time–depth panel primitive.  Computes percentile colour limits
    from *data* unless *vmin* / *vmax* are supplied.  Applies a discrete
    ``BoundaryNorm`` colorbar, an inverted pressure axis, and (optionally) a
    concise date axis.

    Parameters
    ----------
    fig, ax : matplotlib Figure and Axes
        Target figure and axes to draw on.
    data : numpy.ndarray
        2-D field shaped ``(pressure, time)``.
    time : numpy.ndarray
        Time coordinate (length matching ``data``'s second axis).
    pressure : numpy.ndarray
        Pressure coordinate (length matching ``data``'s first axis).
    title : str
        Panel title.
    units : str, optional
        Units string appended to *cb_label* when *cb_label* is ``None``.
    cmap : str, optional
        Colormap name.  Default ``"RdYlBu_r"``.
    style : str, optional
        ``"pcolormesh"`` (default) or ``"contourf"``.
    vmin, vmax : float, optional
        Explicit colour limits.  Computed from data percentiles when omitted.
    n : int, optional
        Target number of colorbar levels.  Default 20.
    cb_label : str, optional
        Colorbar label.  Defaults to ``"{title} ({units})"`` or ``"{title}"``.
    title_loc : str, optional
        Horizontal alignment of the axes title.  Default ``"left"`` (the report
        panel-title convention; pass ``"center"`` for a centred title).
    date_fmt : bool, optional
        When ``True`` (default), apply :func:`date_axis` to *ax*.  Pass
        ``False`` for stacked panels where only the last axis needs the
        formatter.

    Returns
    -------
    matplotlib collection
        The pcolormesh/contourf artist (useful for a shared colorbar).

    """
    bounds, norm = colorbar_norm(data, vmin=vmin, vmax=vmax, n=n)
    if style == "contourf":
        pc = ax.contourf(time, pressure, data, levels=bounds, cmap=cmap, extend="both")
    else:
        pc = ax.pcolormesh(
            time, pressure, data, shading="nearest", cmap=cmap, norm=norm
        )
    cb = fig.colorbar(
        pc,
        ax=ax,
        pad=0.02,
        ticks=nice_colorbar_ticks(float(bounds[0]), float(bounds[-1])),
    )
    if cb_label is not None:
        # Explicit full label requested — keep it as a side label.
        cb.set_label(cb_label)
    elif units:
        # Units-only, above the bar (saves width, reads cleanly); the panel title
        # already carries the variable name.
        cb.ax.set_title(units, fontsize=report_tokens.ANNOT_FS)
    else:
        cb.set_label(title)
    pressure_axis(ax)
    if date_fmt:
        date_axis(ax)
    plot_title(ax, title, loc=title_loc)
    return pc


# ---------------------------------------------------------------------------
# Deterministic square-axes-plus-colorbar layout (mirrors the
# inch-based cruise-map layout in ctdcast plots.py::_map_layout).
# ---------------------------------------------------------------------------
# Fixed inch reservations, independent of figure width.  The lesson from the
# maps work: matplotlib's auto-layout tools (`set_aspect`, `make_axes_locatable`,
# `tight_layout`) fight each other and strand the colorbar (the recurring
# "colorbar too tall" bug) — instead, size every square panel in inches and place
# the axes and its colorbar by hand so the colorbar shares the panel's exact
# pixel height by construction.
_SQ_LABEL_IN: float = 0.62  # y-tick labels + rotated y-axis label
_SQ_XTICK_IN: float = 0.52  # x-tick labels + x-axis label
_SQ_TITLE_IN: float = 0.30  # per-panel title row
_SQ_WGAP_IN: float = 0.78  # horizontal gap between columns (room for y-labels)
_SQ_HGAP_IN: float = 0.72  # vertical gap between rows (title + x-labels)
_SQ_CBAR_GAP_IN: float = 0.14  # gap between grid and shared colorbar
_SQ_CBAR_W_IN: float = 0.16  # colorbar bar width
_SQ_CBAR_TXT_IN: float = 0.52  # colorbar tick text + unit title width
# Per-panel colorbars carry only short tick numbers (the unit is a title on top),
# so they need far less text width than the shared bar — this keeps the squares
# big.  The inter-cell gap is also tighter (each cell already reserves its own
# left y-label + right colorbar).
_SQ_CBAR_TXT_PP_IN: float = 0.30
_SQ_WGAP_PP_IN: float = 0.34
# Extra left-margin inches per y-tick-label character beyond the ~4 that the base
# _SQ_LABEL_IN reserve already fits.  Sizes the adaptive left reserve for wide
# labels (large trajectory displacements) so the rotated axis label stays
# on-canvas without shrinking the square when labels are short.
_SQ_PER_CHAR_IN: float = 0.10


def ytick_reserve_in(y: np.ndarray | float) -> float:
    """Left-margin inches to fit the widest y-tick label plus rotated y-axis label.

    Sizes the ``left_in`` reserve for :func:`square_axes_grid` from the y-data
    range instead of a fixed shrink: the base reserve (``_SQ_LABEL_IN``) already
    fits up to ~4-character tick labels, and each additional character (a larger
    magnitude, or a leading minus) adds ``_SQ_PER_CHAR_IN``.  Returns the base
    reserve unchanged for <=4-character labels, so a small-displacement
    trajectory keeps the full square (no 9% shrink) while a thousands-of-km one
    reserves just enough.

    Parameters
    ----------
    y : numpy.ndarray or float
        The y values (or the maximum absolute y-limit) about to be plotted.

    Returns
    -------
    float
        Left-margin reserve in inches, always >= ``_SQ_LABEL_IN``.

    """
    arr = np.asarray(y, dtype=float).ravel()
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return _SQ_LABEL_IN
    n_digits = len(str(int(round(float(np.max(np.abs(finite)))))))
    n_chars = n_digits + (1 if np.any(finite < 0) else 0)
    return _SQ_LABEL_IN + max(0, n_chars - 4) * _SQ_PER_CHAR_IN


def square_axes_grid(
    fig_w: float,
    nrows: int,
    ncols: int,
    *,
    colorbar: bool = True,
    per_panel_colorbar: bool = False,
    top_pad_in: float = 0.0,
    bottom_pad_in: float = 0.0,
    wgap_in: float | None = None,
    cbar_txt_in: float | None = None,
    left_in: float | None = None,
) -> tuple[plt.Figure, np.ndarray, Any]:
    """Lay out an ``nrows × ncols`` grid of square axes deterministically in inches.

    Every panel is an exact square whose side is computed from the usable width,
    so an equal-aspect plot fills the panel without ``set_aspect`` having to
    shrink the axes box — and a colorbar placed here shares each panel's exact
    pixel height rather than the taller subplot cell.  A single shared colorbar
    axes (spanning the full height of the panel stack) is reserved on the right
    when *colorbar* is true.  ``fig._manual_layout`` is set so the base64 encoder
    skips ``tight_layout`` (which would re-flow these hand-placed axes).

    Callers must NOT call ``set_aspect('equal', adjustable='box')`` on the
    returned axes: the box is already square and authoritative, so use symmetric
    limits (or ``adjustable='datalim'``) instead, or the box would resize and
    strand the shared colorbar again.

    Parameters
    ----------
    fig_w : float
        Figure width in inches — must equal the display slot so the PNG is not
        rescaled by the browser.
    nrows, ncols : int
        Grid shape (both >= 1).
    colorbar : bool
        Reserve and return a single shared colorbar axes on the right.  Default
        True.  Ignored when *per_panel_colorbar* is set.
    per_panel_colorbar : bool
        Give **each** panel its own height-matched colorbar axes to its right
        (for figures where every panel encodes a different field, e.g. a T-S dot
        plot + count heatmap + O₂ panel).  The third return value is then an
        ``(nrows, ncols)`` object array of colorbar axes instead of a single one.
    top_pad_in : float
        Extra inches reserved above the panel-title strip, e.g. for a figure
        ``suptitle``.  Default 0.
    bottom_pad_in : float
        Extra inches reserved below the x-tick strip, e.g. for rotated tick
        labels or a second x-axis label line.  Default 0.
    wgap_in : float, optional
        Override the inter-column gap (inches).  Default (``None``) uses the
        standard gap, which reserves room for each column's y-labels; a caller
        whose panels share a y-axis (right panel hides its y-labels) can pass a
        smaller value to close the gap.
    cbar_txt_in : float, optional
        Override the reserved width (inches) for the shared colorbar's tick
        labels and axis label.  Default (``None``) uses the standard reserve;
        pass a larger value for a long colorbar label so it stays on-canvas.
    left_in : float, optional
        Override the reserved left-margin width (inches) for the y-tick labels
        plus rotated y-axis label.  Default (``None``) uses the standard reserve
        (``_SQ_LABEL_IN``), which fits up to ~4-character tick labels; pass a
        larger value when the y-tick labels are wide (e.g. thousands-of-km
        trajectory displacements) so the axis label stays on-canvas.  Applies to
        the standard (non ``per_panel_colorbar``) layout.  Use
        :func:`ytick_reserve_in` to size it from the data range.

    Returns
    -------
    tuple of (matplotlib.figure.Figure, numpy.ndarray, object)
        The figure; an ``(nrows, ncols)`` object array of panel axes; and the
        colorbar axes — a single shared ``Axes`` (or None) normally, or an
        ``(nrows, ncols)`` object array when *per_panel_colorbar* is set.

    """
    # Optional overrides: a caller with a shared y-axis (right panel hides its
    # y-labels) can pass a smaller *wgap_in*; a long shared-colorbar label needs a
    # wider *cbar_txt_in* reserve so it stays on-canvas.
    _cbar_txt = cbar_txt_in if cbar_txt_in is not None else _SQ_CBAR_TXT_IN
    _cbar_reserve = _SQ_CBAR_GAP_IN + _SQ_CBAR_W_IN + _cbar_txt
    _left = left_in if left_in is not None else _SQ_LABEL_IN
    if per_panel_colorbar:
        # Each cell = y-labels + square panel + its own (tight) colorbar.
        per_cell_fixed = (
            _SQ_LABEL_IN + _SQ_CBAR_GAP_IN + _SQ_CBAR_W_IN + _SQ_CBAR_TXT_PP_IN
        )
        _wgap = wgap_in if wgap_in is not None else _SQ_WGAP_PP_IN
        avail_w = fig_w - ncols * per_cell_fixed - (ncols - 1) * _wgap
    else:
        _wgap = wgap_in if wgap_in is not None else _SQ_WGAP_IN
        right_in = _cbar_reserve if colorbar else _SQ_LABEL_IN
        avail_w = fig_w - _left - right_in - (ncols - 1) * _wgap
    side = max(avail_w / ncols, 0.5)  # square panel side (inches)
    grid_h = nrows * side + (nrows - 1) * _SQ_HGAP_IN
    bottom_in = _SQ_XTICK_IN + bottom_pad_in
    fig_h = _SQ_TITLE_IN + grid_h + bottom_in + top_pad_in

    fig = plt.figure(figsize=(fig_w, fig_h))
    fig._manual_layout = True
    axes = np.empty((nrows, ncols), dtype=object)
    caxes = np.empty((nrows, ncols), dtype=object) if per_panel_colorbar else None
    for r in range(nrows):
        for c in range(ncols):
            if per_panel_colorbar:
                x0 = c * (side + per_cell_fixed + _wgap) + _SQ_LABEL_IN
            else:
                x0 = _left + c * (side + _wgap)
            # Row 0 at the top; y measured from the figure bottom.
            y0 = bottom_in + (nrows - 1 - r) * (side + _SQ_HGAP_IN)
            axes[r, c] = fig.add_axes(
                [x0 / fig_w, y0 / fig_h, side / fig_w, side / fig_h]
            )
            if per_panel_colorbar:
                # Colorbar height == this panel's side (matched by construction).
                _pcx0 = x0 + side + _SQ_CBAR_GAP_IN
                caxes[r, c] = fig.add_axes(
                    [_pcx0 / fig_w, y0 / fig_h, _SQ_CBAR_W_IN / fig_w, side / fig_h]
                )

    if per_panel_colorbar:
        return fig, axes, caxes

    cax = None
    if colorbar:
        cx0 = _SQ_LABEL_IN + ncols * side + (ncols - 1) * _wgap + _SQ_CBAR_GAP_IN
        # If the panels were floored to 0.5" (a too-small fig_w), cx0 can run past
        # the figure edge — clamp so the colorbar stays on-canvas rather than
        # rendering clipped/invisible.
        cx0 = min(cx0, fig_w - _SQ_CBAR_W_IN - _cbar_txt)
        cax = fig.add_axes(
            [cx0 / fig_w, bottom_in / fig_h, _SQ_CBAR_W_IN / fig_w, grid_h / fig_h]
        )
    return fig, axes, cax


def square_limits(
    x: np.ndarray,
    y: np.ndarray,
    *,
    pad_frac: float = 0.05,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return ``(xlim, ylim)`` framing *x*, *y* as an equal-extent square.

    The larger of the x and y data ranges is applied to both axes (each centred
    on its own data midpoint), so an equal-aspect plot of the data is square and
    neither axis is a thin strip.  Non-finite values are ignored; a degenerate
    (zero-extent) input falls back to a unit square.  Choose the limits with this
    helper *before* placing a square axes so the colorbar sizing stays exact.

    Parameters
    ----------
    x, y : numpy.ndarray
        Data coordinates (any shape; flattened, non-finite dropped).
    pad_frac : float
        Fractional padding added to the half-extent on all sides.  Default 0.05.

    Returns
    -------
    tuple of (tuple of float, tuple of float)
        ``((x0, x1), (y0, y1))``.

    """
    xf = np.asarray(x)[np.isfinite(x)]
    yf = np.asarray(y)[np.isfinite(y)]
    if xf.size == 0 or yf.size == 0:
        return (-1.0, 1.0), (-1.0, 1.0)
    xmid = 0.5 * (float(xf.min()) + float(xf.max()))
    ymid = 0.5 * (float(yf.min()) + float(yf.max()))
    half = 0.5 * max(float(xf.max() - xf.min()), float(yf.max() - yf.min()))
    half = half * (1.0 + pad_frac)
    if half <= 0:
        half = 1.0
    return (xmid - half, xmid + half), (ymid - half, ymid + half)


def unit_colorbar(
    cax: Any,
    mappable: Any,
    *,
    unit: str = "",
    ticks: np.ndarray | None = None,
    ticklabels: list[str] | None = None,
) -> Any:
    """Draw *mappable*'s colorbar into the pre-placed *cax* with the unit on top.

    The unit is rendered as a title above the bar (``cax.set_title``) rather than
    a rotated side label, which saves horizontal width and reads cleanly — the
    same convention as the cruise-map depth colorbar.  *cax* is expected to come
    from :func:`square_axes_grid` so its height already matches the plotted
    square.

    Parameters
    ----------
    cax : matplotlib Axes
        Pre-placed colorbar axes.
    mappable : matplotlib ScalarMappable
        The artist (LineCollection, pcolormesh, ScalarMappable, ...) to map.
    unit : str
        Unit string placed above the bar (e.g. ``"m s⁻¹"``).  Empty renders no
        title.
    ticks : numpy.ndarray, optional
        Explicit colorbar tick positions.
    ticklabels : list of str, optional
        Explicit tick labels (same length as *ticks*), e.g.
        ``["start", "end"]`` for a fractional-time bar.

    Returns
    -------
    matplotlib.colorbar.Colorbar

    """
    cb = cax.figure.colorbar(mappable, cax=cax, ticks=ticks)
    if ticklabels is not None:
        if ticks is None or len(ticklabels) != len(ticks):
            raise ValueError("ticklabels must match ticks in length")
        # Pin the locator to the given ticks so the labels can't drift onto
        # auto-placed positions (FixedLocator/FixedFormatter must agree).
        cax.yaxis.set_major_locator(mticker.FixedLocator(list(ticks)))
        cax.set_yticklabels(ticklabels)
    if unit:
        cax.set_title(unit, fontsize=report_tokens.ANNOT_FS)
    return cb


def draw_trajectory(
    x: np.ndarray,
    y: np.ndarray,
    color_data: np.ndarray | None = None,
    cmap: str = "coolwarm",
    xlabel: str = "East displacement (m)",
    ylabel: str = "North displacement (m)",
    colorbar_label: str = "",
    colorbar_unit: str = "",
    title: str = "",
    *,
    width_in: float = report_tokens.W_HALF,
) -> plt.Figure:
    """Plot a 2D trajectory, optionally coloured per-segment by a scalar field.

    When *color_data* is provided, segments are drawn as a LineCollection with
    colours mapped through *cmap* and a height-matched colorbar (unit as a title
    on top).  When omitted, a plain line is drawn.  Start and end are marked with
    green and red markers.  The axes are laid out as an exact square via
    :func:`square_axes_grid` with :func:`square_limits`, so the colorbar height
    always matches the plotted square.

    Parameters
    ----------
    x, y : ndarray
        Trajectory coordinates (same length).
    color_data : ndarray, optional
        Scalar values to map onto the line (same length as x/y).
    cmap : str
        Matplotlib colormap name used when color_data is provided.
    xlabel, ylabel : str
        Axis labels.
    colorbar_label : str
        Fallback colorbar label placed on top when *colorbar_unit* is empty.
    colorbar_unit : str
        Unit string placed above the colorbar (units-only on top, saves width).
    title : str
        Figure title.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure

    """
    # Reserve left margin for wide y-tick labels (thousands-of-km displacements)
    # so the rotated y-axis label is not clipped; short labels keep the full
    # square (see ytick_reserve_in).
    fig, axes, cax = square_axes_grid(
        width_in, 1, 1, colorbar=color_data is not None, left_in=ytick_reserve_in(y)
    )
    ax = axes[0, 0]

    if color_data is not None:
        points = np.array([x, y]).T.reshape(-1, 1, 2)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)
        bounds, norm = colorbar_norm(
            vmin=float(np.nanmin(color_data)), vmax=float(np.nanmax(color_data))
        )
        lc = LineCollection(segments, cmap=cmap, norm=norm, linewidth=1.5)
        lc.set_array(color_data[:-1])
        ax.add_collection(lc)
        unit_colorbar(cax, lc, unit=colorbar_unit or colorbar_label, ticks=bounds[::2])
    else:
        ax.plot(x, y, color="steelblue", linewidth=1.5)

    xlim, ylim = square_limits(x, y)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)

    # Start/end markers
    ax.plot(x[0], y[0], "o", color="green", markersize=8, label="Start", zorder=5)
    ax.plot(x[-1], y[-1], "s", color="red", markersize=8, label="End", zorder=5)
    ax.legend()

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if title:
        plot_title(ax, title)
    ax.axhline(0, color="k", linewidth=0.5, linestyle="--", alpha=0.4)
    ax.axvline(0, color="k", linewidth=0.5, linestyle="--", alpha=0.4)
    # datalim keeps the square box (from square_axes_grid) authoritative so the
    # colorbar's matched height is never invalidated.
    ax.set_aspect("equal", adjustable="datalim")
    grid_despine(ax)
    return fig


def hodograph_panel(
    ax: Any,
    e_v: np.ndarray,
    n_v: np.ndarray,
    t_frac: np.ndarray,
    title: str,
    units: str,
) -> Any:
    """Draw a single velocity hodograph on a pre-squared *ax*; return its mappable.

    Renders a time-coloured ``LineCollection`` trajectory (downsampled to
    <= 2 000 segments for performance) with start/end markers, symmetric
    ``-lim..lim`` limits, and a subtle grid.  The panel does NOT draw its own
    colorbar — the caller owns one shared colorbar (all panels use the same 0->1
    plasma time mapping); pass the returned ``ScalarMappable`` to
    :func:`unit_colorbar` on a shared ``cax`` from :func:`square_axes_grid`.

    *e_v*, *n_v*, and *t_frac* must already be filtered to the same finite-valid
    indices (no NaN, same length).  *ax* is expected to be an exact square from
    :func:`square_axes_grid`; equal aspect is enforced with
    ``adjustable='datalim'`` so the box is never resized (which would strand the
    shared colorbar).

    Parameters
    ----------
    ax : matplotlib Axes
        Pre-squared target axes to draw on.
    e_v, n_v : np.ndarray
        East and north velocity (finite values only, same length).
    t_frac : np.ndarray
        Fractional deployment time 0 -> 1, same length as *e_v*.
    title : str
        Axes title.
    units : str
        Velocity unit string appended to axis labels, e.g. ``"m s^-1"``.

    Returns
    -------
    matplotlib.cm.ScalarMappable
        The 0->1 plasma time mapping, for a shared colorbar.

    """
    lim = max(float(np.nanmax(np.abs(e_v))), float(np.nanmax(np.abs(n_v))), 1e-9) * 1.1
    step = max(1, len(e_v) // 2000)
    pts = np.array([e_v[::step], n_v[::step]]).T.reshape(-1, 1, 2)
    segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
    _bounds_lc, norm_lc = colorbar_norm(vmin=0.0, vmax=1.0, n=10)
    lc = LineCollection(segs, cmap="plasma", norm=norm_lc, lw=0.9, alpha=0.85)
    lc.set_array(t_frac[::step][:-1])
    ax.add_collection(lc)

    sm = plt.cm.ScalarMappable(cmap="plasma", norm=norm_lc)
    sm.set_array([])

    ax.scatter(
        e_v[0],
        n_v[0],
        s=50,
        c="lime",
        marker="o",
        edgecolors="black",
        linewidths=0.7,
        zorder=5,
    )
    ax.scatter(
        e_v[-1],
        n_v[-1],
        s=55,
        c="red",
        marker="s",
        edgecolors="black",
        linewidths=0.7,
        zorder=5,
    )
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    # datalim (not the default 'box'): keep the square box authoritative so the
    # shared colorbar's matched height is never invalidated by an aspect resize.
    ax.set_aspect("equal", adjustable="datalim")
    ax.axhline(0, color="#888", lw=0.7)
    ax.axvline(0, color="#888", lw=0.7)
    ax.set_xlabel(f"East ({units})")
    ax.set_ylabel(f"North ({units})")
    plot_title(ax, title)
    grid_despine(ax)
    return sm


def date_offset_left(ax: Any) -> None:
    """Move the x-axis date offset label (e.g. ``2026-Jul``) to the bottom-left.

    matplotlib's ``ConciseDateFormatter`` draws the year/month offset at the
    bottom-right.  Only the offset's vertical position is updated on each draw, so
    the left x-position set here persists.  Call after setting the date formatter.
    """
    offset = ax.xaxis.get_offset_text()
    offset.set_horizontalalignment("left")
    offset.set_position((0.0, 0.0))


def date_axis(ax: Any) -> None:
    """Apply a concise auto-scaled date formatter to *ax*'s x-axis (offset left)."""
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    date_offset_left(ax)


def pressure_axis(ax: Any) -> None:
    """Configure *ax* as a standard pressure Y-axis: inverted, labelled, gridded."""
    ax.invert_yaxis()
    ax.set_ylabel(params.vlabel("pressure"))
    grid_despine(ax)


def colorbar_norm(
    data: np.ndarray | None = None,
    *,
    vmin: float | None = None,
    vmax: float | None = None,
    n: int = 20,
    symmetric: bool = False,
) -> tuple[np.ndarray, mcolors.BoundaryNorm]:
    """Return ``(bounds, norm)`` for a discrete pcolormesh colorbar.

    Computes percentile limits from *data* when *vmin* / *vmax* are not given.
    Explicit *vmin* / *vmax* override the percentile calculation.  Pass
    ``symmetric=True`` to force the range symmetric about zero.

    Parameters
    ----------
    data : np.ndarray, optional
        Source array used for percentile-based limit computation.  Ignored when
        both *vmin* and *vmax* are given.
    vmin, vmax : float, optional
        Explicit color limits.  Either one or both may be supplied; the other
        falls back to the percentile of *data*.
    n : int
        Target number of colorbar levels (default 20).
    symmetric : bool
        If ``True``, expand [vmin, vmax] to [-max, +max] before computing
        bounds.

    Returns
    -------
    bounds : np.ndarray
        Boundary array for ``BoundaryNorm`` and colorbar ticks.
    norm : matplotlib.colors.BoundaryNorm

    Raises
    ------
    ValueError
        If neither *data* nor both *vmin* and *vmax* are provided.

    """
    if vmin is None or vmax is None:
        if data is None:
            raise ValueError("Provide either data or both vmin and vmax.")
        finite = data[np.isfinite(data)]
        if finite.size == 0:
            vmin = vmin if vmin is not None else 0.0
            vmax = vmax if vmax is not None else 1.0
        else:
            if vmin is None:
                vmin = float(np.nanpercentile(finite, params.COLORBAR_PLOW))
            if vmax is None:
                vmax = float(np.nanpercentile(finite, params.COLORBAR_PHIGH))
    if symmetric:
        abs_max = max(abs(float(vmin)), abs(float(vmax)), 1e-9)
        vmin, vmax = -abs_max, abs_max
    bounds = _nice_colorbar_bounds(float(vmin), float(vmax), n=n)
    norm = mcolors.BoundaryNorm(bounds, ncolors=256)
    return bounds, norm
