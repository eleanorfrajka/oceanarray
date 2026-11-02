"""Unit tests for :mod:`oceanarray.pipeline.pressure`.

Pure-numeric pressure interpolation helpers, tested against hand-computed known
answers on synthetic arrays (not instrument files) — per the fixture rule, the
mathematics gets known answers rather than a file round-trip. A bug in this
module would silently mis-place every instrument on the mooring, so the
assertions are on values, not smoke.

Milestone A of ``.claude/notes/2026-08-19-stage-coverage-plan.md``.
"""

from __future__ import annotations

from pathlib import Path

import gsw
import numpy as np
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.pipeline import pressure as P

#: Trimmed real dsG2 WH75 ADCP stage-3 file over a knock-down window (earth + firmware
#: bin mapping; tilt 4.6–45°).  Raw ``.000`` is not in the repo — see the file's attrs.
DSG2_KNOCKDOWN = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "adcp"
    / "dsG2_adcp_stage3_knockdown.nc"
)
DSG2_LAT = 65.56


def _dt(seconds: list[float]) -> np.ndarray:
    """Build a datetime64[ns] array from seconds past a fixed epoch."""
    base = np.datetime64("2026-01-01T00:00:00", "ns")
    return base + (np.asarray(seconds) * 1e9).astype("timedelta64[ns]")


def _source(hab: float, times: np.ndarray, pressures: np.ndarray, serial: str = "S1"):
    """Build a pressure-source dict as ``interp_pressure_for_hab`` expects."""
    ds = xr.Dataset(
        {"pressure": ("time", np.asarray(pressures, dtype=float))},
        coords={"time": times},
    )
    return {
        "ds": ds,
        "hab": hab,
        "pressure_var": "pressure",
        "instrument": "microcat",
        "serial": serial,
    }


# ---------------------------------------------------------------------------
# _times_to_float
# ---------------------------------------------------------------------------


def test_times_to_float_is_nanoseconds_since_epoch():
    """datetime64 converts to float64 nanoseconds; 1 s == 1e9."""
    t = np.array(["1970-01-01T00:00:00", "1970-01-01T00:00:01"], dtype="datetime64[ns]")
    out = P._times_to_float(t)
    assert out.dtype == np.float64
    np.testing.assert_array_equal(out, np.array([0.0, 1e9]))


# ---------------------------------------------------------------------------
# _is_burst_mode
# ---------------------------------------------------------------------------


def test_is_burst_mode_false_when_too_few_samples():
    """Fewer than 10 samples cannot be classified — returns False."""
    assert P._is_burst_mode(_dt(list(range(5)))) is False


def test_is_burst_mode_false_for_uniform_sampling():
    """Evenly spaced samples (p90 == p50) are not burst mode."""
    assert P._is_burst_mode(_dt([i * 60.0 for i in range(20)])) is False


def test_is_burst_mode_true_for_bimodal_gaps():
    """Many 1 s within-burst gaps and rare long gaps → burst mode."""
    # 4 bursts of 5 pings at 1 Hz, separated by 120 s waits.
    secs: list[float] = []
    t = 0.0
    for _burst in range(4):
        for _ping in range(5):
            secs.append(t)
            t += 1.0
        t += 120.0
    assert P._is_burst_mode(_dt(secs)) is True


def test_is_burst_mode_false_when_all_timestamps_equal():
    """All-equal timestamps give no positive Δt — returns False, no crash."""
    assert P._is_burst_mode(_dt([0.0] * 15)) is False


# ---------------------------------------------------------------------------
# interp_pressure
# ---------------------------------------------------------------------------


def test_interp_pressure_linear_midpoint():
    """Linear interpolation returns the exact midpoint value."""
    src_t = _dt([0.0, 2.0])
    out = P.interp_pressure(src_t, np.array([100.0, 102.0]), _dt([1.0]))
    np.testing.assert_allclose(out, [101.0])


def test_interp_pressure_edge_extrapolation_is_clamped():
    """Targets outside the source range clamp to the first/last value (no NaN)."""
    src_t = _dt([10.0, 20.0])
    out = P.interp_pressure(src_t, np.array([100.0, 110.0]), _dt([0.0, 30.0]))
    np.testing.assert_allclose(out, [100.0, 110.0])


