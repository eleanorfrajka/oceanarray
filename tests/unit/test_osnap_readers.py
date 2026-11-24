"""Tests for the OceanSITES and RODB readers on trimmed real OSNAP files."""

from __future__ import annotations

import logging
import re
import shutil
import warnings
from pathlib import Path

import gsw
import netCDF4
import numpy as np
import pytest
import xarray as xr

from oceanarray import readers
from oceanarray.config import parameters as params
from oceanarray.config.unit_tables import UnitsError
from oceanarray.exceptions import ReaderError
from oceanarray.pipeline.coordinate import apply_declination_to_enu
from oceanarray.pipeline.stage3 import _fill_cf_attrs
from oceanarray.pipeline.units import to_canonical_units
from oceanarray.readers import read_oceansites, read_rodb

OSNAP = Path(__file__).resolve().parents[1] / "fixtures" / "raw" / "osnap"
WHOI_CM = OSNAP / "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc"
GEOMAR_MCTD = OSNAP / "OS_OSNAP-10633_KPO_1111_K7_201408_D_MCTD_OSNAPstyleOceanSITES.nc"
SCRIPPS_MCTD = OSNAP / "CF7_MCTD_2020_22225_50m.nc"
RODB_MICROCAT = OSNAP / "rtwb1_01_2014_001.microcat"
RODB_EDT = OSNAP / "rtwb1_01_2014_11021.edt"
WHOI_ADCP = OSNAP / "OS_OSNAP_CF1_202007_ADCP_173m_cut.nc"
UMM2_ADCP = OSNAP / "OS_OSNAP-UMM2_201407_ADCP_300m_cut.nc"


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
    with pytest.raises(ReaderError, match="none of"):
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


def test_oceansites_drops_records_with_missing_time(tmp_path, caplog):
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

    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        ds = readers.read(path)
    assert re.search("dropped 2 record", caplog.text)
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
def test_padded_tail_is_dropped_through_every_entry_point(tmp_path, entry, caplog):
    """A NaT tail is dropped, counted and recorded, by read() and _read_typed alike."""
    path = _whoi_with_fill_times(tmp_path, slice(-3, None))
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        if entry == "read":
            ds = readers.read(path)
        else:
            ds = readers._read_typed("oceansites", str(path))
    assert re.search(rf"{WHOI_CM.name}: dropped 3 record", caplog.text)
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
    with pytest.raises(ReaderError, match="no record has a timestamp"):
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
    with pytest.raises(ReaderError, match="no record has a timestamp"):
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


def test_rodb_fill_in_a_time_column_drops_the_record(tmp_path, caplog):
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
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        ds = readers.read(path)
    assert re.search("dropped 2 record", caplog.text)
    assert ds.sizes == {"time": full.sizes["time"] - 2}
    assert ds.attrs["n_records_without_time"] == 2
    kept = set(ds["time"].values)
    assert set(np.delete(full["time"].values, [9, 19])) == kept


def test_rodb_header_only_raises_no_data_rows(tmp_path):
    path = _microcat_with_lines(tmp_path, lambda lines: lines[:11])
    with pytest.raises(ReaderError, match="no data rows after the RODB header"):
        read_rodb(path)


def test_rodb_header_and_blank_lines_raises_no_data_rows(tmp_path):
    path = _microcat_with_lines(tmp_path, lambda lines: lines[:11] + ["", "  "])
    with pytest.raises(ReaderError, match="no data rows"):
        read_rodb(path)


def test_rodb_rows_with_fewer_than_four_columns_raise(tmp_path):
    """Data rows without YY MM DD HH (fewer than 4 columns) are refused by name."""
    path = _microcat_with_lines(
        tmp_path, lambda lines: lines[:11] + ["  12.0  3.0", "  13.0  3.1"]
    )
    with pytest.raises(ReaderError, match="fewer than 4 columns"):
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
    with pytest.raises(ReaderError, match="ZZZ"):
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


def test_oceansites_without_standard_name_gets_the_registry_one(tmp_path):
    """A variable with no standard_name is filled from the registry, not left empty."""
    path = tmp_path / WHOI_CM.name
    shutil.copy(WHOI_CM, path)
    with netCDF4.Dataset(path, "a") as nc:
        nc["PRES"].delncattr("standard_name")
    ds = read_oceansites(path)
    assert "standard_name" not in ds["pressure"].attrs
    _fill_cf_attrs(ds)
    assert ds["pressure"].attrs["standard_name"] == "sea_water_pressure"


def test_oceansites_adcp_profile_reads_as_an_adcp():
    ds = read_oceansites(WHOI_ADCP)
    assert dict(ds.sizes) == {"time": 480, params.ADCP_BIN_DIM: 23}
    assert ds["east_velocity"].dims == ("time", params.ADCP_BIN_DIM)
    assert ds["pressure"].dims == ("time",)  # PREST, the head
    assert ds["error_velocity"].attrs["original_name"] == "ECUR"
    assert (np.diff(ds["range"].values) > 0).all()
    with netCDF4.Dataset(WHOI_ADCP) as nc:
        np.testing.assert_array_equal(
            ds["bin_pressure"].values, nc["PRES"][:].filled(np.nan)
        )
    assert ds["bin_pressure"].attrs["bin_pressure_source"] == "originator"
    assert ds.attrs["orientation_instrument"] == "up"
    assert ds.attrs["coordinate_system_source"].startswith("originator")
    assert "MAGVAR median" in ds.attrs["originator_magnetic_declination"]


