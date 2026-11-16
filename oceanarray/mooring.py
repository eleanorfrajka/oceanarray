"""Load, validate and describe one mooring deployment from its YAML file.

A *mooring* here means one deployed instance — ``dune2_1_2026``, from
deployment to recovery — described by a ``*.mooring.yaml`` file (or the plain
dict parsed from one). This module is the single seam for reading that
description; every stage and report receives the loaded dict rather than
re-reading the file.

Inside the package the module is imported under an alias so a function whose
parameter is ``mooring`` can still call the module's functions::

    from oceanarray import mooring as _mooring
    ...
    cfg = _mooring.load(path)

Users write ``oceanarray.mooring.load(...)``. This module handles the description
*file* — ``load``, ``check``, ``template``, ``save``; interpreting the loaded dict
(which entries to process, the position) is the caller's job, through
:func:`oceanarray.instruments.instrument_entries` and
:func:`oceanarray.location.parse_latlon_with_source`.

The file shape is a cross-package contract (moordiag reads the same file), so
``load`` keeps every key exactly as written and never rewrites a value.
"""

import logging
import math
import re
from collections.abc import Iterable, Mapping
from os import PathLike
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from oceanarray.config import validation
from oceanarray.config.validation import ValidationIssue
from oceanarray.exceptions import MooringError
from oceanarray.location import LATLON_KEY_PAIRS
from oceanarray.paths import is_path_component

logger = logging.getLogger(__name__)

# Keys the fixtures, ``cmd_init`` and real UHH configs are known to use. The
# list is deliberately generous: ``cmd_init`` writes ``deployment_cruise`` /
# ``recovery_ship`` etc. while older configs use ``cruise`` / ``ship`` /
# ``status``, and both are valid. An unknown top-level key is a WARNING (not an
# ERROR) for now so a real config carrying a key the fixtures never showed is
# not rejected; promotion to ERROR is planned for a later release, once this list
# has been checked against the configs on the drive (``scripts/survey_mooring_keys.py``).
#: The mooring-level fields whose origin or correction a description may record
#: as ``<field>_source`` (free text), carried into every file beside the field.
SOURCE_FIELDS: tuple[str, ...] = (
    "waterdepth",
    "deployment_time",
    "recovery_time",
    "latitude",
    "longitude",
)

KNOWN_KEYS: frozenset[str] = frozenset(
    {
        # where a mooring-level value came from, or why it was corrected
        *(f"{field}_source" for field in SOURCE_FIELDS),
        # identity / header
        "name",
        "cleaname",
        "year",
        "waterdepth",
        "status",
        # OceanSITES identity (read by the export; see ``_check_export_codes``)
        "platform_code",
        "deployment_code",
        # cruise / ship (both vocabularies)
        "cruise",
        "ship",
        "deployment_cruise",
        "deployment_ship",
        "recovery_cruise",
        "recovery_ship",
        # time
        "deployment_time",
        "recovery_time",
        # position: the bare pair and the three graded tiers the position chain
        # reads in order of preference (seabed → deployment → planned → latitude).
        "latitude",
        "longitude",
        "seabed_latitude",
        "seabed_longitude",
        "deployment_latitude",
        "deployment_longitude",
        "planned_latitude",
        "planned_longitude",
        # QC thresholds (read by the QC pass; shapes checked by _check_qc_overrides).
        "qc_ranges",
        "qc_spike",
        "tilt_qc",
        "qc_flat_line",
        # calibration-dip correction (read at the front of stage 3; ``_check_caldip``)
        "caldip",
        # Deprecated: a raw-data root in the description is a run-time fact; use
        # raw_dir / --raw-dir. check() WARNs; the override is removed in 0.7.0.
        "directory",
        # instrument lists
        "clamp",
        "instruments",
        "inline",
        # optional stack / grid settings for oceanarray.run (``_check_settings``)
        "stack",
        "grid",
        # optional report sections to turn on, keyed by page (``_check_report_sections``)
        "report_sections",
        # people and data source for files that name none (``_check_people_and_source``)
        "people",
        "source",
    }
)

