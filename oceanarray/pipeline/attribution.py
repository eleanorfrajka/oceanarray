"""The people and sources behind a product: investigators, contributors, DOIs, licenses.

OceanSITES 1.4 people lists: ``principal_investigator*`` comma-separated,
``contributor_*`` semicolon-separated, the *n*-th entries of the parallel attributes
describing one person and role. Reading another file's lists is lenient (stage 1 cleans
the ``originator_*`` lists once); writing and reading oceanarray's own lists is strict
(:func:`read_own`). A description's ``people:`` block fills in a file that names nobody
(:func:`people_from_block`). ``source_*`` attributes keep where the data came from, the
file's values first, then the ``source:`` block (:func:`source_attrs`); a product cites
all its sources (:func:`product_citation`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence

import xarray as xr

from oceanarray.exceptions import MooringError
from oceanarray.history import append_history

log = logging.getLogger(__name__)

#: Fields of one principal investigator, and of one contributor entry.
PI_FIELDS = ("name", "email", "url", "id")
CONTRIBUTOR_FIELDS = ("name", "role", "email")

#: The attribute names written, in order.
PI_ATTRS = {
    f: "principal_investigator" + ("" if f == "name" else f"_{f}") for f in PI_FIELDS
}
CONTRIBUTOR_ATTRS = {f: f"contributor_{f}" for f in CONTRIBUTOR_FIELDS}

#: The separator each list is written with (OceanSITES 1.4).
PI_SEPARATOR = ", "
CONTRIBUTOR_SEPARATOR = "; "

#: The first oceanarray whose stage 1 cleans people (a stage file's
#: ``stage1_oceanarray_version``); files from before carry no such attribute.
CLEANED_SINCE = "0.6.0.post1.dev0"

#: The contributor role of a source file's creator, and of its publisher (NERC
#: G04 / ISO 19115).
CREATOR_ROLE = "originator"
PUBLISHER_ROLE = "publisher"

#: Titles stripped from a name, before or after it (case and a trailing dot
#: aside). Extend as new ones turn up in originators' files.
TITLES = frozenset(
    {"dr", "prof", "professor", "phd", "ph.d", "dphil", "mr", "mrs", "ms", "msc"}
)

_INITIALS = re.compile(r"^(?:[A-Z]\.\s?)+$")

#: A contributor role that makes the person a principal investigator too (some
#: originators name their PIs only this way).
_PI_ROLE = re.compile(r"principal investigator|\bPI\b", re.IGNORECASE)


def _is_title(word: str) -> bool:
    """Return whether *word* is a title (``Dr.``, ``Professor``, ``PhD``...)."""
    return word.strip().rstrip(".").casefold() in TITLES


def strip_titles(name: str) -> str:
    """Return *name* without titles before or after it.

    Parameters
    ----------
    name : str
        A person's name as an originator wrote it (``"Dr. Fiamma Straneo"``).

    Returns
    -------
    str
        The name alone (``"Fiamma Straneo"``), with a space after each initial.
    """
    # "M.Femke de Jong" reads as "M. Femke de Jong".
    words = re.sub(r"\.(?=[A-Z][a-z])", ". ", name).split()
    while words and _is_title(words[0]):
        words.pop(0)
    while words and _is_title(words[-1]):
        words.pop()
    return " ".join(words)


def _split(value: object) -> list[str]:
    """Split a list attribute on ``;`` when it has one, else on ``,``."""
    text = "" if value is None else str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(";" if ";" in text else ",")]


def split_names(value: object, n_paired: int | None = None) -> list[str]:
    """Split a names attribute into ``"Given Surname"`` names, one per person, titles removed.

    A ``;`` list splits on ``;`` and reads a comma inside a part as ``"Surname, Given"``. A
    comma-only list is read as surname-given pairs only when the paired list has half as
    many entries and every second part is a given name or initials; otherwise each part is
    a person.

    Parameters
    ----------
    value : object
        The names attribute (``principal_investigator``, ``contributor_name``, ...).
    n_paired : int, optional
        Entries in the paired emails or ids (not roles, often shared).

    Returns
    -------
    list of str
    """
    text = "" if value is None else str(value)
    if ";" in text:
        out = []
        for part in _split(text):
            pieces = [strip_titles(p) for p in part.split(",")]
            pieces = [p for p in pieces if p]
            if len(pieces) == 2:
                out.append(f"{pieces[1]} {pieces[0]}")
            elif pieces:
                out.append(" ".join(pieces))
        return out
    parts = [strip_titles(p) for p in _split(text)]
    parts = [p for p in parts if p]
    if (
        n_paired
        and len(parts) > 1
        and n_paired * 2 == len(parts)
        and all(len(p.split()) == 1 or _INITIALS.match(p) for p in parts[1::2])
    ):
        return [f"{parts[i + 1]} {parts[i]}" for i in range(0, len(parts), 2)]
    return parts


def _aligned(n: int, values: list[str], label: str) -> list[str]:
    """Return *values* one per name, or empty entries when the counts differ.

    A field that does not list one entry per name is not paired at all (it is
    left out, with a warning): guessing which entry belongs to whom would give
    someone another person's email or role. The raw string stays in the
    ``originator_*`` attribute.
    """
    if not values:
        return [""] * n
    if len(values) == n:
        return values
    log.warning(
        "%s: %d entries for %d names; left out (not paired).", label, len(values), n
    )
    return [""] * n


def _aligned_roles(n: int, values: list[str], label: str) -> list[str]:
    """Return one role per name: a single role goes to the first name, the rest blank.

    A role is not copied to every name (a list of one role does not say it is
    shared); a role two people share is written for each (``"PI; PI"``). Any other
    count that differs from the names' is left out, as :func:`_aligned` does.
    """
    if len(values) == 1 and n > 1:
        log.warning(
            "%s: 1 role for %d names; given to the first name, the others left blank.",
            label,
            n,
        )
        return values + [""] * (n - 1)
    return _aligned(n, values, label)


def _paired_count(*fields: object) -> int | None:
    """Return the number of entries the paired fields agree on, or None."""
    counts = {len(_split(f)) for f in fields if _split(f)}
    return counts.pop() if len(counts) == 1 else None


def _single(get: object, stem: str, role: str, prefix: str) -> list[dict[str, str]]:
    """Return the contributor entries of a ``<stem>_name`` / ``<stem>_email`` pair."""
    email = get(f"{stem}_email")  # type: ignore[operator]
    names = split_names(get(f"{stem}_name"), _paired_count(email))  # type: ignore[operator]
    emails = _aligned(len(names), _split(email), f"{prefix}{stem}_email")
    return [
        {"name": n, "role": role, "email": e}
        for n, e in zip(names, emails, strict=True)
    ]


def people_from_attrs(
    attrs: Mapping[str, object],
    *,
    prefix: str = "",
    creator_role: str = CREATOR_ROLE,
) -> dict[str, list[dict[str, str]]]:
    """Return the principal investigators and contributor entries a file's attributes name.

    Parameters
    ----------
    attrs : Mapping
        A file's global attributes.
    prefix : str, optional
        Read ``<prefix>principal_investigator`` and so on: ``"originator_"`` for an
        imported file's own attributes, ``""`` for an oceanarray product.
    creator_role : str, optional
        The contributor role given to the file's creator (``creator_*``, or the
        non-standard ``file_creator_*`` some originators write).

    Returns
    -------
    dict
        ``{"principal_investigator": [...], "contributor": [...]}``: dicts with
        the fields of :data:`PI_FIELDS`, and one dict per person-role entry with
        the fields of :data:`CONTRIBUTOR_FIELDS`.

    Notes
    -----
    See :func:`split_names` for how names are read. Each field is split on its
    own separator (``"A; B"`` names with ``"role 1, role 2"`` roles pair up). A
    field (role, email, url, id) without one entry per name is left out, with a
    warning. The file's creator and publisher join the contributors (roles
    ``originator`` and ``publisher``). A file with no ``principal_investigator``
    takes its PIs from the contributors whose role says "principal investigator"
    or "PI".
    """

    def get(name: str) -> object:
        return attrs.get(prefix + name)

    pi_values = {f: get(PI_ATTRS[f]) for f in PI_FIELDS[1:]}
    names = split_names(
        get("principal_investigator"), _paired_count(*pi_values.values())
    )
    columns = {
        f: _aligned(len(names), _split(v), prefix + PI_ATTRS[f])
        for f, v in pi_values.items()
    }
    pis = [
        {"name": n, **{f: columns[f][i] for f in PI_FIELDS[1:]}}
        for i, n in enumerate(names)
    ]

    contributors = []
    for stem in ("creator", "file_creator"):
        contributors += _single(get, stem, creator_role, prefix)
    contributors += _single(get, "publisher", PUBLISHER_ROLE, prefix)
    role, email = get("contributor_role"), get("contributor_email")
    names = split_names(get("contributor_name"), _paired_count(email))
    roles = _aligned_roles(len(names), _split(role), f"{prefix}contributor_role")
    emails = _aligned(len(names), _split(email), f"{prefix}contributor_email")
    named = [
        {"name": n, "role": one, "email": e}
        for n, r, e in zip(names, roles, emails, strict=True)
        for one in split_roles(r)
    ]
    contributors += named
    if not pis:
        # Some originators name their PIs only by a contributor role.
        pis = [
            {"name": c["name"], "email": c["email"], "url": "", "id": ""}
            for c in named
            if _PI_ROLE.search(c["role"])
        ]
    return {"principal_investigator": pis, "contributor": contributors}


def split_roles(role: str) -> list[str]:
    """Return the roles one contributor entry names, each its own entry.

    ``"data processing and interpretation"`` is two roles, ``"data processing"``
    and ``"interpretation"``; a principal-investigator role (``"project PI"``,
    ``"Principal Investigator"``) reads ``"PI"``. An empty role stays one empty
    entry.

    Parameters
    ----------
    role : str
        One contributor's role, as the originator wrote it.

    Returns
    -------
    list of str
        The roles, in order, without repeats.
    """
    parts = [p.strip() for p in re.split(r"\s+and\s+|\s*&\s*", role or "")]
    out: list[str] = []
    for part in parts:
        one = "PI" if _PI_ROLE.search(part) else part
        if one.casefold() not in (o.casefold() for o in out):
            out.append(one)
    return [o for o in out if o] or [""]


def _name_key(person: Mapping[str, str]) -> str:
    """Return a person's name without titles, initials, case, dots or spaces.

    ``"Andree L. Ramsey"``, ``"Andree L Ramsey"`` and ``"Andree Ramsey"`` give one
    key; so do ``"M. Femke de Jong"`` and ``"M.Femke de Jong"``.
    """
    words = strip_titles(person.get("name", "").replace(".", ". ")).split()
    words = [w for w in words if len(w.rstrip(".")) > 1]
    return "".join(w.rstrip(".,").casefold() for w in words)


def _same(a: Mapping[str, str], b: Mapping[str, str]) -> bool:
    """Return whether two entries are one person.

    Two different ids tell people apart; otherwise one id, one email or one name
    (titles, initials, case, dots and spaces aside) makes them one, so a person
    who changed email, or whose email was mistyped, stays one person.
    """
    ia, ib = a.get("id", "").strip().lower(), b.get("id", "").strip().lower()
    if ia and ib:
        return ia == ib
    ea, eb = a.get("email", "").strip().lower(), b.get("email", "").strip().lower()
    if ea and ea == eb:
        return True
    return bool(_name_key(a)) and _name_key(a) == _name_key(b)


def union(
    groups: Iterable[Mapping[str, list[dict[str, str]]]],
) -> dict[str, list[dict[str, str]]]:
    """Join several people lists without repeats, in order of first appearance.

    See :func:`_same` for when two entries are one person. A principal
    investigator appears once; a contributor appears once per role (case aside),
    and a blank role merges into the person's other entry. A person's first name, url and id are kept (a missing one filled from a later
    entry); their newest email is kept, the earlier one named in a warning.

    Parameters
    ----------
    groups : iterable of dict
        Outputs of :func:`people_from_attrs` (or of this function).

    Returns
    -------
    dict
        The same shape as :func:`people_from_attrs` returns.
    """
    out: dict[str, list[dict[str, str]]] = {
        "principal_investigator": [],
        "contributor": [],
    }
    replaced: dict[str, set[str]] = {}
    for group in groups:
        for kind, fields in (
            ("principal_investigator", PI_FIELDS),
            ("contributor", CONTRIBUTOR_FIELDS),
        ):
            for person in group.get(kind, []):
                entry = {f: str(person.get(f, "") or "").strip() for f in fields}
                if not entry["name"]:
                    continue
                same = [p for p in out[kind] if _same(p, entry)]
                for p in same:
                    for f in fields:
                        if f == "role" or not entry[f]:
                            continue
                        if f == "email" and p[f] and entry[f].lower() != p[f].lower():
                            # The newest email is kept (people move); the old one
                            # is named in a warning.
                            replaced.setdefault(p["name"], set()).add(p[f])
                            p[f] = entry[f]
                        elif not p[f]:
                            p[f] = entry[f]
                if kind == "contributor" and same:
                    role = entry["role"].casefold()
                    if not role or any(p["role"].casefold() == role for p in same):
                        # A repeat, or a blank role for someone already listed.
                        continue
                    blank = next((p for p in same if not p["role"]), None)
                    if blank is not None:
                        # A blank-role entry of this person takes the role.
                        blank["role"] = entry["role"]
                        continue
                    # The same person in a new role: an entry of its own, with the
                    # name and email of their first entry.
                    entry = {
                        **entry,
                        "name": same[0]["name"],
                        "email": same[0]["email"],
                    }
                elif same:
                    continue
                out[kind].append(entry)
    # A PI listed without an email takes the one their contributor entry has.
    for pi in out["principal_investigator"]:
        if not pi["email"]:
            pi["email"] = next(
                (c["email"] for c in out["contributor"] if c["email"] and _same(c, pi)),
                "",
            )
    for name, emails in replaced.items():
        log.warning(
            "%s: earlier email(s) %s replaced by the newest.", name, sorted(emails)
        )
    return out


def _clean(value: str, separator: str, label: str) -> str:
    """Return *value* safe inside a list written with *separator*.

    A separator inside a value would shift every later entry; the value is cut
    at the first one, with a warning.
    """
    mark = separator.strip()
    if mark in value:
        log.warning(
            "%s: %r holds the list separator %r; kept up to it.", label, value, mark
        )
        return value.split(mark)[0].strip()
    return value


def to_attrs(people: Mapping[str, list[dict[str, str]]]) -> dict[str, str]:
    """Return the OceanSITES attributes for a people list.

    Every parallel attribute of a list has one entry per person and role, empty entries
    kept, so the *n*-th entries always describe one person. ``contributor_name``,
    ``_role`` and ``_email`` are written whenever there is a contributor (an
    empty field as ``"; ; "``); a PI field nobody has is left out.

    Parameters
    ----------
    people : dict
        As :func:`people_from_attrs` or :func:`union` return.

    Returns
    -------
    dict of str to str
        ``principal_investigator*`` (comma-separated) and ``contributor_*``
        (semicolon-separated).
    """
    out: dict[str, str] = {}
    for kind, names, sep in (
        ("principal_investigator", PI_ATTRS, PI_SEPARATOR),
        ("contributor", CONTRIBUTOR_ATTRS, CONTRIBUTOR_SEPARATOR),
    ):
        entries = people.get(kind, [])
        for field, attr in names.items():
            values = [_clean(e.get(field, ""), sep, attr) for e in entries]
            # Contributor fields are written whenever there is a contributor, so
            # their entry counts always show; a PI field nobody has is left out.
            if any(values) or (kind == "contributor" and entries):
                out[attr] = sep.join(values)
    return out


def clean(attrs: Mapping[str, object]) -> dict[str, str]:
    """Return the people attributes stage 1 writes for a file.

    The lenient reading of the file's ``originator_*`` lists, joined without
    repeats; empty for a file that names nobody.

    Parameters
    ----------
    attrs : Mapping
        The file's global attributes as read (``originator_*`` from an import).

    Returns
    -------
    dict of str to str
        ``principal_investigator*`` and ``contributor_*``.
    """
    return to_attrs(union([people_from_attrs(attrs, prefix="originator_")]))


def read_own(attrs: Mapping[str, object]) -> dict[str, list[dict[str, str]]]:
    """Read the people lists oceanarray wrote, strictly.

    PIs split on ``", "``, contributors on ``"; "``; every parallel attribute of a
    list must have as many entries as its names. No titles, pairs or roles are
    interpreted: these lists were cleaned once, at stage 1.

    Parameters
    ----------
    attrs : Mapping
        A stage, stack or Stack cat file's global attributes.

    Returns
    -------
    dict
        The same shape as :func:`people_from_attrs` returns.

    Raises
    ------
    oceanarray.exceptions.MooringError
        A list whose parallel attributes have different entry counts.
    """
    out: dict[str, list[dict[str, str]]] = {}
    for kind, names, sep in (
        ("principal_investigator", PI_ATTRS, PI_SEPARATOR),
        ("contributor", CONTRIBUTOR_ATTRS, CONTRIBUTOR_SEPARATOR),
    ):
        columns = {
            field: str(attrs[attr]).split(sep)
            if attrs.get(attr) not in (None, "")
            else None
            for field, attr in names.items()
        }
        n = len(columns["name"]) if columns["name"] is not None else 0
        for field, values in columns.items():
            if values is not None and len(values) != n:
                where = (
                    f"{attrs['mooring_name']}: " if attrs.get("mooring_name") else ""
                )
                raise MooringError(
                    f"{where}{names[field]} has {len(values)} entries for {n} "
                    "names; the people lists of an oceanarray file must line up "
                    "(rerun from stage 1)."
                )
        out[kind] = [
            {
                field: (values[i].strip() if values is not None else "")
                for field, values in columns.items()
            }
            for i in range(n)
        ]
    return out


def cleaned_at_stage1(attrs: Mapping[str, object]) -> bool:
    """Return whether a stage file's people were cleaned by its stage 1.

    Parameters
    ----------
    attrs : Mapping
        A stage file's global attributes.

    Returns
    -------
    bool
        True when stage 1 was run by an oceanarray that cleans people
        (``stage1_oceanarray_version`` at least :data:`CLEANED_SINCE`); an empty
        people list then means the source named no one.
    """
    from oceanarray import provenance

    return provenance.requires_version(
        attrs, CLEANED_SINCE, key=provenance.stage_version_attr(1)
    )


# ── People and source from the mooring description ─────────────────────────

#: The fields a description's ``people:`` block may hold: the OceanSITES 1.4 names
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

#: The fields a description's ``source:`` block may hold.
SOURCE_BLOCK_KEYS: tuple[str, ...] = (*CITATION_FIELDS, "source_note")

#: The ``source_*`` attributes a product carries: one per citation field, and the note.
SOURCE_ATTRS: tuple[str, ...] = (
    *(f"source_{f}" for f in CITATION_FIELDS),
    "source_note",
)

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
    n = len(_split(block.get(name_attr)))
    return [
        (
            field,
            f"{attr} has {len(_split(block.get(attr)))} entries for {n} "
            f"name{'' if n == 1 else 's'} in {name_attr}",
        )
        for field, attr in names.items()
        if field != "name"
        and _split(block.get(attr))
        and len(_split(block.get(attr))) != n
    ]


def check_people_block(block: object) -> tuple[list[str], list[str]]:
    """Check a description's ``people:`` block.

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
            if field == "role" and len(_split(block.get(names["role"]))) == 1:
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


