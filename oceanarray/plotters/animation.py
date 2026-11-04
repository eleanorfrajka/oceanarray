"""Animated plot functions for the oceanarray plotters package.

This module is intentionally separate from the static (Tier-1/2/3) pipeline.
It relies on ``matplotlib.animation`` and requires the ``pillow`` package as a
GIF writer.  ``scipy`` is required for the Tukey smoothing window.

Future interactive equivalents (plotly, bokeh) should live in a companion
``interactive.py`` module so the animation and interactive layers stay
clearly distinct.

Tier classification:  Tier-2 domain functions (xr.Dataset-in / file-out).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

from oceanarray.config.unit_tables import unit_label
from oceanarray.plotters.helpers import tukey_smooth
from oceanarray.plotters.primitives import nice_colorbar_bounds


def animate_hodograph(
    ds: xr.Dataset,
    output_path: str | Path,
    u_var: str = "east_velocity",
    v_var: str = "north_velocity",
    lp_days: float = 4.0,
    smooth_hours: float = 3.0,
    frame_hours: float = 6.0,
    fps: int = 20,
    dpi: int = 100,
) -> Path | None:
    """Write an animated GIF of the smoothed hodograph drawing itself through time.

    Each frame advances by *frame_hours* of real deployment time and reveals
    the smoothed velocity trajectory accumulated up to that moment, so the
    viewer watches rotary/eddy structure build up through the record.

    Signal processing (applied once to the full record before animating):

    * **Raw panel** — *smooth_hours*-Tukey filter of the raw east/north signal.
    * **Eddy panel** — *lp_days*-day rolling-mean removed, then *smooth_hours*-
      Tukey filter to suppress instrument noise.

    A timestamp header on each frame shows the real date and time.

    Requires ``pillow`` (``pip install pillow``) as the GIF writer and
    ``scipy`` for the Tukey window.

    Parameters
    ----------
    ds : xr.Dataset
        Per-instrument dataset with east/north velocity variables and a
        ``time`` dimension.
    output_path : str or Path
        Destination file path; ``.gif`` extension recommended.
    u_var : str
        Eastward velocity variable name (m s⁻¹).
    v_var : str
        Northward velocity variable name (m s⁻¹).
    lp_days : float
        Low-pass window length in days for the eddy-component panel.
    smooth_hours : float
        Tukey smoothing window in hours applied to both panels.
    frame_hours : float
        Time step between frames in hours (default 6 h → one frame per
        quarter-day of deployment).
    fps : int
        Frames per second in the output GIF.
    dpi : int
        Resolution of each frame in dots per inch.

    Returns
    -------
    Path
        The resolved output path on success.
    None
        If *u_var* or *v_var* are absent from *ds*, or if the ``pillow``
        writer is unavailable.

    """
    import pandas as pd
    from matplotlib.animation import FuncAnimation

    output_path = Path(output_path)

    if u_var not in ds.data_vars or v_var not in ds.data_vars:
        return None

    instr_id = ds.attrs.get("id", "")
    east_raw = ds[u_var].values.astype(float).ravel()
    north_raw = ds[v_var].values.astype(float).ravel()
    t = ds["time"].values
    units = unit_label(ds[u_var].attrs.get("units", "m s-1"))

    # Sample interval (seconds)
    dt_s = (
        float(np.median(np.diff(t) / np.timedelta64(1, "s"))) if len(t) > 1 else 3600.0
    )

    # Tukey smoothing window length (samples)
    smooth_n = max(3, int(round(smooth_hours * 3600.0 / dt_s)))

    # LP window for eddy removal (rolling mean)
    lp_n = max(3, int(round(lp_days * 86400.0 / dt_s)))

    # Raw panel: Tukey-smoothed raw signal
    e_raw_sm = tukey_smooth(east_raw, smooth_n)
    n_raw_sm = tukey_smooth(north_raw, smooth_n)

    # Eddy panel: LP mean removed, then Tukey smoothed
    e_lp = (
        pd.Series(east_raw)
        .rolling(window=lp_n, min_periods=1, center=True)
        .mean()
        .values
    )
    n_lp = (
        pd.Series(north_raw)
        .rolling(window=lp_n, min_periods=1, center=True)
        .mean()
        .values
    )
    e_eddy_sm = tukey_smooth(east_raw - e_lp, smooth_n)
    n_eddy_sm = tukey_smooth(north_raw - n_lp, smooth_n)

    # Frame indices — one frame per frame_hours of deployment time
    step_n = max(1, int(round(frame_hours * 3600.0 / dt_s)))
    N = len(east_raw)
    frame_ends = np.arange(step_n, N + step_n, step_n, dtype=int)
    frame_ends = frame_ends[frame_ends <= N]
    if len(frame_ends) == 0 or frame_ends[-1] < N:
        frame_ends = np.append(frame_ends, N)
    n_frames = len(frame_ends)

    # Time colour: permanent fraction 0→1 per sample
    t_frac = np.linspace(0.0, 1.0, N)
    bounds = nice_colorbar_bounds(0.0, 1.0, n=10)
    norm = mcolors.BoundaryNorm(bounds, ncolors=256)
    cmap = plt.get_cmap("viridis")

    # Fixed symmetric axis limits from full smoothed data
    def _sym_lim(e: np.ndarray, n: np.ndarray) -> float:
        vals = np.concatenate([e[np.isfinite(e)], n[np.isfinite(n)]])
        return float(np.nanmax(np.abs(vals))) * 1.08 if vals.size > 0 else 1.0

    raw_lim = _sym_lim(e_raw_sm, n_raw_sm)
    eddy_lim = _sym_lim(e_eddy_sm, n_eddy_sm)

    # ------------------------------------------------------------------ Figure
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Fix the layout explicitly so the colorbar axes position is stable across
    # ax.cla() calls (tight_layout after colorbar creation causes misalignment).
    fig.subplots_adjust(left=0.07, right=0.86, top=0.88, bottom=0.11, wspace=0.35)

    # Colorbar on a fixed axes to the right of ax2 — position in figure coords
    cbar_ax = fig.add_axes([0.88, 0.13, 0.02, 0.70])
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cb = fig.colorbar(
        sm, cax=cbar_ax, label="Time (0 = start, 1 = end of record)", ticks=bounds
    )
    cb.ax.tick_params(labelsize=8)

    # Static instrument label + dynamic timestamp in the headroom above panels
    if instr_id:
        fig.text(0.45, 0.99, instr_id, ha="center", va="top", fontsize=9, color="#555")
    timestamp_text = fig.text(
        0.45, 0.94, "", ha="center", va="top", fontsize=11, fontweight="bold"
    )

    def _setup_ax(ax: plt.Axes, lim: float, title: str) -> None:
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_aspect("equal")
        ax.axhline(0, color="#bbb", lw=0.7, zorder=0)
        ax.axvline(0, color="#bbb", lw=0.7, zorder=0)
        ax.set_xlabel(f"East ({units})")
        ax.set_ylabel(f"North ({units})")
        ax.set_title(title)
        ax.grid(True, linestyle="--", linewidth=0.4, alpha=0.4)

    def _draw_frame(frame_idx: int) -> None:
        k = int(frame_ends[frame_idx])

        # Timestamp from the last sample in this frame
        time_str = pd.Timestamp(t[k - 1]).strftime("%Y-%m-%d %H:%M UTC")
        timestamp_text.set_text(time_str)

        panels = [
            (ax1, e_raw_sm, n_raw_sm, raw_lim, f"Raw ({smooth_hours:.0f}-h smoothed)"),
            (
                ax2,
                e_eddy_sm,
                n_eddy_sm,
                eddy_lim,
                f"Eddy ({lp_days:.0f}-day LP removed, {smooth_hours:.0f}-h smoothed)",
            ),
        ]
        for ax, e, n, lim, title in panels:
            ax.cla()
            _setup_ax(ax, lim, title)

            mask = np.isfinite(e[:k]) & np.isfinite(n[:k])
            if not mask.any():
                continue

            ax.scatter(
                e[:k][mask],
                n[:k][mask],
                c=t_frac[:k][mask],
                cmap=cmap,
                norm=norm,
                s=4,
                alpha=0.8,
                linewidths=0,
                marker="o",
                rasterized=True,
            )
            # Permanent start marker; moving end marker
            idx = np.where(mask)[0]
            ax.scatter(
                e[:k][idx[0]],
                n[:k][idx[0]],
                s=55,
                c="lime",
                zorder=5,
                marker="o",
                edgecolors="black",
                linewidths=0.6,
            )
            ax.scatter(
                e[:k][idx[-1]],
                n[:k][idx[-1]],
                s=65,
                c="red",
                zorder=5,
                marker="s",
                edgecolors="black",
                linewidths=0.6,
            )

    anim = FuncAnimation(
        fig, _draw_frame, frames=n_frames, interval=1000 // fps, blit=False
    )

    try:
        anim.save(str(output_path), writer="pillow", fps=fps, dpi=dpi)
    except Exception:  # noqa: BLE001  — broad catch at I/O boundary; caller checks return value
        plt.close(fig)
        return None

    plt.close(fig)
    return output_path
