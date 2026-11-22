"""The Quality flags section of the stack and Stack cat pages.

Three panels from the file alone: a flag table (per level and variable with anything
flagged: ours and the originator's as bars and percentages, where they agree and
differ, and how many values the rule hides), a flag strip (per instrument and
variable, the originator's above ours), and one translation dictionary per
originator flag table.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
import xarray as xr

from oceanarray import mask_rules
from oceanarray.config import parameters as params
from oceanarray.originator_flags import MAPPED_SUFFIX
from oceanarray.pipeline import masking
from oceanarray.plotters.flags import SHORT, SHOWN_ORIGINATOR, SHOWN_OURS
from oceanarray.plotters.flags import level_dim as _level_dim
from oceanarray.plotters.flags import level_labels as _labels
from oceanarray.plotters.helpers import QC_COLORS

log = logging.getLogger(__name__)


#: Flags counted as flagged when comparing ours with the originator's: suspect and
#: bad. 5 (value changed) and 8 (interpolated) are shown, but are not a judgment.
_QUALITY = (3, 4)


def share_text(n: int, total: int) -> str:
    """Return a count as a share of *total*, as the QC tables write it.

    Parameters
    ----------
    n : int
        Values in a class.
    total : int
        Values in all.

    Returns
    -------
    str
        One decimal without a trailing zero (``"2.1"``, ``"50"``), ``"<0.1"``
        for a share that would round to zero, ``""`` for none.
    """
    if n == 0 or total == 0:
        return ""
    pct = 100.0 * n / total
    return f"{pct:.1f}".rstrip("0").rstrip(".") if pct >= 0.1 else "<0.1"


def flag_counts(flags: np.ndarray, here: np.ndarray | None = None) -> dict[int, int]:
    """Return how many values carry each flag: the QC tables' one count.

    Parameters
    ----------
    flags : numpy.ndarray
        A flag variable's values (any shape).
    here : numpy.ndarray of bool, optional
        Which values to count (those present); every value when not given.

    Returns
    -------
    dict of int to int
        Flag -> number of values, in flag order, flags with none left out.
    """
    flags = np.asarray(flags)
    if here is not None:
        flags = flags[np.asarray(here, dtype=bool)]
    flags = flags[np.isfinite(flags)] if flags.dtype.kind == "f" else flags
    values, counts = np.unique(flags.astype(int), return_counts=True)
    return {int(f): int(c) for f, c in zip(values, counts, strict=True) if c}


def flag_bar(counts: dict[int, int], total: int) -> list[dict[str, Any]]:
    """Return a QC table's bar: one segment per flag present, in flag order.

    Parameters
    ----------
    counts : dict of int to int
        From :func:`flag_counts`.
    total : int
        Values the shares are of.

    Returns
    -------
    list of dict
        ``flag``, ``n``, ``pct`` (float, %, one decimal), ``text`` (:func:`share_text`),
        ``label`` (the flag's word) and ``color``.
    """
    return [
        {
            "flag": f,
            "n": c,
            "pct": round(100.0 * c / total, 1),
            "text": share_text(c, total),
            "label": params.QC_FLAG_LABEL.get(f, str(f)),
            "color": QC_COLORS.get(f, "#999"),
        }
        for f, c in sorted(counts.items())
        if c and total
    ]


def good_share(counts: dict[int, int], total: int) -> dict[str, Any]:
    """Return a QC table's Good % cell: the share flagged good, coloured.

    Good is any of ``parameters.QC_GOOD_FLAGS``. Green at or above
    ``parameters.QC_GOOD_PCT``, amber at or above ``parameters.QC_GOOD_FAIR_PCT``,
    red below; a set whose every value is 0 reads "not checked", uncoloured.

    Parameters
    ----------
    counts : dict of int to int
        From :func:`flag_counts`.
    total : int
        Values the share is of.

    Returns
    -------
    dict
        ``pct`` (float, %), ``text`` (``"0"`` when none is good) and ``color``
        (a page colour variable).
    """
    if set(counts) == {params.QC_NOT_EVALUATED}:
        return {"pct": 0.0, "text": "not checked", "color": "inherit"}
    good = sum(counts.get(f, 0) for f in params.QC_GOOD_FLAGS)
    pct = 100.0 * good / total if total else 0.0
    if pct >= params.QC_GOOD_PCT:
        color = "var(--good)"
    elif pct >= params.QC_GOOD_FAIR_PCT:
        color = "var(--warn)"
    else:
        color = "var(--bad)"
    return {
        "pct": pct,
        "text": share_text(good, total) or "0",
        "color": color,
    }


def good_sentence() -> str:
    """Return the caption sentence that states the Good % colours."""
    good = f"{params.QC_GOOD_PCT:g}"
    fair = f"{params.QC_GOOD_FAIR_PCT:g}"
    return (
        f"Good % (values flagged good or interpolated): green ≥ {good} %, amber "
        f"{fair}–{good} %, red < {fair} %."
    )


def _percent(n: int, total: int) -> str:
    """Return ``"2.2% (415)"``, ``"<0.1% (6)"``, or ``"–"`` for none."""
    text = share_text(n, total)
    return f"{text}% ({n})" if text else "–"


def _summary(
    flags: np.ndarray | None, here: np.ndarray, shown: tuple[int, ...]
) -> dict[str, Any] | None:
    """Return the bar segments, labelled percentages and Good % of one flag set.

    Parameters
    ----------
    flags : numpy.ndarray or None
        The set's flags for one level and variable; None when the file lacks it.
    here : numpy.ndarray of bool
        Where the variable has a value.
    shown : tuple of int
        The flags drawn as their own segments and named; the rest of the bar is
        green.

    Returns
    -------
    dict or None
        ``segments`` (``flag``, ``pct``, ``color``), ``parts`` (one per shown
        flag present) and ``good`` (:func:`good_share`); None without the set.
    """
    if flags is None:
        return None
    total = int(here.sum())
    every = flag_counts(flags, here)
    counts = {f: every.get(f, 0) for f in shown}
    flagged = sum(counts.values())
    segments = [
        {"flag": f, "pct": 100.0 * c / total, "color": QC_COLORS[f]}
        for f, c in counts.items()
        if c
    ]
    rest = total - flagged
    if rest:
        segments.insert(
            0, {"flag": 1, "pct": 100.0 * rest / total, "color": QC_COLORS[1]}
        )
    return {
        "segments": segments,
        "parts": [
            {
                "flag": f,
                "meaning": params.QC_FLAG_MEANING[f],
                "word": params.QC_FLAG_LABEL[f],
                "text": _percent(c, total),
                "color": QC_COLORS[f],
            }
            for f, c in counts.items()
            if c
        ],
        "good": good_share(every, total),
    }


def instrument_labels(ds: xr.Dataset) -> list[str]:
    """Return one label per level: ``"MicroCAT 2941 · 1500 m"`` (type, serial, nominal depth).

    A Stack cat's depth is labelled by its most common type: ``"MicroCAT · 1500 m"``,
    an ADCP bin's ``"ADCP bin 152 m"``.
    A level beyond the end of a shorter ``instrument_type`` or depth array is
    labelled without that part, with a warning.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack (``N_LEVELS``) or Stack cat (``N_DEPTHS``).

    Returns
    -------
    list of str
        One label per level.
    """
    from oceanarray.plotters.helpers import nominal_depths

    from ._navs import _type_label

    if "N_DEPTHS" in ds.dims:
        from oceanarray.pipeline.concat import depth_types

        bins = ds["depth_family"].values.astype(str) == "adcp"
        return [
            (f"ADCP bin {d:.0f} m" if b else f"{_type_label(t.lower())} · {d:.0f} m")
            if np.isfinite(d)
            else t
            for t, d, b in zip(depth_types(ds), nominal_depths(ds), bins, strict=True)
        ]
    if "serial" not in ds.variables:
        return _labels(ds)
    types = ds["instrument_type"].values if "instrument_type" in ds else None
    depths = nominal_depths(ds)
    serials = ds["serial"].values
    for name, arr in (("instrument_type", types), ("nominal depth", depths)):
        if arr is not None and len(arr) < len(serials):
            log.warning(
                f"{name} has {len(arr)} values for {len(serials)} levels; "
                "the levels past its end are labelled without it"
            )
    out = []
    for k, serial in enumerate(serials):
        kind = (
            _type_label(str(types[k]).lower())
            if types is not None and k < len(types)
            else ""
        )
        depth = depths[k] if depths is not None and k < len(depths) else np.nan
        label = f"{kind} {serial}".strip()
        out.append(f"{label} · {depth:.0f} m" if np.isfinite(depth) else label)
    return out


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
    rule = mask_rules.from_attrs(ds.attrs)
    labels = instrument_labels(ds)
    tds = ds.transpose(dim, "time", ...)
    rows: list[dict[str, Any]] = []
    has_values: set[int] = set()
    flagged_levels: set[int] = set()
    quiet = 0
    for var in mask_rules.FLAGGED_VARIABLES:
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


def _used_by_site(levels: dict[str, list[str]]) -> str:
    """Return one line for a site: ``"12 instruments in FLMB_2016, FLMB_2017: T, S"``."""
    deployments = sorted({lvl.split(" ", 1)[0] for lvl in levels})
    variables = sorted({v for vs in levels.values() for v in vs})
    return (
        f"{len(levels)} instrument{'s' if len(levels) > 1 else ''} in "
        f"{', '.join(deployments)}: {', '.join(variables)}"
    )


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
    for var in mask_rules.FLAGGED_VARIABLES:
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
                    "used_by": (
                        _used_by_site(row["levels"])
                        if site
                        else _used_by(row["levels"], users[text])
                    ),
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
