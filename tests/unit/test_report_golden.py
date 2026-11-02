"""Golden-file regeneration test for the mooring report pages.

Renders every report page for the ``dune2`` fixture and asserts the HTML matches
committed golden files, after removing the parts that are not deterministic
across machines: the ``generated`` timestamp becomes ``<GENERATED>``, the file
"last modified" mtimes and the package version are masked, and each base64
PNG payload becomes a fixed ``<PNG>`` placeholder (matplotlib output is not
byte-identical across operating systems, so figure pixels cannot be compared
cross-machine).

This is the safety net for the report-subsystem refactor: a structural change
must keep this green (it proves the HTML did not move); a deliberate visual
change re-baselines it (``REBASELINE_GOLDEN=1 pytest``) so the intended change
*is* the reviewable diff. Figure pixel changes are reviewed by eye, not here.
"""

import os
import pathlib
import re

import pytest

from oceanarray.reports import MooringReport
from oceanarray.utilities import _safe_rel

_FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
_PROC = _FIXTURES / "proc"
_GOLDEN = _FIXTURES / "golden" / "dune2"
_MOORING = "dune2_1_2026"

#: Report pages produced for a single-mooring fixture (relative to the out dir).
_PAGES = [
    "dune2_1_2026_report.html",
    "dune2_1_2026_stack_report.html",
    "dune2_1_2026_grid_report.html",
    "instrument/dune2_1_2026_2941_report.html",
    "instrument/dune2_1_2026_9920_report.html",
]

# The generation timestamp appears as "generated <ts>" (masthead) and as the
# last "&bull; <ts>" segment of the shared report footer.  Anchor to those exact
# contexts so real data timestamps rendered elsewhere are left untouched:
# deployment/recovery times (in <dd> meta-grid cells) and any prose dates.
_TS_RE = re.compile(r"(generated |&bull; )\d{4}-\d\d-\d\d \d\d:\d\d UTC")
# File "Last modified" cells (bare <td>).  st_mtime is not preserved by git, so
# it varies on every checkout; mask it.  Deploy/recovery times use <dd> cells.
_MTIME_RE = re.compile(r"<td>\s*\d{4}-\d\d-\d\d \d\d:\d\d UTC\s*</td>")
_PNG_RE = re.compile(r"base64,[A-Za-z0-9+/=]+")
# Footer package version ("...oceanarray</strong> v0.3.0.post1.dev1 &bull;").  It
# comes from setuptools-scm (_version.py) and moves with every commit/build, so
# mask it — the footer's *presence* of a version is what matters, not the value.
_VER_RE = re.compile(r"(</strong>) v[\w.+!-]+")


def _normalise(html: str) -> str:
    """Return *html* with machine- and checkout-dependent fields made stable.

    Neutralises the generation timestamp, the file "Last modified" mtimes (git
    does not preserve mtimes), and base64 PNG payloads.  matplotlib PNGs are not
    byte-identical across operating systems, so figure pixels are not compared;
    what remains testable is each figure's presence, sizing and surrounding
    markup, plus all HTML/CSS/text.
    """
    html = _TS_RE.sub(r"\1<GENERATED>", html)
    html = _MTIME_RE.sub("<td><MTIME></td>", html)
    html = _VER_RE.sub(r"\1 v<VERSION>", html)
    return _PNG_RE.sub("base64,<PNG>", html)


def test_safe_rel(tmp_path: pathlib.Path) -> None:
    """`_safe_rel` returns a path under *root* relatively, else the bare name."""
    under = tmp_path / "sub" / "file.html"
    assert _safe_rel(under, tmp_path) == str(pathlib.Path("sub") / "file.html")
    outside = tmp_path.parent / "elsewhere.html"
    assert _safe_rel(outside, tmp_path) == "elsewhere.html"


@pytest.fixture(scope="module")
def rendered_dir(tmp_path_factory) -> pathlib.Path:
    """Render every dune2 report page once into a temp dir for the module."""
    out = tmp_path_factory.mktemp("golden_render")
    MooringReport(proc_dir=str(_PROC)).generate(
        _MOORING,
        force=True,
        outdir=str(out),
        instruments=True,
        grid=True,
        stack=True,
    )
    return out


@pytest.mark.parametrize("page", _PAGES)
def test_report_page_matches_golden(rendered_dir: pathlib.Path, page: str) -> None:
    """Each rendered report page matches its committed, normalised golden file."""
    produced_path = rendered_dir / page
    assert produced_path.exists(), f"report did not produce {page}"
    produced = _normalise(produced_path.read_text(encoding="utf-8"))

    golden_path = _GOLDEN / page
    if os.environ.get("REBASELINE_GOLDEN"):
        # Guard: re-baselining in CI would silently overwrite the goldens with the
        # CI run's own output and never catch drift.
        assert not os.environ.get("CI"), "REBASELINE_GOLDEN must not be set in CI"
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(produced, encoding="utf-8")
        pytest.skip(f"re-baselined golden for {page}")

    assert golden_path.exists(), (
        f"missing golden {golden_path.relative_to(_FIXTURES)}; "
        "create it with `REBASELINE_GOLDEN=1 pytest tests/unit/test_report_golden.py`"
    )
    expected = golden_path.read_text(encoding="utf-8")
    if produced != expected:
        # Write the actual output next to the temp render and give a compact,
        # base64-free diff hint rather than dumping megabytes of HTML.
        (rendered_dir / (pathlib.Path(page).name + ".actual")).write_text(
            produced, encoding="utf-8"
        )
        import difflib

        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                produced.splitlines(),
                fromfile=f"golden/{page}",
                tofile=f"produced/{page}",
                lineterm="",
                n=1,
            )
        )
        pytest.fail(
            f"{page} drifted from its golden. If this change is intended, "
            f"re-baseline with REBASELINE_GOLDEN=1.\n{diff[:4000]}"
        )


