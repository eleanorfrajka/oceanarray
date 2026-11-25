"""The mooring YAML (``*.mooring.yaml``) and what it describes.

:mod:`~oceanarray.mooring.yaml_file` loads, checks, templates and saves the file;
:mod:`~oceanarray.mooring.validation` holds the checks on its content,
:mod:`~oceanarray.mooring.blocks` those of its ``people:`` and ``source:`` blocks;
:mod:`~oceanarray.mooring.instruments` reads its instrument entries,
:mod:`~oceanarray.mooring.location` its positions, and
:mod:`~oceanarray.mooring.catalog` the sensor catalog it names. The names below are
used as ``oceanarray.mooring.<name>``.
"""

from oceanarray.mooring.yaml_file import (
    DEPLOYMENT_CODE_PATTERN,
    KNOWN_KEYS,
    OPTIONAL_REPORT_SECTIONS,
    SETTINGS_BLOCKS,
    SOURCE_FIELDS,
    check,
    load,
    resolve_report_sections,
    resolve_spectrum_markers,
    save,
    serial_collision_errors,
    settings_from_yaml,
    settings_yaml,
    template,
)

__all__ = [
    "DEPLOYMENT_CODE_PATTERN",
    "KNOWN_KEYS",
    "OPTIONAL_REPORT_SECTIONS",
    "SETTINGS_BLOCKS",
    "SOURCE_FIELDS",
    "check",
    "load",
    "resolve_report_sections",
    "resolve_spectrum_markers",
    "save",
    "serial_collision_errors",
    "settings_from_yaml",
    "settings_yaml",
    "template",
]
