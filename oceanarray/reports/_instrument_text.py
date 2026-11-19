"""Generated text and tables of the instrument page, read from the instrument's file.

Every value here comes from the stage file the page shows (its attributes, its
sensor record, its flag variables) when the page is built: who the originator is
and where their archive is, how the velocity directions are referenced, the
sensor resolution the originator gives, the processing steps, the quality-flag
table and the notes beside the start and end windows.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from oceanarray.config import parameters as params
from oceanarray.config.unit_tables import unit_label
from oceanarray.pipeline import masking, sensors
from oceanarray.pipeline.qc import originator
from oceanarray.pipeline.qc.originator import MAPPED_SUFFIX
from oceanarray.plotters.helpers import QC_COLORS, marker_flags

from ._captions import HistoryRow, _join, history_rows, rule_sentence
from ._qcflags import flag_bar, flag_counts, good_share, share_text

# ---------------------------------------------------------------------------
# The originator
# ---------------------------------------------------------------------------

#: An ERDDAP dataset URL: the server's base, the protocol and the dataset ID.
_ERDDAP = re.compile(
    r"(?P<base>https?://\S+?/erddap/(?:tabledap|griddap)/)(?P<id>[A-Za-z0-9_.-]+?)"
    r"(?:\.(?:nc|html|csv|json))?(?:[?\s]|$)"
)


@dataclass(frozen=True)
class Originator:
    """Who delivered an imported instrument file, and where their archive is.

    Attributes
    ----------
    label : str
        The header's field name: ``"Originator's dataset"`` (an ERDDAP dataset)
        or ``"Originator's file"``.
    name : str
        The originator's identifier: the ERDDAP dataset ID, else the file name.
    href : str or None
        Link to the originator's archive (the ERDDAP dataset page, the DOI, or
        the originator's information URL), or None when the file gives none.
    file : str
        The name of the file oceanarray read.
    cut_from : str or None
        The dataset the file was cut from, when it is a cut rather than the
        originator's own file.
    """

    label: str
    name: str
    href: str | None
    file: str
    cut_from: str | None = None


def originator_identity(attrs: Mapping[str, Any]) -> Originator | None:
    """Return the originator of an imported file, from its provenance attributes.

    A file cut from an ERDDAP record (``originator_source`` is the ERDDAP dataset
    URL, as the OOI files are) is identified by its ERDDAP dataset ID and linked
    to the dataset's page; a file archived as it was delivered is identified by
    its name and linked to its DOI (``source_doi``) or the originator's
    ``infoUrl``.

    Parameters
    ----------
    attrs : Mapping
        The stage file's global attributes.

    Returns
    -------
    Originator or None
        None for an instrument whose file oceanarray read from the instrument
        itself (no ``originator_file``).
    """
    file = str(attrs.get("originator_file") or "").strip()
    if not file:
        return None
    match = _ERDDAP.search(str(attrs.get("originator_source") or ""))
    if match:
        dataset = match.group("id")
        return Originator(
            "Originator's dataset",
            dataset,
            f"{match.group('base')}{dataset}.html",
            file,
            cut_from=dataset if dataset != file.removesuffix(".nc") else None,
        )
    from oceanarray.pipeline.attribution import bare_doi

    doi = bare_doi(attrs.get("source_doi"))
    info = str(attrs.get("originator_infoUrl") or "").strip()
    href = (
        f"https://doi.org/{doi}" if doi else (info if info.startswith("http") else None)
    )
    return Originator("Originator's file", file, href, file)


# ---------------------------------------------------------------------------
# Velocity direction and sensor resolution
# ---------------------------------------------------------------------------

_VELOCITY = ("east_velocity", "north_velocity")
_DIRECTION_WORDS = re.compile(r"north|declination", re.IGNORECASE)


def _variable_texts(ds: Any, var: str, key: str) -> list[str]:
    """Return the values of attribute *key* the file gives for *var*, in order.

    Read from the variable itself (``key``, ``originator_<key>``) and from the
    sensor records (``originator_<var>_<key>``, where stage 1 moves the
    originator's variable attributes).
    """
    texts: list[str] = []
    if var in getattr(ds, "data_vars", ()):
        attrs = ds[var].attrs
        texts += [attrs.get(key), attrs.get(f"originator_{key}")]
    for record in sensors.records(ds).values():
        texts.append(record.get(f"originator_{var}_{key}"))
    return [str(t).strip() for t in texts if t is not None and str(t).strip()]


def direction_clause(ds: Any) -> str:
    """Return what the velocity directions are relative to, for a caption.

    The velocity variables' ``comment`` is read first (the OOI files say
    "Relative to true north: magnetic declination applied by OOI ..."): its first
    sentence naming north or the declination is used. Otherwise the file's own
    processing says it: the declination stage 3 applied, the originator's
    velocities as delivered, or magnetic north when none was applied.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file.

    Returns
    -------
    str
        E.g. ``"relative to true north: magnetic declination applied by OOI"``,
        ``"relative to true north, declination +12.3° applied"``, ``"as provided
        by the originator"``; ``""`` without velocity.
    """
    if not set(_VELOCITY) & set(getattr(ds, "data_vars", ())):
        return ""
    for var in _VELOCITY:
        for text in _variable_texts(ds, var, "comment"):
            for sentence in re.split(r"(?<=\.)\s+", text):
                if _DIRECTION_WORDS.search(sentence):
                    s = sentence.strip().rstrip(".")
                    if s.lower().startswith("relative to"):
                        return s[:1].lower() + s[1:]
                    return f"as the originator describes them: “{s}”"
    if ds.attrs.get("originator_file"):
        return "as provided by the originator"
    decl = ds.attrs.get("magnetic_declination")
    try:
        return f"relative to true north, declination {float(decl):+.1f}° applied"
    except (TypeError, ValueError):
        return "relative to magnetic north, no declination applied"


def resolution_sentence(ds: Any, variables: Sequence[str]) -> str:
    """Return the sensor resolution the file gives for *variables*, with its origin.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file.
    variables : sequence of str
        The variables shown.

    Returns
    -------
    str
        E.g. ``"Temperature is recorded to 0.0001 °C (originator attribute)."``;
        ``""`` when the file gives no resolution.
    """
    parts = []
    for var in variables:
        values = _variable_texts(ds, var, "resolution")
        if not values:
            continue
        units = _variable_texts(ds, var, "units")
        # The last is the originator's own, which their resolution is given in.
        unit = unit_label(units[-1]) if units else ""
        if not unit and var in getattr(ds, "data_vars", ()):
            unit = unit_label(str(ds[var].attrs.get("units", "")))
        name = var.replace("_", " ")
        parts.append(f"{name} to {values[0]}{f' {unit}' if unit else ''}")
    if not parts:
        return ""
    text = _join(parts)
    return f"{text[:1].upper()}{text[1:]} as recorded (originator attribute)."


# ---------------------------------------------------------------------------
# Flags shown on the figures
# ---------------------------------------------------------------------------


def flag_sets_present(ds: Any, variables: Sequence[str]) -> tuple[bool, bool]:
    """Return whether the file carries (oceanarray's, the originator's) flags for *variables*."""
    names = set(getattr(ds, "data_vars", ()))
    ours = any(f"{v}_qc" in names for v in variables)
    theirs = any(f"{v}{MAPPED_SUFFIX}" in names for v in variables)
    return ours, theirs


def _by(ours: bool, theirs: bool) -> str:
    """Return who flagged, in words: ``"by the originator or oceanarray"``."""
    if ours and theirs:
        return "by the originator or oceanarray"
    return "by the originator" if theirs else "by oceanarray"


def marker_sentence(
    ds: Any, variables: Sequence[str], *, counts: Mapping[int, int] | None = None
) -> str:
    """Return the caption sentence for the dots and crosses on an instrument figure.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file.
    variables : sequence of str
        The variables drawn.
    counts : Mapping of int to int, optional
        Values marked per flag (3, 4, 8) on the figure; the sentence names only
        the markers drawn. Counted from the file when not given.

    Returns
    -------
    str
        E.g. ``"Values flagged by the originator or oceanarray: dots suspect
        (12), crosses bad (2)."``, or a sentence saying nothing is flagged.
    """
    ours, theirs = flag_sets_present(ds, variables)
    if not (ours or theirs):
        return "The file carries no quality flags for these variables."
    if counts is None:
        counts = {3: 0, 4: 0, 8: 0}
        for var in variables:
            flags = marker_flags(ds, var)
            if flags is None or var not in ds.data_vars:
                continue
            here = np.isfinite(np.asarray(ds[var].values, dtype=float))
            for f in counts:
                counts[f] += int(((flags == f) & here).sum())
    who = _by(ours, theirs)
    parts = []
    if counts.get(3):
        parts.append(f"dots suspect ({counts[3]:,})")
    if counts.get(4):
        parts.append(f"crosses bad ({counts[4]:,})")
    if not parts:
        text = f"No value is flagged suspect or bad {who}."
    else:
        text = f"Values flagged {who}: {', '.join(parts)}."
    if counts.get(8):
        text += f" Blue points: values interpolated by oceanarray ({counts[8]:,})."
    return text


# ---------------------------------------------------------------------------
# Processing steps
# ---------------------------------------------------------------------------


def _translation_words(ds: Any) -> list[str]:
    """Return how the originator's flag words were read where the number changed."""
    out: list[str] = []
    for name in ds.data_vars:
        if not str(name).endswith("_qc_originator"):
            continue
        for num, word, ours in originator.dictionary(ds[name]):
            used = int(np.sum(np.asarray(ds[name].values) == num))
            if not used:
                continue
            mapped = 0 if ours is None else ours
            if mapped != num:
                text = (
                    f"'{word}' ({num}) read as "
                    f"{params.QC_FLAG_LABEL.get(mapped, str(mapped))} ({mapped})"
                )
                if text not in out:
                    out.append(text)
    return out


