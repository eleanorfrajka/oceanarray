"""Unit tests for oceanarray/reports/_pdf.py (HTML-to-PDF combination).

`_ordered_report_files` is pure and tested without WeasyPrint; the render test
is gated behind an importorskip because WeasyPrint is an optional extra.
"""

from pathlib import Path

import pytest
import yaml

from oceanarray.reports import MooringReport, combine_mooring_pdf
from oceanarray.reports._pdf import (
    _ordered_report_files,
    _render_combined_html,
    _slug_for,
)

# Minimal mooring YAML that MooringReport.generate() can render a summary from
# (mirrors the fixture in test_report.py::TestMooringReport).
_MOORING_YAML = {
    "name": "TEST_M1",
    "deployment_time": "2024-06-01T00:00:00",
    "recovery_time": "2024-09-01T00:00:00",
    "waterdepth": 1000,
    "latitude": 60.0,
    "longitude": -30.0,
    "deployment_cruise": "TEST01",
    "deployment_ship": "RV Test",
    "instruments": [
        {
            "instrument": "microcat",
            "serial": "1234",
            "hab": 100.0,
            "filename": "test.cnv",
            "file_type": "sbe-cnv",
            "clock_offset": 60,
        }
    ],
}


def _write_html(path: Path, title: str) -> None:
    """Write a minimal valid HTML document to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<html><body><h1>{title}</h1></body></html>")


def _html_page(body: str, style: str = ".masthead{}") -> str:
    """Wrap *body* in a minimal HTML document with one ``<style>`` block."""
    return f"<html><head><style>{style}</style></head><body>{body}</body></html>"


class TestOrderedReportFiles:
    """Reading-order resolution: summary -> instruments -> stack -> grid."""

    def test_full_order(self, tmp_path: Path) -> None:
        """All page types present return in pipeline data-flow order."""
        _write_html(tmp_path / "M1_report.html", "summary")
        _write_html(tmp_path / "instrument" / "M1_7507_report.html", "i2")
        _write_html(tmp_path / "instrument" / "M1_0123_report.html", "i1")
        _write_html(tmp_path / "M1_stack_report.html", "stack")
        _write_html(tmp_path / "M1_grid_report.html", "grid")

        names = [p.name for p in _ordered_report_files(tmp_path, "M1")]
        assert names == [
            "M1_report.html",
            "M1_0123_report.html",  # instruments sorted lexically
            "M1_7507_report.html",
            "M1_stack_report.html",
            "M1_grid_report.html",
        ]

    def test_glob_metachars_in_mooring_name_are_escaped(self, tmp_path: Path) -> None:
        """A mooring name with glob metacharacters matches only its own files.

        Without escaping, the instrument glob ``M[1]_*_report.html`` would treat
        ``[1]`` as a character class and match a sibling ``M1_...`` file while
        missing the literal ``M[1]_...`` file.
        """
        _write_html(tmp_path / "M[1]_report.html", "summary")
        _write_html(tmp_path / "instrument" / "M[1]_7507_report.html", "mine")
        _write_html(tmp_path / "instrument" / "M1_9999_report.html", "other")

        names = [p.name for p in _ordered_report_files(tmp_path, "M[1]")]
        assert names == ["M[1]_report.html", "M[1]_7507_report.html"]
        assert "M1_9999_report.html" not in names

    def test_only_existing_files_returned(self, tmp_path: Path) -> None:
        """Missing page types are skipped, not fabricated."""
        _write_html(tmp_path / "M1_report.html", "summary")
        _write_html(tmp_path / "M1_grid_report.html", "grid")
        names = [p.name for p in _ordered_report_files(tmp_path, "M1")]
        assert names == ["M1_report.html", "M1_grid_report.html"]

    def test_no_files_returns_empty(self, tmp_path: Path) -> None:
        """A directory with no matching reports yields an empty list."""
        assert _ordered_report_files(tmp_path, "M1") == []


class TestSlugFor:
    """Slug derivation for each source report file type."""

    def test_summary(self, tmp_path: Path) -> None:
        assert _slug_for(tmp_path / "M1_report.html", "M1") == "summary"

    def test_stack_and_grid(self, tmp_path: Path) -> None:
        assert _slug_for(tmp_path / "M1_stack_report.html", "M1") == "stack"
        assert _slug_for(tmp_path / "M1_grid_report.html", "M1") == "grid"

    def test_instrument_serial(self, tmp_path: Path) -> None:
        p = tmp_path / "instrument" / "M1_9920_report.html"
        assert _slug_for(p, "M1") == "instr-9920"

    def test_mooring_name_with_underscores(self, tmp_path: Path) -> None:
        """A serial is extracted correctly when the mooring name itself has ``_``."""
        p = tmp_path / "instrument" / "dune2_1_2026_2941_report.html"
        assert _slug_for(p, "dune2_1_2026") == "instr-2941"

    def test_unknown_file_falls_back_to_sanitised_stem(self, tmp_path: Path) -> None:
        """A non-report file (e.g. a cover page) slugs from its sanitised stem."""
        assert _slug_for(tmp_path / "cover_map.html", "M1") == "cover-map"


class TestBuildCombinedHtml:
    """The single-document transform: id namespacing + inter-page link rewriting.

    Pure string transform — no WeasyPrint needed.
    """

    def test_interpage_link_rewritten_and_ids_namespaced(self) -> None:
        summary = _html_page(
            '<div id="top">summary</div>'
            '<a href="M1_stack_report.html">stack</a>'
            '<a href="#qc">qc</a><h2 id="qc">QC</h2>'
        )
        stack = _html_page('<div id="top">stack</div>')
        out = _render_combined_html(
            [
                ("summary", "M1_report.html", summary),
                ("stack", "M1_stack_report.html", stack),
            ]
        )
        # inter-page file link becomes an in-document anchor to the target masthead
        assert 'href="#stack__top"' in out
        assert "M1_stack_report.html" not in out  # no dead file link remains
        # per-page ids and same-page anchors are namespaced by slug
        assert 'id="summary__top"' in out
        assert 'id="stack__top"' in out
        assert 'href="#summary__qc"' in out
        assert 'id="summary__qc"' in out
        # Completeness tripwire: no id survives without a slug prefix.  Scan with a
        # BROADER pattern than the transform (either quote style, spaces around =)
        # so it fails loudly if a future template uses a spelling the transform's
        # `id="..."` regex would miss.
        import re

        ids = re.findall(r"""id\s*=\s*["']([^"']*)["']""", out)
        assert ids  # sanity: some ids present
        assert all("__" in v for v in ids), f"unprefixed id(s) survived: {ids}"

    def test_interpage_link_with_fragment_and_path_rewritten(self) -> None:
        """An ``instrument/...#start`` link maps to the whole-page anchor (phase 1)."""
        summary = (
            "<html><body>"
            '<a href="instrument/M1_9920_report.html#start">6 h</a>'
            "</body></html>"
        )
        instr = '<html><body><div id="top">i</div></body></html>'
        out = _render_combined_html(
            [
                ("summary", "M1_report.html", summary),
                ("instr-9920", "M1_9920_report.html", instr),
            ]
        )
        assert 'href="#instr-9920__top"' in out
        assert "M1_9920_report.html" not in out

    def test_identical_styles_deduplicated(self) -> None:
        css = ".masthead{color:red}"
        out = _render_combined_html(
            [
                ("summary", "M1_report.html", _html_page("<p>1</p>", css)),
                ("stack", "M1_stack_report.html", _html_page("<p>2</p>", css)),
            ]
        )
        assert out.count("<style>") == 1  # identical blocks collapsed to one

    def test_page_without_body_tag_uses_whole_html(self) -> None:
        """A page with no ``<body>`` falls back to using its full HTML (defensive)."""
        out = _render_combined_html(
            [("summary", "M1_report.html", '<div id="top">bare</div>')]
        )
        assert 'id="summary__top"' in out
        assert '<section class="pdf-page">' in out

    def test_each_page_wrapped_in_pdf_page_section(self) -> None:
        one = "<html><body><p>a</p></body></html>"
        two = "<html><body><p>b</p></body></html>"
        out = _render_combined_html(
            [
                ("summary", "M1_report.html", one),
                ("stack", "M1_stack_report.html", two),
            ]
        )
        assert out.count('<section class="pdf-page">') == 2


