"""Principal investigators and contributors, read from the OSNAP originators' own files.

The committed OSNAP raw files (Scripps CF7 2020, WHOI CF7 2020, GEOMAR K7 2014) and
attribute strings copied from other OSNAP East files (egM2 2018, IC1 2020, rrD5 2020,
CF4 2014).
"""

import warnings
from pathlib import Path

import pytest

from oceanarray.pipeline import attribution
from oceanarray.readers import read_oceansites

_OSNAP = Path(__file__).parent.parent / "fixtures" / "raw" / "osnap"


def _people(attrs: dict, prefix: str = "") -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return attribution.union([attribution.people_from_attrs(attrs, prefix=prefix)])


def _clean(path: Path) -> dict:
    """The attributes stage 1 adds for an imported file."""
    return attribution.to_attrs(_people(read_oceansites(path).attrs, "originator_"))


def test_a_title_after_a_name_is_not_a_person():
    """Scripps CF7 2020: creator "Dr. Fiamma Straneo, Professor" is one person."""
    out = _clean(_OSNAP / "CF7_MCTD_2020_22225_50m.nc")
    assert out["principal_investigator"] == "Fiamma Straneo"
    assert out["contributor_name"] == "Fiamma Straneo; Fiamma Straneo; Jamie Holte"
    assert out["contributor_role"] == "originator; PI; Data Processor"


def test_the_publisher_is_a_contributor_with_one_entry_per_role():
    """WHOI CF7 2020: Adam Houk as publisher and as processor, two entries."""
    out = _clean(_OSNAP / "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc")
    # "data processing and interpretation" is two roles: two entries.
    assert out["contributor_name"] == "Adam Houk; Fiamma Straneo; Adam Houk; Adam Houk"
    assert out["contributor_role"] == "publisher; PI; data processing; interpretation"


def test_every_parallel_list_has_one_entry_per_person():
    """The Scripps file has no contributor emails: empty entries keep the count."""
    out = _clean(_OSNAP / "CF7_MCTD_2020_22225_50m.nc")
    n = len(out["contributor_name"].split("; "))
    for attr in ("contributor_role", "contributor_email"):
        assert len(out[attr].split("; ")) == n


def test_one_role_for_two_contributors_goes_to_the_first():
    """GEOMAR K7 2014: one role for two contributors goes to the first; the second is blank."""
    with pytest.warns(UserWarning, match="1 role for 2 names; given to the first name"):
        people = attribution.people_from_attrs(
            read_oceansites(
                _OSNAP
                / "OS_OSNAP-10633_KPO_1111_K7_201408_D_MCTD_OSNAPstyleOceanSITES.nc"
            ).attrs,
            prefix="originator_",
        )
    out = attribution.to_attrs(attribution.union([people]))
    # Johannes Karstensen takes the role, which names two ("data collection and
    # processing"); Marilena Oltmanns's blank one merges into her publisher entry.
    assert out["contributor_name"] == (
        "Marilena Oltmanns; Johannes Karstensen; Johannes Karstensen"
    )
    assert out["contributor_role"] == "publisher; data collection; processing"
    assert out["contributor_email"] == (
        "moltmanns@geomar.de; jkarstensen@geomar.de; jkarstensen@geomar.de"
    )


def test_each_field_is_split_on_its_own_separator():
    """IC1 2020: names split on ';', roles on ',' — two people, two roles."""
    people = _people(
        {
            "contributor_name": "M. Femke de Jong; Nora Fried",
            "contributor_role": "project PI, data processing and interpretation",
            "contributor_email": "femke.de.jong@nioz.nl; nora.fried@nioz.nl",
        }
    )
    assert [(p["name"], p["role"]) for p in people["contributor"]] == [
        ("M. Femke de Jong", "PI"),
        ("Nora Fried", "data processing"),
        ("Nora Fried", "interpretation"),
    ]


def test_pis_named_only_by_a_contributor_role():
    """egM2 2018 (Scripps): no principal_investigator; two contributors are PIs."""
    people = _people(
        {
            "contributor_name": "Amy Bower, Fiamma Straneo, Heather Furey, Tiago Bilo",
            "contributor_role": "Principal Investigator, Principal Investigator, "
            "Data Processor, Data Processor",
            "file_creator_name": "Dr. Tiago Bilo",
            "file_creator_email": "tcarrilhobilo@ucsd.edu",
        }
    )
    assert [p["name"] for p in people["principal_investigator"]] == [
        "Amy Bower",
        "Fiamma Straneo",
    ]
    tiago = [p["role"] for p in people["contributor"] if p["name"] == "Tiago Bilo"]
    assert tiago == ["originator", "Data Processor"]


def test_one_person_with_two_emails_stays_one():
    """rrD5: William Johns as bjonhs@ (a typo) then bjohns@ — one PI, the newest email."""
    groups = [
        attribution.people_from_attrs(
            {
                "principal_investigator": "William Johns",
                "principal_investigator_email": email,
            }
        )
        for email in ("bjonhs@rsmas.miami.edu", "bjohns@rsmas.miami.edu")
    ]
    with pytest.warns(UserWarning, match="replaced by the newest"):
        people = attribution.union(groups)
    assert people["principal_investigator"] == [
        {
            "name": "William Johns",
            "email": "bjohns@rsmas.miami.edu",
            "url": "",
            "id": "",
        }
    ]


