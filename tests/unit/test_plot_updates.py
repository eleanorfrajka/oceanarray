"""Stack cat and Grid cat figure changes (deployment styles, data rules, markers), on OSNAP East records.

The FLMB 2016 and 2017 stacks (OSNAP East), trimmed to 10 days either side of
their four-day overlap, are joined into a site stack and used throughout.
"""

import contextlib
import io
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pytest
import xarray as xr

from oceanarray.analysis import site
from oceanarray.config import parameters as params
from oceanarray.pipeline.concat import cell_deployment, concat
from oceanarray.plotters import current, helpers, ts
from oceanarray.plotters import site as site_plots
from oceanarray.plotters.primitives import pcolormesh_panel
from oceanarray.plotters.timeseries import _mark_deployments

_FIX = Path(__file__).parent.parent / "fixtures" / "proc" / "flmb_overlap"


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


@pytest.fixture(scope="module")
def flmb_site() -> xr.Dataset:
    stacks = []
    for year in (2016, 2017):
        with xr.open_dataset(_FIX / f"FLMB_{year}_stack.nc", engine="netcdf4") as ds:
            stacks.append(ds.load())
    out = _quiet(concat, stacks, name="FLMB")
    return out.assign(cell_deployment=(("time", "N_DEPTHS"), cell_deployment(out)))


# Deployment styles ---------------------------------------------------------


def test_up_to_seven_deployments_each_get_their_own_color():
    for n in range(1, 8):
        colors = [helpers.deployment_color(k, n) for k in range(n)]
        assert len(set(colors)) == n
        assert {helpers.deployment_line(k, n)["ls"] for k in range(n)} == {"-"}
        assert {helpers.deployment_marker(k, n) for k in range(n)} == {"o"}
    # Spread from the first color (early) to the last (late).
    assert helpers.deployment_color(0, 6) == helpers.DEPLOYMENT_COLORS[0]
    assert helpers.deployment_color(5, 6) == helpers.DEPLOYMENT_COLORS[-1]


def test_past_seven_deployments_share_colors_in_pairs():
    n = 11
    for k in range(0, n - 1, 2):
        assert helpers.deployment_color(k, n) == helpers.deployment_color(k + 1, n)
        assert helpers.deployment_marker(k, n) != helpers.deployment_marker(k + 1, n)
        assert helpers.deployment_hatch(k, n) != helpers.deployment_hatch(k + 1, n)
        assert (
            helpers.deployment_line(k, n)["ls"]
            != helpers.deployment_line(k + 1, n)["ls"]
        )
    assert helpers.deployment_color(0, n) != helpers.deployment_color(2, n)


def test_short_names_drop_the_shared_site_prefix():
    assert helpers.short_names(["FLMB_2016", "FLMB_2017"]) == ["2016", "2017"]
    assert helpers.short_names(["FLMB_2016", "egM2_2016"]) == ["FLMB_2016", "egM2_2016"]


# Velocity by depth -----------------------------------------------------------


def test_a_level_is_drawn_only_with_half_its_own_deployment_steps(flmb_site):
    by = current._velocity_by_depth(flmb_site)
    assert by is not None
    cell_dep = flmb_site["cell_deployment"].values
    for (_label, j), drawn in zip(by["groups"], by["cells"], strict=True):
        for k, rows in drawn:
            # Judged on the depth's own steps in that deployment, not the whole
            # deployment window, so a depth sampled part of the time is not penalised.
            own = int((cell_dep[:, j] == k).sum())
            assert rows.sum() >= params.MIN_RECORD_FRACTION * own


def test_no_level_is_drawn_when_none_has_enough_data(flmb_site, monkeypatch):
    monkeypatch.setattr(params, "MIN_RECORD_FRACTION", 1.01)
    assert current.draw_speed_profile_by_deployment(flmb_site) is None
    assert current.draw_direction_profile_by_deployment(flmb_site) is None


