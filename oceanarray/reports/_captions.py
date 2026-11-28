"""Generated text of the stack and grid pages: processing steps, flag sentences, captions.

Every value in these texts is read from the file the page shows (or the stack beside
a grid) when the page is built, so a caption cannot go stale: the time step, the
pressure grid, the interpolation, the flags left out, the levels a figure picked,
the window lengths of a spectrum.  Captions are plain text (they are escaped on the
page); the step tables are rows for a template.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from oceanarray import mask_rules
from oceanarray.analysis.derived import ROSE_MIN_FILLED
from oceanarray.analysis.spectral import SPECTRAL_MIN_FILLED
from oceanarray.config import parameters as params
from oceanarray.originator_flags import MAPPED_SUFFIX
from oceanarray.plotters.helpers import duration_words

from ._navs import _type_label

# ---------------------------------------------------------------------------
# Small pieces
# ---------------------------------------------------------------------------


def step_text(dt_seconds: float) -> str:
    """Return a time step as text: ``"1 h"``, ``"30 min"``, ``"10 s"``, ``"1 d"``.

    Parameters
    ----------
    dt_seconds : float
        The step in seconds.

    Returns
    -------
    str
        ``"?"`` for a step that is not a positive number.
    """
    try:
        dt = float(dt_seconds)
    except (TypeError, ValueError):
        return "?"
    if not np.isfinite(dt) or dt <= 0:
        return "?"
    for size, unit in ((86400.0, "d"), (3600.0, "h"), (60.0, "min")):
        if dt >= size and abs(dt / size - round(dt / size)) < 1e-6:
            return f"{round(dt / size):d} {unit}"
    if dt >= 60.0:
        return f"{dt / 60.0:g} min"
    return f"{dt:g} s"


def cadence_word(dt_seconds: float) -> str:
    """Return the adjective for values one step apart: ``"hourly"``, ``"30-minute"``.

    Parameters
    ----------
    dt_seconds : float
        The step in seconds.

    Returns
    -------
    str
        ``"hourly"`` and ``"daily"`` for one hour and one day; otherwise the
        hyphenated step (``"10-minute"``); ``""`` for an unknown step.
    """
    text = step_text(dt_seconds)
    if text == "?":
        return ""
    words = {"1 h": "hourly", "1 d": "daily"}
    if text in words:
        return words[text]
    value, unit = text.split()
    unit = {"h": "hour", "min": "minute", "s": "second", "d": "day"}[unit]
    return f"{value}-{unit}"


def _join(items: Sequence[str], last: str = "and") -> str:
    """Return ``"a"``, ``"a and b"``, ``"a, b and c"``."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {last} {items[-1]}"


def percent_text(share: float) -> str:
    """Return a share of the record as caption text: ``0.5`` reads ``"50 %"``.

    Parameters
    ----------
    share : float
        A fraction in [0, 1], e.g. a threshold constant.

    Returns
    -------
    str
    """
    return f"{100.0 * float(share):g} %"


def record_fraction_text() -> str:
    """Return the short-record rule's share as words before "its time".

    Returns
    -------
    str
        ``"half"`` when :data:`~oceanarray.config.parameters.MIN_RECORD_FRACTION`
        is 0.5, else e.g. ``"30 % of"``.
    """
    share = params.MIN_RECORD_FRACTION
    return "half" if share == 0.5 else f"{percent_text(share)} of"


#: Added to a T-S caption whose figure has no σθ contours for want of a position.
NO_SIGMA_CAPTION: str = "(no σθ: position unknown)"


def pressures_text(pressures: Sequence[float]) -> str:
    """Return grid pressures as text: ``"150, 400 and 700 dbar"``."""
    return f"{_join([f'{p:.0f}' for p in pressures])} dbar" if pressures else ""


def flag_words(flags: Sequence[int]) -> str:
    """Return flag values with their words: ``"3 (suspect) or 4 (bad)"``."""
    return _join(
        [f"{f} ({params.QC_FLAG_LABEL.get(int(f), str(f))})" for f in flags], "or"
    )


#: A method of interpolation in pressure as a file records it, and its adverb.
_METHOD_ADVERBS: dict[str, str] = {
    "linear": "linearly",
    "pchip": "by PCHIP",
    "cubic spline": "by cubic spline",
    "akima": "by Akima spline",
}
_METHOD_RE = re.compile(
    r"\b(linear|pchip|cubic[ -]spline|akima)\b(?: interpolation)? in pressure",
    flags=re.IGNORECASE,
)


def method_words(ds: Any) -> str:
    """Return how the grid interpolated in pressure, as an adverb, when the file says.

    Read from the variables' ``vertical_interpolation`` attribute, else the
    file's history, and only where the text ties the method to pressure
    (``"linear in pressure"``, ``"linear interpolation in pressure"``).

    Parameters
    ----------
    ds : xarray.Dataset
        The grid.

    Returns
    -------
    str
        ``"linearly"``, ``"by PCHIP"``, ``"by cubic spline"`` or ``"by Akima
        spline"``; ``""`` when the file does not record the method (no method is
        assumed).
    """
    texts = [
        str(ds[v].attrs.get("vertical_interpolation", ""))
        for v in getattr(ds, "data_vars", ())
    ]
    texts.append(str(getattr(ds, "attrs", {}).get("history", "")))
    for text in texts:
        m = _METHOD_RE.search(text)
        if m:
            return _METHOD_ADVERBS[m.group(1).lower().replace("-", " ")]
    return ""


