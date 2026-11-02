"""Combine a mooring's per-report HTML files into a single A4 PDF.

The HTML reports written by :class:`~oceanarray.reports._mooring.MooringReport`
are the single source of truth.  This module post-processes those files with
WeasyPrint — it does not touch report generation or the Jinja templates.  Print
layout (A4 page size, margins, page numbers, page-break avoidance, hidden nav
buttons) is injected as an extra stylesheet at render time.

WeasyPrint is an optional dependency; install it with ``pip install
oceanarray[pdf]``.
"""

from __future__ import annotations

import re
from glob import escape as _glob_escape
from pathlib import Path

# Print-only stylesheet applied to every source report at render time.
# Kept here (not in the Jinja templates) so the HTML output is unchanged and the
# templates remain the single source of truth for on-screen look/feel.
_PRINT_CSS = """
@page {
    size: A4;
    margin: 1.6cm 1.4cm;
    @bottom-center {
        content: counter(page) " / " counter(pages);
        font-size: 8pt;
        color: #666;
    }
}
/* Keep figures and cards from splitting across a page break. */
figure, .card, .metric-card, .instrument-card {
    break-inside: avoid;
}
/* Tables may break across pages, but only at row boundaries — so a tall table
   (e.g. NetCDF global attributes) fills the space under its heading and
   continues overleaf, instead of jumping whole to the next page and orphaning
   the heading with a gap above it.  Individual rows stay intact and the header
   row repeats on each page.

   ``table-layout: fixed`` (with an explicit width, required for it to engage) is
   a large PERFORMANCE fix, not cosmetics: with the default ``auto`` layout
   WeasyPrint scans every cell to compute intrinsic column widths, and a single
   huge cell — e.g. the ~7.7 KB one-line seasenselib ``raw-opaque`` provenance
   JSON in an ADCP global-attributes table — makes that O(content) pass
   pathological (one page went from 0.3 s to 47 s).  Fixed layout skips it:
   columns size from the first row, long values wrap.  Overall report render
   dropped ~50 s → ~6 s.  Print-only, so on-screen tables are unaffected. */
table {
    break-inside: auto;
    table-layout: fixed;
    width: 100%;
}
thead {
    display: table-header-group;
}
tr {
    break-inside: avoid;
}
/* Keep a section heading with the start of its content: never leave a heading
   stranded at the bottom of a page.  (_report_css handles h2; the NetCDF
   attribute/variable tables use h3/h4 subheads, so cover those too.) */
h2, h3, h4 {
    break-after: avoid;
}
/* Cap figures at the printable content width as an overflow ceiling, but do NOT
   use !important: the templates give many figures an inline per-figure cap
   (e.g. <img class="fig" style="max-width:50%">) via the `.fig` convention, and
   an !important here would clobber those and blow every plot up to full width. */
img {
    max-width: 100%;
    height: auto;
    break-inside: avoid;
}
/* A single <img> cannot split across PDF pages.  Multipanel figures are already
   paginated into <=5-panel images (report-figures #10), so this cap is only a
   backstop for any residual over-budget figure: it scales a too-tall image down
   to fit one page.  A4 usable height ~26cm minus room for a heading/note above. */
img.fig {
    max-height: 22cm;
}
/* WeasyPrint cannot resolve ``repeat(auto-fill, minmax(...))`` and collapses
   such grids to a single column (the summary header's .meta-grid balloons as a
   result).  Force an explicit column count in print instead. */
.meta-grid {
    grid-template-columns: repeat(3, 1fr) !important;
}
/* Screen tables set ``white-space: nowrap`` on headers, which runs wide tables
   off the page edge; let them wrap when paginated. */
th {
    white-space: normal !important;
}
/* Print tables denser than screen so more fits per row on A4: smaller type and
   tighter cell padding.  (Screen: 0.83rem, ~0.4-0.45rem padding.) */
table {
    font-size: 0.7rem !important;
}
th, td {
    padding: 0.25rem 0.4rem !important;
}
/* Section 2 "Processing pipeline": in the narrower PDF column the status pills
   (.badge) wrap onto several lines.  Shrink the pill text and padding and stop
   the pipeline from wrapping so each instrument's status sits on one line. */
.pipeline {
    flex-wrap: nowrap !important;
    gap: 0.1rem !important;
}
.badge {
    font-size: 0.55rem !important;
    padding: 0.08em 0.3em !important;
}
.arrow {
    font-size: 0.6rem !important;
    margin: 0 !important;
}
/* Section 3.5 copy-paste boxes are <textarea rows="2"> (deployment_time +
   recovery_time).  WeasyPrint renders them too short and clips the second row;
   force enough height for both lines and let content overflow visibly. */
textarea {
    height: auto !important;
    min-height: 3.4em !important;
    overflow: visible !important;
}
/* Screen caps the body at 1150px; print sets max-width:100%, so notes and
   paragraphs run the full ~18cm page width (~95 chars/line — too long to read
   comfortably).  Cap the text measure at ~78 characters.  Tables and figures
   are block-level siblings, not paragraphs, so they keep full width. */
p, li {
    max-width: 78ch;
}
/* Masthead nav, inventory strip, and the per-heading "↑ top" links jump within a
   single HTML file — dead in a merged PDF (cross-page document links are handled
   separately). */
.page-nav, .inventory-strip, .top-link {
    display: none !important;
}
/* Each source report is wrapped in a .pdf-page section when combined into one
   document; start every page after the first on a fresh sheet (the leading
   report keeps the first page, no blank cover). */
.pdf-page + .pdf-page {
    break-before: page;
}
"""


