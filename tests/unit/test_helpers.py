"""Unit tests for :mod:`oceanarray.pipeline.helpers`.

Pure stack/grid helper functions — QC masking, worst-flag merge, time
conversion, interval detection, nearest/linear resampling, and ADCP head/bin
views — tested against hand-computed known answers on synthetic datasets.

Milestone B of ``.claude/notes/2026-08-19-stage-coverage-plan.md``.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.pipeline import helpers as H


def _dt(seconds: list[float]) -> np.ndarray:
    """datetime64[ns] array from seconds past a fixed epoch."""
    base = np.datetime64("2026-01-01T00:00:00", "ns")
    return base + (np.asarray(seconds) * 1e9).astype("timedelta64[ns]")


def _ds(varname: str, values, times, qc=None) -> xr.Dataset:
    """Single-variable time-series dataset, optionally with a companion _qc var."""
    data = {varname: ("time", np.asarray(values, dtype=float))}
    if qc is not None:
        data[f"{varname}_qc"] = ("time", np.asarray(qc, dtype=np.int8))
    return xr.Dataset(data, coords={"time": times})


# ---------------------------------------------------------------------------
# _times_to_float / _detect_interval_s
# ---------------------------------------------------------------------------


def test_times_to_float_nanoseconds():
    t = np.array(["1970-01-01T00:00:00", "1970-01-01T00:00:01"], dtype="datetime64[ns]")
    np.testing.assert_array_equal(H._times_to_float(t), [0.0, 1e9])


def test_detect_interval_s_median():
    """Median spacing in seconds; irregular gaps take the median."""
    assert H._detect_interval_s(_dt([0.0, 60.0, 120.0, 180.0])) == 60.0
    # gaps 10, 10, 100 -> median 10
    assert H._detect_interval_s(_dt([0.0, 10.0, 20.0, 120.0])) == 10.0


def test_detect_interval_s_default_when_too_few():
    """Fewer than 2 samples falls back to 60 s."""
    assert H._detect_interval_s(_dt([5.0])) == 60.0


# ---------------------------------------------------------------------------
# _apply_qc_mask
# ---------------------------------------------------------------------------


def test_apply_qc_mask_masks_suspect_bad_missing():
    """Flags 3/4/9 → NaN; 0/1/2 kept; for non-pressure, 8 is masked too."""
    ds = _ds(
        "temperature",
        [1, 2, 3, 4, 5, 6],
        _dt([0, 1, 2, 3, 4, 5]),
        qc=[0, 1, 2, 3, 4, 9],
    )
    out = H._apply_qc_mask(ds["temperature"].values.astype(float), ds, "temperature")
    # kept: indices 0,1,2 (flags 0,1,2); masked: 3,4,5 (flags 3,4,9)
    assert np.array_equal(np.isfinite(out), [True, True, True, False, False, False])


def test_apply_qc_mask_pressure_keeps_interpolated_flag8():
    """Pressure keeps flag 8 (interpolated); other variables do not."""
    ds = _ds("pressure", [10, 20, 30], _dt([0, 1, 2]), qc=[1, 8, 4])
    out = H._apply_qc_mask(ds["pressure"].values.astype(float), ds, "pressure")
    assert np.array_equal(np.isfinite(out), [True, True, False])  # flag 8 kept
    # same flags on a non-pressure var: 8 is masked
    ds2 = _ds("salinity", [10, 20, 30], _dt([0, 1, 2]), qc=[1, 8, 4])
    out2 = H._apply_qc_mask(ds2["salinity"].values.astype(float), ds2, "salinity")
    assert np.array_equal(np.isfinite(out2), [True, False, False])


def test_apply_qc_mask_no_qc_var_returns_unchanged():
    ds = _ds("temperature", [1.0, 2.0], _dt([0, 1]))
    src = ds["temperature"].values.astype(float)
    out = H._apply_qc_mask(src, ds, "temperature")
    np.testing.assert_array_equal(out, src)


# ---------------------------------------------------------------------------
# _worst_flag  (documented ordering: 0 < 1 < 2 < 7 < 8 < 3 < 4 < 9)
# ---------------------------------------------------------------------------


def test_worst_flag_priority_ordering():
    """Worst-wins on the OceanSITES priority, not numeric value."""
    a = np.array([1, 2, 8, 4, 0, 7])
    b = np.array([2, 3, 3, 9, 1, 8])
    # expected worst per the ordering 0<1<2<7<8<3<4<9:
    #  (1,2)->2  (2,3)->3  (8,3)->3  (4,9)->9  (0,1)->1  (7,8)->8
    np.testing.assert_array_equal(H._worst_flag(a, b), [2, 3, 3, 9, 1, 8])


def test_worst_flag_bad_beats_interpolated():
    """Flag 4 (bad) outranks 8 (interpolated) even though 8 > 4 numerically."""
    np.testing.assert_array_equal(H._worst_flag(np.array([8]), np.array([4])), [4])


def test_worst_flag_nan_treated_as_missing():
    """NaN inputs are treated as flag 9 (missing), never silently good."""
    out = H._worst_flag(np.array([np.nan]), np.array([1]))
    assert out[0] == 9


# ---------------------------------------------------------------------------
# _nearest_subsample
# ---------------------------------------------------------------------------


def test_nearest_subsample_picks_within_window():
    ds = _ds("temperature", [10.0, 20.0, 30.0], _dt([0.0, 10.0, 20.0]))
    out = H._nearest_subsample(ds, _dt([10.0]), half_window_s=2.0)
    np.testing.assert_allclose(out["temperature"], [20.0])


def test_nearest_subsample_nan_outside_window():
    ds = _ds("temperature", [10.0, 20.0], _dt([0.0, 100.0]))
    out = H._nearest_subsample(ds, _dt([50.0]), half_window_s=5.0)
    assert np.isnan(out["temperature"][0])


def test_nearest_subsample_missing_var_is_nan():
    ds = _ds("temperature", [10.0, 20.0], _dt([0.0, 10.0]))
    out = H._nearest_subsample(ds, _dt([0.0]), half_window_s=5.0)
    assert np.isnan(out["salinity"][0])  # salinity absent -> NaN


# ---------------------------------------------------------------------------
# _linear_interp
# ---------------------------------------------------------------------------


def test_linear_interp_midpoint():
    ds = _ds("temperature", [10.0, 20.0], _dt([0.0, 10.0]))
    out = H._linear_interp(ds, _dt([5.0]))
    np.testing.assert_allclose(out["temperature"], [15.0])


def test_linear_interp_nan_outside_range():
    ds = _ds("temperature", [10.0, 20.0], _dt([10.0, 20.0]))
    out = H._linear_interp(ds, _dt([0.0, 30.0]))
    assert np.isnan(out["temperature"]).all()


def test_linear_interp_qc_takes_worst_bracketing_flag():
    """*_qc flag arrays are resampled conservatively, never linearly averaged.

    Each target inherits the *worst* flag of its two bracketing source samples on
    the OceanSITES priority, so a bad flag adjacent to a target is never laundered
    into a good one. Two invariants: (1) no interpolated intermediate flag (e.g.
    2.5) is ever produced; (2) a target between a bad and a good sample takes the
    bad flag *regardless of ordering* — the earlier searchsorted "next" pick
    dropped a leading bad flag, which this guards against.
    """
    # good-then-bad: midpoint takes the bad(4) flag; endpoints match exactly.
    ds = xr.Dataset(
        {"temperature_qc": ("time", np.array([1, 4], dtype=np.int8))},
        coords={"time": _dt([0.0, 10.0])},
    )
    out = H._linear_interp(ds, _dt([0.0, 5.0, 10.0]))
    np.testing.assert_array_equal(out["temperature_qc"], [1.0, 4.0, 4.0])

    # bad-then-good: the midpoint must NOT drop the leading bad(4) flag. This is
    # the case the old searchsorted-"next" behaviour got wrong (it returned 1).
    ds_rev = xr.Dataset(
        {"temperature_qc": ("time", np.array([4, 1], dtype=np.int8))},
        coords={"time": _dt([0.0, 10.0])},
    )
    out_rev = H._linear_interp(ds_rev, _dt([0.0, 5.0, 10.0]))
    np.testing.assert_array_equal(out_rev["temperature_qc"], [4.0, 4.0, 1.0])


# ---------------------------------------------------------------------------
# ADCP head / bin views
# ---------------------------------------------------------------------------


def _adcp_parent(n_bins: int = 2) -> xr.Dataset:
    """Minimal ADCP stage-3 dataset with head (1-D) and per-bin (2-D) vars."""
    times = _dt([0.0, 1.0])
    bindim = params.ADCP_BIN_DIM
    return xr.Dataset(
        {
            "temperature": ("time", np.array([4.0, 4.1])),  # head var
            "pressure": ("time", np.array([500.0, 500.0])),  # transducer head
            "east_velocity": (("time", bindim), np.array([[1.0, 0.0], [1.0, 0.0]])),
            "north_velocity": (("time", bindim), np.array([[0.0, 1.0], [0.0, 1.0]])),
            "bin_pressure": (
                ("time", bindim),
                np.array([[490.0, 480.0], [490.0, 480.0]]),
            ),
        },
        coords={"time": times, bindim: np.arange(n_bins)},
    )


def test_make_adcp_head_ds_keeps_head_vars_drops_bins():
    head = H._make_adcp_head_ds(_adcp_parent())
    assert "temperature" in head.data_vars
    assert "pressure" in head.data_vars
    assert "east_velocity" not in head.data_vars  # 2-D bin var excluded


def test_make_adcp_bin_ds_derives_speed_direction_and_pressure():
    """Bin 0: pressure←bin_pressure, speed=hypot, direction=atan2(E,N) oceanographic."""
    ds_bin = H._make_adcp_bin_ds(_adcp_parent(), bin_idx=0)
    # bin 0 has east=1, north=0 -> speed 1, direction 90° (eastward flow)
    np.testing.assert_allclose(ds_bin["pressure"].values, [490.0, 490.0])
    np.testing.assert_allclose(ds_bin["current_speed"].values, [1.0, 1.0], atol=1e-5)
    np.testing.assert_allclose(
        ds_bin["current_direction"].values, [90.0, 90.0], atol=1e-4
    )
    # head-only variable dropped from the bin view
    assert "temperature" not in ds_bin.data_vars


def test_make_adcp_bin_ds_direction_northward_is_zero():
    """Bin 1 has east=0, north=1 -> direction 0° (northward flow)."""
    ds_bin = H._make_adcp_bin_ds(_adcp_parent(), bin_idx=1)
    np.testing.assert_allclose(
        ds_bin["current_direction"].values, [0.0, 0.0], atol=1e-4
    )


# ---------------------------------------------------------------------------
# _best_nc / _safe_serial
# ---------------------------------------------------------------------------


def test_best_nc_prefers_stage3_then_stage2(tmp_path):
    proc = tmp_path
    (proc / "microcat").mkdir()
    base = proc / "microcat" / "M1_2941"
    # only stage2 present
    (proc / "microcat" / "M1_2941_stage2.nc").touch()
    assert H._best_nc(proc, "microcat", "M1", "2941").name.endswith("_stage2.nc")
    # stage3 present -> preferred
    (proc / "microcat" / "M1_2941_stage3.nc").touch()
    assert H._best_nc(proc, "microcat", "M1", "2941").name.endswith("_stage3.nc")
    _ = base  # documents the naming pattern under test


def test_best_nc_none_when_only_stage1(tmp_path):
    (tmp_path / "microcat").mkdir()
    assert H._best_nc(tmp_path, "microcat", "M1", "9999") is None


def test_safe_serial_returns_string():
    assert isinstance(H._safe_serial("16430, R01-024"), str)
