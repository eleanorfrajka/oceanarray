"""Read every instrument of one mooring into a dict of datasets.

:func:`read_mooring` reads each raw file (or opens each stage file) of the
instruments a mooring description lists, and stamps each dataset with the
description's facts: the per-instrument entry (serial, instrument, hab, …) and the per-mooring header
(name, position, deployment window, water depth). These attributes are the
contract every pure stage function and product relies on, so they travel with
the data rather than being re-read from the YAML at each step.

A mooring's **identity** is its ``name:`` field (every file is named
``<name>_<serial>_stage<N>.nc``); its **location** is where the caller points
(*mooring_dir* for stage files, *raw_dir* for raw). A directory whose basename
differs from ``name`` is tolerated — the files are still named from ``name`` —
and the validator warns about it.
"""

import warnings
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray import paths, readers
from oceanarray.exceptions import MooringError
from oceanarray.pipeline import _stage, raw_files
from oceanarray.utilities import (
    format_latlon,
    instrument_entries,
    is_unknown_latlon_source,
    parse_latlon_with_source,
)

# Placeholders the pipeline has always written when position / window are absent
# (Eleanor, 2026-10-24: nobody mistakes 0/0 or the YYYY string for data). They
# stay, but become loud: a WARNING naming the missing keys, and a *_source of
# "none" so a consumer never infers the value is real.
_TIME_PLACEHOLDER = "YYYY-mm-ddTHH:MM:ss"

# Per-instrument entry keys copied verbatim onto the dataset (when present).
# ``instrument`` is not listed — it is stamped explicitly in _stamp_raw after
# the loop has already resolved and validated it.
_INSTRUMENT_ATTRS = (
    "hab",
    "clamp_id",
    "sample_interval_seconds",
    "file_type",
)


def _resolve(
    mooring: Mapping[str, Any] | str | Path,
    mooring_dir: str | Path | None,
    stage: int | None,
    caller: str = "read_mooring",
) -> tuple[Mapping[str, Any], Path | None]:
    """Return the loaded description and its directory from *mooring*.

    A path (a ``*.mooring.yaml`` or a directory holding exactly one) supplies its
    own directory; a dict carries no location, so *mooring_dir* is required when a
    stage file is to be read.
    """
    if isinstance(mooring, Mapping):
        if stage is not None and mooring_dir is None:
            raise MooringError(
                f"{caller} was given a mooring dict with stage="
                f"{stage} but no mooring_dir=; a dict has no location to find "
                "the stage files — pass mooring_dir=."
            )
        return mooring, Path(mooring_dir) if mooring_dir is not None else None

    path = Path(mooring)
    if path.is_dir():
        candidates = sorted(path.glob("*.mooring.yaml"))
        if len(candidates) != 1:
            raise MooringError(
                f"{path} holds {len(candidates)} '*.mooring.yaml' files; "
                f"{caller} needs exactly one (or point at the file)."
            )
        return _mooring.load(candidates[0]), Path(mooring_dir or path)
    return _mooring.load(path), Path(mooring_dir or path.parent)


