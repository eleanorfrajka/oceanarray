"""The instrument page for a reader who did not run the pipeline, on real originator files.

Real data from ``tests/fixtures/raw/osnap``: the egM2 2018 MicroCAT s/n 14644 (the
originator's flags in ``<CODE>_flag``: 2 bad and 12 value-changed salinities) and the
CF7 2020 current meter s/n 15637 (no originator flags; the velocity ``comment`` says
how it was rotated; ``resolution`` attributes on every variable).
"""

from __future__ import annotations

import html as html_lib
import re
import shutil
from pathlib import Path

import pytest
import xarray as xr
import yaml

import oceanarray as oa
from oceanarray.reports import MooringReport, _instrument_text
from oceanarray.reports._html_helpers import _build_history_tables, _parse_history

pytest.importorskip("seasenselib", reason="seasenselib not installed")

OSNAP = Path(__file__).parent.parent / "fixtures" / "raw" / "osnap"

MOORINGS = {
    "egM2_2018": (
        {
            "waterdepth": 2436.0,
            "latitude": 59.857,
            "longitude": -40.6957,
            "deployment_time": "2019-01-04T05:00:00",
            "recovery_time": "2019-01-06T05:00:00",
        },
        {
            "serial": "14644",
            "instrument": "microcat",
            "file_type": "oceansites",
            "filename": "M2_MC_2018_2020_SN14644_2190m_cut.nc",
            "nominal_depth": 2190,
            "hab": 246.0,
        },
    ),
    "CF7_2020": (
        {
            "waterdepth": 1000,
            "latitude": 59.92,
            "longitude": -41.44,
            "deployment_time": "2020-07-12T14:00:00",
            "recovery_time": "2020-07-22T15:00:00",
        },
        {
            "serial": "15637",
            "instrument": "currentmeter",
            "file_type": "oceansites",
            "filename": "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc",
            "nominal_depth": 500,
            "hab": 500.0,
        },
    ),
}


@pytest.fixture(scope="module")
def pages(tmp_path_factory) -> dict[str, tuple[str, xr.Dataset]]:
    """Run each mooring through oceanarray; return its instrument page and stage-3 file."""
    tmp = tmp_path_factory.mktemp("instrument_page")
    out: dict[str, tuple[str, xr.Dataset]] = {}
    for name, (moor, inst) in MOORINGS.items():
        raw = tmp / "raw" / name / inst["instrument"]
        raw.mkdir(parents=True)
        shutil.copy(OSNAP / inst["filename"], raw)
        proc = tmp / "proc" / name
        proc.mkdir(parents=True)
        cfg = {"name": name, **moor, "stack": {"dt_seconds": 3600}, "clamp": [inst]}
        mooring_path = proc / f"{name}.mooring.yaml"
        mooring_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
        oa.run(
            mooring_path,
            oa.Paths(raw_dir=tmp / "raw", proc_dir=tmp / "proc"),
            pmin=400,
            pmax=2300,
            dp=50,
        )
        report = tmp / "report" / name
        MooringReport(proc_dir=str(tmp / "proc")).generate(
            name, force=True, outdir=str(report), instruments=True
        )
        page = report / "instrument" / f"{name}_{inst['serial']}_report.html"
        stage3 = proc / inst["instrument"] / f"{name}_{inst['serial']}_stage3.nc"
        with xr.open_dataset(stage3, engine="netcdf4") as ds:
            out[name] = (page.read_text(encoding="utf-8"), ds.load())
    return out


def _text(html: str) -> str:
    """Return the page's text with tags removed and whitespace folded."""
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html)).replace("\u2009", " ")
    return re.sub(r"\s+", " ", text)


