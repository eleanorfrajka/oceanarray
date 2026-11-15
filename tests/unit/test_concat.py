"""Concatenating one site's deployment stacks, on the egM2 2016 and 2018 records (OSNAP East).

The fixture holds three instruments of each deployment, 10 days either side of the
2018 turnaround: two MicroCAT depths found in both years, and a current meter (2016)
and an Aquadopp (2018) 74 m apart.
"""

import contextlib
import copy
import io
import shutil
import warnings
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
import yaml

import oceanarray as oa
from oceanarray import mooring, paths
from oceanarray.cli import build_parser
from oceanarray.exceptions import MooringError
from oceanarray.pipeline.concat import concat, concat_moorings
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.reports._concat import generate_concat_page

_FIX = Path(__file__).parent.parent / "fixtures" / "proc" / "egM2_concat"
_NAMES = ("egM2_2016", "egM2_2018")


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
            _stack(cfg, root / name), paths.product_file(root / name, name, "stack")
        )
    return root


@pytest.fixture(scope="module")
def stacks(site) -> list[xr.Dataset]:
    """The two deployment stacks, loaded."""
    out = []
    for n in _NAMES:
        with xr.open_dataset(paths.product_file(site / n, n, "stack")) as ds:
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


def test_each_level_is_empty_outside_its_deployment(stacks, joined):
    """Values are the deployment stack's, on its own steps; NaN elsewhere."""
    for k, s in enumerate(stacks):
        cols = np.flatnonzero(joined["deployment"].values == k)
        inside = np.isin(joined["time"].values, s["time"].values)
        np.testing.assert_array_equal(
            joined["temperature"].values[np.ix_(inside, cols)], s["temperature"].values
        )
        assert np.isnan(joined["temperature"].values[np.ix_(~inside, cols)]).all()
        assert (joined["temperature_qc"].values[np.ix_(~inside, cols)] == 9).all()


def test_the_turnaround_is_a_gap(joined):
    """2018-06-16 09:00 to 2018-06-19 01:00: no deployment counts, nothing is filled."""
    index = joined["deployment_index"].values
    gap = index == -1
    assert gap.sum() == 63
    assert np.isnan(joined["pressure"].values[gap]).all()


def test_slots_pair_the_same_depth_across_years(joined):
    """Two MicroCAT depths are in both deployments: 6 levels in 4 slots, numbered deep-first."""
    slot = joined["slot"].values
    assert joined.sizes["SLOT"] == 4
    ref = joined["slot_reference_value"].values
    assert (np.diff(ref) <= 0).all()
    ids = list(joined["level_id"].values)
    assert slot[ids.index("egM2_2016/13020")] == slot[ids.index("egM2_2018/11514")]
    assert slot[ids.index("egM2_2016/11331")] == slot[ids.index("egM2_2018/13220")]
    # The 1926 m current meter and the 2000 m Aquadopp are 74 m apart: two slots.
    assert slot[ids.index("egM2_2016/8364")] != slot[ids.index("egM2_2018/CM2000")]


def test_a_wider_tolerance_pairs_the_current_meters(stacks):
    """``stack: slot_tolerance_m: 100`` puts the two velocity instruments in one slot."""
    wide = []
    for s in stacks:
        s = s.copy()
        settings = yaml.safe_load(s.attrs["deployment_settings"])
        settings["stack"]["slot_tolerance_m"] = 100
        s.attrs["deployment_settings"] = yaml.safe_dump(settings)
        wide.append(s)
    out = concat(wide, name="egM2")
    ids = list(out["level_id"].values)
    slot = out["slot"].values
    assert slot[ids.index("egM2_2016/8364")] == slot[ids.index("egM2_2018/CM2000")]


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


def test_different_slot_rules_are_refused(stacks):
    other = stacks[1].copy()
    settings = yaml.safe_load(other.attrs["deployment_settings"])
    settings["stack"]["slot_tolerance_m"] = 100
    other.attrs["deployment_settings"] = yaml.safe_dump(settings)
    with pytest.raises(MooringError, match="different slot rules"):
        concat([stacks[0], other], name="egM2")


