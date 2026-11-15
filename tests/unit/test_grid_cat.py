"""Gridding a site stack, and the QC flags masked before gridding (OSNAP East records).

The FLMB 2016 and 2017 stacks (OSNAP East), trimmed to 10 days either side of
their four-day overlap, test one mooring per profile; the egM2 2016 and 2018
deployments (three instruments each, 10 days either side of the 2018 turnaround)
test the file verb, the CLI and the page.
"""

import contextlib
import io
import json
import logging
import shutil
import warnings
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

import oceanarray as oa
from oceanarray import mooring, paths
from oceanarray.analysis import site
from oceanarray.cli import build_parser
from oceanarray.cli._common import _mask_flags
from oceanarray.exceptions import MooringError
from oceanarray.pipeline._run import resolve_grid_mask_flags
from oceanarray.pipeline.concat import concat, concat_moorings
from oceanarray.pipeline.grid import grid, grid_site, resolve_mask_flags, site_levels
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.plotters.helpers import deployment_line

_FIX = Path(__file__).parent.parent / "fixtures" / "proc"
_EGM2 = ("egM2_2016", "egM2_2018")


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


@pytest.fixture(scope="module")
def flmb() -> list[xr.Dataset]:
    out = []
    for year in (2016, 2017):
        with xr.open_dataset(
            _FIX / "flmb_overlap" / f"FLMB_{year}_stack.nc", engine="netcdf4"
        ) as ds:
            out.append(ds.load())
    return out


@pytest.fixture(scope="module")
def flmb_site(flmb) -> xr.Dataset:
    return _quiet(concat, flmb, name="FLMB")


@pytest.fixture(scope="module")
def egm2(tmp_path_factory) -> Path:
    """The two egM2 deployments, each with its stack written beside its YAML."""
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
    return root


# ── mask_flags ────────────────────────────────────────────────────────


def test_mask_flags_default_list_and_exceptions():
    assert resolve_mask_flags() == {"default": (4, 9)}
    assert resolve_mask_flags([3, 4, 9]) == {"default": (3, 4, 9)}
    assert resolve_mask_flags({"velocity": [3, 4, 9], "dissolved_oxygen": (4, 3)}) == {
        "default": (4, 9),
        "velocity": (3, 4, 9),
        "dissolved_oxygen": (3, 4),
    }


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ({"temperatur": [4]}, "is not a variable"),
        ({"east_velocity": [4]}, "share one key, 'velocity'"),
        ([4, 5], "not an OceanSITES flag value"),
        ("4,9", "expected a list"),
        ([True], "not an OceanSITES flag value"),
    ],
)
def test_mask_flags_refused(value, match):
    with pytest.raises(ValueError, match=match):
        resolve_mask_flags(value)


def test_the_cli_builds_the_same_mapping_as_the_api():
    parser = build_parser()
    one = parser.parse_args(
        ["concat", "a", "b", "--name", "S", "--mask-flags", "4,9", "velocity=3,4,9"]
    )
    two = parser.parse_args(
        [
            "concat",
            "a",
            "b",
            "--name",
            "S",
            "--mask-flags",
            "4,9",
            "--mask-flags",
            "velocity=3,4,9",
        ]
    )
    want = resolve_mask_flags({"default": (4, 9), "velocity": (3, 4, 9)})
    assert _mask_flags(one) == _mask_flags(two) == want


def test_the_cli_refuses_a_key_given_twice(capsys):
    args = build_parser().parse_args(
        ["concat", "a", "b", "--name", "S", "--mask-flags", "4", "4,9"]
    )
    with pytest.raises(SystemExit) as exit_:
        _mask_flags(args)
    assert exit_.value.code == 2
    assert "gives 'default' twice" in capsys.readouterr().err


def test_the_call_then_the_yaml_then_the_default():
    cfg = mooring.load(_FIX / "egM2_concat" / "egM2_2018" / "egM2_2018.mooring.yaml")
    assert resolve_grid_mask_flags(cfg) == {"default": (4, 9)}
    cfg["grid"]["mask_flags"] = {"velocity": [3, 4, 9]}
    assert resolve_grid_mask_flags(cfg)["velocity"] == (3, 4, 9)
    assert resolve_grid_mask_flags(cfg, [9]) == {"default": (9,)}
    cfg["grid"]["mask_flags"] = {"velocty": [3]}
    with pytest.raises(MooringError, match="description's grid: block"):
        resolve_grid_mask_flags(cfg)