#: Optional report sections that ``report_sections`` may turn on, keyed by page.
#: A page or section not listed here is rejected by ``_check_report_sections``.
OPTIONAL_REPORT_SECTIONS: dict[str, frozenset[str]] = {"grid": frozenset({"overflow"})}

#: A deployment code: ``YYYYMM`` with a calendar month (01–12).
DEPLOYMENT_CODE_PATTERN = r"\d{4}(0[1-9]|1[0-2])"

#: The keys each optional settings block may hold, and whether the value must be
#: an integer (True), any non-negative number (False), one of a few words (a
#: tuple) or a flag list (None: ``grid: mask_flags``, checked by the gridder).
_SETTINGS_KEYS: dict[str, dict[str, bool | tuple[str, ...] | None]] = {
    "stack": {
        "dt_seconds": True,
        "depth_match": ("depth", "hab"),
        "depth_tolerance_m": False,
    },
    "grid": {"pmin": False, "pmax": False, "dp": False, "mask_flags": None},
}

REQUIRED_KEYS: list[str] = validation.REQUIRED_MOORING_KEYS

#: The description's settings (as opposed to its facts): what a stack records as
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
        A loaded mooring description.
    stack : Mapping, optional
        The ``stack:`` settings actually used (e.g. a ``dt_seconds`` given on the
        command line); they replace the description's own ``stack:`` block.

    Returns
    -------
    str
        The :data:`SETTINGS_BLOCKS` the description sets, then its instrument
        entries as processed (clamp and inline merged, ``hab`` resolved, without
        the hardware-only keys) under ``instruments``, the key ``load`` also
        reads. Pasted into a mooring description, it reruns the same product.
    """
    from oceanarray.instruments import instrument_entries

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


def _resolve_path(mooring: str | PathLike, proc_dir: str | PathLike | None) -> Path:
    """Resolve a reference (a YAML path, or a bare name + *proc_dir*) to a YAML path."""
    path = Path(mooring)
    if path.suffix in {".yaml", ".yml"} or path.exists():
        return path
    if proc_dir is not None:
        name = str(mooring)
        return Path(proc_dir) / name / f"{name}.mooring.yaml"
    return path


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


def load(
    mooring: str | PathLike | Mapping,
    *,
    proc_dir: str | PathLike | None = None,
) -> dict:
    """Load and validate one mooring's description.

    Parameters
    ----------
    mooring : str or os.PathLike or Mapping
        A path to a ``*.mooring.yaml`` file, a bare mooring name resolved
        against *proc_dir*, or an already-parsed mapping (returned as a shallow
        copy, keys unchanged).
    proc_dir : str or os.PathLike or None, optional
        The processing directory a bare mooring name is resolved against.

    Returns
    -------
    dict
        The parsed description, every key kept as written.

    Raises
    ------
    MooringError
        If the file is missing, is not a mapping, or fails validation with any
        ERROR-level issue. WARNING-level issues are kept on the exception's
        ``issues`` attribute but do not raise.
    """
    if isinstance(mooring, Mapping):
        data = dict(mooring)
        dir_name = None
    else:
        path = _resolve_path(mooring, proc_dir)
        if not path.exists():
            raise MooringError(f"Mooring config not found: {path}")
        try:
            with path.open() as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise MooringError(f"YAML parse error in {path}: {e}") from e
        dir_name = path.parent.name

    if not isinstance(data, dict):
        raise MooringError("Mooring YAML root must be a mapping")

    issues = check(data, dir_name=dir_name)
    errors = [i for i in issues if i.level == "ERROR"]
    if errors:
        summary = "; ".join(i.message for i in errors)
        raise MooringError(f"Invalid mooring config: {summary}", issues)
    return data


def check(
    mooring: str | PathLike | Mapping,
    *,
    dir_name: str | None = None,
) -> list[ValidationIssue]:
    """Validate a mooring description without raising.

    Parameters
    ----------
    mooring : str or os.PathLike or Mapping
        A path to a ``*.mooring.yaml`` file or an already-parsed mapping.
    dir_name : str or None, optional
        The directory name the ``name`` field is compared against. When
        *mooring* is a path, the path's parent name is used and this is ignored.

    Returns
    -------
    list of ValidationIssue
        Every finding (ERROR and WARNING); an empty list means it passed. An
        unknown top-level key is reported as a WARNING.
    """
    if isinstance(mooring, Mapping):
        data: object = dict(mooring)
    else:
        path = Path(mooring)
        if not path.exists():
            return [ValidationIssue("ERROR", f"File not found: {path}")]
        try:
            with path.open() as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            return [ValidationIssue("ERROR", f"YAML parse error: {e}")]
        if dir_name is None:
            dir_name = path.parent.name

    issues = list(validation.validate_mooring(data, dir_name=dir_name))

    if isinstance(data, Mapping):
        issues += _check_settings(data)
        issues += _check_report_sections(data)
        issues += _check_export_codes(data)
        issues += _check_qc_overrides(data)
        issues += _check_caldip(data)
        issues += _check_position_pairs(data)
        issues += _check_legacy_cruise_keys(data)
        issues += _check_sources(data)
        issues += _check_people_and_source(data)
        issues += _check_sensor_models(data)
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
    from oceanarray.instruments import instrument_entries

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


def _check_people_and_source(data: Mapping) -> list[ValidationIssue]:
    """Return ERRORs and WARNINGs for the optional ``people:`` and ``source:`` blocks."""
    from oceanarray.pipeline import attribution

    issues: list[ValidationIssue] = []
    for key, check_block in (
        ("people", attribution.check_people_block),
        ("source", attribution.check_source_block),
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
                from oceanarray.pipeline.grid import resolve_mask_flags

                try:
                    resolve_mask_flags(setting)
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


def resolve_report_sections(page: str, description: Mapping) -> list[str]:
    """Return the optional sections turned on for a report *page*, from *description*.

    Reads ``report_sections.<page>`` from the mooring description and defaults to ``[]``
    (every optional section off).  Every report setting is read from the mooring YAML —
    the one complete description of how a mooring was processed — and this is the single
    place this key is resolved.

    Parameters
    ----------
    page : str
        The report page, e.g. ``"grid"``.
    description : Mapping
        The mooring description.

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


