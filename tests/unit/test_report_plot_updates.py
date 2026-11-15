"""Stack cat and Grid cat pages: direction panel, T-S pins, numeric isopycnal labels, captions.

Real OSNAP East records: the egM2 2016 and 2018 deployments (MicroCATs and one
current meter each) run through ``concat`` to both pages, and the FLMB 2016 and
2017 stacks (trimmed around their overlap) for the T-S diagram.
"""

import contextlib
import html
import io
import re
import shutil
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pytest
import xarray as xr

import oceanarray as oa
from oceanarray import mooring, paths
from oceanarray.analysis import site
from oceanarray.cli import build_parser
from oceanarray.config import parameters as params
from oceanarray.pipeline.concat import cell_deployment, concat
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.plotters import site as site_plots

_FIX = Path(__file__).parent.parent / "fixtures" / "proc"
_EGM2 = ("egM2_2016", "egM2_2018")


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


@pytest.fixture(scope="module")
def egm2_pages(tmp_path_factory) -> tuple[str, str]:
    """The egM2 Stack cat and Grid cat pages, built by ``oceanarray concat``."""
    root = tmp_path_factory.mktemp("osnap")
    for name in _EGM2:
        shutil.copytree(_FIX / "egM2_concat" / name, root / name)
        cfg = mooring.load(root / name / f"{name}.mooring.yaml")
        data = _quiet(
            read_mooring, cfg, stage=3, mooring_dir=root / name, allow_missing=True
        )
        oa.write(
            _quiet(oa.stack, data, cfg), paths.product_file(root / name, name, "stack")
        )
    yamls = [str(root / n / f"{n}.mooring.yaml") for n in _EGM2]
    args = build_parser().parse_args(
        ["concat", *yamls, "--name", "egM2", "--dp", "50", "--force"]
    )
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("error", UserWarning)  # a page that fails warns
        assert args.func(args) == 0
    report = root / "egM2" / "report"
    return tuple(
        html.unescape((report / f"egM2_{kind}_report.html").read_text(encoding="utf-8"))
        for kind in ("stackcat", "gridcat")
    )


@pytest.fixture(scope="module")
def flmb_site() -> xr.Dataset:
    stacks = []
    for year in (2016, 2017):
        with xr.open_dataset(
            _FIX / "flmb_overlap" / f"FLMB_{year}_stack.nc", engine="netcdf4"
        ) as ds:
            stacks.append(ds.load())
    return _quiet(concat, stacks, name="FLMB")


