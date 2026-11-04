"""The ``export`` verb: a mooring's stage-3 files and grid as OceanSITES 1.4 files."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import Any

import xarray as xr
import yaml

from oceanarray import paths
from oceanarray.exceptions import MooringError
from oceanarray.instruments import instrument_entries
from oceanarray.pipeline.oceansites import (
    OceanSitesError,
    oceansites_filename,
    to_oceansites,
)
from oceanarray.writers import write

logger = logging.getLogger(__name__)

#: Errors that leave one file out rather than stopping the export: a dataset that
#: cannot be converted, a file that exists (without ``overwrite``), a file that
#: cannot be read, or a value in it that cannot be parsed.
_SKIPPABLE = (OceanSitesError, FileExistsError, OSError, KeyError, ValueError)


def _array_description(array: str | PathLike | Mapping | None) -> Mapping:
    """Return the array description as a mapping ({} when none is given)."""
    if array is None:
        return {}
    if isinstance(array, Mapping):
        return array
    with Path(array).open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, Mapping):
        raise MooringError(f"{array}: not an array description (a YAML mapping)")
    return data


def export(
    mooring: str | PathLike | Mapping[str, Any],
    *,
    mooring_dir: PathLike | None = None,
    out_dir: PathLike | None = None,
    array: str | PathLike | Mapping | None = None,
    grid: bool = True,
    overwrite: bool = False,
) -> list[Path]:
    """Write a mooring's stage-3 files and grid as OceanSITES 1.4 files.

    Each instrument's stage-3 file becomes a deployment data file and the grid a
    gridded file (see :func:`oceanarray.pipeline.oceansites.to_oceansites`).  An
    instrument whose stage-3 file is missing or cannot be converted is reported
    and left out; the others are written.

    Parameters
    ----------
    mooring : str, os.PathLike or Mapping
        The ``*.mooring.yaml``, the folder holding it, or a loaded description
        (then *mooring_dir* is required).  It must carry ``platform_code``.
    mooring_dir : os.PathLike or None, optional
        The mooring's processed folder.
    out_dir : os.PathLike or None, optional
        Where the files go; default ``<mooring_dir>/oceansites``.
    array : str, os.PathLike, Mapping or None, optional
        The ``*.array.yaml`` (or its loaded mapping); supplies ``site_code`` and
        ``project``.
    grid : bool, optional
        Also write the grid, when ``<name>_grid.nc`` exists (default True).
    overwrite : bool, optional
        Replace files that exist (default False).

    Returns
    -------
    list of pathlib.Path
        The files written, instruments first, then the grid.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If the description cannot be loaded or nothing could be exported.
    oceanarray.pipeline.oceansites.OceanSitesError
        If the description has no usable ``platform_code`` or deployment code.

    Examples
    --------
    >>> oa.export("proc/dune2_1_2026/dune2_1_2026.mooring.yaml", array="proc/MIXSED2026.array.yaml")
    """
    from oceanarray.pipeline import resolve_mooring
    from oceanarray.pipeline.oceansites import deployment_code, platform_code

    cfg, folder = resolve_mooring(mooring, mooring_dir, caller="export")
    # Fail before any file is opened if the description cannot name the files.
    platform_code(cfg)
    deployment_code(cfg)
    array_cfg = _array_description(array)
    target = Path(out_dir) if out_dir is not None else folder / "oceansites"
    name = cfg["name"]

    written: list[Path] = []
    left_out: list[str] = []
    for entry in instrument_entries(cfg):
        if entry.get("skip"):
            continue
        serial = paths.safe_serial(entry.get("serial"))
        path = paths.stage_file(folder, name, str(entry.get("instrument")), serial, 3)
        if not path.exists():
            left_out.append(f"{serial}: no stage-3 file ({path.name})")
            continue
        try:
            with xr.open_dataset(path) as ds:
                out = to_oceansites(
                    ds.load(), cfg, array=array_cfg, source_file=path.name
                )
            written.append(
                write(out, target / oceansites_filename(out), overwrite=overwrite)
            )
        except _SKIPPABLE as e:
            # One instrument that cannot be exported does not stop the others.
            left_out.append(f"{serial}: {e}")

    grid_path = paths.product_file(folder, name, "grid")
    if grid and grid_path.exists():
        stack_path = paths.product_file(folder, name, "stack")
        stack = xr.open_dataset(stack_path) if stack_path.exists() else None
        try:
            with xr.open_dataset(grid_path) as g:
                out = to_oceansites(
                    g.load(),
                    cfg,
                    array=array_cfg,
                    stack=stack,
                    source_file=grid_path.name,
                )
            written.append(
                write(out, target / oceansites_filename(out), overwrite=overwrite)
            )
        except _SKIPPABLE as e:
            left_out.append(f"grid: {e}")
        finally:
            if stack is not None:
                stack.close()

    for item in left_out:
        logger.warning("export %s: left out %s", name, item)
    if not written:
        raise MooringError(f"export {name}: nothing exported ({'; '.join(left_out)})")
    return written
