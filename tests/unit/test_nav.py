"""Tests for the masthead nav builder and its rendering through the vendored macro."""

from __future__ import annotations

import pytest

from oceanarray.reports._env import _ENV
from oceanarray.reports._navs import _type_label, mooring_nav
from oceanarray.reports._vendored.design_tokens import ROLE_ACCENT

_INSTR = [
    {"serial": "2941", "instr_type": "microcat", "stages": {"stage1": True}},
    {"serial": "9920", "instr_type": "aquadopp", "stages": {"stage1": True}},
]


def _render(nav):
    tmpl = _ENV.from_string(
        "{% import '_nav.html' as n %}{{ n.page_nav(nav) }}{{ n.inventory_strip(nav) }}"
    )
    return tmpl.render(nav=nav)


def _pill(nav, label):
    return next(p for r in nav["rows"] for p in r["pills"] if p["label"] == label)


def test_missing_stack_and_grid_pills_are_missing():
    nav = mooring_nav(
        "m", _INSTR, stack_exists=False, grid_exists=False, current="summary"
    )
    assert _pill(nav, "Stack")["state"] == "missing"
    assert _pill(nav, "Grid")["state"] == "missing"
    assert _pill(nav, "Summary")["state"] == "current"


def test_instrument_subdir_prefixes_reports_and_keeps_instrument_links_flat():
    nav = mooring_nav("m", _INSTR, current="2941", in_instrument_subdir=True)
    assert _pill(nav, "Summary")["href"] == "../m_report.html"
    ser = next(p for r in nav["rows"][1:] for p in r["pills"])
    assert ser["href"] == "m_2941_report.html"


def test_toplevel_prefixes_instrument_links_with_subdir():
    nav = mooring_nav("m", _INSTR, current="summary")
    ser = next(p for r in nav["rows"][1:] for p in r["pills"])
    assert ser["href"] == "instrument/m_2941_report.html"


@pytest.mark.parametrize("current", ["summary", "stack", "grid", "2941", "9920"])
def test_exactly_one_current_pill(current):
    nav = mooring_nav("m", _INSTR, current=current)
    n_current = sum(
        1 for r in nav["rows"] for p in r["pills"] if p["state"] == "current"
    )
    assert n_current == 1


def test_roles_are_role_accent_keys_and_back_is_up():
    nav = mooring_nav("m", _INSTR, current="summary", array_href="../a.html")
    pills = [p for r in nav["rows"] for p in r["pills"]] + [nav["back"]]
    assert all(p["role"] in ROLE_ACCENT for p in pills)
    assert nav["back"]["role"] == "up"
    # Roles chosen to render distinctly on oceanarray's navy masthead (not three-dark):
    # Summary dark, Stack blue, Grid purple, instruments green.
    assert _pill(nav, "Summary")["role"] == "entity"
    assert _pill(nav, "Stack")["role"] == "landing"
    assert _pill(nav, "Grid")["role"] == "aggregate-a"
    ser = next(p for r in nav["rows"][1:] for p in r["pills"])
    assert ser["role"] == "aggregate-b"


def test_no_array_href_means_no_back_pill():
    nav = mooring_nav("m", _INSTR, current="summary")
    assert nav["back"] is None


def test_type_labels_never_capitalize():
    assert _type_label("adcp") == "ADCP"
    assert _type_label("sbe37") == "SBE37"
    assert _type_label("aquadopp") == "Aquadopp"
    assert _type_label("currentmeter") == "Current meter"
    assert _type_label("unknownthing") == "UNKNOWNTHING"


def test_types_are_named_by_function_on_the_multi_deployment_pages():
    """The model maps to its function: CTD, current meter (CM), temperature logger (T)."""
    from oceanarray.reports._navs import function_abbr, function_word

    assert [function_word(t) for t in ("microcat", "aquadopp", "currentmeter")] == [
        "CTD",
        "current meter",
        "current meter",
    ]
    assert [function_abbr(t) for t in ("sbe37", "aquadopp", "tr1050", "adcp")] == [
        "CTD",
        "CM",
        "T",
        "ADCP",
    ]
    assert function_word("seapoint") == "Seapoint"  # no function: the model


def test_rendered_nav_has_one_element_per_row_and_no_inline_style():
    nav = mooring_nav("m", _INSTR, current="summary", array_href="../a.html")
    html = _render(nav)
    assert html.count('<nav class="page-nav">') == len(nav["rows"])
    assert 'style="' not in html
    assert "nav-up" in html  # back pill carries the role class


def test_empty_inventory_renders_no_strip():
    nav = mooring_nav("m", _INSTR, current="summary")
    assert nav["inventory"] == []
    assert "inventory-strip" not in _render(nav)


def test_duplicate_serials_do_not_crash_and_keep_one_current():
    # Two instruments sharing a serial must not produce two 'current' pills
    # (which validate_nav would reject, aborting the report); the first wins.
    instr = [
        {"serial": "2941", "instr_type": "microcat", "stages": {"stage1": True}},
        {"serial": "2941", "instr_type": "aquadopp", "stages": {"stage1": True}},
    ]
    nav = mooring_nav("m", instr, current="2941")
    n_current = sum(
        1 for r in nav["rows"] for p in r["pills"] if p["state"] == "current"
    )
    assert n_current == 1


def test_serial_colliding_with_reports_key_does_not_crash():
    # A serial equal to a Reports-row key ("stack") must not double the current.
    instr = [{"serial": "stack", "instr_type": "microcat", "stages": {"stage1": True}}]
    nav = mooring_nav("m", instr, current="stack")
    n_current = sum(
        1 for r in nav["rows"] for p in r["pills"] if p["state"] == "current"
    )
    assert n_current == 1


def test_instrument_pill_missing_when_no_stage_files():
    # Stages present but all False → no generated report → the pill is 'missing'.
    instr = [{"serial": "A1", "instr_type": "microcat", "stages": {"stage1": False}}]
    nav = mooring_nav("m", instr, current="summary")
    ser = next(p for r in nav["rows"][1:] for p in r["pills"])
    assert ser["state"] == "missing"


def test_instrument_pill_present_when_stages_absent():
    # No 'stages' key at all → treated as present (a link), not missing.
    instr = [{"serial": "A1", "instr_type": "microcat"}]
    nav = mooring_nav("m", instr, current="summary")
    ser = next(p for r in nav["rows"][1:] for p in r["pills"])
    assert ser["state"] == "link"


def test_instrument_key_is_an_alias_for_instr_type():
    # The documented 'instrument' key is accepted when 'instr_type' is absent.
    instr = [{"serial": "A1", "instrument": "sbe37", "stages": {"stage1": True}}]
    nav = mooring_nav("m", instr, current="summary")
    assert nav["rows"][1]["label"] == "SBE37:"
