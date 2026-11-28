"""The expand-all button on every report page type, and the "Salinity (PSU)" labels.

Pages are built from the committed fixtures: the dune2 mooring (deployment, stack,
grid and instrument pages, and an array page listing it) and the egM2 2016 and 2018
deployments of OSNAP East (the Stack cat and Grid cat pages of the site).
"""

from __future__ import annotations

import contextlib
import io
import re
import warnings
from pathlib import Path

import pytest
import xarray as xr

from oceanarray.reports._array import generate_array_report

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "proc"
_DUNE2 = "dune2_1_2026"
_EGM2 = ("egM2_2016", "egM2_2018")

#: The button as rendered: a real <button>, collapsed state, hidden until the
#: page script shows it.
_BUTTON = re.compile(
    r'<button type="button" class="expand-all" aria-expanded="false" hidden>'
    r"Expand all</button>"
)


def _quiet(fn, *args, **kwargs):
    """Call *fn* with warnings and stdout silenced."""
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


@pytest.fixture(scope="module")
def pages(tmp_path_factory, dune2_pages, egm2_site) -> dict[str, str]:
    """Every report page type, rendered once, keyed by page type.

    Returns
    -------
    dict of str to str
        HTML of the deployment, stack, grid, instrument, array, Stack cat and
        Grid cat pages.
    """
    out: dict[str, str] = {}
    dune2 = dune2_pages
    out["deployment"] = (dune2 / f"{_DUNE2}_report.html").read_text(encoding="utf-8")
    out["stack"] = (dune2 / f"{_DUNE2}_stack_report.html").read_text(encoding="utf-8")
    out["grid"] = (dune2 / f"{_DUNE2}_grid_report.html").read_text(encoding="utf-8")
    out["instrument"] = (dune2 / "instrument" / f"{_DUNE2}_2941_report.html").read_text(
        encoding="utf-8"
    )

    array_dir = tmp_path_factory.mktemp("array_page")
    array_path = array_dir / "DUNE.array.yaml"
    config = _FIX / _DUNE2 / f"{_DUNE2}.mooring.yaml"
    array_path.write_text(
        "name: DUNE\nmoorings:\n"
        f"- position: '1'\n  mooring: {_DUNE2}\n  config: {config}\n",
        encoding="utf-8",
    )
    page = _quiet(
        generate_array_report,
        array_path,
        _FIX,
        out_path=array_dir / "DUNE_array_report.html",
        force=True,
    )
    out["array"] = Path(page).read_text(encoding="utf-8")

    report = egm2_site / "report"
    out["stackcat"] = (report / "egM2_stackcat_report.html").read_text(encoding="utf-8")
    out["gridcat"] = (report / "egM2_gridcat_report.html").read_text(encoding="utf-8")
    return out


_PAGE_TYPES = (
    "deployment",
    "stack",
    "grid",
    "instrument",
    "array",
    "stackcat",
    "gridcat",
)


@pytest.mark.parametrize("kind", _PAGE_TYPES)
def test_every_page_type_has_one_expand_all_button(pages, kind):
    """One button in a bar right under the jump-nav, with its script and print rules."""
    html = pages[kind]
    assert len(_BUTTON.findall(html)) == 1
    nav_end = html.index("</nav>", html.index('<nav class="jump-nav">'))
    bar = html.index('class="expand-all-bar"')
    assert 0 < bar - nav_end < 120, "button bar under the jump-nav"
    # The mooring-description line, when present, sits in the bar left of the button.
    if '<p class="see-also">' in html:
        assert (
            bar < html.index('<p class="see-also">') < html.index('class="expand-all"')
        )
    # Toggles every <details>, opens them all for printing and restores them after.
    assert "querySelectorAll('details')" in html
    assert "beforeprint" in html and "afterprint" in html
    assert "aria-expanded" in html and "Collapse all" in html
    # The button itself never prints.
    assert re.search(r"@media print \{\s*button\.expand-all \{ display:none; \}", html)


@pytest.mark.parametrize("kind", _PAGE_TYPES)
def test_no_page_says_practical_salinity_outside_the_file_attributes(pages, kind):
    """Salinity is "Salinity (PSU)"; only the NetCDF attribute tables quote the file."""
    text = re.sub(r"<td[^>]*>.*?</td>", "", pages[kind], flags=re.S)
    assert "ractical salinity" not in text


def test_grid_page_captions_say_salinity_psu(pages):
    """Hydrography, T-S and density captions name salinity (PSU)."""
    captions = re.findall(r'<p class="caption">(.*?)</p>', pages["grid"])
    with_salinity = [c for c in captions if "salinity" in c.lower()]
    assert any(c.startswith("Temperature and salinity (PSU)") for c in captions)
    assert any("against salinity (PSU)" in c for c in captions)
    assert sum("potential temperature and salinity (PSU)" in c for c in captions) == 2
    assert all("ractical" not in c for c in with_salinity)


def test_grid_hydrography_panel_title_is_salinity_psu():
    """The grid salinity section is titled "Salinity (PSU)"."""
    from oceanarray.plotters.timeseries import draw_grid_hydro

    with xr.open_dataset(_FIX / _DUNE2 / f"{_DUNE2}_grid.nc", engine="netcdf4") as ds:
        fig = draw_grid_hydro(ds.load())
    titles = [ax.get_title(loc="left") for ax in fig.axes]
    assert "Salinity (PSU)" in titles
    assert not any("ractical" in t for t in titles)


def test_stack_ts_axis_is_salinity_psu():
    """The stack T-S diagram's x-axis reads "Salinity (PSU)"."""
    from oceanarray.plotters.ts import draw_stack_ts_diagram

    with xr.open_dataset(_FIX / _DUNE2 / f"{_DUNE2}_stack.nc", engine="netcdf4") as ds:
        fig = _quiet(draw_stack_ts_diagram, ds.load())
    assert fig.axes[0].get_xlabel() == "Salinity (PSU)"
