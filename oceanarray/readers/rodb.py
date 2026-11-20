"""Reader for RODB text files (``.microcat``, ``.edt``, ``.lowedt``).

These are originator-processed RODB-format time series (RAPID/OSNAP).  Column
codes are mapped to oceanarray names through :data:`RODB_COLUMNS`.  An ADCP
delivered as one ``.edt`` file per bin is joined by :func:`read_rodb_bins`.
"""

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from oceanarray import provenance
from oceanarray.config.parameters import ADCP_BIN_DIM, RDI_BAD_VALUE_CM_S

log = logging.getLogger(__name__)

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
    "Z": ("bin_depth", "m"),
    "EV": ("error_velocity", "cm s-1"),
    **{f"BEAM{b}SS": (f"amplitude_beam{b}", "counts") for b in range(1, 5)},
    **{f"BEAM{b}COR": (f"correlation_beam{b}", "counts") for b in range(1, 5)},
    # RDI percent-good fields 1-4 (field 4: four-beam solutions), as in a raw file.
    **{f"BEAM{b}PGP": (f"percent_good_beam{b}", "percent") for b in range(1, 5)},
}

#: Variables of a per-bin ADCP file that are the same in every bin (the head's).
_HEAD_VARS = ("temperature", "heading", "pitch", "roll")

#: RODB missing-data markers.  Only these: a value such as ``-9.0`` is a real
#: velocity or roll in an ``.edt`` file and must not be blanked.
RODB_FILL_VALUES: tuple[float, ...] = (-9999.0, 1e32, RDI_BAD_VALUE_CM_S)

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
    out.attrs["originator_sha256"] = provenance.sha256(path)
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


def read_rodb_bins(paths: list[str | Path]) -> xr.Dataset:
    """Read one ADCP's RODB ``.edt`` files, one per bin, as one dataset on ``(time, N_BINS)``.

    The bin number comes from ``_bin<NN>`` in each name; a missing number is
    warned about and the bins present are kept, numbered by a ``bin_number``
    coordinate. ``Z`` (bin depth) becomes ``bin_depth`` and ``bin_pressure``
    (``gsw.p_from_z`` at the header latitude, kept as the originator's); ``range``
    is the median distance from the head, placed one bin spacing before bin 1. Head variables are
    taken from the first bin. The beam columns become ``amplitude``,
    ``correlation`` and ``percent_good`` on ``(time, N_BINS, beam)``, ``beam``
    numbered from the column names.

    Parameters
    ----------
    paths : list of str or pathlib.Path
        The per-bin files of one instrument.

    Returns
    -------
    xarray.Dataset
        One ADCP on ``time`` and ``N_BINS``, with ``originator_files``.

    Raises
    ------
    ValueError
        If a file is not ``.edt`` or has no bin number, two files share a bin, the
        header serials differ, or the files' times differ.
    """
    from oceanarray.pipeline.pressure import (
        ORIGINATOR_BIN_PRESSURE_ATTRS,
        originator_bin_geometry,
        pressure_from_depth,
    )

    paths = [Path(p) for p in paths]
    numbered: dict[int, Path] = {}
    for p in paths:
        m = re.search(r"_bin(\d+)$", p.stem)
        if p.suffix.lower() != ".edt" or m is None:
            raise ValueError(f"{p.name}: not a per-bin .edt file (<name>_binNN.edt)")
        if int(m.group(1)) in numbered:
            raise ValueError(f"{p.name} and {numbered[int(m.group(1))].name}: same bin")
        numbered[int(m.group(1))] = p
    numbers = sorted(numbered)
    parts = [read_rodb(numbered[n]) for n in numbers]
    first = parts[0]
    serials = {ds.attrs.get("originator_SerialNumber") for ds in parts}
    if len(serials) > 1:
        raise ValueError(f"{first.attrs['originator_file']}: serials differ: {serials}")
    for ds in parts[1:]:
        if not np.array_equal(ds["time"].values, first["time"].values):
            raise ValueError(
                f"{ds.attrs['originator_file']}: times differ from "
                f"{first.attrs['originator_file']}"
            )
    missing = sorted(set(range(1, numbers[-1] + 1)) - set(numbers))
    if missing:
        log.warning(f"  {first.attrs['originator_file']}: no file for bin(s) {missing}")
    for var in _HEAD_VARS:
        if var in first and any(
            not np.array_equal(ds[var], first[var], equal_nan=True) for ds in parts
        ):
            diff = max(float(np.nanmax(abs(ds[var] - first[var]))) for ds in parts)
            log.warning(
                f"  {var} differs between bins (max {diff:g}); bin {numbers[0]}'s kept"
            )

    out = first[[v for v in _HEAD_VARS if v in first]]
    per_bin = [v for v in first.data_vars if v not in _HEAD_VARS and "_beam" not in v]
    for var in per_bin:
        out[var] = xr.concat([ds[var] for ds in parts], dim=ADCP_BIN_DIM).T
    for name in ("amplitude", "correlation", "percent_good"):
        beams = sorted(
            (int(v.rsplit("_beam", 1)[1]), v)
            for v in first.data_vars
            if v.startswith(f"{name}_beam")
        )
        if beams:
            da = xr.concat(
                [xr.concat([ds[v] for _, v in beams], dim="beam") for ds in parts],
                dim=ADCP_BIN_DIM,
            ).transpose("time", ADCP_BIN_DIM, "beam")
            out[name] = da.assign_coords(beam=np.array([n for n, _ in beams], "int8"))
            out[name].attrs = {"units": first[beams[0][1]].attrs["units"]}

    lat = first.attrs["originator_latitude"]
    out["bin_pressure"] = xr.DataArray(
        pressure_from_depth(out["bin_depth"].values, lat),
        dims=("time", ADCP_BIN_DIM),
        attrs={
            **ORIGINATOR_BIN_PRESSURE_ATTRS,
            "comment": f"gsw.p_from_z(-Z, {lat:.4f}) from the per-bin files",
        },
    )
    # The header's InstrDepth can be the design depth, far from the deployed one, so
    # the head is placed from the bins: one bin spacing before bin 1.
    z = np.nanmedian(out["bin_depth"].values, axis=0)
    if len(numbers) > 1:
        spacing = (z[-1] - z[0]) / (numbers[-1] - numbers[0])
        head, source = z[0] - spacing * numbers[0], "one bin spacing before bin 1"
    else:
        head, source = float(first.attrs["originator_InstrDepth"]), "header InstrDepth"
    orientation, rng = originator_bin_geometry(z - head)
    out = out.assign_coords(
        range=(
            ADCP_BIN_DIM,
            rng,
            {
                "units": "m",
                "long_name": "distance from the transducer to the bin centre",
                "comment": f"nominal, median Z with the head {source}; places the "
                "stack row only, not bin_pressure",
            },
        ),
        bin_number=(ADCP_BIN_DIM, np.array(numbers, dtype="int16")),
    )
    out.attrs = dict(first.attrs)
    out.attrs["originator_files"] = ", ".join(numbered[n].name for n in numbers)
    out.attrs["orientation_instrument"] = orientation
    out.attrs["orientation_instrument_source"] = (
        f"file: Z relative to the head ({source})"
    )
    out.attrs["coordinate_system_source"] = (
        "originator: geographic east/north velocity in the file"
    )
    return out
