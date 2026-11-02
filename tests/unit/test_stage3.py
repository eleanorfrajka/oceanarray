"""Tests for stage3 processing functions."""

import numpy as np
import xarray as xr

from oceanarray.pipeline.coordinate import (
    apply_declination_to_enu as _apply_declination_to_enu,
)


def _make_enu_dataset():
    """Minimal dataset with east/north velocity and a time coordinate."""
    time = np.array(["2020-06-15T12:00:00"], dtype="datetime64[ns]")
    return xr.Dataset(
        {
            "east_velocity": ("time", np.array([0.1])),
            "north_velocity": ("time", np.array([0.05])),
        },
        coords={"time": time},
    )


def test_declination_guard_unknown_position():
    """When lat/lon are (0, 0) with unknown source, skip declination and store 'UNK'."""
    ds = _make_enu_dataset()
    result = _apply_declination_to_enu(
        ds,
        lat=0.0,
        lon=0.0,
        latlon_source="unknown (defaulting to 0, 0)",
    )
    assert result.attrs.get("magnetic_declination") == "UNK"
    # Velocities must be unmodified
    np.testing.assert_array_equal(result["east_velocity"].values, [0.1])
    np.testing.assert_array_equal(result["north_velocity"].values, [0.05])


def test_declination_guard_only_triggers_on_unknown_source():
    """A real (0, 0) position with a non-'unknown' source should not be blocked."""
    ds = _make_enu_dataset()
    # source doesn't contain 'unknown' → guard must NOT fire
    # (ppigrf may or may not be installed; we only check the guard path here)
    result = _apply_declination_to_enu(
        ds,
        lat=0.0,
        lon=0.0,
        latlon_source="seabed GPS fix",  # not 'unknown'
    )
    # Guard did not fire → magnetic_declination is numeric (or missing if ppigrf absent)
    decl = result.attrs.get("magnetic_declination")
    assert decl != "UNK", "guard should not fire for a non-unknown source string"


# ---------------------------------------------------------------------------
# _merge_flags
# ---------------------------------------------------------------------------

from oceanarray.pipeline.qc import (  # noqa: E402 — grouped imports after fixtures
    _merge_flags,
    _tilt_from_span,
)
from oceanarray.pipeline.qc import (  # noqa: E402 — grouped imports after fixtures
    load_qc_config as _load_qc_config,
)
from oceanarray.pipeline.qc import (  # noqa: E402 — grouped imports after fixtures
    merge_salinity_parent_qc as _merge_salinity_parent_qc,
)


def test_merge_flags_priority_bad_wins():
    """QARTOD 4 (BAD) beats 3 (SUSPECT) beats 1 (GOOD)."""
    a = np.array([1, 3, 4, 1], dtype=np.int8)
    b = np.array([4, 1, 3, 1], dtype=np.int8)
    result = _merge_flags(a, b)
    np.testing.assert_array_equal(result, [4, 3, 4, 1])


def test_merge_flags_missing_beats_bad():
    """QARTOD 9 (MISSING) has highest priority and overrides 4 (BAD)."""
    a = np.array([9, 4], dtype=np.int8)
    b = np.array([4, 9], dtype=np.int8)
    result = _merge_flags(a, b)
    np.testing.assert_array_equal(result, [9, 9])


def test_merge_flags_all_same():
    a = np.array([2, 2, 2], dtype=np.int8)
    b = np.array([2, 2, 2], dtype=np.int8)
    result = _merge_flags(a, b)
    np.testing.assert_array_equal(result, [2, 2, 2])


def test_merge_flags_single_array():
    a = np.array([1, 3, 4], dtype=np.int8)
    result = _merge_flags(a)
    np.testing.assert_array_equal(result, a)


def test_merge_flags_three_arrays():
    a = np.array([1, 1, 1], dtype=np.int8)
    b = np.array([1, 3, 1], dtype=np.int8)
    c = np.array([1, 1, 4], dtype=np.int8)
    result = _merge_flags(a, b, c)
    np.testing.assert_array_equal(result, [1, 3, 4])


def test_merge_flags_oceansites_ordering():
    """OceanSITES ordering: interpolated(8) beats good(1) but loses to bad(4);
    unknown(0) is the weakest flag, so good(1) beats unknown(0)."""
    good = np.array([1, 1, 1, 8], dtype=np.int8)
    other = np.array([8, 4, 0, 4], dtype=np.int8)
    result = _merge_flags(good, other)
    #   1 vs 8 -> 8 (interpolated overrides good)
    #   1 vs 4 -> 4 (bad overrides good)
    #   1 vs 0 -> 1 (good beats unknown; unknown is weakest)
    #   8 vs 4 -> 4 (bad overrides interpolated)
    np.testing.assert_array_equal(result, [8, 4, 1, 4])


