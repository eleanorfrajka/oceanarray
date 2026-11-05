"""Tests for :mod:`oceanarray.pipeline.calibration`: reading caldip casts, and the stub.

Reading and checking a cast is implemented (:mod:`oceanarray.pipeline.calibration.caldip`);
the correction is not yet. These tests pin the reader against caldip's real output
``castM4_caldip.nc``, check the four interface functions still raise, and confirm the Stage 3
``caldip_dir`` argument is a data-null action that stamps stub provenance.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from oceanarray.config.parameters import KNOWN_INSTRUMENT_TYPES
from oceanarray.pipeline import calibration as caldip

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "caldip" / "castM4_caldip.nc"
)
FIXTURE_PROC = (
    Path(__file__).resolve().parent.parent / "fixtures" / "proc" / "dune2_1_2026"
)
MOORING = "dune2_1_2026"


# ── contract: the shared join-key rule ──────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("013874", "13874"),
        ("9920*", "9920"),
        (2941, "2941"),
        ("16430, R01-024", "16430"),
    ],
)
def test_canonical_serial(raw, expected):
    """Leading zeros and the trailing marker asterisk are stripped; commas split."""
    assert caldip.canonical_serial(raw) == expected


# ── contract: the real fixture matches what the interface promises ──────────
def test_fixture_has_schema_and_required_provenance():
    """The real cast is schema 1 and carries every required provenance global."""
    with xr.open_dataset(FIXTURE, engine="netcdf4") as ds:
        assert int(ds.attrs["schema_version"]) == caldip.SUPPORTED_SCHEMA_VERSION
        for key in caldip.REQUIRED_PROVENANCE_GLOBALS:
            assert key in ds.attrs, f"missing provenance global {key!r}"


def test_fixture_has_no_dip_role():
    """dip_role is derived by oceanarray, not read — guard against caldip re-adding it."""
    with xr.open_dataset(FIXTURE, engine="netcdf4") as ds:
        assert "dip_role" not in ds.attrs


def test_fixture_instrument_types_in_vocabulary():
    """Every instrument_type in the fixture is a known oceanarray class."""
    known = {t.lower() for t in KNOWN_INSTRUMENT_TYPES}
    with xr.open_dataset(FIXTURE, engine="netcdf4") as ds:
        seen = {str(t).lower() for t in ds["instrument_type"].values}
    assert seen <= known, f"unknown instrument_type(s): {seen - known}"


# ── reading a cast ───────────────────────────────────────────────────────────
def _copy_with(tmp_path: Path, edit) -> Path:
    """Write a copy of the real cast with *edit* applied to the loaded dataset."""
    with xr.open_dataset(FIXTURE, engine="netcdf4") as ds:
        ds = ds.load()
    edit(ds)
    out = tmp_path / "castX_caldip.nc"
    ds.to_netcdf(out)
    return out


def test_read_caldip_cast_joins_serials_and_canonicalises_units():
    """The real cast reads: serials become join keys, T/C/P units the registry spelling."""
    cast = caldip.read_caldip_cast(FIXTURE)
    assert dict(cast.sizes) == {"instrument": 23, "stop": 5}
    assert "13840" in cast["serial"].values
    assert all(not str(s).startswith("0") for s in cast["serial"].values)
    assert cast["temp_diff"].attrs["units"] == "degree_Celsius"
    assert cast["ctd_temp"].attrs["units"] == "degree_Celsius"
    assert cast["cond_diff"].attrs["units"] == "mS cm-1"
    assert cast["press_diff"].attrs["units"] == "dbar"
    # the sign comment survives the units rewrite
    assert "instrument minus CTD" in cast["cond_diff"].attrs["comment"]
    assert cast.attrs["source_path"] == str(FIXTURE)


def test_instrument_record_matches_any_serial_spelling():
    """A leading zero or the YAML marker asterisk still finds the instrument; a stranger does not."""
    cast = caldip.read_caldip_cast(FIXTURE)
    rec = caldip.instrument_record(cast, "013840")
    assert rec is not None and str(rec["serial"].values) == "13840"
    assert dict(rec.sizes) == {"stop": 5}
    np.testing.assert_allclose(
        rec["cond_diff"].values,
        rec["inst_cond"].values - rec["ctd_cond"].values,
        atol=1e-6,
    )
    assert caldip.instrument_record(cast, "13840*") is not None
    assert caldip.instrument_record(cast, "99999") is None


@pytest.mark.parametrize("missing", caldip.REQUIRED_PROVENANCE_GLOBALS)
def test_a_cast_without_required_provenance_is_refused(tmp_path, missing):
    """Any one required provenance attribute missing refuses the cast."""
    path = _copy_with(tmp_path, lambda ds: ds.attrs.pop(missing))
    with pytest.raises(caldip.CaldipProvenanceError, match=missing):
        caldip.read_caldip_cast(path)


def test_a_cast_from_another_schema_is_refused(tmp_path):
    """schema_version 2 is refused, not read as if it were 1."""
    path = _copy_with(tmp_path, lambda ds: ds.attrs.update(schema_version=2))
    with pytest.raises(caldip.CaldipError, match="schema_version"):
        caldip.read_caldip_cast(path)


def test_an_unknown_units_string_is_refused(tmp_path):
    """A conductivity unit oceanarray cannot resolve stops the read."""

    def edit(ds):
        ds["cond_diff"].attrs["units"] = "mmho"

    path = _copy_with(tmp_path, edit)
    with pytest.raises(caldip.CaldipError, match="cond_diff units 'mmho'"):
        caldip.read_caldip_cast(path)


def test_two_serials_with_one_join_key_are_refused(tmp_path):
    """013840 and 13840 in one cast would make the match ambiguous."""

    def edit(ds):
        serials = ds["serial"].values.astype(object)
        serials[1] = "0" + str(serials[0])
        ds["serial"] = ("instrument", serials.astype(str))

    path = _copy_with(tmp_path, edit)
    with pytest.raises(caldip.CaldipError, match="13840"):
        caldip.read_caldip_cast(path)


def test_an_unknown_instrument_type_warns(tmp_path):
    """An instrument_type outside the vocabulary is read, with a warning."""

    def edit(ds):
        types = ds["instrument_type"].values.astype(object)
        types[0] = "mystery"
        ds["instrument_type"] = ("instrument", types.astype(str))

    path = _copy_with(tmp_path, edit)
    with pytest.warns(UserWarning, match="mystery"):
        caldip.read_caldip_cast(path)


# ── interface: the correction functions are still stubs ──────────────────────
def test_find_caldip_casts_is_stub():
    with pytest.raises(NotImplementedError, match="find_caldip_casts"):
        caldip.find_caldip_casts(FIXTURE.parent, "13874")


def test_assign_dip_role_is_stub():
    with pytest.raises(NotImplementedError, match="assign_dip_role"):
        caldip.assign_dip_role(None, None, None)


def test_select_offsets_is_stub():
    with pytest.raises(NotImplementedError, match="select_offsets"):
        caldip.select_offsets(xr.Dataset(), "13874", "temperature")


def test_apply_caldip_is_stub():
    with pytest.raises(NotImplementedError, match="apply_caldip"):
        caldip.apply_caldip(xr.Dataset(), None)


# ── stage 3: caldip_dir is a data-null action that stamps stub provenance ────
def _run_stage3(tmp_root: Path, *, caldip_dir: str | None) -> Path:
    """Copy the mooring fixture into a fresh tree and run Stage 3; return its proc root."""
    from oceanarray.pipeline.stage3 import Stage3Processor

    dest = tmp_root / MOORING
    shutil.copytree(
        FIXTURE_PROC,
        dest,
        ignore=shutil.ignore_patterns("report", "*_grid.nc", "*_stack.nc", "logs"),
    )
    proc = Stage3Processor(proc_dir=str(tmp_root))
    ok = proc.process_mooring(MOORING, force=True, caldip_dir=caldip_dir)
    assert ok, "process_mooring returned False"
    return dest


def test_stage3_caldip_dir_is_data_null_action_with_stub_provenance(tmp_path):
    """Stage 3 with caldip_dir leaves science data unchanged but stamps caldip_applied."""
    plain = _run_stage3(tmp_path / "plain", caldip_dir=None)
    withcd = _run_stage3(tmp_path / "withcd", caldip_dir=str(FIXTURE.parent))

    plain_files = sorted(p.name for p in plain.rglob("*_stage3.nc"))
    withcd_files = sorted(p.name for p in withcd.rglob("*_stage3.nc"))
    assert plain_files == withcd_files and plain_files, "no stage3 output produced"

    for name in plain_files:
        a = next(plain.rglob(name))
        b = next(withcd.rglob(name))
        with (
            xr.open_dataset(a, engine="netcdf4") as da,
            xr.open_dataset(b, engine="netcdf4") as db,
        ):
            assert set(da.data_vars) == set(db.data_vars)
            for v in da.data_vars:
                np.testing.assert_array_equal(
                    np.asarray(da[v].values), np.asarray(db[v].values), err_msg=v
                )
            assert db.attrs.get("caldip_applied") == caldip.CALDIP_STUB_APPLIED
            assert "caldip_applied" not in da.attrs
            # the stub action is also recorded in the timestamped history trail
            assert "caldip" in db.attrs.get("history", "")
            assert "caldip" not in da.attrs.get("history", "")


def test_caldip_dir_reprocesses_existing_outputs(tmp_path):
    """A caldip request invalidates the skip-exists shortcut so the marker reaches every output.

    Without this, a mooring already carrying `_stage3.nc` files would skip on a caldip re-run
    (no --force) and never gain `caldip_applied` — leaving stub-written and pre-stub files
    indistinguishable from their attributes.
    """
    from oceanarray.pipeline.stage3 import Stage3Processor

    root = _run_stage3(
        tmp_path / "m", caldip_dir=None
    )  # first pass: no marker on any output
    for p in root.rglob("*_stage3.nc"):
        with xr.open_dataset(p, engine="netcdf4") as ds:
            assert "caldip_applied" not in ds.attrs

    # re-run WITHOUT force but WITH caldip_dir: skip-exists must be invalidated and every
    # existing output re-stamped.
    proc = Stage3Processor(proc_dir=str(root.parent))
    assert proc.process_mooring(MOORING, caldip_dir=str(FIXTURE.parent))

    outputs = list(root.rglob("*_stage3.nc"))
    assert outputs
    for p in outputs:
        with xr.open_dataset(p, engine="netcdf4") as ds:
            assert ds.attrs.get("caldip_applied") == caldip.CALDIP_STUB_APPLIED
