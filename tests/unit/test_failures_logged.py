"""Unreadable inputs are skipped with a WARNING that names the file; missing folders raise."""

import logging

import pytest

from oceanarray import paths
from oceanarray.exceptions import MooringError
from oceanarray.readers.nortek import _parse_nortek_coord_system
from oceanarray.reports._read import _read_qc_thresholds, _read_timing_info
from oceanarray.reports._recovery_table import generate_recovery_table


@pytest.fixture
def not_netcdf(tmp_path):
    """A stage-2 file name holding bytes that are not NetCDF (a truncated copy)."""
    path = tmp_path / "microcat" / f"M1_2941{paths.stage_suffix(2)}"
    path.parent.mkdir()
    path.write_bytes(b"not a netcdf file")
    return path


def test_qc_thresholds_unreadable_file_warns(not_netcdf, caplog):
    with caplog.at_level(logging.WARNING, logger="oceanarray.reports._read"):
        assert _read_qc_thresholds(not_netcdf) == []
    assert f"{not_netcdf.name}: QC thresholds not read" in caplog.text


def test_timing_unreadable_file_warns(not_netcdf, caplog):
    with caplog.at_level(logging.WARNING, logger="oceanarray.reports._read"):
        assert (
            _read_timing_info(not_netcdf.parent.parent, "microcat", "M1", "2941") == {}
        )
    assert "microcat s/n 2941: timing not read" in caplog.text


def test_nortek_header_missing_warns(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="oceanarray.readers.nortek"):
        assert _parse_nortek_coord_system(tmp_path / "AQD9920.hdr") == "UNK"
    assert "AQD9920.hdr: header not read" in caplog.text


def test_recovery_table_missing_yaml_raises(tmp_path):
    with pytest.raises(MooringError, match="M1: mooring YAML not found"):
        generate_recovery_table(mooring_name="M1", proc_dir=tmp_path, force=True)


def test_recovery_table_missing_yaml_raises_even_with_old_output(tmp_path):
    old = tmp_path / "M1_recovery_table.html"
    old.write_text("<html></html>")
    with pytest.raises(MooringError, match="M1: mooring YAML not found"):
        generate_recovery_table(mooring_name="M1", proc_dir=tmp_path, out_path=old)


def test_stage2_missing_yaml_raises(tmp_path):
    from oceanarray.pipeline.stage2 import Stage2Processor

    (tmp_path / "M1").mkdir()
    with pytest.raises(MooringError, match="M1: mooring YAML not found"):
        Stage2Processor(proc_dir=str(tmp_path)).process_mooring("M1")
