"""Check a mooring YAML without raising: every ERROR and WARNING it holds.

Also the lenient reader used where a file must be read without being checked.
"""

import math
import re
from collections.abc import Mapping
from os import PathLike
from pathlib import Path

import pandas as pd
import yaml

from oceanarray import _deprecated, mask_rules
from oceanarray.exceptions import MooringError
from oceanarray.mooring import catalog, validation
from oceanarray.mooring.blocks import check_people_block, check_source_block
from oceanarray.mooring.instruments import instrument_entries
from oceanarray.mooring.location import LATLON_KEY_PAIRS
from oceanarray.mooring.settings import OPTIONAL_REPORT_SECTIONS, _period_days
from oceanarray.mooring.validation import ValidationIssue
from oceanarray.mooring.yaml_keys import (
    DEPLOYMENT_CODE_PATTERN,
    KNOWN_KEYS,
    SOURCE_FIELDS,
)
from oceanarray.paths import is_path_component

#: The keys each optional settings block may hold, and whether the value must be
#: an integer (True), any non-negative number (False), one of a few words (a
#: tuple) or a flag list (None: ``stack:`` / ``grid: mask_flags``, checked as a rule).
_SETTINGS_KEYS: dict[str, dict[str, bool | tuple[str, ...] | None]] = {
    "stack": {
        "dt_seconds": True,
        "depth_match": ("depth", "hab"),
        "depth_tolerance_m": False,
        "mask_flags": None,
    },
    "grid": {"pmin": False, "pmax": False, "dp": False, "mask_flags": None},
}


def _parse(source: str | PathLike | Mapping) -> dict | None:
    """Parse a config leniently to a mapping, or None if it cannot be read as one."""
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source)
    if not path.exists():
        return None
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _resolve_relative_paths(data: object, path: Path) -> None:
    """Make the mooring YAML's relative paths absolute, from the file's real folder.

    A relative path written in a mooring YAML (``sensor_catalog``) is relative to
    the file as its author wrote it, so it is taken from the folder of the file a
    link points to, not the folder of the link: a processing folder may hold a
    link to a mooring YAML kept elsewhere.  The link's folder still names the
    mooring and holds its stage files.

    Parameters
    ----------
    data : object
        The parsed mooring YAML; changed in place when it is a dict.
    path : pathlib.Path
        The mooring YAML file as given (possibly a link).
    """
    if isinstance(data, dict) and data.get("sensor_catalog"):
        data["sensor_catalog"] = str(
            (path.resolve().parent / str(data["sensor_catalog"])).resolve()
        )


def check(
    mooring_path: str | PathLike | Mapping | None = None,
    *,
    dir_name: str | None = None,
    mooring: str | PathLike | Mapping | None = None,
) -> list[ValidationIssue]:
    """Validate a mooring YAML without raising.

    Parameters
    ----------
    mooring_path : str or os.PathLike
        The ``*.mooring.yaml`` file. A loaded mapping is deprecated; removed in 0.8.0.
    dir_name : str or None, optional
        The folder name the ``name`` field is compared against; default the file's
        folder.
    mooring : str or os.PathLike or Mapping, optional
        Deprecated name of *mooring_path*; removed in 0.8.0.

    Returns
    -------
    list of ValidationIssue
        Every finding (ERROR and WARNING); an empty list means it passed. An
        unknown top-level key is reported as a WARNING.
    """
    mooring_path = _deprecated.resolve_keyword(
        "oceanarray.mooring.check(mooring=)",
        mooring_path,
        mooring,
        required="mooring_path",
        quiet=True,
    )
    if mooring is not None or isinstance(mooring_path, Mapping):
        # One warning for the call, the first old form it uses.
        _deprecated.warn(
            "oceanarray.mooring.check(mooring=)"
            if mooring is not None
            else _deprecated.DICT_GIVEN
        )
    if isinstance(mooring_path, Mapping):
        return _check_data(dict(mooring_path), dir_name)
    path = Path(mooring_path)
    if not path.exists():
        return [ValidationIssue("ERROR", f"File not found: {path}")]
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [ValidationIssue("ERROR", f"YAML parse error: {e}")]
    _resolve_relative_paths(data, path)
    return _check_data(data, path.parent.name if dir_name is None else dir_name)


