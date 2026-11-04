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