# The one source for the commented ``init`` template: key -> inline description.
# ``save`` writes these as end-of-line comments; keep in sync with KNOWN_KEYS.
_FIELD_COMMENTS: dict[str, str] = {
    "name": "mooring identifier; every output file is named from it",
    "platform_code": "OceanSITES station code, no '_' (e.g. DUNE2); used by the export",
    "deployment_code": "YYYYMM of deployment_time (e.g. 202607); used by the export",
    "year": "deployment year, e.g. 2026",
    "waterdepth": "water depth at anchor in metres",
    "deployment_cruise": "cruise ID, e.g. MSM142",
    "deployment_ship": "ship name, e.g. MS Merian",
    "recovery_cruise": "cruise ID at recovery (omit if same cruise)",
    "recovery_ship": "ship name at recovery (omit if same ship)",
    "latitude": "planned position, e.g. 65 29.840 N",
    "longitude": "planned position, e.g. 029 24.600 W",
    "deployment_time": "ISO-8601, e.g. '2026-05-07T17:06'",
    "recovery_time": "ISO-8601, e.g. '2026-07-10T17:45'",
}

# Per-instrument field descriptions. ``save`` comments the first clamp entry that
# carries each field, so the explanation appears once (as the hand-written stub did).
_INSTRUMENT_COMMENTS: dict[str, str] = {
    "serial": (
        "if comma-separated (e.g. '16430, R01-024') the first token is the "
        "primary serial used in filenames/output; the rest is beacon_id. "
        "Characters illegal in filenames are stripped."
    ),
    "hab": "height above bottom (m) of transducer",
    "header": ".hdr file for T-matrix (same base name if null)",
    "clock_offset": "seconds; computer time minus instrument time at recovery",
    "hab_bottom": "HAB of transducer face (downward-looking)",
    "hab_top": "HAB of top of housing",
    "orientation": "down or up",
}