def test_validate_checks_grid_mask_flags():
    cfg = mooring.load(_FIX / "egM2_concat" / "egM2_2018" / "egM2_2018.mooring.yaml")
    cfg["grid"]["mask_flags"] = [4, 9]
    assert not [i for i in mooring.check(cfg) if "mask_flags" in i.message]
    cfg["grid"]["mask_flags"] = {"velocty": [3]}
    errors = [i for i in mooring.check(cfg) if i.level == "ERROR"]
    assert any("'velocty' is not a variable" in i.message for i in errors)


def test_masked_flags_leave_the_grid_and_are_recorded(flmb):
    """FLMB 2016's velocity is flagged 1 or 9: masking 1 leaves no velocity to grid."""
    kept = _quiet(grid, flmb[0], pmin=1000, pmax=2000, dp=100)
    assert "east_velocity" in kept
    assert json.loads(kept.attrs["grid_mask_flags"]) == {"default": [4, 9]}
    gone = _quiet(
        grid, flmb[0], pmin=1000, pmax=2000, dp=100, mask_flags={"velocity": [1, 9]}
    )
    assert "east_velocity" not in gone
    np.testing.assert_array_equal(gone["temperature"], kept["temperature"])


# ── one mooring per profile ───────────────────────────────────────────


def test_each_profile_is_one_mooring(flmb, flmb_site):
    """Where FLMB 2016 and 2017 overlap, the grid is 2017's own grid at those steps."""
    out = _quiet(grid, flmb_site, pmin=200, pmax=2600, dp=50)
    index = out["deployment_index"].values
    for k, stack in enumerate(flmb):
        own = _quiet(grid, stack, pmin=200, pmax=2600, dp=50)
        steps = out["time"].values[index == k]
        mine = out.sel(time=steps)
        theirs = own.sel(time=steps)
        for var in ("temperature", "salinity", "east_velocity"):
            np.testing.assert_allclose(mine[var].values, theirs[var].values)
    overlap = np.intersect1d(flmb[0]["time"].values, flmb[1]["time"].values)
    assert overlap.size and set(index[np.isin(out["time"].values, overlap)]) == {1}


def test_a_site_grid_carries_the_deployment_record(flmb_site):
    out = _quiet(grid, flmb_site, pmin=200, pmax=2600, dp=50)
    assert list(out["deployment_name"].values) == ["FLMB_2016", "FLMB_2017"]
    np.testing.assert_array_equal(
        out["deployment_index"], flmb_site["deployment_index"]
    )
    assert 0 <= out.attrs["slope_angle_deg"] < 360
    assert "direction of the record-mean" in out.attrs["slope_angle_source"]
    given = _quiet(grid, flmb_site, pmin=200, pmax=2600, dp=50, slope_angle=-30)
    assert given.attrs["slope_angle_deg"] == 330
    assert given.attrs["slope_angle_source"] == "given"


def test_the_turnaround_is_empty(egm2):
    stacks = []
    for n in _EGM2:
        with xr.open_dataset(
            paths.product_file(egm2 / n, n, "stack"), engine="netcdf4"
        ) as ds:
            stacks.append(ds.load())
    out = _quiet(grid, _quiet(concat, stacks, name="egM2"), pmin=1000, pmax=2450, dp=50)
    gap = out["deployment_index"].values < 0
    assert gap.any()
    assert np.isnan(out["temperature"].values[gap]).all()


# ── the file, the CLI and the page ────────────────────────────────────


def test_concat_writes_the_site_grid_and_both_pages(egm2):
    yamls = [str(egm2 / n / f"{n}.mooring.yaml") for n in _EGM2]
    args = build_parser().parse_args(
        ["concat", *yamls, "--name", "egM2", "--dp", "50", "--force"]
    )
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("error", UserWarning)  # a page that fails warns
        assert args.func(args) == 0
    folder = egm2 / "egM2"
    gridded = folder / "egM2_gridcat.nc"
    with xr.open_dataset(gridded, engine="netcdf4") as ds:
        assert ds.attrs["stackcat_file"] == "egM2_stackcat.nc"
        assert len(ds.attrs["stackcat_sha256"]) == 64
        assert ds.attrs["dp_dbar"] == 50
        with xr.open_dataset(folder / "egM2_stackcat.nc", engine="netcdf4") as sc:
            assert (ds.attrs["p_start_dbar"], ds.attrs["p_end_dbar"]) == site_levels(
                sc, 50
            )
    page = (folder / "report" / "egM2_gridcat_report.html").read_text(encoding="utf-8")
    # Sections, anomalies, T and S profiles and the T-S diagram; one current meter
    # per deployment gives no velocity grid (two levels are needed to interpolate).
    assert page.count("data:image/png") == 4
    assert 'href="egM2_stackcat_report.html"' in page
    stack_page = (folder / "report" / "egM2_stackcat_report.html").read_text(
        encoding="utf-8"
    )
    assert 'href="egM2_gridcat_report.html"' in stack_page


