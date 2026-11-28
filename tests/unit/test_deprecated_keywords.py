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
from oceanarray import _deprecated, mask_rules, mooring, paths
from oceanarray.mooring import catalog, location
from oceanarray.mooring.instruments import instrument_entries
from oceanarray.pipeline import concat as concat_module
from oceanarray.pipeline import oceansites, qc, raw_files, read_mooring, stack
from oceanarray.reports import MooringReport

_PROC = Path(__file__).parents[1] / "fixtures" / "proc"
_DUNE2 = _PROC / "dune2_1_2026" / "dune2_1_2026.mooring.yaml"
_RAW = Path(__file__).parents[1] / "fixtures" / "raw"
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
    (
        "oceanarray.pipeline.stack.people_source_sha256(mooring=)",
        stack.people_source_sha256,
        (),
        False,
    ),
    ("oceanarray.mask_rules.stack_rule(mooring=)", mask_rules.stack_rule, (), False),
    (
        "oceanarray.mooring.instruments.instrument_entries(mooring=)",
        instrument_entries,
        (),
        False,
    ),
    (
        "oceanarray.pipeline.oceansites.deployment_code(mooring=)",
        oceansites.deployment_code,
        (),
        False,
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
def dune2(mooring_dict) -> dict:
    """The dune2 instruments read raw and from each committed stage file."""
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        read = {"raw": read_mooring.read_mooring(mooring_dict, raw_dir=_RAW)}
        for n in (1, 2, 3):
            read[n] = read_mooring.read_mooring(
                mooring_dict, stage=n, mooring_dir=_DUNE2.parent
            )
    return read


#: (deprecation key, function, its other arguments from the dune2 datasets (by
#: stage, "raw"), the MicroCAT's entry and a folder: positional ones before the
#: mooring YAML, keyword ones after it)
_WITH_MORE = [
    ("oceanarray.stage1(mooring=)", oa.stage1, lambda g: ((g["raw"],), {})),
    ("oceanarray.stage2(mooring=)", oa.stage2, lambda g: ((g[1],), {})),
    ("oceanarray.stage3(mooring=)", oa.stage3, lambda g: ((g[2],), {})),
    ("oceanarray.stack(mooring=)", oa.stack, lambda g: ((g[3],), {})),
    (
        "oceanarray.pipeline.stack.stack_stale_reason(mooring=)",
        stack.stack_stale_reason,
        lambda _: (
            (
                _DUNE2.parent / "dune2_1_2026_stack.nc",
                {p.parent.name: p for p in _DUNE2.parent.glob("*/*_stage3.nc")},
            ),
            {"mooring_dir": _DUNE2.parent, "dt_seconds": 3600},
        ),
    ),
    (
        "oceanarray.mooring.catalog.lookup(mooring=)",
        catalog.lookup,
        lambda g: ((), {"entry": g["entry"]}),
    ),
    (
        "oceanarray.mooring.catalog.digest(mooring=)",
        catalog.digest,
        lambda _: ((), {"records": []}),
    ),
    (
        "oceanarray.mooring.save(mooring=)",
        mooring.save,
        lambda g: ((), {"path": g["tmp"] / "dune2.mooring.yaml", "overwrite": True}),
    ),
    (
        "oceanarray.pipeline.oceansites.to_oceansites(mooring=)",
        oceansites.to_oceansites,
        lambda g: ((g[3]["2941"],), {}),
    ),
    (
        "oceanarray.pipeline.oceansites.platform_code(mooring=)",
        oceansites.platform_code,
        lambda _: ((), {}),
    ),
    (
        "oceanarray.pipeline.raw_files.resolve_raw_file(mooring=)",
        raw_files.resolve_raw_file,
        lambda g: ((g["entry"],), {"raw_dir": _RAW}),
    ),
    (
        "oceanarray.pipeline.read_mooring.join_raw(mooring=)",
        read_mooring.join_raw,
        lambda g: ((g["raw"]["2941"], g["entry"]), {}),
    ),
]


def _assert_same(a: object, b: object) -> None:
    """Assert two results are equal: datasets by their values."""
    if isinstance(a, dict) and all(isinstance(v, xr.Dataset) for v in a.values()):
        assert a.keys() == b.keys()
        for k in a:
            xr.testing.assert_equal(a[k], b[k])
    elif isinstance(a, xr.Dataset):
        xr.testing.assert_equal(a, b)
    else:
        assert a == b


@pytest.mark.parametrize(("key", "func", "others"), _WITH_MORE)
def test_the_old_keyword_warns_where_the_function_needs_more(
    mooring_dict, dune2, tmp_path, key, func, others
):
    """mooring= warns once, naming mooring_dict= and 0.8.0; positional is silent."""
    m = {**mooring_dict, "platform_code": "DUNE2"}
    entry = next(e for e in instrument_entries(m) if str(e["serial"]) == "2941")
    before, after = others({**dune2, "entry": entry, "tmp": tmp_path})
    with pytest.warns(
        DeprecationWarning, match=re.escape(_deprecated.message(key))
    ) as w:
        old = func(*before, mooring=m, **after)
    assert [str(x.message) for x in w].count(_deprecated.message(key)) == 1
    assert "mooring_dict=" in str(w[0].message)
    assert "0.8.0" in str(w[0].message)
    assert w[0].filename == __file__
    _assert_same(old, func(*before, mooring_dict=m, **after))
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "error",
            message=r".*\(mooring=\) is deprecated",
            category=DeprecationWarning,
        )
        _assert_same(old, func(*before, m, **after))


@pytest.mark.parametrize(
    ("name", "call"),
    [
        ("entry", catalog.lookup),
        ("records", catalog.digest),
        ("path", mooring.save),
        (
            "mooring_dir",
            lambda m: stack.stack_stale_reason(_DUNE2, {}, m, dt_seconds=1),
        ),
        ("raw_dir", lambda m: raw_files.resolve_raw_file(instrument_entries(m)[0], m)),
    ],
)
def test_an_argument_after_the_mooring_yaml_is_still_required(mooring_dict, name, call):
    """Leaving out an argument that follows mooring_dict is a TypeError naming it."""
    with pytest.raises(TypeError, match=f"missing required argument: {name!r}"):
        call(mooring_dict)


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
