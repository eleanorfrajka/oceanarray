"""T-S diagram and thermohaline structure plot functions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from oceanarray.pipeline.masking import masked
from oceanarray.plotters.primitives import nice_colorbar_ticks
from oceanarray.reports._vendored import design_tokens as report_tokens

from ..config import parameters as params
from .helpers import QC_MARKER as _QC_MARKER
from .helpers import as_dataset
from .primitives import colorbar_norm, plot_title, square_axes_grid, unit_colorbar

if TYPE_CHECKING:
    import matplotlib.pyplot as plt
    import xarray as xr

    from oceanarray.analysis.derived import DerivedFields

logger = logging.getLogger(__name__)

#: x-axis label of every T-S panel: ``"Salinity (PSU)"`` (practical salinity, PSS-78).
SALINITY_LABEL: str = params.vlabel("salinity")


def _theta(
    ds: xr.Dataset,
    temperature: np.ndarray,
    salinity: np.ndarray,
    pressure: np.ndarray | None,
    derived: DerivedFields | None = None,
) -> tuple[np.ndarray, tuple[float, float] | None, str]:
    """Return the T-S y values: potential temperature when the position is known.

    Parameters
    ----------
    ds : xarray.Dataset
        The product the figure is drawn from (for its position attributes).
    temperature, salinity : numpy.ndarray
        In-situ temperature and practical salinity as the figure uses them
        (flag-masked where it masks), of one shape.
    pressure : numpy.ndarray or None
        Sea pressure broadcastable to *temperature*.
    derived : DerivedFields or None, optional
        The page's derived quantities; its position (and θ, when its shape
        matches) is used instead of reading the attributes again.

    Returns
    -------
    values : numpy.ndarray
        θ (°C), NaN where *temperature* is NaN; in-situ temperature when the
        position or pressure is unknown.
    position : tuple of (float, float) or None
        ``(latitude, longitude)`` for the σθ contours; None when θ could not be
        computed (the panel then has no contours).
    label : str
        The y-axis label for *values*.
    """
    from oceanarray.analysis.derived import mooring_position, potential_temperature_of

    if derived is not None:
        lat, lon = derived.latitude, derived.longitude
        theta = derived.theta
        if theta is not None and np.shape(theta) == np.shape(temperature):
            values = np.where(
                np.isfinite(temperature), np.asarray(theta, float), np.nan
            )
            return values, (lat, lon), params.vlabel("potential_temperature")
    else:
        lat, lon = mooring_position(ds)
    if pressure is not None and lat is not None and lon is not None:
        values = potential_temperature_of(temperature, salinity, pressure, lat, lon)
        return values, (lat, lon), params.vlabel("potential_temperature")
    return temperature, None, params.vlabel("temperature")


@dataclass(frozen=True)
class SigmaContours:
    """σθ on a salinity × θ grid covering a figure's shared T-S axes, built once.

    Attributes
    ----------
    salinity, theta : numpy.ndarray
        The grid's practical salinity and potential temperature (°C).
    sigma : numpy.ndarray
        σθ (kg m⁻³) on the grid.
    interval : float
        The contour interval (kg m⁻³), from :func:`sigma_contour_levels`.
    levels : numpy.ndarray
        The contour levels, whole multiples of *interval*.
    """

    salinity: np.ndarray
    theta: np.ndarray
    sigma: np.ndarray
    interval: float
    levels: np.ndarray


def sigma0_contours(
    s_lim: tuple[float, float],
    t_lim: tuple[float, float],
    position: tuple[float, float] | None,
    n_grid: int = 200,
) -> SigmaContours | None:
    """Return the σθ contours for a T-S figure, from its shared axis limits.

    The contours are at whole multiples of a round interval (0.05, 0.1, 0.2,
    0.25, 0.5, 1 or 2 kg m⁻³) chosen to give four to ten lines across the panel.
    Every panel of the figure draws the same grid (:func:`draw_sigma0_contours`).

    Parameters
    ----------
    s_lim, t_lim : tuple of float
        The figure's practical-salinity and potential-temperature axis limits.
    position : tuple of (float, float) or None
        ``(latitude, longitude)`` of the mooring; no contours when None.
    n_grid : int, optional
        Grid points along each axis.

    Returns
    -------
    SigmaContours or None
        None without a position, or when the limits or σθ are not finite (logged
        at debug level).
    """
    if position is None:
        return None
    from oceanarray.analysis.derived import potential_density_grid

    limits = np.asarray([*s_lim, *t_lim], dtype=float)
    if not np.all(np.isfinite(limits)) or s_lim[1] <= s_lim[0] or t_lim[1] <= t_lim[0]:
        logger.debug("σθ contours not drawn: T-S limits %s, %s", s_lim, t_lim)
        return None
    Sg, Tg, sigma = potential_density_grid(
        s_lim, t_lim, position[0], position[1], n_grid=n_grid
    )
    if not np.isfinite(sigma).any():
        logger.debug("σθ contours not drawn: σθ is all NaN on %s, %s", s_lim, t_lim)
        return None
    interval, levels = sigma_contour_levels(
        float(np.nanmin(sigma)), float(np.nanmax(sigma))
    )
    return SigmaContours(Sg, Tg, sigma, interval, levels)


#: A σθ label centred closer than this share of the axis range to a frame edge is
#: removed (it would be cut).
SIGMA_LABEL_EDGE: float = 0.01
#: σθ labels placed along each contour, at even steps of its in-frame part; a
#: contour shorter in the frame than SIGMA_LABEL_SPACING (axis fractions) gets one.
SIGMA_LABELS_PER_CONTOUR: int = 2
SIGMA_LABEL_SPACING: float = 0.6


def draw_sigma0_contours(ax: plt.Axes, contours: SigmaContours | None) -> float | None:
    """Overlay labelled σθ contours on a θ-S axes.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
        The T-S axes (practical salinity on x, potential temperature on y).
    contours : SigmaContours or None
        The figure's contours (:func:`sigma0_contours`); nothing is drawn when None.

    Returns
    -------
    float or None
        The contour interval (kg m⁻³); None when no contours were drawn.
    """
    if contours is None:
        return None
    cs = ax.contour(
        contours.salinity,
        contours.theta,
        contours.sigma,
        levels=contours.levels,
        colors="0.35",
        linewidths=0.6,
        linestyles="--",
        zorder=1,
    )
    interval = contours.interval
    digits = max(0, -int(np.floor(np.log10(interval) + 1e-9)))
    if abs(interval * 10**digits - round(interval * 10**digits)) > 1e-6:
        digits += 1
    # The grid spans the figure's axis limits. Labels go at even steps along the
    # in-frame part of each contour, and one is kept unless it is within
    # SIGMA_LABEL_EDGE of a frame edge, where it would be cut.
    s_lo, s_hi = float(np.min(contours.salinity)), float(np.max(contours.salinity))
    t_lo, t_hi = float(np.min(contours.theta)), float(np.max(contours.theta))
    ds_, dt_ = SIGMA_LABEL_EDGE * (s_hi - s_lo), SIGMA_LABEL_EDGE * (t_hi - t_lo)

    def inside(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return (
            (s_lo + ds_ <= x)
            & (x <= s_hi - ds_)
            & (t_lo + dt_ <= y)
            & (y <= t_hi - dt_)
        )

    at = []
    for segments in cs.allsegs:
        line = [seg[inside(seg[:, 0], seg[:, 1])] for seg in segments if len(seg)]
        line = np.concatenate(line) if line else np.empty((0, 2))
        if len(line) < 2:
            continue
        # Distance along the contour, in axis fractions; a short one gets one label.
        step = np.hypot(
            np.diff(line[:, 0]) / (s_hi - s_lo), np.diff(line[:, 1]) / (t_hi - t_lo)
        )
        along = np.concatenate([[0.0], np.cumsum(step)])
        n = SIGMA_LABELS_PER_CONTOUR if along[-1] >= SIGMA_LABEL_SPACING else 1
        for frac in np.arange(1, n + 1) / (n + 1):
            at.append(tuple(line[np.searchsorted(along, frac * along[-1])]))
    labels = ax.clabel(
        cs,
        fmt=f"%.{digits}f",
        fontsize=report_tokens.CLABEL_FS,
        inline=True,
        manual=at or False,
    )
    for label in labels:
        x, y = label.get_position()
        if not inside(np.array(x), np.array(y)):
            label.remove()
    return interval


#: The on-figure note and the caption words for a T-S figure without σθ.
NO_SIGMA_NOTE: str = "σθ not drawn: {reason}"


def no_sigma_reason(ds: xr.Dataset, derived: DerivedFields | None = None) -> str:
    """Return why a T-S figure of *ds* has no σθ: ``"no mooring position"`` or ``"no pressure"``.

    Parameters
    ----------
    ds : xarray.Dataset
        The product the figure is drawn from.
    derived : DerivedFields or None, optional
        The page's derived quantities (its position is used when given).

    Returns
    -------
    str
    """
    from oceanarray.analysis.derived import mooring_position

    if derived is not None:
        lat, lon = derived.latitude, derived.longitude
    else:
        lat, lon = mooring_position(ds, warn=False)
    return "no mooring position" if lat is None or lon is None else "no pressure"


def _figure_sigma0_contours(
    ax: plt.Axes,
    ds: xr.Dataset,
    derived: DerivedFields | None,
    position: tuple[float, float] | None,
    s_lim: tuple[float, float],
    t_lim: tuple[float, float],
) -> SigmaContours | None:
    """Return the figure's σθ contours; without a position, note it on *ax* and warn.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
        The figure's first T-S panel, which carries the note.
    ds : xarray.Dataset
        The product the figure is drawn from (for its name and position).
    derived : DerivedFields or None
        The page's derived quantities.
    position : tuple of (float, float) or None
        From :func:`_theta`.
    s_lim, t_lim : tuple of float
        The figure's shared axis limits.

    Returns
    -------
    SigmaContours or None
    """
    if position is not None:
        return sigma0_contours(s_lim, t_lim, position)
    reason = no_sigma_reason(ds, derived)
    logger.warning(
        "%s: σθ contours not drawn on the T-S figure: %s",
        ds.attrs.get("mooring_name", "product"),
        reason,
    )
    ax.text(
        0.02,
        0.98,
        NO_SIGMA_NOTE.format(reason=reason),
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=report_tokens.ANNOT_FS,
        color="0.35",
        zorder=5,
    )
    return None


#: Round contour intervals for σθ on a T-S diagram (kg m⁻³).
_SIGMA_INTERVALS: tuple[float, ...] = (0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0)


def sigma_contour_levels(lo: float, hi: float) -> tuple[float, np.ndarray]:
    """Return a round contour interval and the levels it gives between *lo* and *hi*.

    Parameters
    ----------
    lo, hi : float
        The σ range (kg m⁻³) of the panel.

    Returns
    -------
    tuple of (float, numpy.ndarray)
        The smallest interval of :data:`_SIGMA_INTERVALS` giving at most ten
        levels, and the levels (whole multiples of it within the range).
    """
    span = max(hi - lo, 1e-6)
    interval = next(
        (d for d in _SIGMA_INTERVALS if span / d <= 10), _SIGMA_INTERVALS[-1]
    )
    first = np.ceil(lo / interval - 1e-9) * interval
    levels = np.arange(first, hi + 1e-9, interval)
    return interval, np.round(levels, 6)


def _ts_heatmap_panel(
    ax: plt.Axes,
    fig: plt.Figure,
    S: np.ndarray,
    T: np.ndarray,
    n_bins: int = 80,
    plo: float = 0.01,
    phi: float = 99.99,
    s_lim: tuple | None = None,
    t_lim: tuple | None = None,
    cax: plt.Axes | None = None,
    contours: SigmaContours | None = None,
    t_label: str | None = None,
) -> float | None:
    """Render a T-S 2-D count heatmap on *ax*; return the σθ contour interval.

    *T* is potential temperature when *contours* are given (the figure's σθ,
    drawn on top), else in-situ temperature; *t_label* labels the y-axis.
    """
    import matplotlib.pyplot as plt

    from oceanarray.plotters.primitives import nice_colorbar_ticks

    from .primitives import unit_colorbar

    # s_lim/t_lim, when given, set bins and limits so the heatmap shares the
    # dot-plot's axes exactly; otherwise use a percentile range.
    s_lo, s_hi = (
        s_lim
        if s_lim is not None
        else (float(np.nanpercentile(S, plo)), float(np.nanpercentile(S, phi)))
    )
    t_lo, t_hi = (
        t_lim
        if t_lim is not None
        else (float(np.nanpercentile(T, plo)), float(np.nanpercentile(T, phi)))
    )
    s_edges = np.linspace(s_lo, s_hi, n_bins + 1)
    t_edges = np.linspace(t_lo, t_hi, n_bins + 1)
    counts, _, _ = np.histogram2d(S, T, bins=[s_edges, t_edges])
    log_counts = np.log10(counts.T + 1)
    log_counts = np.ma.masked_where(counts.T == 0, log_counts)

    vmin = float(np.nanmin(log_counts))
    vmax = float(np.nanmax(log_counts))
    bounds, norm = colorbar_norm(vmin=vmin, vmax=vmax)
    cmap = plt.cm.YlOrRd.copy()
    cmap.set_bad("white")
    pc = ax.pcolormesh(
        s_edges, t_edges, log_counts, cmap=cmap, norm=norm, shading="flat"
    )
    _ticks = nice_colorbar_ticks(float(bounds[0]), float(bounds[-1]))
    # cax is a height-matched colorbar axes from square_axes_grid; without one,
    # add a normal colorbar and square the box with set_box_aspect.
    if cax is not None:
        unit_colorbar(cax, pc, unit=r"$\log_{10}$(count)", ticks=_ticks)
    else:
        cb = fig.colorbar(pc, ax=ax, pad=0.02, ticks=_ticks)
        cb.ax.set_title(r"$\log_{10}$(count)", fontsize=report_tokens.ANNOT_FS)
        ax.set_box_aspect(1)
    interval = draw_sigma0_contours(ax, contours)
    ax.set_xlim(s_lo, s_hi)
    ax.set_ylim(t_lo, t_hi)
    ax.set_xlabel(SALINITY_LABEL)
    ax.set_ylabel(t_label or params.vlabel("temperature"))
    plot_title(ax, "T-S heat map")
    return interval


def draw_ts_diagram(  # noqa: C901
    nc_path: Path,
    *,
    width_in: float = report_tokens.W_FULL,
    derived: DerivedFields | None = None,
    flag_sources: tuple[str, ...] | None = None,
    record: dict | None = None,
) -> plt.Figure | None:
    """T-S diagram from a NetCDF path; return a Figure.

    Scatter by pressure, 2-D count heatmap, and (when present) scatter by O2
    saturation: the stack page's figure for one instrument. Every value is drawn;
    values flagged suspect (dots) or bad (crosses), in the worse of the flag sets
    read, are marked on the left panel. The pressure colour bar runs shallow at
    the top; σθ contours are labelled inside the axes.

    Parameters
    ----------
    nc_path : Path
        Path to a stage-3 NetCDF file.
    width_in : float, optional
        Figure width (inches), the report slot; default the full content width.
    derived : DerivedFields or None, optional
        The page's derived quantities (position, θ); read from the file when not
        given.
    flag_sources : tuple of str or None, optional
        Flag-variable suffixes read for the markers (default oceanarray's and the
        originator's).
    record : dict or None, optional
        Filled with what was drawn: ``theta``, ``sigma_interval``, ``colour``
        (``"pressure"`` or ``"sample"``), ``oxygen``, ``counts`` (values marked per
        flag).

    Returns
    -------
    plt.Figure or None
        Figure, or None if temperature or salinity are absent.  The y-axis is
        potential temperature with σθ contours when the position is known,
        in-situ temperature without contours otherwise.

    """
    from .helpers import FLAG_SOURCES, marker_flags

    sources = flag_sources if flag_sources is not None else FLAG_SOURCES

    with as_dataset(nc_path) as ds:
        if "temperature" not in ds.data_vars or "salinity" not in ds.data_vars:
            return None

        T = ds["temperature"].values.astype(float)
        S = ds["salinity"].values.astype(float)
        finite = np.isfinite(T) & np.isfinite(S)
        if finite.sum() < 5:
            return None

        if "pressure" in ds.data_vars:
            C = ds["pressure"].values.astype(float)
            _cbar_unit = params.vunit("pressure")
            cmap_sc = "viridis_r"
        else:
            C = np.arange(len(T), dtype=float)
            _cbar_unit = ""  # sample index — no unit
            cmap_sc = "plasma"

        # The worse of temperature's and salinity's flags, each the worse of the
        # sets read (oceanarray's, the originator's).
        combined_flags = np.ones(len(T), dtype=np.int8)
        for var in ("temperature", "salinity"):
            flags = marker_flags(ds, var, sources)
            if flags is not None:
                for f in (3, 4):
                    combined_flags[flags == f] = np.maximum(
                        combined_flags[flags == f], f
                    )
        suspect_mask = finite & (combined_flags == 3)
        bad_mask = finite & (combined_flags == 4)
        good_mask = finite & ~suspect_mask & ~bad_mask

        has_sat = "oxygen_saturation_pct" in ds.data_vars
        sat_data = ds["oxygen_saturation_pct"].values.astype(float) if has_sat else None
        T, position, t_label = _theta(
            ds,
            T,
            S,
            ds["pressure"].values.astype(float) if "pressure" in ds.data_vars else None,
            derived,
        )
        finite = np.isfinite(T) & np.isfinite(S)
        good_mask &= finite
        suspect_mask &= finite
        bad_mask &= finite
        if finite.sum() < 5:
            return None

    ncols = 3 if has_sat else 2
    # Square panels with per-panel height-matched colorbars (shared deterministic
    # layout — same path as the hodographs/trajectories).
    fig, _ax, _cax = square_axes_grid(width_in, 1, ncols, per_panel_colorbar=True)
    ax_l, ax_r = _ax[0, 0], _ax[0, 1]
    ax_sat = _ax[0, 2] if has_sat else None

    # Panel 1: T-S scatter coloured by pressure (every value; flagged ones marked)
    vmin = np.nanpercentile(C[finite], 5)
    vmax = np.nanpercentile(C[finite], 95)
    sc = ax_l.scatter(
        S[finite],
        T[finite],
        c=C[finite],
        cmap=cmap_sc,
        vmin=vmin,
        vmax=vmax,
        s=4,
        linewidths=0,
        alpha=0.6,
        zorder=2,
        rasterized=True,
    )
    unit_colorbar(
        _cax[0, 0],
        sc,
        unit=_cbar_unit,
        ticks=nice_colorbar_ticks(float(vmin), float(vmax)),
    )
    if _cbar_unit:
        _cax[0, 0].invert_yaxis()  # shallow at the top
    if suspect_mask.any():
        ax_l.scatter(S[suspect_mask], T[suspect_mask], label="suspect", **_QC_MARKER[3])
    if bad_mask.any():
        ax_l.scatter(S[bad_mask], T[bad_mask], label="bad", **_QC_MARKER[4])
    if suspect_mask.any() or bad_mask.any():
        ax_l.legend(loc="best", framealpha=0.8)

    # Shared T-S axis limits (the dot plot's data range) so the heatmap and O₂
    # panels use identical axes, and one σθ grid over them; square every panel box.
    _sf, _tf = S[finite], T[finite]
    _sp = 0.02 * (float(np.nanmax(_sf) - np.nanmin(_sf)) + 1e-9)
    _tp = 0.02 * (float(np.nanmax(_tf) - np.nanmin(_tf)) + 1e-9)
    _s_lim = (float(np.nanmin(_sf)) - _sp, float(np.nanmax(_sf)) + _sp)
    _t_lim = (float(np.nanmin(_tf)) - _tp, float(np.nanmax(_tf)) + _tp)
    contours = _figure_sigma0_contours(ax_l, ds, derived, position, _s_lim, _t_lim)
    interval = draw_sigma0_contours(ax_l, contours)
    ax_l.set_xlabel(SALINITY_LABEL)
    ax_l.set_ylabel(t_label)
    plot_title(ax_l, f"T-S (colour = {'pressure' if _cbar_unit else 'sample'})")
    if record is not None:
        record.update(
            theta=position is not None,
            sigma_interval=interval,
            no_sigma=None if contours is not None else no_sigma_reason(ds, derived),
            colour="pressure" if _cbar_unit else "sample",
            oxygen=False,
            counts={3: int(suspect_mask.sum()), 4: int(bad_mask.sum())},
        )
    ax_l.set_xlim(*_s_lim)
    ax_l.set_ylim(*_t_lim)

    # Panel 2: count heatmap (shares the dot plot's limits + matched colorbar)
    _ts_heatmap_panel(
        ax_r,
        fig,
        S[finite],
        T[finite],
        s_lim=_s_lim,
        t_lim=_t_lim,
        cax=_cax[0, 1],
        contours=contours,
        t_label=t_label,
    )

    # Panel 3: T-S scatter coloured by O2 saturation (when available)
    if ax_sat is not None and sat_data is not None:
        sat_finite = good_mask & np.isfinite(sat_data)
        if sat_finite.any():
            sv = sat_data[sat_finite]
            bounds_s, norm_s = colorbar_norm(
                vmin=float(np.nanpercentile(sv, 2)),
                vmax=float(np.nanpercentile(sv, 98)),
                n=11,
            )
            sc_s = ax_sat.scatter(
                S[sat_finite],
                T[sat_finite],
                c=sv,
                cmap="BrBG",
                norm=norm_s,
                s=4,
                linewidths=0,
                alpha=0.6,
                zorder=2,
                rasterized=True,
            )
            unit_colorbar(
                _cax[0, 2],
                sc_s,
                unit="%",
                ticks=nice_colorbar_ticks(float(bounds_s[0]), float(bounds_s[-1])),
            )
            draw_sigma0_contours(ax_sat, contours)
            if record is not None:
                record["oxygen"] = True
        ax_sat.set_xlabel(SALINITY_LABEL)
        ax_sat.set_ylabel(t_label)
        plot_title(ax_sat, "T-S (colour = O₂ sat.)")
        ax_sat.set_xlim(*_s_lim)
        ax_sat.set_ylim(*_t_lim)

    return fig


def _nice_axis_limits(
    x: np.ndarray, *, plow: float = 1.0, phigh: float = 99.0, pad_frac: float = 0.05
) -> tuple[float, float]:
    """Return padded, outward-rounded axis limits from robust percentiles."""
    # Percentiles (default 1st/99th) keep outliers from stretching the axis; pad each
    # side by pad_frac of that range, then round outward to a tenth of the range's
    # order of magnitude so ticks land on round values.
    p_lo = float(np.nanpercentile(x, plow))
    p_hi = float(np.nanpercentile(x, phigh))
    rng = p_hi - p_lo
    if not np.isfinite(rng) or rng <= 0:
        return p_lo - 0.5, p_hi + 0.5
    lo = p_lo - pad_frac * rng
    hi = p_hi + pad_frac * rng
    step = 10.0 ** np.floor(np.log10(rng)) / 10.0
    return float(np.floor(lo / step) * step), float(np.ceil(hi / step) * step)


def draw_stack_ts_diagram(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_FULL,
    derived: DerivedFields | None = None,
    record: dict | None = None,
    full_range: bool = False,
) -> plt.Figure | None:
    """T-S diagram for a stacked dataset; return a Figure.

    Scatter-by-pressure, count heatmap, and (when present) scatter-by-AOU.
    Panels are arranged in a single row.  The AOU panel is included when
    ``apparent_oxygen_utilization`` is present in *ds* (written by stage3 for
    instruments with dissolved oxygen data).

    QC masking: values the ``mask_flags`` rule hides are left out (the rule the
    stack records, else the default).

    Parameters
    ----------
    ds : xr.Dataset
        Stacked mooring dataset containing ``temperature`` and ``salinity``.
    width_in : float, optional
        Figure width (inches), the report slot; default the full content width.
    derived : DerivedFields or None, optional
        The page's derived quantities (position, θ), computed once by the report;
        read from *ds* when not given.
    record : dict or None, optional
        Filled with what was drawn: ``theta``, ``sigma_interval``, ``no_sigma``
        (why σθ is not drawn, or None), ``colour`` (``"pressure"`` or None) and
        ``oxygen`` (bool).
    full_range : bool, optional
        Axes over every value (the Stack cat), not the 1–99 % range.

    Returns
    -------
    plt.Figure or None
        Figure, or None if temperature or salinity are absent.  The y-axis is
        potential temperature with σθ contours when the position is known,
        in-situ temperature without contours otherwise.

    """
    if "temperature" not in ds.data_vars or "salinity" not in ds.data_vars:
        return None

    S_all = masked(ds, "salinity")
    T_all, position, t_label = _theta(
        ds,
        masked(ds, "temperature"),
        S_all,
        ds["pressure"].values.astype(float) if "pressure" in ds.data_vars else None,
        derived,
    )

    T_flat = T_all.ravel()
    S_flat = S_all.ravel()

    # Pressure: nominal and interpolated values (7, 8) are kept.
    P_flat: np.ndarray | None = None
    if "pressure" in ds.data_vars:
        P_arr = masked(ds, "pressure")
        P_flat = P_arr.ravel()

    finite = np.isfinite(T_flat) & np.isfinite(S_flat)
    if P_flat is not None:
        finite &= np.isfinite(P_flat)
    if finite.sum() < 5:
        return None

    # O2 saturation: optional third panel.
    SAT_flat: np.ndarray | None = None
    if "oxygen_saturation_pct" in ds.data_vars:
        SAT_flat = ds["oxygen_saturation_pct"].values.astype(float).ravel()

    has_sat = SAT_flat is not None and np.isfinite(SAT_flat).any()
    ncols = 3 if has_sat else 2
    # The count colorbar's label ("log10(count)") is wider than the standard reserve.
    fig, _ax, _cax = square_axes_grid(
        width_in, 1, ncols, per_panel_colorbar=True, cbar_txt_in=0.42
    )

    ax_scatter, ax_heat = _ax[0, 0], _ax[0, 1]
    ax_sat = _ax[0, 2] if has_sat else None

    # --- Left panel: T-S scatter coloured by pressure ---
    if P_flat is not None:
        pv = P_flat[finite]
        bounds_p, norm_p = colorbar_norm(
            vmin=float(np.nanpercentile(pv, 2)),
            vmax=float(np.nanpercentile(pv, 98)),
        )
        sc_p = ax_scatter.scatter(
            S_flat[finite],
            T_flat[finite],
            c=pv,
            cmap="viridis_r",
            norm=norm_p,
            s=2,
            linewidths=0,
            alpha=0.5,
            zorder=2,
            rasterized=True,
        )
        unit_colorbar(
            _cax[0, 0],
            sc_p,
            unit=params.vunit("pressure"),
            ticks=nice_colorbar_ticks(float(bounds_p[0]), float(bounds_p[-1])),
        )
        _cax[0, 0].invert_yaxis()  # shallow at the top
    else:
        _cax[0, 0].set_visible(False)  # no pressure → no colorbar
        ax_scatter.scatter(
            S_flat[finite],
            T_flat[finite],
            s=2,
            linewidths=0,
            alpha=0.4,
            rasterized=True,
        )
    # Shared limits (1/99 percentiles unless full_range, padded and rounded outward);
    # the heatmap matches, and one σθ grid covers them.
    pct = {"plow": 0.0, "phigh": 100.0} if full_range else {}
    _s_lim = _nice_axis_limits(S_flat[finite], **pct)
    _t_lim = _nice_axis_limits(T_flat[finite], **pct)
    contours = _figure_sigma0_contours(
        ax_scatter, ds, derived, position, _s_lim, _t_lim
    )
    interval = draw_sigma0_contours(ax_scatter, contours)
    if record is not None:
        record.update(
            theta=position is not None,
            sigma_interval=interval,
            no_sigma=None if contours is not None else no_sigma_reason(ds, derived),
            colour="pressure" if P_flat is not None else None,
            oxygen=False,
        )
    ax_scatter.set_xlabel(SALINITY_LABEL)
    ax_scatter.set_ylabel(t_label)
    plot_title(ax_scatter, "T-S (colour = pressure)")
    ax_scatter.set_xlim(*_s_lim)
    ax_scatter.set_ylim(*_t_lim)

    # --- Middle panel: count heatmap (shares the scatter's limits) ---
    _ts_heatmap_panel(
        ax_heat,
        fig,
        S_flat[finite],
        T_flat[finite],
        s_lim=_s_lim,
        t_lim=_t_lim,
        cax=_cax[0, 1],
        contours=contours,
        t_label=t_label,
    )

    # --- Right panel: T-S scatter coloured by O2 saturation ---
    if ax_sat is not None and SAT_flat is not None:
        sat_finite = finite & np.isfinite(SAT_flat)
        sat_v = SAT_flat[sat_finite]
        bounds_s, norm_s = colorbar_norm(
            vmin=float(np.nanpercentile(sat_v, 2)),
            vmax=float(np.nanpercentile(sat_v, 98)),
            n=11,
        )
        sc_s = ax_sat.scatter(
            S_flat[sat_finite],
            T_flat[sat_finite],
            c=sat_v,
            cmap="YlGnBu",
            norm=norm_s,
            s=2,
            linewidths=0,
            alpha=0.5,
            zorder=2,
            rasterized=True,
        )
        unit_colorbar(
            _cax[0, 2],
            sc_s,
            unit="%",
            ticks=nice_colorbar_ticks(float(bounds_s[0]), float(bounds_s[-1])),
        )
        draw_sigma0_contours(ax_sat, contours)
        if record is not None:
            record["oxygen"] = True
        ax_sat.set_xlabel(SALINITY_LABEL)
        ax_sat.set_ylabel(t_label)
        plot_title(ax_sat, "T-S (colour = O₂ sat.)")
        ax_sat.set_xlim(*_s_lim)
        ax_sat.set_ylim(*_t_lim)

    return fig


def draw_grid_ts_diagram(
    ds: xr.Dataset,
    n_bins: int = 60,
    *,
    width_in: float = report_tokens.W_FULL,
    derived: DerivedFields | None = None,
) -> tuple[plt.Figure, dict] | None:
    """T-S diagram for gridded mooring data; return a (Figure, bounds_dict) tuple.

    Left panel: 2-D count heatmap (log₁₀ samples per T-S bin).
    Right panel (when ``oxygen_saturation_pct`` is present): median O₂ saturation
    per T-S bin, computed with ``scipy.stats.binned_statistic_2d``.  Bins with
    fewer than 5 samples are masked white.

    This lets you see which water masses (T-S combinations) are oxygen-rich vs
    oxygen-depleted at this mooring — a compact water-mass characterisation.

    Parameters
    ----------
    ds : xr.Dataset
        Gridded dataset with at least ``temperature`` and ``salinity`` variables.
    n_bins : int
        Number of bins per axis for the 2-D histogram.
    width_in : float, optional
        Figure width (inches), the report slot; default the full content width.
    derived : DerivedFields or None, optional
        The page's derived quantities (position, θ), computed once by the report;
        read from *ds* when not given.

    Returns
    -------
    tuple of (plt.Figure, bounds_dict) or None
        The T-S axes are potential temperature (σθ contours) when the position is
        known, else in-situ temperature.  ``bounds_dict`` contains the
        axis/colorbar limits computed from the data so the hydro pcolormesh panels
        can share the same scales:

        - ``"t_lim"`` : (vmin, vmax) for in-situ temperature
        - ``"s_lim"`` : (vmin, vmax) for salinity
        - ``"o2_lim"`` : (vmin, vmax) for oxygen_saturation_pct, or None
        - ``"sigma_interval"`` : the σθ contour interval (kg m⁻³), or None
          without contours

    """
    import matplotlib.pyplot as plt
    from scipy.stats import binned_statistic_2d

    if "temperature" not in ds.data_vars or "salinity" not in ds.data_vars:
        return None

    T_insitu = ds["temperature"].values.astype(float)
    S2 = ds["salinity"].values.astype(float)
    p_grid = (
        ds["pressure"].broadcast_like(ds["temperature"]).values.astype(float)
        if "pressure" in ds.coords or "pressure" in ds.data_vars
        else None
    )
    T2, position, t_label = _theta(ds, T_insitu, S2, p_grid, derived)
    T, S = T2.ravel(), S2.ravel()
    finite = np.isfinite(T) & np.isfinite(S)
    if finite.sum() < 10:
        return None

    # Axis limits from robust percentiles (1/99, padded and rounded outward) so
    # outliers don't stretch the box.  The bounds returned for the hydro panels are
    # those of in-situ temperature, which they show.
    s_lo, s_hi = _nice_axis_limits(S[finite])
    t_lo, t_hi = _nice_axis_limits(T[finite])
    insitu = T_insitu.ravel()[finite]
    ts_bounds: dict = {
        "t_lim": _nice_axis_limits(insitu),
        "s_lim": (s_lo, s_hi),
        "o2_lim": None,
    }

    has_o2 = "oxygen_saturation_pct" in ds.data_vars
    O2 = ds["oxygen_saturation_pct"].values.astype(float).ravel() if has_o2 else None
    o2_valid = finite & np.isfinite(O2) if (O2 is not None) else np.zeros_like(finite)
    has_o2 = has_o2 and o2_valid.any()

    ncols = 2 if has_o2 else 1
    fig, _ax, _cax = square_axes_grid(
        width_in,
        1,
        ncols,
        per_panel_colorbar=True,
    )
    axes = list(_ax[0])
    contours = _figure_sigma0_contours(
        axes[0], ds, derived, position, (s_lo, s_hi), (t_lo, t_hi)
    )

    # Panel 1: count heatmap (shares the data limits + matched colorbar)
    ts_bounds["sigma_interval"] = _ts_heatmap_panel(
        axes[0],
        fig,
        S[finite],
        T[finite],
        n_bins=n_bins,
        s_lim=(s_lo, s_hi),
        t_lim=(t_lo, t_hi),
        cax=_cax[0, 0],
        contours=contours,
        t_label=t_label,
    )
    plot_title(axes[0], "Values per bin")

    # Panel 2: median O2 saturation per T-S bin
    if has_o2 and O2 is not None:
        s_edges = np.linspace(s_lo, s_hi, n_bins + 1)
        t_edges = np.linspace(t_lo, t_hi, n_bins + 1)

        o2_med, _, _, _ = binned_statistic_2d(
            S[o2_valid],
            T[o2_valid],
            O2[o2_valid],
            statistic="median",
            bins=[s_edges, t_edges],
        )
        cnt, _, _, _ = binned_statistic_2d(
            S[o2_valid],
            T[o2_valid],
            O2[o2_valid],
            statistic="count",
            bins=[s_edges, t_edges],
        )
        # o2_med shape is (n_s, n_t); transpose to (n_t, n_s) for pcolormesh
        populated = cnt >= 5
        o2_masked = np.ma.masked_where(~populated | ~np.isfinite(o2_med), o2_med)

        valid_vals = o2_med[populated & np.isfinite(o2_med)]
        if valid_vals.size:
            o2_vmin = float(np.nanpercentile(valid_vals, 2))
            o2_vmax = float(np.nanpercentile(valid_vals, 98))
            ts_bounds["o2_lim"] = (o2_vmin, o2_vmax)
            bounds, norm = colorbar_norm(vmin=o2_vmin, vmax=o2_vmax, n=11)
            cmap = plt.cm.BrBG.copy()
            cmap.set_bad("white")
            pc = axes[1].pcolormesh(
                s_edges,
                t_edges,
                o2_masked.T,
                cmap=cmap,
                norm=norm,
                shading="flat",
            )
            unit_colorbar(
                _cax[0, 1],
                pc,
                unit="%",
                ticks=nice_colorbar_ticks(float(bounds[0]), float(bounds[-1])),
            )
            draw_sigma0_contours(axes[1], contours)
            axes[1].set_xlim(s_lo, s_hi)
            axes[1].set_ylim(t_lo, t_hi)
        axes[1].set_xlabel(params.vlabel("salinity"))
        axes[1].set_ylabel(t_label)
        plot_title(axes[1], "Median O₂ saturation per T-S bin")

    return fig, ts_bounds


#: Share of a level's T-S points enclosed by its outline on the T-S by deployment figure.
TS_OUTLINE_FRACTION: float = 0.5
#: Bins of the 2-D histogram behind each outline, along S and T.
TS_OUTLINE_BINS: tuple[int, int] = (140, 135)
#: A level gets an outline and a median only with at least this many T-S points: a
#: small floor so a short but complete deployment (e.g. a two-week mooring) is kept,
#: while a level with almost no data is left out. A count, not a duration, so the
#: sampling rate does not decide it.
TS_MIN_LEVEL_POINTS: int = 24
#: Above this many level outlines the figure draws medians and lines only (a site with
#: many deployments and many variable shallow levels becomes unreadable otherwise).
TS_MAX_OUTLINES: int = 40
#: Levels whose median pressures are closer than this (dbar) are averaged for the
#: median line, so co-located instruments do not make it zigzag.
TS_COLOCATED_DBAR: float = 10.0

#: Most points drawn per deployment in the T-S point cloud, picked at random.
TS_MAX_POINTS: int = 5000

#: Seed for that pick, so a page drawn twice shows the same points.
TS_POINTS_SEED: int = 0


def _outline_level(H: np.ndarray, fraction: float) -> float:
    """Return the histogram value whose higher bins hold *fraction* of the counts."""
    flat = np.sort(H.ravel())[::-1]
    cumulative = np.cumsum(flat) / flat.sum()
    return float(flat[min(np.searchsorted(cumulative, fraction), flat.size - 1)])


def _smooth3(H: np.ndarray) -> np.ndarray:
    """Return *H* averaged over each bin's 3 × 3 neighbourhood (edges padded with zeros)."""
    from numpy.lib.stride_tricks import sliding_window_view

    return sliding_window_view(np.pad(H, 1), (3, 3)).mean(axis=(-1, -2))


def _cover(limits: tuple[float, float], values: list[float]) -> tuple[float, float]:
    """Return *limits* widened (by 5 % of the span) to include every value."""
    lo, hi = limits
    vmin, vmax = min(values), max(values)
    pad = 0.05 * (max(hi, vmax) - min(lo, vmin))
    return (
        min(lo, vmin - pad) if vmin < lo else lo,
        max(hi, vmax + pad) if vmax > hi else hi,
    )


def _median_line(
    levels: list[tuple[float, float, float]],
) -> tuple[np.ndarray, np.ndarray]:
    """Return S and T of a deployment's median line through its levels in pressure order.

    Parameters
    ----------
    levels : list of (float, float, float)
        ``(median pressure, median S, median T)`` per level.

    Returns
    -------
    tuple of numpy.ndarray
        S and T of the line's vertices; levels closer than
        :data:`TS_COLOCATED_DBAR` in pressure are averaged into one vertex.
    """
    levels = sorted(levels)
    groups = [[levels[0]]]
    for item in levels[1:]:
        if item[0] - groups[-1][-1][0] < TS_COLOCATED_DBAR:
            groups[-1].append(item)
        else:
            groups.append([item])
    vertices = np.array([np.mean(g, axis=0) for g in groups])
    return vertices[:, 1], vertices[:, 2]


def draw_ts_by_deployment(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_HALF,
    derived: DerivedFields | None = None,
    record: dict | None = None,
) -> plt.Figure | None:
    """T-S of a concatenated stack by deployment: an outline and a median per instrument level.

    For each depth and deployment with at least :data:`TS_MIN_LEVEL_POINTS` points of
    temperature and salinity, the outline encloses :data:`TS_OUTLINE_FRACTION` of
    that level's points (the densest region of its T-S histogram, smoothed over
    3 × 3 bins), and a dot marks its median S and T. With more than
    :data:`TS_MAX_OUTLINES` such levels the outlines are left out and only the
    medians and lines are drawn. A line joins each
    deployment's medians in pressure order (levels within
    :data:`TS_COLOCATED_DBAR` averaged): a guide to the eye along the profile, not
    interpolated data. Two consecutive deployments share a color; the second has
    dashed outlines and line and a square marker. Values the Stack cat's
    ``mask_flags`` rule hides are left out. A shift between the deployments at
    the same depth is what this panel is for.

    Parameters
    ----------
    ds : xr.Dataset
        A concatenated stack, one column per depth (``time, N_LEVELS``), with
        ``temperature``, ``salinity``, ``cell_deployment(time, N_LEVELS)`` (the
        deployment behind each value, -1 none) and ``deployment_name(DEPLOYMENT)``;
        ``pressure`` orders the median line (else the column order).
    width_in : float, optional
        Figure width in inches.

    derived : DerivedFields or None, optional
        The page's derived quantities (position, θ), computed once by the report;
        read from *ds* when not given.  The y-axis is potential temperature with
        σθ contours when the position is known, else in-situ temperature.
    record : dict or None, optional
        Filled with ``levels`` (levels drawn), ``outlines`` (bool) and
        ``sigma_interval``.

    Returns
    -------
    plt.Figure or None
        None without temperature, salinity or the deployment record, or when no
        level has enough data.
    """
    import matplotlib.pyplot as plt

    from .helpers import (
        deployment_color,
        deployment_line,
        deployment_marker,
        legend_below,
        short_names,
    )

    needed = ("temperature", "salinity", "cell_deployment", "deployment_name")
    if any(v not in ds.variables for v in needed):
        return None
    S = masked(ds, "salinity")
    T, position, t_label = _theta(
        ds,
        masked(ds, "temperature"),
        S,
        masked(ds, "pressure") if "pressure" in ds.data_vars else None,
        derived,
    )
    P = (
        ds["pressure"].values.astype(float)
        if "pressure" in ds and ds["pressure"].shape == T.shape
        else None
    )
    names = short_names([str(n) for n in ds["deployment_name"].values])
    n_dep = len(names)
    cell_dep = ds["cell_deployment"].values

    # (k, column, S, T) of every level that is drawn.
    cells = []
    for j in range(cell_dep.shape[1]):
        for k in np.unique(cell_dep[:, j][cell_dep[:, j] >= 0]):
            ok = (cell_dep[:, j] == k) & np.isfinite(S[:, j]) & np.isfinite(T[:, j])
            if ok.sum() >= TS_MIN_LEVEL_POINTS:
                cells.append((int(k), j, S[ok, j], T[ok, j], ok))
    if not cells:
        return None
    s_all = np.concatenate([c[2] for c in cells])
    t_all = np.concatenate([c[3] for c in cells])
    # Axis limits from the points, widened to every level's median so no
    # median line runs off the panel.
    s_med = [float(np.median(c[2])) for c in cells]
    t_med = [float(np.median(c[3])) for c in cells]
    s_lim = _cover(_nice_axis_limits(s_all, plow=0.0, phigh=100.0), s_med)
    t_lim = _cover(_nice_axis_limits(t_all, plow=0.0, phigh=100.0), t_med)
    s_edges = np.linspace(*s_lim, TS_OUTLINE_BINS[0] + 1)
    t_edges = np.linspace(*t_lim, TS_OUTLINE_BINS[1] + 1)
    s_mid, t_mid = (s_edges[:-1] + s_edges[1:]) / 2, (t_edges[:-1] + t_edges[1:]) / 2

    fig, ax = plt.subplots(figsize=(width_in, width_in), layout="constrained")
    outlines = len(cells) <= TS_MAX_OUTLINES
    medians: dict[int, list[tuple[float, float, float]]] = {}
    for k, j, s, t, ok in cells:
        style = deployment_line(k, n_dep)
        H = (
            _smooth3(np.histogram2d(s, t, bins=[s_edges, t_edges])[0])
            if outlines
            else None
        )
        if H is not None and H.sum() > 0:
            ax.contour(
                s_mid,
                t_mid,
                H.T,
                levels=[_outline_level(H, TS_OUTLINE_FRACTION)],
                colors=[style["color"]],
                linestyles=[style["ls"]],
                linewidths=0.9,
                zorder=3,
            )
        order = float(np.nanmedian(P[ok, j])) if P is not None else float(j)
        medians.setdefault(k, []).append(
            (order, float(np.median(s)), float(np.median(t)))
        )
    for k, levels in medians.items():
        style = deployment_line(k, n_dep)
        line_s, line_t = _median_line(levels)
        ax.plot(
            line_s, line_t, lw=1.8, alpha=0.5, zorder=4, solid_capstyle="round", **style
        )
        ax.plot(
            [m[1] for m in levels],
            [m[2] for m in levels],
            ls="none",
            marker=deployment_marker(k, n_dep),
            color=deployment_color(k, n_dep),
            mec="black",
            mew=0.4,
            ms=4.5,
            zorder=5,
        )
    interval = draw_sigma0_contours(
        ax, _figure_sigma0_contours(ax, ds, derived, position, s_lim, t_lim)
    )
    if record is not None:
        record.update(levels=len(cells), outlines=outlines, sigma_interval=interval)
    ax.set_xlim(*s_lim)
    ax.set_ylim(*t_lim)
    ax.set_xlabel(SALINITY_LABEL)
    ax.set_ylabel(t_label)
    legend_below(
        fig,
        [
            plt.Line2D(
                [],
                [],
                marker=deployment_marker(k, n_dep),
                mec="black",
                mew=0.4,
                label=names[k],
                **deployment_line(k, n_dep),
            )
            for k in sorted(medians)
        ],
    )
    plot_title(ax, "T-S by deployment")
    return fig


def draw_ts_points_by_deployment(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_HALF,
    derived: DerivedFields | None = None,
    record: dict | None = None,
) -> plt.Figure | None:
    """T-S point cloud of a concatenated stack, one color per deployment, thinned.

    At most :data:`TS_MAX_POINTS` values per deployment, picked at random with a
    fixed seed (:data:`TS_POINTS_SEED`), so years of hourly values do not bury one
    another and the page is the same each time it is drawn. Values the Stack cat's
    ``mask_flags`` rule hides are left out. Deployments are drawn in
    random order, point by point, so none sits on top of all the others.

    Parameters
    ----------
    ds : xr.Dataset
        A concatenated stack, one column per depth (``time, N_LEVELS``), with
        ``temperature``, ``salinity``, ``cell_deployment(time, N_LEVELS)`` (the
        deployment behind each value, -1 none) and ``deployment_name(DEPLOYMENT)``.
    width_in : float, optional
        Figure width in inches.

    derived : DerivedFields or None, optional
        The page's derived quantities (position, θ), computed once by the report;
        read from *ds* when not given.  The y-axis is potential temperature with
        σθ contours when the position is known, else in-situ temperature.
    record : dict or None, optional
        Filled with ``sigma_interval``.

    Returns
    -------
    plt.Figure or None
        None without temperature, salinity or the deployment record, or with no
        value left.
    """
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    from .helpers import (
        deployment_color,
        deployment_marker,
        legend_below,
        short_names,
    )

    needed = ("temperature", "salinity", "cell_deployment", "deployment_name")
    if any(v not in ds.variables for v in needed):
        return None
    S = masked(ds, "salinity")
    T, position, t_label = _theta(
        ds,
        masked(ds, "temperature"),
        S,
        masked(ds, "pressure") if "pressure" in ds.data_vars else None,
        derived,
    )
    names = short_names([str(n) for n in ds["deployment_name"].values])
    n_dep = len(names)
    cell_dep = ds["cell_deployment"].values
    rng = np.random.default_rng(TS_POINTS_SEED)
    s_pts, t_pts, k_pts = [], [], []
    for k in range(n_dep):
        cells = cell_dep == k
        s, t = S[cells], T[cells]
        ok = np.flatnonzero(np.isfinite(s) & np.isfinite(t))
        if ok.size > TS_MAX_POINTS:
            ok = np.sort(rng.choice(ok, TS_MAX_POINTS, replace=False))
        s_pts.append(s[ok])
        t_pts.append(t[ok])
        k_pts.append(np.full(ok.size, k))
    s_all, t_all = np.concatenate(s_pts), np.concatenate(t_pts)
    if not s_all.size:
        return None
    k_all = np.concatenate(k_pts)
    order = rng.permutation(s_all.size)
    fig, ax = plt.subplots(figsize=(width_in, width_in), layout="constrained")
    # One scatter call per marker shape; points of every deployment interleaved.
    markers = np.array([deployment_marker(k, n_dep) for k in range(n_dep)])
    colors = np.array([deployment_color(k, n_dep) for k in range(n_dep)])
    for marker in np.unique(markers):
        pick = order[markers[k_all[order]] == marker]
        ax.scatter(
            s_all[pick],
            t_all[pick],
            s=3,
            linewidths=0,
            alpha=0.5,
            marker=marker,
            c=colors[k_all[pick]],
            rasterized=True,
        )
    s_lim = _nice_axis_limits(s_all, plow=0.0, phigh=100.0)
    t_lim = _nice_axis_limits(t_all, plow=0.0, phigh=100.0)
    interval = draw_sigma0_contours(
        ax, _figure_sigma0_contours(ax, ds, derived, position, s_lim, t_lim)
    )
    if record is not None:
        record["sigma_interval"] = interval
    ax.set_xlim(*s_lim)
    ax.set_ylim(*t_lim)
    ax.set_xlabel(SALINITY_LABEL)
    ax.set_ylabel(t_label)
    handles = [
        Line2D(
            [],
            [],
            ls="none",
            marker=deployment_marker(k, n_dep),
            ms=6,
            color=deployment_color(k, n_dep),
            label=names[k],
        )
        for k in range(n_dep)
        if (k_all == k).any()
    ]
    legend_below(fig, handles)
    plot_title(ax, f"T-S by deployment (≤ {TS_MAX_POINTS:,} points each)")
    return fig


def draw_ts_medians(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_HALF,
    derived: DerivedFields | None = None,
    record: dict | None = None,
) -> plt.Figure | None:
    """Median T and S of each instrument in each deployment, with interquartile whiskers.

    One point per depth and deployment of a concatenated stack, in the deployment's
    color (Okabe-Ito). Values the Stack cat's ``mask_flags``
    rule hides are left out.

    Parameters
    ----------
    ds : xr.Dataset
        A concatenated stack, one column per depth, with ``temperature``,
        ``salinity``, ``cell_deployment(time, N_LEVELS)`` and
        ``deployment_name(DEPLOYMENT)``.
    width_in : float, optional
        Figure width in inches.

    derived : DerivedFields or None, optional
        The page's derived quantities (position, θ), computed once by the report;
        read from *ds* when not given.  The y-axis is potential temperature with
        σθ contours when the position is known, else in-situ temperature.
    record : dict or None, optional
        Filled with ``sigma_interval``.

    Returns
    -------
    plt.Figure or None
        None when no level has both temperature and salinity.
    """
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FormatStrFormatter, MaxNLocator

    from .helpers import deployment_color, deployment_marker, short_names

    needed = ("temperature", "salinity", "cell_deployment", "deployment_name")
    if any(v not in ds.variables for v in needed):
        return None
    arrays = {}
    for var in ("temperature", "salinity"):
        arrays[var] = masked(ds, var)
    arrays["temperature"], position, t_label = _theta(
        ds,
        arrays["temperature"],
        arrays["salinity"],
        masked(ds, "pressure") if "pressure" in ds.data_vars else None,
        derived,
    )
    names = short_names([str(n) for n in ds["deployment_name"].values])
    n_dep = len(names)
    cell_dep = ds["cell_deployment"].values
    rows = []
    for j in range(cell_dep.shape[1]):
        t, s = arrays["temperature"][:, j], arrays["salinity"][:, j]
        for k in np.unique(cell_dep[:, j][cell_dep[:, j] >= 0]):
            ok = np.isfinite(t) & np.isfinite(s) & (cell_dep[:, j] == k)
            if ok.sum() < 3:
                continue
            rows.append(
                (
                    int(k),
                    np.percentile(s[ok], [25, 50, 75]),
                    np.percentile(t[ok], [25, 50, 75]),
                )
            )
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(width_in, width_in), layout="constrained")
    for k, sq, tq in rows:
        color = deployment_color(k, n_dep)
        ax.errorbar(
            sq[1],
            tq[1],
            xerr=[[sq[1] - sq[0]], [sq[2] - sq[1]]],
            yerr=[[tq[1] - tq[0]], [tq[2] - tq[1]]],
            fmt=deployment_marker(k, n_dep),
            color=color,
            ecolor=color,
            elinewidth=1.0,
            markersize=5,
            markeredgecolor="black",
            markeredgewidth=0.4,
        )
    all_s = np.concatenate([[r[1][0], r[1][2]] for r in rows])
    all_t = np.concatenate([[r[2][0], r[2][2]] for r in rows])
    s_lim = _nice_axis_limits(all_s, plow=0.0, phigh=100.0)
    t_lim = _nice_axis_limits(all_t, plow=0.0, phigh=100.0)
    interval = draw_sigma0_contours(
        ax, _figure_sigma0_contours(ax, ds, derived, position, s_lim, t_lim)
    )
    if record is not None:
        record["sigma_interval"] = interval
    ax.set_xlim(*s_lim)
    ax.set_ylim(*t_lim)
    ax.xaxis.set_major_locator(MaxNLocator(4))
    ax.xaxis.set_major_formatter(FormatStrFormatter("%.3f"))
    ax.set_xlabel(SALINITY_LABEL)
    ax.set_ylabel(t_label)
    used = sorted({r[0] for r in rows})
    ax.legend(
        handles=[
            plt.Line2D(
                [],
                [],
                ls="",
                marker=deployment_marker(k, n_dep),
                color=deployment_color(k, n_dep),
                markeredgecolor="black",
                markeredgewidth=0.4,
                label=names[k],
            )
            for k in used
        ],
        loc="best",
        fontsize="small",
    )
    plot_title(ax, "T-S medians (IQR whiskers)")
    return fig
