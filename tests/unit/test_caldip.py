"""Contract tests for the caldip stub (:mod:`oceanarray.pipeline.qc.calibration`).

The module carries only the shared contract (constants, serial join-key rule) and the interface
the correction is built to (five functions that raise :class:`NotImplementedError`). These
tests pin the contract against caldip's real output ``castM4_caldip.nc`` and confirm the Stage 3
``caldip_dir`` argument is a data-null action that stamps stub provenance.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from oceanarray.config.parameters import KNOWN_INSTRUMENT_TYPES
from oceanarray.pipeline.qc import calibration as caldip

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


# ── interface: every function is a stub that raises ─────────────────────────
def test_read_caldip_cast_is_stub():
    with pytest.raises(NotImplementedError, match="read_caldip_cast"):
        caldip.read_caldip_cast(FIXTURE)


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
