"""Write a mooring YAML: the ``init`` template and :func:`save` with field comments."""

import logging
from collections.abc import Iterable, Mapping
from os import PathLike
from pathlib import Path

from oceanarray.mooring.check_yaml import check

logger = logging.getLogger(__name__)

# The one source for the commented ``init`` template: key -> inline description.
# ``save`` writes these as end-of-line comments; keep in sync with KNOWN_KEYS.
_FIELD_COMMENTS: dict[str, str] = {
    "name": "mooring identifier; every output file is named from it",
    "platform_code": "OceanSITES station code, no '_' (e.g. DUNE2); used by the export",
    "deployment_code": "YYYYMM of deployment_time (e.g. 202607); used by the export",
    "year": "deployment year, e.g. 2026",
    "waterdepth": "water depth at anchor in metres",
    "deployment_cruise": "cruise ID, e.g. MSM142",
    "deployment_ship": "ship name, e.g. MS Merian",
    "recovery_cruise": "cruise ID at recovery (omit if same cruise)",
    "recovery_ship": "ship name at recovery (omit if same ship)",
    "latitude": "planned position, e.g. 65 29.840 N",
    "longitude": "planned position, e.g. 029 24.600 W",
    "deployment_time": "ISO-8601, e.g. '2026-05-07T17:06'",
    "recovery_time": "ISO-8601, e.g. '2026-07-10T17:45'",
}

# Per-instrument field descriptions. ``save`` comments the first clamp entry that
# carries each field, so the explanation appears once (as the hand-written stub did).
_INSTRUMENT_COMMENTS: dict[str, str] = {
    "serial": (
        "if comma-separated (e.g. '16430, R01-024') the first token is the "
        "primary serial used in filenames/output; the rest is beacon_id. "
        "Characters illegal in filenames are stripped."
    ),
    "hab": "height above bottom (m) of transducer",
    "header": ".hdr file for T-matrix (same base name if null)",
    "clock_offset": "seconds added to instrument time (positive: clock slow)",
    "computer_clock_at_recovery": (
        "UTC when the clock was read; correction = computer - instrument"
    ),
    "hab_bottom": "HAB of transducer face (downward-looking)",
    "hab_top": "HAB of top of housing",
    "orientation": "down or up",
}

# One example clamp block per instrument type; a user fills these in and deletes
# the unused ones. Key order is the order written to the file.
_TEMPLATE_INSTRUMENTS: list[dict] = [
    {
        "instrument": "aquadopp",
        "serial": None,
        "hab": None,
        "sample_interval_seconds": None,
        "filename": None,
        "file_type": "nortek-raw",
        "header": None,
        "clock_offset": 0,
        "computer_clock_at_recovery": None,
        "instrument_clock_at_recovery": None,
    },
    {
        "instrument": "microcat",
        "serial": None,
        "hab": None,
        "sample_interval_seconds": None,
        "filename": None,
        "file_type": "sbe-cnv",
        "clock_offset": 0,
        "computer_clock_at_recovery": None,
        "instrument_clock_at_recovery": None,
    },
    {
        "instrument": "rbrsolo",
        "serial": None,
        "hab": None,
        "sample_interval_seconds": None,
        "filename": None,
        "file_type": "rbr-rsk",
        "clock_offset": 0,
        "computer_clock_at_recovery": None,
        "instrument_clock_at_recovery": None,
    },
    {
        "instrument": "ADCP",
        "serial": None,
        "hab_bottom": None,
        "hab_top": None,
        "sample_interval_seconds": None,
        "filename": None,
        "file_type": "rdi-raw",
        "orientation": "down",
        "clock_offset": 0,
        "computer_clock_at_recovery": None,
        "instrument_clock_at_recovery": None,
    },
]


def template(name: str, *, instruments: list[dict] | None = None) -> dict:
    """Return a skeleton mooring YAML for *name*, as a plain dict.

    The required and recommended keys are present with ``None`` values for a user
    to fill in; ``clamp`` carries one example block per instrument type (or
    *instruments* if given). :func:`save` renders it with the field comments.

    Parameters
    ----------
    name : str
        The mooring name (becomes the ``name`` field; matches the directory).
    instruments : list of dict or None, optional
        Clamp entries to use instead of the one-per-type examples.

    Returns
    -------
    dict
        The skeleton mooring YAML, keys in write order.
    """
    doc: dict = {"name": name}
    for key in (
        "platform_code",
        "year",
        "waterdepth",
        "deployment_cruise",
        "deployment_ship",
        "recovery_cruise",
        "recovery_ship",
        "latitude",
        "longitude",
        "deployment_time",
        "deployment_code",
        "recovery_time",
    ):
        doc[key] = None
    examples = _TEMPLATE_INSTRUMENTS if instruments is None else instruments
    doc["clamp"] = [dict(entry) for entry in examples]
    return doc


