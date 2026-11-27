"""The ``export`` verb: a mooring's stage-3 files and grid as OceanSITES 1.4 files."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import Any

import xarray as xr
import yaml

from oceanarray import _deprecated
from oceanarray.exceptions import MooringError
from oceanarray.mooring.instruments import instrument_entries
from oceanarray.paths import Paths, product_file, safe_serial, stage_file
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


def _put(out: xr.Dataset, target: Path, *, overwrite: bool, dry_run: bool) -> Path:
    """Write *out* under its OceanSITES name in *target*, or only name it on a dry run."""
    path = target / oceansites_filename(out)
    if dry_run:
        if path.exists() and not overwrite:
            raise FileExistsError(f"{path} exists (use overwrite=True / --force)")
        return path
    return write(out, path, overwrite=overwrite)


def export(
    yaml_path: str | PathLike | None = None,
    paths: Paths | None = None,
    *,
    array_yaml: str | PathLike | Mapping | None = None,
    grid: bool = True,
    overwrite: bool = False,
    dry_run: bool = False,
    mooring: str | PathLike | Mapping[str, Any] | None = None,
    mooring_dir: PathLike | None = None,
    out_dir: PathLike | None = None,
    array: str | PathLike | Mapping | None = None,
) -> list[Path]:
    """Write a mooring's stage-3 files and grid as OceanSITES 1.4 files.

    Each instrument's stage-3 file becomes a deployment data file and the grid a
    gridded file (see :func:`oceanarray.pipeline.oceansites.to_oceansites`).  An
    instrument whose stage-3 file is missing or cannot be converted is reported
    and left out; the others are written.

    Parameters
    ----------
    yaml_path : str or os.PathLike
        The ``*.mooring.yaml``; it must carry ``platform_code``.
    paths : oceanarray.Paths
        Where the files are: the stage-3 files and grid in ``paths.mooring_dir(name)``,
        the OceanSITES files written to ``paths.export_dir``.
    array_yaml : str, os.PathLike, Mapping or None, optional
        The ``*.array.yaml`` (or its loaded mapping); supplies ``site_code`` and
        ``project``.
    grid : bool, optional
        Also write the grid, when ``<name>_grid.nc`` exists (default True).
    overwrite : bool, optional
        Replace files that exist (default False).
    dry_run : bool, optional
        Convert but write nothing; return the paths that would be written.
    mooring : str or os.PathLike or Mapping, optional
        Deprecated name of *yaml_path*; removed in 0.8.0.
    mooring_dir, out_dir : os.PathLike, optional
        Deprecated folders (*out_dir* default ``<mooring_dir>/oceansites``), used when
        *paths* is not given; removed in 0.8.0.
    array : str, os.PathLike or Mapping, optional
        Deprecated name of *array_yaml*; removed in 0.8.0.

    Returns
    -------
    list of pathlib.Path
        The files written, instruments first, then the grid.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If the mooring YAML cannot be loaded, a folder is missing from *paths*, or
        nothing could be exported.
    oceanarray.pipeline.oceansites.OceanSitesError
        If the mooring YAML has no usable ``platform_code`` or deployment code.
    TypeError
        *paths* is not an :class:`oceanarray.Paths`.

    Examples
    --------
    Not run as tests: they need a mooring YAML and its files on disk.

    >>> import oceanarray as oa
    >>> paths = oa.Paths.from_file("paths.yaml")  # doctest: +SKIP
    >>> oa.export(paths.yaml_path("M2_2016"), paths, array_yaml="OSNAPeast_2016.array.yaml")  # doctest: +SKIP
    """
    from oceanarray.pipeline import resolve_verb_call
    from oceanarray.pipeline.oceansites import deployment_code, platform_code

    renamed = {
        "oceanarray.export(mooring=)": mooring,
        "oceanarray.export(array=)": array,
    }
    yaml_path = _deprecated.resolve_keyword(
        "oceanarray.export(mooring=)",
        yaml_path,
        mooring,
        required="yaml_path",
        quiet=True,
    )
    array_yaml = _deprecated.resolve_keyword(
        "oceanarray.export(array=)", array_yaml, array, quiet=True
    )
    mooring_dict, _, paths, _ = resolve_verb_call(
        "export",
        yaml_path,
        paths,
        {"mooring_dir": mooring_dir, "out_dir": out_dir},
        lambda name, folder: Paths.from_mooring_dir(
            name, folder, export_dir=out_dir or folder / "oceansites"
        ),
        renamed=[k for k, v in renamed.items() if v is not None],
    )
    # Fail before any file is opened if the mooring YAML cannot name the files.
    platform_code(mooring_dict)
    deployment_code(mooring_dict)
    array_cfg = _array_description(array_yaml)
    name = mooring_dict["name"]
    folder = paths.mooring_dir(name)
    if not folder.is_dir():
        raise MooringError(f"Mooring directory not found: {folder}")
    if paths.export_dir is None:
        raise MooringError(
            f"{name}: no export_dir given; set export_dir in the paths file "
            "(paths.yaml) or Paths(export_dir=...)."
        )
    target = paths.export_dir

    written: list[Path] = []
    left_out: list[str] = []
    for entry in instrument_entries(mooring_dict):
        if entry.get("skip"):
            continue
        serial = safe_serial(entry.get("serial"))
        path = stage_file(folder, name, str(entry.get("instrument")), serial, 3)
        if not path.exists():
            left_out.append(f"{serial}: no stage-3 file ({path.name})")
            continue
        try:
            with xr.open_dataset(path) as ds:
                out = to_oceansites(
                    ds.load(), mooring_dict, array=array_cfg, source_file=path.name
                )
            written.append(_put(out, target, overwrite=overwrite, dry_run=dry_run))
        except _SKIPPABLE as e:
            # One instrument that cannot be exported does not stop the others.
            left_out.append(f"{serial}: {e}")

    grid_path = product_file(folder, name, "grid")
    if grid and grid_path.exists():
        stack_path = product_file(folder, name, "stack")
        stack = xr.open_dataset(stack_path) if stack_path.exists() else None
        try:
            with xr.open_dataset(grid_path) as g:
                out = to_oceansites(
                    g.load(),
                    mooring_dict,
                    array=array_cfg,
                    stack=stack,
                    source_file=grid_path.name,
                )
            written.append(_put(out, target, overwrite=overwrite, dry_run=dry_run))
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
