"""OceanSITES 1.4 files from the dune2 stage-3, grid and ADCP fixtures."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from oceanarray import mooring, writers
from oceanarray.pipeline.oceansites import (
    OceanSitesError,
    oceansites_filename,
    to_oceansites,
    type_code,
)
from oceanarray.readers.oceansites import read_oceansites

_PROC = Path("tests/fixtures/proc/dune2_1_2026")
_MICROCAT = _PROC / "microcat" / "dune2_1_2026_2941_stage3.nc"
_AQUADOPP = _PROC / "aquadopp" / "dune2_1_2026_9920_stage3.nc"

#: Global attributes the manual (§2.2) marks required, or the GDAC inventory reads.
_REQUIRED_GLOBALS = (
    "platform_code",
    "data_mode",
    "format_version",
    "data_type",
    "geospatial_lat_min",
    "geospatial_lat_max",
    "geospatial_lon_min",
    "geospatial_lon_max",
    "geospatial_vertical_min",
    "geospatial_vertical_max",
    "time_coverage_start",
    "time_coverage_end",
)


@pytest.fixture
def description() -> dict:
    """The dune2 mooring description with the OceanSITES platform code added."""
    return {
        **mooring.load(_PROC / "dune2_1_2026.mooring.yaml"),
        "platform_code": "DUNE2",
    }


@pytest.fixture(params=[_MICROCAT, _AQUADOPP], ids=["microcat", "aquadopp"])
def stage3(request: pytest.FixtureRequest) -> xr.Dataset:
    """One real stage-3 dataset."""
    return xr.open_dataset(request.param)


def test_round_trip_through_the_reader(
    stage3: xr.Dataset, description: dict, tmp_path: Path
) -> None:
    """Written and read back with read_oceansites, every exported variable is unchanged."""
    out = to_oceansites(stage3, description)
    back = read_oceansites(writers.write(out, tmp_path / oceansites_filename(out)))
    exported = [v for v in back.data_vars if not v.endswith("_qc_originator")]
    assert exported
    for name in exported:
        np.testing.assert_array_equal(back[name].values, stage3[name].values)
    # TIME is float64 days since 1950 (manual §2.4), which resolves ~1 µs; the
    # Aquadopp stamps carry sub-millisecond clock-drift corrections.
    drift = (back["time"].values - stage3["time"].values).astype("timedelta64[ns]")
    assert np.abs(drift.astype("int64")).max() < 1_000


def test_qc_flags_are_carried_and_missing_values_flagged_9(
    stage3: xr.Dataset, description: dict
) -> None:
    """``<PARAM>_QC`` holds the stage-3 flags (same table 2), and 9 wherever the value is missing."""
    out = to_oceansites(stage3, description)
    temp_qc = out["TEMP_QC"].values[:, 0]
    expected = np.where(
        np.isfinite(stage3["temperature"].values), stage3["temperature_qc"].values, 9
    )
    np.testing.assert_array_equal(temp_qc, expected)
    assert out["TEMP"].attrs["ancillary_variables"] == "TEMP_QC"
    assert out["TEMP_QC"].attrs["conventions"] == "OceanSITES QC Flags"


def test_file_names_follow_section_4_1(description: dict) -> None:
    """OS_<platform>_<YYYYMM>_<mode>_<type>-<serial>-<depth>m; pressure interpolated → CTD."""
    ctd = to_oceansites(xr.open_dataset(_MICROCAT), description)
    cm = to_oceansites(xr.open_dataset(_AQUADOPP), description)
    assert oceansites_filename(ctd) == "OS_DUNE2_202607_P_CTD-2941-320m.nc"
    assert oceansites_filename(cm) == "OS_DUNE2_202607_P_CM-9920-519m.nc"


def test_required_globals_present(stage3: xr.Dataset, description: dict) -> None:
    """Every required / GDAC-read global is written; the version is 1.4."""
    out = to_oceansites(stage3, description)
    missing = [k for k in _REQUIRED_GLOBALS if k not in out.attrs]
    assert missing == []
    assert out.attrs["format_version"] == "1.4"
    assert "OceanSITES-1.4" in out.attrs["Conventions"]
    assert out.attrs["time_coverage_start"].endswith("Z")


def test_site_code_only_from_the_array_description(description: dict) -> None:
    """site_code comes from the array description and is never invented."""
    ds = xr.open_dataset(_MICROCAT)
    assert "site_code" not in to_oceansites(ds, description).attrs
    out = to_oceansites(
        ds, description, array={"site_code": "MIXSED", "project": "MIXSED"}
    )
    assert out.attrs["site_code"] == "MIXSED"


def test_dimensions_and_depth(description: dict) -> None:
    """Variables are on (TIME, DEPTH); DEPTH is the nominal depth, PRES the measured one."""
    out = to_oceansites(xr.open_dataset(_MICROCAT), description)
    assert out["TEMP"].dims == ("TIME", "DEPTH")
    assert {out.sizes[d] for d in ("DEPTH", "LATITUDE", "LONGITUDE")} == {1}
    assert float(out["DEPTH"].values[0]) == 320.0
    assert "pressure source" in out["PRES"].attrs["comment"]


def test_data_mode_per_variable_makes_the_file_mixed(description: dict) -> None:
    """A per-variable data_mode gives DM_indicator per variable and global M."""
    ds = xr.open_dataset(_MICROCAT)
    ds.attrs["caldip_applied"] = "caldip offsets applied from cast 7"
    for entry in description["clamp"]:
        if str(entry.get("serial")) == "2941":
            entry["data_mode"] = {
                "temperature": "D",
                "conductivity": "D",
                "salinity": "D",
            }
    out = to_oceansites(ds, description)
    assert out["TEMP"].attrs["DM_indicator"] == "D"
    assert out["PRES"].attrs["DM_indicator"] == "P"
    assert out.attrs["data_mode"] == "M"
    assert "_M_" in oceansites_filename(out)


def test_delayed_mode_needs_the_calibration_dip(description: dict) -> None:
    """D is refused when the stage-3 file does not record the caldip correction."""
    for entry in description["clamp"]:
        if str(entry.get("serial")) == "2941":
            entry["data_mode"] = "D"
    with pytest.raises(OceanSitesError, match="calibration-dip"):
        to_oceansites(xr.open_dataset(_MICROCAT), description)


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"platform_code": None}, "no platform_code"),
        ({"platform_code": "DUNE2_1"}, "file-name separator"),
        ({"platform_code": 12}, "not text"),
        ({"deployment_code": "2026-07"}, "not YYYYMM"),
        ({"deployment_code": "202613"}, "not YYYYMM"),
        ({"latitude": None, "longitude": None}, "no position"),
    ],
)
def test_description_codes_refused(
    description: dict, change: dict, fragment: str
) -> None:
    """A missing or malformed platform or deployment code stops the export."""
    with pytest.raises(OceanSitesError, match=fragment):
        to_oceansites(xr.open_dataset(_MICROCAT), {**description, **change})


def test_type_codes_follow_the_variables() -> None:
    """No pressure at all → CT; temperature only → T."""
    ds = xr.open_dataset(_MICROCAT)
    assert type_code(ds) == "CTD"
    assert type_code(ds.drop_vars(["pressure", "pressure_qc"])) == "CT"
    assert type_code(ds[["temperature"]]) == "T"


# The ADCP fixture is a real dsG2 record cut for the knockdown tests; it lacks the
# instrument's serial and height above bottom, so those are supplied here. Its
# orientation is the file's own: the YAML says down, the instrument header up, and
# the YAML wins, as in the stack.
_ADCP = Path("tests/fixtures/adcp/dsG2_adcp_stage3_knockdown.nc")
_ADCP_DESCRIPTION = {
    "name": "dsG2_1_2026",
    "platform_code": "DSG2",
    "waterdepth": 985,
    "deployment_time": "2026-05-07T11:10:00",
    "latitude": "65 33.5 N",
    "longitude": "29 30.0 W",
}


def _adcp(**attrs: object) -> xr.Dataset:
    """The ADCP fixture with its instrument metadata set."""
    ds = xr.open_dataset(_ADCP)
    ds.attrs.update({"serial": "1234", "hab": 400, **attrs})
    return ds


def test_adcp_bins_are_the_depth_axis(tmp_path: Path) -> None:
    """One file per ADCP: DEPTH = transducer depth + range (down-looking), values unchanged."""
    ds = _adcp()
    out = to_oceansites(ds, _ADCP_DESCRIPTION)
    assert oceansites_filename(out) == "OS_DSG2_202605_P_ADCP-1234-585m.nc"
    expected = 985 - 400 + ds["range"].values
    np.testing.assert_allclose(out["DEPTH"].values, expected, rtol=1e-6)
    assert out["UCUR"].attrs["sensor_orientation"] == "downward"
    back = xr.open_dataset(writers.write(out, tmp_path / oceansites_filename(out)))
    np.testing.assert_array_equal(back["UCUR"].values, ds["east_velocity"].values)
    np.testing.assert_array_equal(back["PRES"].values, ds["bin_pressure"].values)


def test_upward_adcp_bins_decrease_in_depth() -> None:
    """An upward-looking ADCP's bins are shallower than its transducer."""
    out = to_oceansites(_adcp(orientation_yaml="up"), _ADCP_DESCRIPTION)
    assert out["DEPTH"].values[-1] < out["DEPTH"].values[0] < 585
    assert out["VCUR"].attrs["sensor_orientation"] == "upward"


