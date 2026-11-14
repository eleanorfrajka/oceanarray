"""Tests for the OceanSITES and RODB readers on trimmed real OSNAP files."""

from __future__ import annotations

import shutil
import warnings
from pathlib import Path

import netCDF4
import numpy as np
import pytest
import xarray as xr

from oceanarray import readers
from oceanarray.pipeline.coordinate import apply_declination_to_enu
from oceanarray.pipeline.units import UnitsError, to_canonical_units
from oceanarray.readers import read_oceansites, read_rodb

OSNAP = Path(__file__).resolve().parents[1] / "fixtures" / "raw" / "osnap"
WHOI_CM = OSNAP / "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc"
GEOMAR_MCTD = OSNAP / "OS_OSNAP-10633_KPO_1111_K7_201408_D_MCTD_OSNAPstyleOceanSITES.nc"
SCRIPPS_MCTD = OSNAP / "CF7_MCTD_2020_22225_50m.nc"
RODB_MICROCAT = OSNAP / "rtwb1_01_2014_001.microcat"
RODB_EDT = OSNAP / "rtwb1_01_2014_11021.edt"


def test_oceansites_current_meter_is_enu_with_originator_declination():
    ds = read_oceansites(WHOI_CM)
    assert set(ds.data_vars) == {"pressure", "east_velocity", "north_velocity"}
    assert ds.sizes == {"time": 480}
    assert np.issubdtype(ds["time"].dtype, np.datetime64)
    assert ds.attrs["coordinate_system"] == "ENU"
    assert "applied by the originator" in ds.attrs["originator_magnetic_declination"]
    assert ds["east_velocity"].attrs["units"] == "meter/second"
    assert ds["east_velocity"].attrs["original_name"] == "UCUR"
    assert ds.attrs["originator_data_mode"] == "D"
    assert ds.attrs["originator_latitude"] == pytest.approx(59.93, abs=0.01)


def test_oceansites_flags_without_meanings_are_marked_assumed():
    ds = read_oceansites(GEOMAR_MCTD)
    qc = ds["pressure_qc_originator"]
    assert qc.dtype == np.int8
    assert set(np.unique(qc.values)) == {8}  # interpolated pressure, kept as such
    assert "assumed" in qc.attrs["flag_meanings_source"]
    assert "interpolated_value" in qc.attrs["flag_meanings"]
    assert "coordinate_system" not in ds.attrs


def test_oceansites_flag_meanings_from_the_file_are_kept():
    ds = read_oceansites(SCRIPPS_MCTD)
    qc = ds["temperature_qc_originator"]
    assert "flag_meanings_source" not in qc.attrs
    assert qc.attrs["flag_meanings"].startswith("no_qc_performed appears_correct")
    assert ds["conductivity"].attrs["units"] == "S/m"  # as stated, however wrong


def test_oceansites_rejects_a_file_with_no_known_codes(tmp_path):
    path = tmp_path / "x.nc"
    xr.Dataset({"FOO": ("TIME", [1.0])}, coords={"TIME": [0.0]}).to_netcdf(path)
    with pytest.raises(ValueError, match="none of"):
        read_oceansites(path)


def test_oceansites_one_record_file_keeps_its_time_axis(tmp_path):
    """A single-record (TIME=1, DEPTH=1) file must not squeeze TIME to a scalar.

    `squeeze(drop=True)` would drop the length-1 TIME as well, which then fails both the
    data path (`dims="time"` on a 0-d value) and the QC path (`dims[0]` on a scalar).
    """
    path = tmp_path / "onerec.nc"
    xr.Dataset(
        {
            "TEMP": (
                ("TIME", "DEPTH"),
                np.array([[4.2]], dtype="float32"),
                {"units": "degree_Celsius", "standard_name": "sea_water_temperature"},
            ),
            "TEMP_QC": (("TIME", "DEPTH"), np.array([[1]], dtype="int8")),
        },
        coords={
            "TIME": np.array(["2026-01-01"], dtype="datetime64[ns]"),
            "DEPTH": [100.0],
        },
    ).to_netcdf(path)

    ds = read_oceansites(path)
    assert ds["temperature"].dims == ("time",)
    assert ds["temperature"].sizes == {"time": 1}
    assert ds["temperature_qc_originator"].dims == ("time",)


def test_oceansites_drops_records_with_missing_time(tmp_path):
    """NaT padding at the tail of TIME is dropped at read time with a count.

    Some originators pad a record that ended early with missing timestamps; left in,
    the NaT entries make the time index non-monotonic and break stage-2 trimming.
    """
    path = tmp_path / "padded.nc"
    times = np.array(
        ["2020-01-01", "2020-01-02", "2020-01-03", "NaT", "NaT"],
        dtype="datetime64[ns]",
    )
    xr.Dataset(
        {
            "TEMP": (
                "TIME",
                np.array([4.0, 4.1, 4.2, np.nan, np.nan], dtype="float32"),
                {"units": "degree_Celsius", "standard_name": "sea_water_temperature"},
            ),
        },
        coords={"TIME": times},
    ).to_netcdf(path)

    with pytest.warns(UserWarning, match="dropped 2 record"):
        ds = readers.read(path)
    assert ds.sizes == {"time": 3}
    assert not np.isnat(ds["time"].values).any()


