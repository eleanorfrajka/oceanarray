"""The processing-stage marker that records and enforces stage order.

Each stage file carries a ``processing_stage`` attribute naming the stage that
produced it; :func:`stamp` writes it, :func:`require` refuses a dataset that is at
the wrong stage for the next step. A file written before the marker existed is
tolerated for one release: read back, a missing marker is a WARNING and the
dataset is taken at the stage the caller expected, so existing proc trees keep
working.
"""

import datetime
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import xarray as xr

from oceanarray.exceptions import MooringError

STAGE_ATTR = "processing_stage"


class StageError(MooringError):
    """Raised when a dataset is at the wrong processing stage for the step requested."""


@dataclass
class StageRun:
    """What one stage did to a mooring's files.

    Attributes
    ----------
    written : list of pathlib.Path
        The stage files written.
    files : list of pathlib.Path
        Every stage file the instruments asked for now have, written or kept
        because it was already there, in the description's order.
    failed : list of str
        One message per instrument that failed, naming it.
    matched : int
        The instruments the stage was asked to process (skipped ones included).
    """

    written: list[Path] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    matched: int = 0


def _history_stamp() -> str:
    """Return the current UTC time as the package's ``history`` timestamp."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%MZ")


def append_history(ds: xr.Dataset, note: str) -> None:
    """Append a timestamped *note* to ``ds.attrs['history']`` in place.

    The one place the ``history`` timestamp format and the ``"; "`` separator
    live, so every stage appends in the same form.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset whose ``history`` attribute is extended.
    note : str
        The event text, timestamped and appended.
    """
    entry = f"{_history_stamp()}: {note}"
    existing = ds.attrs.get("history", "")
    ds.attrs["history"] = f"{existing}; {entry}" if existing else entry


def stamp(
    ds: xr.Dataset,
    stage: int,
    *,
    source: str | None = None,
    inferred: bool = False,
) -> xr.Dataset:
    """Return a copy of *ds* marked as the output of *stage*.

    Sets the ``processing_stage`` marker, appends a ``history`` line, and records
    ``source_file`` when *source* is given (an existing ``source_file`` is kept).

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset to mark.
    stage : int
        The stage that produced *ds* (1, 2 or 3).
    source : str or None, optional
        The source filename to record as ``source_file``.
    inferred : bool, optional
        When True, the stage was not earned by processing but read off an
        unmarked file's name; the ``history`` line says so, keeping the
        provenance honest.

    Returns
    -------
    xarray.Dataset
        A copy of *ds* carrying the marker, history line and (optional) source.
    """
    out = ds.copy()
    out.attrs[STAGE_ATTR] = int(stage)
    note = f"processing_stage {int(stage)}"
    if inferred:
        note += " inferred from filename"
    append_history(out, note)
    if source is not None:
        out.attrs["source_file"] = source
    return out


def require(
    datasets: Mapping[str, xr.Dataset] | Sequence[xr.Dataset],
    stage: int,
    next_stage: int,
) -> None:
    """Raise :class:`StageError` unless every dataset is at *stage* before *next_stage*.

    A dataset with no ``processing_stage`` marker (written before the marker
    existed) is a WARNING and is tolerated at *stage*, for one release.

    Parameters
    ----------
    datasets : Mapping of str to xarray.Dataset, or sequence of xarray.Dataset
        The datasets about to enter stage *next_stage*.
    stage : int
        The stage the datasets must already be at.
    next_stage : int
        The stage about to run, named in the error and the warning.

    Raises
    ------
    StageError
        If any dataset carries a marker other than *stage*, naming the stage to run.
    """
    items = datasets.items() if isinstance(datasets, Mapping) else enumerate(datasets)
    for key, ds in items:
        marker = ds.attrs.get(STAGE_ATTR)
        if marker is None:
            warnings.warn(
                f"stage {next_stage}: dataset {key!r} has no '{STAGE_ATTR}' marker; "
                f"assuming stage {stage} (written before the marker existed).",
                stacklevel=2,
            )
            continue
        if int(marker) != stage:
            raise StageError(
                f"stage {next_stage} expects stage-{stage} datasets; {key!r} is "
                f"stage {int(marker)} — run stage {stage} first."
            )
