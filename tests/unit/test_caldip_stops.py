"""Reducing one dip's bottle stops to an offset or slope (:mod:`oceanarray.pipeline.calibration.stops`).

All on caldip's real cast ``castM4_caldip.nc`` (msm142, 3 April 2026; stops at about 2029,
1522, 909, 355 and 52 dbar), with edited copies where a case needs a stop removed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from oceanarray.pipeline import calibration as cal

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "caldip" / "castM4_caldip.nc"
)


@pytest.fixture(scope="module")
def cast() -> xr.Dataset:
    """The real cast, read and checked."""
    return cal.read_caldip_cast(FIXTURE)


def _record(cast: xr.Dataset, serial: str) -> xr.Dataset:
    rec = cal.instrument_record(cast, serial)
    assert rec is not None
    return rec.copy(deep=True)


def _at(rec: xr.Dataset, pressure: float) -> int:
    """Index of the stop nearest *pressure*."""
    return int(np.argmin(np.abs(rec["ctd_press"].values - pressure)))


def test_an31_bottle_example():
    """Sea-Bird AN31's own bottle example gives its published slope, 1.000138."""
    slope, sigma, _ = cal.through_origin_slope(
        np.array([4.63421, 3.25349, 3.16777]), np.array([4.63481, 3.25398, 3.16822])
    )
    assert round(slope, 6) == 1.000138
    assert np.isfinite(sigma)
    one, sigma_one, _ = cal.through_origin_slope(np.array([32.0]), np.array([32.016]))
    assert one == pytest.approx(1.0005) and np.isnan(sigma_one)


def test_flagged_stops_are_usable(cast):
    """caldip 'flagged' means reads off by more than its threshold — usable for a correction.

    TR1050 13874 reads ~0.008 °C warm at every stop, so caldip flags all five; excluding
    them would leave exactly the instrument that needs correcting uncorrected.
    """
    rec = _record(cast, "13874")
    assert set(rec["temp_flag"].values) == {cal.CALDIP_FLAG_FLAGGED}
    assert cal.usable_stops(rec, "temperature").all()
    # no conductivity cell: no_data everywhere, nothing usable
    assert not cal.usable_stops(rec, "conductivity").any()


def test_temperature_standard_is_the_deepest_stop(cast):
    """standard temperature: the difference at the deepest usable stop."""
    rec = _record(cast, "13840")
    red = cal.reduce_dip(rec, "temperature")
    i = _at(rec, 2029)
    assert red.kind == "offset" and red.n == 1
    assert red.value == pytest.approx(float(rec["temp_diff"].values[i]))
    assert red.stop_pressures == (pytest.approx(float(rec["ctd_press"].values[i])),)


def test_conductivity_stops_deep_and_stable(cast):
    """Below 500 dbar (two qualify), and 909 dbar dropped: its cond_std is > 3x the smallest."""
    rec = _record(cast, "13840")
    stops, rule = cal.conductivity_stops(rec)
    used = sorted(round(p) for p in rec["ctd_press"].values[stops])
    assert used == [1522, 2029]
    assert "500 dbar" in rule and "cond_std" in rule


def test_conductivity_stops_fall_back_to_200_dbar(cast):
    """With fewer than two usable stops below 500 dbar, stops below 200 dbar count instead."""
    rec = _record(cast, "13840")
    for p in (2029, 1522):
        rec["cond_flag"].values[_at(rec, p)] = 2  # no_data
    stops, rule = cal.conductivity_stops(rec)
    assert "deeper than 200 dbar" in rule
    assert sorted(round(p) for p in rec["ctd_press"].values[stops]) == [355, 909]


def test_conductivity_never_uses_a_stop_above_200_dbar(cast):
    """A cast whose only usable conductivity stop is at 52 dbar gives no slope."""
    rec = _record(cast, "13840")
    flags = rec["cond_flag"].values
    flags[:] = 2
    flags[_at(rec, 52)] = 1
    red = cal.reduce_dip(rec, "conductivity")
    assert not red.available
    assert red.reason == "no usable stop below 200 dbar"


