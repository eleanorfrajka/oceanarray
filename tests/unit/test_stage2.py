"""Unit tests for oceanarray.stage2 module (synthetic data only).

Version: 2.0 - Fixed clock offset tests with proper array comparison and immutability testing
"""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

pytest.importorskip("seasenselib", reason="seasenselib not installed")

from oceanarray.pipeline.stage2 import (
    Stage2Processor,
    _add_missing_metadata,
    _apply_clock_offset,
    _drop_redundant_sbe_vars,
    _parse_clock_str,
    _parse_yaml_time,
    _resolve_clock_drift,
    _trim_to_deployment_window,
    detect_deployment_window,
)

pytestmark = pytest.mark.needs_seasenselib


class TestStage2Processor:
    """Test cases for Stage2Processor class."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def processor(self, temp_dir):
        """Create a Stage2Processor instance for testing."""
        return Stage2Processor(proc_dir=str(temp_dir))

    @pytest.fixture
    def sample_yaml_data(self):
        """Sample YAML configuration data for Stage 2."""
        return {
            "name": "test_mooring",
            "waterdepth": 1000,
            "longitude": -30.0,
            "latitude": 60.0,
            "deployment_time": "2018-08-12T09:00:00",
            "recovery_time": "2018-08-26T19:00:00",
            "instruments": [
                {
                    "instrument": "microcat",
                    "serial": 7518,
                    "depth": 100,
                    "clock_offset": 300,  # 5 minutes
                }
            ],
        }

    @pytest.fixture
    def sample_raw_dataset(self):
        """Create a sample raw dataset for testing."""
        start_time = pd.to_datetime("2018-08-12T08:00:00")
        end_time = pd.to_datetime("2018-08-26T20:00:00")
        time_range = pd.date_range(start_time, end_time, freq="10min")

        data = {
            "temperature": (["time"], np.random.random(len(time_range)) + 20),
            "salinity": (["time"], np.random.random(len(time_range)) + 35),
            "pressure": (["time"], np.random.random(len(time_range)) + 100),
            "timeS": (["time"], np.arange(len(time_range))),
        }

        ds = xr.Dataset(data, coords={"time": time_range})

        ds.attrs["mooring_name"] = "test_mooring"
        ds["serial_number"] = 7518
        ds["instrument"] = "microcat"
        ds["InstrDepth"] = 100

        return ds

    def test_init(self, temp_dir):
        """Test Stage2Processor initialization."""
        processor = Stage2Processor(proc_dir=str(temp_dir))
        assert processor._proc_dir == temp_dir
        assert processor.log_file is None

    def test_setup_logging(self, processor, temp_dir):
        """Test logging setup."""
        mooring_name = "test_mooring"
        output_path = temp_dir / "output"
        output_path.mkdir()

        processor._setup_logging(mooring_name, output_path)

        assert processor.log_file is not None
        assert processor.log_file.parent == output_path / "processing_logs"
        assert mooring_name in processor.log_file.name
        assert "stage2.log" in processor.log_file.name

    def test_read_yaml_time_valid(self):
        """Test reading valid time from YAML."""
        data = {"deployment_time": "2018-08-12T09:00:00"}
        result = _parse_yaml_time(data, "deployment_time")
        expected = pd.to_datetime("2018-08-12T09:00:00").to_datetime64()
        assert result == expected

    def test_read_yaml_time_missing(self):
        """Test reading missing time from YAML."""
        data = {}
        result = _parse_yaml_time(data, "deployment_time")
        assert pd.isna(result)

    def test_read_yaml_time_empty_string(self):
        """Test reading empty string time from YAML."""
        data = {"deployment_time": ""}
        result = _parse_yaml_time(data, "deployment_time")
        assert pd.isna(result)

    def test_read_yaml_time_invalid_raises(self):
        """A present-but-unparseable time is a log-sheet typo, not a missing field."""
        from oceanarray.exceptions import MooringError

        data = {"deployment_time": "not-a-date"}
        with pytest.raises(MooringError, match="deployment_time"):
            _parse_yaml_time(data, "deployment_time")

    def test_apply_clock_offset_zero(self, sample_raw_dataset):
        """Test applying zero clock offset - Version 2.0 with proper array testing."""
        original_time = sample_raw_dataset.time.copy()
        result = _apply_clock_offset(sample_raw_dataset, 0)

        np.testing.assert_array_equal(result.time.values, original_time.values)

        np.testing.assert_array_equal(
            sample_raw_dataset.time.values, original_time.values
        )

    def test_apply_clock_offset_positive(self, sample_raw_dataset):
        """Test applying positive clock offset - Version 2.0 with immutability testing."""
        offset_seconds = 300  # 5 minutes
        original_time = sample_raw_dataset.time.copy()
        result = _apply_clock_offset(sample_raw_dataset, offset_seconds)

        assert "clock_offset" in result.variables
        assert result["clock_offset"].values == offset_seconds
        assert result["clock_offset"].attrs["units"] == "s"

        expected_time = original_time + np.timedelta64(offset_seconds, "s")
        np.testing.assert_array_equal(result.time.values, expected_time.values)

        np.testing.assert_array_equal(
            sample_raw_dataset.time.values, original_time.values
        )

    def test_apply_clock_offset_negative(self, sample_raw_dataset):
        """Test applying negative clock offset - Version 2.0 with immutability testing."""
        offset_seconds = -600  # -10 minutes
        original_time = sample_raw_dataset.time.copy()
        result = _apply_clock_offset(sample_raw_dataset, offset_seconds)

        expected_time = original_time + np.timedelta64(offset_seconds, "s")
        np.testing.assert_array_equal(result.time.values, expected_time.values)

        np.testing.assert_array_equal(
            sample_raw_dataset.time.values, original_time.values
        )

    def test_trim_to_deployment_window_both_bounds(self, sample_raw_dataset):
        """Test trimming with both deployment and recovery times."""
        deploy_time = np.datetime64("2018-08-12T10:00:00")
        recover_time = np.datetime64("2018-08-26T18:00:00")

        result = _trim_to_deployment_window(
            sample_raw_dataset, deploy_time, recover_time
        )

        assert result.time.min() >= deploy_time
        assert result.time.max() <= recover_time
        assert len(result.time) < len(sample_raw_dataset.time)

    def test_trim_to_deployment_window_deploy_only(self, sample_raw_dataset):
        """Test trimming with only deployment time."""
        deploy_time = np.datetime64("2018-08-12T10:00:00")
        recover_time = np.datetime64("NaT")

        result = _trim_to_deployment_window(
            sample_raw_dataset, deploy_time, recover_time
        )

        assert result.time.min() >= deploy_time
        assert result.time.max() == sample_raw_dataset.time.max()

    def test_trim_to_deployment_window_recover_only(self, sample_raw_dataset):
        """Test trimming with only recovery time."""
        deploy_time = np.datetime64("NaT")
        recover_time = np.datetime64("2018-08-26T18:00:00")

        result = _trim_to_deployment_window(
            sample_raw_dataset, deploy_time, recover_time
        )

        assert result.time.min() == sample_raw_dataset.time.min()
        assert result.time.max() <= recover_time

    def test_trim_to_deployment_window_no_trimming(self, sample_raw_dataset):
        """Test trimming with no valid times."""
        deploy_time = np.datetime64("NaT")
        recover_time = np.datetime64("NaT")

        result = _trim_to_deployment_window(
            sample_raw_dataset, deploy_time, recover_time
        )

        assert len(result.time) == len(sample_raw_dataset.time)

    def test_trim_to_deployment_window_empty_result(self, sample_raw_dataset):
        """Test trimming that results in empty dataset."""
        deploy_time = np.datetime64("2019-01-01T00:00:00")
        recover_time = np.datetime64("2019-01-02T00:00:00")

        result = _trim_to_deployment_window(
            sample_raw_dataset, deploy_time, recover_time
        )

        assert len(result.time) == 0

    def test_add_missing_metadata(self, sample_raw_dataset):
        """Metadata scalar variables are added from the description entry when absent."""
        ds = sample_raw_dataset.copy()
        ds = ds.drop_vars(["InstrDepth", "instrument", "serial_number"])

        instrument_config = {"depth": 150, "instrument": "new_microcat", "serial": 9999}
        result = _add_missing_metadata(ds, instrument_config)

        assert result["InstrDepth"].values == 150
        assert result["instrument"].values == "new_microcat"
        assert result["serial_number"].values == 9999

    def test_add_missing_metadata_no_overwrite(self, sample_raw_dataset):
        """Existing metadata variables are not overwritten."""
        instrument_config = {
            "depth": 999,
            "instrument": "different_instrument",
            "serial": 8888,
        }
        result = _add_missing_metadata(sample_raw_dataset, instrument_config)

        assert result["InstrDepth"].values == 100
        assert result["instrument"].values == "microcat"
        assert result["serial_number"].values == 7518

    def test_add_missing_metadata_depth_default(self):
        """A missing depth defaults InstrDepth to 0; identity comes from read_mooring.

        The old filepath-fallback (instrument/serial inferred from the filename) is
        gone — ``read_mooring`` joins the file with its description instead.
        """
        ds = xr.Dataset(
            {"temperature": (["time"], np.random.random(10))},
            coords={"time": pd.date_range("2018-01-01", periods=10, freq="h")},
        )
        result = _add_missing_metadata(ds, {"instrument": "microcat", "serial": 7518})

        assert result["instrument"].values == "microcat"
        assert result["serial_number"].values == 7518
        assert result["InstrDepth"].values == 0
        assert "history" not in result.attrs

    def test_clean_unnecessary_variables(self, sample_raw_dataset):
        """Test removal of unnecessary variables."""
        result = _drop_redundant_sbe_vars(sample_raw_dataset)

        assert "timeS" not in result.variables
        assert "temperature" in result.variables
        assert "salinity" in result.variables
        assert "pressure" in result.variables


class TestDetectDeploymentWindow:
    """Tests for the detect_deployment_window function."""

    def test_bench_data_in_middle_window(self):
        """Bench data in the first/last 25 % does not prevent correct detection.

        Synthetic pressure: 50 bench samples (0 dbar) + 200 deployed samples
        (1200 dbar) + 50 bench samples (0 dbar).  The old fixed-12h algorithm
        failed on this layout; the new middle-50 % approach must detect start at
        index 50 and end at index 249.
        """
        n_bench = 50
        n_deploy = 200
        pressure = np.array([0.0] * n_bench + [1200.0] * n_deploy + [0.0] * n_bench)
        time = pd.date_range("2026-05-01", periods=len(pressure), freq="30min")
        ds = xr.Dataset({"pressure": ("time", pressure), "time": time})

        sug_start, sug_end, source = detect_deployment_window(ds)

        assert source == "pressure_pmin10dbar", f"unexpected source: {source}"
        assert sug_start is not None, "start should be detected"
        assert sug_end is not None, "end should be detected"
        assert sug_start == ds["time"].values[50], (
            f"expected start at index 50 ({ds['time'].values[50]}), got {sug_start}"
        )
        assert sug_end == ds["time"].values[249], (
            f"expected end at index 249 ({ds['time'].values[249]}), got {sug_end}"
        )


class TestErrorHandling:
    """Test error handling scenarios - Version 2.0."""

    def test_invalid_yaml_file(self, tmp_path):
        """Test handling of invalid YAML file."""
        processor = Stage2Processor(proc_dir=str(tmp_path))

        mooring_dir = tmp_path / "test_mooring"
        mooring_dir.mkdir(parents=True)

        invalid_yaml = mooring_dir / "test_mooring.mooring.yaml"
        invalid_yaml.write_text("invalid: yaml: content: [")

        result = processor.process_mooring("test_mooring")
        assert result is False

    def test_processing_directory_not_found(self, tmp_path):
        """Test handling when processing directory doesn't exist."""
        processor = Stage2Processor(proc_dir=str(tmp_path))
        result = processor.process_mooring("nonexistent_mooring")
        assert result is False


