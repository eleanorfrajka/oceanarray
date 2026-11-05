"""Find a mooring's calibration dips: the caldip file for each end, and which end a cast is.

The mooring YAML names the cast for each end of the deployment::

    caldip:
      deployment: {cruise: msm142_2026, cast: castM4}
      recovery:   {cruise: odb2026, cast: castX}

so a mooring whose two dips were processed on different cruises resolves both. A mooring with
one dip is an ordinary case. caldip never pairs dips; this module does, from the YAML.
"""

from __future__ import annotations

from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from oceanarray.pipeline.calibration.caldip import CALDIP_SUFFIX, CaldipError

#: The two ends of a deployment, as the YAML names them, and the role each dip plays.
DIP_ENDS: dict[str, str] = {"deployment": "pre", "recovery": "post"}


def caldip_root(
    block: Mapping[str, Any],
    *,
    caldip_dir: str | PathLike | None = None,
    yaml_dir: str | PathLike | None = None,
) -> Path | None:
    """Return the caldip output root: ``caldip_dir`` if given, else the block's ``root``.

    A relative ``root`` is taken relative to the folder holding the mooring YAML.

    Parameters
    ----------
    block : Mapping
        The mooring YAML's ``caldip:`` block.
    caldip_dir : str, os.PathLike or None, optional
        The run-time root (``caldip_dir=``); wins over the YAML.
    yaml_dir : str, os.PathLike or None, optional
        The folder holding the mooring YAML, for a relative ``root``.

    Returns
    -------
    pathlib.Path or None
        The root, or ``None`` when neither is given.

    """
    if caldip_dir is not None:
        return Path(caldip_dir)
    root = block.get("root")
    if not root:
        return None
    root = Path(root)
    if not root.is_absolute() and yaml_dir is not None:
        root = Path(yaml_dir) / root
    return root


def caldip_file(root: str | PathLike, cruise: str, cast: str) -> Path | None:
    """Return the ``{cast}_caldip.nc`` for one dip beneath a caldip root, or ``None``.

    The file is looked for at ``<root>/<cruise>/{cast}_caldip.nc`` first, then anywhere beneath
    ``root`` with ``cruise`` among the folders of its path (caldip's own layout is
    ``proc_calib/<cruise>/cal_dip/``).

    Parameters
    ----------
    root : str or os.PathLike
        The caldip output root.
    cruise : str
        The cruise the dip was processed under, as the YAML names it.
    cast : str
        The cast name, e.g. ``"castM4"``.

    Returns
    -------
    pathlib.Path or None
        The file, or ``None`` when there is none.

    Raises
    ------
    CaldipError
        If more than one file matches.

    """
    root = Path(root)
    name = f"{cast}{CALDIP_SUFFIX}"
    direct = root / cruise / name
    if direct.is_file():
        return direct
    matches = sorted(p for p in root.rglob(name) if cruise in p.relative_to(root).parts)
    if len(matches) > 1:
        listed = ", ".join(str(m) for m in matches)
        msg = f"{name} for cruise {cruise!r} is ambiguous beneath {root}: {listed}"
        raise CaldipError(msg)
    return matches[0] if matches else None


def caldip_ends(
    block: Mapping[str, Any],
    *,
    caldip_dir: str | PathLike | None = None,
    yaml_dir: str | PathLike | None = None,
) -> dict[str, Path | None]:
    """Return the caldip file for each end the ``caldip:`` block names.

    Parameters
    ----------
    block : Mapping
        The mooring YAML's ``caldip:`` block (checked by :func:`oceanarray.mooring.check`).
    caldip_dir : str, os.PathLike or None, optional
        The run-time root; wins over the block's ``root``.
    yaml_dir : str, os.PathLike or None, optional
        The folder holding the mooring YAML, for a relative ``root``.

    Returns
    -------
    dict
        ``{"deployment": path or None, "recovery": path or None}`` for the ends the block
        names; ``None`` where the file is not found.

    Raises
    ------
    CaldipError
        If no root is given, or a file is ambiguous.

    """
    root = caldip_root(block, caldip_dir=caldip_dir, yaml_dir=yaml_dir)
    if root is None:
        msg = "no caldip root: give caldip.root in the YAML or caldip_dir="
        raise CaldipError(msg)
    return {
        end: caldip_file(root, block[end]["cruise"], block[end]["cast"])
        for end in DIP_ENDS
        if end in block
    }


def cast_time(cast: xr.Dataset) -> pd.Timestamp:
    """Return the time of a cast: the middle of its bottle stops.

    Parameters
    ----------
    cast : xarray.Dataset
        A cast from :func:`~oceanarray.pipeline.calibration.caldip.read_caldip_cast`.

    Returns
    -------
    pandas.Timestamp
        The midpoint of the earliest and latest stop times.

    """
    times = pd.to_datetime(np.asarray(cast["time"].values).ravel())
    times = times[~times.isna()]
    if times.empty:
        msg = f"{cast.attrs.get('cast_id', 'cast')}: no stop times"
        raise CaldipError(msg)
    return times.min() + (times.max() - times.min()) / 2


def assign_dip_role(
    cast_time: Any, deployment_time: Any, recovery_time: Any
) -> str | None:
    """Classify a cast as the pre- or post-deployment dip for an instrument.

    ``dip_role`` is not in the caldip file: pre- versus post-deployment is a property of the
    (instrument, deployment) pair, so it is derived from the cast time against the
    instrument's deployment window.

    Parameters
    ----------
    cast_time : Any
        The cast time (see :func:`cast_time`).
    deployment_time : Any
        The instrument's deployment time.
    recovery_time : Any
        The instrument's recovery time.

    Returns
    -------
    str or None
        ``"pre"`` before deployment, ``"post"`` after recovery, ``None`` during the
        deployment or when a time is missing.

    """
    try:
        t = pd.Timestamp(cast_time)
        start = pd.Timestamp(deployment_time)
        end = pd.Timestamp(recovery_time)
    except (TypeError, ValueError):
        return None
    if any(pd.isna(x) for x in (t, start, end)):
        return None
    t, start, end = (x.tz_localize(None) if x.tzinfo else x for x in (t, start, end))
    if t < start:
        return "pre"
    if t > end:
        return "post"
    return None