def people_from_block(block: Mapping[str, object]) -> dict[str, str]:
    """Return the people attributes a description's ``people:`` block gives.

    The block is read with the same rules as a file's own lists
    (:func:`people_from_attrs`), so titles, ``"Surname, Given"`` and two-role
    entries come out the same way.

    Parameters
    ----------
    block : Mapping
        The ``people:`` block (OceanSITES 1.4 field names).

    Returns
    -------
    dict of str to str
        ``principal_investigator*`` and ``contributor_*``, as :func:`to_attrs`
        writes them; empty when the block names nobody.
    """
    return to_attrs(union([people_from_attrs(block)]))


def check_source_block(block: object) -> tuple[list[str], list[str]]:
    """Check a description's ``source:`` block.

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


def source_attrs(
    attrs: Mapping[str, object], block: Mapping[str, object] | None
) -> tuple[dict[str, str], list[str]]:
    """Return the ``source_*`` attributes for a file, its own values first.

    Field by field: a value the file gives (its ``originator_<field>``) is kept;
    the description's ``source:`` block fills the fields the file lacks; a field
    both give keeps the file's value and is reported.

    Parameters
    ----------
    attrs : Mapping
        The file's global attributes (``originator_*`` from an import).
    block : Mapping or None
        The description's ``source:`` block, if any.

    Returns
    -------
    tuple of (dict of str to str, list of str)
        ``source_<field>`` for every field either gives (a DOI bare, see
        :func:`bare_doi`; ``source_note`` from the block), and the fields given by
        both, with the value left out.
    """
    block = block or {}
    out: dict[str, str] = {}
    both: list[str] = []
    for field in CITATION_FIELDS:
        own = str(attrs.get(f"originator_{field}") or "").strip()
        given = str(block.get(field) or "").strip()
        if own and given and own != given:
            both.append(
                f"source {field}: the file's value is kept; the description's "
                f"{given!r} is not used"
            )
        value = own or given
        if field == "doi" and value:
            value = bare_doi(value) or value
        if value:
            out[f"source_{field}"] = value
    note = str(block.get("source_note") or "").strip()
    if note:
        out["source_note"] = note
    return out, both


def _is_link(text: str) -> bool:
    """Return whether *text* is a URL or a DOI (not a piece of a bibliographic reference)."""
    return bool(re.match(r"(https?://|doi:|10\.\d{4,}/)\S+$", text))


def list_items(value: object) -> list[str]:
    """Split a list attribute into its items, each once, in first-seen order.

    Splits on ``;``; a part whose comma-separated pieces are all URLs or DOIs is
    split on the commas too, so ``"a, b; b, a"`` gives ``a``, ``b``. Any other part
    (``"Jones, 2020"``, a bibliographic reference) stays whole.

    Parameters
    ----------
    value : object
        The attribute's value, or None.

    Returns
    -------
    list of str
        The distinct items.
    """
    items: list[str] = []
    for part in str(value or "").split(";"):
        pieces = [p.strip() for p in part.split(",")]
        for item in pieces if all(_is_link(p) for p in pieces) else [part.strip()]:
            if item and item not in items:
                items.append(item)
    return items


def _names_doi(text: str, doi: str) -> bool:
    """Return whether *text* names *doi* itself, not a longer DOI that starts with it."""
    # Before it: anything but a DOI character (a doi.org URL's "/" is fine);
    # after it: not a longer DOI.
    return bool(re.search(rf"(?<![\w.-]){re.escape(doi)}(?![\w/-])", text))


def _derived(sources: Sequence[Mapping[str, object]]) -> tuple[list[str], list[str]]:
    """Return each distinct source's citation (with its DOI) and the references."""
    derived: list[str] = []
    refs: list[str] = []
    for src in sources:
        # A source may name several DOIs, joined by "; " (a DOI holds no "; ").
        dois = [
            d
            for d in (bare_doi(p) for p in str(src.get("source_doi") or "").split("; "))
            if d
        ]
        # The source's own citation, verbatim, with its DOI when the text lacks it.
        citation = str(src.get("source_citation") or "").strip()
        missing = [f"doi:{d}" for d in dois if not _names_doi(citation, d)]
        cited = [" ".join([citation, *missing])] if citation else missing
        for c in cited:
            if c and c not in derived:
                derived.append(c)
        for ref in [f"doi:{d}" for d in dois] + list_items(
            src.get("source_references")
        ):
            if ref not in refs:
                refs.append(ref)
    return derived, refs


