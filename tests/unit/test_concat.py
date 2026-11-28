"""Concatenating one site's deployment stacks, on the egM2 2016 and 2018 records (OSNAP East).

The fixture holds three instruments of each deployment, 10 days either side of the
2018 turnaround: two MicroCAT depths found in both years, and a current meter (2016)
and an Aquadopp (2018) 74 m apart. The FLMB 2016 and 2017 stacks (OSNAP East),
trimmed to 10 days either side of their four-day overlap, test the overlap block.
"""

import contextlib
import copy
import io
import re
import shutil
import tracemalloc
import warnings
from pathlib import Path

import netCDF4
import numpy as np
import pytest
import xarray as xr
import yaml

import oceanarray as oa
from oceanarray import mooring, paths
from oceanarray.cli import build_parser
from oceanarray.exceptions import MooringError
from oceanarray.pipeline.concat import (
    _assign_depths,
    _tolerances,
    concat,
    concat_moorings,
)
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.reports._concat import generate_concat_page

_FIX = Path(__file__).parent.parent / "fixtures" / "proc" / "egM2_concat"
_NAMES = ("egM2_2016", "egM2_2018")
_FLMB = Path(__file__).parent.parent / "fixtures" / "proc" / "flmb_overlap"
_ADCP = (
    Path(__file__).parent.parent / "fixtures" / "adcp" / "dsG2_adcp_stage3_knockdown.nc"
)


def _level_values(out: xr.Dataset, level: int, own: xr.Dataset, var: str) -> np.ndarray:
    """Rebuild one level's series on its own stack's steps from the grid and the overlap block."""
    steps = np.searchsorted(out["time"].values, own["time"].values)
    s = int(out["depth_index"].values[level])
    values = np.full(steps.size, np.nan)
    mine = out["level_index"].values[steps, s] == level
    values[mine] = out[var].values[steps[mine], s]
    spill = np.flatnonzero(out["overlap_level_index"].values == level)
    at = np.searchsorted(own["time"].values, out["overlap_time"].values[spill])
    values[at] = out[f"overlap_{var}"].values[spill]
    assert mine.sum() + spill.size == steps.size
    return values


def _assert_nothing_lost(out: xr.Dataset, stacks: list[xr.Dataset]) -> None:
    """Every value of every level of every stack is in the grid or the overlap block."""
    offset = 0
    for s in stacks:
        for var in ("temperature", "temperature_qc", "pressure", "east_velocity"):
            if var not in s:
                continue
            own = s[var].transpose("time", "N_LEVELS").values.astype(float)
            for j in range(s.sizes["N_LEVELS"]):
                np.testing.assert_array_equal(
                    _level_values(out, offset + j, s, var), own[:, j]
                )
        offset += s.sizes["N_LEVELS"]


@pytest.fixture(scope="module")
def flmb() -> list[xr.Dataset]:
    """The two FLMB stacks around their 2017 overlap, loaded."""
    out = []
    for year in (2016, 2017):
        with xr.open_dataset(_FLMB / f"FLMB_{year}_stack.nc", engine="netcdf4") as ds:
            out.append(ds.load())
    return out


def _stack(cfg: dict, folder: Path, **kwargs) -> xr.Dataset:
    """Stack one deployment's committed stage-3 files (the ADCP has none: skipped)."""
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        data = read_mooring(cfg, stage=3, mooring_dir=folder, allow_missing=True)
        return oa.stack(data, cfg, **kwargs)


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    """A copy of the two deployments, each with its stack written beside its YAML."""
    root = tmp_path_factory.mktemp("osnap")
    for name in _NAMES:
        shutil.copytree(_FIX / name, root / name)
        cfg = mooring.load(root / name / f"{name}.mooring.yaml")
        oa.write(
            _stack(cfg, root / name),
            paths.product_file(root / name, mooring_name=name, product="stack"),
        )
    return root


@pytest.fixture(scope="module")
def stacks(site) -> list[xr.Dataset]:
    """The two deployment stacks, loaded."""
    out = []
    for n in _NAMES:
        with xr.open_dataset(
            paths.product_file(site / n, mooring_name=n, product="stack"),
            engine="netcdf4",
        ) as ds:
            out.append(ds.load())
    return out


@pytest.fixture(scope="module")
def joined(stacks) -> xr.Dataset:
    """The two stacks concatenated, given in reverse order."""
    return concat(stacks[::-1], name="egM2")