def _check_data(data: object, dir_name: str | None) -> list[ValidationIssue]:
    """Return every ERROR and WARNING in a parsed mooring YAML."""
    issues = list(validation.validate_mooring(data, dir_name=dir_name))

    if isinstance(data, Mapping):
        issues += _check_settings(data)
        issues += _check_report_sections(data)
        issues += _check_spectrum_markers(data)
        issues += _check_export_codes(data)
        issues += _check_qc_overrides(data)
        issues += _check_caldip(data)
        issues += _check_position_pairs(data)
        issues += _check_legacy_cruise_keys(data)
        issues += _check_sources(data)
        issues += _check_people_and_source(data)
        issues += _check_sensor_models(data)
        issues += _check_catalog(data)
        for key in data:
            if key not in KNOWN_KEYS:
                issues.append(
                    ValidationIssue(
                        "WARNING",
                        f"Unknown top-level key '{key}' — not one of the recognised "
                        f"mooring keys. Check the spelling; it is ignored by the "
                        f"pipeline.",
                    )
                )
    return issues


def _check_position_pairs(data: Mapping) -> list[ValidationIssue]:
    """WARN when a position key has no partner in its own pair (lat and lon come from one pair)."""
    half = [
        lat_key if data.get(lat_key) not in (None, "") else lon_key
        for lat_key, lon_key in LATLON_KEY_PAIRS
        if (data.get(lat_key) in (None, "")) != (data.get(lon_key) in (None, ""))
    ]
    if not half:
        return []
    pairs = ", ".join(f"{a}/{b}" for a, b in LATLON_KEY_PAIRS)
    return [
        ValidationIssue(
            "WARNING",
            f"Position keys {', '.join(half)} have no partner in their own pair; "
            f"latitude and longitude are read from one pair only ({pairs}), so "
            f"{'this key is' if len(half) == 1 else 'these keys are'} ignored.",
        )
    ]


def _check_legacy_cruise_keys(data: Mapping) -> list[ValidationIssue]:
    """WARN when a recovered mooring gives its cruise or ship only with the legacy single key."""
    if data.get("recovery_time") in (None, ""):
        return []
    legacy = [
        key
        for key in ("cruise", "ship")
        if data.get(key) not in (None, "") and data.get(f"recovery_{key}") in (None, "")
    ]
    if not legacy:
        return []
    names = " and ".join(legacy)
    one = len(legacy) == 1
    return [
        ValidationIssue(
            "WARNING",
            f"Legacy {'key' if one else 'keys'} {names} {'is' if one else 'are'} read as "
            f"the deployment {'value' if one else 'values'} only, so the report shows "
            f"the recovery {names} as UNK. Add "
            f"{' and '.join('recovery_' + k for k in legacy)} (and prefer "
            f"{' and '.join('deployment_' + k for k in legacy)} to the legacy "
            f"{'key' if one else 'keys'}).",
        )
    ]


def _check_export_codes(data: Mapping) -> list[ValidationIssue]:
    """Check ``platform_code`` and ``deployment_code``, the OceanSITES file-name fields."""
    issues: list[ValidationIssue] = []
    platform = data.get("platform_code")
    if platform is not None and (not isinstance(platform, str) or "_" in platform):
        # "_" separates the fields of an OceanSITES file name (manual §4.1).
        issues.append(
            ValidationIssue(
                "WARNING",
                f"platform_code {platform!r} must be text without '_' (the OceanSITES "
                f"file-name separator); the export refuses it.",
            )
        )
    code = data.get("deployment_code")
    if code is not None:
        expected = _deployment_month(data.get("deployment_time"))
        if not re.fullmatch(DEPLOYMENT_CODE_PATTERN, str(code)):
            issues.append(
                ValidationIssue(
                    "WARNING",
                    f"deployment_code {code!r} is not YYYYMM; the export refuses it.",
                )
            )
        elif expected is not None and str(code) != expected:
            issues.append(
                ValidationIssue(
                    "WARNING",
                    f"deployment_code {code} does not match deployment_time "
                    f"(month {expected}).",
                )
            )
    return issues


#: The QC override blocks, at mooring level and in each instrument entry: whether
#: the block is keyed by variable, and the fields each entry may hold.
_QC_BLOCKS: dict[str, tuple[bool, frozenset[str]]] = {
    "qc_ranges": (True, frozenset({"fail_span", "suspect_span"})),
    "qc_spike": (True, frozenset({"suspect_threshold", "fail_threshold"})),
    "qc_flat_line": (True, frozenset({"suspect_n", "fail_n", "tolerance"})),
    "tilt_qc": (False, frozenset({"suspect_threshold", "fail_threshold"})),
}


