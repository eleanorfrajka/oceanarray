"""The Quality flags section of the stack and Stack cat pages.

Three panels from the file alone: a flag table (per level and variable with anything
flagged: ours and the originator's as bars and percentages, where they agree and
differ, and how many values the rule hides), a flag strip (per instrument and
variable, the originator's above ours), and one translation dictionary per
originator flag table.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.pipeline import masking
from oceanarray.pipeline.qc.originator import MAPPED_SUFFIX
from oceanarray.plotters.flags import SHORT, SHOWN_ORIGINATOR, SHOWN_OURS
from oceanarray.plotters.flags import level_dim as _level_dim
from oceanarray.plotters.flags import level_labels as _labels
from oceanarray.plotters.helpers import QC_COLORS


def rule_text(ds: xr.Dataset) -> str:
    """Return the file's rule in words: ``"4"``, or ``"4; salinity 3, 4"``."""
    rule = masking.from_attrs(ds.attrs)
    parts = [", ".join(map(str, rule["default"])) or "none"]
    parts += [
        f"{key} {', '.join(map(str, flags)) or 'none'}"
        for key, flags in rule.items()
        if key != "default"
    ]
    return "; ".join(parts)


#: Flags counted as flagged when comparing ours with the originator's: suspect and
#: bad. 5 (value changed) and 8 (interpolated) are shown, but are not a judgment.
_QUALITY = (3, 4)


def _percent(n: int, total: int) -> str:
    """Return ``"2.2% (415)"``, ``"<0.1% (6)"``, or ``"–"`` for none."""
    if n == 0 or total == 0:
        return "–"
    pct = 100.0 * n / total
    return f"<0.1% ({n})" if pct < 0.1 else f"{pct:.1f}% ({n})"


def _summary(
    flags: np.ndarray | None, here: np.ndarray, shown: tuple[int, ...]
) -> dict[str, Any] | None:
    """Return the bar segments and labelled percentages of one flag set, or None."""
    if flags is None:
        return None
    total = int(here.sum())
    counts = {f: int(((flags == f) & here).sum()) for f in shown}
    flagged = sum(counts.values())
    segments = [
        {"flag": f, "pct": 100.0 * c / total, "color": QC_COLORS[f]}
        for f, c in counts.items()
        if c
    ]
    good = total - flagged
    if good:
        segments.insert(
            0, {"flag": 1, "pct": 100.0 * good / total, "color": QC_COLORS[1]}
        )
    return {
        "segments": segments,
        "parts": [
            {
                "flag": f,
                "meaning": params.QC_FLAG_MEANING[f],
                "text": _percent(c, total),
                "color": QC_COLORS[f],
            }
            for f, c in counts.items()
            if c
        ],
    }


def count_rows(ds: xr.Dataset) -> tuple[list[dict[str, Any]], list[str], int]:
    """Return the flag table: one row per level and variable with anything flagged.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack or Stack cat.

    Returns
    -------
    rows : list of dict
        ``level``, ``variable``, ``n`` (values), ``ours`` and ``originator`` (bar
        segments and percentages, or None without that flag set), ``both``,
        ``only_originator``, ``only_ours`` (values flagged 3 or 4 by both, one or
        the other) and ``hidden`` (values the rule hides).
    unflagged : list of str
        The levels with values but nothing flagged at all.
    quiet_rows : int
        Level-and-variable pairs with values and nothing flagged (left out).
    """
    dim = _level_dim(ds)
    rule = masking.from_attrs(ds.attrs)
    labels = _labels(ds)
    tds = ds.transpose(dim, "time", ...)
    rows: list[dict[str, Any]] = []
    has_values: set[int] = set()
    flagged_levels: set[int] = set()
    quiet = 0
    for var in masking.FLAGGED_VARIABLES:
        if var not in ds.data_vars:
            continue
        values = tds[var].values
        present = np.isfinite(values)
        hidden = masking.flagged(tds, var, rule) & present
        ours = tds[f"{var}_qc"].values if f"{var}_qc" in ds.data_vars else None
        name = f"{var}{MAPPED_SUFFIX}"
        theirs = tds[name].values if name in ds.data_vars else None
        for k in range(values.shape[0]):
            here = present[k]
            if not here.any():
                continue
            has_values.add(k)
            o = ours[k] if ours is not None else None
            t = theirs[k] if theirs is not None else None
            ours_s = _summary(o, here, SHOWN_OURS)
            theirs_s = _summary(t, here, SHOWN_ORIGINATOR)
            if not ((ours_s and ours_s["parts"]) or (theirs_s and theirs_s["parts"])):
                quiet += 1
                continue
            flagged_levels.add(k)
            ours_q = (
                np.isin(o, _QUALITY) & here if o is not None else np.zeros_like(here)
            )
            theirs_q = (
                np.isin(t, _QUALITY) & here if t is not None else np.zeros_like(here)
            )
            rows.append(
                {
                    "level": labels[k],
                    "variable": var,
                    "n": int(here.sum()),
                    "ours": ours_s,
                    "originator": theirs_s,
                    "both": int((ours_q & theirs_q).sum()),
                    "only_originator": int((theirs_q & ~ours_q).sum()),
                    "only_ours": int((ours_q & ~theirs_q).sum()),
                    "hidden": int(hidden[k].sum()),
                }
            )
    unflagged = [labels[k] for k in sorted(has_values - flagged_levels)]
    return rows, unflagged, quiet


