"""``oceanarray.process(mooring)`` runs stages 1–3 on the dune2 fixtures and writes their files.

Builds a fresh proc tree holding only the description, runs ``process`` against the
committed raw files, and checks the written stage files against the committed
fixtures value for value. Also covers the stage selection, the skip-if-exists
default, and the failures that now raise ``MooringError``.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

import oceanarray as oa
from oceanarray import paths
from oceanarray.exceptions import MooringError

pytest.importorskip("seasenselib", reason="seasenselib not installed")

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_RAW = _FIXTURES / "raw"
_PROC = _FIXTURES / "proc" / "dune2_1_2026"
_NAME = "dune2_1_2026"
_INSTR = {"2941": "microcat", "9920": "aquadopp"}


def _rel(serial: str, stage: int) -> Path:
    """Return an instrument's stage file relative to the mooring directory."""
    return paths.stage_file(Path(), _NAME, _INSTR[serial], serial, stage)


def _values_differ(a: Path, b: Path) -> list[str]:
    """Return the variables whose values differ between two netCDF files (NaN-aware, exact)."""
    with (
        xr.open_dataset(a, decode_times=False) as x,
        xr.open_dataset(b, decode_times=False) as y,
    ):
        diffs = []
        for v in sorted(set(x.variables) | set(y.variables)):
            if v not in x.variables or v not in y.variables:
                diffs.append(f"{v}: present in only one")
                continue
            xv, yv = x[v].values, y[v].values
            if xv.shape != yv.shape or not np.array_equal(
                xv, yv, equal_nan=xv.dtype.kind == "f"
            ):
                diffs.append(f"{v}: values differ")
        return diffs


def _fresh_tree(tmp_path: Path) -> Path:
    """Return the path of a description in a proc tree that holds nothing else."""
    moor = tmp_path / "proc" / _NAME
    moor.mkdir(parents=True)
    shutil.copy(_PROC / f"{_NAME}.mooring.yaml", moor)
    return moor / f"{_NAME}.mooring.yaml"


@pytest.fixture(scope="module")
def processed(tmp_path_factory) -> tuple[Path, list[Path]]:
    """Run process once over a fresh tree; return the description path and the files written."""
    yaml_path = _fresh_tree(tmp_path_factory.mktemp("process"))
    return yaml_path, oa.process(yaml_path, raw_dir=_RAW)


def test_process_writes_every_stage_file_in_stage_order(processed):
    """Two instruments, three stages: six files, all stage 1 before stage 2 before stage 3."""
    _, written = processed
    assert [p.name for p in written] == [
        _rel(s, n).name for n in (1, 2, 3) for s in ("2941", "9920")
    ]
    assert all(p.exists() for p in written)


@pytest.mark.parametrize("stage", [1, 2, 3])
@pytest.mark.parametrize("serial", ["2941", "9920"])
def test_process_reproduces_the_committed_fixtures(processed, stage, serial):
    """Each written stage file holds the committed fixture's values."""
    yaml_path, _ = processed
    rel = _rel(serial, stage)
    assert _values_differ(yaml_path.parent / rel, _PROC / rel) == []


def test_process_writes_a_log_per_stage(processed):
    """Each stage leaves its own log under the mooring directory."""
    yaml_path, _ = processed
    logs = {p.name.rsplit("_", 1)[-1] for p in yaml_path.parent.rglob("*.log")}
    assert {"stage1.log", "stage2.log", "stage3.log"} <= logs


def test_process_keeps_existing_files_and_returns_them(processed, capsys):
    """A second run rewrites nothing but still returns every file, and says so."""
    yaml_path, written = processed
    before = {p: p.stat().st_mtime_ns for p in written}
    assert oa.process(yaml_path, raw_dir=_RAW) == written
    assert {p: p.stat().st_mtime_ns for p in written} == before
    out = capsys.readouterr().out
    assert "Stage 3: 0 file(s) written, 2 kept" in out


def test_process_force_rewrites_the_serial_asked_for(own_tree, capsys):
    """force with serials rewrites that instrument's file and returns only it."""
    path = own_tree.parent / _rel("2941", 2)
    mtime = path.stat().st_mtime_ns
    assert oa.process(own_tree, stages=2, serials=["2941"], force=True) == [path]
    assert path.stat().st_mtime_ns != mtime
    assert "Stage 2: 1 file(s) written, 0 kept" in capsys.readouterr().out


