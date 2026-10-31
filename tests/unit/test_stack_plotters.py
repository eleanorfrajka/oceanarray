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

from oceanarray import parameters as params
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