def test_interp_pressure_ignores_nan_sources():
    """Non-finite source samples are dropped before interpolation."""
    src_t = _dt([0.0, 1.0, 2.0])
    out = P.interp_pressure(src_t, np.array([100.0, np.nan, 102.0]), _dt([1.0]))
    np.testing.assert_allclose(out, [101.0])


def test_interp_pressure_returns_nan_when_fewer_than_two_valid():
    """With <2 finite samples the whole target is NaN."""
    src_t = _dt([0.0, 1.0])
    out = P.interp_pressure(src_t, np.array([100.0, np.nan]), _dt([0.0, 1.0]))
    assert np.isnan(out).all()
    assert out.shape == (2,)


# ---------------------------------------------------------------------------
# interp_pressure_for_hab
# ---------------------------------------------------------------------------


def test_interp_for_hab_near_neighbour_applies_static_offset():
    """Within HAB_THRESHOLD: nearest source + (src_hab - target_hab) dbar offset.

    Source at hab 100 m; target 1 m lower (hab 99, deeper) → +1 dbar, matching
    ~1 dbar per metre of seawater.
    """
    times = _dt([0.0, 1.0])
    src = _source(100.0, times, [200.0, 200.0])
    p, method = P.interp_pressure_for_hab(99.0, [src], times, serial="TGT")
    np.testing.assert_allclose(p, [201.0, 201.0])
    assert "near-neighbour" in method


def test_interp_for_hab_bracketed_weighted_blend():
    """Outside threshold with sources above and below: linear blend by hab, no offset."""
    times = _dt([0.0, 1.0])  # >=2 points so interp_pressure does not NaN-fill
    below = _source(90.0, times, [200.0, 200.0], serial="LO")
    above = _source(110.0, times, [180.0, 180.0], serial="HI")
    # target hab 100 sits halfway → 0.5*200 + 0.5*180 = 190
    msgs: list[str] = []
    p, method = P.interp_pressure_for_hab(
        100.0, [below, above], times, serial="TGT", log_fn=msgs.append
    )
    np.testing.assert_allclose(p, [190.0, 190.0])
    assert "bracketed" in method
    assert msgs and "TGT" in msgs[0]


def test_interp_for_hab_extrapolated_out_of_range_warns_in_method():
    """Only-below source outside threshold: extrapolate with offset, flag out of range."""
    times = _dt([0.0, 1.0])
    below = _source(90.0, times, [200.0, 200.0], serial="LO")
    # target hab 100, Δ=10 > 2, no source above → offset = 90 - 100 = -10 → 190
    msgs: list[str] = []
    p, method = P.interp_pressure_for_hab(
        100.0, [below], times, serial="TGT", log_fn=msgs.append
    )
    np.testing.assert_allclose(p, [190.0, 190.0])
    assert "out of range" in method
    assert msgs and "WARNING" in msgs[0]


def test_interp_for_hab_log_fn_is_called():
    """The logging callback receives a message when provided."""
    times = _dt([0.0, 1.0])
    src = _source(100.0, times, [200.0, 200.0])
    msgs: list[str] = []
    P.interp_pressure_for_hab(100.0, [src], times, serial="TGT", log_fn=msgs.append)
    assert msgs and "TGT" in msgs[0]


# ---------------------------------------------------------------------------
# interpolate_pressure
# ---------------------------------------------------------------------------


def _target_ds(times: np.ndarray) -> xr.Dataset:
    return xr.Dataset(
        {"pressure": ("time", np.full(len(times), 500.0))},
        coords={"time": times},
    )


def test_interpolate_pressure_empty_target_returns_no_data():
    ds = xr.Dataset(coords={"time": _dt([])})
    out, method = P.interpolate_pressure(
        ds, {"hab": 100.0, "serial": "T", "qc_flags": {}}, [], _dt([]), False
    )
    assert method == "no data"


