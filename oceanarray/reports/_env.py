"""File-based Jinja environment for the report HTML templates.

Report page templates live in ``reports/templates/`` and are loaded with a
:class:`~jinja2.FileSystemLoader` so they can use ``{% extends %}`` and
``{% include %}`` — which the previous inline template strings could not.

The environment settings are byte-identical to the previous per-module
``Environment(autoescape=True)`` calls (autoescape on; ``trim_blocks`` and
``lstrip_blocks`` at Jinja's defaults of ``False``), so migrating a template
from an inline string to a file does not change the rendered output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import ChoiceLoader, Environment, FileSystemLoader
from markupsafe import escape

from .. import __version__ as _oceanarray_version
from ..config import parameters as params
from . import _figdebug
from ._html_helpers import _build_history_tables
from ._vendored.figure_png import source_of
from .design import _JS_SORT_TABLES, FIGURE_SOURCE, SHARED_CSS

#: Directory holding the report page templates.
TEMPLATES_DIR: Path = Path(__file__).with_name("templates")
#: Directory holding the vendored shared templates (nav, nc-table macros); searched
#: after the package's own, so a local template of the same name still wins.
VENDORED_TEMPLATES_DIR: Path = Path(__file__).parent / "_vendored" / "templates"

_ENV = Environment(
    loader=ChoiceLoader(
        [
            FileSystemLoader(str(TEMPLATES_DIR)),
            FileSystemLoader(str(VENDORED_TEMPLATES_DIR)),
        ]
    ),
    autoescape=True,
    auto_reload=False,
)
#: Package identity available to every template (masthead wordmark).
_ENV.globals["package_name"] = params.PACKAGE_NAME


#: Package version for the footer, from ``oceanarray.__version__`` (the
#: setuptools-scm ``_version.py``, falling back to installed metadata, else
#: ``"0.0.0"``).  The footer always renders it (so the golden is stable across
#: build/no-build checkouts); the golden test masks the value so a version bump
#: does not churn the fixtures.
_ENV.globals["package_version"] = _oceanarray_version
#: Per-figure debug lookup (``func · figsize · png``) for templates' ``.debug``
#: sections; returns "" unless ``OCEANARRAY_REPORT_DEBUG`` is set.
_ENV.globals["figdbg"] = _figdebug.figdbg
#: Generated stylesheet — the single source of truth for report CSS (tokens,
#: type/spacing scale, slot classes, shared chrome).  base.html injects it via
#: ``{{ css | safe }}``; page-specific rules live in a small local block that
#: references these token variables (U0.1).  The accent is applied in the local
#: ``_report_css`` wiring; the vendored ``_css.py`` names no package.
_ENV.globals["css"] = SHARED_CSS
#: Click-to-sort table script, injected once at the end of ``<body>`` by base.html.
_ENV.globals["js_sort_tables"] = _JS_SORT_TABLES
#: Per-figure ``source:`` caption toggle (which draw function produced each panel)
#: and the base64 -> source lookup it reads.  Off for oceanarray (design.FIGURE_SOURCE).
_ENV.globals["show_source"] = FIGURE_SOURCE
_ENV.globals["source_of"] = source_of

#: The vendored netCDF-inventory table macros, accessed as callables so the render
#: functions below invoke them directly from the builder output; ``show_source``
#: resolves from the env globals above.
_NC = _ENV.get_template("_nc_tables.html").module


def render_template(name: str, /, **context: Any) -> str:
    """Render report template *name* with *context* and return the HTML string.

    Parameters
    ----------
    name : str
        Template filename relative to ``reports/templates/`` (e.g.
        ``"instrument.html"``).
    **context
        Template variables, forwarded to :meth:`jinja2.Template.render`.

    Returns
    -------
    str
        The rendered HTML.

    """
    context.setdefault("debug", _figdebug.enabled())
    return _ENV.get_template(name).render(**context)


# ---------------------------------------------------------------------------
# Shared report partials — one copy of the history list and the NetCDF metadata
# tables, rendered to markup for manifest ``html``/``table`` panel payloads on
# every page (grid, stack, instrument, mooring).  The template files are still
# named ``_grid_*.html`` for now; renaming them to ``_report_*.html`` is a
# cosmetic follow-up (needs ``git mv``), tracked in minor-fixes.
# ---------------------------------------------------------------------------


def render_history(history_entries: Any) -> str:
    """Render the processing history as a header table + a dated Time/Event table.

    The undated header items (processed-by, Reader, Format, Source file, Stages) render
    through the vendored ``properties_table``; the dated entries through a sortable
    Time/Event table, split from the parsed entries by
    :func:`~oceanarray.reports._html_helpers._build_history_tables`.
    """
    header_rows, event_rows = _build_history_tables(history_entries)
    return render_template(
        "_grid_history.html",
        header_rows=header_rows,
        event_rows=event_rows,
        history_source="oceanarray/reports/_html_helpers.py:_build_history_tables",
    )


def render_nc_variables(nc_meta: Any) -> str:
    """Render the netCDF variables table (``nc_variables`` panel payload).

    Dimensioned variables grouped by dimension set
    (:func:`~oceanarray.reports._vendored.nc_tables.variables_rows`), drawn by the
    vendored ``variables_tables`` macro; QC companions fold into the parent row's
    ``(_QC)`` marker.
    """
    if nc_meta.get("error"):
        return (
            f'<p class="none-note">Could not read file: {escape(nc_meta["error"])}</p>'
        )
    if "var_groups" not in nc_meta:  # no NetCDF for this instrument -> empty nc_meta
        return ""
    return str(
        _NC.variables_tables(
            nc_meta["var_groups"],
            optional=("units", "minmax", "stdname", "attrs"),
            source=nc_meta.get("var_src"),
        )
    )


def render_nc_coords(nc_meta: Any) -> str:
    """Render the netCDF coordinates table (``nc_coords`` panel payload).

    One row per coordinate variable
    (:func:`~oceanarray.reports._vendored.nc_tables.coords_rows`), drawn by the
    vendored ``coords_table`` macro.
    """
    if nc_meta.get("error") or "coords_rows" not in nc_meta:
        return ""
    return "<h3>Coordinates</h3>" + str(
        _NC.coords_table(
            nc_meta["coords_rows"],
            optional=("units", "minmax", "stdname", "attrs"),
            source=nc_meta.get("coords_src"),
        )
    )


def render_nc_scalars(nc_meta: Any) -> str:
    """Render the scalar-metadata table (``nc_scalars`` panel payload).

    0-d variables (:func:`~oceanarray.reports._vendored.nc_tables.scalars_rows`),
    drawn by the vendored ``scalars_table`` macro; ``SENSOR_*`` variables are
    included here.
    """
    if nc_meta.get("error") or "scalar_rows" not in nc_meta:
        return ""
    return "<h3>Scalar variables</h3>" + str(
        _NC.scalars_table(
            nc_meta["scalar_rows"],
            optional=("units", "minmax", "stdname", "attrs"),
            source=nc_meta.get("scalar_src"),
        )
    )


def render_nc_globals(nc_meta: Any) -> str:
    """Render the global-attributes table (``nc_globals`` panel payload).

    Attributes split into the canonical display groups
    (:func:`~oceanarray.reports._vendored.nc_tables.globals_rows` with oceanarray's
    :data:`~oceanarray.reports.design.ATTR_GROUP_EXTRAS`), drawn by the vendored
    ``globals_tables`` macro.
    """
    if nc_meta.get("error") or "global_groups" not in nc_meta:
        return ""
    return str(
        _NC.globals_tables(nc_meta["global_groups"], source=nc_meta.get("global_src"))
    )