def test_every_level_of_every_deployment_is_kept(stacks, joined):
    assert joined.sizes["N_LEVELS"] == sum(s.sizes["N_LEVELS"] for s in stacks) == 6
    assert list(joined["deployment_name"].values) == list(_NAMES)
    assert joined.attrs["dt_seconds"] == 3600
    assert joined["temperature"].dims == ("time", "N_DEPTHS")


def test_nothing_is_lost(stacks, joined):
    _assert_nothing_lost(joined, stacks)


def test_a_depth_is_empty_outside_its_levels_deployments(joined):
    """A depth holds a level only on its deployment's steps; elsewhere NaN, flag 9."""
    empty = joined["level_index"].values == -1
    assert empty.any()
    assert np.isnan(joined["temperature"].values[empty]).all()
    assert (joined["temperature_qc"].values[empty] == 9).all()


def test_the_turnaround_is_a_gap(joined):
    """2018-06-16 09:00 to 2018-06-19 01:00: no deployment counts, nothing is filled."""
    gap = joined["deployment_index"].values == -1
    assert gap.sum() == 63
    assert (joined["level_index"].values[gap] == -1).all()
    assert np.isnan(joined["pressure"].values[gap]).all()
    assert (joined["temperature_qc"].values[gap] == 9).all()


def test_a_level_alone_at_its_depth(joined):
    """The 2016 current meter has a depth of its own: empty through all of 2018."""
    ids = list(joined["level_id"].values)
    level = ids.index("egM2_2016/8364")
    s = int(joined["depth_index"].values[level])
    assert (joined["depth_index"].values == s).sum() == 1
    col = joined["level_index"].values[:, s]
    in_2016 = joined["deployment_index"].values == 0
    assert (col[in_2016] == level).all() and (col[~in_2016] == -1).all()


def test_depths_pair_across_years(joined):
    """Two MicroCAT depths are in both deployments: 6 levels at 4 depths, numbered deep-first."""
    depth_index = joined["depth_index"].values
    assert joined.sizes["N_DEPTHS"] == 4
    ref = joined["depth_match_value"].values
    assert (np.diff(ref) <= 0).all()
    ids = list(joined["level_id"].values)
    assert (
        depth_index[ids.index("egM2_2016/13020")]
        == depth_index[ids.index("egM2_2018/11514")]
    )
    assert (
        depth_index[ids.index("egM2_2016/11331")]
        == depth_index[ids.index("egM2_2018/13220")]
    )
    # The 1926 m current meter and the 2000 m Aquadopp are 74 m apart: two depths.
    assert (
        depth_index[ids.index("egM2_2016/8364")]
        != depth_index[ids.index("egM2_2018/CM2000")]
    )


def test_a_wider_tolerance_pairs_the_current_meters(stacks):
    """``stack: depth_tolerance_m: 100`` puts the two velocity instruments in one depth."""
    wide = []
    for s in stacks:
        s = s.copy()
        settings = yaml.safe_load(s.attrs["deployment_settings"])
        settings["stack"]["depth_tolerance_m"] = 100
        s.attrs["deployment_settings"] = yaml.safe_dump(settings)
        wide.append(s)
    out = concat(wide, name="egM2")
    ids = list(out["level_id"].values)
    depth_index = out["depth_index"].values
    assert (
        depth_index[ids.index("egM2_2016/8364")]
        == depth_index[ids.index("egM2_2018/CM2000")]
    )


def test_the_deployment_record(joined):
    assert joined["deployment_waterdepth"].values.tolist() == [2459.0, 2436.0]
    np.testing.assert_allclose(
        joined["deployment_latitude"].values, [59.85917, 59.9], atol=1e-4
    )
    for k in range(2):
        settings = yaml.safe_load(str(joined["deployment_settings"].values[k]))
        assert settings["stack"]["dt_seconds"] == 3600


def test_different_steps_are_refused(site, stacks):
    cfg = mooring.load(site / "egM2_2018" / "egM2_2018.mooring.yaml")
    fine = _stack(cfg, site / "egM2_2018", dt_seconds=1800)
    with pytest.raises(MooringError, match="different time steps"):
        concat([stacks[0], fine], name="egM2")


def test_one_stack_is_refused(stacks):
    with pytest.raises(MooringError, match="at least two"):
        concat(stacks[:1], name="egM2")


def test_different_depth_rules_are_refused(stacks):
    other = stacks[1].copy()
    settings = yaml.safe_load(other.attrs["deployment_settings"])
    settings["stack"]["depth_tolerance_m"] = 100
    other.attrs["deployment_settings"] = yaml.safe_dump(settings)
    with pytest.raises(MooringError, match="different depth-matching rules"):
        concat([stacks[0], other], name="egM2")