def test_conductivity_slope_is_an31_weighted(cast):
    """The slope through the origin over the chosen stops, weighted N / cond_std²."""
    rec = _record(cast, "13840")
    stops, _ = cal.conductivity_stops(rec)
    a = rec["inst_cond"].values[stops]
    b = rec["ctd_cond"].values[stops]
    w = rec["N"].values[stops] / rec["cond_std"].values[stops] ** 2
    red = cal.reduce_dip(rec, "conductivity")
    assert red.kind == "slope" and red.n == 2
    assert red.value == pytest.approx(np.sum(w * a * b) / np.sum(w * a * a))
    assert (
        1.0004 < red.value < 1.0007
    )  # the instrument reads low by ~0.017 mS/cm at ~32
    assert np.isfinite(red.sigma) and "weighted" in red.rule
    assert red.extra["conductivity_range"][0] <= red.extra["conductivity_range"][1]


def test_conductivity_offset_alternative_uses_the_same_stops(cast):
    """slope=False gives the mean difference over the same deep, stable stops."""
    rec = _record(cast, "13840")
    stops, _ = cal.conductivity_stops(rec)
    red = cal.reduce_dip(rec, "conductivity", slope=False)
    assert red.kind == "offset"
    assert red.value == pytest.approx(float(np.mean(rec["cond_diff"].values[stops])))


def test_pressure_standard_near_the_deployment_pressure(cast):
    """Stops within 200 dbar are averaged; with none, the nearest is used."""
    rec = _record(cast, "13840")
    red = cal.reduce_dip(rec, "pressure", deployment_pressure=1500.0)
    assert red.n == 1
    assert red.value == pytest.approx(float(rec["press_diff"].values[_at(rec, 1522)]))
    far = cal.reduce_dip(rec, "pressure", deployment_pressure=1200.0)
    assert far.n == 1 and "nearest" in far.rule
    assert far.value == pytest.approx(float(rec["press_diff"].values[_at(rec, 909)]))


def test_rapid_depth_average(cast):
    """rapid T: below the deepest stop the difference is held; between stops interpolated."""
    rec = _record(cast, "13840")
    deep = float(rec["ctd_press"].values[_at(rec, 2029)])
    held = cal.rapid_depth_average(rec, "temperature", interval=(deep, deep + 100))
    assert held.value == pytest.approx(float(rec["temp_diff"].values[_at(rec, 2029)]))
    red = cal.reduce_dip(rec, "temperature", scheme="rapid")
    lo, hi = sorted(rec["temp_diff"].values[[_at(rec, 1522), _at(rec, 2029)]])
    assert lo <= red.value <= hi
    none = cal.rapid_depth_average(rec, "temperature", interval=(4000, 5000))
    assert not none.available and "do not reach" in none.reason


def test_rapid_pressure_at_depth(cast):
    """rapid P: at a stop it is that stop's offset; far below the cast, a fit in pressure."""
    rec = _record(cast, "13840")
    p1522 = float(rec["ctd_press"].values[_at(rec, 1522)])
    at = cal.reduce_dip(rec, "pressure", scheme="rapid", deployment_pressure=p1522)
    assert at.value == pytest.approx(float(rec["press_diff"].values[_at(rec, 1522)]))
    deep = cal.rapid_pressure_at_depth(rec, 3000.0)
    assert deep.available and "extrapolated" in deep.rule
    shallow = cal.rapid_pressure_at_depth(rec, 10.0)
    assert not shallow.available


def test_reduce_dip_refuses_unknowns(cast):
    rec = _record(cast, "13840")
    with pytest.raises(ValueError, match="variable"):
        cal.reduce_dip(rec, "salinity")
    with pytest.raises(ValueError, match="scheme"):
        cal.reduce_dip(rec, "temperature", scheme="kanzow")
    with pytest.raises(ValueError, match="deployment pressure"):
        cal.reduce_dip(rec, "pressure")