def test_merge_priority_single_source():
    """qc._merge_flags and mooring.helpers._worst_flag share ONE priority table.

    Guards against the two merge paths drifting: both derive from
    parameters.QC_MERGE_PRIORITY, so they must agree on every combination.
    """
    from oceanarray.config import parameters as params
    from oceanarray.pipeline.helpers import _worst_flag
    from oceanarray.pipeline.qc import _QC_PRIORITY

    assert _QC_PRIORITY is params.QC_MERGE_PRIORITY
    a = np.array([1, 8, 0, 3], dtype=np.int8)
    b = np.array([8, 4, 1, 8], dtype=np.int8)
    # _worst_flag must match _merge_flags element-wise for the same inputs.
    np.testing.assert_array_equal(_worst_flag(a, b), _merge_flags(a, b))


def test_set_qc_attrs_preserves_author_long_name():
    """set_qc_attrs fills long_name only when absent — never clobbers a standalone
    diagnostic flag's own long_name (e.g. seabed_qc), but still attaches the table."""
    from oceanarray.pipeline.qc import set_qc_attrs

    ds = xr.Dataset(
        {"seabed_qc": ("time", np.array([1, 1], dtype=np.int8))},
        coords={"time": [0, 1]},
    )
    ds["seabed_qc"].attrs["long_name"] = "Seabed proximity QC flag"
    set_qc_attrs(ds, "seabed")
    assert ds["seabed_qc"].attrs["long_name"] == "Seabed proximity QC flag"
    assert "flag_meanings" in ds["seabed_qc"].attrs
    assert np.asarray(ds["seabed_qc"].attrs["flag_values"]).dtype == np.int8


# ---------------------------------------------------------------------------
# _tilt_from_span
# ---------------------------------------------------------------------------


def test_tilt_from_span_both_keys():
    qc_ranges = {"tilt": {"suspect_span": [-20, 20], "fail_span": [-30, 30]}}
    result = _tilt_from_span(qc_ranges)
    assert result["suspect_threshold"] == 20.0
    assert result["fail_threshold"] == 30.0


def test_tilt_from_span_absent_key():
    """No 'tilt' key → empty dict returned, not an error."""
    result = _tilt_from_span({})
    assert result == {}


def test_tilt_from_span_partial():
    """Only fail_span present → only fail_threshold returned."""
    qc_ranges = {"tilt": {"fail_span": [-45, 45]}}
    result = _tilt_from_span(qc_ranges)
    assert "fail_threshold" in result
    assert "suspect_threshold" not in result


# ---------------------------------------------------------------------------
# _load_qc_config
# ---------------------------------------------------------------------------


def test_load_qc_config_returns_four_dicts():
    gr, sp, tilt, fl = _load_qc_config({}, {})
    assert isinstance(gr, dict)
    assert isinstance(sp, dict)
    assert isinstance(tilt, dict)
    assert isinstance(fl, dict)


def test_load_qc_config_tilt_not_in_gross_range():
    """'tilt' must not appear in the gross_range dict — it is handled separately."""
    gr, _, _, _ = _load_qc_config({}, {})
    assert "tilt" not in gr


def test_load_qc_config_mooring_override_applied():
    """Mooring-level qc_ranges overrides the package default."""
    mooring_cfg = {
        "qc_ranges": {"temperature": {"fail_span": [-5, 50], "suspect_span": [-2, 40]}}
    }
    gr, _, _, _ = _load_qc_config(mooring_cfg, {})
    assert gr["temperature"]["fail_span"] == [-5, 50]


def test_load_qc_config_instrument_override_wins_over_mooring():
    """Instrument-level qc_ranges takes precedence over mooring-level."""
    mooring_cfg = {"qc_ranges": {"temperature": {"fail_span": [-5, 50]}}}
    entry = {"qc_ranges": {"temperature": {"fail_span": [-2, 35]}}}
    gr, _, _, _ = _load_qc_config(mooring_cfg, entry)
    assert gr["temperature"]["fail_span"] == [-2, 35]


# ---------------------------------------------------------------------------
# _apply_qc_tests
# ---------------------------------------------------------------------------

import pandas as pd  # noqa: E402 — needed for date_range in helpers below

from oceanarray.pipeline.qc import apply_qc_tests as _apply_qc_tests  # noqa: E402


def _make_pressure_dataset(n=50, stuck=False, out_of_range=False):
    """Minimal dataset for pressure QC tests."""
    time = pd.date_range("2026-01-01", periods=n, freq="10min")
    if stuck:
        pressure = np.zeros(n, dtype=float)
    elif out_of_range:
        pressure = np.full(n, 500.0)
        pressure[10] = 15000.0  # well outside fail_span
    else:
        pressure = np.full(n, 500.0)
    return xr.Dataset({"pressure": ("time", pressure)}, coords={"time": time})