def test_the_file_verb_writes_the_stackcat_and_its_cruises(site):
    yamls = [site / n / f"{n}.mooring.yaml" for n in _NAMES]
    with contextlib.redirect_stdout(io.StringIO()):
        out = concat_moorings(yamls, name="egM2", overwrite=True)
    assert out == site / "egM2" / "egM2_stackcat.nc"
    with xr.open_dataset(out, engine="netcdf4") as ds:
        assert ds.attrs["oceanarray_version"]
        assert all(len(h) == 64 for h in ds["stack_sha256"].values)
        assert list(ds["deployment_cruise"].values) == ["DY054", "AR30-03"]
        assert list(ds["recovery_cruise"].values) == ["MSM74", "AR46"]
        assert list(ds["stack_file"].values) == [
            "../egM2_2016/egM2_2016_stack.nc",
            "../egM2_2018/egM2_2018_stack.nc",
        ]


def test_a_stack_whose_people_were_not_cleaned_is_refused(site, tmp_path):
    """egM2 2018 restacked from stage files without their stage-1 version (as files
    from before stage 1 cleaned people): concat names it and asks for stage 1."""
    root = tmp_path / "osnap"
    for n in _NAMES:
        shutil.copytree(site / n, root / n)
    cfg = mooring.load(root / "egM2_2018" / "egM2_2018.mooring.yaml")
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        data = read_mooring(cfg, stage=3, mooring_dir=root / "egM2_2018")
        for ds in data.values():
            del ds.attrs["stage1_oceanarray_version"]
        stale = oa.stack(data, cfg)
    assert stale.attrs["people_not_cleaned"]
    oa.write(
        stale,
        paths.product_file(
            root / "egM2_2018", mooring_name="egM2_2018", product="stack"
        ),
        overwrite=True,
    )
    yamls = [root / n / f"{n}.mooring.yaml" for n in _NAMES]
    with pytest.raises(MooringError, match="egM2_2018: built from stage files"):
        concat_moorings(yamls, name="egM2")


def test_the_file_verb_will_not_write_into_a_deployment_folder(site):
    yamls = [site / n / f"{n}.mooring.yaml" for n in _NAMES]
    with pytest.raises(MooringError, match="holds a mooring YAML"):
        concat_moorings(yamls, name="egM2", out_dir=site / "egM2_2016")


def test_a_dry_run_writes_nothing(site, tmp_path):
    yamls = [site / n / f"{n}.mooring.yaml" for n in _NAMES]
    with contextlib.redirect_stdout(io.StringIO()):
        out = concat_moorings(yamls, name="egM2", out_dir=tmp_path / "x", dry_run=True)
    assert not out.exists()


def test_the_report_page(site):
    yamls = [site / n / f"{n}.mooring.yaml" for n in _NAMES]
    with contextlib.redirect_stdout(io.StringIO()):
        out = concat_moorings(yamls, name="egM2", overwrite=True)
        page = generate_concat_page(out, out.parent / "report")
    html = page.read_text(encoding="utf-8")
    assert page.name == "egM2_stackcat_report.html"
    # The breadcrumb is shown by default, with no external ancestors needed.
    assert '<nav class="breadcrumb">' in html and ">egM2<" in html
    assert "Concatenated stack" in html and "Stack cat" in html
    assert "Instruments (avg)" in html and "3 (MicroCAT 2" in html
    for section in (
        "Deployments",
        "Instruments",
        "Hydrography",
        "T-S diagram",
    ):
        assert section in html
    # Per-deployment diagnostics stay in each deployment's own report.
    assert (
        "Clock alignment check" not in html
        and "Adjacent instrument spacing" not in html
    )


def test_the_page_links_reports_kept_elsewhere(site, tmp_path):
    """With --report-dir, the deployment and instrument links point into <DIR>/<deployment>."""
    yamls = [site / n / f"{n}.mooring.yaml" for n in _NAMES]
    reports = tmp_path / "outputs" / "report"
    (reports / "egM2_2016" / "instrument").mkdir(parents=True)
    (reports / "egM2_2016" / "egM2_2016_stack_report.html").touch()
    (reports / "egM2_2016" / "instrument" / "egM2_2016_13020_report.html").touch()
    with contextlib.redirect_stdout(io.StringIO()):
        out = concat_moorings(yamls, name="egM2", out_dir=tmp_path / "egM2")
        page = generate_concat_page(out, out.parent / "report", report_dir=reports)
    html = page.read_text(encoding="utf-8")
    assert 'href="../../outputs/report/egM2_2016/egM2_2016_stack_report.html"' in html
    assert (
        'href="../../outputs/report/egM2_2016/instrument/egM2_2016_13020_report.html"'
        in html
    )
    # The page names depths by their depth, never by number.
    assert "<th>Slot</th>" not in html


