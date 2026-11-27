"""``plotters.helpers.masked`` and ``ruled``: released names that warn and give the new code's result.

Run on the committed egM2 2018 records (OSNAP East), stacked here so the stack
records its ``mask_flags`` rule, the committed dune2_1_2026 stack (which records
no rule and has flag-4 velocities) and an egM2 2018 MicroCAT stage-3 file.
"""

import contextlib
import io
import re
import warnings
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

import oceanarray as oa
from oceanarray import _deprecated, mask_rules, mooring
from oceanarray.pipeline import masking
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.plotters import helpers

_PROC = Path(__file__).parents[1] / "fixtures" / "proc"
_EGM2 = _PROC / "egM2_concat" / "egM2_2018"
_DUNE2_STACK = _PROC / "dune2_1_2026" / "dune2_1_2026_stack.nc"
_MICROCAT = _EGM2 / "microcat" / "egM2_2018_13220_stage3.nc"

_MASKED = "oceanarray.plotters.helpers.masked"
_RULED = "oceanarray.plotters.helpers.ruled"


@pytest.fixture(scope="module")
def ruled_stack() -> xr.Dataset:
    """The egM2 2018 stack, built from its stage-3 records; it records its rule."""
    loaded = mooring.load(_EGM2 / "egM2_2018.mooring.yaml")
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        data = read_mooring(loaded, stage=3, mooring_dir=_EGM2, allow_missing=True)
        return oa.stack(data, loaded)


def _open(path: Path) -> xr.Dataset:
    """Return a committed file, loaded."""
    with xr.open_dataset(path, engine="netcdf4") as ds:
        return ds.load()


@pytest.mark.parametrize(
    ("old", "call"),
    [
        (_MASKED, lambda ds: helpers.masked(ds, "temperature")),
        (_RULED, helpers.ruled),
    ],
)
def test_the_old_name_warns_at_the_caller(ruled_stack, old, call):
    """Each alias warns once, naming its replacement and the removal release, at the call."""
    with pytest.warns(
        DeprecationWarning, match=re.escape(_deprecated.message(old))
    ) as w:
        call(ruled_stack)
    assert len(w) == 1
    assert _deprecated.RENAMED_NEXT[old] in str(w[0].message)
    assert f"removed in {_deprecated.REMOVED_IN_NEXT};" in str(w[0].message)
    assert w[0].filename == __file__


@pytest.mark.parametrize("old", [_MASKED, _RULED])
def test_the_alias_docstring_names_the_next_removal(old):
    """The docstring says which release removes the alias."""
    func = getattr(helpers, old.rsplit(".", 1)[-1])
    assert func.__doc__.startswith("Deprecated alias of ")
    assert func.__doc__.endswith(f"removed in {_deprecated.REMOVED_IN_NEXT}.")


def test_ruled_is_true_for_a_stack_with_a_rule(ruled_stack):
    """A stack written now records its rule, so ruled says so."""
    assert mask_rules.STACK_ATTR in ruled_stack.attrs
    with pytest.warns(DeprecationWarning):
        assert helpers.ruled(ruled_stack) is True


def test_ruled_is_false_for_an_instrument_file():
    """A stage-3 instrument file records no rule."""
    ds = _open(_MICROCAT)
    with pytest.warns(DeprecationWarning):
        assert helpers.ruled(ds) is False


@pytest.mark.parametrize("var", ["temperature", "salinity", "east_velocity"])
def test_masked_on_a_ruled_stack_is_the_rule_applied(ruled_stack, var):
    """On a stack with a rule, masked gives the stack's rule applied, as in 0.6.2."""
    with pytest.warns(DeprecationWarning):
        old = helpers.masked(ruled_stack, var)
    expected = masking.masked(
        ruled_stack, var, mask_rules.from_attrs(ruled_stack.attrs)
    )
    np.testing.assert_array_equal(np.isnan(old), np.isnan(expected))
    np.testing.assert_array_equal(old, expected)
    assert old.dtype == np.float64


@pytest.mark.parametrize("var", ["east_velocity", "north_velocity"])
def test_masked_hides_what_the_masking_function_hides(var):
    """On a file without a rule, masked gives what pipeline.masking.masked gives."""
    ds = _open(_DUNE2_STACK)
    with pytest.warns(DeprecationWarning):
        old = helpers.masked(ds, var)
    np.testing.assert_array_equal(np.isnan(old), np.isnan(masking.masked(ds, var)))
    # The default rule hides the flag-4 velocities this stack holds.
    hidden = np.isnan(old) & ~np.isnan(ds[var].values)
    assert hidden.any()
    assert (ds[f"{var}_qc"].values[hidden] == 4).all()


def test_masked_on_an_instrument_file_matches_the_masking_function():
    """A stage-3 instrument file gives the same NaN pattern as the new function."""
    ds = _open(_MICROCAT)
    with pytest.warns(DeprecationWarning):
        old = helpers.masked(ds, "salinity")
    np.testing.assert_array_equal(
        np.isnan(old), np.isnan(masking.masked(ds, "salinity"))
    )