def _is_number(value: object) -> bool:
    """Return True for an int or float (not a bool)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _qc_field_issue(field: str, value: object) -> str | None:
    """Return what is wrong with one QC field's value, or None if it is valid."""
    if field.endswith("_span"):
        if (
            not isinstance(value, list | tuple)
            or len(value) != 2
            or not all(_is_number(v) for v in value)
            or not value[0] < value[1]
        ):
            return "must be two increasing numbers, e.g. [0, 40]"
    elif field in ("suspect_n", "fail_n"):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            return "must be a whole number of samples, 1 or more"
    elif not _is_number(value) or value < 0:
        return "must be a number, 0 or more"
    return None


def _check_qc_block(
    block: str, value: object, where: str, inherited: object = None
) -> list[ValidationIssue]:
    """Check one QC override block's shape; unknown variables are only a WARNING."""
    from oceanarray.config import parameters as params

    by_variable, fields = _QC_BLOCKS[block]
    allowed = ", ".join(sorted(fields))
    if not isinstance(value, Mapping):
        return [
            ValidationIssue("ERROR", f"{where}{block} must be a mapping, not {value!r}")
        ]
    entries = value.items() if by_variable else [(None, value)]
    issues: list[ValidationIssue] = []
    for var, entry in entries:
        label = f"{where}{block}" + (f".{var}" if var is not None else "")
        if var is not None:
            if not isinstance(entry, Mapping):
                issues.append(
                    ValidationIssue("ERROR", f"{label} must be a mapping of {allowed}")
                )
                continue
            if block == "qc_ranges" and var in ("pitch", "roll"):
                issues.append(
                    ValidationIssue(
                        "WARNING",
                        f"{label} is ignored: tilt QC uses the geometric tilt; "
                        "set tilt_qc instead",
                    )
                )
                continue
            if var not in params.VARIABLES and var != "tilt":
                # A configured variable the registry does not know is most likely a
                # typo; it is still allowed (the file may hold such a variable).
                issues.append(
                    ValidationIssue(
                        "WARNING",
                        f"{label}: {var!r} is not a registered variable; check the "
                        "spelling (thresholds apply only if a file has it)",
                    )
                )
            if (
                block == "qc_ranges"
                and var not in params.QC_GROSS_RANGE
                and var != "tilt"
                and "fail_span" not in entry
                # An instrument entry inherits the mooring-level spans (the two
                # are merged before QC runs).
                and not (
                    isinstance(inherited, Mapping)
                    and isinstance(inherited.get(var), Mapping)
                    and "fail_span" in inherited[var]
                )
            ):
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        f"{label} needs a fail_span: {var!r} has no default to start from",
                    )
                )
        for field, field_value in entry.items():
            if field not in fields:
                issues.append(
                    ValidationIssue(
                        "ERROR", f"{label}: unknown field {field!r}; allowed: {allowed}"
                    )
                )
                continue
            problem = _qc_field_issue(field, field_value)
            if problem:
                issues.append(
                    ValidationIssue(
                        "ERROR", f"{label}.{field} = {field_value!r} {problem}"
                    )
                )
    return issues


def _check_qc_overrides(data: Mapping) -> list[ValidationIssue]:
    """Check the QC override blocks at mooring level and in every instrument entry.

    A misspelt field or a malformed span would otherwise be dropped silently by
    the threshold merge, and QC would run with the defaults.
    """
    issues: list[ValidationIssue] = []
    for block in _QC_BLOCKS:
        if block in data:
            issues += _check_qc_block(block, data[block], "")
    lists = [data.get(key) for key in ("clamp", "instruments", "inline")]
    for entries in lists:
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                continue
            where = f"instrument {entry.get('serial', f'index {i}')}: "
            for block in _QC_BLOCKS:
                if block in entry:
                    issues += _check_qc_block(
                        block, entry[block], where, data.get(block)
                    )
    return issues


#: The fields of the ``caldip:`` block and of each of its two ends.
_CALDIP_KEYS = frozenset({"root", "deployment", "recovery", "scheme", "override"})
_CALDIP_END_KEYS = frozenset({"cruise", "cast"})
_CALDIP_OVERRIDE_KEYS = frozenset({"method", "use"})


