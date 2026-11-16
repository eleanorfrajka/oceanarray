"""T-S diagram and thermohaline structure plot functions."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from oceanarray.plotters.primitives import nice_colorbar_ticks
from oceanarray.reports._vendored import design_tokens as report_tokens

from ..config import parameters as params
from .helpers import QC_MARKER as _QC_MARKER
from .helpers import masked
from .primitives import colorbar_norm, plot_title, square_axes_grid, unit_colorbar

if TYPE_CHECKING:
    import matplotlib.pyplot as plt
    import xarray as xr


@lru_cache(maxsize=32)
def _sigma0_grid(
    s_lo: float, s_hi: float, t_lo: float, t_hi: float, n_grid: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return S, T and σ0 on an *n_grid* × *n_grid* grid (cached: ~3 calls per figure share one)."""
    import gsw

    Sg, Tg = np.meshgrid(
        np.linspace(s_lo, s_hi, n_grid), np.linspace(t_lo, t_hi, n_grid)
    )
    SA = gsw.SA_from_SP(Sg, 0.0, 0.0, 0.0)
    CT = gsw.CT_from_t(SA, Tg, 0.0)
    return Sg, Tg, gsw.sigma0(SA, CT)


def _add_sigma0_contours(
    ax: plt.Axes, S_data: np.ndarray, T_data: np.ndarray, n_grid: int = 200
) -> None:
    """Overlay sigma-0 contour lines on a T-S axes."""
    try:
        s_min, s_max = np.nanmin(S_data), np.nanmax(S_data)
        t_min, t_max = np.nanmin(T_data), np.nanmax(T_data)
        s_pad = max((s_max - s_min) * 0.05, 0.05)
        t_pad = max((t_max - t_min) * 0.05, 0.05)
        # Limits rounded (to 0.001) so panels of one figure share the cached grid.
        Sg, Tg, sigma0 = _sigma0_grid(
            round(float(s_min - s_pad), 3),
            round(float(s_max + s_pad), 3),
            round(float(t_min - t_pad), 3),
            round(float(t_max + t_pad), 3),
            n_grid,
        )
        cs = ax.contour(
            Sg,
            Tg,
            sigma0,
            levels=8,
            colors="0.35",
            linewidths=0.6,
            linestyles="--",
            zorder=1,
        )
        ax.clabel(cs, fmt="%.1f", fontsize=report_tokens.CLABEL_FS, inline=True)
    except (TypeError, ValueError):
        pass


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
) -> None:
    """Render a T-S 2-D count heatmap on *ax*."""
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
    _add_sigma0_contours(ax, S, T)
    ax.set_xlim(s_lo, s_hi)
    ax.set_ylim(t_lo, t_hi)
    ax.set_xlabel(params.vlabel("salinity"))
    ax.set_ylabel(params.vlabel("temperature"))
    plot_title(ax, "T-S heat map")