class TestCombineMooringPdf:
    """End-to-end PDF combination."""

    def test_missing_reports_raises(self, tmp_path: Path) -> None:
        """No HTML and no cover -> FileNotFoundError, not an empty PDF."""
        with pytest.raises(FileNotFoundError, match="No report HTML files"):
            combine_mooring_pdf(tmp_path, "M1")

    def test_renders_pdf(self, tmp_path: Path) -> None:
        """Combining existing HTML pages produces a non-empty PDF file."""
        pytest.importorskip("weasyprint", reason="pdf extra not installed")
        _write_html(tmp_path / "M1_report.html", "summary")
        _write_html(tmp_path / "instrument" / "M1_7507_report.html", "instr")
        _write_html(tmp_path / "M1_stack_report.html", "stack")

        out = combine_mooring_pdf(tmp_path, "M1")

        assert out == tmp_path / "M1_report.pdf"
        assert out.exists()
        data = out.read_bytes()
        assert data.startswith(b"%PDF-")
        assert len(data) > 0

    def test_crosspage_link_resolves_inside_merged_pdf(self, tmp_path: Path) -> None:
        """A summary→stack link becomes an internal PDF destination, not a dead URI.

        This is the whole point of the single-document combine: rendering each
        page separately (the old behaviour) leaves the inter-page link as an
        external ``/URI`` to a ``.html`` file that does not exist in the PDF.
        WeasyPrint compresses annotations into object streams, so the check
        inflates the FlateDecode streams before searching.
        """
        pytest.importorskip("weasyprint", reason="pdf extra not installed")
        import re
        import zlib

        (tmp_path / "M1_report.html").write_text(
            '<html><body><div id="top">summary</div>'
            '<a href="M1_stack_report.html">stack</a></body></html>'
        )
        (tmp_path / "M1_stack_report.html").write_text(
            '<html><body><div id="top">stack</div></body></html>'
        )

        out = combine_mooring_pdf(tmp_path, "M1")
        pdf = out.read_bytes()

        blob = pdf
        for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.DOTALL):
            try:
                blob += zlib.decompress(m.group(1))
            except zlib.error:
                continue

        assert b"/Link" in blob, "no link annotation emitted"
        assert b"/Dest" in blob, "link did not resolve to an internal destination"
        assert b"/URI" not in blob, "link left as an external URI"
        assert b".html" not in blob, "dead .html file link survived into the PDF"

    def test_custom_output_path(self, tmp_path: Path) -> None:
        """An explicit output_path is honoured."""
        pytest.importorskip("weasyprint", reason="pdf extra not installed")
        _write_html(tmp_path / "M1_report.html", "summary")
        dest = tmp_path / "custom" / "out.pdf"
        dest.parent.mkdir()

        out = combine_mooring_pdf(tmp_path, "M1", output_path=dest)

        assert out == dest
        assert dest.read_bytes().startswith(b"%PDF-")

    def test_renders_real_report_html(self, tmp_path: Path) -> None:
        """The real Jinja summary page (not synthetic HTML) renders to PDF.

        This exercises the actual report template through WeasyPrint — the print
        CSS, embedded styles and base64 images — which the synthetic-HTML tests
        above do not cover.
        """
        pytest.importorskip("weasyprint", reason="pdf extra not installed")
        proc = tmp_path / "proc"
        mooring_proc = proc / "TEST_M1"
        mooring_proc.mkdir(parents=True)
        (mooring_proc / "TEST_M1.mooring.yaml").write_text(yaml.dump(_MOORING_YAML))

        report = MooringReport(proc_dir=str(proc))
        summary = report.generate("TEST_M1")
        assert summary is not None and summary.exists()

        # generate() writes to proc/TEST_M1/report/ by default.
        out = combine_mooring_pdf(summary.parent, "TEST_M1")
        assert out == summary.parent / "TEST_M1_report.pdf"
        assert out.read_bytes().startswith(b"%PDF-")


