"""Canonicalise variable units on the way out of each processing stage.

The canonical spelling per variable is the registry's
(:data:`oceanarray.config.parameters.VARIABLES`). Which reader spellings convert to it, and
with what factor, is the shared family data in
:mod:`oceanarray.reports._vendored.units_tables`, read through
:func:`oceanarray.reports._vendored.units.resolve_units`; oceanarray's view of it, with
its own additions, is :data:`oceanarray.config.unit_tables.TABLES`, and a test keeps the
registry and the tables' canonicals in agreement.  This module holds the transform that
applies them, :func:`to_canonical_units`, run at every stage boundary so each written
file carries the registry spelling for each quantity (N² excepted, see
:data:`~oceanarray.config.unit_tables.UNIT_SPELLINGS_KEPT`).

The display spelling (``mS cm-1`` → ``mS/cm``) is a separate concern, derived from the
canonical string by :func:`oceanarray.config.unit_tables.unit_label`; it is never
stored.
"""

import json
import logging
from collections.abc import Callable

import numpy as np
import xarray as xr

from oceanarray import logger
from oceanarray.config import parameters as params
from oceanarray.config.unit_tables import TABLES, UNIT_SPELLINGS_KEPT
from oceanarray.reports._vendored.units import Tables, UnitsError, resolve_units

log = logging.getLogger(__name__)

# Re-exported so existing callers keep importing it from the processing layer.
__all__ = ["UnitsError", "to_canonical_units"]


def _canonical_by_standard_name() -> dict[str, str]:
    """Return the CF ``standard_name`` → canonical-units map from the registry."""
    # Built per call, not cached at import, so a runtime override of
    # parameters.VARIABLES is honored.
    mapping: dict[str, str] = {}
    for entry in params.VARIABLES.values():
        sn, units = entry.get("standard_name"), entry.get("units")
        if not sn or not units:
            continue
        # Two entries sharing a standard_name with different units would let the
        # lookup pick one silently by dict order. This is a registry bug, not a
        # data problem, so not a UnitsError: per-instrument handlers must not catch it.
        if mapping.setdefault(sn, units) != units:
            raise RuntimeError(
                f"registry inconsistency: standard_name {sn!r} maps to both "
                f"{mapping[sn]!r} and {units!r} — one quantity cannot have two "
                f"canonical units (a per-mass/per-volume mismatch would land here)."
            )
    return mapping


def _canonical_units_for(
    var: str, ds: xr.Dataset, sn_map: dict[str, str] | None = None
) -> str | None:
    """Return the canonical units for *var* in *ds*, or ``None`` if unregistered."""
    if var in params.VARIABLES:
        return params.VARIABLES[var].get("units")
    # Fall back to standard_name; callers in a loop pass a pre-built sn_map.
    if sn_map is None:
        sn_map = _canonical_by_standard_name()
    sn = ds[var].attrs.get("standard_name")
    return sn_map.get(sn)


#: The global attribute holding the in-file ledger of units changes.
UNITS_LEDGER_ATTR = "units_converted"


def to_canonical_units(
    ds: xr.Dataset,
    *,
    tables: Tables = TABLES,
    log_fn: Callable[[str], None] | None = None,
    stage: int | str | None = None,
) -> xr.Dataset:
    """Rewrite each registered variable's units to its canonical spelling, in place; idempotent.

    The canonical comes from :data:`parameters.VARIABLES` (by name, or by
    ``standard_name``); *tables* says which other spellings are accepted (aliases, and
    spellings scoped to a quantity, e.g. ``PSU`` for salinity). Values are scaled by the
    factor; a true conversion adds ``units_converted_from`` / ``units_conversion_factor``,
    and every change is recorded in ``units_converted`` (JSON, kept across stages).
    Temperatures get ``units_metadata = "temperature: on_scale"``. Spellings in
    :data:`~oceanarray.config.unit_tables.UNIT_SPELLINGS_KEPT` and unregistered variables
    are left as they are.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset to canonicalise (also returned).
    tables : Tables, optional
        Accepted spellings; default :data:`oceanarray.config.unit_tables.TABLES`.
    log_fn : callable, optional
        Deprecated (removed in 0.7.0): configure logging instead.
    stage : int or str, optional
        Stage or label (``"stack"``), for the messages.

    Returns
    -------
    xarray.Dataset

    Raises
    ------
    UnitsError
        A registered variable has no units or an unrecognised spelling.
    """
    with logger.deprecated_log_fn(log_fn):
        return _canonicalise_units(ds, tables=tables, stage=stage)


