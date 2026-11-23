"""The ``history`` attribute: one timestamped entry per event, in one form.

Every stage and reader that records what it did appends to ``history``
through :func:`append_history`, so the timestamp format and the separator between
entries live in one place.
"""

import datetime

import xarray as xr


def history_stamp() -> str:
    """Return the current UTC time as the package's ``history`` timestamp.

    Returns
    -------
    str
        ``YYYY-mm-ddTHH:MMZ``.
    """
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%MZ")


#: Separates the entries of ``history``; a note never contains it.
HISTORY_SEPARATOR = "; "


def clean_history_note(note: str) -> str:
    """Return *note* without the entry separator, so it reads back as one entry.

    ``history`` is read back by splitting on ``"; "`` (between entries) and then on
    the first ``": "`` (time from text); a ``"; "`` inside a note would split it
    mid-sentence. Each one becomes ``", "``. Several events of one entry are joined
    with ``" | "`` instead.

    Parameters
    ----------
    note : str
        The event text.

    Returns
    -------
    str
        *note* with every ``"; "`` replaced by ``", "``, stripped.
    """
    return str(note).replace(HISTORY_SEPARATOR, ", ").strip()


def append_history(ds: xr.Dataset, note: str) -> None:
    """Append a timestamped *note* to ``ds.attrs['history']`` in place.

    The one place the ``history`` timestamp format and the ``"; "`` separator
    live, so every stage appends in the same form. A ``"; "`` inside *note* is
    cleaned to ``", "`` (:func:`clean_history_note`) so the entry is read back
    whole.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset whose ``history`` attribute is extended.
    note : str
        The event text, timestamped and appended.
    """
    entry = f"{history_stamp()}: {clean_history_note(note)}"
    existing = ds.attrs.get("history", "")
    ds.attrs["history"] = f"{existing}{HISTORY_SEPARATOR}{entry}" if existing else entry