def test_apply_qc_tests_good_data():
    """All-good pressure → all flags should be 1 (GOOD)."""
    ds = _make_pressure_dataset()
    from oceanarray.config import parameters as params

    gr = {"pressure": params.QC_GROSS_RANGE["pressure"]}
    result = _apply_qc_tests(ds, gr, {})
    assert "pressure_qc" in result.data_vars
    assert np.all(result["pressure_qc"].values == 1)


def test_apply_qc_tests_gross_range_flags_bad():
    """Single out-of-range spike → that sample is flagged 4 (BAD)."""
    ds = _make_pressure_dataset(out_of_range=True)
    from oceanarray.config import parameters as params

    gr = {"pressure": params.QC_GROSS_RANGE["pressure"]}
    result = _apply_qc_tests(ds, gr, {})
    flags = result["pressure_qc"].values
    assert flags[10] == 4, f"expected BAD at index 10, got {flags[10]}"
    assert np.sum(flags == 4) == 1


def test_apply_qc_tests_threshold_attrs_stored():
    """Threshold values used must be stored as attrs on the _qc variable."""
    ds = _make_pressure_dataset()
    from oceanarray.config import parameters as params

    gr = {"pressure": params.QC_GROSS_RANGE["pressure"]}
    result = _apply_qc_tests(ds, gr, {})
    attrs = result["pressure_qc"].attrs
    assert "qc_gross_range_fail_min" in attrs
    assert "qc_gross_range_fail_max" in attrs


def test_apply_qc_tests_flat_line_flags_fail():
    """Pressure stuck at zero for all 50 samples → most samples flagged FAIL (4).

    Note: ioos_qc flat_line_test with tolerance=0.0 has an edge case where
    perfectly stuck data (diff == 0.0) is NOT flagged because the check is
    ``abs(diff) > tolerance`` (strict, not ``>=``).  Use the package default
    tolerance (0.001 dbar) which correctly triggers on values that don't
    change by more than 1 mbar.
    """
    ds = _make_pressure_dataset(n=50, stuck=True)
    from oceanarray.config import parameters as params

    gr = {"pressure": params.QC_GROSS_RANGE["pressure"]}
    # Use package default tolerance (0.001), NOT 0.0 — see note above
    fl = {"pressure": params.QC_FLAT_LINE["pressure"]}
    result = _apply_qc_tests(ds, gr, {}, flat_line=fl)
    flags = result["pressure_qc"].values
    assert np.any(flags == 4), "expected at least one FAIL flag for stuck pressure"


# ---------------------------------------------------------------------------
# _merge_salinity_parent_qc
# ---------------------------------------------------------------------------


def test_merge_salinity_parent_qc_bad_temp_propagates():
    """BAD temperature flag (4) should propagate into salinity_qc."""
    time = pd.date_range("2026-01-01", periods=5, freq="10min")
    ds = xr.Dataset(
        {
            "temperature": ("time", np.full(5, 10.0)),
            "salinity": ("time", np.full(5, 35.0)),
            "temperature_qc": ("time", np.array([1, 1, 4, 1, 1], dtype=np.int8)),
        },
        coords={"time": time},
    )
    result = _merge_salinity_parent_qc(ds)
    assert result["salinity_qc"].values[2] == 4


def test_merge_salinity_parent_qc_pressure_flag8_ok():
    """pressure_qc=8 (interpolated) must NOT degrade salinity_qc."""
    time = pd.date_range("2026-01-01", periods=5, freq="10min")
    ds = xr.Dataset(
        {
            "pressure": ("time", np.full(5, 500.0)),
            "salinity": ("time", np.full(5, 35.0)),
            "pressure_qc": ("time", np.full(5, 8, dtype=np.int8)),
        },
        coords={"time": time},
    )
    result = _merge_salinity_parent_qc(ds)
    # flag 8 on pressure → salinity_qc should remain 1 (GOOD) not 8
    assert np.all(result["salinity_qc"].values == 1), (
        f"pressure flag=8 should not degrade salinity; got {result['salinity_qc'].values}"
    )


def test_declination_skipped_if_already_applied():
    """If magnetic_declination attr already exists, _apply_declination_to_enu is a no-op."""
    ds = _make_enu_dataset()
    ds.attrs["magnetic_declination"] = 5.0
    ds["east_velocity"].values[0] = 0.1
    result = _apply_declination_to_enu(ds, lat=55.0, lon=-20.0)
    # No change — already applied
    np.testing.assert_array_equal(result["east_velocity"].values, [0.1])


def test_declination_skipped_if_no_velocity():
    """Dataset without east/north velocity is returned unchanged."""
    time = np.array(["2020-06-15T12:00:00"], dtype="datetime64[ns]")
    ds = xr.Dataset(
        {"temperature": ("time", np.array([5.0]))},
        coords={"time": time},
    )
    result = _apply_declination_to_enu(ds, lat=55.0, lon=-20.0)
    assert "magnetic_declination" not in result.attrs
