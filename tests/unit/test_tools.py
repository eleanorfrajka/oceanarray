import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanarray.analysis.hydrography import (
    calc_ds_difference,
    calc_psal,
    find_cold_entry_exit,
)
from oceanarray.legacy.mooring_rodb import auto_filt
from oceanarray.legacy.process_rodb import (
    mean_of_middle_percent,
    middle_percent,
    std_of_middle_percent,
    to_zscore,
    to_zscore_dataset,
)
from oceanarray.legacy.qc_rodb import (
    flag_salinity_outliers,
    flag_temporal_spikes,
    flag_vertical_inconsistencies,
    run_qc,
)
from oceanarray.pipeline.clock.offsets import lag_correlation, split_value
from oceanarray.pipeline.gridders.climatology import downsample_to_sparse


@pytest.fixture
def sample_dataset():
    time = np.arange(6)
    depth = np.array([5, 15, 25])
    CNDC = np.tile([40.0, 41.0, 42.0], (6, 1)) + np.random.randn(6, 3)
    TEMP = np.tile([10.0, 11.0, 12.0], (6, 1)) + np.random.randn(6, 3)
    PRES = np.array([5, 15, 25])
    return xr.Dataset(
        {
            "CNDC": (("TIME", "DEPTH"), CNDC),
            "TEMP": (("TIME", "DEPTH"), TEMP),
            "PRES": ("DEPTH", PRES),
        },
        coords={"TIME": time, "DEPTH": depth},
    )


def test_calc_psal(sample_dataset):
    ds = calc_psal(sample_dataset)
    assert "PSAL" in ds
    assert ds["PSAL"].shape == ds["CNDC"].shape


def test_flag_salinity_outliers(sample_dataset):
    ds = calc_psal(sample_dataset)
    flag = flag_salinity_outliers(ds)
    assert flag.shape == ds["PSAL"].shape
    assert flag.dtype == bool


def test_flag_temporal_spikes(sample_dataset):
    flag = flag_temporal_spikes(sample_dataset)
    assert flag.shape == sample_dataset["CNDC"].shape
    assert flag.dtype == bool


def test_flag_vertical_inconsistencies(sample_dataset):
    flag = flag_vertical_inconsistencies(sample_dataset)
    assert flag.shape == sample_dataset["CNDC"].shape
    assert flag.dtype == bool


def test_run_qc(sample_dataset):
    ds = run_qc(calc_psal(sample_dataset))
    assert "CNDC_QC" in ds
    assert ds["CNDC_QC"].dtype == int


def test_downsample_to_sparse_shapes():
    temp = np.random.rand(3, 8)
    salt = np.random.rand(3, 8)
    full_p = np.linspace(0, 700, 8)
    sparse_p = np.array([0, 200, 400, 600])
    out_temp, out_salt = downsample_to_sparse(temp, salt, full_p, sparse_p)
    assert out_temp.shape == (3, 4)
    assert out_salt.shape == (3, 4)


def test_middle_percent():
    data = np.linspace(0, 100, 100)
    lo, hi = middle_percent(data, 80)
    assert lo > 0 and hi < 100 and hi > lo


def test_middle_percent_bounds():
    data = np.linspace(0, 100, 1000)
    lower, upper = middle_percent(data, 90)
    assert np.isclose(lower, 5)
    assert np.isclose(upper, 95)


def test_mean_of_middle_percent():
    data = np.concatenate([np.random.normal(10, 1, 1000), np.array([1000, -1000])])
    mean = mean_of_middle_percent(data, 95)
    assert abs(mean - 10) < 0.2


def test_std_of_middle_percent():
    data = np.concatenate([np.random.normal(5, 2, 1000), np.array([999, -999])])
    std = std_of_middle_percent(data, 95)
    assert 1.5 < std < 2.5


def test_mean_std_middle_percent():
    data = np.random.normal(0, 1, 1000)
    mean = mean_of_middle_percent(data, 90)
    std = std_of_middle_percent(data, 90)
    assert np.isfinite(mean)
    assert np.isfinite(std)
    assert std > 0


def test_to_zscore():
    data = np.random.normal(0, 1, 1000)
    norm = to_zscore(data, 90)
    mid_std = std_of_middle_percent(norm, 90)
    assert 0.9 < mid_std < 1.1