def test_process_takes_a_loaded_description_with_its_directory(processed):
    """A dict needs mooring_dir=; given it, the run finds the files."""
    yaml_path, _ = processed
    cfg = oa.mooring.load(yaml_path)
    with pytest.raises(MooringError, match="mooring_dir="):
        oa.process(cfg, stages=3)
    files = oa.process(cfg, stages=3, mooring_dir=yaml_path.parent)
    assert [p.name for p in files] == [_rel(s, 3).name for s in ("2941", "9920")]


@pytest.mark.parametrize("stages", [4, 0, "stack", (), (1, "grid"), True])
def test_process_refuses_anything_but_stages_1_to_3(processed, stages):
    """Stack and grid are not process's stages; nor is any other number."""
    yaml_path, _ = processed
    with pytest.raises(ValueError, match="expected 1, 2, 3"):
        oa.process(yaml_path, stages=stages)


def test_process_raises_naming_the_instrument_whose_raw_file_is_missing(tmp_path):
    """One missing raw file: the other instrument is written, then MooringError names the missing one."""
    raw = tmp_path / "raw"
    shutil.copytree(_RAW / _NAME, raw / _NAME)
    for f in (raw / _NAME / "aquadopp").iterdir():
        f.unlink()
    yaml_path = _fresh_tree(tmp_path)
    with pytest.raises(MooringError, match=r"(?s)stage 1 failed for 1 of 2.*9920"):
        oa.process(yaml_path, raw_dir=raw)
    moor = yaml_path.parent
    assert (moor / _rel("2941", 1)).exists()
    assert not list(moor.rglob(paths.stage_glob(2))), "stage 2 ran after stage 1 failed"


def test_process_raises_when_no_serial_matches(processed):
    """A serials filter that matches nothing is an error, not an empty success."""
    yaml_path, _ = processed
    with pytest.raises(MooringError, match="no instrument matching 0000"):
        oa.process(yaml_path, stages=2, serials=["0000"])


def test_process_raises_without_a_raw_directory(tmp_path):
    """Stage 1 with no raw_dir (and no absolute directory: in the description) raises."""
    with pytest.raises(MooringError, match="raw_dir="):
        oa.process(_fresh_tree(tmp_path), stages=1)


def test_process_keeps_the_name_and_stage_form_for_one_release(processed, tmp_path):
    """process(name, stage, proc_dir=…) still runs the old way and returns a bool."""
    yaml_path, _ = processed
    shutil.copytree(yaml_path.parent, tmp_path / _NAME)
    with pytest.warns(DeprecationWarning, match=r"removed in 0\.7\.0.*oceanarray\.run"):
        assert oa.process(_NAME, 2, proc_dir=tmp_path) is True


def test_process_turns_a_stage3_failure_into_a_logged_mooring_error(
    processed, tmp_path, monkeypatch
):
    """An error inside the stage-3 transform raises MooringError and is written to the stage log."""
    import oceanarray.pipeline.stage3 as st3

    def _boom(*_a, **_k):
        raise ValueError("boom")

    monkeypatch.setattr(st3, "stage3", _boom)
    yaml_path, _ = processed
    moor = tmp_path / _NAME
    shutil.copytree(yaml_path.parent, moor)
    with pytest.raises(MooringError, match="stage 3 failed.*boom"):
        oa.process(moor / yaml_path.name, stages=3, force=True)
    logs = list(moor.rglob("*stage3.log"))
    assert any("ERROR: Stage 3 failed: boom" in p.read_text() for p in logs)


def test_process_restores_the_reader_loggers(tmp_path):
    """Stage 1 under process leaves the seasenselib/pycnv loggers as it found them."""
    import logging

    lib = logging.getLogger("seasenselib")
    before = (lib.level, lib.propagate, list(lib.handlers))
    oa.process(_fresh_tree(tmp_path), stages=1, raw_dir=_RAW)
    assert (lib.level, lib.propagate, list(lib.handlers)) == before


def test_stage3_dry_run_with_no_stage2_files_still_fails(tmp_path):
    """The class's dry run with nothing to read reports the error and returns False, as before."""
    from oceanarray.pipeline.stage3 import Stage3Processor

    _fresh_tree(tmp_path)
    proc = Stage3Processor(proc_dir=str(tmp_path / "proc"))
    assert proc.process_mooring(_NAME, dry_run=True) is False


