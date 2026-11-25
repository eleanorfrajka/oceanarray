"""Deprecated: moved to :mod:`oceanarray.mooring.location`; removed in 0.8.0.

Every name of the moved module still imports from here, with a DeprecationWarning
naming its new home.
"""

import warnings

from oceanarray.mooring import location as _new


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
