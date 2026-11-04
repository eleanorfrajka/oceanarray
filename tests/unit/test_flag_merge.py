"""Agreement test: _worst_flag (pipeline/qc) and _merge_flags (pipeline/qc).

Both functions use the same parameters.QC_MERGE_PRIORITY_LUT.  If the LUT or
either function diverges, gridded products silently promote bad data to good.
This test parametrises all 100 valid flag-pair combinations (0–9 × 0–9) and
asserts that both functions return the same worst-wins result.
"""

import numpy as np
import pytest

from oceanarray.pipeline.qc import _merge_flags, _worst_flag

_FLAGS = list(range(10))
_ALL_PAIRS = [(a, b) for a in _FLAGS for b in _FLAGS]


@pytest.mark.parametrize("a,b", _ALL_PAIRS, ids=[f"{a}v{b}" for a, b in _ALL_PAIRS])
def test_worst_flag_agrees_with_merge_flags(a, b):
    """_worst_flag and _merge_flags must return the same result for every flag pair.

    Both implement worst-wins using QC_MERGE_PRIORITY_LUT; testing all 100 pairs
    ensures neither function can drift from the other without a test failure.
    """
    arr_a = np.array([a], dtype=np.int8)
    arr_b = np.array([b], dtype=np.int8)

    result_worst = int(_worst_flag(arr_a.astype(float), arr_b.astype(float))[0])
    result_merge = int(_merge_flags(arr_a, arr_b)[0])

    assert result_worst == result_merge, (
        f"_worst_flag({a}, {b}) = {result_worst} but _merge_flags({a}, {b}) = {result_merge}"
    )
