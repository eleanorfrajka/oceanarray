"""The pure ``stage2(datasets, mooring)`` reproduces the committed stage-2 fixtures.

Locks byte-identity of the clock-correction + trimming science: reading the committed
stage-1 fixtures and running the pure ``stage2`` must yield, value-for-value, the
committed stage-2 fixtures (attributes may differ only by the stamping contract).
No seasenselib needed — this reads stage files, it does not parse raw instruments.
"""

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.pipeline.stage2 import stage2

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


def test_stage2_reproduces_committed_fixtures():
    """stage2(read_mooring(stage-1 fixtures)) equals the committed stage-2 fixtures, value-for-value."""
    cfg = _mooring.load(_YAML)
    datasets = read_mooring(cfg, stage=1, mooring_dir=_FIX)
    results = stage2(datasets, cfg)

    assert sorted(results) == ["2941", "9920"]
    for serial, produced in results.items():
        committed = xr.open_dataset(
            _FIX / _INSTR[serial] / f"dune2_1_2026_{serial}_stage2.nc",
            engine="netcdf4",
            decode_timedelta=False,
        )
        diffs = _values_identical(produced, committed)
        committed.close()
        assert not diffs, f"{serial} stage-2 values drifted: {diffs}"


def test_stage2_stamps_the_stage_marker():
    """Every stage-2 dataset carries processing_stage = 2."""
    cfg = _mooring.load(_YAML)
    results = stage2(read_mooring(cfg, stage=1, mooring_dir=_FIX), cfg)
    assert all(ds.attrs["processing_stage"] == 2 for ds in results.values())


def test_stage2_input_wrong_stage_would_not_be_stage1():
    """read_mooring(stage=1) marks its datasets stage 1 — the precondition stage2 assumes."""
    cfg = _mooring.load(_YAML)
    datasets = read_mooring(cfg, stage=1, mooring_dir=_FIX)
    assert all(ds.attrs["processing_stage"] == 1 for ds in datasets.values())


@pytest.mark.filterwarnings("ignore")
def test_clock_recommended_window_preserved():
    """The aquadopp recommended clock window (−55 s drift) is reproduced exactly."""
    cfg = _mooring.load(_YAML)
    results = stage2(read_mooring(cfg, stage=1, mooring_dir=_FIX), cfg)
    aq = results["9920"]
    assert aq.attrs["suggested_deployment_time_utc"] == "2026-07-11T20:30:00"
    assert aq.attrs["suggested_recovery_time_utc"] == "2026-07-25T11:43:05"


def _proc_copy(tmp_path: Path) -> Path:
    """Copy the dune2 fixture mooring (stage 1 and 2 only) into a fresh proc root."""
    import shutil

    shutil.copytree(
        _FIX,
        tmp_path / "dune2_1_2026",
        ignore=shutil.ignore_patterns("*_stage3.nc", "*_stack.nc", "*_grid.nc"),
    )
    return tmp_path


@pytest.mark.filterwarnings("ignore")
def test_stage2_processor_writes_the_instruments_that_succeed(tmp_path):
    """One unreadable stage-1 file is logged; the other instrument is still written."""
    from oceanarray.pipeline.stage2 import Stage2Processor

    root = _proc_copy(tmp_path)
    moor = root / "dune2_1_2026"
    (moor / "microcat" / "dune2_1_2026_2941_stage1.nc").write_bytes(b"not netcdf")
    for p in moor.glob("*/*_stage2.nc"):
        p.unlink()

    assert Stage2Processor(proc_dir=str(root)).process_mooring(
        "dune2_1_2026", force=True
    )
    assert (moor / "aquadopp" / "dune2_1_2026_9920_stage2.nc").exists()
    assert not (moor / "microcat" / "dune2_1_2026_2941_stage2.nc").exists()


@pytest.mark.filterwarnings("ignore")
def test_stage2_processor_skips_existing_outputs_without_force(tmp_path):
    """Existing stage-2 files are left alone (and count as success) unless force is given."""
    from oceanarray.pipeline.stage2 import Stage2Processor

    root = _proc_copy(tmp_path)
    outputs = sorted((root / "dune2_1_2026").glob("*/*_stage2.nc"))
    before = [p.read_bytes() for p in outputs]

    assert Stage2Processor(proc_dir=str(root)).process_mooring("dune2_1_2026")
    assert [p.read_bytes() for p in outputs] == before


@pytest.mark.filterwarnings("ignore")
def test_stage2_processor_logs_to_console_and_stage_log(tmp_path, capsys, monkeypatch):
    """The pure function's messages reach stdout and the stage log, as before the carve."""
    monkeypatch.setattr("oceanarray.logger.LOGGING_ENABLED", True)
    from oceanarray.pipeline.stage2 import Stage2Processor

    root = _proc_copy(tmp_path)
    assert Stage2Processor(proc_dir=str(root)).process_mooring(
        "dune2_1_2026", force=True
    )
    assert "Final time range" in capsys.readouterr().out
    logs = list((root / "dune2_1_2026").rglob("*stage2*.log"))
    assert logs, "no stage-2 log written"
    assert "Final time range" in logs[0].read_text()


def test_stage2_refuses_a_stage1_file_with_missing_times():
    """A stage-1 file holding NaT (written before the readers dropped them) is refused."""
    from oceanarray.exceptions import MooringError

    cfg = _mooring.load(_YAML)
    ds = read_mooring(cfg, stage=1, serials=["2941"], mooring_dir=_FIX)["2941"].load()
    time = ds["time"].values.copy()
    time[-3:] = np.datetime64("NaT", "ns")
    ds = ds.assign_coords(time=time)
    with pytest.raises(MooringError, match="2941: 3 record"):
        stage2({"2941": ds}, cfg)
