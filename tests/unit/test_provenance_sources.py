"""People and source from the mooring description, the version attribute and DOI links.

Real files only: the OSNAP OceanSITES imports (their own people, citation, license
and acknowledgement), the egM2 2016 description (an RODB mooring whose files name
no one) and a dune2 stage-3 file.
"""

from __future__ import annotations

import copy
import warnings
from pathlib import Path

import pytest
import xarray as xr
import yaml

from oceanarray import mooring, provenance
from oceanarray.pipeline import attribution
from oceanarray.readers import read_oceansites
from oceanarray.reports._html_helpers import doi_link
from oceanarray.writers.netcdf import write

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
OSNAP = FIXTURES / "raw" / "osnap"
K7 = OSNAP / "OS_OSNAP-10633_KPO_1111_K7_201408_D_MCTD_OSNAPstyleOceanSITES.nc"
CF7 = OSNAP / "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc"
EGM2 = FIXTURES / "proc" / "egM2_concat" / "egM2_2016" / "egM2_2016.mooring.yaml"
DUNE2_STAGE3 = (
    FIXTURES / "proc" / "dune2_1_2026" / "microcat" / "dune2_1_2026_2941_stage3.nc"
)

#: The Arctic Data Center package the OSNAP files were downloaded from.
ADC_DOI = "10.18739/A2707WQ62"


def _egm2() -> dict:
    return yaml.safe_load(EGM2.read_text())


def _own_people(path: Path) -> dict:
    """Return a file's people as a ``people:`` block (its ``originator_*`` names, unprefixed)."""
    attrs = read_oceansites(path).attrs
    return {
        key.removeprefix("originator_"): str(value)
        for key, value in attrs.items()
        if key.removeprefix("originator_") in attribution.PEOPLE_BLOCK_KEYS
    }


# ── people: ───────────────────────────────────────────────────────────


def test_a_people_block_reads_like_the_file_it_was_copied_from():
    """CF7's PI and contributor lists, typed into a description, clean as stage 1 cleans the file."""
    block = _own_people(CF7)
    assert block["principal_investigator"] == "Fiamma Straneo"
    same_fields = {
        f"originator_{key}": value
        for key, value in block.items()
        if key != "people_source"
    }
    assert attribution.people_from_block(block) == attribution.clean(same_fields)


def test_a_valid_people_block_passes_validate():
    data = _egm2()
    data["people"] = {**_own_people(CF7), "people_source": "OSNAP CF7 file header"}
    assert not [i for i in mooring.check(data) if "people" in i.message]


def test_one_role_for_two_names_goes_to_the_first_with_a_warning():
    """K7 gives one role for two contributors: the first name gets it, the second a blank."""
    data = _egm2()
    data["people"] = {**_own_people(K7), "people_source": "OSNAP K7 file header"}
    issues = [
        (i.level, i.message) for i in mooring.check(data) if "people" in i.message
    ]
    assert [lv for lv, _ in issues] == ["WARNING"]
    assert "the first name gets the role" in issues[0][1]
    with pytest.warns(UserWarning, match="1 role for 2 names; given to the first name"):
        out = attribution.people_from_block(
            {k: data["people"][k] for k in ("contributor_name", "contributor_role")}
        )
    assert out["contributor_name"] == (
        "Johannes Karstensen; Johannes Karstensen; Marilena Oltmanns"
    )
    assert out["contributor_role"] == "data collection; processing; "


def test_emails_that_do_not_line_up_are_still_an_error():
    data = _egm2()
    data["people"] = {
        "contributor_name": "Johannes Karstensen; Marilena Oltmanns",
        "contributor_email": "jkarstensen@geomar.de",
        "people_source": "OSNAP K7 file header",
    }
    errors = [i.message for i in mooring.check(data) if i.level == "ERROR"]
    assert errors == [
        "'people:' contributor_email has 1 entries for 2 names in contributor_name; "
        "give one entry per person, in order."
    ]


def test_a_role_written_for_each_person_passes_without_a_warning():
    """K7's two contributors, each given the shared role: no finding, both keep it."""
    data = _egm2()
    data["people"] = {
        "contributor_name": "Johannes Karstensen; Marilena Oltmanns",
        "contributor_role": "PI; PI",
        "people_source": "OSNAP K7 file header",
    }
    assert not [i for i in mooring.check(data) if "people" in i.message]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = attribution.people_from_block(data["people"])
    assert out["contributor_name"] == "Johannes Karstensen; Marilena Oltmanns"
    assert out["contributor_role"] == "PI; PI"


def test_people_lists_that_do_not_line_up_are_an_error():
    data = _egm2()
    data["people"] = {
        "principal_investigator": "Penny Holliday",
        "principal_investigator_email": "a@noc.ac.uk, b@noc.ac.uk",
        "people_source": "cruise report",
    }
    errors = [i.message for i in mooring.check(data) if i.level == "ERROR"]
    assert any(
        "principal_investigator_email has 2 entries for 1 name" in m for m in errors
    )


def test_an_unknown_people_field_is_an_error_and_no_source_a_warning():
    data = _egm2()
    data["people"] = {"principal_investigator": "Penny Holliday", "pi_orcid": "x"}
    issues = [
        (i.level, i.message) for i in mooring.check(data) if "people" in i.message
    ]
    assert ("ERROR", next(m for lv, m in issues if "pi_orcid" in m)) in issues
    data["people"] = {"principal_investigator": "Penny Holliday"}
    issues = [
        (i.level, i.message) for i in mooring.check(data) if "people" in i.message
    ]
    assert [lv for lv, m in issues] == ["WARNING"]
    assert "people_source" in issues[0][1]


