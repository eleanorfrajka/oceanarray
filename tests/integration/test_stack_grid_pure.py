"""The pure ``stack(datasets, mooring)`` and ``grid(ds_stack)`` on the dune2 stage-3 fixtures.

Builds the stack from the committed two-instrument stage-3 fixtures and checks its
layout and values against those inputs: the time axis is the deployment window on
whole steps, the levels run deepest first, and each level's values are the
instrument's own samples at the nearest stack time. The grid is checked against ``numpy.interp`` of the stack
at sampled time steps.
"""

import copy
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray.config import parameters as params
from oceanarray.exceptions import MooringError
from oceanarray.pipeline.grid import grid
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.pipeline.stack import stack

_FIX = Path(__file__).parent.parent / "fixtures" / "proc" / "dune2_1_2026"
_YAML = _FIX / "dune2_1_2026.mooring.yaml"
_DT = 3600


@pytest.fixture(scope="module")
def cfg() -> dict:
    """The dune2 mooring description."""
    return _mooring.load(_YAML)


@pytest.fixture(scope="module")
def stage3(cfg) -> dict[str, xr.Dataset]:
    """The committed stage-3 datasets, loaded."""
    return {
        s: ds.load() for s, ds in read_mooring(cfg, stage=3, mooring_dir=_FIX).items()
    }


@pytest.fixture(scope="module")
def ds_stack(stage3, cfg) -> xr.Dataset:
    """The stack of the two stage-3 datasets on an hourly axis."""
    return stack(stage3, cfg, dt_seconds=_DT)


def test_stack_time_axis_is_the_deployment_window(ds_stack, cfg):
    """The time axis runs through the deployment window on whole steps of dt_seconds."""
    t = ds_stack["time"].values
    deploy = np.datetime64(cfg["deployment_time"], "ns")  # 20:30
    assert t[0] == np.datetime64("2026-07-11T21:00", "ns")  # rounded up to the hour
    assert deploy <= t[0] < deploy + np.timedelta64(_DT, "s")
    assert np.all(np.diff(t) == np.timedelta64(_DT, "s"))
    assert t[-1] <= np.datetime64(cfg["recovery_time"], "ns")


def test_stack_levels_run_deepest_first(ds_stack):
    """Levels are ordered by height above bottom: the aquadopp (226 m) below the microcat."""
    assert ds_stack["serial"].values.tolist() == ["9920", "2941"]
    assert np.all(np.diff(ds_stack["hab"].values) > 0)
    assert ds_stack.attrs["mooring_name"] == "dune2_1_2026"


