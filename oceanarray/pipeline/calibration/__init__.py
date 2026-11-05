"""Calibration-dip corrections at the front of Stage 3 (interface stub).

caldip is a separate package that compares mooring instruments against a shipboard CTD at
bottle stops and writes one netCDF per cast (``{cast}_caldip.nc``); the statistics CSV is a
derived export that oceanarray does not read. oceanarray reads that netCDF, matches instruments
by serial, and corrects temperature, conductivity and pressure at the front of Stage 3, before
pressure interpolation. A correction changes the values, as the Stage 2 clock-drift
correction does; it is not quality control.

**Status: everything up to applying the correction is implemented.**
:mod:`.caldip` reads and checks a cast (:func:`read_caldip_cast`, :func:`instrument_record`);
:mod:`.dips` finds the file for each end the mooring YAML names (:func:`caldip_ends`) and
which end a cast is (:func:`assign_dip_role`); :mod:`.stops` reduces one dip's bottle stops to
an offset or a slope per variable (:func:`reduce_dip`). :func:`apply_caldip` is the interface
the correction is built to; it still raises :class:`NotImplementedError`.

The carrier, the field contract, the correction model and the stop-selection rules are
documented in ``docs/source/methods/calibration.rst``. Sign convention: every ``*_diff``
variable is ``instrument − CTD``, so an instrument is corrected toward the reference by
subtracting the offset (``corrected = measured − diff``).
"""

from __future__ import annotations

import xarray as xr

from oceanarray.pipeline.calibration.caldip import (
    CALDIP_FLAG_FLAGGED,
    CALDIP_FLAG_OK,
    CALDIP_FLAGS_USABLE,
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
from oceanarray.pipeline.calibration.dips import (
    DIP_ENDS,
    assign_dip_role,
    caldip_ends,
    caldip_file,
    caldip_root,
    cast_time,
)
from oceanarray.pipeline.calibration.stops import (
    STEMS,
    Reduction,
    conductivity_offset,
    conductivity_slope,
    conductivity_stops,
    deepest_offset,
    pressure_offset,
    rapid_depth_average,
    rapid_pressure_at_depth,
    reduce_dip,
    through_origin_slope,
    usable_stops,
)

__all__ = [
    "CALDIP_FLAG_FLAGGED",
    "CALDIP_FLAG_OK",
    "CALDIP_FLAGS_USABLE",
    "CALDIP_QUANTITIES",
    "CALDIP_STUB_APPLIED",
    "CALDIP_SUFFIX",
    "DIP_ENDS",
    "PROVENANCE_PASSTHROUGH_GLOBALS",
    "REQUIRED_PROVENANCE_GLOBALS",
    "SUPPORTED_SCHEMA_VERSION",
    "CaldipError",
    "CaldipProvenanceError",
    "Reduction",
    "STEMS",
    "conductivity_offset",
    "conductivity_slope",
    "conductivity_stops",
    "deepest_offset",
    "pressure_offset",
    "rapid_depth_average",
    "rapid_pressure_at_depth",
    "reduce_dip",
    "through_origin_slope",
    "usable_stops",
    "apply_caldip",
    "assign_dip_role",
    "canonical_serial",
    "caldip_ends",
    "caldip_file",
    "caldip_root",
    "cast_time",
    "instrument_record",
    "read_caldip_cast",
]

#: Value written to the ``caldip_applied`` global attribute of ``_stage3.nc`` when a caldip
#: correction was requested but only the null-action stub ran. The first real correction
#: replaces this string, so the stub -> non-stub transition is visible in the output file.
#: Plain ASCII (a hyphen, not an em-dash) — a portable marker, matched byte-for-byte by the
#: contract test.
CALDIP_STUB_APPLIED = (
    "none - caldip stub (correction not implemented); Stage 3 science data unchanged"
)


def apply_caldip(
    ds: xr.Dataset, pre: Reduction | None, post: Reduction | None = None
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
    pre : Reduction or None
        The pre-deployment dip's correction.
    post : Reduction or None, optional
        The post-deployment dip's correction.

    Returns
    -------
    xarray.Dataset
        The corrected dataset.

    Raises
    ------
    NotImplementedError
        Always — see ``docs/source/methods/calibration.rst``.

    """
    del ds, pre, post
    msg = "apply_caldip is not implemented; see docs/source/methods/calibration.rst"
    raise NotImplementedError(msg)