def _check_caldip(data: Mapping) -> list[ValidationIssue]:
    """Check the ``caldip:`` block: its keys, its two ends, the scheme and any override.

    Every shape mistake is an ERROR: a misspelt key or an unknown method would
    otherwise change which correction runs, silently.
    """
    from oceanarray.config import parameters as params

    if "caldip" not in data:
        return []
    block = data["caldip"]

    def err(message: str) -> ValidationIssue:
        return ValidationIssue("ERROR", f"caldip: {message}")

    if not isinstance(block, Mapping):
        return [err("must be a mapping (root, deployment, recovery, scheme, override)")]
    issues: list[ValidationIssue] = []
    for key in block:
        if key not in _CALDIP_KEYS:
            issues.append(err(f"unknown key '{key}'; allowed: {sorted(_CALDIP_KEYS)}"))
    ends = [end for end in ("deployment", "recovery") if end in block]
    if not ends:
        issues.append(err("names no cast; give deployment and/or recovery"))
    for end in ends:
        value = block[end]
        if not isinstance(value, Mapping) or set(value) != _CALDIP_END_KEYS:
            issues.append(err(f"{end} must be {{cruise: <cruise>, cast: <cast>}}"))
        elif not all(isinstance(value[k], str) and value[k] for k in _CALDIP_END_KEYS):
            issues.append(err(f"{end}: cruise and cast must be non-empty text"))
        elif not all(is_path_component(value[k]) for k in _CALDIP_END_KEYS):
            issues.append(
                err(f"{end}: cruise and cast must each be one folder or file name")
            )
    if "root" in block and not (isinstance(block["root"], str) and block["root"]):
        issues.append(err("root must be a path"))
    scheme = block.get("scheme", "standard")
    if scheme not in params.CALDIP_SCHEMES:
        issues.append(
            err(f"scheme {scheme!r} is not one of {list(params.CALDIP_SCHEMES)}")
        )
    override = block.get("override")
    if override is not None:
        if not isinstance(override, Mapping):
            issues.append(err("override must be keyed by variable"))
            override = {}
        for var, entry in override.items():
            if var not in params.CALDIP_TARGETS:
                issues.append(
                    err(
                        f"override: '{var}' is not one of {list(params.CALDIP_TARGETS)}"
                    )
                )
                continue
            if not isinstance(entry, Mapping):
                issues.append(err(f"override.{var} must be a mapping (method, use)"))
                continue
            for field, value in entry.items():
                if field not in _CALDIP_OVERRIDE_KEYS:
                    issues.append(
                        err(
                            f"override.{var}: unknown field '{field}'; allowed: "
                            f"{sorted(_CALDIP_OVERRIDE_KEYS)}"
                        )
                    )
                elif field == "method" and value not in params.CALDIP_METHODS:
                    issues.append(
                        err(
                            f"override.{var}.method {value!r} is not one of "
                            f"{list(params.CALDIP_METHODS)}"
                        )
                    )
                elif field == "method" and value == "slope" and var != "conductivity":
                    issues.append(
                        err(f"override.{var}.method slope applies to conductivity only")
                    )
                elif field == "use" and value not in params.CALDIP_USE:
                    issues.append(
                        err(
                            f"override.{var}.use {value!r} is not one of "
                            f"{list(params.CALDIP_USE)}"
                        )
                    )
                elif (
                    field == "use"
                    and value != "both"
                    and _use_names_missing_end(value, ends)
                ):
                    issues.append(
                        err(f"override.{var}.use {value!r} names a dip the block lacks")
                    )
    return issues


def _use_names_missing_end(use: str, ends: list[str]) -> bool:
    """Return True when ``use`` keeps a dip end the ``caldip:`` block does not name."""
    wanted = {"pre": "deployment", "post": "recovery"}[use]
    return wanted not in ends


def _deployment_month(deployment_time: object) -> str | None:
    """Return ``YYYYMM`` of a deployment time, or None if it cannot be read."""
    if deployment_time is None:
        return None
    try:
        return pd.Timestamp(str(deployment_time)).strftime("%Y%m")
    except (ValueError, TypeError):
        return None


def _check_sources(data: Mapping) -> list[ValidationIssue]:
    """Return ERRORs for a ``<field>_source`` that is not text (see :data:`SOURCE_FIELDS`)."""
    return [
        ValidationIssue(
            "ERROR",
            f"'{field}_source' must be text (where the value came from); "
            f"got {data[f'{field}_source']!r}.",
        )
        for field in SOURCE_FIELDS
        if f"{field}_source" in data
        and data[f"{field}_source"] is not None
        and not isinstance(data[f"{field}_source"], str)
    ]


