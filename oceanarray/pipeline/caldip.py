"""CalDip calibration-dip contract and interface for the front of Stage 3 (stub).

caldip is a separate package that compares mooring instruments against a shipboard CTD at
bottle stops and writes one netCDF per cast (``{cast}_caldip.nc``); the statistics CSV is a
derived export that oceanarray does not read. oceanarray reads that netCDF, matches instruments
by serial, and applies per-variable offsets at the front of Stage 3, before pressure
interpolation.

**Status: the correction is not implemented here.** This module carries only the *contract*
(the constants and serial join-key rule both packages share) and the *interface* the correction
is built to: :func:`read_caldip_cast`, :func:`find_caldip_casts`, :func:`assign_dip_role`,
:func:`select_offsets`, and :func:`apply_caldip` each raise :class:`NotImplementedError`. The
implementation lands on a separate branch; a reference implementation exists for cross-checking.

Carrier and field contract are fixed by the three-way coordination note
(ctdcast → caldip → oceanarray) and documented for builders in
``docs/source/methods/calibration.rst`` (which owns the correction model and stop-selection
rules). Sign convention: every ``*_diff`` variable is ``instrument − CTD``, so an instrument is
corrected toward the reference by subtracting the offset (``corrected = measured − diff``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import xarray as xr

from oceanarray import _deprecated
from oceanarray.paths import safe_serial

#: Filename suffix of the per-cast caldip netCDF (strict — excludes CSV exports and variants).
CALDIP_SUFFIX = "_caldip.nc"

#: Schema version this consumer understands (``schema_version`` global attribute).
SUPPORTED_SCHEMA_VERSION = 1

#: Integer value meaning "ok" in caldip's ``*_flag`` variables.
#: ``flag_meanings`` order is ``ok no_data flagged missing unknown`` = ``1 2 3 4 9``.
CALDIP_FLAG_OK = 1

#: Global attributes that must all be present for a file to carry usable provenance.
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

#: Value written to the ``caldip_applied`` global attribute of ``_stage3.nc`` when a caldip
#: correction was requested but only the null-action stub ran. The first real correction
#: replaces this string, so the stub -> non-stub transition is visible in the output file.
#: Plain ASCII (a hyphen, not an em-dash) — a portable marker, matched byte-for-byte by the
#: contract test.
CALDIP_STUB_APPLIED = (
    "none - caldip stub (correction not implemented); Stage 3 science data unchanged"
)


class CaldipError(Exception):
    """Base error for caldip-file reading and matching."""


class CaldipProvenanceError(CaldipError):
    """Raised when a caldip file carries no provenance and so must not be applied."""


@dataclass(frozen=True)
class Offsets:
    """One variable's caldip offset selected for an instrument at a bottle stop.

    Parameters
    ----------
    value : float
        The offset (``instrument − CTD``) to subtract from the instrument value, in the
        variable's units.
    stop_pressure : float
        Bottle-stop pressure the offset was taken from (dbar).
    n : int
        Sample count in the comparison window at that stop.
    flag : int
        caldip ``*_flag`` value at the stop (see :data:`CALDIP_FLAG_OK`).
    source_tracking_id : str
        ``tracking_id`` of the caldip file the offset came from, for provenance.

    """

    value: float
    stop_pressure: float
    n: int
    flag: int
    source_tracking_id: str


def canonical_serial(serial: Any) -> str:
    """Return the join-key form of an instrument serial.

    The caldip↔oceanarray contract matches instruments on ``serial`` after making it
    filename-safe and stripping leading zeros, so ``013874`` and ``13874`` join and the
    trailing YAML marker asterisk (``9920*``) is dropped. Both packages apply it on read,
    so the join is symmetric. This is contract, not method, so it lives with the stub.

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


normalize_serial = _deprecated.alias(
    "oceanarray.pipeline.caldip.normalize_serial", canonical_serial
)


def read_caldip_cast(path: Path) -> xr.Dataset:
    """Open and validate one caldip per-cast netCDF (interface stub — not implemented).

    The implementation opens ``{cast}_caldip.nc``, checks ``schema_version`` equals
    :data:`SUPPORTED_SCHEMA_VERSION` and every :data:`REQUIRED_PROVENANCE_GLOBALS` is present
    (raising :class:`CaldipProvenanceError` otherwise), normalises the ``serial`` coordinate
    with :func:`canonical_serial`, and warns when an ``instrument_type`` is outside
    ``parameters.KNOWN_INSTRUMENT_TYPES`` or disagrees with the mooring YAML.

    Parameters
    ----------
    path : Path
        Path to a ``{cast}_caldip.nc`` file.

    Returns
    -------
    xarray.Dataset
        The validated cast with dimensions ``instrument`` and ``stop``.

    Raises
    ------
    NotImplementedError
        Always — see ``docs/source/methods/calibration.rst``.

    """
    del path
    msg = "read_caldip_cast is not implemented; see docs/source/methods/calibration.rst"
    raise NotImplementedError(msg)


