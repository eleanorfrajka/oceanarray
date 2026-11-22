"""oceanarray's view of the family units tables.

The one place oceanarray speaks about units.  Three override dicts are folded over the
vendored family defaults (:data:`oceanarray.reports._vendored.units.DEFAULT_TABLES`)
into :data:`TABLES`, which the storage pass
(:func:`oceanarray.pipeline.units.to_canonical_units`) and the display entry point
(:func:`unit_label`) both take.

An override entry identical to the family default is a no-op and ``merge_tables`` warns
about it, so a restatement cannot silently shadow a later change to the base; only
oceanarray's real differences from the family are live here.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from oceanarray.exceptions import OceanarrayError
from oceanarray.reports._vendored.units import (
    DEFAULT_TABLES,
    Tables,
    format_units,
    merge_tables,
)
from oceanarray.reports._vendored.units import UnitsError as _SharedUnitsError
from oceanarray.reports._vendored.units import resolve_units as _shared_resolve_units

log = logging.getLogger(__name__)


class UnitsError(OceanarrayError, _SharedUnitsError):
    """Raised for a unit oceanarray cannot read or convert.

    The shared report layer's ``UnitsError`` (still a ``ValueError``) placed under
    :class:`~oceanarray.exceptions.OceanarrayError`; ``except`` on either catches it.
    """


def resolve_units(
    units: str,
    standard_name: str | None = None,
    *,
    tables: Tables | None = None,
    canonical: str | None = None,
) -> tuple[str, float]:
    """Return ``(canonical spelling, factor)`` for a reader *units* string.

    The shared layer's ``resolve_units``, raising oceanarray's :class:`UnitsError`.

    Parameters
    ----------
    units : str
        The units string as read.
    standard_name : str, optional
        CF standard name, which selects the canonical spelling when known.
    tables : Tables, optional
        Unit tables; default :data:`TABLES` (oceanarray's overrides on the family's).
    canonical : str, optional
        The canonical spelling, when the caller already knows it.

    Returns
    -------
    tuple of (str, float)
        The canonical spelling and the factor that converts to it.

    Raises
    ------
    UnitsError
        When *units* is empty or not a spelling of the canonical.
    """
    try:
        return _shared_resolve_units(
            units,
            standard_name,
            tables=TABLES if tables is None else tables,
            canonical=canonical,
        )
    except _SharedUnitsError as exc:
        raise UnitsError(str(exc)) from exc


#: Stored-spelling overrides, keyed by CF ``standard_name``.  oceanarray writes the
#: squared buoyancy frequency as ``s-2``, the spelling its gridded files have always
#: carried; the family canonical is ``rad2 s-2``.
CANONICAL_UNIT_OVERRIDES: dict[str, str] = {
    "square_of_brunt_vaisala_frequency_in_sea_water": "s-2",
}

#: Extra reader spellings the family table lacks, keyed by canonical spelling, each
#: evidenced. A spelling with a factor other than ``1`` would be a conversion and
#: belongs in the vendored table after review, never here.
UNIT_ALIAS_EXTRAS: dict[str, dict[str, float]] = {
    "mL L-1": {
        "ml l-1": 1.0,  # the RBR hex reader writes dissolved oxygen as 'ml l-1'
        # the oxygen display label, leaked into a units attr by a pre-0.5.0 stage
        "mL/L": 1.0,
    },
    # the N² display label, leaked into a units attr by a pre-0.5.0 stage
    "s-2": {"s⁻²": 1.0},
    # The angle spellings 'degrees' and '°' (for pitch/roll/heading/current_direction/tilt)
    # now live in the vendored family ALIASES (ctdcast #58, from oceanarray's finding); keeping
    # them here would restate the base value and shadow a future upstream change.  'deg' stays
    # out — it is lat/lon-scoped in the vendored SCOPED_ALIASES, where the axis disambiguates it.
    # The reader writes battery as 'volts'; the CF/UDUNITS canonical symbol is 'V'.
    # Evidence: dune2 battery_voltage stores units='volts'.  Factor 1.
    "V": {"volts": 1.0},
    # OceanSITES 1.3 files from the OSNAP arrays (WHOI, NIOZ, UMiami, GEOMAR, Scripps)
    # spell units in words.  Evidence: OS_OSNAP_WHOI_CF7_202007_CM_500m.nc,
    # os_osnap-ic2_201807_cm_611m.nc, OS_OSNAP-UMD2_201407_*.nc,
    # OS_OSNAP-10633_KPO_1111_K7_201408_D_MCTD_OSNAPstyleOceanSITES.nc.  Factor 1.
    "dbar": {
        "decibar": 1.0,
        # The OOI Data Explorer ERDDAP spells units ERDDAP-style. Evidence:
        # ooi-gi03flma-ri000-00-velptb000.nc (pressure 'decibars'). Factor 1.
        "decibars": 1.0,
        # The same ERDDAP serves the FLMA/FLMB single-point current-meter (VELPT)
        # pressure labelled 'decibars' but holding kPa (ten times the deployed depth at
        # every level); the instrument entry sets units_override: {pressure: kPa}.
        # A conversion, not a spelling: belongs in the vendored family table once
        # ctdcast reviews it (raised there); held here until then.
        "kPa": 0.1,
    },
    "m s-1": {
        "meter/second": 1.0,
        "m.s-1": 1.0,  # OOI ERDDAP (ooi-gi03flma-ri000-00-velptb000.nc). Factor 1.
        # RODB files (RAPID/BODC) carry velocities in cm/s, as documented for the
        # U/V/W codes in config/legacy/rodb_keys.yaml; evidence rtwb1_01_2014_11021.edt.
        # A conversion, not a spelling: belongs in the vendored family table once
        # ctdcast reviews it (raised there); held here until then.
        "cm s-1": 0.01,
    },
    "m": {"meter": 1.0},
    "mS cm-1": {"mS.cm-1": 1.0},  # OOI ERDDAP CTD conductivity. Factor 1.
    # caldip writes temperatures and temperature differences as 'degree_C' (UDUNITS).
    # Evidence: castM4_caldip.nc temp_diff / inst_temp / ctd_temp.  Factor 1.
    "degree_Celsius": {"degree_C": 1.0},
}

#: Extra spellings meaningful for one quantity only, keyed by CF ``standard_name``.
#: Evidence: the OSNAP OceanSITES files above write practical salinity as 'psu'; the OOI
#: ERDDAP writes '1e-3'.
UNIT_SCOPED_EXTRAS: dict[str, dict[str, float]] = {
    # OOI ERDDAP writes practical salinity as '1e-3' (the old parts-per-thousand
    # spelling); the values are practical salinity. Factor 1.
    "sea_water_practical_salinity": {"psu": 1.0, "1e-3": 1.0},
}

#: Spellings the storage pass keeps as the file has them, keyed by CF ``standard_name``.
#: For a frequency the two spellings differ only in whether the radian is written, so
#: neither is forced into the other: a file that arrives as ``rad2 s-2`` stays ``rad2
#: s-2`` and one that arrives as ``s-2`` stays ``s-2``.  Any other accepted spelling (a
#: leaked display label) is still rewritten to the canonical.
UNIT_SPELLINGS_KEPT: dict[str, frozenset[str]] = {
    "square_of_brunt_vaisala_frequency_in_sea_water": frozenset({"s-2", "rad2 s-2"}),
}

#: Display-label overrides, keyed by units string.  Empty: the family rule already labels
#: every canonical oceanarray uses — ``degree`` → ``°``, ``degrees_east/north`` → ``°E``/``°N``,
#: ``V`` → ``V`` — so the non-canonical reader spellings (``degrees``, ``volts``) are handled
#: as aliases of those canonicals in :data:`UNIT_ALIAS_EXTRAS`, not relabelled here.  Add an
#: entry only for a label the family rule cannot produce (e.g. ``{"percent": "%sat"}``).
UNIT_LABEL_OVERRIDES: dict[str, str] = {}

#: oceanarray's resolved view: the family defaults with the overrides above folded in.
TABLES: Tables = merge_tables(
    DEFAULT_TABLES,
    canonical=CANONICAL_UNIT_OVERRIDES,
    aliases=UNIT_ALIAS_EXTRAS,
    scoped=UNIT_SCOPED_EXTRAS,
    labels=UNIT_LABEL_OVERRIDES,
)


def unit_label(units: str, standard_name: str | None = None) -> str:
    """Return the display label for a units string, with oceanarray's overrides applied.

    The single display-unit entry point:
    :func:`oceanarray.reports._vendored.units.format_units` against :data:`TABLES`.
    Converts the stored CF/UDUNITS spelling to the slash form used on plot axes (``mS
    cm-1`` → ``mS/cm``, ``kg m-3`` → ``kg/m³``, ``s-2`` → ``s⁻²``, ``degree_Celsius`` →
    ``°C``); dimensionless practical salinity renders ``PSU`` and dimensionless
    turbidity ``NTU``.

    Parameters
    ----------
    units : str
        CF/UDUNITS units string (e.g. ``"mS cm-1"``).
    standard_name : str or None, optional
        CF standard name, for the dimensionless salinity and turbidity labels.

    Returns
    -------
    str
        The display units string; ``""`` when *units* is empty.
    """
    label = format_units(units, standard_name, tables=TABLES)
    # Diagnostic: when OCEANARRAY_UNIT_DEBUG_MARK=1, append a trailing "*" to every
    # non-empty label so a rendered report shows which labels passed through this
    # format-units pipeline (vs. a raw units attribute shown directly). Off by default.
    if label and os.environ.get("OCEANARRAY_UNIT_DEBUG_MARK") == "1":
        return label + "*"
    return label


def var_unit_label(da: Any) -> str:
    """Return the display unit for a variable, read from its stored ``units`` attribute.

    Unlike passing a literal unit string to :func:`unit_label`, this reads the unit the
    data actually carries, so a stored-unit change (or a conversion on write) flows
    through to the axis label — a label can never silently claim a unit the data no
    longer has.  Returns ``""`` when the variable carries no ``units`` attribute.

    Parameters
    ----------
    da : xarray.DataArray
        The variable whose ``units`` (and optional ``standard_name``) attribute is read.

    Returns
    -------
    str
        The display units string, or ``""`` when the variable has no ``units``.
    """
    return unit_label(da.attrs.get("units", ""), da.attrs.get("standard_name"))


def warn_if_not_canonical(da: Any, expected: str, *, quantity: str) -> None:
    """Warn when *da* does not carry the canonical units a derivation assumes.

    A derived display — displacement in km, height-above-seabed in m, a spectral
    density, a period — hard-codes the unit of its *output*, which is only correct if
    the *source* variable carries the unit the derivation was written for.  The storage
    pass (:func:`oceanarray.pipeline.units.to_canonical_units`) canonicalises every
    registered variable, so this never fires on an oceanarray-written file; it catches a
    foreign or hand-edited file whose units the derivation would otherwise silently
    mislabel.  A spelling that resolves to *expected* at factor 1 is not flagged; a
    variable with no ``units`` attribute is not flagged (nothing to check against).

    Parameters
    ----------
    da : xarray.DataArray
        The source variable whose ``units`` the derivation depends on.
    expected : str
        The canonical units the derivation was written for (e.g. ``"m s-1"``, ``"dbar"``,
        ``"degree_Celsius"``).
    quantity : str
        Short description of the derived quantity, for the warning message.

    """
    u = str(da.attrs.get("units", "")).strip()
    if not u or u == expected:
        return
    # Only honour the variable's standard_name for scoped aliases when that standard
    # name denotes the expected quantity; otherwise a cross-quantity scoped alias (a
    # latitude ``deg`` checked against an expected ``dbar``) can resolve as a false
    # match and silence the guard.
    standard_name = da.attrs.get("standard_name")
    if TABLES.canonical.get(standard_name) != expected:
        standard_name = None
    try:
        if resolve_units(u, standard_name, tables=TABLES, canonical=expected) == (
            expected,
            1.0,
        ):
            return
    except UnitsError:
        pass
    log.warning(
        "%s: source variable carries units %r, not the expected %r; "
        "the derived label assumes %r and may be wrong.",
        quantity,
        u,
        expected,
        expected,
    )
