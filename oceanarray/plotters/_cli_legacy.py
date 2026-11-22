"""Legacy CLI plot functions, kept pending the ``oceanarray plot <file>`` redesign.

STATUS: this module is the last remnant of the pre-tier plotting code. Only the
three functions the CLI still calls remain — ``draw_microcat_raw`` and
``draw_aquadopp_raw`` (``oceanarray process --plot``) and
``draw_mooring_timeseries`` (``oceanarray plot``). They are scheduled for
replacement by a single file-oriented ``oceanarray plot <file>`` built on the
report's ``_make_instrument_b64s``; this module is deleted then. Do not
build new code on these functions or add to this module — new plotting belongs in
the three-tier ``plotters/`` package.
"""

import logging
import re
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

from oceanarray import paths
from oceanarray.config import parameters as params
from oceanarray.plotters.helpers import nominal_depth_text, serial_text

log = logging.getLogger(__name__)

#: The report style, applied while each figure is drawn and saved (never set
#: globally, so later figures in the same process are not changed).
_STYLE = plt.style.context(str(params.MPLSTYLE))


@_STYLE
def draw_microcat_raw(ds, save_path=None):
    """Plot temperature, conductivity, and pressure from a raw/stage2 microcat NetCDF.

    Works with seasenselib variable names (temperature, conductivity, pressure).
    """
    panels = [("temperature", "Temperature [°C]", "tab:red")]
    if "conductivity" in ds.data_vars:
        panels.append(("conductivity", "Conductivity [mS/cm]", "tab:blue"))
    if "pressure" in ds.data_vars:
        panels.append(("pressure", "Pressure [dbar]", "tab:green"))

    nrows = len(panels)
    fig, axs = plt.subplots(nrows, 1, figsize=(12, 3 * nrows), sharex=True)
    if nrows == 1:
        axs = [axs]

    for ax, (var, label, color) in zip(axs, panels, strict=False):
        ax.plot(ds["time"], ds[var], color=color, linewidth=0.5)
        ax.set_ylabel(label)
    axs[-1].set_xlabel("Time")

    axs[0].set_title(
        f"MicroCAT s/n: {serial_text(ds)}  |  Target depth: {nominal_depth_text(ds)}"
    )

    locator = mdates.AutoDateLocator()
    formatter = mdates.ConciseDateFormatter(locator)
    axs[-1].xaxis.set_major_locator(locator)
    axs[-1].xaxis.set_major_formatter(formatter)
    plt.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")

    return fig


@_STYLE
def draw_aquadopp_raw(ds, save_path=None):
    """Plot east velocity, north velocity, and pressure from a raw/stage2 Aquadopp NetCDF."""

    # Pick pressure variable — prefer 'pressure', fall back to 'pressure_1'
    pvar = next((v for v in ("pressure", "pressure_1") if v in ds.data_vars), None)

    panels = [
        ("east_velocity", "East velocity [m/s]", "tab:blue", False),
        ("north_velocity", "North velocity [m/s]", "tab:orange", False),
    ]
    if pvar:
        panels.append((pvar, "Pressure [dbar]", "tab:green", True))
    for tvar, tlabel in (("Pitch", "Pitch [°]"), ("Roll", "Roll [°]")):
        if tvar in ds.data_vars:
            panels.append((tvar, tlabel, "tab:purple", False))

    nrows = len(panels)
    fig, axs = plt.subplots(nrows, 1, figsize=(12, 3 * nrows), sharex=True)
    if nrows == 1:
        axs = [axs]

    for ax, (var, label, color, invert) in zip(axs, panels, strict=False):
        ax.plot(ds["time"], ds[var], color=color, linewidth=0.5)
        if var in ("east_velocity", "north_velocity"):
            ax.axhline(0, color="k", linewidth=0.5, linestyle="--")
        ax.set_ylabel(label)
        if invert:
            vmin = float(ds[var].min())
            vmax = float(ds[var].max())
            pad = max((vmax - vmin) * 0.1, 0.5)
            ax.set_ylim(vmax + pad, vmin - pad)
    axs[-1].set_xlabel("Time")

    axs[0].set_title(
        f"Aquadopp s/n: {serial_text(ds)}  |  Target depth: {nominal_depth_text(ds)}"
    )

    locator = mdates.AutoDateLocator()
    axs[-1].xaxis.set_major_locator(locator)
    axs[-1].xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    plt.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")

    return fig


# ---------------------------------------------------------------------------
# Multi-instrument mooring overview plot
# ---------------------------------------------------------------------------


def _instrument_label(nc_path: Path, ds: xr.Dataset) -> str:
    """Build a short instrument label, e.g. 'MC2942 868m'."""
    instrument_dir = nc_path.parent.name
    prefix = params.INSTRUMENT_ABBREV.get(instrument_dir, instrument_dir[:2].upper())

    # Strip trailing non-alphanumeric chars (e.g. * markers in serial strings)
    serial = re.sub(r"[^\w]", "", serial_text(ds))
    depth = nominal_depth_text(ds).replace(" ", "")
    return f"{prefix}{serial} {depth if depth != '?' else ''}".strip()


