"""Tier-2 domain wrappers for spectral diagnostics.

spectrum.py contains:
  - ``wavelet_panel``: Tier-1 primitive for rendering a single wavelet scalogram.
  - ``draw_spectrum``: Two-panel Welch PSD of gridded temperature (migrated from
    ``report/_plots.py``).
  - ``draw_wavelet``: Continuous wavelet transform scalogram for gridded temperature
    (migrated from ``report/_plots.py``).
  - ``draw_grid_rotary_spectrum``: Two-panel rotary velocity spectrum (migrated from
    ``report/_plots.py``).

Pairs with :mod:`oceanarray.analysis.spectral` for spectral computations.

Post-OdB remaining migrations from report/_plots.py:
  plot_grid_fig (was _make_grid_fig_b64), plot_grid_n2 (was _make_grid_n2_b64).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np

from oceanarray.reports._vendored import design_tokens as report_tokens
from oceanarray.utilities import _nice_colorbar_bounds, period_axis_ticks

from .. import parameters as params
from ..analysis.spectral import gonella_rotary_spectrum
from ..config.unit_tables import unit_label, warn_if_not_canonical
from .helpers import grid_despine
from .primitives import figure_title, plot_title, square_axes_grid


def _mark_frequency_line(ax: plt.Axes, period: float, color: str) -> None:
    """Draw a marked-frequency reference line (tidal/inertial) on a spectrum.

    Uniform style across the temperature and rotary spectra: a thin dotted
    vertical line (``pen("thinner")``), so both figures mark frequencies the
    same way.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
        Spectrum axes (period on the x-axis).
    period : float
        Period at which to draw the line (same x-units as the axes).
    color : str
        Line colour.

    """
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

    Returns
    -------
    matplotlib.cm.ScalarMappable
        Mappable suitable for passing to ``fig.colorbar()``.

    """
    n_scales, n_time = power.shape
    log_power = np.log10(np.where(power > 0, power, np.nan))

    vmin = float(np.nanpercentile(log_power, 2))
    vmax = float(np.nanpercentile(log_power, 98))
    bounds = _nice_colorbar_bounds(vmin, vmax, n=20)
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


