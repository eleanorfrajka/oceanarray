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

#: Height of one flag bar in the flag strip (inches).
BAR_HEIGHT_IN = 0.18


def _flag_sets(
    ds: xr.Dataset,
) -> dict[str, tuple[np.ndarray | None, np.ndarray | None]]:
    """Return each flagged variable's ``(originator, ours)`` flags, ``(level, time)``.

    A flag set the file lacks is None; in a set the file has, a step with no
    value is 9 and a value with no flag 0.
    """
    dim = level_dim(ds)
    tds = ds.transpose(dim, "time", ...)
    out = {}
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
        if any(f is not None for f in sets):
            out[var] = (sets[0], sets[1])
    return out


def _flagged(theirs: np.ndarray | None, ours: np.ndarray | None) -> bool:
    """Return whether either row carries a flag worth a bar."""
    return (theirs is not None and np.isin(theirs, SHOWN_ORIGINATOR).any()) or (
        ours is not None and np.isin(ours, SHOWN_OURS).any()
    )


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
    out: dict[int, list] = {}
    for var, (theirs, ours) in _flag_sets(ds).items():
        for k in range(ds.sizes[level_dim(ds)]):
            row = (
                None if theirs is None else theirs[k],
                None if ours is None else ours[k],
            )
            if _flagged(*row):
                out.setdefault(k, []).append((var, *row))
    return out


def adcp_groups(ds: xr.Dataset) -> dict[int, list[int]]:
    """Return each ADCP head's level (or depth) and those of its bins.

    In a Stack cat, ADCPs of several deployments whose heads share a depth form
    one group. Bins of an ADCP whose head is not in *ds* are left out.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack or Stack cat.

    Returns
    -------
    dict of int to list of int
        Head index → bin indices, in index order.
    """
    from oceanarray.paths import adcp_parent_serial

    if "serial" not in ds.variables:
        return {}
    serials = [str(s) for s in ds["serial"].values]
    at = ds["depth_index"].values if "N_DEPTHS" in ds.dims else np.arange(len(serials))
    heads = {
        (adcp_parent_serial(s), dep): int(at[i])
        for i, (s, dep) in enumerate(
            zip(serials, _deployments(ds, len(serials)), strict=True)
        )
        if s.endswith("_hd") and at[i] >= 0
    }
    out: dict[int, list[int]] = {h: [] for h in heads.values()}
    seen = set(heads.values())
    for i, (s, dep) in enumerate(
        zip(serials, _deployments(ds, len(serials)), strict=True)
    ):
        head = heads.get((adcp_parent_serial(s), dep))
        if head is None or s.endswith("_hd") or at[i] < 0 or at[i] in seen:
            continue
        out[head].append(int(at[i]))
        seen.add(int(at[i]))
    return {h: sorted(b) for h, b in out.items()}


def _deployments(ds: xr.Dataset, n: int) -> np.ndarray:
    """Return each level's deployment (zeros in a single-deployment stack)."""
    return ds["deployment"].values if "deployment" in ds.variables else np.zeros(n)


def _rows(
    entries: list[tuple[str, np.ndarray | None, np.ndarray | None]],
    at: str = "",
) -> list[tuple[str, np.ndarray]]:
    """Return a panel's ``(label, flags)`` bars: the originator's above oceanarray's.

    A flag set that is None (not in the file) gets no bar; *at* (``" 152 m"``)
    follows the variable's short name.
    """
    bars = []
    for var, theirs, ours in entries:
        short = SHORT.get(var, var)
        for who, row in (("originator", theirs), ("oceanarray", ours)):
            if row is not None:
                bars.append((f"{short}{at} {who}", row))
    return bars


