from datetime import datetime

import numpy as np
import pytest
import xarray as xr

from oceanarray import logger, utilities
from oceanarray.utilities import drop_all_zero_vars, iso8601_duration_from_seconds

logger.disable_logging()


def test_concat_with_scalar_vars_preserves_scalars():
    ds1 = xr.Dataset({"TEMP": ("TIME", [1.0, 2.0]), "meta": xr.DataArray(42)})
    ds2 = xr.Dataset({"TEMP": ("TIME", [3.0, 4.0]), "meta": xr.DataArray(99)})
    out = utilities.concat_with_scalar_vars([ds1, ds2], dim="TIME")
    assert "meta" in out
    assert out["meta"].ndim == 0
    assert out["meta"].item() in [42, 99]


def test_check_necessary_variables_pass():
    ds = xr.Dataset({"TEMP": ("TIME", [1, 2, 3]), "PRES": ("TIME", [10, 20, 30])})
    utilities.check_necessary_variables(ds, ["TEMP", "PRES"])


def test_check_necessary_variables_fail():
    ds = xr.Dataset({"TEMP": ("TIME", [1, 2, 3])})
    with pytest.raises(KeyError):
        utilities.check_necessary_variables(ds, ["TEMP", "PRES"])


def test_get_time_key_standard():
    ds = xr.Dataset(coords={"TIME": ("TIME", [datetime(2000, 1, 1)])})
    assert utilities.get_time_key(ds) == "TIME"


def test_get_time_key_nonstandard():
    ds = xr.Dataset(coords={"DATETIME": ("time", [np.datetime64("2020-01-01")])})
    assert utilities.get_time_key(ds) == "DATETIME"


def test_get_time_key_raises():
    ds = xr.Dataset(coords={"DEPTH": ("DEPTH", [10, 20])})
    with pytest.raises(ValueError):
        utilities.get_time_key(ds)


def test_get_dims_basic():
    ds = xr.Dataset(
        {"TEMP": (("TIME", "DEPTH"), np.zeros((3, 2)))},
        coords={"TIME": [0, 1, 2], "DEPTH": [10, 20]},
    )
    pres_key, time_key, pres_dim, time_dim = utilities.get_dims(ds)
    assert pres_key == "DEPTH"
    assert time_key == "TIME"
    assert pres_dim == "DEPTH"
    assert time_dim == "TIME"


def test_is_iso8601_utc_valid_formats():
    assert utilities.is_iso8601_utc("2020-01-01T00:00:00Z")
    assert utilities.is_iso8601_utc("2020/01/01T00:00:00Z")


def test_is_iso8601_utc_invalid_format():
    assert not utilities.is_iso8601_utc("01-01-2020 00:00")


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (86400, "P1D"),
        (172800, "P2D"),
        (3600, "PT1H"),
        (7200, "PT2H"),
        (1800, "PT30M"),
        (60, "PT1M"),
        (90, "PT1M"),  # rounded
        (30, "PT30S"),
        (0, "PT0S"),
    ],
)
def test_iso8601_duration_from_seconds(seconds, expected):
    assert iso8601_duration_from_seconds(seconds) == expected


def test_is_iso8601_utc():
    assert utilities.is_iso8601_utc("2023-06-09T12:00:00Z")
    assert utilities.is_iso8601_utc("2023/06/09T12:00:00Z")
    assert not utilities.is_iso8601_utc("2023-06-09 12:00:00")


# ---------------------------------------------------------------------------
# drop_all_zero_vars
# ---------------------------------------------------------------------------


def test_drop_all_zero_vars_drops_all_zero_float():
    ds = xr.Dataset(
        {
            "amplitude_beam1": ("time", np.zeros(5, dtype=float)),
            "temperature": ("time", np.ones(5)),
        }
    )
    out = drop_all_zero_vars(ds, ["amplitude_beam"])
    assert "amplitude_beam1" not in out.data_vars
    assert "temperature" in out.data_vars


def test_drop_all_zero_vars_keeps_mixed_float():
    ds = xr.Dataset(
        {
            "amplitude_beam1": ("time", np.array([0.0, 1.0, 0.0])),
        }
    )
    out = drop_all_zero_vars(ds, ["amplitude_beam"])
    assert "amplitude_beam1" in out.data_vars


