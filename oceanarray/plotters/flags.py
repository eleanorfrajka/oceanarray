"""Quality-flag strip: time × level, oceanarray's beside the originator's."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from oceanarray.config import parameters as params
from oceanarray.pipeline.masking import FLAGGED_VARIABLES
from oceanarray.pipeline.qc.originator import MAPPED_SUFFIX
from oceanarray.reports._vendored import design_tokens as report_tokens

from .helpers import QC_COLORS

if TYPE_CHECKING:
    import matplotlib.pyplot as plt
    import xarray as xr


def level_dim(ds: xr.Dataset) -> str:
    """Return the level dimension: ``N_DEPTHS`` (a Stack cat) or ``N_LEVELS``."""
    return "N_DEPTHS" if "N_DEPTHS" in ds.dims else "N_LEVELS"


def worst_flags(ds: xr.Dataset, suffix: str) -> np.ndarray | None:
    """Return the worst flag per (level, time) over the flagged variables.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack or Stack cat.
    suffix : str
        ``"_qc"`` (ours) or ``"_qc_originator_mapped"`` (the originator's).

    Returns
    -------
    numpy.ndarray or None
        int8 ``(level, time)``; 9 where no variable has a value; None when no
        variable has this flag set.
    """
    from oceanarray.pipeline.qc import _worst_flag

    dim = level_dim(ds)
    out = None
    present = None
    for var in FLAGGED_VARIABLES:
        name = f"{var}{suffix}"
        if name not in ds.data_vars or var not in ds.data_vars:
            continue
        here = np.isfinite(ds[var].transpose(dim, "time").values)
        # A variable a level does not have (its 9s) says nothing about the others.
        flags = np.where(
            here, ds[name].transpose(dim, "time").values, params.QC_NOT_EVALUATED
        )
        out = flags.astype(np.int8) if out is None else _worst_flag(out, flags)
        present = here if present is None else present | here
    if out is None:
        return None
    out[~present] = params.QC_MISSING
    return out


def level_labels(ds: xr.Dataset) -> list[str]:
    """Return one label per level: instrument and serial, or a site depth.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack (``N_LEVELS``) or Stack cat (``N_DEPTHS``).

    Returns
    -------
    list of str
        ``"microcat 2941"`` per level, or ``"microcat 1500 m"`` per depth.
    """
    if "N_DEPTHS" in ds.dims:
        depth = ds["nominal_depth"].values if "nominal_depth" in ds else None
        family = ds["depth_family"].values if "depth_family" in ds else None
        return [
            f"{family[k] if family is not None else ''} {depth[k]:.0f} m".strip()
            if depth is not None and np.isfinite(depth[k])
            else f"depth {k}"
            for k in range(ds.sizes["N_DEPTHS"])
        ]
    types = ds["instrument_type"].values if "instrument_type" in ds else None
    return [
        f"{types[k] if types is not None else ''} {s}".strip()
        for k, s in enumerate(ds["serial"].values)
    ]


#: Short names for the variables, used in labels and the flag table.
SHORT: dict[str, str] = {
    "temperature": "T",
    "salinity": "S",
    "conductivity": "C",
    "pressure": "P",
    "east_velocity": "u",
    "north_velocity": "v",
    "up_velocity": "w",
    "current_speed": "speed",
    "current_direction": "direction",
    "dissolved_oxygen": "O2",
    "turbidity": "turbidity",
}

#: The flags that make an instrument and variable worth a row: ours (3 suspect,
#: 4 bad, 8 interpolated) and the originator's (3, 4, 5 value changed).
SHOWN_OURS = (3, 4, 8)
SHOWN_ORIGINATOR = (3, 4, 5)


def flagged_rows(
    ds: xr.Dataset,
) -> dict[int, list[tuple[str, np.ndarray | None, np.ndarray | None]]]:
    """Return, per level with anything flagged, the variables' flag rows.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack or Stack cat.

    Returns
    -------
    dict of int to list of (str, numpy.ndarray or None, numpy.ndarray or None)
        Level index → ``(variable, originator, ours)`` for each variable flagged
        by either. A flag set the file lacks is None (no row is drawn for it: it
        was not checked, which is not the same as missing); in a set the file
        has, a step with no value is 9 and a value with no flag 0.
    """
    dim = level_dim(ds)
    tds = ds.transpose(dim, "time", ...)
    out: dict[int, list] = {}
    for var in FLAGGED_VARIABLES:
        if var not in ds.data_vars:
            continue
        here = np.isfinite(tds[var].values)
        sets: list[np.ndarray | None] = []
        for name in (f"{var}{MAPPED_SUFFIX}", f"{var}_qc"):
            if name not in ds.data_vars:
                sets.append(None)
                continue
            flags = np.asarray(tds[name].values, dtype=float)
            base = np.where(here, params.QC_NOT_EVALUATED, params.QC_MISSING)
            sets.append(
                np.where(here & np.isfinite(flags), flags, base).astype(np.int8)
            )
        theirs, ours = sets
        if theirs is None and ours is None:
            continue
        for k in range(here.shape[0]):
            if (theirs is not None and np.isin(theirs[k], SHOWN_ORIGINATOR).any()) or (
                ours is not None and np.isin(ours[k], SHOWN_OURS).any()
            ):
                out.setdefault(k, []).append(
                    (
                        var,
                        None if theirs is None else theirs[k],
                        None if ours is None else ours[k],
                    )
                )
    return out


def _rows(
    entries: list[tuple[str, np.ndarray | None, np.ndarray | None]],
) -> list[tuple[str, np.ndarray]]:
    """Return a panel's ``(label, flags)`` bars: the originator's above oceanarray's.

    A flag set that is None (not in the file) gets no bar.
    """
    bars = []
    for var, theirs, ours in entries:
        short = SHORT.get(var, var)
        for who, row in (("originator", theirs), ("oceanarray", ours)):
            if row is not None:
                bars.append((f"{short} {who}", row))
    return bars


def draw_flag_strip(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_FULL,
    labels: list[str] | None = None,
) -> plt.Figure | None:
    """Draw flags through time: one panel per instrument, a pair of bars per variable.

    Each panel holds, for every variable flagged by oceanarray or the
    originator, the originator's flags (translated to our table) above ours on a
    shared time axis, so where one flagged a value and the other did not reads
    straight down the pair. Panels sit two to a row (half width, even alone).
    Instruments with nothing flagged are left out, and so is a flag set the file
    does not have (oceanarray runs no conductivity QC, say). 9: no value; 0: a
    value with no flag.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack or Stack cat.
    width_in : float, optional
        Figure width in inches.
    labels : list of str, optional
        One title per level (the page's instrument names); :func:`level_labels`
        when not given.

    Returns
    -------
    matplotlib.figure.Figure or None
        None when nothing is flagged.
    """
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch

    panels = flagged_rows(ds)
    if not panels:
        return None
    levels = sorted(panels)
    # Always two columns, so a panel is half the page wide even when alone.
    ncols = 2
    nrows = -(-len(levels) // ncols)
    rows = {k: _rows(panels[k]) for k in levels}
    heights = [
        max(len(rows[k]) for k in levels[r * ncols : (r + 1) * ncols])
        for r in range(nrows)
    ]
    values = [f for f in params.QC_FLAG_VALUES if f in QC_COLORS]
    cmap = ListedColormap([QC_COLORS[f] for f in values])
    norm = BoundaryNorm([*(v - 0.5 for v in values), values[-1] + 0.5], cmap.N)
    time = ds["time"].values
    t_num = mdates.date2num(time.astype("datetime64[ns]"))
    labels = labels if labels is not None else level_labels(ds)
    fig, axs = plt.subplots(
        nrows,
        ncols,
        figsize=(width_in, 0.9 + sum(0.45 + 0.36 * h for h in heights)),
        sharex=True,
        squeeze=False,
        height_ratios=[0.6 + h for h in heights],
        layout="constrained",
    )
    present: set[int] = set()
    for n, k in enumerate(levels):
        ax = axs[n // ncols, n % ncols]
        ticks = [label for label, _row in rows[k]]
        bars = [row for _label, row in rows[k]]
        image = np.stack(bars)
        present |= {int(f) for f in np.unique(image)}
        ax.imshow(
            image,
            aspect="auto",
            interpolation="nearest",
            cmap=cmap,
            norm=norm,
            extent=[t_num[0], t_num[-1], len(bars) - 0.5, -0.5],
        )
        # A single flagged step is narrower than a pixel: draw the flags that
        # matter as lines on top so none disappears.
        for r, row in enumerate(bars):
            for f in (3, 4, 5, 8):
                at = t_num[row == f]
                if at.size:
                    ax.vlines(at, r - 0.5, r + 0.5, color=QC_COLORS[f])
        # A white line between variables (a pair, or one bar when a set is absent).
        for r in range(1, len(ticks)):
            if ticks[r].split(" ")[0] != ticks[r - 1].split(" ")[0]:
                ax.axhline(r - 0.5, color="white")
        ax.set_yticks(range(len(bars)), ticks)
        ax.tick_params(axis="y", length=0)
        ax.set_title(labels[k], loc="left")
    for n in range(len(levels), nrows * ncols):
        axs[n // ncols, n % ncols].set_visible(False)
        # The panel above an empty slot is the bottom of its column: give it dates.
        if n >= ncols:
            axs[n // ncols - 1, n % ncols].tick_params(labelbottom=True)
    locator = mdates.AutoDateLocator(minticks=2, maxticks=4)
    for ax in axs[-1]:
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    words = params.QC_FLAG_LABEL
    shown = sorted(f for f in present if f in QC_COLORS)
    fig.legend(
        handles=[Patch(color=QC_COLORS[f], label=words[f]) for f in shown],
        loc="outside lower center",
        ncols=min(len(shown), 4),
        frameon=False,
    )
    return fig
