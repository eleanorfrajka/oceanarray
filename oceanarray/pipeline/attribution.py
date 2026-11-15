"""The people behind a product: principal investigators and contributors, as OceanSITES attributes.

OceanSITES 1.4 lists principal investigators (``principal_investigator``,
``_email``, ``_url``, ``_id``) separated by commas, and contributors
(``contributor_name``, ``_role``, ``_email``) separated by semicolons; the
parallel attributes of one list hold one entry per person and role, so the *n*-th entries
describe one person.

Reading another file's lists is lenient (originators use either separator, add
titles, spell a name or an email two ways); writing is strict (the manual's
separators, no separator inside a value, every parallel attribute the same
length). A contributor with two roles takes two entries. Stage 1 cleans an
imported file's own lists (its ``originator_*`` attributes) with the lenient
reader, once; every later step reads oceanarray's own lists with the strict
reader (:func:`read_own`) and only joins them.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable, Mapping

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
    """Split a list of names into people.

    Titles are stripped (a part that is only a title, as in ``"Dr. Fiamma
    Straneo, Professor"``, goes). A list with a ``;`` is split on it, and a comma
    left inside a part reads as ``"Surname, Given"`` (``"de Jong, Femke"`` →
    ``"Femke de Jong"``). A comma-only list is read as surname-given pairs only
    when both say so: the paired emails or ids list half as many entries as it
    has parts, and every second part is a given name or initials (one word, or
    ``"A. B."``). Otherwise each part is a person.

    Parameters
    ----------
    value : object
        The names attribute (``principal_investigator``, ``contributor_name``...).
    n_paired : int, optional
        How many entries the paired emails or ids list (roles are left out: one
        role is often shared by several names).

    Returns
    -------
    list of str
        One name per person, ``"Given Surname"``, no title, no separator inside.
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
    warnings.warn(
        f"{label}: {len(values)} entries for {n} names; left out (not paired).",
        stacklevel=3,
    )
    return [""] * n


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
    roles = _aligned(len(names), _split(role), f"{prefix}contributor_role")
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
        warnings.warn(
            f"{name}: earlier email(s) {sorted(emails)} replaced by the newest.",
            stacklevel=2,
        )
    return out


def _clean(value: str, separator: str, label: str) -> str:
    """Return *value* safe inside a list written with *separator*.

    A separator inside a value would shift every later entry; the value is cut
    at the first one, with a warning.
    """
    mark = separator.strip()
    if mark in value:
        warnings.warn(
            f"{label}: {value!r} holds the list separator {mark!r}; kept up to it.",
            stacklevel=3,
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
    ValueError
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
                raise ValueError(
                    f"{names[field]} has {len(values)} entries for {n} names; the "
                    "people lists of an oceanarray file must line up (rerun from "
                    "stage 1)."
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