def interpolated_words(ds: Any) -> str:
    """Return ``"interpolated linearly in pressure"``, or without the adverb.

    Parameters
    ----------
    ds : xarray.Dataset
        The grid.

    Returns
    -------
    str
        ``"interpolated in pressure"`` when the file does not record the method.
    """
    return " ".join(w for w in ("interpolated", method_words(ds), "in pressure") if w)


# ---------------------------------------------------------------------------
# The flags rule
# ---------------------------------------------------------------------------


def _rule(attrs: Mapping, key: str) -> dict | None:
    """Return the rule a file records under *key*, or None when it records none."""
    text = attrs.get(key)
    if not text:
        return None
    try:
        return mask_rules.resolve_mask_flags(json.loads(str(text)))
    except (ValueError, TypeError):
        return None


def _rule_sources(attrs: Mapping) -> str:
    """Return which flag sets the rule was applied to, in words, or ``""``."""
    sources = str(attrs.get("stack_mask_flag_sources", ""))
    if not sources:
        return ""
    return (
        "applied to oceanarray's and the originator's flags"
        if MAPPED_SUFFIX in sources
        else "applied to oceanarray's flags"
    )


def flag_sentence(
    attrs: Mapping,
    variables: Sequence[str],
    *,
    key: str = "grid_mask_flags",
    action: str = "left out before gridding",
) -> str:
    """Return the sentence naming the flags left out of *variables*, from the file's rule.

    Parameters
    ----------
    attrs : Mapping
        The file's global attributes.
    variables : sequence of str
        The variables the figure shows.
    key : str, optional
        The attribute holding the rule (``grid_mask_flags`` or ``stack_mask_flags``).
    action : str, optional
        What happened to those values.

    Returns
    -------
    str
        E.g. ``"Values flagged 4 (bad) were left out before gridding."``; a
        sentence saying the rule is not recorded for a file written before it was.
    """
    rule = _rule(attrs, key)
    if rule is None:
        return "Which flags were left out is not recorded in this file."
    groups: dict[tuple[int, ...], list[str]] = {}
    for v in variables:
        groups.setdefault(mask_rules.flags_for(rule, v), []).append(v)
    if len(groups) <= 1:
        flags = next(iter(groups), tuple(rule["default"]))
        if not flags:
            return f"No flagged values were {action}."
        return f"Values flagged {flag_words(flags)} were {action}."
    parts = [
        f"{_join([v.replace('_', ' ') for v in names])}: {flag_words(flags) or 'none'}"
        for flags, names in groups.items()
    ]
    return f"Flagged values were {action} ({'; '.join(parts)})."


def _flag_names(flags: Sequence[int]) -> str:
    """Return flags in words, without missing (9, nothing to draw): ``"suspect or bad"``."""
    return _join(
        [params.QC_FLAG_LABEL.get(int(f), str(f)) for f in flags if int(f) != 9], "or"
    )


def rule_sentence(
    rule: Mapping[str, tuple[int, ...]] | None,
    variables: Sequence[str],
    *,
    ours: bool = True,
    theirs: bool = True,
    suspect: int | None = None,
) -> str:
    """Return the sentence saying which flagged values a figure leaves out, in words.

    Parameters
    ----------
    rule : Mapping or None
        The flag rule (``mask_rules.resolve_mask_flags``); None when the file does
        not record one.
    variables : sequence of str
        The variables the figure shows.
    ours, theirs : bool, optional
        Whether oceanarray's and the originator's flags are present for them.
    suspect : int or None, optional
        How many values flagged suspect the figure draws (its figure record).
        The sentence says suspect values are shown only when this is positive;
        otherwise it says nothing of them.

    Returns
    -------
    str
        E.g. ``"Values flagged bad, by the originator or oceanarray, are left
        out."``, with ``"; suspect values are shown"`` before the full stop when
        the figure draws some.
    """
    if rule is None:
        return "Which flags were left out is not recorded in this file."
    groups: dict[tuple[int, ...], list[str]] = {}
    for v in variables:
        groups.setdefault(mask_rules.flags_for(rule, v), []).append(v)
    if not groups:
        groups = {tuple(rule["default"]): []}
    if ours and theirs:
        who = ", by the originator or oceanarray,"
    elif theirs:
        who = " by the originator"
    else:
        who = " by oceanarray"
    if len(groups) == 1:
        flags = next(iter(groups))
        words = _flag_names(flags)
        if not words:
            return "No flagged values are left out."
        shown = "; suspect values are shown" if suspect and 3 not in flags else ""
        return f"Values flagged {words}{who} are left out{shown}."
    parts = [
        f"{_join([v.replace('_', ' ') for v in names])}: {_flag_names(flags) or 'none'}"
        for flags, names in groups.items()
    ]
    return f"Flagged values{who.rstrip(',')} are left out ({', '.join(parts)})."


def stack_rule_sentence(ds: Any, variables: Sequence[str]) -> str:
    """Return :func:`rule_sentence` for a stack's figure, from its recorded rule.

    A stack written before the rule was recorded (no ``stack_mask_flags``) is
    drawn with the default rule (``parameters.MASK_FLAGS``); the sentence says so.

    Parameters
    ----------
    ds : xarray.Dataset
        The stack (or Stack cat); its ``stack_mask_flags`` is the rule.
    variables : sequence of str
        The variables the figure shows.

    Returns
    -------
    str
    """
    names = set(getattr(ds, "data_vars", ()))
    present = [v for v in variables if v in names] or list(variables)
    rule = _rule(ds.attrs, mask_rules.STACK_ATTR)
    if rule is None:
        ours = any(f"{v}_qc" in names for v in present)
        theirs = any(f"{v}{MAPPED_SUFFIX}" in names for v in present)
        return rule_sentence(
            mask_rules.resolve_mask_flags(None),
            present,
            ours=ours or not theirs,
            theirs=theirs,
        ).replace(
            " are left out", " are left out (the default: the stack records no rule)"
        )
    ours = any(f"{v}_qc" in names for v in present)
    theirs = any(f"{v}{MAPPED_SUFFIX}" in names for v in present)
    return rule_sentence(rule, present, ours=ours or not theirs, theirs=theirs)


