"""The pure ``stage3(datasets, mooring)`` reproduces the committed stage-3 fixtures.

Locks the pressure-interpolation, QC and coordinate-rotation science: reading the
committed stage-2 fixtures and running the pure ``stage3`` must yield, value-for-value,
the committed stage-3 fixtures. Also covers the stage-marker guard and the
``mooring_name`` identity check ``read_mooring`` applies to stage files.
"""

import logging
import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray.exceptions import MooringError, StageError
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.pipeline.stage3 import stage3
from oceanarray.writers import write

_FIX = Path(__file__).parent.parent / "fixtures" / "proc" / "dune2_1_2026"
_YAML = _FIX / "dune2_1_2026.mooring.yaml"
_INSTR = {"2941": "microcat", "9920": "aquadopp"}


def _values_identical(a: xr.Dataset, b: xr.Dataset) -> list[str]:
    """Return the names of data variables whose values differ (NaN-aware, exact)."""
    diffs = []
    for v in sorted(set(a.data_vars) | set(b.data_vars)):
        if v not in a.data_vars or v not in b.data_vars:
            diffs.append(f"{v}: present in only one")
            continue
        av, bv = a[v].values, b[v].values
        if av.shape != bv.shape:
            diffs.append(f"{v}: shape {av.shape} vs {bv.shape}")
        elif not np.array_equal(av, bv, equal_nan=av.dtype.kind == "f"):
            diffs.append(f"{v}: values differ")
    return diffs


@pytest.fixture(scope="module")
def results() -> dict[str, xr.Dataset]:
    """Run the pure stage 3 once on the committed stage-2 fixtures."""
    cfg = _mooring.load(_YAML)
    return stage3(read_mooring(cfg, stage=2, mooring_dir=_FIX), cfg)


def test_stage3_reproduces_committed_fixtures(results, tmp_path):
    """stage3(read_mooring(stage-2 fixtures)), as written, equals the committed stage-3 fixtures.

    Compared after a write/read round trip: the writer stores derived float64
    variables (interpolated pressure, salinity) as float32.
    """
    assert sorted(results) == ["2941", "9920"]
    for serial, ds in results.items():
        path = write(ds, tmp_path / f"{serial}_stage3.nc")
        produced = xr.open_dataset(path, engine="netcdf4", decode_timedelta=False)
        committed = xr.open_dataset(
            _FIX / _INSTR[serial] / f"dune2_1_2026_{serial}_stage3.nc",
            engine="netcdf4",
            decode_timedelta=False,
        )
        diffs = _values_identical(produced, committed)
        produced.close()
        committed.close()
        assert not diffs, f"{serial} stage-3 values drifted: {diffs}"


def test_stage3_stamps_the_stage_marker(results):
    """Every stage-3 dataset carries processing_stage = 3."""
    assert all(ds.attrs["processing_stage"] == 3 for ds in results.values())


def test_stage3_interpolates_pressure_onto_the_sensorless_microcat(results):
    """The microcat has no pressure sensor: its pressure is interpolated (flag 8)."""
    flags = np.unique(results["2941"]["pressure_qc"].values)
    assert flags.tolist() == [8]


def test_stage3_refuses_stage1_datasets():
    """stage3 given stage-1 datasets raises StageError naming stage 2."""
    cfg = _mooring.load(_YAML)
    datasets = read_mooring(cfg, stage=1, mooring_dir=_FIX)
    with pytest.raises(StageError, match="run stage 2 first"):
        stage3(datasets, cfg)


def _copy_with_mooring_name(tmp_path: Path, value: str) -> Path:
    """Copy the fixture mooring with the microcat stage-2 file's mooring_name set to *value*."""
    dest = tmp_path / "dune2_1_2026"
    shutil.copytree(_FIX, dest, ignore=shutil.ignore_patterns("*_stage3.nc"))
    path = dest / "microcat" / "dune2_1_2026_2941_stage2.nc"
    with xr.open_dataset(path, engine="netcdf4", decode_timedelta=False) as ds:
        ds = ds.load()
    ds.attrs["mooring_name"] = value
    ds.to_netcdf(path)
    return dest


