"""Unit tests for oceanarray.stage2 module (synthetic data only).

Version: 2.0 - Fixed clock offset tests with proper array comparison and immutability testing
"""

import logging
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

pytest.importorskip("seasenselib", reason="seasenselib not installed")

from oceanarray.exceptions import MooringError
from oceanarray.mooring.instruments import parse_clock_reading, resolve_clock_correction
from oceanarray.pipeline.stage2 import (
    Stage2Processor,
    _add_missing_metadata,
    _apply_clock_offset,
    _drop_redundant_sbe_vars,
    _parse_yaml_time,
    _trim_to_deployment_window,
    _warn_short_record,
    detect_deployment_window,
)

_DUNE2 = Path(__file__).parents[1] / "fixtures" / "proc" / "dune2_1_2026"


@pytest.fixture
def dune2_stage1() -> xr.Dataset:
    """The committed dune2 MicroCAT 2941 stage-1 file."""
    with xr.open_dataset(_DUNE2 / "microcat" / "dune2_1_2026_2941_stage1.nc") as ds:
        return ds.load()


@pytest.fixture
def dune2_entry() -> dict:
    """The description entry of MicroCAT 2941."""
    from oceanarray import mooring

    cfg = mooring.load(_DUNE2 / "dune2_1_2026.mooring.yaml")
    return next(e for e in cfg["clamp"] if str(e["serial"]) == "2941")


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
        from oceanarray.logger import detach_handlers

        p = Stage2Processor(proc_dir=str(temp_dir))
        yield p
        # Close the stage log so Windows can delete the temp directory.
        detach_handlers(getattr(p, "_log_handlers", []))

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
        ds["instrument_serial"] = "7518"
        ds["instrument"] = "microcat"
        ds["instrument_depth"] = 100.0

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

    def test_add_missing_metadata(self, dune2_stage1, dune2_entry):
        """The identity scalars are added from the description entry when absent."""
        ds = dune2_stage1.drop_vars(["instrument_depth", "instrument_serial"])
        result = _add_missing_metadata(ds, dune2_entry)

        assert result["instrument_serial"].values == "2941"
        assert result["instrument_depth"].values == 745 - 425  # waterdepth - hab
        assert result["instrument_depth"].attrs["units"] == "m"

    def test_add_missing_metadata_prefers_nominal_depth(
        self, dune2_stage1, dune2_entry
    ):
        """A nominal_depth in the entry wins over waterdepth - hab; YAML depth is not read."""
        ds = dune2_stage1.drop_vars(["instrument_depth"])
        entry = {**dune2_entry, "nominal_depth": 318, "depth": 999}
        assert _add_missing_metadata(ds, entry)["instrument_depth"].values == 318

    def test_add_missing_metadata_no_overwrite(self, dune2_stage1, dune2_entry):
        """Existing identity scalars are not overwritten."""
        entry = {**dune2_entry, "hab": 0, "instrument": "different_instrument"}
        result = _add_missing_metadata(dune2_stage1, entry)

        assert result["instrument_depth"].values == 320
        assert result["instrument"].values == "microcat"

    def test_add_missing_metadata_renames_old_names(self, dune2_stage1, dune2_entry):
        """A stage-1 file before 0.6.1 (InstrDepth, integer serial_number) gets the new names."""
        old = dune2_stage1.drop_vars(["instrument_depth", "instrument_serial"])
        old = old.assign(InstrDepth=320, serial_number=2941)
        result = _add_missing_metadata(old, dune2_entry)

        assert "InstrDepth" not in result and "serial_number" not in result
        assert result["instrument_serial"].values == "2941"

    def test_add_missing_metadata_without_a_depth(self, dune2_stage1, dune2_entry):
        """An entry with neither nominal_depth nor hab writes no instrument_depth."""
        ds = dune2_stage1.drop_vars(["instrument_depth"])
        entry = {k: v for k, v in dune2_entry.items() if k != "hab"}
        assert "instrument_depth" not in _add_missing_metadata(ds, entry)

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
        """A missing processing directory raises, naming the path."""
        processor = Stage2Processor(proc_dir=str(tmp_path))
        with pytest.raises(MooringError, match="processing directory not found"):
            processor.process_mooring("nonexistent_mooring")


