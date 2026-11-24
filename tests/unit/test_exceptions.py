"""Every public error class oceanarray defines derives from OceanarrayError."""

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

import oceanarray
from oceanarray.exceptions import MooringError, OceanarrayError, StageError

_OSNAP = Path(__file__).parent.parent / "fixtures" / "raw" / "osnap"

# The shared report layer belongs to ctdcast; oceanarray's door to its units error is
# config.unit_tables.UnitsError, which the walk checks.
_SKIP = (
    "oceanarray.legacy",
    "oceanarray.reports._vendored",
    "oceanarray.pipeline.clock",
)


def _package_errors():
    for info in pkgutil.walk_packages(oceanarray.__path__, "oceanarray."):
        if info.name.startswith(_SKIP):
            continue
        module = importlib.import_module(info.name)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseException)
                and obj.__module__ == info.name
                and not name.startswith("_")
            ):
                yield obj


@pytest.mark.filterwarnings(
    "ignore::DeprecationWarning"
)  # walking imports the deprecated clock alias
def test_every_package_error_derives_from_the_base():
    errors = list(_package_errors())
    assert MooringError in errors and StageError in errors
    stray = [e.__qualname__ for e in errors if not issubclass(e, OceanarrayError)]
    assert stray == []


def test_stage_error_is_not_a_mooring_error_but_still_a_value_error():
    assert not issubclass(StageError, MooringError)
    assert issubclass(StageError, ValueError)
    assert issubclass(MooringError, ValueError)


def test_a_unit_problem_is_an_oceanarray_error():
    """A real stage-1 variable with an unreadable unit raises an OceanarrayError."""
    from oceanarray.config.unit_tables import UnitsError
    from oceanarray.pipeline.units import to_canonical_units
    from oceanarray.readers import read_oceansites

    ds = read_oceansites(_OSNAP / "CF7_MCTD_2020_22225_50m.nc")
    var = next(v for v in ("temperature", "TEMP") if v in ds)
    ds[var].attrs["units"] = "furlongs"
    with pytest.raises(OceanarrayError):
        to_canonical_units(ds)
    assert issubclass(UnitsError, OceanarrayError) and issubclass(
        UnitsError, ValueError
    )
