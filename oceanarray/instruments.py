"""The instruments a mooring description lists: its entries and inline hardware serials."""

from __future__ import annotations

from typing import Any


def extract_inline_instruments(
    inline_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract processable instrument entries from the mooring YAML ``inline`` list.

    Most ``inline`` entries describe passive hardware (ropes, shackles, floats).
    This function filters for entries that have an ``instrument`` field and either
    a ``filename`` (to be processed) or ``skip: true`` (to be reported as skipped).

    Normalisation applied to each matching entry:

    - ``hab`` is set from ``hab_bottom`` if ``hab`` is not already present.
      For a downward-looking instrument the transducer is at the bottom of the
      housing (``hab_bottom``); for an upward-looking one use ``hab_top``.
    - ``serial`` is split on the first comma and the first token is used as the
      primary serial number.  Any remaining tokens are joined and stored under
      ``beacon_id`` in the returned dict.
    - ``source: "inline"`` is added to distinguish these entries from ``clamp``
      entries in downstream logging.

    **Serial parsing is fragile**: the convention ``serial: 16430, R01-024``
    assumes the *first* comma-separated token is the instrument serial and the
    rest is a transponder/beacon ID.  If a YAML author places the beacon serial
    first, the wrong value will be used as the output filename stem.  The
    validator emits a WARNING for any inline entry whose serial contains a comma
    so operators can confirm the ordering is correct.

    Args:
        inline_list: The raw list parsed from the ``inline`` YAML key.

    Returns
    -------
        List of normalised instrument-config dicts ready for stage processing.

    """
    result: list[dict[str, Any]] = []
    for entry in inline_list:
        if not isinstance(entry, dict):
            continue
        if "instrument" not in entry:
            continue
        if not entry.get("filename") and not entry.get("skip"):
            continue

        normalized: dict[str, Any] = dict(entry)

        if "hab" not in normalized and "hab_bottom" in normalized:
            normalized["hab"] = normalized["hab_bottom"]

        serial_raw = str(entry.get("serial", ""))
        if "," in serial_raw:
            parts = [p.strip() for p in serial_raw.split(",")]
            normalized["serial"] = parts[0]
            normalized["beacon_id"] = ", ".join(parts[1:])

        normalized["source"] = "inline"
        result.append(normalized)
    return result


def instrument_entries(mooring: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a mooring's processable instruments: clamp (or legacy) plus inline.

    The one place the clamp/``instruments`` fallback and the inline merge live:
    the ``clamp`` list (falling back to the legacy ``instruments`` key) followed
    by the normalised, processable entries from the ``inline`` hardware list. A
    present-but-null ``clamp:`` / ``inline:`` (YAML ``None``) is treated as empty.

    Parameters
    ----------
    mooring : dict
        A loaded mooring description.

    Returns
    -------
    list of dict
        One entry per instrument, in clamp-then-inline order.
    """
    clamp = mooring.get("clamp")
    if clamp is None:
        clamp = mooring.get("instruments")
    return list(clamp or []) + extract_inline_instruments(mooring.get("inline") or [])
