"""Calibration-dip corrections at the front of Stage 3 (interface stub).

caldip is a separate package that compares mooring instruments against a shipboard CTD at
bottle stops and writes one netCDF per cast (``{cast}_caldip.nc``); the statistics CSV is a
derived export that oceanarray does not read. oceanarray reads that netCDF, matches instruments
by serial, and corrects temperature, conductivity and pressure at the front of Stage 3, before
pressure interpolation. A correction changes the values, as the Stage 2 clock-drift
correction does; it is not quality control.

**Status: reading is implemented, the correction is not.** :mod:`.caldip` reads and checks a
cast (:func:`read_caldip_cast`, :func:`instrument_record`). :func:`find_caldip_casts`,
:func:`assign_dip_role`, :func:`select_offsets` and :func:`apply_caldip` are the interface the
correction is built to; each still raises :class:`NotImplementedError`.

The carrier, the field contract, the correction model and the stop-selection rules are
documented in ``docs/source/methods/calibration.rst``. Sign convention: every ``*_diff``
variable is ``instrument − CTD``, so an instrument is corrected toward the reference by
subtracting the offset (``corrected = measured − diff``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import xarray as xr

from oceanarray.pipeline.calibration.caldip import (
    CALDIP_FLAG_OK,
    CALDIP_QUANTITIES,
    CALDIP_SUFFIX,
    PROVENANCE_PASSTHROUGH_GLOBALS,
    REQUIRED_PROVENANCE_GLOBALS,
    SUPPORTED_SCHEMA_VERSION,
    CaldipError,
    CaldipProvenanceError,
    canonical_serial,
    instrument_record,
    read_caldip_cast,
)

__all__ = [
    "CALDIP_FLAG_OK",
    "CALDIP_QUANTITIES",
    "CALDIP_STUB_APPLIED",
    "CALDIP_SUFFIX",
    "PROVENANCE_PASSTHROUGH_GLOBALS",
    "REQUIRED_PROVENANCE_GLOBALS",
    "SUPPORTED_SCHEMA_VERSION",
    "CaldipError",
    "CaldipProvenanceError",
    "Offsets",
    "apply_caldip",
    "assign_dip_role",
    "canonical_serial",
    "find_caldip_casts",
    "instrument_record",
    "read_caldip_cast",
    "select_offsets",
]

#: Value written to the ``caldip_applied`` global attribute of ``_stage3.nc`` when a caldip
#: correction was requested but only the null-action stub ran. The first real correction
#: replaces this string, so the stub -> non-stub transition is visible in the output file.
#: Plain ASCII (a hyphen, not an em-dash) — a portable marker, matched byte-for-byte by the
#: contract test.
CALDIP_STUB_APPLIED = (
    "none - caldip stub (correction not implemented); Stage 3 science data unchanged"
)


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