def _check_sensor_models(data: Mapping) -> list[ValidationIssue]:
    """Return ERRORs for an instrument's ``sensor_model`` without its ``sensor_model_source``."""
    issues: list[ValidationIssue] = []
    for entry in instrument_entries(data):
        if not isinstance(entry, Mapping) or entry.get("sensor_model") in (None, ""):
            continue
        label = f"{entry.get('instrument', '?')} s/n {entry.get('serial', '?')}"
        if not isinstance(entry["sensor_model"], str):
            issues.append(
                ValidationIssue("ERROR", f"{label}: sensor_model must be text.")
            )
        if not entry.get("sensor_model_source"):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"{label}: sensor_model needs sensor_model_source (where the model "
                    "came from, e.g. the cruise report).",
                )
            )
    return issues


def _check_catalog(data: Mapping) -> list[ValidationIssue]:
    """Return an ERROR when ``sensor_catalog`` names a file that cannot be used."""
    path = data.get(catalog.KEY)
    if path is None:
        return []
    if not isinstance(path, (str, PathLike)):
        return [ValidationIssue("ERROR", f"sensor_catalog: {path!r} is not a path")]
    if not Path(path).is_absolute():
        # A mooring YAML given as a dict: the path is relative to the mooring
        # directory, which the caller resolves (``pipeline.resolve_mooring``).
        return []
    try:
        catalog.load(path)
    except MooringError as e:
        return [ValidationIssue("ERROR", str(e))]
    return []


def _check_people_and_source(data: Mapping) -> list[ValidationIssue]:
    """Return ERRORs and WARNINGs for the optional ``people:`` and ``source:`` blocks."""
    issues: list[ValidationIssue] = []
    for key, check_block in (
        ("people", check_people_block),
        ("source", check_source_block),
    ):
        if data.get(key) is None:
            continue
        errors, warns = check_block(data[key])
        issues += [ValidationIssue("ERROR", m) for m in errors]
        issues += [ValidationIssue("WARNING", m) for m in warns]
    return issues


def _check_settings(data: Mapping) -> list[ValidationIssue]:
    """Return ERRORs for a malformed ``stack:`` or ``grid:`` block (both optional)."""
    issues: list[ValidationIssue] = []
    for block, allowed in _SETTINGS_KEYS.items():
        if block not in data or data[block] is None:
            continue
        value = data[block]
        if not isinstance(value, Mapping):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"'{block}:' must be a mapping of {', '.join(allowed)}; "
                    f"got {value!r}.",
                )
            )
            continue
        for key, setting in value.items():
            if key not in allowed:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        f"'{block}: {key}' is not a {block} setting; expected one of "
                        f"{', '.join(allowed)}.",
                    )
                )
                continue
            integer = allowed[key]
            if integer is None:
                try:
                    mask_rules.resolve_mask_flags(setting)
                except ValueError as exc:
                    issues.append(ValidationIssue("ERROR", f"{block}: {exc}."))
                continue
            if isinstance(integer, tuple):
                if setting not in integer:
                    issues.append(
                        ValidationIssue(
                            "ERROR",
                            f"'{block}: {key}' must be one of {', '.join(integer)}; "
                            f"got {setting!r}.",
                        )
                    )
                continue
            ok = (
                isinstance(setting, int)
                if integer
                else isinstance(setting, int | float)
            )
            if (
                isinstance(setting, bool)
                or not ok
                or not math.isfinite(setting)
                or setting < 0
            ):
                kind = (
                    "a non-negative integer"
                    if integer
                    else "a finite non-negative number"
                )
                issues.append(
                    ValidationIssue(
                        "ERROR", f"'{block}: {key}' must be {kind}; got {setting!r}."
                    )
                )
        if block == "stack" and value.get("dt_seconds") == 0:
            issues.append(ValidationIssue("ERROR", "'stack: dt_seconds' must be > 0."))
        if block == "grid":
            p0, p1, step = (value.get(k) for k in ("pmin", "pmax", "dp"))
            if step == 0:
                issues.append(ValidationIssue("ERROR", "'grid: dp' must be > 0."))
            if isinstance(p0, int | float) and isinstance(p1, int | float) and p1 <= p0:
                issues.append(
                    ValidationIssue(
                        "ERROR", f"'grid: pmax' ({p1}) must be above pmin ({p0})."
                    )
                )
    grid_block = data.get("grid")
    if isinstance(grid_block, Mapping) and grid_block.get("mask_flags") is not None:
        try:
            mask_rules.combine(
                mask_rules.stack_rule(data),
                mask_rules.resolve_mask_flags(grid_block["mask_flags"]),
            )
        except ValueError as exc:
            issues.append(ValidationIssue("ERROR", f"grid: {exc}."))
    return issues


