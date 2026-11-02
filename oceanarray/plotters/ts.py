"""T-S diagram and thermohaline structure plot functions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from oceanarray.reports._vendored import design_tokens as report_tokens

from ..config import parameters as params
from ..utilities import nice_colorbar_ticks
from .helpers import QC_MARKER as _QC_MARKER
from .primitives import colorbar_norm, plot_title, square_axes_grid, unit_colorbar

if TYPE_CHECKING:
    import matplotlib.pyplot as plt
    import xarray as xr


def _add_sigma0_contours(
    ax: plt.Axes, S_data: np.ndarray, T_data: np.ndarray, n_grid: int = 200
) -> None:
    """Overlay sigma-0 contour lines on a T-S axes."""
    try:
        import gsw

        s_min, s_max = np.nanmin(S_data), np.nanmax(S_data)
        t_min, t_max = np.nanmin(T_data), np.nanmax(T_data)
        s_pad = max((s_max - s_min) * 0.05, 0.05)
        t_pad = max((t_max - t_min) * 0.05, 0.05)
        s_c = np.linspace(s_min - s_pad, s_max + s_pad, n_grid)
        t_c = np.linspace(t_min - t_pad, t_max + t_pad, n_grid)
        Sg, Tg = np.meshgrid(s_c, t_c)
        SA = gsw.SA_from_SP(Sg, 0.0, 0.0, 0.0)
        CT = gsw.CT_from_t(SA, Tg, 0.0)
        sigma0 = gsw.sigma0(SA, CT)
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

    from ..utilities import nice_colorbar_ticks
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

    QC masking: values flagged 3, 4 or 9 (``parameters.QC_BAD_FLAGS``) are
    excluded; nominal and interpolated values (flags 7, 8) are kept.

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

    T_all = ds["temperature"].values.copy().astype(float)
    S_all = ds["salinity"].values.copy().astype(float)
    if "temperature_qc" in ds.data_vars:
        T_all[np.isin(ds["temperature_qc"].values, params.QC_BAD_FLAGS)] = np.nan
    if "salinity_qc" in ds.data_vars:
        S_all[np.isin(ds["salinity_qc"].values, params.QC_BAD_FLAGS)] = np.nan

    T_flat = T_all.ravel()
    S_flat = S_all.ravel()

    # Pressure: keep flags 7/8 (nominal, interpolated); only QC_BAD_FLAGS excluded.
    P_flat: np.ndarray | None = None
    if "pressure" in ds.data_vars:
        P_arr = ds["pressure"].values.astype(float)
        if "pressure_qc" in ds.data_vars:
            P_arr[np.isin(ds["pressure_qc"].values, params.QC_BAD_FLAGS)] = np.nan
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
