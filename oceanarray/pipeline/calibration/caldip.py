"""Read caldip's per-cast netCDF: the bottle-stop comparisons a calibration correction is built from.

caldip compares each mooring instrument with the shipboard CTD at the bottle stops of one cast
and writes ``{cast}_caldip.nc`` (dimensions ``instrument`` and ``stop``). caldip measures;
oceanarray decides: this module only reads and checks a cast. Choosing stops, reducing them to
an offset or a slope, pairing a deployment dip with a recovery dip and applying the result are
done elsewhere in :mod:`oceanarray.pipeline.calibration`.

A cast is refused, never applied, when it

- carries a ``schema_version`` other than :data:`SUPPORTED_SCHEMA_VERSION`,
- lacks any of :data:`REQUIRED_PROVENANCE_GLOBALS`,
- has a units string on a temperature, conductivity or pressure variable that is not a
  recognised spelling of oceanarray's canonical unit for that quantity, or
- has two instruments whose serials are the same join key.

Sign convention: every ``*_diff`` variable is ``instrument − CTD``, so an instrument is
corrected toward the reference by subtracting it (``corrected = measured − diff``).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.config.unit_tables import TABLES
from oceanarray.paths import safe_serial
from oceanarray.reports._vendored.units import UnitsError, resolve_units

#: Filename suffix of the per-cast caldip netCDF (strict — excludes CSV exports and variants).
CALDIP_SUFFIX = "_caldip.nc"

#: Schema version this reader understands (``schema_version`` global attribute).
SUPPORTED_SCHEMA_VERSION = 1

#: Integer value meaning "ok" in caldip's ``*_flag`` variables.
#: ``flag_meanings`` order is ``ok no_data flagged missing unknown`` = ``1 2 3 4 9``.
CALDIP_FLAG_OK = 1

#: caldip's ``flagged``: the stop's difference exceeds caldip's acceptance threshold
#: (``flagging_threshold``, e.g. 0.005 °C). The comparison itself is sound — a stop that reads
#: off is exactly what a correction is for — so it is usable for a correction.
CALDIP_FLAG_FLAGGED = 3

#: The flag values whose stops a correction may use.
CALDIP_FLAGS_USABLE = (CALDIP_FLAG_OK, CALDIP_FLAG_FLAGGED)

#: Global attributes that must all be present before a cast may be used.
REQUIRED_PROVENANCE_GLOBALS = (
    "schema_version",
    "caldip_version",
    "tracking_id",
    "cruise_id",
    "cast_id",
    "data_mode",
)

#: Global attributes oceanarray copies through into ``_stage3.nc`` when a correction is applied,
#: under caldip/ctdcast's own names (no translation layer). Values may legitimately be ``UNK``
#: for a ``.cnv``-input caldip cast.
PROVENANCE_PASSTHROUGH_GLOBALS = (
    "tracking_id",
    "source_tracking_id",
    "cruise_id",
    "cast_id",
    "caldip_version",
    "schema_version",
    "data_mode",
    "ctd_path",
    "ctd_stage",
    "input_mode",
    "qc_flags_honoured",
    "ctd_conductivity_slope",
    "ctd_cond_slope_adjusted",
    "ctd_temp_sensor_serial",
    "ctd_cond_sensor_serial",
    "ctd_temp_sensor_caldate",
    "ctd_cond_sensor_caldate",
    "ctd_temp_processing_level",
    "ctd_cond_processing_level",
    "ctd_press_processing_level",
)

#: caldip's short variable stem → oceanarray's registry name, whose units are the canonical.
CALDIP_QUANTITIES = {"temp": "temperature", "cond": "conductivity", "press": "pressure"}

#: The per-quantity variables a cast carries, by suffix (``{stem}_diff`` …) and prefix
#: (``inst_{stem}``, ``ctd_{stem}``).
_UNIT_SUFFIXES = ("_diff", "_std")
_UNIT_PREFIXES = ("inst_", "ctd_")


class CaldipError(Exception):
    """Base error for caldip-file reading and matching."""


class CaldipProvenanceError(CaldipError):
    """Raised when a caldip file lacks the provenance required before it may be used."""


def canonical_serial(serial: Any) -> str:
    """Return the join-key form of an instrument serial.

    The caldip↔oceanarray contract matches instruments on ``serial`` after making it
    filename-safe and stripping leading zeros, so ``013874`` and ``13874`` join and the
    trailing YAML marker asterisk (``9920*``) is dropped. Both packages apply it on read,
    so the join is symmetric.

    Parameters
    ----------
    serial : Any
        Raw serial value from a caldip file coordinate or a mooring YAML entry.

    Returns
    -------
    str
        The join-key serial: filename-safe with leading zeros removed. An all-zero token
        falls back to its filename-safe form rather than the empty string.

    """
    safe = safe_serial(serial)
    return safe.lstrip("0") or safe


def read_caldip_cast(path: str | Path) -> xr.Dataset:
    """Open one ``{cast}_caldip.nc``, check it, and return it ready for matching.

    The file is loaded into memory and closed. The ``serial`` coordinate is replaced by its
    join key (:func:`canonical_serial`), and the units of every temperature, conductivity and
    pressure variable are rewritten to oceanarray's canonical spelling (values multiplied by
    the conversion factor when one applies). The source path is recorded in
    ``attrs["source_path"]``.

    Parameters
    ----------
    path : str or pathlib.Path
        Path to a ``{cast}_caldip.nc`` file.

    Returns
    -------
    xarray.Dataset
        The cast, with dimensions ``instrument`` and ``stop``.

    Raises
    ------
    CaldipError
        If the schema version is not :data:`SUPPORTED_SCHEMA_VERSION`, a units string is not
        a recognised spelling of the canonical unit, or two serials share a join key.
    CaldipProvenanceError
        If any of :data:`REQUIRED_PROVENANCE_GLOBALS` is missing.

    Examples
    --------
    >>> cast = read_caldip_cast("proc_calib/msm142_2026/castM4_caldip.nc")
    >>> instrument_record(cast, "013840")["cond_diff"].values

    """
    path = Path(path)
    with xr.open_dataset(path, engine="netcdf4") as _ds:
        ds = _ds.load()

    _check_provenance(ds, path)
    _check_schema(ds, path)
    ds = _canonical_units(ds, path)

    keys = [canonical_serial(s) for s in ds["serial"].values]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        msg = (
            f"{path.name}: serials {duplicates} occur more than once after normalising"
        )
        raise CaldipError(msg)
    ds = ds.assign_coords(serial=("instrument", keys))
    ds["serial"].attrs["comment"] = (
        "join key: canonical_serial of the serial caldip wrote"
    )

    _warn_unknown_instrument_types(ds, path)
    ds.attrs["source_path"] = str(path)
    return ds


def instrument_record(cast: xr.Dataset, serial: Any) -> xr.Dataset | None:
    """Return one instrument's per-stop record from a cast, or ``None`` if it is not there.

    Parameters
    ----------
    cast : xarray.Dataset
        A cast from :func:`read_caldip_cast`.
    serial : Any
        Instrument serial in any form; matched by :func:`canonical_serial`.

    Returns
    -------
    xarray.Dataset or None
        The instrument's slice, indexed over ``stop`` (the per-stop CTD variables come
        with it), or ``None`` when the serial is not in the cast.

    """
    key = canonical_serial(serial)
    idx = np.flatnonzero(cast["serial"].values == key)
    if idx.size == 0:
        return None
    return cast.isel(instrument=int(idx[0]))


def _check_provenance(ds: xr.Dataset, path: Path) -> None:
    """Refuse a cast missing any required provenance attribute."""
    missing = [k for k in REQUIRED_PROVENANCE_GLOBALS if k not in ds.attrs]
    if missing:
        msg = f"{path.name}: missing provenance attributes {missing}; the cast cannot be used"
        raise CaldipProvenanceError(msg)


def _check_schema(ds: xr.Dataset, path: Path) -> None:
    """Refuse a cast written to a schema this reader does not know."""
    schema = ds.attrs["schema_version"]
    try:
        version = int(schema)
    except (TypeError, ValueError):
        version = None
    if version != SUPPORTED_SCHEMA_VERSION:
        msg = (
            f"{path.name}: schema_version {schema!r}, but this reader understands "
            f"{SUPPORTED_SCHEMA_VERSION}; update oceanarray or re-run caldip"
        )
        raise CaldipError(msg)


def _canonical_units(ds: xr.Dataset, path: Path) -> xr.Dataset:
    """Rewrite the T/C/P variables' units to the registry spelling, or refuse."""
    for stem, quantity in CALDIP_QUANTITIES.items():
        canonical = params.VARIABLES[quantity]["units"]
        names = [f"{stem}{s}" for s in _UNIT_SUFFIXES]
        names += [f"{p}{stem}" for p in _UNIT_PREFIXES]
        for name in names:
            if name not in ds:
                continue
            units = str(ds[name].attrs.get("units", "")).strip()
            try:
                spelling, factor = resolve_units(
                    units, canonical=canonical, tables=TABLES
                )
            except UnitsError as e:
                msg = f"{path.name}: {name} units {units!r} — {e}"
                raise CaldipError(msg) from e
            if factor != 1.0:
                ds[name].data = ds[name].data * factor
            ds[name].attrs["units"] = spelling
    return ds


def _warn_unknown_instrument_types(ds: xr.Dataset, path: Path) -> None:
    """Warn on any ``instrument_type`` outside oceanarray's vocabulary."""
    if "instrument_type" not in ds:
        return
    known = {t.lower() for t in params.KNOWN_INSTRUMENT_TYPES}
    seen = {str(t) for t in np.atleast_1d(ds["instrument_type"].values)}
    unknown = sorted(t for t in seen if t.lower() not in known)
    if unknown:
        warnings.warn(
            f"{path.name}: instrument_type {unknown} not in oceanarray's vocabulary",
            stacklevel=3,
        )