def _per_level_entries(ds: xr.Dataset, var: str) -> list[tuple[str, dict]]:
    """Return each level's (dictionary text, counts) for *var*."""
    name = f"{var}_flag_translation"
    if name not in ds:
        return []
    counts = ds[f"{name}_counts"].values if f"{name}_counts" in ds else None
    return [
        (str(text), json.loads(str(counts[k])) if counts is not None else {})
        for k, text in enumerate(ds[name].values)
    ]


def _used_by(levels: dict[str, list[str]], users: dict[str, str]) -> str:
    """Return ``"all 6 microcats: T, S, C"`` or ``"22219: T, S; 22163: S"``.

    "all" only when every level using the dictionary (*users*: level → type)
    has this number on the same variables.
    """
    sets = {tuple(v) for v in levels.values()}
    if len(levels) > 1 and len(sets) == 1 and set(levels) == set(users):
        types = set(users.values())
        kind = f"{types.pop()}s" if len(types) == 1 else "instruments"
        return f"all {len(levels)} {kind}: {', '.join(next(iter(sets)))}"
    return "; ".join(f"{lvl}: {', '.join(v)}" for lvl, v in levels.items())


def translation_tables(ds: xr.Dataset) -> list[dict[str, Any]]:
    """Return one dictionary per originator flag table found in the file.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack or Stack cat (per-level ``<var>_flag_translation`` and ``_counts``).

    Returns
    -------
    list of dict
        Per table: ``rows`` (``number``, ``word``, ``ours``, ``meaning``,
        ``used_by``), only the numbers found on the data, and ``unknown`` (the
        words with no known meaning found on the data).
    """
    site = "deployment_name" in ds and "deployment" in ds
    names = ds["deployment_name"].values if site else None
    level_dep = ds["deployment"].values if site else None
    serials = ds["serial"].values
    types = ds["instrument_type"].values if "instrument_type" in ds else None

    def level(k: int) -> str:
        prefix = f"{names[int(level_dep[k])]} " if site else ""
        return f"{prefix}{serials[k]}"

    tables: dict[str, dict[int, dict[str, Any]]] = {}
    users: dict[str, dict[str, str]] = {}
    for var in masking.FLAGGED_VARIABLES:
        for k, (text, counts) in enumerate(_per_level_entries(ds, var)):
            if text in ("", "none"):
                continue
            table = tables.setdefault(text, {})
            users.setdefault(text, {})[level(k)] = (
                str(types[k]) if types is not None else "instrument"
            )
            for entry in text.split("; "):
                number, rest = entry.split(" ", 1)
                if not counts.get(number):
                    continue  # a number the file lists but never uses
                word, _, ours = rest.partition(" -> ")
                row = table.setdefault(
                    int(number),
                    {
                        "number": int(number),
                        "word": word,
                        "ours": None if "(" in ours else int(ours),
                        "levels": {},
                    },
                )
                row["levels"].setdefault(level(k), []).append(SHORT.get(var, var))
    out = []
    for text, table in tables.items():
        rows = []
        for number in sorted(table):
            row = table[number]
            rows.append(
                {
                    "number": row["number"],
                    "word": row["word"],
                    "ours": row["ours"],
                    "meaning": (
                        params.QC_FLAG_MEANING[row["ours"]]
                        if row["ours"] is not None
                        else "unknown (no known meaning)"
                    ),
                    "used_by": _used_by(row["levels"], users[text]),
                }
            )
        out.append(
            {
                "rows": rows,
                "unknown": [r["word"] for r in rows if r["ours"] is None],
            }
        )
    return out


#: The mooring-YAML keys that change a QC test's thresholds (mooring or instrument
#: level; see :func:`oceanarray.pipeline.qc.resolve_qc_config`).
QC_OVERRIDE_KEYS: tuple[str, ...] = ("qc_ranges", "qc_spike", "tilt_qc", "qc_flat_line")


def threshold_note(mooring: dict[str, Any]) -> str:
    """Return where the description changes the QC thresholds, in words, or ``""``.

    Parameters
    ----------
    mooring : dict
        The loaded mooring description.

    Returns
    -------
    str
        ``"the mooring (qc_spike); 2941 (qc_ranges)"``, or ``""`` when every
        instrument ran with the defaults.
    """
    from oceanarray.instruments import instrument_entries

    parts = []
    own = [k for k in QC_OVERRIDE_KEYS if k in mooring]
    if own:
        parts.append(f"the mooring ({', '.join(own)})")
    for entry in instrument_entries(mooring):
        keys = [k for k in QC_OVERRIDE_KEYS if k in entry]
        if keys:
            parts.append(f"{entry.get('serial', '?')} ({', '.join(keys)})")
    return "; ".join(parts)