def test_oceansites_non_datetime_calendar_is_not_dropped(tmp_path):
    """A non-standard calendar decodes to cftime objects; the drop must not crash.

    `np.isnat` only accepts datetime64; the reader guards on dtype and returns such a
    file unchanged rather than raising.
    """
    path = tmp_path / "cftime.nc"
    ds = xr.Dataset(
        {
            "TEMP": (
                "TIME",
                np.array([4.0, 4.1, 4.2], dtype="float32"),
                {"units": "degree_Celsius", "standard_name": "sea_water_temperature"},
            ),
        },
        coords={"TIME": np.array([0.0, 1.0, 2.0])},
    )
    ds["TIME"].attrs = {"units": "days since 2020-01-01", "calendar": "360_day"}
    ds.to_netcdf(path)

    out = readers.read(path)
    assert out.sizes == {"time": 3}


def _whoi_with_fill_times(tmp_path: Path, index: slice) -> Path:
    """Copy the WHOI current-meter file with TIME[index] set to its fill (NaN)."""
    path = tmp_path / WHOI_CM.name
    shutil.copy(WHOI_CM, path)
    with netCDF4.Dataset(path, "a") as nc:
        time = nc["TIME"][:].filled(np.nan)
        time[index] = np.nan
        nc["TIME"][:] = time
    return path


@pytest.mark.parametrize("entry", ["read", "_read_typed"])
def test_padded_tail_is_dropped_through_every_entry_point(tmp_path, entry):
    """A NaT tail is dropped, counted and recorded, by read() and _read_typed alike."""
    path = _whoi_with_fill_times(tmp_path, slice(-3, None))
    with pytest.warns(UserWarning, match=rf"{WHOI_CM.name}: dropped 3 record"):
        if entry == "read":
            ds = readers.read(path)
        else:
            ds = readers._read_typed("oceansites", str(path))
    assert ds.sizes == {"time": 477}
    assert ds.attrs["n_records_without_time"] == 3
    assert (np.diff(ds["time"].values) > np.timedelta64(0, "s")).all()


def test_read_oceansites_itself_keeps_the_padding(tmp_path):
    """The format reader is faithful; the drop happens once, at the choke point."""
    path = _whoi_with_fill_times(tmp_path, slice(-3, None))
    ds = read_oceansites(path)
    assert ds.sizes == {"time": 480}
    assert int(np.isnat(ds["time"].values).sum()) == 3


def test_all_missing_time_raises(tmp_path):
    path = _whoi_with_fill_times(tmp_path, slice(None))
    with pytest.raises(ValueError, match="no record has a timestamp"):
        readers.read(path)


def test_zero_length_time_raises(tmp_path):
    """A file with a zero-length time dimension raises, not returns an empty dataset."""
    path = tmp_path / "empty.nc"
    xr.Dataset(
        {
            "TEMP": (
                "TIME",
                np.array([], dtype="float32"),
                {"units": "degree_Celsius", "standard_name": "sea_water_temperature"},
            ),
        },
        coords={"TIME": np.array([], dtype="datetime64[ns]")},
    ).to_netcdf(path)
    with pytest.raises(ValueError, match="no record has a timestamp"):
        readers.read(path)


def test_non_standard_calendar_on_a_real_file_is_returned_whole(tmp_path):
    """cftime time cannot hold NaT; the guard returns the dataset unchanged."""
    path = tmp_path / WHOI_CM.name
    shutil.copy(WHOI_CM, path)
    with netCDF4.Dataset(path, "a") as nc:
        nc["TIME"].setncattr("calendar", "360_day")
    ds = readers.read(path)
    assert ds.sizes == {"time": 480}
    assert "n_records_without_time" not in ds.attrs


def test_complete_file_carries_no_dropped_count():
    ds = readers.read(WHOI_CM)
    assert "n_records_without_time" not in ds.attrs


def _microcat_with_lines(tmp_path: Path, edit) -> Path:
    """Copy the RODB microcat into tmp_path with its lines passed through *edit*."""
    path = tmp_path / RODB_MICROCAT.name
    path.write_text("\n".join(edit(RODB_MICROCAT.read_text().splitlines())) + "\n")
    return path


def test_rodb_fill_in_a_time_column_drops_the_record(tmp_path):
    """HH = -9999 and YY = -9999 give no time (not midnight) and are dropped."""

    def edit(lines):
        hh = lines[20].split()
        hh[3] = "-9999"
        yy = lines[30].split()
        yy[0] = "-9999"
        lines[20], lines[30] = "  ".join(hh), "  ".join(yy)
        return lines

    path = _microcat_with_lines(tmp_path, edit)
    full = read_rodb(RODB_MICROCAT)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        raw = read_rodb(path)
    assert int(np.isnat(raw["time"].values).sum()) == 2
    with pytest.warns(UserWarning, match="dropped 2 record"):
        ds = readers.read(path)
    assert ds.sizes == {"time": full.sizes["time"] - 2}
    assert ds.attrs["n_records_without_time"] == 2
    kept = set(ds["time"].values)
    assert set(np.delete(full["time"].values, [9, 19])) == kept