class TestParseClockReading:
    """Tests for instruments.parse_clock_reading — the one clock timestamp parser."""

    def test_time_only_hh_mm_ss(self):
        t = parse_clock_reading("18:43:30")
        assert t.hour == 18
        assert t.minute == 43
        assert t.second == 30

    def test_compact_yyyymmdd(self):
        t = parse_clock_reading("20260710T18:43:30")
        assert t.year == 2026
        assert t.month == 7
        assert t.day == 10
        assert t.hour == 18
        assert t.minute == 43
        assert t.second == 30

    def test_standard_iso(self):
        t = parse_clock_reading("2026-07-10T18:43:30")
        assert t.year == 2026
        assert t.month == 7
        assert t.day == 10
        assert t.hour == 18
        assert t.minute == 43
        assert t.second == 30

    def test_compact_and_standard_agree(self):
        assert parse_clock_reading("20260710T18:43:30") == parse_clock_reading(
            "2026-07-10T18:43:30"
        )

    def test_unparseable_is_none(self):
        assert parse_clock_reading("not a time") is None
        assert parse_clock_reading("") is None
        assert parse_clock_reading(None) is None

    def test_drift_time_only_instrument_slow(self):
        """Instrument 33 s behind computer → drift = +33 s."""
        comp = parse_clock_reading("18:44:03")
        inst = parse_clock_reading("18:43:30")
        assert (comp - inst).total_seconds() == 33.0

    def test_drift_compact_matches_time_only(self):
        """All formats give identical drift."""
        drift_compact = (
            parse_clock_reading("20260710T19:12:30")
            - parse_clock_reading("20260710T19:12:39")
        ).total_seconds()
        drift_time = (
            parse_clock_reading("19:12:30") - parse_clock_reading("19:12:39")
        ).total_seconds()
        assert drift_compact == drift_time == -9.0


class TestResolveClockCorrection:
    """Tests for instruments.resolve_clock_correction with real YAML field names."""

    def test_option_b_standard_iso_instrument_slow(self):
        corr = resolve_clock_correction(
            {
                "computer_clock_at_recovery": "2026-07-10T19:12:39",
                "instrument_clock_at_recovery": "2026-07-10T19:12:30",
            }
        )
        assert corr.seconds == 9.0
        assert corr.applied is True
        assert corr.source == "recovery readings"
        assert "clock_drift=+9.0s" in corr.reason

    def test_option_b_time_only(self):
        corr = resolve_clock_correction(
            {
                "computer_clock_at_recovery": "19:12:39",
                "instrument_clock_at_recovery": "19:12:30",
            }
        )
        assert corr.seconds == 9.0

    def test_option_b_takes_priority_over_option_a(self):
        corr = resolve_clock_correction(
            {
                "clock_drift_seconds": 999,
                "computer_clock_at_recovery": "2026-07-10T19:12:39",
                "instrument_clock_at_recovery": "2026-07-10T19:12:30",
            }
        )
        assert corr.seconds == 9.0  # the readings win

    def test_option_a_drift_seconds(self):
        corr = resolve_clock_correction({"clock_drift_seconds": 33})
        assert corr.seconds == 33.0
        assert corr.source == "clock_drift_seconds"
        assert "33.0s" in corr.reason

    def test_no_clock_info_is_no_correction(self):
        corr = resolve_clock_correction({})
        assert corr.seconds == 0.0
        assert corr.applied is False
        assert corr.source == "none"

    def test_unknown_readings_are_not_read(self):
        """'unknown' in YAML is treated as no reading, not a correction."""
        corr = resolve_clock_correction(
            {
                "computer_clock_at_recovery": "unknown",
                "instrument_clock_at_recovery": "unknown",
            }
        )
        assert corr.seconds == 0.0
        assert corr.source == "not read"


class TestShortRecordWarning:
    """``_warn_short_record`` flags a record that misses >5 % of the planned window."""

    # A two-year window: deployed 2014-08-01, recovered 2016-08-11.
    DEPLOY = np.datetime64("2014-08-01T00:00:00")
    RECOVER = np.datetime64("2016-08-11T00:00:00")

    def test_record_ending_early_warns_once(self, caplog):
        # CF1_2014 MC50: record stops 2015-05-17, ~15 months before recovery.
        with caplog.at_level(logging.INFO, logger="oceanarray"):
            _warn_short_record(
                "MC50",
                "currentmeter",
                self.DEPLOY,
                self.RECOVER,
                np.datetime64("2014-08-02T00:00:00"),
                np.datetime64("2015-05-17T00:00:00"),
            )
        warnings = [r for r in caplog.records if "before recovery" in r.getMessage()]
        assert len(warnings) == 1
        assert "months" in warnings[0].getMessage()

    def test_full_record_is_silent(self, caplog):
        with caplog.at_level(logging.INFO, logger="oceanarray"):
            _warn_short_record(
                "MC100",
                "microcat",
                self.DEPLOY,
                self.RECOVER,
                np.datetime64("2014-08-02T00:00:00"),
                np.datetime64("2016-08-10T00:00:00"),
            )
        assert not [r for r in caplog.records if "recovery" in r.getMessage()]

    def test_unset_window_skips_the_check(self, caplog):
        with caplog.at_level(logging.INFO, logger="oceanarray"):
            _warn_short_record(
                "MC50",
                "microcat",
                np.datetime64("NaT"),
                self.RECOVER,
                np.datetime64("2014-08-02T00:00:00"),
                np.datetime64("2015-05-17T00:00:00"),
            )
        assert not caplog.records


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