def test_adcp_without_orientation_is_refused() -> None:
    """The orientation decides the bin depths, so it is never assumed."""
    ds = _adcp()
    del ds.attrs["orientation_yaml"], ds.attrs["orientation_instrument"]
    with pytest.raises(OceanSitesError, match="orientation"):
        to_oceansites(ds, _ADCP_DESCRIPTION)


_GRID = _PROC / "dune2_1_2026_grid.nc"
_STACK = _PROC / "dune2_1_2026_stack.nc"


def test_grid_is_a_gridded_file_on_its_pressure_levels(
    description: dict, tmp_path: Path
) -> None:
    """The grid keeps its values; DEPTH is gsw.z_from_p of its levels, PRES the levels."""
    import gsw

    grid = xr.open_dataset(_GRID)
    out = to_oceansites(grid, description, stack=xr.open_dataset(_STACK))
    assert (
        oceansites_filename(out) == "OS_DUNE2-202607_20260711-20260725_GRD_PT1H-DP50.nc"
    )
    lat = out["LATITUDE"].values[0]
    np.testing.assert_allclose(
        out["DEPTH"].values, -gsw.z_from_p(grid["pressure"].values, lat), rtol=1e-6
    )
    np.testing.assert_array_equal(out["PRES"].values, grid["pressure"].values)
    back = xr.open_dataset(writers.write(out, tmp_path / oceansites_filename(out)))
    np.testing.assert_array_equal(back["TEMP"].values, grid["temperature"].values)
    assert out["TEMP"].attrs["cell_methods"] == "TIME: point DEPTH: point"
    assert out.attrs["time_coverage_resolution"] == "PT1H"


