"""Old public names kept for one release, in one table; each warns when used.

Every entry in :data:`RENAMED` still works in 0.6 and is removed in
:data:`REMOVED_IN`. The migration page lists every entry (a test checks it).
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import warnings
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

#: The release that removes every name in :data:`RENAMED`.
REMOVED_IN = "0.7.0"

#: Old dotted name -> what to use instead.
RENAMED: dict[str, str] = {
    "oceanarray.plotters.timeseries.draw_grid": (
        "xarray's DataArray.plot.pcolormesh (or .plot.contourf) on the grid variable"
    ),
    "oceanarray.parameters": "oceanarray.config.parameters",
    "oceanarray.config.validation.validate_mooring_yaml": "oceanarray.mooring.check",
    "oceanarray.pipeline.stage1.MooringProcessor": (
        "oceanarray.process(yaml_path, stages=1), or oceanarray.stage1 on datasets"
    ),
    "oceanarray.pipeline.stage2.Stage2Processor": (
        "oceanarray.process(yaml_path, stages=2), or oceanarray.stage2 on datasets"
    ),
    "oceanarray.pipeline.stage3.Stage3Processor": (
        "oceanarray.process(yaml_path, stages=3), or oceanarray.stage3 on datasets"
    ),
    "oceanarray.pipeline.stack.MooringStacker": (
        "oceanarray.run(yaml_path, stages=3), or oceanarray.stack on datasets"
    ),
    "oceanarray.pipeline.grid.MooringGridder": (
        "oceanarray.run(yaml_path, stages=3), or oceanarray.grid on a stack"
    ),
    "oceanarray.inspect.vars": "oceanarray.inspect.variables",
    "oceanarray.inspect.attrs": "oceanarray.inspect.attributes",
    "oceanarray.legacy.process_rodb.normalize_by_middle_percent": (
        "oceanarray.legacy.process_rodb.to_zscore"
    ),
    "oceanarray.legacy.process_rodb.normalize_dataset_by_middle_percent": (
        "oceanarray.legacy.process_rodb.to_zscore_dataset"
    ),
}

_F = TypeVar("_F", bound=Callable[..., Any])

#: True while oceanarray itself calls a deprecated name on the user's behalf.
_internal: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "oceanarray_internal_call", default=False
)


def message(old: str) -> str:
    """Return the warning text for the deprecated name *old*."""
    return f"{old} is deprecated and is removed in {REMOVED_IN}; use {RENAMED[old]}."


def warn(old: str, *, stacklevel: int = 3) -> None:
    """Warn that *old* is deprecated, unless oceanarray is calling it itself.

    Parameters
    ----------
    old : str
        A key of :data:`RENAMED`.
    stacklevel : int, optional
        Passed to :func:`warnings.warn`; the default points at the caller of the
        function that calls this.
    """
    if not _internal.get():
        warnings.warn(message(old), DeprecationWarning, stacklevel=stacklevel)


def alias(old: str, func: _F) -> _F:
    """Return a function named after *old* that warns, then calls *func*.

    Parameters
    ----------
    old : str
        A key of :data:`RENAMED`, the full dotted old name.
    func : callable
        The function that replaces it.

    Returns
    -------
    callable
        The deprecated alias.
    """

    @functools.wraps(func)
    def _alias(*args: Any, **kwargs: Any) -> Any:
        warn(old)
        return func(*args, **kwargs)

    _alias.__name__ = _alias.__qualname__ = old.rsplit(".", 1)[-1]
    _alias.__doc__ = (
        f"Deprecated alias of :func:`{func.__module__}.{func.__name__}`; "
        f"removed in {REMOVED_IN}."
    )
    return _alias  # type: ignore[return-value]


@contextlib.contextmanager
def internal() -> Iterator[None]:
    """Silence deprecation warnings for names oceanarray itself still calls inside the block."""
    token = _internal.set(True)
    try:
        yield
    finally:
        _internal.reset(token)
