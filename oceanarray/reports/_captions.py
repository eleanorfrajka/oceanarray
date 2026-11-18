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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from oceanarray.analysis.derived import ROSE_MIN_FILLED
from oceanarray.analysis.spectral import SPECTRAL_MIN_FILLED
from oceanarray.config import parameters as params
from oceanarray.pipeline import masking
from oceanarray.pipeline.qc.originator import MAPPED_SUFFIX
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
        return masking.resolve_mask_flags(json.loads(str(text)))
    except (ValueError, TypeError):
        return None


def rule_text(rule: Mapping) -> str:
    """Return a rule as the settings column shows it: ``"4"`` or ``"4; velocity 3, 4"``."""
    parts = [", ".join(map(str, rule["default"])) or "none"]
    parts += [
        f"{key} {', '.join(map(str, flags)) or 'none'}"
        for key, flags in rule.items()
        if key != "default"
    ]
    return "; ".join(parts)


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
        groups.setdefault(masking.flags_for(rule, v), []).append(v)
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


def _combine_what(n: int | None, dt: float, history: str, *, stack: bool) -> str:
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
    if stack:
        return (
            f"{who} were placed on a common time axis; each step takes the "
            f"instrument's nearest sample within ±{half}, with no interpolation or "
            "averaging in time; steps with no sample are left empty"
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
        f"rule: {rule_text(rule)}" + (f" ({source})" if source else ""),
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
    attrs = ds.attrs
    history = str(attrs.get("history", ""))
    from oceanarray.pipeline.dataset import time_step_seconds

    dt = time_step_seconds(ds)
    n = n_instruments or _n_from_history(history)
    pressure = np.asarray(ds["pressure"].values, dtype=float)
    dp = attrs.get("dp_dbar")
    if dp is None and len(pressure) > 1:
        dp = float(np.median(np.diff(pressure)))
    pmin = attrs.get("p_start_dbar", pressure.min() if len(pressure) else np.nan)
    pmax = attrs.get("p_end_dbar", pressure.max() if len(pressure) else np.nan)
    combine = _combine_what(n, dt, history, stack=False)
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


def stack_steps(ds: Any) -> list[Step]:
    """Return the processing steps that produced a stack, with the settings used.

    Parameters
    ----------
    ds : xarray.Dataset
        The stack.

    Returns
    -------
    list of Step
        Combine, Flags.
    """
    attrs = ds.attrs
    history = str(attrs.get("history", ""))
    from oceanarray.pipeline.dataset import time_step_seconds

    dt = time_step_seconds(ds)
    n = ds.sizes.get("N_LEVELS") or _n_from_history(history)
    return [
        Step(
            "Combine",
            _combine_what(n, dt, history, stack=True),
            f"{step_text(dt)} steps",
        ),
        _flags_step(attrs, masking.STACK_ATTR, "left out"),
    ]


# ---------------------------------------------------------------------------
# Velocity direction reference
# ---------------------------------------------------------------------------


def direction_reference(ds_stack: Any) -> str:
    """Return what the velocity directions are relative to, from the stack.

    Parameters
    ----------
    ds_stack : xarray.Dataset or None
        The stack the velocities came from.

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
    east = ds_stack["east_velocity"].transpose("time", dim).values
    has_vel = np.isfinite(east).any(axis=0)
    n = int(has_vel.sum())
    if n == 0:
        return ""
    imported = (
        np.array([bool(str(f)) for f in ds_stack["originator_file"].values])
        if "originator_file" in ds_stack.variables
        else np.zeros(ds_stack.sizes[dim], dtype=bool)
    )
    magnetic = set(str(ds_stack.attrs.get("stage2_magnetic_north", "")).split())
    serials = (
        [str(s) for s in ds_stack["serial"].values]
        if "serial" in ds_stack.variables
        else []
    )
    kinds = set()
    for i in np.flatnonzero(has_vel):
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


def grid_captions(c: Any) -> dict[str, str]:
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
    out["nc_file"] = (
        f"The grid file {c.nc_file}: its coordinates, variables and global "
        "attributes, as stored."
    )
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
    """Return the sentence naming the description's extra markers, or ``""``."""
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


def stack_ts_caption(default: str, derived: Any) -> str:
    """Return the stack page's T-S caption: *default*, led by the no-σθ note when needed.

    Parameters
    ----------
    default : str
        The page's standing caption.
    derived : DerivedFields or None
        The page's derived quantities; without a position the figure plots
        in-situ temperature and draws no σθ.

    Returns
    -------
    str
    """
    if derived is not None and (derived.latitude is None or derived.longitude is None):
        return (
            f"In-situ temperature against salinity (PSU) {NO_SIGMA_CAPTION}. {default}"
        )
    return default


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
    directions = f" ({reference})" if reference else ""
    return (
        f"Direction toward which the current flows{directions} and speed for each "
        "current meter. Wedge length is the percentage of the record in that "
        "direction; colours are speed classes. "
        + flag_sentence(
            ds_stack.attrs,
            ["east_velocity", "north_velocity"],
            key=masking.STACK_ATTR,
            action="left out",
        )
    )