def test_interpolate_pressure_no_sources_returns_message():
    times = _dt([0.0, 1.0])
    ds = _target_ds(times)
    info = {"hab": 100.0, "serial": "T", "qc_flags": {}}
    out, method = P.interpolate_pressure(ds, info, [{"ds": None}], times, False)
    assert method == "no sources available"


def test_interpolate_pressure_sets_flag_8_and_attrs():
    """Interpolated pressure is written with qc flag 8 and CF pressure attrs."""
    times = _dt([0.0, 1.0])
    ds = _target_ds(times)
    src = _source(100.0, times, [200.0, 200.0])
    info = {"hab": 100.0, "serial": "T", "qc_flags": {"pressure": 0}}
    out, method = P.interpolate_pressure(ds, info, [src], times, False)
    assert (out["pressure_qc"].values == 8).all()
    assert out["pressure"].attrs["units"] == "dbar"
    assert out["pressure"].attrs["standard_name"] == "sea_water_pressure"
    # near-neighbour, same hab → pressure equals source
    np.testing.assert_allclose(out["pressure"].values, [200.0, 200.0])


def test_interpolate_pressure_bad_flag_preserves_original():
    """pressure_bad_flag preserves original pressure as pressure_orig and replaces it."""
    times = _dt([0.0, 1.0])
    ds = _target_ds(times)  # original pressure = 500.0
    src = _source(100.0, times, [200.0, 200.0])
    info = {"hab": 100.0, "serial": "T", "qc_flags": {"pressure": 4}}
    out, method = P.interpolate_pressure(ds, info, [src], times, True)
    assert "pressure_orig" in out.data_vars
    np.testing.assert_allclose(out["pressure_orig"].values, [500.0, 500.0])
    assert (out["pressure_orig_qc"].values == 4).all()
    # new pressure came from the source, not the original 500
    np.testing.assert_allclose(out["pressure"].values, [200.0, 200.0])


def test_interpolate_pressure_hab_segments_switch_at_breakpoint():
    """A hab_segments breakpoint applies the new HAB at and after the timestamp."""
    times = _dt([0.0, 1.0, 2.0, 3.0])
    ds = _target_ds(times)
    src = _source(100.0, times, [200.0, 200.0, 200.0, 200.0])
    bp = _dt([2.0])[0]  # breakpoint at t=2
    info = {
        "hab": 100.0,  # segment 0: same hab → offset 0 → 200
        "hab_segments": [(bp, 99.0)],  # segment 1: hab 99 → +1 dbar → 201
        "serial": "T",
        "qc_flags": {"pressure": 0},
    }
    out, method = P.interpolate_pressure(ds, info, [src], times, False)
    np.testing.assert_allclose(out["pressure"].values, [200.0, 200.0, 201.0, 201.0])
    assert "hab=100.0m" in method and "hab=99.0m" in method


def test_interpolate_pressure_empty_leading_segment_is_skipped():
    """A hab_segments breakpoint at the first timestamp leaves segment 0 empty."""
    times = _dt([5.0, 6.0, 7.0])
    ds = _target_ds(times)
    src = _source(100.0, times, [200.0, 200.0, 200.0])
    bp = times[0]  # breakpoint at the very first sample → segment 0 spans nothing
    info = {
        "hab": 100.0,
        "hab_segments": [(bp, 99.0)],
        "serial": "T",
        "qc_flags": {"pressure": 0},
    }
    out, method = P.interpolate_pressure(ds, info, [src], times, False)
    # all samples use the post-breakpoint hab (99 → +1 dbar)
    np.testing.assert_allclose(out["pressure"].values, [201.0, 201.0, 201.0])


# ---------------------------------------------------------------------------
# compute_adcp_bin_pressure
# ---------------------------------------------------------------------------


def _adcp_ds(orientation: str | None, n_bins: int = 3) -> xr.Dataset:
    times = _dt([0.0, 1.0])
    ds = xr.Dataset(
        {"pressure": ("time", np.array([500.0, 500.0]))},
        coords={
            "time": times,
            "range": (params.ADCP_BIN_DIM, np.array([10.0, 20.0, 30.0][:n_bins])),
        },
    )
    if orientation is not None:
        ds.attrs["orientation_yaml"] = orientation
    return ds


