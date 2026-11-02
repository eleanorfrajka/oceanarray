"""Mooring report package — public API."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oceanarray.reports._mooring import MooringReport
    from oceanarray.reports._pdf import combine_mooring_pdf
    from oceanarray.reports._summary import summary
    from oceanarray.reports._verb import report, report_paths

__all__ = ["MooringReport", "combine_mooring_pdf", "report", "report_paths", "summary"]

#: Public name -> the submodule that defines it.  Resolved lazily (see
#: :func:`__getattr__`) so importing this package — or any submodule, such as the
#: vendored layer or the ``design`` hook — does not import the page pipeline,
#: which imports the plotters and forms a cycle back through the vendored
#: ``design_tokens`` (the successor to ``config.report_tokens``).
_EXPORTS = {
    "MooringReport": "_mooring",
    "combine_mooring_pdf": "_pdf",
    "report": "_verb",
    "report_paths": "_verb",
    "summary": "_summary",
}


def __getattr__(name: str) -> object:
    """Resolve a public report name lazily, importing its module on first access."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    obj = getattr(importlib.import_module(f"oceanarray.reports.{module}"), name)
    globals()[name] = obj  # bind so later accesses are a normal attribute hit
    return obj
