"""``mask_flags``: which flagged values are hidden from the pages and the grid.

The stack keeps every value with its flags beside it: ours (``<var>_qc``) and the
originator's translated to our table (``<var>_qc_originator_mapped``). One rule,
``mask_flags``, decides what is not shown on the stack and Stack cat pages and not
gridded: a value is left out when **either** flag is in the rule. The stack records
its rule as ``stack_mask_flags``; the grid uses it and may only add flags.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

import numpy as np
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.pipeline.qc.originator import MAPPED_SUFFIX

#: The velocity variables: one ``mask_flags`` key, ``velocity``, sets the flags
#: masked in all of them (by ``velocity_flag``, the ADCP seabed, percent-good and
#: error-velocity flags, and each component's own flags).
VELOCITY_GROUP: frozenset[str] = frozenset(
    {
        "east_velocity",
        "north_velocity",
        "up_velocity",
        "current_speed",
        "current_direction",
    }
)

#: The variables whose flags the stack carries, the pages count and the flag strip
#: draws: one list, so a flag the stack carries is never left off the page.
FLAGGED_VARIABLES: tuple[str, ...] = (
    "temperature",
    "salinity",
    "conductivity",
    "pressure",
    "east_velocity",
    "north_velocity",
    "up_velocity",
    "current_speed",
    "current_direction",
    "dissolved_oxygen",
    "turbidity",
)

#: The ADCP flags that mask the velocity group, as well as ``velocity_flag``.
ADCP_VELOCITY_QC: tuple[str, ...] = (
    "seabed_qc",
    "percent_good_qc",
    "error_velocity_qc",
)

#: The attribute in which a stack (or Stack cat) records its rule.
STACK_ATTR = "stack_mask_flags"

#: Names the flag sets the rule is applied to, recorded beside it.
FLAG_SOURCES = f"<var>_qc (oceanarray), <var>{MAPPED_SUFFIX} (originator)"

Rule = dict[str, tuple[int, ...]]


def _flag_list(key: str, flags: object) -> tuple[int, ...]:
    """Return *flags* as a sorted tuple of our flag values, or raise ValueError."""
    if isinstance(flags, int) and not isinstance(flags, bool):
        flags = (flags,)
    if isinstance(flags, str) or not isinstance(flags, Iterable):
        raise ValueError(
            f"mask_flags {key!r}: expected a list of flag values, got {flags!r}"
        )
    out = []
    for flag in flags:
        if (
            isinstance(flag, bool)
            or not isinstance(flag, int | np.integer)
            or int(flag) not in params.QC_FLAG_VALUES
        ):
            raise ValueError(
                f"mask_flags {key!r}: {flag!r} is not a flag value "
                f"({', '.join(map(str, params.QC_FLAG_VALUES))})"
            )
        out.append(int(flag))
    return tuple(sorted(set(out)))


def resolve_mask_flags(
    mask_flags: Iterable[int] | Mapping[str, Iterable[int]] | None = None,
) -> Rule:
    """Return a rule: a default and per-variable exceptions.

    Parameters
    ----------
    mask_flags : iterable of int, or Mapping of str to iterable of int, or None
        ``None`` for the default (``parameters.MASK_FLAGS``: 4); a list of flags for
        every variable; or a mapping with an optional ``"default"`` and one key per
        exception: a variable name or ``"velocity"`` for the velocity group
        (:data:`VELOCITY_GROUP`).

    Returns
    -------
    dict of str to tuple of int
        Always holds ``"default"``; the other keys as given.

    Raises
    ------
    ValueError
        An unknown key (or a member of the velocity group named on its own), or a
        value that is not a list of flag values.
    """
    out = {"default": tuple(params.MASK_FLAGS)}
    if mask_flags is None:
        return out
    given = (
        dict(mask_flags) if isinstance(mask_flags, Mapping) else {"default": mask_flags}
    )
    known = (set(params.VARIABLES) - VELOCITY_GROUP) | {"default", "velocity"}
    for key, flags in given.items():
        if key in VELOCITY_GROUP:
            raise ValueError(
                f"mask_flags {key!r}: the velocity variables share one key, 'velocity'"
            )
        if key not in known:
            raise ValueError(
                f"mask_flags {key!r} is not a variable; expected 'default', "
                "'velocity' or a variable name (e.g. 'temperature', 'dissolved_oxygen')"
            )
        out[key] = _flag_list(key, flags)
    return out


def flags_for(rule: Mapping[str, tuple[int, ...]], var: str) -> tuple[int, ...]:
    """Return the flags *rule* hides for variable *var*.

    Parameters
    ----------
    rule : Mapping
        From :func:`resolve_mask_flags`.
    var : str
        A variable name.

    Returns
    -------
    tuple of int
        The variable's own entry (``velocity`` for the velocity group), else the
        default.
    """
    key = "velocity" if var in VELOCITY_GROUP else var
    return tuple(rule.get(key, rule["default"]))


def to_json(rule: Mapping[str, tuple[int, ...]]) -> str:
    """Return *rule* as the JSON a file records (``{"default": [4], ...}``).

    Parameters
    ----------
    rule : Mapping
        From :func:`resolve_mask_flags`.

    Returns
    -------
    str
        Keys in the order given, flag lists sorted.
    """
    return json.dumps({k: list(v) for k, v in rule.items()})


def rule_words(rule: Mapping[str, tuple[int, ...]], *, table: bool = False) -> str:
    """Return a flag rule in words.

    Parameters
    ----------
    rule : Mapping
        From :func:`resolve_mask_flags`.
    table : bool, optional
        The report's settings form, ``"4; velocity 3, 4"``, instead of the
        history's ``"4 (velocity 3, 4)"`` (which also spells keys with spaces).

    Returns
    -------
    str
    """

    def flags_text(flags: tuple[int, ...]) -> str:
        return ", ".join(map(str, flags)) or "none"

    default = flags_text(rule["default"])
    others = [
        f"{key if table else key.replace('_', ' ')} {flags_text(flags)}"
        for key, flags in rule.items()
        if key != "default"
    ]
    if table:
        return "; ".join([default, *others])
    return f"{default} ({', '.join(others)})" if others else default


def from_attrs(attrs: Mapping[str, object], key: str = STACK_ATTR) -> Rule:
    """Return the rule a file records, or the default when it records none.

    Parameters
    ----------
    attrs : Mapping
        The file's global attributes.
    key : str, optional
        The attribute (``stack_mask_flags``, or ``grid_mask_flags`` on a grid).

    Returns
    -------
    dict of str to tuple of int
        The recorded rule; the default for a file written before rules were
        recorded.
    """
    text = attrs.get(key)
    if not text:
        return resolve_mask_flags(None)
    return resolve_mask_flags(json.loads(str(text)))


def combine(
    stack_rule: Mapping[str, tuple[int, ...]],
    grid_rule: Mapping[str, tuple[int, ...]] | None,
) -> Rule:
    """Return the grid's rule: the stack's, with what ``grid: mask_flags`` adds.

    Parameters
    ----------
    stack_rule : Mapping
        The stack's recorded rule.
    grid_rule : Mapping or None
        The grid's own rule, or None to use the stack's.

    Returns
    -------
    dict of str to tuple of int
        The grid's rule.

    Raises
    ------
    ValueError
        When *grid_rule* would grid a value the stack's pages hide: the grid can
        only add flags.
    """
    if grid_rule is None:
        return dict(stack_rule)
    keys = ["default", *[k for k in (*stack_rule, *grid_rule) if k != "default"]]
    out: Rule = {}
    for key in dict.fromkeys(keys):
        want = set(flags_for(grid_rule, key))
        have = set(flags_for(stack_rule, key))
        if not have <= want:
            raise ValueError(
                f"mask_flags {key!r}: the grid's {sorted(want)} would grid values "
                f"flagged {sorted(have - want)} that the stack's rule hides "
                f"({sorted(have)}); the grid can only add flags"
            )
        out[key] = tuple(sorted(want))
    return out


def _flag_values(ds: xr.Dataset, name: str, like: xr.DataArray) -> np.ndarray | None:
    """Return flag variable *name* laid out like *like*, or None when absent."""
    if name not in ds.data_vars or set(ds[name].dims) != set(like.dims):
        return None
    return ds[name].transpose(*like.dims).values


def flagged(
    ds: xr.Dataset, var: str, rule: Mapping[str, tuple[int, ...]]
) -> np.ndarray:
    """Return where *rule* hides values of *var*: either flag set in the rule.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack, Stack cat or instrument file.
    var : str
        The data variable.
    rule : Mapping
        From :func:`resolve_mask_flags` or :func:`from_attrs`.

    Returns
    -------
    numpy.ndarray of bool
        The shape of ``ds[var]``; True where a flag of the variable (and, for the
        velocity group, ``velocity_flag`` and the ADCP flags) is in the rule.
    """
    like = ds[var]
    hide = flags_for(rule, var)
    out = np.zeros(like.shape, dtype=bool)
    names = [f"{var}_qc", f"{var}{MAPPED_SUFFIX}"]
    if var in VELOCITY_GROUP:
        names += ["velocity_flag", *ADCP_VELOCITY_QC]
        names += [f"{v}{MAPPED_SUFFIX}" for v in sorted(VELOCITY_GROUP)]
    for name in names:
        values = _flag_values(ds, name, like)
        if values is not None:
            out |= np.isin(values, hide)
    return out


def masked(
    ds: xr.Dataset, var: str, rule: Mapping[str, tuple[int, ...]] | None = None
) -> np.ndarray:
    """Return the values of *var* as float, NaN where *rule* hides them.

    Parameters
    ----------
    ds : xarray.Dataset
        A stack, Stack cat or instrument file.
    var : str
        The data variable.
    rule : Mapping, optional
        The rule; by default the one *ds* records (``stack_mask_flags``), else
        ``parameters.MASK_FLAGS``.

    Returns
    -------
    numpy.ndarray
        A float copy of ``ds[var].values``.
    """
    rule = from_attrs(ds.attrs) if rule is None else rule
    values = ds[var].values.astype(np.float64)
    values[flagged(ds, var, rule)] = np.nan
    return values


def union(
    stack_rule: Mapping[str, tuple[int, ...]], extra: Mapping[str, tuple[int, ...]]
) -> Rule:
    """Return *stack_rule* with what *extra* adds, key by key (never fewer flags).

    Parameters
    ----------
    stack_rule : Mapping
        The stack's rule.
    extra : Mapping
        Flags to add (e.g. the description's ``grid: mask_flags``).

    Returns
    -------
    dict of str to tuple of int
        For every key in either: the flags of both.
    """
    keys = ["default", *[k for k in (*stack_rule, *extra) if k != "default"]]
    return {
        key: tuple(
            sorted(
                set(flags_for(stack_rule, key))
                | set(flags_for({"default": (), **extra}, key))
            )
        )
        for key in dict.fromkeys(keys)
    }


def stack_rule(
    mooring: Mapping[str, object],
    given: Iterable[int] | Mapping[str, Iterable[int]] | None = None,
) -> Rule:
    """Return the stack's rule: the call, else ``stack: mask_flags``, else the default.

    Parameters
    ----------
    mooring : Mapping
        The loaded mooring description.
    given : iterable of int, or Mapping, optional
        The rule the caller (or ``--mask-flags``) gave.

    Returns
    -------
    dict of str to tuple of int
        The resolved rule.

    Raises
    ------
    ValueError
        When the chosen value is not a valid rule.
    """
    if given is not None:
        return resolve_mask_flags(given)
    block = mooring.get("stack") or {}
    value = block.get("mask_flags") if isinstance(block, Mapping) else None
    return resolve_mask_flags(value)