# ---------------------------------------------------------------------------
# Processing steps
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One row of a processing-steps table.

    Attributes
    ----------
    name : str
        The step (``"Combine"``, ``"Grid"``, ``"Flags"``).
    what : str
        What was done, in words.
    settings : str
        The settings used, read from the file.
    link : tuple of (str, str) or None
        ``(text, href)`` of a link the template appends to *what*.
    """

    name: str
    what: str
    settings: str
    link: tuple[str, str] | None = None


def _n_from_history(history: str) -> int | None:
    """Return the instrument count of a history's stack step, or None."""
    m = re.search(r"stack:\s*(\d+)\s+instruments", history)
    return int(m.group(1)) if m else None


def _combine_what(n: int | None, dt: float, history: str) -> str:
    """Return the Combine step's text, as the file's history says it was done."""
    who = f"The {n} instruments" if n else "The instruments"
    half = duration_words(dt / 2.0 / 86400.0) if np.isfinite(dt) else "half a step"
    if "interpolated (linear in time)" in history:
        # A stack built before every instrument was taken by nearest sample.
        return (
            f"{who} were placed on a common time axis; instruments sampling at "
            f"least once a step take the nearest sample within ±{half}, slower ones "
            "were interpolated linearly in time"
        )
    return (
        f"{who} were placed on a common time axis; each step takes the nearest "
        f"sample within ±{half}"
    )


def _flags_step(attrs: Mapping, key: str, action: str) -> Step:
    """Return the Flags step of a stack or grid, from its recorded rule."""
    rule = _rule(attrs, key)
    if rule is None:
        return Step(
            "Flags",
            "Which flags were left out is not recorded in this file",
            "not recorded",
        )
    words = flag_words(rule["default"]) or "nothing"
    others = [
        f"{k.replace('_', ' ')}: {flag_words(v) or 'none'}"
        for k, v in rule.items()
        if k != "default"
    ]
    what = f"Values flagged {words} {action}" + (
        f" ({'; '.join(others)})" if others else ""
    )
    source = _rule_sources(attrs)
    return Step(
        "Flags",
        what,
        f"rule: {mask_rules.rule_words(rule, table=True)}"
        + (f" ({source})" if source else ""),
    )


def grid_steps(
    ds: Any, n_instruments: int | None = None, stack_href: str | None = None
) -> list[Step]:
    """Return the processing steps that produced a grid, with the settings used.

    Parameters
    ----------
    ds : xarray.Dataset
        The grid.
    n_instruments : int or None, optional
        Instruments in its stack; read from the history when not given.
    stack_href : str or None, optional
        Link to the stack page, for the Combine step.

    Returns
    -------
    list of Step
        Combine, Grid, Flags.
    """
    from oceanarray.pipeline.dataset import canonical_grid_attrs, time_step_seconds

    attrs = canonical_grid_attrs(ds.attrs)
    history = str(attrs.get("history", ""))

    dt = time_step_seconds(ds)
    n = n_instruments or _n_from_history(history)
    pressure = np.asarray(ds["pressure"].values, dtype=float)
    dp = attrs.get("dp_dbar")
    if dp is None and len(pressure) > 1:
        dp = float(np.median(np.diff(pressure)))
    pmin = attrs.get("pmin_dbar", pressure.min() if len(pressure) else np.nan)
    pmax = attrs.get("pmax_dbar", pressure.max() if len(pressure) else np.nan)
    combine = _combine_what(n, dt, history)
    return [
        Step(
            "Combine",
            combine,
            f"{step_text(dt)} steps",
            link=("see the stack page", stack_href) if stack_href else None,
        ),
        Step(
            "Grid",
            f"{_sentence(interpolated_words(ds))} between instruments; no "
            "extrapolation above the shallowest or below the deepest",
            f"{float(dp):g} dbar levels, {float(pmin):g}–{float(pmax):g} dbar",
        ),
        _flags_step(attrs, "grid_mask_flags", "left out before gridding"),
    ]


# ---------------------------------------------------------------------------
# Processing history, entry by entry
# ---------------------------------------------------------------------------

#: A dated history entry's timestamp (``2026-07-29T12:10Z``).
_WHEN = re.compile(r"^(\d{4}-\d\d-\d\d)T(\d\d:\d\d)")
#: A reader's own processed-by line: ``2026-11-15T21:09:44.4Z - Processed by …``.
_READER_LINE = re.compile(r"^(\d{4}-\d\d-\d\dT[\d:.]+Z?) - (.*)$")


@dataclass(frozen=True)
class HistoryRow:
    """One entry of a file's ``history`` attribute, as the processing table shows it.

    Attributes
    ----------
    when : str
        The entry's time (``"2026-11-18 18:35"``, UTC), ``"—"`` for an undated entry.
    events : tuple of str
        What was done: the entry's text, one item per event it records.
    settings : str
        The settings used, read from the file's attributes.
    """

    when: str
    events: tuple[str, ...]
    settings: str = ""


def _when(stamp: str) -> str:
    """Return a history timestamp as ``"2026-11-18 18:35"``."""
    m = _WHEN.match(stamp)
    return f"{m.group(1)} {m.group(2)}" if m else stamp


