"""Builder for the masthead page-nav mapping — oceanarray's mooring report pages.

The nav is a plain mapping rendered by the vendored ``templates/_nav.html`` macros
and validated by :func:`oceanarray.reports._vendored.page_model.validate_nav`.  This module is
the one place that knows oceanarray's report pages and their roles; the *shape* it
builds is the shared cross-package contract, so each sibling package carries its own
``_navs.py`` building the same shape for its own pages.

A pill's colour is its *role* (a :data:`~oceanarray.reports._vendored.design_tokens.ROLE_ACCENT`
key).  oceanarray's masthead is a solid ``--ocean`` navy, which is the same dark as the
``entity``/``collection``/``component`` roles — so pills in those roles vanish against it.
The pages therefore take roles that render *distinctly* on navy: Summary ``entity`` (dark,
reads as the bold label), Stack ``landing`` (blue), Grid ``aggregate-a`` (purple),
instruments ``aggregate-b`` (green), the Array up-link ``up`` (grey-blue).  This diverges
from the family "three dark pills" scheme (ctdcast/glidertest render on per-page coloured
mastheads, so dark pills show there); oceanarray's uniform navy masthead needs the contrast.
"""

from __future__ import annotations

from typing import Any

from ._vendored.page_model import validate_nav

#: Each known instrument type (keyed by the lower-cased ``instr_type``): its model
#: label and its function. Model labels upper-case acronyms and model numbers and
#: title-case product names, never ``str.capitalize`` ("Adcp"); an unknown type falls
#: back to upper-case. The function names a type on the pages that mix deployments
#: (Stack cat, Grid cat, year page); the model stays on the deployment and
#: instrument pages.
_TYPE_LABELS: dict[str, tuple[str, str | None]] = {
    "adcp": ("ADCP", "ADCP"),
    "rdi": ("RDI", "ADCP"),
    "aquadopp": ("Aquadopp", "current meter"),
    "nortek": ("Nortek", "current meter"),
    "microcat": ("MicroCAT", "CTD"),
    "currentmeter": ("Current meter", "current meter"),
    "rbr": ("RBR", None),
    "rbrduet": ("RBRduet", "temperature logger"),
    "rbrsolo": ("RBRsolo", "temperature logger"),
    "sbe16": ("SBE16", "CTD"),
    "sbe37": ("SBE37", "CTD"),
    "sbe56": ("SBE56", "temperature logger"),
    "thermistor": ("Thermistor", "temperature logger"),
    "tr1050": ("TR-1050", "temperature logger"),
    "seapoint": ("Seapoint", None),
}

#: The abbreviation of each function.
FUNCTION_ABBR: dict[str, str] = {
    "CTD": "CTD",
    "current meter": "CM",
    "temperature logger": "T",
    "ADCP": "ADCP",
}

#: Display order of instrument types in counts and summaries; other types follow
#: alphabetically.
_TYPE_ORDER = [
    "microcat",
    "aquadopp",
    "rbrsolo",
    "tr1050",
    "seapoint",
    "adcp",
]


def _type_label(itype: str) -> str:
    """Return the model label for an instrument type, upper-casing the unknown."""
    return _TYPE_LABELS.get(itype, (itype.upper(),))[0]


def function_word(itype: str) -> str:
    """Return the type's function ("current meter"), else its model label.

    Parameters
    ----------
    itype : str
        The lower-cased instrument type.

    Returns
    -------
    str
    """
    return _TYPE_LABELS.get(itype, (None, None))[1] or _type_label(itype)


def function_abbr(itype: str) -> str:
    """Return the type's function abbreviation ("CM"), else its model label."""
    word = function_word(itype)
    return FUNCTION_ABBR.get(word, word)


def _state(*, is_current: bool, is_missing: bool) -> str:
    """Return a pill state, with ``current`` outranking ``missing``."""
    # The page being viewed always exists, so it is "current" even if flagged missing.
    if is_current:
        return "current"
    return "missing" if is_missing else "link"


