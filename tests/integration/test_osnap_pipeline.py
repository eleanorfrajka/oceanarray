"""Stages 1-3 on imported OSNAP files (OceanSITES netCDF and RODB text).

Builds two small moorings in a temp tree from the trimmed fixtures in
``tests/fixtures/raw/osnap`` and runs the pipeline: the readers, the YAML
``units_override``, the value-range check, and stage 3 leaving the originator's
declination alone.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
import yaml

pytest.importorskip("seasenselib", reason="seasenselib not installed")
pytestmark = pytest.mark.needs_seasenselib

from oceanarray.pipeline import _process_legacy as process  # noqa: E402

OSNAP = Path(__file__).parent.parent / "fixtures" / "raw" / "osnap"


def _mooring(tmp_path, name, header, instruments):
    raw = tmp_path / "raw" / name
    proc = tmp_path / "proc" / name
    proc.mkdir(parents=True)
    for inst in instruments:
        (raw / inst["instrument"]).mkdir(parents=True, exist_ok=True)
        shutil.copy(OSNAP / inst["filename"], raw / inst["instrument"])
    cfg = {"name": name, **header, "clamp": instruments}
    (proc / f"{name}.mooring.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return tmp_path / "raw", tmp_path / "proc"


def _open(proc, name, instrument, serial, stage):
    path = proc / name / instrument / f"{name}_{serial}_stage{stage}.nc"
    assert path.exists(), path
    return xr.open_dataset(path, engine="netcdf4").load()


RTWB = {
    "waterdepth": 1600,
    "latitude": "57 28.240 N",
    "longitude": "012 42.300 W",
    "deployment_time": "2014-07-17T12:00",
    "recovery_time": "2015-06-22T12:00",
}
CF7 = {
    "waterdepth": 1901,
    "latitude": "59 55.576 N",
    "longitude": "041 25.885 W",
    "deployment_time": "2020-07-12T14:00",
    "recovery_time": "2021-11-12T00:00",
}


def test_rodb_mooring_runs_to_stage3_without_reapplying_declination(tmp_path):
    raw, proc = _mooring(
        tmp_path,
        "rtwb1_01_2014",
        RTWB,
        [
            {
                "serial": 11335,
                "instrument": "microcat",
                "file_type": "rodb",
                "filename": "rtwb1_01_2014_001.microcat",
                "hab": 1500,
            },
            {
                "serial": 11021,
                "instrument": "currentmeter",
                "file_type": "rodb",
                "filename": "rtwb1_01_2014_11021.edt",
                "hab": 1505,
            },
        ],
    )
    assert process("rtwb1_01_2014", [1, 2, 3], proc_dir=proc, raw_dir=raw, force=True)
    s1 = _open(proc, "rtwb1_01_2014", "currentmeter", 11021, 1)
    s3 = _open(proc, "rtwb1_01_2014", "currentmeter", 11021, 3)
    assert s1["east_velocity"].attrs["units"] == "m s-1"
    assert s3.attrs["originator_magnetic_declination"] == -7.33
    assert "magnetic_declination" not in s3.attrs
    common = np.intersect1d(s1["time"].values, s3["time"].values)
    np.testing.assert_allclose(
        s3["east_velocity"].sel(time=common).values,
        s1["east_velocity"].sel(time=common).values,
    )
    mc = _open(proc, "rtwb1_01_2014", "microcat", 11335, 1)
    assert mc["conductivity"].attrs["units"] == "mS cm-1"


def test_mislabelled_units_refused_then_fixed_by_override(tmp_path):
    inst = {
        "serial": 22225,
        "instrument": "microcat",
        "file_type": "oceansites",
        "filename": "CF7_MCTD_2020_22225_50m.nc",
        "hab": 1851,
    }
    raw, proc = _mooring(tmp_path / "a", "cf7", CF7, [dict(inst)])
    process("cf7", [1], proc_dir=proc, raw_dir=raw, force=True)
    assert not (proc / "cf7" / "microcat" / "cf7_22225_stage1.nc").exists()
    log = "".join(p.read_text() for p in (proc / "cf7").rglob("*.log"))
    assert "units_override" in log

    raw, proc = _mooring(
        tmp_path / "b",
        "cf7",
        CF7,
        [{**inst, "units_override": {"conductivity": "mS/cm"}}],
    )
    assert process("cf7", [1], proc_dir=proc, raw_dir=raw, force=True)
    s1 = _open(proc, "cf7", "microcat", 22225, 1)
    assert s1["conductivity"].attrs["units_override_from"] == "S/m"
    assert 25 < float(np.nanmedian(s1["conductivity"].values)) < 45


def test_oceansites_current_meter_to_stage3(tmp_path):
    raw, proc = _mooring(
        tmp_path,
        "cf7",
        CF7,
        [
            {
                "serial": 500,
                "instrument": "currentmeter",
                "file_type": "oceansites",
                "filename": "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc",
                "hab": 1401,
            }
        ],
    )
    assert process("cf7", [1, 2, 3], proc_dir=proc, raw_dir=raw, force=True)
    s3 = _open(proc, "cf7", "currentmeter", 500, 3)
    assert s3.attrs["coordinate_system"] == "ENU"
    assert "magnetic_declination" not in s3.attrs
    assert s3.attrs["originator_institution"].startswith("Scripps")
