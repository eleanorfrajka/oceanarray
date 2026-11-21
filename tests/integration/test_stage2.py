"""Integration tests for Stage2Processor using committed fixture files.

Runs stage2 (deployment trim + clock-drift correction) on the committed stage1
NC fixtures and compares the output to the committed stage2 fixtures.  Verifies
that the processor produces consistent output across code changes without
requiring seasenselib.
"""

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

pytest.importorskip(
    "seasenselib", reason="stage2 imports seasenselib.writers at module load"
)

from oceanarray.pipeline.stage2 import Stage2Processor

FIXTURE_PROC = Path(__file__).parent.parent / "fixtures" / "proc" / "dune2_1_2026"
MOORING = "dune2_1_2026"

MICROCAT_S2 = FIXTURE_PROC / "microcat" / f"{MOORING}_2941_stage2.nc"
AQUADOPP_S2 = FIXTURE_PROC / "aquadopp" / f"{MOORING}_9920_stage2.nc"


class TestStage2Microcat:
    """Stage2 output for microcat (serial 2941) matches committed fixture."""

    @pytest.fixture(autouse=True)
    def run(self, proc_root_stage1):
        """Run stage2 on the committed stage1 NC and open both outputs."""
        proc = Stage2Processor(proc_dir=str(proc_root_stage1))
        ok = proc.process_mooring(MOORING, force=True)
        assert ok, "Stage2Processor.process_mooring returned False"

        out_nc = proc_root_stage1 / MOORING / "microcat" / f"{MOORING}_2941_stage2.nc"
        self._got = xr.open_dataset(out_nc)
        self._ref = xr.open_dataset(MICROCAT_S2)
        yield
        self._got.close()
        self._ref.close()

    def test_time_records_trimmed(self):
        assert self._got.sizes["time"] == self._ref.sizes["time"]

    def test_record_count(self):
        assert self._got.sizes["time"] == 19635

    def test_temperature_matches_fixture(self):
        np.testing.assert_array_equal(
            self._got["temperature"].values,
            self._ref["temperature"].values,
        )

    def test_conductivity_matches_fixture(self):
        np.testing.assert_array_equal(
            self._got["conductivity"].values,
            self._ref["conductivity"].values,
        )

    def test_clock_offset_written(self):
        assert "clock_offset" in self._got.data_vars

    def test_processing_level_attr(self):
        assert self._got.attrs.get("processing_level") == "L1"

    def test_stage1_provenance_attrs_present(self):
        assert "stage1_time_start" in self._got.attrs
        assert "stage1_time_end" in self._got.attrs


class TestStage2Aquadopp:
    """Stage2 output for aquadopp (serial 9920) matches committed fixture."""

    @pytest.fixture(autouse=True)
    def run(self, proc_root_stage1):
        proc = Stage2Processor(proc_dir=str(proc_root_stage1))
        ok = proc.process_mooring(MOORING, force=True)
        assert ok, "Stage2Processor.process_mooring returned False"

        out_nc = proc_root_stage1 / MOORING / "aquadopp" / f"{MOORING}_9920_stage2.nc"
        self._got = xr.open_dataset(out_nc)
        self._ref = xr.open_dataset(AQUADOPP_S2)
        yield
        self._got.close()
        self._ref.close()

    def test_time_records_trimmed(self):
        assert self._got.sizes["time"] == self._ref.sizes["time"]

    def test_record_count(self):
        assert self._got.sizes["time"] == 9818

    def test_beam_dimension_preserved(self):
        assert self._got.sizes.get("beam") == 3

    def test_velocity_beam1_matches_fixture(self):
        np.testing.assert_array_equal(
            self._got["velocity_beam1"].values,
            self._ref["velocity_beam1"].values,
        )

    def test_clock_drift_seconds_written(self):
        assert "clock_drift_seconds" in self._got.data_vars

    def test_processing_level_attr(self):
        assert self._got.attrs.get("processing_level") == "L1"
