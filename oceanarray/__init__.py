"""oceanarray: processing oceanographic mooring data, from raw instrument files to gridded products.

The public API is :data:`__all__`; every name in it resolves after
``import oceanarray``. Names load on first use, so importing the package stays
cheap.

Examples
--------
>>> import oceanarray as oa
>>> oa.run("proc/dune2_1_2026/dune2_1_2026.mooring.yaml", raw_dir="raw/", report=True)
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from oceanarray import mooring
    from oceanarray.exceptions import MooringError
    from oceanarray.pipeline import process, run
    from oceanarray.pipeline._stage import StageError
    from oceanarray.pipeline.export import export
    from oceanarray.pipeline.grid import grid
    from oceanarray.pipeline.read_mooring import read_mooring
    from oceanarray.pipeline.stack import stack
    from oceanarray.pipeline.stage1 import stage1
    from oceanarray.pipeline.stage2 import stage2
    from oceanarray.pipeline.stage3 import stage3
    from oceanarray.pipeline.write_mooring import write_mooring
    from oceanarray.readers import read
    from oceanarray.reports import report
    from oceanarray.writers import write

__all__ = [
    "MooringError",
    "StageError",
    "__version__",
    "export",
    "grid",
    "mooring",
    "process",
    "read",
    "read_mooring",
    "report",
    "run",
    "stack",
    "stage1",
    "stage2",
    "stage3",
    "write",
    "write_mooring",
]

#: Public name -> (module, attribute); attribute None means the module itself.
_EXPORTS: dict[str, tuple[str, str | None]] = {
    "MooringError": ("oceanarray.exceptions", "MooringError"),
    "StageError": ("oceanarray.pipeline._stage", "StageError"),
    "export": ("oceanarray.pipeline.export", "export"),
    "grid": ("oceanarray.pipeline.grid", "grid"),
    "mooring": ("oceanarray.mooring", None),
    "process": ("oceanarray.pipeline", "process"),
    "read": ("oceanarray.readers", "read"),
    "read_mooring": ("oceanarray.pipeline.read_mooring", "read_mooring"),
    "report": ("oceanarray.reports", "report"),
    "run": ("oceanarray.pipeline", "run"),
    "stack": ("oceanarray.pipeline.stack", "stack"),
    "stage1": ("oceanarray.pipeline.stage1", "stage1"),
    "stage2": ("oceanarray.pipeline.stage2", "stage2"),
    "stage3": ("oceanarray.pipeline.stage3", "stage3"),
    "write": ("oceanarray.writers", "write"),
    "write_mooring": ("oceanarray.pipeline.write_mooring", "write_mooring"),
}

#: Old top-level names kept for one release: name -> the module now holding it.
_DEPRECATED: dict[str, str] = {"parameters": "oceanarray.config.parameters"}


def __getattr__(name: str) -> object:
    """Resolve a public name, a deprecated alias or a subpackage on first access."""
    if name in _EXPORTS:
        module, attr = _EXPORTS[name]
        mod = importlib.import_module(module)
        obj = mod if attr is None else getattr(mod, attr)
        globals()[name] = obj  # later accesses are a plain attribute hit
        return obj
    if name in _DEPRECATED:
        from oceanarray import _deprecated

        _deprecated.warn(f"{__name__}.{name}")
        return importlib.import_module(_DEPRECATED[name])
    if not name.startswith("_"):
        try:
            return importlib.import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as e:
            if e.name != f"{__name__}.{name}":
                raise
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """List the public names alongside the module's own."""
    return sorted(set(globals()) | set(__all__))
