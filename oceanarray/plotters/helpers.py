"""Shared helpers for the plotters package.

Provides colormap helpers and rose-diagram rendering used across multiple
Tier-2 plotter modules.

plotters/ never serialises to base64: the Tier-3 wrappers in reports/_plots.py
encode each figure through the shared report encoder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from oceanarray.config.unit_tables import unit_label

if TYPE_CHECKING:
    import matplotlib.pyplot as plt
    import xarray as xr


#: Okabe-Ito 8-colour qualitative palette — the accessibility-standard
#: colourblind-safe set (distinguishable under protan/deutan/tritan CVD).  Note
#: the yellow (#F0E442) is pale on a white background; the linewidth tiers in
#: :func:`distinct_line_styles` and the linestyle help keep it legible.
OKABE_ITO: list[str] = [
    "#000000",  # black
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#009E73",  # bluish green
    "#F0E442",  # yellow
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#CC79A7",  # reddish purple
]

#: One color per deployment on a concatenated page: Okabe-Ito without black (kept
#: for markers and text), in rainbow order — vermillion, orange, yellow, green,
#: sky blue, blue, purple — so time reads along the spectrum.
DEPLOYMENT_COLORS: list[str] = [
    "#D55E00",  # vermillion
    "#E69F00",  # orange
    "#F0E442",  # yellow
    "#009E73",  # bluish green
    "#56B4E9",  # sky blue
    "#0072B2",  # blue
    "#CC79A7",  # reddish purple
]

#: Line styles that pair with :data:`DEPLOYMENT_COLORS` in :func:`deployment_line`.
DEPLOYMENT_LINESTYLES: tuple[str, ...] = ("-", "--", ":", "-.")

#: A level's per-deployment statistic (median speed, mean direction) is drawn only
#: when the level has finite values for at least this fraction of the
#: deployment's time steps, so a level that only has data part of the time (an
#: instrument that stops, knockdown near the top) is not compared with full ones.
PROFILE_MIN_FRACTION: float = 0.5


def ruled(ds: xr.Dataset) -> bool:
    """Return whether *ds* records a ``mask_flags`` rule (a stack or Stack cat)."""
    from oceanarray.pipeline.masking import STACK_ATTR

    return STACK_ATTR in ds.attrs


def masked(ds: xr.Dataset, var: str) -> np.ndarray:
    """Return a float copy of ``ds[var]`` with the values a page does not show as NaN.

    A stack or Stack cat hides what its rule says (``stack_mask_flags``: ours or
    the originator's flag in the rule; see
    :func:`oceanarray.pipeline.masking.masked`); any other file (an instrument
    file, a grid) hides values whose ``<var>_qc`` is in
    ``parameters.QC_BAD_FLAGS``.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset a figure draws.
    var : str
        The data variable.

    Returns
    -------
    numpy.ndarray
        The values, NaN where hidden.
    """
    if ruled(ds):
        from oceanarray.pipeline import masking

        return masking.masked(ds, var)
    from oceanarray.config import parameters as params

    values = ds[var].values.astype(float)
    if f"{var}_qc" in ds.data_vars:
        values[np.isin(ds[f"{var}_qc"].values, params.QC_BAD_FLAGS)] = np.nan
    return values


def deployment_steps(ds: xr.Dataset, k: int) -> int:
    """Return the number of time steps of deployment *k* in a concatenated stack.

    Parameters
    ----------
    ds : xarray.Dataset
        A concatenated stack with ``deployment_index(time)``, or with
        ``cell_deployment(time, N_DEPTHS)`` only.
    k : int
        The deployment's index.

    Returns
    -------
    int
        Time steps assigned to the deployment (``deployment_index``); without it,
        the most steps any depth has for the deployment.
    """
    if "deployment_index" in ds and ds["deployment_index"].dims == ("time",):
        return int((ds["deployment_index"].values == k).sum())
    return int((ds["cell_deployment"].values == k).sum(axis=0).max())


def short_names(names: list[str]) -> list[str]:
    """Return deployment names without the site prefix they share (``FLMA_2014`` → ``2014``).

    Parameters
    ----------
    names : list of str
        Deployment names, e.g. ``["FLMB_2016", "FLMB_2017"]``.

    Returns
    -------
    list of str
        The names without the shared ``<site>_`` prefix, or *names* unchanged
        when they do not all share one.
    """
    prefix = names[0].rsplit("_", 1)[0] + "_" if names and "_" in names[0] else ""
    if prefix and all(n.startswith(prefix) for n in names):
        return [n.removeprefix(prefix) for n in names]
    return names


def legend_below(fig: plt.Figure, handles: list) -> None:
    """Put a deployment legend under the axes, outside them, in up to six columns.

    Parameters
    ----------
    fig : matplotlib.figure.Figure
        The figure; drawn with ``layout="constrained"`` so the legend gets room.
    handles : list of matplotlib.artist.Artist
        One handle per deployment, in time order.
    """
    fig.legend(
        handles=handles,
        loc="outside lower center",
        ncols=min(len(handles), 6),
        fontsize="small",
        frameon=False,
        columnspacing=1.0,
        handlelength=1.6,
    )


#: The thin dotted line marking each deployment's start on a time axis: a dot
#: every 8 line widths, so the dots stay apart at a thin width.
DEPLOYMENT_BOUNDARY: dict = {"color": "0.25", "lw": 0.6, "ls": (0, (1, 8))}


#: Markers that pair with :data:`DEPLOYMENT_COLORS` in :func:`deployment_marker`:
#: the two deployments sharing a color get a circle and a square; past fourteen
#: deployments the pairs repeat as triangle and diamond.
DEPLOYMENT_MARKERS: tuple[str, ...] = ("o", "s", "^", "D")

#: Hatches that pair with :data:`DEPLOYMENT_COLORS` in :func:`deployment_hatch`,
#: for filled shapes (boxes) where a marker does not apply.
DEPLOYMENT_HATCHES: tuple[str, ...] = ("", "////", "..", "xx")


def _paired(n: int | None) -> bool:
    """Return whether deployments share colors in pairs: more than seven, or *n* unknown."""
    return n is None or n > len(DEPLOYMENT_COLORS)


def _pair_style(k: int, n: int | None = None) -> int:
    """Return deployment *k*'s style index: 0 alone; 0 or 1 within a color pair, 2 or 3 past fourteen."""
    if not _paired(n):
        return 0
    m = len(DEPLOYMENT_COLORS)
    return (k % 2 + 2 * (k // (2 * m))) % 4


def deployment_color(k: int, n: int | None = None) -> str:
    """Return deployment *k*'s color, from early (red) to late (purple).

    Up to seven deployments (*n* ≤ 7) each get their own color, spread evenly
    along :data:`DEPLOYMENT_COLORS`. With more, or when *n* is not given, two
    consecutive deployments share a color, so the colors still change evenly from
    early to late; the two are told apart by :func:`deployment_line`,
    :func:`deployment_marker` or :func:`deployment_hatch`.

    Parameters
    ----------
    k : int
        The deployment's index, in time order.
    n : int, optional
        The number of deployments on the figure.

    Returns
    -------
    str
        A hex color.
    """
    m = len(DEPLOYMENT_COLORS)
    if not _paired(n):
        return DEPLOYMENT_COLORS[round(k * (m - 1) / (n - 1)) if n > 1 else 0]
    return DEPLOYMENT_COLORS[(k // 2) % m]


def deployment_marker(k: int, n: int | None = None) -> str:
    """Return deployment *k*'s marker: circle; within a color pair, circle then square.

    Parameters
    ----------
    k : int
        The deployment's index, in time order.
    n : int, optional
        The number of deployments on the figure (see :func:`deployment_color`).

    Returns
    -------
    str
        A matplotlib marker code from :data:`DEPLOYMENT_MARKERS`.
    """
    return DEPLOYMENT_MARKERS[_pair_style(k, n)]


def deployment_hatch(k: int, n: int | None = None) -> str:
    """Return deployment *k*'s hatch for a filled shape: none; within a color pair, none then diagonal.

    Parameters
    ----------
    k : int
        The deployment's index, in time order.
    n : int, optional
        The number of deployments on the figure (see :func:`deployment_color`).

    Returns
    -------
    str
        A matplotlib hatch pattern from :data:`DEPLOYMENT_HATCHES` (``""`` = plain).
    """
    return DEPLOYMENT_HATCHES[_pair_style(k, n)]


def deployment_line(k: int, n: int | None = None) -> dict:
    """Return deployment *k*'s line color and style.

    Up to seven deployments (*n* ≤ 7): one color each, solid. With more, or *n*
    not given: each color twice, solid then dashed, stepping slowly through
    :data:`DEPLOYMENT_COLORS` (red, red dashed, orange, orange dashed, ...), so
    the colors change evenly with time; past fourteen deployments the pairs
    repeat dotted, then dash-dot.

    Parameters
    ----------
    k : int
        The deployment's index, in time order.
    n : int, optional
        The number of deployments on the figure.

    Returns
    -------
    dict
        ``{"color": ..., "ls": ...}`` for ``Axes.plot``.
    """
    return {
        "color": deployment_color(k, n),
        "ls": DEPLOYMENT_LINESTYLES[_pair_style(k, n)],
    }


def distinct_line_styles(n: int) -> list[tuple[str, str, float]]:
    """Return *n* colourblind-safe ``(color, linestyle, linewidth)`` line styles.

    Cycles the 8-colour :data:`OKABE_ITO` palette and advances the linestyle
    every 8 lines (solid → dashed → dash-dot → dotted), so up to 32 lines each
    get a unique ``(color, linestyle)`` pair — enough to tell apart a mooring's
    worth of instruments where colour alone (max 8–20 hues) collides.  Linewidth
    increases with the linestyle group so the sparser styles (dash-dot, dotted)
    stay as visible as the solid ones: solid thinnest, dashed thin, dash-dot and
    dotted thicker.  Beyond 32 lines the linestyle group is clamped (styles
    repeat) rather than raising.

    Parameters
    ----------
    n : int
        Number of line styles to return (>= 0).

    Returns
    -------
    list of (str, str, float)
        ``(hex_color, linestyle, linewidth)`` per line, in order.

    """
    linestyles = ["-", "--", "-.", ":"]
    linewidths = [0.8, 1.1, 1.5, 1.5]  # solid thinnest; dash-dot/dotted thicker
    out: list[tuple[str, str, float]] = []
    for i in range(max(n, 0)):
        grp = min(i // len(OKABE_ITO), len(linestyles) - 1)
        out.append((OKABE_ITO[i % len(OKABE_ITO)], linestyles[grp], linewidths[grp]))
    return out


def grid_despine(ax: plt.Axes, *, axis: str = "both") -> None:
    """Turn the grid on and hide the top and right spines (report convention).

    The report style keeps ``axes.grid`` off by default and figures opt in; when
    they do, the top and right spines are redundant clutter.  Call this instead of
    ``ax.grid(True)`` so the two always travel together.  Grid appearance (dotted,
    faint) comes from the active mplstyle, not hard-coded here, so a single style
    change restyles every grid.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
        Axes to style.
    axis : {"both", "x", "y"}, optional
        Which gridlines to draw (default ``"both"``).  Bar/profile plots that
        want one-directional gridlines pass ``"x"`` or ``"y"`` and still get the
        top/right spines hidden.

    """
    ax.grid(True, axis=axis)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def level_line_styles(
    serials: list[str], varname: str
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return per-serial ``(colours, linestyles)`` for one variable's level lines.

    *serials* are in the order to colour them (deep-first on a stack). They are
    mapped onto the variable's **line** colormap
    (:data:`parameters.LINE_CMAPS_BY_VARIABLE`, falling back to the field map then
    viridis), so temperature runs from the cold/deep (blue) end to the
    warm/shallow (red) end and pressure from dark to lighter blue. Each colour is
    shared by a consecutive **pair** of serials told apart by linestyle (solid,
    then dashed), which halves the number of distinct colours so the ramp stays
    legible for many instruments. Washed-out colours are skipped by luminance
    (:func:`ordered_line_colors`).

    Parameters
    ----------
    serials : list of str
        Instrument serials, in colouring order.
    varname : str
        The variable whose line colormap is used.

    Returns
    -------
    tuple of dict
        ``{serial: colour}`` and ``{serial: linestyle}``.
    """
    from ..config import parameters as params

    cmap_name = (
        params.LINE_CMAPS_BY_VARIABLE.get(varname)
        or params.CMAPS_BY_VARIABLE.get(varname)
        or "viridis"
    )
    ladder = ["-", "--"]
    pair_colors = ordered_line_colors(cmap_name, max(1, (len(serials) + 1) // 2))
    colors = {s: pair_colors[i // len(ladder)] for i, s in enumerate(serials)}
    styles = {s: ladder[i % len(ladder)] for i, s in enumerate(serials)}
    return colors, styles


def ordered_line_colors(cmap_name: str, n: int, *, max_luminance: float = 0.72) -> list:
    """Return *n* colours from *cmap_name*, in colormap order, skipping pale ones.

    Samples the colormap on a fine grid, keeps only positions whose relative
    luminance is ``<= max_luminance`` (so no line washes out against white), then
    returns *n* colours evenly spaced across the usable positions.  For a
    diverging colormap this drops the pale midpoint, leaving two saturated arcs;
    for a sequential one it drops the pale end.  Callers assign the colours in a
    fixed order (e.g. deep-first) so the darkest end maps to the intended extreme.

    Parameters
    ----------
    cmap_name : str
        Matplotlib colormap name.
    n : int
        Number of colours to return (>= 1).
    max_luminance : float
        Rec. 709 relative-luminance ceiling in ``[0, 1]``; positions lighter than
        this are excluded.  Default 0.72 — low enough that the least-saturated
        remaining colour on a diverging map (the arc boundary either side of the
        excluded pale centre) is still legible on white.

    Returns
    -------
    list of RGBA tuples
        *n* colours (length exactly ``max(n, 1)``).

    """
    import matplotlib.pyplot as plt

    cmap = plt.get_cmap(cmap_name)
    grid = np.linspace(0.0, 1.0, 256)
    cols = cmap(grid)
    lum = 0.2126 * cols[:, 0] + 0.7152 * cols[:, 1] + 0.0722 * cols[:, 2]
    mask = lum <= max_luminance
    usable = grid[mask]
    usable_lum = lum[mask]
    if usable.size == 0:
        usable, usable_lum = grid, lum
    n = max(n, 1)
    if n == 1:
        # A single line: order carries no meaning, so pick the darkest (most
        # saturated) usable colour rather than the midpoint, which on a diverging
        # map lands in the pale gap between the two arcs.
        picks = [usable[int(np.argmin(usable_lum))]]
    else:
        idx = np.linspace(0, usable.size - 1, n).round().astype(int)
        picks = usable[idx]
    return [cmap(float(f)) for f in picks]


# ---------------------------------------------------------------------------
# QC colours and marker styles (OceanSITES Reference Table 2)
# Defined here (Tier 2) so both plotters/ and report/ can import without
# creating a plotters → report circular dependency.
# ---------------------------------------------------------------------------

QC_COLORS: dict[int, str] = {
    0: "#999999",
    1: "#27ae60",
    2: "#a8e6cf",
    3: "#f39c12",
    4: "#e74c3c",
    5: "#e67e22",
    7: "#9b59b6",
    8: "#3498db",
    9: "#bdc3c7",
}

QC_MARKER: dict[int, dict] = {
    3: {
        "marker": "+",
        "c": QC_COLORS[3],
        "s": 15,
        "linewidths": 0.8,
        "zorder": 3,
        "rasterized": True,
    },
    4: {
        "marker": "+",
        "c": QC_COLORS[4],
        "s": 15,
        "linewidths": 0.8,
        "zorder": 4,
        "rasterized": True,
    },
    8: {
        "marker": ".",
        "c": QC_COLORS[8],
        "s": 8,
        "linewidths": 0.5,
        "zorder": 2,
        "rasterized": True,
    },
}


def tukey_smooth(arr: np.ndarray, window_n: int) -> np.ndarray:
    """Zero-phase Tukey (cosine-tapered) smooth, NaN-gap aware.

    Uses a convolution-based approach so NaN gaps do not propagate: output
    points near gaps are weighted only by the finite neighbours that fall
    within the window.  Output is set to NaN where fewer than 10 % of the
    window weights are finite (edges of large data gaps).

    Requires ``scipy``.

    Parameters
    ----------
    arr : np.ndarray
        1-D array, possibly containing NaNs.
    window_n : int
        Window length in samples.

    Returns
    -------
    np.ndarray
        Smoothed array, same shape as *arr*.

    """
    from scipy.signal import windows as scipy_windows

    if window_n < 3 or window_n >= len(arr):
        return arr.copy()
    win = scipy_windows.tukey(window_n, alpha=0.5)
    win = win / win.sum()
    finite = np.isfinite(arr)
    smoothed = np.convolve(np.where(finite, arr, 0.0), win, mode="same")
    wsum = np.convolve(finite.astype(float), win, mode="same")
    with np.errstate(invalid="ignore"):
        return np.where(wsum > 0.1, smoothed / wsum, np.nan)


def _velocity_panel_style(
    var: str,
    finite_vals: np.ndarray,
    div_abs_max: float,
) -> tuple:
    """Return (bounds, norm, cmap, cb_label) for one velocity pcolormesh panel."""
    # Single home for velocity colormap choices so the ADCP and grid/stack reports
    # draw identical panels: every velocity pcolormesh calls this, never inline.
    # div_abs_max is the caller's 2nd/98th-percentile magnitude across all ENU
    # components; finite_vals sets the seq bounds only (pass [] for div/cyc).
    import matplotlib.colors as mcolors

    from .primitives import colorbar_norm

    if var in ("east_velocity", "north_velocity", "up_velocity", "error_velocity"):
        bounds, norm = colorbar_norm(vmin=-div_abs_max, vmax=div_abs_max)
        return bounds, norm, "Spectral_r", unit_label("m s-1")
    if var == "current_speed":
        spd_max = float(np.percentile(finite_vals, 98)) if len(finite_vals) else 1.0
        bounds, norm = colorbar_norm(vmin=0.0, vmax=max(spd_max, 1e-4))
        return bounds, norm, "plasma", unit_label("m s-1")
    if var == "current_direction":
        import matplotlib.pyplot as plt

        # Cyclic colormap so 0° and 360° share a colour; twilight is perceptually
        # uniform (hsv is not).  twilight has 510 native colours, so a plain
        # BoundaryNorm(ncolors=256) maps 0–360° onto only the first half of the
        # cycle (pale→dark, non-cyclic — Eleanor 2026-08-18).  Resample to one
        # colour per bin and match ncolors so the full cycle shows and 0°==360°.
        bounds = np.linspace(0, 360, 21)
        cmap = plt.get_cmap("twilight", len(bounds) - 1)
        norm = mcolors.BoundaryNorm(bounds, cmap.N)
        return bounds, norm, cmap, unit_label("degrees")
    if var == "bin_pressure":
        p_lo = float(np.percentile(finite_vals, 2)) if len(finite_vals) else 0.0
        p_hi = float(np.percentile(finite_vals, 98)) if len(finite_vals) else 1000.0
        bounds, norm = colorbar_norm(vmin=p_lo, vmax=p_hi)
        return bounds, norm, "PuRd", unit_label("dbar")
    # Unknown variable — fall back to diverging
    bounds, norm = colorbar_norm(vmin=-div_abs_max, vmax=div_abs_max)
    return bounds, norm, "Spectral_r", unit_label("m s-1")


def _rose_ax(
    ax: plt.Axes,
    east: np.ndarray,
    north: np.ndarray,
    title: str = "",
    n_dir: int = 16,
    cmap: str = "Blues",
    max_speed: float | None = None,
) -> tuple | None:
    """Draw a current rose on a polar Axes (compass convention, N up, CW)."""
    # Returns (spd_edges, colors) for a shared colorbar, or None when the panel is
    # hidden because fewer than 2 finite values are present.
    import matplotlib.pyplot as plt

    speed = np.sqrt(east**2 + north**2)
    direction = np.degrees(np.arctan2(east, north)) % 360
    valid = np.isfinite(speed) & np.isfinite(direction)
    speed, direction = speed[valid], direction[valid]
    if len(speed) < 2:
        ax.set_visible(False)
        return None

    dir_edges = np.linspace(0, 360, n_dir + 1)
    dir_centers = (dir_edges[:-1] + dir_edges[1:]) / 2
    theta = np.radians(dir_centers)
    bar_width = 2 * np.pi / n_dir * 0.9

    if max_speed is None:
        max_speed = max(float(np.nanpercentile(speed, 99)), 1e-9)
    n_spd = 5
    spd_edges = np.linspace(0, max_speed, n_spd + 1)
    colors = getattr(plt.cm, cmap)(np.linspace(0.25, 1.0, n_spd))

    total = len(speed)
    freqs = np.zeros((n_dir, n_spd))
    for i, (d0, d1) in enumerate(zip(dir_edges[:-1], dir_edges[1:], strict=False)):
        in_dir = (direction >= d0) & (direction < d1)
        for j in range(n_spd):
            in_spd = (speed >= spd_edges[j]) & (speed < spd_edges[j + 1])
            freqs[i, j] = np.sum(in_dir & in_spd) / total

    bottom = np.zeros(n_dir)
    for j in range(n_spd):
        ax.bar(
            theta,
            freqs[:, j],
            width=bar_width,
            bottom=bottom,
            color=colors[j],
            align="center",
            linewidth=0.2,
            edgecolor="white",
        )
        bottom += freqs[:, j]

    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_xticks(np.radians([0, 90, 180, 270]))
    ax.set_xticklabels(["N", "E", "S", "W"])
    # Tuck the N/E/S/W labels closer to the frame (about half the default ~3.5 pad)
    # so panels can sit nearer each other without "W" crowding the next axis.
    ax.tick_params(axis="x", pad=1.75)
    ax.set_rticks([])
    ax.set_title(title, pad=2)
    return spd_edges, colors
