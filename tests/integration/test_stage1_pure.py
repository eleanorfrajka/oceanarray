"""The pure ``stage1(datasets, mooring)`` and the raw read it takes its input from.

Reading the committed raw dune2 files with ``read_mooring(stage=None)`` and running
``stage1`` must reproduce, value for value, the committed stage-1 fixtures — also
when the description names no files (found by the recovery-file naming convention)
and when the Aquadopp sits in the legacy ``nortek/`` folder.
"""

import copy
import shutil
import warnings
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray.exceptions import MooringError
from oceanarray.pipeline._stage import STAGE_ATTR, StageError
from oceanarray.pipeline.read_mooring import read_mooring

pytest.importorskip("seasenselib", reason="seasenselib not installed")

from oceanarray.pipeline.stage1 import stage1

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_RAW = _FIXTURES / "raw"
_PROC = _FIXTURES / "proc" / "dune2_1_2026"
_YAML = _PROC / "dune2_1_2026.mooring.yaml"
_INSTR = {"2941": "microcat", "9920": "aquadopp"}


def _values_identical(a: xr.Dataset, b: xr.Dataset) -> list[str]:
    """Return the variables whose values differ (NaN-aware, exact)."""
    diffs = []
    for v in sorted(set(a.variables) | set(b.variables)):
        if v not in a.variables or v not in b.variables:
            diffs.append(f"{v}: present in only one")
            continue
        av, bv = a[v].values, b[v].values
        if av.shape != bv.shape:
            diffs.append(f"{v}: shape {av.shape} vs {bv.shape}")
        elif not np.array_equal(av, bv, equal_nan=av.dtype.kind == "f"):
            diffs.append(f"{v}: values differ")
    return diffs


def _assert_matches_fixtures(results: dict[str, xr.Dataset], tmp_path: Path) -> None:
    """Each stage-1 result, written and read back, equals the committed fixture."""
    from oceanarray.writers import write

    assert sorted(results) == ["2941", "9920"]
    for serial, ds in results.items():
        path = write(ds, tmp_path / f"{serial}_stage1.nc")
        with (
            xr.open_dataset(path, decode_times=False) as produced,
            xr.open_dataset(
                _PROC / _INSTR[serial] / f"dune2_1_2026_{serial}_stage1.nc",
                decode_times=False,
            ) as committed,
        ):
            diffs = _values_identical(produced, committed)
            # No variable attribute is lost (the committed fixtures predate
            # ``units_metadata``, which stage 1 now adds).
            diffs += [
                f"{v}: lost attributes {sorted(set(committed[v].attrs) - set(produced[v].attrs))}"
                for v in committed.variables
                if v in produced.variables
                and set(committed[v].attrs) - set(produced[v].attrs)
            ]
        assert not diffs, f"{serial} stage-1 drifted: {diffs}"


def _stage1_from(raw_root: Path, cfg: dict) -> dict[str, xr.Dataset]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return stage1(read_mooring(cfg, stage=None, raw_dir=raw_root), cfg)


def test_stage1_reproduces_committed_fixtures(tmp_path):
    """stage1(read_mooring(raw)) equals the committed stage-1 fixtures, marked stage 1."""
    results = _stage1_from(_RAW, _mooring.load(_YAML))
    assert all(ds.attrs[STAGE_ATTR] == 1 for ds in results.values())
    _assert_matches_fixtures(results, tmp_path)


def test_raw_read_finds_files_by_naming_convention(tmp_path):
    """With no filename, file_type or header in the description, the same files are found."""
    cfg = copy.deepcopy(_mooring.load(_YAML))
    for entry in cfg["clamp"]:
        for key in ("filename", "file_type", "header"):
            entry.pop(key, None)
    results = _stage1_from(_RAW, cfg)
    assert results["2941"].attrs["source_file"] == "2941_recovery.asc"
    assert results["9920"].attrs["file_type"] == "nortek-ascii"
    _assert_matches_fixtures(results, tmp_path / "out")


