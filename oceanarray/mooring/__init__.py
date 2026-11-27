"""The mooring YAML (``*.mooring.yaml``) and what it describes.

:mod:`~oceanarray.mooring.load_yaml` loads the file, :mod:`~oceanarray.mooring.check_yaml`
checks it, :mod:`~oceanarray.mooring.write_yaml` templates and saves it;
:mod:`~oceanarray.mooring.yaml_keys` lists its keys and
:mod:`~oceanarray.mooring.settings` reads its settings.
:mod:`~oceanarray.mooring.validation` holds the checks on its content,
:mod:`~oceanarray.mooring.blocks` those of its ``people:`` and ``source:`` blocks;
:mod:`~oceanarray.mooring.instruments` reads its instrument entries,
:mod:`~oceanarray.mooring.location` its positions, and
:mod:`~oceanarray.mooring.catalog` the sensor catalog it names. The names below are
used as ``oceanarray.mooring.<name>``.
"""

from oceanarray.mooring.check_yaml import check, serial_collision_errors
from oceanarray.mooring.load_yaml import folder_yaml_path, load
from oceanarray.mooring.settings import (
    OPTIONAL_REPORT_SECTIONS,
    SETTINGS_BLOCKS,
    resolve_report_sections,
    resolve_spectrum_markers,
    settings_from_yaml,
    settings_yaml,
)
from oceanarray.mooring.write_yaml import save, template
from oceanarray.mooring.yaml_keys import (
    DEPLOYMENT_CODE_PATTERN,
    KNOWN_KEYS,
    SOURCE_FIELDS,
)

__all__ = [
    "DEPLOYMENT_CODE_PATTERN",
    "KNOWN_KEYS",
    "OPTIONAL_REPORT_SECTIONS",
    "SETTINGS_BLOCKS",
    "SOURCE_FIELDS",
    "check",
    "folder_yaml_path",
    "load",
    "resolve_report_sections",
    "resolve_spectrum_markers",
    "save",
    "serial_collision_errors",
    "settings_from_yaml",
    "settings_yaml",
    "template",
]
