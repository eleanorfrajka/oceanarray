"""``mask_flags``: which flagged values are hidden from the pages and the grid.

The stack keeps every value with its flags beside it: ours (``<var>_qc``) and the
originator's translated to our table (``<var>_qc_originator_mapped``). One rule,
``mask_flags``, decides what is not shown on the stack and Stack cat pages and not
gridded: a value is left out when **either** flag is in the rule. The stack records
its rule as ``stack_mask_flags``; the grid uses it and may only add flags. The rule
itself (parsing, combining, recording) is :mod:`oceanarray.mask_rules`.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import xarray as xr

from oceanarray.mask_rules import (
    ADCP_VELOCITY_QC,
    VELOCITY_GROUP,
    flags_for,
    from_attrs,
)
from oceanarray.originator_flags import MAPPED_SUFFIX


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
        From :func:`~oceanarray.mask_rules.resolve_mask_flags` or
        :func:`~oceanarray.mask_rules.from_attrs`.

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