def test_the_direction_panel_is_centred_and_labelled_in_true_degrees(flmb_site):
    fig = current.draw_direction_profile_by_deployment(flmb_site)
    assert fig is not None
    ax = fig.axes[0]
    lo, hi = ax.get_xlim()
    assert hi - lo == pytest.approx(360.0)
    labels = [t.get_text() for t in ax.get_xticklabels()]
    assert all(0 <= float(x) < 360 for x in labels)
    plt.close(fig)


def test_the_speed_panel_depth_axis_is_in_metres(flmb_site):
    fig = current.draw_speed_profile_by_deployment(flmb_site)
    by = current._velocity_by_depth(flmb_site)
    labels = [t.get_text() for t in fig.axes[0].get_yticklabels()]
    assert labels == [f"{d:.0f}" for d in by["depth"]]
    plt.close(fig)


# T-S by deployment -----------------------------------------------------------


def test_the_median_line_averages_colocated_levels():
    s, t = ts._median_line(
        [(1500.0, 34.90, 3.5), (1505.0, 34.92, 3.3), (2000.0, 34.93, 3.0)]
    )
    assert s.tolist() == pytest.approx([34.91, 34.93])
    assert t.tolist() == pytest.approx([3.4, 3.0])


def test_ts_by_deployment_draws_outlines_medians_and_lines(flmb_site, monkeypatch):
    # Draw every level regardless of size, to check the outline/median/line logic.
    monkeypatch.setattr(ts, "TS_MIN_LEVEL_POINTS", 1)
    fig = ts.draw_ts_by_deployment(flmb_site)
    assert fig is not None
    ax = fig.axes[0]
    assert ax.collections, "no outline was drawn"
    assert not [
        c for c in ax.collections if c.__class__.__name__ == "PathCollection"
    ], "the scatter of points is gone"
    plt.close(fig)


def test_a_short_full_deployment_is_not_dropped(flmb_site):
    # The fixture's deployments are ~2 weeks each; at the default minimum they are
    # still drawn (a short but complete deployment is kept, not dropped for being
    # shorter than some duration — which would lose a mooring like dune2).
    fig = ts.draw_ts_by_deployment(flmb_site)
    assert fig is not None
    plt.close(fig)


def test_too_many_outlines_leave_medians_and_lines_only(flmb_site, monkeypatch):
    monkeypatch.setattr(ts, "TS_MIN_LEVEL_POINTS", 1)
    monkeypatch.setattr(ts, "TS_MAX_OUTLINES", 0)
    fig = ts.draw_ts_by_deployment(flmb_site)
    contours = [c for c in fig.axes[0].collections if "Contour" in c.__class__.__name__]
    # Only the σ0 background remains.
    assert len(contours) == 1
    plt.close(fig)


# Grid cat sections -----------------------------------------------------------


def test_display_bins_with_too_little_data_are_blank(flmb_site):
    bins = site.display_bins(
        flmb_site["time"].values, flmb_site["deployment_index"].values, "1D"
    )
    values = flmb_site["temperature"].values
    plain = site.bin_means(values, bins.step_bin, len(bins.centers))
    strict = site.bin_means(values, bins.step_bin, len(bins.centers), min_fraction=0.5)
    count = np.zeros_like(plain)
    np.add.at(count, bins.step_bin, np.isfinite(values))
    steps = np.bincount(bins.step_bin, minlength=len(bins.centers))[:, None]
    few = (count > 0) & (count < 0.5 * steps)
    assert np.isnan(strict[few]).all()
    keep = count >= 0.5 * steps
    np.testing.assert_allclose(strict[keep], plain[keep])


def test_bin_means_accumulate_in_float64(flmb_site):
    bins = site.display_bins(
        flmb_site["time"].values, flmb_site["deployment_index"].values, "1D"
    )
    values = flmb_site["salinity"].values.astype(np.float32)
    got = site.bin_means(values, bins.step_bin, len(bins.centers))
    want = site.bin_means(values.astype(np.float64), bins.step_bin, len(bins.centers))
    np.testing.assert_array_equal(got, want)


