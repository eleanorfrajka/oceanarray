"""Nortek (Aquadopp / AquaPro) header parsers and variable normalisers.

The ``.hdr`` parsers read the coordinate system, transformation matrix and
pressure calibration from a Nortek header file; :func:`to_canonical_nortek`
tidies the dataset seasenselib produces; :func:`load_nortek_csv` reads the
AquaPro CSV export directly.
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from oceanarray.logger import log_info


def _parse_nortek_coord_system(hdr_path: Path) -> str:
    """Return the coordinate system from a Nortek .hdr file (BEAM/XYZ/ENU or UNK)."""
    # Two header formats: old text ("Coordinate system   BEAM", 2+ spaces, above the
    # "Data file format" line) and new CSV (CY="BEAM" in a GETAVG, line).
    # Neither found -> "UNK", never assumed ENU, so stage 3 refuses to treat the
    # velocities as geographic.
    try:
        with hdr_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                # Old text-format header block (ends at "Data file format")
                if "Data file format" in line:
                    break
                m = re.match(r"^\s*Coordinate system\s{2,}(\S+)", line, re.IGNORECASE)
                if m:
                    return m.group(1).strip().upper()
                # New CSV-format: GETAVG,...,CY="BEAM",...
                if line.startswith("GETAVG,"):
                    cy = re.search(r'\bCY="([^"]+)"', line)
                    if cy:
                        return cy.group(1).strip().upper()
    except Exception:  # noqa: BLE001  — intentional broad catch at I/O boundary
        pass
    return "UNK"


def _parse_nortek_T_matrix_hdr(hdr_path: Path) -> dict[str, float] | None:
    """Parse the 3×3 Nortek transformation matrix from a .hdr file."""
    # "Transformation matrix" line holds the first 3 values; two indented
    # continuation lines hold 3 each. None if fewer than 9 values are found.
    try:
        floats: list = []
        in_matrix = False
        with hdr_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if "Data file format" in line:
                    break
                stripped = line.rstrip()
                if re.search(r"Transformation matrix", stripped, re.IGNORECASE):
                    tail = re.split(r"matrix", stripped, flags=re.IGNORECASE)[-1]
                    floats.extend(
                        float(v)
                        for v in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", tail)
                    )
                    in_matrix = True
                    continue
                if in_matrix:
                    vals = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", stripped)
                    if vals:
                        floats.extend(float(v) for v in vals)
                    elif not stripped.strip() or (
                        stripped and not stripped[0].isspace()
                    ):
                        break
                if len(floats) >= 9:
                    break
        if len(floats) >= 9:
            keys = ["M11", "M12", "M13", "M21", "M22", "M23", "M31", "M32", "M33"]
            return {k: floats[i] for i, k in enumerate(keys)}
    except Exception:  # noqa: BLE001  — intentional broad catch at I/O boundary
        pass
    return None


def _parse_nortek_T_matrix_csv(csv_path: Path) -> dict[str, float] | None:
    """Parse the 3×3 Nortek transformation matrix from a String Data.csv file."""
    # Looks for GETXFAVG or GETXFBURST with ROWS=3,COLS=3,M11=...,M33=... in the
    # pipe-separated field format; None unless all nine entries are found.
    try:
        with csv_path.open(encoding="utf-8", errors="replace") as f:
            content = f.read()
        for prefix in ("GETXFAVG", "GETXFBURST"):
            m = re.search(rf"{prefix},ROWS=3,COLS=3,([^|\n]+)", content, re.IGNORECASE)
            if not m:
                continue
            params_str = m.group(1)
            result: dict[str, float] = {}
            for i in range(1, 4):
                for j in range(1, 4):
                    key = f"M{i}{j}"
                    km = re.search(
                        key + r"=([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)",
                        params_str,
                        re.IGNORECASE,
                    )
                    if km:
                        result[key] = float(km.group(1))
            if len(result) == 9:
                return result
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _parse_nortek_pressure_cal(hdr_path: Path) -> dict:
    """Return pressure sensor calibration key-value pairs from a Nortek .hdr file."""
    # The block starts at a "Pressure ... calibration" line and ends at the next blank
    # line; returns {} if not found.
    cal: dict = {}
    in_section = False
    try:
        with hdr_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if "Data file format" in line:
                    break
                stripped = line.rstrip()
                if re.search(r"pressure.{0,10}calibration", stripped, re.IGNORECASE):
                    in_section = True
                    continue
                if not in_section:
                    continue
                if stripped == "":
                    break  # blank line ends the section
                # Key-value lines have ≥2 spaces between key and value
                m = re.match(r"^\s*(.+?)\s{2,}(.+?)\s*$", stripped)
                if m:
                    key = re.sub(r"\W+", "_", m.group(1).strip().lower()).strip("_")
                    cal[key] = m.group(2).strip()
    except (OSError, AttributeError):
        pass
    return cal


def to_canonical_nortek(dataset: xr.Dataset, coord_system: str = "ENU") -> xr.Dataset:
    """Normalise variable names produced by the seasenselib nortek readers.

    The Nortek .hdr file describes columns for both the main .dat file and the
    diagnostics .dia file in the same "Data file format" block.  seasenselib's
    header reader captures all of them, causing the pipeline's smart-numbering
    logic to produce phantom _1 / _2 / … duplicates for every variable (the _1
    columns are always NaN because the .dat file only has the plain columns).

    This method:
      1. Rescues pressure_1 → pressure and pressure_2 → depth (Nortek has two
         pressure columns: dbar and m).
      2. Drops ALL _{n} phantom duplicates of known physics variables.
      3. Renames velocity variables to reflect the instrument's coordinate system:
           BEAM → velocity_beam1 / velocity_beam2 / velocity_beam3
           XYZ  → x_velocity    / y_velocity    / z_velocity
           ENU  → east_velocity / north_velocity / up_velocity  (no rename)
      4. Stores ``coordinate_system`` as a dataset attribute.
    """
    # ── 1. Pressure: first occurrence is dbar, second is metres ──────────
    if "pressure_1" in dataset.data_vars and "pressure" not in dataset.data_vars:
        dataset = dataset.rename_vars({"pressure_1": "pressure"})

    if "pressure_2" in dataset.data_vars:
        p2 = dataset["pressure_2"]
        if np.all(np.isnan(p2.values)):
            dataset = dataset.drop_vars("pressure_2")
        else:
            attrs = dict(p2.attrs)
            attrs.update({"units": "m", "long_name": "Depth"})
            dataset = dataset.drop_vars("pressure_2")
            dataset["depth"] = p2.assign_attrs(attrs)

    # ── 2. Drop all _{n} phantom duplicates ───────────────────────────────
    # seasenselib reads both .dat and .dia column definitions from the .hdr,
    # producing phantom _1 duplicates for every variable (all NaN because
    # the .dat file only has the plain columns).  Match on any {base}_{digits}
    # suffix — drop if the plain base exists OR if the column is entirely NaN.
    _suffix_re = re.compile(r"^(.+)_\d+$")
    _to_drop = []
    for _vname in list(dataset.data_vars):
        _m = _suffix_re.match(_vname)
        if not _m:
            continue
        _base = _m.group(1)
        if _base in dataset.data_vars or (
            dataset[_vname].dtype.kind == "f"
            and dataset[_vname].size > 0
            and bool(np.all(np.isnan(dataset[_vname].values)))
        ):
            _to_drop.append(_vname)
    if _to_drop:
        dataset = dataset.drop_vars(_to_drop)

    # Drop redundant datetime component columns — the time coordinate suffices.
    _dt_cols = ["Year", "Month", "Day", "Hour", "Minute", "Second"]
    _dt_drop = [c for c in _dt_cols if c in dataset.data_vars]
    if _dt_drop:
        dataset = dataset.drop_vars(_dt_drop)

    # ── 3. Velocity renaming based on coordinate system ───────────────────
    cs = coord_system.strip().upper()
    dataset.attrs["coordinate_system"] = cs

    _vel_src = ["east_velocity", "north_velocity", "up_velocity"]
    if cs == "BEAM":
        _vel_dst = ["velocity_beam1", "velocity_beam2", "velocity_beam3"]
    elif cs == "XYZ":
        _vel_dst = ["x_velocity", "y_velocity", "z_velocity"]
    elif cs == "UNK":
        _vel_dst = _vel_src  # leave as-is; coordinate_system=UNK signals unknown
    else:  # ENU — names are already correct
        _vel_dst = _vel_src

    if _vel_src != _vel_dst:
        rename_map = {
            src: dst
            for src, dst in zip(_vel_src, _vel_dst, strict=False)
            if src in dataset.data_vars
        }
        if rename_map:
            dataset = dataset.rename_vars(rename_map)

    # ── 4. Amplitude renaming — always beam-based, not ENU ───────────────
    _amp_rename = {
        s: d
        for s, d in [
            ("east_amplitude", "amplitude_beam1"),
            ("north_amplitude", "amplitude_beam2"),
            ("up_amplitude", "amplitude_beam3"),
        ]
        if s in dataset.data_vars
    }
    if _amp_rename:
        dataset = dataset.rename_vars(_amp_rename)
        for _aname in _amp_rename.values():
            if _aname in dataset.data_vars:
                dataset[_aname].attrs.setdefault("units", "counts")
                dataset[_aname].attrs.setdefault(
                    "long_name", f"Acoustic signal amplitude {_aname[-1]}"
                )

    # ── 5. Normalize remaining Nortek column names to snake_case ─────────
    # seasenselib normalises some names (battery_voltage, speed_of_sound, …)
    # but leaves others verbatim from the .hdr column headers.
    _NORTEK_RENAME = {
        "Heading": "heading",
        "Pitch": "pitch",
        "Roll": "roll",
        "Direction": "direction",
        "Speed": "speed",
        "Error code": "error_code",
        "Status code": "status_code",
        # "Analog input 1" and "Analog input 2" come through as analog_input_1/2
        # from seasenselib already — no rename needed here.
        # seasenselib maps this to speed_of_sound but also keeps the original
        "Soundspeed used": "speed_of_sound",
    }
    _norm_rename: dict = {}
    _norm_drop: list = []
    for _old, _new in _NORTEK_RENAME.items():
        if _old not in dataset.data_vars:
            continue
        if _new in dataset.data_vars:
            _norm_drop.append(_old)  # canonical already exists; drop the duplicate
        else:
            _norm_rename[_old] = _new
    if _norm_rename:
        dataset = dataset.rename_vars(_norm_rename)
    if _norm_drop:
        dataset = dataset.drop_vars(_norm_drop)

    # ── 6. CSV format: demote constant time-series to attrs; drop scaffolding ──
    # The nortek-csv-oa reader stores deployment-config columns as full time series
    # even though they never change.  Promote them to global attrs and drop.
    _CSV_TO_ATTR = {
        "coordinatesystem": "coordinate_system",
        "blanking": "nortek_blanking_m",
        "cellsize": "nortek_cellsize_m",
        "nbeams": "nortek_nbeams",
        "ncells": "nortek_ncells",
    }
    for _cv, _ca in _CSV_TO_ATTR.items():
        if _cv not in dataset.data_vars:
            continue
        _arr = dataset[_cv].values
        if _arr.dtype.kind in ("U", "S", "O"):
            _unique = list({str(x) for x in _arr.flat})
            if len(_unique) == 1:
                dataset.attrs.setdefault(_ca, _unique[0])
                dataset = dataset.drop_vars(_cv)
        else:
            try:
                _farr = _arr.astype(float)
                _uv = np.unique(_farr[np.isfinite(_farr)])
                if len(_uv) == 1:
                    dataset.attrs.setdefault(_ca, float(_uv[0]))
                    dataset = dataset.drop_vars(_cv)
            except (ValueError, TypeError):
                pass

    # Drop pure scaffolding columns with no scientific value
    _CSV_DROP = [
        "serialnumber",
        "statuspreviouswakeupstate",
        "idx",
        "ensemblecounter",
    ]
    _csv_drop = [v for v in _CSV_DROP if v in dataset.data_vars]
    if _csv_drop:
        dataset = dataset.drop_vars(_csv_drop)

    # ── 7. Consolidate per-beam amplitude and correlation into (time, beam) ─
    # Combine amplitude_beam1/2/3 → amplitude(time, beam) and
    # correlation_beam1/2/3 → correlation(time, beam) to match the 3-D
    # layout used by the RDI ADCP reader.  Only applied when all three beam
    # variables are present and 1-D (shape guard against future 2-D readers).
    for _basename, _units, _long_name in [
        ("amplitude", "counts", "Acoustic signal amplitude"),
        ("correlation", "%", "Acoustic signal correlation"),
    ]:
        _bvars = [f"{_basename}_beam{i}" for i in range(1, 4)]
        if not all(v in dataset.data_vars for v in _bvars):
            continue
        if any(dataset[v].ndim != 1 for v in _bvars):
            continue
        _tdim = dataset[_bvars[0]].dims[0]
        _arr = np.stack([dataset[v].values for v in _bvars], axis=-1).astype(np.float32)
        dataset[_basename] = xr.DataArray(
            _arr,
            dims=(_tdim, "beam"),
            coords={"beam": np.array([1, 2, 3], dtype=np.int8)},
            attrs={"units": _units, "long_name": _long_name},
        )
        dataset = dataset.drop_vars(_bvars)

    return dataset


def _canonical_nortek_var_name(col: str) -> str:
    """Convert a raw Nortek CSV column name to a valid NetCDF variable name."""
    name = col.lower().strip()
    name = re.sub(r"[^\w\s]", " ", name)  # special chars → spaces
    name = re.sub(r"\s+", "_", name.strip())
    return re.sub(r"_+", "_", name)


def _parse_nortek_csv_columns(df: pd.DataFrame) -> dict:
    """Build a variable dict from all columns in a Nortek CSV DataFrame."""
    # AquaPro exports mix camelCase (velBeam1#1, speedOfSound) for the velocity block
    # and Title Case (Pressure, Soundspeed) for the environmental block, so canonical
    # matching is case-insensitive. Unmapped columns are kept under cleaned names so
    # no raw data is silently discarded.
    # Build case-insensitive lookup: lower_name -> first df column name
    col_map: dict[str, str] = {}
    for col in df.columns:
        key = col.lower().strip()
        if key not in col_map:
            col_map[key] = col  # first occurrence wins

    # Canonical name mappings — covers both camelCase and Title Case spellings
    canonical: dict[str, str] = {}  # df_col_name -> var_name

    scalar_mappings = [
        (["temperature"], "temperature"),
        (["pressure"], "pressure"),  # first = dbar
        (["heading"], "heading"),
        (["pitch"], "pitch"),
        (["roll"], "roll"),
        (["soundspeed", "speedofsound"], "speed_of_sound"),
        (["battery voltage", "batteryvoltage"], "battery_voltage"),
        (["speed"], "speed"),
    ]
    for csv_names, var_name in scalar_mappings:
        for csv_name in csv_names:
            if csv_name in col_map:
                canonical[col_map[csv_name]] = var_name
                break

    # AquaPro camelCase beam variables
    for i in [1, 2, 3]:
        for data_type, prefix in [
            ("vel", "velocity"),
            ("amp", "amplitude"),
            ("corr", "correlation"),
        ]:
            df_col = f"{data_type}Beam{i}#1"
            if df_col in df.columns:
                canonical[df_col] = f"{prefix}_beam{i}"

    # Columns to drop entirely; the time coordinate already carries the components
    time_components = {"year", "month", "day", "hour", "minute", "second"}
    # The second (and any further) occurrence of "pressure" is depth in meters,
    # derived from pressure and not needed: the first (dbar) is canonical
    first_pressure_col = col_map.get("pressure")
    duplicate_pressure = {
        col
        for col in df.columns
        if col.lower().strip() == "pressure" and col != first_pressure_col
    }
    drop_cols = time_components | duplicate_pressure | {"datetime"}

    # Build data_vars: canonical names first, then everything else
    data_vars: dict = {}
    assigned_df_cols: set = set(canonical.keys())

    for df_col, var_name in canonical.items():
        data_vars[var_name] = (["time"], df[df_col].values)

    for df_col in df.columns:
        if df_col in assigned_df_cols:
            continue
        if df_col.lower().strip() in drop_cols or df_col in drop_cols:
            continue
        var_name = _canonical_nortek_var_name(df_col)
        if var_name and var_name not in data_vars:
            data_vars[var_name] = (["time"], df[df_col].values)

    return data_vars


def _add_nortek_csv_attributes(ds: xr.Dataset) -> xr.Dataset:
    """Add units and metadata attributes to variables in a Nortek CSV dataset."""
    attr_map = {
        "temperature": {"units": "degrees_C", "long_name": "Water Temperature"},
        "pressure": {"units": "dbar", "long_name": "Pressure"},
        "heading": {"units": "degrees", "long_name": "Heading"},
        "pitch": {"units": "degrees", "long_name": "Pitch"},
        "roll": {"units": "degrees", "long_name": "Roll"},
        "speed_of_sound": {"units": "m/s", "long_name": "Speed of Sound"},
        "battery_voltage": {"units": "V", "long_name": "Battery Voltage"},
    }
    for var_name, attrs in attr_map.items():
        if var_name in ds.data_vars:
            ds[var_name].attrs.update(attrs)

    for i in [1, 2, 3]:
        if f"velocity_beam{i}" in ds.data_vars:
            ds[f"velocity_beam{i}"].attrs.update(
                {
                    "units": "m/s",
                    "long_name": f"Velocity Beam {i}",
                    "coordinate_system": "BEAM",
                }
            )
        if f"amplitude_beam{i}" in ds.data_vars:
            ds[f"amplitude_beam{i}"].attrs.update(
                {"units": "counts", "long_name": f"Amplitude Beam {i}"}
            )
        if f"correlation_beam{i}" in ds.data_vars:
            ds[f"correlation_beam{i}"].attrs.update(
                {"units": "%", "long_name": f"Correlation Beam {i}"}
            )

    return ds


def load_nortek_csv(
    file_path: str | Path,
    header_file: str | None = None,  # noqa: ARG001
) -> xr.Dataset:
    """Load Nortek CSV data exported from AquaPro software.

    Parameters
    ----------
    file_path : str or Path
        Path to the semicolon-delimited CSV data file (e.g. "Average Velocity DF3.csv").
    header_file : str, optional
        Path to the accompanying Units.csv file (reserved for future metadata use).

    Returns
    -------
    xr.Dataset
        Dataset with time coordinate and variables for velocity beams,
        amplitude, correlation, and environmental channels.

    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Nortek CSV file not found: {file_path}")

    df = pd.read_csv(file_path, delimiter=";")
    df["datetime"] = pd.to_datetime(df["dateTime"])
    times = df["datetime"].values

    data_vars = _parse_nortek_csv_columns(df)
    ds = xr.Dataset(data_vars, coords={"time": times})

    ds.attrs.update(
        {
            "instrument_type": "Nortek_Aquadopp",
            "filename": str(file_path),
            "data_format": "Nortek_CSV_Export",
            "coordinate_system": "BEAM",
        }
    )

    if "serialNumber" in df.columns:
        ds.attrs["serial_number"] = str(df["serialNumber"].iloc[0])

    ds = _add_nortek_csv_attributes(ds)

    log_info("Nortek CSV: loaded %d samples from %s", len(times), file_path.name)
    return ds
