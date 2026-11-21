"""Integration tests for the stage3 → stack → grid pipeline.

Stage3 tests run stage2 then stage3 in sequence within a tmp directory and
assert structural properties of the output.  We do not compare against
committed stage3 fixtures because those were generated from an earlier stage2
run; any code change that alters processing will cause fixture drift, making a
golden comparison brittle.  Structural tests are the right tool here: the
fixture is the reference for stage2 output (tested in test_stage2_pure.py); stage3
output is validated by checking that QC variables exist, flag values are valid,
and ENU rotation produced the expected variables.

Stack and grid tests assert structural properties for the same reason: the
committed stack/grid NC files were generated from the full mooring (~20
instruments); the two-instrument fixture YAML produces a different but valid
output.
"""

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

pytest.importorskip(
    "seasenselib", reason="stage2/grid import seasenselib.writers at module load"
)

from oceanarray.pipeline.grid import MooringGridder
from oceanarray.pipeline.stack import MooringStacker
from oceanarray.pipeline.stage2 import Stage2Processor
from oceanarray.pipeline.stage3 import Stage3Processor

FIXTURE_PROC = Path(__file__).parent.parent / "fixtures" / "proc" / "dune2_1_2026"
MOORING = "dune2_1_2026"


@pytest.fixture
def proc_root_stage2_fresh(proc_root_stage1):
    """Run stage2 on committed stage1 fixtures; return the proc root."""
    proc = Stage2Processor(proc_dir=str(proc_root_stage1))
    ok = proc.process_mooring(MOORING, force=True)
    assert ok, "Stage2Processor.process_mooring returned False"
    return proc_root_stage1


@pytest.fixture
def proc_root_stage3_fresh(proc_root_stage2_fresh):
    """Run stage3 on the freshly-generated stage2 output; return the proc root."""
    proc = Stage3Processor(proc_dir=str(proc_root_stage2_fresh))
    ok = proc.process_mooring(MOORING, force=True)
    assert ok, "Stage3Processor.process_mooring returned False"
    return proc_root_stage2_fresh


class TestStage3Microcat:
    """Stage3 output for microcat (serial 2941) has correct structure and QC flags."""

    @pytest.fixture(autouse=True)
    def ds(self, proc_root_stage3_fresh):
        out_nc = (
            proc_root_stage3_fresh / MOORING / "microcat" / f"{MOORING}_2941_stage3.nc"
        )
        self._ds = xr.open_dataset(out_nc)
        yield
        self._ds.close()

    def test_time_records_unchanged(self):
        assert self._ds.sizes["time"] == 19635

    def test_qc_variables_added(self):
        for var in ("temperature_qc", "conductivity_qc", "salinity_qc", "pressure_qc"):
            assert var in self._ds.data_vars, f"missing {var}"

    def test_salinity_derived(self):
        assert "salinity" in self._ds.data_vars

    def test_pressure_interpolated(self):
        p = self._ds["pressure"].values
        assert not np.all(np.isnan(p)), "pressure is all NaN after interpolation"
        assert np.nanmin(p) > 0, "pressure should be positive dbar"

    def test_temperature_qc_valid_flags(self):
        vals = self._ds["temperature_qc"].values
        assert np.issubdtype(vals.dtype, np.integer)
        assert np.all((vals >= 0) & (vals <= 9))

    def test_processing_level_attr(self):
        assert self._ds.attrs.get("processing_level") == "L1"

    def test_qc_convention_attr(self):
        assert "qc_convention" in self._ds.attrs

    def test_stage2_provenance_attr(self):
        assert "stage2_source_file" in self._ds.attrs


class TestStage3Aquadopp:
    """Stage3 output for aquadopp (serial 9920) has ENU rotation and valid QC flags."""

    @pytest.fixture(autouse=True)
    def ds(self, proc_root_stage3_fresh):
        out_nc = (
            proc_root_stage3_fresh / MOORING / "aquadopp" / f"{MOORING}_9920_stage3.nc"
        )
        self._ds = xr.open_dataset(out_nc)
        yield
        self._ds.close()

    def test_time_records_unchanged(self):
        assert self._ds.sizes["time"] == 9818

    def test_enu_velocity_variables_present(self):
        for var in ("east_velocity", "north_velocity", "up_velocity"):
            assert var in self._ds.data_vars, f"missing ENU variable {var}"

    def test_enu_qc_variables_present(self):
        for var in ("east_velocity_qc", "north_velocity_qc", "up_velocity_qc"):
            assert var in self._ds.data_vars, f"missing {var}"

    def test_enu_qc_valid_flags(self):
        vals = self._ds["east_velocity_qc"].values
        assert np.issubdtype(vals.dtype, np.integer)
        assert np.all((vals >= 0) & (vals <= 9))

    def test_current_speed_and_direction(self):
        assert "current_speed" in self._ds.data_vars
        assert "current_direction" in self._ds.data_vars

    def test_processing_level_attr(self):
        assert self._ds.attrs.get("processing_level") == "L1"


class TestStackStructural:
    """Stack output from the two-instrument fixture has correct structure."""

    @pytest.fixture(autouse=True)
    def ds(self, proc_root_stage3_fresh):
        stacker = MooringStacker(proc_dir=str(proc_root_stage3_fresh))
        ok = stacker.stack(MOORING, force=True)
        assert ok, "MooringStacker.stack returned False"
        out_nc = proc_root_stage3_fresh / MOORING / f"{MOORING}_stack.nc"
        self._ds = xr.open_dataset(out_nc)
        self._proc_root = proc_root_stage3_fresh
        yield
        self._ds.close()

    def test_output_file_exists(self):
        assert (self._proc_root / MOORING / f"{MOORING}_stack.nc").exists()

    def test_time_dimension_present(self):
        assert "time" in self._ds.sizes
        assert self._ds.sizes["time"] > 0

    def test_two_levels(self):
        assert self._ds.sizes["N_LEVELS"] == 2

    def test_temperature_present(self):
        assert "temperature" in self._ds.data_vars

    def test_no_all_nan_temperature(self):
        assert not np.all(np.isnan(self._ds["temperature"].values))


class TestGridStructural:
    """Grid output from the two-instrument fixture has correct structure."""

    @pytest.fixture(autouse=True)
    def ds(self, proc_root_stage3_fresh):
        stacker = MooringStacker(proc_dir=str(proc_root_stage3_fresh))
        stacker.stack(MOORING, force=True)
        gridder = MooringGridder(proc_dir=str(proc_root_stage3_fresh))
        ok = gridder.grid(MOORING, p_start=100.0, p_end=600.0, dp=50.0, force=True)
        assert ok, "MooringGridder.grid returned False"
        out_nc = proc_root_stage3_fresh / MOORING / f"{MOORING}_grid.nc"
        self._ds = xr.open_dataset(out_nc)
        self._proc_root = proc_root_stage3_fresh
        yield
        self._ds.close()

    def test_output_file_exists(self):
        assert (self._proc_root / MOORING / f"{MOORING}_grid.nc").exists()

    def test_time_and_pressure_dimensions(self):
        assert "time" in self._ds.sizes
        assert "pressure" in self._ds.sizes

    def test_pressure_axis_in_range(self):
        p = self._ds["pressure"].values
        assert p.min() >= 100.0
        assert p.max() <= 600.0

    def test_pressure_attrs_recorded(self):
        assert "p_start_dbar" in self._ds.attrs
        assert "p_end_dbar" in self._ds.attrs
        assert "dp_dbar" in self._ds.attrs

    def test_temperature_variable_present(self):
        assert "temperature" in self._ds.data_vars