def _profiles(ds: xr.Dataset, pressure: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each deployment's median T and S per level, interpolated onto *pressure*."""
    n = ds["deployment_name"].size
    th = np.full((n, pressure.size), np.nan)
    sa = np.full_like(th, np.nan)
    cells = cell_deployment(ds)
    for k in range(n):
        p, s, t = [], [], []
        for j in range(cells.shape[1]):
            ok = (cells[:, j] == k) & np.isfinite(ds["salinity"].values[:, j])
            if ok.any():
                p.append(float(np.nanmean(ds["pressure"].values[ok, j])))
                s.append(float(np.nanmedian(ds["salinity"].values[ok, j])))
                t.append(float(np.nanmedian(ds["temperature"].values[ok, j])))
        order = np.argsort(p)
        inside = (pressure >= min(p)) & (pressure <= max(p))
        sa[k, inside] = np.interp(
            pressure[inside], np.array(p)[order], np.array(s)[order]
        )
        th[k, inside] = np.interp(
            pressure[inside], np.array(p)[order], np.array(t)[order]
        )
    return th, sa


# Stack cat: direction beside speed ------------------------------------------


def test_the_stack_cat_draws_direction_beside_speed(egm2_pages):
    stack_page, _ = egm2_pages
    assert "Current speed and direction by depth" in stack_page
    assert 'alt="speed_profile"' in stack_page
    assert 'alt="direction_profile"' in stack_page
    # Speed then direction, in one row of half-width figures.
    assert stack_page.index('alt="speed_profile"') < stack_page.index(
        'alt="direction_profile"'
    )
    assert "draw_direction_profile_by_deployment" in stack_page
    # The captions name the rules the figures follow.
    from oceanarray.plotters.helpers import PROFILE_MIN_FRACTION

    assert f"at least {PROFILE_MIN_FRACTION:.0%} of its time steps" in stack_page
    assert f"less than {params.SLOPE_STEADINESS_MIN:g} of the mean speed" in stack_page
    assert "▼ above the panel" in stack_page


def test_the_stack_cat_ts_caption_describes_the_outlines():
    # The egM2 test records are shorter than the 30 days a level needs, so the
    # figure (and its caption) is not on that page: check the caption itself.
    from oceanarray.plotters import ts
    from oceanarray.reports._concat import CONCAT_CAPTIONS

    caption = CONCAT_CAPTIONS["ts_by_deployment"]
    assert f"densest {ts.TS_OUTLINE_FRACTION:.0%}" in caption
    assert f"fewer than {ts.TS_MIN_LEVEL_POINTS:g} points" in caption
    assert f"more than {ts.TS_MAX_OUTLINES} levels" in caption


# Grid cat: pins, labels, captions --------------------------------------------


def test_the_grid_cat_captions_name_pins_labels_and_blank_bins(egm2_pages):
    _, grid_page = egm2_pages
    assert "Circles: each deployment's instruments measuring both T and S" in grid_page
    assert "labeled with the value only" in grid_page
    assert f"less than {site.DISPLAY_MIN_FRACTION:.0%} of its time" in grid_page
    assert "▼ above the panel" in grid_page
    assert "change in sampling" in grid_page


def test_isopycnal_labels_are_numbers_only(flmb_site):
    pressure = np.arange(1700.0, 2800.0, 20.0)
    th, sa = _profiles(flmb_site, pressure)
    names = [str(n) for n in flmb_site["deployment_name"].values]
    lat = float(flmb_site.attrs.get("latitude", 59.71547))
    lon = float(flmb_site.attrs.get("longitude", -39.31479))
    fig = site_plots.draw_site_ts(
        th,
        sa,
        names,
        lon,
        lat,
        sigma0_levels=(27.80, 27.85, 27.88),
        sigma2_levels=(37.0, 37.05, 37.1),
    )
    labels = [t.get_text() for t in fig.axes[0].texts]
    assert labels, "no isopycnal in range: pick levels inside the FLMB T-S range"
    assert all(re.fullmatch(r"\d+(\.\d+)?", s) for s in labels), labels
    plt.close(fig)


def test_a_pin_within_half_a_step_of_the_curves_end_is_marked_there(flmb_site):
    pressure = np.arange(1700.0, 2800.0, 20.0)
    th, sa = _profiles(flmb_site, pressure)
    names = [str(n) for n in flmb_site["deployment_name"].values]
    pins = site.instrument_mean_pressures(flmb_site)
    k = 0
    ok = np.isfinite(th[k]) & np.isfinite(sa[k])
    deepest = pressure[ok].max()
    # One pin just below the curve's deepest level (inside half a step), one
    # a full step below it (outside): only the first is drawn, at the end.
    near, far = deepest + 8.0, deepest + 20.0
    pins_k = [[*pins[0], near, far], *([[]] * (len(names) - 1))]
    fig = site_plots.draw_site_ts(
        th, sa, names, None, None, pressure=pressure, pins=pins_k
    )
    (circles,) = [ln for ln in fig.axes[0].lines if ln.get_marker() == "o"]
    xs = circles.get_xdata()
    inside = [x for x in pins[0] if pressure[ok].min() - 10 <= x <= deepest + 10]
    assert len(xs) == len(inside) + 1
    assert np.isclose(xs[-1], sa[k][ok][-1])
    plt.close(fig)


# Stack cat T-S points, Grid cat coverage rule ---------------------------------


def test_ts_points_are_thinned_the_same_way_each_time(flmb_site, monkeypatch):
    from oceanarray.plotters import ts

    ds = flmb_site.assign(
        cell_deployment=(("time", "N_DEPTHS"), cell_deployment(flmb_site))
    )
    monkeypatch.setattr(ts, "TS_MAX_POINTS", 200)

    def drawn(fig):
        from matplotlib.collections import PathCollection

        return np.concatenate(
            [
                c.get_offsets()
                for c in fig.axes[0].collections
                if type(c) is PathCollection  # the scatters, not the σ0 contours
            ]
        )

    first, second = (ts.draw_ts_points_by_deployment(ds) for _ in range(2))
    a, b = drawn(first), drawn(second)
    assert len(a) <= 200 * ds["deployment_name"].size
    np.testing.assert_array_equal(a, b)
    plt.close("all")


def test_a_level_filled_part_of_the_time_has_no_median(flmb_site):
    values = flmb_site["temperature"].values[:, :1].copy()
    index = flmb_site["deployment_index"].values
    k = int(index[np.isfinite(values[:, 0])][0])
    rows = np.flatnonzero(index == k)
    values[rows[: int(0.6 * rows.size)], 0] = np.nan  # 40 % of the deployment left
    kept = site.deployment_medians(values, index, flmb_site["deployment_name"].size)
    ruled = site.deployment_medians(
        values, index, flmb_site["deployment_name"].size, min_fraction=0.5
    )
    assert np.isfinite(kept[k, 0]) and np.isnan(ruled[k, 0])
