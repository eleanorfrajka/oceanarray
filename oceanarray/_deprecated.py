"""Old public names kept for one release, in one table per removal release; each warns when used.

Every entry in :data:`RENAMED` still works in 0.6 and is removed in
:data:`REMOVED_IN`; every entry in :data:`RENAMED_NEXT` was deprecated during 0.7
and is removed in :data:`REMOVED_IN_NEXT`. Each table's migration page lists every
entry (a test checks it).
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

#: The release that removes every name in :data:`RENAMED_NEXT`, and every module
#: moved during 0.7.
REMOVED_IN_NEXT = "0.8.0"

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

#: Old dotted name -> what to use instead, for names deprecated during 0.7. A
#: renamed keyword is written ``<function>(<old keyword>=)``.
RENAMED_NEXT: dict[str, str] = {
    "oceanarray.mooring.resolve_report_sections(description=)": (
        "the mooring_dict= keyword"
    ),
    "oceanarray.mooring.resolve_spectrum_markers(description=)": (
        "the mooring_dict= keyword"
    ),
    "oceanarray.mooring.serial_collision_errors(mooring_config=)": (
        "the mooring= keyword"
    ),
    "oceanarray.mooring.settings_yaml(cfg=)": "the mooring_dict= keyword",
    "oceanarray.mooring.location.parse_latlon(cfg=)": "the mooring_dict= keyword",
    "oceanarray.mooring.location.parse_latlon_with_source(cfg=)": (
        "the mooring_dict= keyword"
    ),
    "oceanarray.pipeline.concat.concat(descriptions=)": "the mooring_dicts= keyword",
    "oceanarray.pipeline.concat.concat_moorings(descriptions=)": (
        "the yaml_paths= keyword"
    ),
    "oceanarray.pipeline.qc.load_qc_config(mooring_cfg=)": "the mooring_dict= keyword",
    "oceanarray.reports.MooringReport.generate(cfg=)": "the mooring_dict= keyword",
    "oceanarray.pipeline.read_mooring.header_attrs(cfg=)": "the mooring_dict= keyword",
    "oceanarray.pipeline.stack.mooring_attrs(mooring_config=)": (
        "the mooring_dict= keyword"
    ),
}

_F = TypeVar("_F", bound=Callable[..., Any])
_T = TypeVar("_T")

#: True while oceanarray itself calls a deprecated name on the user's behalf.
_internal: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "oceanarray_internal_call", default=False
)


def message(old: str) -> str:
    """Return the warning text for the deprecated name *old*."""
    if old in RENAMED_NEXT:
        return (
            f"{old} is deprecated and is removed in {REMOVED_IN_NEXT}; "
            f"use {RENAMED_NEXT[old]}."
        )
    return f"{old} is deprecated and is removed in {REMOVED_IN}; use {RENAMED[old]}."


def warn(old: str, *, stacklevel: int = 3) -> None:
    """Warn that *old* is deprecated, unless oceanarray is calling it itself.

    Parameters
    ----------
    old : str
        A key of :data:`RENAMED` or :data:`RENAMED_NEXT`.
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


def resolve_keyword(
    old: str, value: _T | None, old_value: _T | None, *, required: str | None = None
) -> _T | None:
    """Return the argument given under its new keyword or under its old one, warning for the old.

    Parameters
    ----------
    old : str
        A key of :data:`RENAMED_NEXT`, ``<function>(<old keyword>=)``.
    value : object or None
        The argument given under the new name (positionally or by keyword).
    old_value : object or None
        The argument given under the old keyword.
    required : str, optional
        The new parameter's name, when the call must give one of the two.

    Returns
    -------
    object or None
        The argument given; None when neither was given and none is required.

    Raises
    ------
    TypeError
        Both were given, or neither while *required*.
    """
    function = old.partition("(")[0].rsplit(".", 1)[-1]
    keyword = old.partition("(")[2].rstrip("=)")
    if old_value is None:
        if value is None and required is not None:
            raise TypeError(f"{function}() missing required argument: {required!r}")
        return value
    if value is not None:
        raise TypeError(f"{function}() got both {keyword}= and {RENAMED_NEXT[old]}")
    # Points at the caller of the function whose keyword was renamed.
    warn(old, stacklevel=4)
    return old_value


@contextlib.contextmanager
def internal() -> Iterator[None]:
    """Silence deprecation warnings for names oceanarray itself still calls inside the block."""
    token = _internal.set(True)
    try:
        yield
    finally:
        _internal.reset(token)