def test_adcp_bin_pressure_skipped_without_pressure_or_range():
    ds = xr.Dataset(coords={"time": _dt([0.0])})
    msgs: list[str] = []
    out = P.compute_adcp_bin_pressure(ds, lat=60.0, log_fn=msgs.append)
    assert "bin_pressure" not in out.data_vars
    assert any("skipped" in m for m in msgs)


def test_adcp_bin_pressure_down_increases_with_bin():
    """Downward-looking: deeper bins → higher pressure than the transducer."""
    out = P.compute_adcp_bin_pressure(_adcp_ds("down"), lat=60.0)
    bp = out["bin_pressure"].values  # (time, N_BINS)
    assert (bp[:, 0] > 500.0).all()
    assert (np.diff(bp, axis=1) > 0).all()  # monotonically deeper with range


def test_adcp_bin_pressure_up_decreases_with_bin():
    """Upward-looking: shallower bins → lower pressure than the transducer."""
    out = P.compute_adcp_bin_pressure(_adcp_ds("up"), lat=60.0)
    bp = out["bin_pressure"].values
    assert (bp[:, 0] < 500.0).all()
    assert (np.diff(bp, axis=1) < 0).all()


def test_adcp_bin_pressure_unknown_orientation_assumes_down_and_warns():
    """Missing orientation assumes down-looking and emits a WARNING via log_fn."""
    msgs: list[str] = []
    out = P.compute_adcp_bin_pressure(_adcp_ds(None), lat=60.0, log_fn=msgs.append)
    bp = out["bin_pressure"].values
    assert (np.diff(bp, axis=1) > 0).all()  # behaves as down
    assert any("WARNING" in m and "orientation" in m for m in msgs)


def test_adcp_bin_pressure_records_formula_in_comment():
    out = P.compute_adcp_bin_pressure(_adcp_ds("down"), lat=60.0)
    comment = out["bin_pressure"].attrs["comment"]
    assert "gsw.p_from_z" in comment and "down-looking" in comment


def _nominal_bin_pressure(ds: xr.Dataset, lat: float) -> np.ndarray:
    """Untilted bin pressure: transducer + gsw.p_from_z(-range) (down-looking sign)."""
    p = ds["pressure"].values.astype(float)
    rng = ds["range"].values.astype(float)
    return p[:, None] + gsw.p_from_z(-rng, lat)[None, :]


def test_adcp_bin_pressure_guard_skips_firmware_bin_mapping():
    """Real dsG2 (earth + firmware bin mapping): cos(tilt) must NOT be applied.

    The firmware recorded earth coordinates with bin mapping on, so it has already
    tilt-placed the bins; applying cos(tilt) again would double-correct.  bin_pressure must
    equal the untilted value and the provenance must say so.
    """
    ds = xr.open_dataset(DSG2_KNOCKDOWN, decode_timedelta=False).load()
    ds.close()
    assert ds.attrs["firmware_coordinate_system"] == "earth"
    assert ds.attrs["firmware_bin_mapping"] == "yes"
    msgs: list[str] = []
    out = P.compute_adcp_bin_pressure(
        ds.drop_vars("bin_pressure"), lat=DSG2_LAT, log_fn=msgs.append
    )
    assert out["bin_pressure"].attrs["tilt_correction"] == "none (firmware bin mapping)"
    np.testing.assert_allclose(
        out["bin_pressure"].values, _nominal_bin_pressure(ds, DSG2_LAT), atol=0.5
    )
    assert any("bin mapping" in m for m in msgs)