def find_caldip_casts(caldip_dir: Path, serial: Any) -> list[Path]:
    """Return every caldip cast beneath a directory in which an instrument appears (stub).

    oceanarray receives a *set* of casts and searches — caldip never pairs dips. The
    implementation returns each ``*_caldip.nc`` under ``caldip_dir`` whose instruments include
    the normalised ``serial``.

    Parameters
    ----------
    caldip_dir : Path
        Root directory of caldip per-cast output.
    serial : Any
        Instrument serial, normalised with :func:`canonical_serial` before matching.

    Returns
    -------
    list of Path
        Matching cast files, in a stable order.

    Raises
    ------
    NotImplementedError
        Always — see ``docs/source/methods/calibration.rst``.

    """
    del caldip_dir, serial
    msg = (
        "find_caldip_casts is not implemented; see docs/source/methods/calibration.rst"
    )
    raise NotImplementedError(msg)


def assign_dip_role(
    cast_time: Any, deployment_time: Any, recovery_time: Any
) -> str | None:
    """Classify a cast as the pre- or post-deployment dip for an instrument (stub).

    ``dip_role`` is not in the caldip file (three-way note): pre- versus post-deployment is a
    property of the ``(instrument, deployment)`` pair, so oceanarray derives it from the cast
    ``time`` against the instrument's deployment window.

    Parameters
    ----------
    cast_time : Any
        The caldip cast time.
    deployment_time : Any
        The instrument's deployment time.
    recovery_time : Any
        The instrument's recovery time.

    Returns
    -------
    str or None
        ``"pre"``, ``"post"``, or ``None`` when the cast falls outside the window / is
        unassignable.

    Raises
    ------
    NotImplementedError
        Always — see ``docs/source/methods/calibration.rst``.

    """
    del cast_time, deployment_time, recovery_time
    msg = "assign_dip_role is not implemented; see docs/source/methods/calibration.rst"
    raise NotImplementedError(msg)


def select_offsets(ds: xr.Dataset, serial: Any, variable: str) -> Offsets:
    """Select the bottle-stop offset for one instrument variable (stub).

    Stop selection is per variable (``docs/source/methods/calibration.rst``, correction
    model): pressure from the stop nearest deployment depth, temperature from the deepest
    stop, conductivity from the deepest stop or a fit across stops. The choice of stop and
    reduction is oceanarray's, not caldip's.

    Parameters
    ----------
    ds : xarray.Dataset
        A cast from :func:`read_caldip_cast`.
    serial : Any
        Instrument serial, normalised before matching.
    variable : str
        ``"temperature"``, ``"conductivity"``, or ``"pressure"``.

    Returns
    -------
    Offsets
        The selected offset and its provenance.

    Raises
    ------
    NotImplementedError
        Always — see ``docs/source/methods/calibration.rst``.

    """
    del ds, serial, variable
    msg = "select_offsets is not implemented; see docs/source/methods/calibration.rst"
    raise NotImplementedError(msg)


def apply_caldip(
    ds: xr.Dataset, offsets_pre: Offsets, offsets_post: Offsets | None = None
) -> xr.Dataset:
    """Apply a variable's caldip offset(s) to a Stage 3 dataset (stub).

    Subtracts the offset (``corrected = measured − diff``). With both a pre- and a
    post-deployment offset the correction is a linear-in-time interpolation across the
    deployment (``docs/source/methods/calibration.rst``, pre/post combination); with one it is
    constant. The implementation records :data:`PROVENANCE_PASSTHROUGH_GLOBALS`, the applied
    value, and the method in the output attributes.

    Parameters
    ----------
    ds : xarray.Dataset
        The Stage 3 dataset to correct.
    offsets_pre : Offsets
        The pre-deployment (or single) offset.
    offsets_post : Offsets or None, optional
        The post-deployment offset, when a bracketing dip exists.

    Returns
    -------
    xarray.Dataset
        The corrected dataset.

    Raises
    ------
    NotImplementedError
        Always — see ``docs/source/methods/calibration.rst``.

    """
    del ds, offsets_pre, offsets_post
    msg = "apply_caldip is not implemented; see docs/source/methods/calibration.rst"
    raise NotImplementedError(msg)
