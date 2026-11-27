"""Validation of mooring YAMLs.

The ``instrument`` field names the subfolder of the raw and processed files
(``<dir>/<mooring>/<instrument>/``): ``microcat``, ``sbe56``, ``sbe16``, ``rbrsolo``,
``rbrduet``, ``aquadopp``, ``currentmeter``, ``adcp`` or ``tr1050`` (not model names
such as ``sbe37`` or ``nortek``).

Clock corrections (stage 2, before trimming to the deployment) are seconds **added**
to instrument time, positive when the instrument clock was slow: ``clock_offset`` at
deployment, ramping linearly to the correction at recovery, from
``clock_drift_seconds`` or, preferred, the pair ``computer_clock_at_recovery`` /
``instrument_clock_at_recovery`` (computer − instrument). A pair read as ``unknown``
(or left empty) is a warning: that instrument's drift is not corrected. A recovery
correction far from those of the other instruments on the mooring is a warning too
(``parameters.CLOCK_DRIFT_OUTLIER_SECONDS``). The uncorrected time is kept as
``time_orig``. See :doc:`/yaml_configuration`.
"""

import re

import numpy as np

from oceanarray import paths
from oceanarray.config import parameters as params
from oceanarray.exceptions import ValidationIssue
from oceanarray.mooring.instruments import (
    clock_seconds,
    hab_for,
    is_missing_clock_reading,
    parse_clock_reading,
    resolve_clock_correction,
)

# ``is_missing_clock_reading`` is re-exported here (imported above): the clock
# vocabulary lives in ``oceanarray.mooring.instruments`` with the resolver, but callers still
# import it from validation.


def _recovery_correction(entry: dict) -> float | None:
    """Return the clock correction applied at recovery (s), or None if there is none.

    The same :func:`~oceanarray.mooring.instruments.resolve_clock_correction` stage 2 applies,
    so the outlier check compares the values the pipeline actually uses.
    """
    correction = resolve_clock_correction(entry)
    if correction.source in ("recovery readings", "clock_drift_seconds"):
        return correction.seconds
    return None


def _drift_outliers(entries: list) -> list["ValidationIssue"]:
    """Return a WARNING per instrument whose recovery correction is far from the others'."""
    found = [
        (e.get("serial", "?"), c)
        for e in entries
        if isinstance(e, dict) and (c := _recovery_correction(e)) is not None
    ]
    if len(found) < 3:
        return []
    out = []
    for k, (serial, value) in enumerate(found):
        others = float(np.median([c for j, (_, c) in enumerate(found) if j != k]))
        if abs(value - others) > params.CLOCK_DRIFT_OUTLIER_SECONDS:
            out.append(
                ValidationIssue(
                    "WARNING",
                    f"[serial {serial}] clock correction at recovery {value:+.0f} s is "
                    f"{abs(value - others):.0f} s from the median of the other "
                    f"instruments ({others:+.0f} s); check the recovery logsheet",
                )
            )
    return out


# Instrument-type validity is single-sourced from ``parameters.KNOWN_INSTRUMENT_TYPES``
# (derived from ``INSTRUMENT_FILE_TYPES``).  This keeps ``oceanarray list`` and
# ``oceanarray validate`` in agreement; add new instrument types there, not here.

KNOWN_ALIASES: dict[str, str] = {
    "sbe37": "microcat",
    "nortek": "aquadopp",
}

# Valid ``file_type:`` values are single-sourced from ``parameters.ALL_FILE_TYPES``
# (union of ``INSTRUMENT_FILE_TYPES`` readers and ``EXTRA_FILE_TYPES``).  Add new
# file types there, not here — this keeps ``validate`` and stage1 in agreement.
VALID_FILE_TYPES = params.ALL_FILE_TYPES

UNSUPPORTED_FILE_TYPES: dict[str, str] = {}

# Only what cannot be processed without is required; the deployment window is
# recommended (Stage 2 can detect it from the records) — see RECOMMENDED_MOORING_KEYS.
REQUIRED_MOORING_KEYS = ["name", "waterdepth"]
RECOMMENDED_MOORING_KEYS = ["deployment_time", "recovery_time"]

