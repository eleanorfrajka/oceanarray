"""Unit tests for :mod:`oceanarray.pipeline.qc`.

QARTOD QC wrappers, OceanSITES flag handling, and CTD derivations. The flag
scheme (OceanSITES Reference Table 2) and merge priority are the scientific
conventions under test — assertions encode the documented behaviour so a silent
change to a threshold, a flag mapping, or the merge order is caught.

Milestone B of ``.claude/notes/2026-08-19-stage-coverage-plan.md``.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.pipeline import qc as Q


def _dt(seconds: list[float]) -> np.ndarray:
    base = np.datetime64("2026-01-01T00:00:00", "ns")
    return base + (np.asarray(seconds) * 1e9).astype("timedelta64[ns]")


# ---------------------------------------------------------------------------
# _merge_flags  (OceanSITES priority: 0 < 1 < 2 < 7 < 8 < 3 < 4 < 9)
# ---------------------------------------------------------------------------


def test_merge_flags_worst_wins_by_priority():
    a = np.array([1, 2, 8, 4, 0], dtype=np.int8)
    b = np.array([2, 3, 3, 9, 1], dtype=np.int8)
    # (1,2)->2  (2,3)->3  (8,3)->3  (4,9)->9  (0,1)->1
    np.testing.assert_array_equal(Q._merge_flags(a, b), [2, 3, 3, 9, 1])


def test_merge_flags_bad_outranks_interpolated():
    """Flag 4 (bad) beats 8 (interpolated) despite 8 > 4 numerically."""
    np.testing.assert_array_equal(
        Q._merge_flags(np.array([8], np.int8), np.array([4], np.int8)), [4]
    )


def test_merge_flags_two_dimensional():
    a = np.array([[1, 4], [3, 1]], dtype=np.int8)
    b = np.array([[2, 1], [1, 9]], dtype=np.int8)
    np.testing.assert_array_equal(Q._merge_flags(a, b), [[2, 4], [3, 9]])


# ---------------------------------------------------------------------------
# _qartod_flags  (QARTOD result -> OceanSITES; UNKNOWN(2) -> unknown(0))
# ---------------------------------------------------------------------------


def test_ingest_qartod_remaps_unknown_2_to_0():
    """QARTOD 'could not evaluate' (2) must not assert probably_good; -> 0."""
    out = Q._qartod_flags(np.array([1, 2, 3, 4], dtype=float))
    np.testing.assert_array_equal(out, [1, 0, 3, 4])
    assert out.dtype == np.int8


def test_ingest_qartod_nan_becomes_missing_9():
    out = Q._qartod_flags(np.array([1.0, np.nan, 3.0]))
    np.testing.assert_array_equal(out, [1, 9, 3])


def test_ingest_qartod_masked_array_filled_with_9():
    masked = np.ma.array([1, 5, 3], mask=[False, True, False])
    np.testing.assert_array_equal(Q._qartod_flags(masked), [1, 9, 3])


# ---------------------------------------------------------------------------
# set_qc_attrs
# ---------------------------------------------------------------------------


def test_set_qc_attrs_writes_oceansites_flag_metadata():
    ds = xr.Dataset(
        {
            "temperature": (
                "time",
                np.array([10.0, 11.0]),
                {"standard_name": "sea_water_temperature"},
            ),
            "temperature_qc": ("time", np.array([1, 1], dtype=np.int8)),
        },
        coords={"time": _dt([0, 1])},
    )
    Q.set_qc_attrs(ds, "temperature")
    a = ds["temperature_qc"].attrs
    assert a["conventions"] == params.QC_CONVENTION
    assert a["flag_meanings"] == params.QC_FLAG_MEANINGS
    np.testing.assert_array_equal(a["flag_values"], params.QC_FLAG_VALUES_I8)
    # status_flag standard_name derived from the parent's standard_name
    assert a["standard_name"] == "sea_water_temperature status_flag"
    assert a["long_name"] == "quality flag for temperature"


def test_set_qc_attrs_skips_standard_name_when_parent_has_none():
    ds = xr.Dataset(
        {
            "turbidity": ("time", np.array([1.0, 2.0])),
            "turbidity_qc": ("time", np.array([1, 1], dtype=np.int8)),
        },
        coords={"time": _dt([0, 1])},
    )
    Q.set_qc_attrs(ds, "turbidity")
    assert "standard_name" not in ds["turbidity_qc"].attrs  # never fabricated


def test_set_qc_attrs_preserves_existing_long_name():
    ds = xr.Dataset(
        {
            "seabed": ("time", np.array([0.0])),
            "seabed_qc": (
                "time",
                np.array([1], dtype=np.int8),
                {"long_name": "seabed detection flag"},
            ),
        },
        coords={"time": _dt([0])},
    )
    Q.set_qc_attrs(ds, "seabed")
    assert ds["seabed_qc"].attrs["long_name"] == "seabed detection flag"


# ---------------------------------------------------------------------------
# _deep_merge / _tilt_from_span / load_qc_config
# ---------------------------------------------------------------------------


def test_deep_merge_override_wins_at_variable_level():
    base = {
        "temperature": {"fail_span": [-2, 40]},
        "pressure": {"fail_span": [0, 6000]},
    }
    override = {
        "temperature": {"fail_span": [-3, 45]},
        "salinity": {"fail_span": [0, 42]},
    }
    merged = Q._deep_merge(base, override)
    assert merged["temperature"]["fail_span"] == [-3, 45]  # overridden
    assert merged["pressure"]["fail_span"] == [0, 6000]  # untouched
    assert merged["salinity"]["fail_span"] == [0, 42]  # added
    assert base["temperature"]["fail_span"] == [-2, 40]  # original not mutated


def test_tilt_from_span_extracts_upper_bounds():
    out = Q._tilt_from_span(
        {"tilt": {"suspect_span": [-20, 20], "fail_span": [-30, 30]}}
    )
    assert out == {"suspect_threshold": 20.0, "fail_threshold": 30.0}


def test_tilt_from_span_absent_tilt_returns_empty():
    assert Q._tilt_from_span({"temperature": {"fail_span": [-2, 40]}}) == {}


def test_load_qc_config_defaults_and_strips_tilt_from_gross_range():
    gr, sp, tilt, fl = Q.load_qc_config({}, {})
    assert "tilt" not in gr  # tilt is handled separately, never a gross-range var
    assert isinstance(sp, dict) and isinstance(fl, dict)


def test_load_qc_config_instrument_override_beats_mooring():
    mooring = {"qc_spike": {"temperature": {"fail_threshold": 5.0}}}
    entry = {"qc_spike": {"temperature": {"fail_threshold": 2.0}}}
    _gr, sp, _tilt, _fl = Q.load_qc_config(mooring, entry)
    assert sp["temperature"]["fail_threshold"] == 2.0  # instrument wins


# ---------------------------------------------------------------------------
# apply_tilt_qc — fallback path (compute from raw pitch/roll)
# ---------------------------------------------------------------------------


def _vel_ds_with_attitude(pitch, roll) -> xr.Dataset:
    n = len(pitch)
    return xr.Dataset(
        {
            "east_velocity": ("time", np.ones(n)),
            "pitch": ("time", np.asarray(pitch, dtype=float)),
            "roll": ("time", np.asarray(roll, dtype=float)),
        },
        coords={"time": _dt(list(range(n)))},
    )


def test_apply_tilt_qc_noop_without_pitch_or_roll():
    ds = xr.Dataset(
        {"east_velocity": ("time", np.ones(3))}, coords={"time": _dt([0, 1, 2])}
    )
    out, n_suspect, n_bad = Q.apply_tilt_qc(ds, {})
    assert (n_suspect, n_bad) == (0, 0)
    assert "east_velocity_qc" not in out.data_vars


def test_apply_tilt_qc_fallback_flags_by_threshold():
    """tilt = max(|pitch|,|roll|); >=fail -> 4, >=suspect -> 3, else 1."""
    ds = _vel_ds_with_attitude(pitch=[10.0, 25.0, 35.0], roll=[5.0, 5.0, 5.0])
    out, n_suspect, n_bad = Q.apply_tilt_qc(
        ds, {"suspect_threshold": 20.0, "fail_threshold": 30.0}
    )
    assert (n_suspect, n_bad) == (1, 1)
    np.testing.assert_array_equal(out["east_velocity_qc"].values, [1, 3, 4])
    # thresholds persisted for the report reference lines
    assert out.attrs["tilt_suspect_threshold"] == 20.0
    assert out.attrs["tilt_fail_threshold"] == 30.0


# ---------------------------------------------------------------------------
# merge_salinity_parent_qc / unify_velocity_qc
# ---------------------------------------------------------------------------


def test_merge_salinity_parent_qc_propagates_worst_but_pressure_8_is_good():
    ds = xr.Dataset(
        {
            "salinity": ("time", np.array([35.0, 35.0])),
            "salinity_qc": ("time", np.array([1, 1], dtype=np.int8)),
            "temperature_qc": ("time", np.array([1, 4], dtype=np.int8)),
            "conductivity_qc": ("time", np.array([1, 1], dtype=np.int8)),
            "pressure_qc": ("time", np.array([8, 8], dtype=np.int8)),  # interpolated
        },
        coords={"time": _dt([0, 1])},
    )
    out = Q.merge_salinity_parent_qc(ds)
    # t=0: all good -> 1 (pressure 8 treated as good, not degrading salinity)
    # t=1: temperature bad -> 4
    np.testing.assert_array_equal(out["salinity_qc"].values, [1, 4])


def test_unify_velocity_qc_writes_worst_flag_to_all_components():
    ds = xr.Dataset(
        {
            "east_velocity_qc": ("time", np.array([1, 3], dtype=np.int8)),
            "north_velocity_qc": ("time", np.array([1, 1], dtype=np.int8)),
            "up_velocity_qc": ("time", np.array([4, 1], dtype=np.int8)),
        },
        coords={"time": _dt([0, 1])},
    )
    out = Q.unify_velocity_qc(ds)
    for k in ("east_velocity_qc", "north_velocity_qc", "up_velocity_qc"):
        np.testing.assert_array_equal(out[k].values, [4, 3])  # worst across all three


# ---------------------------------------------------------------------------
# apply_qc_tests — QARTOD gross-range (needs ioos_qc)
# ---------------------------------------------------------------------------


def test_apply_qc_tests_gross_range_flags_bad_suspect_good():
    ds = xr.Dataset(
        {"temperature": ("time", np.array([20.0, 37.0, 100.0]))},
        coords={"time": _dt([0, 1, 2])},
    )
    gr = {"temperature": {"fail_span": [-2.5, 40.0], "suspect_span": [-2.0, 35.0]}}
    out = Q.apply_qc_tests(ds, gr, {})
    flags = out["temperature_qc"].values
    # 20 in suspect span -> good(1); 37 outside suspect but inside fail -> suspect(3);
    # 100 outside fail -> bad(4)
    np.testing.assert_array_equal(flags, [1, 3, 4])
    # thresholds stored for provenance
    a = out["temperature_qc"].attrs
    assert a["qc_gross_range_fail_max"] == 40.0
    assert a["qc_gross_range_suspect_max"] == 35.0


def test_apply_qc_tests_preserves_worse_existing_flag():
    """A pre-existing bad flag (e.g. from pressure interpolation) is not downgraded."""
    ds = xr.Dataset(
        {
            "temperature": ("time", np.array([20.0, 20.0])),
            "temperature_qc": ("time", np.array([1, 4], dtype=np.int8)),
        },
        coords={"time": _dt([0, 1])},
    )
    gr = {"temperature": {"fail_span": [-2.5, 40.0], "suspect_span": [-2.0, 35.0]}}
    out = Q.apply_qc_tests(ds, gr, {})
    # both values are good by gross-range, but the pre-existing bad(4) at t=1 survives
    np.testing.assert_array_equal(out["temperature_qc"].values, [1, 4])


# ---------------------------------------------------------------------------
# derive_oxygen_saturation (needs gsw) — structural + missing-var no-op
# ---------------------------------------------------------------------------


def test_derive_oxygen_saturation_noop_when_inputs_missing():
    ds = xr.Dataset(
        {"temperature": ("time", np.array([10.0]))}, coords={"time": _dt([0])}
    )
    out = Q.derive_oxygen_saturation(ds)
    assert "oxygen_saturation_pct" not in out.data_vars


def _o2_ds(value: float, units: str, t=8.0, sp=35.0, p=500.0):
    """A 2-point oxygen dataset carrying *units* on dissolved_oxygen."""
    return xr.Dataset(
        {
            "dissolved_oxygen": ("time", np.full(2, value), {"units": units}),
            "temperature": ("time", np.full(2, t)),
            "salinity": ("time", np.full(2, sp)),
            "pressure": ("time", np.full(2, p)),
        },
        coords={"time": _dt([0, 1])},
    )


def test_derive_oxygen_saturation_per_mass_uses_value_directly():
    """A per-mass (umol kg-1) input is compared to O2sol directly — no ÷density.

    Cross-checked against an independent gsw path.  Catches a regression that
    double-divides a per-mass value by seawater density (the pre-fix bug).
    """
    import gsw

    do, t, sp, p = 250.0, 8.0, 35.0, 500.0  # µmol/kg
    out = Q.derive_oxygen_saturation(_o2_ds(do, "umol kg-1", t, sp, p))
    assert "oxygen_saturation_pct" in out.data_vars

    sa = gsw.SA_from_SP(sp, p, 0.0, 0.0)
    pt = gsw.pt0_from_t(sa, t, p)
    o2sol = gsw.O2sol_SP_pt(sp, pt)  # µmol/kg
    exp_pct = 100.0 * do / o2sol  # value used as-is, not divided by rho
    exp_aou = o2sol - do
    np.testing.assert_allclose(out["oxygen_saturation_pct"].values, exp_pct, rtol=1e-3)
    np.testing.assert_allclose(
        out["apparent_oxygen_utilization"].values, exp_aou, rtol=1e-3
    )


def test_derive_oxygen_saturation_per_volume_divides_by_density():
    """A per-volume (umol L-1) input is converted to per mass by ÷(rho/1000)."""
    import gsw

    do, t, sp, p = 250.0, 8.0, 35.0, 500.0  # µmol/L
    out = Q.derive_oxygen_saturation(_o2_ds(do, "umol L-1", t, sp, p))

    sa = gsw.SA_from_SP(sp, p, 0.0, 0.0)
    pt = gsw.pt0_from_t(sa, t, p)
    ct = gsw.CT_from_t(sa, t, p)
    o2sol = gsw.O2sol_SP_pt(sp, pt)
    o2_kg = do / (gsw.rho(sa, ct, p) / 1000.0)
    exp_pct = 100.0 * o2_kg / o2sol
    np.testing.assert_allclose(out["oxygen_saturation_pct"].values, exp_pct, rtol=1e-3)
    # The two bases must differ by the density factor (~2.5%), proving the branch.
    per_mass = Q.derive_oxygen_saturation(_o2_ds(do, "umol kg-1", t, sp, p))
    ratio = (
        per_mass["oxygen_saturation_pct"].values[0]
        / out["oxygen_saturation_pct"].values[0]
    )
    assert 1.02 < ratio < 1.04


def test_derive_oxygen_saturation_unknown_units_raises():
    from oceanarray.pipeline.units import UnitsError

    with pytest.raises(UnitsError, match="dissolved_oxygen units"):
        Q.derive_oxygen_saturation(_o2_ds(250.0, "mL/L"))
