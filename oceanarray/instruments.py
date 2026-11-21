"""The instruments a mooring description lists: its entries and inline hardware serials."""

from __future__ import annotations

from typing import Any


def hab_for(entry: dict[str, Any]) -> Any:
    """Return an entry's height above bottom, filling it by orientation when absent.

    An explicit ``hab`` always wins.  Otherwise an upward-looking instrument's HAB is
    ``hab_top`` and a downward-looking one's is ``hab_bottom`` (the orientation
    convention); an entry with no ``orientation`` falls back to ``hab_bottom`` — the
    historical downward default.  Returns ``None`` when the needed key is absent.
    """
    if entry.get("hab") is not None:
        return entry["hab"]
    if str(entry.get("orientation", "") or "").lower() in ("up", "upward"):
        return entry.get("hab_top")
    return entry.get("hab_bottom")


def nominal_depth(entry: dict[str, Any], waterdepth: Any) -> float | None:
    """Return an instrument's design depth below the surface (m).

    Parameters
    ----------
    entry : dict
        The instrument entry of a mooring description.
    waterdepth : float or None
        The mooring's water depth (m).

    Returns
    -------
    float or None
        The entry's ``nominal_depth``, else ``waterdepth - hab``; None without either.
    """
    if entry.get("nominal_depth") is not None:
        return float(entry["nominal_depth"])
    hab = hab_for(entry)
    if hab is None or waterdepth is None:
        return None
    return float(waterdepth) - float(hab)


def extract_inline_instruments(
    inline_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the instrument entries of the YAML ``inline`` list, marked ``source: "inline"``.

    Entries with an ``instrument`` and either a ``filename`` or ``skip: true``. ``serial`` is
    split on the first comma: the first token is the serial (filenames, outputs), the rest
    ``beacon_id``; the validator warns on any comma so the order can be confirmed. ``hab`` is
    filled later (:func:`instrument_entries`).

    Parameters
    ----------
    inline_list : list of dict
        The ``inline`` list as parsed.

    Returns
    -------
    list of dict
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
    entries = list(clamp or []) + extract_inline_instruments(
        mooring.get("inline") or []
    )
    # Fill hab from hab_bottom/hab_top by orientation (clamp and inline alike), so the
    # stack, the report and validate all read the same resolved hab.
    resolved: list[dict[str, Any]] = []
    for entry in entries:
        # A malformed (non-mapping) entry passes through unchanged, as before the hab
        # fill; callers skip it and validate reports it.
        if not isinstance(entry, dict):
            resolved.append(entry)
            continue
        hab = hab_for(entry)
        if hab is not None and entry.get("hab") is None:
            entry = {**entry, "hab": hab}
        resolved.append(entry)
    return resolved