def _units_words(text: object) -> list[str]:
    """Return the unit conversions a file records (``units_converted``), in words.

    Only conversions that changed the values are named; spelling changes (factor 1)
    are summed up as one phrase.
    """
    import json

    try:
        changes = json.loads(str(text)) if text else {}
    except ValueError:
        return []
    if not isinstance(changes, dict):
        return []
    named, spelled = [], False
    for var, change in changes.items():
        if not isinstance(change, dict):
            continue
        factor = change.get("factor", 1.0)
        try:
            factor = float(factor)
        except (TypeError, ValueError):
            factor = 1.0
        if abs(factor - 1.0) < 1e-12:
            spelled = True
            continue
        named.append(
            f"{var.replace('_', ' ')} converted from {unit_label(str(change.get('from', '')))} "
            f"to {unit_label(str(change.get('to', '')))} (×{factor:g})"
        )
    if spelled:
        named.append("unit spellings standardized")
    return named


def _tests(ds: Any) -> list[str]:
    """Return the automatic tests whose limits the file's flag variables record."""
    return sorted(
        {
            m.group(1).replace("_", " ")
            for v in ds.data_vars
            if str(v).endswith("_qc")
            for k in ds[v].attrs
            if (m := re.match(r"qc_(gross_range|spike|flat_line)_", str(k)))
        }
    )