def mooring_nav(
    mooring_name: str,
    instruments: list[dict[str, Any]],
    *,
    stack_exists: bool = True,
    grid_exists: bool = True,
    current: str = "",
    in_instrument_subdir: bool = False,
    array_href: str | None = None,
) -> dict:
    """Build the masthead nav mapping for one mooring report page.

    One "Reports" row (Summary, Stack, Grid) and one row per instrument type
    (serial pills).  Exactly the page being rendered is marked ``current``; a Stack
    or Grid product that does not exist, or an instrument with no processed stage
    files, is marked ``missing``.  An "Array" up-link is prepended when *array_href*
    is given.  Hrefs carry the same ``../`` / ``instrument/`` prefixing the previous
    HTML builder used.

    Parameters
    ----------
    mooring_name : str
        Mooring identifier used to build sibling report filenames.
    instruments : list of dict
        Each dict has ``serial`` and ``instr_type`` (or ``instrument``) keys, and an
        optional ``stages`` mapping; the format produced by the report builders.
    stack_exists, grid_exists : bool, default True
        Whether the stack / grid report is available (else the pill is ``missing``).
    current : str, default ""
        Which page this is: ``"summary"``, ``"stack"``, ``"grid"``, or a serial
        string for a per-instrument page.  Its pill is marked ``current``.
    in_instrument_subdir : bool, default False
        True when rendered inside ``report/instrument/`` (one level deeper): the
        Summary/Stack/Grid hrefs gain a ``../`` prefix and instrument links stay
        flat; otherwise instrument links gain an ``instrument/`` prefix.
    array_href : str or None, default None
        Relative href to the array-level report; when given, an "Array" up-link
        pill (role ``up``) is prepended.

    Returns
    -------
    dict
        The nav mapping, already passed through :func:`validate_nav`.
    """
    pfx = "../" if in_instrument_subdir else ""
    ipfx = "" if in_instrument_subdir else "instrument/"

    reports_pills = [
        {
            "label": "Summary",
            "href": f"{pfx}{mooring_name}_report.html",
            "role": "entity",
            "state": _state(is_current=current == "summary", is_missing=False),
        },
        {
            "label": "Stack",
            "href": f"{pfx}{mooring_name}_stack_report.html",
            "role": "landing",
            "state": _state(is_current=current == "stack", is_missing=not stack_exists),
        },
        {
            "label": "Grid",
            "href": f"{pfx}{mooring_name}_grid_report.html",
            "role": "aggregate-a",
            "state": _state(is_current=current == "grid", is_missing=not grid_exists),
        },
    ]
    rows = [{"label": "Reports:", "pills": reports_pills}]

    by_type: dict[str, list[dict[str, Any]]] = {}
    for instr in instruments:
        itype = str(instr.get("instr_type", instr.get("instrument", "other"))).lower()
        by_type.setdefault(itype, []).append(instr)

    for itype, group in by_type.items():
        pills = []
        for instr in group:
            ser = str(instr.get("serial", ""))
            stages = instr.get("stages", {})
            has_report = any(stages.values()) if stages else True
            pills.append(
                {
                    "label": ser,
                    "href": f"{ipfx}{mooring_name}_{ser}_report.html",
                    "role": "aggregate-b",
                    "state": _state(
                        is_current=current == ser, is_missing=not has_report
                    ),
                }
            )
        rows.append({"label": f"{_type_label(itype)}:", "pills": pills})

    back = (
        {"label": "Array", "href": array_href, "role": "up", "state": "link"}
        if array_href
        else None
    )

    # A degenerate config can match ``current`` on two pills — duplicate serials
    # across instruments, or a serial equal to "summary"/"stack"/"grid".  The
    # vendored ``validate_nav`` forbids more than one ``current``; keep the first
    # in render order and demote the rest to a link rather than abort the whole
    # report.  (Such a config also collides on the per-instrument report filename;
    # that is a separate data problem, not the nav's to resolve.)
    seen_current = False
    for row in rows:
        for pill in row["pills"]:
            if pill["state"] == "current":
                if seen_current:
                    pill["state"] = "link"
                seen_current = True

    nav = {"rows": rows, "back": back, "inventory": []}
    validate_nav(nav)
    return nav
