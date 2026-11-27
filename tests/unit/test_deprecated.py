"""Every deprecated name in one table: it still works, warns, and is on the migration page."""

import contextlib
import importlib
import re
import types
import warnings
from pathlib import Path

import pytest

import oceanarray as oa
from oceanarray import _deprecated

_PAGE = Path(__file__).parents[2] / "docs" / "source" / "migrating_0_6_0.rst"


def _resolve(dotted: str) -> object:
    """Return the object at a dotted name, without calling it."""
    module, _, attr = dotted.rpartition(".")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return getattr(importlib.import_module(module), attr)


@pytest.mark.parametrize("old", sorted(_deprecated.RENAMED))
def test_every_old_name_still_resolves(old):
    """A deprecated name is still there for one release."""
    assert _resolve(old) is not None


@pytest.mark.parametrize("old", sorted(_deprecated.RENAMED))
def test_every_old_name_is_on_the_migration_page(old):
    """The migration page lists each old name with what to use instead."""
    page = _PAGE.read_text(encoding="utf-8")
    assert f"``{old}``" in page
    new = _deprecated.RENAMED[old]
    assert new in page or f"``{new}``" in page


def _use(old: str) -> None:
    """Look up and use the deprecated name *old* the way a caller would."""
    module, _, attr = old.rpartition(".")
    obj = getattr(importlib.import_module(module), attr)
    if isinstance(obj, types.ModuleType):
        return  # the lookup itself is the use
    if isinstance(obj, type):
        kwargs = {"raw_dir": "."} if attr == "MooringProcessor" else {}
        obj(proc_dir=".", **kwargs)
        return
    # An alias warns before its arguments are checked; the one hand-written
    # wrapper needs its path argument.
    args = ("nowhere.mooring.yaml",) if attr == "validate_mooring_yaml" else ()
    with contextlib.suppress(Exception):
        obj(*args)


@pytest.mark.parametrize("old", sorted(_deprecated.RENAMED))
def test_using_an_old_name_warns_with_its_replacement(old):
    """Every entry warns on use, naming the replacement and the removal release."""
    with pytest.warns(DeprecationWarning, match=re.escape(_deprecated.message(old))):
        _use(old)


def test_an_alias_returns_what_the_new_name_returns():
    """The alias calls the new function unchanged."""
    import numpy as np

    from oceanarray.legacy import process_rodb

    values = np.arange(20.0)
    with pytest.warns(DeprecationWarning):
        old = process_rodb.normalize_by_middle_percent(values)
    np.testing.assert_array_equal(old, process_rodb.to_zscore(values))


def test_top_level_parameters_warns_and_is_the_config_module():
    """oceanarray.parameters still reaches the parameters module, with a warning."""
    from oceanarray.config import parameters

    with pytest.warns(DeprecationWarning, match="oceanarray.config.parameters"):
        assert oa.parameters is parameters


def test_internal_calls_stay_quiet():
    """Inside internal(), oceanarray's own use of an old class does not warn."""
    from oceanarray.pipeline.stack import MooringStacker

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        with _deprecated.internal():
            MooringStacker(proc_dir=".")


def test_every_public_name_resolves_after_a_bare_import():
    """Each name in oceanarray.__all__ is reachable after ``import oceanarray``."""
    for name in oa.__all__:
        assert getattr(oa, name) is not None, name
    assert set(oa.__all__) <= set(dir(oa))


def test_an_unknown_name_is_an_attribute_error():
    """A typo is an AttributeError, not an import of something else."""
    with pytest.raises(AttributeError):
        oa.no_such_name  # noqa: B018


