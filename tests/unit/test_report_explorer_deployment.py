"""The deployment report as read by someone who did not run the pipeline.

Checks on the dune2 fixture (real instrument data): the instrument summary leads,
tables carry nominal depth and HAB, the QC summary lists only variables with
something not flagged good, the knockdown figures compare pressure with pressure,
and the mooring description closes the page.
"""

from __future__ import annotations

import pathlib

import gsw
import numpy as np
import pytest
import xarray as xr

from oceanarray.analysis.derived import knockdown
from oceanarray.location import parse_coordinate
from oceanarray.pipeline.pressure import pressure_from_depth
from oceanarray.reports._html_helpers import _format_seconds
from oceanarray.reports._mooring import SKIP_NO_REASON, _trim_note

_PROC = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "proc"
_MOORING = "dune2_1_2026"


@pytest.fixture(scope="module")
def page(dune2_pages) -> str:
    """The rendered dune2 deployment page."""
    return (dune2_pages / f"{_MOORING}_report.html").read_text(encoding="utf-8")


def test_instrument_summary_leads_and_description_closes(page: str) -> None:
    """The first section is the instrument summary; the YAML box is the last one."""
    first = page.index('id="instruments"')
    assert first < page.index('id="pipeline"') < page.index('id="timing"')
    assert page.index('id="description"') > page.index('id="issues"')
    assert "dune2_1_2026.mooring.yaml" in page and 'class="yaml-box"' in page


def test_tables_identify_instruments_by_nominal_depth_and_hab(page: str) -> None:
    """Instrument tables carry Nominal depth and HAB, units on their own line."""
    assert 'Nominal depth<span class="th-unit">m</span>' in page
    assert 'HAB<span class="th-unit">m</span>' in page
    assert "HAB: height above bottom" in page
    assert "Hab&nbsp;(m)" not in page


def test_processing_steps_are_collapsed(page: str) -> None:
    """The per-instrument processing table sits in a closed <details>."""
    assert '<details class="processing"><summary>' in page
    assert (
        "--pmin"
        in page.split('<details class="processing">', 1)[1].split("</details>", 1)[0]
    )


def test_qc_summary_lists_only_variables_not_all_good(page: str) -> None:
    """Every QC row has a flag other than good; the rest are counted in one line."""
    assert "All other variables (" in page
    assert (
        "<code>temperature</code>"
        not in page.split('id="qc"', 1)[1].split('id="knockdown"', 1)[0]
    )


def test_footer_links_the_documentation_without_cruise_or_config(page: str) -> None:
    """The footer names the package (linked) and the time, nothing else."""
    footer = page.split('<div class="report-footer">', 1)[1].split("</div>", 1)[0]
    assert "ocean-uhh.github.io/oceanarray" in footer
    assert "config:" not in footer


def test_durations_and_trim_note() -> None:
    """Durations read as hours and minutes; a cut is reported per end."""
    assert [_format_seconds(s) for s in (3600, 900, 9000, 30)] == [
        "1 h",
        "15 min",
        "2 h 30 min",
        "30 s",
    ]
    assert _format_seconds(2 * 86400 + 6 * 3600) == "2.2 days"
    assert _format_seconds(float("nan")) == "—"
    timing = {
        "stage1_start": "2014-08-10 00:00",
        "stage2_start": "2014-08-10 12:00",
        "stage1_end": "2015-07-21 12:00",
        "stage2_end": "2015-07-21 12:00",
    }
    assert _trim_note(timing) == "12 h at start"
    assert _trim_note({}) == ""
    assert SKIP_NO_REASON == "skipped (no reason provided in YAML)"


def test_knockdown_design_depth_is_compared_as_pressure() -> None:
    """The knockdown figures use the design depth converted to dbar, not metres."""
    with xr.open_dataset(
        _PROC / _MOORING / f"{_MOORING}_stack.nc", engine="netcdf4"
    ) as ds:
        waterdepth = float(ds.attrs["waterdepth"])
        kd = knockdown(ds)
        lat = parse_coordinate(ds.attrs["latitude"])  # stored as text, e.g. "65 43.913"
    assert kd is not None and kd.records
    assert kd.latitude == pytest.approx(lat)
    for p_nom, _serial, _p, hab in kd.records:
        expected = float(gsw.p_from_z(-(waterdepth - hab), lat))
        assert p_nom == pytest.approx(expected)
        assert p_nom > waterdepth - hab  # dbar exceed metres at depth
    assert pressure_from_depth(2000.0, 60.0) == pytest.approx(2028.8, abs=0.5)
    assert np.isfinite(lat)