_SLOT_IMG_RE = re.compile(
    r'<img class="fig slot-([a-z-]+)"[^>]*base64,([A-Za-z0-9+/=]+)'
)

# Row-layout (Section.layout="row") figures carry the slot on the ``.fig-cell``
# wrapper while the enclosed img is bare ``class="fig"`` (it fills the cell), so
# they are invisible to _SLOT_IMG_RE.  This pairs each cell's slot with the next
# enclosed PNG without crossing into the following cell (tempered ``(?!fig-cell)``).
_CELL_IMG_RE = re.compile(
    r'fig-cell slot-([a-z-]+)">'
    r"(?:(?!fig-cell).)*?"
    r'<img class="fig"[^>]*base64,([A-Za-z0-9+/=]+)',
    re.DOTALL,
)


def test_png_geometry_on_rendered_page(rendered_dir: pathlib.Path) -> None:
    """Every ``slot-*``-classed figure's PNG width equals its slot width in px.

    The U0.2 slot system renders each figure at ``SLOTS[slot].inches`` wide and
    the template labels it ``class="fig slot-<name>"``, so the saved PNG is
    ``round(SLOTS[slot].inches * FIG_DPI)`` pixels wide.  This pins that invariant
    (spec §10.2): a figure can never again render at one width while the page
    displays it at another (the class-vs-``width:100%`` bug U0.2 fixed).  Only
    slot-classed figures are checked — bare ``class="fig"`` figures render
    full-canvas or in flex layouts and are covered by the golden HTML test.
    Row-layout (``Section.layout="row"``) figures are the exception: their slot
    lives on the ``.fig-cell`` wrapper, so they are checked separately below to
    stop a row panel's declared slot silently diverging from its adapter's render
    width (rep/09).
    """
    import base64
    import io

    from PIL import Image

    from oceanarray.reports._vendored import design_tokens as tok

    checked = 0
    for page in _PAGES:
        html = (rendered_dir / page).read_text(encoding="utf-8")
        for slot, b64 in _SLOT_IMG_RE.findall(html):
            width_px = Image.open(io.BytesIO(base64.b64decode(b64))).size[0]
            expected = round(tok.SLOTS[slot][1] * tok.FIG_DPI)
            # ±1 px: a half-integer inches*dpi (e.g. quarter = 2.25*150 = 337.5)
            # is truncated by matplotlib (337) while round() gives 338.  The check
            # exists to catch mis-slots, which are off by hundreds of px.
            assert abs(width_px - expected) <= 1, (
                f"{page}: slot-{slot} figure is {width_px}px, expected ~{expected}px "
                f"(SLOTS[{slot!r}].inches={tok.SLOTS[slot][1]} * FIG_DPI={tok.FIG_DPI})"
            )
            checked += 1
        # Row-layout figures: slot on the .fig-cell wrapper, bare img inside.
        for slot, b64 in _CELL_IMG_RE.findall(html):
            width_px = Image.open(io.BytesIO(base64.b64decode(b64))).size[0]
            expected = round(tok.SLOTS[slot][1] * tok.FIG_DPI)
            assert abs(width_px - expected) <= 1, (
                f"{page}: fig-cell slot-{slot} figure is {width_px}px, expected "
                f"~{expected}px — row-layout slot vs adapter render-width mismatch"
            )
            checked += 1
    assert checked > 0, "no slot-classed figures found; test is not exercising anything"


@pytest.mark.parametrize("alt", ["hydro_pts", "vel_uvw"])
def test_stack_timeseries_figures_render_at_full_width(
    rendered_dir: pathlib.Path, alt: str
) -> None:
    """The stack's multi-row time series are drawn at the full slot width, legend inside.

    They used to save with a tight bounding box around a legend placed outside the
    canvas, so the PNG came out narrower or wider than the 9-inch slot and the page
    rescaled it.
    """
    import base64
    import io

    from PIL import Image

    from oceanarray.reports._vendored import design_tokens as tok

    html = (rendered_dir / "dune2_1_2026_stack_report.html").read_text(encoding="utf-8")
    match = re.search(rf'base64,([A-Za-z0-9+/=]+)" alt="{alt}"', html)
    assert match, f"{alt} figure not found on the stack page"
    width_px = Image.open(io.BytesIO(base64.b64decode(match.group(1)))).size[0]
    assert width_px == round(tok.W_FULL * tok.FIG_DPI)
