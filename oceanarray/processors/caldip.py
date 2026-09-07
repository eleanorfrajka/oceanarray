"""Read caldip per-cast netCDF calibration-dip statistics for use at the front of Stage 3.

caldip is a separate package that compares mooring instruments against a shipboard CTD at
bottle stops and writes one netCDF per cast (``{cast}_caldip.nc``); the statistics CSV is a
derived export that oceanarray does not read. This module reads that netCDF, normalises the
instrument serial join key, guards the provenance, and selects the bottle stop each variable's
correction is taken from. It does **not** yet apply the corrections to Stage 3 data — the
per-variable application thresholds are still being settled and the ``ctd_*`` reference
provenance is ``UNK`` until caldip's ``ctdcast-input`` step lands.

Carrier and contract are fixed by
``.claude/notes/2026-09-06-threeway-coordination-ctdcast-caldip-oceanarray.md`` (Interfaces
section) and the reconciliation in ``.claude/notes/20260905-caldip-apply-plan.md`` (§10).

Sign convention: every ``*_diff`` variable is ``instrument − CTD``, so an instrument is
corrected toward the reference by **subtracting** the offset (``corrected = measured − diff``).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from oceanarray.config.parameters import KNOWN_INSTRUMENT_TYPES
from oceanarray.paths import safe_serial

#: Filename suffix of the per-cast caldip netCDF (strict — excludes CSV exports and variants).
CALDIP_SUFFIX = "_caldip.nc"

#: Schema version this reader understands (``schema_version`` global attribute).
SUPPORTED_SCHEMA_VERSION = 1

#: Integer value meaning "ok" in caldip's ``*_flag`` variables.
#: ``flag_meanings`` order is ``ok no_data flagged missing unknown`` = ``1 2 3 4 9``.
CALDIP_FLAG_OK = 1

#: Global attributes that must all be present for a file to carry any usable provenance.
#: A file with none of these is unprovenanced and must be refused, never applied.
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
#: for a ``.cnv``-input caldip cast until caldip's ``ctdcast-input`` step populates them.
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

#: Pressure correction is applied only when ``|offset| > 5 dbar`` (settled 2026-09-05; see the
#: apply plan §8). This is an *application* threshold, distinct from the pre-deployment
#: *acceptance* tolerance of 5 dbar in the caldip docs — same number, different purpose.
PRESSURE_APPLY_THRESHOLD_DBAR = 5.0

#: caldip variable base names oceanarray corrects, in the order stops are selected per variable.
CALDIP_VARIABLES = ("temperature", "conductivity", "pressure")


class CaldipError(Exception):
    """Base error for caldip-file reading and matching."""


class CaldipProvenanceError(CaldipError):
    """Raised when a caldip file carries no provenance and so must not be applied."""


def normalize_serial(serial: Any) -> str:
    """Return the join-key form of an instrument serial.

    The caldip↔oceanarray contract matches instruments on ``serial`` after making it
    filename-safe and stripping leading zeros, so ``013874`` and ``13874`` join and the
    trailing YAML marker asterisk (``9920*``) is dropped. Both packages normalise on read,
    so the join is symmetric.

    Parameters
    ----------
    serial : Any
        Raw serial value from a caldip file coordinate or a mooring YAML entry.

    Returns
    -------
    str
        Normalised serial: filename-safe with leading zeros removed. An all-zero token
        falls back to its filename-safe form rather than the empty string.

    """
    safe = safe_serial(serial)
    return safe.lstrip("0") or safe


def find_caldip_file(caldip_dir: Path, cast: str, cruise: str | None = None) -> Path:
    """Locate one cast's caldip netCDF beneath a caldip root directory.

    Searches recursively for ``{cast}{CALDIP_SUFFIX}`` so the flat (``outputs/``) and nested
    (``data/proc_calib/{cruise}/cal_dip/``) layouts both resolve. When a cast name recurs and
    ``cruise`` is given, matches whose path contains the cruise are preferred.

    Parameters
    ----------
    caldip_dir : Path
        Root directory of caldip output.
    cast : str
        Cast name, e.g. ``"castM4"``.
    cruise : str or None, optional
        Cruise identifier used to disambiguate a recurring cast name.

    Returns
    -------
    Path
        The resolved caldip netCDF path.

    Raises
    ------
    FileNotFoundError
        If no matching file is found.
    CaldipError
        If the cast name is ambiguous and ``cruise`` does not resolve it.

    """
    caldip_dir = Path(caldip_dir)
    matches = sorted(caldip_dir.rglob(f"{cast}{CALDIP_SUFFIX}"))
    if not matches:
        msg = f"No {cast}{CALDIP_SUFFIX} found beneath {caldip_dir}"
        raise FileNotFoundError(msg)
    if len(matches) > 1 and cruise is not None:
        by_cruise = [m for m in matches if cruise in m.parts]
        if by_cruise:
            matches = by_cruise
    if len(matches) > 1:
        listed = ", ".join(str(m) for m in matches)
        msg = (
            f"Ambiguous cast {cast!r} beneath {caldip_dir}: {listed}. "
            "Pass cruise= to disambiguate."
        )
        raise CaldipError(msg)
    return matches[0]


def read_caldip_cast(path: Path) -> xr.Dataset:
    """Open and validate a caldip per-cast netCDF, with serials normalised for joining.

    Loads the file into memory (closing the handle), refuses it if it carries no provenance,
    warns on a schema-version or instrument-type it does not recognise, and replaces the
    ``serial`` coordinate with its normalised join key (see :func:`normalize_serial`).

    Parameters
    ----------
    path : Path
        Path to a ``{cast}_caldip.nc`` file.

    Returns
    -------
    xarray.Dataset
        The caldip cast with dimensions ``instrument`` and ``stop`` and a normalised
        ``serial`` coordinate.

    Raises
    ------
    CaldipProvenanceError
        If none of :data:`REQUIRED_PROVENANCE_GLOBALS` are present.

    """
    path = Path(path)
    with xr.open_dataset(path, engine="netcdf4") as _ds:
        ds = _ds.load()

    _check_provenance(ds, path)

    schema = ds.attrs.get("schema_version")
    if schema is not None and int(schema) != SUPPORTED_SCHEMA_VERSION:
        warnings.warn(
            f"{path.name}: schema_version={schema} but this reader targets "
            f"{SUPPORTED_SCHEMA_VERSION}; fields may not match.",
            stacklevel=2,
        )

    ds = ds.assign_coords(
        serial=("instrument", [normalize_serial(s) for s in ds["serial"].values])
    )
    _warn_unknown_instrument_types(ds, path)
    return ds


def offsets_for_serial(ds: xr.Dataset, serial: Any) -> xr.Dataset | None:
    """Return the per-stop caldip record for one instrument, or ``None`` if absent.

    Parameters
    ----------
    ds : xarray.Dataset
        A cast read by :func:`read_caldip_cast` (serials already normalised).
    serial : Any
        Instrument serial in any form; normalised before matching.

    Returns
    -------
    xarray.Dataset or None
        The single-instrument slice (indexed over ``stop``), or ``None`` if the serial is
        not in this cast.

    """
    key = normalize_serial(serial)
    idx = np.where(ds["serial"].values == key)[0]
    if idx.size == 0:
        return None
    return ds.isel(instrument=int(idx[0]))


def select_stop(
    inst_ds: xr.Dataset, variable: str, deployment_pressure: float | None = None
) -> int:
    """Return the ``stop`` index whose bottle stop a variable's correction is taken from.

    Selection is per variable, following the apply plan: pressure uses the stop nearest the
    instrument's deployment pressure; temperature and conductivity use the deepest stop.

    Parameters
    ----------
    inst_ds : xarray.Dataset
        A single-instrument slice from :func:`offsets_for_serial`.
    variable : str
        One of ``"pressure"``, ``"temperature"``, ``"conductivity"``.
    deployment_pressure : float or None, optional
        The instrument's deployment pressure (dbar), required for ``"pressure"``.

    Returns
    -------
    int
        The selected ``stop`` index.

    Raises
    ------
    ValueError
        If ``variable`` is unknown, or ``deployment_pressure`` is missing for pressure.

    """
    bl_press = np.asarray(inst_ds["bl_press"].values, dtype=float)
    if variable in ("temperature", "conductivity"):
        return int(np.nanargmax(bl_press))
    if variable == "pressure":
        if deployment_pressure is None:
            msg = "deployment_pressure is required to select a pressure stop"
            raise ValueError(msg)
        return int(np.nanargmin(np.abs(bl_press - deployment_pressure)))
    msg = f"Unknown caldip variable {variable!r}"
    raise ValueError(msg)


def apply_offset(measured: Any, diff: Any) -> Any:
    """Return an instrument value corrected toward the CTD reference.

    Implements the caldip sign convention ``corrected = measured − diff`` where
    ``diff = instrument − CTD``. Broadcasts over arrays.

    Parameters
    ----------
    measured : Any
        The instrument value(s) to correct.
    diff : Any
        The caldip ``*_diff`` offset(s).

    Returns
    -------
    Any
        ``measured - diff``.

    """
    return measured - diff


def _check_provenance(ds: xr.Dataset, path: Path) -> None:
    """Refuse a file with no provenance; warn on a partial set."""
    missing = [k for k in REQUIRED_PROVENANCE_GLOBALS if k not in ds.attrs]
    if len(missing) == len(REQUIRED_PROVENANCE_GLOBALS):
        msg = (
            f"{path.name}: no provenance attributes; refusing to apply "
            "(contract: an unprovenanced file must not be applied)."
        )
        raise CaldipProvenanceError(msg)
    if missing:
        warnings.warn(
            f"{path.name}: missing provenance attrs {missing}; proceeding, incomplete.",
            stacklevel=2,
        )


def _warn_unknown_instrument_types(ds: xr.Dataset, path: Path) -> None:
    """Warn on any ``instrument_type`` outside oceanarray's vocabulary."""
    if "instrument_type" not in ds:
        return
    known = {t.lower() for t in KNOWN_INSTRUMENT_TYPES}
    seen = {str(t) for t in np.atleast_1d(ds["instrument_type"].values)}
    unknown = sorted(t for t in seen if t.lower() not in known)
    if unknown:
        warnings.warn(
            f"{path.name}: instrument_type {unknown} not in oceanarray vocabulary "
            f"{sorted(KNOWN_INSTRUMENT_TYPES)}; the caldip file predates the shared "
            "instrument-class contract.",
            stacklevel=2,
        )