def product_citation(sources: Sequence[Mapping[str, object]]) -> dict[str, str]:
    """Return how to cite a product built from *sources*: their own citations, licenses and references.

    Parameters
    ----------
    sources : sequence of Mapping
        The ``source_*`` attributes of each input.

    Returns
    -------
    dict of str to str
        ``citation`` (each distinct source citation, with its DOI if the text lacks it),
        ``license`` (each distinct license) and ``references`` (each DOI and reference
        once), joined with ``"; "``; empty keys left out.
    """
    derived, refs = _derived(sources)
    licenses: list[str] = []
    for src in sources:
        text = str(src.get("source_license") or "").strip()
        if text and text not in licenses:
            licenses.append(text)
    out = {}
    if derived:
        out["citation"] = "; ".join(derived)
    if licenses:
        out["license"] = "; ".join(licenses)
    if refs:
        out["references"] = "; ".join(refs)
    return out


def apply_product_citation(
    ds: xr.Dataset, title: str, sources: Sequence[Mapping[str, object]]
) -> xr.Dataset:
    """Cite a product as its sources are, and record how it was made, in place.

    Replaces ``citation``, ``license`` and ``references`` (an input's copies are
    removed first) with :func:`product_citation`, and appends the
    :func:`derivation` line to ``history``.

    Parameters
    ----------
    ds : xarray.Dataset
        The product (a stack, Stack cat, grid or Grid cat).
    title : str
        What the product is, e.g. ``"CF5_2020 stack"``.
    sources : sequence of Mapping
        The ``source_*`` attributes of each input.

    Returns
    -------
    xarray.Dataset
        *ds*.
    """
    from oceanarray import provenance

    for key in ("citation", "license", "references"):
        ds.attrs.pop(key, None)
    ds.attrs.update(product_citation(sources))
    version = provenance.version_attrs()[provenance.VERSION_ATTR]
    append_history(ds, derivation(title, version, sources))
    return ds


