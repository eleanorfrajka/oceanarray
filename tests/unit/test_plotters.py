import matplotlib
import numpy as np
import pandas as pd
import pytest
import xarray as xr

matplotlib.use("Agg")  # Use non-interactive backend for testing


from oceanarray import inspect
from oceanarray.plotters.current import draw_instrument_rose
from oceanarray.reports._vendored.design_tokens import W_FULL
from oceanarray.tools.rapid_interp import plot_climatology


def test_plot_climatology_runs():
    temp = np.linspace(0, 10, 50)
    months = np.arange(1, 13)
    data = np.random.rand(12, 50)
    clim_ds = xr.Dataset(
        {
            "dTdp": (("month", "TEMP"), data),
            "TEMP": ("TEMP", temp),
        },
        coords={"month": months},
    )
    fig, ax = plot_climatology(clim_ds, var="dTdp")
    assert fig is not None
    assert ax is not None


def test_show_variables_returns_styler():
    ds = xr.Dataset(
        {
            "TEMP": ("TIME", np.random.rand(5)),
            "PRES": ("TIME", np.random.rand(5)),
        },
        coords={"TIME": pd.date_range("2021-01-01", periods=5)},
    )
    result = inspect.variables(ds)
    assert hasattr(result, "data") or hasattr(result, "render")


def test_show_attributes_returns_dataframe():
    ds = xr.Dataset(attrs={"title": "Test Dataset", "institution": "Ocean Lab"})
    df = inspect.attributes(ds)
    assert isinstance(df, pd.DataFrame)
    assert "Attribute" in df.columns
    assert "Value" in df.columns


def test_show_variables_on_xarray_dataset():
    import numpy as np
    import xarray as xr

    time = np.arange("2023-01", "2023-01-10", dtype="datetime64[D]")
    ds = xr.Dataset(
        {
            "temperature": ("TIME", np.random.rand(len(time))),
        },
        coords={"TIME": time},
    )
    ds["temperature"].attrs["units"] = "degC"
    ds["temperature"].attrs["comment"] = "Surface temperature"
    ds["temperature"].attrs["standard_name"] = "sea_water_temperature"

    styled = inspect.variables(ds)
    html = styled.to_html()
    assert "<table" in html  # crude but effective confirmation


def test_show_attributes_from_dataset():
    import xarray as xr

    ds = xr.Dataset()
    ds.attrs["title"] = "Test Dataset"
    ds.attrs["institution"] = "Ocean Lab"

    df = inspect.attributes(ds)
    assert "Attribute" in df.columns
    assert "Value" in df.columns


def _write_sample_nc(path):
    """Write a small netCDF file with one variable and two global attributes."""
    ds = xr.Dataset(
        {"temperature": ("TIME", np.arange(5.0))},
        coords={"TIME": np.arange(5)},
        attrs={"title": "Test", "institution": "Ocean Lab"},
    )
    ds["temperature"].attrs["units"] = "degC"
    ds.to_netcdf(path)


def test_inspect_vars_from_file_path(tmp_path):
    """inspect.variables() opens a netCDF file path (regression: was xr.Dataset, now open_dataset)."""
    nc = tmp_path / "m.nc"
    _write_sample_nc(nc)
    styled = inspect.variables(str(nc))
    assert "temperature" in styled.to_html()
    # File must be released so it can be reopened for writing afterwards.
    xr.open_dataset(nc).close()


def test_inspect_attrs_from_file_path(tmp_path):
    """inspect.attributes() reads a netCDF file path and does not leak the handle."""
    nc = tmp_path / "m.nc"
    _write_sample_nc(nc)
    df = inspect.attributes(str(nc))
    assert set(df["Attribute"]) >= {"title", "institution"}
    xr.open_dataset(nc).close()


def test_inspect_vars_empty_dataset():
    """inspect.variables() on a variable-less Dataset returns an empty table, not AttributeError."""
    styled = inspect.variables(xr.Dataset())
    assert len(styled.data) == 0


def test_pcolormesh_panel_all_nan_slice():
    """pcolormesh_panel renders an all-NaN field instead of crashing in log10(nan)."""
    import matplotlib.pyplot as plt

    from oceanarray.plotters.primitives import pcolormesh_panel

    fig, ax = plt.subplots()
    data = np.full((4, 6), np.nan)
    pc = pcolormesh_panel(fig, ax, data, np.arange(6), np.arange(4), title="T")
    assert pc is not None
    plt.close(fig)