def history_rows(
    history: str, settings: Callable[[str], str] | None = None
) -> list[HistoryRow]:
    """Return a file's ``history`` attribute as table rows, one per entry.

    The attribute is read as the pipeline writes it (entries separated by
    ``"; "``, each ``"<time>: <text>"``, events of one entry joined by ``" | "``);
    an undated item (a reader's ``Reader: …`` line) is added to the entry before it.

    Parameters
    ----------
    history : str
        The ``history`` attribute.
    settings : callable, optional
        Given an entry's text, returns its settings (from the file's attributes).

    Returns
    -------
    list of HistoryRow
    """
    from ._html_helpers import _parse_history

    rows: list[dict[str, Any]] = []
    for entry in _parse_history(history or ""):
        stamp, text = entry["timestamp"], entry["text"]
        if _WHEN.match(stamp):
            rows.append(
                {"when": _when(stamp), "events": [e.strip() for e in text.split(" | ")]}
            )
            continue
        m = _READER_LINE.match(text) if not stamp else None
        if m:
            rows.append({"when": _when(m.group(1)), "events": [m.group(2)]})
            continue
        line = f"{stamp}: {text}" if stamp else text
        if rows:
            rows[-1]["events"].append(line)
        else:
            rows.append({"when": "—", "events": [line]})
    return [
        HistoryRow(
            r["when"],
            tuple(r["events"]),
            settings(" | ".join(r["events"])) if settings else "",
        )
        for r in rows
    ]


def stack_history_rows(ds: Any) -> list[HistoryRow]:
    """Return a stack, Stack cat or Grid cat's processing table: its history, with settings.

    Parameters
    ----------
    ds : xarray.Dataset
        The stack, site stack or site grid.

    Returns
    -------
    list of HistoryRow
    """
    from oceanarray.pipeline.dataset import time_step_seconds

    attrs = ds.attrs
    dt = time_step_seconds(ds)

    def flags(key: str) -> str:
        rule = _rule(attrs, key)
        if rule is None:
            return ""
        source = _rule_sources(attrs) if key == mask_rules.STACK_ATTR else ""
        return f"rule: {mask_rules.rule_words(rule, table=True)}" + (
            f" ({source})" if source else ""
        )

    def depths() -> str:
        tolerance = attrs.get("depth_tolerance_m")
        match = attrs.get("depth_match", "depth")
        return f"{step_text(dt)} steps" + (
            f", one depth within {float(tolerance):g} m by {match}"
            if tolerance not in (None, "")
            else ""
        )

    # The settings of each entry, by the text it starts with (and, for flags,
    # the attribute it names).
    stack_step = ("stack:", "Step 1 stack:")
    by_prefix: tuple[tuple[str | tuple[str, ...], Callable[[], str]], ...] = (
        (stack_step, lambda: f"{step_text(dt)} steps"),
        ("join:", depths),
        ("overlaps:", lambda: f"rule: the {attrs.get('overlap_rule', 'newer')} one"),
        ("grid:", lambda: f"{float(attrs.get('dp_dbar', np.nan)):g} dbar levels"),
    )

    def settings(text: str) -> str:
        if text.startswith("flags:"):
            key = (
                "grid_mask_flags"
                if "grid_mask_flags" in text
                else mask_rules.STACK_ATTR
            )
            return flags(key)
        return next((f() for prefix, f in by_prefix if text.startswith(prefix)), "")

    rows = history_rows(str(attrs.get("history", "")), settings)
    if "deployment_history" not in ds:
        return rows
    # A site record starts with each deployment's own stack step.
    stamps = sorted(
        r.when
        for h in ds["deployment_history"].values
        for r in history_rows(str(h))
        if r.events and r.events[0].startswith(stack_step)
    )
    combine = HistoryRow(
        f"{stamps[0]} – {stamps[-1]}" if len(set(stamps)) > 1 else (stamps or ["—"])[0],
        (
            "combine: each deployment's instruments placed on its own time axis "
            "(its stack page; its record is below)",
        ),
        f"{step_text(dt)} steps",
    )
    return [combine, *rows]


# ---------------------------------------------------------------------------
# Velocity direction reference
# ---------------------------------------------------------------------------


def direction_reference(ds_stack: Any) -> str:
    """Return what the velocity directions are relative to, from the stack.

    Parameters
    ----------
    ds_stack : xarray.Dataset or None
        The stack (or site stack) the velocities came from.

    Returns
    -------
    str
        ``"relative to true north, declination applied"``,
        ``"relative to true north, as provided by the originator"``, a mix of the
        two, ``"relative to magnetic north, no declination applied"`` for
        stage-2 velocities, or ``""`` when the stack is not available.
    """
    if ds_stack is None or "east_velocity" not in getattr(ds_stack, "data_vars", ()):
        return ""
    dim = "N_LEVELS"
    site = "N_DEPTHS" in ds_stack["east_velocity"].dims
    east = ds_stack["east_velocity"].transpose("time", "N_DEPTHS" if site else dim)
    has_vel = np.isfinite(east.values).any(axis=0)
    if site:  # a site stack: a level has velocity when its depth does
        has_vel = has_vel[ds_stack["depth_index"].values]
    n = int(has_vel.sum())
    if n == 0:
        return ""
    imported = (
        np.array([bool(str(f)) for f in ds_stack["originator_file"].values])
        if "originator_file" in ds_stack.variables
        else np.zeros(ds_stack.sizes[dim], dtype=bool)
    )
    # The stage-2 serials; per deployment in a site stack whose deployments differ
    # (concat writes the attribute as a deployment_ variable).
    attr = "stage2_magnetic_north"
    shared = set(str(ds_stack.attrs.get(attr, "")).split())
    per_deployment = (
        [set(str(m).split()) for m in ds_stack[f"deployment_{attr}"].values]
        if f"deployment_{attr}" in ds_stack.variables
        else None
    )
    serials = (
        [str(s) for s in ds_stack["serial"].values]
        if "serial" in ds_stack.variables
        else []
    )
    kinds = set()
    for i in np.flatnonzero(has_vel):
        magnetic = (
            per_deployment[int(ds_stack["deployment"].values[i])]
            if per_deployment is not None
            else shared
        )
        if serials and serials[i] in magnetic:
            kinds.add("magnetic")
        elif imported[i]:
            kinds.add("originator")
        else:
            kinds.add("declination")
    if kinds == {"magnetic"}:
        return "relative to magnetic north, no declination applied"
    words = {
        "declination": "declination applied",
        "originator": "as provided by the originator",
    }
    true = [words[k] for k in ("declination", "originator") if k in kinds]
    text = f"relative to true north, {' or '.join(true)}" if true else ""
    if "magnetic" in kinds:
        text += "; some relative to magnetic north, no declination applied"
    return text


