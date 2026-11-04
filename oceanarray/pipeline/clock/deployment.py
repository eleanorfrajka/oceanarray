"""When each instrument entered and left the water: find the deployment window from temperature."""

import logging

import numpy as np
import pandas as pd

# Initialize logging
_log = logging.getLogger(__name__)


def _first_sustained(mask, min_len):
    for s, e in _runs_of_true(mask):
        if (e - s + 1) >= min_len:
            return s, e
    return None


def _last_sustained(mask, min_len):
    runs = [(s, e) for (s, e) in _runs_of_true(mask) if (e - s + 1) >= min_len]
    return runs[-1] if runs else None


def _rolling_median(y: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return np.asarray(y, float)
    return pd.Series(y).rolling(win, center=True, min_periods=1).median().to_numpy()


def _sampling_seconds(time):
    t = pd.to_datetime(time).to_numpy()
    if t.size < 2:
        return np.nan
    d = np.diff(t).astype("timedelta64[s]").astype(float)
    d = d[np.isfinite(d) & (d > 0)]
    return float(np.nanmedian(d)) if d.size else np.nan


def _runs_of_true(mask: np.ndarray) -> list[tuple[int, int]]:
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    gaps = np.diff(idx) > 1
    starts = np.r_[idx[0], idx[1:][gaps]]
    ends = np.r_[idx[:-1][gaps], idx[-1]]
    return [(int(s), int(e)) for s, e in zip(starts, ends, strict=True)]


def _best_step_change(y: np.ndarray, min_seg: int = 1) -> tuple[int, float, float]:
    """One change-point (piecewise constant) via SSE minimization.
    Returns (k, mu_left, mu_right) with 0 < k < n (split after k-1).
    Enforces min_seg samples on each side.
    """
    y = np.asarray(y, float)
    n = y.size
    if n < 2 * min_seg + 1:
        # too short; no split
        mu = float(np.nanmean(y))
        return max(min_seg, n // 2), mu, mu

    # cumulative sums
    c1 = np.nancumsum(np.where(np.isfinite(y), y, 0.0))
    c2 = np.nancumsum(np.where(np.isfinite(y), y * y, 0.0))
    cnt = np.nancumsum(np.isfinite(y).astype(float))

    best_k, best_sse = None, np.inf
    best_mu1, best_mu2 = np.nan, np.nan

    for k in range(min_seg, n - min_seg + 1):
        n1 = cnt[k - 1]
        n2 = cnt[-1] - cnt[k - 1]
        if n1 < min_seg or n2 < min_seg:
            continue
        s1 = c1[k - 1]
        s2 = c1[-1] - c1[k - 1]
        q1 = c2[k - 1]
        q2 = c2[-1] - c2[k - 1]
        mu1 = s1 / n1
        mu2 = s2 / n2
        sse = (q1 - n1 * mu1 * mu1) + (q2 - n2 * mu2 * mu2)
        if sse < best_sse:
            best_sse = sse
            best_k = k
            best_mu1 = float(mu1)
            best_mu2 = float(mu2)

    if best_k is None:
        mu = float(np.nanmean(y))
        return max(min_seg, n // 2), mu, mu
    return best_k, best_mu1, best_mu2


# ---------- helpers you can customise ----------
def likely_at_bottom_window(
    time: np.ndarray,
    strategy: str = "fixed_hours",
    *,
    bottom_margin_hours: float = 24.0,
    bottom_inner_fraction: float = 0.25,
    deployment_time: np.datetime64 | None = None,
    recovery_time: np.datetime64 | None = None,
    deployment_margin_hours: float = 2.0,
) -> tuple[np.datetime64, np.datetime64]:
    """Return (bot_start, bot_end): the window where the instrument is almost surely at depth.

    Strategies:

    - ``'fixed_hours'``: [tmin + margin, tmax - margin]
    - ``'percent_span'``: [tmin + f, tmax - f] using the inner fraction (e.g. 25 %)
    - ``'deployment_bounds'``: [deploy + 2 h, recover - 2 h]
    """
    t = pd.to_datetime(time)
    tmin, tmax = t[0], t[-1]  # <-- was .iloc[0], .iloc[-1]
    print(f"My start is {tmin} and end is {tmax}")

    if strategy == "fixed_hours":
        start = tmin + pd.Timedelta(hours=bottom_margin_hours)
        end = tmax - pd.Timedelta(hours=bottom_margin_hours)
    elif (
        strategy == "deployment_bounds"
        and (deployment_time is not None)
        and (recovery_time is not None)
    ):
        start = pd.to_datetime(deployment_time) + pd.Timedelta(
            hours=deployment_margin_hours
        )
        end = pd.to_datetime(recovery_time) - pd.Timedelta(
            hours=deployment_margin_hours
        )
    else:  # 'percent_span' default
        span = tmax - tmin
        pad = pd.to_timedelta(bottom_inner_fraction, unit="D") * (
            span / pd.Timedelta(days=1)
        )
        start = tmin + pad
        end = tmax - pad

    # clip to available range
    start = max(start, tmin)
    end = min(end, tmax)
    if end <= start:
        # fallback: middle third
        start = tmin + (tmax - tmin) * 0.33
        end = tmin + (tmax - tmin) * 0.66

    return np.datetime64(start.to_datetime64()), np.datetime64(end.to_datetime64())


def compute_threshold_sigma_band(
    time,
    temp,
    sea_mean,
    sea_std,
    *,
    band_sigma=3.0,
    min_band_halfwidth=0.10,  # °C
    warm_side="auto",  # "high", "low", or "auto"
    hours=24.0,
    smooth_window=9,
    warm_percentile=90,
):
    """Decide a single threshold using sigma-band logic.

    - Band half-width = max(band_sigma*sea_std, min_band_halfwidth).
    - If warm_side == "high": threshold = sea_mean + half_width  (deep is below threshold)
      If warm_side == "low" : threshold = sea_mean - half_width  (deep is above threshold)
      If warm_side == "auto": infer 'high' vs 'low' from early/late warm percentiles.

    Returns
    -------
    threshold : float
    deep_is_below : bool
        True if 'deep' should be considered <= threshold, False if >= threshold.

    """
    halfw = max(float(band_sigma) * float(sea_std), float(min_band_halfwidth))

    # decide warm side
    side = warm_side
    if side == "auto":
        t = pd.to_datetime(time)
        y = np.asarray(temp, float)
        ys = (
            pd.Series(y)
            .rolling(smooth_window, center=True, min_periods=1)
            .median()
            .to_numpy()
        )

        tmin, tmax = t[0], t[-1]
        early = t <= (tmin + pd.Timedelta(hours=hours))
        late = t >= (tmax - pd.Timedelta(hours=hours))

        warm_samples = []
        if np.any(early):
            warm_samples.append(np.nanpercentile(ys[early], warm_percentile))
        if np.any(late):
            warm_samples.append(np.nanpercentile(ys[late], warm_percentile))

        if len(warm_samples):
            warm_est = float(np.nanmedian(warm_samples))
            side = "high" if warm_est >= sea_mean else "low"
        else:
            side = "high"  # sensible default for temperature

    if side == "low":
        threshold = float(sea_mean - halfw)
        deep_is_below = False  # deep temps are >= threshold (colder is "higher" only if variable inversed; for temp, 'low' warm side is rare)
    else:
        threshold = float(sea_mean + halfw)
        deep_is_below = True  # deep temps <= threshold (usual case for temperature)

    return threshold, deep_is_below


def determine_sea_values(
    time: np.ndarray, temp: np.ndarray, bot_start: np.datetime64, bot_end: np.datetime64
) -> tuple[float, float]:
    """Return (sea_mean, sea_std) computed over the bottom window [bot_start, bot_end].
    If the mask is empty, fall back to whole-series robust stats.
    """
    t = pd.to_datetime(time)
    y = np.asarray(temp, float)
    m = (t >= pd.to_datetime(bot_start)) & (t <= pd.to_datetime(bot_end))
    if np.any(m):
        sea_mean = float(np.nanmean(y[m]))
        sea_std = (
            float(np.nanstd(y[m], ddof=1)) if np.sum(np.isfinite(y[m])) > 1 else 0.0
        )
    else:
        # fallback: middle 50% as proxy
        q25, q75 = np.nanpercentile(y, [25, 75])
        m2 = (y >= q25) & (y <= q75)
        sea_mean = float(np.nanmean(y[m2])) if np.any(m2) else float(np.nanmean(y))
        sea_std = (
            float(np.nanstd(y[m2], ddof=1))
            if np.sum(np.isfinite(y[m2])) > 1
            else float(np.nanstd(y, ddof=1))
        )
    return sea_mean, sea_std


def likely_at_surface(
    time: np.ndarray,
    temp: np.ndarray,
    sea_mean: float,
    sea_std: float,
    *,
    hours: float = 24.0,
    smooth_window: int = 9,
    dwell_seconds: int = 1800,
    tol_sigma: float = 2.0,
) -> tuple[np.datetime64 | None, np.datetime64 | None, float]:
    """Return (sink_time, float_time, threshold).
    - sink_time: first sustained entry into 'sea' regime found in the early window.
    - float_time: last sustained exit from 'sea' regime found in the late window.
    - threshold: separator between warm and sea (midpoint between sea_mean and estimated warm_mean).
    """
    t = pd.to_datetime(time)
    y = np.asarray(temp, float)
    ys = _rolling_median(y, smooth_window)

    tmin, tmax = t[0], t[-1]
    early_mask = t <= (tmin + pd.Timedelta(hours=hours))
    late_mask = t >= (tmax - pd.Timedelta(hours=hours))

    te, ye = t[early_mask], ys[early_mask]
    tl, yl = t[late_mask], ys[late_mask]

    dt = _sampling_seconds(time)
    min_len = int(np.ceil(dwell_seconds / dt)) if np.isfinite(dt) and dt > 0 else 1

    # "cold" test relative to sea_mean
    cold_limit = sea_mean + tol_sigma * sea_std
    is_cold_e = ye <= cold_limit
    is_cold_l = yl <= cold_limit

    sink_time = None
    float_time = None
    warm_means = []

    # ---- EARLY window: warm -> cold
    if te.size >= 2 * min_len + 1:
        k_e, mu_e1, mu_e2 = _best_step_change(ye, min_seg=min_len)
        # prefer splits where later segment looks like sea
        if (mu_e2 < mu_e1) and (abs(mu_e2 - sea_mean) <= abs(mu_e1 - sea_mean)):
            # first sustained cold run AFTER k_e
            post_mask = np.zeros_like(is_cold_e, dtype=bool)
            post_mask[k_e:] = True
            runs = _runs_of_true(is_cold_e & post_mask)
            runs = [(s, e) for (s, e) in runs if (e - s + 1) >= min_len]
            if runs:
                s0, _ = runs[0]
                sink_time = np.datetime64(te[s0].to_datetime64())
            warm_means.append(mu_e1)  # early warm
        else:
            # fallback: first sustained cold run in early window
            runs = _runs_of_true(is_cold_e)
            runs = [(s, e) for (s, e) in runs if (e - s + 1) >= min_len]
            if runs:
                s0, _ = runs[0]
                sink_time = np.datetime64(te[s0].to_datetime64())
            # estimate warm as mean before first cold run
            if runs and runs[0][0] > 0:
                warm_means.append(float(np.nanmean(ye[: runs[0][0]])))
    else:
        # too short: simple sustained cold
        runs = _runs_of_true(is_cold_e)
        runs = [(s, e) for (s, e) in runs if (e - s + 1) >= min_len]
        if runs:
            s0, _ = runs[0]
            sink_time = np.datetime64(te[s0].to_datetime64())
        if runs and runs[0][0] > 0:
            warm_means.append(float(np.nanmean(ye[: runs[0][0]])))

    # ---- LATE window: cold -> warm
    if tl.size >= 2 * min_len + 1:
        k_l, mu_l1, mu_l2 = _best_step_change(yl, min_seg=min_len)
        # prefer splits where earlier segment looks like sea
        if (mu_l1 < mu_l2) and (abs(mu_l1 - sea_mean) <= abs(mu_l2 - sea_mean)):
            # last sustained cold run BEFORE k_l
            pre_mask = np.zeros_like(is_cold_l, dtype=bool)
            pre_mask[:k_l] = True
            runs = _runs_of_true(is_cold_l & pre_mask)
            runs = [(s, e) for (s, e) in runs if (e - s + 1) >= min_len]
            if runs:
                _, eL = runs[-1]
                float_time = np.datetime64(tl[eL].to_datetime64())
            warm_means.append(mu_l2)  # late warm
        else:
            # fallback: last sustained cold run in late window
            runs = _runs_of_true(is_cold_l)
            runs = [(s, e) for (s, e) in runs if (e - s + 1) >= min_len]
            if runs:
                _, eL = runs[-1]
                float_time = np.datetime64(tl[eL].to_datetime64())
            # estimate warm as mean after last cold run
            if runs and runs[-1][1] < (yl.size - 1):
                warm_means.append(float(np.nanmean(yl[runs[-1][1] + 1 :])))
    else:
        runs = _runs_of_true(is_cold_l)
        runs = [(s, e) for (s, e) in runs if (e - s + 1) >= min_len]
        if runs:
            _, eL = runs[-1]
            float_time = np.datetime64(tl[eL].to_datetime64())
        if runs and runs[-1][1] < (yl.size - 1):
            warm_means.append(float(np.nanmean(yl[runs[-1][1] + 1 :])))

    # ---- threshold: midpoint between sea_mean and an estimated warm mean
    if warm_means:
        warm_mean = float(np.nanmedian(warm_means))
        threshold = 0.5 * (sea_mean + warm_mean)
    else:
        # fallback if we never saw warm segments: small offset above sea
        threshold = sea_mean + max(0.5, 2.0 * sea_std)

    return sink_time, float_time, float(threshold)


def find_entry_exit_from_threshold(time, temp, threshold):
    """Return (sink_time, float_time) where temp is below threshold.
    No smoothing, no dwell time — just first/last index where condition holds.
    """
    t = pd.to_datetime(time).to_numpy()  # datetime64[ns] array
    y = np.asarray(temp, float)

    finite = np.isfinite(y)
    below = finite & (y < threshold)

    idx = np.where(below)[0]
    if idx.size:
        sink_time = np.datetime64(t[idx[0]])
        float_time = np.datetime64(t[idx[-1]])
    else:
        sink_time = np.datetime64("NaT", "ns")
        float_time = np.datetime64("NaT", "ns")

    return sink_time, float_time


# ---------- main, function-only API ----------


def find_deployment(
    ds,
    var_name="temperature",
    *,
    bottom_strategy="percent_span",
    bottom_margin_hours=24.0,
    bottom_inner_fraction=0.25,
    deployment_time=None,
    recovery_time=None,
    deployment_margin_hours=2.0,
    surface_window_hours=24.0,  # noqa: ARG001  — reserved for future surface-window logic
    smooth_window=9,  # noqa: ARG001  — reserved for future smoothing step
    method="sigma_band",  # noqa: ARG001  — reserved for future method dispatch
    band_sigma=3.0,  # noqa: ARG001  — reserved for future sigma_band method
    dwell_seconds=1800,  # noqa: ARG001  — reserved for future dwell filter
):
    """Populate ds with:
      - start_time (N_LEVELS) : first deep time (when temps settle cold)
      - end_time   (N_LEVELS) : last deep time (before temps warm)
      - split_value(N_LEVELS) : threshold separating surface vs sea regimes

    Returns ds with the variables added/updated.
    """
    if var_name not in ds:
        raise ValueError(f"{var_name!r} not found in dataset.")
    if ds[var_name].dims != ("time", "N_LEVELS"):
        raise ValueError(
            f"{var_name!r} must have dims ('time','N_LEVELS'). Got {ds[var_name].dims}"
        )

    nlev = ds.sizes.get("N_LEVELS", None)
    if nlev is None:
        raise ValueError("Dimension 'N_LEVELS' not found in dataset.")

    # ensure outputs exist with correct dtype/shape
    if "start_time" not in ds:
        ds["start_time"] = ("N_LEVELS", np.full(nlev, np.datetime64("NaT", "ns")))
    if "end_time" not in ds:
        ds["end_time"] = ("N_LEVELS", np.full(nlev, np.datetime64("NaT", "ns")))
    if "split_value" not in ds:
        ds["split_value"] = ("N_LEVELS", np.full(nlev, np.nan))

    time = ds["time"].values
    print(f"My time starts at {time[0]}")

    for i in range(nlev):
        data1 = ds[var_name][:, i].values
        deployment_time = ds["time"][0].values
        recovery_time = ds["time"][-1].values
        print(f"recovery time is {recovery_time}")

        # 1) pick bottom window using your chosen strategy
        bot_start, bot_end = likely_at_bottom_window(
            time,
            strategy=bottom_strategy,
            bottom_margin_hours=bottom_margin_hours,
            bottom_inner_fraction=bottom_inner_fraction,
            deployment_time=deployment_time,
            recovery_time=recovery_time,
            deployment_margin_hours=deployment_margin_hours,
        )
        print(f"My bot_start is {bot_start} and bot_end is {bot_end}")
        # 2) sea (cold) representative value from bottom window
        sea_mean, sea_std = determine_sea_values(time, data1, bot_start, bot_end)
        print(f"My sea_mean is {sea_mean} and std is {sea_std}")

        # 2) threshold (separate step)
        thr, deep_is_below = compute_threshold_sigma_band(
            time,
            data1,
            sea_mean,
            sea_std,
            band_sigma=1.5,
            min_band_halfwidth=0.10,
            warm_side="auto",  # or "high"
            hours=24.0,
            smooth_window=9,
            warm_percentile=90,
        )
        # 3) entry/exit from threshold (sustained crossings)
        sink_time, float_time = find_entry_exit_from_threshold(time, data1, thr)

        # 4) assign
        ds["start_time"][i] = (
            np.datetime64(sink_time)
            if sink_time is not None
            else np.datetime64("NaT", "ns")
        )
        ds["end_time"][i] = (
            np.datetime64(float_time)
            if float_time is not None
            else np.datetime64("NaT", "ns")
        )
        ds["split_value"][i] = float(thr) if np.isfinite(thr) else np.nan

        # 5) print summary (same style as you had)
        serial = str(ds["serial_number"].values[i]) if "serial_number" in ds else None
        inst = str(ds["instrument"].values[i]) if "instrument" in ds else None
        label = f"{i}"
        if serial:
            label += f"/{serial}"
        if inst:
            label += f":{inst}"
        print(
            f"{label}: Split at {ds['split_value'][i].item():.2f}.  "
            f"Start after {ds['start_time'][i].values}.  "
            f"End with {ds['end_time'][i].values}."
        )

    return ds