def _mooring_attrs(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Return the per-mooring header attributes, stamped on every instrument.

    Position and the deployment window are always stamped (placeholder when
    absent) with a ``*_source`` of ``"none"`` and a WARNING; other header values
    are omitted when absent rather than defaulted. ``mooring_name`` is the
    description's ``name`` (the mooring's identity).
    """
    name = cfg.get("name")
    if name is None:
        raise MooringError("the mooring description has no 'name'; it is required.")
    attrs: dict[str, Any] = {"mooring_name": name}

    lat, lon, source = parse_latlon_with_source(cfg)
    attrs["latitude"] = lat
    attrs["longitude"] = lon
    if is_unknown_latlon_source(source):
        attrs["location_source"] = "none"
        warnings.warn(
            f"{name}: no position in the mooring description "
            f"(seabed_/deployment_/planned_latitude or latitude); "
            f"stamping latitude=0, longitude=0 with location_source='none'.",
            stacklevel=2,
        )
    else:
        attrs["location_source"] = source
        lat_s, lon_s = format_latlon(lat, lon)
        attrs["latitude_str"] = lat_s
        attrs["longitude_str"] = lon_s

    deploy = cfg.get("deployment_time")
    recover = cfg.get("recovery_time")
    if deploy is not None and recover is not None:
        attrs["deployment_time"] = str(deploy)
        attrs["recovery_time"] = str(recover)
        attrs["deployment_window_source"] = "deployment_time/recovery_time"
    else:
        attrs["deployment_time"] = _TIME_PLACEHOLDER
        attrs["recovery_time"] = _TIME_PLACEHOLDER
        attrs["deployment_window_source"] = "none"
        warnings.warn(
            f"{name}: no deployment window in the mooring description "
            f"(deployment_time/recovery_time); stamping the placeholder with "
            f"deployment_window_source='none'.",
            stacklevel=2,
        )

    for key in ("waterdepth", "density_reference"):
        if cfg.get(key) is not None:
            attrs[key] = cfg[key]

    return attrs


def read_mooring(
    mooring: Mapping[str, Any] | str | Path,
    *,
    stage: int | None = None,
    serials: Iterable[str] | None = None,
    allow_missing: bool = False,
    mooring_dir: str | Path | None = None,
    raw_dir: str | Path | None = None,
) -> dict[str, xr.Dataset]:
    """Read one mooring's instruments into a dict keyed by instrument serial.

    Parameters
    ----------
    mooring : Mapping or str or pathlib.Path
        A loaded mooring description, a ``*.mooring.yaml`` path, or a directory
        holding exactly one such file.
    stage : int or None, optional
        ``None`` finds and reads each raw file (from *raw_dir*) as stage 1 does —
        a missing ``filename`` found by naming convention, the Nortek header, the
        raw header's calibration metadata — and returns it unmarked, ready for
        :func:`oceanarray.pipeline.stage1.stage1`; entries marked ``skip`` are left
        out. ``1``, ``2`` or ``3`` opens the stage file (from *mooring_dir*) and
        checks its ``processing_stage`` marker. The description's facts are
        stamped on every path.
    serials : iterable of str or None, optional
        If given, read only the instruments whose (cleaned) serial is listed.
    allow_missing : bool, optional
        When True, an instrument with no file at the requested stage is skipped
        with a WARNING instead of raising (the CLI ``--allow-mixed-stages`` flag).
    mooring_dir : str or pathlib.Path or None, optional
        The mooring's own directory, holding the per-instrument subdirectories.
        Required for ``stage`` 2 or 3 when *mooring* is a dict; derived from the
        path otherwise.
    raw_dir : str or pathlib.Path or None, optional
        Cruise-level raw-data root; required for ``stage=None``.

    Returns
    -------
    dict of str to xarray.Dataset
        One dataset per instrument, keyed by cleaned serial, in the order the
        description lists them.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If a dict is given with a stage but no *mooring_dir*, or the description
        has no ``name``.
    ValueError
        If *raw_dir* is not given for ``stage=None``.
    FileNotFoundError
        If an instrument's file for the chosen *stage* is absent and
        *allow_missing* is False.
    oceanarray.pipeline._stage.StageError
        If an opened stage file carries the wrong ``processing_stage`` marker.
    """
    cfg, mooring_dir = _resolve(mooring, mooring_dir, stage)
    if stage is None and raw_dir is None:
        raise ValueError("read_mooring(stage=None) needs raw_dir= to find raw files.")

    name = cfg["name"]
    wanted = {paths.safe_serial(s) for s in serials} if serials is not None else None
    moor_attrs = _mooring_attrs(cfg)

    datasets: dict[str, xr.Dataset] = {}
    for entry in instrument_entries(cfg):
        if not isinstance(entry, dict):
            continue
        serial = paths.safe_serial(entry.get("serial", ""))
        if wanted is not None and serial not in wanted:
            continue
        instr_type = entry.get("instrument")
        if not instr_type:
            raise MooringError(
                f"{name}: instrument entry for serial {serial!r} has no "
                "'instrument' key; it names the subdirectory and the stamped "
                "instrument type, so it cannot be defaulted."
            )

        if stage is None:
            if entry.get("skip"):
                continue
            try:
                raw = raw_files.resolve_raw_file(entry, cfg, raw_dir)
                if not raw.path.exists():
                    raise FileNotFoundError(
                        f"{name}: {instr_type} s/n {serial} raw file not found at "
                        f"{raw.path}."
                    )
            except (MooringError, FileNotFoundError):
                if not allow_missing:
                    raise
                warnings.warn(
                    f"{name}: {instr_type} s/n {serial} has no raw file; skipping "
                    "(allow_missing).",
                    stacklevel=2,
                )
                continue
            ds = raw_files.read_raw_file(raw)
            ds = _join_description(ds, raw.entry, serial, instr_type, moor_attrs)
        else:
            ds = _read_stage(
                serial, instr_type, name, stage, mooring_dir, allow_missing
            )
            if ds is None:
                continue
            _stage.require({serial: ds}, stage, stage)
            ds = _join_description(ds, entry, serial, instr_type, moor_attrs)

        datasets[serial] = ds

    return datasets


def join_raw(
    ds: xr.Dataset, entry: dict[str, Any], mooring: Mapping[str, Any]
) -> xr.Dataset:
    """Join one instrument's raw dataset with its description, as ``read_mooring`` does.

    For a caller that has already found and read the raw file (the stage-1 class,
    which needs the file's path before reading to decide whether to skip it).

    Parameters
    ----------
    ds : xarray.Dataset
        The raw dataset, from :func:`oceanarray.pipeline.raw_files.read_raw_file`.
    entry : dict
        The instrument's description entry.
    mooring : Mapping
        The loaded mooring description.

    Returns
    -------
    xarray.Dataset
        A copy carrying the stamping-contract attributes, unmarked.
    """
    serial = paths.safe_serial(entry.get("serial", ""))
    instr_type = entry.get("instrument")
    if not instr_type:
        raise MooringError(
            f"{mooring['name']}: instrument entry for serial {serial!r} has no "
            "'instrument' key; it names the subdirectory and the stamped "
            "instrument type, so it cannot be defaulted."
        )
    return _join_description(ds, entry, serial, instr_type, _mooring_attrs(mooring))


def _join_description(
    ds: xr.Dataset,
    entry: dict[str, Any],
    serial: str,
    instr_type: str,
    moor_attrs: dict[str, Any],
) -> xr.Dataset:
    """Join a dataset with its mooring description — the stamping contract (ruling 1).

    Reading a mooring means joining a file with the description it belongs to, so the
    stamping-contract attributes travel on the dataset whatever path it came by (raw
    or a stage file). ``mooring_name`` is the mooring's identity: a file whose
    ``mooring_name`` differs from the description's ``name`` only by case is corrected
    with a ``history`` line; one that differs otherwise was made under a different
    description and raises :class:`MooringError` (reprocess it from stage 1). A
    ``serial`` that differs from the entry's raises too (rule 11 — never mislabel).
    Other contract attributes are filled when absent.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset just read.
    entry : dict
        The instrument's description entry.
    serial : str
        The cleaned serial from the entry.
    instr_type : str
        The instrument type from the entry.
    moor_attrs : dict
        The per-mooring header attributes from :func:`_mooring_attrs`.

    Returns
    -------
    xarray.Dataset
        A copy carrying the stamping-contract attributes.
    """
    ds = ds.copy()
    existing = ds.attrs.get("serial")
    if existing is not None and paths.safe_serial(existing) != serial:
        raise MooringError(
            f"{moor_attrs['mooring_name']}: dataset serial {existing!r} contradicts "
            f"the description's {serial!r} for this instrument — refusing to mislabel it."
        )
    name = moor_attrs["mooring_name"]
    existing_name = ds.attrs.get("mooring_name")
    if existing_name is not None and existing_name != name:
        if str(existing_name).lower() != str(name).lower():
            raise MooringError(
                f"{name}: the dataset's mooring_name {existing_name!r} differs from the "
                f"description's name {name!r} by more than case — it was made under a "
                "different description; reprocess it from stage 1."
            )
        _stage.append_history(
            ds, f"mooring_name corrected from {existing_name!r} to {name!r} (case)"
        )
    for key, value in moor_attrs.items():
        # mooring_name is the identity (checked above); the rest are filled only
        # when absent.
        if key == "mooring_name" or key not in ds.attrs:
            ds.attrs[key] = value
    ds.attrs["serial"] = serial
    for key in _INSTRUMENT_ATTRS:
        if entry.get(key) is not None and key not in ds.attrs:
            ds.attrs[key] = entry[key]
    ds.attrs["instrument"] = instr_type
    ds.attrs["stack"] = bool(entry.get("stack", True))
    return ds


def _read_stage(
    serial: str,
    instr_type: str,
    name: str,
    stage: int,
    mooring_dir: Path | None,
    allow_missing: bool,
) -> xr.Dataset | None:
    """Open one instrument's stage file, or refuse/skip when it is absent.

    A file written before the ``processing_stage`` marker existed is stamped
    in memory with the stage read off its filename (``inferred``) and a WARNING,
    so the returned dataset always carries a marker — nothing is written to disk
    by reading, and the read/write pair stays symmetric. Persist the marker with
    ``stamp``.
    """
    stage_path = paths.stage_file(mooring_dir, name, instr_type, serial, stage)
    if not stage_path.exists():
        if allow_missing:
            warnings.warn(
                f"{name}: {instr_type} s/n {serial} has no stage-{stage} "
                f"file ({stage_path.name}); skipping (allow_missing).",
                stacklevel=2,
            )
            return None
        raise FileNotFoundError(
            f"{name}: {instr_type} s/n {serial} has no stage-{stage} file "
            f"at {stage_path}. Run stage {stage} first, or pass allow_missing=True "
            f"(CLI --allow-mixed-stages)."
        )
    ds = readers.read(stage_path)
    if _stage.STAGE_ATTR not in ds.attrs:
        warnings.warn(
            f"{name}: {instr_type} s/n {serial} stage-{stage} file has no "
            f"'{_stage.STAGE_ATTR}' marker (written before it existed); inferring "
            f"stage {stage} from the filename. Run stamp to persist it.",
            stacklevel=2,
        )
        ds = _stage.stamp(ds, stage, inferred=True)
    return ds