def test_drop_all_zero_vars_all_nan_dropped():
    ds = xr.Dataset(
        {
            "amplitude_beam1": ("time", np.array([np.nan, np.nan, np.nan])),
        }
    )
    out = drop_all_zero_vars(ds, ["amplitude_beam"])
    assert "amplitude_beam1" not in out.data_vars


def test_drop_all_zero_vars_integer_zero_dropped():
    ds = xr.Dataset(
        {
            "amplitude_beam1": ("time", np.zeros(5, dtype=np.int16)),
        }
    )
    out = drop_all_zero_vars(ds, ["amplitude_beam"])
    assert "amplitude_beam1" not in out.data_vars


def test_drop_all_zero_vars_nonmatching_prefix_kept():
    ds = xr.Dataset(
        {
            "temperature": ("time", np.zeros(5)),
        }
    )
    out = drop_all_zero_vars(ds, ["amplitude_beam"])
    assert "temperature" in out.data_vars


# ---------------------------------------------------------------------------
# extract_inline_instruments — beacon_id splitting
# ---------------------------------------------------------------------------


def test_extract_inline_instruments_beacon_id():
    """serial='16430, R01-024' → primary serial '16430', beacon_id 'R01-024'."""
    inline = [
        {
            "instrument": "aquadopp",
            "serial": "16430, R01-024",
            "filename": "16430.dat",
            "hab_bottom": 1.5,
        }
    ]
    result = utilities.extract_inline_instruments(inline)
    assert len(result) == 1
    entry = result[0]
    assert entry["serial"] == "16430"
    assert entry["beacon_id"] == "R01-024"


def test_extract_inline_instruments_no_comma_serial():
    """serial without comma passes through unchanged; no beacon_id key added."""
    inline = [{"instrument": "microcat", "serial": 7518, "filename": "data.cnv"}]
    result = utilities.extract_inline_instruments(inline)
    assert result[0]["serial"] == 7518
    assert "beacon_id" not in result[0]


def test_extract_inline_instruments_hab_bottom_to_hab():
    """hab_bottom is copied to hab when hab is absent."""
    inline = [
        {
            "instrument": "aquadopp",
            "serial": "1234",
            "filename": "f.dat",
            "hab_bottom": 2.0,
        }
    ]
    result = utilities.extract_inline_instruments(inline)
    assert result[0]["hab"] == 2.0


def test_extract_inline_instruments_skip_entry_included():
    """Entry with skip=True and no filename should still be returned."""
    inline = [{"instrument": "microcat", "serial": 9999, "skip": True}]
    result = utilities.extract_inline_instruments(inline)
    assert len(result) == 1


def test_extract_inline_instruments_no_instrument_excluded():
    """Entry without 'instrument' key is filtered out."""
    inline = [{"serial": 1234, "filename": "data.cnv"}]
    result = utilities.extract_inline_instruments(inline)
    assert result == []


def test_instrument_entries_merges_clamp_and_inline():
    """clamp entries come first, then the processable inline ones (normalised)."""
    cfg = {
        "clamp": [{"serial": 1, "instrument": "microcat"}],
        "inline": [
            {"type": "rope"},  # passive hardware, dropped
            {"instrument": "aquadopp", "serial": 9, "filename": "a.dat"},
        ],
    }
    entries = utilities.instrument_entries(cfg)
    assert [e["serial"] for e in entries] == [1, 9]
    assert entries[1]["source"] == "inline"


def test_instrument_entries_legacy_instruments_key():
    """The legacy 'instruments' key is read when 'clamp' is absent."""
    cfg = {"instruments": [{"serial": 5, "instrument": "microcat"}]}
    assert [e["serial"] for e in utilities.instrument_entries(cfg)] == [5]


def test_instrument_entries_tolerates_null_clamp_and_inline():
    """A present-but-null 'clamp:' / 'inline:' yields no instruments, not a crash."""
    assert utilities.instrument_entries({"clamp": None, "inline": None}) == []


# ---------------------------------------------------------------------------
# get_dims — DataArray and multi-dim pressure branches
# ---------------------------------------------------------------------------


def test_get_dims_with_dataarray():
    """Passing a DataArray is converted to Dataset internally.

    xr.DataArray.to_dataset() requires a name, so we use a named DataArray.
    """
    da = xr.DataArray(
        np.zeros((3, 2)),
        dims=("TIME", "DEPTH"),
        coords={"TIME": [0, 1, 2], "DEPTH": [10, 20]},
        name="pressure",
    )
    pres_key, time_key, pres_dim, time_dim = utilities.get_dims(da)
    assert time_key == "TIME"
    assert pres_key == "pressure"


