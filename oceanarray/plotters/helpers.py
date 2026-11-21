"""Shared helpers for the plotters package.

Provides colormap helpers and rose-diagram rendering used across multiple
Tier-2 plotter modules.

plotters/ never serialises to base64: the Tier-3 wrappers in reports/_plots.py
encode each figure through the shared report encoder.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

from oceanarray.config.unit_tables import unit_label
from oceanarray.pipeline._stage import normalize_identity_names

if TYPE_CHECKING:
    from pathlib import Path

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
    """Return deployment *k*'s hex color, from early (red) to late (purple).

    Up to seven deployments get one color each; with more, or *n* not given, consecutive
    pairs share a color, told apart by :func:`deployment_line`, :func:`deployment_marker`
    or :func:`deployment_hatch`.

    Parameters
    ----------
    k : int
        Deployment index, in time order.
    n : int, optional
        Deployments on the figure.

    Returns
    -------
    str
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
    """Return deployment *k*'s ``{"color", "ls"}`` for ``Axes.plot``.

    Up to seven deployments: one color each, solid. Otherwise each color twice, solid
    then dashed; past fourteen the pairs repeat dotted, then dash-dot.

    Parameters
    ----------
    k : int
        Deployment index, in time order.
    n : int, optional
        Deployments on the figure.

    Returns
    -------
    dict
    """
    return {
        "color": deployment_color(k, n),
        "ls": DEPLOYMENT_LINESTYLES[_pair_style(k, n)],
    }