def test_oceansites_values_outside_the_valid_range_are_missing():
    """BINDEPTH 99999 (no _FillValue, valid_max 12000) is no depth, not 99999 m."""
    with netCDF4.Dataset(UMM2_ADCP) as nc:
        nc.set_auto_mask(False)
        z = nc["BINDEPTH"][:]
        assert "_FillValue" not in nc["BINDEPTH"].ncattrs()
        assert (z > nc["BINDEPTH"].valid_max).any()
    ds = read_oceansites(UMM2_ADCP)
    assert float(ds["bin_depth"].max()) <= 12000
    assert float(ds["bin_pressure"].max()) < 400  # head at 300 m
    assert np.isfinite(ds["range"].values).all()
    assert "set missing: BINDEPTH 3369" in ds.attrs["history"]


def test_oceansites_integer_position_outside_the_valid_range_is_missing(tmp_path):
    """An integer BINDEPTH's 99999 is set missing too, not kept as a depth."""
    path = tmp_path / UMM2_ADCP.name
    with xr.open_dataset(UMM2_ADCP, decode_cf=False) as src:
        src = src.load()
    src["BINDEPTH"] = src["BINDEPTH"].round().astype("int32")
    src.to_netcdf(path)
    ds = read_oceansites(path)
    assert float(ds["bin_depth"].max()) <= 12000
    assert "set missing: BINDEPTH 3369" in ds.attrs["history"]


def test_oceansites_positions_set_missing_others_kept_with_counts(caplog):
    """CF1: BINDEPTH outside the range is missing; ROLL (file valid_min 0) is kept."""
    with netCDF4.Dataset(WHOI_ADCP) as nc:
        nc.set_auto_mask(False)
        roll = nc["ROLL"][:].ravel()
    assert (roll < 0).sum() == 480
    with caplog.at_level("INFO"):
        ds = read_oceansites(WHOI_ADCP)
    np.testing.assert_array_equal(ds["roll"].values, roll)
    history = ds.attrs["history"]
    assert "valid range, set missing: BINDEPTH 7" in history
    assert "valid range, kept: ROLL 480" in history
    assert "kept: ROLL 480" in caplog.text
    assert "history" not in read_oceansites(WHOI_CM).attrs


def test_oceansites_conductivity_with_an_s_per_m_bound_is_kept(caplog):
    """K7 CNDC: valid_max 7 (S/m) on mS/cm values; kept, with a warning."""
    with netCDF4.Dataset(GEOMAR_MCTD) as nc:
        nc.set_auto_mask(False)
        assert nc["CNDC"].units == "mS/cm" and nc["CNDC"].valid_max == 7
        cndc = nc["CNDC"][:].ravel()
    ds = read_oceansites(GEOMAR_MCTD)
    np.testing.assert_array_equal(ds["conductivity"].values, cndc)
    assert "valid range, kept: CNDC 480" in ds.attrs["history"]
    assert any(
        r.levelname == "WARNING" and "kept: CNDC 480" in r.message
        for r in caplog.records
    )


@pytest.mark.parametrize("valid_range", [[0.0], [0.0, 5.0, 9.0], [0.0, 5.0]])
def test_oceansites_valid_range_of_two_values_only(tmp_path, caplog, valid_range):
    """A scalar or 3-element valid_range is ignored with a warning, not a crash."""
    path = tmp_path / "vr.nc"
    shutil.copy(WHOI_ADCP, path)
    with netCDF4.Dataset(path, "a") as nc:
        ecur = nc["ECUR"][:].filled(np.nan)
        nc["ECUR"].delncattr("valid_min")
        nc["ECUR"].delncattr("valid_max")
        nc["ECUR"].valid_range = np.array(valid_range, dtype="f4")
    ds = read_oceansites(path)
    assert int(ds["error_velocity"].isnull().sum()) == np.isnan(ecur).sum()
    kept = f"ECUR {int((ecur < 0).sum())}"
    assert (kept in ds.attrs["history"]) == (len(valid_range) == 2)
    assert ("ECUR valid_range" in caplog.text) == (len(valid_range) != 2)


def test_oceansites_heading_unit_correction_keeps_the_file_s_word(tmp_path):
    path = tmp_path / "hdg.nc"
    shutil.copy(WHOI_ADCP, path)
    with netCDF4.Dataset(path, "a") as nc:
        nc["HEADING"].units = "decibar"
    attrs = read_oceansites(path)["heading"].attrs
    assert attrs["units"] == "degree"
    assert attrs["originator_units"] == "decibar"
    assert "corrected" in attrs["comment"]
    ref = read_oceansites(WHOI_ADCP)["heading"].attrs
    rest = {k: v for k, v in attrs.items() if k not in ("originator_units", "comment")}
    assert rest.keys() == ref.keys()
    assert rest.get("standard_name") == ref.get("standard_name")


