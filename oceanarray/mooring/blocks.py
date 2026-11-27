"""The ``people:`` and ``source:`` blocks of a mooring YAML: their fields and checks.

A ``people:`` block holds the OceanSITES 1.4 people fields (``principal_investigator*``,
``contributor_*``) and ``people_source``; a ``source:`` block holds the citation fields
and ``source_note``. :func:`check_people_block` and :func:`check_source_block` return
the errors and warnings :func:`oceanarray.mooring.check` reports for them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

#: Fields of one principal investigator, and of one contributor entry.
PI_FIELDS = ("name", "email", "url", "id")
CONTRIBUTOR_FIELDS = ("name", "role", "email")

#: The attribute names written, in order.
PI_ATTRS = {
    f: "principal_investigator" + ("" if f == "name" else f"_{f}") for f in PI_FIELDS
}
CONTRIBUTOR_ATTRS = {f: f"contributor_{f}" for f in CONTRIBUTOR_FIELDS}


def split_list(value: object) -> list[str]:
    """Split a list attribute on ``;`` when it has one, else on ``,``.

    Parameters
    ----------
    value : object
        The attribute value; ``None`` or blank gives an empty list.

    Returns
    -------
    list of str
        The stripped parts.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(";" if ";" in text else ",")]


#: The fields a mooring YAML's ``people:`` block may hold: the OceanSITES 1.4 names
#: of :data:`PI_ATTRS` and :data:`CONTRIBUTOR_ATTRS`, and where they came from.
PEOPLE_BLOCK_KEYS: tuple[str, ...] = (
    *PI_ATTRS.values(),
    *CONTRIBUTOR_ATTRS.values(),
    "people_source",
)

#: What a ``source:`` block (and a file) can say about where the data came from;
#: each is written as ``source_<field>``.
CITATION_FIELDS: tuple[str, ...] = (
    "doi",
    "citation",
    "license",
    "acknowledgement",
    "references",
)

#: The fields a mooring YAML's ``source:`` block may hold.
SOURCE_BLOCK_KEYS: tuple[str, ...] = (*CITATION_FIELDS, "source_note")

#: A DOI: the ``10.`` prefix, a registrant code, a slash and a suffix.
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:")


def bare_doi(value: object) -> str | None:
    """Return *value* as a bare DOI (``10.xxxx/...``), or None if it is not one.

    Parameters
    ----------
    value : object
        A DOI as written: bare, ``doi:``-prefixed or as a ``https://doi.org/`` link.

    Returns
    -------
    str or None
        The bare DOI, or None for an empty value or one that is not a DOI.
    """
    text = "" if value is None else str(value).strip()
    for prefix in _DOI_PREFIXES:
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    return text if _DOI.match(text) else None


def _entry_counts(
    block: Mapping[str, object], names: Mapping[str, str]
) -> list[tuple[str, str]]:
    """Return ``(field, problem)`` for each field of one list whose entry count differs from its names'."""
    name_attr = names["name"]
    n = len(split_list(block.get(name_attr)))
    return [
        (
            field,
            f"{attr} has {len(split_list(block.get(attr)))} entries for {n} "
            f"name{'' if n == 1 else 's'} in {name_attr}",
        )
        for field, attr in names.items()
        if field != "name"
        and split_list(block.get(attr))
        and len(split_list(block.get(attr))) != n
    ]


def check_people_block(block: object) -> tuple[list[str], list[str]]:
    """Check a mooring YAML's ``people:`` block.

    Parameters
    ----------
    block : object
        The ``people:`` value as parsed from the YAML.

    Returns
    -------
    tuple of (list of str, list of str)
        Errors (not a mapping, an unknown field, a value that is not text, a field
        whose entry count differs from its names') and warnings (no
        ``people_source``, no ``principal_investigator`` or ``contributor_name``).
    """
    if not isinstance(block, Mapping):
        return [
            f"'people:' must be a mapping of {', '.join(PEOPLE_BLOCK_KEYS)}; got {block!r}."
        ], []
    errors = [
        f"'people: {key}' is not an OceanSITES people field; expected one of "
        f"{', '.join(PEOPLE_BLOCK_KEYS)}."
        for key in block
        if key not in PEOPLE_BLOCK_KEYS
    ]
    errors += [
        f"'people: {key}' must be text; got {value!r}."
        for key, value in block.items()
        if key in PEOPLE_BLOCK_KEYS and value is not None and not isinstance(value, str)
    ]
    if errors:
        return errors, []
    warnings_: list[str] = []
    for names in (PI_ATTRS, CONTRIBUTOR_ATTRS):
        for field, problem in _entry_counts(block, names):
            if field == "role" and len(split_list(block.get(names["role"]))) == 1:
                warnings_.append(
                    f"'people:' {problem}: the first name gets the role, the others a "
                    "blank one (write a role two people share for each, e.g. 'PI; PI')."
                )
            else:
                errors.append(
                    f"'people:' {problem}; give one entry per person, in order."
                )
    if not block.get("people_source"):
        warnings_.append(
            "'people:' has no people_source; say where these people came from "
            "(it is written into the files' history)."
        )
    if not block.get(PI_ATTRS["name"]) and not block.get(CONTRIBUTOR_ATTRS["name"]):
        warnings_.append(
            "'people:' names no principal_investigator or contributor_name."
        )
    return errors, warnings_


def check_source_block(block: object) -> tuple[list[str], list[str]]:
    """Check a mooring YAML's ``source:`` block.

    Parameters
    ----------
    block : object
        The ``source:`` value as parsed from the YAML.

    Returns
    -------
    tuple of (list of str, list of str)
        Errors (not a mapping, an unknown field, a value that is not text, a
        ``doi`` that is not a DOI) and warnings (no ``source_note``).
    """
    if not isinstance(block, Mapping):
        return [
            f"'source:' must be a mapping of {', '.join(SOURCE_BLOCK_KEYS)}; got {block!r}."
        ], []
    errors = [
        f"'source: {key}' is not a source field; expected one of "
        f"{', '.join(SOURCE_BLOCK_KEYS)}."
        for key in block
        if key not in SOURCE_BLOCK_KEYS
    ]
    errors += [
        f"'source: {key}' must be text; got {value!r}."
        for key, value in block.items()
        if key in SOURCE_BLOCK_KEYS and value is not None and not isinstance(value, str)
    ]
    doi = block.get("doi")
    if isinstance(doi, str) and doi.strip() and bare_doi(doi) is None:
        errors.append(
            f"'source: doi' {doi!r} is not a DOI (expected 10.xxxx/..., with or "
            "without doi: or https://doi.org/)."
        )
    warnings_ = (
        []
        if block.get("source_note")
        else ["'source:' has no source_note; say where these values came from."]
    )
    return errors, warnings_
