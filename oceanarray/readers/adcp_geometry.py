"""The originator's ADCP bin geometry, as read from an imported file.

An originator's file (RODB, OceanSITES) gives each bin's depth or distance from the
head; these helpers turn that into the ADCP's orientation and ``range`` coordinate,
and name the ``bin_pressure`` attributes of bin pressures taken from the file. The
readers use them, and stage 3 checks ``bin_pressure_source`` against them.
"""

from __future__ import annotations

import warnings

import numpy as np

from oceanarray.config.parameters import ADCP_BIN_DIM

#: ``bin_pressure`` attributes of bin pressures read from an originator's file; stage 3
#: keeps a ``bin_pressure`` with this ``bin_pressure_source``.
ORIGINATOR_BIN_PRESSURE_ATTRS: dict[str, str] = {
    "units": "dbar",
    "bin_pressure_source": "originator",
    "tilt_correction": "none (originator bin pressures)",
}


def originator_bin_geometry(offset: np.ndarray) -> tuple[str, np.ndarray]:
    """Return an imported ADCP's orientation and its bins' distances from the head.

    Parameters
    ----------
    offset : numpy.ndarray
        Bin depth minus head depth (m), on ``(bin,)`` or ``(time, bin)``.

    Returns
    -------
    orientation : str
        ``"up"`` when the bins are above the head (median *offset* < 0), else ``"down"``.
    range : numpy.ndarray
        ``|offset|`` on ``(bin,)``; for a 2-D *offset*, the median over time.
    """
    rng = np.abs(offset)
    if rng.ndim == 2:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            rng = np.nanmedian(rng, axis=0)
    return ("up" if np.nanmedian(offset) < 0 else "down"), rng


def originator_range(rng: np.ndarray, how: str) -> tuple[str, np.ndarray, dict]:
    """Return an imported ADCP's ``range`` coordinate, for ``assign_coords``.

    Parameters
    ----------
    rng : numpy.ndarray
        Bin distances from the head (m), from :func:`originator_bin_geometry`.
    how : str
        How the distances were found, for the ``comment``.

    Returns
    -------
    tuple
        ``(dim, values, attrs)`` on :data:`~oceanarray.config.parameters.ADCP_BIN_DIM`.
    """
    return (
        ADCP_BIN_DIM,
        rng,
        {
            "units": "m",
            "long_name": "distance from the transducer to the bin centre",
            "comment": f"nominal, {how}; places the stack row only, not bin_pressure",
        },
    )