def test_rodb_header_only_raises_no_data_rows(tmp_path):
    path = _microcat_with_lines(tmp_path, lambda lines: lines[:11])
    with pytest.raises(ValueError, match="no data rows after the RODB header"):
        read_rodb(path)


def test_rodb_header_and_blank_lines_raises_no_data_rows(tmp_path):
    path = _microcat_with_lines(tmp_path, lambda lines: lines[:11] + ["", "  "])
    with pytest.raises(ValueError, match="no data rows"):
        read_rodb(path)


def test_rodb_rows_with_fewer_than_four_columns_raise(tmp_path):
    """Data rows without YY MM DD HH (fewer than 4 columns) are refused by name."""
    path = _microcat_with_lines(
        tmp_path, lambda lines: lines[:11] + ["  12.0  3.0", "  13.0  3.1"]
    )
    with pytest.raises(ValueError, match="fewer than 4 columns"):
        read_rodb(path)


def test_oceansites_keeps_all_records_when_time_is_complete():
    """A file with no NaT padding is returned unchanged (480 records)."""
    ds = read_oceansites(WHOI_CM)
    assert ds.sizes == {"time": 480}
    assert not np.isnat(ds["time"].values).any()


def test_rodb_microcat_names_units_fill_and_time():
    ds = read_rodb(RODB_MICROCAT)
    assert list(ds.data_vars) == ["temperature", "conductivity", "pressure"]
    assert ds["conductivity"].attrs["units"] == "mS/cm"
    assert int(ds["conductivity"].isnull().sum()) == 1  # the one -9999 fill
    # decimal hours rounded to the nearest second, not truncated to the minute
    t0 = ds["time"].values[0].astype("datetime64[s]")
    assert str(t0).endswith(":01")
    assert ds.attrs["originator_SerialNumber"] == "11335"
    assert ds.attrs["originator_longitude"] == pytest.approx(-12.705, abs=1e-3)


def test_rodb_edt_keeps_real_minus_nine_and_records_declination():
    ds = read_rodb(RODB_EDT)
    assert (
        (ds["east_velocity"].values == -9.0).any()
        or (ds["north_velocity"].values == -9.0).any()
        or (ds["up_velocity"].values == -9.0).any()
    )
    assert ds.attrs["originator_magnetic_declination"] == -7.33
    assert ds.attrs["coordinate_system"] == "ENU"
    assert ds["east_velocity"].attrs["units"] == "cm s-1"
    step = np.diff(ds["time"].values[:2])[0].astype("timedelta64[s]")
    assert step == np.timedelta64(1200, "s")  # 18.0000 -> 18.3333 h is 20 min


def test_rodb_unknown_column_raises(tmp_path):
    path = tmp_path / "x.microcat"
    path.write_text("Mooring = m\nColumns = YY:MM:DD:HH:ZZZ\n2014 7 17 12.5 1.0\n")
    with pytest.raises(ValueError, match="ZZZ"):
        read_rodb(path)


def test_units_pass_converts_the_imported_spellings():
    cm = to_canonical_units(read_oceansites(WHOI_CM))
    assert cm["east_velocity"].attrs["units"] == "m s-1"
    assert cm["pressure"].attrs["units"] == "dbar"
    edt = read_rodb(RODB_EDT)
    raw = edt["east_velocity"].values.copy()
    out = to_canonical_units(edt)
    assert out["east_velocity"].attrs["units"] == "m s-1"
    np.testing.assert_allclose(out["east_velocity"].values, raw * 0.01)
    mctd = to_canonical_units(read_oceansites(GEOMAR_MCTD))
    assert mctd["salinity"].attrs["units"] == "1"


def test_declination_is_not_applied_twice_to_an_imported_file():
    ds = read_rodb(RODB_EDT)
    ds = to_canonical_units(ds)
    before = ds["east_velocity"].values.copy()
    out = apply_declination_to_enu(ds, 57.47, -12.71, latlon_source="yaml")
    np.testing.assert_array_equal(out["east_velocity"].values, before)
    assert "magnetic_declination" not in out.attrs


def test_mislabelled_conductivity_converts_out_of_range():
    """The Scripps file's 'S/m' makes the units pass give ~350 mS/cm."""
    out = to_canonical_units(read_oceansites(SCRIPPS_MCTD))
    assert float(np.nanmedian(out["conductivity"].values)) > 80.0
    assert issubclass(UnitsError, ValueError)


def test_lowedt_is_tagged_low_passed(tmp_path):
    path = tmp_path / "rtwb1_01_2014_11021.lowedt"
    path.write_text(RODB_EDT.read_text())
    ds = read_rodb(path)
    assert ds.attrs["originator_low_passed"].startswith("2-day low-pass")
