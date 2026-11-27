"""Read every instrument of one mooring into a dict of datasets.

:func:`read_mooring` reads each raw file (or opens each stage file) of the
instruments a mooring YAML lists, and stamps each dataset with the
mooring YAML's facts: the per-instrument entry (serial, instrument, hab, …) and the per-mooring header
(name, position, deployment window, water depth). These attributes are the
contract every pure stage function and product relies on, so they travel with
the data rather than being re-read from the YAML at each step.

A mooring's **identity** is its ``name:`` field (every file is named
``<name>_<serial>_stage<N>.nc``); its **location** is where the caller points
(*mooring_dir* for stage files, *raw_dir* for raw). A directory whose basename
differs from ``name`` is tolerated — the files are still named from ``name`` —
and the validator warns about it.
"""

import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import xarray as xr

from oceanarray import _deprecated, paths, readers
from oceanarray import mooring as _mooring
from oceanarray.exceptions import MooringError
from oceanarray.history import append_history
from oceanarray.mooring.instruments import instrument_entries
from oceanarray.mooring.location import (
    format_latlon,
    is_unknown_latlon_source,
    parse_latlon_with_source,
)
from oceanarray.pipeline import _stage, raw_files

log = logging.getLogger(__name__)

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
    "file_type",
)


def _resolve_description(
    mooring: Mapping[str, Any] | str | Path,
    mooring_dir: str | Path | None,
    stage: int | None,
    caller: str = "read_mooring",
) -> tuple[Mapping[str, Any], Path | None]:
    """Return the loaded mooring YAML and its directory from *mooring*."""
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


