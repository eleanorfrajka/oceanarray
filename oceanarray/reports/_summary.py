"""A text summary of a mooring's processed instruments, for the console."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import Any

from oceanarray import paths

#: Variables whose presence the summary lists, in this order.
_SUMMARY_VARS = (
    "temperature",
    "conductivity",
    "pressure",
    "east_velocity",
    "north_velocity",
    "up_velocity",
    "velocity_beam1",
)


def summary(
    mooring: str | PathLike | Mapping[str, Any],
    *,
    mooring_dir: PathLike | None = None,
) -> str:
    """Return one line per processed instrument: records, time span, interval, variables.

    The instruments and their files come from the mooring description, not from
    searching the folder: each listed instrument's stage-3 file is summarised, or
    its stage-2 file when there is no stage 3, with the stage-1 record count
    beside it.

    Parameters
    ----------
    mooring : str or os.PathLike or Mapping
        A ``*.mooring.yaml`` path, a directory holding exactly one, or a loaded
        description (then *mooring_dir* is required).
    mooring_dir : os.PathLike or None, optional
        The mooring's own directory, when *mooring* is a loaded description.

    Returns
    -------
    str
        The summary, grouped by instrument type; ``"  No processed files found."``
        when no instrument has a stage-2 or stage-3 file.
    """
    import xarray as xr

    from oceanarray.pipeline import _resolve_mooring
    from oceanarray.utilities import instrument_entries

    cfg, moor_dir = _resolve_mooring(mooring, mooring_dir, "summary")
    name = cfg["name"]
    by_type: dict[str, list[str]] = {}
    for entry in instrument_entries(cfg):
        if not isinstance(entry, dict) or entry.get("skip"):
            continue
        serial = paths.safe_serial(entry.get("serial", ""))
        instr_type = entry.get("instrument", "unknown")
        found = [
            (n, p)
            for n in (3, 2)
            if (p := paths.stage_file(moor_dir, name, instr_type, serial, n)).exists()
        ]
        if not found:
            continue
        stage_n, best = found[0]
        try:
            line = _instrument_line(
                xr, best, stage_n, moor_dir, name, instr_type, serial
            )
        except Exception as e:  # noqa: BLE001  — a summary line must not stop the summary
            line = f"    {best.name}: ERROR — {e}"
        by_type.setdefault(instr_type, []).append(line)
    if not by_type:
        return "  No processed files found."
    out: list[str] = []
    for instr_type, lines in sorted(by_type.items()):
        out.append(f"\n  {instr_type}")
        out += lines
    return "\n".join(out)


def _instrument_line(
    xr: Any,
    best: Path,
    stage_n: int,
    moor_dir: Path,
    name: str,
    instr_type: str,
    serial: str,
) -> str:
    """Return the summary line for one instrument's best stage file."""
    import numpy as np

    with xr.open_dataset(best, decode_timedelta=False) as ds:
        n_rec = len(ds.time)
        t0 = str(ds.time.values[0])[:16].replace("T", " ")
        t1 = str(ds.time.values[-1])[:16].replace("T", " ")
        depth = f"{ds['InstrDepth'].item():.0f} m" if "InstrDepth" in ds else "?"
        present = [v for v in _SUMMARY_VARS if v in ds.data_vars]
        if n_rec > 1:
            dt_s = np.median(
                np.diff(ds.time.values).astype("timedelta64[s]").astype(float)
            )
            interval = f"{dt_s:.0f} s"
        else:
            interval = "? s"
    stage1 = paths.stage_file(moor_dir, name, instr_type, serial, 1)
    try:
        if stage1.exists():
            with xr.open_dataset(stage1, decode_timedelta=False) as raw:
                n_raw = str(len(raw.time))
        else:
            n_raw = "?"
    except Exception:  # noqa: BLE001  — an unreadable stage-1 file is shown, not raised
        n_raw = "HDF ERR"
    counts = f"{n_raw:>7} raw → {n_rec:>7} stage{stage_n}"
    mtime = datetime.datetime.fromtimestamp(best.stat().st_mtime).strftime(
        "%Y-%m-%d %H:%M"
    )
    return (
        f"    s/n {serial:<8}  {counts}  {t0} → {t1}  depth: {depth}  "
        f"dt: {interval}  processed: {mtime}  vars: {', '.join(present)}"
    )
