"""Mark stage files written before the ``processing_stage`` marker existed.

Stage files now carry a ``processing_stage`` attribute; a file written before it
existed is read with a WARNING and its stage inferred from the filename. ``stamp``
writes the marker into such a file, declaring the stage its name already states, so
later reads are quiet. Only the two global attributes ``processing_stage`` and
``history`` are touched; the data, coordinates and encodings are left as they are.
It never relabels a file: a stage that contradicts the filename, or a marker already
present with another value, is refused.
"""

from pathlib import Path

import netCDF4

from oceanarray import paths
from oceanarray.pipeline._stage import STAGE_ATTR, StageError, _history_stamp

_STAGES = (1, 2, 3)


def _parse_stage(path: Path) -> int | None:
    """Return the stage a filename states, or None if it is not a stage file."""
    for stage in _STAGES:
        if path.name.endswith(paths.stage_suffix(stage)):
            return stage
    return None


def stamp_file(path: str | Path, stage: int) -> bool:
    """Write the ``processing_stage`` marker into one stage file, in place.

    Parameters
    ----------
    path : str or pathlib.Path
        A stage file, ``<name>_<serial>_stage<N>.nc``.
    stage : int
        The stage to declare; must equal the ``N`` in the filename.

    Returns
    -------
    bool
        True if the marker was written, False if the file already carried it.

    Raises
    ------
    oceanarray.pipeline._stage.StageError
        If the filename is not a stage file, names a different stage, or the file
        already carries a different ``processing_stage``.
    """
    path = Path(path)
    named = _parse_stage(path)
    if named is None:
        raise StageError(
            f"{path.name} is not a stage file (<name>_<serial>_stage<N>.nc); "
            "refusing to stamp it."
        )
    if named != stage:
        raise StageError(
            f"{path.name} is a stage-{named} file by its name; refusing to "
            f"declare it stage {stage}."
        )
    with netCDF4.Dataset(path, "a") as nc:
        attrs = nc.ncattrs()
        if STAGE_ATTR in attrs:
            marker = int(nc.getncattr(STAGE_ATTR))
            if marker == stage:
                return False
            raise StageError(
                f"{path.name} already carries {STAGE_ATTR} = {marker}; refusing "
                f"to declare it stage {stage}."
            )
        entry = f"{_history_stamp()}: processing_stage {stage} declared via stamp"
        history = nc.getncattr("history") if "history" in attrs else ""
        nc.setncattr(STAGE_ATTR, stage)
        nc.setncattr("history", f"{history}; {entry}" if history else entry)
    return True