# Safe serials reserved because a per-instrument report named for them would
# overwrite a mooring page: ``{mooring}_stack_report.html`` / ``_grid_report.html``.
# ``summary`` is NOT reserved — the summary page is ``{mooring}_report.html``, so a
# ``summary`` serial writes a distinct ``{mooring}_summary_report.html`` and collides
# with nothing (the nav current-key clash is already handled in ``reports/_navs.py``).
_RESERVED_SERIALS = frozenset({"stack", "grid"})


def check_depth_key(entry: dict) -> ValidationIssue | None:
    """Return the issue for an instrument entry's ``depth:`` key, which is not read.

    A WARNING when the entry also gives ``nominal_depth`` or a HAB, else an ERROR
    (the instrument then has no depth); None without ``depth:``.
    """
    if "depth" not in entry:
        return None
    level = (
        "WARNING"
        if entry.get("nominal_depth") is not None or hab_for(entry) is not None
        else "ERROR"
    )
    return ValidationIssue(
        level, "depth is not read; give nominal_depth (m, positive down) or hab"
    )


def check_serials(entries: list[dict]) -> list[ValidationIssue]:
    """Return ERRORs for serials whose output files would silently overwrite.

    Serials are compared on :func:`oceanarray.paths.safe_serial` — the exact token
    the pipeline uses to build filenames — so ``9920``, ``9920*`` and ``9920, R01``
    are the same instrument as far as files go (the trailing ``*`` diagram marker
    never makes two serials distinct).  Flags a missing or empty-after-stripping
    serial, a serial reserved for a report page (:data:`_RESERVED_SERIALS`), and two
    entries sharing a safe serial (their per-instrument report pages always collide;
    their stage NetCDF files also collide when the instrument types match).

    Parameters
    ----------
    entries : list of dict
        The mooring's instrument entries (``clamp`` + ``instruments`` + inline).

    Returns
    -------
    list of ValidationIssue
        One ERROR per problem; empty when every serial is unique and filename-safe.
    """
    issues: list[ValidationIssue] = []
    by_safe: dict[str, list[tuple[str, str]]] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        raw = entry.get("serial")
        label = f"[serial {raw}]" if raw is not None else f"[instrument index {i}]"
        safe = paths.safe_serial(raw) if raw is not None else ""
        if not safe:
            why = (
                "missing" if raw is None else f"{raw!r}, which is empty after stripping"
            )
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"{label} serial is {why} — every instrument needs a unique, "
                    f"filename-safe serial.",
                )
            )
            continue
        if safe.lower() in _RESERVED_SERIALS:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"{label} serial {safe!r} is reserved — it would overwrite the "
                    f"mooring's {{mooring}}_{safe}_report.html page. Rename it.",
                )
            )
        # Key on the lower-cased serial: output filenames collide on a
        # case-insensitive filesystem (macOS, Windows), so `Alpha` and `alpha`
        # are a collision there even though they are distinct strings.
        by_safe.setdefault(safe.lower(), []).append(
            (label, str(entry.get("instrument") or "unknown").lower())
        )
    for safe, group in by_safe.items():
        if len(group) > 1:
            labels = ", ".join(lbl for lbl, _ in group)
            types = [t for _, t in group]
            stage_too = len(set(types)) < len(types)
            what = (
                "stage NetCDF files AND report pages"
                if stage_too
                else "per-instrument report pages"
            )
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"Serial {safe!r} is used by {len(group)} instruments ({labels}) — "
                    f"their {what} would collide and silently overwrite. Serials must be "
                    f"unique within a mooring (the `*` marker is ignored for this check).",
                )
            )
    return issues


