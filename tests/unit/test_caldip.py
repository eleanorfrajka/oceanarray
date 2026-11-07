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


@pytest.mark.parametrize("schema", [2, 1.5, "2", "one"])
def test_a_cast_from_another_schema_is_refused(tmp_path, schema):
    """Any schema_version other than 1 is refused — 1.5 is not truncated to 1."""
    path = _copy_with(tmp_path, lambda ds: ds.attrs.update(schema_version=schema))
    with pytest.raises(caldip.CaldipError, match="schema_version"):
        caldip.read_caldip_cast(path)


@pytest.mark.parametrize("schema", [1.0, "1"])
def test_schema_1_written_as_a_float_or_text_is_read(tmp_path, schema):
    """1.0 and "1" are version 1."""
    path = _copy_with(tmp_path, lambda ds: ds.attrs.update(schema_version=schema))
    assert caldip.read_caldip_cast(path).sizes["instrument"] == 23


def test_a_unit_conversion_is_converted_and_recorded(tmp_path):
    """Conductivity in S/m is converted ×10 to mS cm-1, and the conversion is recorded."""
    names = ("cond_diff", "cond_std", "inst_cond", "ctd_cond")

    def edit(ds):
        for name in names:
            ds[name] = ds[name] / 10.0
            ds[name].attrs["units"] = "S/m"

    original = caldip.read_caldip_cast(FIXTURE)
    converted = caldip.read_caldip_cast(_copy_with(tmp_path, edit))
    for name in names:
        np.testing.assert_allclose(converted[name].values, original[name].values)
        attrs = converted[name].attrs
        assert attrs["units"] == "mS cm-1"
        assert attrs["units_converted_from"] == "S/m"
        assert attrs["units_conversion_factor"] == 10.0
    # a pure spelling rewrite records nothing
    assert "units_converted_from" not in original["temp_diff"].attrs


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


# ── finding the dips ─────────────────────────────────────────────────────────
_BLOCK = {
    "deployment": {"cruise": "msm142_2026", "cast": "castM4"},
    "recovery": {"cruise": "odb2026", "cast": "castX"},
}


def _place(root: Path, *parts: str) -> Path:
    """Copy the real cast to root/<parts…>/castM4_caldip.nc; return the file."""
    dest = root.joinpath(*parts, FIXTURE.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, dest)
    return dest


def test_caldip_file_flat_and_caldip_layouts(tmp_path):
    """<root>/<cruise>/ is found first; caldip's proc_calib/<cruise>/cal_dip/ next."""
    flat = _place(tmp_path / "a", "msm142_2026")
    assert caldip.caldip_file(tmp_path / "a", "msm142_2026", "castM4") == flat
    nested = _place(tmp_path / "b", "proc_calib", "msm142_2026", "cal_dip")
    assert caldip.caldip_file(tmp_path / "b", "msm142_2026", "castM4") == nested
    # the cruise must be on the path: another cruise's castM4 is not this one
    assert caldip.caldip_file(tmp_path / "b", "odb2026", "castM4") is None


def test_caldip_file_ambiguous_is_an_error(tmp_path):
    """Two castM4 files under one cruise cannot be told apart."""
    _place(tmp_path, "proc_calib", "msm142_2026", "cal_dip")
    _place(tmp_path, "proc_calib", "msm142_2026", "old")
    with pytest.raises(caldip.CaldipError, match="ambiguous"):
        caldip.caldip_file(tmp_path, "msm142_2026", "castM4")


@pytest.mark.parametrize(
    ("cruise", "cast"),
    [("/tmp", "castM4"), ("..", "castM4"), ("msm142_2026", "../castM4"), ("a\\b", "c")],
)
def test_caldip_file_refuses_names_that_leave_the_root(tmp_path, cruise, cast):
    """A cruise or cast that is not one folder or file name cannot reach outside the root."""
    with pytest.raises(caldip.CaldipError, match="single folder or file name"):
        caldip.caldip_file(tmp_path, cruise, cast)


def test_caldip_root_precedence(tmp_path):
    """caldip_dir= wins over the YAML root; a relative root is from the YAML's folder."""
    block = {**_BLOCK, "root": "../caldip_out"}
    yaml_dir = tmp_path / "proc" / "m"
    assert caldip.caldip_root(block, yaml_dir=yaml_dir) == yaml_dir / "../caldip_out"
    assert caldip.caldip_root(block, caldip_dir=tmp_path) == tmp_path
    assert caldip.caldip_root(_BLOCK) is None


def test_caldip_ends_finds_each_named_end(tmp_path):
    """The deployment dip is found; the recovery dip, not yet processed, is None."""
    found = _place(tmp_path, "msm142_2026")
    assert caldip.caldip_ends(_BLOCK, caldip_dir=tmp_path) == {
        "deployment": found,
        "recovery": None,
    }
    with pytest.raises(caldip.CaldipError, match="no caldip root"):
        caldip.caldip_ends(_BLOCK)


def test_cast_time_is_the_middle_of_the_stops():
    """castM4's stops run 03:53–05:04 on 3 April 2026; the cast time is their midpoint."""
    cast = caldip.read_caldip_cast(FIXTURE)
    assert str(caldip.cast_time(cast)) == "2026-04-03 04:28:44"


@pytest.mark.parametrize(
    ("when", "role"),
    [
        ("2026-04-03T04:28", "pre"),
        ("2026-06-01T00:00", None),
        ("2027-07-20T00:00", "post"),
        (None, None),
    ],
)
def test_assign_dip_role(when, role):
    """Before deployment is pre, after recovery is post, during or unknown is neither."""
    assert caldip.assign_dip_role(when, "2026-05-07T17:06", "2027-07-10T17:45Z") == role


def test_assign_dip_role_converts_timezones_to_utc():
    """20:00 UTC is before a deployment at 17:06-05 (22:06 UTC), so the dip is pre."""
    assert (
        caldip.assign_dip_role(
            "2026-05-07T20:00Z", "2026-05-07T17:06-05:00", "2027-07-10T17:45Z"
        )
        == "pre"
    )


# ── interface: the correction functions are still stubs ──────────────────────
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