def distinct_line_styles(n: int) -> list[tuple[str, str, float]]:
    """Return *n* colourblind-safe ``(color, linestyle, linewidth)`` styles.

    Cycles the 8 :data:`OKABE_ITO` colours, changing linestyle every 8 lines (solid,
    dashed, dash-dot, dotted; thicker for the sparser ones); styles repeat past 32.

    Parameters
    ----------
    n : int
        Number of styles (≥ 0).

    Returns
    -------
    list of (str, str, float)
    """
    linestyles = ["-", "--", "-.", ":"]
    linewidths = [0.8, 1.1, 1.5, 1.5]  # solid thinnest; dash-dot/dotted thicker
    out: list[tuple[str, str, float]] = []
    for i in range(max(n, 0)):
        grp = min(i // len(OKABE_ITO), len(linestyles) - 1)
        out.append((OKABE_ITO[i % len(OKABE_ITO)], linestyles[grp], linewidths[grp]))
    return out


def grid_despine(ax: plt.Axes, *, axis: str = "both") -> None:
    """Turn the grid on and hide the top and right spines (grid look from the mplstyle).

    Parameters
    ----------
    ax : matplotlib.axes.Axes
        Axes to style.
    axis : {"both", "x", "y"}, optional
        Which gridlines to draw.
    """
    ax.grid(True, axis=axis)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def level_line_styles(
    serials: list[str], varname: str
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return ``({serial: colour}, {serial: linestyle})`` for one variable's level lines.

    Serials, in colouring order (deep first on a stack), run along the variable's line
    colormap (:data:`parameters.LINE_CMAPS_BY_VARIABLE`); each colour serves a pair,
    solid then dashed; pale colours are skipped (:func:`ordered_line_colors`).

    Parameters
    ----------
    serials : list of str
        Serials in colouring order.
    varname : str
        The variable whose colormap is used.

    Returns
    -------
    tuple of dict
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
    """Return *n* colours from *cmap_name* in colormap order, skipping pale ones.

    Parameters
    ----------
    cmap_name : str
        Matplotlib colormap name.
    n : int
        Number of colours (≥ 1).
    max_luminance : float, optional
        Rec. 709 relative-luminance ceiling in ``[0, 1]`` (drops a diverging map's pale
        centre, a sequential map's pale end).

    Returns
    -------
    list of tuple
        ``max(n, 1)`` RGBA colours.
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

#: Markers of flagged values on the instrument page's figures: small dots for
#: suspect (3), crosses for bad (4), small blue points for interpolated (8).
QC_MARKER: dict[int, dict] = {
    3: {
        "marker": ".",
        "c": QC_COLORS[3],
        "s": 16,
        "linewidths": 0,
        "zorder": 3,
        "rasterized": True,
    },
    4: {
        "marker": "x",
        "c": QC_COLORS[4],
        "s": 16,
        "linewidths": 0.9,
        "zorder": 4,
        "rasterized": True,
    },
    8: {
        "marker": ".",
        "c": QC_COLORS[8],
        "s": 6,
        "linewidths": 0,
        "zorder": 2,
        "rasterized": True,
    },
}


#: The flag sets an instrument page reads: oceanarray's and the originator's
#: (translated to oceanarray's table).
FLAG_SOURCES: tuple[str, ...] = ("_qc", "_qc_originator_mapped")


def as_dataset(src: Path | str | xr.Dataset) -> xr.Dataset:
    """Return a stage file's data in memory: read from *src*, or *src* itself.

    A page that has the file open already passes it, so each figure does not read
    it again; a shallow copy, so a figure adding variables leaves the page's own.

    Parameters
    ----------
    src : pathlib.Path, str or xarray.Dataset
        The file, or the dataset already read from it.

    Returns
    -------
    xarray.Dataset
        Loaded; closing it closes nothing the caller holds.
    """
    import xarray as xr

    if isinstance(src, xr.Dataset):
        return src.copy()
    with xr.open_dataset(src, decode_timedelta=False, engine="netcdf4") as ds:
        return ds.load()


def marker_flags(
    ds: Any, var: str, sources: Sequence[str] = FLAG_SOURCES
) -> np.ndarray | None:
    """Return the flag each value of *var* is marked with: the worse of the flag sets.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file.
    var : str
        The data variable.
    sources : sequence of str, optional
        Flag-variable suffixes read (``"_qc"``, ``"_qc_originator_mapped"``).

    Returns
    -------
    numpy.ndarray or None
        int8 like ``ds[var]``: 4 if any set says bad, else 3 if any says suspect, else 8
        where ours says interpolated, else 1; None without any of these sets.
    """
    sets = [
        np.asarray(ds[f"{var}{s}"].values)
        for s in sources
        if f"{var}{s}" in ds.data_vars
    ]
    if not sets:
        return None
    out = np.ones(sets[0].shape, dtype=np.int8)
    for flag in (8, 3, 4):  # in rising order: a worse flag overwrites
        for flags in sets:
            out[flags == flag] = flag
    return out


def tukey_smooth(arr: np.ndarray, window_n: int) -> np.ndarray:
    """Zero-phase Tukey (cosine-tapered) smooth that weights only finite neighbours (needs scipy).

    Parameters
    ----------
    arr : numpy.ndarray
        1-D array, possibly with NaNs.
    window_n : int
        Window length (samples).

    Returns
    -------
    numpy.ndarray
        Same shape; NaN where under 10 % of the window weight is finite.
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

    from oceanarray.physics import speed_direction

    speed, direction = speed_direction(east, north)
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


#: Speed classes of the current roses (cm/s): each class's lower edge; the last
#: class is open-ended.
ROSE_SPEED_CLASSES_CMS: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0, 30.0)


def rose_speed_labels() -> list[str]:
    """Return the legend labels of the rose speed classes (``"0–5"`` … ``">30"``)."""
    edges = ROSE_SPEED_CLASSES_CMS
    labels = [f"{a:g}–{b:g}" for a, b in zip(edges[:-1], edges[1:], strict=True)]
    return [*labels, f">{edges[-1]:g}"]


def rose_colors(cmap: str = "Blues") -> np.ndarray:
    """Return one RGBA colour per rose speed class, light (slow) to dark (fast)."""
    import matplotlib.pyplot as plt

    return plt.get_cmap(cmap)(np.linspace(0.25, 1.0, len(ROSE_SPEED_CLASSES_CMS)))


def current_rose(
    ax: plt.Axes,
    east: np.ndarray,
    north: np.ndarray,
    *,
    title: str = "",
    n_dir: int = 16,
    cmap: str = "Blues",
    r_max: float | None = None,
) -> bool:
    """Draw a current rose on a polar Axes: the stack and grid pages' one design.

    Each wedge is a direction toward which the current flows (16 sectors,
    compass convention: north up, clockwise); its length is the percentage of the
    record in that direction, split into the speed classes
    :data:`ROSE_SPEED_CLASSES_CMS` (light to dark).  Rings are labelled in percent
    along the bearing where the wedges are shortest (that sector and its two
    neighbours), so the labels sit clear of the wedges.

    Parameters
    ----------
    ax : matplotlib.projections.polar.PolarAxes
        The target axes.
    east, north : numpy.ndarray
        Velocity components (m s⁻¹), one value per time step.
    title : str, optional
        Panel title.
    n_dir : int, optional
        Number of direction sectors (default 16).
    cmap : str, optional
        Colormap of the speed classes (default ``"Blues"``).
    r_max : float or None, optional
        The radial scale (%): one value for every rose of a figure
        (:func:`rose_max_percent`), so their rings compare; the rose's own longest
        wedge when not given.

    Returns
    -------
    bool
        False (and the axes hidden) when fewer than two values are present.
    """
    from matplotlib.ticker import MaxNLocator

    from oceanarray.physics import speed_direction

    speed, direction = speed_direction(
        np.asarray(east, dtype=float), np.asarray(north, dtype=float)
    )
    valid = np.isfinite(speed) & np.isfinite(direction)
    speed_cms, direction = 100.0 * speed[valid], direction[valid]
    if len(speed_cms) < 2:
        ax.set_visible(False)
        return False
    dir_edges = np.linspace(0.0, 360.0, n_dir + 1)
    theta = np.radians(0.5 * (dir_edges[:-1] + dir_edges[1:]))
    sector = np.clip(np.digitize(direction, dir_edges) - 1, 0, n_dir - 1)
    spd_class = np.digitize(speed_cms, ROSE_SPEED_CLASSES_CMS) - 1
    pct = np.zeros((n_dir, len(ROSE_SPEED_CLASSES_CMS)))
    np.add.at(pct, (sector, spd_class), 100.0 / len(speed_cms))
    colors = rose_colors(cmap)
    bottom = np.zeros(n_dir)
    for j in range(pct.shape[1]):
        ax.bar(
            theta,
            pct[:, j],
            width=2 * np.pi / n_dir * 0.9,
            bottom=bottom,
            color=colors[j],
            align="center",
            linewidth=0.2,
            edgecolor="white",
        )
        bottom += pct[:, j]
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_xticks(np.radians([0, 90, 180, 270]))
    ax.set_xticklabels(["N", "E", "S", "W"])
    ax.tick_params(axis="x", pad=1.75)
    top = float(bottom.max()) if r_max is None else float(r_max)
    rings = [
        r
        for r in MaxNLocator(nbins=3, steps=[1, 2, 2.5, 5, 10]).tick_values(0, top)
        if 0 < r <= top * 1.02
    ]
    ax.set_ylim(0, max(top, rings[-1] if rings else top) * 1.05)
    ax.set_rticks(rings)
    ax.set_yticklabels([f"{r:g} %" for r in rings], fontsize=7, color="0.35")
    ax.set_rlabel_position(_quiet_bearing(bottom))
    ax.set_title(title, pad=2)
    return True


def _quiet_bearing(lengths: np.ndarray) -> float:
    """Return the compass bearing (°) where a rose's wedges are shortest.

    Parameters
    ----------
    lengths : numpy.ndarray
        Total wedge length (%) of each of the rose's equal sectors, clockwise
        from north.

    Returns
    -------
    float
        Centre of the sector whose own and two neighbours' longest wedge is the
        shortest (a ring label spans about three sectors near the centre); the
        first such sector clockwise from north on a tie.
    """
    lengths = np.asarray(lengths, dtype=float)
    n = len(lengths)
    span = np.maximum.reduce([np.roll(lengths, 1), lengths, np.roll(lengths, -1)])
    return (int(np.argmin(span)) + 0.5) * 360.0 / n


def nominal_depth_text(ds: xr.Dataset) -> str:
    """Return one instrument file's nominal depth as ``"1500 m"``, or ``"?"``.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file.

    Returns
    -------
    str
        The depth to the metre, or ``"?"`` when the file has none.
    """
    ds = normalize_identity_names(ds)
    if "instrument_depth" in ds.variables:
        return f"{float(ds['instrument_depth'].values):.0f} m"
    return "?"


def serial_text(ds: xr.Dataset) -> str:
    """Return one instrument file's serial: ``instrument_serial``, else ``serial``, else ``"?"``.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file.

    Returns
    -------
    str
        The serial as the file records it.
    """
    ds = normalize_identity_names(ds)
    if "instrument_serial" in ds.variables:
        return str(ds["instrument_serial"].item())
    return str(ds.attrs.get("serial", "?"))


def tilt_limits(ds: xr.Dataset) -> tuple[float, float]:
    """Return ``(suspect, fail)`` tilt limits (°) from the attributes, else ``QC_TILT``.

    Parameters
    ----------
    ds : xarray.Dataset
        A stage file or stack.

    Returns
    -------
    tuple of float
    """
    from oceanarray.config import parameters as params

    return (
        float(
            ds.attrs.get("tilt_suspect_threshold", params.QC_TILT["suspect_threshold"])
        ),
        float(ds.attrs.get("tilt_fail_threshold", params.QC_TILT["fail_threshold"])),
    )


def nominal_depths(ds: xr.Dataset) -> np.ndarray | None:
    """Return each level's nominal depth (m): ``instrument_depth``, else water depth minus HAB.

    A Stack cat's depths have their own (``nominal_depth`` on ``N_DEPTHS``).

    Parameters
    ----------
    ds : xarray.Dataset
        A stack on ``N_LEVELS``, or a Stack cat.

    Returns
    -------
    numpy.ndarray or None
        One value per level or depth (NaN where unknown); None without a source.
    """
    if "N_DEPTHS" in ds.dims:
        return ds["nominal_depth"].values.astype(float)
    ds = normalize_identity_names(ds)
    if "instrument_depth" in ds.variables and ds["instrument_depth"].ndim == 1:
        return np.asarray(ds["instrument_depth"].values, dtype=float)
    waterdepth = ds.attrs.get("waterdepth")
    if "hab" in ds.variables and waterdepth not in (None, ""):
        try:
            return float(waterdepth) - np.asarray(ds["hab"].values, dtype=float)
        except (TypeError, ValueError):
            return None
    return None


def rose_max_percent(east: np.ndarray, north: np.ndarray, n_dir: int = 16) -> float:
    """Return the longest wedge (% of the record) of :func:`current_rose`'s rose.

    Parameters
    ----------
    east, north : numpy.ndarray
        Velocity components (m s⁻¹).
    n_dir : int, optional
        Number of direction sectors.

    Returns
    -------
    float
        0.0 when fewer than two values are present.
    """
    from oceanarray.physics import speed_direction

    _, direction = speed_direction(
        np.asarray(east, dtype=float), np.asarray(north, dtype=float)
    )
    direction = direction[np.isfinite(direction)]
    if len(direction) < 2:
        return 0.0
    edges = np.linspace(0.0, 360.0, n_dir + 1)
    sector = np.clip(np.digitize(direction, edges) - 1, 0, n_dir - 1)
    return float(np.bincount(sector, minlength=n_dir).max() * 100.0 / len(direction))


def draw_roses(
    roses: list[tuple[np.ndarray, np.ndarray, str]],
    *,
    width_in: float,
    max_cols: int = 4,
    own_scale: bool = False,
) -> plt.Figure | None:
    """Draw current roses in rows of up to *max_cols*, each a quarter of the width.

    The roses share one radial scale (the longest wedge of any of them), so their
    rings compare, unless *own_scale*; one speed-class legend; the figure is as
    wide as the roses it holds, centred.

    Parameters
    ----------
    roses : list of (numpy.ndarray, numpy.ndarray, str)
        ``(east, north, title)`` per rose (m s⁻¹).
    width_in : float
        Width of the slot (inches); each rose takes ``width_in / max_cols``.
    max_cols : int, optional
        Roses per row.
    own_scale : bool, optional
        Give each rose its own radial scale (its longest wedge), for roses of
        subsets of very different size.

    Returns
    -------
    matplotlib.figure.Figure or None
        None when *roses* is empty.
    """
    import math

    import matplotlib.pyplot as plt

    if not roses:
        return None
    n = len(roses)
    ncols = min(n, max_cols)
    nrows = math.ceil(n / ncols)
    legend_in = 0.6
    fig_h = nrows * (width_in / max_cols + 0.65) + legend_in
    fig, axs = plt.subplots(
        nrows,
        ncols,
        figsize=(width_in, fig_h),
        subplot_kw={"projection": "polar"},
        squeeze=False,
    )
    side = ncols * width_in / max_cols
    margin = (width_in - side) / 2 / width_in
    fig.subplots_adjust(
        left=0.05 * side / width_in + margin,
        right=1 - 0.05 * side / width_in - margin,
        wspace=0.4,
        hspace=0.45,
        top=1 - 0.4 / fig_h,
        bottom=(0.2 + legend_in) / fig_h,
    )
    r_max = None if own_scale else max(rose_max_percent(e, v) for e, v, _ in roses)
    axs_flat = axs.flatten()
    for ax, (east, north, title) in zip(axs_flat, roses, strict=False):
        current_rose(ax, east, north, title=title, r_max=r_max or None)
    for ax in axs_flat[n:]:
        ax.set_visible(False)
    rose_legend(fig, y=0.05 / fig_h)
    return fig


def rose_legend(fig: plt.Figure, *, cmap: str = "Blues", y: float = 0.0) -> None:
    """Add the speed-class legend of :func:`current_rose` along *fig*'s bottom.

    Parameters
    ----------
    fig : matplotlib.figure.Figure
        The figure of roses.
    cmap : str, optional
        The roses' colormap.
    y : float, optional
        Figure-fraction height of the legend's bottom edge.
    """
    from matplotlib.patches import Patch

    handles = [
        Patch(facecolor=c, edgecolor="white", label=lbl)
        for c, lbl in zip(rose_colors(cmap), rose_speed_labels(), strict=True)
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, y),
        ncol=len(handles),
        title="Speed (cm/s)",
        frameon=False,
        fontsize=8,
        title_fontsize=8,
    )


def duration_words(days: float, *, adjective: bool = False) -> str:
    """Return a duration as words: ``"14 days"``, ``"5.3 days"``, ``"12 hours"``, ``"30 min"``.

    Parameters
    ----------
    days : float
        The duration in days.
    adjective : bool, optional
        Hyphenated, singular form before a noun (``"14-day"``, ``"12-hour"``).

    Returns
    -------
    str
    """

    def fmt(x: float) -> str:
        return f"{x:.0f}" if abs(x - round(x)) < 0.05 or x >= 10 else f"{x:.1f}"

    if days >= 1.0:
        value, unit = fmt(days), "day"
    elif days * 24.0 >= 1.0:
        value, unit = fmt(days * 24.0), "hour"
    else:
        value, unit = fmt(days * 1440.0), "min"
    if adjective:
        return f"{value}-{unit}"
    if unit == "min":
        return f"{value} min"
    return f"{value} {unit}" + ("" if value == "1" else "s")