def test_the_page_links_each_mooring_description_near_the_top(site, tmp_path):
    """One link per deployment page found, to its "Mooring description" section."""
    yamls = [site / n / f"{n}.mooring.yaml" for n in _NAMES]
    reports = tmp_path / "outputs" / "report"
    for n in _NAMES:
        (reports / n).mkdir(parents=True)
        (reports / n / f"{n}_report.html").touch()
    with contextlib.redirect_stdout(io.StringIO()):
        out = concat_moorings(yamls, name="egM2", out_dir=tmp_path / "egM2")
        page = generate_concat_page(out, out.parent / "report", report_dir=reports)
    html = page.read_text(encoding="utf-8")
    see_also = html.split('<p class="see-also">', 1)[1].split("</p>", 1)[0]
    for n in _NAMES:
        assert (
            f'href="../../outputs/report/{n}/{n}_report.html#description"' in see_also
        )
    # Under the jump-nav, on the line of the expand-all button, just left of it.
    assert html.index('class="jump-nav"') < html.index('<p class="see-also">')
    assert html.index('<p class="see-also">') < html.index('class="expand-all"')


def test_the_command(site):
    args = build_parser().parse_args(
        [
            "concat",
            "egM2_2016",
            "egM2_2018",
            "--name",
            "egM2",
            "--proc-dir",
            str(site),
            "--force",
            "--no-report",
        ]
    )
    with contextlib.redirect_stdout(io.StringIO()):
        assert args.func(args) == 0
    bad = build_parser().parse_args(
        ["concat", "egM2_2016", "--name", "egM2", "--proc-dir", str(site), "--force"]
    )
    with contextlib.redirect_stdout(io.StringIO()) as out:
        assert bad.func(bad) == 1
    assert "at least two" in out.getvalue()


def test_validate_knows_the_depth_keys(mooring_yaml):
    cfg = mooring.load(_FIX / "egM2_2016" / "egM2_2016.mooring.yaml")
    good = copy.deepcopy(cfg)
    good["stack"] = {
        "dt_seconds": 3600,
        "depth_match": "hab",
        "depth_tolerance_m": 75,
    }
    assert not [i for i in mooring.check(mooring_yaml(good)) if i.level == "ERROR"]
    bad = copy.deepcopy(cfg)
    bad["stack"] = {"dt_seconds": 3600, "depth_match": "pressure"}
    assert any(
        "depth_match" in i.message
        for i in mooring.check(mooring_yaml(bad))
        if i.level == "ERROR"
    )


def test_the_stage_each_level_came_from_survives(joined):
    assert "source_stage" in joined.coords
    assert (joined["source_stage"].values == 3).all()


def test_a_water_depth_that_is_not_a_number_reads_nan(stacks):
    unknown = stacks[0].copy()
    unknown.attrs["waterdepth"] = "UNK"
    out = concat([unknown, stacks[1]], name="egM2")
    assert np.isnan(out["deployment_waterdepth"].values[0])
    assert np.isnan(out["depth"].values[out["deployment"].values == 0]).all()


def test_a_blank_deployment_time_still_builds_the_page(stacks, tmp_path):
    blank = stacks[0].copy()
    blank.attrs["deployment_time"] = ""
    out = tmp_path / "egM2_stackcat.nc"
    oa.write(concat([blank, stacks[1]], name="egM2"), out)
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("error", UserWarning)
        page = generate_concat_page(out, tmp_path / "report")
    assert page is not None and page.exists()


def test_depths_by_hab_are_still_listed_deep_first(stacks, tmp_path):
    """With ``depth_match: hab`` the table and labels still run by depth."""
    by_hab = []
    for s in stacks:
        s = s.copy()
        settings = yaml.safe_load(s.attrs["deployment_settings"])
        settings["stack"]["depth_match"] = "hab"
        s.attrs["deployment_settings"] = yaml.safe_dump(settings)
        by_hab.append(s)
    out = tmp_path / "egM2_stackcat.nc"
    oa.write(concat(by_hab, name="egM2"), out)
    with contextlib.redirect_stdout(io.StringIO()):
        page = generate_concat_page(out, tmp_path / "report")
    html = page.read_text(encoding="utf-8")

    # An Instruments-table cell: type, serial (linked when its page exists), depth.
    def cell(serial: str) -> int:
        return re.search(rf"[> ]{serial}(</a>)?<br><small>", html).start()

    assert cell("11514") < cell("13220")  # 2385 m before 1500 m


