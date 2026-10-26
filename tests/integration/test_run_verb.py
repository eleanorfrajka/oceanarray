"""``oceanarray.run(mooring)``: stages 1–3, then stack and grid, on the dune2 fixtures.

A fresh proc tree holding only the description is run against the committed raw
files. Covers the files returned, ``data_mode``, the stack/grid settings from the
call and from the description, carrying on past failures (the default), and the
strict ``keep_going=False``.
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


def _fresh_tree(tmp_path: Path, extra_yaml: str = "") -> Path:
    """Return the path of a description, optionally extended, in an otherwise empty proc tree."""
    moor = tmp_path / "proc" / _NAME
    moor.mkdir(parents=True)
    text = (_PROC / f"{_NAME}.mooring.yaml").read_text() + extra_yaml
    (moor / f"{_NAME}.mooring.yaml").write_text(text)
    return moor / f"{_NAME}.mooring.yaml"


def _attrs(path: Path) -> dict:
    """Return a netCDF file's global attributes."""
    with xr.open_dataset(path, decode_times=False) as ds:
        return dict(ds.attrs)


@pytest.fixture(scope="module")
def ran(tmp_path_factory) -> tuple[Path, list[Path]]:
    """Run once over a fresh tree, hourly stack; return the description path and the files."""
    yaml_path = _fresh_tree(tmp_path_factory.mktemp("run"))
    return yaml_path, oa.run(yaml_path, raw_dir=_RAW, dt_seconds=3600)


def test_run_returns_the_stage_files_then_the_stack_and_grid(ran):
    """Six stage files, then the stack, then the grid — all on disk."""
    yaml_path, files = ran
    moor = yaml_path.parent
    stage = [
        paths.stage_file(moor, _NAME, _INSTR[s], s, n)
        for n in (1, 2, 3)
        for s in ("2941", "9920")
    ]
    assert files == [
        *stage,
        paths.product_file(moor, _NAME, "stack"),
        paths.product_file(moor, _NAME, "grid"),
    ]
    assert all(p.exists() for p in files)


def test_stage3_stack_and_grid_are_provisional(ran):
    """data_mode is P on everything stage 3 and after; stages 1 and 2 claim nothing."""
    _, files = ran
    modes = [_attrs(p).get("data_mode") for p in files]
    assert modes == [None] * 4 + ["P"] * 4


def test_strict_stack_is_all_stage3(ran):
    """Every level comes from a stage-3 file, and nothing is recorded as stage 2."""
    _, files = ran
    with xr.open_dataset(files[-2]) as ds:
        assert set(ds["source_stage"].values.tolist()) == {3}
        assert "stage2_instruments" not in ds.attrs


def test_run_matches_the_stacker_and_gridder(ran, tmp_path):
    """The stack and grid hold the same values as MooringStacker and MooringGridder make."""
    from oceanarray.pipeline.grid import MooringGridder
    from oceanarray.pipeline.stack import MooringStacker

    yaml_path, files = ran
    moor = tmp_path / _NAME
    shutil.copytree(yaml_path.parent, moor, ignore=shutil.ignore_patterns("*.nc"))
    for p in files[:6]:
        dest = moor / p.relative_to(yaml_path.parent)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, dest)
    MooringStacker(proc_dir=str(tmp_path)).stack(_NAME, dt_seconds=3600, force=True)
    MooringGridder(proc_dir=str(tmp_path)).grid(_NAME, force=True)
    for product, ours in (("stack", files[-2]), ("grid", files[-1])):
        with (
            xr.open_dataset(ours) as a,
            xr.open_dataset(paths.product_file(moor, _NAME, product)) as b,
        ):
            for v in b.data_vars:
                assert np.array_equal(
                    a[v].values, b[v].values, equal_nan=a[v].dtype.kind == "f"
                ), f"{product}: {v}"


def test_grid_settings_come_from_the_description_unless_given(tmp_path):
    """A grid: block sets the levels; a keyword overrides it."""
    yaml_path = _fresh_tree(
        tmp_path,
        "stack:\n  dt_seconds: 3600\ngrid:\n  p_start: 100\n  p_end: 300\n  dp: 50\n",
    )
    files = oa.run(yaml_path, raw_dir=_RAW)
    with xr.open_dataset(files[-1]) as ds:
        assert ds["pressure"].values.tolist() == [100, 150, 200, 250, 300]
    with xr.open_dataset(files[-2]) as ds:
        assert np.all(np.diff(ds["time"].values) == np.timedelta64(3600, "s"))
    files = oa.run(yaml_path, raw_dir=_RAW, dp=100)
    with xr.open_dataset(files[-1]) as ds:
        assert ds["pressure"].values.tolist() == [100, 200, 300]


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ("stack: 60\n", "must be a mapping"),
        ("stack:\n  dt: 60\n", "not a stack setting"),
        ("stack:\n  dt_seconds: 1.5\n", "non-negative integer"),
        ("grid:\n  p_start: 500\n  p_end: 100\n", "must be above p_start"),
        ("grid:\n  dp: 0\n", "must be > 0"),
    ],
)
def test_check_refuses_a_malformed_settings_block(tmp_path, block, message):
    """A wrong stack:/grid: block is an ERROR, so load (and run) refuse it."""
    yaml_path = _fresh_tree(tmp_path, block)
    errors = [i.message for i in oa.mooring.check(yaml_path) if i.level == "ERROR"]
    assert any(message in e for e in errors), errors
    with pytest.raises(MooringError, match=message):
        oa.mooring.load(yaml_path)