def _check_report_sections(data: Mapping) -> list[ValidationIssue]:
    """Return ERRORs for a malformed ``report_sections`` block (optional).

    A mapping of page -> list of optional section names.  A page with no optional
    sections, or a section name that is not optional for that page, is an ERROR naming
    the sections the page does have (today only ``overflow`` on ``grid``).
    """
    issues: list[ValidationIssue] = []
    block = data.get("report_sections")
    if block is None:
        return issues
    if not isinstance(block, Mapping):
        return [
            ValidationIssue(
                "ERROR",
                f"'report_sections:' must be a mapping of page -> [section, ...]; "
                f"got {block!r}.",
            )
        ]
    for page, sections in block.items():
        if page not in OPTIONAL_REPORT_SECTIONS:
            pages = ", ".join(sorted(OPTIONAL_REPORT_SECTIONS))
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"'report_sections: {page}' has no optional sections; pages with "
                    f"optional sections: {pages}.",
                )
            )
            continue
        if not isinstance(sections, (list, tuple)):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"'report_sections: {page}' must be a list of section names; "
                    f"got {sections!r}.",
                )
            )
            continue
        allowed = OPTIONAL_REPORT_SECTIONS[page]
        for name in sections:
            # A non-string name (e.g. a nested list) is unhashable; guard before the
            # membership test so check() reports an ERROR rather than raising.
            if not isinstance(name, str) or name not in allowed:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        f"'report_sections: {page}' has no optional section "
                        f"{name!r}; optional sections for {page}: "
                        f"{', '.join(sorted(allowed))}.",
                    )
                )
    return issues


def _check_spectrum_markers(data: Mapping) -> list[ValidationIssue]:
    """Return ERRORs for a malformed ``spectrum_markers`` block (optional).

    A mapping of label -> period with its unit (``s``, ``min``, ``h`` or ``d``),
    e.g. ``{"4 d": "4 d", "S2": "12 h"}``.
    """
    block = data.get("spectrum_markers")
    if block is None:
        return []
    if not isinstance(block, Mapping):
        return [
            ValidationIssue(
                "ERROR",
                "'spectrum_markers:' must be a mapping of label -> period with its "
                f"unit (e.g. '4 d', '12.42 h', '9 min'); got {block!r}.",
            )
        ]
    return [
        ValidationIssue(
            "ERROR",
            f"'spectrum_markers: {label}' must be a period with its unit "
            f"(s, min, h or d), e.g. '4 d'; got {period!r}.",
        )
        for label, period in block.items()
        if _period_days(period) is None
    ]


def serial_collision_errors(
    mooring: str | Path | Mapping | None = None,
    *,
    mooring_config: str | Path | Mapping | None = None,
) -> list[str]:
    """Return the serial-collision ERROR messages for a mooring YAML, or ``[]``.

    The pre-flight guard the processing path runs before any stage writes: it
    builds the combined instrument list (``clamp`` + ``instruments`` + inline, same
    rule as :func:`~oceanarray.mooring.validation.validate_mooring`) and returns only the
    :func:`~oceanarray.mooring.validation.check_serials`
    ERROR messages.  It parses leniently through :func:`_parse`
    — a missing or unparseable file returns ``[]`` (those are reported by the normal
    validator, not this guard) — rather than :func:`load`, which
    would raise on the very collision this guard reports and swallow it.

    Parameters
    ----------
    mooring : str or pathlib.Path or Mapping
        A mooring YAML path, or an already-parsed mapping.
    mooring_config : str or pathlib.Path or Mapping, optional
        Deprecated name of *mooring*; removed in 0.8.0.

    Returns
    -------
    list of str
        One message per serial collision; empty when the serials are safe.
    """
    mooring = _deprecated.resolve_keyword(
        "oceanarray.mooring.serial_collision_errors(mooring_config=)",
        mooring,
        mooring_config,
        required="mooring",
    )
    data = _parse(mooring)
    if not isinstance(data, dict):
        return []
    instrument_list = data.get("clamp", data.get("instruments", []))
    inline = [
        e
        for e in data.get("inline", [])
        if isinstance(e, dict)
        and "instrument" in e
        and (e.get("filename") or e.get("skip"))
    ]
    combined = list(instrument_list) + inline
    return [i.message for i in validation.check_serials(combined) if i.level == "ERROR"]