def test_an_overlap_goes_to_the_overlap_block(flmb):
    """FLMB 2016 and 2017 overlap for 93 hours: 2017 fills the depths, 2016 is kept aside."""
    out = concat(flmb, name="FLMB")
    assert out.sizes["N_DEPTHS"] == 20 and out.sizes["N_LEVELS"] == 40
    assert out.sizes["N_OVERLAP"] == 93 * 20
    both = np.isin(out["time"].values, flmb[0]["time"].values) & np.isin(
        out["time"].values, flmb[1]["time"].values
    )
    assert both.sum() == 93
    assert (out["deployment_index"].values[both] == 1).all()
    level_dep = out["deployment"].values
    assert (level_dep[out["level_index"].values[both]] == 1).all()
    assert (level_dep[out["overlap_level_index"].values] == 0).all()
    _assert_nothing_lost(out, flmb)


def test_the_older_rule_keeps_the_older_at_each_depth(flmb):
    out = concat(flmb, name="FLMB", overlap="older")
    level_dep = out["deployment"].values
    assert (level_dep[out["overlap_level_index"].values] == 1).all()
    _assert_nothing_lost(out, flmb)


def test_three_deployments_in_the_water_at_once(flmb):
    """FLMB 2017 again, renamed and 2 days later: a three-way overlap loses nothing."""
    third = flmb[1].assign_coords(time=flmb[1]["time"].values + np.timedelta64(48, "h"))
    third.attrs = {**flmb[1].attrs, "mooring_name": "FLMB_2017b"}
    out = concat([flmb[0], flmb[1], third], name="FLMB")
    _assert_nothing_lost(out, [flmb[0], flmb[1], third])
    level_dep = out["deployment"].values
    filled = out["level_index"].values >= 0
    index = np.broadcast_to(out["deployment_index"].values[:, None], filled.shape)
    # Every depth here has a level of each deployment: the newest one fills it.
    assert (level_dep[out["level_index"].values[filled]] == index[filled]).all()


def test_a_stack_that_skips_a_step_is_refused(flmb):
    holey = flmb[1].drop_isel(time=10)
    with pytest.raises(MooringError, match="skips steps"):
        concat([flmb[0], holey], name="FLMB")


def test_numeric_attributes_that_agree_are_kept_once(flmb):
    """``2825`` and ``2825.0`` are one value, not two per-deployment strings."""
    a, b = flmb[0].copy(), flmb[1].copy()
    a.attrs["site_depth"], b.attrs["site_depth"] = "2825", 2825.0
    out = concat([a, b], name="FLMB")
    assert "deployment_site_depth" not in out and "site_depth" in out.attrs


def test_the_build_holds_about_one_result():
    """Lazily opened stacks: the peak is well under three times the result."""
    opened = [
        xr.open_dataset(_FLMB / f"FLMB_{y}_stack.nc", engine="netcdf4")
        for y in (2016, 2017)
    ]
    tracemalloc.start()
    try:
        out = concat(opened, name="FLMB")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        for ds in opened:
            ds.close()
    assert peak < 3 * out.nbytes


def test_the_file_is_chunked_by_about_a_year(site, tmp_path):
    yamls = [site / n / f"{n}.mooring.yaml" for n in _NAMES]
    with contextlib.redirect_stdout(io.StringIO()):
        out = concat_moorings(yamls, name="egM2", out_dir=tmp_path / "egM2")
    with netCDF4.Dataset(out) as nc:
        n_time = nc.dimensions["time"].size
        assert nc["temperature"].chunking() == [min(n_time, 8760), 4]
        assert nc["level_index"].dtype == np.int16
    with xr.open_dataset(out, engine="netcdf4") as ds:
        assert (ds["level_index"].values == -1).any()