def _without_aquadopp_raw(tmp_path: Path) -> Path:
    """Return a raw root where the aquadopp's files are missing."""
    raw = tmp_path / "raw"
    shutil.copytree(_RAW / _NAME, raw / _NAME)
    for f in (raw / _NAME / "aquadopp").iterdir():
        f.unlink()
    return raw


def test_strict_run_stops_at_the_failed_stage(tmp_path):
    """Strict (keep_going=False): a missing raw file raises in stage 1 and nothing is stacked."""
    raw = _without_aquadopp_raw(tmp_path)
    yaml_path = _fresh_tree(tmp_path)
    with pytest.raises(MooringError, match="stage 1 failed"):
        oa.run(yaml_path, raw_dir=raw, dt_seconds=3600, keep_going=False)
    assert not paths.product_file(yaml_path.parent, _NAME, "stack").exists()


def test_keep_going_process_carries_on_without_the_failed_instrument(tmp_path, capsys):
    """keep_going: the aquadopp is left out, the microcat goes through all three stages."""
    raw = _without_aquadopp_raw(tmp_path)
    files = oa.process(_fresh_tree(tmp_path), raw_dir=raw, keep_going=True)
    assert [p.name for p in files] == [f"{_NAME}_2941_stage{n}.nc" for n in (1, 2, 3)]
    assert "failure(s) left behind by keep_going" in capsys.readouterr().out


def test_keep_going_stacks_from_stage2_when_stage3_fails(tmp_path, monkeypatch):
    """A failed stage 3 under keep_going: the stack takes the stage-2 files and says so."""
    import oceanarray.pipeline.stage3 as st3

    def _boom(*_a, **_k):
        raise ValueError("boom")

    monkeypatch.setattr(st3, "stage3", _boom)
    yaml_path = _fresh_tree(tmp_path)
    files = oa.run(yaml_path, raw_dir=_RAW, dt_seconds=3600, keep_going=True)
    with xr.open_dataset(files[-2]) as ds:
        assert set(ds["source_stage"].values.tolist()) == {2}
        assert ds.attrs["stage2_instruments"] == "2941 9920"
    assert _attrs(files[-1])["stage2_instruments"] == "2941 9920"
    with pytest.raises(MooringError, match="stage 3 failed"):
        oa.run(yaml_path, raw_dir=_RAW, dt_seconds=3600, keep_going=False)


def test_strict_stack_refuses_a_missing_stage3_file(ran, tmp_path):
    """The stack alone: a missing stage-3 file raises without keep_going, falls back with it."""
    from oceanarray.pipeline.stack import _stack_file

    yaml_path, _ = ran
    moor = tmp_path / _NAME
    shutil.copytree(yaml_path.parent, moor)
    paths.stage_file(moor, _NAME, "microcat", "2941", 3).unlink()
    cfg = oa.mooring.load(moor / yaml_path.name)
    with pytest.raises(MooringError, match="no stage-3 file for microcat s/n 2941"):
        _stack_file(cfg, moor, dt_seconds=3600)
    _, ds = _stack_file(cfg, moor, dt_seconds=3600, keep_going=True)
    assert ds.attrs["stage2_instruments"] == "2941"


def test_run_from_stage2_needs_no_raw_directory(ran, tmp_path):
    """stages=(2, 3) re-runs from the stage-1 files and restacks, with no raw_dir."""
    yaml_path, _ = ran
    moor = tmp_path / _NAME
    shutil.copytree(yaml_path.parent, moor)
    files = oa.run(moor / yaml_path.name, stages=(2, 3), dt_seconds=3600)
    assert [p.name for p in files[-2:]] == [f"{_NAME}_stack.nc", f"{_NAME}_grid.nc"]


def _cli(args: list[str]) -> int:
    """Run the oceanarray CLI with *args*; return its exit code."""
    from oceanarray.cli import build_parser

    ns = build_parser().parse_args(args)
    return ns.func(ns)


def test_cli_run_keep_going_stacks_what_exists(tmp_path, capsys, monkeypatch):
    """A missing raw file: --strict exits 1 with no stack; the default carries on and stacks the rest."""
    import oceanarray.cli as cli

    monkeypatch.setattr(cli, "_generate_mooring_report", lambda *_a, **_k: True)
    raw = _without_aquadopp_raw(tmp_path)
    yaml_path = _fresh_tree(tmp_path)
    proc = str(yaml_path.parent.parent)
    base = ["run", _NAME, "--proc-dir", proc, "--raw-dir", str(raw), "--dt", "3600"]
    assert _cli([*base, "--strict"]) == 1
    assert "stage 1 failed" in capsys.readouterr().out
    assert not paths.product_file(yaml_path.parent, _NAME, "stack").exists()
    assert _cli(base) == 0
    with xr.open_dataset(paths.product_file(yaml_path.parent, _NAME, "stack")) as ds:
        assert ds["serial"].values.tolist() == ["2941"]
        assert "9920" in ds.attrs["instruments_omitted"]