def _canonicalise_units(
    ds: xr.Dataset,
    *,
    tables: Tables = TABLES,
    stage: int | str | None = None,
) -> xr.Dataset:
    """Implement :func:`to_canonical_units` (without ``log_fn``)."""
    sn_map = _canonical_by_standard_name()  # built once per call, not per variable
    changed: dict[str, dict[str, object]] = {}
    for var in list(ds.variables):
        canonical = _canonical_units_for(var, ds, sn_map)
        if canonical is None:
            continue
        current = str(ds[var].attrs.get("units", "")).strip()
        if current != canonical:
            if not current:
                raise UnitsError(
                    f"{var!r} is a registered variable but carries no units "
                    f"attribute; its canonical is {canonical!r}. The reader dropped "
                    f"the units — fix it there; no silent substitution."
                )
            # A registered variable's quantity is the registry's, not whatever
            # standard_name the reader stamped (seasenselib's RDI reader writes
            # sea_water_salinity on practical salinity); the file's attribute decides
            # only for a variable matched by standard_name.
            if var in params.VARIABLES:
                standard_name = params.VARIABLES[var].get("standard_name")
            else:
                standard_name = ds[var].attrs.get("standard_name")
            if current in UNIT_SPELLINGS_KEPT.get(standard_name, ()):
                continue  # a kept spelling (N² with or without rad2) — not rewritten
            where = f" (stage {stage})" if stage else ""
            try:
                _, factor = resolve_units(
                    current, standard_name, tables=tables, canonical=canonical
                )
            except UnitsError as exc:
                raise UnitsError(f"{var!r}{where}: {exc}") from exc
            attrs = dict(ds[var].attrs)
            if factor != 1.0:
                converted = ds[var].copy(
                    data=np.asarray(ds[var].values, dtype=float) * factor
                )
                # The value scale changed, so any integer packing carried from the
                # reader is no longer valid; drop it and let the writer re-encode.
                for key in ("dtype", "scale_factor", "add_offset"):
                    converted.encoding.pop(key, None)
                ds[var] = converted
                attrs["units_converted_from"] = current
                attrs["units_conversion_factor"] = factor
            attrs["units"] = canonical
            ds[var].attrs = attrs
            changed[var] = {"from": current, "to": canonical, "factor": factor}
            msg = f"  {var}: {current} -> {canonical} (x{factor:g})"
            if stage is not None and stage != 1:
                msg += " - written by a pre-0.5.0 stage 1; regenerate to silence"
            # A real conversion (factor != 1) is worth an INFO line; a pure spelling
            # rewrite is DEBUG detail.
            (log.info if factor != 1.0 else log.debug)(msg)
        # CF 1.11: mark the temperature scale so a reader can tell a scale value from
        # a difference.  Landed at every boundary like the units string itself.
        if canonical == "degree_Celsius":
            ds[var].attrs["units_metadata"] = "temperature: on_scale"
    if changed:
        # In-file ledger of every units change, carried forward through the stages
        # (a later change of the same variable replaces its entry).
        ledger = json.loads(str(ds.attrs.get(UNITS_LEDGER_ATTR, "{}")) or "{}")
        ledger.update(changed)
        ds.attrs[UNITS_LEDGER_ATTR] = json.dumps(ledger, sort_keys=True)
    return ds