def test_get_dims_2d_pressure():
    """Pressure with dims (time, depth) — pres_dim should be the non-time dim."""
    ds = xr.Dataset(
        {
            "temperature": (("TIME", "DEPTH"), np.zeros((3, 4))),
            "pressure": (("TIME", "DEPTH"), np.zeros((3, 4))),
        },
        coords={"TIME": np.arange(3), "DEPTH": np.arange(4)},
    )
    pres_key, time_key, pres_dim, time_dim = utilities.get_dims(ds)
    assert pres_key == "pressure"
    assert time_key == "TIME"
    assert pres_dim == "DEPTH"
    assert time_dim == "TIME"


# ---------------------------------------------------------------------------
# _nice_colorbar_bounds
# ---------------------------------------------------------------------------


def test_nice_colorbar_bounds_standard():
    bounds = utilities._nice_colorbar_bounds(0.5, 7.5, n=20)
    assert np.all(np.diff(bounds) > 0), "bounds must be monotonically increasing"
    assert np.all(np.isfinite(bounds))
    # Boundaries span the data range.
    assert bounds[0] <= 0.5 and bounds[-1] >= 7.5
    # Step snapped to the nice 1/2/2.5/5 family, and roughly n levels.
    step = bounds[1] - bounds[0]
    frac = step / 10.0 ** np.floor(np.log10(step))
    assert np.isclose(frac, min((1.0, 2.0, 2.5, 5.0), key=lambda c: abs(c - frac)))
    assert 10 <= len(bounds) <= 40  # count is adaptive, no longer fixed at n+1


def test_nice_colorbar_ticks_align_to_bounds():
    """Labelled ticks must land on discrete colour-step boundaries.

    Regression guard: the boundary step used to be rounded to any 1-sig-fig value
    (0.03, 30), so round ticks (0.2, 100) fell between the colour changes rather
    than on them (Eleanor 2026-08-18).
    """
    for vmin, vmax in [(27.5, 27.95), (82.0, 808.0), (0.5, 7.5)]:
        bounds = utilities._nice_colorbar_bounds(vmin, vmax)
        ticks = utilities.nice_colorbar_ticks(float(bounds[0]), float(bounds[-1]))
        for t in ticks:
            assert np.any(np.isclose(t, bounds)), (
                f"tick {t} off the boundary grid for ({vmin}, {vmax})"
            )


def test_nice_colorbar_bounds_zero_span():
    """vmin == vmax: function must not crash and must return n+1 values."""
    bounds = utilities._nice_colorbar_bounds(5.0, 5.0, n=20)
    assert len(bounds) == 21
    assert np.all(np.isfinite(bounds))


def test_nice_colorbar_bounds_salinity_range():
    """Typical salinity range gives a clean 0.02 step covering the data."""
    bounds = utilities._nice_colorbar_bounds(34.78, 35.14, n=20)
    assert bounds[0] <= 34.78 and bounds[-1] >= 35.14
    assert np.isclose(bounds[1] - bounds[0], 0.02)


def _touch(path, mtime):
    """Create *path* and set its modification time to *mtime* (epoch seconds)."""
    path.write_text("x")
    import os

    os.utime(path, (mtime, mtime))


def test_should_skip_regeneration_force(tmp_path):
    """force=True never skips, even when the output is up to date."""
    out = tmp_path / "out.nc"
    src = tmp_path / "src.raw"
    _touch(src, 100)
    _touch(out, 200)
    assert utilities.should_skip_regeneration(out, True, False, src) is False


def test_should_skip_regeneration_missing_output(tmp_path):
    """A non-existent output is never skipped."""
    out = tmp_path / "out.nc"
    src = tmp_path / "src.raw"
    _touch(src, 100)
    assert utilities.should_skip_regeneration(out, False, False, src) is False


def test_should_skip_regeneration_skip_existing(tmp_path):
    """skip_existing=True skips a present output regardless of source mtimes."""
    out = tmp_path / "out.nc"
    src = tmp_path / "src.raw"
    _touch(out, 100)
    _touch(src, 999)  # newer source, but skip_existing ignores mtimes
    assert utilities.should_skip_regeneration(out, False, True, src) is True