def test_old_clock_package_warns_and_maps_to_the_moved_modules():
    """The pre-move clock path still imports, warns once, and gives the moved modules."""
    import subprocess
    import sys

    code = (
        "import warnings\n"
        "warnings.simplefilter('error', DeprecationWarning)\n"
        "try:\n"
        "    import oceanarray.pipeline.clock\n"
        "except DeprecationWarning as w:\n"
        "    print('warned:', w)\n"
        "warnings.simplefilter('ignore', DeprecationWarning)\n"
        "from oceanarray.pipeline.clock import offsets, deployment\n"
        "from oceanarray.pipeline.clock.offsets import lag_correlation\n"
        "from oceanarray.analysis import clock\n"
        "print(offsets is clock.offsets, deployment is clock.deployment,"
        " lag_correlation is clock.offsets.lag_correlation)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert "warned: oceanarray.pipeline.clock is deprecated" in out
    assert "oceanarray.analysis.clock" in out
    assert out.strip().endswith("True True True")


@pytest.mark.parametrize(
    ("name", "new_home"),
    [
        ("pressure_from_depth", "oceanarray.physics"),
        ("depth_from_pressure", "oceanarray.physics"),
        ("ORIGINATOR_BIN_PRESSURE_ATTRS", "oceanarray.readers.adcp_geometry"),
        ("originator_bin_geometry", "oceanarray.readers.adcp_geometry"),
    ],
)
def test_moved_pressure_names_warn_and_are_the_moved_objects(name, new_home):
    """A name moved out of pipeline.pressure still imports there, warning with its new home."""
    from oceanarray.pipeline import pressure

    with pytest.warns(DeprecationWarning, match=re.escape(f"use {new_home}.{name}")):
        old = getattr(pressure, name)
    assert old is getattr(importlib.import_module(new_home), name)


@pytest.mark.parametrize(
    ("old", "new", "name"),
    [
        ("oceanarray.instruments", "oceanarray.mooring.instruments", "nominal_depth"),
        ("oceanarray.location", "oceanarray.mooring.location", "parse_latlon"),
        (
            "oceanarray.config.validation",
            "oceanarray.mooring.validation",
            "check_serials",
        ),
    ],
)
def test_moved_mooring_modules_warn_and_give_the_moved_objects(old, new, name):
    """An old mooring-module path still imports, warns with the new home, and gives the same object."""
    module = importlib.import_module(old)
    new_module = importlib.import_module(new)
    match = re.escape(
        f"{old}.{name} is deprecated and is removed in 0.8.0; use {new}.{name}"
    )
    with pytest.warns(DeprecationWarning, match=match):
        attribute = getattr(module, name)
    namespace: dict = {}
    with pytest.warns(DeprecationWarning, match=match):
        exec(f"from {old} import {name}", namespace)
    assert attribute is namespace[name] is getattr(new_module, name)


@pytest.mark.parametrize(
    "old",
    ["oceanarray.instruments", "oceanarray.location", "oceanarray.config.validation"],
)
def test_moved_mooring_modules_reject_an_unknown_name(old):
    """A name the moved module does not have is an AttributeError on the old path too."""
    with pytest.raises(AttributeError, match=re.escape(old)):
        importlib.import_module(old).no_such_name  # noqa: B018


_PAGE_NEXT = Path(__file__).parents[2] / "docs" / "source" / "migrating_0_7_0.rst"


@pytest.mark.parametrize("old", sorted(_deprecated.RENAMED_NEXT))
def test_every_name_deprecated_in_0_7_is_on_its_migration_page(old):
    """The 0.7.0 migration page lists each old keyword with the new one."""
    page = _PAGE_NEXT.read_text(encoding="utf-8")
    assert f"``{old}``" in page
    new = re.fullmatch(r"the (\w+=) keyword", _deprecated.RENAMED_NEXT[old])
    assert new is not None
    assert f"``{new.group(1)}``" in page


@pytest.mark.parametrize("old", sorted(_deprecated.RENAMED_NEXT))
def test_a_name_deprecated_in_0_7_is_removed_in_the_next_release(old):
    """Its warning names the second removal release, not the first."""
    assert f"removed in {_deprecated.REMOVED_IN_NEXT};" in _deprecated.message(old)
    assert _deprecated.REMOVED_IN not in _deprecated.message(old)
