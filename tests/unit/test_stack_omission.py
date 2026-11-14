"""The stack builds a partial file when one instrument fails, and records it.

An instrument with unrecognised units (or an unreadable file) is dropped from the
stack rather than aborting it — partial stacks are operationally useful — but the
omission is recorded in an ``instruments_omitted`` global attribute and the stack
returns False so the CLI exits 1 ("something failed", file still written).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import xarray as xr
import yaml

from oceanarray.exceptions import MooringError
from oceanarray.pipeline.stack import MooringStacker

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "proc" / "dune2_1_2026"


def _corrupt_units(nc_path: Path, var: str, bad_units: str) -> None:
    ds = xr.open_dataset(nc_path, engine="netcdf4", decode_timedelta=False).load()
    ds.close()
    ds[var].attrs["units"] = bad_units
    ds.to_netcdf(nc_path, engine="netcdf4")


def test_stack_without_deployment_window_raises(tmp_path):
    """A config load() accepts but with no deployment window fails loud, not KeyError."""
    shutil.copytree(_FIXTURE, tmp_path / "dune2_1_2026")
    proc = tmp_path / "dune2_1_2026"
    yaml_path = proc / "dune2_1_2026.mooring.yaml"
    cfg = yaml.safe_load(yaml_path.read_text())
    cfg.pop("deployment_time", None)
    cfg.pop("recovery_time", None)
    yaml_path.write_text(yaml.dump(cfg))
    with pytest.raises(MooringError, match="deployment_time"):
        MooringStacker(proc_dir=str(tmp_path)).stack("dune2_1_2026", force=True)


def test_stack_omits_bad_units_instrument_but_builds_and_records(tmp_path):
    shutil.copytree(_FIXTURE, tmp_path / "dune2_1_2026")
    proc = tmp_path / "dune2_1_2026"
    # Give the microcat (serial 2941) an unrecognised temperature unit so the
    # units pass raises UnitsError when the stack loads it.
    _corrupt_units(
        proc / "microcat" / "dune2_1_2026_2941_stage3.nc", "temperature", "Kelvin"
    )

    ok = MooringStacker(proc_dir=str(tmp_path)).stack("dune2_1_2026", force=True)

    stack_path = proc / "dune2_1_2026_stack.nc"
    assert stack_path.exists(), "a partial stack must still be written"
    assert ok is False, "an omission must make stack() return False (CLI exit 1)"

    ds = xr.open_dataset(stack_path, engine="netcdf4", decode_timedelta=False)
    omitted = ds.attrs.get("instruments_omitted", "")
    # The omitted instrument must leave NO phantom level: N_LEVELS and the recorded
    # omission agree (one of two instruments dropped -> exactly one level, 9920).
    assert ds.sizes["N_LEVELS"] == 1, f"phantom level: N_LEVELS={ds.sizes['N_LEVELS']}"
    assert "2941" not in [str(s) for s in ds["serial"].values], (
        "the omitted serial must not appear as a stack level"
    )
    ds.close()
    assert "2941" in omitted, f"omitted instrument not recorded: {omitted!r}"
    assert "units error" in omitted.lower()


def test_stack_all_instruments_omitted_writes_no_file(tmp_path):
    shutil.copytree(_FIXTURE, tmp_path / "dune2_1_2026")
    proc = tmp_path / "dune2_1_2026"
    (proc / "dune2_1_2026_stack.nc").unlink()  # drop the committed fixture stack
    # Corrupt BOTH instruments so every one is omitted.
    _corrupt_units(
        proc / "microcat" / "dune2_1_2026_2941_stage3.nc", "temperature", "Kelvin"
    )
    _corrupt_units(
        proc / "aquadopp" / "dune2_1_2026_9920_stage3.nc", "temperature", "Kelvin"
    )

    ok = MooringStacker(proc_dir=str(tmp_path)).stack("dune2_1_2026", force=True)

    assert ok is False
    assert not (proc / "dune2_1_2026_stack.nc").exists(), (
        "an all-omitted mooring must not write a stack file"
    )


def _set_yaml_key(proc: Path, serial: int, key: str, value: object) -> None:
    import yaml

    path = proc / "dune2_1_2026.mooring.yaml"
    cfg = yaml.safe_load(path.read_text())
    for entry in cfg["clamp"]:
        if str(entry["serial"]).rstrip("*") == str(serial):
            entry[key] = value
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))


def test_stack_false_excludes_and_records_without_failing(tmp_path):
    shutil.copytree(_FIXTURE, tmp_path / "dune2_1_2026")
    proc = tmp_path / "dune2_1_2026"
    _set_yaml_key(proc, 2941, "stack", False)
    ok = MooringStacker(proc_dir=str(tmp_path)).stack("dune2_1_2026", force=True)
    assert ok is True, "an exclusion is deliberate, not a failure"
    ds = xr.open_dataset(proc / "dune2_1_2026_stack.nc", engine="netcdf4")
    assert ds.sizes["N_LEVELS"] == 1
    assert "2941" in ds.attrs["instruments_excluded"]
    assert "instruments_omitted" not in ds.attrs
    ds.close()


def test_low_passed_file_without_stack_false_is_omitted(tmp_path):
    shutil.copytree(_FIXTURE, tmp_path / "dune2_1_2026")
    proc = tmp_path / "dune2_1_2026"
    nc = proc / "microcat" / "dune2_1_2026_2941_stage3.nc"
    ds = xr.open_dataset(nc, engine="netcdf4", decode_timedelta=False).load()
    ds.close()
    ds.attrs["originator_low_passed"] = "2-day low-pass, 12-hour time step"
    ds.to_netcdf(nc, engine="netcdf4")
    ok = MooringStacker(proc_dir=str(tmp_path)).stack("dune2_1_2026", force=True)
    assert ok is False
    out = xr.open_dataset(proc / "dune2_1_2026_stack.nc", engine="netcdf4")
    assert "stack: false" in out.attrs["instruments_omitted"]
    out.close()


def test_stack_names_an_entry_with_no_hab():
    """A stacked entry with no hab is omitted and named, not silently dropped."""
    from oceanarray import mooring as _mooring
    from oceanarray.pipeline.read_mooring import read_mooring
    from oceanarray.pipeline.stack import stack

    cfg = _mooring.load(_FIXTURE / "dune2_1_2026.mooring.yaml")
    for entry in cfg["clamp"]:
        if str(entry["serial"]) == "2941":
            entry.pop("hab")
    datasets = read_mooring(cfg, stage=3, mooring_dir=_FIXTURE)
    ds = stack(datasets, cfg)
    omitted = ds.attrs["instruments_omitted"]
    assert "microcat s/n 2941 (no hab in the mooring YAML" in omitted
    assert [str(s) for s in ds["serial"].values] == ["9920"]


def test_stack_does_not_report_a_skipped_entry_with_no_hab():
    from oceanarray import mooring as _mooring
    from oceanarray.pipeline.read_mooring import read_mooring
    from oceanarray.pipeline.stack import stack

    cfg = _mooring.load(_FIXTURE / "dune2_1_2026.mooring.yaml")
    datasets = read_mooring(cfg, stage=3, serials=["9920"], mooring_dir=_FIXTURE)
    for entry in cfg["clamp"]:
        if str(entry["serial"]) == "2941":
            entry.pop("hab")
            entry["skip"] = True
    ds = stack(datasets, cfg)
    assert "instruments_omitted" not in ds.attrs