def test_stack_level_is_nearest_sample_of_the_instrument(ds_stack, stage3):
    """The microcat level's temperature is its own sample nearest each stack time."""
    src = stage3["2941"]
    level = ds_stack["temperature"].sel(N_LEVELS=1).values
    src_t = src["time"].values
    for k in (0, 50, 200, len(level) - 1):
        t = ds_stack["time"].values[k]
        j = int(np.argmin(np.abs(src_t - t)))
        if abs(src_t[j] - t) <= np.timedelta64(_DT // 2, "s"):
            assert level[k] == pytest.approx(float(src["temperature"].values[j]))


def test_stack_excludes_instruments_marked_stack_false(stage3, cfg):
    """An instrument with stack: false is left out and named in instruments_excluded."""
    cfg2 = copy.deepcopy(cfg)
    for entry in cfg2["clamp"]:
        if str(entry.get("serial")).startswith("9920"):
            entry["stack"] = False
    ds = stack(stage3, cfg2, dt_seconds=_DT)
    assert ds["serial"].values.tolist() == ["2941"]
    assert "9920" in ds.attrs["instruments_excluded"]


def test_stack_refuses_without_a_deployment_window(stage3, cfg):
    """No deployment window in the description raises MooringError."""
    cfg2 = {k: v for k, v in cfg.items() if k != "recovery_time"}
    with pytest.raises(MooringError, match="deployment_time"):
        stack(stage3, cfg2, dt_seconds=_DT)


def test_grid_is_linear_interpolation_in_pressure(ds_stack):
    """Each gridded column is numpy.interp of the stack column, with no extrapolation."""
    ds_grid = grid(ds_stack, pmin=100, pmax=800, dp=50)
    p_grid = ds_grid["pressure"].values
    for k in (0, 100, 300):
        p = ds_stack["pressure"].values[k, :]
        v = ds_stack["temperature"].values[k, :]
        ok = np.isfinite(p) & np.isfinite(v)
        if ok.sum() < 2:
            continue
        order = np.argsort(p[ok])
        expected = np.interp(
            p_grid, p[ok][order], v[ok][order], left=np.nan, right=np.nan
        )
        np.testing.assert_allclose(
            ds_grid["temperature"].values[k, :], expected, equal_nan=True
        )


def test_grid_refuses_a_stack_without_pressure(ds_stack):
    """A stack with no pressure variable cannot be gridded."""
    with pytest.raises(ValueError, match="pressure"):
        grid(ds_stack.drop_vars("pressure"))


def test_stacker_records_an_unreadable_file_as_omitted(tmp_path):
    """A stage file that cannot be read is named in instruments_omitted and the run reports failure."""
    import shutil

    from oceanarray.pipeline.stack import MooringStacker

    moor = tmp_path / "dune2_1_2026"
    shutil.copytree(
        _FIX, moor, ignore=shutil.ignore_patterns("*_stack.nc", "*_grid.nc")
    )
    (moor / "microcat" / "dune2_1_2026_2941_stage3.nc").write_bytes(b"truncated")

    ok = MooringStacker(proc_dir=str(tmp_path)).stack(
        "dune2_1_2026", dt_seconds=_DT, force=True
    )
    assert ok is False
    with xr.open_dataset(moor / "dune2_1_2026_stack.nc") as ds:
        assert "2941" in ds.attrs["instruments_omitted"]
        assert ds["serial"].values.tolist() == ["9920"]


def test_stack_output_metadata(ds_stack):
    """Serials stay text, depths carry units, positions are numbers, times ISO 8601."""
    serial = ds_stack["instrument_serial"]
    assert serial.dtype.kind in "OU"
    assert serial.values.tolist() == ["9920*", "2941"]
    assert ds_stack["instrument_depth"].values.tolist() == [745 - 226, 745 - 425]
    assert ds_stack["instrument_depth"].attrs["units"] == "m"
    assert ds_stack.attrs["latitude"] == pytest.approx(65 + 43.913 / 60)
    assert ds_stack.attrs["longitude"] == pytest.approx(-27.8)
    assert ds_stack.attrs["waterdepth"] == 745.0
    assert ds_stack.attrs["deployment_time"] == "2026-07-11T20:30:00Z"
    assert ds_stack["sigma0"].attrs["standard_name"] == "sea_water_sigma_theta"
    assert ds_stack["current_direction"].attrs["units"] == "degree"


def test_stack_accepts_stage_files_before_0_6_1(stage3, cfg):
    """A stage file with InstrDepth and an integer serial_number stacks under the new names."""
    old = {}
    for s, ds in stage3.items():
        renamed = ds.rename(instrument_depth="InstrDepth")
        old[s] = renamed.drop_vars("instrument_serial").assign(
            serial_number=int(s.rstrip("*"))
        )
    out = stack(old, cfg, dt_seconds=_DT)
    assert "InstrDepth" not in out and "serial_number" not in out
    assert out["instrument_serial"].values.tolist() == ["9920", "2941"]
    assert out["instrument_depth"].attrs["long_name"] == "nominal instrument depth"


@pytest.mark.parametrize(
    ("ref", "name", "entry"),
    [(0, "sigma0", "potential_density"), (2000, "sigma2", "sigma2")],
)
def test_stack_sigma_attributes_come_from_the_registry(stage3, cfg, ref, name, entry):
    """σ0 is the registry's potential_density (sea_water_sigma_theta); σ2 has no standard name."""
    out = stack(stage3, {**cfg, "density_reference": ref}, dt_seconds=_DT)
    assert out[name].attrs == {**params.cf_attrs(entry), "reference_pressure_dbar": ref}


def test_stack_omits_an_adcp_without_orientation(stage3, cfg):
    """No orientation is assumed: the ADCP is left out, with the reason."""
    adcp = xr.open_dataset(
        _FIX.parents[1] / "adcp" / "dsG2_adcp_stage3_knockdown.nc"
    ).load()
    del adcp.attrs["orientation_yaml"], adcp.attrs["orientation_instrument"]
    cfg2 = copy.deepcopy(cfg)
    cfg2["clamp"].append({"serial": 16430, "instrument": "ADCP", "hab": 400})
    ds = stack({**stage3, "16430": adcp}, cfg2, dt_seconds=_DT)
    assert "ADCP s/n 16430 (ADCP orientation None" in ds.attrs["instruments_omitted"]
    assert not any(str(s).startswith("16430") for s in ds["serial"].values)