def test_adcp_bins_match_within_half_their_bin_size():
    """The dsG2 ADCP's 16 m bins: tolerance 8 m, and each bin of a re-hung ADCP finds its own."""
    with xr.open_dataset(_ADCP, engine="netcdf4") as ds:
        ranges = ds["range"].values.astype(float)
    levels = []
    for dep, hab0 in ((0, 1000.0), (1, 1003.0)):
        levels += [
            {
                "deployment": dep,
                "serial": f"24533_b{i:02d}",
                "instrument_type": "adcp",
                "depth": 2000.0 - hab0 + r,
                "hab": hab0 - r,
            }
            for i, r in enumerate(ranges)
        ]
    bin_size = float(np.median(np.diff(ranges)))
    assert bin_size == pytest.approx(16.0)
    np.testing.assert_allclose(_tolerances(levels, 50.0), 8.0)
    depth_index, depth_groups = _assign_depths(levels, "depth", 50.0)
    assert len(depth_groups) == ranges.size
    assert {s["family"] for s in depth_groups} == {"adcp"}
    n = ranges.size
    assert (depth_index[:n] == depth_index[n:]).all()


def test_an_adcp_head_never_shares_a_depth_with_a_bin():
    """dsG2 re-hung by two bins (32 m): the 2nd head lands on the 1st's bin 2, but keeps its own depth."""
    with xr.open_dataset(_ADCP, engine="netcdf4") as ds:
        ranges = ds["range"].values.astype(float)
    levels = []
    for dep, hab0 in ((0, 800.0), (1, 800.0 - ranges[2])):
        levels.append(
            {
                "deployment": dep,
                "serial": "16430_hd",
                "instrument_type": "adcp",
                "depth": np.nan,
                "hab": hab0,
            }
        )
        levels += [
            {
                "deployment": dep,
                "serial": f"16430_b{i:02d}",
                "instrument_type": "adcp",
                "depth": np.nan,
                "hab": hab0 - r,
            }
            for i, r in enumerate(ranges)
        ]
    depth_index, depth_groups = _assign_depths(levels, "hab", 50.0)
    for k in set(depth_index.tolist()):
        members = [levels[i]["serial"] for i in np.flatnonzero(depth_index == k)]
        kinds = {m.endswith("_hd") for m in members}
        assert len(kinds) == 1, members
        assert depth_groups[k]["family"] == ("adcp_head" if kinds == {True} else "adcp")


def test_the_people_of_the_imported_files_are_joined(joined):
    """egM2 2018's originators name Amy Bower once, and Tiago Bilo once (with and without "Dr.")."""
    names = joined.attrs["contributor_name"].split("; ")
    assert names.count("Amy Bower") == 1
    # Tiago Bilo is a contributor twice: the file's creator, and a processor.
    roles = joined.attrs["contributor_role"].split("; ")
    assert sorted(
        r for n, r in zip(names, roles, strict=True) if n == "Tiago Bilo"
    ) == [
        "Data Processor",
        "originator",
    ]
    assert joined.attrs["principal_investigator"] == "Amy Bower, Fiamma Straneo"
    assert "creator_name" not in joined.attrs
    assert joined["deployment_contributor_name"].values[0] == ""


def test_a_waterdepth_source_reaches_the_stack_and_the_deployment_record(
    site, tmp_path
):
    """egM2 2018 given a waterdepth_source: its stack carries it, and the site stack
    lists it for 2018 and an empty string for 2016, which says nothing."""
    text = "corrected from the originator's value; agrees with the measured pressures"
    root = tmp_path / "osnap"
    for n in _NAMES:
        shutil.copytree(site / n, root / n)
    mooring_path = root / "egM2_2018" / "egM2_2018.mooring.yaml"
    cfg = mooring.load(mooring_path)
    cfg["waterdepth_source"] = text
    mooring_path.write_text(
        yaml.safe_dump(dict(cfg), sort_keys=False), encoding="utf-8"
    )
    cfg = mooring.load(mooring_path)
    stack = _stack(cfg, root / "egM2_2018")
    assert stack.attrs["waterdepth_source"] == text
    oa.write(
        stack,
        paths.product_file(
            root / "egM2_2018", mooring_name="egM2_2018", product="stack"
        ),
        overwrite=True,
    )
    yamls = [root / n / f"{n}.mooring.yaml" for n in _NAMES]
    with contextlib.redirect_stdout(io.StringIO()):
        out = concat_moorings(yamls, name="egM2")
    with xr.open_dataset(out, engine="netcdf4") as ds:
        assert list(ds["deployment_waterdepth_source"].values) == ["", text]
        assert "waterdepth_source" not in ds.attrs
        assert "deployment_time_source" not in ds


