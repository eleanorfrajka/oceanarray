"""Tier-2 spectral figures: Welch spectra, wavelet scalograms and rotary spectra of a grid.

Computations are in :mod:`oceanarray.analysis.spectral`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np

from oceanarray.plotters.primitives import nice_colorbar_bounds, period_axis_ticks
from oceanarray.reports._vendored import design_tokens as report_tokens

from ..analysis.spectral import gonella_rotary_spectrum
from ..config import parameters as params
from ..config.unit_tables import unit_label, warn_if_not_canonical
from .helpers import duration_words, grid_despine
from .primitives import plot_title, square_axes_grid, unit_colorbar


def _mark_frequency_line(ax: plt.Axes, period: float, color: str) -> None:
    """Draw a marked-frequency reference line (tidal/inertial) on a spectrum."""
    # One thin dotted style so the temperature and rotary spectra mark frequencies
    # the same way; period is in the axes' x-units.
    ax.axvline(period, color=color, lw=report_tokens.pen("thin"), ls=":", alpha=0.65)


if TYPE_CHECKING:
    import matplotlib.axes
    import xr


def wavelet_panel(
    ax: matplotlib.axes.Axes,
    times: np.ndarray,
    periods: np.ndarray,
    power: np.ndarray,
    coi: np.ndarray,
    signif: np.ndarray | None = None,
    title: str = "",
    *,
    gap_mask: np.ndarray | None = None,
    min_period: float | None = None,
) -> mcolors.ScalarMappable:
    """Draw one wavelet scalogram panel onto *ax*.

    Renders log10(power) as a filled contour plot with period on a log y-axis
    (inverted so short periods are at the top), time on x.

    The COI region (``period > coi[t]``) is hatched with diagonal lines.
    Callers should pass ``effective_coi`` from ``compute_cwt`` rather than the
    raw pycwt COI — the effective COI already incorporates gap-edge wings so that
    one hatching call covers record edges, gap columns, and the surrounding
    unreliable region.

    The y-axis is trimmed to ``coi.max()`` so entirely-hatched long-period rows
    are not shown; gappier records automatically get a tighter period range.

    An optional 95 % significance contour is drawn in black.

    Parameters
    ----------
    ax:
        Target axes object.
    times:
        1-D array of time values (any type accepted by matplotlib, e.g. numpy
        datetime64 or float index).
    periods:
        1-D array of wavelet periods in days (length ``n_scales``).
    power:
        2-D array of real wavelet power, shape ``(n_scales, n_time)``.
    coi:
        1-D array of COI periods in days, length ``n_time``.  Pass
        ``effective_coi`` from ``compute_cwt`` to include gap-edge wings.
    signif:
        Optional 1-D significance threshold array, length ``n_scales``.  Where
        ``power[i, t] > signif[i]`` the transform is significant at the chosen
        confidence level.  If None, no significance contour is drawn.
    title:
        Axes title string (e.g. depth label).
    gap_mask:
        Optional boolean array, length ``n_time``: True where the series was
        gap-filled before the transform; those columns are cross-hatched.
    min_period:
        Shortest period shown (days); default the shortest computed.

    Returns
    -------
    matplotlib.cm.ScalarMappable
        Mappable suitable for passing to ``fig.colorbar()``.

    """
    n_scales, n_time = power.shape
    log_power = np.log10(np.where(power > 0, power, np.nan))

    vmin = float(np.nanpercentile(log_power, 2))
    vmax = float(np.nanpercentile(log_power, 98))
    bounds = nice_colorbar_bounds(vmin, vmax, n=20)
    norm = mcolors.BoundaryNorm(bounds, ncolors=256)
    cmap = plt.get_cmap("RdYlBu_r")

    cf = ax.contourf(
        times,
        periods,
        log_power,
        levels=bounds,
        norm=norm,
        cmap=cmap,
        extend="both",
    )

    # COI: hatch the entire unreliable region in one pass.  The caller passes
    # effective_coi which already encodes both record edges and gap-edge wings,
    # so gap columns and their COI halos are all covered by a single fill_between.
    # No separate gap hatching is needed.
    ax.fill_between(
        times,
        coi,
        periods[-1],
        facecolor="none",
        hatch="////",
        edgecolor="0.55",
        linewidth=0.0,
    )

    # Gaps filled before the transform: cross-hatched over the whole column.
    if gap_mask is not None and np.any(gap_mask):
        ax.fill_between(
            times,
            periods[0],
            periods[-1],
            where=np.asarray(gap_mask, dtype=bool),
            facecolor="none",
            hatch="xxxx",
            edgecolor="0.25",
            linewidth=0.0,
            step="mid",
        )

    # Significance contour
    if signif is not None:
        sig2d = signif[:, np.newaxis] * np.ones(n_time)
        ax.contour(
            times, periods, power / sig2d, levels=[1.0], colors="k", linewidths=0.8
        )

    # Trim the y-axis to the longest period that is reliable at any time step.
    # Entirely-hatched long-period rows are excluded; gappy records get a
    # tighter limit automatically.
    _p_min = float(periods.min())
    if min_period is not None:
        _p_min = max(_p_min, float(min_period))
    _p_max_reliable = float(coi.max())
    if _p_max_reliable <= _p_min:
        _p_max_reliable = float(periods.max())

    ax.set_yscale("log")
    ax.set_ylim(_p_min, _p_max_reliable)
    ax.invert_yaxis()

    # Human-readable y-axis ticks filtered to the visible range (shared helper)
    _ytv, _ytl = period_axis_ticks(_p_min, _p_max_reliable)
    if _ytv:
        from matplotlib.ticker import NullLocator

        ax.set_yticks(_ytv)
        ax.set_yticklabels(_ytl)
        ax.yaxis.set_minor_locator(NullLocator())
    ax.set_ylabel("Period")
    if title:
        plot_title(ax, title)

    return cf


#: Colours of the frequency markers: M2, K1, the inertial period, then any others.
_MARKER_COLORS: dict[str, str] = {"M2": "#c0392b", "K1": "#e67e22", "f": "#27ae60"}
_OTHER_MARKER_COLOR = "#7f8c8d"


def spectrum_markers(
    lat: float | None,
    extra: Sequence[tuple[str, float]] = (),
) -> list[tuple[str, float, str]]:
    """Return the frequency markers of the spectra: M2, K1, the inertial period, *extra*.

    Parameters
    ----------
    lat : float or None
        Mooring latitude (degrees); no inertial marker when None or 0.
    extra : sequence of (str, float), optional
        Further ``(label, period in days)`` markers (the mooring description's
        ``spectrum_markers``).

    Returns
    -------
    list of (str, float, str)
        ``(label, period in days, colour)``.
    """
    from oceanarray.analysis.spectral import (
        K1_PERIOD_DAYS,
        M2_PERIOD_DAYS,
        inertial_period_days,
    )

    out = [
        ("M2", M2_PERIOD_DAYS, _MARKER_COLORS["M2"]),
        ("K1", K1_PERIOD_DAYS, _MARKER_COLORS["K1"]),
    ]
    f_period = inertial_period_days(lat)
    if f_period is not None:
        out.append(("f", f_period, _MARKER_COLORS["f"]))
    out += [(str(lbl), float(per), _OTHER_MARKER_COLOR) for lbl, per in extra]
    return out


def _mark_periods(
    ax: plt.Axes,
    markers: Sequence[tuple[str, float, str]],
    lo: float,
    hi: float,
    scale: float = 1.0,
    y: float = 0.03,
    va: str = "bottom",
) -> None:
    """Draw and label the *markers* whose period (days) lies in ``[lo, hi]``."""
    from matplotlib.transforms import blended_transform_factory

    trans = blended_transform_factory(ax.transData, ax.transAxes)
    for lbl, period, color in markers:
        if lo <= period <= hi:
            _mark_frequency_line(ax, period * scale, color)
            ax.text(
                period * scale,
                y,
                lbl,
                rotation=90,
                va=va,
                ha="center",
                color=color,
                transform=trans,
                bbox={
                    "boxstyle": "round,pad=0.1",
                    "fc": "white",
                    "ec": "none",
                    "alpha": 0.6,
                },
            )


def _level_colormap() -> mcolors.Colormap:
    """Return the pressure colormap of spectrum lines (light = shallow, dark = deep)."""
    return mcolors.LinearSegmentedColormap.from_list(
        "Blues_dark", plt.get_cmap("Blues")(np.linspace(0.3, 1.0, 256))
    )


def draw_spectrum(
    da_temp: xr.DataArray,
    dt_seconds: float,
    lat: float = 0.0,
    hf_segment_days: float = 7.0,
    hf_x_max_days: float = 2.0,
    *,
    width_in: float = report_tokens.W_FULL,
    lf_segment_days: float = 14.0,
    levels: Sequence[int] | None = None,
    markers: Sequence[tuple[str, float]] = (),
) -> plt.Figure | None:
    """Two-panel Welch spectrum of gridded temperature at a few levels.

    Left: ``lf_segment_days`` Hann windows over all periods the windows resolve.
    Right: ``hf_segment_days`` Hann windows, zoomed to periods shorter than
    ``hf_x_max_days``.  Both 50 % overlap; on a record too short for several
    windows the windows are shortened (:func:`oceanarray.analysis.spectral.welch_segment`).
    The left panel gap-fills by linear interpolation before the estimate; the
    right uses only windows within unbroken runs of values.  Lines are coloured
    by pressure (colour bar); vertical lines mark M2, K1, the inertial period and
    any *markers*; a dashed −2 slope is drawn for reference.

    Parameters
    ----------
    da_temp : xarray.DataArray
        Gridded temperature with dimensions ``pressure`` and ``time``.
    dt_seconds : float
        Uniform time step of the grid in seconds.
    lat : float, optional
        Mooring latitude in decimal degrees, for the inertial period; 0 omits it.
    hf_segment_days : float, optional
        Window length of the right panel in days (default 7).
    hf_x_max_days : float, optional
        Longest period shown in the right panel in days (default 2); at most 3
        puts that axis in hours.
    width_in : float, optional
        Figure width in inches (the display slot).
    lf_segment_days : float, optional
        Window length of the left panel in days (default 14).
    levels : sequence of int, optional
        Pressure-level indices to draw (the page's choice); default
        :func:`oceanarray.analysis.spectral.select_levels` on all grid levels.
    markers : sequence of (str, float), optional
        Extra ``(label, period in days)`` markers.

    Returns
    -------
    matplotlib.figure.Figure or None
        None when no level has enough values.
    """
    from matplotlib.ticker import NullLocator

    from oceanarray.analysis.spectral import (
        select_levels,
        welch_psd,
        welch_psd_gapaware,
        welch_segment,
    )

    warn_if_not_canonical(
        da_temp, "degree_Celsius", quantity="temperature PSD (°C² cpd⁻¹)"
    )
    if da_temp.dims[0] != "pressure":
        da_temp = da_temp.transpose("pressure", ...)
    arr = da_temp.values.astype(float)
    press_vals = (
        da_temp.coords["pressure"].values.astype(float)
        if "pressure" in da_temp.coords
        else np.arange(arr.shape[0], dtype=float)
    )
    n_time = arr.shape[1]
    dt_days = dt_seconds / 86400.0
    seg_lf = welch_segment(n_time, dt_seconds, lf_segment_days)
    seg_hf = welch_segment(n_time, dt_seconds, hf_segment_days, min_samples=8)
    if levels is None:
        levels = select_levels(arr, press_vals)

    psds_lf, psds_hf, p_lf, p_hf = [], [], [], []
    freq_lf = freq_hf = None
    for k in levels:
        col = arr[k, :]
        good = np.isfinite(col)
        if good.sum() < seg_lf:
            continue
        filled = np.interp(np.arange(n_time), np.flatnonzero(good), col[good])
        freq_lf, p = welch_psd(filled, dt_days, seg_lf)
        psds_lf.append(p)
        p_lf.append(press_vals[k])
        f_hf, p_h, _n = welch_psd_gapaware(col, dt_days, seg_hf)
        if f_hf is not None:
            freq_hf = f_hf
            psds_hf.append(p_h)
            p_hf.append(press_vals[k])
    if freq_lf is None or not psds_lf:
        return None

    all_markers = spectrum_markers(lat if lat else None, markers)
    cmap = _level_colormap()
    lo_p, hi_p = min(p_lf), max(p_lf)
    if lo_p == hi_p:
        lo_p, hi_p = lo_p - 1.0, hi_p + 1.0
    norm = mcolors.Normalize(vmin=lo_p, vmax=hi_p)

    fig, _axes, cax = square_axes_grid(width_in, 1, 2, bottom_pad_in=0.5)
    ax_lf, ax_hf = _axes[0, 0], _axes[0, 1]
    nyq = 2.0 * dt_days

    def _panel(
        ax: plt.Axes,
        freq: np.ndarray,
        psds: list[np.ndarray],
        pressures: list[float],
        x_lo: float,
        x_hi: float,
        scale: float,
    ) -> None:
        """Draw one level's spectra, the −2 slope and the period axis on *ax*."""
        fmask = (freq > 0) & (freq <= 1.0 / nyq)
        period = 1.0 / freq[fmask]
        for psd, pv in zip(psds, pressures, strict=True):
            ax.loglog(period * scale, psd[fmask], color=cmap(norm(pv)), lw=0.9)
        keep = (period >= x_lo) & (period <= x_hi)
        if keep.any():
            mid = np.sqrt(x_lo * x_hi)
            i = int(np.argmin(np.abs(period - mid)))
            ref = float(np.median([psd[fmask][i] for psd in psds]))
            if np.isfinite(ref) and ref > 0:
                xs = np.array([x_lo, x_hi])
                ax.loglog(
                    xs * scale,
                    ref * (xs / mid) ** 2,
                    color="k",
                    lw=0.9,
                    ls="--",
                    alpha=0.4,
                )
        ax.set_xlim(x_hi * scale, x_lo * scale)
        ax.set_ylabel("PSD (°C² cpd⁻¹)")
        ax.xaxis.set_minor_locator(NullLocator())
        grid_despine(ax)

    # Left: every period the windows resolve.
    x_hi_lf = seg_lf * dt_days
    x_lo_lf = nyq
    _panel(ax_lf, freq_lf, psds_lf, p_lf, x_lo_lf, x_hi_lf, 1.0)
    tv, tl = period_axis_ticks(x_lo_lf, x_hi_lf)
    ax_lf.set_xticks(tv)
    ax_lf.set_xticklabels(tl, rotation=45, ha="right")
    ax_lf.set_xlabel("Period")
    _mark_periods(ax_lf, all_markers, x_lo_lf, x_hi_lf)
    plot_title(ax_lf, f"{duration_words(seg_lf * dt_days, adjective=True)} windows")

    # Right: the short periods, with shorter windows.
    use_hours = hf_x_max_days <= 3.0
    scale = 24.0 if use_hours else 1.0
    x_hi_hf = min(hf_x_max_days, seg_hf * dt_days)
    if psds_hf and freq_hf is not None:
        _panel(ax_hf, freq_hf, psds_hf, p_hf, nyq, x_hi_hf, scale)
        _mark_periods(ax_hf, all_markers, nyq, x_hi_hf, scale=scale)
    else:
        ax_hf.text(
            0.5,
            0.5,
            "No unbroken run as long as one window",
            transform=ax_hf.transAxes,
            ha="center",
            va="center",
            color="gray",
        )
    ticks_h = [
        (48.0, "2d"),
        (24.0, "1d"),
        (12.0, "12h"),
        (6.0, "6h"),
        (2.0, "2h"),
        (1.0, "1h"),
        (0.5, "30min"),
        (10.0 / 60, "10min"),
    ]
    lo_h, hi_h = nyq * 24.0, x_hi_hf * 24.0
    hf_t = [(h / 24.0 * scale, lbl) for h, lbl in ticks_h if lo_h <= h <= hi_h]
    ax_hf.set_xticks([v for v, _ in hf_t])
    ax_hf.set_xticklabels([lbl for _, lbl in hf_t], rotation=45, ha="right")
    ax_hf.set_xlabel(f"Period ({'hours' if use_hours else 'days'})")
    plot_title(
        ax_hf,
        f"{duration_words(seg_hf * dt_days, adjective=True)} windows, periods under "
        f"{duration_words(hf_x_max_days)}",
    )

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    ticks = [float(p) for p in p_lf]
    cb = unit_colorbar(cax, sm, unit=params.vunit("pressure"), ticks=ticks)
    cb.ax.set_yticklabels([f"{p:.0f}" for p in ticks])
    cax.invert_yaxis()
    return fig


def draw_wavelet(
    da_temp: xr.DataArray,
    dt_seconds: float,
    wavelet: str = "morlet",
    *,
    width_in: float = report_tokens.W_FULL,
    levels: Sequence[int] | None = None,
) -> plt.Figure | None:
    """Continuous wavelet transform scalogram for gridded temperature; return a Figure.

    Produces up to three stacked wavelet + time-series panel pairs, at the
    outermost levels with values for at least
    :data:`~oceanarray.analysis.spectral.SPECTRAL_MIN_FILLED` of the record and one
    between (:func:`oceanarray.analysis.spectral.select_levels`), unless *levels* names
    them.  The period axis starts at six time steps, and never below 2 hours.
    Columns gap-filled before the transform are cross-hatched.

    Each wavelet panel shows log10(Morlet power) as a filled contour plot; the
    gap-aware cone of influence (COI) is hatched -- this covers both the record
    edges and the COI wings that spread out from every data gap.  The y-axis is
    trimmed per-panel to the longest period that is reliable at any time step, so
    gappier levels automatically get a tighter period range.  A 95 % significance
    contour is drawn in black (falls back to white-noise background if AR(1)
    estimation fails).  Below each wavelet panel a short temperature time series
    for that depth level shares the x-axis.

    Parameters
    ----------
    da_temp:
        Gridded temperature DataArray with dimensions ``(pressure, time)``.
    dt_seconds:
        Sample interval in seconds.
    wavelet:
        ``"morlet"`` (default, Morlet omega_0=6) or ``"mexican_hat"``.
    width_in : float, optional
        Figure width (inches), the report slot; default the full content width.
    levels : sequence of int, optional
        Pressure-level indices to draw (at most three are used).

    Returns
    -------
    plt.Figure or None
        Figure, or None if the dataset has insufficient temperature data.

    """
    try:
        import pycwt as _pycwt  # noqa: F401
    except ImportError:
        return None

    import matplotlib.pyplot as plt

    from oceanarray.analysis.spectral import compute_cwt
    from oceanarray.plotters.spectrum import wavelet_panel

    if da_temp.dims[0] != "pressure":
        da_temp = da_temp.transpose("pressure", ...)
    arr = da_temp.values
    press_vals = (
        da_temp.coords["pressure"].values.astype(float)
        if "pressure" in da_temp.coords
        else np.arange(arr.shape[0], dtype=float)
    )
    _n_time = arr.shape[1]

    from oceanarray.analysis.spectral import select_levels

    sel = list(levels) if levels is not None else select_levels(arr, press_vals, n=3)
    sel = sel[:3]
    if not sel:
        return None
    min_period = max(6.0 * dt_seconds, 7200.0) / 86400.0

    # Time axis -- use coordinate if present, else integer index
    if "time" in da_temp.coords:
        times = da_temp.coords["time"].values
    else:
        times = np.arange(_n_time)

    # --- compute CWT for each selected level ---
    results = []
    for k in sel:
        pv = press_vals[k]
        cwt_out = compute_cwt(arr[k, :], dt_seconds, wavelet=wavelet)
        results.append((pv, cwt_out))

    # --- figure: short T time series above each tall wavelet panel ---
    # The time series sits above so the high-frequency variability visible
    # by eye can be compared directly to the top (short-period) rows of the
    # scalogram below it.
    from matplotlib.gridspec import GridSpec

    n_panels = len(results)
    # height ratios: 1 part time series (top), 2.75 parts wavelet (bottom), per
    # level.  The scalogram was too tall relative to its width, so it is trimmed
    # ~17 % (3 -> 2.75 of the ratio) and the whole pair shortened (4.5 -> 3.8 in),
    # which also takes the time series down ~10 %.
    hr = [1, 2.75] * n_panels
    # constrained_layout crops the surrounding whitespace and places the spanning
    # colorbar cleanly (the encoder skips tight_layout for constrained figures).
    fig = plt.figure(figsize=(width_in, 3.8 * n_panels), layout="constrained")
    gs = GridSpec(2 * n_panels, 1, figure=fig, height_ratios=hr)

    tax: list = []  # time series axes (top of each pair)
    wax: list = []  # wavelet axes (bottom of each pair)
    for i in range(n_panels):
        _sharex = tax[0] if tax else None
        tax.append(fig.add_subplot(gs[2 * i, 0], sharex=_sharex))
        wax.append(fig.add_subplot(gs[2 * i + 1, 0], sharex=tax[-1]))

    mappable = None
    for i, (pv, cwt_out) in enumerate(results):
        # Temperature time series (top)
        ts = arr[sel[i], :]
        tax[i].plot(times, ts, lw=0.6, color="0.3")
        tax[i].set_ylabel(f"T ({unit_label('degree_Celsius')})")
        grid_despine(tax[i])
        # Pressure level in the bottom-left corner (was a title, which overlapped
        # the scalogram of the pair above).
        tax[i].text(
            0.01,
            0.08,
            f"{pv:.0f} dbar",
            transform=tax[i].transAxes,
            ha="left",
            va="bottom",
            fontsize=report_tokens.ANNOT_FS,
            bbox={
                "boxstyle": "round,pad=0.15",
                "fc": "white",
                "ec": "none",
                "alpha": 0.7,
            },
        )
        plt.setp(tax[i].get_xticklabels(), visible=False)

        # Wavelet scalogram (bottom)
        mappable = wavelet_panel(
            wax[i],
            times,
            cwt_out["periods"],
            cwt_out["power"],
            cwt_out["effective_coi"],
            signif=cwt_out["signif"],
            gap_mask=cwt_out["gap_mask"],
            min_period=min_period,
        )
        if i < n_panels - 1:
            plt.setp(wax[i].get_xticklabels(), visible=False)
        else:
            wax[i].set_xlabel("Time")

    if mappable is not None:
        # Pass all axes (wavelet + time series) so matplotlib shrinks them
        # all equally, keeping each wavelet panel aligned with its T panel.
        cbar = fig.colorbar(mappable, ax=wax + tax, fraction=0.02, pad=0.04)
        cbar.set_label(r"$\log_{10}$(power) (°C² d)")

    return fig


def draw_grid_rotary_spectrum(
    ds: xr.Dataset,
    lat: float = 0.0,
    *,
    width_in: float = report_tokens.W_FULL,
    levels: Sequence[int] | None = None,
    markers: Sequence[tuple[str, float]] = (),
) -> plt.Figure | None:
    """Two-panel rotary velocity spectrum for the grid report; return a Figure.

    Left panel: CW (solid, reds) and CCW (dashed, blues) power spectra on the same axes,
    one line per selected pressure level.  Right panel: rotary coefficient
    r = (CCW - CW) / (CCW + CW) on a linear [-1, 1] scale.
    Welch PSD with Hann window, 14-day segments (shortened on a short record,
    :func:`oceanarray.analysis.spectral.welch_segment`), 50% overlap.  Vertical
    lines mark M2, K1, the inertial period and any *markers*.

    Levels: *levels*, else three from those with both components for at least
    :data:`~oceanarray.analysis.spectral.SPECTRAL_MIN_FILLED` of the record
    (:func:`oceanarray.analysis.spectral.select_levels`).

    Parameters
    ----------
    ds : xr.Dataset
        Gridded dataset with ``east_velocity`` and ``north_velocity`` on
        ``(time, pressure)`` dimensions.
    lat : float
        Mooring latitude (degrees, positive north) used for the inertial period marker.
    width_in : float, optional
        Figure width (inches), the report slot; default the full content width.
    levels : sequence of int, optional
        Pressure-level indices to draw (the page's choice).
    markers : sequence of (str, float), optional
        Extra ``(label, period in days)`` markers.

    Returns
    -------
    plt.Figure or None
        Figure, or None if insufficient velocity data.

    """
    import matplotlib.colors as mcolors
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.lines import Line2D
    from matplotlib.transforms import blended_transform_factory

    if "east_velocity" not in ds.data_vars or "north_velocity" not in ds.data_vars:
        return None

    # Grid dims are (time, pressure) -- transpose to (pressure, time) for indexing
    east_da = ds["east_velocity"]
    north_da = ds["north_velocity"]
    if east_da.dims[0] != "pressure":
        east_da = east_da.transpose("pressure", "time")
        north_da = north_da.transpose("pressure", "time")

    # A grid holds no flags: the grid's rule was applied when it was made.
    arr_u = east_da.values.copy()  # (pressure, time)
    arr_v = north_da.values.copy()

    press_vals = ds.coords["pressure"].values.astype(float)
    n_lev, n_time = arr_u.shape
    from oceanarray.pipeline.dataset import time_step_seconds

    dt_s = time_step_seconds(ds)
    if not np.isfinite(dt_s) or dt_s <= 0:
        return None
    dt_days = dt_s / 86400.0

    from oceanarray.analysis.spectral import select_levels, welch_segment

    segment_length = welch_segment(n_time, dt_s, 14.0)
    fs = 1.0 / dt_days
    noverlap = int(0.5 * segment_length)

    if levels is None:
        both = np.where(np.isfinite(arr_u) & np.isfinite(arr_v), arr_u, np.nan)
        levels = select_levels(both, press_vals, n=3)
    valid_lev_idx = [
        k
        for k in levels
        if np.sum(np.isfinite(arr_u[k, :])) >= segment_length
        and np.sum(np.isfinite(arr_v[k, :])) >= segment_length
    ]
    if not valid_lev_idx:
        return None

    freq_out: np.ndarray | None = None
    s_cw_list: list[np.ndarray] = []
    s_ccw_list: list[np.ndarray] = []
    r_list: list[np.ndarray] = []
    press_plotted: list[float] = []

    for k in valid_lev_idx:
        u_col = arr_u[k, :].copy()
        v_col = arr_v[k, :].copy()
        # Gap-fill by linear interpolation before Welch
        for col in (u_col, v_col):
            good = np.isfinite(col)
            if good.sum() < 2:
                break
            col[:] = np.interp(np.arange(n_time), np.where(good)[0], col[good])
        else:
            f_uu, s_cw, s_ccw, r = gonella_rotary_spectrum(
                u_col, v_col, fs, segment_length, noverlap
            )
            if freq_out is None:
                freq_out = f_uu
            s_cw_list.append(s_cw)
            s_ccw_list.append(s_ccw)
            r_list.append(r)
            press_plotted.append(float(press_vals[k]))

    if freq_out is None or not s_cw_list:
        return None

    nyq_period = 2.0 * dt_days
    x_min = nyq_period
    x_max = min(30.0, n_time * dt_days / 2.0)
    fmask = (freq_out > 0) & (freq_out <= 1.0 / nyq_period)
    period_plot = 1.0 / freq_out[fmask]
    freq_pm = freq_out[fmask]

    # Effective DOF for the Welch estimate (Hann window, 50 % overlap ~= 1.5x segments)
    # Used for the 95 % significance threshold on the rotary coefficient.
    n_segments = max(1.0, (n_time - noverlap) / (segment_length - noverlap))
    K_eff = max(2.0, 1.5 * n_segments)
    from scipy.stats import f as _f_dist

    _dof_half = max(2, int(round(K_eff)))
    _F_crit = _f_dist.ppf(0.975, _dof_half, _dof_half)
    r_sig_95 = (_F_crit - 1.0) / (_F_crit + 1.0)

    # Log-band average of the rotary coefficient -- average S_CW and S_CCW
    # within each band first, then compute r, to avoid ratio noise.
    n_bands = max(12, min(25, len(period_plot) // 5))
    _log_edges = np.linspace(
        np.log10(period_plot.min()), np.log10(period_plot.max()), n_bands + 1
    )
    p_band_centers = 10 ** (0.5 * (_log_edges[:-1] + _log_edges[1:]))
    r_banded_list: list[np.ndarray] = []
    for _scw, _sccw in zip(s_cw_list, s_ccw_list, strict=False):
        _scw_f, _sccw_f = _scw[fmask], _sccw[fmask]
        _rb = np.full(n_bands, np.nan)
        for _i in range(n_bands):
            # band edges in frequency: small period -> high freq
            _f_lo = 1.0 / 10 ** _log_edges[_i]
            _f_hi = 1.0 / 10 ** _log_edges[_i + 1]
            _f_lo, _f_hi = min(_f_lo, _f_hi), max(_f_lo, _f_hi)
            _mb = (freq_pm >= _f_lo) & (freq_pm <= _f_hi)
            if _mb.sum() > 0:
                _cw_m = _scw_f[_mb].mean()
                _ccw_m = _sccw_f[_mb].mean()
                _d = _cw_m + _ccw_m
                if _d > 0:
                    _rb[_i] = (_ccw_m - _cw_m) / _d
        r_banded_list.append(_rb)

    markers = spectrum_markers(lat if lat else None, markers)

    p_arr = np.array(press_plotted)
    p_min, p_max = p_arr.min(), p_arr.max()
    if p_min == p_max:
        p_min -= 1.0
        p_max += 1.0
    # Floor the colour scale part-way up the colormap so even the shallowest kept
    # level is a visible shade (the pure-white end is invisible on white).
    _span = p_max - p_min
    norm_p = mcolors.Normalize(vmin=p_min - 0.54 * _span, vmax=p_max)
    cmap_cw = plt.get_cmap("Reds")
    cmap_ccw = plt.get_cmap("Blues")

    # Square panels via the shared helper; bottom pad for the rotated ticks.
    fig, _axes, _ = square_axes_grid(width_in, 1, 2, colorbar=False, bottom_pad_in=0.5)
    ax_spec, ax_rot = _axes[0, 0], _axes[0, 1]

    # Panel 1: CW (solid, reds) + CCW (dashed, blues)
    for s_cw, s_ccw, p in zip(s_cw_list, s_ccw_list, press_plotted, strict=False):
        cw_col = cmap_cw(norm_p(p))
        ccw_col = cmap_ccw(norm_p(p))
        ax_spec.loglog(period_plot, s_cw[fmask], color=cw_col, lw=1.0, alpha=0.85)
        ax_spec.loglog(
            period_plot, s_ccw[fmask], color=ccw_col, lw=1.0, alpha=0.85, ls="--"
        )

    trans1 = blended_transform_factory(ax_spec.transData, ax_spec.transAxes)
    for label, period_d, color in markers:
        if x_min <= period_d <= x_max:
            _mark_frequency_line(ax_spec, period_d, color)
            ax_spec.text(
                period_d,
                0.97,
                label,
                rotation=90,
                va="top",
                ha="center",
                color=color,
                transform=trans1,
                bbox={
                    "boxstyle": "round,pad=0.1",
                    "fc": "white",
                    "ec": "none",
                    "alpha": 0.6,
                },
            )
    ax_spec.set_xlim(x_max, x_min)
    _rot_tv, _rot_tl = period_axis_ticks(x_min, x_max)
    from matplotlib.ticker import NullLocator as _NL

    ax_spec.set_xticks(_rot_tv)
    ax_spec.set_xticklabels(_rot_tl, rotation=45, ha="right")
    ax_spec.xaxis.set_minor_locator(_NL())
    ax_spec.set_xlabel("Period")
    ax_spec.set_ylabel("PSD (m² s⁻² cpd⁻¹)")
    plot_title(ax_spec, "Rotary spectra")
    # Depth legend replaces the pressure colorbar (frees horizontal space so the
    # panels can be square): one blue (CCW-shade) swatch per level, plus the
    # CW/CCW line-style key.  Colour intensity = depth; red family = CW, blue = CCW.
    _style_handles = [
        Line2D([0], [0], color="#c0392b", lw=1.5, label="CW (solid, red)"),
        Line2D([0], [0], color="#2c7fb8", lw=1.5, ls="--", label="CCW (dashed, blue)"),
    ]
    _depth_handles = [
        Line2D([0], [0], color=cmap_ccw(norm_p(p)), lw=3.0, label=f"{p:.0f} dbar")
        for p in press_plotted
    ]
    ax_spec.legend(
        handles=_style_handles + _depth_handles,
        loc="lower left",
        fontsize=report_tokens.ANNOT_FS,
        framealpha=0.85,
    )

    # Panel 2: Rotary coefficient r -- raw (thin) + band-averaged (thick) + significance
    for r, r_banded, p in zip(r_list, r_banded_list, press_plotted, strict=False):
        col = cmap_ccw(norm_p(p))
        # Raw per-bin r: thin, semi-transparent
        ax_rot.semilogx(period_plot, r[fmask], color=col, lw=0.5, alpha=0.25)
        # Log-band averaged r: thick main line
        valid_b = np.isfinite(r_banded)
        if valid_b.any():
            ax_rot.semilogx(
                p_band_centers[valid_b],
                r_banded[valid_b],
                color=col,
                lw=2.0,
                alpha=0.9,
                marker="o",
                ms=3,
            )

    # 95 % significance band (grey shading + dashed lines)
    ax_rot.axhspan(-r_sig_95, r_sig_95, color="#7f8c8d", alpha=0.08, zorder=0)
    ax_rot.axhline(r_sig_95, color="#7f8c8d", lw=1.0, ls="--", alpha=0.7)
    ax_rot.axhline(-r_sig_95, color="#7f8c8d", lw=1.0, ls="--", alpha=0.7)

    trans2 = blended_transform_factory(ax_rot.transData, ax_rot.transAxes)
    for label, period_d, color in markers:
        if x_min <= period_d <= x_max:
            _mark_frequency_line(ax_rot, period_d, color)
            ax_rot.text(
                period_d,
                0.03,
                label,
                rotation=90,
                va="bottom",
                ha="center",
                color=color,
                transform=trans2,
                bbox={
                    "boxstyle": "round,pad=0.1",
                    "fc": "white",
                    "ec": "none",
                    "alpha": 0.6,
                },
            )
    ax_rot.axhline(0, color="k", lw=0.8, ls="-", alpha=0.4)
    ax_rot.set_xlim(x_max, x_min)
    ax_rot.set_xticks(_rot_tv)
    ax_rot.set_xticklabels(_rot_tl, rotation=45, ha="right")
    ax_rot.xaxis.set_minor_locator(_NL())
    ax_rot.set_ylim(-1.1, 1.1)
    ax_rot.set_xlabel("Period")
    ax_rot.set_ylabel("Rotary coefficient r")
    plot_title(ax_rot, f"r = (CCW - CW) / (CCW + CW)  [DOF ~= {2 * _dof_half}]")
    ax_rot.text(
        0.02,
        0.97,
        "CCW dominant (r > 0)",
        transform=ax_rot.transAxes,
        va="top",
        ha="left",
        color="steelblue",
    )
    ax_rot.text(
        0.02,
        0.05,
        "CW dominant (r < 0)",
        transform=ax_rot.transAxes,
        va="bottom",
        ha="left",
        color="#c0392b",
    )
    ax_rot.legend(
        handles=[
            Line2D(
                [0],
                [0],
                color="#7f8c8d",
                lw=1.0,
                ls="--",
                label=f"95 % sig. (|r| > {r_sig_95:.2f})",
            )
        ],
        loc="upper right",
        fontsize=report_tokens.ANNOT_FS,
        framealpha=0.7,
    )
    # Depth is conveyed by the panel-1 legend; no pressure colorbar here (keeps
    # the panel square and matches the "legend not colorbar" convention).

    return fig
