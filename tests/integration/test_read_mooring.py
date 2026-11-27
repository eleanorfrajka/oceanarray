"""Tests for :func:`oceanarray.pipeline.read_mooring.read_mooring`.

The stage=2|3 paths and the attribute-stamping logic need no seasenselib; the
stage=None raw read does (SBE + Nortek) and skips without it. Inputs are the
committed dune2 fixture tree. A mooring's identity is its ``name``; its location
is the directory passed as ``mooring_dir`` (or derived from a path).
"""

import logging
import re
import shutil
import warnings
from pathlib import Path

import pytest
import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray.exceptions import MooringError
from oceanarray.pipeline import _stage
from oceanarray.pipeline.read_mooring import header_attrs, read_mooring

_FIX = Path(__file__).parent.parent / "fixtures"
_PROC = _FIX / "proc"
_RAW = _FIX / "raw"
_MOORING_DIR = _PROC / "dune2_1_2026"
_YAML = _MOORING_DIR / "dune2_1_2026.mooring.yaml"


def _cfg() -> dict:
    return _mooring.load(_YAML)


def test_stage3_keys_equal_the_yaml_serials():
    """read_mooring(stage=3) returns one dataset per instrument, keyed by serial."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # unmarked fixtures WARN via require
        datasets = read_mooring(_cfg(), stage=3, mooring_dir=_MOORING_DIR)
    assert sorted(datasets) == ["2941", "9920"]


def test_stage3_from_a_directory_path():
    """A directory path finds its one *.mooring.yaml and derives its own location."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        datasets = read_mooring(_MOORING_DIR, stage=3)
    assert sorted(datasets) == ["2941", "9920"]


def test_stage3_warns_and_infers_on_unmarked_files(tmp_path, caplog):
    """A stage file written before the marker existed has its stage inferred (with a WARNING)."""
    moor_dir = tmp_path / "dune2_1_2026"
    shutil.copytree(_MOORING_DIR, moor_dir)
    for path in moor_dir.glob("*/*_stage3.nc"):
        with xr.open_dataset(path, engine="netcdf4", decode_timedelta=False) as ds:
            ds = ds.load()
        del ds.attrs[_stage.STAGE_ATTR]
        ds.to_netcdf(path)
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        datasets = read_mooring(_cfg(), stage=3, mooring_dir=moor_dir)
    assert re.search("inferring stage 3 from the filename", caplog.text)
    # The inferred marker is stamped in memory, so the returned datasets carry it.
    assert all(ds.attrs[_stage.STAGE_ATTR] == 3 for ds in datasets.values())


def test_serials_filter_selects_one():
    """A serials filter reads only the listed instrument."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        datasets = read_mooring(
            _cfg(), stage=3, mooring_dir=_MOORING_DIR, serials=["2941"]
        )
    assert list(datasets) == ["2941"]


def test_missing_stage_file_refuses(tmp_path):
    """A listed instrument with no file at the requested stage raises, naming it."""
    moor_dir = tmp_path / "dune2_1_2026"
    shutil.copytree(_MOORING_DIR, moor_dir)
    (moor_dir / "aquadopp" / "dune2_1_2026_9920_stage3.nc").unlink()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(FileNotFoundError, match="no stage-3 file"):
            read_mooring(_cfg(), stage=3, mooring_dir=moor_dir)


def test_allow_missing_skips_the_absent_file(tmp_path):
    """allow_missing turns the refusal into a skip with a warning."""
    moor_dir = tmp_path / "dune2_1_2026"
    shutil.copytree(_MOORING_DIR, moor_dir)
    (moor_dir / "aquadopp" / "dune2_1_2026_9920_stage3.nc").unlink()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        datasets = read_mooring(
            _cfg(), stage=3, mooring_dir=moor_dir, allow_missing=True
        )
    assert list(datasets) == ["2941"]


def test_dict_with_stage_but_no_mooring_dir_raises():
    """A dict has no location, so read_mooring(dict, stage=3) needs mooring_dir."""
    with pytest.raises(MooringError, match="no mooring_dir"):
        read_mooring(_cfg(), stage=3)


def test_raw_without_raw_dir_raises():
    """read_mooring(stage=None) with no raw_dir is a usage error."""
    with pytest.raises(ValueError, match="needs raw_dir"):
        read_mooring(_cfg(), stage=None)


# ---------------------------------------------------------------------------
# Per-mooring header stamping (no I/O)
# ---------------------------------------------------------------------------


def test_mooring_attrs_stamp_position_and_window():
    """A full mooring YAML stamps position/window with their real sources."""
    attrs = header_attrs(_cfg())
    assert attrs["mooring_name"] == "dune2_1_2026"
    assert attrs["location_source"] == "latitude/longitude"
    assert attrs["deployment_window_source"] == "deployment_time/recovery_time"
    assert attrs["waterdepth"] == 745
    assert "latitude_str" in attrs


def test_mooring_attrs_warn_and_mark_none_when_absent(caplog):
    """No position: none stamped. No window: the placeholder. Both *_source=none."""
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        attrs = header_attrs({"name": "x"})
    assert "WARNING" in caplog.text
    assert "latitude" not in attrs and "longitude" not in attrs
    assert attrs["location_source"] == "none"
    assert attrs["deployment_window_source"] == "none"
    assert "latitude_str" not in attrs  # not stamped for a placeholder position


# ---------------------------------------------------------------------------
# Raw read (needs seasenselib)
# ---------------------------------------------------------------------------


def test_raw_read_stamps_the_facts_and_no_marker():
    """read_mooring(stage=None) reads each raw file, stamps the facts, and leaves it unmarked."""
    pytest.importorskip("seasenselib", reason="seasenselib not installed")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        datasets = read_mooring(_cfg(), stage=None, raw_dir=_RAW)
    assert sorted(datasets) == ["2941", "9920"]
    mc = datasets["2941"]
    assert _stage.STAGE_ATTR not in mc.attrs
    assert mc.attrs["serial"] == "2941"
    assert mc.attrs["instrument"] == "microcat"
    assert mc.attrs["hab"] == 425
    assert mc.attrs["file_type"] == "sbe-ascii"
    assert mc.attrs["mooring_name"] == "dune2_1_2026"
    assert mc.attrs["source_file"] == "2941_recovery.asc"
    assert mc.attrs["stack"] is True
