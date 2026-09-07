"""Unit tests for the caldip per-cast netCDF reader (:mod:`oceanarray.processors.caldip`).

Tests run against the committed real cast ``castM4_caldip.nc`` (msm142, a ``.cnv``-input cast
whose ``ctd_*`` provenance is legitimately ``UNK``) plus small synthetic edge cases.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from oceanarray.config.parameters import KNOWN_INSTRUMENT_TYPES
from oceanarray.processors import caldip


def _write_caldip(path, *, serial="013874", itype="microcat", provenance=True):
    """Write a minimal one-instrument, one-stop caldip netCDF for reader edge-case tests."""
    ds = xr.Dataset(
        {
            "temp_diff": (("instrument", "stop"), [[0.01]]),
            "instrument_type": ("instrument", [itype]),
        },
        coords={
            "serial": ("instrument", [serial]),
            "bl_press": ("stop", [2000]),
        },
    )
    if provenance:
        ds.attrs.update({k: "x" for k in caldip.REQUIRED_PROVENANCE_GLOBALS})
        ds.attrs["schema_version"] = 1
    ds.to_netcdf(path, engine="netcdf4")
    return path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("013874", "13874"),
        ("9920*", "9920"),
        ("16430, R01-024", "16430"),
        (13874, "13874"),
        ("13874", "13874"),
        ("0", "0"),
    ],
)
def test_normalize_serial(raw, expected):
    """Leading zeros and the trailing marker asterisk are stripped; commas split."""
    assert caldip.normalize_serial(raw) == expected


def test_read_caldip_cast_shape(caldip_cast):
    """The real cast has the contract dimensions and per-cell/per-stop variables."""
    assert caldip_cast.sizes == {"instrument": 23, "stop": 5}
    for var in ("temp_diff", "cond_diff", "press_diff", "temp_flag", "N"):
        assert caldip_cast[var].dims == ("instrument", "stop")
    for var in ("ctd_temp", "ctd_cond", "ctd_press"):
        assert caldip_cast[var].dims == ("stop",)


def test_reader_normalises_leading_zero_serials(tmp_path):
    """The reader strips leading zeros from a file's serials on read (defensive join key)."""
    path = _write_caldip(tmp_path / "castLZ_caldip.nc", serial="013874")
    out = caldip.read_caldip_cast(path)
    assert out["serial"].values.tolist() == ["13874"]


def test_reader_warns_on_unknown_instrument_type(tmp_path):
    """An instrument_type outside oceanarray's vocabulary warns (contract non-conformance)."""
    path = _write_caldip(tmp_path / "castBad_caldip.nc", itype="rbr")
    with pytest.warns(UserWarning, match="instrument_type"):
        caldip.read_caldip_cast(path)


def test_real_fixture_is_contract_conformant(caldip_cast, caldip_cast_path):
    """The committed real cast conforms: normalised serials, vocabulary types, no dip_role."""
    assert all(not s.startswith("0") for s in caldip_cast["serial"].values.tolist())
    known = {t.lower() for t in KNOWN_INSTRUMENT_TYPES}
    assert {t.lower() for t in caldip_cast["instrument_type"].values.tolist()} <= known
    with xr.open_dataset(caldip_cast_path, engine="netcdf4") as raw:
        assert "dip_role" not in raw.attrs


def test_sign_convention_holds_in_real_file(caldip_cast):
    """``temp_diff == inst_temp − ctd_temp``, so ``corrected = measured − diff``."""
    inst = caldip_cast["inst_temp"].values
    ctd = caldip_cast["ctd_temp"].values  # (stop,) broadcasts over instrument
    diff = caldip_cast["temp_diff"].values
    finite = np.isfinite(inst) & np.isfinite(diff)
    np.testing.assert_allclose((inst - ctd)[finite], diff[finite], atol=1e-9)
    corrected = caldip.apply_offset(inst[finite], diff[finite])
    np.testing.assert_allclose(
        corrected, np.broadcast_to(ctd, inst.shape)[finite], atol=1e-9
    )