def test_every_flag_value_has_a_reader_word() -> None:
    """The QC tables and legends have a word for every flag in the central table."""
    from oceanarray.config import parameters as params
    from oceanarray.reports._read import _QC_LABELS

    assert set(_QC_LABELS) == set(params.QC_FLAG_VALUES)
    assert all(_QC_LABELS[f] for f in params.QC_FLAG_VALUES)
    assert _QC_LABELS[0] == "no QC" and _QC_LABELS[3] == "suspect"


def _qc_instrument(path: pathlib.Path, serial: str) -> dict:
    """One instrument record for the QC table, from a real stage-3 file."""
    from oceanarray.reports._read import _read_qc_summary

    return {
        "serial": serial,
        "depth": None,
        "hab": None,
        "type_label": "MicroCAT",
        "qc_summary": _read_qc_summary(path),
    }


def test_qc_single_flag_variables_are_one_line_each(tmp_path) -> None:
    """A variable never checked (all 0) is a line, not a row; all interpolated (8) is good.

    The MicroCAT 2941 file is real; its pressure is interpolated from the
    neighbouring instrument throughout (flag 8), which counts as good.  To exercise
    the not-checked line its temperature flags are set to 0, as an originator file
    without flags reads.
    """
    from oceanarray.reports._mooring import _qc_table

    src = _PROC / _MOORING / "microcat" / f"{_MOORING}_2941_stage3.nc"
    with xr.open_dataset(src, engine="netcdf4") as ds:
        ds = ds.load()
    ds["temperature_qc"][:] = 0
    unchecked = tmp_path / src.name
    ds.to_netcdf(unchecked, engine="netcdf4")

    qc = _qc_table([_qc_instrument(unchecked, "2941")])
    assert qc["rows"] == []
    lines = {line["label"]: line for line in qc["lines"]}
    assert lines["Not checked"]["names"] == ["temperature (2941)"]
    assert set(lines) == {"Not checked"}
    # pressure (all 8), conductivity, salinity; flag 0 is never counted as good
    assert qc["n_good"] == 3

    html = __import__("oceanarray.reports._env", fromlist=["x"]).render_template(
        "_mooring_qc.html", qc=qc
    )
    assert "Not checked (1): temperature (2941)." in html
    assert "All other variables (3): 100&nbsp;% good." in html


