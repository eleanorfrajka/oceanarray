"""Derived quantities computed once per report in ``analysis.derived``.

All checks run on the dune2 fixture (real MicroCAT and Aquadopp records): potential
temperature and σθ for the T-S figures, the knockdown inputs, speed and direction,
the depth-to-pressure helper, and N².
"""

from __future__ import annotations

import logging
import pathlib

import gsw
import numpy as np
import pytest
import xarray as xr

from oceanarray.analysis import derived as dv
from oceanarray.location import parse_coordinate
from oceanarray.pipeline.pressure import depth_from_pressure, pressure_from_depth

_DUNE2 = (
    pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "proc" / "dune2_1_2026"
)


@pytest.fixture(scope="module")
def stack() -> xr.Dataset:
    """The dune2 stack (MicroCAT 2941 and Aquadopp 9920)."""
    with xr.open_dataset(_DUNE2 / "dune2_1_2026_stack.nc", engine="netcdf4") as ds:
        return ds.load()


@pytest.fixture(scope="module")
def grid() -> xr.Dataset:
    """The dune2 grid."""
    with xr.open_dataset(_DUNE2 / "dune2_1_2026_grid.nc", engine="netcdf4") as ds:
        return ds.load()


def test_position_is_parsed_and_never_zero(stack: xr.Dataset) -> None:
    """The stack's text position parses; a (0, 0) placeholder pair is skipped."""
    lat, lon = dv.mooring_position(stack)
    assert lat == pytest.approx(parse_coordinate("65 43.913"))
    assert lon == pytest.approx(-27.8)
    path = _DUNE2 / "microcat" / "dune2_1_2026_2941_stage3.nc"
    with xr.open_dataset(path, engine="netcdf4") as ds:
        # deployment_* carry "00 00.000" placeholders; the plain pair is used.
        assert dv.mooring_position(ds) == (pytest.approx(lat), pytest.approx(lon))


def test_theta_is_below_in_situ_temperature_at_depth(stack: xr.Dataset) -> None:
    """θ from the stack is colder than in-situ T at ~400 dbar, by the TEOS-10 amount."""
    fields = dv.derived_fields(stack)
    t = stack["temperature"].values
    theta = fields.theta.values
    ok = np.isfinite(t) & np.isfinite(theta)
    assert ok.sum() > 1000
    assert np.all(theta[ok] < t[ok])
    lat, lon = fields.latitude, fields.longitude
    i = np.argwhere(ok)[0]
    sa = gsw.SA_from_SP(
        stack["salinity"].values[tuple(i)], stack["pressure"].values[tuple(i)], lon, lat
    )
    expected = gsw.pt0_from_t(sa, t[tuple(i)], stack["pressure"].values[tuple(i)])
    assert theta[tuple(i)] == pytest.approx(float(expected))


def test_sigma_theta_contours_are_computed_from_theta(stack: xr.Dataset) -> None:
    """The contour grid's σθ equals gsw.sigma0 of (SA at 0 dbar, CT from θ)."""
    lat, lon = dv.mooring_position(stack)
    s, th, sig = dv.potential_density_grid((34.8, 35.2), (0.0, 8.0), lat, lon, n_grid=5)
    sa = gsw.SA_from_SP(s[2, 3], 0.0, lon, lat)
    assert sig[2, 3] == pytest.approx(
        float(gsw.sigma0(sa, gsw.CT_from_pt(sa, th[2, 3])))
    )
    _, _, sig2 = dv.potential_density_grid(
        (34.8, 35.2), (0.0, 8.0), lat, lon, n_grid=5, reference_dbar=2000
    )
    assert np.all(sig2 > sig)


def test_ts_figures_plot_theta_against_salinity(stack: xr.Dataset) -> None:
    """The stack T-S axes read θ / Salinity (PSU) and carry labelled contours."""
    from oceanarray.plotters.ts import SALINITY_LABEL, draw_stack_ts_diagram

    fig = draw_stack_ts_diagram(stack, derived=dv.derived_fields(stack))
    ax = fig.axes[0]
    assert ax.get_xlabel() == SALINITY_LABEL == "Salinity (PSU)"
    assert ax.get_ylabel().startswith("Potential temperature")
    assert ax.texts, "σθ contour labels expected"


