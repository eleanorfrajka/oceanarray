"""The export verb and ``oceanarray export`` on a copy of the dune2 mooring folder."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import xarray as xr

import oceanarray as oa
from oceanarray.pipeline.oceansites import OceanSitesError

_FIXTURE = Path("tests/fixtures/proc/dune2_1_2026")
_EXPECTED = {
    "OS_DUNE2_202607_P_CTD-2941-320m.nc",
    "OS_DUNE2_202607_P_CM-9920-519m.nc",
    "OS_DUNE2-202607_20260711-20260725_GRD_PT1H-DP50.nc",
}


def _copy(tmp_path: Path, *, platform: str | None = "DUNE2") -> Path:
    """Copy the dune2 folder, optionally adding a platform_code; return its YAML."""
    folder = tmp_path / "dune2_1_2026"
    shutil.copytree(_FIXTURE, folder)
    yaml_path = folder / "dune2_1_2026.mooring.yaml"
    if platform is not None:
        text = yaml_path.read_text()
        yaml_path.write_text(
            text.replace(
                "name: dune2_1_2026",
                f"name: dune2_1_2026\nplatform_code: {platform}",
                1,
            )
        )
    return yaml_path


def test_export_writes_the_instruments_and_the_grid(tmp_path: Path) -> None:
    """Every instrument with a stage-3 file, and the grid, land in <folder>/oceansites."""
    written = oa.export(_copy(tmp_path))
    assert {p.name for p in written} == _EXPECTED
    assert all(p.parent.name == "oceansites" for p in written)
    with xr.open_dataset(written[0]) as ds:
        assert ds.attrs["format_version"] == "1.4"


def test_export_without_a_platform_code_refuses_before_writing(tmp_path: Path) -> None:
    """No platform_code, no file names: nothing is written."""
    yaml_path = _copy(tmp_path, platform=None)
    with pytest.raises(OceanSitesError, match="platform_code"):
        oa.export(yaml_path)
    assert not (yaml_path.parent / "oceansites").exists()


def test_export_no_grid_and_refuses_to_overwrite(tmp_path: Path) -> None:
    """grid=False leaves the grid out; a second run refuses to replace the files."""
    yaml_path = _copy(tmp_path)
    written = oa.export(yaml_path, grid=False)
    assert len(written) == 2
    # Existing files are left out, not replaced; with every file there, nothing is written.
    with pytest.raises(oa.MooringError, match="nothing exported"):
        oa.export(yaml_path, grid=False)
    assert len(oa.export(yaml_path, grid=False, overwrite=True)) == 2


def test_cli_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``oceanarray export MOORING -o DIR`` writes the files and exits 0."""
    from oceanarray.cli import main

    yaml_path = _copy(tmp_path)
    out = tmp_path / "os"
    monkeypatch.setattr(
        sys, "argv", ["oceanarray", "export", str(yaml_path), "-o", str(out)]
    )
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    assert {p.name for p in out.iterdir()} == _EXPECTED


def test_export_written_coordinates_have_no_fill_value(tmp_path: Path) -> None:
    """Manual §2.4: no fill value on TIME, DEPTH, LATITUDE or LONGITUDE in the file."""
    import netCDF4

    for path in oa.export(_copy(tmp_path)):
        with netCDF4.Dataset(path) as nc:
            for name in ("TIME", "DEPTH", "LATITUDE", "LONGITUDE"):
                assert "_FillValue" not in nc[name].ncattrs(), (path.name, name)