def test_should_skip_regeneration_stale_source(tmp_path):
    """A source newer than the output forces regeneration (no skip)."""
    out = tmp_path / "out.nc"
    yaml = tmp_path / "m.mooring.yaml"
    _touch(out, 100)
    _touch(yaml, 200)  # e.g. edited YAML after the output was built
    assert utilities.should_skip_regeneration(out, False, False, yaml) is False


def test_should_skip_regeneration_up_to_date(tmp_path):
    """An output newer than all sources is skipped."""
    out = tmp_path / "out.nc"
    src = tmp_path / "src.raw"
    yaml = tmp_path / "m.mooring.yaml"
    _touch(src, 100)
    _touch(yaml, 100)
    _touch(out, 200)
    assert utilities.should_skip_regeneration(out, False, False, src, yaml) is True


def test_dms_to_deg_plain_float():
    """A plain decimal-degree string parses unchanged, sign preserved."""
    assert utilities._parse_dms("-27.8") == pytest.approx(-27.8)
    assert utilities._parse_dms("65.7319") == pytest.approx(65.7319)


def test_dms_to_deg_hemisphere_suffix():
    """A trailing S/W hemisphere negates the summed magnitude."""
    assert utilities._parse_dms("29 25.878 W") == pytest.approx(-29.4313, abs=1e-4)
    assert utilities._parse_dms("65 34.004 N") == pytest.approx(65.56673, abs=1e-4)


def test_dms_to_deg_signed_degrees_negate_whole_value():
    """A leading minus signs the whole magnitude, not just the degrees field.

    Regression: ``"-27 48.000"`` is -(27 + 48/60) = -27.8, not -27 + 48/60 = -26.2.
    """
    assert utilities._parse_dms("-27 48.000") == pytest.approx(-27.8)


def test_format_latlon_hemisphere_from_sign():
    """format_latlon derives N/S and E/W from the sign; magnitude is unsigned."""
    lat_s, lon_s = utilities.format_latlon(65.7319, -27.8)
    assert lat_s == "65.7319° N"
    assert lon_s == "27.8000° W"
    lat_s, lon_s = utilities.format_latlon(-12.5, 42.0, ndp=1)
    assert lat_s == "12.5° S"
    assert lon_s == "42.0° E"


def test_find_best_dtype_timedelta_passthrough_beats_name_rules():
    """A timedelta64 variable is preserved even when its name would force a cast.

    The dtype-kind passthrough (kind ``"m"``) must take precedence over the
    name-based rules: ``serial`` would otherwise cast to ``int32`` and ``*_qc``
    to ``int8``, silently corrupting a duration variable.
    """
    td = xr.DataArray(np.array([1, 2, 3], dtype="timedelta64[ns]"), dims="time")
    assert utilities.find_best_dtype("serial", td) is np.timedelta64
    assert utilities.find_best_dtype("gap_qc", td) is np.timedelta64


def test_parse_latlon_with_source_unknown_when_absent():
    """An empty config yields (0, 0) tagged as an unknown source."""
    lat, lon, source = utilities.parse_latlon_with_source({})
    assert (lat, lon) == (0.0, 0.0)
    assert source.startswith("unknown")


def test_parse_latlon_with_source_skips_zero_placeholder():
    """A (0, 0) seabed placeholder is skipped for a later key with a real fix."""
    cfg = {
        "seabed_latitude": 0.0,
        "seabed_longitude": 0.0,
        "deployment_latitude": "-27 48.000",
        "deployment_longitude": "10 30.000 E",
    }
    lat, lon, source = utilities.parse_latlon_with_source(cfg)
    assert lat == pytest.approx(-27.8)
    assert lon == pytest.approx(10.5)
    assert source == "deployment_latitude/deployment_longitude"


def test_parse_latlon_with_source_skips_unparseable_key():
    """An unparseable coordinate string is skipped for the next valid key pair."""
    cfg = {
        "seabed_latitude": "not-a-coord",
        "seabed_longitude": "also-bad",
        "latitude": "65 34.004 N",
        "longitude": "29 25.878 W",
    }
    lat, lon, source = utilities.parse_latlon_with_source(cfg)
    assert lat == pytest.approx(65.56673, abs=1e-4)
    assert lon == pytest.approx(-29.4313, abs=1e-4)
    assert source == "latitude/longitude"