def test_raw_read_finds_the_legacy_nortek_folder(tmp_path):
    """An Aquadopp file in the legacy nortek/ folder is found, header included."""
    raw = tmp_path / "raw" / "dune2_1_2026"
    shutil.copytree(_RAW / "dune2_1_2026" / "microcat", raw / "microcat")
    shutil.copytree(_RAW / "dune2_1_2026" / "aquadopp", raw / "nortek")
    results = _stage1_from(tmp_path / "raw", _mooring.load(_YAML))
    _assert_matches_fixtures(results, tmp_path)


def test_raw_read_refuses_an_instrument_with_no_file(tmp_path):
    """No filename and nothing matching the convention raises, naming the instrument."""
    cfg = copy.deepcopy(_mooring.load(_YAML))
    cfg["clamp"][0].pop("filename")
    raw = tmp_path / "raw" / "dune2_1_2026"
    shutil.copytree(_RAW / "dune2_1_2026" / "aquadopp", raw / "aquadopp")
    with pytest.raises(MooringError, match="2941"):
        read_mooring(cfg, stage=None, raw_dir=tmp_path / "raw")


def test_stage1_refuses_a_marked_dataset():
    """A dataset already carrying processing_stage is not raw."""
    cfg = _mooring.load(_YAML)
    datasets = read_mooring(cfg, stage=1, mooring_dir=_PROC, serials=["2941"])
    with pytest.raises(StageError, match="stage 1 expects raw datasets"):
        stage1(datasets, cfg)


def test_stage1_leaves_its_input_datasets_unchanged():
    """stage1 works on copies: the raw datasets keep their variables, values and attributes."""
    cfg = _mooring.load(_YAML)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = read_mooring(cfg, stage=None, raw_dir=_RAW)
    before = {s: ds.copy(deep=True) for s, ds in raw.items()}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stage1(raw, cfg)
    for serial, ds in raw.items():
        assert ds.attrs == before[serial].attrs, serial
        assert _values_identical(ds, before[serial]) == [], serial


def _class_tree(tmp_path: Path, folder: str = "dune2_1_2026") -> tuple[Path, Path]:
    """Lay out raw/<folder>/ and proc/<folder>/<folder>.mooring.yaml for MooringProcessor."""
    raw = tmp_path / "raw" / folder
    shutil.copytree(_RAW / "dune2_1_2026", raw)
    proc = tmp_path / "proc" / folder
    proc.mkdir(parents=True)
    shutil.copy(_YAML, proc / f"{folder}.mooring.yaml")
    return raw, proc


@pytest.mark.filterwarnings("ignore")
def test_processor_logs_an_unreadable_raw_file_and_writes_the_rest(tmp_path):
    """A missing Nortek header makes the Aquadopp unreadable; the MicroCAT is still written."""
    from oceanarray.pipeline.stage1 import MooringProcessor

    raw, proc = _class_tree(tmp_path)
    (raw / "aquadopp" / "9920_recovery.hdr").unlink()
    ok = MooringProcessor(
        raw_dir=str(tmp_path / "raw"), proc_dir=str(tmp_path / "proc")
    ).process_mooring("dune2_1_2026", force=True)
    assert ok is True
    assert (proc / "microcat" / "dune2_1_2026_2941_stage1.nc").exists()
    assert not (proc / "aquadopp" / "dune2_1_2026_9920_stage1.nc").exists()


@pytest.mark.filterwarnings("ignore")
def test_processor_finds_raw_files_by_folder_and_names_outputs_from_name(tmp_path):
    """Raw files are looked up under the mooring folder; outputs are named from name:."""
    from oceanarray.pipeline.stage1 import MooringProcessor

    _, proc = _class_tree(tmp_path, folder="dune2_copy")
    assert MooringProcessor(
        raw_dir=str(tmp_path / "raw"), proc_dir=str(tmp_path / "proc")
    ).process_mooring("dune2_copy", force=True)
    assert (proc / "microcat" / "dune2_1_2026_2941_stage1.nc").exists()
    assert (proc / "aquadopp" / "dune2_1_2026_9920_stage1.nc").exists()