def instrument_history_rows(
    ds: Any,
    *,
    file_type: str = "",
    raw_name: str = "",
    rule: Mapping[str, tuple[int, ...]] | None = None,
) -> list[HistoryRow]:
    """Return the processing table of an instrument page: its file's history, entry by entry.

    Each row is one entry of the ``history`` attribute, as the pipeline wrote it;
    the settings column is read from the file's attributes for the steps that
    have any (how the file was read, the deployment window, the tests run, how
    the originator's flag words were read).

    Parameters
    ----------
    ds : xarray.Dataset
        The stage file the page shows.
    file_type : str, optional
        The instrument entry's ``file_type`` (how the file was read).
    raw_name : str, optional
        The file read, when the instrument's own (not an originator's).
    rule : Mapping, optional
        The flag rule the stack and grid apply (``stack: mask_flags``).

    Returns
    -------
    list of HistoryRow
    """
    attrs = ds.attrs
    who = originator_identity(attrs)

    def settings(text: str) -> str:
        parts: list[str] = []
        if re.match(r"(stage 1:|processing_stage 1)", text):
            if file_type:
                parts.append(f"file type: {file_type}")
            name = (
                who.file
                if who is not None
                else raw_name or attrs.get("source_file", "")
            )
            if name:
                parts.append(
                    f"file: {name}"
                    + (f" (cut from {who.cut_from})" if who and who.cut_from else "")
                )
            parts += _units_words(attrs.get("units_converted"))
        elif re.match(r"(stage 2:|processing_stage 2)", text):
            window = [
                str(attrs.get(k, ""))[:16].replace("T", " ")
                for k in ("deployment_time", "recovery_time")
            ]
            if all(window):
                parts.append(f"window {window[0]} to {window[1]}")
            if "clock_offset" in getattr(ds, "variables", {}):
                try:
                    offset = float(np.ravel(ds["clock_offset"].values)[0])
                except (TypeError, ValueError, IndexError):
                    offset = 0.0
                if offset:
                    parts.append(f"clock offset {offset:+.0f} s")
        if "originator flags translated" in text:
            parts += _translation_words(ds)
        if "QARTOD" in text:
            tests = _tests(ds)
            if tests:
                parts.append(f"tests: {', '.join(tests)}")
            if rule is not None:
                words = _join(
                    [params.QC_FLAG_LABEL.get(f, str(f)) for f in rule["default"]], "or"
                )
                parts.append(f"stack and grid leave out {words or 'nothing'}")
        return ", ".join(parts)

    return history_rows(str(attrs.get("history", "")), settings)