def _ordered_report_files(report_dir: Path, mooring_name: str) -> list[Path]:
    """Return the mooring's existing report HTML files in reading order."""
    # Summary -> per-instrument -> stack -> grid, matching the pipeline data flow.
    files: list[Path] = []
    summary = report_dir / f"{mooring_name}_report.html"
    if summary.exists():
        files.append(summary)

    instr_dir = report_dir / "instrument"
    if instr_dir.is_dir():
        # Escape the mooring name: glob metacharacters (e.g. ``[`` in a name)
        # would otherwise be interpreted as patterns and match the wrong files.
        pattern = f"{_glob_escape(mooring_name)}_*_report.html"
        files.extend(sorted(instr_dir.glob(pattern)))

    for suffix in ("stack", "grid"):
        p = report_dir / f"{mooring_name}_{suffix}_report.html"
        if p.exists():
            files.append(p)

    return files


# Regexes for the single-document combine.  These namespace anchor ids per page
# so links resolve within the merged PDF.
#
# SAFETY CONDITION — no inline SVG.  Rewriting ``id="x"`` is safe only because
# figures embed as base64 PNG (``_encode.py::_fig_to_base64``) and there is no
# ``<svg>`` anywhere under ``oceanarray/reports/``.  SVG references its own ids
# via ``url(#grad1)`` and ``xlink:href="#…"``, which this pass does NOT rewrite;
# namespacing ``id=`` without them would detach gradients/clip-paths and yield a
# silently unstyled figure in the PDF only.  If figures ever switch to SVG, this
# combine must also rewrite ``url(#…)`` / ``xlink:href``.
_ID_ATTR_RE = re.compile(r'(?<=\s)id="([^"]*)"')
_LOCAL_HREF_RE = re.compile(r'href="#([^"]*)"')
_STYLE_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.DOTALL | re.IGNORECASE)


def _slug_for(path: Path, mooring_name: str) -> str:
    """Return the slug that namespaces a source report's ids in the merged document."""
    name = path.name
    if name == f"{mooring_name}_report.html":
        return "summary"
    if name == f"{mooring_name}_stack_report.html":
        return "stack"
    if name == f"{mooring_name}_grid_report.html":
        return "grid"
    prefix = f"{mooring_name}_"
    suffix = "_report.html"
    if (
        path.parent.name == "instrument"
        and name.startswith(prefix)
        and name.endswith(suffix)
    ):
        return f"instr-{name[len(prefix) : -len(suffix)]}"
    # Anything else (e.g. a cover page) falls back to the sanitized file stem.
    return re.sub(r"[^A-Za-z0-9]+", "-", path.stem).strip("-") or "page"


def _namespace_page(slug: str, html: str) -> tuple[list[str], str]:
    """Extract a page's ``<style>`` blocks and its namespaced ``<body>`` inner HTML."""
    styles = _STYLE_RE.findall(html)
    body_match = _BODY_RE.search(html)
    body = body_match.group(1) if body_match else html
    # Prefix ids and same-page hrefs with the slug so anchors from different pages
    # cannot collide once concatenated.  Inter-page links (whole filenames) are left
    # for _rewrite_interpage_hrefs, which needs every page's slug first.
    body = _ID_ATTR_RE.sub(lambda m: f'id="{slug}__{m.group(1)}"', body)
    body = _LOCAL_HREF_RE.sub(lambda m: f'href="#{slug}__{m.group(1)}"', body)
    return styles, body


