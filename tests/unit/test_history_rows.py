"""The processing tables of the stack and instrument pages, read from ``history``.

Real data: the dune2 fixture's stack (written before its history was dated) and
a stack built here from the egM2 2018 fixture's stage-3 files.
"""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import xarray as xr

import oceanarray as oa
from oceanarray import mooring, read_mooring
from oceanarray.reports._captions import history_rows, stack_history_rows

_PROC = Path(__file__).resolve().parents[1] / "fixtures" / "proc"


def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def test_a_new_stack_has_one_row_per_step_with_its_settings():
    cfg = mooring.load(_PROC / "egM2_concat" / "egM2_2018" / "egM2_2018.mooring.yaml")
    data = _quiet(
        read_mooring,
        cfg,
        stage=3,
        mooring_dir=_PROC / "egM2_concat" / "egM2_2018",
        allow_missing=True,
    )
    ds = _quiet(oa.stack, data, cfg)
    rows = stack_history_rows(ds)
    assert [r.events[0].split(":")[0] for r in rows[:2]] == ["stack", "flags"]
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", r.when) for r in rows)
    assert rows[0].settings == "1 h steps"
    assert rows[1].settings.startswith("rule: 4")
    assert "3 instruments placed on a 3600 s time axis" in rows[0].events[0]


def test_an_old_undated_stack_history_is_one_row():
    with xr.open_dataset(
        _PROC / "dune2_1_2026" / "dune2_1_2026_stack.nc", engine="netcdf4"
    ) as ds:
        rows = stack_history_rows(ds.load())
    assert rows[0].when == "—"
    assert rows[0].events[0].startswith("Step 1 stack: 22 instruments")
    assert rows[0].settings == "1 h steps"


def test_events_of_one_entry_stay_in_one_row():
    with xr.open_dataset(
        _PROC / "dune2_1_2026" / "microcat" / "dune2_1_2026_2941_stage3.nc",
        engine="netcdf4",
    ) as ds:
        history = ds.attrs["history"]
    rows = history_rows(history)
    # The reader's own line starts the table, its items folded into that row.
    assert rows[0].events[0].startswith("Processed by SeaSenseLib")
    assert any(e.startswith("Reader: ") for e in rows[0].events)
    assert all(r.when != "—" for r in rows)