# ---------------------------------------------------------------------------
# Grid captions
# ---------------------------------------------------------------------------


def _sentence(text: str) -> str:
    """Return *text* with its first letter capitalised (the rest unchanged)."""
    return text[:1].upper() + text[1:] if text else text


def grid_captions(c: Any) -> dict[str, str]:  # noqa: C901
    """Return the grid page's captions, built from the page's context.

    Parameters
    ----------
    c : GridContext
        The page's context (:func:`oceanarray.reports._grid.build_grid_context`).

    Returns
    -------
    dict of str to str
        Caption by panel id; a panel missing here keeps its default caption.
    """
    ds = c.ds
    attrs = ds.attrs
    interpolated = interpolated_words(ds)
    cadence = cadence_word(c.dt_s)
    out: dict[str, str] = {}
    pressure = np.asarray(ds["pressure"].values, dtype=float)
    dv = set(ds.data_vars)

    hydro_vars = [v for v in ("temperature", "salinity") if v in dv]
    if "salinity" not in dv and "conductivity" in dv and "temperature" in dv:
        hydro_vars.append("salinity")
    if hydro_vars:
        what = (
            "Temperature and salinity (PSU)"
            if len(hydro_vars) == 2
            else _sentence(hydro_vars[0].replace("salinity", "salinity (PSU)"))
        )
        marks = (
            " Triangles at right: the instruments' median pressures."
            if c.instruments
            else ""
        )
        out["hydro"] = (
            f"{what} on the pressure grid, {interpolated} between "
            "instruments; nothing is extrapolated beyond the shallowest or deepest "
            f"instrument at each time.{marks} " + flag_sentence(attrs, hydro_vars)
        )

    if {"temperature", "salinity"} <= dv:
        interval = (c.ts_bounds or {}).get("sigma_interval")
        values = f"{cadence} values" if cadence else "values"
        if c.derived is not None and c.derived.theta is not None:
            text = (
                "Potential temperature against salinity (PSU) for every grid "
                f"cell (interpolated values included), as the number of {values} "
                "in each bin (log scale)."
            )
            if interval:
                text += (
                    f" Dashed lines: potential density σθ, every {interval:g} "
                    f"{params.vunit('potential_density')}."
                )
        else:
            text = (
                "In-situ temperature against salinity (PSU) for every grid cell "
                f"(interpolated values included), as the number of {values} in each "
                f"bin (log scale) {NO_SIGMA_CAPTION}: no potential temperature or "
                "density without the mooring position."
            )
        text += " " + flag_sentence(attrs, ["temperature", "salinity"])
        if "oxygen_saturation_pct" in dv:
            text += (
                " Right: median oxygen saturation in each bin with at least 5 values."
            )
        out["ts_diagram"] = text

    vel_instr = [r for r in c.instruments if "east_velocity" in r.variables]
    reference = direction_reference(c.ds_stack)
    if {"east_velocity", "north_velocity"} & dv:
        between = (
            f"between the {len(vel_instr)} current meters (triangles at right)"
            if vel_instr
            else "between the current meters"
        )
        toward = f" ({reference})" if reference else ""
        out["vel_stacked"] = (
            "Eastward, northward and upward velocity, current speed, and the "
            f"direction toward which the current flows{toward}, {interpolated} "
            f"{between}; no values above the shallowest or "
            "below the deepest. One colour scale for the three components, "
            "symmetric about zero, to the larger magnitude of the 1st and 99th "
            "percentiles; speed (to its 98th percentile) and direction (a cyclic "
            "scale, 0–360°, clockwise from north) have their own. "
            + flag_sentence(attrs, ["east_velocity", "north_velocity", "up_velocity"])
        )
        values = f"{cadence} values" if cadence else "values"
        seabed = (
            " Black line: seabed, from the water depth."
            if c.seabed_dbar is not None
            else ""
        )
        out["vel_iqr"] = (
            "Distribution of current speed (left) and eastward/northward velocity "
            "(middle) at each grid level over the deployment: line = median, dark "
            "shading = middle 50 % (interquartile range), light shading = "
            f"2.5–97.5 % (speed only). Right: number of {values} at each "
            f"level.{seabed}"
        )
        if c.max_speed_level is not None:
            p = pressure[c.max_speed_level]
            near = _nearest(vel_instr, p)
            nearest = (
                f" (nearest instrument: {_type_label(near.instrument_type)} "
                f"{near.serial} at {near.pressure:.0f} dbar)"
                if near is not None
                else ""
            )
            lead = f"{_sentence(cadence)} current" if cadence else "Current"
            out["vel_timeseries"] = (
                f"{lead} speed and eastward/northward velocity at {p:.0f} dbar, the "
                f"grid level with the highest mean speed{nearest}."
            )
        rose_p = [pressure[k] for k in c.rose_levels]
        directions = f" ({reference})" if reference else ""
        filled = percent_text(ROSE_MIN_FILLED)
        left_out = _left_out_sentence(c, pressure, vel_instr)
        levels = (
            f"Levels: {pressures_text(rose_p)}, from those with values at least "
            f"{filled} of the time."
            if rose_p
            else f"No level has values at least {filled} of the time."
        )
        out["roses"] = (
            f"Direction toward which the current flows{directions} and speed at "
            "grid levels (interpolated between current meters). Wedge length is the "
            "percentage of the record in that direction; colours are speed classes. "
            f"{levels}{left_out} "
            + flag_sentence(attrs, ["east_velocity", "north_velocity"])
        )
        if rose_p:
            out["hodograph"] = (
                f"Velocity vectors (24-hour smoothed) at {pressures_text(rose_p)} (the "
                "levels of the current roses, with values at least "
                f"{filled} of the time), traced through the deployment: the "
                f"line joins the successive smoothed {cadence or 'time-step'} "
                "velocities, coloured from start (dark) to end (yellow); green "
                f"circle = start, red square = end.{left_out}"
            )
        out["progressive_vector"] = (
            "Cumulative displacement from adding up the hourly velocity at each "
            "level (a progressive vector diagram): how far water passing the mooring "
            "would travel if the flow were the same everywhere. It shows the mean "
            "flow direction and how steady it is, not the path of a water parcel."
        ).replace("hourly", cadence or "hourly")

    flags_ts = flag_sentence(attrs, ["temperature", "salinity"])
    density = params.vunit("potential_density")
    for ref, words in (
        ("sigma0", f"σ0 (referenced to the surface; {density})"),
        ("sigma2", f"σ2 (referenced to 2000 dbar; {density})"),
    ):
        out[ref] = (
            f"Potential density anomaly {words}, from potential temperature and "
            f"salinity (PSU) on the grid. {flags_ts}"
        )
    dp = float(np.median(np.diff(pressure))) if len(pressure) > 1 else float("nan")
    out["n2"] = (
        f"Buoyancy frequency squared (log₁₀ N², {params.vunit('n2')}), calculated "
        "simply between "
        f"neighbouring {dp:g} dbar levels of the gridded temperature and salinity "
        f"(GSW, no adiabatic levelling). Because the grid is {interpolated} "
        "between instruments, N² changes mostly at the instrument pressures; zero or "
        "negative values are drawn in the lowest colour. For analysis, calculate N² "
        "from the measurements yourself."
    )

    out.update(_spectral_captions(c, pressure))
    return out


