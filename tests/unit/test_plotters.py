from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import pytest
import xarray as xr

matplotlib.use("Agg")  # Use non-interactive backend for testing


from oceanarray import inspect
from oceanarray.pipeline.gridders.rapid_interp import plot_climatology
from oceanarray.plotters.current import draw_instrument_rose
from oceanarray.reports._vendored.design_tokens import W_FULL


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


_EGM2_CM = (
    Path(__file__).resolve().parents[1]
    / "fixtures/proc/egM2_concat/egM2_2018/aquadopp/egM2_2018_CM2000_stage3.nc"
)


def test_instrument_rose_is_one_stack_page_rose():
    """One instrument's rose is the stack page's design: one rose a quarter wide, titled."""
    import matplotlib.pyplot as plt

    fig = draw_instrument_rose(_EGM2_CM, width_in=W_FULL, title="CM2000 · 2000 m")
    assert fig is not None
    polar = [ax for ax in fig.axes if ax.name == "polar" and ax.get_visible()]
    assert len(polar) == 1
    assert polar[0].get_title() == "CM2000 · 2000 m"
    assert fig.get_size_inches()[1] == pytest.approx(W_FULL / 4 + 0.65 + 0.6)
    # Rings are labelled in percent of the record.
    assert all(t.get_text().endswith("%") for t in polar[0].get_yticklabels())
    plt.close(fig)


def test_rose_ring_labels_sit_where_the_wedges_are_shortest():
    """egM2 CM2000: the percent labels go along the quietest bearing, not on a wedge."""
    import matplotlib.pyplot as plt

    from oceanarray.physics import speed_direction

    with xr.open_dataset(_EGM2_CM, engine="netcdf4") as ds:
        east = ds["east_velocity"].values
        north = ds["north_velocity"].values
    fig = draw_instrument_rose(_EGM2_CM, width_in=W_FULL)
    ax = next(a for a in fig.axes if a.name == "polar" and a.get_visible())
    _, direction = speed_direction(east, north)
    direction = direction[np.isfinite(direction)]
    sector = np.clip(np.digitize(direction, np.linspace(0, 360, 17)) - 1, 0, 15)
    share = np.bincount(sector, minlength=16) / len(direction)
    span = np.maximum.reduce([np.roll(share, 1), share, np.roll(share, -1)])
    at = int(ax.get_rlabel_position() // 22.5)
    assert span[at] == span.min()
    assert share[at] < share.max()
    plt.close(fig)


def test_instrument_rose_draws_suspect_values_in_their_own_rose():
    """dune2 Aquadopp 9920: good and suspect (oceanarray) roses, each on its own scale."""
    import matplotlib.pyplot as plt

    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures/proc/dune2_1_2026/aquadopp/dune2_1_2026_9920_stage3.nc"
    )
    fig = draw_instrument_rose(path, width_in=W_FULL, title="9920")
    polar = [ax for ax in fig.axes if ax.name == "polar" and ax.get_visible()]
    titles = [ax.get_title() for ax in polar]
    assert len(polar) == 2 and titles[0].startswith("Good: ")
    assert titles[1] == "Suspect (oceanarray): 1,127 values"
    assert polar[0].get_ylim() != polar[1].get_ylim()
    plt.close(fig)


def test_data_histogram_keeps_suspect_values_without_a_rule():
    """dune2 Aquadopp 9920: its 1,127 suspect velocities are among the values kept."""
    import matplotlib.pyplot as plt

    from oceanarray.plotters.diagnostic import draw_data_histogram

    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures/proc/dune2_1_2026/aquadopp/dune2_1_2026_9920_stage3.nc"
    )
    with xr.open_dataset(path) as ds:
        east = ds["east_velocity"].values
        flags = ds["east_velocity_qc"].values
    fig = draw_data_histogram(path)
    ax = next(a for a in fig.axes if a.get_xlabel().startswith("Eastward velocity"))
    all_bars, kept = ax.containers  # grey (every value), then the values kept
    n_kept = sum(bar.get_height() for bar in kept)
    plt.close(fig)
    assert int((np.isfinite(east) & (flags == 3)).sum()) == 1127
    assert n_kept == int((np.isfinite(east) & (flags != 4)).sum())


def test_instrument_rose_leaves_out_what_the_rule_hides():
    """A rule hiding every flag the file has leaves nothing to draw."""
    import matplotlib.pyplot as plt

    from oceanarray.mask_rules import resolve_mask_flags

    everything = resolve_mask_flags([0, 1, 2, 3, 4, 5, 7, 8, 9])
    fig = draw_instrument_rose(_EGM2_CM, width_in=W_FULL, rule=everything)
    polar = [ax for ax in fig.axes if ax.name == "polar"]
    assert not any(ax.get_visible() for ax in polar)
    plt.close(fig)


_OLD_STAGE3 = Path(
    "tests/fixtures/proc/egM2_concat/egM2_2018/microcat/egM2_2018_13220_stage3.nc"
)


def test_plot_titles_read_files_before_0_6_1():
    """InstrDepth and serial_number (text or integer) reach the titles and labels."""
    from oceanarray.config import parameters as params
    from oceanarray.plotters import _cli_legacy
    from oceanarray.plotters.helpers import nominal_depth_text, serial_text

    with xr.open_dataset(_OLD_STAGE3) as ds:
        assert (serial_text(ds), nominal_depth_text(ds)) == ("13220", "1500 m")
        label = _cli_legacy._instrument_label(_OLD_STAGE3, ds)
        title = _cli_legacy.draw_microcat_raw(ds).axes[0].get_title()
    assert label == f"{params.INSTRUMENT_ABBREV['microcat']}13220 1500m"
    assert title == "MicroCAT s/n: 13220  |  Target depth: 1500 m"
    old_int = Path(
        "tests/fixtures/proc/before_sensor_records/dune2_1_2026_2941_stage3.nc"
    )
    with xr.open_dataset(old_int) as ds:
        assert (serial_text(ds), nominal_depth_text(ds)) == ("2941", "320 m")
