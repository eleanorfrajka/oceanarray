"""Keywords renamed to say what they carry: the old keyword still works, warns, and gives the same result.

Run on the committed dune2_1_2026 mooring YAML and the egM2 2016 and 2018 records
(OSNAP East).
"""

import contextlib
import io
import re
import shutil
import warnings
from pathlib import Path

import pytest
import xarray as xr

import oceanarray as oa
from oceanarray import _deprecated, mooring, paths
from oceanarray.mooring import location
from oceanarray.mooring.instruments import instrument_entries
from oceanarray.pipeline import concat as concat_module
from oceanarray.pipeline import qc, read_mooring, stack
from oceanarray.reports import MooringReport

_PROC = Path(__file__).parents[1] / "fixtures" / "proc"
_DUNE2 = _PROC / "dune2_1_2026" / "dune2_1_2026.mooring.yaml"
_EGM2 = _PROC / "egM2_concat"
_NAMES = ("egM2_2016", "egM2_2018")


@pytest.fixture(scope="module")
def mooring_dict() -> dict:
    """The dune2_1_2026 mooring YAML, loaded."""
    return mooring.load(_DUNE2)


#: (deprecation key, function, positional arguments before the renamed one,
#: whether the argument is the YAML path rather than the loaded YAML)
_CASES = [
    (
        "oceanarray.mooring.resolve_report_sections(description=)",
        mooring.resolve_report_sections,
        ("grid",),
        False,
    ),
    (
        "oceanarray.mooring.resolve_spectrum_markers(description=)",
        mooring.resolve_spectrum_markers,
        (),
        False,
    ),
    ("oceanarray.mooring.settings_yaml(cfg=)", mooring.settings_yaml, (), False),
    (
        "oceanarray.mooring.location.parse_latlon(cfg=)",
        location.parse_latlon,
        (),
        False,
    ),
    (
        "oceanarray.mooring.location.parse_latlon_with_source(cfg=)",
        location.parse_latlon_with_source,
        (),
        False,
    ),
    (
        "oceanarray.pipeline.read_mooring.header_attrs(cfg=)",
        read_mooring.header_attrs,
        (),
        False,
    ),
    (
        "oceanarray.pipeline.stack.mooring_attrs(mooring_config=)",
        stack.mooring_attrs,
        (),
        False,
    ),
    (
        "oceanarray.mooring.serial_collision_errors(mooring_config=)",
        mooring.serial_collision_errors,
        (),
        True,
    ),
]


def _keywords(key: str) -> tuple[str, str]:
    """Return the old and the new keyword of a deprecation key."""
    old = key.partition("(")[2].rstrip("=)")
    new = re.fullmatch(r"the (\w+)= keyword", _deprecated.RENAMED_NEXT[key]).group(1)
    return old, new


@pytest.mark.parametrize(("key", "func", "before", "by_path"), _CASES)
def test_the_old_keyword_warns_and_gives_the_new_keywords_result(
    mooring_dict, key, func, before, by_path
):
    """The old keyword warns, naming the new keyword and 0.8.0, and changes nothing."""
    old, new = _keywords(key)
    value = _DUNE2 if by_path else mooring_dict
    with pytest.warns(
        DeprecationWarning, match=re.escape(_deprecated.message(key))
    ) as w:
        given_old = func(*before, **{old: value})
    text = str(w[0].message)
    assert f"{new}=" in text
    assert "0.8.0" in text
    assert w[0].filename == __file__  # points at the caller
    assert given_old == func(*before, **{new: value})


@pytest.mark.parametrize(("key", "func", "before", "by_path"), _CASES)
def test_a_positional_call_does_not_warn(mooring_dict, key, func, before, by_path):
    """Giving the argument by position is unchanged and silent."""
    _, new = _keywords(key)
    value = _DUNE2 if by_path else mooring_dict
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        positional = func(*before, value)
    assert positional == func(*before, **{new: value})


@pytest.mark.parametrize(("key", "func", "before", "by_path"), _CASES)
def test_a_missing_argument_names_the_new_keyword(key, func, before, by_path):  # noqa: ARG001
    """Leaving the argument out is still a TypeError, naming the new keyword."""
    _, new = _keywords(key)
    with pytest.raises(
        TypeError, match=re.escape(f"missing required argument: {new!r}")
    ):
        func(*before)


def test_both_keywords_at_once_are_refused(mooring_dict):
    """Giving the old and the new keyword together is a TypeError."""
    with pytest.raises(TypeError, match="got both description= and the mooring_dict="):
        mooring.resolve_spectrum_markers(mooring_dict, description=mooring_dict)


def test_dune2_settings_come_through_the_old_keyword(mooring_dict):
    """The values read under an old keyword are the YAML's own."""
    with pytest.warns(DeprecationWarning):
        assert mooring.resolve_report_sections("grid", description=mooring_dict) == [
            "overflow"
        ]
    with pytest.warns(DeprecationWarning):
        markers = mooring.resolve_spectrum_markers(description=mooring_dict)
    assert [label for label, _ in markers] == ["1.8 d", "4 d", "9 min"]


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    """A copy of the two egM2 deployments, each with its stack written beside its YAML."""
    root = tmp_path_factory.mktemp("egm2")
    for name in _NAMES:
        shutil.copytree(_EGM2 / name, root / name)
        loaded = mooring.load(root / name / f"{name}.mooring.yaml")
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            data = read_mooring.read_mooring(
                loaded, stage=3, mooring_dir=root / name, allow_missing=True
            )
            oa.write(
                oa.stack(data, loaded), paths.product_file(root / name, name, "stack")
            )
    return root