def test_grid_history_lists_the_source_files(description: dict) -> None:
    """§4.2.1: a gridded file names the lower-level files it was made from."""
    out = to_oceansites(
        xr.open_dataset(_GRID), description, stack=xr.open_dataset(_STACK)
    )
    last = out.attrs["history"].splitlines()[-1]
    assert "dune2_1_2026_grid.nc" in last
    assert "dune2_1_2026_2941_stage3.nc" in last


def test_adcp_keeps_the_transducer_pressure() -> None:
    """The transducer's measured pressure (the knock-down record) is on TIME, with its depth."""
    ds = _adcp()
    out = to_oceansites(ds, _ADCP_DESCRIPTION)
    assert out["TRANSDUCER_PRES"].dims == ("TIME",)
    np.testing.assert_array_equal(out["TRANSDUCER_PRES"].values, ds["pressure"].values)
    assert out["TRANSDUCER_PRES"].attrs["sensor_depth"] == 585.0
    assert out["TRANSDUCER_PRES"].attrs["sensor_orientation"] == "downward"
    assert out["TRANSDUCER_PRES"].attrs["sensor_serial_number"] == "1234"
    assert out["TRANSDUCER_PRES"].attrs["sensor_mount"] == "mounted_on_mooring_line"
    assert out["TRANSDUCER_PRES_QC"].dims == ("TIME",)


def test_grid_other_than_provisional_is_refused(description: dict) -> None:
    """A grid records no per-input modes, so a non-P grid cannot be exported."""
    grid = xr.open_dataset(_GRID)
    grid.attrs["data_mode"] = "D"
    with pytest.raises(OceanSitesError, match="provisional"):
        to_oceansites(grid, description)


def test_adcp_flags_bin_pressure_from_the_transducer_and_seabed_on_velocity() -> None:
    """PRES_QC takes the transducer's pressure flags; the seabed flag reaches only velocity.

    The fixture carries no flags, so stage-3-style ones are laid on the real record:
    transducer pressure interpolated (8) for the first ten records, and the deepest
    bin below the seabed (4) throughout.
    """
    ds = _adcp()
    n_time, n_bins = ds.sizes["time"], ds.sizes["N_BINS"]
    pressure_qc = np.ones(n_time, dtype="int8")
    pressure_qc[:10] = 8
    seabed_qc = np.ones((n_time, n_bins), dtype="int8")
    seabed_qc[:, -1] = 4
    ds["pressure_qc"] = ("time", pressure_qc)
    ds["seabed_qc"] = (("time", "N_BINS"), seabed_qc)
    out = to_oceansites(ds, _ADCP_DESCRIPTION)
    finite = np.isfinite(out["PRES"].values)
    expected = np.where(finite, np.broadcast_to(pressure_qc[:, None], finite.shape), 9)
    np.testing.assert_array_equal(out["PRES_QC"].values, expected)
    ucur_qc = out["UCUR_QC"].values
    has_value = np.isfinite(out["UCUR"].values[:, -1])
    assert (ucur_qc[has_value, -1] == 4).all()
    # A passed seabed test leaves the (unchecked, 0) velocity flags as they were.
    assert set(np.unique(ucur_qc[:, 0])) <= {0, 9}