def _nearest(levels: Sequence[Any], p: float) -> Any:
    """Return the instrument level nearest pressure *p*, or None."""
    if not levels:
        return None
    return min(levels, key=lambda r: abs(r.pressure - p))


def _left_out_sentence(
    c: Any, pressure: np.ndarray, current_meters: Sequence[Any]
) -> str:
    """Return the sentence naming the levels the roses leave out, or ``""``.

    Each level is named by its pressure, with the current meter within half a
    grid step of it when there is one: ``" Levels with values less than 50 % of
    the time are left out: 150 dbar (Aquadopp 400115)."``
    """
    if not c.rose_left_out:
        return ""
    half = 0.5 * float(np.median(np.diff(pressure))) if len(pressure) > 1 else 0.0
    names = []
    for k in c.rose_left_out:
        p = float(pressure[k])
        near = _nearest(current_meters, p)
        meter = (
            f" ({_type_label(near.instrument_type)} {near.serial})"
            if near is not None and abs(near.pressure - p) <= half
            else ""
        )
        names.append(f"{p:.0f} dbar{meter}")
    return (
        f" Levels with values less than {percent_text(ROSE_MIN_FILLED)} of the time "
        f"are left out: {_join(names)}."
    )


def _markers_sentence(c: Any, lo: float, hi: float) -> str:
    """Return the sentence naming the mooring YAML's extra markers, or ``""``."""
    extra = list(c.spectrum_markers)
    if not extra:
        return ""
    shown = [lbl for lbl, per in extra if lo <= per <= hi]
    hidden = [lbl for lbl, per in extra if not lo <= per <= hi]
    text = ""
    if shown:
        text += f" Also marked, from the mooring description: {_join(shown)}."
    if hidden:
        text += f" Not drawn (outside the periods shown): {_join(hidden)}."
    return text


def _windows(n_time: int, dt_s: float, days: float, min_samples: int = 128) -> str:
    """Return the Welch window length as caption text, saying when it was shortened."""
    from oceanarray.analysis.spectral import welch_segment

    seg = welch_segment(n_time, dt_s, days, min_samples=min_samples)
    actual = seg * dt_s / 86400.0
    text = f"{duration_words(actual, adjective=True)} Hann windows"
    if abs(actual - days) > 0.5 * dt_s / 86400.0:
        record = duration_words(n_time * dt_s / 86400.0)
        text += f" (shortened from {duration_words(days)}: the record is {record} long)"
    return text


