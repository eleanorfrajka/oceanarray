"""The stack-page draw functions, on the 22-instrument dune2 stack fixture.

The fixture's pressure carries real QC flags — good (1), interpolated (8),
potentially correctable bad (3) and bad (4) — so the masking rule is checked on
the flags the pipeline writes, not on made-up ones.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pytest
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.plotters.current import (
    draw_aquadopp_speed_profile,
    draw_aquadopp_tilt_panels,
)
from oceanarray.plotters.diagnostic import draw_instrument_spacing
from oceanarray.plotters.timeseries import draw_stack_timeseries

_STACK = (
    Path(__file__).parent.parent
    / "fixtures"
    / "proc"
    / "dune2_1_2026"
    / "dune2_1_2026_stack.nc"
)


@pytest.fixture(scope="module")
def ds() -> xr.Dataset:
    """The 22-instrument dune2 stack, loaded."""
    with xr.open_dataset(_STACK) as stack:
        return stack.load()


def _lines(fig) -> dict[str, np.ndarray]:
    """Return each labelled line's y-data, keyed by its label (the serial)."""
    return {
        line.get_label(): np.asarray(line.get_ydata(), dtype=float)
        for ax in fig.axes
        for line in ax.get_lines()
        if not line.get_label().startswith("_")
    }


def test_pressure_row_keeps_interpolated_and_drops_bad_values(ds):
    """Flag-8 pressure is drawn; flags 3, 4 and 9 are not."""
    fig = draw_stack_timeseries(
        ds, [("pressure", "p", True, {"adcp"})], color_var="temperature"
    )
    lines = _lines(fig)
    plt.close(fig)
    serials = [str(s) for s in ds["serial"].values]
    qc = ds["pressure_qc"].values
    step = max(1, ds.sizes["time"] // 5000)
    # 2942 has only interpolated (8) pressure: a ">= 3" mask would drop it entirely.
    assert "2942" in lines
    for serial in ("2942", "240231", "3026"):
        i = serials.index(serial)
        y = lines[serial]
        flags = qc[::step, i]
        assert np.all(np.isnan(y[np.isin(flags, params.QC_BAD_FLAGS)])), serial
        kept = ~np.isin(flags, params.QC_BAD_FLAGS) & np.isfinite(
            ds["pressure"].values[::step, i]
        )
        assert np.all(np.isfinite(y[kept])), serial


def test_one_legend_entry_per_instrument_drawn(ds):
    """The rows share one legend listing each drawn instrument once, deep-first."""
    fig = draw_stack_timeseries(
        ds,
        [
            ("pressure", "p", True, {"adcp"}),
            ("temperature", "t", False, None),
        ],
        color_var="temperature",
    )
    labels = [t.get_text() for t in fig.legends[0].get_texts()]
    plt.close(fig)
    serials = [str(s) for s in ds["serial"].values]
    assert labels == [s for s in serials if s in labels]
    assert len(labels) == len(set(labels))


def test_no_figure_without_the_variables(ds):
    """A row list naming no variable in the stack draws nothing."""
    assert (
        draw_stack_timeseries(ds, [("nope", "x", False, None)], color_var="x") is None
    )


def test_spacing_histogram_counts_pairs_two_dbar_or_more_apart(ds):
    """The spacing histogram's counts total the adjacent pairs ≥ 2 dbar apart."""
    fig = draw_instrument_spacing(ds)
    total = sum(p.get_height() for p in fig.axes[0].patches)
    plt.close(fig)
    p = ds["pressure"].values
    with np.errstate(all="ignore"):
        order = np.argsort(np.nanmedian(p, axis=0))
    d = np.diff(p[:, order], axis=1)
    assert total == np.sum(np.isfinite(d) & (d >= 2.0))


def _aquadopps(ds) -> list[int]:
    return [i for i, t in enumerate(ds["instrument_type"].values) if t == "aquadopp"]


def test_speed_profile_leaves_out_levels_without_speed(ds):
    """No figure when no Aquadopp has a finite speed; a level without one is left out."""
    blank = ds.copy(deep=True)
    for name in ("current_speed", "east_velocity", "north_velocity"):
        blank[name][:, _aquadopps(ds)] = np.nan
    assert draw_aquadopp_speed_profile(blank) is None
    one = ds.copy(deep=True)
    k = _aquadopps(ds)[0]
    for name in ("current_speed", "east_velocity", "north_velocity"):
        one[name][:, k] = np.nan
    fig = draw_aquadopp_speed_profile(one)
    labels = [t.get_text() for t in fig.axes[0].texts]
    plt.close(fig)
    assert f"s/n {ds['serial'].values[k]}" not in labels
    assert len(labels) == len(_aquadopps(ds)) - 1


def test_tilt_scatter_is_blank_for_a_level_without_pitch_and_roll(ds):
    """Its scatter panel is off and the figure keeps its size, row by row."""
    full = draw_aquadopp_tilt_panels(ds)
    size = full.get_size_inches()
    plt.close(full)
    k = _aquadopps(ds)[0]  # the deepest: the first row
    no_pr = ds.copy(deep=True)
    no_pr["pitch"][:, k] = np.nan
    no_pr["roll"][:, k] = np.nan
    fig = draw_aquadopp_tilt_panels(no_pr)
    scatter = fig.axes[1::2]
    np.testing.assert_array_equal(fig.get_size_inches(), size)
    assert not scatter[0].axison and not scatter[0].texts
    assert scatter[1].axison
    plt.close(fig)


@pytest.mark.parametrize("stored", [None, (25.0, 40.0)])
def test_tilt_lines_are_the_stored_limits_else_the_qc_default(ds, stored):
    """The lines match the limits stored with the flags, else ``QC_TILT``."""
    ds = ds.copy()
    if stored:
        ds.attrs["tilt_suspect_threshold"], ds.attrs["tilt_fail_threshold"] = stored
    want = stored or (
        params.QC_TILT["suspect_threshold"],
        params.QC_TILT["fail_threshold"],
    )
    fig = draw_aquadopp_tilt_panels(ds)
    flat = {
        float(line.get_ydata()[0])
        for line in fig.axes[0].get_lines()
        if len(set(line.get_ydata())) == 1
    }
    plt.close(fig)
    assert flat == set(want)


def test_every_flag_bar_has_one_height(ds):
    """Every flag bar is BAR_HEIGHT_IN high (half the earlier 0.36 in), across the figure."""
    from oceanarray.plotters.flags import BAR_HEIGHT_IN, draw_flag_strip

    fig = draw_flag_strip(ds)
    fig.canvas.draw()
    per_bar = [
        ax.get_position().height * fig.get_figheight() / len(ax.get_yticks())
        for ax in fig.axes
    ]
    bars = {len(ax.get_yticks()) for ax in fig.axes}
    plt.close(fig)
    assert len(bars) > 1
    assert BAR_HEIGHT_IN == 0.36 / 2
    np.testing.assert_allclose(per_bar, BAR_HEIGHT_IN, rtol=0.01)