class TestParseClockStr:
    """Tests for _parse_clock_str — the clock timestamp parser."""

    def test_time_only_hh_mm_ss(self):
        t = _parse_clock_str("18:43:30")
        assert t.hour == 18
        assert t.minute == 43
        assert t.second == 30

    def test_compact_yyyymmdd(self):
        t = _parse_clock_str("20260710T18:43:30")
        assert t.year == 2026
        assert t.month == 7
        assert t.day == 10
        assert t.hour == 18
        assert t.minute == 43
        assert t.second == 30

    def test_standard_iso(self):
        t = _parse_clock_str("2026-07-10T18:43:30")
        assert t.year == 2026
        assert t.month == 7
        assert t.day == 10
        assert t.hour == 18
        assert t.minute == 43
        assert t.second == 30

    def test_compact_and_standard_agree(self):
        assert _parse_clock_str("20260710T18:43:30") == _parse_clock_str(
            "2026-07-10T18:43:30"
        )

    def test_drift_time_only_instrument_slow(self):
        """Instrument 33 s behind computer → drift = +33 s."""
        comp = _parse_clock_str("18:44:03")
        inst = _parse_clock_str("18:43:30")
        assert (comp - inst).total_seconds() == 33.0

    def test_drift_time_only_instrument_fast(self):
        """Instrument 7 s ahead of computer → drift = −7 s."""
        comp = _parse_clock_str("21:03:00")
        inst = _parse_clock_str("21:03:07")
        assert (comp - inst).total_seconds() == -7.0

    def test_drift_compact_matches_time_only(self):
        """All formats give identical drift."""
        drift_compact = (
            _parse_clock_str("20260710T19:12:30")
            - _parse_clock_str("20260710T19:12:39")
        ).total_seconds()
        drift_time = (
            _parse_clock_str("19:12:30") - _parse_clock_str("19:12:39")
        ).total_seconds()
        assert drift_compact == drift_time == -9.0