def test_no_position_gives_in_situ_temperature_without_contours(
    stack: xr.Dataset, caplog
) -> None:
    """Without a position: in-situ T labelled so, no contours, a note and a warning."""
    from matplotlib.contour import ContourSet

    from oceanarray.plotters.ts import draw_stack_ts_diagram

    bare = stack.copy()
    bare.attrs = {
        k: v for k, v in stack.attrs.items() if "lat" not in k and "lon" not in k
    }
    with caplog.at_level(logging.WARNING, logger="oceanarray.analysis.derived"):
        fields = dv.derived_fields(bare)
        fig = draw_stack_ts_diagram(bare, derived=fields)
    assert fields.theta is None
    assert caplog.text.count("unknown") == 1
    assert fig.axes[0].get_ylabel().startswith("Temperature")
    assert [t.get_text() for t in fig.axes[0].texts] == [
        "σθ not drawn: no mooring position"
    ]
    assert not any(isinstance(c, ContourSet) for ax in fig.axes for c in ax.collections)
    assert "σθ contours not drawn on the T-S figure: no mooring position" in caplog.text


def test_speed_direction_matches_the_stack(stack: xr.Dataset) -> None:
    """Speed and direction agree with the stack's own; direction runs clockwise from north."""
    speed, direction = dv.speed_direction(
        stack["east_velocity"], stack["north_velocity"]
    )
    ok = np.isfinite(speed.values) & np.isfinite(stack["current_speed"].values)
    assert ok.any()
    np.testing.assert_allclose(
        speed.values[ok], stack["current_speed"].values[ok], rtol=1e-5
    )
    d = direction.values[np.isfinite(direction.values)]
    assert d.min() >= 0 and d.max() < 360
    _, compass = dv.speed_direction(
        np.array([0.0, 1.0, 0.0, -1.0]), np.array([1.0, 0.0, -1.0, 0.0])
    )
    np.testing.assert_allclose(compass, [0, 90, 180, 270])


def test_depth_pressure_round_trip() -> None:
    """Depth converts to pressure and back; pressure exceeds depth at depth."""
    p = pressure_from_depth(745.0, 65.73)
    assert p > 745.0
    assert depth_from_pressure(p, 65.73) == pytest.approx(745.0, abs=1e-6)


def test_knockdown_is_built_once_per_deployment_report(tmp_path, monkeypatch) -> None:
    """The deployment page builds the knockdown inputs once for its three figures."""
    from oceanarray.reports import MooringReport, _mooring

    calls = []
    real = _mooring.build_knockdown

    def counted(ds):
        calls.append(1)
        return real(ds)

    monkeypatch.setattr(_mooring, "build_knockdown", counted)
    MooringReport(proc_dir=str(_DUNE2.parent)).generate(
        "dune2_1_2026", force=True, outdir=str(tmp_path)
    )
    assert len(calls) == 1


def test_seabed_line_and_overflow_level_use_pressure(grid: xr.Dataset) -> None:
    """The velocity-profile seabed line and the overflow level sit at the seabed pressure."""
    from oceanarray.plotters.diagnostic import draw_velocity_iqr_profile
    from oceanarray.plotters.hydrography import draw_overflow_temperature

    lat, _ = dv.mooring_position(grid)
    p_bed = pressure_from_depth(float(grid.attrs["waterdepth"]), lat)
    assert dv.seabed_pressure(grid) == pytest.approx(p_bed)
    assert p_bed > float(grid.attrs["waterdepth"])

    fig = draw_velocity_iqr_profile(grid)
    if fig is not None:
        ys = [
            ln.get_ydata()[0]
            for ln in fig.axes[0].get_lines()
            if ln.get_linewidth() == 2.0
        ]
        assert any(y == pytest.approx(p_bed) for y in ys)

    fig = draw_overflow_temperature(grid)
    assert fig is not None
    title = fig.axes[0].get_title(loc="left")
    p_grid = grid["pressure"].values
    expected = float(
        p_grid[
            np.argmin(np.abs(p_grid - np.clip(p_bed - 100, p_grid.min(), p_grid.max())))
        ]
    )
    assert title.startswith(f"{expected:.0f} dbar")


def test_n_squared_is_unchanged_and_warns_without_longitude(
    grid: xr.Dataset, caplog
) -> None:
    """N² equals the TEOS-10 computation it replaced; no silent longitude 0."""
    lat, lon = dv.mooring_position(grid)
    t = grid["temperature"].transpose("pressure", "time").values.astype(float)
    s = grid["salinity"].transpose("pressure", "time").values.astype(float)
    p = grid["pressure"].values.astype(float)
    n2, p_mid = dv.n_squared(t, s, p, lat, lon)
    p2 = np.broadcast_to(p[:, None], t.shape)
    sa = gsw.SA_from_SP(s, p2, lon, lat)
    ref, _ = gsw.Nsquared(sa, gsw.CT_from_t(sa, t, p2), p2, lat=lat)
    np.testing.assert_array_equal(np.isnan(n2), np.isnan(ref))
    np.testing.assert_allclose(n2[np.isfinite(n2)], ref[np.isfinite(ref)])
    with caplog.at_level(logging.WARNING, logger="oceanarray.analysis.derived"):
        dv.n_squared(t, s, p, lat, None)
    assert "longitude unknown" in caplog.text
