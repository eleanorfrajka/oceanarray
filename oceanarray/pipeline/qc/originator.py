"""Originator QC flags translated to oceanarray's flag table by meaning.

An originator's flag variable pairs ``flag_values`` with ``flag_meanings``; the
number on a sample only finds its word, and the word is looked up in one vocabulary
(``config/flag_meanings.yaml``) to give our flag. Two originators that use 3 for
different things are therefore never confused, and a word we do not know is never
guessed: its samples are written 0 (unknown) with a warning.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from importlib import resources
from typing import Any

import numpy as np
import xarray as xr
import yaml

from oceanarray.config import parameters as params

log = logging.getLogger(__name__)

#: Our flags an originator word may never translate to: oceanarray alone sets
#: nominal (7) and interpolated (8) values.
OURS_ONLY = frozenset({7, 8})

#: Suffix of the translated flag variable beside ``<var>_qc_originator``.
MAPPED_SUFFIX = "_qc_originator_mapped"


@cache
def vocabulary() -> tuple[dict[str, int], frozenset[str]]:
    """Return the meaning vocabulary: word -> our flag, and the unmapped words.

    Returns
    -------
    tuple of (dict of str to int, frozenset of str)
        The translations and the known words that are never translated.

    Raises
    ------
    RuntimeError
        When a word of the packaged table translates to a flag outside our table
        or to 7 or 8 (a package bug, not a problem with the user's input).
    """
    text = (
        resources.files("oceanarray.config")
        .joinpath("flag_meanings.yaml")
        .read_text(encoding="utf-8")
    )
    raw = yaml.safe_load(text) or {}
    meanings = {str(k).lower(): int(v) for k, v in (raw.get("meanings") or {}).items()}
    unmapped = frozenset(str(w).lower() for w in raw.get("unmapped") or [])
    for word, flag in meanings.items():
        if flag not in params.QC_FLAG_VALUES or flag in OURS_ONLY:
            raise RuntimeError(
                f"flag_meanings.yaml: {word!r} -> {flag} is not a flag an originator "
                f"word can translate to"
            )
    return meanings, unmapped


def dictionary(flag_var: xr.DataArray) -> list[tuple[int, str, int | None]]:
    """Return the translation of one originator flag variable, entry by entry.

    Parameters
    ----------
    flag_var : xarray.DataArray
        An originator flag variable with ``flag_values`` and ``flag_meanings``.

    Returns
    -------
    list of (int, str, int or None)
        ``(originator number, its word, our flag)`` for each of the file's flag
        values; our flag is None for a word not in the vocabulary or listed as
        unmapped. Empty when the attributes are missing or their counts differ.
    """
    values = np.atleast_1d(np.asarray(flag_var.attrs.get("flag_values", [])))
    words = str(flag_var.attrs.get("flag_meanings", "")).lower().split()
    if values.size == 0 or len(words) != values.size:
        return []
    meanings, _ = vocabulary()
    return [(int(v), w, meanings.get(w)) for v, w in zip(values, words, strict=True)]


def translate(
    flag_var: xr.DataArray, *, label: str = ""
) -> tuple[np.ndarray, list[tuple[int, str, int | None]], dict[int, int]]:
    """Translate an originator flag variable to our flags.

    A sample whose number the file does not list, or whose word is unknown or
    unmapped, is written 0 (unknown), so ``mask_flags`` can never act on a meaning
    we do not know; one WARNING gives the counts.

    Parameters
    ----------
    flag_var : xarray.DataArray
        The ``<var>_qc_originator`` variable (int, the originator's numbers).
    label : str, optional
        Names the instrument and variable in the warning.

    Returns
    -------
    flags : numpy.ndarray
        Our flags, int8, the shape of *flag_var*.
    entries : list of (int, str, int or None)
        The dictionary (:func:`dictionary`).
    counts : dict of int to int
        Samples per originator number found on the data.
    """
    raw = np.asarray(flag_var.values)
    entries = dictionary(flag_var)
    lut = {num: ours for num, _, ours in entries if ours is not None}
    out = np.zeros(raw.shape, dtype=np.int8)
    numbers, n = np.unique(raw, return_counts=True)
    counts = {int(k): int(c) for k, c in zip(numbers, n, strict=True)}
    unknown: dict[int, int] = {}
    for num, c in counts.items():
        if num in lut:
            out[raw == num] = lut[num]
        else:
            unknown[num] = c
    if unknown:
        words = {num: word for num, word, _ in entries}
        detail = ", ".join(
            f"{num} {words.get(num, '(not in flag_values)')}: {c}"
            for num, c in sorted(unknown.items())
        )
        why = "" if entries else " (flag_values and flag_meanings missing or unpaired)"
        log.warning(
            f"  {label}: originator flags with no known meaning{why}, written 0 "
            f"(unknown): {detail}"
        )
    return out, entries, counts


def entries_text(entries: list[tuple[int, str, int | None]]) -> str:
    """Return a dictionary as text: ``"3 appears_probably_bad -> 3; ..."``.

    Parameters
    ----------
    entries : list of (int, str, int or None)
        From :func:`dictionary`.

    Returns
    -------
    str
        One ``"<number> <word> -> <ours>"`` per entry (``-> 0 (unknown word)`` for an
        untranslated one), or ``"none"`` when there are no entries.
    """
    if not entries:
        return "none"
    return "; ".join(
        f"{num} {word} -> {ours if ours is not None else '0 (unknown word)'}"
        for num, word, ours in entries
    )


def add_mapped(ds: xr.Dataset, *, label: str = "") -> tuple[xr.Dataset, str | None]:
    """Write ``<var>_qc_originator_mapped`` beside every ``<var>_qc_originator``.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument file at stage 3.
    label : str, optional
        Names the instrument in warnings and history.

    Returns
    -------
    ds : xarray.Dataset
        *ds* with the translated flags (our table) and, on each, the originator's
        words, the dictionary and its counts; ``originator_flag_meanings = "none"``
        as a global attribute when the file has no originator flags.
    note : str or None
        The history clause (counts per originator -> ours), None with no flags.
    """
    found = [v for v in ds.data_vars if str(v).endswith("_qc_originator")]
    if not found:
        ds.attrs["originator_flag_meanings"] = "none"
        return ds, None
    lines: list[str] = []
    for name in found:
        src = ds[name]
        var = str(name).removesuffix("_qc_originator")
        flags, entries, counts = translate(src, label=f"{label} {var}".strip())
        lut = {num: ours for num, _, ours in entries}
        attrs: dict[str, Any] = {
            "long_name": f"originator QC flag for {var}, in oceanarray's flag table",
            "flag_values": params.QC_FLAG_VALUES_I8,
            "flag_meanings": params.QC_FLAG_MEANINGS,
            "conventions": params.QC_CONVENTION,
            "originator_flag_meanings": str(src.attrs.get("flag_meanings", "")),
            "flag_translation": entries_text(entries),
            "flag_translation_counts": json.dumps(
                {str(k): v for k, v in sorted(counts.items())}
            ),
        }
        if 5 in flags:
            attrs["comment"] = params.QC_FLAG_5_COMMENT
        for key in ("originator_flag_variable", "flag_meanings_source"):
            if key in src.attrs:
                attrs[key] = src.attrs[key]
        ds[f"{var}{MAPPED_SUFFIX}"] = xr.Variable(src.dims, flags, attrs=attrs)
        lines.append(
            f"{var}: "
            + ", ".join(
                f"{num}->{lut.get(num) if lut.get(num) is not None else 0} ({c})"
                for num, c in sorted(counts.items())
            )
        )
    note = "originator flags translated by meaning, originator->ours (samples): " + (
        " | ".join(lines)
    )
    return ds, note
