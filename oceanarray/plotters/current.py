"""Tier-2 domain wrappers for current/velocity instrument plots.

These functions accept xarray Datasets and delegate rendering to Tier-1
primitives in _primitives.py.  They understand oceanarray variable naming
conventions but do not serialise to base64 (that happens in report/_plots.py).

Coordinate transformations come from :mod:`oceanarray.pipeline.coordinate`.

Public draw_* functions (migrated from report/_plots.py):
- draw_instrument_rose: rose diagram grid for a single Aquadopp instrument.
- draw_rose_grid: grid of current roses for instruments in a stack dataset.
- draw_grid_rose: grid of current roses by pressure level for the grid report.
- draw_grid_progressive_vector: progressive vector diagram by pressure level for the grid
  report (``draw_grid_trajectory`` is its former name).
- draw_adcp_velocity: stacked colour panels for the ADCP per-instrument HTML page.
- draw_adcp_rose: current rose panels for an ADCP per-instrument report.
- draw_adcp_hodograph: two-depth hodograph for an ADCP per-instrument report.
- draw_grid_hodograph: two-depth hodograph for the grid report.
- draw_aquadopp_tilt_panels: absolute pitch, roll and pressure tilt per Aquadopp for the stack report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from matplotlib.collections import LineCollection

from oceanarray.config import parameters as params
from oceanarray.config.unit_tables import (
    unit_label,
    var_unit_label,
    warn_if_not_canonical,
)
from oceanarray.physics import flow_direction, speed_direction
from oceanarray.plotters import helpers
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
    draw_trajectory,
    figure_title,
    hodograph_panel,
    nice_colorbar_bounds,
    plot_title,
    square_axes_grid,
    square_limits,
    unit_colorbar,
    ytick_reserve_in,
)
from oceanarray.reports._vendored import design_tokens as report_tokens


def progressive_vector(
    east_2d: np.ndarray,
    north_2d: np.ndarray,
    dt_s: np.ndarray,
    pressure: np.ndarray,
) -> list[tuple[float, np.ndarray, np.ndarray]]:
    """Compute pseudo-Lagrangian progressive-vector trajectories by pressure level.

    Integrates east and north velocity time series using the Euler forward
    method to produce cumulative horizontal displacement from the origin.
    Levels with all-NaN velocity are silently skipped.

    Parameters
    ----------
    east_2d : np.ndarray
        East velocity array, shape ``(n_time, n_pressure)`` in m s⁻¹.
        NaN is treated as zero for the integration step in which it occurs.
    north_2d : np.ndarray
        North velocity array, shape ``(n_time, n_pressure)`` in m s⁻¹.
        NaN is treated as zero for the integration step in which it occurs.
    dt_s : np.ndarray
        1-D array of time-step sizes in seconds, length ``n_time - 1``.
    pressure : np.ndarray
        1-D pressure array of length ``n_pressure`` in dbar.

    Returns
    -------
    list of tuple[float, np.ndarray, np.ndarray]
        One entry ``(p_val, x_km, y_km)`` per pressure level that has at least
        one finite velocity sample.  ``p_val`` is the pressure in dbar;
        ``x_km`` and ``y_km`` are 1-D arrays of cumulative east and north
        displacement in km, length ``n_time``.

    """
    trajs: list[tuple[float, np.ndarray, np.ndarray]] = []
    for k, p_val in enumerate(pressure):
        e_col = east_2d[:, k]
        n_col = north_2d[:, k]
        if not np.any(np.isfinite(e_col)):
            continue
        u = np.nan_to_num(e_col, nan=0.0)
        v = np.nan_to_num(n_col, nan=0.0)
        x = np.concatenate([[0.0], np.cumsum(u[:-1] * dt_s)]) / 1000.0
        y = np.concatenate([[0.0], np.cumsum(v[:-1] * dt_s)]) / 1000.0
        trajs.append((float(p_val), x, y))
    return trajs


def draw_temperature_trajectory(
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

    Note: the velocity integration step will move to ``oceanarray.analysis``
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
    matplotlib.figure.Figure, or None when east/north velocity or temperature is absent.

    """
    # An imported current meter may hold only pressure and velocity (no temperature) or
    # only a subset; the panel is then not applicable rather than an error.
    if any(v not in ds.data_vars for v in (u_var, v_var, temp_var)):
        return None
    # Temperature present but all-NaN (imported fill) would make the colour scale reduce
    # a zero-size array; not applicable rather than an error.
    if not np.isfinite(ds[temp_var].values).any():
        return None
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

    return draw_trajectory(
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


def draw_speed_boxplot(
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
    matplotlib.figure.Figure, or None when the speed variable is absent or has no
    finite values (e.g. an imported current meter carrying only pressure and velocity).

    """
    import matplotlib.pyplot as plt

    if speed_var not in ds.data_vars:
        return None
    speed = ds[speed_var].values.ravel()
    speed_clean = speed[np.isfinite(speed)]  # drops NaN and ±inf (imported fills)
    if speed_clean.size == 0:
        return None

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


def draw_multi_aquadopp_progressive_vector(
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
    """Progressive vector diagram of every Aquadopp, coloured by temperature.

    Each line starts at the origin and adds up the measured east/north velocity
    over time (Euler forward; a missing velocity adds nothing): how far water
    passing the instrument would travel if the flow were the same everywhere.
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
        None if no Aquadopp instruments, or no east/north velocities, are found in
        the dataset.

    Notes
    -----
    Adapted from ``02_aqdp_ploter.py`` by L. Moscatel (lmoscat),
    Universitat de Barcelona.

    """
    instr_types = ds[instr_type_var].values
    serials = ds[serial_var].values
    aqd_idx = [i for i, t in enumerate(instr_types) if str(t).lower() == "aquadopp"]

    # A stack built from stage 2 holds beam velocities, which the stack drops.
    if not aqd_idx or u_var not in ds or v_var not in ds:
        return None

    time = ds["time"].values
    dt = np.array(
        [(time[j] - time[j - 1]) / np.timedelta64(1, "s") for j in range(1, len(time))],
        dtype=float,
    )

    # Colour by temperature only when at least one finite value exists among the
    # Aquadopps; an imported current meter's temperature may be absent or all-NaN, which
    # would otherwise make np.nanmin below reduce a zero-size array.
    has_temp = temp_var in ds.data_vars and bool(
        np.isfinite(ds[temp_var].values[:, aqd_idx]).any()
    )

    # The km label assumes both velocity components in m/s integrated over dt in seconds.
    warn_if_not_canonical(ds[u_var], "m s-1", quantity="trajectory displacement (km)")
    warn_if_not_canonical(ds[v_var], "m s-1", quantity="trajectory displacement (km)")

    # Build trajectories and collect temperature data for shared norm
    trajs = []
    all_temps: list = []
    # A stack's rule hides flagged values (a file without one keeps its velocity).
    u_all = helpers.masked(ds, u_var) if helpers.ruled(ds) else ds[u_var].values
    v_all = helpers.masked(ds, v_var) if helpers.ruled(ds) else ds[v_var].values
    t_all = helpers.masked(ds, temp_var) if has_temp else None
    for i in aqd_idx:
        u = np.nan_to_num(u_all[:, i], nan=0.0)
        v = np.nan_to_num(v_all[:, i], nan=0.0)
        x = np.concatenate([[0.0], np.cumsum(u[:-1] * dt)]) / 1000.0
        y = np.concatenate([[0.0], np.cumsum(v[:-1] * dt)]) / 1000.0
        temp = t_all[:, i] if t_all is not None else None
        trajs.append((i, x, y, temp))
        if temp is not None:
            all_temps.append(temp[np.isfinite(temp)])

    cmap = plt.get_cmap("coolwarm")
    if has_temp and all_temps:
        flat = np.concatenate(all_temps)
        _tmin = float(np.nanmin(flat))
        _tmax = float(np.nanmax(flat))
        _bounds = nice_colorbar_bounds(_tmin, _tmax, n=20)
        # Enforce minimum tick spacing of 0.5 °C so labels don't crowd
        if len(_bounds) > 1 and (_bounds[1] - _bounds[0]) < 0.5:
            _n_t = max(2, int((_tmax - _tmin) / 0.5))
            _bounds = nice_colorbar_bounds(_tmin, _tmax, n=_n_t)
    else:
        _bounds = nice_colorbar_bounds(0.0, 1.0, n=20)
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


def draw_hodograph(
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


def draw_aquadopp_speed_profile(
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
            spd, _ = speed_direction(u, v)
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


def draw_adcp_progressive_vector(
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
    """Progressive vector diagram of each ADCP bin, coloured by height above bottom.

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
    stack_rule = helpers.ruled(ds)
    if stack_rule:
        # The stack's rule hides by every velocity flag (seabed, ping quality too).
        u_all = helpers.masked(ds, u_var)
        v_all = helpers.masked(ds, v_var)
    for i in adcp_idx:
        u = ds[u_var].values[:, i].copy().astype(float)
        v = ds[v_var].values[:, i].copy().astype(float)
        if stack_rule:
            u, v = u_all[:, i].copy(), v_all[:, i].copy()
            if seabed_qc_var in ds.data_vars and np.all(
                np.isin(ds[seabed_qc_var].values[:, i], params.QC_BAD_FLAGS)
            ):
                continue  # a bin always below the seabed

        # Mask by seabed proximity
        elif seabed_qc_var in ds.data_vars:
            sqc = ds[seabed_qc_var].values[:, i]
            # Skip bins that are always below the seabed
            if np.all(np.isin(sqc, params.QC_BAD_FLAGS)):
                continue
            u[np.isin(sqc, params.QC_BAD_FLAGS)] = np.nan
            v[np.isin(sqc, params.QC_BAD_FLAGS)] = np.nan

        # Mask by ping quality
        if percent_good_qc_var in ds.data_vars and not stack_rule:
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
    _bounds = nice_colorbar_bounds(hab_min, hab_max, n=20)
    # Enforce minimum tick spacing of 50 m so labels don't crowd
    if len(_bounds) > 1 and (_bounds[1] - _bounds[0]) < 50.0:
        _n_hab = max(2, int((hab_max - hab_min) / 50.0))
        _bounds = nice_colorbar_bounds(hab_min, hab_max, n=_n_hab)
    norm: mcolors.BoundaryNorm = mcolors.BoundaryNorm(_bounds, ncolors=256)

    # Half width — shown in a 50% flex column beside the Aquadopp trajectory
    # (see stack.html), matching draw_multi_aquadopp_progressive_vector.
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


def rose_panels(
    ds: xr.Dataset, rule: dict | None = None
) -> list[tuple[np.ndarray, np.ndarray, str, int]]:
    """Return an instrument rose's panels: good, suspect and bad values apart.

    A value's flag is the worse of the originator's and oceanarray's for east and
    north velocity (:func:`~oceanarray.plotters.helpers.marker_flags`). Good holds
    the values flagged neither suspect nor bad, less any *rule* hides; a suspect
    or bad panel is returned only when such values exist.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file with ``east_velocity`` and ``north_velocity``.
    rule : dict or None, optional
        The flag rule (``masking.resolve_mask_flags``); the default when not given.

    Returns
    -------
    list of (numpy.ndarray, numpy.ndarray, str, int)
        ``(east, north, label, n)`` per panel, e.g. ``"Suspect (originator)"``
        with its number of values; good first.
    """
    from oceanarray.pipeline import masking
    from oceanarray.pipeline.qc.originator import MAPPED_SUFFIX

    rule = rule if rule is not None else masking.resolve_mask_flags(None)
    east = np.asarray(ds["east_velocity"].values, dtype=float)
    north = np.asarray(ds["north_velocity"].values, dtype=float)
    here = np.isfinite(east) & np.isfinite(north)
    who: dict[int, list[str]] = {3: [], 4: []}
    worst = np.ones(east.shape, dtype=np.int8)
    for suffix, name in ((MAPPED_SUFFIX, "originator"), ("_qc", "oceanarray")):
        flags = [
            helpers.marker_flags(ds, v, sources=(suffix,))
            for v in ("east_velocity", "north_velocity")
        ]
        flags = [f for f in flags if f is not None]
        for f in (3, 4):
            hit = np.logical_or.reduce([fl == f for fl in flags]) if flags else False
            if np.any(hit & here):
                who[f].append(name)
                worst[hit & (worst < f)] = f
    good = here & (worst == 1)
    kept_e = np.where(good, masking.masked(ds, "east_velocity", rule), np.nan)
    kept_n = np.where(good, masking.masked(ds, "north_velocity", rule), np.nan)
    kept = np.isfinite(kept_e) & np.isfinite(kept_n)
    panels = [(kept_e, kept_n, "Good", int(kept.sum()))]
    for f, word in ((3, "Suspect"), (4, "Bad")):
        sel = here & (worst == f)
        if sel.any():
            panels.append(
                (
                    np.where(sel, east, np.nan),
                    np.where(sel, north, np.nan),
                    f"{word} ({', '.join(who[f])})",
                    int(sel.sum()),
                )
            )
    return panels


def draw_instrument_rose(
    nc_path: Path | xr.Dataset,
    *,
    width_in: float = report_tokens.W_FULL,
    title: str | None = None,
    rule: dict | None = None,
) -> plt.Figure | None:
    """Draw one instrument's current roses in the stack page's design; return Figure or None.

    The good values' rose (:func:`oceanarray.plotters.helpers.current_rose`),
    with the values *rule* hides left out, and beside it a rose of the values
    flagged suspect and one of those flagged bad, when there are any
    (:func:`rose_panels`). Each rose has its own ring scale: the flagged ones are
    small subsets. One speed-class legend.

    Parameters
    ----------
    nc_path : Path or xarray.Dataset
        The instrument's stage-3 NetCDF file, or the file already open.
    width_in : float, optional
        Figure width in inches (the display slot).
    title : str or None, optional
        The rose's title (the page's ``"{serial} · {depth} m"``) when only the
        good rose is drawn; the serial when not given. With flagged roses each
        is titled by its values instead (``"Suspect (originator): 246 values"``).
    rule : dict or None, optional
        The flag rule (``masking.resolve_mask_flags``); the default rule when
        not given.

    Returns
    -------
    matplotlib.figure.Figure or None
        None without east and north velocity.
    """
    ds = helpers.as_dataset(nc_path)
    if "east_velocity" not in ds.data_vars or "north_velocity" not in ds.data_vars:
        return None
    panels = rose_panels(ds, rule)
    if title is None:
        title = str(ds.attrs.get("serial", ""))
    if len(panels) == 1:
        return helpers.draw_roses([panels[0][:2] + (title,)], width_in=width_in)
    roses = [(e, n, f"{label}: {count:,} values") for e, n, label, count in panels]
    return helpers.draw_roses(roses, width_in=width_in, own_scale=True)


def draw_rose_grid(
    ds: xr.Dataset,
    serial_list: list,
    *,
    width_in: float = report_tokens.W_FULL,
) -> tuple[plt.Figure, int] | None:
    """Draw current roses (up to 4 per row) for the instruments with ENU velocity data.

    Each rose is titled with the serial and nominal depth; all share one radial
    scale (:func:`oceanarray.plotters.helpers.draw_roses`).

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
    if "east_velocity" not in ds.data_vars or "north_velocity" not in ds.data_vars:
        return None

    east_all = ds["east_velocity"].values.copy()
    north_all = ds["north_velocity"].values.copy()

    # Mask suspect/bad data from all available QC variables before plotting roses.
    # seabed_qc handles ADCP bins below the seafloor; percent_good_qc and
    # error_velocity_qc handle low-quality ADCP pings.  A flag in QC_BAD_FLAGS (3, 4, 9) is
    # treated as invalid; nominal (7) and interpolated (8) are kept.
    if helpers.ruled(ds):
        # A stack: its mask_flags rule covers every velocity flag.
        east_all = helpers.masked(ds, "east_velocity")
        north_all = helpers.masked(ds, "north_velocity")
    for _qc_var in (
        "east_velocity_qc",
        "seabed_qc",
        "percent_good_qc",
        "error_velocity_qc",
    ):
        if _qc_var in ds.data_vars and not helpers.ruled(ds):
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
    depths = helpers.nominal_depths(ds)
    roses = []
    for instr_i in aqd_idx:
        serial = serial_list[instr_i] if instr_i < len(serial_list) else "?"
        depth = depths[instr_i] if depths is not None else np.nan
        title = f"{serial} · {depth:.0f} m" if np.isfinite(depth) else str(serial)
        roses.append((east_all[:, instr_i], north_all[:, instr_i], title))
    fig = helpers.draw_roses(roses, width_in=width_in)
    return fig, n


def draw_grid_rose(
    ds: xr.Dataset,
    max_roses: int = 4,
    *,
    width_in: float = report_tokens.W_FULL,
    levels: list[int] | None = None,
) -> plt.Figure | None:
    """Draw current roses at grid levels, in the stack page's rose design.

    One rose per level (:func:`oceanarray.plotters.helpers.current_rose`), titled
    with its pressure.  By default the levels are those of
    :func:`oceanarray.analysis.derived.velocity_levels`: levels with velocity for
    at least half the record, at most *max_roses*, from the shallowest to the
    deepest.

    Parameters
    ----------
    ds : xarray.Dataset
        Gridded dataset with ``east_velocity`` and ``north_velocity`` (m s⁻¹) on
        ``(time, pressure)``.
    max_roses : int, optional
        Most roses drawn when *levels* is not given (default 4).
    width_in : float, optional
        Figure width in inches (the display slot).
    levels : list of int, optional
        Grid level indices to draw (the page's shared choice).

    Returns
    -------
    matplotlib.figure.Figure or None
        None when velocity is absent or no level qualifies.
    """
    from oceanarray.analysis.derived import velocity_levels

    if "east_velocity" not in ds.data_vars or "north_velocity" not in ds.data_vars:
        return None
    east_all = ds["east_velocity"].transpose("time", "pressure").values
    north_all = ds["north_velocity"].transpose("time", "pressure").values
    pressure = ds["pressure"].values
    if levels is None:
        levels = velocity_levels(east_all, north_all, n=max_roses)
    if not levels:
        return None

    return helpers.draw_roses(
        [(east_all[:, k], north_all[:, k], f"{pressure[k]:.0f} dbar") for k in levels],
        width_in=width_in,
    )


def draw_grid_progressive_vector(
    ds: xr.Dataset,
    *,
    width_in: float = report_tokens.W_HALF,
) -> plt.Figure | None:
    """Progressive vector diagram at every grid level for the grid report.

    For each pressure level, adds up the hourly velocity (Euler forward) into a
    cumulative displacement from the origin: how far water passing the mooring
    would travel if the flow were the same everywhere.  It shows the mean flow
    direction and its steadiness, not the path of a water parcel.  Returns
    ``None`` when east/north velocity are absent or all-NaN.

    Parameters
    ----------
    ds : xarray.Dataset
        Gridded dataset with dimensions ``(time, pressure)``, containing
        ``east_velocity`` and ``north_velocity`` in m s⁻¹.
    width_in : float, optional
        Figure width in inches -- the display-slot width the report builder
        resolves; standalone callers get the full content width.

    Returns
    -------
    matplotlib.figure.Figure or None

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


#: Former name of :func:`draw_grid_progressive_vector`, kept for callers.
draw_grid_trajectory = draw_grid_progressive_vector
#: Former name of :func:`draw_multi_aquadopp_progressive_vector`, kept for callers.
draw_multi_aquadopp_trajectories = draw_multi_aquadopp_progressive_vector
#: Former name of :func:`draw_adcp_progressive_vector`, kept for callers.
draw_adcp_trajectories = draw_adcp_progressive_vector


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

    with helpers.as_dataset(nc_path) as ds:
        # Compute speed and direction from east/north if both present
        arrays = {}
        for v in ds.data_vars:
            arrays[v] = ds[v]
        if "east_velocity" in ds and "north_velocity" in ds:
            e = ds["east_velocity"].values.astype(float)
            n = ds["north_velocity"].values.astype(float)
            # Direction the water flows toward, clockwise from north.
            spd, dirn = speed_direction(e, n)
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

    _PERCENTILES = [10, 37, 63, 90]

    with helpers.as_dataset(nc_path) as ds:
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
        all_speeds = np.concatenate([speed_direction(e, n)[0] for e, n, _ in panels])
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


def _draw_hodograph_pair(
    ax_raw: Any,
    ax_eddy: Any,
    east_1d: np.ndarray,
    north_1d: np.ndarray,
    label: str,
    smooth_hours: float,
    lp_days: float,
    units: str,
    dt_s: float,
) -> Any:
    """Draw one row (raw + eddy panels) of a hodograph figure onto two Axes."""
    # Raw: Tukey-smoothed east vs north density; eddy: low-pass rolling mean removed,
    # then Tukey-smoothed. Returns the shared 0-1 plasma time mapping (one colorbar
    # for the figure), or None if neither panel had enough finite data.
    import pandas as pd

    from oceanarray.plotters.helpers import tukey_smooth

    smooth_n = max(3, int(round(smooth_hours * 3600.0 / dt_s)))
    lp_n = max(3, int(round(lp_days * 86400.0 / dt_s)))

    e_sm = tukey_smooth(east_1d, smooth_n)
    n_sm = tukey_smooth(north_1d, smooth_n)

    e_lp = (
        pd.Series(east_1d)
        .rolling(window=lp_n, min_periods=1, center=True)
        .mean()
        .values
    )
    n_lp = (
        pd.Series(north_1d)
        .rolling(window=lp_n, min_periods=1, center=True)
        .mean()
        .values
    )
    e_eddy = tukey_smooth(east_1d - e_lp, smooth_n)
    n_eddy = tukey_smooth(north_1d - n_lp, smooth_n)

    # Fractional time 0→1 for colour mapping; matches east_1d length
    t_frac = np.linspace(0.0, 1.0, len(east_1d))

    def _draw(ax: Any, e: np.ndarray, n: np.ndarray, title: str) -> Any:
        mask = np.isfinite(e) & np.isfinite(n)
        if mask.sum() < 2:
            ax.text(
                0.5, 0.5, "No data", transform=ax.transAxes, ha="center", va="center"
            )
            ax.set_title(title)
            return None
        return hodograph_panel(ax, e[mask], n[mask], t_frac[mask], title, units)

    sm_raw = _draw(ax_raw, e_sm, n_sm, f"{label} — raw ({smooth_hours:.0f}-h smoothed)")
    sm_eddy = _draw(
        ax_eddy, e_eddy, n_eddy, f"{label} — eddy ({lp_days:.0f}-day LP removed)"
    )
    return sm_raw or sm_eddy


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
    with helpers.as_dataset(nc_path) as ds:
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
    levels: list[int] | None = None,
) -> plt.Figure | None:
    """Hodographs at grid levels for the grid report; return a Figure.

    One square panel per level (two per row): the *smooth_hours*-smoothed east
    and north velocity traced through the deployment, coloured from start to end,
    with a circle at the start and a square at the end.  By default the levels
    are those of the current roses
    (:func:`oceanarray.analysis.derived.velocity_levels`).

    Parameters
    ----------
    ds : xarray.Dataset
        Gridded mooring dataset with ``east_velocity`` and ``north_velocity``.
    smooth_hours : float, optional
        Tukey smoothing window in hours (default 24).
    width_in : float, optional
        Figure width in inches (the display slot).
    levels : list of int, optional
        Grid level indices (the page's shared choice with the roses).

    Returns
    -------
    matplotlib.figure.Figure or None
        None if velocity is absent or no level qualifies.
    """
    from oceanarray.analysis.derived import velocity_levels

    if "east_velocity" not in ds or "north_velocity" not in ds:
        return None
    east_2d = ds["east_velocity"].transpose("time", "pressure").values.astype(float)
    north_2d = ds["north_velocity"].transpose("time", "pressure").values.astype(float)
    n_time = east_2d.shape[0]
    if n_time < 4:
        return None
    pressure = ds["pressure"].values
    if levels is None:
        levels = velocity_levels(east_2d, north_2d)
    if not levels:
        return None
    units = params.vunit("east_velocity") or unit_label(
        ds["east_velocity"].attrs.get("units", "m s-1")
    )
    from oceanarray.pipeline.dataset import time_step_seconds

    dt_s = max(float(time_step_seconds(ds)), 1.0)
    smooth_n = max(3, int(round(smooth_hours * 3600.0 / dt_s)))

    ncols = min(2, len(levels))
    nrows = -(-len(levels) // ncols)
    fig, axes, cax = square_axes_grid(width_in, nrows, ncols)
    flat = list(axes.ravel())
    sm = None
    for ax, k in zip(flat, levels, strict=False):
        label = f"{pressure[k]:.0f} dbar"
        e_sm = tukey_smooth(east_2d[:, k], smooth_n)
        n_sm = tukey_smooth(north_2d[:, k], smooth_n)
        mask = np.isfinite(e_sm) & np.isfinite(n_sm)
        if mask.sum() < 2:
            ax.text(
                0.5, 0.5, "No data", transform=ax.transAxes, ha="center", va="center"
            )
            plot_title(ax, label)
            continue
        t_frac = np.linspace(0.0, 1.0, n_time)[mask]
        sm = hodograph_panel(ax, e_sm[mask], n_sm[mask], t_frac, label, units)
    for ax in flat[len(levels) :]:
        ax.set_visible(False)
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


def _velocity_groups(ds: xr.Dataset) -> list[tuple[str, int]]:
    """Return ``(label, column)`` per velocity depth of a concatenated stack, deep-first.

    One column per depth (its ``serial`` is the depth's label); only columns with
    finite east and north velocity are kept, and a depth with no known depth goes
    last.
    """
    if "east_velocity" not in ds or "north_velocity" not in ds:
        return []
    u, v = ds["east_velocity"].values, ds["north_velocity"].values
    has = np.isfinite(u).any(axis=0) & np.isfinite(v).any(axis=0)
    depth = ds["depth"].values.astype(float)
    labels = [str(x) for x in ds["serial"].values]
    cols = sorted(
        np.flatnonzero(has),
        key=lambda j: -depth[j] if np.isfinite(depth[j]) else np.inf,
    )
    return [(labels[j], int(j)) for j in cols]


def draw_rose_outlines(
    ds: xr.Dataset,
    *,
    n_sectors: int = 16,
    width_in: float = report_tokens.W_FULL,
) -> plt.Figure | None:
    """One current rose per velocity depth of a concatenated stack, one outline per deployment.

    Each outline is the share (%) of that deployment's samples in each direction
    sector, so deployments of different length compare directly; every rose has
    the same ring scale. Direction is where the current flows to (0° = N). Values
    the Stack cat's ``mask_flags`` rule hides are left out.

    Parameters
    ----------
    ds : xr.Dataset
        A concatenated stack, one column per depth, with east/north velocity,
        ``depth``, ``serial`` (the depth's label), ``cell_deployment(time, N_LEVELS)``
        and ``deployment_name``.
    n_sectors : int, optional
        Number of direction sectors.
    width_in : float, optional
        Figure width in inches.

    Returns
    -------
    plt.Figure or None
        None when no level has velocity.
    """
    from oceanarray.plotters.helpers import deployment_line, short_names

    groups = _velocity_groups(ds)
    if not groups:
        return None
    u = helpers.masked(ds, "east_velocity")
    v = helpers.masked(ds, "north_velocity")
    cell_dep = ds["cell_deployment"].values
    names = short_names([str(n) for n in ds["deployment_name"].values])
    n_dep = len(names)
    edges = np.linspace(0.0, 2 * np.pi, n_sectors + 1)
    ncols = min(4, len(groups))
    nrows = int(np.ceil(len(groups) / ncols))
    panel = width_in / ncols
    fig, axs = plt.subplots(
        nrows,
        ncols,
        figsize=(width_in, panel * nrows + 0.4),
        subplot_kw={"projection": "polar"},
        squeeze=False,
        layout="constrained",
    )
    used: set[int] = set()
    rmax = 0.0
    for ax, (label, j) in zip(axs.ravel(), groups, strict=False):
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.set_xticks(np.radians([0, 90, 180, 270]))
        ax.set_xticklabels(["N", "E", "S", "W"])
        ax.tick_params(axis="y", labelsize="x-small")
        for k in np.unique(cell_dep[:, j][cell_dep[:, j] >= 0]):
            k = int(k)
            ok = np.isfinite(u[:, j]) & np.isfinite(v[:, j]) & (cell_dep[:, j] == k)
            if ok.sum() < 2:
                continue
            direction = np.radians(speed_direction(u[ok, j], v[ok, j])[1])
            counts, _ = np.histogram(direction, edges)
            share = 100.0 * counts / counts.sum()
            # The outline of the sector bars: an arc across each sector at its
            # share, joined radially to the next.
            theta = np.concatenate(
                [np.linspace(edges[i], edges[i + 1], 8) for i in range(n_sectors)]
            )
            radius = np.repeat(share, 8)
            ax.plot(
                np.r_[theta, theta[0]],
                np.r_[radius, radius[0]],
                linewidth=1.6,
                **deployment_line(k, n_dep),
            )
            used.add(k)
            rmax = max(rmax, float(share.max()))
        plot_title(ax, label)
    for ax in axs.ravel()[: len(groups)]:
        ax.set_ylim(0.0, rmax * 1.05)  # one ring scale, so roses compare
    for ax in axs.ravel()[len(groups) :]:
        ax.set_visible(False)
    fig.legend(
        handles=[
            plt.Line2D(
                [], [], linewidth=1.6, label=names[k], **deployment_line(k, n_dep)
            )
            for k in sorted(used)
        ],
        loc="outside lower center",
        ncols=min(len(used), 6),
        fontsize="small",
    )
    return fig


def _velocity_by_depth(ds: xr.Dataset) -> dict | None:
    """Return the velocity depths of a concatenated stack and what each deployment has there.

    Shared by the speed and direction panels so both use the same depths, the
    same flags left out and the same data rule.

    Parameters
    ----------
    ds : xr.Dataset
        A concatenated stack (see :func:`draw_speed_profile_by_deployment`).

    Returns
    -------
    dict or None
        ``groups`` (``(label, column)`` with a known depth), ``depth`` (per group),
        ``u``, ``v`` (time × column, the values the rule hides set to NaN), ``cell_dep``,
        ``names``, ``offset`` (spacing of side-by-side markers, m) and ``cells``:
        for each group, the deployments drawn there as ``(k, rows)``, ``rows`` the
        boolean time mask of that deployment's finite velocity. A deployment is
        drawn at a depth only when at least
        :data:`~oceanarray.config.parameters.MIN_RECORD_FRACTION` of its own steps
        at that depth have valid velocity, so a depth sampled for only part of a
        deployment is judged on its own record, not the whole deployment window.
        None when no depth has velocity.
    """
    from oceanarray.plotters.helpers import short_names

    depth = ds["depth"].values.astype(float)
    # Placed at depth: a depth with no known depth cannot be drawn.
    groups = [(label, j) for label, j in _velocity_groups(ds) if np.isfinite(depth[j])]
    if not groups:
        return None
    u = helpers.masked(ds, "east_velocity")
    v = helpers.masked(ds, "north_velocity")
    cell_dep = ds["cell_deployment"].values
    cells = []
    for _label, j in groups:
        drawn = []
        for k in np.unique(cell_dep[:, j][cell_dep[:, j] >= 0]):
            k = int(k)
            in_dep = cell_dep[:, j] == k
            rows = in_dep & np.isfinite(u[:, j]) & np.isfinite(v[:, j])
            if rows.sum() >= max(3, params.MIN_RECORD_FRACTION * int(in_dep.sum())):
                drawn.append((k, rows))
        cells.append(drawn)
    group_depth = [float(depth[j]) for _, j in groups]
    spacing = (
        np.diff(sorted(group_depth)) if len(group_depth) > 1 else np.array([200.0])
    )
    n_dep = max(1, ds.sizes.get("DEPLOYMENT", 1))
    offset = (
        0.8 * float(np.min(spacing[spacing > 0])) if (spacing > 0).any() else 100.0
    ) / n_dep
    return {
        "groups": groups,
        "depth": group_depth,
        "u": u,
        "v": v,
        "cell_dep": cell_dep,
        "names": short_names([str(n) for n in ds["deployment_name"].values]),
        "n": int(ds["deployment_name"].size),
        "offset": offset,
        "cells": cells,
    }


def _depth_axis(ax: plt.Axes, by: dict) -> None:
    """Label a depth axis with the velocity depths, in metres, deepest at the bottom."""
    ax.set_yticks(by["depth"])
    ax.set_yticklabels([f"{d:.0f}" for d in by["depth"]])
    ax.invert_yaxis()
    ax.set_ylabel("Nominal depth (m)")


def draw_speed_profile_by_deployment(
    ds: xr.Dataset, *, width_in: float = report_tokens.W_HALF
) -> plt.Figure | None:
    """Current-speed box per velocity depth and deployment of a concatenated stack, overlaid.

    Boxes of one depth sit at the depth's nominal depth, one per deployment in its
    color (two consecutive deployments share a color, the second hatched), so a
    change in speed between deployments shows as boxes that do not overlap.
    Values the ``mask_flags`` rule hides are left out, and a deployment's box is drawn at a
    depth only when at least
    :data:`~oceanarray.config.parameters.MIN_RECORD_FRACTION` of that depth's own
    steps in the deployment have valid velocity (a depth sampled for only part of
    the deployment is judged on its own record, not the whole window).

    Parameters
    ----------
    ds : xr.Dataset
        A concatenated stack, one column per depth, with east/north velocity,
        ``depth``, ``serial`` (the depth's label), ``cell_deployment(time, N_LEVELS)``
        and ``deployment_name``.
    width_in : float, optional
        Figure width in inches.

    Returns
    -------
    plt.Figure or None
        None when no level has velocity.
    """
    from matplotlib.patches import Patch

    from oceanarray.plotters.helpers import (
        deployment_color,
        deployment_hatch,
        legend_below,
    )

    by = _velocity_by_depth(ds)
    if by is None:
        return None
    speed, _ = speed_direction(by["u"], by["v"])
    box_w = by["offset"]

    fig, ax = plt.subplots(figsize=(width_in, width_in * 0.9), layout="constrained")
    used: set[int] = set()
    for (_label, j), y, drawn in zip(
        by["groups"], by["depth"], by["cells"], strict=True
    ):
        for m, (k, rows) in enumerate(drawn):
            color = deployment_color(k, by["n"])
            # Side by side about the depth's value, so deployments do not overlap.
            ax.boxplot(
                speed[rows, j],
                positions=[y + (m - (len(drawn) - 1) / 2) * box_w],
                widths=box_w,
                orientation="horizontal",
                patch_artist=True,
                showfliers=False,
                boxprops={
                    "facecolor": color,
                    "alpha": 0.45,
                    "edgecolor": color,
                    "hatch": deployment_hatch(k, by["n"]),
                },
                medianprops={"color": color, "linewidth": 1.5},
                whiskerprops={"color": color},
                capprops={"color": color},
            )
            used.add(k)
    if not used:
        plt.close(fig)
        return None
    _depth_axis(ax, by)
    ax.set_xlabel(
        params.vlabel("current_speed")
        if "current_speed" in params.VARIABLES
        else "Current speed (m/s)"
    )
    ax.set_xlim(left=0)
    grid_despine(ax, axis="x")
    legend_below(
        fig,
        [
            Patch(
                facecolor=deployment_color(k, by["n"]),
                edgecolor=deployment_color(k, by["n"]),
                alpha=0.45,
                hatch=deployment_hatch(k, by["n"]),
                label=by["names"][k],
            )
            for k in sorted(used)
        ],
    )
    plot_title(ax, "Current speed by depth and deployment")
    return fig


def _daily_mean(time: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Return the means of *values* in each UTC day of *time* that has a finite value."""
    import pandas as pd

    series = pd.Series(values.astype(np.float64), index=pd.to_datetime(time))
    return series.resample("1D").mean().dropna().to_numpy()


def draw_direction_profile_by_deployment(
    ds: xr.Dataset, *, width_in: float = report_tokens.W_HALF
) -> plt.Figure | None:
    """Draw the current direction per velocity depth and deployment of a concatenated stack.

    The companion of :func:`draw_speed_profile_by_deployment`: the same depths,
    flags and data rule, the same deployment colors and side-by-side placement.
    Direction is where the water flows **to**, degrees clockwise from true north.
    Each marker is the direction of the deployment's mean velocity at that depth
    (from daily means, so tides do not count); its bar is the circular standard
    deviation of the daily-mean directions, stopped at the axis edge with an arrow
    where it runs past. The direction axis is centred on the mean direction of all
    depths and deployments, ±180°, so a flow near north is not split across 0°/360°.

    Parameters
    ----------
    ds : xr.Dataset
        A concatenated stack (see :func:`draw_speed_profile_by_deployment`).
    width_in : float, optional
        Figure width in inches.

    Returns
    -------
    plt.Figure or None
        None when no level has velocity.
    """
    from oceanarray.plotters.helpers import (
        deployment_color,
        deployment_marker,
        legend_below,
    )

    by = _velocity_by_depth(ds)
    if by is None:
        return None
    time = ds["time"].values
    points = []  # (k, y, direction °, spread °)
    sum_u = sum_v = 0.0
    for (_label, j), y, drawn in zip(
        by["groups"], by["depth"], by["cells"], strict=True
    ):
        for m, (k, rows) in enumerate(drawn):
            du = _daily_mean(time[rows], by["u"][rows, j])
            dv = _daily_mean(time[rows], by["v"][rows, j])
            n = min(du.size, dv.size)
            if n < 3:
                continue
            du, dv = du[:n], dv[:n]
            mu, mv = float(du.mean()), float(dv.mean())
            angle = np.arctan2(du, dv)
            resultant = float(np.hypot(np.cos(angle).mean(), np.sin(angle).mean()))
            spread = (
                float(np.degrees(np.sqrt(-2.0 * np.log(resultant))))
                if resultant > 0
                else 180.0
            )
            offset = (m - (len(drawn) - 1) / 2) * by["offset"]
            points.append(
                (
                    k,
                    y + offset,
                    float(flow_direction(mu, mv)),
                    min(spread, 180.0),
                )
            )
            sum_u += mu
            sum_v += mv
    if not points:
        return None
    centre = float(flow_direction(sum_u, sum_v))

    fig, ax = plt.subplots(figsize=(width_in, width_in * 0.9), layout="constrained")
    used: set[int] = set()
    lo_edge, hi_edge = centre - 180.0, centre + 180.0
    for k, y, direction, spread in points:
        x = centre + ((direction - centre + 180.0) % 360.0) - 180.0
        color = deployment_color(k, by["n"])
        # A bar past the axis edge stops there, with an arrow pointing on.
        lo, hi = max(x - spread, lo_edge), min(x + spread, hi_edge)
        ax.plot([lo, hi], [y, y], color=color, lw=1.0)
        for end, clipped, arrow in (
            (lo, lo > x - spread, "<"),
            (hi, hi < x + spread, ">"),
        ):
            ax.plot(end, y, marker=arrow if clipped else "|", color=color, ms=4)
        ax.plot(
            x, y, marker=deployment_marker(k, by["n"]), color=color, ms=5, ls="none"
        )
        used.add(k)
    _depth_axis(ax, by)
    ax.set_xlim(lo_edge, hi_edge)
    ticks = np.arange(
        np.ceil((centre - 180.0) / 90.0) * 90.0, centre + 180.0 + 1e-9, 90.0
    )
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{t % 360:.0f}" for t in ticks])
    ax.set_xlabel("Current direction, toward (°T)")
    grid_despine(ax, axis="x")
    legend_below(
        fig,
        [
            plt.Line2D(
                [],
                [],
                ls="none",
                marker=deployment_marker(k, by["n"]),
                color=deployment_color(k, by["n"]),
                label=by["names"][k],
            )
            for k in sorted(used)
        ],
    )
    plot_title(ax, "Current direction by depth and deployment")
    return fig
