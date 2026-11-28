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
        "oceanarray.process(mooring_path, stages=1), or oceanarray.stage1 on datasets"
    ),
    "oceanarray.pipeline.stage2.Stage2Processor": (
        "oceanarray.process(mooring_path, stages=2), or oceanarray.stage2 on datasets"
    ),
    "oceanarray.pipeline.stage3.Stage3Processor": (
        "oceanarray.process(mooring_path, stages=3), or oceanarray.stage3 on datasets"
    ),
    "oceanarray.pipeline.stack.MooringStacker": (
        "oceanarray.run(mooring_path, stages=3), or oceanarray.stack on datasets"
    ),
    "oceanarray.pipeline.grid.MooringGridder": (
        "oceanarray.run(mooring_path, stages=3), or oceanarray.grid on a stack"
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

#: The deprecation key for a mooring folder given where a verb takes its mooring_path.
FOLDER_GIVEN = "a mooring folder in place of a verb's mooring_path"

#: The deprecation key for a verb called without a Paths.
NO_PATHS = "a verb called without paths"

#: The deprecation key for a loaded mooring YAML given where its path is taken.
DICT_GIVEN = "a mooring dict in place of a mooring_path"

#: Old dotted name -> what to use instead, for names deprecated during 0.7. A
#: renamed keyword is written ``<function>(<old keyword>=)``; a file location or
#: a call form is described in words.
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
        "the mooring_paths= keyword"
    ),
    "oceanarray.pipeline.qc.load_qc_config(mooring_cfg=)": "the mooring_dict= keyword",
    "oceanarray.reports.MooringReport.generate(cfg=)": "the mooring_dict= keyword",
    "oceanarray.reports.MooringReport.generate(yaml_path=)": (
        "the mooring_path= keyword"
    ),
    "oceanarray.plotters.helpers.masked": "oceanarray.pipeline.masking.masked",
    "oceanarray.plotters.helpers.ruled": "oceanarray.mask_rules.STACK_ATTR in ds.attrs",
    "oceanarray.pipeline.read_mooring.header_attrs(cfg=)": "the mooring_dict= keyword",
    "oceanarray.pipeline.stack.mooring_attrs(mooring_config=)": (
        "the mooring_dict= keyword"
    ),
    "oceanarray.stage1(mooring=)": "the mooring_dict= keyword",
    "oceanarray.stage2(mooring=)": "the mooring_dict= keyword",
    "oceanarray.stage3(mooring=)": "the mooring_dict= keyword",
    "oceanarray.stack(mooring=)": "the mooring_dict= keyword",
    "oceanarray.pipeline.stack.stack_stale_reason(mooring=)": (
        "the mooring_dict= keyword"
    ),
    "oceanarray.pipeline.stack.people_source_sha256(mooring=)": (
        "the mooring_dict= keyword"
    ),
    "oceanarray.mask_rules.stack_rule(mooring=)": "the mooring_dict= keyword",
    "oceanarray.mooring.catalog.lookup(mooring=)": "the mooring_dict= keyword",
    "oceanarray.mooring.catalog.digest(mooring=)": "the mooring_dict= keyword",
    "oceanarray.mooring.instruments.instrument_entries(mooring=)": (
        "the mooring_dict= keyword"
    ),
    "oceanarray.mooring.save(mooring=)": "the mooring_dict= keyword",
    "oceanarray.pipeline.oceansites.to_oceansites(mooring=)": (
        "the mooring_dict= keyword"
    ),
    "oceanarray.pipeline.oceansites.to_oceansites(array=)": "the array_dict= keyword",
    "oceanarray.pipeline.oceansites.deployment_code(mooring=)": (
        "the mooring_dict= keyword"
    ),
    "oceanarray.pipeline.oceansites.platform_code(mooring=)": (
        "the mooring_dict= keyword"
    ),
    "oceanarray.pipeline.raw_files.resolve_raw_file(mooring=)": (
        "the mooring_dict= keyword"
    ),
    "oceanarray.pipeline.read_mooring.join_raw(mooring=)": "the mooring_dict= keyword",
    "a mooring YAML inside proc_dir/<mooring_name>/": (
        "<yaml_dir>/<mooring_name>.mooring.yaml (yaml_dir of paths.yaml or oceanarray.Paths)"
    ),
    NO_PATHS: (
        "an oceanarray.Paths as the second argument, e.g. oceanarray.run(mooring_path, paths)"
    ),
    FOLDER_GIVEN: "the folder's <mooring_name>.mooring.yaml as mooring_path",
    DICT_GIVEN: "the mooring YAML's path as mooring_path",
    "oceanarray.process(mooring=)": "the mooring_path= keyword",
    "oceanarray.process(mooring_dir=)": (
        "paths.with_mooring(mooring_name, proc_dir=...) in oceanarray.process(mooring_path, paths)"
    ),
    "oceanarray.process(raw_dir=)": (
        "oceanarray.Paths(raw_dir=...) in oceanarray.process(mooring_path, paths)"
    ),
    "oceanarray.process(caldip_dir=)": (
        "oceanarray.Paths(caldip_dir=...) in oceanarray.process(mooring_path, paths)"
    ),
    "oceanarray.run(mooring=)": "the mooring_path= keyword",
    "oceanarray.run(mooring_dir=)": (
        "paths.with_mooring(mooring_name, proc_dir=...) in oceanarray.run(mooring_path, paths)"
    ),
    "oceanarray.run(raw_dir=)": (
        "oceanarray.Paths(raw_dir=...) in oceanarray.run(mooring_path, paths)"
    ),
    "oceanarray.run(caldip_dir=)": (
        "oceanarray.Paths(caldip_dir=...) in oceanarray.run(mooring_path, paths)"
    ),
    "oceanarray.report(mooring=)": (
        "the mooring_path= keyword, or array_path= for kind='array'"
    ),
    "oceanarray.report(mooring_dir=)": (
        "paths.with_mooring(mooring_name, proc_dir=...) in oceanarray.report(mooring_path, paths)"
    ),
    "oceanarray.report(proc_dir=)": (
        "oceanarray.Paths(proc_dir=...) in oceanarray.report(array_path, paths, kind='array')"
    ),
    "oceanarray.report(raw_dir=)": (
        "oceanarray.Paths(raw_dir=...) in oceanarray.report(mooring_path, paths)"
    ),
    "oceanarray.report(out_dir=)": (
        "paths.with_mooring(mooring_name, report_dir=...) in oceanarray.report(mooring_path, paths)"
    ),
    "oceanarray.report(report_dir=)": (
        "oceanarray.Paths(report_dir=...) in oceanarray.report(mooring_path, paths)"
    ),
    "oceanarray.report(pdf_dir=)": (
        "oceanarray.report(mooring_path, paths, pdf=True), which writes the PDF beside the pages"
    ),
    "oceanarray.export(mooring=)": "the mooring_path= keyword",
    "oceanarray.export(mooring_dir=)": (
        "paths.with_mooring(mooring_name, proc_dir=...) in oceanarray.export(mooring_path, paths)"
    ),
    "oceanarray.export(out_dir=)": (
        "oceanarray.Paths(export_dir=...) in oceanarray.export(mooring_path, paths)"
    ),
    "oceanarray.export(array=)": (
        "array_path= (a path); for a parsed mapping, "
        "oceanarray.pipeline.oceansites.to_oceansites(array_dict=)"
    ),
    "oceanarray.mooring.load(mooring=)": "the mooring_path= keyword",
    "oceanarray.mooring.load(proc_dir=)": (
        "oceanarray.mooring.load(paths.mooring_path(mooring_name))"
    ),
    "oceanarray.mooring.check(mooring=)": "the mooring_path= keyword",
    "oceanarray.paths.mooring_proc_dir": (
        "oceanarray.Paths(proc_dir=...).mooring_dir(mooring_name)"
    ),
    "oceanarray.paths.raw_mooring_dir": (
        "oceanarray.Paths(raw_dir=...).raw_mooring_dir(mooring_name)"
    ),
    "oceanarray.paths.resolve_report_dir": (
        "oceanarray.Paths(report_dir=...).report_mooring_dir(mooring_name) "
        "(report_dir/<mooring_name>), or paths.with_mooring(mooring_name, "
        "report_dir=folder) for an exact folder such as <mooring_dir>/report"
    ),
    "oceanarray.paths.resolve_pdf_path": (
        "paths.report_mooring_dir(mooring_name), where oceanarray.report(mooring_path, "
        "paths, pdf=True) writes <mooring_name>_report.pdf"
    ),
    "oceanarray.paths.require_current_layout(proc_root=)": "the proc_dir= keyword",
    "oceanarray.paths.require_current_layout(mooring=)": ("the mooring_name= keyword"),
    "oceanarray.paths.stage_output_name(positional arguments)": (
        "keyword arguments: serial=, stage=, tag="
    ),
    "oceanarray.paths.stage_output_name(mooring=)": "the mooring_name= keyword",
    "oceanarray.paths.stage_file(positional arguments)": (
        "keyword arguments: mooring_name=, instrument=, serial=, stage="
    ),
    "oceanarray.paths.stage_file(name=)": "the mooring_name= keyword",
    "oceanarray.paths.product_file(positional arguments)": (
        "keyword arguments: mooring_name=, product="
    ),
    "oceanarray.paths.product_file(name=)": "the mooring_name= keyword",
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
        A key of :data:`RENAMED` or :data:`RENAMED_NEXT`, the full dotted old name.
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
        f"removed in {REMOVED_IN_NEXT if old in RENAMED_NEXT else REMOVED_IN}."
    )
    return _alias  # type: ignore[return-value]