class TestCmdReportPdfFlag:
    """The `oceanarray report --pdf` CLI path (cmd_report glue)."""

    def _make_proc(self, tmp_path: Path) -> Path:
        """Create a minimal current-layout proc dir with one mooring YAML."""
        proc = tmp_path / "proc"
        (proc / "TEST_M1").mkdir(parents=True)
        (proc / "TEST_M1" / "TEST_M1.mooring.yaml").write_text(yaml.dump(_MOORING_YAML))
        return proc

    def test_pdf_flag_builds_pdf(self, tmp_path: Path) -> None:
        """`report --pdf` generates the summary then writes the combined PDF."""
        pytest.importorskip("weasyprint", reason="pdf extra not installed")
        from oceanarray.cli import build_parser

        proc = self._make_proc(tmp_path)
        out = tmp_path / "rep"
        args = build_parser().parse_args(
            [
                "report",
                "TEST_M1",
                "--proc-dir",
                str(proc),
                "--raw-dir",
                str(proc),
                "--pdf",
                "--output-dir",
                str(out),
            ]
        )
        assert args.func(args) == 0
        pdf = out / "TEST_M1_report.pdf"
        assert pdf.exists()
        assert pdf.read_bytes().startswith(b"%PDF-")

    def test_all_pdf_best_effort_but_explicit_pdf_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """If the PDF can't be built: `--all` keeps HTML success (0); `--pdf` fails (1)."""
        import oceanarray.reports as report_pkg
        from oceanarray.cli import build_parser

        def _boom(*_a, **_k):
            msg = "WeasyPrint not installed"
            raise ImportError(msg)

        monkeypatch.setattr(report_pkg, "combine_mooring_pdf", _boom)
        proc = self._make_proc(tmp_path)
        base = [
            "report",
            "TEST_M1",
            "--proc-dir",
            str(proc),
            "--raw-dir",
            str(proc),
            "--output-dir",
            str(tmp_path / "rep"),
        ]
        args_all = build_parser().parse_args([*base, "--all"])
        assert args_all.func(args_all) == 0  # best-effort: HTML still succeeded
        args_pdf = build_parser().parse_args([*base, "--pdf"])
        assert args_pdf.func(args_pdf) == 1  # explicit request unmet = failure

    def test_pdf_dry_run_reports_target(self, tmp_path: Path, capsys) -> None:
        """`report --pdf --dry-run` prints the PDF target and writes nothing."""
        from oceanarray.cli import build_parser

        proc = self._make_proc(tmp_path)
        args = build_parser().parse_args(
            [
                "report",
                "TEST_M1",
                "--proc-dir",
                str(proc),
                "--raw-dir",
                str(proc),
                "--pdf",
                "--dry-run",
            ]
        )
        assert args.func(args) == 0
        assert "PDF:" in capsys.readouterr().out
        assert not (proc / "TEST_M1" / "report" / "TEST_M1_report.pdf").exists()