def _rewrite_interpage_hrefs(body: str, target_anchors: dict[str, str]) -> str:
    """Rewrite links to sibling report files into in-document anchors."""
    # A trailing #fragment on the link is dropped: links are whole-page jumps.
    for filename, anchor in target_anchors.items():
        pattern = re.compile(r'href="[^"]*' + re.escape(filename) + r'(?:#[^"]*)?"')
        body = pattern.sub(f'href="{anchor}"', body)
    return body


def _render_combined_html(pages: list[tuple[str, str, str]]) -> str:
    """Assemble one HTML document from ordered ``(slug, filename, html)`` pages."""
    # Jump target is the masthead: every page carries id="top" from base.html.
    # Each page is wrapped in .pdf-page so the print CSS starts it on a fresh sheet.
    target_anchors = {filename: f"#{slug}__top" for slug, filename, _ in pages}
    all_styles: list[str] = []
    sections: list[str] = []
    for slug, _filename, html in pages:
        styles, body = _namespace_page(slug, html)
        body = _rewrite_interpage_hrefs(body, target_anchors)
        all_styles.extend(styles)
        sections.append(f'<section class="pdf-page">{body}</section>')
    # De-duplicate identical <style> blocks (the same report CSS on every page),
    # order-preserving.
    style_tag = "\n".join(
        f"<style>{block}</style>" for block in dict.fromkeys(all_styles)
    )
    body_html = "\n".join(sections)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"{style_tag}\n</head>\n<body>\n{body_html}\n</body>\n</html>\n"
    )


def combine_mooring_pdf(
    report_dir: Path,
    mooring_name: str,
    output_path: Path | None = None,
    cover_html: Path | None = None,
) -> Path:
    """Merge a mooring's HTML reports into one A4 PDF.

    Renders each source HTML file to a WeasyPrint document with the print
    stylesheet applied, then concatenates their pages into a single PDF.

    Parameters
    ----------
    report_dir : Path
        Directory holding the mooring's report HTML files.
    mooring_name : str
        Mooring identifier used as the filename stem.
    output_path : Path, optional
        Destination PDF path.  Defaults to
        ``report_dir / f"{mooring_name}_report.pdf"``.
    cover_html : Path, optional
        HTML file prepended as the leading page (e.g. a location map).
        ``None`` produces no cover.

    Returns
    -------
    Path
        The written PDF path.

    Raises
    ------
    ImportError
        If WeasyPrint is not installed.
    FileNotFoundError
        If no report HTML files are found for the mooring.

    """
    # Validate inputs before requiring the optional engine, so "no report files"
    # is reported the same way whether or not WeasyPrint is installed (and so the
    # test for it does not need the extra on CI).
    report_dir = Path(report_dir)
    files = _ordered_report_files(report_dir, mooring_name)
    if cover_html is None and not files:
        msg = f"No report HTML files found for '{mooring_name}' in {report_dir}."
        raise FileNotFoundError(msg)

    try:
        from weasyprint import CSS, HTML
    except ImportError as exc:  # pragma: no cover - exercised via extra
        msg = (
            "PDF output requires WeasyPrint. Install it with "
            "'pip install oceanarray[pdf]'."
        )
        raise ImportError(msg) from exc

    css = CSS(string=_PRINT_CSS)

    sources: list[Path] = []
    if cover_html is not None:
        sources.append(Path(cover_html))
    sources.extend(files)

    # Build ONE document from all pages and render it once: WeasyPrint only
    # resolves internal hyperlinks within a single render(), so cross-page links
    # (stack/grid/instrument header cards) are dead when each file is rendered
    # separately.  Ids are namespaced per page first so anchors do not collide.
    pages = [
        (_slug_for(f, mooring_name), f.name, f.read_text(encoding="utf-8"))
        for f in sources
    ]
    combined_html = _render_combined_html(pages)

    if output_path is None:
        output_path = report_dir / f"{mooring_name}_report.pdf"
    output_path = Path(output_path)

    HTML(string=combined_html, base_url=str(report_dir)).write_pdf(
        str(output_path), stylesheets=[css]
    )
    return output_path