def test_cli_run_takes_stages_and_the_description_settings(ran, monkeypatch):
    """--stage reaches run(), keep_going defaults True (--strict makes it False); unset settings stay None."""
    import oceanarray.cli as cli
    import oceanarray.pipeline as pipeline

    seen: dict = {}
    monkeypatch.setattr(pipeline, "run", lambda _path, **kw: seen.update(kw) or [])
    monkeypatch.setattr(cli, "_generate_mooring_report", lambda *_a, **_k: True)
    yaml_path, _ = ran
    proc = str(yaml_path.parent.parent)
    assert _cli(["run", _NAME, "--proc-dir", proc, "--stage", "3", "2"]) == 0
    assert seen["stages"] == (2, 3)
    assert seen["keep_going"] is True
    assert _cli(["run", _NAME, "--proc-dir", proc, "--stage", "3", "--strict"]) == 0
    assert seen["keep_going"] is False
    assert [seen[k] for k in ("dt_seconds", "p_start", "p_end", "dp")] == [None] * 4


def _copy(ran, tmp_path) -> Path:
    """A copy of the processed tree; returns its description path."""
    yaml_path, _ = ran
    shutil.copytree(yaml_path.parent, tmp_path / _NAME)
    return tmp_path / _NAME / yaml_path.name


def test_keep_going_does_not_stack_stage3_files_older_than_a_failed_stage3(
    ran, tmp_path, monkeypatch
):
    """Stage 3 fails on a tree that already has stage-3 files: the stack uses stage 2, and says why."""
    import oceanarray.pipeline.stage3 as st3

    def _boom(*_a, **_k):
        raise ValueError("boom")

    monkeypatch.setattr(st3, "stage3", _boom)
    yaml_path = _copy(ran, tmp_path)
    files = oa.run(
        yaml_path, stages=(2, 3), force=True, dt_seconds=3600, keep_going=True
    )
    with xr.open_dataset(files[-2]) as ds:
        assert set(ds["source_stage"].values.tolist()) == {2}
        assert "stage 3: boom" in ds.attrs["keep_going_failures"]


def test_keep_going_leaves_out_an_instrument_that_failed_this_run(ran, tmp_path):
    """The aquadopp's raw file is gone: its older files are not stacked, and the grid says so."""
    yaml_path = _copy(ran, tmp_path)
    raw = _without_aquadopp_raw(tmp_path)
    files = oa.run(yaml_path, raw_dir=raw, force=True, dt_seconds=3600, keep_going=True)
    with xr.open_dataset(files[-2]) as ds:
        assert ds["serial"].values.tolist() == ["2941"]
        assert "9920" in ds.attrs["instruments_omitted"]
        assert "failed in stage 1 this run" in ds.attrs["instruments_omitted"]
    grid_attrs = _attrs(files[-1])
    assert "9920" in grid_attrs["instruments_omitted"]
    assert "stage 1:" in grid_attrs["keep_going_failures"]


def test_strict_run_removes_a_grid_the_new_stack_no_longer_matches(
    ran, tmp_path, monkeypatch
):
    """A strict stack that left an instrument out is written, and the old grid is removed."""
    import oceanarray.pipeline.stack as st

    real = st.stack

    def _omitting(*a, **k):
        ds = real(*a, **k)
        ds.attrs["instruments_omitted"] = "aquadopp s/n 9920 (test)"
        return ds

    monkeypatch.setattr(st, "stack", _omitting)
    yaml_path = _copy(ran, tmp_path)
    grid = paths.product_file(yaml_path.parent, _NAME, "grid")
    assert grid.exists()
    with pytest.raises(MooringError, match="written but not gridded"):
        oa.run(yaml_path, stages=3, dt_seconds=1800, keep_going=False)
    assert paths.product_file(yaml_path.parent, _NAME, "stack").exists()
    assert not grid.exists()


@pytest.mark.parametrize(
    ("kwargs", "extra_yaml", "message"),
    [
        ({"dt_seconds": 0}, "", "dt_seconds=0 must be > 0"),
        ({"dt_seconds": 1.5}, "", "not a whole number"),
        ({"dp": True}, "", "dp=True is not a number"),
        ({}, "grid:\n  p_end: 150\n", "must be above p_start=200"),
        ({"p_start": 500, "p_end": 100}, "", "must be above p_start=500"),
    ],
)
def test_run_refuses_bad_settings_before_processing(
    tmp_path, kwargs, extra_yaml, message
):
    """The merged settings are checked first: nothing is processed with a bad one."""
    yaml_path = _fresh_tree(tmp_path, extra_yaml)
    with pytest.raises(MooringError, match=message):
        oa.run(yaml_path, raw_dir=_RAW, **kwargs)
    assert not list(yaml_path.parent.rglob("*.nc"))