def test_the_file_verb_writes_the_stackcat_and_its_cruises(site):
    yamls = [site / n / f"{n}.mooring.yaml" for n in _NAMES]
    with contextlib.redirect_stdout(io.StringIO()):
        out = concat_moorings(yamls, name="egM2", overwrite=True)
    assert out == site / "egM2" / "egM2_stackcat.nc"
    with xr.open_dataset(out) as ds:
        assert list(ds["deployment_cruise"].values) == ["DY054", "AR30-03"]
        assert list(ds["recovery_cruise"].values) == ["MSM74", "AR46"]
        assert list(ds["source_file"].values) == [
            "../egM2_2016/egM2_2016_stack.nc",
            "../egM2_2018/egM2_2018_stack.nc",
        ]


def test_the_file_verb_will_not_write_into_a_deployment_folder(site):
    yamls = [site / n / f"{n}.mooring.yaml" for n in _NAMES]
    with pytest.raises(MooringError, match="holds a mooring description"):
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
    assert "Concatenated stack" in html and "Stack cat" in html
    assert "Instruments (avg)" in html and "3 (MicroCAT 2" in html
    for section in (
        "Deployments",
        "Instruments by depth slot",
        "Hydrography",
        "T-S diagram",
    ):
        assert section in html
    # Per-deployment diagnostics stay in each deployment's own report.
    assert (
        "Clock alignment check" not in html
        and "Adjacent instrument spacing" not in html
    )


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


def test_validate_knows_the_slot_keys():
    cfg = mooring.load(_FIX / "egM2_2016" / "egM2_2016.mooring.yaml")
    good = copy.deepcopy(cfg)
    good["stack"] = {
        "dt_seconds": 3600,
        "slot_reference": "hab",
        "slot_tolerance_m": 75,
    }
    assert not [i for i in mooring.check(good) if i.level == "ERROR"]
    bad = copy.deepcopy(cfg)
    bad["stack"] = {"dt_seconds": 3600, "slot_reference": "pressure"}
    assert any(
        "slot_reference" in i.message for i in mooring.check(bad) if i.level == "ERROR"
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


def test_slots_by_hab_are_still_listed_deep_first(stacks, tmp_path):
    """With ``slot_reference: hab`` the table and labels still run by depth."""
    by_hab = []
    for s in stacks:
        s = s.copy()
        settings = yaml.safe_load(s.attrs["deployment_settings"])
        settings["stack"]["slot_reference"] = "hab"
        s.attrs["deployment_settings"] = yaml.safe_dump(settings)
        by_hab.append(s)
    out = tmp_path / "egM2_stackcat.nc"
    oa.write(concat(by_hab, name="egM2"), out)
    with contextlib.redirect_stdout(io.StringIO()):
        page = generate_concat_page(out, tmp_path / "report")
    html = page.read_text(encoding="utf-8")
    first = html.index("microcat 11514")
    assert first < html.index("microcat 13220")  # 2385 m before 1500 m


def test_an_overlap_keeps_both_and_the_newer_counts(stacks):
    """The 2018 record moved 5 days earlier, so both moorings are in the water at once."""
    shift = np.timedelta64(5 * 24, "h") + np.timedelta64(3 * 24, "h")
    early = stacks[1].assign_coords(time=stacks[1]["time"].values - shift)
    out = concat([stacks[0], early], name="egM2")
    both = np.isin(out["time"].values, stacks[0]["time"].values) & np.isin(
        out["time"].values, early["time"].values
    )
    assert both.sum() > 0
    assert (out["deployment_index"].values[both] == 1).all()
    cols16 = out["deployment"].values == 0
    cols18 = out["deployment"].values == 1
    assert np.isfinite(out["temperature"].values[np.ix_(both, cols16)]).any()
    assert np.isfinite(out["temperature"].values[np.ix_(both, cols18)]).any()
    older = concat([stacks[0], early], name="egM2", overlap="older")
    assert (older["deployment_index"].values[both] == 0).all()