def _format_optional_blocks() -> str:
    """Return the commented-out ``stack:``, ``grid:``, ``people:`` and ``source:`` blocks."""
    from oceanarray.config import parameters as params

    return "\n".join(
        [
            "optional: uncomment to change the stack and grid settings",
            "stack:",
            f"  dt_seconds: {params.STACK_DT_SECONDS:g}  # time step of the stack, seconds",
            f"  mask_flags: {list(params.MASK_FLAGS)}  # flags hidden from the pages "
            "and the grid; or {default: [4], velocity: [3, 4]}",
            "grid:",
            f"  pmin: {params.GRID_PMIN_DBAR:g}  # shallowest grid level, dbar",
            f"  pmax: {params.GRID_PMAX_DBAR:g}  # deepest grid level, dbar",
            f"  dp: {params.GRID_DP_DBAR:g}  # grid spacing, dbar",
            "  # mask_flags: [3, 4]  # the grid may leave out more than the stack hides",
            "",
            "optional: who and where the data came from, for files that do not say",
            "people:  # used when the data files name no one",
            "  principal_investigator: Given Surname  # comma-separated",
            "  principal_investigator_email: name@example.org",
            "  principal_investigator_id: https://orcid.org/0000-0000-0000-0000",
            "  contributor_name: Given Surname; Given Surname  # semicolon-separated",
            "  contributor_role: data processing; technician",
            "  people_source: where these names came from",
            "source:  # fills what the data files do not say",
            "  doi: 10.xxxx/xxxxx  # the data package's DOI",
            "  citation: how the source asks to be cited",
            "  license: the source's license",
            "  acknowledgement: funding and acknowledgement",
            "  source_note: where these values came from",
        ]
    )


def save(
    mooring: Mapping,
    path: str | PathLike,
    *,
    overwrite: bool = False,
    optional_blocks: bool = False,
) -> Path:
    """Write a mooring YAML to a file with field comments.

    Lenient by design — a mooring YAML being drafted may be incomplete, so ``save``
    runs :func:`check` and logs a warning on any ERROR but still writes (unlike
    :func:`load`, which refuses an invalid mooring YAML about to be processed). The
    field comments come from the module's comment tables, so they cannot drift
    from the validator.

    Parameters
    ----------
    mooring : Mapping
        The mooring YAML to write (e.g. from :func:`template`).
    path : str or os.PathLike
        Destination ``*.mooring.yaml`` path.
    overwrite : bool, default False
        Overwrite an existing file; otherwise a ``FileExistsError`` is raised.
    optional_blocks : bool, default False
        Also write the optional ``stack:`` and ``grid:`` blocks, commented out with
        their default values, before ``clamp`` (``oceanarray init`` does).

    Returns
    -------
    pathlib.Path
        The path written.

    Raises
    ------
    FileExistsError
        If *path* exists and *overwrite* is False.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists")

    errors = [i.message for i in check(mooring) if i.level == "ERROR"]
    if errors:
        logger.warning(
            "saving an incomplete mooring YAML (%d issue(s) to resolve): %s",
            len(errors),
            "; ".join(errors),
        )

    def _block(
        items: Iterable[tuple[str, object]],
        comments: Mapping[str, str],
        seen: set[str] | None = None,
    ) -> CommentedMap:
        """CommentedMap of *items*; comment keys in *comments* (first occurrence if *seen*)."""
        block = CommentedMap()
        for key, value in items:
            block[key] = value
            if key in comments and (seen is None or key not in seen):
                block.yaml_add_eol_comment(comments[key], key)
                if seen is not None:
                    seen.add(key)
        return block

    data = dict(mooring)
    clamp = data.pop("clamp", None)
    doc = _block(data.items(), _FIELD_COMMENTS)
    if clamp is not None:
        seen: set[str] = set()
        doc["clamp"] = CommentedSeq(
            _block(entry.items(), _INSTRUMENT_COMMENTS, seen) for entry in clamp
        )
        if optional_blocks:
            doc.yaml_set_comment_before_after_key(
                "clamp", before=_format_optional_blocks(), indent=0
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = YAML()
    writer.default_flow_style = False
    writer.width = 120
    # newline="\n": deterministic LF on every platform — Windows text mode would
    # otherwise write CRLF and break the byte-identical init-template golden.
    with path.open("w", newline="\n") as fh:
        writer.dump(doc, fh)
    return path