class TestResolveClockDrift:
    """Tests for Stage2Processor._resolve_clock_drift with real YAML field names."""

    def test_option_b_standard_iso_instrument_slow(self):
        cfg = {
            "computer_clock_at_recovery": "2026-07-10T19:12:39",
            "instrument_clock_at_recovery": "2026-07-10T19:12:30",
        }
        drift_s, note = _resolve_clock_drift(cfg)
        assert drift_s == 9.0
        assert "clock_drift=+9.0s" in note

    def test_option_b_standard_iso_instrument_fast(self):
        cfg = {
            "computer_clock_at_recovery": "2026-07-10T21:03:00",
            "instrument_clock_at_recovery": "2026-07-10T21:03:07",
        }
        drift_s, note = _resolve_clock_drift(cfg)
        assert drift_s == -7.0
        assert "clock_drift=-7.0s" in note

    def test_option_b_time_only(self):
        cfg = {
            "computer_clock_at_recovery": "19:12:39",
            "instrument_clock_at_recovery": "19:12:30",
        }
        drift_s, note = _resolve_clock_drift(cfg)
        assert drift_s == 9.0

    def test_option_b_compact_datetime(self):
        cfg = {
            "computer_clock_at_recovery": "20260710T19:12:39",
            "instrument_clock_at_recovery": "20260710T19:12:30",
        }
        drift_s, note = _resolve_clock_drift(cfg)
        assert drift_s == 9.0

    def test_option_b_takes_priority_over_option_a(self):
        cfg = {
            "clock_drift_seconds": 999,
            "computer_clock_at_recovery": "2026-07-10T19:12:39",
            "instrument_clock_at_recovery": "2026-07-10T19:12:30",
        }
        drift_s, _ = _resolve_clock_drift(cfg)
        assert drift_s == 9.0  # Option B wins

    def test_option_a_drift_seconds(self):
        cfg = {"clock_drift_seconds": 33}
        drift_s, note = _resolve_clock_drift(cfg)
        assert drift_s == 33.0
        assert "33.0s" in note

    def test_no_clock_info_returns_zero(self):
        drift_s, _ = _resolve_clock_drift({})
        assert drift_s == 0.0

    def test_unknown_string_ignored(self):
        """'unknown' in YAML should be treated as no correction."""
        cfg = {
            "computer_clock_at_recovery": "unknown",
            "instrument_clock_at_recovery": "unknown",
        }
        drift_s, _ = _resolve_clock_drift(cfg)
        assert drift_s == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
