"""Find and read one instrument's raw file, as stage 1 does.

A mooring description names each instrument's raw file (``filename``, ``file_type``,
``header_file``); when it does not, the file is found from the instrument type and
serial by the recovery-file naming conventions. The raw read also takes what only the
raw file holds: the calibration coefficients in an SBE header and the Nortek
pressure calibration and transformation matrix. Everything after the read is stage 1's
transform (:func:`oceanarray.pipeline.stage1.stage1`).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import xarray as xr

from oceanarray import paths, readers
from oceanarray.exceptions import MooringError
from oceanarray.logger import log_message
from oceanarray.paths import safe_serial
from oceanarray.readers import sbe
from oceanarray.readers.nortek import (
    _parse_nortek_pressure_cal,
    _parse_nortek_T_matrix_csv,
    _parse_nortek_T_matrix_hdr,
)

_NORTEK_TYPES = ("nortek-raw", "nortek-ascii", "nortek-csv")


@dataclass(frozen=True)
class RawFile:
    """One instrument's raw file, as found from its description entry.

    Attributes
    ----------
    entry : dict
        The description entry, with ``filename``, ``file_type`` and ``header_file``
        filled in when they were found by naming convention.
    path : pathlib.Path
        The raw data file.
    header : pathlib.Path or None
        The Nortek header file, when the instrument has one.
    file_type : str
        The reader format.
    instrument_dir : str
        The instrument subdirectory (``"aquadopp"`` for the deprecated ``"nortek"``).
    tag : str
        The ``adcp-matlab`` per-file tag carried into the output filename, else ``""``.
    """

    entry: dict
    path: Path
    header: Path | None
    file_type: str
    instrument_dir: str
    tag: str


def find_file_tag(
    filename: str, tags: tuple[str, ...] = ("_000", "_001", "_002")
) -> str:
    """Return the first of *tags* found in *filename*, or ``""``."""
    filename = str(filename)
    for tag in tags:
        if tag in filename:
            return tag
    return ""


def guess_instrument_filename(
    instrument_config: dict[str, Any],
    mooring_name: str,
    raw_mooring_dir: Path | None,
    input_dir: Path | None,
) -> tuple | None:
    """Try standard recovery-file naming conventions to locate a raw data file.

    Used when ``filename`` is absent or ``null`` in the mooring YAML.
    Searches ``{raw_mooring_dir}/{instrument}/`` then ``{raw_mooring_dir}/``.

    Returns ``(filename, file_type, header_filename_or_None)`` when a
    candidate is found on disk, or ``None`` if no match is found (in which
    case a diagnostic message listing the paths tried is emitted via
    the package logger).

    Naming conventions applied (all use the ``{serial}_recovery.<ext>``
    pattern except ADCP):

    * ``microcat`` serial < 6000  → ``.asc`` / ``sbe-ascii`` (older SBE 37)
    * ``microcat`` serial ≥ 6000  → ``.hex`` / ``sbe-hex`` (newer SBE 37)
    * ``rbrsolo`` / ``rbrduet`` / ``seapoint`` → ``.rsk`` / ``rbr-rsk``
    * ``aquadopp`` / ``nortek`` serial < 400000 → ``.dat`` / ``nortek-ascii``
      + ``.hdr`` header file.  ``nortek`` is accepted but deprecated; a
      warning is logged recommending ``aquadopp`` instead.
    * ``aquadopp`` / ``nortek`` serial ≥ 400000 → globs for
      ``converted_raw/A{serial}*/Average Velocity DF3.csv`` under the
      instrument directory, returns ``nortek-csv`` with the corresponding
      ``String Data.csv`` as the header path.
    * ``ADCP`` → tries ``{serial}_{mooring_name}_recovery.000`` then
      ``{serial}_{mooring_prefix}_recovery.000`` / ``rdi-raw``

    A trailing ``*`` or other non-alphanumeric annotation in the serial
    field is stripped before building the candidate filename.
    """
    instr_type = str(instrument_config.get("instrument", "")).lower()
    raw_serial = str(instrument_config.get("serial", "")).split(",")[0].strip()
    if not raw_serial or raw_serial == "unknown":
        return None
    # Strip illegal filename chars (e.g. '*' used as a YAML annotation marker)

    raw_serial = safe_serial(raw_serial)

    candidates: list = []  # list of (filename, file_type, header_or_None)

    if instr_type == "microcat":
        serial_int = int(raw_serial) if raw_serial.isdigit() else None
        if serial_int is not None and serial_int < 6000:
            candidates.append((f"{raw_serial}_recovery.asc", "sbe-ascii", None))
        else:
            candidates.append((f"{raw_serial}_recovery.hex", "sbe-hex", None))

    elif instr_type in ("rbrsolo", "rbrduet", "seapoint"):
        candidates.append((f"{raw_serial}_recovery.rsk", "rbr-rsk", None))

    elif instr_type in ("aquadopp", "nortek"):
        if instr_type == "nortek":
            log_message(
                f"AUTO-FILENAME: instrument type 'nortek' is deprecated — "
                f"use 'aquadopp' in the YAML for serial {raw_serial}"
            )
        serial_int = int(raw_serial) if raw_serial.isdigit() else None
        if serial_int is not None and serial_int >= 400000:
            # 400### series: data lives under converted_raw/A{serial}*/
            # The full internal serial suffix varies, so we use glob.
            _search_400: list = []
            if raw_mooring_dir is not None:
                _search_400 += [raw_mooring_dir / "aquadopp", raw_mooring_dir]
            if input_dir is not None:
                _search_400 += [
                    input_dir / "aquadopp" / mooring_name,
                    input_dir,
                ]
            _tried_400: list = []
            for _root in _search_400:
                _conv = _root / "converted_raw"
                _tried_400.append(
                    str(_conv / f"A{raw_serial}*" / "Average Velocity DF3.csv")
                )
                if not _conv.is_dir():
                    continue
                for _sub in sorted(_conv.glob(f"A{raw_serial}*")):
                    if not _sub.is_dir():
                        continue
                    for _csv in ("Average Velocity DF3.csv", "String Data.csv"):
                        _csv_path = _sub / _csv
                        if _csv_path.exists():
                            _hdr = _sub / "String Data.csv"
                            _hdr_rel = (
                                str(_hdr.relative_to(_root)) if _hdr.exists() else None
                            )
                            return (
                                str(_csv_path.relative_to(_root)),
                                "nortek-csv",
                                _hdr_rel,
                            )
            # Also try the .aqd binary directly (nortek-raw format).
            # These may live in aquadopp/ OR legacy nortek/ subdirs.
            _aqd_roots = [
                *[r / "aquadopp" for r in [raw_mooring_dir] if r is not None],
                *[r / "nortek" for r in [raw_mooring_dir] if r is not None],
                *([raw_mooring_dir] if raw_mooring_dir is not None else []),
            ]
            for _root in _aqd_roots:
                if _root is None or not _root.is_dir():
                    continue
                for _aqd in sorted(_root.glob(f"A{raw_serial}*.aqd")):
                    _hdr = _aqd.with_suffix(".hdr")
                    return (
                        _aqd.name,
                        "nortek-raw",
                        _hdr.name if _hdr.exists() else None,
                    )
                _tried_400.append(str(_root / f"A{raw_serial}*.aqd"))
            log_message(
                f"AUTO-FILENAME: no converted_raw/A{raw_serial}*/ or "
                f"A{raw_serial}*.aqd found for "
                f"aquadopp {raw_serial} — tried:\n  " + "\n  ".join(_tried_400)
            )
            return None
        candidates.append(
            (
                f"{raw_serial}_recovery.dat",
                "nortek-ascii",
                f"{raw_serial}_recovery.hdr",
            )
        )

    elif instr_type == "tr1050":
        candidates.append((f"{raw_serial}_recovery.hex", "rbr-hex", None))

    elif instr_type == "adcp":
        # Try full mooring name first, then the mooring prefix (before first '_')
        # since either naming convention may be in use.
        candidates.append(
            (f"{raw_serial}_{mooring_name}_recovery.000", "rdi-raw", None)
        )
        _pfx = mooring_name.split("_")[0]
        if _pfx != mooring_name:
            candidates.append((f"{raw_serial}_{_pfx}_recovery.000", "rdi-raw", None))

    if not candidates:
        log_message(
            f"AUTO-FILENAME: instrument type '{instr_type}' not recognised "
            f"— add 'filename:' to YAML for {instr_type} {raw_serial}"
        )
        return None

    # Search directories: new layout (raw_mooring_dir/instrument/), then
    # mooring subdir (raw_mooring_dir/), then legacy input_dir.
    search_roots: list = []
    if raw_mooring_dir is not None:
        search_roots.append(raw_mooring_dir / instr_type)
        search_roots.append(raw_mooring_dir)
    if input_dir is not None:
        search_roots.append(input_dir / instr_type / mooring_name)
        search_roots.append(input_dir)

    tried: list = []
    for fname, ftype, hdr in candidates:
        for root in search_roots:
            candidate_path = root / fname
            tried.append(str(candidate_path))
            if candidate_path.exists():
                return fname, ftype, hdr

    log_message(
        f"AUTO-FILENAME: no file found for {instr_type} {raw_serial} — tried:\n  "
        + "\n  ".join(tried)
    )
    return None


def resolve_raw_file(
    entry: dict[str, Any],
    mooring: dict[str, Any],
    raw_dir: str | Path | None,
    *,
    folder: str | None = None,
) -> RawFile:
    """Find one instrument's raw file (and Nortek header) from its description entry.

    The file is ``<raw_dir>/<name>/<instrument>/<filename>``. A missing ``filename``
    is found by naming convention (:func:`guess_instrument_filename`). An Aquadopp
    file not in ``aquadopp/`` is looked for in the legacy ``nortek/`` folder. An
    absolute ``directory:`` in the description (deprecated) replaces the raw root,
    with the old ``<directory>/<instrument>/<name>/<filename>`` layout.

    Parameters
    ----------
    entry : dict
        The instrument's description entry.
    mooring : dict
        The loaded mooring description.
    raw_dir : str or pathlib.Path or None
        The cruise-level raw-data root.
    folder : str or None, optional
        The mooring's folder name under *raw_dir* (and under an absolute
        ``directory:``); defaults to the description's ``name``.

    Returns
    -------
    RawFile
        The resolved file.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If the entry names no file and none is found by convention.
    """
    name = folder or mooring["name"]
    override = mooring.get("directory")
    if override and Path(override).is_absolute():
        input_dir: Path | None = Path(override)
        raw_mooring_dir: Path | None = None
    else:
        input_dir = None
        raw_mooring_dir = paths.raw_mooring_dir(raw_dir, name)

    entry = dict(entry)
    instrument = entry.get("instrument", "unknown")
    serial = entry.get("serial", "unknown")
    if not entry.get("filename"):
        guess = guess_instrument_filename(entry, name, raw_mooring_dir, input_dir)
        if guess is None:
            raise MooringError(
                f"{name}: {instrument} s/n {serial} has no 'filename' and no file "
                "matching the recovery-file naming convention was found."
            )
        found, found_type, found_header = guess
        log_message(f"AUTO-FILENAME: {instrument} {serial} → {found} ({found_type})")
        entry["filename"] = found
        if found_type and not entry.get("file_type"):
            entry["file_type"] = found_type
        if found_header and not entry.get("header_file"):
            entry["header_file"] = found_header

    filename = entry["filename"]
    file_type = entry.get("file_type", "")
    # 'nortek' as an instrument directory name is deprecated — always use 'aquadopp'.
    instrument_dir = instrument
    if instrument == "nortek":
        log_message(
            f"WARNING: instrument='nortek' is deprecated (serial {serial}); "
            f"update YAML to instrument='aquadopp'. Using 'aquadopp/' directory."
        )
        instrument_dir = "aquadopp"

    if raw_mooring_dir is not None:
        path = raw_mooring_dir / instrument_dir / filename
        if not path.exists() and instrument_dir == "aquadopp":
            legacy = raw_mooring_dir / "nortek" / filename
            if legacy.exists():
                log_message(
                    f"INFO: {filename} found in legacy 'nortek/' subdir "
                    f"(serial {serial}); consider renaming to 'aquadopp/'."
                )
                path = legacy
    else:
        path = input_dir / instrument_dir / name / filename

    header = None
    header_key = entry.get("header_file") or entry.get("header")
    if file_type in _NORTEK_TYPES and header_key:
        if raw_mooring_dir is not None:
            header = raw_mooring_dir / instrument_dir / header_key
            if not header.exists():
                legacy_header = raw_mooring_dir / "nortek" / header_key
                if legacy_header.exists():
                    header = legacy_header
        else:
            header = input_dir / instrument_dir / name / header_key

    tag = find_file_tag(filename) if file_type == "adcp-matlab" else ""
    return RawFile(entry, path, header, file_type, instrument_dir, tag)


def read_raw_file(raw: RawFile) -> xr.Dataset:
    """Read one raw file and the metadata only its header holds.

    Adds the Nortek pressure calibration and transformation matrix as
    ``nortek_pressure_cal_*`` / ``nortek_T_*`` attributes (and
    ``nortek_pointing_down``), and the SBE calibration coefficients as
    ``SENSOR_*`` variables, and records ``source_file`` and ``file_type``.

    Parameters
    ----------
    raw : RawFile
        The resolved raw file.

    Returns
    -------
    xarray.Dataset
        The raw dataset, before stage 1's transforms.
    """
    read_path = raw.path
    if raw.file_type == "sbe-ascii":
        read_path = sbe.repair_sbe_ascii(raw.path)
    header = str(raw.header) if raw.header is not None else None
    dataset = readers._read_typed(raw.file_type, str(read_path), header)

    if raw.file_type in ("nortek-raw", "nortek-ascii") and header:
        for key, value in _parse_nortek_pressure_cal(Path(header)).items():
            dataset.attrs[f"nortek_pressure_cal_{key}"] = value
    if raw.file_type in _NORTEK_TYPES and header:
        t_matrix = _parse_nortek_T_matrix_hdr(Path(header))
        if t_matrix is None:
            t_matrix = _parse_nortek_T_matrix_csv(Path(header))
        for key, value in (t_matrix or {}).items():
            dataset.attrs[f"nortek_T_{key}"] = value
        dataset.attrs["nortek_pointing_down"] = str(
            bool(raw.entry.get("pointing_down", False))
        )

    if raw.file_type == "sbe-ascii":
        dataset = _add_sbe_ascii_sensor_vars(dataset, raw.path, raw.entry)
    elif raw.file_type == "sbe-hex":
        dataset = _add_sbe_hex_sensor_vars(dataset, raw.path, raw.entry)

    dataset.attrs["source_file"] = raw.entry["filename"]
    if raw.file_type:
        dataset.attrs["file_type"] = raw.file_type
    return dataset


#: The SBE sensors stage 1 records, as (variable suffix, sensor type, sensor model).
_SBE_SENSORS = {
    "temperature": ("TEMP", "TEMPERATURE", "Sea-Bird SBE temperature sensor"),
    "conductivity": ("CNDC", "CONDUCTIVITY", "Sea-Bird SBE conductivity sensor"),
    "pressure": ("PRES", "PRESSURE", "Sea-Bird pressure sensor"),
}


def _format_date_iso(raw: str, formats: tuple[str, ...]) -> str:
    """Return *raw* as ``YYYY-MM-DD`` when it parses with one of *formats*, else unchanged."""
    import datetime

    for fmt in formats:
        try:
            return datetime.datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return raw


def _add_sensor_var(
    dataset: xr.Dataset,
    sensor: str,
    instr_serial: str,
    sensor_serial: str,
    cal_date: str,
    coefficients: str,
) -> None:
    """Add one ``SENSOR_<TEMP|CNDC|PRES>_<serial>`` scalar variable carrying the calibration."""
    import numpy as np

    suffix, sensor_type, model = _SBE_SENSORS[sensor]
    dataset[f"SENSOR_{suffix}_{instr_serial}"] = xr.Variable(
        (),
        np.array(b"", dtype="|S1"),
        attrs={
            "long_name": f"{model} metadata",
            "sensor_type": sensor_type,
            "sensor_serial_number": sensor_serial,
            "sensor_model": model,
            "sensor_calibration_date": cal_date,
            f"{sensor_type}_calibration_coefficients": coefficients,
            "cf_role": "sensor_id",
            "coverage_content_type": "auxiliaryInformation",
        },
    )


def _add_sbe_ascii_sensor_vars(
    dataset: xr.Dataset, file_path: Path, instrument_config: dict[str, Any]
) -> xr.Dataset:
    """Parse calibration coefficients from an SBE ASCII header and add SENSOR_* vars."""
    # seasenselib's sbe-ascii reader discards the "* TA0 = ..." header lines, so
    # re-read the raw file to populate the report calibration table.
    try:
        text = file_path.read_text(errors="ignore")
    except OSError:
        return dataset

    instr_serial = safe_serial(instrument_config.get("serial", ""))
    # The start of a new calibration section or an end-of-header marker.
    section_start = re.compile(
        r"^(temperature|conductivity|pressure|rtc|S>|\*END\*)", re.IGNORECASE
    )

    def _block(start_pat: str) -> str:
        """Return ``KEY=VAL, …`` from a labelled block of ``*     KEY = VAL`` lines."""
        coeffs: dict[str, str] = {}
        in_block = False
        for line in text.splitlines():
            stripped = line.lstrip("* ").strip()
            if re.match(start_pat, stripped, re.IGNORECASE):
                in_block = True
                continue
            if in_block:
                m = re.match(r"([A-Za-z_]\w*)\s*=\s*(.+)", stripped)
                if m:
                    coeffs[m.group(1)] = m.group(2).strip()
                elif stripped and section_start.match(stripped):
                    break
        return ", ".join(f"{k}={v}" for k, v in coeffs.items())

    def _date(pat: str) -> str:
        m = re.search(pat, text, re.IGNORECASE)
        return _format_date_iso(m.group(1).strip(), ("%d-%b-%y",)) if m else "—"

    # Pressure header line: "* pressure S/N 2385215, range = 2900 psia: 10-nov-08".
    sn_match = re.search(r"pressure\s+S/N\s+(\d+)", text, re.IGNORECASE)
    blocks = {
        "temperature": (r"temperature:", r"\* temperature:\s+(.+)", instr_serial),
        "conductivity": (r"conductivity:", r"\* conductivity:\s+(.+)", instr_serial),
        "pressure": (
            r"pressure\s+S/N",
            r"\* pressure\s+S/N[^:]+:\s+(.+)",
            sn_match.group(1) if sn_match else instr_serial,
        ),
    }
    for sensor, (start_pat, date_pat, sensor_serial) in blocks.items():
        coefficients = _block(start_pat)
        if coefficients:
            _add_sensor_var(
                dataset,
                sensor,
                instr_serial,
                sensor_serial,
                _date(date_pat),
                coefficients,
            )
    return dataset


def _add_sbe_hex_sensor_vars(
    dataset: xr.Dataset, file_path: Path, instrument_config: dict[str, Any]
) -> xr.Dataset:
    """Parse calibration coefficients from an SBE hex header and add SENSOR_* vars."""
    try:
        from seasenselib.readers.sbe_hex_reader import parse_hex_header_sensors

        sensor_info = parse_hex_header_sensors(file_path)
    except Exception:  # noqa: BLE001  — optional; don't break stage1 if unavailable
        return dataset

    cal_coeffs = sensor_info.get("calibration_coefficients", {})
    if not cal_coeffs:
        return dataset

    instr_serial = safe_serial(instrument_config.get("serial", ""))
    for sensor in _SBE_SENSORS:
        if sensor not in cal_coeffs:
            continue
        coeffs = cal_coeffs[sensor]["coefficients"]
        _add_sensor_var(
            dataset,
            sensor,
            instr_serial,
            coeffs.get("serialnum", instr_serial),
            _format_date_iso(coeffs.get("caldate", "—"), ("%d-%b-%y", "%d-%b-%Y")),
            ", ".join(
                f"{k}={v}"
                for k, v in coeffs.items()
                if k not in ("serialnum", "caldate")
            ),
        )
    return dataset
