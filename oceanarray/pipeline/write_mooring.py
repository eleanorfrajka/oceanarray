"""Write one mooring's per-instrument datasets to their stage files.

The counterpart to :func:`oceanarray.pipeline.read_mooring.read_mooring`: it
names each file from the dataset's own stamped facts — identity (``name``),
``instrument`` and ``serial`` — under the directory the caller points at. It is
pipeline orchestration (it interprets the stamped mooring YAML to lay out files),
not a writer; the byte-level write seam is :func:`oceanarray.writers.write`.
"""

from collections.abc import Mapping
from pathlib import Path

import xarray as xr

from oceanarray import paths
from oceanarray.exceptions import MooringError
from oceanarray.pipeline._stage import STAGE_ATTR
from oceanarray.writers import write


def write_mooring(
    datasets: Mapping[str, xr.Dataset],
    mooring_dir: Path | str,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Write per-instrument datasets to ``<mooring_dir>/<instrument>/<name>_<serial>_stage<N>.nc``.

    The mooring name, instrument type, serial and stage come from each dataset's
    ``mooring_name``, ``instrument``, ``serial`` and ``processing_stage``
    attributes (stamped by :func:`~oceanarray.pipeline.read_mooring.read_mooring`),
    so the location is the only argument. The key of each dict entry must equal
    the dataset's ``serial`` attribute.

    Parameters
    ----------
    datasets : Mapping of str to xarray.Dataset
        Per-instrument datasets keyed by serial.
    mooring_dir : pathlib.Path or str
        The mooring's own directory, holding the per-instrument subdirectories.
    overwrite : bool, optional
        Passed through to :func:`oceanarray.writers.write`; False refuses to
        replace an existing file.

    Returns
    -------
    list of pathlib.Path
        The paths written, in iteration order.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If a dataset carries no ``processing_stage`` marker, or its ``serial``
        attribute does not match its dict key.
    """
    moor_dir = Path(mooring_dir)
    written: list[Path] = []
    for serial, ds in datasets.items():
        stage = ds.attrs.get(STAGE_ATTR)
        if stage is None:
            raise MooringError(
                f"s/n {serial}: the dataset has no '{STAGE_ATTR}' marker; "
                "stamp it before writing."
            )
        ds_serial = paths.safe_serial(ds.attrs.get("serial", ""))
        if ds_serial != serial:
            raise MooringError(
                f"dataset serial {ds_serial!r} does not match its key {serial!r}; "
                "the write would mislabel the file."
            )
        name = ds.attrs.get("mooring_name")
        instr_type = ds.attrs.get("instrument")
        if not name or not instr_type:
            raise MooringError(
                f"s/n {serial}: the dataset is missing "
                f"{'mooring_name' if not name else 'instrument'}; both name the "
                "output file and cannot be defaulted — stamp the dataset first."
            )
        path = paths.stage_file(moor_dir, name, instr_type, serial, int(stage))
        written.append(write(ds, path, overwrite=overwrite))
    return written