# One example clamp block per instrument type; a user fills these in and deletes
# the unused ones. Key order is the order written to the file.
_TEMPLATE_INSTRUMENTS: list[dict] = [
    {
        "instrument": "aquadopp",
        "serial": None,
        "hab": None,
        "sample_interval_seconds": None,
        "filename": None,
        "file_type": "nortek-raw",
        "header": None,
        "clock_offset": 0,
        "computer_clock_at_recovery": None,
        "instrument_clock_at_recovery": None,
    },
    {
        "instrument": "microcat",
        "serial": None,
        "hab": None,
        "sample_interval_seconds": None,
        "filename": None,
        "file_type": "sbe-cnv",
        "clock_offset": 0,
        "computer_clock_at_recovery": None,
        "instrument_clock_at_recovery": None,
    },
    {
        "instrument": "rbrsolo",
        "serial": None,
        "hab": None,
        "sample_interval_seconds": None,
        "filename": None,
        "file_type": "rbr-rsk",
        "clock_offset": 0,
        "computer_clock_at_recovery": None,
        "instrument_clock_at_recovery": None,
    },
    {
        "instrument": "ADCP",
        "serial": None,
        "hab_bottom": None,
        "hab_top": None,
        "sample_interval_seconds": None,
        "filename": None,
        "file_type": "rdi-raw",
        "orientation": "down",
        "clock_offset": 0,
        "computer_clock_at_recovery": None,
        "instrument_clock_at_recovery": None,
    },
]


def template(name: str, *, instruments: list[dict] | None = None) -> dict:
    """Return a skeleton mooring description for *name*, as a plain dict.

    The required and recommended keys are present with ``None`` values for a user
    to fill in; ``clamp`` carries one example block per instrument type (or
    *instruments* if given). :func:`save` renders it with the field comments.

    Parameters
    ----------
    name : str
        The mooring name (becomes the ``name`` field; matches the directory).
    instruments : list of dict or None, optional
        Clamp entries to use instead of the one-per-type examples.

    Returns
    -------
    dict
        The skeleton description, keys in write order.
    """
    doc: dict = {"name": name}
    for key in (
        "platform_code",
        "year",
        "waterdepth",
        "deployment_cruise",
        "deployment_ship",
        "recovery_cruise",
        "recovery_ship",
        "latitude",
        "longitude",
        "deployment_time",
        "deployment_code",
        "recovery_time",
    ):
        doc[key] = None
    examples = _TEMPLATE_INSTRUMENTS if instruments is None else instruments
    doc["clamp"] = [dict(entry) for entry in examples]
    return doc


def _format_optional_blocks() -> str:
    """Return the commented-out ``stack:``, ``grid:``, ``people:`` and ``source:`` blocks."""
    from oceanarray.config import parameters as params
    from oceanarray.pipeline._run import DEFAULTS

    st, gr = DEFAULTS["stack"], DEFAULTS["grid"]
    return "\n".join(
        [
            "optional: uncomment to change the stack and grid settings",
            "stack:",
            f"  dt_seconds: {st['dt_seconds']:g}  # time step of the stack, seconds",
            "grid:",
            f"  pmin: {gr['pmin']:g}  # shallowest grid level, dbar",
            f"  pmax: {gr['pmax']:g}  # deepest grid level, dbar",
            f"  dp: {gr['dp']:g}  # grid spacing, dbar",
            f"  mask_flags: {list(params.GRID_MASK_FLAGS)}  # QC flags left out "
            "before gridding; or {default: [4, 9], velocity: [3, 4, 9]}",
            "",
            "optional: who and where the data came from, for files that do not say",
            "people:  # used when the data files name no one",
            "  principal_investigator: Given Surname  # comma-separated",
            "  principal_investigator_email: name@example.org",
            "  principal_investigator_id: https://orcid.org/0000-0000-0000-0000",
            "  contributor_name: Given Surname; Given Surname  # semicolon-separated",
            "  contributor_role: data processing; technician",
            "  people_source: where these names came from",
            "source:  # fills what the data files do not say",
            "  doi: 10.xxxx/xxxxx  # the data package's DOI",
            "  citation: how the source asks to be cited",
            "  license: the source's license",
            "  acknowledgement: funding and acknowledgement",
            "  source_note: where these values came from",
        ]
    )