def _yaml_paths(site: Path) -> list[Path]:
    """Return the two deployments' mooring YAML paths."""
    return [site / n / f"{n}.mooring.yaml" for n in _NAMES]


def _stacks(site: Path) -> list[xr.Dataset]:
    """Return the two deployment stacks, loaded."""
    out = []
    for n in _NAMES:
        with xr.open_dataset(
            paths.product_file(site / n, n, "stack"), engine="netcdf4"
        ) as ds:
            out.append(ds.load())
    return out


def test_concat_descriptions_warns_and_gives_the_same_site_stack(site):
    """concat(descriptions=) still fills the DEPLOYMENT record, as mooring_dicts= does."""
    stacks = _stacks(site)
    mooring_dicts = [mooring.load(p) for p in _yaml_paths(site)]
    key = "oceanarray.pipeline.concat.concat(descriptions=)"
    with pytest.warns(
        DeprecationWarning, match=re.escape(_deprecated.message(key))
    ) as w:
        old = concat_module.concat(stacks, name="egM2", descriptions=mooring_dicts)
    assert "mooring_dicts=" in str(w[0].message)
    assert "0.8.0" in str(w[0].message)
    assert w[0].filename == __file__
    new = concat_module.concat(stacks, name="egM2", mooring_dicts=mooring_dicts)
    xr.testing.assert_equal(old, new)
    assert list(old["deployment_cruise"].values) == list(
        new["deployment_cruise"].values
    )


def test_concat_without_mooring_dicts_does_not_warn(site):
    """Leaving the mooring YAMLs out is still allowed, and silent."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        out = concat_module.concat(_stacks(site), name="egM2")
    assert "deployment_cruise" not in out


def test_concat_moorings_descriptions_warns_and_writes_the_same_file(site, tmp_path):
    """concat_moorings(descriptions=) writes the same site stack as yaml_paths=."""
    key = "oceanarray.pipeline.concat.concat_moorings(descriptions=)"
    with (
        contextlib.redirect_stdout(io.StringIO()),
        pytest.warns(DeprecationWarning, match=re.escape(_deprecated.message(key))),
    ):
        old = concat_module.concat_moorings(
            descriptions=_yaml_paths(site), name="egM2", out_dir=tmp_path / "old"
        )
    with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        positional = concat_module.concat_moorings(
            _yaml_paths(site), name="egM2", out_dir=tmp_path / "positional"
        )
    with contextlib.redirect_stdout(io.StringIO()):
        new = concat_module.concat_moorings(
            yaml_paths=_yaml_paths(site), name="egM2", out_dir=tmp_path / "new"
        )
    with (
        xr.open_dataset(old, engine="netcdf4") as a,
        xr.open_dataset(new, engine="netcdf4") as b,
        xr.open_dataset(positional, engine="netcdf4") as c,
    ):
        xr.testing.assert_equal(a, b)
        xr.testing.assert_equal(c, b)


def test_concat_moorings_needs_its_yaml_paths():
    """concat_moorings with no mooring YAMLs names the new keyword."""
    with pytest.raises(TypeError, match="missing required argument: 'yaml_paths'"):
        concat_module.concat_moorings(name="egM2")


def test_load_qc_config_mooring_cfg_warns_and_gives_the_same_thresholds(mooring_dict):
    """load_qc_config(mooring_cfg=) gives the thresholds mooring_dict= gives, and warns."""
    entry = instrument_entries(mooring_dict)[0]
    key = "oceanarray.pipeline.qc.load_qc_config(mooring_cfg=)"
    with pytest.warns(
        DeprecationWarning, match=re.escape(_deprecated.message(key))
    ) as w:
        old = qc.load_qc_config(mooring_cfg=mooring_dict, entry=entry)
    assert "mooring_dict=" in str(w[0].message)
    assert w[0].filename == __file__
    assert old == qc.load_qc_config(mooring_dict=mooring_dict, entry=entry)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert old == qc.load_qc_config(mooring_dict, entry)


def test_load_qc_config_still_needs_both_arguments(mooring_dict):
    """Leaving out the mooring YAML or the entry is a TypeError naming it."""
    with pytest.raises(TypeError, match="'mooring_dict'"):
        qc.load_qc_config()
    with pytest.raises(TypeError, match="'entry'"):
        qc.load_qc_config(mooring_dict)


def test_mooring_report_generate_cfg_warns_and_writes_the_same_page(
    mooring_dict, tmp_path
):
    """MooringReport.generate(cfg=) writes the summary page mooring_dict= writes."""
    proc = tmp_path / "proc"
    shutil.copytree(_DUNE2.parent, proc / _DUNE2.parent.name)
    report = MooringReport(proc_dir=str(proc))
    name = mooring_dict["name"]
    key = "oceanarray.reports.MooringReport.generate(cfg=)"
    quiet = contextlib.redirect_stdout(io.StringIO())
    with (
        quiet,
        pytest.warns(
            DeprecationWarning, match=re.escape(_deprecated.message(key))
        ) as w,
    ):
        old = report.generate(name, outdir=str(tmp_path / "old"), cfg=mooring_dict)
    assert any(m.filename == __file__ for m in w)
    with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        new = report.generate(
            name, outdir=str(tmp_path / "new"), mooring_dict=mooring_dict
        )
    assert old.name == new.name
    assert old.exists() and new.exists()
