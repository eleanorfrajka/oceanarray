"""Integration tests for stage1 output fixtures.

These tests assert structural and provenance properties of the committed stage1
NetCDF files without running the processor (which requires seasenselib).  They
catch regressions in the CF-NetCDF output format — missing variables, wrong
dimension names, absent required attributes — that unit tests cannot reach
because unit tests never touch a real instrument file.
"""

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

FIXTURE_PROC = Path(__file__).parent.parent / "fixtures" / "proc" / "dune2_1_2026"

MICROCAT_S1 = FIXTURE_PROC / "microcat" / "dune2_1_2026_2941_stage1.nc"
AQUADOPP_S1 = FIXTURE_PROC / "aquadopp" / "dune2_1_2026_9920_stage1.nc"


class TestMicrocatStage1:
    """Structural tests for the committed microcat (serial 2941) stage1 fixture."""

    @pytest.fixture(autouse=True)
    def ds(self):
        """Open and close the stage1 dataset around each test."""
        self._ds = xr.open_dataset(MICROCAT_S1)
        yield
        self._ds.close()

    def test_time_dimension_present(self):
        assert "time" in self._ds.sizes

    def test_record_count(self):
        assert self._ds.sizes["time"] == 19898

    def test_core_variables_present(self):
        for var in ("temperature", "conductivity"):
            assert var in self._ds.data_vars, f"missing {var}"

    def test_serial_number_scalar(self):
        assert "serial_number" in self._ds.data_vars
        assert self._ds["serial_number"].dims == ()

    def test_no_all_nan_temperature(self):
        assert not np.all(np.isnan(self._ds["temperature"].values))

    def test_sensor_metadata_scalars_present(self):
        # One sensor record per instrument, holding each sensor's calibration.
        assert "SENSOR_CTD_2941" in self._ds.data_vars
        assert self._ds["temperature"].attrs["sensor"] == "SENSOR_CTD_2941"
        dates = self._ds["SENSOR_CTD_2941"].attrs["sensor_calibration_dates"]
        assert dates.startswith("temperature: ")

    def test_processing_level_attr(self):
        assert self._ds.attrs.get("processing_level") == "L1"

    def test_processor_name_attr(self):
        assert "processor_name" in self._ds.attrs


class TestAquadoppStage1:
    """Structural tests for the committed aquadopp (serial 9920) stage1 fixture."""

    @pytest.fixture(autouse=True)
    def ds(self):
        self._ds = xr.open_dataset(AQUADOPP_S1)
        yield
        self._ds.close()

    def test_time_dimension_present(self):
        assert "time" in self._ds.sizes

    def test_record_count(self):
        assert self._ds.sizes["time"] == 9960

    def test_beam_dimension_present(self):
        assert "beam" in self._ds.sizes

    def test_velocity_variables_present(self):
        for var in ("velocity_beam1", "velocity_beam2", "velocity_beam3"):
            assert var in self._ds.data_vars, f"missing {var}"

    def test_no_all_nan_velocity(self):
        assert not np.all(np.isnan(self._ds["velocity_beam1"].values))

    def test_processing_level_attr(self):
        assert self._ds.attrs.get("processing_level") == "L1"