def test_offsets_for_serial(caldip_cast):
    """A known serial resolves (leading-zero-insensitive); an absent one returns None."""
    inst = caldip.offsets_for_serial(caldip_cast, "013874")
    assert inst is not None
    assert inst.sizes == {"stop": 5}
    assert caldip.offsets_for_serial(caldip_cast, "13874") is not None
    assert caldip.offsets_for_serial(caldip_cast, "does-not-exist") is None


def test_select_stop_per_variable(caldip_cast):
    """T/C take the deepest stop; P takes the stop nearest the deployment pressure."""
    inst = caldip.offsets_for_serial(caldip_cast, "13874")
    bl = inst["bl_press"].values
    deepest = int(np.argmax(bl))
    assert caldip.select_stop(inst, "temperature") == deepest
    assert caldip.select_stop(inst, "conductivity") == deepest
    shallow = int(np.argmin(bl))
    assert (
        caldip.select_stop(inst, "pressure", deployment_pressure=float(bl[shallow]))
        == shallow
    )


def test_select_stop_pressure_requires_deployment_pressure(caldip_cast):
    """Pressure stop selection refuses to guess a deployment pressure."""
    inst = caldip.offsets_for_serial(caldip_cast, "13874")
    with pytest.raises(ValueError, match="deployment_pressure"):
        caldip.select_stop(inst, "pressure")


def test_select_stop_unknown_variable(caldip_cast):
    """An unknown variable name is rejected, not silently defaulted."""
    inst = caldip.offsets_for_serial(caldip_cast, "13874")
    with pytest.raises(ValueError, match="Unknown caldip variable"):
        caldip.select_stop(inst, "salinity")


def test_refuse_unprovenanced_file(caldip_cast_path, tmp_path):
    """A file stripped of all provenance globals is refused, not applied."""
    with xr.open_dataset(caldip_cast_path, engine="netcdf4") as _ds:
        ds = _ds.load()
    for key in caldip.REQUIRED_PROVENANCE_GLOBALS:
        ds.attrs.pop(key, None)
    bad = tmp_path / "castX_caldip.nc"
    ds.to_netcdf(bad, engine="netcdf4")
    with pytest.raises(caldip.CaldipProvenanceError):
        caldip.read_caldip_cast(bad)


def test_find_caldip_file(tmp_path):
    """The cast file resolves recursively; a missing cast raises FileNotFoundError."""
    nested = tmp_path / "proc_calib" / "msm142" / "cal_dip"
    nested.mkdir(parents=True)
    target = nested / f"castM4{caldip.CALDIP_SUFFIX}"
    target.write_bytes(b"")
    assert caldip.find_caldip_file(tmp_path, "castM4") == target
    with pytest.raises(FileNotFoundError):
        caldip.find_caldip_file(tmp_path, "castZ9")


def test_stage3_caldip_dir_not_yet_implemented(tmp_path):
    """Requesting a caldip correction raises rather than silently skipping it."""
    from oceanarray.processors.stage3 import Stage3Processor

    proc = Stage3Processor(proc_dir=str(tmp_path))
    with pytest.raises(NotImplementedError, match="caldip"):
        proc.process_mooring("any_mooring", caldip_dir=str(tmp_path))


def test_find_caldip_file_disambiguates_by_cruise(tmp_path):
    """A recurring cast name is resolved by the cruise in its path."""
    for cruise in ("msm142", "later99"):
        d = tmp_path / cruise / "cal_dip"
        d.mkdir(parents=True)
        (d / f"castM4{caldip.CALDIP_SUFFIX}").write_bytes(b"")
    resolved = caldip.find_caldip_file(tmp_path, "castM4", cruise="later99")
    assert "later99" in resolved.parts
    with pytest.raises(caldip.CaldipError, match="Ambiguous"):
        caldip.find_caldip_file(tmp_path, "castM4")