def test_read_mooring_corrects_case_only_mooring_name(tmp_path):
    """A mooring_name differing only by case is corrected, with a history line."""
    dest = _copy_with_mooring_name(tmp_path, "Dune2_1_2026")
    cfg = _mooring.load(dest / "dune2_1_2026.mooring.yaml")
    ds = read_mooring(cfg, stage=2, serials=["2941"], mooring_dir=dest)["2941"]
    assert ds.attrs["mooring_name"] == "dune2_1_2026"
    assert "mooring_name corrected from 'Dune2_1_2026'" in ds.attrs["history"]


def test_read_mooring_refuses_a_different_mooring_name(tmp_path):
    """A mooring_name differing by more than case raises, naming both."""
    dest = _copy_with_mooring_name(tmp_path, "dune2")
    cfg = _mooring.load(dest / "dune2_1_2026.mooring.yaml")
    with pytest.raises(MooringError, match=r"'dune2'.*'dune2_1_2026'"):
        read_mooring(cfg, stage=2, serials=["2941"], mooring_dir=dest)


def test_stage3_leaves_its_input_datasets_unchanged():
    """stage3 works on copies: the caller's stage-2 datasets keep their variables and values."""
    cfg = _mooring.load(_YAML)
    datasets = read_mooring(cfg, stage=2, mooring_dir=_FIX)
    before = {s: ds.copy(deep=True) for s, ds in datasets.items()}
    stage3(datasets, cfg)
    for serial, ds in datasets.items():
        assert set(ds.data_vars) == set(before[serial].data_vars), serial
        assert ds.attrs == before[serial].attrs, serial
        assert _values_identical(ds, before[serial]) == [], serial


def test_stage3_processor_logs_to_console_and_stage_log(tmp_path, caplog):
    """The pure function's messages reach the logger and the stage log, as before the carve."""
    from oceanarray.pipeline.stage3 import Stage3Processor

    shutil.copytree(
        _FIX,
        tmp_path / "dune2_1_2026",
        ignore=shutil.ignore_patterns("*_stack.nc", "*_grid.nc"),
    )
    with caplog.at_level(logging.INFO, logger="oceanarray"):
        assert Stage3Processor(proc_dir=str(tmp_path)).process_mooring(
            "dune2_1_2026", force=True
        )
    assert "Pressure sources (1): aquadopp 9920" in caplog.text
    logs = list((tmp_path / "dune2_1_2026").rglob("*stage3*.log"))
    assert logs, "no stage-3 log written"
    assert "Pressure sources (1): aquadopp 9920" in logs[0].read_text()


def test_stage3_processor_names_files_from_the_description(tmp_path):
    """A mooring directory named differently from name: still finds and writes the right files."""
    from oceanarray.pipeline.stage3 import Stage3Processor

    moor = tmp_path / "dune2_copy"
    shutil.copytree(_FIX, moor, ignore=shutil.ignore_patterns("*_stage3.nc"))
    (moor / "dune2_1_2026.mooring.yaml").rename(moor / "dune2_copy.mooring.yaml")
    assert Stage3Processor(proc_dir=str(tmp_path)).process_mooring("dune2_copy")
    assert (moor / "microcat" / "dune2_1_2026_2941_stage3.nc").exists()
    assert (moor / "aquadopp" / "dune2_1_2026_9920_stage3.nc").exists()