def draw_ts_diagram(
    nc_path: Path, *, width_in: float = report_tokens.W_FULL
) -> plt.Figure | None:
    """T-S diagram from a NetCDF path; return a Figure.

    Scatter by pressure, 2-D count heatmap, and (when present) scatter by O2 saturation.

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
        Figure, or None if temperature or salinity are absent.

    """
    import xarray as xr

    with xr.open_dataset(nc_path, decode_timedelta=False) as ds:
        ds.load()

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

        t_flags = (
            ds["temperature_qc"].values.astype(int)
            if "temperature_qc" in ds
            else np.ones(len(T), dtype=int)
        )
        s_flags = (
            ds["salinity_qc"].values.astype(int)
            if "salinity_qc" in ds
            else np.ones(len(T), dtype=int)
        )
        combined_flags = np.where(
            np.maximum(t_flags, s_flags) >= 4,
            4,
            np.where(np.maximum(t_flags, s_flags) == 3, 3, 1),
        )
        good_mask = finite & (combined_flags == 1)
        suspect_mask = finite & (combined_flags == 3)
        bad_mask = finite & (combined_flags == 4)

        has_sat = "oxygen_saturation_pct" in ds.data_vars
        sat_data = ds["oxygen_saturation_pct"].values.astype(float) if has_sat else None

    ncols = 3 if has_sat else 2
    # Square panels with per-panel height-matched colorbars (shared deterministic
    # layout — same path as the hodographs/trajectories).
    fig, _ax, _cax = square_axes_grid(width_in, 1, ncols, per_panel_colorbar=True)
    ax_l, ax_r = _ax[0, 0], _ax[0, 1]
    ax_sat = _ax[0, 2] if has_sat else None

    # Panel 1: T-S scatter coloured by pressure
    vmin = np.nanpercentile(C[finite], 5)
    vmax = np.nanpercentile(C[finite], 95)
    sc = ax_l.scatter(
        S[good_mask],
        T[good_mask],
        c=C[good_mask],
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
    if suspect_mask.any():
        ax_l.scatter(
            S[suspect_mask],
            T[suspect_mask],
            label=f"suspect ({suspect_mask.sum()})",
            **_QC_MARKER[3],
        )
    if bad_mask.any():
        ax_l.scatter(
            S[bad_mask],
            T[bad_mask],
            label=f"bad ({bad_mask.sum()})",
            **_QC_MARKER[4],
        )
    if suspect_mask.any() or bad_mask.any():
        ax_l.legend(loc="best", framealpha=0.8)
    _add_sigma0_contours(ax_l, S[finite], T[finite])
    ax_l.set_xlabel(params.vlabel("salinity"))
    ax_l.set_ylabel(params.vlabel("temperature"))
    plot_title(ax_l, "T-S (colour = pressure)")

    # Shared T-S axis limits (the dot plot's data range) so the heatmap and O₂
    # panels use identical axes; square every panel box.
    _sf, _tf = S[finite], T[finite]
    _sp = 0.02 * (float(np.nanmax(_sf) - np.nanmin(_sf)) + 1e-9)
    _tp = 0.02 * (float(np.nanmax(_tf) - np.nanmin(_tf)) + 1e-9)
    _s_lim = (float(np.nanmin(_sf)) - _sp, float(np.nanmax(_sf)) + _sp)
    _t_lim = (float(np.nanmin(_tf)) - _tp, float(np.nanmax(_tf)) + _tp)
    ax_l.set_xlim(*_s_lim)
    ax_l.set_ylim(*_t_lim)

    # Panel 2: count heatmap (shares the dot plot's limits + matched colorbar)
    _ts_heatmap_panel(
        ax_r, fig, S[finite], T[finite], s_lim=_s_lim, t_lim=_t_lim, cax=_cax[0, 1]
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
            _add_sigma0_contours(ax_sat, S[sat_finite], T[sat_finite])
        ax_sat.set_xlabel(params.vlabel("salinity"))
        ax_sat.set_ylabel(params.vlabel("temperature"))
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
    ds: xr.Dataset, *, width_in: float = report_tokens.W_FULL
) -> plt.Figure | None:
    """T-S diagram for a stacked dataset; return a Figure.

    Scatter-by-pressure, count heatmap, and (when present) scatter-by-AOU.
    Panels are arranged in a single row.  The AOU panel is included when
    ``apparent_oxygen_utilization`` is present in *ds* (written by stage3 for
    instruments with dissolved oxygen data).

    QC masking: on a stack, values its ``mask_flags`` rule hides are left out; on
    an instrument file, values flagged 3, 4 or 9 (``parameters.QC_BAD_FLAGS``).

    Parameters
    ----------
    ds : xr.Dataset
        Stacked mooring dataset containing ``temperature`` and ``salinity``.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None
        Figure, or None if temperature or salinity are absent.

    """
    if "temperature" not in ds.data_vars or "salinity" not in ds.data_vars:
        return None

    T_all = masked(ds, "temperature")
    S_all = masked(ds, "salinity")

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
    fig, _ax, _cax = square_axes_grid(width_in, 1, ncols, per_panel_colorbar=True)

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
    _add_sigma0_contours(ax_scatter, S_flat[finite], T_flat[finite])
    ax_scatter.set_xlabel(params.vlabel("salinity"))
    ax_scatter.set_ylabel(params.vlabel("temperature"))
    plot_title(ax_scatter, "T-S (colour = pressure)")

    # Shared limits from robust percentiles (1/99, padded 5% each side and rounded
    # outward) so a few outliers don't stretch the box; the heatmap matches (boxes
    # are already square from square_axes_grid).
    _s_lim = _nice_axis_limits(S_flat[finite])
    _t_lim = _nice_axis_limits(T_flat[finite])
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
        _add_sigma0_contours(ax_sat, S_flat[sat_finite], T_flat[sat_finite])
        ax_sat.set_xlabel(params.vlabel("salinity"))
        ax_sat.set_ylabel(params.vlabel("temperature"))
        plot_title(ax_sat, "T-S (colour = O₂ sat.)")
        ax_sat.set_xlim(*_s_lim)
        ax_sat.set_ylim(*_t_lim)

    return fig


def draw_grid_ts_diagram(
    ds: xr.Dataset, n_bins: int = 60, *, width_in: float = report_tokens.W_FULL
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
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    tuple of (plt.Figure, bounds_dict) or None
        ``bounds_dict`` contains the axis/colorbar limits computed from the data so
        the hydro pcolormesh panels can share the same scales:

        - ``"t_lim"`` : (vmin, vmax) for temperature
        - ``"s_lim"`` : (vmin, vmax) for salinity
        - ``"o2_lim"`` : (vmin, vmax) for oxygen_saturation_pct, or None

    """
    import matplotlib.pyplot as plt
    from scipy.stats import binned_statistic_2d

    if "temperature" not in ds.data_vars or "salinity" not in ds.data_vars:
        return None

    T = ds["temperature"].values.astype(float).ravel()
    S = ds["salinity"].values.astype(float).ravel()
    finite = np.isfinite(T) & np.isfinite(S)
    if finite.sum() < 10:
        return None

    # Axis limits from robust percentiles (1/99, padded and rounded outward) so
    # outliers don't stretch the box — returned to caller so hydro panels share
    # the same scales.
    s_lo, s_hi = _nice_axis_limits(S[finite])
    t_lo, t_hi = _nice_axis_limits(T[finite])
    ts_bounds: dict = {"t_lim": (t_lo, t_hi), "s_lim": (s_lo, s_hi), "o2_lim": None}

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

    # Panel 1: count heatmap (shares the data limits + matched colorbar)
    _ts_heatmap_panel(
        axes[0],
        fig,
        S[finite],
        T[finite],
        n_bins=n_bins,
        s_lim=(s_lo, s_hi),
        t_lim=(t_lo, t_hi),
        cax=_cax[0, 0],
    )
    plot_title(axes[0], r"T-S count ($\log_{10}$ samples per bin)")

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
            _add_sigma0_contours(axes[1], S[o2_valid], T[o2_valid])
            axes[1].set_xlim(s_lo, s_hi)
            axes[1].set_ylim(t_lo, t_hi)
        axes[1].set_xlabel(params.vlabel("salinity"))
        axes[1].set_ylabel(params.vlabel("temperature"))
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
    ds: xr.Dataset, *, width_in: float = report_tokens.W_HALF
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
    T = masked(ds, "temperature")
    S = masked(ds, "salinity")
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
    s_lim = _cover(_nice_axis_limits(s_all), s_med)
    t_lim = _cover(_nice_axis_limits(t_all), t_med)
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
    _add_sigma0_contours(ax, s_all, t_all)
    ax.set_xlim(*s_lim)
    ax.set_ylim(*t_lim)
    ax.set_xlabel(params.vlabel("salinity"))
    ax.set_ylabel(params.vlabel("temperature"))
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
    ds: xr.Dataset, *, width_in: float = report_tokens.W_HALF
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
    T = masked(ds, "temperature")
    S = masked(ds, "salinity")
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
    _add_sigma0_contours(ax, s_all, t_all)
    ax.set_xlim(*_nice_axis_limits(s_all))
    ax.set_ylim(*_nice_axis_limits(t_all))
    ax.set_xlabel(params.vlabel("salinity"))
    ax.set_ylabel(params.vlabel("temperature"))
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
    ds: xr.Dataset, *, width_in: float = report_tokens.W_HALF
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

    Returns
    -------
    plt.Figure or None
        None when no level has both temperature and salinity.
    """
    import matplotlib.pyplot as plt

    from .helpers import deployment_color, deployment_marker

    needed = ("temperature", "salinity", "cell_deployment", "deployment_name")
    if any(v not in ds.variables for v in needed):
        return None
    arrays = {}
    for var in ("temperature", "salinity"):
        arrays[var] = masked(ds, var)
    names = [str(n) for n in ds["deployment_name"].values]
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
    _add_sigma0_contours(ax, all_s, all_t)
    ax.set_xlim(*_nice_axis_limits(all_s))
    ax.set_ylim(*_nice_axis_limits(all_t))
    ax.set_xlabel(params.vlabel("salinity"))
    ax.set_ylabel(params.vlabel("temperature"))
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
