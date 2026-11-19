"""Structural integrity tests for the instrument section manifest (``_instrument.py``).

Pure-structural checks over the registry and profile (no rendering): panel ids
resolve, no orphans, no duplicate section id/title, and — critically — the
collapsed start and end windows keep the anchor ``start`` that ``mooring.html``
deep-links to.
"""

from oceanarray.reports._instrument import (
    INSTRUMENT_DEFAULT,
    INSTRUMENT_PANELS,
    INSTRUMENT_SECTIONS,
)
from oceanarray.reports._vendored.page_model import PanelGroup, Section


def _static_panel_ids(profile) -> list[str]:
    """String panel-id references in a profile (skips PanelGroups)."""
    ids: list[str] = []
    for sec in profile.entries:
        ids += [p for p in sec.panels if isinstance(p, str)]
    return ids


def test_every_referenced_panel_id_exists():
    """Every static panel id referenced by a section resolves against the registry."""
    for pid in _static_panel_ids(INSTRUMENT_DEFAULT):
        assert pid in INSTRUMENT_PANELS, pid


def test_every_registered_panel_is_referenced():
    """No orphan panels — every registry entry is used by the profile."""
    assert set(INSTRUMENT_PANELS) == set(_static_panel_ids(INSTRUMENT_DEFAULT))


def test_no_duplicate_section_id_or_title():
    """The profile has unique section ids and titles (the dup-'Velocity' guard)."""
    ids = [s.id for s in INSTRUMENT_DEFAULT.entries]
    titles = [s.title for s in INSTRUMENT_DEFAULT.entries]
    assert len(ids) == len(set(ids))
    assert len(titles) == len(set(titles))


def test_start_anchor_is_preserved():
    """The windows box in the processing section keeps id 'start' (mooring.html links #start)."""
    from oceanarray.reports._env import render_template

    assert "windows" in INSTRUMENT_SECTIONS["processing"].panels
    html = render_template(
        "_instrument_windows_note.html",
        hours=6,
        kinds=set(),
        end_suggested=True,
        disturbed="",
        images=[],
    )
    assert '<details class="processing" id="start">' in html


def test_rose_sections_are_distinct():
    """The ADCP and non-ADCP rose sections are separate ids (mutually exclusive)."""
    assert INSTRUMENT_SECTIONS["adcp_rose"].id != INSTRUMENT_SECTIONS["rose"].id
    assert INSTRUMENT_SECTIONS["adcp_rose"].title != INSTRUMENT_SECTIONS["rose"].title


def test_timeseries_uses_a_panelgroup():
    """The paginated time-series run expands as panels, not sections."""
    ts = INSTRUMENT_SECTIONS["timeseries"]
    assert any(isinstance(p, PanelGroup) for p in ts.panels)


def test_appendix_is_the_netcdf_file():
    """The one appendix is the collapsed NetCDF file (A)."""
    appendix = [s for s in INSTRUMENT_DEFAULT.entries if s.role == "appendix"]
    assert [s.id for s in appendix] == ["netcdf"]


def test_all_profile_entries_are_sections():
    """Instrument profile holds no top-level Expand (only Sections)."""
    assert all(isinstance(s, Section) for s in INSTRUMENT_DEFAULT.entries)