def test_no_grid_writes_the_site_stack_only(egm2, tmp_path):
    root = tmp_path / "osnap"
    for n in _EGM2:
        shutil.copytree(egm2 / n, root / n)
    yamls = [str(root / n / f"{n}.mooring.yaml") for n in _EGM2]
    args = build_parser().parse_args(
        ["concat", *yamls, "--name", "egM2", "--no-grid", "--no-report"]
    )
    with contextlib.redirect_stdout(io.StringIO()):
        assert args.func(args) == 0
    assert (root / "egM2" / "egM2_stackcat.nc").exists()
    assert not (root / "egM2" / "egM2_gridcat.nc").exists()


def test_grid_site_refuses_a_deployment_stack(egm2):
    with pytest.raises(MooringError, match="is not a site stack"):
        grid_site(paths.product_file(egm2 / "egM2_2016", "egM2_2016", "stack"))


# ── display bins and colors ───────────────────────────────────────────


def test_no_display_bin_spans_two_deployments(flmb_site):
    index = flmb_site["deployment_index"].values
    bins = site.display_bins(flmb_site["time"].values, index, "7D")
    assert np.all(np.diff(bins.edges) > np.timedelta64(0, "ns"))
    np.testing.assert_array_equal(bins.owner[bins.step_bin], index)
    # The change from 2016 to 2017 is an edge.
    change = flmb_site["time"].values[np.flatnonzero(np.diff(index))[0] + 1]
    assert change in bins.edges


def test_deployment_lines_pair_each_color_solid_then_dashed():
    lines = [deployment_line(k) for k in range(16)]
    assert [line["ls"] for line in lines[:4]] == ["-", "--", "-", "--"]
    assert lines[0]["color"] == lines[1]["color"] != lines[2]["color"]
    assert lines[14]["color"] == lines[0]["color"] and lines[14]["ls"] == ":"


def test_a_level_without_a_qc_variable_is_kept(flmb):
    """A deployment whose stack has no pressure_qc: concat fills its flags with 9 (no
    QC), which must not mask its values, so its profiles stay in the site grid."""
    bare = flmb[1].drop_vars("pressure_qc")
    joined = _quiet(concat, [flmb[0], bare], name="FLMB")
    assert (
        joined["pressure_qc"].values[joined["deployment_index"].values == 1] == 9
    ).any()
    out = _quiet(grid, joined, pmin=200, pmax=2600, dp=50)
    later = out["deployment_index"].values == 1
    assert np.isfinite(out["temperature"].values[later]).any()


def test_the_along_slope_axis_points_along_the_mean_flow(flmb_site):
    out = _quiet(grid, flmb_site, pmin=200, pmax=2600, dp=50)
    along, _ = site.slope_components(
        out["east_velocity"], out["north_velocity"], out.attrs["slope_angle_deg"]
    )
    assert float(along.mean()) > 0


def test_a_zero_grid_spacing_is_an_error_not_a_crash(egm2):
    yamls = [egm2 / n / f"{n}.mooring.yaml" for n in _EGM2]
    with contextlib.redirect_stdout(io.StringIO()):
        path = concat_moorings(yamls, name="egM2", overwrite=True)
    with pytest.raises(MooringError, match="dp=0 must be > 0"):
        grid_site(path, dp=0, overwrite=True)


@pytest.fixture(scope="module")
def dune2() -> xr.Dataset:
    """dune2 1 2026's stack: Aquadopps with velocity flagged 3 (tilt)."""
    with xr.open_dataset(
        _FIX / "dune2_1_2026" / "dune2_1_2026_stack.nc", engine="netcdf4"
    ) as ds:
        return ds.load()


def test_suspect_velocity_is_kept_by_default_and_left_out_when_asked(dune2):
    assert (dune2["velocity_flag"].values == 3).any()
    kept = _quiet(grid, dune2, pmin=100, pmax=800, dp=10)
    strict = _quiet(
        grid, dune2, pmin=100, pmax=800, dp=10, mask_flags={"velocity": [3, 4, 9]}
    )
    n_kept = np.isfinite(kept["east_velocity"].values).sum()
    n_strict = np.isfinite(strict["east_velocity"].values).sum()
    assert n_strict < n_kept
    # Temperature is not velocity: the same either way.
    np.testing.assert_array_equal(kept["temperature"], strict["temperature"])


