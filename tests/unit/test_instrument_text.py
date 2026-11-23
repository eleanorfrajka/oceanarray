"""The instrument page's generated text, read from the instrument's file.

Real data: the dune2 fixture's Aquadopp 9920 (its stage-1 file still holds the
recovery, its stage-3 file is trimmed) and the egM2 2018 current meter CM2000
(an OceanSITES import). The OOI provenance attributes are copied verbatim from an
OOI FLMA 2016 current-meter file cut by osnap-east's fetch script (no OOI file is
small enough to keep as a fixture).
"""

from __future__ import annotations

from pathlib import Path

import xarray as xr

from oceanarray.history import append_history
from oceanarray.mask_rules import resolve_mask_flags
from oceanarray.reports import _instrument_text as it
from oceanarray.reports._captions import rule_sentence
from oceanarray.reports._html_helpers import _build_history_tables, _parse_history

_PROC = Path(__file__).resolve().parents[1] / "fixtures" / "proc"
_DUNE2 = _PROC / "dune2_1_2026" / "aquadopp"
_EGM2_CM = (
    _PROC / "egM2_concat" / "egM2_2018" / "aquadopp" / "egM2_2018_CM2000_stage3.nc"
)

#: Provenance of an OOI cut, as the FLMA 2016 s/n 11669 stage file records it.
_OOI = {
    "originator_file": "OS_OOI-FLMA_201607_CM_SN11669_1700m.nc",
    "originator_id": 104106,
    "originator_source": (
        "https://erddap.dataexplorer.oceanobservatories.org/erddap/tabledap/"
        "ooi-gi03flma-ri000-00-velptb000"
    ),
    "originator_comment": (
        "Cut from the OOI ERDDAP record at the asset-management deployment times; "
        "unit spellings normalised, current-meter pressure rescaled to dbar, "
        "otherwise unchanged."
    ),
}


def _open(path: Path) -> xr.Dataset:
    with xr.open_dataset(path, engine="netcdf4") as ds:
        return ds.load()


def test_an_ooi_cut_is_named_by_its_erddap_dataset():
    who = it.originator_identity(_OOI)
    assert who.label == "Originator's dataset"
    assert who.name == "ooi-gi03flma-ri000-00-velptb000"
    assert who.href == (
        "https://erddap.dataexplorer.oceanobservatories.org/erddap/tabledap/"
        "ooi-gi03flma-ri000-00-velptb000.html"
    )
    assert who.cut_from == "ooi-gi03flma-ri000-00-velptb000"
    assert who.file == "OS_OOI-FLMA_201607_CM_SN11669_1700m.nc"


def test_a_file_archived_as_is_is_named_by_its_file():
    ds = _open(_EGM2_CM)
    who = it.originator_identity(ds.attrs)
    assert who.label == "Originator's file"
    assert who.name == "AQD_OSNAP-MM2_201806_CM_2000m.nc"
    assert who.cut_from is None
    doi = it.originator_identity({**ds.attrs, "source_doi": "10.1594/PANGAEA.896648"})
    assert doi.href == "https://doi.org/10.1594/PANGAEA.896648"


def test_own_instruments_have_no_originator():
    assert (
        it.originator_identity(_open(_DUNE2 / "dune2_1_2026_9920_stage3.nc").attrs)
        is None
    )


def test_the_direction_clause_never_says_decl_unknown():
    ds = _open(_EGM2_CM)
    assert it.direction_clause(ds) == "as provided by the originator"
    ds["east_velocity"].attrs["comment"] = (
        "Relative to true north: magnetic declination applied by OOI "
        "(VELPTMN-VLE/VLN Level 1 product)."
    )
    assert it.direction_clause(ds) == (
        "relative to true north: magnetic declination applied by OOI "
        "(VELPTMN-VLE/VLN Level 1 product)"
    )
    own = _open(_DUNE2 / "dune2_1_2026_9920_stage3.nc")
    assert "?" not in it.direction_clause(own)


def test_the_rule_sentence_names_who_flagged():
    rule = resolve_mask_flags(None)
    both = rule_sentence(rule, ["temperature"], ours=True, theirs=True)
    assert both == "Values flagged bad, by the originator or oceanarray, are left out."
    assert rule_sentence(rule, ["temperature"], ours=True, theirs=False) == (
        "Values flagged bad by oceanarray are left out."
    )
    # Suspect values are named only when the figure draws some.
    assert rule_sentence(rule, ["temperature"], theirs=False, suspect=12) == (
        "Values flagged bad by oceanarray are left out; suspect values are shown."
    )
    assert "suspect" not in rule_sentence(rule, ["temperature"], suspect=0)
    wide = resolve_mask_flags([3, 4, 9])
    assert rule_sentence(wide, ["salinity"], theirs=False) == (
        "Values flagged suspect or bad by oceanarray are left out."
    )
    split = resolve_mask_flags({"velocity": [3, 4]})
    assert "east velocity: suspect or bad" in rule_sentence(
        split, ["temperature", "east_velocity"]
    )


def test_coinciding_window_times_are_one_line():
    lines = it.window_lines(
        ("2026-07-29T12:10:30", "2026-08-02T09:00"),
        ("2026-07-29T12:10", "2026-08-02T10:00"),
    )
    labels = [label for _t, _c, label in lines]
    assert labels == ["Description = suggested", "Suggested", "Description"]


def test_a_disturbed_end_is_found_in_the_pressure_record():
    """The stage-1 file still holds the recovery; the trimmed stage-3 file does not."""
    raw = _open(_DUNE2 / "dune2_1_2026_9920_stage1.nc")
    note = it.disturbed_end(raw)
    assert note.startswith("The last 6 h look disturbed: pressure changes by")
    assert note.endswith("The originator's end time is kept.")
    assert it.disturbed_end(_open(_DUNE2 / "dune2_1_2026_9920_stage3.nc")) == ""


def test_append_history_keeps_a_note_one_entry():
    ds = _open(_EGM2_CM)
    ds.attrs["history"] = "2026-11-15T13:07Z: processing_stage 1"
    append_history(ds, "people: from the description (2 PI; 1 warning)")
    entries = _parse_history(ds.attrs["history"])
    assert [e["text"] for e in entries] == [
        "processing_stage 1",
        "people: from the description (2 PI, 1 warning)",
    ]
    _header, events = _build_history_tables(entries)
    assert len(events) == 2


def test_the_flag_table_counts_each_set():
    ds = _open(_EGM2_CM)
    table = it.flag_table(ds)
    assert [g["heading"] for g in table.groups] == ["oceanarray"]
    assert table.lines[0] == "The originator's file carries no quality flags."


def test_a_ts_figure_without_theta_says_so_in_its_caption():
    """The figure's record decides the caption: in-situ temperature, no σθ, and why."""
    from oceanarray.reports._captions import stack_captions

    with xr.open_dataset(
        _PROC / "dune2_1_2026" / "dune2_1_2026_stack.nc", engine="netcdf4"
    ) as ds:
        stack = ds.load()
    record = {"theta": False, "sigma_interval": None, "no_sigma": "no mooring position"}
    caption = stack_captions(stack, {"ts_diagram": record})["ts_diagram"]
    assert caption.startswith("In-situ temperature (no mooring position, no σθ)")
    assert "σθ, every" not in caption
    egm2 = _open(_EGM2_CM)
    egm2_caption = it.ts_caption(egm2, record, {})
    assert egm2_caption.startswith("In-situ temperature (no mooring position, no σθ)")
