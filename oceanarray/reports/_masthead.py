"""Masthead facts shared by the mooring, stack and grid report pages.

The common facts (cruises, ships, position, water depth, deployment window,
duration, instruments) come from the mooring description once, in one fixed
order, so the three pages cannot disagree on a label, an order or a value. Each
page appends its own product facts in the order defined here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from markupsafe import Markup

from oceanarray import location

from ._html_helpers import _format_dt, _format_duration, _parse_dt
from ._navs import _TYPE_ORDER, _type_label

#: Shown when a value is absent from the description.
MISSING = "—"
#: Shown for a recovery cruise or ship the description does not give.
UNKNOWN = "UNK"

#: One masthead fact: ``(label, value, missing)``.
Fact = tuple[str, str, bool]

#: Keys of a page's own values, passed as ``extra`` to :func:`masthead_facts`.
#: Pages and the fact lists below use these names, never bare strings.
DT_S = "dt_s"
N_TIME = "n_time"
NC_FILE = "nc_file"
GRID_DP = "grid_dp"
N_LEVELS = "n_levels"
P_RANGE = "p_range"

#: Per-page product facts, appended after the common facts: ``(key, label, unit)``.
_PAGE_FACTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "mooring": (),
    "stack": (
        (DT_S, Markup("Samp.&nbsp;&Delta;t"), "s"),
        (N_TIME, "Records", ""),
        (NC_FILE, Markup("Source&nbsp;file"), ""),
    ),
    "grid": (
        (DT_S, Markup("Samp.&nbsp;&Delta;t"), "s"),
        (N_TIME, "Records", ""),
        (GRID_DP, Markup("Grid&nbsp;&Delta;P"), ""),
        (N_LEVELS, Markup("Pressure&nbsp;levels"), ""),
        (P_RANGE, Markup("Pressure&nbsp;range"), ""),
        (NC_FILE, Markup("Source&nbsp;file"), ""),
    ),
}


def _first(cfg: Mapping[str, Any], *keys: str) -> Any:
    """Return the first non-empty value among *keys* in *cfg*, else None."""
    for key in keys:
        value = cfg.get(key)
        if value not in (None, ""):
            return value
    return None


def _fact(label: str, value: Any, unit: str = "") -> Fact:
    """Return one fact, marking ``—`` and ``UNK`` as missing and adding the unit."""
    text = (
        MISSING if value is None else value if isinstance(value, Markup) else str(value)
    )
    missing = text in (MISSING, UNKNOWN)
    if unit and not missing:
        text = Markup("{}&thinsp;{}").format(text, unit)
    return label, text, missing


def cruise_label(cfg: Mapping[str, Any]) -> str:
    """Return a description's cruise as one line, for the page footer and instrument page.

    Parameters
    ----------
    cfg : Mapping
        A loaded mooring description.

    Returns
    -------
    str
        The deployment cruise, or ``"<deployment> / <recovery>"`` when the
        recovery cruise differs; ``"—"`` when none is given.

    """
    deploy = _first(cfg, "deployment_cruise", "cruise") or MISSING
    recover = _first(cfg, "recovery_cruise") or deploy
    return str(deploy) if deploy == recover else f"{deploy} / {recover}"


def format_position(cfg: Mapping[str, Any]) -> tuple[str, str]:
    """Return a description's (latitude, longitude) in degrees and decimal minutes.

    Parameters
    ----------
    cfg : Mapping
        A loaded mooring description.

    Returns
    -------
    tuple[str, str]
        For example ``("65° 43.91′ N", "027° 48.00′ W")``; ``("—", "—")`` when no
        position key pair parses (stage 1 already warns about that).

    """
    lat, lon, source = location.parse_latlon_with_source(dict(cfg))
    if location.is_unknown_latlon_source(source):
        return MISSING, MISSING
    return location.format_ddm(lat, is_lat=True), location.format_ddm(lon, is_lat=False)


def format_instrument_counts(
    entries: Iterable[Any],
    *,
    labels: Mapping[str, str] | None = None,
    per: int = 1,
) -> str:
    """Return an instrument count broken down by type.

    Parameters
    ----------
    entries : iterable
        Instrument entries of a description (``instruments.instrument_entries``);
        entries that are not dicts are ignored.
    labels : Mapping, optional
        Display labels by lower-case instrument type, consulted before the
        package's own type labels.
    per : int, optional
        Divide every count by this (the number of deployments, for the average
        per deployment of a concatenated site); a count that is not whole shows
        one decimal.

    Returns
    -------
    str
        For example ``"2 (MicroCAT 1 · Aquadopp 1)"``, with ``" · 1 skipped"``
        appended when entries carry ``skip: true``. Types follow the package's
        type order, then the alphabet.

    """
    labels = labels or {}
    counts: Counter[str] = Counter()
    skipped = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("skip"):
            skipped += 1
            continue
        counts[str(entry.get("instrument") or "unknown").lower()] += 1
    order = [t.lower() for t in _TYPE_ORDER]
    types = [t for t in order if t in counts] + sorted(
        t for t in counts if t not in order
    )

    def _count(n: int) -> str:
        value = n / per
        return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"

    text = _count(sum(counts.values()))
    if types:
        by_type = " · ".join(
            f"{labels.get(t) or _type_label(t)} {_count(counts[t])}" for t in types
        )
        text += f" ({by_type})"
    if skipped:
        text += f" · {skipped} skipped"
    return text


def common_facts(cfg: Mapping[str, Any], entries: Iterable[Any]) -> list[Fact]:
    """Return the facts every mooring-level page shows, in their fixed order.

    Parameters
    ----------
    cfg : Mapping
        A loaded mooring description.
    entries : iterable
        Its instrument entries (``instruments.instrument_entries(cfg)``).

    Returns
    -------
    list of tuple
        ``(label, value, missing)`` for: Deployment cruise, Deployment ship,
        Recovery cruise, Recovery ship, Latitude, Longitude, Water depth,
        Deployment, Recovery, Duration, Instruments. A recovery cruise or ship
        absent from the description reads ``UNK``; the legacy single ``cruise``
        and ``ship`` keys are the deployment values only.

    """
    deploy_dt = _parse_dt(cfg.get("deployment_time"))
    recover_dt = _parse_dt(cfg.get("recovery_time"))
    latitude, longitude = format_position(cfg)
    return [
        _fact("Deployment cruise", _first(cfg, "deployment_cruise", "cruise")),
        _fact("Deployment ship", _first(cfg, "deployment_ship", "ship")),
        _fact("Recovery cruise", _first(cfg, "recovery_cruise") or UNKNOWN),
        _fact("Recovery ship", _first(cfg, "recovery_ship") or UNKNOWN),
        _fact("Latitude", latitude),
        _fact("Longitude", longitude),
        _fact("Water depth", cfg.get("waterdepth"), "m"),
        _fact("Deployment", _format_dt(deploy_dt)),
        _fact("Recovery", _format_dt(recover_dt)),
        _fact("Duration", _format_duration(deploy_dt, recover_dt)),
        _fact("Instruments", format_instrument_counts(entries)),
    ]


def masthead_facts(
    page: str, common: list[Fact], extra: Mapping[str, Any] | None = None
) -> list[Fact]:
    """Return a page's masthead: the common facts, then the page's own.

    Parameters
    ----------
    page : {"mooring", "stack", "grid"}
        The report page.
    common : list of tuple
        The facts from :func:`common_facts`.
    extra : Mapping, optional
        The page's own values, keyed by this module's key constants
        (:data:`N_TIME`, :data:`NC_FILE`, ...); a key
        absent here reads ``—``.

    Returns
    -------
    list of tuple
        ``(label, value, missing)`` in display order.

    """
    extra = extra or {}
    own = [_fact(label, extra.get(key), unit) for key, label, unit in _PAGE_FACTS[page]]
    return list(common) + own