def test_pcolormesh_panel_returns_mappable():
    """pcolormesh_panel draws a (pressure × time) field and returns the mappable."""
    import matplotlib.pyplot as plt

    from oceanarray.plotters.primitives import pcolormesh_panel

    time = np.arange("2023-01-01", "2023-01-11", dtype="datetime64[D]")
    pressure = np.array([0.0, 50.0, 100.0, 150.0])
    data = np.random.rand(pressure.size, time.size)  # shaped (pressure, time)

    fig, ax = plt.subplots()
    pc = pcolormesh_panel(fig, ax, data, time, pressure, title="Temp", units="°C")
    assert pc is not None
    assert ax.yaxis_inverted()  # pressure increases downward
    plt.close(fig)


def test_draw_trajectory_lc_array_length():
    """LineCollection colour array must have N-1 entries for N trajectory points."""
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    from oceanarray.plotters.primitives import draw_trajectory

    x = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    y = np.array([0.0, 1.0, 0.0, -1.0, 0.0])
    color = np.linspace(5.0, 25.0, 5)  # N=5 values → N-1=4 segments

    fig = draw_trajectory(x, y, color_data=color)
    ax = fig.axes[0]
    lcs = [c for c in ax.collections if isinstance(c, LineCollection)]
    assert lcs, "expected a LineCollection in the trajectory figure"
    arr = lcs[0].get_array()
    assert len(arr) == len(x) - 1, (
        f"LineCollection has {len(arr)} colour values for {len(x)} points; "
        f"expected {len(x) - 1} (one per segment)"
    )
    plt.close(fig)


def test_nice_axis_limits_pads_and_rounds():
    """1/99 percentiles, +5% pad each side, rounded outward to a clean step."""
    from oceanarray.plotters.ts import _nice_axis_limits

    # linspace 0..100 has exact 1st/99th percentiles of 1 and 99 (range 98):
    # 1 - 0.05*98 = -3.9 floors to -4; 99 + 4.9 = 103.9 ceils to 104 (step 1).
    x = np.linspace(0.0, 100.0, 101)
    assert _nice_axis_limits(x) == (-4.0, 104.0)


def test_nice_axis_limits_excludes_outliers():
    """Outliers past the 1/99 percentiles must not stretch the limits."""
    from oceanarray.plotters.ts import _nice_axis_limits

    x = np.concatenate([np.linspace(34.66, 35.18, 999), [30.0, 40.0]])
    lo, hi = _nice_axis_limits(x)
    assert 34.5 < lo < 34.66 and 35.18 < hi < 35.4  # outliers 30/40 excluded


def test_nice_axis_limits_degenerate_range():
    """Zero-width data falls back to a symmetric ±0.5 window (no crash)."""
    from oceanarray.plotters.ts import _nice_axis_limits

    lo, hi = _nice_axis_limits(np.full(50, 5.0))
    assert lo == 4.5 and hi == 5.5


def test_ytick_reserve_in_no_shrink_for_short_labels():
    """<=4-character y-tick labels keep the base reserve (no square shrink)."""
    from oceanarray.plotters.primitives import _SQ_LABEL_IN, ytick_reserve_in

    # "-800" and "-250" are 4 characters -> base reserve unchanged.
    assert ytick_reserve_in(np.array([-800.0, 50.0])) == _SQ_LABEL_IN
    assert ytick_reserve_in(np.array([-250.0, 120.0])) == _SQ_LABEL_IN


def test_ytick_reserve_in_grows_for_wide_labels():
    """Wide (>=5-char) tick labels reserve more left margin, monotonically."""
    from oceanarray.plotters.primitives import (
        _SQ_LABEL_IN,
        _SQ_PER_CHAR_IN,
        ytick_reserve_in,
    )

    # "-1600" is 5 characters -> base + one extra char.
    assert ytick_reserve_in(np.array([-1600.0, 0.0])) == _SQ_LABEL_IN + _SQ_PER_CHAR_IN
    # "-25000" (6 chars) reserves strictly more than "-1600" (5 chars).
    assert ytick_reserve_in(np.array([-25000.0, 0.0])) > ytick_reserve_in(
        np.array([-1600.0, 0.0])
    )