def processing_levels(ds: Any) -> list[tuple[str, str]]:
    """Return ``(variable, processing_level)`` for each variable that records one."""
    return [
        (str(v), str(ds[v].attrs["processing_level"]))
        for v in ds.data_vars
        if ds[v].attrs.get("processing_level")
    ]


# ---------------------------------------------------------------------------
# Quality flags table
# ---------------------------------------------------------------------------

#: The flag sets of the table: (suffix, column-group heading, who in words).
_TABLE_SETS: tuple[tuple[str, str, str], ...] = (
    (MAPPED_SUFFIX, "Originator", "by the originator"),
    ("_qc", "oceanarray", "by oceanarray"),
)


@dataclass
class FlagTable:
    """The Quality flags table of an instrument page.

    Attributes
    ----------
    groups : list of dict
        One per flag set present: ``heading`` and ``flags`` (the classes other than
        good present in its rows, each ``flag``, ``label``, ``color``).
    rows : list of dict
        One per variable with anything not good in either set: ``var``, ``n``
        (values), and per group the share flagged good (``good``,
        ``_qcflags.good_share``), the percentages (``pct``: flag -> text) and the
        bar segments (``bar``).
    lines : list of str
        One-line notes: variables a set did not check, sets the file lacks.
    n_good : int
        Variables with every value good in every set (not listed).
    """

    @property
    def legend(self) -> list[dict[str, Any]]:
        """Return the flag classes of every group, once each, in flag order."""
        seen: dict[int, dict[str, Any]] = {}
        for group in self.groups:
            for f in group["flags"]:
                seen.setdefault(f["flag"], f)
        return [seen[k] for k in sorted(seen)]

    groups: list[dict[str, Any]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    n_good: int = 0


def flag_table(ds: Any) -> FlagTable:
    """Return the instrument's flags by variable: the originator's and oceanarray's.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage-3 file.

    Returns
    -------
    FlagTable
    """
    names = set(ds.data_vars)
    sets = [s for s in _TABLE_SETS if any(str(v).endswith(s[0]) for v in names)]
    variables = [
        v
        for v in (*masking.FLAGGED_VARIABLES, *sorted(names))
        if v in names and any(f"{v}{s[0]}" in names for s in sets)
    ]
    variables = list(dict.fromkeys(variables))
    table = FlagTable()
    present: dict[str, set[int]] = {s[0]: set() for s in sets}
    unchecked: dict[str, list[str]] = {s[0]: [] for s in sets}
    for var in variables:
        here = np.isfinite(np.asarray(ds[var].values, dtype=float)).ravel()
        total = int(here.sum())
        if not total:
            continue
        row: dict[str, Any] = {"var": var, "n": total, "sets": {}}
        flagged = False
        for suffix, _, _ in sets:
            name = f"{var}{suffix}"
            if name not in names:
                continue
            by_flag = flag_counts(np.asarray(ds[name].values).ravel(), here)
            if set(by_flag) == {0}:
                unchecked[suffix].append(var)
                continue
            other = {f: c for f, c in by_flag.items() if f not in params.QC_GOOD_FLAGS}
            if other:
                flagged = True
                present[suffix] |= set(other)
            row["sets"][suffix] = {
                "pct": {f: share_text(c, total) for f, c in other.items()},
                "good": good_share(by_flag, total),
                "bar": flag_bar(by_flag, total),
            }
        if flagged:
            table.rows.append(row)
        else:
            table.n_good += 1
    for suffix, heading, who in sets:
        table.groups.append(
            {
                "suffix": suffix,
                "heading": heading,
                "flags": [
                    {
                        "flag": f,
                        "label": params.QC_FLAG_LABEL.get(f, str(f)),
                        "color": QC_COLORS.get(f, "#999"),
                    }
                    for f in sorted(present[suffix])
                ],
            }
        )
        if unchecked[suffix]:
            table.lines.append(
                f"Not checked {who} (every value 'no QC'): "
                f"{', '.join(v.replace('_', ' ') for v in unchecked[suffix])}."
            )
    if ds.attrs.get("originator_file") and not any(s[0] == MAPPED_SUFFIX for s in sets):
        table.lines.insert(0, "The originator's file carries no quality flags.")
    return table


# ---------------------------------------------------------------------------
# Start and end windows
# ---------------------------------------------------------------------------


def window_lines(
    suggested: Sequence[str | None],
    described: Sequence[str | None],
    *,
    tolerance_s: float = 60.0,
) -> list[tuple[str, str, str]]:
    """Return the vertical lines of the start and end windows: one per time.

    A suggested time (from the pressure record) and the mooring description's
    time within *tolerance_s* of each other are drawn as one line.

    Parameters
    ----------
    suggested : sequence of str or None
        The suggested deployment and recovery times.
    described : sequence of str or None
        The description's deployment and recovery times.
    tolerance_s : float, optional
        Two times this close are one line.

    Returns
    -------
    list of (str, str, str)
        ``(time, colour, label)`` for the window figure.
    """
    import pandas as pd

    def _ts(value: Any) -> pd.Timestamp | None:
        if value is None or value == "":
            return None
        try:
            ts = pd.Timestamp(
                value.isoformat() if hasattr(value, "isoformat") else value
            )
        except (TypeError, ValueError):
            return None
        return ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo else ts

    out: list[tuple[str, str, str]] = []
    for sug, des in zip(suggested, described, strict=True):
        s, d = _ts(sug), _ts(des)
        if (
            s is not None
            and d is not None
            and abs((s - d).total_seconds()) <= tolerance_s
        ):
            out.append((d.isoformat(), "#27ae60", "Description = suggested"))
            continue
        if s is not None:
            out.append((s.isoformat(), "#e67e22", "Suggested"))
        if d is not None:
            out.append((d.isoformat(), "#27ae60", "Description"))
    return out


def disturbed_end(ds: Any, hours: float = 6.0, threshold_dbar: float = 20.0) -> str:
    """Return a note when the record's last hours look disturbed, from its pressure.

    The pressure range over the last *hours* is compared with the ranges of every
    other window of that length: the end is called disturbed when it exceeds
    *threshold_dbar* and twice the 95th percentile of the others.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file.
    hours : float, optional
        Window length.
    threshold_dbar : float, optional
        Smallest pressure change that counts.

    Returns
    -------
    str
        The note, or ``""``.
    """
    if "pressure" not in getattr(ds, "data_vars", ()) or "time" not in ds.coords:
        return ""
    import pandas as pd

    p = pd.Series(
        np.asarray(ds["pressure"].values, dtype=float),
        index=pd.to_datetime(ds["time"].values),
    ).dropna()
    if len(p) < 4:
        return ""
    window = pd.Timedelta(hours=hours)
    if p.index[-1] - p.index[0] < 4 * window:
        return ""
    rng = p.rolling(window).max() - p.rolling(window).min()
    last = float(p[p.index >= p.index[-1] - window].pipe(lambda s: s.max() - s.min()))
    windows = rng[p.index >= p.index[0] + window].values
    windows = windows[np.isfinite(windows)]
    # Without a finite window (a gappy record) the typical range is not known:
    # only the fixed threshold applies.
    typical = float(np.percentile(windows, 95)) if windows.size else None
    if last <= max(threshold_dbar, 2.0 * typical if typical is not None else 0.0):
        return ""
    compared = (
        f", where 95 % of {hours:g}-hour windows in the record change by less than "
        f"{typical:.0f} dbar"
        if typical is not None
        else ""
    )
    return (
        f"The last {hours:g} h look disturbed: pressure changes by {last:.0f} "
        f"dbar{compared}. The originator's end time is kept."
    )


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------


def timeseries_caption(
    ds: Any, variables: Sequence[str], counts: Mapping[int, int]
) -> str:
    """Return the time-series caption: what is drawn, and the markers.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file.
    variables : sequence of str
        The variables drawn, in panel order.
    counts : Mapping of int to int
        Values marked per flag on the figure.

    Returns
    -------
    str
    """
    velocity = {
        "east_velocity": "eastward",
        "north_velocity": "northward",
        "up_velocity": "upward",
    }
    names = []
    for v in variables:
        if v in velocity:
            continue
        if v == "salinity":
            recomputed = "salinity_originator" in ds.data_vars or not ds.attrs.get(
                "originator_file"
            )
            names.append(
                "salinity (PSU, recomputed from C, T, P)"
                if recomputed and "conductivity" in variables
                else "salinity (PSU)"
            )
        elif v == "_pitch_roll_combo":
            names.append("pitch and roll")
        else:
            names.append(v.replace("_", " "))
    vel = [velocity[v] for v in velocity if v in variables]
    if vel:
        direction = direction_clause(ds)
        names.append(
            f"{'/'.join(vel)} velocity" + (f" ({direction})" if direction else "")
        )
    whose = "current meter's" if vel else "instrument's"
    text = f"The {whose} record over the deployment: {_join(names)}. "
    return text + marker_sentence(ds, variables, counts=counts)


def ts_caption(ds: Any, record: Mapping[str, Any], counts: Mapping[int, int]) -> str:
    """Return the T-S caption of an instrument page, from what the figure drew.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file.
    record : Mapping
        What the figure drew: ``theta`` (bool), ``sigma_interval`` (float or None),
        ``colour`` (``"pressure"`` or ``"sample"``), ``oxygen`` (bool).
    counts : Mapping of int to int
        Values marked per flag.

    Returns
    -------
    str
    """
    t = (
        "Potential temperature"
        if record.get("theta", True)
        else f"In-situ temperature ({record.get('no_sigma') or 'position unknown'}, no σθ)"
    )
    colour = (
        "pressure"
        if record.get("colour", "pressure") == "pressure"
        else "sample number"
    )
    text = (
        f"{t} against salinity (PSU). Left: each sample coloured by {colour}. Right: "
        "number of samples in each bin (log scale)."
    )
    if record.get("oxygen"):
        text += " A third panel colours each sample by oxygen saturation."
    interval = record.get("sigma_interval")
    if interval:
        text += f" Dashed: σθ every {interval:g} {params.vunit('potential_density')}."
    return f"{text} {marker_sentence(ds, ['temperature', 'salinity'], counts=counts)}"


def distributions_caption(
    ds: Any, variables: Sequence[str], rule: Mapping[str, tuple[int, ...]]
) -> str:
    """Return the caption of the value distributions, with the rule they apply.

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file.
    variables : sequence of str
        The variables drawn.
    rule : Mapping
        The flag rule (grey bars: every value; coloured: the values it keeps).

    Returns
    -------
    str
    """
    ours, theirs = flag_sets_present(ds, variables)
    text = (
        "Distribution of each variable over the record (log count). Orange dashed: "
        "suspect limit; red dotted: fail limit of the gross-range test, drawn only "
        "when they fall inside the axes."
    )
    if ours or theirs:
        text += " Grey: every value; coloured: the values kept. " + rule_sentence(
            rule, variables, ours=ours, theirs=theirs
        )
    resolution = resolution_sentence(ds, variables)
    return f"{text} {resolution}".strip()


def rose_caption(ds: Any, rule: Mapping[str, tuple[int, ...]]) -> str:
    """Return the current rose caption of an instrument page (the stack's design).

    Parameters
    ----------
    ds : xarray.Dataset
        An instrument's stage file.
    rule : Mapping
        The flag rule applied (to the good values' rose).

    Returns
    -------
    str
        What the roses show: the good values and, when any, the suspect and bad
        values in their own roses, each with its own ring scale.
    """
    from oceanarray.plotters.current import rose_panels

    direction = direction_clause(ds)
    ours, theirs = flag_sets_present(ds, list(_VELOCITY))
    text = (
        "Distribution of current direction (the direction the water flows toward"
        + (f", {direction}" if direction else "")
        + ") and speed. Wedge length is the percentage of the record in that "
        "direction; colours are speed classes."
    )
    if not (ours or theirs) or not set(_VELOCITY) <= set(ds.data_vars):
        return text
    labels = [label for _e, _n, label, _c in rose_panels(ds, rule)[1:]]
    if labels:
        text += (
            " Left: the values flagged neither suspect nor bad; beside it, "
            + _join([f"those flagged {lbl[0].lower()}{lbl[1:]}" for lbl in labels])
            + ". Each rose has its own ring scale."
        )
    if not labels:
        text += " No values flagged suspect or bad."
    elif not any(lbl.startswith("Bad") for lbl in labels):
        text += " No values flagged bad."
    return text


#: Caption of the collapsed NetCDF appendix of an instrument page.
NETCDF_CAPTION = (
    "Contents of the file this page was drawn from, as written by oceanarray. To "
    "produce it yourself, run oceanarray with the mooring description on the "
    "deployment page; the originator's data are linked at the top."
)

__all__ = [
    "NETCDF_CAPTION",
    "FlagTable",
    "Originator",
    "direction_clause",
    "disturbed_end",
    "distributions_caption",
    "flag_sets_present",
    "flag_table",
    "instrument_history_rows",
    "marker_sentence",
    "originator_identity",
    "processing_levels",
    "resolution_sentence",
    "rose_caption",
    "timeseries_caption",
    "ts_caption",
    "window_lines",
]
