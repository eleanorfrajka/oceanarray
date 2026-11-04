"""Time filters applied to a series before it is gridded."""

from __future__ import annotations

import numpy as np


def filter_sigma_tukey(
    data: np.ndarray, window_samples: int, alpha: float = 0.5
) -> np.ndarray:
    """Apply a Tukey moving-average filter along axis=1 (time), NaN-aware.

    Uses a finite-weight convolution: each output point is the weighted mean
    of the finite values within the window, so NaN gaps never contaminate
    adjacent points.  Output is set to NaN where fewer than 10 % of the
    window weights are finite (edges of large data gaps).

    Parameters
    ----------
    data : np.ndarray
        2-D array with shape ``(n_pressure, n_time)``.  NaN marks missing
        values.
    window_samples : int
        Length of the Tukey window in samples.  Values < 3 or ≥ n_time
        return a copy of *data* unchanged.
    alpha : float, optional
        Shape parameter of the Tukey window in ``[0, 1]`` (default 0.5).
        ``alpha=0`` is a rectangular window; ``alpha=1`` is a Hann window.

    Returns
    -------
    np.ndarray
        Smoothed array with the same shape as *data*.  Rows that are entirely
        NaN are returned unchanged.

    """
    from scipy.signal import convolve
    from scipy.signal.windows import tukey

    if window_samples < 3 or window_samples >= data.shape[1]:
        return data.copy()
    w = tukey(window_samples, alpha=alpha).astype(np.float64)
    w /= w.sum()
    n_p = data.shape[0]
    result = data.copy()
    for k in range(n_p):
        col = data[k, :]
        finite = np.isfinite(col)
        if not finite.any():
            continue
        smoothed = convolve(np.where(finite, col, 0.0), w, mode="same")
        wsum = convolve(finite.astype(float), w, mode="same")
        with np.errstate(invalid="ignore"):
            result[k, :] = np.where(wsum > 0.1, smoothed / wsum, np.nan)
    return result