def test_adcp_bin_pressure_apply_on_beam_matches_cos_tilt():
    """BEAM input: bin ranges are tilt-reduced by cos(θ), verified on dsG2's real tilt.

    No current UHH deployment records beam coordinates (all are earth + bin mapping), so this
    path is exercised only by this test's forced-beam metadata — but it is the correct
    treatment for beam input and future deployments.  Uses the fixture's real pitch/roll and
    range vector rather than a synthetic tilt.
    """
    ds = xr.open_dataset(DSG2_KNOCKDOWN, decode_timedelta=False).load()
    ds.close()
    ds = ds.drop_vars("bin_pressure")
    ds.attrs["firmware_coordinate_system"] = "beam"
    ds.attrs["firmware_bin_mapping"] = "no"

    out = P.compute_adcp_bin_pressure(ds, lat=DSG2_LAT)
    assert out["bin_pressure"].attrs["tilt_correction"] == "applied"

    # Reconstruct θ via the documented definition and assert range·cos(θ) through gsw.
    roll_r = np.deg2rad(ds["roll"].values.astype(float))
    p_true = np.arctan(
        np.tan(np.deg2rad(ds["pitch"].values.astype(float))) * np.cos(roll_r)
    )
    tilt = np.arccos(np.clip(np.cos(p_true) * np.cos(roll_r), -1.0, 1.0))
    rng = ds["range"].values.astype(float)
    p = ds["pressure"].values.astype(float)
    expected = p[:, None] + gsw.p_from_z(
        -rng[None, :] * np.cos(tilt)[:, None], DSG2_LAT
    )
    np.testing.assert_allclose(out["bin_pressure"].values, expected, atol=0.5)

    # The far bin at the biggest knock-down is many dbar shallower than nominal.
    shallower = _nominal_bin_pressure(ds, DSG2_LAT) - out["bin_pressure"].values
    assert shallower.max() > 50.0


def test_adcp_bin_pressure_no_pitch_roll_skips_with_warning():
    """Known beam coords but no pitch/roll: fall back to nominal, loudly."""
    ds = _adcp_ds("down")  # pressure + range, no pitch/roll
    ds.attrs["firmware_coordinate_system"] = (
        "beam"  # known non-mapped; reaches the tilt step
    )
    msgs: list[str] = []
    out = P.compute_adcp_bin_pressure(ds, lat=60.0, log_fn=msgs.append)
    assert (
        out["bin_pressure"].attrs["tilt_correction"] == "none (pitch/roll unavailable)"
    )
    assert out["bin_pressure"].attrs["tilt_source"] == "n/a"
    assert any("pitch/roll" in m for m in msgs)


def test_adcp_bin_pressure_unknown_firmware_fails_closed():
    """Unknown firmware config (no attrs) must NOT apply cos(tilt), even with pitch/roll.

    Absence of the firmware attrs (a non-RDI ADCP, or a file predating the Stage-1 surfacing)
    must not be read as beam input — otherwise a firmware-mapped file whose attrs were lost
    would be double-corrected.  The correction fires only on positive non-mapped evidence.
    """
    ds = _adcp_ds("down")  # no firmware_* attrs
    ds["pitch"] = ("time", np.array([20.0, 20.0]))
    ds["roll"] = ("time", np.array([5.0, 5.0]))
    msgs: list[str] = []
    out = P.compute_adcp_bin_pressure(ds, lat=60.0, log_fn=msgs.append)
    assert (
        out["bin_pressure"].attrs["tilt_correction"] == "none (firmware config unknown)"
    )
    # bin_pressure is the untilted nominal despite the 20° pitch
    np.testing.assert_allclose(
        out["bin_pressure"].values, _nominal_bin_pressure(ds, 60.0), atol=1e-4
    )
    assert any("unknown" in m for m in msgs)


def test_adcp_bin_pressure_nan_pitch_roll_warns_and_does_not_claim_clean():
    """Beam input with gappy pitch/roll: NaN bins are flagged, not silently 'applied' clean."""
    ds = _adcp_ds("down")
    ds.attrs["firmware_coordinate_system"] = "beam"
    ds["pitch"] = ("time", np.array([20.0, np.nan]))  # one gap
    ds["roll"] = ("time", np.array([5.0, 5.0]))
    msgs: list[str] = []
    out = P.compute_adcp_bin_pressure(ds, lat=60.0, log_fn=msgs.append)
    bp = out["bin_pressure"].values
    assert np.isfinite(bp[0]).all()  # good timestep
    assert np.isnan(bp[1]).all()  # gap timestep → NaN depth, not a wrong number
    assert any("missing" in m and "NaN" in m for m in msgs)