def draw_spectrum(
    da_temp: xr.DataArray,
    dt_seconds: float,
    lat: float = 0.0,
    hf_segment_days: float = 1.0,
    hf_x_max_days: float = 3.0,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Two-panel Welch PSD of gridded temperature, one line per depth level.

    Left panel: low-frequency overview using 14-day Hann windows.
    Right panel: high-frequency zoom using ``hf_segment_days`` Hann windows,
    giving more (shorter) windows and a smoother estimate at tidal and inertial
    frequencies.

    Parameters
    ----------
    da_temp:
        Gridded temperature DataArray with dimensions (pressure, time).
    dt_seconds:
        Uniform time step of the grid in seconds.
    lat:
        Mooring latitude in decimal degrees; used to compute the inertial
        frequency marker.  Pass 0 to omit.
    hf_segment_days:
        Window length for the HF panel in days.  Default 2 days (~180 windows
        per year, ~7x more than the LF panel).  Reduce to focus on higher
        frequencies, e.g. ``hf_segment_days=1/24`` for 1-hour windows on
        sub-hourly data.
    hf_x_max_days:
        Upper x-axis limit (longest period shown) for the HF panel in days.
        When <= 3 the HF x-axis is displayed in hours; otherwise in days.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Notes
    -----
    The LF panel uses gap-filled interpolation before calling ``welch_psd``
    (current behaviour).  The HF panel uses ``welch_psd_gapaware``, which
    operates only on contiguous finite runs and skips any window that straddles
    a gap -- avoiding the low-pass bias that linear interpolation introduces at
    high frequencies.

    To switch the LF panel to gap-aware as well, replace the two lines in the
    computation loop that read::

        col_filled = col.copy()
        if not good.all():
            col_filled = np.interp(...)
        f, p = welch_psd(col_filled, dt_days, segment_length_lf)

    with the single line::

        f, p, _ = welch_psd_gapaware(col, dt_days, segment_length_lf)

    """
    import matplotlib.colors as mcolors
    import matplotlib.pyplot as plt
    from matplotlib.transforms import blended_transform_factory

    from ..tools import welch_psd, welch_psd_gapaware

    # The PSD axis is labelled °C² cpd⁻¹; the variance is in the temperature's unit².
    warn_if_not_canonical(
        da_temp, "degree_Celsius", quantity="temperature PSD (°C² cpd⁻¹)"
    )

    if da_temp.dims[0] != "pressure":
        da_temp = da_temp.transpose("pressure", ...)
    arr = da_temp.values
    press_vals = (
        da_temp.coords["pressure"].values.astype(float)
        if "pressure" in da_temp.coords
        else np.arange(arr.shape[0], dtype=float)
    )

    n_lev, n_time = arr.shape
    dt_days = dt_seconds / 86400.0

    # LF: 14-day Hann windows
    seg_lf = max(128, int(14.0 / dt_days))
    segment_length_lf = min(seg_lf, max(n_time // 4, 128))

    # HF: hf_segment_days Hann windows (caller controls this)
    seg_hf = max(8, int(hf_segment_days / dt_days))
    segment_length_hf = min(seg_hf, max(n_time // 4, 8))

    # Level selection: pick levels near multiples of 100 dbar that have enough
    # finite samples for at least one LF Welch window.  For each 100-dbar target
    # the nearest valid level is used; targets with no level within 75 dbar are
    # skipped.  Levels are kept in ascending pressure order so the colormap
    # (light = shallow, dark = deep) matches the legend from top to bottom.
    _candidates = [
        (k, press_vals[k])
        for k in range(n_lev)
        if np.sum(np.isfinite(arr[k, :])) >= segment_length_lf
    ]
    if _candidates:
        _cp = np.array([pv for _, pv in _candidates])
        _lo100 = int(np.ceil(_cp.min() / 100.0)) * 100
        _hi100 = int(np.floor(_cp.max() / 100.0)) * 100
        _seen_k: set = set()
        valid_lev_idx = []
        for _tgt in np.arange(_lo100, _hi100 + 1, 100.0):
            _bi = int(np.argmin(np.abs(_cp - _tgt)))
            _k = _candidates[_bi][0]
            if _k not in _seen_k and np.abs(_cp[_bi] - _tgt) <= 75.0:
                _seen_k.add(_k)
                valid_lev_idx.append(_k)
    else:
        valid_lev_idx = []

    freq_lf = freq_hf = None
    psds_lf: list[np.ndarray] = []
    press_plotted_lf: list[float] = []
    psds_hf: list[np.ndarray] = []
    press_plotted_hf: list[float] = []
    hf_total_wins = 0
    for k in valid_lev_idx:
        col = arr[k, :].copy()
        good = np.isfinite(col)

        # LF: gap-fill then Welch.  To switch to gap-aware, replace these two
        # lines with: f, p, _ = welch_psd_gapaware(col, dt_days, segment_length_lf)
        col_filled = col.copy()
        if not good.all():
            col_filled = np.interp(np.arange(n_time), np.where(good)[0], col[good])
        f, p = welch_psd(col_filled, dt_days, segment_length_lf)
        if freq_lf is None:
            freq_lf = f
        psds_lf.append(p)
        press_plotted_lf.append(press_vals[k])

        # HF: gap-aware -- only windows within contiguous finite runs
        f_hf, p_hf, n_w = welch_psd_gapaware(col, dt_days, segment_length_hf)
        if f_hf is not None:
            if freq_hf is None:
                freq_hf = f_hf
            psds_hf.append(p_hf)
            press_plotted_hf.append(press_vals[k])
            hf_total_wins += n_w
        # else: no contiguous run long enough for an HF window at this level -- skip silently

    if freq_lf is None or not psds_lf:
        return None

    if not psds_hf:
        print(
            f"  NOTE: temperature spectrum HF -- no valid windows at any level "
            f"(segment={segment_length_hf} samples = {hf_segment_days:.3g} d); "
            f"HF panel will be blank"
        )

    # Window counts for subplot titles
    n_win_lf = max(1, 2 * n_time // segment_length_lf - 1)
    # HF: actual windows used (gap-aware), averaged across contributing levels
    n_win_hf = hf_total_wins // max(1, len(psds_hf)) if psds_hf else 0

    # LF markers (periods in days)
    lf_markers = [
        ("M2", 1.0 / 1.9323, "#c0392b"),
        ("K1", 23.9345 / 24.0, "#e67e22"),
        ("1.8d", 1.8, "#7f8c8d"),
        ("4d", 4.0, "#95a5a6"),
    ]
    # HF markers -- tuples are (label, period_days, color, y_axes).
    # M2 label is raised (y=0.15) so it clears the f label (y=0.03) at high
    # latitudes where the inertial period approaches the M2 semidiurnal period.
    hf_markers = [
        ("M2", 1.0 / 1.9323, "#c0392b", 0.15),
        ("9min", 9.0 / 1440.0, "#7f8c8d", 0.03),
    ]
    if lat != 0.0:
        import gsw as _gsw

        f_inert_cpd = abs(_gsw.f(lat)) * 86400.0 / (2.0 * np.pi)
        inert_period_d = 1.0 / f_inert_cpd
        lf_markers.append(("f", inert_period_d, "#27ae60"))
        hf_markers.append(("f", inert_period_d, "#27ae60", 0.03))

    p_arr = np.array(press_plotted_lf)
    p_min, p_max = p_arr.min(), p_arr.max()
    if p_min == p_max:
        p_min -= 1.0
        p_max += 1.0
    # Blues (not reversed): shallow -> light blue, deep -> dark blue.
    # Clip the lightest 25 % so shallow levels are never near-white.
    # Adjust the 0.25 start value to taste: higher = all lines darker.
    _blues = plt.get_cmap("Blues")
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "Blues_dark", _blues(np.linspace(0.25, 1.0, 256))
    )
    norm = mcolors.Normalize(vmin=p_min, vmax=p_max)

    nyq_period = 2.0 * dt_days

    # HF x-axis: switch to hours when the range is <= 3 days (tidal focus)
    use_hours = hf_x_max_days <= 3.0
    hf_scale = 24.0 if use_hours else 1.0
    hf_unit = "hours" if use_hours else "days"
    hf_seg_hours = hf_segment_days * 24.0
    hf_seg_label = (
        f"{int(hf_seg_hours)}-hour"
        if hf_seg_hours == int(hf_seg_hours)
        else f"{hf_seg_hours:.3g}-hour"
    )

    from matplotlib.ticker import NullLocator

    # Square panels via the shared helper; bottom pad for the rotated HF ticks,
    # top pad for the figure suptitle above the per-panel titles.
    fig, _axes, _ = square_axes_grid(
        width_in, 1, 2, colorbar=False, bottom_pad_in=0.5, top_pad_in=0.6
    )
    ax_lf, ax_hf = _axes[0, 0], _axes[0, 1]

    x_min_lf = max(nyq_period, 10.0 / 1440.0)  # right edge: Nyquist or 10 min
    # Left edge = longest period Welch can estimate = 1/min_freq = window length
    x_max_lf = 1.0 / float(freq_lf[freq_lf > 0].min())
    x_min_hf = nyq_period
    x_max_hf = min(hf_x_max_days, hf_segment_days)

    fmask_lf = (freq_lf > 0) & (freq_lf <= 1.0 / nyq_period)
    freq_plot_lf = freq_lf[fmask_lf]
    period_plot_lf = 1.0 / freq_plot_lf

    # -- LF panel --
    for psd, pv in zip(psds_lf, press_plotted_lf, strict=False):
        ax_lf.loglog(
            period_plot_lf,
            psd[fmask_lf],
            color=cmap(norm(pv)),
            lw=0.8,
            alpha=0.75,
            label=f"{pv:.0f} dbar",
        )

    idx_1d = np.argmin(np.abs(freq_plot_lf - 1.0))
    median_at_1d = float(np.median([psd[fmask_lf][idx_1d] for psd in psds_lf]))
    if np.isfinite(median_at_1d) and median_at_1d > 0:
        ref_p = np.array([x_min_lf, x_max_lf])
        ax_lf.loglog(
            ref_p,
            median_at_1d * ref_p**2,
            color="k",
            lw=0.9,
            ls="--",
            alpha=0.35,
            label="-2 slope",
        )

    ax_lf.set_xlim(x_max_lf, x_min_lf)
    ax_lf.set_ylim(1e-6, 1e2)
    _lf_tv, _lf_tl = period_axis_ticks(x_min_lf, x_max_lf)
    ax_lf.set_xticks(_lf_tv)
    ax_lf.set_xticklabels(_lf_tl)
    ax_lf.xaxis.set_minor_locator(NullLocator())
    trans_lf = blended_transform_factory(ax_lf.transData, ax_lf.transAxes)
    for lbl, pd_d, clr in lf_markers:
        if x_min_lf <= pd_d <= x_max_lf:
            _mark_frequency_line(ax_lf, pd_d, clr)
            ax_lf.text(
                pd_d,
                0.03,
                lbl,
                rotation=90,
                va="bottom",
                ha="center",
                color=clr,
                transform=trans_lf,
                bbox={
                    "boxstyle": "round,pad=0.1",
                    "fc": "white",
                    "ec": "none",
                    "alpha": 0.6,
                },
            )

    ax_lf.set_xlabel("Period")
    ax_lf.set_ylabel("PSD (°C² cpd⁻¹)")
    plot_title(ax_lf, f"Low frequency\n14-day windows ({n_win_lf})")
    # Single shared legend -- depth labels from LF lines serve both panels
    ax_lf.legend(loc="upper right", title="Depth")

    # -- HF panel --
    if psds_hf and freq_hf is not None:
        fmask_hf = (freq_hf > 0) & (freq_hf <= 1.0 / nyq_period)
        freq_plot_hf = freq_hf[fmask_hf]
        period_plot_hf = 1.0 / freq_plot_hf

        for psd, pv in zip(psds_hf, press_plotted_hf, strict=False):
            ax_hf.loglog(
                period_plot_hf * hf_scale,
                psd[fmask_hf],
                color=cmap(norm(pv)),
                lw=0.8,
                alpha=0.75,
            )

        # -2 slope anchored at geometric midpoint of the HF x range
        _log_mid = np.sqrt(x_min_hf * x_max_hf)
        _idx_mid = np.argmin(np.abs(period_plot_hf - _log_mid))
        _med_mid = float(np.median([psd[fmask_hf][_idx_mid] for psd in psds_hf]))
        if np.isfinite(_med_mid) and _med_mid > 0:
            ref_p_hf = np.array([x_min_hf, x_max_hf])
            ax_hf.loglog(
                ref_p_hf * hf_scale,
                _med_mid * (ref_p_hf / _log_mid) ** 2,
                color="k",
                lw=0.9,
                ls="--",
                alpha=0.35,
                label="-2 slope",
            )

        trans_hf = blended_transform_factory(ax_hf.transData, ax_hf.transAxes)
        for lbl, pd_d, clr, y_lbl in hf_markers:
            pd_scaled = pd_d * hf_scale
            lo, hi = x_min_hf * hf_scale, x_max_hf * hf_scale
            if lo <= pd_scaled <= hi:
                _mark_frequency_line(ax_hf, pd_scaled, clr)
                ax_hf.text(
                    pd_scaled,
                    y_lbl,
                    lbl,
                    rotation=90,
                    va="bottom",
                    ha="center",
                    color=clr,
                    transform=trans_hf,
                    bbox={
                        "boxstyle": "round,pad=0.1",
                        "fc": "white",
                        "ec": "none",
                        "alpha": 0.6,
                    },
                )

    else:
        ax_hf.text(
            0.5,
            0.5,
            "No valid windows\n(all data gapped)",
            transform=ax_hf.transAxes,
            ha="center",
            va="center",
            color="gray",
        )

    ax_hf.set_xlim(x_max_hf * hf_scale, x_min_hf * hf_scale)
    ax_hf.set_ylim(1e-8, 1e2)
    _hf_ticks_h = [  # (period_hours, label) -- edit here to adjust tick positions
        (48.0, "2d"),
        (24.0, "1d"),
        (12.0, "12h"),
        (6.0, "6h"),
        (2.0, "2h"),
        (1.0, "1h"),
        (0.5, "30min"),
        (10.0 / 60, "10min"),
        (5.0 / 60, "5min"),
        (2.0 / 60, "2min"),
    ]
    _lo_hf, _hi_hf = x_min_hf * hf_scale, x_max_hf * hf_scale
    _hf_t = [
        (ph / 24.0 * hf_scale, lbl)
        for ph, lbl in _hf_ticks_h
        if _lo_hf <= ph / 24.0 * hf_scale <= _hi_hf
    ]
    ax_hf.set_xticks([v for v, _ in _hf_t])
    ax_hf.set_xticklabels([lbl for _, lbl in _hf_t], rotation=45, ha="right")
    ax_hf.xaxis.set_minor_locator(NullLocator())
    ax_hf.set_xlabel(f"Period ({hf_unit})")
    ax_hf.set_ylabel("PSD (°C² cpd⁻¹)")
    n_win_hf_label = str(n_win_hf) if psds_hf else "0"
    # Two-line panel titles: heading + window detail, so the detail fits the
    # narrow square panels without overflowing.
    plot_title(
        ax_hf,
        f"High frequency\n{hf_seg_label} windows ({n_win_hf_label}, gap-aware)",
    )

    figure_title(fig, "Temperature power spectrum — Welch PSD per depth")

    return fig


def draw_wavelet(
    da_temp: xr.DataArray,
    dt_seconds: float,
    wavelet: str = "morlet",
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Continuous wavelet transform scalogram for gridded temperature; return a Figure.

    Produces three stacked wavelet + time-series panel pairs.  Depth levels are
    selected from 100-dbar multiples that have **at least 75 % data coverage**
    (sparse near-bottom levels are excluded).  Levels within 100 dbar of the
    shallowest valid level are also excluded to avoid gappy near-surface data.
    From the remaining candidates the deepest, an upper-middle, and a
    lower-middle level are chosen (biased toward the deeper water column).

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
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

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

    from oceanarray.plotters.spectrum import wavelet_panel
    from oceanarray.tools import compute_cwt

    if da_temp.dims[0] != "pressure":
        da_temp = da_temp.transpose("pressure", ...)
    arr = da_temp.values
    press_vals = (
        da_temp.coords["pressure"].values.astype(float)
        if "pressure" in da_temp.coords
        else np.arange(arr.shape[0], dtype=float)
    )
    n_lev, _n_time = arr.shape

    # --- level selection: 100-dbar multiples with >= 75 % data coverage ---
    # The 75 % threshold is much stricter than "at least one window" so that
    # sparse levels (e.g. a deep instrument with only a few days of data) are
    # excluded.  Levels near the seabed often have patchy records; requiring
    # 75 % coverage means the wavelet estimate is based on most of the deployment.
    _min_coverage = int(0.75 * _n_time)
    _candidates = [
        (k, press_vals[k])
        for k in range(n_lev)
        if np.sum(np.isfinite(arr[k, :])) >= _min_coverage
    ]
    if not _candidates:
        return None

    _cp = np.array([pv for _, pv in _candidates])
    _lo100 = int(np.ceil(_cp.min() / 100.0)) * 100
    _hi100 = int(np.floor(_cp.max() / 100.0)) * 100
    _seen_k: set = set()
    _valid_idx: list = []
    for _tgt in np.arange(_lo100, _hi100 + 1, 100.0):
        _bi = int(np.argmin(np.abs(_cp - _tgt)))
        _k = _candidates[_bi][0]
        if _k not in _seen_k and np.abs(_cp[_bi] - _tgt) <= 75.0:
            _seen_k.add(_k)
            _valid_idx.append(_k)

    if not _valid_idx:
        return None

    # Exclude levels within 100 dbar of the shallowest valid level --
    # near-surface data tends to be gappy and the wavelet is unreliable there.
    _shallowest_p = press_vals[_valid_idx[0]]
    _deep_idx = [k for k in _valid_idx if press_vals[k] >= _shallowest_p + 100.0]

    if not _deep_idx:
        _deep_idx = _valid_idx  # fall back if nothing passes the filter

    # Pick 3 levels biased toward mid-to-deep water.
    # Start from max(0, N-5) so that when there are many levels we skip
    # the shallowest portion, e.g. [300..900] -> pick 500, 700, 900.
    n_v = len(_deep_idx)
    if n_v >= 3:
        _start = max(0, n_v - 5)
        _idxs = np.round(np.linspace(_start, n_v - 1, 3)).astype(int)
        sel = [_deep_idx[i] for i in _idxs]
    elif n_v == 2:
        sel = [_deep_idx[0], _deep_idx[1]]
    else:
        sel = [_deep_idx[0]]

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
) -> plt.Figure | None:
    """Two-panel rotary velocity spectrum for the grid report; return a Figure.

    Left panel: CW (solid, reds) and CCW (dashed, blues) power spectra on the same axes,
    one line per selected pressure level.  Right panel: rotary coefficient
    r = (CCW - CW) / (CCW + CW) on a linear [-1, 1] scale.
    Welch PSD with Hann window, 14-day segments, 50% overlap.

    Level selection: min(4, max(1, n_valid_levels // 5)) evenly-spaced levels
    from those with >= 5% finite data in both east and north velocity.

    Parameters
    ----------
    ds : xr.Dataset
        Gridded dataset with ``east_velocity`` and ``north_velocity`` on
        ``(time, pressure)`` dimensions.
    lat : float
        Mooring latitude (degrees, positive north) used for the inertial period marker.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

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

    arr_u = east_da.values.copy()  # (pressure, time)
    arr_v = north_da.values.copy()

    # Apply QC flags (QC_BAD_FLAGS -> NaN)
    for _qc_var, _arr in (
        ("east_velocity_qc", arr_u),
        ("north_velocity_qc", arr_v),
    ):
        if _qc_var in ds.data_vars:
            _qc = ds[_qc_var].values
            if _qc.ndim == arr_u.ndim:
                if ds[_qc_var].dims[0] != "pressure":
                    _qc = _qc.T
                _arr[np.isin(_qc, params.QC_BAD_FLAGS)] = np.nan

    press_vals = ds.coords["pressure"].values.astype(float)
    n_lev, n_time = arr_u.shape
    dt_s = float(ds.attrs.get("dt_seconds", 3600))
    dt_days = dt_s / 86400.0

    seg_14d = max(128, int(14.0 / dt_days))
    segment_length = min(seg_14d, max(n_time // 4, 128))
    fs = 1.0 / dt_days
    noverlap = int(0.5 * segment_length)

    # Valid levels: both u and v have >= segment_length finite values
    valid_lev_idx = [
        k
        for k in range(n_lev)
        if np.sum(np.isfinite(arr_u[k, :])) >= segment_length
        and np.sum(np.isfinite(arr_v[k, :])) >= segment_length
    ]
    if len(valid_lev_idx) < 2:
        return None

    # Subsample: min(4, max(1, n_valid // 5))
    n_select = min(4, max(1, len(valid_lev_idx) // 5))
    if len(valid_lev_idx) > n_select:
        sel = np.linspace(0, len(valid_lev_idx) - 1, n_select, dtype=int)
        valid_lev_idx = [valid_lev_idx[i] for i in sel]

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

    # Tidal/inertial frequency markers
    markers = [
        ("M2", 1.0 / 1.9323, "#c0392b"),
        ("K1", 23.93 / 24.0, "#e67e22"),
        ("1.8d", 1.8, "#7f8c8d"),
        ("4d", 4.0, "#95a5a6"),
    ]
    if lat != 0.0:
        import gsw as _gsw

        f_inert = abs(_gsw.f(lat))
        f_inert_cpd = f_inert * 86400.0 / (2.0 * np.pi)
        markers.append(("f", 1.0 / f_inert_cpd, "#27ae60"))

    # Show at most 3 depth levels: the shallowest maps to the palest colour and is
    # hard to see against white, so drop it and evenly subsample the rest.
    if len(press_plotted) > 3:
        _order = list(np.argsort(press_plotted))  # shallow -> deep
        _keep = _order[1:]  # drop the shallowest
        if len(_keep) > 3:
            _pick = np.linspace(0, len(_keep) - 1, 3).round().astype(int)
            _keep = [_keep[i] for i in _pick]
        _keep = sorted(_keep)
        s_cw_list = [s_cw_list[i] for i in _keep]
        s_ccw_list = [s_ccw_list[i] for i in _keep]
        r_list = [r_list[i] for i in _keep]
        r_banded_list = [r_banded_list[i] for i in _keep]
        press_plotted = [press_plotted[i] for i in _keep]

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
