"""Tests for the single netCDF writer, :func:`oceanarray.writers.write`.

The guarantees are oracle-free: one write path, compression present on every numeric
variable, lossless round-trip, pinned time encoding, attribute cleaning (``None``
dropped), and name sanitization. The migration check against the old per-path writer
was removed with the writer cleanup — after that branch there is no old writer to match.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from oceanarray.utilities import cast_output_dtypes
from oceanarray.writers import write
from oceanarray.writers.netcdf import (
    _DROP,
    _netcdf_safe_attr_value,
    _netcdf_safe_names,
    _prepare,
)

#: Root of the committed real-data fixtures (trimmed dune2_1_2026 mooring).
FIXTURE_PROC = (
    Path(__file__).resolve().parents[1] / "fixtures" / "proc" / "dune2_1_2026"
)

# Committed dune2_1_2026 outputs, one per write path (per-instrument stage1/2/3 for
# a scalar microcat and a velocity aquadopp, plus the mooring grid and stack). Used
# as real-data inputs for the compression, round-trip, time-encoding and name tests.
_FIXTURES = {
    "microcat_stage1": FIXTURE_PROC / "microcat" / "dune2_1_2026_2941_stage1.nc",
    "microcat_stage2": FIXTURE_PROC / "microcat" / "dune2_1_2026_2941_stage2.nc",
    "microcat_stage3": FIXTURE_PROC / "microcat" / "dune2_1_2026_2941_stage3.nc",
    "aquadopp_stage1": FIXTURE_PROC / "aquadopp" / "dune2_1_2026_9920_stage1.nc",
    "aquadopp_stage2": FIXTURE_PROC / "aquadopp" / "dune2_1_2026_9920_stage2.nc",
    "aquadopp_stage3": FIXTURE_PROC / "aquadopp" / "dune2_1_2026_9920_stage3.nc",
    "grid": FIXTURE_PROC / "dune2_1_2026_grid.nc",
    "stack": FIXTURE_PROC / "dune2_1_2026_stack.nc",
}


def _open_raw(path: Path) -> xr.Dataset:
    """Open *path* without CF decoding, so raw stored values are visible."""
    return xr.open_dataset(path, engine="netcdf4", decode_cf=False)


def _write_uncompressed(ds: xr.Dataset, path: Path) -> None:
    """Write *ds* through the writer's full pipeline but with no compression.

    Baseline for the losslessness test: `_prepare(ds, compress=False)` does everything
    :func:`write` does (cast, clean, pin time) except the zlib filter, so any decoded
    difference is compression, not the pipeline.
    """
    _prepare(ds, compress=False).to_netcdf(path, engine="netcdf4", format="NETCDF4")


# ---------------------------------------------------------------------------
# Guard: one write path
# ---------------------------------------------------------------------------


def test_single_to_netcdf_path():
    """``to_netcdf(`` appears in ``oceanarray/`` only in the writer and the allowlist.

    The allowlist carries its reason so adding a seventh write path is a visible
    decision, not a silent one.
    """
    pkg = Path(__file__).resolve().parents[2] / "oceanarray"
    allowed = {
        # legacy physics code; rewrite deferred (per-file-ignores mark it so).
        pkg / "tools" / "rapid_interp.py",
    }
    offenders = []
    for py in pkg.rglob("*.py"):
        if pkg / "writers" in py.parents or pkg / "legacy" in py.parents:
            continue
        if py in allowed:
            continue
        # encoding pinned: some package files carry UTF-8 glyphs (e.g. S m⁻¹) that
        # Path.read_text would fail to decode under Windows' cp1252 default.
        if "to_netcdf(" in py.read_text(encoding="utf-8"):
            offenders.append(str(py.relative_to(pkg)))
    assert not offenders, f"unexpected to_netcdf outside the writer: {offenders}"


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(_FIXTURES))
def test_compression_on_every_numeric_variable(key, tmp_path):
    """Every variable with a dimension and a compressible dtype is zlib+shuffle."""
    src = xr.open_dataset(_FIXTURES[key], engine="netcdf4")
    out = tmp_path / "out.nc"
    write(src, out)

    reopened = xr.open_dataset(out, engine="netcdf4")
    try:
        checked = 0
        for name, var in reopened.variables.items():
            if var.ndim >= 1 and var.dtype.kind not in ("O", "U", "S"):
                enc = reopened[name].encoding
                assert enc.get("zlib") is True, f"{name} not zlib-compressed"
                assert enc.get("shuffle") is True, f"{name} missing shuffle filter"
                assert enc.get("complevel") == 4, f"{name} wrong complevel"
                checked += 1
        assert checked > 0
    finally:
        reopened.close()
        src.close()


@pytest.mark.parametrize("key", list(_FIXTURES))
def test_roundtrip_lossless(key, tmp_path):
    """Compressed output decodes identically to an uncompressed write of the same input.

    Proves compression is lossless and that no lossy quantization slipped in.
    """
    src = xr.open_dataset(_FIXTURES[key], engine="netcdf4")
    compressed = tmp_path / "compressed.nc"
    plain = tmp_path / "plain.nc"
    write(src, compressed)
    _write_uncompressed(src, plain)

    a = xr.open_dataset(compressed, engine="netcdf4")
    b = xr.open_dataset(plain, engine="netcdf4")
    try:
        xr.testing.assert_identical(a, b)
    finally:
        a.close()
        b.close()
        src.close()


# ---------------------------------------------------------------------------
# Datetime and coordinate attribute handling
# ---------------------------------------------------------------------------


def test_datetime_units_attr_does_not_break_write(tmp_path):
    """A decoded time coordinate still carrying a ``units`` attr writes without error."""
    time = xr.date_range("2026-01-01", periods=4, freq="h", use_cftime=False)
    ds = xr.Dataset(
        {"temperature": ("time", np.linspace(4.0, 8.0, 4))},
        coords={"time": time},
    )
    # A stray units attr on a decoded datetime coord is exactly what collides
    # with xarray's time encoding at write time unless it is moved to encoding.
    ds["time"].attrs["units"] = "seconds since 1970-01-01"
    out = tmp_path / "time.nc"
    write(ds, out)  # must not raise
    assert out.exists()


def test_non_time_coordinate_keeps_units(tmp_path):
    """The datetime attrs-move must not strip ``units`` from non-time coordinates."""
    ds = xr.Dataset(
        {"temperature": (("depth",), np.array([4.0, 5.0, 6.0]))},
        coords={"depth": ("depth", np.array([10.0, 20.0, 30.0]))},
    )
    ds["depth"].attrs["units"] = "m"
    out = tmp_path / "depth.nc"
    write(ds, out)

    reopened = xr.open_dataset(out, engine="netcdf4")
    try:
        assert reopened["depth"].attrs.get("units") == "m"
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# Name sanitization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(_FIXTURES))
def test_name_sanitization_is_noop_on_fixtures(key):
    """Real stage files carry canonical names, so sanitization renames nothing."""
    src = xr.open_dataset(_FIXTURES[key], engine="netcdf4")
    try:
        assert _netcdf_safe_names(src) is src
    finally:
        src.close()


def test_name_sanitization_renames_slash(tmp_path):
    """A ``/`` in a variable name is replaced and the original recorded."""
    ds = xr.Dataset({"cond0S/m": ("x", np.arange(3.0))}, coords={"x": np.arange(3)})
    out = tmp_path / "slash.nc"
    write(ds, out)

    reopened = xr.open_dataset(out, engine="netcdf4")
    try:
        assert "cond0S_m" in reopened.variables
        assert reopened["cond0S_m"].attrs.get("original_name") == "cond0S/m"
    finally:
        reopened.close()


def test_name_sanitization_collision_raises():
    """Two distinct names that would sanitize to the same name are refused."""
    ds = xr.Dataset(
        {"a/b": ("x", np.arange(2.0)), "a_b": ("x", np.arange(2.0))},
        coords={"x": np.arange(2)},
    )
    with pytest.raises(ValueError, match="sanitization would map"):
        _netcdf_safe_names(ds)


# ---------------------------------------------------------------------------
# Attribute cleaning and atomic write
# ---------------------------------------------------------------------------


def test_clean_attr_value_branches():
    """dict -> JSON, None -> _DROP, lists and tuples pass through, scalars unchanged."""
    import json

    assert _netcdf_safe_attr_value({"a": 1}) == json.dumps({"a": 1})
    assert _netcdf_safe_attr_value(None) is _DROP
    assert _netcdf_safe_attr_value(["a", "b"]) == ["a", "b"]
    assert _netcdf_safe_attr_value((1, 2)) == (1, 2)
    assert _netcdf_safe_attr_value([]) == []
    assert _netcdf_safe_attr_value("s") == "s"
    assert _netcdf_safe_attr_value(3.5) == 3.5


def test_bool_attribute_is_stored_as_int8():
    """A bool attribute (netCDF has no boolean type) becomes CF 0/1 as int8."""
    import numpy as np

    for value, expected in ((True, 1), (False, 0)):
        out = _netcdf_safe_attr_value(value)
        assert out == expected
        assert isinstance(out, np.int8)


def test_none_attributes_dropped_with_warning(tmp_path):
    """A None global or variable attribute is dropped from the file, with a warning."""
    ds = xr.Dataset(
        {"temperature": ("x", np.arange(3.0))},
        coords={"x": np.arange(3)},
        attrs={"title": "keep", "comment": None},
    )
    ds["temperature"].attrs["note"] = None
    out = tmp_path / "none.nc"
    with pytest.warns(UserWarning, match="dropping attribute"):
        write(ds, out)

    reopened = xr.open_dataset(out, engine="netcdf4")
    try:
        assert reopened.attrs.get("title") == "keep"
        assert "comment" not in reopened.attrs
        assert "note" not in reopened["temperature"].attrs
    finally:
        reopened.close()


def test_write_failure_leaves_no_output_or_temp(tmp_path):
    """A failed write removes its temp file and never leaves a partial output."""
    ds = xr.Dataset({"t": ("x", np.arange(3.0))}, coords={"x": np.arange(3)})
    # A set survives attribute cleaning (not dict/None/list/tuple) but netCDF4
    # cannot store it, so to_netcdf raises inside write's atomic block.
    ds.attrs["bad"] = {1, 2, 3}
    out = tmp_path / "fail.nc"

    with pytest.raises(Exception):  # noqa: B017 - backend raises TypeError/ValueError
        write(ds, out)

    assert not out.exists()
    assert not list(tmp_path.glob("*.tmp")), "temp file left behind"


# ---------------------------------------------------------------------------
# Time encoding — pinned epoch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(_FIXTURES))
def test_time_encoding_pinned(key, tmp_path):
    """Every datetime variable is written float64 ``seconds since 1970-01-01``.

    The pinned literal is ``...T00:00:00``; xarray normalises a midnight epoch to
    ``seconds since 1970-01-01`` on disk, so that is what a reader sees. Decoded
    instants stay the same to sub-microsecond — float64 seconds loses at most a few
    hundred ns of the source's nanosecond timestamps, negligible for mooring data.
    """
    src = xr.open_dataset(_FIXTURES[key], engine="netcdf4")
    out = tmp_path / "out.nc"
    write(src, out)

    raw = _open_raw(out)
    dec = xr.open_dataset(out, engine="netcdf4")
    try:
        checked = 0
        for name, var in src.variables.items():
            if not np.issubdtype(var.dtype, np.datetime64):
                continue
            assert raw[name].attrs.get("units") == "seconds since 1970-01-01"
            assert raw[name].attrs.get("calendar") == "proleptic_gregorian"
            assert raw[name].dtype == np.float64
            a = src[name].values.astype("datetime64[ns]").astype("int64")
            b = dec[name].values.astype("datetime64[ns]").astype("int64")
            assert np.nanmax(np.abs(a - b)) < 1000, f"{name}: > 1 us drift"
            checked += 1
        assert checked > 0
    finally:
        raw.close()
        dec.close()
        src.close()


def test_nat_round_trips(tmp_path):
    """A datetime variable containing NaT writes and decodes back to NaT.

    The pinned float64 time encoding sets no ``_FillValue``; xarray writes ``nan``
    itself, so NaT (e.g. stack's ``time_orig``) survives without explicit handling.
    """
    t = np.array(["2026-01-01", "NaT", "2026-01-03"], dtype="datetime64[ns]")
    ds = xr.Dataset(
        {"v": ("time", np.arange(3.0))},
        coords={"time": t, "time_orig": ("time", t)},
    )
    out = tmp_path / "nat.nc"
    write(ds, out)

    reopened = xr.open_dataset(out, engine="netcdf4")
    try:
        assert bool(np.isnat(reopened["time_orig"].values[1]))
        assert not np.isnat(reopened["time_orig"].values[0])
    finally:
        reopened.close()


def test_timedelta_not_pinned_and_round_trips(tmp_path):
    """A timedelta64 variable is a duration, not an absolute time: it is not pinned.

    It must not receive the 1970 datetime epoch (``_TIME_ENCODING``): the writer stores
    it exactly as plain xarray would — a bare duration unit, no ``since`` reference — so
    the durations survive. Read back with ``decode_timedelta=False``, the path oceanarray
    uses for every file (``cli.py``). Forcing ``decode_timedelta=True`` instead trips an
    xarray-internal conflict with the ``dtype`` attribute xarray itself writes for
    non-nanosecond timedelta on some versions (seen on the Python 3.10 CI leg's older
    xarray), which is xarray's round-trip quirk, not the writer's behaviour to verify.
    """
    td = np.array([3600, 7200, 10800], dtype="timedelta64[s]")
    ds = xr.Dataset({"gap": ("x", td)}, coords={"x": np.arange(3)})
    out = tmp_path / "td.nc"
    ref = tmp_path / "td_ref.nc"
    write(ds, out)
    ds.to_netcdf(ref, engine="netcdf4")  # plain-xarray serialization, for comparison

    got = xr.open_dataset(
        out, engine="netcdf4", decode_cf=False, decode_timedelta=False
    )
    plain = xr.open_dataset(
        ref, engine="netcdf4", decode_cf=False, decode_timedelta=False
    )
    try:
        units = str(got["gap"].attrs.get("units", ""))
        # Not pinned to the datetime epoch: a duration unit never carries "since".
        assert "since" not in units
        assert "1970" not in units
        # Serialized identically to plain xarray — the writer adds no pinning.
        assert units == str(plain["gap"].attrs.get("units", ""))
        np.testing.assert_array_equal(got["gap"].values, plain["gap"].values)
    finally:
        got.close()
        plain.close()


def test_cftime_time_keeps_units_and_calendar(tmp_path):
    """An object-dtype cftime time coordinate keeps its CF units and calendar.

    A dataset decoded with ``use_cftime=True`` holds time as object dtype, not
    ``datetime64``, so it is not pinned to ``_TIME_ENCODING``. The writer must still
    preserve its ``units`` (epoch) and ``calendar`` rather than let xarray re-guess the
    epoch from the data minimum — a non-default epoch (here 1990, with data in 2000)
    makes the difference visible.
    """
    pytest.importorskip("cftime")
    times = xr.date_range(
        "2000-01-01", periods=3, freq="D", use_cftime=True, calendar="noleap"
    )
    ds = xr.Dataset({"v": ("time", [1.0, 2.0, 3.0])}, coords={"time": ("time", times)})
    ds["time"].encoding = {"units": "days since 1990-01-01", "calendar": "noleap"}
    out = tmp_path / "cf.nc"
    write(ds, out)

    raw = _open_raw(out)
    try:
        assert raw["time"].attrs.get("units") == "days since 1990-01-01"
        assert raw["time"].attrs.get("calendar") == "noleap"
    finally:
        raw.close()


def test_package_level_writers_write_binding():
    """``import oceanarray as oa; oa.writers.write`` resolves after a bare import.

    The changelog and docs advertise package-level access to the write seam; a bare
    ``import oceanarray`` must bind ``writers`` (and its ``write``) without an explicit
    ``import oceanarray.writers``.
    """
    import oceanarray as oa

    assert oa.writers.write is write


# ---------------------------------------------------------------------------
# Dtype casting — stale packing encoding does not survive a cast
# ---------------------------------------------------------------------------


def test_cast_drops_stale_packing_encoding(tmp_path):
    """A packed int16 input, once cast to float32, is written unpacked as float32.

    ``cast_output_dtypes`` rebuilds every cast variable with empty encoding, so the
    ``dtype``/``scale_factor`` carried by a packed read cannot survive the writer's
    encoding allowlist and silently re-quantize the data.  The writer relies on this
    xarray behaviour rather than on oceanarray code, so it is pinned here.
    """
    # Build a genuinely packed int16 file: float values stored via scale_factor.
    packed = tmp_path / "packed.nc"
    ds = xr.Dataset(
        {"temperature": ("x", np.array([4.0, 5.5, 6.25, 7.125]))},
        coords={"x": np.arange(4)},
    )
    ds["temperature"].encoding = {
        "dtype": "int16",
        "scale_factor": 0.001,
        "_FillValue": np.int16(-32767),
    }
    ds.to_netcdf(packed, engine="netcdf4")

    # Read back decoded: float values, encoding carries the int16 packing.
    src = xr.open_dataset(packed, engine="netcdf4")
    try:
        assert src["temperature"].encoding.get("dtype") == np.dtype("int16")
        cast = cast_output_dtypes(src)
        # The cast rebuilds the variable with no encoding, so nothing is left to
        # repack it on write.
        assert cast["temperature"].dtype == np.float32
        assert cast["temperature"].encoding == {}

        out = tmp_path / "out.nc"
        write(src, out)
    finally:
        src.close()

    # On disk the variable is float32, not repacked into int16.
    raw = _open_raw(out)
    try:
        assert raw["temperature"].dtype == np.float32
        assert "scale_factor" not in raw["temperature"].attrs
    finally:
        raw.close()