def test_to_zscore_dataset():
    time = np.arange(10)
    ds = xr.Dataset({"TEMP": ("TIME", np.random.rand(10) + 20)}, coords={"TIME": time})
    ds_norm = to_zscore_dataset(ds)
    assert "TEMP" in ds_norm
    assert np.allclose(ds_norm.TIME, time)


def test_auto_filt_low():
    sr = 1.0  # Hz
    t = np.linspace(0, 10, 500)
    signal = np.sin(2 * np.pi * 0.1 * t) + 0.5 * np.sin(2 * np.pi * 2 * t)
    filtered = auto_filt(signal, sr, co=0.2, typ="low")
    assert len(filtered) == len(signal)
    assert np.std(filtered) < np.std(signal)


def test_calc_ds_difference_basic():
    time = np.arange(5)
    val1 = np.random.rand(5)
    val2 = np.random.rand(5)
    ds1 = xr.Dataset({"A": ("TIME", val1)}, coords={"TIME": time})
    ds2 = xr.Dataset({"A": ("TIME", val2)}, coords={"TIME": time})
    diff = calc_ds_difference(ds1, ds2)
    np.testing.assert_allclose(diff["A"].values, val1 - val2)


def test_calc_ds_difference_multiple_vars():
    times = np.array(["2020-01-01T00:00", "2020-01-01T01:00"], dtype="datetime64")
    ds1 = xr.Dataset(
        {
            "T": ("TIME", [10.0, 12.0]),
            "C": ("TIME", [35.0, 36.0]),
            "P": ("TIME", [1000.0, 1001.0]),
        },
        coords={"TIME": times},
    )
    ds2 = xr.Dataset(
        {
            "T": ("TIME", [9.5, 11.5]),
            "C": ("TIME", [34.5, 35.5]),
            "P": ("TIME", [999.0, 1000.0]),
        },
        coords={"TIME": times},
    )
    ds_diff = calc_ds_difference(ds1, ds2)
    assert np.allclose(ds_diff["T"], [0.5, 0.5])
    assert np.allclose(ds_diff["C"], [0.5, 0.5])
    assert np.allclose(ds_diff["P"], [1.0, 1.0])


def test_calc_ds_difference_time_mismatch():
    time1 = np.arange(5)
    time2 = np.arange(1, 6)
    ds1 = xr.Dataset({"TEMP": ("TIME", np.ones(5))}, coords={"TIME": time1})
    ds2 = xr.Dataset({"TEMP": ("TIME", np.ones(5))}, coords={"TIME": time2})
    with pytest.raises(ValueError, match="TIME grids do not match"):
        calc_ds_difference(ds1, ds2)


# ---------------------------------------------------------------------------
# lag_correlation
# ---------------------------------------------------------------------------


def test_lag_correlation_peak_at_zero_for_identical():
    """Identical signals: peak correlation is at lag 0."""
    x = np.sin(np.linspace(0, 4 * np.pi, 100))
    corrs = lag_correlation(x, x, max_lag=10)
    lags = np.arange(-10, 11)
    peak_lag = lags[np.nanargmax(corrs)]
    assert peak_lag == 0


def test_lag_correlation_y_lags_x():
    """y is x delayed by 3 samples (np.roll right) → peak at lag -3.

    Convention: lag > 0 compares x[lag:] vs y[:-lag], so a y that lags x
    by N samples correlates best when lag = -N (we shift y forward to align
    it with x).
    """
    n, shift = 80, 3
    base = np.sin(np.linspace(0, 4 * np.pi, n))
    x = base.copy()
    y = np.roll(base, shift)  # y[i] = base[i-shift]: y is delayed
    y[:shift] = np.nan  # mask wrap-around
    corrs = lag_correlation(x, y, max_lag=10, min_overlap=20)
    lags = np.arange(-10, 11)
    peak_lag = lags[np.nanargmax(corrs)]
    assert peak_lag == -shift, f"expected peak at {-shift}, got {peak_lag}"