def _spectral_captions(c: Any, pressure: np.ndarray) -> dict[str, str]:
    """Return the captions of the temperature spectrum, wavelet and rotary spectrum."""
    from oceanarray.analysis.spectral import (
        K1_PERIOD_DAYS,
        M2_PERIOD_DAYS,
        inertial_period_days,
    )

    out: dict[str, str] = {}
    n_time = int(c.ds.sizes.get("time", 0))
    dt_s = c.dt_s
    if not np.isfinite(dt_s) or dt_s <= 0 or n_time < 2:
        return out
    f_days = inertial_period_days(c.lat if c.lat_resolved else None)
    lines = f"M2 ({M2_PERIOD_DAYS * 24:.1f} h), K1 ({K1_PERIOD_DAYS * 24:.1f} h)"
    if f_days is not None:
        lines += f", inertial period ({f_days * 24:.1f} h at this latitude)"
    else:
        lines += " (no inertial period: the latitude is unknown)"
    nyq = 2.0 * dt_s / 86400.0
    from oceanarray.analysis.spectral import welch_segment

    t_levels = [pressure[k] for k in c.temp_levels]
    if t_levels:
        lf = _windows(n_time, dt_s, 14.0)
        hf = _windows(n_time, dt_s, 7.0, min_samples=8)
        lf_max = welch_segment(n_time, dt_s, 14.0) * dt_s / 86400.0
        where = (
            "the grid levels nearest the instruments"
            if c.instruments
            else "grid levels"
        )
        out["temp_spectrum"] = (
            f"Power spectral density of temperature at {len(t_levels)} levels "
            f"(Welch estimate): {pressures_text(t_levels)}, {where} with values at "
            f"least {percent_text(SPECTRAL_MIN_FILLED)} of the time, shallowest, "
            "deepest and evenly between (colour "
            f"bar). Left: {lf}; right: {hf}, zoomed to periods under 2 days; "
            f"50 % overlap. Vertical lines: {lines}. Dashed: a −2 slope for "
            "reference." + _markers_sentence(c, nyq, lf_max)
        )
    w_levels = [pressure[k] for k in c.wavelet_levels]
    if w_levels:
        min_p = max(6.0 * dt_s, 7200.0) / 86400.0
        out["wavelet"] = (
            f"Wavelet power of temperature at {pressures_text(w_levels)} (Morlet, "
            "ω₀ = 6; Torrence & Compo 1998), log₁₀ °C² d: the outermost levels with "
            f"values at least {percent_text(SPECTRAL_MIN_FILLED)} of the time and one "
            "between. Hatched: "
            "edge-affected (cone of influence); cross-hatched: gaps filled before "
            "the transform. Black contours: 95 % significance against red noise. "
            f"Periods from {duration_words(min_p)} up."
        )
    r_levels = [pressure[k] for k in c.rotary_levels]
    if r_levels:
        rw = _windows(n_time, dt_s, 14.0)
        r_max = welch_segment(n_time, dt_s, 14.0) * dt_s / 86400.0
        out["rotary_spectrum"] = (
            f"Rotary spectra of velocity at {pressures_text(r_levels)}: clockwise "
            "(red, solid) and counter-clockwise (blue, dashed) components, and the "
            "rotary coefficient r = (CCW − CW)/(CCW + CW) (right; r < 0: clockwise "
            f"dominant). Welch estimate, {rw}, 50 % overlap. Vertical lines: M2, K1 "
            "and the inertial period f. Clockwise rotation near f and the tides is "
            "expected for internal waves in the Northern Hemisphere."
            + _markers_sentence(c, nyq, r_max)
        )
        if c.lat_resolved and c.lat < 0:
            out["rotary_spectrum"] = out["rotary_spectrum"].replace(
                "Clockwise rotation near f and the tides is expected for internal "
                "waves in the Northern Hemisphere.",
                "Counter-clockwise rotation near f and the tides is expected for "
                "internal waves in the Southern Hemisphere.",
            )
    return out


def netcdf_caption(
    file: str, description: str = "at the end of the deployment page"
) -> str:
    """Return the caption of a page's NetCDF appendix.

    Parameters
    ----------
    file : str
        What the file is: ``"file"``, ``"stack file"``, ``"site grid file"``.
    description : str, optional
        Where the mooring YAML is, from this page.

    Returns
    -------
    str
    """
    where = "at the top" if file == "file" else "at the top of each instrument's page"
    return (
        f"Contents of the {file} this page was drawn from, as written by oceanarray. "
        f"To produce it yourself, run oceanarray with the mooring description "
        f"{description}; the originator's data are linked {where}."
    )


def stack_rose_caption(ds_stack: Any) -> str:
    """Return the stack page's current-rose caption, from the stack.

    Parameters
    ----------
    ds_stack : xarray.Dataset
        The stack.

    Returns
    -------
    str
    """
    reference = direction_reference(ds_stack)
    return (
        "Distribution of current direction (the direction the water flows toward"
        + (f", {reference}" if reference else "")
        + ") and speed for each current meter. Wedge length is the percentage of the "
        "record in that direction; colours are speed classes; rings share one scale. "
        + stack_rule_sentence(ds_stack, ["east_velocity", "north_velocity"])
    )


# ---------------------------------------------------------------------------
# Stack captions
# ---------------------------------------------------------------------------

#: The variables each stack figure shows, for its flag sentence.
STACK_FIGURE_VARIABLES: dict[str, tuple[str, ...]] = {
    "hydro_pts": ("pressure", "temperature", "salinity"),
    "dissolved_oxygen": ("dissolved_oxygen",),
    "vel_uvw": ("east_velocity", "north_velocity", "up_velocity"),
    "turbidity": ("turbidity",),
    "ts_diagram": ("temperature", "salinity"),
    "ts_points": ("temperature", "salinity"),
    "ts_medians": ("temperature", "salinity"),
    "ts_by_deployment": ("temperature", "salinity"),
    "roses": ("east_velocity", "north_velocity"),
    "speed_profile": ("east_velocity", "north_velocity"),
    "direction_profile": ("east_velocity", "north_velocity"),
}