def resolve_keyword(
    old: str,
    value: _T | None,
    old_value: _T | None,
    *,
    required: str | None = None,
    quiet: bool = False,
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
    quiet : bool, optional
        Do not warn for the old keyword: the caller warns once for the whole call.

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
    if not quiet:
        # Points at the caller of the function whose keyword was renamed.
        warn(old, stacklevel=4)
    return old_value


def keyword_only(
    old: str, order: tuple[str, ...], **renamed: str
) -> Callable[[_F], _F]:
    """Return a decorator that reads old calls of a function whose arguments after the first became keyword-only.

    Arguments after the first given by position are read in their old *order*, and
    an old keyword as its new one, with one warning: ``<old>(positional arguments)``
    or ``<old>(<old keyword>=)``, keys of :data:`RENAMED_NEXT`.

    Parameters
    ----------
    old : str
        The function's full dotted name.
    order : tuple of str
        The new names of the arguments after the first, in their old order.
    **renamed : str
        Old keyword -> new keyword.

    Returns
    -------
    callable
        The decorator.
    """

    def decorate(func: _F) -> _F:
        """Return *func* reading its old calls."""

        @functools.wraps(func)
        def _read_old(*args: Any, **kwargs: Any) -> Any:
            keys = [f"{old}(positional arguments)"] if len(args) > 1 else []
            if len(args) > len(order) + 1:
                raise TypeError(
                    f"{func.__name__}() takes at most {len(order) + 1} arguments"
                )
            moved = [*zip(order, args[1:], strict=False)]
            for old_keyword, new_keyword in renamed.items():
                if old_keyword in kwargs:
                    keys.append(f"{old}({old_keyword}=)")
                    moved.append((new_keyword, kwargs.pop(old_keyword)))
            names = [k for k, _ in moved] + list(kwargs)
            both = sorted({k for k in names if names.count(k) > 1})
            if both:
                raise TypeError(f"{func.__name__}() got multiple values for {both}")
            if keys:
                warn(keys[0])
            return func(*args[:1], **dict(moved), **kwargs)

        return _read_old  # type: ignore[return-value]

    return decorate


@contextlib.contextmanager
def internal() -> Iterator[None]:
    """Silence deprecation warnings for names oceanarray itself still calls inside the block."""
    token = _internal.set(True)
    try:
        yield
    finally:
        _internal.reset(token)