def test_validate_wants_a_source_as_text(mooring_yaml):
    cfg = mooring.load(_FIX / "egM2_2018" / "egM2_2018.mooring.yaml")
    cfg["waterdepth_source"] = "from the cruise report"
    assert not [i for i in mooring.check(mooring_yaml(cfg)) if "_source" in i.message]
    cfg["waterdepth_source"] = 3125
    assert any(
        i.level == "ERROR" and "'waterdepth_source' must be text" in i.message
        for i in mooring.check(mooring_yaml(cfg))
    )


# The Stack cat page for a reader who did not run the pipeline -------------------


@pytest.fixture(scope="module")
def flmb_site(flmb) -> xr.Dataset:
    """The FLMB 2016 and 2017 stacks joined."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return concat(flmb, name="FLMB")


def test_the_history_records_each_step_dated(flmb_site):
    """Join, overlaps and flags are dated history entries, read back as table rows."""
    from oceanarray.reports._captions import stack_history_rows

    rows = stack_history_rows(flmb_site)
    events = [r.events[0].split(":", 1)[0] for r in rows]
    assert events[:4] == ["combine", "join", "overlaps", "flags"]
    assert all(r.when != "—" for r in rows[1:4])
    by = {r.events[0].split(":", 1)[0]: r.settings for r in rows}
    assert by["join"].startswith("1 h steps, one depth within")
    assert by["overlaps"] == "rule: the newer one"
    assert by["flags"].startswith("rule: 4")


def test_depths_are_named_by_depth_and_told_apart_by_type(flmb_site):
    """ "1824 m" for a depth; the CTD and current meter at one depth add their type
    only where both are drawn (the hydrography legend, where both have temperature)."""
    import numpy as np

    from oceanarray.reports._concat import _depth_names, _report_view

    unique = _depth_names(flmb_site)
    assert "30 m" in unique
    assert {"1824 m CTD", "1824 m CM"} <= set(unique)
    assert not any("≈" in n or "T/S" in n for n in unique)
    plain = _depth_names(flmb_site, np.zeros(flmb_site.sizes["N_DEPTHS"], bool))
    assert "1824 m CTD" not in plain and plain.count("1824 m") == 2
    held = np.isfinite(flmb_site["temperature"].values).any(0)
    assert {"1824 m CTD", "1824 m CM"} <= set(_depth_names(flmb_site, held))
    # The figures read the plain label; level_id stays unique.
    view = _report_view(flmb_site)
    assert "2724 m" in view["serial"].values and "2724 m CM" in view["level_id"].values


def test_a_short_record_is_left_out_of_every_figure_and_named(flmb_site):
    """A deployment with velocity at a depth for less than half its time is dropped."""
    from oceanarray.reports._concat import _drop_short, _page_captions, _report_view

    view = _report_view(flmb_site)
    j = list(view["level_id"].values).index("2724 m CM")
    cell = view["cell_deployment"].values[:, j]
    first = np.flatnonzero(cell == 0)
    cut = view.copy(deep=True)
    for v in ("east_velocity", "north_velocity"):
        cut[v][first[: int(0.6 * first.size)], j] = np.nan
    dropped, left_out = _drop_short(
        cut, cut["cell_deployment"].values, ["2016", "2017"], cut["serial"].values
    )
    assert left_out == {"velocity": ["2016 at 2724 m"]}
    assert np.isnan(dropped["east_velocity"].values[first, j]).all()
    assert np.isfinite(dropped["temperature"].values[first, j]).any()
    captions = _page_captions(flmb_site, dropped, {}, left_out, False, [])
    assert "(2016 at 2724 m)" in captions["speed_profile"]
    assert "2016 at 2724 m" in captions["roses"]
    assert "2016 at 2724 m" not in captions["ts_points"]
    # Oxygen goes with the hydrography; turbidity keeps the deployment marks.
    short = {"hydrography": ["2016 at 30 m"]}
    captions = _page_captions(flmb_site, view, {}, short, False, [])
    assert "half their deployment: 2016 at 30 m" in captions["dissolved_oxygen"]
    assert "▼" in captions["dissolved_oxygen"] and "▼" in captions["turbidity"]


def test_the_short_record_rule_is_worded_from_its_fraction(flmb_site, monkeypatch):
    from oceanarray.config import parameters as params
    from oceanarray.reports._concat import _page_captions, _report_view

    monkeypatch.setattr(params, "MIN_RECORD_FRACTION", 0.3)
    short = {"velocity": ["2016 at 2724 m"]}
    captions = _page_captions(flmb_site, _report_view(flmb_site), {}, short, False, [])
    assert "less than 30 % of their time" in captions["speed_profile"]
    assert "less than 30 % of their deployment" in captions["roses"]
    assert "half" not in captions["speed_profile"] + captions["roses"]


def test_instruments_are_named_by_function_not_model(stacks, tmp_path):
    """Paired at 100 m, the RCM and the Aquadopp share a row as one function: the row
    reads "current meter", no cell names a type, and the caption has no key."""
    from oceanarray.reports._concat import _depth_rows, _page_captions, _report_view

    wide = []
    for s in stacks:
        s = s.copy()
        settings = yaml.safe_load(s.attrs["deployment_settings"])
        settings["stack"]["depth_tolerance_m"] = 100
        s.attrs["deployment_settings"] = yaml.safe_dump(settings)
        wide.append(s)
    out = concat(wide, name="egM2")
    rows = _depth_rows(out, tmp_path / "egM2_stackcat.nc", tmp_path)
    assert [r["types"] for r in rows] == ["CTD", "current meter", "CTD"]
    assert not any(r["mixed"] for r in rows)
    (cm,) = [r for r in rows if r["types"] == "current meter"]
    assert {e["abbr"] for c in cm["cells"] for e in c} == {"CM"}
    caption = _page_captions(out, _report_view(out), {}, {}, False, rows)
    assert "type in front" not in caption["instr_table"]


def test_one_deployments_magnetic_north_velocity_is_named(stacks):
    """Stage-2 velocity in 2018 only: concat keeps it per deployment, the captions read it."""
    from oceanarray.reports._captions import direction_reference

    magnetic = stacks[1].copy()
    magnetic.attrs["stage2_magnetic_north"] = "CM2000"
    out = concat([stacks[0], magnetic], name="egM2")
    assert list(out["deployment_stage2_magnetic_north"].values) == ["", "CM2000"]
    assert direction_reference(out) == (
        "relative to true north, as provided by the originator; some relative to "
        "magnetic north, no declination applied"
    )
    assert direction_reference(concat(stacks, name="egM2")) == (
        "relative to true north, as provided by the originator"
    )


def test_the_time_axis_ends_at_the_data(flmb_site):
    """With deployment windows, the axis spans the values drawn, not the windows."""
    from oceanarray.plotters.timeseries import draw_stack_timeseries
    from oceanarray.reports._concat import _deployment_windows, _report_view

    view = _report_view(flmb_site)
    fig = draw_stack_timeseries(
        view,
        [("temperature", "T", False, None)],
        color_var="temperature",
        deployments=_deployment_windows(flmb_site),
    )
    import matplotlib.dates as mdates

    last = np.datetime64(
        mdates.num2date(fig.axes[0].get_xlim()[1]).replace(tzinfo=None)
    )
    finite = np.isfinite(view["temperature"].values).any(axis=1)
    assert abs(last - view["time"].values[finite][-1]) < np.timedelta64(1, "s")


def test_the_page_has_one_netcdf_appendix_and_reader_headings(site):
    yamls = [site / n / f"{n}.mooring.yaml" for n in _NAMES]
    with contextlib.redirect_stdout(io.StringIO()):
        out = concat_moorings(yamls, name="egM2", overwrite=True)
        html = generate_concat_page(out, out.parent / "report").read_text("utf-8")
    assert 'id="netcdf"' in html and 'id="netcdf_variables"' not in html
    assert "Instruments by depth" not in html and "Draft" not in html
    assert "<th>#</th>" not in html
    assert "overlap block" in html.split('id="deployments"')[1].split("</h2>", 2)[1]
    assert html.count("overlap block") == 1


def test_direction_bars_stop_at_the_axis_edge_with_an_arrow(flmb_site):
    """FLMB: spreads that run past ±180° of the mean stop at the edge, marked ">" or "<"."""
    from oceanarray.plotters.current import draw_direction_profile_by_deployment
    from oceanarray.reports._concat import _report_view

    fig = draw_direction_profile_by_deployment(_report_view(flmb_site))
    ax = fig.axes[0]
    lo, hi = ax.get_xlim()
    lines = ax.get_lines()
    assert all(lo <= x <= hi for ln in lines for x in ln.get_xdata())
    arrows = [ln for ln in lines if ln.get_marker() in ("<", ">")]
    assert arrows
    assert all(ln.get_xdata()[0] in (lo, hi) for ln in arrows)