def test_sections_have_no_horizontal_grid(flmb_site):
    # Two days of the FLMB temperature section, levels at their median pressure.
    fig, ax = plt.subplots()
    temp = flmb_site["temperature"].values[:48]
    pres = flmb_site["pressure"].values[:48]
    cols = [j for j in range(temp.shape[1]) if np.isfinite(temp[:, j]).all()]
    cols = sorted(cols, key=lambda j: float(np.nanmedian(pres[:, j])))
    p = np.array([float(np.nanmedian(pres[:, j])) for j in cols])
    t = flmb_site["time"].values[:48]
    pcolormesh_panel(fig, ax, temp[:, cols].T, t, p, title="T")
    assert not any(line.get_visible() for line in ax.yaxis.get_gridlines())
    plt.close(fig)


def test_each_deployment_change_gets_a_down_triangle(flmb_site):
    fig, axes = plt.subplots(2, 1)
    time = flmb_site["time"].values
    index = flmb_site["deployment_index"].values
    spans = []
    for k, name in enumerate(flmb_site["deployment_name"].values):
        rows = np.flatnonzero(index == k)
        spans.append((time[rows[0]], time[rows[-1]], str(name)))
    _mark_deployments(list(axes), spans)
    triangles = [ln for ln in axes[0].lines if ln.get_marker() == "v"]
    assert len(triangles) == len(spans) - 1
    plt.close(fig)


def test_instrument_mean_pressures_are_t_and_s_levels_per_deployment(flmb_site):
    pins = site.instrument_mean_pressures(flmb_site)
    assert len(pins) == flmb_site["deployment_name"].size
    for k, means in enumerate(pins):
        assert means == sorted(means)
        cells = cell_deployment(flmb_site)
        both = np.isfinite(flmb_site["temperature"].values) & np.isfinite(
            flmb_site["salinity"].values
        )
        expected = sum(
            bool(((cells[:, j] == k) & both[:, j]).any()) for j in range(cells.shape[1])
        )
        assert len(means) == expected


def test_site_ts_marks_each_pin_on_its_curve(flmb_site):
    pressure = np.arange(1700.0, 2800.0, 20.0)
    names = [str(n) for n in flmb_site["deployment_name"].values]
    # A deployment's median profile, from its stack levels, on the pressure levels.
    th = np.full((len(names), pressure.size), np.nan)
    sa = np.full_like(th, np.nan)
    pins = site.instrument_mean_pressures(flmb_site)
    cells = cell_deployment(flmb_site)
    for k in range(len(names)):
        p, s, t = [], [], []
        for j in range(cells.shape[1]):
            rows = cells[:, j] == k
            ok = rows & np.isfinite(flmb_site["salinity"].values[:, j])
            if ok.any():
                p.append(float(np.nanmean(flmb_site["pressure"].values[ok, j])))
                s.append(float(np.nanmedian(flmb_site["salinity"].values[ok, j])))
                t.append(float(np.nanmedian(flmb_site["temperature"].values[ok, j])))
        order = np.argsort(p)
        inside = (pressure >= min(p)) & (pressure <= max(p))
        sa[k, inside] = np.interp(
            pressure[inside], np.array(p)[order], np.array(s)[order]
        )
        th[k, inside] = np.interp(
            pressure[inside], np.array(p)[order], np.array(t)[order]
        )
    fig = site_plots.draw_site_ts(
        th, sa, names, None, None, pressure=pressure, pins=pins
    )
    circles = [ln for ln in fig.axes[0].lines if ln.get_marker() == "o"]
    expected = 0
    for k in range(len(names)):
        ok = np.isfinite(th[k]) & np.isfinite(sa[k])
        expected += int(
            bool([x for x in pins[k] if pressure[ok].min() <= x <= pressure[ok].max()])
        )
    assert len(circles) == expected
    plt.close(fig)
