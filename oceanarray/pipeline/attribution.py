"""The people behind a product: principal investigators and contributors, as OceanSITES attributes.

OceanSITES 1.4 lists principal investigators (``principal_investigator``,
``_email``, ``_url``, ``_id``; comma-separated) and contributors
(``contributor_name``, ``_role``, ``_email``; semicolon-separated). A product
joins the lists of its sources without repeats: an imported file's own people
(its ``originator_*`` attributes), or each deployment's stack. The source's
creator becomes a contributor with the ISO 19115 role ``originator``; the
``creator_*`` of a product is whoever produced it, which no source can say.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

#: Fields of one principal investigator, and of one contributor.
PI_FIELDS = ("name", "email", "url", "id")
CONTRIBUTOR_FIELDS = ("name", "role", "email")

#: The attribute names written, in order.
PI_ATTRS = {
    f: "principal_investigator" + ("" if f == "name" else f"_{f}") for f in PI_FIELDS
}
CONTRIBUTOR_ATTRS = {f: f"contributor_{f}" for f in CONTRIBUTOR_FIELDS}

#: The role a source's creator takes among the product's contributors.
CREATOR_ROLE = "originator"

_TITLE = re.compile(r"^(dr|prof|professor)\.?\s+", re.IGNORECASE)


def _split(value: object) -> list[str]:
    """Split a list attribute on ``;`` when it has one, else on ``,``."""
    text = "" if value is None else str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(";" if ";" in text else ",")]


def _aligned(names: list[str], values: list[str], whole: object) -> list[str]:
    """Return *values* one per name: as given when the counts agree.

    When they do not, the whole string goes to the first name and the others get
    none, so a shared contact email or a url with a comma in it is kept once and
    never makes two people look like one.
    """
    if len(values) == len(names):
        return values
    text = "" if whole is None else str(whole).strip()
    return [text] + [""] * (len(names) - 1) if names else []


def _keys(person: Mapping[str, str]) -> set[str]:
    """Return what identifies a person: their id, email and name (titles, case, spacing and dots aside)."""
    keys = {
        f"{field}:{person.get(field, '').strip().lower()}"
        for field in ("id", "email")
        if person.get(field, "").strip()
    }
    name = re.sub(r"[\s.]+", "", _TITLE.sub("", person.get("name", "")).casefold())
    if name:
        keys.add(f"name:{name}")
    return keys


def people_from_attrs(
    attrs: Mapping[str, object], *, prefix: str = "", creator_role: str = CREATOR_ROLE
) -> dict[str, list[dict[str, str]]]:
    """Return the principal investigators and contributors a file's attributes name.

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
        ``{"principal_investigator": [...], "contributor": [...]}``, each a list of
        dicts with the fields of :data:`PI_FIELDS` or :data:`CONTRIBUTOR_FIELDS`.

    Notes
    -----
    A list may be separated by ``;`` or ``,`` (originators use both). Roles,
    emails or urls that do not pair one-to-one with the names go whole to the first
    name, so nothing is dropped and no two people are merged by a shared value.
    """

    def get(name: str) -> object:
        return attrs.get(prefix + name)

    pis = []
    names = _split(get("principal_investigator"))
    columns = {
        f: _aligned(names, _split(get(PI_ATTRS[f])), get(PI_ATTRS[f]))
        for f in PI_FIELDS[1:]
    }
    for i, name in enumerate(names):
        if name:
            pis.append({"name": name, **{f: columns[f][i] for f in PI_FIELDS[1:]}})

    contributors = []
    for stem, role in (("creator", creator_role), ("file_creator", creator_role)):
        names = _split(get(f"{stem}_name"))
        emails = _aligned(names, _split(get(f"{stem}_email")), get(f"{stem}_email"))
        contributors += [
            {"name": n, "role": role, "email": e}
            for n, e in zip(names, emails, strict=True)
            if n
        ]
    names = _split(get("contributor_name"))
    roles = _aligned(names, _split(get("contributor_role")), get("contributor_role"))
    emails = _aligned(names, _split(get("contributor_email")), get("contributor_email"))
    contributors += [
        {"name": n, "role": r, "email": e}
        for n, r, e in zip(names, roles, emails, strict=True)
        if n
    ]
    return {"principal_investigator": pis, "contributor": contributors}


def union(
    groups: Iterable[Mapping[str, list[dict[str, str]]]],
) -> dict[str, list[dict[str, str]]]:
    """Join several people lists without repeats, in order of first appearance.

    Two entries are one person when they share an id, an email or a name (titles
    such as "Dr.", case, spaces and dots aside). A repeated contributor keeps every
    distinct role, joined by ``", "``; a missing email, url or id is filled from a
    later entry.

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
    for group in groups:
        for kind, fields in (
            ("principal_investigator", PI_FIELDS),
            ("contributor", CONTRIBUTOR_FIELDS),
        ):
            for person in group.get(kind, []):
                entry = {f: str(person.get(f, "") or "").strip() for f in fields}
                keys = _keys(entry)
                seen = next((p for p in out[kind] if _keys(p) & keys), None)
                if seen is None:
                    out[kind].append(entry)
                    continue
                for f in fields:
                    if f == "role":
                        roles = [r for r in seen["role"].split(", ") if r]
                        if entry["role"] and entry["role"] not in roles:
                            seen["role"] = ", ".join([*roles, entry["role"]])
                    elif not seen[f]:
                        seen[f] = entry[f]
    return out


def to_attrs(people: Mapping[str, list[dict[str, str]]]) -> dict[str, str]:
    """Return the OceanSITES attributes for a people list; a field nobody has is left out.

    Parameters
    ----------
    people : dict
        As :func:`people_from_attrs` or :func:`union` return.

    Returns
    -------
    dict of str to str
        ``principal_investigator*`` (comma-separated) and ``contributor_*``
        (semicolon-separated), each list in step with its names.
    """
    out: dict[str, str] = {}
    for kind, names, sep in (
        ("principal_investigator", PI_ATTRS, ", "),
        ("contributor", CONTRIBUTOR_ATTRS, "; "),
    ):
        entries = people.get(kind, [])
        for field, attr in names.items():
            values = [e.get(field, "") for e in entries]
            if any(values):
                out[attr] = sep.join(values)
    return out