def test_history_entries_read_back_whole(pages):
    """No stage note splits on the separator: every dated entry is one event row or more."""
    for _html, ds in pages.values():
        header, events = _build_history_tables(_parse_history(ds.attrs["history"]))
        # Header rows come only from undated reader items, never a split note.
        assert all(
            label in ("Processed", "Reader", "Format", "Source file", "Stages")
            for label, _v, _k in header
        ), header
        texts = [ev for _n, _t, ev in events]
        assert not any(t.startswith(("0 warnings", "(ours")) for t in texts)
    _html, egm2 = pages["egM2_2018"]
    rows = [
        ev
        for _n, _t, ev in _build_history_tables(_parse_history(egm2.attrs["history"]))[
            1
        ]
    ]
    assert any(
        r.startswith("salinity recomputed from C, T, P, the originator's kept")
        for r in rows
    )
    assert "salinity: 1->1 (172), 4->4 (2), 5->5 (12), 9->9 (4)" in rows


def test_header_names_the_instrument_and_the_originators_file(pages):
    html, _ds = pages["egM2_2018"]
    assert "<title>MicroCAT 14644 · 2190 m</title>" in html
    text = _text(html)
    assert "Originator's file M2_MC_2018_2020_SN14644_2190m_cut.nc" in text
    assert "Nominal depth 2190 m" in text and "Sampling interval 15 min" in text
    assert "Source file" not in text and "stage3.nc" not in text.split("Jump to")[0]


def test_quality_flags_read_the_originators_flags(pages):
    """The 2 bad and 12 value-changed salinities of the originator appear."""
    html, _ds = pages["egM2_2018"]
    section = html[html.index('id="qc"') : html.index('id="processing"')]
    assert "Quality flags" in html and "Flags by variable" in section
    assert ">Originator<" in section and ">oceanarray<" in section
    text = _text(section)
    # Good % (amber: 80-95 %) beside the classes, for each flag set.
    assert re.search(r"salinity 186 92\.5 1\.1 6\.5 100", text), text
    assert "Good % (values flagged good or interpolated): green ≥ 95 %" in text
    assert "QC test limits" in section and "stage" not in text.lower()


def test_figure_markers_are_described_from_the_file(pages):
    html, _ds = pages["egM2_2018"]
    text = _text(html)
    assert (
        "salinity (PSU, recomputed from C, T, P). Values flagged by the originator or "
        "oceanarray: crosses bad (2)." in text
    )
    assert "Potential temperature against salinity (PSU)." in text


def test_processing_section_holds_the_record(pages):
    html, _ds = pages["egM2_2018"]
    section = html[html.index('id="processing"') :]
    for summary in (
        "Files used to build this page",
        "As recorded in the file's history attribute",
        "Start and end of the record",
        "Data value distributions",
    ):
        assert f"<summary>{summary}</summary>" in section
    assert "Originator's data" in section and ">Raw<" not in section
    assert '<details class="processing" id="start">' in section


def test_current_meter_direction_and_resolution_come_from_the_file(pages):
    html, ds = pages["CF7_2020"]
    text = _text(html)
    assert "<title>Current meter 15637 · 500 m</title>" in html
    assert (
        "as the originator describes them: “data have been corrected for in-situ sound "
        "velocity and rotated relatvie to true north”" in text
    )
    assert "decl = ?" not in text
    assert "The originator's file carries no quality flags." in text
    assert "(originator attribute)" in text and "east velocity to 0.001 m/s" in text
    assert _instrument_text.direction_clause(ds).startswith(
        "as the originator describes"
    )


def test_a_file_without_resolution_says_nothing_about_it(pages):
    _html, ds = pages["egM2_2018"]
    assert _instrument_text.resolution_sentence(ds, ["temperature", "salinity"]) == ""


def test_no_declination_warning_for_the_originators_true_north(pages):
    """CF7 15637 arrived in true north: the rose says so and warns of nothing."""
    html, ds = pages["CF7_2020"]
    assert "originator_magnetic_declination" in ds.attrs
    assert "declination was not applied" not in html
    assert "latitude/longitude are missing" not in html


def test_the_instrument_warning_says_why_declination_is_missing():
    """dune2 9920 (oceanarray applied it): a warning only with no position at stage 3."""
    from oceanarray.reports._instrument import _declination_unapplied_reason

    path = (
        Path(__file__).parent.parent
        / "fixtures/proc/dune2_1_2026/aquadopp/dune2_1_2026_9920_stage3.nc"
    )
    with xr.open_dataset(path, engine="netcdf4") as ds:
        ds = ds.load()
    assert _declination_unapplied_reason(ds) == ""
    ds.attrs["magnetic_declination"] = "UNK"
    assert _declination_unapplied_reason(ds) == "no mooring position at stage 3"
    assert _declination_unapplied_reason(None) == ""