def derivation(
    title: str, version: str, sources: Sequence[Mapping[str, object]]
) -> str:
    """Return the history line saying what a product was built with and from.

    Parameters
    ----------
    title : str
        What the product is, e.g. ``"CF5_2020 stack"``.
    version : str
        The oceanarray version that built it (its public part is written).
    sources : sequence of Mapping
        The ``source_*`` attributes of each input.

    Returns
    -------
    str
        ``"CF5_2020 stack built with oceanarray 0.6.1 from: doi:10.x/y"``, or
        without ``from:`` when there are no sources.
    """
    from oceanarray import provenance

    public = provenance.public_version(version) or str(version)
    derived, _ = _derived(sources)
    line = f"{title} built with oceanarray {public}"
    # One history entry: "; " separates entries, so the citations are kept apart
    # with " | " and any "; " inside one becomes ", ".
    cited = " | ".join(c.rstrip(".").replace("; ", ", ") for c in derived)
    return f"{line} from: {cited}" if derived else line


def deployment_sources(
    attrs: Mapping[str, object], record: Mapping[str, Sequence] | None = None
) -> list[dict[str, str]]:
    """Return the ``source_*`` of each input a product was built from.

    Parameters
    ----------
    attrs : Mapping
        The product's global attributes (its joined ``source_*``).
    record : Mapping of str to sequence, optional
        A site product's per-deployment values, ``deployment_source_<field>`` → one
        value per deployment; when given, each deployment is one source.

    Returns
    -------
    list of dict of str to str
        One ``{"source_<field>": value}`` per deployment, or the product's own
        ``source_*`` as a single source; empty when nothing is recorded.
    """
    keys = [f"source_{f}" for f in CITATION_FIELDS]
    if record:
        n = max((len(v) for v in record.values()), default=0)
        return [
            {
                key: str(record[f"deployment_{key}"][i])
                for key in keys
                if f"deployment_{key}" in record and str(record[f"deployment_{key}"][i])
            }
            for i in range(n)
        ]
    own = {key: str(attrs[key]) for key in keys if attrs.get(key)}
    return [own] if own else []