@_STYLE
def draw_mooring_timeseries(
    proc_dir: Path,
    mooring: str,
    var_y: str = "temperature",
    var_color: str = None,
    colormap: str = None,
    downsample_seconds: int = None,
    markersize: float = None,
    save_path: Path = None,
    show: bool = True,
) -> plt.Figure:
    """Plot all instruments in a mooring on shared axes.

    Two modes depending on whether *var_color* is supplied:

    **Scatter mode** (``var_color`` given)
        x = time, y = ``var_y`` (typically ``pressure``), colour = ``var_color``
        (typically ``temperature``).  Instruments without both variables are
        skipped.  The y-axis is inverted so depth increases downward.

    **Line mode** (``var_color`` is None)
        x = time, y = ``var_y``.  Each instrument that has the variable is
        plotted as a separate line and included in the legend.

    Parameters
    ----------
    proc_dir : Path
        Path to the mooring's proc directory (e.g. ``<proc-dir>/<mooring>``).
    mooring : str
        Mooring name, e.g. ``dsG3_1_2026``.
    var_y : str
        Variable to place on the y-axis (default: ``temperature``).
    var_color : str, optional
        Variable to use for scatter-plot colour.  If None, a line plot is made.
    colormap : str, optional
        Matplotlib colormap name.  Defaults to ``parameters.DEFAULT_COLORMAP``.
    downsample_seconds : int, optional
        Resample interval in seconds.  Defaults to ``parameters.DOWNSAMPLE_SECONDS``.
    save_path : Path, optional
        If given, save the figure here instead of displaying it.

    Returns
    -------
    matplotlib.figure.Figure

    """
    cmap_name = colormap or params.DEFAULT_COLORMAP
    dt_s = downsample_seconds or params.DOWNSAMPLE_SECONDS

    mooring_proc = Path(proc_dir) / mooring
    use_files = sorted(mooring_proc.rglob(paths.stage_glob(2)))

    if not use_files:
        raise FileNotFoundError(f"No _stage2.nc files found under {mooring_proc}")

    scatter_mode = var_color is not None

    # ------------------------------------------------------------------
    # Load and resample all instruments that have the required variables
    # ------------------------------------------------------------------
    segments = []  # list of (label, time_array, y_array, color_array_or_None)

    for nc in use_files:
        try:
            ds = xr.open_dataset(nc, decode_timedelta=False)
        except (
            OSError,
            ValueError,
        ) as e:  # one unreadable file must not abort the plot
            log.warning("%s: left out of the plot, cannot be read (%s)", nc.name, e)
            continue

        has_y = var_y in ds.data_vars
        has_c = (not scatter_mode) or (var_color in ds.data_vars)
        if not (has_y and has_c):
            ds.close()
            continue

        try:
            keep = [
                v
                for v in ([var_y] + ([var_color] if scatter_mode else []))
                if v in ds.data_vars
            ]
            if len(ds["time"]) > 1:
                median_dt = float(
                    np.median(
                        np.diff(ds["time"].values)
                        .astype("timedelta64[s]")
                        .astype(float)
                    )
                )
                step = max(1, round(dt_s / median_dt))
            else:
                step = 1
            ds_small = ds[keep].isel(time=slice(None, None, step)).load()
        except (
            OSError,
            KeyError,
            ValueError,
            IndexError,
            TypeError,
            ArithmeticError,
        ) as e:
            log.warning(
                "%s: left out of the plot, cannot be subsampled (%s)", nc.name, e
            )
            ds.close()
            continue

        label = _instrument_label(nc, ds)
        ds.close()

        t = ds_small["time"].values
        y = ds_small[var_y].values
        c = ds_small[var_color].values if scatter_mode else None
        segments.append((label, t, y, c))

    if not segments:
        raise ValueError(
            f"No instruments have variable '{var_y}'"
            + (f" and '{var_color}'" if scatter_mode else "")
        )

    # ------------------------------------------------------------------
    # Compute colour limits from combined 5th–95th percentile
    # ------------------------------------------------------------------
    if scatter_mode:
        all_c = np.concatenate([c for _, _, _, c in segments if c is not None])
        all_c = all_c[np.isfinite(all_c)]
        vmin = float(np.percentile(all_c, params.COLORBAR_PLOW))
        vmax = float(np.percentile(all_c, params.COLORBAR_PHIGH))

    # ------------------------------------------------------------------
    # Draw
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=params.FIGURE_SIZE_WIDE)

    if scatter_mode:
        cmap = plt.get_cmap(cmap_name)
        sc = None
        for label, t, y, c in segments:
            sc = ax.scatter(
                t,
                y,
                c=c,
                cmap=cmap,
                vmin=vmin,
                vmax=vmax,
                s=markersize if markersize is not None else 4,
                alpha=0.6,
                rasterized=True,
                label=label,
            )
        if sc is not None:
            cbar = fig.colorbar(sc, ax=ax, pad=0.02)
            cbar.set_label(var_color.replace("_", " ").title())
        ax.invert_yaxis()
        ax.set_ylabel(var_y.replace("_", " ").title())
    else:
        for label, t, y, _ in segments:
            ax.plot(t, y, linewidth=0.8, label=label)
        ax.set_ylabel(var_y.replace("_", " ").title())
        ax.legend(fontsize=9, loc="best", ncol=2)

    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.set_xlabel("Time")

    title_parts = [mooring, f"dt={dt_s}s"]
    if scatter_mode:
        title_parts.append(
            f"colour: {var_color}  [{params.COLORBAR_PLOW}–{params.COLORBAR_PHIGH}th pct]"
        )
    ax.set_title("  |  ".join(title_parts))

    plt.tight_layout()

    if save_path:
        fig.savefig(save_path, bbox_inches="tight")
    if show:
        plt.show()
    if not show or save_path:
        plt.close(fig)

    return fig
