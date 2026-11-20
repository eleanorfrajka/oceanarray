"""Tests for the shared masthead facts of the mooring, stack and grid report pages."""

from pathlib import Path

import yaml

from oceanarray.instruments import instrument_entries
from oceanarray.reports import _masthead
from oceanarray.reports._masthead import (
    common_facts,
    cruise_label,
    format_instrument_counts,
    format_position,
    masthead_facts,
)

_DUNE2 = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "proc"
    / "dune2_1_2026"
    / "dune2_1_2026.mooring.yaml"
)

#: The OSNAP RODB mooring description (``tests/fixtures/raw/osnap/run_example.sh``).
_RTWB = {
    "name": "rtwb1_01_2014",
    "waterdepth": 1600,
    "latitude": "57 28.240 N",
    "longitude": "012 42.300 W",
    "deployment_time": "2014-07-17T12:00",
    "recovery_time": "2015-06-22T12:00",
    "clamp": [
        {"serial": 11335, "instrument": "microcat", "hab": 1500},
        {"serial": 11021, "instrument": "currentmeter", "hab": 1505},
    ],
}


def _dune2() -> dict:
    return yaml.safe_load(_DUNE2.read_text())


def _as_dict(facts):
    return {label: (str(value), miss) for label, value, miss in facts}


def test_dune2_common_facts_in_order():
    cfg = _dune2()
    facts = common_facts(cfg, instrument_entries(cfg))
    assert [str(f[0]) for f in facts] == [
        "Deployment cruise",
        "Deployment ship",
        "Recovery cruise",
        "Recovery ship",
        "Latitude",
        "Longitude",
        "Water depth",
        "Deployment",
        "Recovery",
        "Duration",
        "Instruments",
    ]
    d = _as_dict(facts)
    # The legacy single cruise/ship keys are the deployment values; the fixture also
    # gives recovery_cruise/recovery_ship (same cruise), so recovery is not UNK.
    assert d["Deployment cruise"] == ("OdB", False)
    assert d["Deployment ship"] == ("Odon de Buen", False)
    assert d["Recovery cruise"] == ("OdB", False)
    assert d["Recovery ship"] == ("Odon de Buen", False)
    assert d["Latitude"] == ("65° 43.91′ N", False)
    assert d["Longitude"] == ("027° 48.00′ W", False)
    assert d["Water depth"] == ("745&thinsp;m", False)
    assert d["Duration"] == ("13.6 days", False)
    assert d["Instruments"] == ("2 (MicroCAT 1 · Aquadopp 1)", False)


def test_rodb_mooring_recovery_cruise_and_ship_unknown():
    d = _as_dict(common_facts(_RTWB, instrument_entries(_RTWB)))
    assert d["Deployment cruise"] == ("—", True)
    assert d["Deployment ship"] == ("—", True)
    assert d["Recovery cruise"] == ("UNK", True)
    assert d["Recovery ship"] == ("UNK", True)
    assert d["Duration"] == ("11.2 months", False)
    assert d["Latitude"] == ("57° 28.24′ N", False)


def test_separate_recovery_cruise_shown_on_its_own():
    cfg = {**_dune2(), "recovery_cruise": "MSM153"}
    d = _as_dict(common_facts(cfg, instrument_entries(cfg)))
    assert d["Deployment cruise"] == ("OdB", False)
    assert d["Recovery cruise"] == ("MSM153", False)
    assert cruise_label(cfg) == "OdB / MSM153"
    assert cruise_label(_dune2()) == "OdB"


def test_instrument_counts_by_type():
    assert format_instrument_counts(instrument_entries(_dune2())) == (
        "2 (MicroCAT 1 · Aquadopp 1)"
    )
    assert format_instrument_counts(instrument_entries(_RTWB)) == (
        "2 (MicroCAT 1 · Current meter 1)"
    )


def test_instrument_counts_note_skipped_entries():
    cfg = _dune2()
    cfg["clamp"][1]["skip"] = True
    assert format_instrument_counts(instrument_entries(cfg)) == (
        "1 (MicroCAT 1) · 1 skipped"
    )


def test_unparseable_position_is_missing():
    assert format_position({"latitude": "unknown", "longitude": "unknown"}) == (
        "—",
        "—",
    )


def test_mooring_stack_grid_share_the_common_facts():
    cfg = _dune2()
    common = common_facts(cfg, instrument_entries(cfg))
    mooring = masthead_facts("mooring", common)
    stack = masthead_facts(
        "stack",
        common,
        {_masthead.DT_S: 120, _masthead.N_TIME: 9, _masthead.NC_FILE: "s.nc"},
    )
    grid = masthead_facts(
        "grid", common, {_masthead.N_TIME: 9, _masthead.NC_FILE: "g.nc"}
    )
    n = len(common)
    assert mooring == common
    assert stack[:n] == common
    assert grid[:n] == common
    assert [str(f[0]) for f in stack[n:]] == [
        "Samp.&nbsp;&Delta;t",
        "Records",
        "Source&nbsp;file",
    ]
    assert str(stack[n][1]) == "120&thinsp;s"
    # A grid fact not given reads as missing.
    assert _as_dict(grid)["Grid&nbsp;&Delta;P"] == ("—", True)


def test_nan_position_reads_missing_not_a_crash():
    cfg = {**_dune2(), "latitude": "nan"}
    assert format_position(cfg) == ("—", "—")
    d = _as_dict(common_facts(cfg, instrument_entries(cfg)))
    assert d["Latitude"] == ("—", True)
    assert d["Longitude"] == ("—", True)


def test_page_extra_keys_match_the_keys_masthead_facts_reads(tmp_path, monkeypatch):
    """The keys the stack and grid pages pass are exactly the ones their facts read."""
    from oceanarray.reports import MooringReport

    passed: dict[str, set] = {}
    real = _masthead.masthead_facts

    def spy(page, common, extra=None):
        passed[page] = set(extra or {})
        return real(page, common, extra)

    monkeypatch.setattr(_masthead, "masthead_facts", spy)
    MooringReport(proc_dir=str(_DUNE2.parents[1])).generate(
        "dune2_1_2026",
        force=True,
        outdir=str(tmp_path),
        instruments=False,
        grid=True,
        stack=True,
    )
    assert set(passed) == {"mooring", "stack", "grid"}
    for page, keys in passed.items():
        assert keys == _masthead.page_fact_keys(page), page
