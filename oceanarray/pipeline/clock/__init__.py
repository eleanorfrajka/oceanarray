"""Deprecated: the clock modules moved to :mod:`oceanarray.analysis.clock`.

Importing this package warns once and maps ``oceanarray.pipeline.clock.offsets`` and
``oceanarray.pipeline.clock.deployment`` to the moved modules, so existing imports
keep working until the removal release.
"""

import sys
import warnings

from oceanarray.analysis.clock import deployment, offsets

warnings.warn(
    "oceanarray.pipeline.clock is deprecated and is removed in 0.8.0; "
    "use oceanarray.analysis.clock.",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[f"{__name__}.deployment"] = deployment
sys.modules[f"{__name__}.offsets"] = offsets

__all__ = ["deployment", "offsets"]
