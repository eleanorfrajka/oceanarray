"""The originator flag vocabulary: an originator's flag words and our flags.

An originator's flag variable pairs ``flag_values`` with ``flag_meanings``; the
number on a sample only finds its word, and the word is looked up in one vocabulary
(``config/flag_meanings.yaml``, packaged) to give our flag. Two originators that use
3 for different things are therefore never confused.
"""

from __future__ import annotations

from functools import cache
from importlib import resources

import numpy as np
import xarray as xr
import yaml

from oceanarray.config import parameters as params

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