#: The 14644 page as committed, made stable (see :func:`_stable`).
_GOLDEN = (
    Path(__file__).parent.parent
    / "fixtures"
    / "golden"
    / "osnap"
    / "egM2_2018_14644_report.html"
)


def _stable(html: str) -> str:
    """Return the page with what a rerun changes masked: run times, versions, file hashes and sizes."""
    from unit.test_report_golden import _normalise

    html = _normalise(html)
    from datetime import UTC, datetime

    # The page was built moments ago: today's times are the run's, not the data's.
    today = re.escape(datetime.now(UTC).strftime("%Y-%m-%d"))
    html = re.sub(today + r"[ T]\d\d:\d\d(:\d\d)?(\.\d+)?Z?", "<WHEN>", html)
    html = re.sub(
        r"(_version</td><td><code>string</code></td><td>)[^<]+", r"\1<V>", html
    )
    # Stage files are rewritten each run; their size moves by a few hundred bytes.
    html = re.sub(r'(<td class="num">)[\d.,]+ (?:B|KB|MB)(</td>)', r"\1<SIZE>\2", html)
    return re.sub(
        r"(stage\d_source_sha256</td><td><code>string</code></td><td>)[0-9a-f]+",
        r"\1<SHA>",
        html,
    )


def test_the_14644_page_matches_its_golden(pages):
    """The MicroCAT 14644 page, from the originator's file, as committed."""
    import os

    produced = _stable(pages["egM2_2018"][0])
    if os.environ.get("REBASELINE_GOLDEN"):
        assert not os.environ.get("CI"), "REBASELINE_GOLDEN must not be set in CI"
        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text(produced, encoding="utf-8")
        pytest.skip("re-baselined the 14644 golden")
    assert produced == _GOLDEN.read_text(encoding="utf-8")


def test_the_deployment_qc_summary_reads_the_originators_flags(pages, tmp_path):
    """14644's originator flags (2 bad, 12 value-changed salinities) reach the summary."""
    from oceanarray.reports._read import _read_qc_summary

    path = tmp_path / "egM2_2018_14644_stage3.nc"
    oa.write(pages["egM2_2018"][1], path)
    rows = {(r["var"], r["by"]): r for r in _read_qc_summary(path)}
    theirs = rows[("salinity", "originator")]
    counts = {f["flag"]: f["n"] for f in theirs["flags"]}
    assert counts[4] == 2 and counts[5] == 12
    assert ("salinity", "oceanarray") in rows


def test_character_flags_are_read_as_numbers_and_letters_left_out(
    pages, tmp_path, caplog
):
    """14644's flags written as characters, as originator (BODC-style) files have them.

    No committed fixture has character flags: the mapped originator flags are
    converted to digit characters and oceanarray's to letters, in a copy.
    """
    import logging

    import numpy as np

    from oceanarray.reports._read import _read_qc_summary

    ds = pages["egM2_2018"][1].copy()
    mapped = ds["salinity_qc_originator_mapped"]
    ours = ds["salinity_qc"]
    ds["salinity_qc_originator_mapped"] = (
        mapped.dims,
        mapped.values.astype(int).astype("S1"),
        mapped.attrs,
    )
    ds["salinity_qc"] = (ours.dims, np.full(ours.shape, b"A"), ours.attrs)
    path = tmp_path / "egM2_2018_14644_char_flags.nc"
    ds.to_netcdf(path, engine="netcdf4")
    with caplog.at_level(logging.WARNING, logger="oceanarray.reports._read"):
        rows = {(r["var"], r["by"]): r for r in _read_qc_summary(path)}
    counts = {f["flag"]: f["n"] for f in rows[("salinity", "originator")]["flags"]}
    assert counts[4] == 2 and counts[5] == 12
    assert ("salinity", "oceanarray") not in rows
    assert "salinity_qc flags are not all digits" in caplog.text