def join_values(
    attrs_seq: Sequence[Mapping[str, object]],
    keys: Sequence[str],
    *,
    what: str,
    warn: Sequence[str] = (),
    lists: Sequence[str] = (),
) -> dict[str, str]:
    """Return attributes of several files, one joined value each.

    Each value is kept whole (a citation may itself hold ``"; "``), once, in order;
    values that differ are joined with ``"; "``. A list attribute (*lists*) is
    joined item by item (:func:`list_items`), so the same items in another order
    are kept once.

    Parameters
    ----------
    attrs_seq : Sequence of Mapping
        The global attributes of each file, in order.
    keys : Sequence of str
        The attributes to join.
    what : str
        What the files are, for the warning (``"the stage files"``).
    warn : Sequence of str, optional
        The attributes for which a second value draws a WARNING.
    lists : Sequence of str, optional
        The attributes that are lists.

    Returns
    -------
    dict of str to str
        ``{key: joined value}`` for every key any file gives.
    """
    out: dict[str, str] = {}
    for key in keys:
        values: list[str] = []
        for attrs in attrs_seq:
            value = str(attrs.get(key) or "").strip()
            if value and value not in values:
                values.append(value)
        if key in lists:
            # Warn only when the files list different items, not the same ones
            # in another order.
            differ = len({frozenset(list_items(v)) for v in values}) > 1
            values = list_items("; ".join(values))
        else:
            differ = len(values) > 1
        if differ and key in warn:
            log.warning(
                f"  {what} give {len(values)} values of {key}; all are kept"
                + (", and a user must follow each" if key == "source_license" else "")
            )
        if values:
            out[key] = "; ".join(values)
    return out


def join_sources(
    attrs_seq: Sequence[Mapping[str, object]],
    *,
    what: str,
    warn: Sequence[str] = SOURCE_ATTRS,
) -> dict[str, str]:
    """Return the ``source_*`` attributes of several files, one joined value each.

    Parameters
    ----------
    attrs_seq : Sequence of Mapping
        The global attributes of each file, in order.
    what : str
        What the files are, for the warning (``"the stage files"``).
    warn : Sequence of str, optional
        Attributes for which a second value warns: every field for a mooring (one data
        package), the license only for a site's deployments.

    Returns
    -------
    dict of str to str
        As :func:`join_values` (``source_references`` is a list).
    """
    return join_values(
        attrs_seq, SOURCE_ATTRS, what=what, warn=warn, lists=("source_references",)
    )
