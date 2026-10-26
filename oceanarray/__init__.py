"""Oceanarray: Tools for processing oceanographic mooring data.

This package provides functions for processing, quality control, and analysis
of oceanographic time series data from moorings and instruments.
"""

try:
    # Written by setuptools-scm at build time (gitignored); the live version.
    from oceanarray._version import __version__
except ImportError:  # pragma: no cover - source tree with no build artefact
    try:
        from importlib.metadata import PackageNotFoundError as _PNF
        from importlib.metadata import version as _v

        __version__ = _v("oceanarray")
    except _PNF:  # pragma: no cover
        __version__ = "0.0.0"

from oceanarray.config import parameters  # noqa: I001  — binds ``parameters`` before modules that import it
from oceanarray import mooring, writers
from oceanarray.exceptions import MooringError
from oceanarray.pipeline import process, run

# Note: not every name in ``__all__`` resolves after a bare ``import oceanarray``.
# ``config``, ``pipeline``, ``parameters``, ``process``, ``run``, ``mooring`` and ``writers``
# are imported here (directly or as a side effect); ``analysis``, ``tools``,
# ``plotters`` and ``utilities`` are listed for discoverability but load only on
# first explicit import.
__all__ = [
    "__version__",
    # Subpackages (canonical locations)
    "analysis",
    "config",
    "pipeline",
    "tools",
    "writers",
    # Top-level modules
    "mooring",
    "parameters",
    "plotters",
    "utilities",
    # Public API
    "MooringError",
    "process",
    "run",
]
