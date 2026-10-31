"""Tier-2 domain wrappers for current/velocity instrument plots.

These functions accept xarray Datasets and delegate rendering to Tier-1
primitives in _primitives.py.  They understand oceanarray variable naming
conventions but do not serialise to base64 (that happens in report/_plots.py).

Pairs with :mod:`oceanarray.analysis.vector` for coordinate transformations.

Public draw_* functions (migrated from report/_plots.py):
- draw_instrument_rose: rose diagram grid for a single Aquadopp instrument.
- draw_rose_grid: grid of current roses for instruments in a stack dataset.
- draw_grid_rose: grid of current roses by pressure level for the grid report.
- draw_grid_trajectory: pseudo-Lagrangian trajectory by pressure for the grid report.
- draw_adcp_velocity: stacked colour panels for the ADCP per-instrument HTML page.
- draw_adcp_rose: current rose panels for an ADCP per-instrument report.
- draw_adcp_hodograph: two-depth hodograph for an ADCP per-instrument report.
- draw_grid_hodograph: two-depth hodograph for the grid report.
- draw_aquadopp_tilt_panels: |pitch|, |roll| and pressure tilt per Aquadopp for the stack report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from matplotlib.collections import LineCollection

from oceanarray import parameters as params
from oceanarray.analysis.vector import progressive_vector, xyz_to_enu_2d
from oceanarray.config.unit_tables import (
    unit_label,
    var_unit_label,
    warn_if_not_canonical,
)
from oceanarray.plotters.helpers import (
    _rose_ax,
    _velocity_panel_style,
    grid_despine,
    tukey_smooth,
)
from oceanarray.plotters.primitives import (
    colorbar_norm,
    date_axis,
    date_offset_left,
    figure_title,
    hodograph_panel,
    plot_title,
    plot_trajectory,
    square_axes_grid,
    square_limits,
    unit_colorbar,
    ytick_reserve_in,
)
from oceanarray.reports._vendored import design_tokens as report_tokens
from oceanarray.utilities import _nice_colorbar_bounds


def plot_temperature_trajectory(
    ds: xr.Dataset,
    u_var: str = "east_velocity",
    v_var: str = "north_velocity",
    temp_var: str = "temperature",
    title: str = "",
) -> object:
    """Lagrangian particle trajectory coloured per-segment by temperature.

    Integrates *u_var* and *v_var* (east/north velocity) over time using a
    forward Euler scheme.  NaN velocity values are treated as zero so the
    trajectory is not interrupted.

    The displacement is in metres; for long deployments the trajectory will
    drift far from origin and should be interpreted as a pseudo-Lagrangian
    tracer, not a real particle path.

    Note: the velocity integration step will move to ``oceanarray.tools``
    post-OdB so that it can be reused independently of plotting.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset containing east/north velocity and temperature variables.
    u_var : str
        Name of the eastward velocity variable (m s⁻¹).
    v_var : str
        Name of the northward velocity variable (m s⁻¹).
    temp_var : str
        Name of the temperature variable used for colouring.
    title : str
        Optional figure title; defaults to the dataset ``id`` attribute.

    Returns
    -------
    matplotlib.figure.Figure

    """
    # The km label assumes both velocity components in m/s integrated over dt in seconds.
    warn_if_not_canonical(ds[u_var], "m s-1", quantity="trajectory displacement (km)")
    warn_if_not_canonical(ds[v_var], "m s-1", quantity="trajectory displacement (km)")
    u = np.nan_to_num(ds[u_var].values, nan=0.0)
    v = np.nan_to_num(ds[v_var].values, nan=0.0)
    time = ds["time"].values

    dt = np.array(
        [(time[i] - time[i - 1]) / np.timedelta64(1, "s") for i in range(1, len(time))],
        dtype=float,
    )
    x = np.concatenate([[0.0], np.cumsum(u[:-1] * dt)]) / 1000.0
    y = np.concatenate([[0.0], np.cumsum(v[:-1] * dt)]) / 1000.0

    temp = ds[temp_var].values
    long_name = ds[temp_var].attrs.get("long_name", temp_var)
    # Pretty units from the registry (°C), not the file's CF attr (degC/degree_Celsius).
    units = params.vunit("temperature") or ds[temp_var].attrs.get("units", "")

    if not title:
        title = ds.attrs.get("id", "")

    return plot_trajectory(
        x,
        y,
        color_data=temp,
        cmap="coolwarm",
        xlabel="East displacement (km)",
        ylabel="North displacement (km)",
        colorbar_label=long_name,
        colorbar_unit=units,
        title=title,
    )


def plot_speed_boxplot(
    ds: xr.Dataset,
    speed_var: str = "current_speed",
    *,
    width_in: float = report_tokens.W_THIRD,
) -> object:
    """Boxplot of current speed with printed percentile statistics.

    Prints the 5th, 25th, 50th, 75th and 95th percentiles to stdout.
    NaN values are excluded before plotting.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset containing the speed variable.
    speed_var : str
        Name of the current speed variable.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure

    """
    import matplotlib.pyplot as plt

    speed = ds[speed_var].values.ravel()
    speed_clean = speed[~np.isnan(speed)]

    units = params.vunit("speed") or ds[speed_var].attrs.get("units", "")
    long_name = ds[speed_var].attrs.get("long_name", speed_var)
    ylabel = f"{long_name} ({units})" if units else long_name

    for p in [5, 25, 50, 75, 95]:
        val = np.percentile(speed_clean, p)
        print(f"  {p:2d}th percentile: {val:.4f} {units}")

    fig, ax = plt.subplots(figsize=(width_in, 3.5))
    bp = ax.boxplot(
        speed_clean,
        vert=True,
        patch_artist=True,
        widths=0.5,
        flierprops={"marker": ".", "markersize": 3, "alpha": 0.4},
    )
    bp["boxes"][0].set_facecolor("steelblue")
    bp["boxes"][0].set_alpha(0.7)
    ax.set_ylabel(ylabel)
    ax.set_xticks([])
    instr_id = ds.attrs.get("id", "")
    if instr_id:
        plot_title(ax, instr_id)
    grid_despine(ax, axis="y")
    fig.tight_layout()
    return fig


def plot_multi_aquadopp_trajectories(
    ds: xr.Dataset,
    u_var: str = "east_velocity",
    v_var: str = "north_velocity",
    temp_var: str = "temperature",
    instr_type_var: str = "instrument_type",
    serial_var: str = "serial",
    title: str = "",
    *,
    width_in: float = report_tokens.W_HALF,
) -> plt.Figure | None:
    """Multi-instrument Lagrangian trajectories for all Aquadopps, coloured by temperature.

    Each trajectory starts at the origin and is built by integrating the
    east/north velocity over time (Euler forward; NaN velocities set to zero).
    All trajectories share a single temperature colour scale so instruments can
    be compared directly.  End points are annotated with the instrument serial.

    Parameters
    ----------
    ds : xr.Dataset
        Stacked mooring dataset with shape (time, N_LEVELS).  Must contain
        *instr_type_var*, *serial_var*, *u_var*, *v_var*.
    u_var, v_var : str
        Eastward and northward velocity variables (m s⁻¹).
    temp_var : str
        Temperature variable for colouring; omitted if not present in ds.
    instr_type_var, serial_var : str
        Dimension-coordinate variable names identifying each instrument.
    title : str
        Optional figure title; falls back to the dataset ``id`` attribute.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure or None
        None if no Aquadopp instruments are found in the dataset.

    Notes
    -----
    Adapted from ``02_aqdp_ploter.py`` by L. Moscatel (lmoscat),
    Universitat de Barcelona.

    """
    instr_types = ds[instr_type_var].values
    serials = ds[serial_var].values
    aqd_idx = [i for i, t in enumerate(instr_types) if str(t).lower() == "aquadopp"]

    if not aqd_idx:
        return None

    time = ds["time"].values
    dt = np.array(
        [(time[j] - time[j - 1]) / np.timedelta64(1, "s") for j in range(1, len(time))],
        dtype=float,
    )

    has_temp = temp_var in ds.data_vars

    # The km label assumes both velocity components in m/s integrated over dt in seconds.
    warn_if_not_canonical(ds[u_var], "m s-1", quantity="trajectory displacement (km)")
    warn_if_not_canonical(ds[v_var], "m s-1", quantity="trajectory displacement (km)")

    # Build trajectories and collect temperature data for shared norm
    trajs = []
    all_temps: list = []
    for i in aqd_idx:
        u = np.nan_to_num(ds[u_var].values[:, i], nan=0.0)
        v = np.nan_to_num(ds[v_var].values[:, i], nan=0.0)
        x = np.concatenate([[0.0], np.cumsum(u[:-1] * dt)]) / 1000.0
        y = np.concatenate([[0.0], np.cumsum(v[:-1] * dt)]) / 1000.0
        temp = ds[temp_var].values[:, i] if has_temp else None
        trajs.append((i, x, y, temp))
        if temp is not None:
            all_temps.append(temp[np.isfinite(temp)])

    cmap = plt.get_cmap("coolwarm")
    if has_temp and all_temps:
        flat = np.concatenate(all_temps)
        _tmin = float(np.nanmin(flat))
        _tmax = float(np.nanmax(flat))
        _bounds = _nice_colorbar_bounds(_tmin, _tmax, n=20)
        # Enforce minimum tick spacing of 0.5 °C so labels don't crowd
        if len(_bounds) > 1 and (_bounds[1] - _bounds[0]) < 0.5:
            _n_t = max(2, int((_tmax - _tmin) / 0.5))
            _bounds = _nice_colorbar_bounds(_tmin, _tmax, n=_n_t)
    else:
        _bounds = _nice_colorbar_bounds(0.0, 1.0, n=20)
    norm: mcolors.BoundaryNorm = mcolors.BoundaryNorm(_bounds, ncolors=256)

    _all_x = np.concatenate([x for _, x, _, _ in trajs])
    _all_y = np.concatenate([y for _, _, y, _ in trajs])
    fig, axes, cax = square_axes_grid(
        width_in, 1, 1, colorbar=has_temp, left_in=ytick_reserve_in(_all_y)
    )
    ax = axes[0, 0]

    for instr_i, x, y, temp in trajs:
        serial = str(serials[instr_i])

        if has_temp and temp is not None:
            points = np.array([x, y]).T.reshape(-1, 1, 2)
            segments = np.concatenate([points[:-1], points[1:]], axis=1)
            lc = LineCollection(
                segments, cmap=cmap, norm=norm, linewidth=1.5, alpha=0.85
            )
            lc.set_array(temp[:-1])
            ax.add_collection(lc)
            _tail = temp[-max(1, len(temp) // 20) :]
            _tail_finite = _tail[np.isfinite(_tail)]
            if _tail_finite.size == 0:
                _tail_finite = temp[np.isfinite(temp)]
            end_color = (
                cmap(norm(float(_tail_finite.mean())))
                if _tail_finite.size > 0
                else cmap(0.5)
            )
        else:
            ax.plot(x, y, linewidth=1.5, alpha=0.85)
            end_color = "steelblue"

        # End-point marker and label
        ax.plot(
            x[-1],
            y[-1],
            "s",
            color=end_color,
            markersize=6,
            zorder=5,
            markeredgecolor="white",
            markeredgewidth=0.5,
        )
        ax.annotate(
            f"{serial}",
            xy=(x[-1], y[-1]),
            xytext=(6, 3),
            textcoords="offset points",
            color="black",
            ha="left",
            va="bottom",
        )

    # Origin marker (all trajectories share the same start)
    ax.plot(0, 0, "o", color="black", markersize=7, zorder=6, label="Start (all)")
    ax.legend(loc="upper left")

    if has_temp and cax is not None:
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        # Pretty units from the registry (°C), not the file's CF attr.
        units = params.vunit("temperature") or ds[temp_var].attrs.get("units", "")
        unit_colorbar(cax, sm, unit=units, ticks=_bounds[::2])

    # Square the axes to the union of all trajectories so equal aspect fills the
    # panel and the shared colorbar height stays matched.
    xlim, ylim = square_limits(_all_x, _all_y)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_xlabel("East displacement (km)")
    ax.set_ylabel("North displacement (km)")
    ax.axhline(0, color="k", linewidth=0.5, linestyle="--", alpha=0.4)
    ax.axvline(0, color="k", linewidth=0.5, linestyle="--", alpha=0.4)
    ax.set_aspect("equal", adjustable="datalim")
    grid_despine(ax)
    if not title:
        title = ds.attrs.get("id", "")
    if title:
        plot_title(ax, title)
    return fig


def plot_hodograph(
    ds: xr.Dataset,
    u_var: str = "east_velocity",
    v_var: str = "north_velocity",
    lp_days: float = 4.0,
    smooth_hours: float = 3.0,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure:
    """Two-panel hodograph: Tukey-smoothed raw and eddy-only, coloured by time.

    Panel 1: *smooth_hours*-Tukey-smoothed raw east-vs-north velocity.
    Panel 2: eddy component — raw minus a *lp_days*-day rolling-mean low-pass,
    then *smooth_hours*-Tukey smoothed to suppress instrument noise.

    Points are coloured by fractional time through the record (0 = start,
    1 = end) using a discrete viridis colorbar so the temporal evolution of
    rotary motion can be followed by eye.  The figure title shows the
    instrument id from ``ds.attrs["id"]`` when available.

    If *u_var* or *v_var* are absent, a placeholder figure is returned with
    an explanatory message — the caller always receives a renderable image.

    Parameters
    ----------
    ds : xr.Dataset
        Per-instrument dataset containing east and north velocity variables.
    u_var : str
        Eastward velocity variable name (m s⁻¹).
    v_var : str
        Northward velocity variable name (m s⁻¹).
    lp_days : float
        Low-pass window length in days for the eddy-component panel.
    smooth_hours : float
        Tukey smoothing window in hours applied to both panels.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure

    """
    import pandas as pd

    instr_id = ds.attrs.get("id", "")
    # Deterministic square-panel layout + one shared time colorbar — the same
    # primitive used by the ADCP and grid hodographs, so all three render
    # identically and the colorbar height always matches the plotted square.
    fig, axes, cax = square_axes_grid(
        width_in, 1, 2, top_pad_in=0.3 if instr_id else 0.0
    )
    ax_raw, ax_eddy = axes[0, 0], axes[0, 1]
    if instr_id:
        figure_title(fig, instr_id)

    if u_var not in ds.data_vars or v_var not in ds.data_vars:
        for ax in axes.ravel():
            ax.set_visible(False)
        if cax is not None:
            cax.set_visible(False)
        fig.text(
            0.5,
            0.5,
            "No east/north velocities",
            ha="center",
            va="center",
            fontsize=report_tokens.ANNOT_FS,
            color="#95a5a6",
        )
        return fig

    east_raw = ds[u_var].values.astype(float).ravel()
    north_raw = ds[v_var].values.astype(float).ravel()
    t = ds["time"].values
    units = params.vunit("east_velocity") or unit_label(
        ds[u_var].attrs.get("units", "m s-1")
    )

    dt_s = (
        float(np.median(np.diff(t) / np.timedelta64(1, "s"))) if len(t) > 1 else 3600.0
    )
    smooth_n = max(3, int(round(smooth_hours * 3600.0 / dt_s)))
    lp_n = max(3, int(round(lp_days * 86400.0 / dt_s)))

    # Raw panel: Tukey-smoothed raw signal
    east = tukey_smooth(east_raw, smooth_n)
    north = tukey_smooth(north_raw, smooth_n)

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
    e_eddy = tukey_smooth(east_raw - e_lp, smooth_n)
    n_eddy = tukey_smooth(north_raw - n_lp, smooth_n)

    # Time fraction 0→1 for colour encoding
    t_frac = np.linspace(0.0, 1.0, len(east_raw))

    def _panel(ax: plt.Axes, e: np.ndarray, n: np.ndarray, title: str) -> Any:
        mask = np.isfinite(e) & np.isfinite(n)
        if mask.sum() < 2:
            ax.text(
                0.5, 0.5, "No data", transform=ax.transAxes, ha="center", va="center"
            )
            plot_title(ax, title)
            return None
        return hodograph_panel(ax, e[mask], n[mask], t_frac[mask], title, units)

    sm = _panel(ax_raw, east, north, f"Raw ({smooth_hours:.0f}-h smoothed)")
    sm_eddy = _panel(
        ax_eddy,
        e_eddy,
        n_eddy,
        f"Eddy ({lp_days:.0f}-day LP removed, {smooth_hours:.0f}-h smoothed)",
    )
    sm = sm or sm_eddy

    if sm is not None:
        unit_colorbar(cax, sm, ticks=np.array([0.0, 1.0]), ticklabels=["start", "end"])
    elif cax is not None:
        cax.set_visible(False)
    return fig


def plot_aquadopp_speed_profile(
    ds: xr.Dataset,
    speed_var: str = "current_speed",
    u_var: str = "east_velocity",
    v_var: str = "north_velocity",
    instr_type_var: str = "instrument_type",
    serial_var: str = "serial",
    hab_var: str = "hab",
    *,
    width_in: float = report_tokens.W_HALF,
) -> plt.Figure | None:
    """Horizontal speed boxplots for all Aquadopps, one per instrument at its HAB.

    X-axis: current speed.  Y-axis: height above bottom (m).  All Aquadopps
    appear on the same axes so the speed distribution can be compared across
    depths.

    If *speed_var* is not present in *ds*, speed is computed from *u_var* and
    *v_var* as ``sqrt(u² + v²)``.

    Parameters
    ----------
    ds : xr.Dataset
        Stacked mooring dataset with shape (time, N_LEVELS).
    speed_var : str
        Preferred speed variable name; computed from u/v if absent.
    u_var, v_var : str
        Used to compute speed when *speed_var* is not present.
    instr_type_var, serial_var, hab_var : str
        Dimension-coordinate variable names.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure or None
        None if no Aquadopp instruments are found.

    Notes
    -----
    Adapted from ``02_aqdp_ploter.py`` by L. Moscatel (lmoscat),
    Universitat de Barcelona.

    """
    instr_types = ds[instr_type_var].values
    serials = ds[serial_var].values
    habs = ds[hab_var].values
    aqd_idx = [i for i, t in enumerate(instr_types) if str(t).lower() == "aquadopp"]

    if not aqd_idx:
        return None

    # Collect per-instrument speed arrays
    records = []
    for i in aqd_idx:
        if speed_var in ds.data_vars:
            spd = ds[speed_var].values[:, i].ravel()
        elif u_var in ds.data_vars and v_var in ds.data_vars:
            u = ds[u_var].values[:, i]
            v = ds[v_var].values[:, i]
            spd = np.sqrt(u**2 + v**2)
        else:
            continue
        spd_clean = spd[np.isfinite(spd)]
        records.append((float(habs[i]), str(serials[i]), spd_clean))

    if not records:
        return None

    hab_vals = [r[0] for r in records]
    hab_range = max(hab_vals) - min(hab_vals) if len(hab_vals) > 1 else 10.0
    box_width = max(2.0, hab_range * 0.06)

    fig, ax = plt.subplots(figsize=(width_in, max(3, len(records) * 0.7 + 1)))

    for hab, serial, spd_clean in records:
        bp = ax.boxplot(
            spd_clean,
            vert=False,
            positions=[hab],
            widths=box_width,
            patch_artist=True,
            flierprops={
                "marker": ".",
                "markersize": 2,
                "alpha": 0.3,
                "color": "steelblue",
            },
            medianprops={"color": "navy", "linewidth": 1.5},
            manage_ticks=False,
        )
        bp["boxes"][0].set_facecolor("steelblue")
        bp["boxes"][0].set_alpha(0.6)

        # Serial label to the right of the actual whisker end (not the theoretical cap)
        whisker_end = bp["whiskers"][1].get_xdata()[-1]
        ax.text(
            whisker_end * 1.02,
            hab,
            f"s/n {serial}",
            va="center",
            color="#333",
        )

    units = params.vunit("speed") or (
        ds[speed_var].attrs.get("units", "m/s") if speed_var in ds.data_vars else "m/s"
    )
    ax.set_xlabel(f"Current speed ({units})")
    ax.set_ylabel("Height above bottom (m)")
    ax.set_xlim(left=0)
    ax.set_ylim(min(hab_vals) - box_width * 1.5, max(hab_vals) + box_width * 1.5)
    grid_despine(ax, axis="x")
    fig.tight_layout()
    return fig


def plot_adcp_trajectories(
    ds: xr.Dataset,
    u_var: str = "east_velocity",
    v_var: str = "north_velocity",
    instr_type_var: str = "instrument_type",
    hab_var: str = "hab",
    seabed_qc_var: str = "seabed_qc",
    percent_good_qc_var: str = "percent_good_qc",
    *,
    width_in: float = report_tokens.W_HALF,
) -> plt.Figure | None:
    """Lagrangian per-bin trajectories for ADCP data, coloured by HAB.

    Each depth bin integrated by Euler-forward from the origin.  Bins that are
    entirely below the seabed (every seabed_qc flag bad) are silently omitted.  QC
    masking is applied before integration so bad pings are treated as zero
    velocity (do not accumulate spurious displacement).

    Parameters
    ----------
    ds : xr.Dataset
        Stacked mooring dataset.  ADCP bins are identified by
        ``instrument_type == "ADCP"``.
    u_var, v_var : str
        Eastward and northward velocity variable names (m s⁻¹).
    instr_type_var, hab_var : str
        Coordinate variable names.
    seabed_qc_var : str
        QC variable for seabed proximity; bins with every flag bad (``QC_BAD_FLAGS``) are skipped.
    percent_good_qc_var : str
        Ping-quality QC; timesteps with a bad flag (``QC_BAD_FLAGS``) are zeroed before integration.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure or None
        None if no ADCP bins are found.

    """
    if u_var not in ds.data_vars or v_var not in ds.data_vars:
        return None

    instr_types = ds[instr_type_var].values
    habs = ds[hab_var].values
    adcp_idx = [i for i, t in enumerate(instr_types) if str(t).upper() == "ADCP"]

    if not adcp_idx:
        return None

    time = ds["time"].values
    dt = np.array(
        [(time[j] - time[j - 1]) / np.timedelta64(1, "s") for j in range(1, len(time))],
        dtype=float,
    )

    # Build per-bin trajectories with seabed + percent_good masking
    trajs: list = []  # (hab, x, y)
    hab_all: list = []
    for i in adcp_idx:
        u = ds[u_var].values[:, i].copy().astype(float)
        v = ds[v_var].values[:, i].copy().astype(float)

        # Mask by seabed proximity
        if seabed_qc_var in ds.data_vars:
            sqc = ds[seabed_qc_var].values[:, i]
            # Skip bins that are always below the seabed
            if np.all(np.isin(sqc, params.QC_BAD_FLAGS)):
                continue
            u[np.isin(sqc, params.QC_BAD_FLAGS)] = np.nan
            v[np.isin(sqc, params.QC_BAD_FLAGS)] = np.nan

        # Mask by ping quality
        if percent_good_qc_var in ds.data_vars:
            pqc = ds[percent_good_qc_var].values[:, i]
            u[np.isin(pqc, params.QC_BAD_FLAGS)] = np.nan
            v[np.isin(pqc, params.QC_BAD_FLAGS)] = np.nan

        # NaN → 0 for integration (no displacement during missing pings)
        x = (
            np.concatenate([[0.0], np.cumsum(np.nan_to_num(u[:-1], nan=0.0) * dt)])
            / 1000.0
        )
        y = (
            np.concatenate([[0.0], np.cumsum(np.nan_to_num(v[:-1], nan=0.0) * dt)])
            / 1000.0
        )
        trajs.append((float(habs[i]), x, y))
        hab_all.append(float(habs[i]))

    if not trajs:
        return None

    hab_min, hab_max = min(hab_all), max(hab_all)
    cmap = plt.get_cmap("viridis")
    _bounds = _nice_colorbar_bounds(hab_min, hab_max, n=20)
    # Enforce minimum tick spacing of 50 m so labels don't crowd
    if len(_bounds) > 1 and (_bounds[1] - _bounds[0]) < 50.0:
        _n_hab = max(2, int((hab_max - hab_min) / 50.0))
        _bounds = _nice_colorbar_bounds(hab_min, hab_max, n=_n_hab)
    norm: mcolors.BoundaryNorm = mcolors.BoundaryNorm(_bounds, ncolors=256)

    # Half width — shown in a 50% flex column beside the Aquadopp trajectory
    # (see stack.html), matching plot_multi_aquadopp_trajectories.
    _all_x = np.concatenate([x for _, x, _ in trajs])
    _all_y = np.concatenate([y for _, _, y in trajs])
    fig, axes, cax = square_axes_grid(width_in, 1, 1, left_in=ytick_reserve_in(_all_y))
    ax = axes[0, 0]

    for hab, x, y in trajs:
        points = np.array([x, y]).T.reshape(-1, 1, 2)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)
        lc = LineCollection(segments, cmap=cmap, norm=norm, linewidth=1.0, alpha=0.6)
        lc.set_array(np.full(len(segments), hab))
        ax.add_collection(lc)

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    unit_colorbar(cax, sm, unit="m", ticks=_bounds[::2])

    ax.plot(0, 0, "o", color="black", markersize=7, zorder=6, label="Start")
    ax.legend(loc="upper left")
    xlim, ylim = square_limits(_all_x, _all_y)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_xlabel("East displacement (km)")
    ax.set_ylabel("North displacement (km)")
    ax.axhline(0, color="k", linewidth=0.5, linestyle="--", alpha=0.4)
    ax.axvline(0, color="k", linewidth=0.5, linestyle="--", alpha=0.4)
    ax.set_aspect("equal", adjustable="datalim")
    grid_despine(ax)
    plot_title(ax, "ADCP bins coloured by HAB")
    return fig


def draw_instrument_rose(
    nc_path: Path,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Rose diagram grid for a single Aquadopp instrument; return Figure or None.

    Loads the stage-3 NetCDF at *nc_path*, builds one polar panel per available
    velocity QC tier (ENU magnetic, ENU good, suspect, fail), and returns the
    Figure.  Returns ``None`` when no velocity data are found.

    Parameters
    ----------
    nc_path : Path
        Path to a stage-3 NetCDF file for a single Aquadopp instrument.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure or None

    """
    import matplotlib.pyplot as plt
    import xarray as xr

    ds = xr.open_dataset(nc_path, decode_timedelta=False).load()
    ds.close()

    panels = []

    has_xyz = all(f"velocity_{c}" in ds.data_vars for c in ("x", "y", "z"))
    has_orientation = all(v in ds.data_vars for v in ("heading", "pitch", "roll"))
    decl = ds.attrs.get("magnetic_declination")
    if has_xyz and has_orientation and decl is not None:
        vx_r = ds["velocity_x"].values.astype(float)
        vy_r = ds["velocity_y"].values.astype(float)
        vz_r = ds["velocity_z"].values.astype(float)
        hdg = ds["heading"].values.astype(float)
        pch = ds["pitch"].values.astype(float)
        rll = ds["roll"].values.astype(float)
        e_mag, n_mag = xyz_to_enu_2d(vx_r, vy_r, vz_r, hdg, pch, rll, 0.0)
        if np.any(np.isfinite(e_mag)):
            panels.append((e_mag, n_mag, "ENU magnetic\n(decl = 0°)", "Purples"))

    if "east_velocity" in ds.data_vars and "north_velocity" in ds.data_vars:
        e_all = ds["east_velocity"].values.astype(float)
        n_all = ds["north_velocity"].values.astype(float)
        qc = (
            ds["east_velocity_qc"].values.astype(int)
            if "east_velocity_qc" in ds.data_vars
            else np.ones(len(e_all), dtype=int)
        )

        def _masked(flag_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            e = e_all.copy()
            n = n_all.copy()
            e[~flag_mask] = np.nan
            n[~flag_mask] = np.nan
            return e, n

        # Every flag but 3, 4 and 9 (params.QC_BAD_FLAGS) is usable: good (1),
        # probably good (2), unknown (0), nominal (7) and interpolated (8).
        good_mask = ~np.isin(qc, params.QC_BAD_FLAGS)
        susp_mask = qc == 3
        fail_mask = qc == 4

        decl_str = f"{float(decl):+.1f}°" if decl is not None else "?"
        enu_title = f"ENU true (decl = {decl_str})\nflags 0–2, 7, 8 (usable)"
        if np.any(np.isfinite(e_all[good_mask])):
            panels.append((*_masked(good_mask), enu_title, "Blues"))
        if np.any(np.isfinite(e_all[susp_mask])):
            panels.append((*_masked(susp_mask), "ENU — suspect\n(flag 3)", "Oranges"))
        if np.any(np.isfinite(e_all[fail_mask])):
            panels.append((*_masked(fail_mask), "ENU — fail\n(flag 4)", "Reds"))

    if not panels:
        return None

    ncols = len(panels)
    # Each rose is a circle (polar axes keep a circular aspect).  The figure keeps the
    # full slot width so the PNG is displayed at 100% and the browser does not rescale
    # it (which shrinks the panel fonts).  Row height is one rose-diameter per column;
    # a lone rose (ncols == 1) uses the same third-slot row height and is centred in a
    # narrow box below (see subplots_adjust) so it is a small centred circle with its
    # 2-line title clear, not a full-slot-width square.
    lone = ncols == 1
    per_rose = width_in / 3.0 if lone else width_in / ncols
    fig, axs = plt.subplots(
        1,
        ncols,
        figsize=(width_in, per_rose + 0.4),
        subplot_kw={"projection": "polar"},
        squeeze=False,
    )
    for ax, (east, north, title, cmap) in zip(axs[0], panels, strict=False):
        _rose_ax(ax, east, north, title=title, cmap=cmap)

    # Polar figures skip the encoder's tight_layout, so set panel spacing here.  A lone
    # rose is centred in a box ~a fifth of the width so the circle is width-limited
    # (like a 3-panel cell), leaving vertical room for its 2-line title; without this
    # the circle fills the short figure's height and the title's first line clips.
    # Multi-panel rows get wspace for room left-to-right.
    if lone:
        fig.subplots_adjust(left=0.39, right=0.61)
    else:
        fig.subplots_adjust(wspace=0.5)
    return fig


def draw_rose_grid(
    ds: xr.Dataset,
    serial_list: list,
    *,
    width_in: float = report_tokens.W_FULL,
) -> tuple[plt.Figure, int] | None:
    """Grid of current roses (max 4 per row) for instruments with ENU velocity data.

    Parameters
    ----------
    ds : xr.Dataset
        Stack dataset with ``east_velocity`` and ``north_velocity``.
    serial_list : list
        Serial numbers corresponding to the instrument axis of the velocity arrays.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    tuple of (plt.Figure, int) or None
        Figure and the number of rose panels rendered, or None if no velocity data.

    """
    import math

    import matplotlib.pyplot as plt

    if "east_velocity" not in ds.data_vars or "north_velocity" not in ds.data_vars:
        return None

    east_all = ds["east_velocity"].values.copy()
    north_all = ds["north_velocity"].values.copy()

    # Mask suspect/bad data from all available QC variables before plotting roses.
    # seabed_qc handles ADCP bins below the seafloor; percent_good_qc and
    # error_velocity_qc handle low-quality ADCP pings.  A flag in QC_BAD_FLAGS (3, 4, 9) is
    # treated as invalid; nominal (7) and interpolated (8) are kept.
    for _qc_var in (
        "east_velocity_qc",
        "seabed_qc",
        "percent_good_qc",
        "error_velocity_qc",
    ):
        if _qc_var in ds.data_vars:
            _qc = ds[_qc_var].values
            if _qc.shape == east_all.shape:
                east_all[np.isin(_qc, params.QC_BAD_FLAGS)] = np.nan
                north_all[np.isin(_qc, params.QC_BAD_FLAGS)] = np.nan

    has_vel = [np.any(np.isfinite(east_all[:, i])) for i in range(east_all.shape[1])]
    hab_vals = ds.coords["hab"].values if "hab" in ds.coords else None

    # Split by instrument type: ADCP bins are capped at 4 per serial (top, 2 mid,
    # bottom by HAB), single-point instruments (Aquadopp, etc.) keep all.
    # The variable may be "instrument_type" (mooring_level) or "instrument" (the stack).
    _type_var = (
        "instrument_type"
        if "instrument_type" in ds
        else ("instrument" if "instrument" in ds else None)
    )
    instr_type_vals = ds[_type_var].values if _type_var is not None else None
    _all_cand = [i for i in range(len(serial_list)) if i < len(has_vel) and has_vel[i]]

    aqd_idx: list[int] = []
    if instr_type_vals is not None:
        # Non-ADCP: all levels with velocity
        aqd_idx = [i for i in _all_cand if str(instr_type_vals[i]).upper() != "ADCP"]

        # ADCP: group by serial, select ≤4 representative bins by HAB
        from collections import defaultdict

        adcp_by_serial: dict[str, list[int]] = defaultdict(list)
        for i in _all_cand:
            if str(instr_type_vals[i]).upper() == "ADCP":
                ser = str(serial_list[i]) if i < len(serial_list) else "?"
                adcp_by_serial[ser].append(i)

        _ADCP_PCTS = [0, 33, 67, 100]
        for _idxs in adcp_by_serial.values():
            # Sort by HAB so percentile picks are spatially meaningful
            if hab_vals is not None:
                _idxs = sorted(_idxs, key=lambda k: hab_vals[k])
            n_b = len(_idxs)
            selected: list[int] = []
            for pct in _ADCP_PCTS:
                k = int(round(pct / 100 * (n_b - 1)))
                k = max(0, min(n_b - 1, k))
                if _idxs[k] not in selected:
                    selected.append(_idxs[k])
            aqd_idx.extend(selected)
    else:
        aqd_idx = _all_cand

    n = len(aqd_idx)
    if n == 0:
        return None

    # Always a 4-wide grid; vary the number of rows and render at full width so
    # every rose is the same size regardless of instrument count (empty trailing
    # cells are hidden below).  Row height tracks the ¼-width cell so roses stay
    # square-ish.
    ncols = 4
    nrows = math.ceil(n / ncols)

    _fig_h = nrows * (width_in / ncols + 0.65)
    fig, axs = plt.subplots(
        nrows,
        ncols,
        figsize=(width_in, _fig_h),
        subplot_kw={"projection": "polar"},
        squeeze=False,
    )
    # Trim the outer left/right margins and the inter-panel gap (the tucked-in
    # N/E/S/W labels no longer need the wide gap).  Also trim the top/bottom
    # margins to a small fixed inch reserve: the matplotlib defaults (0.88/0.11)
    # left ~1-2 rows of whitespace above and below on a tall grid (Eleanor
    # 2026-08-18).  Encoder skips tight_layout for polar figures, so set the
    # margins explicitly.
    fig.subplots_adjust(
        left=0.05,
        right=0.95,
        wspace=0.4,
        hspace=0.45,
        top=1 - 0.4 / _fig_h,
        bottom=0.2 / _fig_h,
    )
    axs_flat = axs.flatten()

    for plot_i, instr_i in enumerate(aqd_idx):
        serial = serial_list[instr_i] if instr_i < len(serial_list) else "?"
        if hab_vals is not None and instr_i < len(hab_vals):
            title = f"{serial} ({hab_vals[instr_i]:.0f} m)"
        else:
            title = str(serial)
        _rose_ax(
            axs_flat[plot_i], east_all[:, instr_i], north_all[:, instr_i], title=title
        )

    for k in range(n, len(axs_flat)):
        axs_flat[k].set_visible(False)

    return fig, n


def draw_grid_rose(
    ds: xr.Dataset,
    max_roses: int = 4,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Grid of current roses, one per pressure level, for the grid report.

    Shows up to *max_roses* pressure levels (at most 1/5th of valid levels,
    capped at 4), each labelled with its pressure (dbar).  Levels with no finite
    ENU velocity are skipped.  Returns ``None`` when east/north velocity are
    absent or all-NaN.

    Parameters
    ----------
    ds : xr.Dataset
        Gridded dataset with dimensions ``(time, pressure)`` containing
        ``east_velocity`` and ``north_velocity`` in m s⁻¹.
    max_roses : int
        Maximum number of rose panels to draw (default 4).
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None

    """
    import math

    import matplotlib.pyplot as plt

    if "east_velocity" not in ds.data_vars or "north_velocity" not in ds.data_vars:
        return None

    east_all = ds["east_velocity"].values.copy()  # (time, pressure)
    north_all = ds["north_velocity"].values.copy()

    for _qc_var in ("east_velocity_qc", "north_velocity_qc"):
        if _qc_var in ds.data_vars:
            _qc = ds[_qc_var].values
            if _qc.shape == east_all.shape:
                east_all[np.isin(_qc, params.QC_BAD_FLAGS)] = np.nan
                north_all[np.isin(_qc, params.QC_BAD_FLAGS)] = np.nan

    pressure = ds["pressure"].values  # 1-D (n_levels,)
    valid_idx = [k for k in range(len(pressure)) if np.any(np.isfinite(east_all[:, k]))]
    if not valid_idx:
        return None

    # Subsample: min(max_roses, max(1, n_valid // 5)) — same rule as rotary spectrum
    n_select = min(max_roses, max(1, len(valid_idx) // 5))
    if len(valid_idx) > n_select:
        step = len(valid_idx) / n_select
        valid_idx = [valid_idx[int(round(i * step))] for i in range(n_select)]

    n = len(valid_idx)
    ncols = min(n, 4)
    nrows = math.ceil(n / ncols)

    fig, axs = plt.subplots(
        nrows,
        ncols,
        figsize=(width_in, nrows * 3.2),
        subplot_kw={"projection": "polar"},
        squeeze=False,
    )
    # Match the instrument-page rose left-right spacing (polar → no tight_layout).
    fig.subplots_adjust(wspace=0.5)
    axs_flat = axs.flatten()

    for plot_i, k in enumerate(valid_idx):
        title = f"{int(pressure[k])} dbar"
        _rose_ax(axs_flat[plot_i], east_all[:, k], north_all[:, k], title=title)

    for k in range(n, len(axs_flat)):
        axs_flat[k].set_visible(False)

    return fig


def draw_grid_trajectory(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_HALF,
) -> plt.Figure | None:
    """Pseudo-Lagrangian current-vector integral by pressure level for the grid report.

    For each pressure level, integrates east and north velocity over time using
    the Euler forward method to produce a cumulative displacement trajectory from
    the origin (0, 0).  Returns ``None`` when east/north velocity are absent or
    all-NaN.

    Parameters
    ----------
    ds : xr.Dataset
        Gridded dataset with dimensions ``(time, pressure)``, containing
        ``east_velocity`` and ``north_velocity`` in m s⁻¹.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None

    """
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    if "east_velocity" not in ds.data_vars or "north_velocity" not in ds.data_vars:
        return None

    pressure = ds["pressure"].values  # 1-D (n_levels,)
    time = ds["time"].values
    dt = np.array(
        [(time[j] - time[j - 1]) / np.timedelta64(1, "s") for j in range(1, len(time))],
        dtype=float,
    )

    east = ds["east_velocity"].values.copy()  # (time, pressure)
    north = ds["north_velocity"].values.copy()

    # Apply QC masking before integration
    for _qv, _dv in (("east_velocity_qc", east), ("north_velocity_qc", north)):
        if _qv in ds.data_vars:
            _qc = ds[_qv].values
            _dv[np.isin(_qc, params.QC_BAD_FLAGS)] = np.nan

    # Build one trajectory per pressure level; skip levels with all-NaN velocity
    trajs = progressive_vector(east, north, dt, pressure)

    if not trajs:
        return None

    p_vals = [t[0] for t in trajs]
    _bounds, norm = colorbar_norm(vmin=min(p_vals), vmax=max(p_vals))
    cmap = plt.get_cmap("viridis_r")  # shallow (low p) → light; deep → dark

    _all_x = np.concatenate([x for _, x, _ in trajs])
    _all_y = np.concatenate([y for _, _, y in trajs])
    fig, axes, cax = square_axes_grid(width_in, 1, 1, left_in=ytick_reserve_in(_all_y))
    ax = axes[0, 0]

    for p_val, x, y in trajs:
        points = np.array([x, y]).T.reshape(-1, 1, 2)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)
        lc = LineCollection(segments, cmap=cmap, norm=norm, linewidth=0.8, alpha=0.7)
        lc.set_array(np.full(len(segments), p_val))
        ax.add_collection(lc)

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    unit_colorbar(cax, sm, unit=params.vunit("pressure"), ticks=_bounds[::2])

    ax.plot(0, 0, "o", color="black", markersize=6, zorder=6, label="Start")
    ax.legend(loc="upper left")
    xlim, ylim = square_limits(_all_x, _all_y)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_xlabel("East displacement (km)")
    ax.set_ylabel("North displacement (km)")
    ax.axhline(0, color="k", linewidth=0.5, linestyle="--", alpha=0.4)
    ax.axvline(0, color="k", linewidth=0.5, linestyle="--", alpha=0.4)
    ax.set_aspect("equal", adjustable="datalim")
    grid_despine(ax)
    return fig


def draw_adcp_velocity(
    nc_path: str,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Stacked colour panels for the ADCP per-instrument HTML report page; return a Figure.

    Reads the stage-3 NetCDF file at *nc_path* and produces a multi-panel
    time × range pcolormesh figure.

    **Coordinate convention**: velocities are in geographic ENU (East–North–Up), after
    magnetic declination correction applied in stage 3.  Positive east = rightward facing
    north; positive north = toward True North.

    **Direction convention** (``current_direction`` panel): direction *toward which* the
    water flows, clockwise from True North (0° = northward, 90° = eastward).  This is
    the oceanographic convention, opposite to meteorological "direction from".
    Computed as ``atan2(east, north) mod 360``.

    Panels rendered for each variable present in the file:

    ====================  ================================  =====================
    Variable              Label                             Colormap
    ====================  ================================  =====================
    east_velocity         East velocity (m s⁻¹)             Spectral_r (div)
    north_velocity        North velocity (m s⁻¹)            Spectral_r (div)
    up_velocity           Up velocity (m s⁻¹)               Spectral_r (div)
    error_velocity        Error velocity (m s⁻¹)            Spectral_r (div)
    current_speed         Current speed (m s⁻¹)             plasma (seq, 0→98th)
    current_direction     Current direction (°T)            hsv (cyclic, 0–360°)
    bin_pressure          Bin pressure (dbar)               viridis (seq)
    ====================  ================================  =====================

    Diverging panels (east/north/up/error) share symmetric colormap bounds set to
    ±max(``|2nd pctile|``, ``|98th pctile|``) of all finite ENU velocity values.

    Bins flagged at or below the seabed (``seabed_qc`` in ``QC_BAD_FLAGS``) are masked to NaN.
    Y-axis (range coordinate) is inverted for downward-looking instruments (pressure
    increases into the page); non-inverted for upward-looking.

    Parameters
    ----------
    nc_path : str
        Path to a stage-3 NetCDF file for a single ADCP instrument.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None
        Figure, or None if no velocity data are present or range coordinate is absent.

    """
    import matplotlib.pyplot as plt
    import xarray as xr

    # (varname, label, panel_type)  — panel_type: "div" | "seq" | "cyc"
    _COMPONENTS = [
        ("east_velocity", f"East velocity ({unit_label('m s-1')})", "div"),
        ("north_velocity", f"North velocity ({unit_label('m s-1')})", "div"),
        ("up_velocity", f"Up velocity ({unit_label('m s-1')})", "div"),
        ("error_velocity", f"Error velocity ({unit_label('m s-1')})", "div"),
        ("current_speed", f"Current speed ({unit_label('m s-1')})", "seq"),
        ("current_direction", f"Current direction ({unit_label('degrees')}T)", "cyc"),
        ("bin_pressure", f"Bin pressure ({unit_label('dbar')})", "seq"),
    ]

    with xr.open_dataset(nc_path, decode_timedelta=False) as ds:
        # Compute speed and direction from east/north if both present
        arrays = {}
        for v in ds.data_vars:
            arrays[v] = ds[v]
        if "east_velocity" in ds and "north_velocity" in ds:
            e = ds["east_velocity"].values.astype(float)
            n = ds["north_velocity"].values.astype(float)
            spd = np.sqrt(e**2 + n**2)
            # Oceanographic convention: direction water flows TO, 0=N clockwise
            dirn = np.degrees(np.arctan2(e, n)) % 360.0
            arrays["current_speed"] = spd
            arrays["current_direction"] = dirn

        # Apply seabed mask (seabed_qc ≥ 3 → at or below seabed) to all
        # (time, N_BINS) arrays before plotting.  Done after speed/direction
        # are computed so they are masked consistently.
        if "seabed_qc" in ds.data_vars:
            seabed_mask = np.isin(
                ds["seabed_qc"].values, params.QC_BAD_FLAGS
            )  # (time, N_BINS)
            masked_arrays = {}
            for k, v in arrays.items():
                arr = v if isinstance(v, np.ndarray) else v.values
                if arr.ndim == 2 and arr.shape == seabed_mask.shape:
                    arr = arr.astype(float).copy()
                    arr[seabed_mask] = np.nan
                masked_arrays[k] = arr
            arrays = masked_arrays

        present = [(v, lbl, pt) for v, lbl, pt in _COMPONENTS if v in arrays]
        if not present:
            return None

        time = ds["time"].values

        if "range" in ds.coords:
            range_coord = ds["range"].values
        else:
            range_coord = None
            for v, _, _ in present:
                if v not in ds:
                    continue
                for dim in ds[v].dims:
                    if dim == "time":
                        continue
                    for _cn, cda in ds.coords.items():
                        if cda.dims == (dim,):
                            range_coord = cda.values
                            break
                    if range_coord is not None:
                        break
                if range_coord is not None:
                    break
        if range_coord is None:
            return None

        # Shared diverging bound from all ENU components (2nd/98th pctile magnitude)
        # Passed to _velocity_panel_style for div panels; seq/cyc panels compute
        # their own bounds inside the helper from the per-panel finite values.
        div_list = [
            arrays[v].ravel()
            if isinstance(arrays[v], np.ndarray)
            else arrays[v].values.ravel()
            for v, _, pt in present
            if pt == "div"
        ]
        if div_list:
            div_vals = np.concatenate(div_list)
            finite_div = div_vals[np.isfinite(div_vals)]
            abs_max = (
                max(
                    abs(float(np.percentile(finite_div, 2))),
                    abs(float(np.percentile(finite_div, 98))),
                    1e-4,
                )
                if len(finite_div)
                else 1.0
            )
        else:
            abs_max = 1.0

        n = len(present)
        fig, axes = plt.subplots(
            n, 1, figsize=(width_in, 3.5 * n), sharex=True, squeeze=False
        )

        orientation = ds.attrs.get("orientation_yaml") or ds.attrs.get(
            "orientation_instrument", "down"
        )
        looking_down = str(orientation).lower() == "down"
        ylabel = "Range (m, ↓ deeper)" if looking_down else "Range (m from transducer)"

        # Determine the deepest range to show.
        # Prefer seabed_qc: deepest bin that is ever above the seabed
        # (flag 1 = good).  Fall back to deepest bin with any finite
        # velocity data.  Fall back further to the full range_coord span.
        if "seabed_qc" in ds.data_vars:
            seabed_qc_vals = ds["seabed_qc"].values  # (time, N_BINS)
            bin_ever_good = np.any(seabed_qc_vals == 1, axis=0)  # (N_BINS,)
            range_max = (
                float(range_coord[bin_ever_good].max())
                if bin_ever_good.any()
                else float(range_coord.max())
            )
        else:
            range_max = float(range_coord.max())
            for vel_var in ("east_velocity", "north_velocity", "up_velocity"):
                if vel_var not in arrays:
                    continue
                vel = (
                    arrays[vel_var]
                    if isinstance(arrays[vel_var], np.ndarray)
                    else arrays[vel_var].values
                )
                bin_has_data = np.any(np.isfinite(vel), axis=0)  # (N_BINS,)
                if bin_has_data.any():
                    range_max = float(range_coord[bin_has_data].max())
                break

        for ax, (var, label, _pt) in zip(axes[:, 0], present, strict=False):
            raw = arrays[var]
            data2d = raw if isinstance(raw, np.ndarray) else raw.values
            # Ensure (time, N_BINS) then transpose to (N_BINS, time) for pcolormesh
            if data2d.shape[0] != len(time):
                data2d = data2d.T
            data2d = data2d.T  # now (N_BINS, time)

            fv = data2d.ravel()
            fv = fv[np.isfinite(fv)]
            bounds, norm, cmap, cb_label = _velocity_panel_style(var, fv, abs_max)
            pc = ax.pcolormesh(
                time, range_coord, data2d, shading="nearest", cmap=cmap, norm=norm
            )
            cb = fig.colorbar(pc, ax=ax, pad=0.02, ticks=bounds[::2])
            cb.set_label(cb_label)
            ax.set_ylabel(ylabel)
            plot_title(ax, label)
            grid_despine(ax)
            # Show from 0 (includes blanking zone) to deepest valid bin.
            # set_ylim with reversed args inverts for downward-looking.
            if looking_down:
                ax.set_ylim(range_max, 0.0)
            else:
                ax.set_ylim(0.0, range_max)

        date_axis(axes[-1, 0])

        return fig


def draw_adcp_rose(
    nc_path: str,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Draw current rose panels for an ADCP: depth-average plus percentile-selected bins.

    Selects the depth-average and up to four individual range bins at the 10th,
    37th, 63rd, and 90th percentile positions of the valid bin indices (bins with
    at least 5 % of time steps having finite data).  Returns None if east/north
    velocity are absent or too few samples exist.

    Parameters
    ----------
    nc_path : str
        Path to a stage-3 ADCP NetCDF file.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None
        Figure, or None if east/north velocity are absent or too few samples exist.

    """
    import matplotlib.pyplot as plt
    import xarray as xr

    _PERCENTILES = [10, 37, 63, 90]

    with xr.open_dataset(nc_path, decode_timedelta=False) as ds:
        if "east_velocity" not in ds or "north_velocity" not in ds:
            return None

        east_2d = ds["east_velocity"].values.astype(float)  # (time, N_BINS)
        north_2d = ds["north_velocity"].values.astype(float)

        n_time, n_bins = east_2d.shape

        if "range" in ds.coords:
            range_vals = ds["range"].values
        else:
            range_vals = np.arange(n_bins, dtype=float)

        # Find valid bins: at least 5 % of time steps have finite data
        min_finite = max(2, int(0.05 * n_time))
        valid_mask = np.array(
            [np.sum(np.isfinite(east_2d[:, i])) >= min_finite for i in range(n_bins)]
        )
        valid_bins = np.where(valid_mask)[0]

        # Build panel list: depth-average first, then percentile-selected bins
        panels = []

        e_mean = np.nanmean(east_2d, axis=1)
        n_mean = np.nanmean(north_2d, axis=1)
        if np.sum(np.isfinite(e_mean)) >= 2:
            panels.append((e_mean, n_mean, "Depth average"))

        if len(valid_bins) >= 1:
            # Select bins at percentile positions; deduplicate
            selected_indices = []
            for pct in _PERCENTILES:
                pos = int(round(pct / 100.0 * (len(valid_bins) - 1)))
                pos = max(0, min(len(valid_bins) - 1, pos))
                idx = int(valid_bins[pos])
                if idx not in selected_indices:
                    selected_indices.append(idx)

            for idx in selected_indices:
                e_bin = east_2d[:, idx]
                n_bin = north_2d[:, idx]
                if np.sum(np.isfinite(e_bin)) < 2:
                    continue
                actual = float(range_vals[idx])
                panels.append((e_bin, n_bin, f"{actual:.0f} m"))

        if not panels:
            return None

        # Shared speed scale across all panels (99th percentile of all data)
        all_speeds = np.concatenate([np.sqrt(e**2 + n**2) for e, n, _ in panels])
        shared_max = max(
            float(np.nanpercentile(all_speeds[np.isfinite(all_speeds)], 99)), 1e-9
        )

        ncols = len(panels)
        fig, axs = plt.subplots(
            1,
            ncols,
            figsize=(width_in, 4.0),
            subplot_kw={"projection": "polar"},
            squeeze=False,
        )
        spd_edges = colors = None
        for ax, (east, north, title) in zip(axs[0], panels, strict=False):
            result = _rose_ax(ax, east, north, title=title, max_speed=shared_max)
            if result is not None:
                spd_edges, colors = result

        # Add shared colorbar below the rose panels
        if spd_edges is not None and colors is not None:
            import matplotlib.cm as mcm
            import matplotlib.colors as mcolors

            cmap_obj = mcolors.ListedColormap(colors)
            norm = mcolors.BoundaryNorm(spd_edges, len(colors))
            sm = mcm.ScalarMappable(cmap=cmap_obj, norm=norm)
            sm.set_array([])
            cbar = fig.colorbar(
                sm,
                ax=axs[0].tolist(),
                orientation="horizontal",
                fraction=0.04,
                pad=0.08,
                aspect=40,
            )
            cbar.set_label(params.vlabel("speed"))
            cbar.set_ticks(spd_edges)
            cbar.set_ticklabels([f"{v:.2f}" for v in spd_edges])

    return fig


def draw_adcp_hodograph(
    nc_path: str,
    lp_days: float = 4.0,
    smooth_hours: float = 24.0,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Two-depth hodograph for an ADCP per-instrument report; return a Figure.

    Picks the bins nearest the 25th and 75th percentile of the valid range and
    renders a 2×2 figure: top row = far bin (75th pctile — typically shallower
    for upward-looking), bottom row = near bin (25th pctile).  Left column =
    Tukey-smoothed raw; right column = eddy (LP mean removed).  Returns None if
    east/north velocity are absent, 1-D, or too few valid bins.

    Accepts both the current naming convention (``east_velocity`` /
    ``north_velocity``) and the legacy dolfyn names (``u`` / ``v``) so that
    files produced before the stage1 renaming was introduced can still be
    visualised.  Files need to be regenerated with the current stage1 to get
    the canonical names.

    Parameters
    ----------
    nc_path : str
        Path to a stage-3 ADCP NetCDF file.
    lp_days : float
        Low-pass filter cutoff in days for eddy extraction.
    smooth_hours : float
        Tukey smoothing window in hours for the raw panel.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None
        Figure, or None if velocity data are absent or insufficient.

    """
    import xarray as xr

    with xr.open_dataset(nc_path, decode_timedelta=False) as ds:
        # Support both canonical names and legacy dolfyn names
        u_var = next((v for v in ("east_velocity", "u") if v in ds), None)
        v_var = next((v for v in ("north_velocity", "v") if v in ds), None)
        if u_var is None or v_var is None:
            return None

        # Normalise dimension order to (time, bins)
        east_da = ds[u_var]
        north_da = ds[v_var]
        if east_da.dims[0] != "time":
            east_da = east_da.transpose("time", ...)
            north_da = north_da.transpose("time", ...)
        east_2d = east_da.values.astype(float)
        north_2d = north_da.values.astype(float)
        if east_2d.ndim != 2:
            return None

        n_time, n_bins = east_2d.shape
        if n_time < 4:
            return None

        # Range coordinate — try both the current dim name and "range"
        bin_dim = east_da.dims[1] if east_da.ndim == 2 else None
        range_coord = next(
            (c for c in ("range", bin_dim) if c and c in ds.coords),
            None,
        )
        range_vals = (
            ds.coords[range_coord].values
            if range_coord is not None
            else np.arange(n_bins, dtype=float)
        )
        units = params.vunit("east_velocity") or unit_label(
            ds[u_var].attrs.get("units", "m s-1")
        )

        if "time" in ds.coords and ds["time"].size >= 2:
            t_ns = ds["time"].values.astype("datetime64[ns]").astype(float)
            dt_s = float(np.nanmedian(np.diff(t_ns))) / 1e9
        else:
            dt_s = 3600.0
        dt_s = max(dt_s, 1.0)

    min_finite = max(2, int(0.05 * n_time))
    valid_mask = np.array(
        [np.sum(np.isfinite(east_2d[:, i])) >= min_finite for i in range(n_bins)]
    )
    valid_bins = np.where(valid_mask)[0]
    if len(valid_bins) < 2:
        return None

    i_near = valid_bins[max(0, int(round(0.25 * (len(valid_bins) - 1))))]
    i_far = valid_bins[
        min(len(valid_bins) - 1, int(round(0.75 * (len(valid_bins) - 1))))
    ]
    if i_near == i_far:
        return None

    label_far = f"{float(range_vals[i_far]):.0f} m range"
    label_near = f"{float(range_vals[i_near]):.0f} m range"

    from oceanarray.reports._plots import _draw_hodograph_pair

    # Deterministic square-panel grid: each hodograph is an exact square so the
    # single shared time colorbar (right) matches the panel height exactly.
    fig, axes, cax = square_axes_grid(width_in, 2, 2)

    sm_far = _draw_hodograph_pair(
        axes[0, 0],
        axes[0, 1],
        east_2d[:, i_far],
        north_2d[:, i_far],
        f"Far bin ({label_far})",
        smooth_hours,
        lp_days,
        units,
        dt_s,
    )
    sm_near = _draw_hodograph_pair(
        axes[1, 0],
        axes[1, 1],
        east_2d[:, i_near],
        north_2d[:, i_near],
        f"Near bin ({label_near})",
        smooth_hours,
        lp_days,
        units,
        dt_s,
    )

    sm = sm_far or sm_near
    if sm is not None:
        unit_colorbar(cax, sm, ticks=np.array([0.0, 1.0]), ticklabels=["start", "end"])
    elif cax is not None:
        cax.set_visible(False)

    return fig


def draw_grid_hodograph(
    ds: xr.Dataset,
    smooth_hours: float = 24.0,
    *,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """Two-depth hodograph for the grid report; return a Figure.

    Takes an already-loaded xr.Dataset (not a path).  Picks the pressure levels
    nearest the 25th and 75th percentile of the valid gridded pressure range and
    renders a 1×2 figure (shallow / deep, each showing the ``smooth_hours``-smoothed
    hodograph).  Returns None if east/north velocity are absent or fewer than two
    valid levels exist.

    Parameters
    ----------
    ds : xr.Dataset
        Gridded mooring dataset with ``east_velocity`` and ``north_velocity``.
    smooth_hours : float
        Tukey smoothing window in hours.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    plt.Figure or None
        Figure, or None if velocity data are absent or insufficient.

    """
    if "east_velocity" not in ds or "north_velocity" not in ds:
        return None
    east_2d = ds["east_velocity"].values.astype(float)  # (time, pressure)
    north_2d = ds["north_velocity"].values.astype(float)
    if east_2d.ndim != 2:
        return None

    n_time, n_levels = east_2d.shape
    if n_time < 4:
        return None

    # Apply QC masking where companion QC variables are present
    if "east_velocity_qc" in ds:
        east_2d = np.where(
            np.isin(ds["east_velocity_qc"].values, params.QC_BAD_FLAGS), np.nan, east_2d
        )
    if "north_velocity_qc" in ds:
        north_2d = np.where(
            np.isin(ds["north_velocity_qc"].values, params.QC_BAD_FLAGS),
            np.nan,
            north_2d,
        )

    if "pressure" in ds.coords:
        pressure = ds.coords["pressure"].values
    elif "pressure" in ds:
        pressure = ds["pressure"].values
    else:
        pressure = np.arange(n_levels, dtype=float)

    units = params.vunit("east_velocity") or unit_label(
        ds["east_velocity"].attrs.get("units", "m s-1")
    )

    if "time" in ds.coords and ds["time"].size >= 2:
        t_ns = ds["time"].values.astype("datetime64[ns]").astype(float)
        dt_s = float(np.nanmedian(np.diff(t_ns))) / 1e9
    else:
        dt_s = 3600.0
    dt_s = max(dt_s, 1.0)

    min_finite = max(2, int(0.05 * n_time))
    valid_plevs = np.array(
        [k for k in range(n_levels) if np.sum(np.isfinite(east_2d[:, k])) >= min_finite]
    )
    if len(valid_plevs) < 2:
        return None

    i_shallow = valid_plevs[max(0, int(round(0.25 * (len(valid_plevs) - 1))))]
    i_deep = valid_plevs[
        min(len(valid_plevs) - 1, int(round(0.75 * (len(valid_plevs) - 1))))
    ]
    if i_shallow == i_deep:
        return None

    label_shallow = f"{int(pressure[i_shallow])} dbar"
    label_deep = f"{int(pressure[i_deep])} dbar"

    smooth_n = max(3, int(round(smooth_hours * 3600.0 / dt_s)))

    # Same deterministic square-panel layout as the ADCP hodograph, so both
    # reports render hodographs and their shared time colorbar identically.
    fig, axes, cax = square_axes_grid(width_in, 1, 2)
    ax_shallow, ax_deep = axes[0, 0], axes[0, 1]

    sm = None
    for ax, i_lev, label in [
        (ax_shallow, i_shallow, f"Shallow ({label_shallow})"),
        (ax_deep, i_deep, f"Deep ({label_deep})"),
    ]:
        e_sm = tukey_smooth(east_2d[:, i_lev], smooth_n)
        n_sm = tukey_smooth(north_2d[:, i_lev], smooth_n)
        mask = np.isfinite(e_sm) & np.isfinite(n_sm)
        if mask.sum() < 2:
            ax.text(
                0.5, 0.5, "No data", transform=ax.transAxes, ha="center", va="center"
            )
            plot_title(ax, label)
            continue
        t_frac = np.linspace(0.0, 1.0, len(east_2d[:, i_lev]))[mask]
        sm = hodograph_panel(
            ax,
            e_sm[mask],
            n_sm[mask],
            t_frac,
            f"{label} — {smooth_hours:.0f}-h smoothed",
            units,
        )

    if sm is not None:
        unit_colorbar(cax, sm, ticks=np.array([0.0, 1.0]), ticklabels=["start", "end"])
    elif cax is not None:
        cax.set_visible(False)

    return fig


def draw_aquadopp_tilt_panels(
    ds: xr.Dataset, *, step: int = 1, width_in: float = report_tokens.W_FULL
) -> plt.Figure | None:
    """One subplot per Aquadopp showing pitch, roll, and tilt_from_pressure.

    Within each row the time-series panel and its paired scatter panel share one
    0–90° y-axis, so the pitch/roll time series and the pressure-tilt scatter read
    on the same scale.  Gridlines are drawn on both panels.  Horizontal reference
    lines are drawn at the suspect and fail thresholds read from ds.attrs (falling
    back to 20° / 30° if absent).

    Parameters
    ----------
    ds : xarray.Dataset
        A stack with ``instrument_type``, ``serial`` and ``hab`` on ``N_LEVELS``
        and any of ``pitch``, ``roll``, ``tilt_from_pressure``.
    step : int, optional
        Plot every *step*-th time sample.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure or None
        ``None`` if no Aquadopp level is found or none of the relevant variables
        exists.
    """
    import matplotlib.dates as mdates

    tilt_suspect = float(ds.attrs.get("tilt_suspect_threshold", 20.0))
    tilt_fail = float(ds.attrs.get("tilt_fail_threshold", 30.0))

    instr_types = ds["instrument_type"].values
    serials = ds["serial"].values
    habs = ds["hab"].values
    ref_habs = (
        ds["tilt_pressure_ref_hab"].values
        if "tilt_pressure_ref_hab" in ds.data_vars
        else None
    )
    ref_serials = (
        ds["tilt_pressure_ref_serial"].values
        if "tilt_pressure_ref_serial" in ds.data_vars
        else None
    )

    aq_indices = [i for i, t in enumerate(instr_types) if str(t).lower() == "aquadopp"]
    if not aq_indices:
        return None

    # Cap the number of stacked panels so the figure stays a sane height for PDF
    # pagination (one panel is ~2.24 in; 5 → ~11 in).  Deep-first order is kept, so
    # the deepest Aquadopps are shown; a note flags any that were dropped.
    _MAX_TILT_ROWS = 5
    _n_dropped = max(0, len(aq_indices) - _MAX_TILT_ROWS)
    aq_indices = aq_indices[:_MAX_TILT_ROWS]

    has_pitch = "pitch" in ds.data_vars
    has_roll = "roll" in ds.data_vars
    has_tilt_p = "tilt_from_pressure" in ds.data_vars
    if not has_pitch and not has_roll and not has_tilt_p:
        return None

    # The angle unit for every axis on this figure, read from whichever stored angle
    # variable is present (pitch/roll/tilt share it) so the label tracks the data: a
    # file written in radians would relabel, not silently claim degrees.
    _angle_var = (
        "pitch" if has_pitch else ("roll" if has_roll else "tilt_from_pressure")
    )
    deg_u = var_unit_label(ds[_angle_var])

    time_ds = ds["time"].values[::step]
    n_panels = len(aq_indices)

    fig = plt.figure(figsize=(width_in, 2.24 * n_panels), constrained_layout=True)
    # Tighten the inter-row gap; upper rows drop their x tick labels/label so
    # the rows can pack closer.
    fig.get_layout_engine().set(h_pad=0.03, hspace=0.0)
    gs = fig.add_gridspec(n_panels, 3, width_ratios=[2, 2, 1])

    ax_ts_first = None
    for row, i in enumerate(aq_indices):
        serial = serials[i]
        hab = habs[i]

        ax_ts = fig.add_subplot(gs[row, :2], sharex=ax_ts_first)
        if ax_ts_first is None:
            ax_ts_first = ax_ts
        # Scatter shares this row's y-axis so pitch/roll read on the same
        # 0–90° scale as the time series.
        ax_sc = fig.add_subplot(gs[row, 2], sharey=ax_ts)

        p_data = r_data = tp_data = None
        if has_pitch:
            p_data = np.abs(ds["pitch"].values[::step, i].astype(float))
            if np.any(np.isfinite(p_data)):
                ax_ts.plot(time_ds, p_data, lw=0.7, color="#2980b9", label="|pitch|")
        if has_roll:
            r_data = np.abs(ds["roll"].values[::step, i].astype(float))
            if np.any(np.isfinite(r_data)):
                ax_ts.plot(time_ds, r_data, lw=0.7, color="#27ae60", label="|roll|")
        if has_tilt_p:
            tp_data = ds["tilt_from_pressure"].values[::step, i].astype(float)
            if np.any(np.isfinite(tp_data)):
                ax_ts.plot(
                    time_ds,
                    tp_data,
                    lw=0.9,
                    color="#e67e22",
                    ls="--",
                    label="tilt (pressure)",
                )

        ax_ts.axhline(tilt_suspect, color="tab:orange", lw=0.8, ls="--", zorder=0)
        ax_ts.axhline(tilt_fail, color="tab:red", lw=0.8, ls=":", zorder=0)
        ax_ts.set_ylim(0.0, 90.0)
        ax_ts.set_ylabel(f"Degrees ({deg_u})" if deg_u else "Degrees")
        grid_despine(ax_ts)

        _ref_note = ""
        if ref_habs is not None and np.isfinite(ref_habs[i]):
            _ref_s = str(ref_serials[i]) if ref_serials is not None else "?"
            _ref_note = f"  [ref: s/n {_ref_s} @ {ref_habs[i]:.0f} m]"
        ax_ts.set_title(f"s/n {serial}  ({hab:.0f} m hab){_ref_note}")
        if ax_ts.get_legend_handles_labels()[0]:
            # Legend inside the time-series panel (was anchored outside at
            # 1.01, which landed over the neighbouring scatter panel).
            ax_ts.legend(loc="best", framealpha=0.8)

        if row < n_panels - 1:
            ax_ts.tick_params(labelbottom=False)
        else:
            loc = mdates.AutoDateLocator()
            ax_ts.xaxis.set_major_locator(loc)
            ax_ts.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
            date_offset_left(ax_ts)
            ax_ts.tick_params(axis="x")

        if tp_data is not None and np.any(np.isfinite(tp_data)):
            sc_kw = {"s": 3, "alpha": 0.25, "rasterized": True, "linewidths": 0}
            if p_data is not None and np.any(np.isfinite(p_data)):
                fin = np.isfinite(tp_data) & np.isfinite(p_data)
                ax_sc.scatter(
                    tp_data[fin],
                    p_data[fin],
                    color="#2980b9",
                    label="|pitch|",
                    **sc_kw,
                )
            if r_data is not None and np.any(np.isfinite(r_data)):
                fin = np.isfinite(tp_data) & np.isfinite(r_data)
                ax_sc.scatter(
                    tp_data[fin],
                    r_data[fin],
                    color="#27ae60",
                    label="|roll|",
                    **sc_kw,
                )
            ax_sc.plot(
                [0, 90],
                [0, 90],
                color="0.4",
                lw=0.8,
                ls="--",
                label="1:1",
                zorder=2,
            )
            ax_sc.axvline(tilt_suspect, color="tab:orange", lw=0.7, ls="--", zorder=0)
            ax_sc.axvline(tilt_fail, color="tab:red", lw=0.7, ls=":", zorder=0)
            ax_sc.axhline(tilt_suspect, color="tab:orange", lw=0.7, ls="--", zorder=0)
            ax_sc.axhline(tilt_fail, color="tab:red", lw=0.7, ls=":", zorder=0)
            ax_sc.set_xlim(0.0, 90.0)
            ax_sc.set_ylim(0.0, 90.0)  # re-assert after scatter autoscale
            # Match x/y ticks (both are 0–90° tilt); the shared y also sets the
            # time-series panel's y-ticks.
            ax_sc.set_xticks([0, 30, 60, 90])
            ax_sc.set_yticks([0, 30, 60, 90])
            ax_sc.set_ylabel(
                f"|pitch|, |roll| [{deg_u}]" if deg_u else "|pitch|, |roll|"
            )
            if row == n_panels - 1:
                ax_sc.set_xlabel(
                    f"tilt (pressure) [{deg_u}]" if deg_u else "tilt (pressure)"
                )
            else:
                ax_sc.tick_params(labelbottom=False)
            grid_despine(ax_sc)
            # No scatter legend — the time-series panel legend already names
            # pitch/roll; a second legend here is a redundant repeat.
        else:
            ax_sc.text(
                0.5,
                0.5,
                "no tilt data",
                transform=ax_sc.transAxes,
                ha="center",
                va="center",
                color="gray",
            )
            ax_sc.set_axis_off()

    if _n_dropped:
        # No explicit y: constrained_layout reserves space for the suptitle
        # above the panels, so it no longer overprints the top panel's title.
        fig.suptitle(
            f"Showing {_MAX_TILT_ROWS} deepest Aquadopps ({_n_dropped} more not shown)",
            fontsize=report_tokens.ANNOT_FS,
        )
    return fig