def test_speed_and_direction_come_from_the_gridded_components(dune2):
    out = _quiet(grid, dune2, pmin=100, pmax=800, dp=10)
    east, north = out["east_velocity"].values, out["north_velocity"].values
    # Stored as float32: equal to its precision.
    np.testing.assert_allclose(out["current_speed"], np.hypot(east, north), rtol=1e-5)
    direction = np.degrees(np.arctan2(east, north)) % 360.0
    gap = np.abs(out["current_direction"].values - direction)
    assert np.nanmax(np.minimum(gap, 360.0 - gap)) < 1e-3


def test_a_deployment_whose_grid_settings_differ_is_named(egm2, caplog):
    """egM2 2018's YAML grids 1000–2450 dbar: a site grid on its own range says so."""
    yamls = [egm2 / n / f"{n}.mooring.yaml" for n in _EGM2]
    with contextlib.redirect_stdout(io.StringIO()):
        path = concat_moorings(yamls, name="egM2", overwrite=True)
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        grid_site(path, dp=25, overwrite=True)
    assert "egM2_2018's own grid: settings (" in caplog.text


def test_without_a_position_no_isopycnals_are_drawn(egm2, tmp_path):
    from oceanarray.reports._gridcat import generate_gridcat_page

    yamls = [egm2 / n / f"{n}.mooring.yaml" for n in _EGM2]
    with contextlib.redirect_stdout(io.StringIO()):
        stackcat = concat_moorings(yamls, name="egM2", overwrite=True)
        gridcat = grid_site(stackcat, dp=50, overwrite=True)
    folder = tmp_path / "egM2"
    folder.mkdir()
    shutil.copy(stackcat, folder / stackcat.name)
    with xr.open_dataset(gridcat, engine="netcdf4") as ds:
        nowhere = ds.load()
    nowhere["deployment_latitude"][:] = np.nan
    nowhere["deployment_longitude"][:] = np.nan
    oa.write(nowhere, folder / gridcat.name)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        page = generate_gridcat_page(folder / gridcat.name, folder / "report")
    assert "no deployment has a position" in out.getvalue()
    assert "No isopycnals: no deployment has a position" in page.read_text(
        encoding="utf-8"
    )


def test_a_one_level_section_draws(flmb_site):
    from oceanarray.plotters.site import draw_site_sections

    out = _quiet(grid, flmb_site, pmin=1000, pmax=1000, dp=50)
    bins = site.display_bins(out["time"].values, out["deployment_index"].values, "7D")
    shown = site.display_means(out, bins)
    fig = draw_site_sections(
        [{"data": shown["temperature"].values, "title": "T", "cmap": "viridis"}],
        bins.centers,
        out["pressure"].values,
        bins.edges,
        bins.owner,
    )
    assert fig is not None


def test_a_grid_from_an_older_stack_is_not_linked(egm2, tmp_path):
    """The Stack cat page links the Grid cat only when it was made from this Stack cat."""
    from oceanarray.reports._concat import generate_concat_page

    yamls = [egm2 / n / f"{n}.mooring.yaml" for n in _EGM2]
    with contextlib.redirect_stdout(io.StringIO()):
        stackcat = concat_moorings(yamls, name="egM2", overwrite=True)
        grid_site(stackcat, dp=50, overwrite=True)
        linked = generate_concat_page(stackcat, tmp_path / "a")
        # The stack changes (rebuilt with the other overlap rule's history): the old
        # grid no longer matches it.
        with xr.open_dataset(stackcat, engine="netcdf4") as ds:
            changed = ds.load()
        changed.attrs["history"] += "; rebuilt"
        oa.write(changed, stackcat, overwrite=True)
        stale = generate_concat_page(stackcat, tmp_path / "b")
    assert 'href="egM2_gridcat_report.html"' in linked.read_text(encoding="utf-8")
    assert 'href="egM2_gridcat_report.html"' not in stale.read_text(encoding="utf-8")


def test_still_water_has_no_along_slope_direction(flmb_site):
    """FLMB with its velocities set to zero: no direction, not 0°."""
    still = flmb_site.copy()
    for v in ("east_velocity", "north_velocity"):
        still[v] = still[v] * 0.0
    out = _quiet(grid, still, pmin=200, pmax=2600, dp=50)
    assert "slope_angle_deg" not in out.attrs
    assert out.attrs["slope_angle_source"].startswith("none: steadiness 0.00")