def test_raw_read_allow_missing_skips_an_absent_named_file(tmp_path):
    """A filename: that does not exist raises, or is skipped with allow_missing."""
    raw = tmp_path / "raw" / "dune2_1_2026"
    shutil.copytree(_RAW / "dune2_1_2026" / "aquadopp", raw / "aquadopp")
    cfg = _mooring.load(_YAML)
    with pytest.raises(FileNotFoundError, match="2941"):
        read_mooring(cfg, stage=None, raw_dir=tmp_path / "raw")
    with pytest.warns(UserWarning, match="2941"):
        datasets = read_mooring(
            cfg, stage=None, raw_dir=tmp_path / "raw", allow_missing=True
        )
    assert list(datasets) == ["9920"]


def test_stage1_refuses_a_serial_missing_from_the_description():
    """A dataset with no matching entry cannot get its metadata; stage1 raises."""
    cfg = _mooring.load(_YAML)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = read_mooring(cfg, stage=None, raw_dir=_RAW, serials=["2941"])
    with pytest.raises(MooringError, match="'1234'"):
        stage1({"1234": raw["2941"]}, cfg)


def test_stage1_pointing_down_flips_y_and_z():
    """A downward-pointing Aquadopp has XYZ rows 2 and 3 negated, x unchanged."""
    cfg = _mooring.load(_YAML)
    down = copy.deepcopy(cfg)
    for entry in down["clamp"]:
        if str(entry["serial"]).startswith("9920"):
            entry["pointing_down"] = True
    up = _stage1_from(_RAW, cfg)["9920"]
    flipped = _stage1_from(_RAW, down)["9920"]
    assert flipped.attrs["nortek_pointing_down"] == "True"
    np.testing.assert_array_equal(flipped["velocity_x"].values, up["velocity_x"].values)
    np.testing.assert_array_equal(
        flipped["velocity_y"].values, -up["velocity_y"].values
    )
    np.testing.assert_array_equal(
        flipped["velocity_z"].values, -up["velocity_z"].values
    )


def test_stage1_records_records_dropped_for_missing_time(tmp_path):
    """A raw file with fill times: stage 1 writes the count and a history line."""
    from oceanarray import readers
    from oceanarray.writers import write

    osnap = _RAW / "osnap"
    lines = (osnap / "rtwb1_01_2014_001.microcat").read_text().splitlines()
    row = lines[20].split()
    row[3] = "-9999"  # a fill in the HH column
    lines[20] = "  ".join(row)
    path = tmp_path / "rtwb1_01_2014_001.microcat"
    path.write_text("\n".join(lines) + "\n")

    cfg = {
        "name": "rtwb1_01_2014",
        "waterdepth": 1600,
        "latitude": "57 28.240 N",
        "longitude": "012 42.300 W",
        "deployment_time": "2014-07-17T12:00",
        "recovery_time": "2015-06-22T12:00",
        "clamp": [
            {
                "serial": 11335,
                "instrument": "microcat",
                "file_type": "rodb",
                "filename": path.name,
                "hab": 1500,
            }
        ],
    }
    with pytest.warns(UserWarning, match="dropped 1 record"):
        raw = readers._read_typed("rodb", str(path))
    raw.attrs["source_file"] = path.name
    out = stage1({"11335": raw}, cfg)["11335"]
    assert "dropped 1 record(s) with no time at read" in out.attrs["history"]
    written = xr.open_dataset(write(out, tmp_path / "stage1.nc"), engine="netcdf4")
    assert int(written.attrs["n_records_without_time"]) == 1
    written.close()
