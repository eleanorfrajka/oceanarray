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

from collections.abc import Callable

import numpy as np
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.config.unit_tables import TABLES, UNIT_SPELLINGS_KEPT
from oceanarray.reports._vendored.units import Tables, UnitsError, resolve_units

# Re-exported so existing callers keep importing it from the processing layer.
__all__ = ["UnitsError", "to_canonical_units"]


def _canonical_by_standard_name() -> dict[str, str]:
    """Return the CF ``standard_name`` → canonical-units map, built from the registry.

    Built on each call (not cached at import) so a runtime override of
    :data:`parameters.VARIABLES` is honoured, consistent with the rest of
    :mod:`oceanarray.config.parameters`.  The build also guards the registry: two entries
    sharing a ``standard_name`` but declaring different canonical units would let the
    lookup pick one silently by dict order, so that is raised instead.

    Returns
    -------
    dict of str to str
        Standard name mapped to the canonical units string.

    Raises
    ------
    RuntimeError
        If two registry entries share a ``standard_name`` with different ``units``.
        This is a package bug in :data:`parameters.VARIABLES`, not a data problem, so
        it is deliberately not a :class:`UnitsError` — a stage's per-instrument
        ``UnitsError`` handler must not catch it and carry on.

    """
    mapping: dict[str, str] = {}
    for entry in params.VARIABLES.values():
        sn, units = entry.get("standard_name"), entry.get("units")
        if not sn or not units:
            continue
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
    """Return the canonical units for *var* in *ds*, or ``None`` if unregistered.

    Matches on the variable name first, then on its ``standard_name`` attribute.

    Parameters
    ----------
    var : str
        Variable or coordinate name in *ds*.
    ds : xarray.Dataset
        Dataset holding *var* (its ``standard_name`` attribute is consulted).
    sn_map : dict of str to str, optional
        The ``standard_name`` → canonical-units map from
        :func:`_canonical_by_standard_name`.  Pass the pre-built map when calling in
        a loop to avoid rebuilding it per variable; built here when omitted.

    Returns
    -------
    str or None
        The registry canonical units string, or ``None`` when *var* is not a
        registered physical variable.

    """
    if var in params.VARIABLES:
        return params.VARIABLES[var].get("units")
    if sn_map is None:
        sn_map = _canonical_by_standard_name()
    sn = ds[var].attrs.get("standard_name")
    return sn_map.get(sn)


def to_canonical_units(
    ds: xr.Dataset,
    *,
    tables: Tables = TABLES,
    log_fn: Callable[[str], None] | None = None,
    stage: int | str | None = None,
) -> xr.Dataset:
    """Rewrite each registered variable's units to its canonical spelling.

    The canonical spelling comes from the registry, :data:`parameters.VARIABLES` — by
    variable name, or by ``standard_name`` for an unregistered variable whose
    ``standard_name`` a registry entry carries.  *tables* decides only which other
    spellings are accepted for that canonical: the alias spellings, and the spellings
    scoped to the quantity (``PSU`` is salinity-only; the quantity is the registry's
    ``standard_name`` for a registered variable).  The value is multiplied by the factor
    and the attribute rewritten to the canonical string.  A genuine conversion (factor ≠
    1) stamps ``units_converted_from`` and ``units_conversion_factor`` for provenance; a
    pure spelling rewrite does not (the value is unchanged).  Temperature variables
    additionally get the CF-1.11 ``units_metadata = "temperature: on_scale"`` attribute.

    The pass is idempotent — a variable already carrying the canonical string is left
    unchanged — so it can be run on the way out of every stage without
    double-converting.  One deliberate exception to one spelling per quantity: a
    spelling listed in :data:`oceanarray.config.unit_tables.UNIT_SPELLINGS_KEPT` is left
    as the file has it, so N² is written as ``s-2`` or ``rad2 s-2``, whichever arrived.
    Unregistered engineering variables (no :data:`parameters.VARIABLES` entry and no
    matching ``standard_name``) are left untouched.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset to canonicalise in place (also returned).
    tables : Tables, optional
        The accepted spellings (aliases and scoped spellings) to resolve against;
        defaults to :data:`oceanarray.config.unit_tables.TABLES`.  The canonical itself
        always comes from the registry.
    log_fn : callable, optional
        Called with one message per variable whose units string changed.  A change at
        any stage other than 1 is annotated as written by a pre-0.5.0 stage 1.
    stage : int or str, optional
        Processing stage, used only to annotate the log and error messages — an int
        (1, 2, 3) or a label (``"stack"``, ``"grid"``).

    Returns
    -------
    xarray.Dataset
        The same dataset, with canonical units on every registered variable.

    Raises
    ------
    UnitsError
        If a registered variable carries a units string that is not a recognised
        spelling of its canonical, or no units attribute at all.  The pass never
        guesses or silently passes an unknown unit through.

    """
    sn_map = _canonical_by_standard_name()  # built once per call, not per variable
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
            if log_fn:
                msg = f"  {var}: {current} -> {canonical} (x{factor:g})"
                if stage is not None and stage != 1:
                    msg += " - written by a pre-0.5.0 stage 1; regenerate to silence"
                log_fn(msg)
        # CF 1.11: mark the temperature scale so a reader can tell a scale value from
        # a difference.  Landed at every boundary like the units string itself.
        if canonical == "degree_Celsius":
            ds[var].attrs["units_metadata"] = "temperature: on_scale"
    return ds