def test_qc_value_changed_rows_read_the_reader_word(tmp_path) -> None:
    """Flag 5 (an originator's QC changed the value) is counted and read "value changed".

    The MicroCAT 2941 file is real; a tenth of its temperature flags are set to 5,
    as an imported file whose originator adjusted those values reads.
    """
    from oceanarray.reports._env import render_template
    from oceanarray.reports._mooring import _qc_table

    src = _PROC / _MOORING / "microcat" / f"{_MOORING}_2941_stage3.nc"
    with xr.open_dataset(src, engine="netcdf4") as ds:
        ds = ds.load()
    flags = ds["temperature_qc"].values
    flags[: flags.size // 10] = 5
    ds["temperature_qc"][:] = flags
    changed = tmp_path / src.name
    ds.to_netcdf(changed, engine="netcdf4")

    qc = _qc_table([_qc_instrument(changed, "2941")])
    (row,) = [r for r in qc["rows"] if r["var"] == "temperature"]
    assert 5 in row["pct"]
    assert {f["flag"]: f["label"] for f in qc["flags"]}[5] == "value changed"
    html = render_template("_mooring_qc.html", qc=qc)
    assert "value changed&nbsp;(5)" in html and "Value changed" in html


def test_unknown_latitude_warns_and_skips_the_design_pressure(caplog) -> None:
    """No position: one warning, no design pressure, the HAB figure without its line."""
    import logging

    from oceanarray.plotters.diagnostic import (
        draw_knockdown_anomaly,
        draw_knockdown_displacement,
        draw_knockdown_hab,
    )

    with xr.open_dataset(
        _PROC / _MOORING / f"{_MOORING}_stack.nc", engine="netcdf4"
    ) as ds:
        ds = ds.load()
    del ds.attrs["latitude"]
    with caplog.at_level(logging.WARNING, logger="oceanarray.analysis.derived"):
        kd = knockdown(ds)
    assert kd is not None and kd.records and all(np.isnan(r.p_nom) for r in kd.records)
    assert caplog.text.count("latitude unknown") == 1
    assert draw_knockdown_anomaly(ds, knockdown=kd) is None
    assert draw_knockdown_displacement(ds, knockdown=kd) is None
    fig = draw_knockdown_hab(ds, knockdown=kd)
    assert fig is not None
    assert not fig.axes[0].get_lines() or all(
        ln.get_label() != "expected pressure" for ln in fig.axes[0].get_lines()
    )


def test_hab_axis_stops_at_the_water_depth() -> None:
    """The HAB axis (and so the reference line) never reaches above the sea surface."""
    from oceanarray.plotters.diagnostic import draw_knockdown_hab

    with xr.open_dataset(
        _PROC / _MOORING / f"{_MOORING}_stack.nc", engine="netcdf4"
    ) as ds:
        ds = ds.load()
    waterdepth = float(ds.attrs["waterdepth"])
    fig = draw_knockdown_hab(ds)
    assert fig is not None
    assert fig.axes[0].get_xlim()[1] <= waterdepth


def test_figure_source_is_read_when_the_page_renders(monkeypatch) -> None:
    """Setting OCEANARRAY_FIGURE_SOURCE after import still switches the source line."""
    from oceanarray.reports._env import _ENV

    tmpl = _ENV.from_string("{% if show_source %}on{% else %}off{% endif %}")
    monkeypatch.setenv("OCEANARRAY_FIGURE_SOURCE", "0")
    assert tmpl.render() == "off"
    monkeypatch.setenv("OCEANARRAY_FIGURE_SOURCE", "1")
    assert tmpl.render() == "on"


def test_stack_and_grid_pages_link_the_mooring_description(
    page: str, dune2_pages
) -> None:
    """The stack and grid pages link the deployment page's description near the top."""
    pages = [dune2_pages / f"{_MOORING}_{k}_report.html" for k in ("stack", "grid")]
    href = f'href="{_MOORING}_report.html#description"'
    for p in pages:
        html = p.read_text(encoding="utf-8")
        assert href in html
        # On the expand-all line under the jump-nav, just left of the button.
        assert (
            html.index('class="jump-nav"')
            < html.index(href)
            < html.index('class="expand-all"')
        )
    assert 'id="description"' in page


def test_pipeline_regrid_command_is_runnable(page: str) -> None:
    """The copyable regrid command names the description and parses as ``oceanarray run``."""
    import html as _html
    import re
    import shlex

    from oceanarray.cli import build_parser

    m = re.search(r"cursor:text\">(oceanarray run [^<]+)</pre>", page)
    assert m, "regrid command not on the page"
    argv = shlex.split(_html.unescape(m.group(1)))[1:]
    assert argv[1] == f"{_MOORING}.mooring.yaml"
    args = build_parser().parse_args(argv)
    assert args.from_stage == 3  # no raw files needed
    assert args.pmin is not None and args.pmax is not None


def test_every_known_instrument_type_has_a_readable_label() -> None:
    """No known instrument type falls back to the upper-cased code ("THERMISTOR")."""
    from oceanarray.config import parameters as params
    from oceanarray.reports._navs import _TYPE_LABELS

    missing = sorted(
        t for t in params.KNOWN_INSTRUMENT_TYPES if t.lower() not in _TYPE_LABELS
    )
    assert not missing, missing


def test_good_share_counts_interpolated_as_good_and_zero_as_not_checked() -> None:
    """MicroCAT 2941: interpolated pressure (all 8) is a green 100; all-0 is not checked."""
    from oceanarray.reports._qcflags import flag_counts, good_share

    src = _PROC / _MOORING / "microcat" / f"{_MOORING}_2941_stage3.nc"
    with xr.open_dataset(src, engine="netcdf4") as ds:
        flags = ds["pressure_qc"].values
    counts = flag_counts(flags)
    assert set(counts) == {8}
    good = good_share(counts, flags.size)
    assert (good["text"], good["color"]) == ("100", "var(--good)")
    assert good_share({0: flags.size}, flags.size)["text"] == "not checked"


def test_qc_table_has_one_good_column(page: str) -> None:
    """The explicit Good % column is not repeated by the per-flag columns."""
    import re

    table = page.split('id="qc"', 1)[-1].split("</table>", 1)[0]
    heads = re.findall(r"<th[^>]*>(.*?)</th>", table, flags=re.S)
    assert (
        sum(1 for h in heads if re.sub("<[^>]+>", "", h).strip().startswith("Good"))
        == 1
    )