def test_ytick_reserve_in_all_nan_returns_base():
    """All-NaN y falls back to the base reserve rather than raising."""
    from oceanarray.plotters.primitives import _SQ_LABEL_IN, ytick_reserve_in

    assert ytick_reserve_in(np.full(5, np.nan)) == _SQ_LABEL_IN


def test_distinct_line_styles_unique_pairs():
    """Up to 32 lines get a unique (color, linestyle) pair; 33rd clamps, no raise."""
    from oceanarray.plotters.helpers import OKABE_ITO, distinct_line_styles

    styles = distinct_line_styles(29)
    assert len(styles) == 29
    assert len({(c, ls) for c, ls, _lw in styles}) == 29  # all distinct
    # Colour cycles the 8-colour Okabe-Ito palette.
    assert styles[0][0] == OKABE_ITO[0]
    assert styles[8][0] == OKABE_ITO[0] and styles[8][1] == "--"  # next linestyle
    # Linewidth increases with the linestyle group; dash-dot and dotted both thicker.
    lw_by_ls = {ls: lw for _c, ls, lw in styles}
    assert lw_by_ls["-"] < lw_by_ls["--"] < lw_by_ls["-."]
    assert lw_by_ls["-."] == lw_by_ls[":"]
    # >32 lines clamp the linestyle group rather than raising.
    assert len(distinct_line_styles(40)) == 40
    assert distinct_line_styles(0) == []


def test_plot_title_is_left_aligned():
    """plot_title left-aligns the panel title by default."""
    import matplotlib.pyplot as plt

    from oceanarray.plotters.primitives import plot_title

    fig, ax = plt.subplots()
    t = plot_title(ax, "Panel")
    assert t.get_horizontalalignment() == "left"
    plt.close(fig)


def test_figure_title_is_centered():
    """figure_title places a centred figure-level suptitle."""
    import matplotlib.pyplot as plt

    from oceanarray.plotters.primitives import figure_title

    fig, _ax = plt.subplots()
    t = figure_title(fig, "Figure")
    assert t.get_horizontalalignment() == "center"
    assert fig._suptitle is not None
    plt.close(fig)


def _rose_ds(n=240, suspect=0):
    """1-D ENU-velocity dataset for a rose: no magnetic tier, *suspect* samples flagged 3."""
    ang = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    qc = np.ones(n, dtype=int)
    qc[:suspect] = 3
    return xr.Dataset(
        {
            "east_velocity": ("time", np.cos(ang)),
            "north_velocity": ("time", np.sin(ang)),
            "east_velocity_qc": ("time", qc),
        },
        coords={"time": pd.date_range("2021-01-01", periods=n, freq="h")},
    )


def test_instrument_rose_lone_tier_is_boxed_to_third_slot(tmp_path):
    """One QC tier gives one rose at third-slot height, centred in a narrow box.

    The narrow box makes the circle width-limited so its 2-line title clears; a
    full-width lone rose fills the short figure and clips the title's first line.
    """
    path = tmp_path / "one.nc"
    _rose_ds().to_netcdf(path)
    fig = draw_instrument_rose(path, width_in=W_FULL)
    assert fig is not None
    assert len(fig.axes) == 1
    assert fig.get_size_inches()[1] == pytest.approx(W_FULL / 3 + 0.4)
    assert fig.subplotpars.left > 1 / 3
    assert fig.subplotpars.right < 2 / 3
    # The bug this guards: a full-width lone rose fills the short figure and the
    # 2-line title clips above it.  Assert the rendered title stays in the figure.
    fig.canvas.draw()
    ax = fig.axes[0]
    assert "\n" in ax.get_title()  # the ENU-true panel title is two lines
    assert ax.title.get_window_extent().y1 <= fig.bbox.y1 + 1


def test_instrument_rose_two_tiers_keep_half_slot_height(tmp_path):
    """Two QC tiers (good + suspect) keep width_in/2 height; the lone-rose cap must not shrink them."""
    path = tmp_path / "two.nc"
    _rose_ds(suspect=60).to_netcdf(path)
    fig = draw_instrument_rose(path, width_in=W_FULL)
    assert len(fig.axes) == 2
    assert fig.get_size_inches()[1] == pytest.approx(W_FULL / 2 + 0.4)