def save(
    mooring: Mapping,
    path: str | PathLike,
    *,
    overwrite: bool = False,
    optional_blocks: bool = False,
) -> Path:
    """Write a mooring description to a YAML file with field comments.

    Lenient by design — a description being drafted may be incomplete, so ``save``
    runs :func:`check` and logs a warning on any ERROR but still writes (unlike
    :func:`load`, which refuses an invalid description about to be processed). The
    field comments come from the module's comment tables, so they cannot drift
    from the validator.

    Parameters
    ----------
    mooring : Mapping
        The description to write (e.g. from :func:`template`).
    path : str or os.PathLike
        Destination ``*.mooring.yaml`` path.
    overwrite : bool, default False
        Overwrite an existing file; otherwise a ``FileExistsError`` is raised.
    optional_blocks : bool, default False
        Also write the optional ``stack:`` and ``grid:`` blocks, commented out with
        their default values, before ``clamp`` (``oceanarray init`` does).

    Returns
    -------
    pathlib.Path
        The path written.

    Raises
    ------
    FileExistsError
        If *path* exists and *overwrite* is False.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists")

    errors = [i.message for i in check(mooring) if i.level == "ERROR"]
    if errors:
        logger.warning(
            "saving an incomplete mooring description (%d issue(s) to resolve): %s",
            len(errors),
            "; ".join(errors),
        )

    def _block(
        items: Iterable[tuple[str, object]],
        comments: Mapping[str, str],
        seen: set[str] | None = None,
    ) -> CommentedMap:
        """CommentedMap of *items*; comment keys in *comments* (first occurrence if *seen*)."""
        block = CommentedMap()
        for key, value in items:
            block[key] = value
            if key in comments and (seen is None or key not in seen):
                block.yaml_add_eol_comment(comments[key], key)
                if seen is not None:
                    seen.add(key)
        return block

    data = dict(mooring)
    clamp = data.pop("clamp", None)
    doc = _block(data.items(), _FIELD_COMMENTS)
    if clamp is not None:
        seen: set[str] = set()
        doc["clamp"] = CommentedSeq(
            _block(entry.items(), _INSTRUMENT_COMMENTS, seen) for entry in clamp
        )
        if optional_blocks:
            doc.yaml_set_comment_before_after_key(
                "clamp", before=_format_optional_blocks(), indent=0
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = YAML()
    writer.default_flow_style = False
    writer.width = 120
    # newline="\n": deterministic LF on every platform — Windows text mode would
    # otherwise write CRLF and break the byte-identical init-template golden.
    with path.open("w", newline="\n") as fh:
        writer.dump(doc, fh)
    return path


def serial_collision_errors(mooring_config: str | Path | Mapping) -> list[str]:
    """Return the serial-collision ERROR messages for a mooring config, or ``[]``.

    The pre-flight guard the processing path runs before any stage writes: it
    builds the combined instrument list (``clamp`` + ``instruments`` + inline, same
    rule as :func:`~oceanarray.config.validation.validate_mooring`) and returns only the
    :func:`~oceanarray.config.validation.check_serials`
    ERROR messages.  It parses leniently through :func:`_parse`
    — a missing or unparseable file returns ``[]`` (those are reported by the normal
    validator, not this guard) — rather than :func:`load`, which
    would raise on the very collision this guard reports and swallow it.

    Parameters
    ----------
    mooring_config : str or pathlib.Path or Mapping
        A mooring YAML path, or an already-parsed mapping.

    Returns
    -------
    list of str
        One message per serial collision; empty when the serials are safe.
    """
    data = _parse(mooring_config)
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