@pytest.fixture
def own_tree(processed, tmp_path) -> Path:
    """A copy of the processed tree this test may change; returns its description path."""
    yaml_path, _ = processed
    shutil.copytree(yaml_path.parent, tmp_path / _NAME)
    return tmp_path / _NAME / yaml_path.name


def _pressure(path: Path) -> tuple[dict, np.ndarray]:
    """Return a stage file's pressure attributes and QC flags."""
    with xr.open_dataset(path, decode_times=False) as ds:
        return dict(ds["pressure"].attrs), ds["pressure_qc"].values


def test_target_records_the_stage2_files_its_pressure_came_from(processed):
    """The sensorless microcat names the aquadopp's stage-2 file and its hash; the source names none."""
    yaml_path, _ = processed
    moor = yaml_path.parent
    attrs, _ = _pressure(moor / _rel("2941", 3))
    assert attrs["pressure_source_serials"] == "9920"
    assert attrs["pressure_source_files"].startswith(f"{_rel('9920', 2).name} sha256=")
    src_attrs, _ = _pressure(moor / _rel("9920", 3))
    assert "pressure_source_files" not in src_attrs


def test_stage3_on_a_target_alone_still_interpolates_its_pressure(own_tree):
    """serials=[target] reads the whole mooring, so the pressure source is there; only the target is written."""
    written = oa.process(own_tree, stages=3, serials=["2941"], force=True)
    assert [p.name for p in written] == [_rel("2941", 3).name]
    _, qc = _pressure(own_tree.parent / _rel("2941", 3))
    assert set(np.unique(qc)) == {8}


def test_a_changed_source_names_its_kept_targets_as_stale(own_tree, capsys):
    """Rewriting the aquadopp's stage 2 then its stage 3 alone warns that the microcat's pressure is stale."""
    oa.process(own_tree, stages=2, serials=["9920"], force=True)
    capsys.readouterr()
    oa.process(own_tree, stages=3, serials=["9920"], force=True)
    out = capsys.readouterr().out
    assert f"WARNING: {_rel('2941', 3).name}: its pressure was interpolated" in out


def test_no_stale_warning_when_the_sources_are_unchanged(own_tree, capsys):
    """Re-running stage 3 for the source alone, with its stage 2 untouched, warns about nothing."""
    oa.process(own_tree, stages=3, serials=["9920"], force=True)
    assert "its pressure was interpolated" not in capsys.readouterr().out


def _same_as_file(ds: xr.Dataset, path: Path) -> list[str]:
    """Return the variables whose dtype or values differ between *ds* and *path* read back."""
    with xr.open_dataset(path) as back:
        diffs = []
        for v in sorted(set(ds.variables) | set(back.variables)):
            if v not in ds.variables or v not in back.variables:
                diffs.append(f"{v}: present in only one")
                continue
            a, b = ds[v].values, back[v].values
            if a.dtype != b.dtype:
                diffs.append(f"{v}: dtype {a.dtype} vs {b.dtype}")
            elif not np.array_equal(a, b, equal_nan=a.dtype.kind in "fcmM"):
                diffs.append(f"{v}: values differ")
        return diffs


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_a_stage_in_memory_equals_its_file(processed, tmp_path, stage):
    """Each stage returns its storage dtypes, so composing in memory and reading back agree exactly."""
    from oceanarray.pipeline.read_mooring import read_mooring
    from oceanarray.pipeline.stage1 import stage1
    from oceanarray.pipeline.stage2 import stage2
    from oceanarray.pipeline.stage3 import stage3
    from oceanarray.writers import write

    yaml_path, _ = processed
    cfg = oa.mooring.load(yaml_path)
    if stage == 1:
        out = stage1(read_mooring(cfg, raw_dir=_RAW), cfg)
    else:
        step = {2: stage2, 3: stage3}[stage]
        out = step(
            read_mooring(cfg, stage=stage - 1, mooring_dir=yaml_path.parent), cfg
        )
    for serial, ds in out.items():
        path = write(ds, tmp_path / f"{serial}.nc")
        assert _same_as_file(ds, path) == [], serial
