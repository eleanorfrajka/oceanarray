"""Calibration-dip corrections at the front of stage 3 (reading and reduction only, so far).

caldip writes one ``{cast}_caldip.nc`` per cast, comparing instruments with a CTD at
bottle stops. :mod:`.caldip` reads a cast, :mod:`.dips` finds each end's file,
:mod:`.stops` reduces a dip to an offset or slope per variable; :func:`apply_caldip`
(combining and applying) still raises :class:`NotImplementedError`. ``*_diff`` is
``instrument − CTD``, so ``corrected = measured − diff``. See
``docs/source/methods/calibration.rst``.
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
    STABLE_CTD_STD,
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
    stable_stops,
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
    "STABLE_CTD_STD",
    "STEMS",
    "conductivity_offset",
    "conductivity_slope",
    "conductivity_stops",
    "deepest_offset",
    "pressure_offset",
    "rapid_depth_average",
    "rapid_pressure_at_depth",
    "reduce_dip",
    "stable_stops",
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
    """Subtract a variable's caldip offset from a stage-3 dataset (not implemented yet).

    Pre and post offsets are to be interpolated linearly in time, one is constant
    (``docs/source/methods/calibration.rst``).

    Parameters
    ----------
    ds : xarray.Dataset
        Stage-3 dataset.
    pre : Reduction or None
        Pre-deployment correction.
    post : Reduction or None, optional
        Post-deployment correction.

    Returns
    -------
    xarray.Dataset

    Raises
    ------
    NotImplementedError
        Always.
    """
    del ds, pre, post
    msg = "apply_caldip is not implemented; see docs/source/methods/calibration.rst"
    raise NotImplementedError(msg)
