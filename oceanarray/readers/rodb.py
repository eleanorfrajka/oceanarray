"""Reader for RODB text files (``.microcat``, ``.edt``, ``.lowedt``).

These are originator-processed RODB-format time series (RAPID/OSNAP).  Column
codes are mapped to oceanarray names through :data:`RODB_COLUMNS`.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

#: RODB column codes → (oceanarray name, units).  Units are those documented for
#: each code in ``config/legacy/rodb_keys.yaml`` (RODB files carry no units).
RODB_COLUMNS: dict[str, tuple[str, str]] = {
    "T": ("temperature", "degree_Celsius"),
    "C": ("conductivity", "mS/cm"),
    "P": ("pressure", "dbar"),
    "U": ("east_velocity", "cm s-1"),
    "V": ("north_velocity", "cm s-1"),
    "W": ("up_velocity", "cm s-1"),
    "HDG": ("heading", "degree"),
    "PIT": ("pitch", "degree"),
    "ROL": ("roll", "degree"),
    "IPOW": ("input_power", "V"),
    "USS": ("signal_strength_east", "counts"),
    "VSS": ("signal_strength_north", "counts"),
    "WSS": ("signal_strength_up", "counts"),
}

#: RODB missing-data markers.  Only these: a value such as ``-9.0`` is a real
#: velocity or roll in an ``.edt`` file and must not be blanked.
RODB_FILL_VALUES: tuple[float, ...] = (-9999.0, 1e32)

#: RODB file extensions: ``.microcat`` (calibrated), ``.edt`` (magnetic deviation
#: and sound speed corrected, despiked), ``.lowedt`` (``.edt`` low-passed).
RODB_EXTENSIONS: frozenset[str] = frozenset({".microcat", ".edt", ".lowedt"})


def _rodb_position(text: str) -> float:
    """Return decimal degrees from a RODB ``"57 28.240 N"`` position string."""
    deg, minutes, hemi = text.split()
    value = float(deg) + float(minutes) / 60.0
    return -value if hemi.upper() in ("S", "W") else value


def read_rodb(path: str | Path) -> xr.Dataset:
    """Read a RODB text file (``.microcat``, ``.edt``, ``.lowedt``).

    The header (``Key = value`` lines) is kept as ``originator_*`` attributes; the
    ``Columns`` line names the data columns.  ``YY MM DD HH`` (decimal hours) build
    ``time``, rounded to the nearest second.  Other columns are renamed through
    :data:`RODB_COLUMNS` with their documented units; :data:`RODB_FILL_VALUES` become
    NaN; a record with a fill in ``YY MM DD HH`` gets a ``NaT`` time (dropped, with
    the count, by :func:`oceanarray.readers.read` and stage 1).  An
    ``.edt``/``.lowedt`` header's ``MagDeviation`` (already applied by the
    originator) is recorded as ``originator_magnetic_declination`` and the
    velocities marked ``coordinate_system = "ENU"``.  A ``.lowedt`` file is marked
    ``originator_low_passed``.

    Parameters
    ----------
    path : str or pathlib.Path
        The RODB file.

    Returns
    -------
    xarray.Dataset
        One ``time`` dimension, oceanarray variable names and ``originator_*``
        attributes.

    Raises
    ------
    ValueError
        If the header has no ``Columns`` line, a column is not in
        :data:`RODB_COLUMNS`, ``YY:MM:DD:HH`` are missing, or no data rows follow
        the header.
    """
    path = Path(path)
    lines = path.read_text().splitlines()
    header: dict[str, str] = {}
    start = None
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if "=" not in line:
            start = i
            break
        key, val = (s.strip() for s in line.split("=", 1))
        header[key] = val
    if "Columns" not in header:
        raise ValueError(f"{path.name}: no 'Columns' header line; not a RODB file")
    columns = header["Columns"].split(":")
    if columns[:4] != ["YY", "MM", "DD", "HH"]:
        raise ValueError(f"{path.name}: columns must start YY:MM:DD:HH, got {columns}")
    unknown = [c for c in columns[4:] if c not in RODB_COLUMNS]
    if unknown:
        raise ValueError(
            f"{path.name}: RODB column(s) {unknown} have no mapping; add them to "
            f"RODB_COLUMNS with the units documented in config/legacy/rodb_keys.yaml"
        )

    if start is None:
        raise ValueError(f"{path.name}: no data rows after the RODB header")

    data = np.atleast_2d(np.genfromtxt(lines[start:], dtype=float))
    if data.shape[1] < 4:
        raise ValueError(
            f"{path.name}: data rows have fewer than 4 columns (need YY MM DD HH)"
        )
    for fill in RODB_FILL_VALUES:
        data[data == fill] = np.nan
    # A fill in YY/MM/DD/HH leaves the record with no time: build it from the
    # complete rows only and mark the rest NaT (a NaN hour cast to int64 is
    # platform-dependent and can read as midnight).
    valid = np.isfinite(data[:, :4]).all(axis=1)
    time = np.full(len(data), np.datetime64("NaT", "ns"))
    if valid.any():
        rows = data[valid]
        day = pd.to_datetime(
            {"year": rows[:, 0], "month": rows[:, 1], "day": rows[:, 2]}
        ).to_numpy()
        seconds = np.rint(rows[:, 3] * 3600.0).astype("int64")
        time[valid] = day + seconds.astype("timedelta64[s]")

    out = xr.Dataset(coords={"time": time})
    for j, code in enumerate(columns[4:], start=4):
        name, units = RODB_COLUMNS[code]
        out[name] = xr.DataArray(
            data[:, j], dims="time", attrs={"units": units, "original_name": code}
        )

    for key, val in header.items():
        out.attrs[f"originator_{key}"] = val
    for key in ("Latitude", "Longitude"):
        if key in header:
            out.attrs[f"originator_{key.lower()}"] = _rodb_position(header[key])
    out.attrs["originator_file"] = path.name
    out.attrs["originator_format"] = "RODB"
    if "MagDeviation" in header:
        out.attrs["originator_magnetic_declination"] = float(header["MagDeviation"])
    if {"east_velocity", "north_velocity"} & set(out.data_vars):
        out.attrs["coordinate_system"] = "ENU"
        out.attrs.setdefault(
            "originator_magnetic_declination",
            "applied by the originator; value not recorded",
        )
    if path.suffix.lower() == ".lowedt":
        out.attrs["originator_low_passed"] = "2-day low-pass, 12-hour time step"
    return out