@pytest.fixture(scope="module")
def no_position(tmp_path_factory) -> tuple[dict, dict[str, xr.Dataset]]:
    """Stage 3 on the committed stage-2 fixtures with the mooring position removed."""
    moor = tmp_path_factory.mktemp("no_position") / "dune2_1_2026"
    shutil.copytree(_FIX, moor, ignore=shutil.ignore_patterns("*_stage3.nc", "*.nc.*"))
    cfg = _mooring.load(moor / _YAML.name)
    for key in ("latitude", "longitude"):
        cfg.pop(key, None)
    datasets = {
        s: ds.load() for s, ds in read_mooring(cfg, stage=2, mooring_dir=moor).items()
    }
    return cfg, stage3(datasets, cfg)


def test_stage3_without_position_records_declination_not_applied(no_position):
    """A BEAM/XYZ Aquadopp with no mooring position runs; history says why."""
    _, out = no_position
    assert out["9920"].attrs["magnetic_declination"] == "UNK"
    assert "declination not applied (position unknown)" in out["9920"].attrs["history"]
    assert "ppigrf IGRF" not in out["9920"].attrs["history"]


def test_stack_without_position_names_the_unknown_declination(no_position):
    """The stack builds; the unknown level's declination is NaN and named."""
    from oceanarray.pipeline.stack import stack

    cfg, out = no_position
    ds = stack(out, cfg)
    decl = ds["magnetic_declination"]
    assert decl.dims == ("N_LEVELS",)
    level = [str(s) for s in ds["serial"].values].index("9920")
    assert np.isnan(decl.values[level])
    assert "aquadopp s/n 9920" in decl.attrs["comment"]
    assert "microcat" not in decl.attrs["comment"]


def test_instrument_report_warns_when_declination_unknown(no_position, tmp_path):
    """The no-position aquadopp's instrument page shows the declination-not-applied banner.

    Regression: stage 3 writes ``magnetic_declination="UNK"``; the report's warning keys
    on that value, not merely on the attribute being present, so the banner must still show.
    """
    import yaml

    from oceanarray import report, write_mooring

    cfg, out = no_position
    mdir = tmp_path / "dune2_1_2026"
    write_mooring(out, mdir)
    (mdir / "dune2_1_2026.mooring.yaml").write_text(
        yaml.safe_dump(cfg), encoding="utf-8"
    )
    report(mdir, instruments=True, stack=False, grid=False)
    html = (mdir / "report" / "instrument" / "dune2_1_2026_9920_report.html").read_text(
        encoding="utf-8"
    )
    assert (
        "Magnetic declination was not applied (no mooring position at stage 3)" in html
    )


def test_stage3_native_enu_without_position_records_declination_not_applied():
    """A native-ENU instrument with no position records the note too (no BEAM→ENU rotation).

    The history clause used to be reached only inside the BEAM→ENU block, so a native-ENU
    instrument got `magnetic_declination="UNK"` with no matching history line.
    """
    import pandas as pd

    t = pd.date_range("2020-01-01", periods=50, freq="h").values
    ds = xr.Dataset(
        {
            "east_velocity": ("time", np.full(50, 0.1), {"units": "m s-1"}),
            "north_velocity": ("time", np.full(50, 0.05), {"units": "m s-1"}),
            "up_velocity": ("time", np.zeros(50), {"units": "m s-1"}),
            "pressure": ("time", np.linspace(100, 101, 50), {"units": "dbar"}),
            "temperature": ("time", np.full(50, 5.0), {"units": "degree_Celsius"}),
        },
        coords={"time": t},
        attrs={
            "coordinate_system": "ENU",
            "instrument": "aquadopp",
            "serial": "X1",
            "mooring_name": "m",
            "processing_stage": 2,
        },
    )
    cfg = {
        "name": "m",
        "waterdepth": 1000,
        "clamp": [{"serial": "X1", "instrument": "aquadopp", "hab": 500}],
    }
    out = stage3({"X1": ds}, cfg)["X1"]
    assert out.attrs["magnetic_declination"] == "UNK"
    assert "declination not applied (position unknown)" in out.attrs["history"]
    assert "BEAM→ENU rotation applied" not in out.attrs["history"]