def _suspect_sentence(record: Mapping[str, Any] | None) -> str:
    """Return the caption sentence for the black dots, when the figure drew any."""
    n = int((record or {}).get("suspect", 0) or 0)
    return f" Black dots: values flagged suspect ({n:,})." if n else ""


def _legend_sentence(record: Mapping[str, Any] | None) -> str:
    """Return a sentence naming the instruments in the small legend, if any."""
    legend = list((record or {}).get("legend") or [])
    if not legend:
        return ""
    one = len(legend) == 1
    return (
        f" {_join(legend)} {'has' if one else 'have'} no line in the pressure panel: "
        f"the legend names {'it' if one else 'them'}."
    )


def stack_flag_sentences(ds: Any) -> dict[str, str]:
    """Return each stack figure's flag sentence, by panel id, from the stack's rule.

    Parameters
    ----------
    ds : xarray.Dataset
        The stack (or Stack cat).

    Returns
    -------
    dict of str to str
        :func:`stack_rule_sentence` for the variables of each figure
        (:data:`STACK_FIGURE_VARIABLES`).
    """
    return {
        pid: stack_rule_sentence(ds, list(variables))
        for pid, variables in STACK_FIGURE_VARIABLES.items()
    }


def _dt(ds: Any) -> float:
    """Return a stack's time step (s), NaN when unknown."""
    from oceanarray.pipeline.dataset import time_step_seconds

    try:
        return float(time_step_seconds(ds))
    except (TypeError, ValueError, KeyError):
        return float("nan")


def stack_captions(
    ds: Any,
    records: Mapping[str, Mapping[str, Any]] | None = None,
    ds_stack: Any = None,
) -> dict[str, str]:
    """Return the stack page's figure captions, built from the stack and its figures.

    Parameters
    ----------
    ds : xarray.Dataset
        The stack.
    records : Mapping, optional
        What each figure drew (by panel id): the time series' ``suspect``,
        ``serials`` and ``legend``; the T-S diagram's ``theta``,
        ``sigma_interval``, ``colour`` and ``oxygen``.
    ds_stack : xarray.Dataset, optional
        The stack the velocities came from, for :func:`direction_reference`;
        default *ds*.

    Returns
    -------
    dict of str to str
        Captions by panel id.
    """
    records = records or {}
    flags = stack_flag_sentences(ds)
    hydro = records.get("hydro_pts")
    vel = records.get("vel_uvw") or {}
    labelled = bool((hydro or {}).get("labelled"))
    out = {
        "hydro_pts": (
            "Pressure, temperature and salinity from every instrument over the "
            "deployment, on a shared time axis. Each instrument keeps one colour and "
            "line style in all three panels, from the deepest (blue) to the shallowest "
            "(red)"
            + (
                "; the serial at the right end of each line in the pressure panel "
                "names it."
                if labelled
                else "."
            )
            + (_legend_sentence(hydro) if labelled else "")
            + f" {flags['hydro_pts']}"
            + _suspect_sentence(hydro)
            + " Downward spikes in pressure are the mooring being pushed down by "
            "strong currents."
        ),
        "dissolved_oxygen": (
            "Dissolved oxygen from each instrument with an oxygen sensor "
            f"({params.vunit('dissolved_oxygen')}), in the colours of the hydrography "
            f"figure. {flags['dissolved_oxygen']} Oxygen saturation (%) is on each "
            "instrument's own page."
        ),
        "turbidity": (
            "Turbidity from each instrument with a turbidity sensor, in the colours "
            "of the hydrography figure; dots mark each sample so values near zero "
            "show. Units as in the files (NTU, FTU or V, depending on the sensor). "
            + flags["turbidity"]
        ),
    }
    reference = direction_reference(ds if ds_stack is None else ds_stack)
    serials = list(vel.get("serials") or [])
    who = (
        f"the current meter {serials[0]}" if len(serials) == 1 else "each current meter"
    )
    has_w = "up_velocity" in getattr(ds, "data_vars", ())
    components = "Eastward, northward and upward" if has_w else "Eastward and northward"
    keyed = set((hydro or {}).get("labelled") or [])
    legend = list(vel.get("legend") or [])
    colours = ""
    if len(serials) != 1 or keyed:
        colours = " Colours and line styles as in the hydrography figure" + (
            "; the legend names the instruments not in it." if legend and keyed else "."
        )
    out["vel_uvw"] = (
        f"{components} velocity from {who} over the deployment"
        + (f", {reference}" if reference else "")
        + "."
        + colours
        + (
            " Upward velocity is in a shorter panel: its spread is expected to be small."
            if has_w
            else ""
        )
        + f" {flags['vel_uvw']}"
        + _suspect_sentence(vel)
    )
    ts = records.get("ts_diagram") or {}
    t = (
        "Potential temperature"
        if ts.get("theta", True)
        else f"In-situ temperature ({ts.get('no_sigma') or 'position unknown'}, no σθ)"
    )
    cadence = cadence_word(_dt(ds))
    text = (
        f"{t} against salinity (PSU) for all samples. Left: coloured by pressure. "
        f"Right: number of {cadence + ' ' if cadence else ''}samples in each bin "
        "(log scale), showing where the record spends most of its time."
    )
    if ts.get("oxygen"):
        text += " A third panel is coloured by oxygen saturation."
    if ts.get("sigma_interval"):
        text += (
            f" Dashed lines: potential density σθ, every {ts['sigma_interval']:g} "
            f"{params.vunit('potential_density')}."
        )
    out["ts_diagram"] = f"{text} {flags['ts_diagram']}"
    return out