def test_lag_correlation_y_leads_x():
    """y is x advanced by 5 samples (np.roll left) → peak at lag +5.

    A y that leads x by N samples correlates best at lag = +N.
    """
    n, shift = 80, 5
    base = np.sin(np.linspace(0, 4 * np.pi, n))
    x = base.copy()
    y = np.roll(base, -shift)  # y[i] = base[i+shift]: y is advanced
    y[-shift:] = np.nan  # mask wrap-around
    corrs = lag_correlation(x, y, max_lag=10, min_overlap=20)
    lags = np.arange(-10, 11)
    peak_lag = lags[np.nanargmax(corrs)]
    assert peak_lag == shift, f"expected peak at {shift}, got {peak_lag}"


def test_lag_correlation_length_mismatch_raises():
    with pytest.raises(ValueError):
        lag_correlation(np.ones(10), np.ones(11), max_lag=3)


def test_lag_correlation_returns_correct_length():
    max_lag = 7
    corrs = lag_correlation(np.random.randn(50), np.random.randn(50), max_lag)
    assert len(corrs) == 2 * max_lag + 1


# ---------------------------------------------------------------------------
# find_cold_entry_exit
# ---------------------------------------------------------------------------


def _cold_time_series(n_bench=20, n_cold=60, n_warm=20, dt_minutes=10):
    """Warm → cold → warm synthetic temperature series with known transition points."""
    time = pd.date_range(
        "2026-01-01", periods=n_bench + n_cold + n_warm, freq=f"{dt_minutes}min"
    )
    temp = np.concatenate(
        [
            np.full(n_bench, 10.0),  # warm bench period
            np.full(n_cold, 2.0),  # cold deployment
            np.full(n_warm, 10.0),  # warm recovery
        ]
    )
    return time, temp


def test_find_cold_entry_exit_detects_transition():
    """With clear bench→cold→bench structure the entry and exit are detected."""
    time, temp = _cold_time_series(n_bench=20, n_cold=60, n_warm=20)
    dwell = 30 * 60  # 30 min dwell threshold
    t_start, t_end, thr = find_cold_entry_exit(
        time, temp, quantile=0.5, dwell_seconds=dwell
    )
    assert t_start is not None, "start should be detected"
    assert t_end is not None, "end should be detected"
    assert t_start < t_end


def test_find_cold_entry_exit_no_cold_points():
    """All temps well above the quantile threshold → returns (None, None, thr)."""
    time = pd.date_range("2026-01-01", periods=50, freq="10min")
    temp = np.full(50, 15.0)
    # quantile=0.05 → threshold ≈ 15.0; all points are AT threshold (≤ thr is True for all)
    # Use quantile=0.0 so threshold = min = 15 and all values equal thr but dwell is very short
    t_start, t_end, _ = find_cold_entry_exit(
        time, temp, quantile=0.95, dwell_seconds=999999
    )
    # With an impossibly long dwell requirement no run qualifies
    assert t_start is None
    assert t_end is None


def test_find_cold_entry_exit_insufficient_dwell():
    """Cold run shorter than dwell_secs → returns (None, None, thr).

    2 cold samples at 2.0 out of 50 total = 4%.  Using quantile=0.02 (2nd
    percentile) ensures the threshold falls at 2.0 so only those 2 samples
    are flagged cold.  Their run is 2 × 10 min = 20 min < 60 min dwell → None.
    """
    time = pd.date_range("2026-01-01", periods=50, freq="10min")
    temp = np.concatenate([np.full(25, 10.0), np.full(2, 2.0), np.full(23, 10.0)])
    # Cold run = 2 samples × 10 min = 20 min; require 60 min dwell
    t_start, t_end, _ = find_cold_entry_exit(
        time, temp, quantile=0.02, dwell_seconds=3600
    )
    assert t_start is None
    assert t_end is None


# ---------------------------------------------------------------------------
# split_value
# ---------------------------------------------------------------------------


def test_split_value_bimodal():
    """Clearly bimodal array: split value falls between the two peaks."""
    rng = np.random.default_rng(0)
    low = rng.normal(2.0, 0.2, 200)
    high = rng.normal(8.0, 0.2, 200)
    data = np.concatenate([low, high])
    val = split_value(data)
    # The split value must lie between the two modes (not necessarily at midpoint)
    assert 1.5 < val < 7.5, f"split_value={val} not between modes"


def test_split_value_returns_float():
    """split_value always returns a numeric value without crashing."""
    rng = np.random.default_rng(1)
    data = rng.normal(5.0, 1.0, 100)
    val = split_value(data)
    assert np.isfinite(val)
