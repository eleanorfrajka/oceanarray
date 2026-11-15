"""The per-mooring run summary dedupes warnings with counts and writes JSON."""

import json
import logging

from oceanarray.pipeline.summary import RunSummary


def test_run_summary_dedupes_counts_and_round_trips(tmp_path):
    summary = RunSummary()
    pkg = logging.getLogger("oceanarray")
    pkg.addHandler(summary)
    try:
        child = logging.getLogger("oceanarray.pipeline.stack")
        child.warning("thermistor MT60 is not a known type")
        child.warning("thermistor MT60 is not a known type")
        child.warning("aquadopp 9920 samples every 120 s")
        child.info("this info line is below WARNING and is not counted")
    finally:
        pkg.removeHandler(summary)

    assert summary.total == 3  # the info line is not counted
    counts = {e["message"]: e["count"] for e in summary.entries()}
    assert counts == {
        "thermistor MT60 is not a known type": 2,
        "aquadopp 9920 samples every 120 s": 1,
    }

    path = summary.finish("egM2_2018", tmp_path)
    assert path == tmp_path / "processing_logs" / "egM2_2018_summary.json"
    data = json.loads(path.read_text())
    assert data["mooring"] == "egM2_2018"
    assert data["n_warnings"] == 3
    assert {w["message"]: w["count"] for w in data["warnings"]} == counts


def test_run_summary_with_no_warnings_writes_empty(tmp_path):
    summary = RunSummary()
    path = summary.finish("dune2_1_2026", tmp_path)
    data = json.loads(path.read_text())
    assert data == {"mooring": "dune2_1_2026", "n_warnings": 0, "warnings": []}
