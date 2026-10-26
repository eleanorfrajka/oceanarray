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
:func:`oceanarray.utilities.instrument_entries` and
:func:`oceanarray.utilities.parse_latlon_with_source`.

The file shape is a cross-package contract (moordiag reads the same file), so
``load`` keeps every key exactly as written and never rewrites a value.
"""

import logging
import math
from collections.abc import Iterable, Mapping
from os import PathLike
from pathlib import Path

import yaml

from oceanarray.config import validation
from oceanarray.config.validation import ValidationIssue
from oceanarray.exceptions import MooringError

logger = logging.getLogger(__name__)

# Keys the fixtures, ``cmd_init`` and real UHH configs are known to use. The
# list is deliberately generous: ``cmd_init`` writes ``deployment_cruise`` /
# ``recovery_ship`` etc. while older configs use ``cruise`` / ``ship`` /
# ``status``, and both are valid. An unknown top-level key is a WARNING (not an
# ERROR) for now so a real config carrying a key the fixtures never showed is
# not rejected; promotion to ERROR is planned for a later release, once this list
# has been checked against the configs on the drive (``scripts/survey_mooring_keys.py``).
KNOWN_KEYS: frozenset[str] = frozenset(
    {
        # identity / header
        "name",
        "cleaname",
        "year",
        "waterdepth",
        "status",
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
        # QC thresholds (read by the QC pass).
        "qc_ranges",
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
    }
)

#: The keys each optional settings block may hold, and whether the value must be
#: an integer.
_SETTINGS_KEYS: dict[str, dict[str, bool]] = {
    "stack": {"dt_seconds": True},
    "grid": {"p_start": False, "p_end": False, "dp": False},
}

REQUIRED_KEYS: list[str] = validation.REQUIRED_MOORING_KEYS


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
            p0, p1, step = (value.get(k) for k in ("p_start", "p_end", "dp"))
            if step == 0:
                issues.append(ValidationIssue("ERROR", "'grid: dp' must be > 0."))
            if isinstance(p0, int | float) and isinstance(p1, int | float) and p1 <= p0:
                issues.append(
                    ValidationIssue(
                        "ERROR", f"'grid: p_end' ({p1}) must be above p_start ({p0})."
                    )
                )
    return issues


# The one source for the commented ``init`` template: key -> inline description.
# ``save`` writes these as end-of-line comments; keep in sync with KNOWN_KEYS.
_FIELD_COMMENTS: dict[str, str] = {
    "name": "mooring identifier (matches directory and filename)",
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
        "year",
        "waterdepth",
        "deployment_cruise",
        "deployment_ship",
        "recovery_cruise",
        "recovery_ship",
        "latitude",
        "longitude",
        "deployment_time",
        "recovery_time",
    ):
        doc[key] = None
    examples = _TEMPLATE_INSTRUMENTS if instruments is None else instruments
    doc["clamp"] = [dict(entry) for entry in examples]
    return doc


def save(mooring: Mapping, path: str | PathLike, *, overwrite: bool = False) -> Path:
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

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = YAML()
    writer.default_flow_style = False
    writer.width = 120
    # newline="\n": deterministic LF on every platform — Windows text mode would
    # otherwise write CRLF and break the byte-identical init-template golden.
    with path.open("w", newline="\n") as fh:
        writer.dump(doc, fh)
    return path
