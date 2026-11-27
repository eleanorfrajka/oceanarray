"""The settings in a mooring YAML, as opposed to its facts.

What a product records as its ``deployment_settings``, and the report options read
from the file.
"""

from collections.abc import Mapping
from os import PathLike
from typing import Any

import yaml

from oceanarray.mooring.instruments import instrument_entries

#: Optional report sections that ``report_sections`` may turn on, keyed by page.
#: A page or section not listed here is rejected by ``_check_report_sections``.
OPTIONAL_REPORT_SECTIONS: dict[str, frozenset[str]] = {"grid": frozenset({"overflow"})}

#: The mooring YAML's settings (as opposed to its facts): what a stack records as
#: its ``deployment_settings``.
SETTINGS_BLOCKS: tuple[str, ...] = (
    "density_reference",
    "qc_ranges",
    "qc_spike",
    "qc_flat_line",
    "tilt_qc",
    "stack",
    "grid",
    "caldip",
    "report_sections",
    "spectrum_markers",
)

#: Instrument-entry keys that describe hardware only and are left out of
#: ``deployment_settings``.
_HARDWARE_ONLY_KEYS: frozenset[str] = frozenset({"image", "label"})


def _plain(value: Any) -> Any:
    """Return *value* with numpy scalars, paths and tuples turned into plain YAML types."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if (
        hasattr(value, "item")
        and callable(value.item)
        and getattr(value, "ndim", 1) == 0
    ):
        return value.item()
    if isinstance(value, PathLike):
        return str(value)
    return value


def settings_yaml(cfg: Mapping, *, stack: Mapping | None = None) -> str:
    """Return the settings that built a product, as YAML text.

    Parameters
    ----------
    cfg : Mapping
        A loaded mooring YAML.
    stack : Mapping, optional
        The ``stack:`` settings actually used (e.g. a ``dt_seconds`` given on the
        command line); they replace the mooring YAML's own ``stack:`` block.

    Returns
    -------
    str
        The :data:`SETTINGS_BLOCKS` the mooring YAML sets, then its instrument
        entries as processed (clamp and inline merged, ``hab`` resolved, without
        the hardware-only keys) under ``instruments``, the key ``load`` also
        reads. Pasted into a mooring YAML, it reruns the same product.
    """
    out: dict = {k: cfg[k] for k in SETTINGS_BLOCKS if cfg.get(k) is not None}
    if stack is not None:
        out["stack"] = {**(out.get("stack") or {}), **stack}
    out["instruments"] = [
        {k: v for k, v in e.items() if k not in _HARDWARE_ONLY_KEYS}
        for e in instrument_entries(cfg)
        if isinstance(e, Mapping)
    ]
    return yaml.safe_dump(_plain(out), sort_keys=False)


def settings_from_yaml(text: str) -> dict:
    """Return the settings a product recorded (``deployment_settings``) as a mapping.

    Parameters
    ----------
    text : str
        The YAML text :func:`settings_yaml` wrote.

    Returns
    -------
    dict
        The settings; empty when the text is blank, not YAML, or not a mapping.
    """
    try:
        parsed = yaml.safe_load(text or "")
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


#: Units a ``spectrum_markers`` period may be given in, with their length in days.
_PERIOD_UNITS: dict[str, float] = {
    "s": 1.0 / 86400.0,
    "min": 1.0 / 1440.0,
    "h": 1.0 / 24.0,
    "d": 1.0,
}


def _period_days(value: object) -> float | None:
    """Return a period such as ``"1.8 d"`` or ``"9 min"`` in days, or None if malformed."""
    import re

    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*(s|min|h|d)\s*", str(value))
    if m is None:
        return None
    days = float(m.group(1)) * _PERIOD_UNITS[m.group(2)]
    return days if days > 0 else None


def resolve_spectrum_markers(description: Mapping) -> list[tuple[str, float]]:
    """Return the extra periods marked on the spectra, from the mooring YAML.

    Parameters
    ----------
    description : Mapping
        The mooring YAML; its optional ``spectrum_markers`` maps a label to
        a period with its unit (``"1.8 d"``, ``"12.42 h"``, ``"9 min"``).

    Returns
    -------
    list of (str, float)
        ``(label, period in days)``, in the mooring YAML's order; malformed entries
        (which ``check`` reports) are skipped.
    """
    block = description.get("spectrum_markers")
    if not isinstance(block, Mapping):
        return []
    out = []
    for label, period in block.items():
        days = _period_days(period)
        if days is not None:
            out.append((str(label), days))
    return out


def resolve_report_sections(page: str, description: Mapping) -> list[str]:
    """Return the optional sections turned on for a report *page*, from *description*.

    Reads ``report_sections.<page>`` from the mooring YAML and defaults to ``[]``
    (every optional section off).  Every report setting is read from the mooring YAML —
    the one complete description of how a mooring was processed — and this is the single
    place this key is resolved.

    Parameters
    ----------
    page : str
        The report page, e.g. ``"grid"``.
    description : Mapping
        The mooring YAML.

    Returns
    -------
    list of str
        The optional section names turned on for *page* (empty when none).
    """
    block = description.get("report_sections")
    if not isinstance(block, Mapping):
        return []
    val = block.get(page)
    return list(val) if isinstance(val, (list, tuple)) else []