def _panel_bars(ds: xr.Dataset) -> dict[int, list[tuple[str, np.ndarray]]]:
    """Return, per panel (level or ADCP head), its ``(label, flags)`` bars.

    An ADCP is one panel at its head: the series the head carries once (its
    flags, filled from its bins where the head has none), then each other
    variable at each bin that has it flagged, labelled with the bin depth.
    """
    from .helpers import nominal_depths

    sets = _flag_sets(ds)
    panels = {k: _rows(e) for k, e in flagged_rows(ds).items()}
    depths = nominal_depths(ds)
    for head, bins in adcp_groups(ds).items():
        for b in bins:
            panels.pop(b, None)
        bars = []
        own = []
        for var, pair in sets.items():
            if not np.isfinite(
                ds[var].transpose(level_dim(ds), "time").values[head]
            ).any():
                continue
            own.append(var)
            row = []
            for flags in pair:
                if flags is None:
                    row.append(None)
                    continue
                merged = flags[head].copy()
                for b in bins:
                    fill = np.isin(merged, (params.QC_NOT_EVALUATED, params.QC_MISSING))
                    fill &= flags[b] != params.QC_MISSING
                    merged[fill] = flags[b][fill]
                row.append(merged)
            if _flagged(*row):
                bars += _rows([(var, *row)])
        for var, (theirs, ours) in sets.items():
            if var in own:
                continue
            for b in bins:
                row = (
                    None if theirs is None else theirs[b],
                    None if ours is None else ours[b],
                )
                if _flagged(*row):
                    at = f" {depths[b]:.0f} m" if depths is not None else f" bin {b}"
                    bars += _rows([(var, *row)], at)
        panels.pop(head, None)
        if bars:
            panels[head] = bars
    return panels


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
    straight down the pair. Panels sit two to a row (half width, even alone);
    every bar is :data:`BAR_HEIGHT_IN` high, so a panel's height follows its
    number of bars. An ADCP is one panel, titled at its head: the head's series
    once, then the other variables per bin (``"u 152 m"``).
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

    rows = _panel_bars(ds)
    if not rows:
        return None
    levels = sorted(rows)
    # Always two columns, so a panel is half the page wide even when alone.
    ncols = 2
    nrows = -(-len(levels) // ncols)
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
    # Placed in inches, not by a layout engine: every bar is BAR_HEIGHT_IN high
    # and the panels of a row share their top.
    label_in, gap_in, title_in, foot_in = 1.25, 0.1, 0.25, 1.0
    panel_w = (width_in - ncols * label_in - gap_in) / ncols
    height_in = gap_in + sum(title_in + BAR_HEIGHT_IN * h + gap_in for h in heights)
    height_in += foot_in
    fig = plt.figure(figsize=(width_in, height_in))
    fig._manual_layout = True
    tops = height_in - np.cumsum(
        [0, *(title_in + BAR_HEIGHT_IN * h + gap_in for h in heights)]
    )
    axs: list = []
    present: set[int] = set()
    for n, k in enumerate(levels):
        r, c = divmod(n, ncols)
        h = BAR_HEIGHT_IN * len(rows[k])
        x = label_in + c * (label_in + panel_w)
        y = tops[r] - gap_in - title_in - h
        ax = fig.add_axes(
            (x / width_in, y / height_in, panel_w / width_in, h / height_in),
            sharex=axs[0] if axs else None,
        )
        axs.append(ax)
        # Dates only under the last panel of each column.
        last = n + ncols >= len(levels)
        ax.tick_params(labelbottom=last, bottom=last)
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
            if ticks[r].rsplit(" ", 1)[0] != ticks[r - 1].rsplit(" ", 1)[0]:
                ax.axhline(r - 0.5, color="white")
        ax.set_yticks(range(len(bars)), ticks)
        ax.tick_params(axis="y", length=0, labelsize=8)
        ax.set_title(labels[k], loc="left", pad=3)
    locator = mdates.AutoDateLocator(minticks=2, maxticks=4)
    axs[0].xaxis.set_major_locator(locator)
    axs[0].xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    words = params.QC_FLAG_LABEL
    shown = sorted(f for f in present if f in QC_COLORS)
    fig.legend(
        handles=[Patch(color=QC_COLORS[f], label=words[f]) for f in shown],
        loc="lower center",
        ncols=min(len(shown), 4),
        frameon=False,
    )
    return fig
