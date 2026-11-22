"""Tests for the processing-stage marker (:mod:`oceanarray.pipeline._stage`)."""

import logging
import re
from pathlib import Path

import pytest
import xarray as xr

from oceanarray.pipeline._stage import STAGE_ATTR, StageError, require, stamp

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "proc" / "dune2_1_2026"
_STAGE3 = _FIXTURE / "microcat" / "dune2_1_2026_2941_stage3.nc"


def _ds() -> xr.Dataset:
    return xr.open_dataset(_STAGE3, engine="netcdf4", decode_timedelta=False)


def test_stamp_sets_marker_history_and_source() -> None:
    """``stamp`` writes the marker, a history line and ``source_file``, leaving the input alone."""
    ds = _ds()
    had_marker = STAGE_ATTR in ds.attrs
    out = stamp(ds, 2, source="x.nc")
    assert out.attrs[STAGE_ATTR] == 2
    assert "processing_stage 2" in out.attrs["history"]
    assert out.attrs["source_file"] == "x.nc"
    assert had_marker == (STAGE_ATTR in ds.attrs)  # the input dataset is untouched
    ds.close()


def test_stamp_appends_to_existing_history() -> None:
    """A stamp appends to an existing ``history`` rather than replacing it."""
    ds = _ds()
    ds.attrs["history"] = "prior"
    out = stamp(ds, 1)
    assert out.attrs["history"].startswith("prior; ")
    ds.close()


def test_require_passes_at_the_right_stage() -> None:
    """``require`` returns quietly when every dataset carries the expected marker."""
    ds = stamp(_ds(), 2)
    require({"2941": ds}, 2, 3)  # no raise


def test_require_raises_on_the_wrong_stage() -> None:
    """A stage-1 dataset entering stage 3 raises ``StageError`` naming the stage to run."""
    ds = stamp(_ds(), 1)
    with pytest.raises(StageError, match="run stage 2 first"):
        require({"2941": ds}, 2, 3)


def test_require_tolerates_a_missing_marker_with_a_warning(caplog) -> None:
    """An unmarked dataset (written before the marker) is a WARNING, not a refusal."""
    ds = _ds()
    ds.attrs.pop(STAGE_ATTR, None)
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        require([ds], 2, 3)
    assert re.search("no 'processing_stage'", caplog.text)
    ds.close()