def header_attrs(
    mooring_dict: Mapping[str, Any] | None = None,
    *,
    warn: bool = True,
    cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the per-mooring header attributes, stamped on every instrument.

    Parameters
    ----------
    mooring_dict : Mapping
        The loaded mooring YAML.
    warn : bool, optional
        Warn when the position or deployment window is missing (default True).
    cfg : Mapping, optional
        Deprecated name of *mooring_dict*; removed in 0.8.0.

    Returns
    -------
    dict of str to Any
        ``mooring_name``, ``location_source``, the deployment window (ISO 8601)
        and, when given, ``latitude`` / ``longitude`` (decimal degrees),
        ``waterdepth`` and ``density_reference``.
    """
    mooring_dict = _deprecated.resolve_keyword(
        "oceanarray.pipeline.read_mooring.header_attrs(cfg=)",
        mooring_dict,
        cfg,
        required="mooring_dict",
    )
    # The deployment window is always stamped (placeholder + *_source "none" when
    # absent); a missing position is left out, so a file's own is kept.
    name = mooring_dict.get("name")
    if name is None:
        raise MooringError("the mooring YAML has no 'name'; it is required.")
    attrs: dict[str, Any] = {"mooring_name": name}

    lat, lon, source = parse_latlon_with_source(mooring_dict)
    if is_unknown_latlon_source(source):
        attrs["location_source"] = "none"
        if warn:
            log.warning(
                "%s: no position in the mooring YAML "
                "(seabed_/deployment_/planned_latitude or latitude); "
                "leaving latitude/longitude out, location_source='none'.",
                name,
            )
    else:
        attrs["latitude"] = lat
        attrs["longitude"] = lon
        attrs["location_source"] = source
        lat_s, lon_s = format_latlon(lat, lon)
        attrs["latitude_str"] = lat_s
        attrs["longitude_str"] = lon_s

    deploy = mooring_dict.get("deployment_time")
    recover = mooring_dict.get("recovery_time")
    if deploy is not None and recover is not None:
        attrs["deployment_time"] = _stage.iso_time(deploy)
        attrs["recovery_time"] = _stage.iso_time(recover)
        attrs["deployment_window_source"] = "deployment_time/recovery_time"
    else:
        attrs["deployment_time"] = _TIME_PLACEHOLDER
        attrs["recovery_time"] = _TIME_PLACEHOLDER
        attrs["deployment_window_source"] = "none"
        if warn:
            log.warning(
                "%s: no deployment window in the mooring YAML "
                "(deployment_time/recovery_time); stamping the placeholder with "
                "deployment_window_source='none'.",
                name,
            )

    for key in ("waterdepth", "density_reference"):
        if mooring_dict.get(key) is not None:
            attrs[key] = mooring_dict[key]

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
        A loaded mooring YAML, a ``*.mooring.yaml`` path, or a directory
        holding exactly one such file.
    stage : int or None, optional
        ``None`` finds and reads each raw file (from *raw_dir*) as stage 1 does —
        a missing ``filename`` found by naming convention, the Nortek header, the
        raw header's calibration metadata — and returns it unmarked, ready for
        :func:`oceanarray.pipeline.stage1.stage1`; entries marked ``skip`` are left
        out. ``1``, ``2`` or ``3`` opens the stage file (from *mooring_dir*) and
        checks its ``processing_stage`` marker. The mooring YAML's facts are
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
        mooring YAML lists them.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If a dict is given with a stage but no *mooring_dir*, or the mooring YAML
        has no ``name``.
    ValueError
        If *raw_dir* is not given for ``stage=None``.
    FileNotFoundError
        If an instrument's file for the chosen *stage* is absent and
        *allow_missing* is False.
    oceanarray.exceptions.StageError
        If an opened stage file carries the wrong ``processing_stage`` marker.
    """
    mooring_dict, mooring_dir = _resolve_description(mooring, mooring_dir, stage)
    if stage is None and raw_dir is None:
        raise ValueError("read_mooring(stage=None) needs raw_dir= to find raw files.")

    name = mooring_dict["name"]
    wanted = {paths.safe_serial(s) for s in serials} if serials is not None else None
    moor_attrs = header_attrs(mooring_dict)

    datasets: dict[str, xr.Dataset] = {}
    for entry in instrument_entries(mooring_dict):
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
                raw = raw_files.resolve_raw_file(entry, mooring_dict, raw_dir)
                if not raw.path.exists():
                    raise FileNotFoundError(
                        f"{name}: {instr_type} s/n {serial} raw file not found at "
                        f"{raw.path}."
                    )
            except (MooringError, FileNotFoundError):
                if not allow_missing:
                    raise
                log.warning(
                    "%s: %s s/n %s has no raw file; skipping (allow_missing).",
                    name,
                    instr_type,
                    serial,
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
    """Join one instrument's raw dataset with its mooring YAML, as ``read_mooring`` does.

    For a caller that has already found and read the raw file (the stage-1 class,
    which needs the file's path before reading to decide whether to skip it).

    Parameters
    ----------
    ds : xarray.Dataset
        The raw dataset, from :func:`oceanarray.pipeline.raw_files.read_raw_file`.
    entry : dict
        The instrument's mooring YAML entry.
    mooring : Mapping
        The loaded mooring YAML.

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
    return _join_description(ds, entry, serial, instr_type, header_attrs(mooring))


def _join_description(
    ds: xr.Dataset,
    entry: dict[str, Any],
    serial: str,
    instr_type: str,
    moor_attrs: dict[str, Any],
) -> xr.Dataset:
    """Join a dataset with its mooring YAML (the stamping contract)."""
    # The contract attributes must travel on the dataset whatever path it came by
    # (raw or a stage file).
    ds = ds.copy()
    existing = ds.attrs.get("serial")
    # A serial that differs from the entry's is refused: never mislabel.
    if existing is not None and paths.safe_serial(existing) != serial:
        raise MooringError(
            f"{moor_attrs['mooring_name']}: dataset serial {existing!r} contradicts "
            f"the mooring YAML's {serial!r} for this instrument — refusing to mislabel it."
        )
    name = moor_attrs["mooring_name"]
    existing_name = ds.attrs.get("mooring_name")
    # mooring_name is the identity: a case-only difference is corrected with a
    # history line; any other means the file was made under another mooring YAML.
    if existing_name is not None and existing_name != name:
        if str(existing_name).lower() != str(name).lower():
            raise MooringError(
                f"{name}: the dataset's mooring_name {existing_name!r} differs from the "
                f"mooring YAML's name {name!r} by more than case — it was made under a "
                "different mooring YAML; reprocess it from stage 1."
            )
        append_history(
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
    """Open one instrument's stage file, or refuse/skip when it is absent."""
    stage_path = paths.stage_file(mooring_dir, name, instr_type, serial, stage)
    if not stage_path.exists():
        if allow_missing:
            log.warning(
                "%s: %s s/n %s has no stage-%s file (%s); skipping (allow_missing).",
                name,
                instr_type,
                serial,
                stage,
                stage_path.name,
            )
            return None
        raise FileNotFoundError(
            f"{name}: {instr_type} s/n {serial} has no stage-{stage} file "
            f"at {stage_path}. Run stage {stage} first, or pass allow_missing=True "
            f"(CLI --allow-mixed-stages)."
        )
    ds = readers.read(stage_path)
    # A file older than the marker is stamped in memory only: reading never writes
    # to disk, so the read/write pair stays symmetric. ``stamp`` persists it.
    if _stage.STAGE_ATTR not in ds.attrs:
        log.warning(
            "%s: %s s/n %s stage-%s file has no '%s' marker (written before it "
            "existed); inferring stage %s from the filename. Run stamp to persist it.",
            name,
            instr_type,
            serial,
            stage,
            _stage.STAGE_ATTR,
            stage,
        )
        ds = _stage.stamp(ds, stage, inferred=True)
    return ds
