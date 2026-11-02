"""Tests for the top-level reader dispatcher :func:`oceanarray.readers.read`.

Content sniffing and the RODB / OceanSITES paths need no seasenselib; the SBE
and Nortek paths do, and import it lazily through a ``pytest.importorskip``.
All inputs are the committed raw fixtures under ``tests/fixtures/raw/``.
"""

import shutil
from pathlib import Path

import pytest
import xarray as xr

from oceanarray import readers
from oceanarray.config import parameters as params

_RAW = Path(__file__).parent.parent / "fixtures" / "raw"
_DUNE2 = _RAW / "dune2_1_2026"

_SBE_ASCII = _DUNE2 / "microcat" / "2941_recovery.asc"
_NORTEK_DAT = _DUNE2 / "aquadopp" / "9920_recovery.dat"
_RODB_EDT = _RAW / "osnap" / "rtwb1_01_2014_11021.edt"
_RODB_MICROCAT = _RAW / "osnap" / "rtwb1_01_2014_001.microcat"
_OCEANSITES_NC = _RAW / "osnap" / "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc"


# ---------------------------------------------------------------------------
# Content sniffing — the suffix never decides
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (_SBE_ASCII, "sbe-ascii"),
        (_NORTEK_DAT, "nortek-ascii"),
        (_RODB_EDT, "rodb"),
        (_RODB_MICROCAT, "rodb"),
    ],
)
def test_sniff_text_identifies_each_type(path, expected):
    """Each text fixture is identified from its content, as a valid ``file_type:`` value."""
    sniffed = readers._sniff_text(path)
    assert sniffed == expected
    assert sniffed in params.ALL_FILE_TYPES


def test_sniff_ignores_the_suffix(tmp_path):
    """An SBE ASCII file copied to a ``.dat`` name still sniffs as sbe-ascii."""
    wrong = tmp_path / "instrument.dat"
    shutil.copy(_SBE_ASCII, wrong)
    assert readers._sniff_text(wrong) == "sbe-ascii"


def test_unknown_content_raises_naming_the_file(tmp_path):
    """A file of unrecognised content and no ``file_type=`` raises, naming it."""
    junk = tmp_path / "mystery.dat"
    junk.write_text("random text\nwith no instrument header\n")
    with pytest.raises(ValueError, match="mystery.dat: cannot determine"):
        readers.read(junk)


# ---------------------------------------------------------------------------
# netCDF — OceanSITES is recognised, anything else is returned unchanged
# ---------------------------------------------------------------------------


def test_netcdf_magic_detected():
    """An OSNAP OceanSITES file is recognised as netCDF by its magic number."""
    assert readers._is_netcdf(_OCEANSITES_NC)
    assert not readers._is_netcdf(_SBE_ASCII)


def test_read_oceansites_by_content():
    """An OceanSITES netCDF is read to oceanarray names without a file_type."""
    ds = readers.read(_OCEANSITES_NC)
    assert "east_velocity" in ds.data_vars
    assert "time" in ds.dims


def test_read_oceanarray_netcdf_returned_as_is(tmp_path):
    """A netCDF that is not OceanSITES is returned unchanged."""
    path = tmp_path / "own.nc"
    xr.Dataset({"foo": ("time", [1.0, 2.0])}).to_netcdf(path, engine="netcdf4")
    ds = readers.read(path)
    assert "foo" in ds.data_vars


# ---------------------------------------------------------------------------
# Reading by content — RODB (no seasenselib), SBE and Nortek (seasenselib)
# ---------------------------------------------------------------------------


def test_read_rodb_by_content():
    """A RODB ``.edt`` file reads to oceanarray velocity names by content."""
    ds = readers.read(_RODB_EDT)
    assert {"east_velocity", "north_velocity"} <= set(ds.data_vars)
    assert ds.attrs["coordinate_system"] == "ENU"


def test_read_sbe_ascii_by_content():
    """An SBE ASCII file reads by content (needs seasenselib)."""
    pytest.importorskip("seasenselib", reason="seasenselib not installed")
    ds = readers.read(_SBE_ASCII)
    assert "temperature" in ds.data_vars


def test_read_nortek_by_content_finds_the_hdr():
    """A Nortek ``.dat`` reads by content, taking the coordinate system from the .hdr."""
    pytest.importorskip("seasenselib", reason="seasenselib not installed")
    ds = readers.read(_NORTEK_DAT)
    assert "coordinate_system" in ds.attrs


def test_file_type_overrides_the_sniffer():
    """An explicit ``file_type=`` is used instead of sniffing the content."""
    ds = readers.read(_RODB_EDT, file_type="rodb")
    assert "temperature" in ds.data_vars


def test_unknown_file_type_raises():
    """An unknown ``file_type=`` raises ValueError naming the type."""
    with pytest.raises(ValueError, match="Unknown file type"):
        readers.read(_RODB_EDT, file_type="not-a-type")
