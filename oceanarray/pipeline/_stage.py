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

import pandas as pd
import xarray as xr

from oceanarray import provenance
from oceanarray.exceptions import MooringError
from oceanarray.instruments import nominal_depth

STAGE_ATTR = "processing_stage"


#: Attributes of the instrument's identity scalars.
IDENTITY_ATTRS: dict[str, dict[str, str]] = {
    "instrument_depth": {
        "units": "m",
        "long_name": "nominal instrument depth",
        "comment": "design depth below the surface, from the mooring description",
    },
    "instrument_serial": {
        "long_name": "the instrument's serial number; shared by all bins of one ADCP"
    },
}


#: The names stage files before 0.6.1 gave the identity scalars.
OLD_IDENTITY_NAMES: dict[str, str] = {
    "InstrDepth": "instrument_depth",
    "serial_number": "instrument_serial",
}


def normalize_identity_names(ds: xr.Dataset) -> xr.Dataset:
    """Return ``ds`` with the identity scalars under their names since 0.6.1.

    Parameters
    ----------
    ds : xarray.Dataset
        A stage file or stack, of any version.

    Returns
    -------
    xarray.Dataset
        ``InstrDepth`` renamed ``instrument_depth`` and ``serial_number`` renamed
        ``instrument_serial`` (as text), with :data:`IDENTITY_ATTRS`; ``ds`` itself
        when it has neither old name.
    """
    renames = {
        old: new
        for old, new in OLD_IDENTITY_NAMES.items()
        if old in ds.variables and new not in ds.variables
    }
    if not renames:
        return ds
    ds = ds.rename(renames)
    for new in renames.values():
        var = ds[new].variable
        values = var.values.astype(str) if new == "instrument_serial" else var.values
        ds[new] = xr.Variable(var.dims, values, IDENTITY_ATTRS[new])
    return ds


def identity_variables(
    entry: Mapping, waterdepth: float | None
) -> dict[str, xr.Variable]:
    """Return an instrument's ``instrument_serial`` and ``instrument_depth`` scalars.

    Parameters
    ----------
    entry : Mapping
        The instrument entry of the mooring description.
    waterdepth : float or None
        The mooring's water depth (m).

    Returns
    -------
    dict of str to xarray.Variable
        ``instrument_serial`` (text) when the entry has a serial, and
        ``instrument_depth`` (m) when :func:`oceanarray.instruments.nominal_depth`
        gives one.
    """
    out = {}
    if entry.get("serial") is not None:
        out["instrument_serial"] = xr.Variable(
            (), str(entry["serial"]), IDENTITY_ATTRS["instrument_serial"]
        )
    depth = nominal_depth(dict(entry), waterdepth)
    if depth is not None:
        out["instrument_depth"] = xr.Variable(
            (), depth, IDENTITY_ATTRS["instrument_depth"]
        )
    return out


def iso_time(value: object) -> str:
    """Return an instant as ISO 8601 UTC to the second, ``YYYY-MM-DDThh:mm:ssZ``.

    The OceanSITES 1.4 form (manual §2.2) for time attributes.

    Parameters
    ----------
    value : object
        A time :class:`pandas.Timestamp` accepts (string, datetime, datetime64).

    Returns
    -------
    str
        The time, truncated to whole seconds.
    """
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


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
    failed_serials : list of str
        The (cleaned) serials of those instruments.
    matched : int
        The instruments the stage was asked to process (skipped ones included).
    """

    written: list[Path] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    failed_serials: list[str] = field(default_factory=list)
    matched: int = 0


def _history_stamp() -> str:
    """Return the current UTC time as the package's ``history`` timestamp."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%MZ")


#: What each stage did, as its ``history`` entry says it.
STAGE_WORDS: dict[int, str] = {
    1: "read into oceanarray's variable names and units",
    2: "trimmed to the deployment window",
    3: "checked (automatic tests, originator flags translated) and derived variables added",
}

#: Separates the entries of ``history``; a note never contains it.
HISTORY_SEPARATOR = "; "


def clean_history_note(note: str) -> str:
    """Return *note* without the entry separator, so it reads back as one entry.

    ``history`` is read back by splitting on ``"; "`` (between entries) and then on
    the first ``": "`` (time from text); a ``"; "`` inside a note would split it
    mid-sentence. Each one becomes ``", "``. Several events of one entry are joined
    with ``" | "`` instead.

    Parameters
    ----------
    note : str
        The event text.

    Returns
    -------
    str
        *note* with every ``"; "`` replaced by ``", "``, stripped.
    """
    return str(note).replace(HISTORY_SEPARATOR, ", ").strip()


def append_history(ds: xr.Dataset, note: str) -> None:
    """Append a timestamped *note* to ``ds.attrs['history']`` in place.

    The one place the ``history`` timestamp format and the ``"; "`` separator
    live, so every stage appends in the same form. A ``"; "`` inside *note* is
    cleaned to ``", "`` (:func:`clean_history_note`) so the entry is read back
    whole.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset whose ``history`` attribute is extended.
    note : str
        The event text, timestamped and appended.
    """
    entry = f"{_history_stamp()}: {clean_history_note(note)}"
    existing = ds.attrs.get("history", "")
    ds.attrs["history"] = f"{existing}{HISTORY_SEPARATOR}{entry}" if existing else entry


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
    note = f"stage {int(stage)}: {STAGE_WORDS.get(int(stage), 'written')} (processing_stage {int(stage)})"
    if inferred:
        note += " inferred from filename"
    append_history(out, note)
    if source is not None:
        out.attrs["source_file"] = source
    # The version that ran this stage stays with the file through later stages.
    out.attrs[provenance.stage_version_attr(stage)] = provenance.version_attrs()[
        provenance.VERSION_ATTR
    ]
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