def validate_mooring(
    data: object, *, dir_name: str | None = None
) -> list[ValidationIssue]:
    """Validate a parsed mooring configuration mapping.

    Checks:

    - Required top-level keys are present
    - The ``name`` matches *dir_name* when both are given (output filenames use
      the directory name, so a mismatch loads zero files on a case-sensitive
      filesystem)
    - Each instrument entry uses a valid ``instrument`` name (not a model alias)
    - Each instrument entry with a ``file_type`` uses a recognised value
    - Instruments with ``filename`` also have ``file_type``
    - Instruments without ``filename`` are flagged as warnings (not yet staged)
    - Stacked instruments with a ``filename`` (not ``skip``, not ``stack: false``)
      and no ``hab`` are flagged as warnings (the stack omits them)
    - A ``depth`` key, which is not read, is a warning, or an error without
      ``nominal_depth`` or ``hab``

    Parameters
    ----------
    data : object
        The parsed YAML root; an ERROR is returned if it is not a mapping.
    dir_name : str, optional
        The name of the directory the config was read from. When given, the
        ``name`` field is compared against it (case-sensitively).

    Returns
    -------
    list of ValidationIssue
        One issue per finding; an empty list means the config passed all checks.
    """
    issues: list[ValidationIssue] = []

    if not isinstance(data, dict):
        return [ValidationIssue("ERROR", "YAML root must be a mapping")]

    # Check required mooring-level keys. Only what cannot be processed without is
    # required (``name`` builds every filename; ``waterdepth`` is in no record).
    for key in REQUIRED_MOORING_KEYS:
        if key not in data:
            issues.append(ValidationIssue("ERROR", f"Missing required key: '{key}'"))

    # The deployment window is recommended, not required: Stage 2 can detect each
    # instrument's in-water interval from the records and write the union, so an
    # absent declared window is a WARNING (the detected window is recorded with
    # ``deployment_window_source``), never an ERROR.
    for key in RECOMMENDED_MOORING_KEYS:
        if key not in data:
            issues.append(
                ValidationIssue(
                    "WARNING",
                    f"No '{key}' — Stage 2 will use the window detected from the "
                    f"records (recorded as deployment_window_source='detected').",
                )
            )

    # A raw-data root in the mooring YAML is a run-time fact, not part of the
    # mooring; it belongs to raw_dir / --raw-dir. Deprecated: the stage-1
    # absolute-path override is removed in 0.7.0.
    if "directory" in data:
        issues.append(
            ValidationIssue(
                "WARNING",
                "directory is ignored; pass the raw-data root as raw_dir "
                "(--raw-dir), not the mooring YAML. Deprecated — the override is "
                "removed in 0.7.0.",
            )
        )

    # The directory name and the ``name:`` field should match.  They name output
    # files on two code paths that are mid-migration: the processing stages still
    # name from the directory, while read_mooring / write_mooring name from
    # ``name:``; from 0.6.0 both use ``name:``.  A mismatch (most often a copied
    # mooring folder left unrenamed) therefore writes outputs under different
    # moorings depending on the path, so warn until they are identical.
    yaml_name = data.get("name")
    if isinstance(yaml_name, str) and dir_name is not None and yaml_name != dir_name:
        issues.append(
            ValidationIssue(
                "WARNING",
                f"directory '{dir_name}' differs from 'name: {yaml_name}' "
                f"(compared case-sensitively). The processing stages name outputs "
                f"from the directory and read_mooring/write_mooring from 'name:' "
                f"(from 0.6.0 both use 'name:'); make the directory and 'name:' "
                f"identical so files land under the same mooring on either path.",
            )
        )

    # Validate instrument list (supports both 'clamp' and legacy 'instruments').
    # Also validate inline hardware entries that carry instrument data.
    instrument_list = data.get("clamp", data.get("instruments", []))
    inline_list = data.get("inline", [])
    inline_instruments = [
        e
        for e in inline_list
        if isinstance(e, dict)
        and "instrument" in e
        and (e.get("filename") or e.get("skip"))
    ]
    combined_list = list(instrument_list) + inline_instruments

    if not combined_list:
        issues.append(
            ValidationIssue(
                "WARNING",
                "No instruments found under 'clamp', 'instruments', or 'inline'",
            )
        )
        return issues

    for i, entry in enumerate(combined_list):
        if not isinstance(entry, dict):
            continue

        serial = entry.get("serial", f"index {i}")
        prefix = f"[serial {serial}]"

        instrument = entry.get("instrument")
        file_type = entry.get("file_type")
        filename = entry.get("filename")

        serial_str = str(serial)
        illegal = set(re.findall(r"[^\w\-]", serial_str))
        if illegal == {"*"}:
            issues.append(
                ValidationIssue(
                    "WARNING",
                    f"{prefix} serial='{serial_str}' carries a `*` marker — it is kept in the "
                    f"mooring diagram and stripped from filenames and nav labels (as today).",
                )
            )
        elif illegal:
            issues.append(
                ValidationIssue(
                    "WARNING",
                    f"{prefix} serial='{serial_str}' contains characters illegal in filenames "
                    f"(e.g. '*') — they will be stripped automatically when constructing output filenames",
                )
            )

        # Fragile serial parsing for inline instruments with compound serials.
        # extract_inline_instruments splits on the first comma and uses the first
        # token as the instrument serial.  Warn so operators can verify the ordering.
        if (
            entry.get("source") == "inline" or entry in inline_instruments
        ) and "," in serial_str:
            parts = [p.strip() for p in serial_str.split(",")]
            issues.append(
                ValidationIssue(
                    "WARNING",
                    f"{prefix} inline serial='{serial_str}' contains a comma — "
                    f"the first token '{parts[0]}' will be used as the instrument serial "
                    f"and '{', '.join(parts[1:])}' stored as beacon_id.  "
                    f"Confirm the instrument serial comes first.",
                )
            )

        if instrument is None:
            issues.append(
                ValidationIssue("WARNING", f"{prefix} Missing 'instrument' field")
            )
        elif instrument in KNOWN_ALIASES:
            correct = KNOWN_ALIASES[instrument]
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"{prefix} instrument='{instrument}' is deprecated — use '{correct}' instead",
                )
            )
        elif not params.is_known_instrument_type(instrument):
            issues.append(
                ValidationIssue(
                    "WARNING",
                    f"{prefix} instrument='{instrument}' is not in the known list: "
                    f"{', '.join(sorted(params.KNOWN_INSTRUMENT_TYPES))}",
                )
            )

        if (
            file_type is not None
            and file_type not in VALID_FILE_TYPES
            and file_type != "TBD"
        ):
            if file_type in params.DEPRECATED_FILE_TYPE_ALIASES:
                canonical = params.DEPRECATED_FILE_TYPE_ALIASES[file_type]
                issues.append(
                    ValidationIssue(
                        "WARNING",
                        f"{prefix} file_type='{file_type}' is deprecated — use "
                        f"'{canonical}' instead (it is remapped automatically for now)",
                    )
                )
            elif file_type in UNSUPPORTED_FILE_TYPES:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        f"{prefix} file_type='{file_type}' is not supported: {UNSUPPORTED_FILE_TYPES[file_type]}",
                    )
                )
            else:
                issues.append(
                    ValidationIssue(
                        "WARNING",
                        f"{prefix} file_type='{file_type}' is not recognised. "
                        f"Known types: {', '.join(sorted(VALID_FILE_TYPES))}",
                    )
                )

        if filename and not file_type:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"{prefix} has 'filename' but no 'file_type'",
                )
            )

        if not filename and not entry.get("skip"):
            issues.append(
                ValidationIssue(
                    "WARNING",
                    f"{prefix} instrument='{instrument}' has no 'filename' — not yet staged for processing",
                )
            )

        if "stack" in entry and not isinstance(entry["stack"], bool):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"{prefix} stack={entry['stack']!r} must be true or false",
                )
            )

        if filename and any(c in str(filename) for c in "*?["):
            ok = (
                str(instrument).lower() == "adcp"
                and entry.get("file_type") == "rodb"
                and re.fullmatch(r"[^*?\[]*_bin\*\.edt", str(filename))
            )
            if not ok:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        f"{prefix} filename {filename!r}: a wildcard is only for an "
                        "ADCP's RODB per-bin files, as <name>_bin*.edt",
                    )
                )

        if (
            str(instrument).lower() == "adcp"
            and not entry.get("skip")
            and str(entry.get("orientation", "")).lower() not in ("up", "down")
        ):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"{prefix} instrument='{instrument}' needs orientation: up or down "
                    f"(got {entry.get('orientation')!r}); it is never assumed",
                )
            )

        # A stacked entry needs a height above bottom to be placed as a level. hab is
        # filled from hab_bottom (down) / hab_top (up) by orientation; if that resolves
        # to nothing the stack omits the instrument. An entry with no filename is already
        # flagged above as not yet staged.
        if (
            filename
            and not entry.get("skip")
            and entry.get("stack", True) is not False
            and hab_for(entry) is None
        ):
            orientation = str(entry.get("orientation", "") or "").lower()
            if orientation:
                needed = "hab_top" if orientation in ("up", "upward") else "hab_bottom"
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        f"{prefix} instrument='{instrument}' is '{orientation}'-looking "
                        f"but has no '{needed}' (nor 'hab') — the stack cannot place it.",
                    )
                )
            else:
                issues.append(
                    ValidationIssue(
                        "WARNING",
                        f"{prefix} instrument='{instrument}' has no 'hab' — the stack "
                        f"will omit it; set hab, or stack: false",
                    )
                )

        depth_issue = check_depth_key(entry)
        if depth_issue is not None:
            issues.append(
                depth_issue._replace(message=f"{prefix} {depth_issue.message}")
            )

        # Clock correction field checks
        has_comp = "computer_clock_at_recovery" in entry
        has_inst = "instrument_clock_at_recovery" in entry
        if has_comp != has_inst:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"{prefix} computer_clock_at_recovery and instrument_clock_at_recovery "
                    f"must both be present or both absent",
                )
            )
        elif (
            has_comp
            and has_inst
            and any(
                is_missing_clock_reading(entry[k])
                for k in ("computer_clock_at_recovery", "instrument_clock_at_recovery")
            )
        ):
            fallback = (
                "; clock_drift_seconds is used instead"
                if entry.get("clock_drift_seconds") not in (None, "")
                else ": drift not corrected"
            )
            issues.append(
                ValidationIssue(
                    "WARNING",
                    f"{prefix} no clock reading at recovery "
                    f"(computer {entry['computer_clock_at_recovery']!r}, instrument "
                    f"{entry['instrument_clock_at_recovery']!r}){fallback}",
                )
            )
        elif has_comp and has_inst:
            comp_t = parse_clock_reading(entry["computer_clock_at_recovery"])
            inst_t = parse_clock_reading(entry["instrument_clock_at_recovery"])
            for ts_key, parsed in (
                ("computer_clock_at_recovery", comp_t),
                ("instrument_clock_at_recovery", inst_t),
            ):
                if parsed is None:
                    issues.append(
                        ValidationIssue(
                            "ERROR",
                            f"{prefix} {ts_key}='{entry[ts_key]}' is not a valid timestamp",
                        )
                    )
            if (
                "clock_drift_seconds" in entry
                and comp_t is not None
                and inst_t is not None
            ):
                try:
                    stated_drift = float(entry["clock_drift_seconds"])
                except (TypeError, ValueError):
                    stated_drift = None
                if stated_drift is not None:
                    # The same correction stage 2 applies (time-only readings normalised
                    # modulo 24 h), so a pair straddling midnight is not a false conflict.
                    computed_drift = resolve_clock_correction(
                        {
                            k: entry[k]
                            for k in (
                                "computer_clock_at_recovery",
                                "instrument_clock_at_recovery",
                            )
                        }
                    ).seconds
                    if abs(computed_drift - stated_drift) > 1.0:
                        issues.append(
                            ValidationIssue(
                                "ERROR",
                                f"{prefix} clock_drift_seconds={stated_drift:.1f}s conflicts with "
                                f"timestamp pair (computed drift={computed_drift:+.1f}s); "
                                f"remove clock_drift_seconds or correct the timestamps",
                            )
                        )
                    else:
                        issues.append(
                            ValidationIssue(
                                "WARNING",
                                f"{prefix} both clock_drift_seconds and timestamp pair are present; "
                                f"timestamp pair will be used (they agree to <1 s)",
                            )
                        )

        # clock_offset and clock_drift_seconds must be a finite number of seconds.
        # clock_seconds raises MooringError (a ValueError) naming the key and serial;
        # config cannot import exceptions (same layer), so catch it as ValueError.
        for num_key in ("clock_offset", "clock_drift_seconds"):
            try:
                clock_seconds(entry, num_key)
            except ValueError as exc:
                issues.append(ValidationIssue("ERROR", str(exc)))

    issues.extend(_drift_outliers(combined_list))
    issues.extend(check_serials(combined_list))
    return issues