# ── source: ───────────────────────────────────────────────────────────


def test_the_file_wins_field_by_field_and_the_block_fills_the_doi():
    """CF7 gives citation, license, acknowledgement, references; only the DOI comes from YAML."""
    attrs = read_oceansites(CF7).attrs
    block = {
        "doi": f"https://doi.org/{ADC_DOI}",
        "citation": "a different citation",
        "source_note": "Arctic Data Center package",
    }
    out, both = attribution.source_attrs(attrs, block)
    assert out["source_doi"] == ADC_DOI
    assert out["source_citation"] == attrs["originator_citation"]
    assert out["source_license"] == attrs["originator_license"]
    assert out["source_acknowledgement"] == attrs["originator_acknowledgement"]
    assert out["source_note"] == "Arctic Data Center package"
    assert len(both) == 1 and "citation" in both[0]


def test_a_file_that_says_nothing_takes_the_whole_block():
    """RODB files carry no source attributes: every field comes from the description."""
    block = {"doi": ADC_DOI, "citation": "NOC", "license": "CC-BY 4.0"}
    out, both = attribution.source_attrs({}, block)
    assert out == {
        "source_doi": ADC_DOI,
        "source_citation": "NOC",
        "source_license": "CC-BY 4.0",
    }
    assert both == []


def test_validate_checks_the_source_block():
    data = _egm2()
    data["source"] = {"doi": "doi:" + ADC_DOI, "source_note": "ADC"}
    assert not [i for i in mooring.check(data) if i.message.startswith("'source")]
    data["source"] = {"doi": "not-a-doi", "licence": "x"}
    errors = [i.message for i in mooring.check(data) if i.level == "ERROR"]
    assert any("'source: doi'" in m for m in errors)
    assert any("'source: licence'" in m for m in errors)


def test_a_product_cites_what_it_was_derived_from():
    """Two deployments, two packages: each cited once, the license sentence, the references joined."""
    cf7, _ = attribution.source_attrs(read_oceansites(CF7).attrs, {"doi": ADC_DOI})
    rodb, _ = attribution.source_attrs({}, {"citation": "NOC mooring data"})
    out = attribution.product_citation(
        "CF7 site record", "0.6.1+gabc1234", [cf7, cf7, rodb]
    )
    assert out["citation"] == (
        "CF7 site record, processed with oceanarray 0.6.1. Derived from: "
        f"doi:{ADC_DOI}; NOC mooring data. Users must also abide by the conditions "
        "of the source licenses."
    )
    assert out["references"] == (
        f"doi:{ADC_DOI}; http://www.oceansites.org; http://www.o-snap.org"
    )


def test_the_init_stub_shows_people_and_source_commented_out(tmp_path: Path):
    out = mooring.save(
        mooring.template("dsX_1_2026"),
        tmp_path / "x.mooring.yaml",
        optional_blocks=True,
    )
    text = out.read_text()
    assert yaml.safe_load(text).keys().isdisjoint({"people", "source"})
    assert "# people:" in text and "# source:" in text


# ── the version that wrote a file ─────────────────────────────────────


def test_a_checkout_names_its_commit_in_the_version():
    version = provenance.version_attrs()[provenance.VERSION_ATTR]
    commit = provenance._git("rev-parse", "--short", "HEAD")
    if commit:
        assert f"+g{commit}" in version
    assert provenance.LEGACY_COMMIT_ATTR not in provenance.version_attrs()
    assert provenance.public_version(version) == version.split("+")[0]


def test_the_commit_label_replaces_any_installed_one():
    assert (
        provenance._with_commit("0.6.0.post1.dev5+gold", "f52121d", dirty=True)
        == "0.6.0.post1.dev5+gf52121d.dirty"
    )
    assert provenance._with_commit("0.6.1", None, dirty=False) == "0.6.1"
    assert provenance.requires_version(
        {"oceanarray_version": "0.6.1+gf52121d.dirty"}, "0.6.0"
    )


def test_an_old_commit_attribute_is_not_carried_into_a_new_file(tmp_path: Path):
    """A stage file written before 0.6.1 kept the commit separately; rewriting drops it."""
    with xr.open_dataset(DUNE2_STAGE3, engine="netcdf4") as ds:
        old = copy.deepcopy(ds.load())
    old.attrs[provenance.LEGACY_COMMIT_ATTR] = "abc1234-dirty"
    out = write(old, tmp_path / "rewritten.nc")
    with xr.open_dataset(out, engine="netcdf4") as ds:
        assert provenance.LEGACY_COMMIT_ATTR not in ds.attrs
        assert (
            ds.attrs[provenance.VERSION_ATTR]
            == provenance.version_attrs()[provenance.VERSION_ATTR]
        )


# ── reports ───────────────────────────────────────────────────────────


def test_a_doi_is_a_link_and_anything_else_is_text():
    assert doi_link(f"doi:{ADC_DOI}") == (
        f'<a href="https://doi.org/{ADC_DOI}">{ADC_DOI}</a>'
    )
    assert doi_link("<b>not a doi</b>") == "&lt;b&gt;not a doi&lt;/b&gt;"
    assert doi_link(None) == ""
