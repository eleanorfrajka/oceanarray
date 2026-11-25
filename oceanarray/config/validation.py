"""Deprecated: moved to :mod:`oceanarray.mooring.validation`; removed in 0.8.0.

Every name of the moved module still imports from here, with a DeprecationWarning
naming its new home.
"""

import warnings

from oceanarray import _deprecated
from oceanarray.exceptions import ValidationIssue as _ValidationIssue
from oceanarray.mooring import validation as _new
from oceanarray.mooring import yaml_file as _mooring


def __getattr__(name: str) -> object:
    """Return *name* from the moved module, warning that this path is deprecated."""
    if not hasattr(_new, name):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    warnings.warn(
        f"{__name__}.{name} is deprecated and is removed in 0.8.0; "
        f"use {_new.__name__}.{name}.",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(_new, name)


def validate_mooring_yaml(yaml_path: str) -> list[_ValidationIssue]:
    """Validate a mooring YAML configuration file by path.

    .. deprecated::
        Use :func:`oceanarray.mooring.check`, which reads the file once and adds
        the unknown-key check. This wrapper delegates to it and is removed in
        0.7.0.

    Parameters
    ----------
    yaml_path : str
        Path to the mooring YAML.

    Returns
    -------
    list of ValidationIssue
        One issue per finding; an empty list means the file passed all checks.
    """
    _deprecated.warn("oceanarray.config.validation.validate_mooring_yaml")
    return _mooring.check(yaml_path)