def test_cf_2014_and_2020_name_straneo_once():
    """WHOI CF7 (fstraneo@ucsd.edu) and Scripps CF7 (Dr. ..., Professor): one PI."""
    groups = [
        attribution.people_from_attrs(
            read_oceansites(_OSNAP / f).attrs, prefix="originator_"
        )
        for f in ("OS_OSNAP_WHOI_CF7_202007_CM_500m.nc", "CF7_MCTD_2020_22225_50m.nc")
    ]
    out = attribution.to_attrs(attribution.union(groups))
    assert out["principal_investigator"] == "Fiamma Straneo"
    assert (
        "Professor" not in out["contributor_name"]
        and "," not in out["contributor_name"]
    )


def test_name_variants_are_one_person():
    """CF4 2014: "Andree L Ramsey" and "Andree L. Ramsey"; IC1: "M.Femke" and "M. Femke"."""
    for a, b in (
        ("Andree L Ramsey", "Andree L. Ramsey"),
        ("Andree Ramsey", "Andree L. Ramsey"),
        ("M.Femke de Jong", "M. Femke de Jong"),
    ):
        people = _people(
            {"contributor_name": f"{a}; {b}", "contributor_role": "processor"}
        )
        assert len(people["contributor"]) == 1


def test_surname_given_becomes_given_surname():
    """A "Surname, Given" PI (one email for the pair) is written without its comma."""
    people = _people(
        {
            "principal_investigator": "de Jong, Femke",
            "principal_investigator_email": "femke.de.jong@nioz.nl",
        }
    )
    assert people["principal_investigator"][0]["name"] == "Femke de Jong"
    two = _people(
        {
            "principal_investigator": "Amy Bower, Fiamma Straneo",
            "principal_investigator_email": "abower@whoi.edu",
        }
    )
    assert [p["name"] for p in two["principal_investigator"]] == [
        "Amy Bower",
        "Fiamma Straneo",
    ]


def test_a_shared_contact_email_keeps_both_people():
    """Two contributors with one email between them: both stay, the email is not paired."""
    with pytest.warns(UserWarning, match="left out"):
        parsed = attribution.people_from_attrs(
            {
                "contributor_name": "M. Femke de Jong; Nora Fried",
                "contributor_role": "project PI, data processing and interpretation",
                "contributor_email": "femke.de.jong@nioz.nl",
            }
        )
    people = attribution.union([parsed])
    assert [p["name"] for p in people["contributor"]] == [
        "M. Femke de Jong",
        "Nora Fried",
        "Nora Fried",
    ]
    # Every entry empty: the attribute is still written, one empty entry per entry.
    assert attribution.to_attrs(people)["contributor_email"] == "; ; "


def test_and_splits_a_role_and_pi_reads_pi():
    """IC1 2020: "project PI, data processing and interpretation" for two names."""
    out = attribution.to_attrs(
        _people(
            {
                "contributor_name": "M. Femke de Jong; Nora Fried",
                "contributor_role": "project PI, data processing and interpretation",
                "contributor_email": "femke.de.jong@nioz.nl; nora.fried@nioz.nl",
            }
        )
    )
    assert out["contributor_name"] == "M. Femke de Jong; Nora Fried; Nora Fried"
    assert out["contributor_role"] == "PI; data processing; interpretation"
    assert out["contributor_email"] == (
        "femke.de.jong@nioz.nl; nora.fried@nioz.nl; nora.fried@nioz.nl"
    )


def test_the_written_lists_round_trip():
    """A stack's own attributes read back as the same people."""
    first = _people(
        read_oceansites(_OSNAP / "CF7_MCTD_2020_22225_50m.nc").attrs, "originator_"
    )
    attrs = attribution.to_attrs(first)
    again = attribution.to_attrs(_people(attrs))
    assert again == attrs


def test_the_strict_reader_refuses_lists_that_do_not_line_up():
    with pytest.raises(ValueError, match="must line up"):
        attribution.read_own(
            {"contributor_name": "Amy Bower; Adam Houk", "contributor_role": "PI"}
        )
    with pytest.raises(ValueError, match="must line up"):
        # A comma inside a PI's name shifts every entry after it.
        attribution.read_own(
            {
                "principal_investigator": "de Jong, Femke",
                "principal_investigator_email": "femke.de.jong@nioz.nl",
            }
        )


def test_only_stage1_cleans():
    """The lenient reader of other people's lists is used in one place: stage 1."""
    import oceanarray

    root = Path(oceanarray.__file__).parent
    users = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if "people_from_attrs" in p.read_text(encoding="utf-8")
        or "attribution.clean(" in p.read_text(encoding="utf-8")
    )
    assert users == ["pipeline/attribution.py", "pipeline/stage1.py"]


@pytest.mark.parametrize(
    ("written", "ok"),
    [
        ("0.0.post1.dev72", True),  # a checkout without tags (CI)
        ("0.6.0.post1.dev5", True),  # a branch build
        ("0.7.0", True),
        ("0.6.0", False),  # the 0.6.0 release did not clean people
        (None, False),  # written before versions were recorded
    ],
)
def test_the_version_check(written, ok):
    from oceanarray import provenance

    attrs = {} if written is None else {"stage1_oceanarray_version": written}
    assert attribution.cleaned_at_stage1(attrs) is ok
    assert (
        provenance.requires_version(attrs, "0.7.0", key="stage1_oceanarray_version")
        is ok
    )