@pytest.mark.parametrize("instrdepth", ["dropped", "fill"])
def test_oceansites_profile_with_no_head_depth_is_refused(tmp_path, instrdepth):
    """No PREST and an INSTRDEPTH absent or at its fill: refused, not zero bins."""
    path = tmp_path / "no_head.nc"
    with xr.open_dataset(WHOI_ADCP, decode_cf=False) as src:
        src = src.load().drop_vars("PREST")
    if instrdepth == "dropped":
        src = src.drop_vars("INSTRDEPTH")
    else:
        fill = np.float32(99999.0)
        src["INSTRDEPTH"] = src["INSTRDEPTH"].copy(data=[fill])
        src["INSTRDEPTH"].encoding["_FillValue"] = fill
    src.to_netcdf(path)
    with pytest.raises(ReaderError, match="no head pressure"):
        read_oceansites(path)


def test_oceansites_profile_with_no_bin_depth_is_refused(tmp_path):
    """Every bin depth at its fill: refused, not an ADCP with zero bins."""
    path = tmp_path / "no_bins.nc"
    shutil.copy(WHOI_ADCP, path)
    with netCDF4.Dataset(path, "a") as nc:
        nc["DEPTH"][:] = nc["DEPTH"]._FillValue
    with pytest.raises(ReaderError, match="no bin has a depth"):
        read_oceansites(path)


def test_oceansites_profile_without_velocity_bins_is_refused(tmp_path):
    """A variable on a second dimension it cannot place is refused by name."""
    path = tmp_path / "no_ucur.nc"
    with xr.open_dataset(WHOI_ADCP) as src:
        src.drop_vars("UCUR").to_netcdf(path)
    with pytest.raises(ReaderError, match=r"is on BINDEPTH \(23 levels\)"):
        read_oceansites(path)


def test_oceansites_profile_orientation_contradicting_bin_depths_is_refused(tmp_path):
    path = tmp_path / "down.nc"
    shutil.copy(WHOI_ADCP, path)
    with netCDF4.Dataset(path, "a") as nc:
        nc["UCUR"].sensor_orientation = "downward"
    with pytest.raises(ReaderError, match="sensor_orientation says 'down'"):
        read_oceansites(path)


RODB_BINS = sorted(OSNAP.glob("nocm3_03_2016_20961_bin*.edt"))


def test_rodb_bins_join_on_n_bins_keeping_the_numbers(caplog):
    from oceanarray.readers import read_rodb_bins

    ds = read_rodb_bins(RODB_BINS)
    assert dict(ds.sizes) == {"time": 480, params.ADCP_BIN_DIM: 3, "beam": 4}
    assert ds["bin_number"].values.tolist() == [1, 2, 4]
    assert "no file for bin(s) [3]" in caplog.text
    np.testing.assert_allclose(ds["range"], [4.0, 8.0, 16.0])
    assert ds.attrs["orientation_instrument"] == "down"
    z = read_rodb(RODB_BINS[2])["bin_depth"].values
    np.testing.assert_array_equal(
        ds["bin_pressure"].values[:, 2],
        gsw.p_from_z(-z, ds.attrs["originator_latitude"]),
    )
    assert ds["percent_good"].dims == ("time", params.ADCP_BIN_DIM, "beam")
    assert ds["error_velocity"].attrs["units"] == "cm s-1"


def test_rodb_bins_beams_follow_their_numbers_not_the_column_order(tmp_path):
    from oceanarray.readers import read_rodb_bins

    ref = read_rodb_bins(RODB_BINS)
    paths = [Path(shutil.copy(p, tmp_path)) for p in RODB_BINS]
    for p in paths:
        text = p.read_text()
        p.write_text(text.replace("BEAM1SS:BEAM2SS", "BEAM2SS:BEAM1SS", 1))
    ds = read_rodb_bins(paths)
    assert ds["beam"].values.tolist() == [1, 2, 3, 4]
    np.testing.assert_array_equal(
        ds["amplitude"].sel(beam=1), ref["amplitude"].sel(beam=2)
    )


@pytest.mark.parametrize(
    "change, match",
    [
        ("same_bin", "same bin"),
        ("short", "times differ"),
        ("lowedt", "not a per-bin .edt"),
    ],
)
def test_rodb_bins_refusals(tmp_path, change, match):
    from oceanarray.readers import read_rodb_bins

    paths = [Path(shutil.copy(p, tmp_path)) for p in RODB_BINS]
    if change == "same_bin":
        paths.append(
            Path(shutil.copy(paths[1], tmp_path / "nocm3_03_2016_20961_bin2.edt"))
        )
    elif change == "short":
        paths[1].write_text("\n".join(paths[1].read_text().splitlines()[:-1]) + "\n")
    else:
        paths[1] = paths[1].rename(paths[1].with_suffix(".lowedt"))
    with pytest.raises(ReaderError, match=match):
        read_rodb_bins(paths)
