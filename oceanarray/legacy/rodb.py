"""rodbhead.py - Decode RO database keywords.

Originally written in MATLAB by:
  - G. Krahmann, IfM Kiel, Oct 1995 (v0.1.0)
  - Updated for EPIC conventions: Feb 1996 (v0.1.1)
  - Optimized and extended by multiple contributors through 2005
  - Last change noted in MATLAB: C. Mertens, May 1997 (v0.1.4)
  - Extensive extensions by D. Kieke (2000) and T. Kanzow (2005)

Ported to Python by E Frajka-Williams, 2025
"""

import logging
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import xarray as xr
import yaml

from .convertOS import parse_rodb_metadata

log = logging.getLogger(__name__)

REVERSE_KEYS = {
    "mooring": "Mooring",
    "serial_number": "SerialNumber",
    "water_depth": "WaterDepth",
    "instrdepth": "InstrDepth",  # if present in attrs
    "latitude": "Latitude",  # if present in attrs
    "longitude": "Longitude",  # if present in attrs
    "start_time": ["Start_Date", "Start_Time"],  # split into date + time
    "end_time": ["End_Date", "End_Time"],  # split into date + time
    "columns": "Columns",
}


# in rodb.py
def is_rodb_file(filepath: Path) -> bool:
    """Check whether a file is RODB-style based on filename and/or content."""
    return filepath.suffix.lower() in {".raw", ".use", ".microcat"}


def parse_rodb_keys_file(filepath):
    """Parse a rodb_keys.txt file with MATLAB-style lines into structured dicts.

    Returns a dictionary with a list of entries under the 'RODB_KEYS' key.
    """
    entries = []
    pattern = re.compile(r"^\s*'(?P<line>[^']+)';\s*\.\.\.\s*%?\s*(?P<comment>.*)?")

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("'"):
                continue

            match = pattern.match(line)
            if not match:
                continue
            # Extract the line content and optional comment
            print(line)
            raw_fields = match.group("line").split()
            if len(raw_fields) < 4:
                continue  # not enough fields

            key = raw_fields[0]
            fmt = raw_fields[1]
            count = int(raw_fields[2])
            code = int(raw_fields[3])
            comment = match.group("comment").strip() if match.group("comment") else None

            entry = {
                "key": key,
                "format": fmt,
                "count": count,
                "code": code,
            }
            if comment:
                entry["comment"] = comment

            entries.append(entry)

    return {"RODB_KEYS": entries}


# Example usage
if __name__ == "__main__":
    infile = Path("rodb_keys.txt")  # Replace with your actual file path
    rodb_keys_data = parse_rodb_keys_file(infile)

    with open("rodb_keys.yaml", "w") as outfile:
        yaml.dump(rodb_keys_data, outfile, sort_keys=False)


# Load full RODB key metadata from YAML file
RODB_KEYS_PATH = Path(__file__).parent.parent / "config" / "legacy" / "rodb_keys.yaml"
with open(RODB_KEYS_PATH, "r") as f:
    RODB_KEYS = yaml.safe_load(f)

# Dummy placeholder values that indicate missing data
DUMMY_VALUES = [-9999, -99.999, -999.999]


def rodbload(filepath, variables: list[str] = None) -> xr.Dataset:
    """Read a RODB .use or .raw file into an xarray.Dataset."""
    filepath = Path(filepath)
    with open(filepath, "r") as f:
        lines = f.readlines()

    header, data_start_index = parse_rodb_metadata(filepath)

    default_dim = "N_MEASUREMENTS"

    # Determine variables
    var_string = header.get("COLUMNS", "YY:MM:DD:HH:T:C:P")
    file_variables = var_string.split(":")
    if variables is None:
        variables = file_variables
    col_indices = {var: i for i, var in enumerate(file_variables) if var in variables}

    # Load numeric data
    data = np.genfromtxt(lines[data_start_index:], dtype=float)
    if data.ndim == 1:
        data = data[np.newaxis, :]

    for dummy in DUMMY_VALUES:
        data[data == dummy] = np.nan

    coords = {default_dim: np.arange(data.shape[0])}
    data_vars = {var: (default_dim, data[:, col_indices[var]]) for var in variables}

    if all(var in col_indices for var in ["YY", "MM", "DD", "HH"]):
        y, m, d, h = (data[:, col_indices[v]] for v in ["YY", "MM", "DD", "HH"])
        time = [
            datetime(int(yy), int(mm), int(dd), int(hh), int((hh % 1) * 60))
            for yy, mm, dd, hh in zip(y, m, d, h)
        ]
        coords["TIME"] = (default_dim, np.array(time, dtype="datetime64[s]"))
    else:
        # Optional: warn if TIME can't be constructed
        missing = [v for v in ["YY", "MM", "DD", "HH"] if v not in col_indices]
        log.warning("Could not create TIME coordinate. Missing: %s", ", ".join(missing))

    ds = xr.Dataset(data_vars, coords=coords)

    # Now convert header fields into attributes and variables
    attrs = {}

    # Global metadata
    attrs["mooring"] = header.get("MOORING")
    attrs["serial_number"] = header.get("SERIALNUMBER")
    attrs["water_depth"] = int(header["WATERDEPTH"]) if "WATERDEPTH" in header else None

    # Combine start and end date+time into ISO strings
    if "START_DATE" in header and "START_TIME" in header:
        attrs["start_time"] = f"{header['START_DATE']}T{header['START_TIME']}"
    if "END_DATE" in header and "END_TIME" in header:
        attrs["end_time"] = f"{header['END_DATE']}T{header['END_TIME']}"

    # Convert InstrDepth, Latitude, Longitude to variables
    if "INSTRDEPTH" in header:
        ds["InstrDepth"] = header["INSTRDEPTH"] = float(header["INSTRDEPTH"])
    if "LATITUDE" in header:
        lat_str = header["LATITUDE"]
        ds["Latitude"] = float(lat_str.split()[0]) + float(lat_str.split()[1]) / 60.0
        if "S" in lat_str:
            ds["Latitude"] *= -1
    if "LONGITUDE" in header:
        lon_str = header["LONGITUDE"]
        ds["Longitude"] = float(lon_str.split()[0]) + float(lon_str.split()[1]) / 60.0
        if "W" in lon_str:
            ds["Longitude"] *= -1

    # Add file reference and inferred columns
    attrs["source_file"] = str(filepath)
    attrs["columns"] = list(data_vars.keys())
    # Promote TIME to an index if available
    if "TIME" in ds.coords:
        ds = ds.swap_dims({default_dim: "TIME"})

    ds.attrs.update({k: v for k, v in attrs.items() if v is not None})
    return ds


NOMINAL_HEADER_ORDER = [
    "Mooring",
    "SerialNumber",
    "WaterDepth",
    "InstrDepth",
    "Start_Date",
    "Start_Time",
    "End_Date",
    "End_Time",
    "Latitude",
    "Longitude",
    "Columns",
]

REVERSE_ATTR_KEYS = {
    "Mooring": "mooring",
    "SerialNumber": "serial_number",
    "WaterDepth": "water_depth",
    "InstrDepth": "instrdepth",
    "Start_Date": "start_time",
    "Start_Time": "start_time",
    "End_Date": "end_time",
    "End_Time": "end_time",
    "Latitude": "latitude",
    "Longitude": "longitude",
    "Columns": "columns",
}


def format_latlon(value, is_lat=True):
    deg = int(abs(value))
    minutes = (abs(value) - deg) * 60
    width = 2 if is_lat else 3
    hemi = ("N" if value >= 0 else "S") if is_lat else ("E" if value >= 0 else "W")
    return f"{deg:0{width}d} {minutes:06.3f} {hemi}"


def rodbsave(filepath, ds: xr.Dataset, fmt=None):
    """Save an xarray.Dataset to a RODB-style .use or .raw file.

    Parameters
    ----------
    filepath : str or Path
        Output file path.
    ds : xarray.Dataset
        Dataset to write. Expects time-series variables to be indexed by 'obs'.
    fmt : str, optional
        Format string passed to np.savetxt. If None, one is generated automatically.

    """
    filepath = Path(filepath)
    attrs = ds.attrs.copy()

    # Select time-series variables
    timeseries_vars = attrs.get("columns")
    if timeseries_vars is None:
        timeseries_vars = [v for v in ds.data_vars if "obs" in ds[v].dims]

    if fmt is None:
        default_fmts = {
            "YY": "%4d",
            "MM": "%4d",
            "DD": "%4d",
            "HH": "%10.5f",
            "T": "%9.4f",
            "C": "%9.4f",
            "P": "%7.1f",
        }
        fmt_parts = []
        for var in timeseries_vars:
            fmt_parts.append(default_fmts.get(var, "%10.4f"))
        fmt = " ".join(fmt_parts)
    written_keys = set()

    with open(filepath, "w", newline="") as f:
        for key in NOMINAL_HEADER_ORDER:
            attr_key = REVERSE_ATTR_KEYS.get(key)

            if (
                key in ["Start_Date", "Start_Time"]
                and "start_time" in attrs
                and "Start_Date" not in written_keys
            ):
                try:
                    dt = datetime.fromisoformat(attrs["start_time"])
                except ValueError:
                    dt = datetime.strptime(attrs["start_time"], "%Y/%m/%dT%H:%M")
                f.write(f"{'Start_Date':<22} = {dt.strftime('%Y/%m/%d')}\n")
                f.write(f"{'Start_Time':<22} = {dt.strftime('%H:%M')}\n")
                written_keys.update(["Start_Date", "Start_Time"])
                continue

            if (
                key in ["End_Date", "End_Time"]
                and "end_time" in attrs
                and "End_Date" not in written_keys
            ):
                try:
                    dt = datetime.fromisoformat(attrs["end_time"])
                except ValueError:
                    dt = datetime.strptime(attrs["end_time"], "%Y/%m/%dT%H:%M")
                f.write(f"{'End_Date':<22} = {dt.strftime('%Y/%m/%d')}\n")
                f.write(f"{'End_Time':<22} = {dt.strftime('%H:%M')}\n")
                written_keys.update(["End_Date", "End_Time"])
                continue

            if key == "Latitude" and "Latitude" not in written_keys:
                val = ds.get("Latitude", None)
                if val is not None:
                    f.write(
                        f"{'Latitude':<22} = {format_latlon(val.values.item(), is_lat=True)}\n"
                    )
                    written_keys.add("Latitude")
                continue

            if key == "Longitude" and "Longitude" not in written_keys:
                val = ds.get("Longitude", None)
                if val is not None:
                    f.write(
                        f"{'Longitude':<22} = {format_latlon(val.values.item(), is_lat=False)}\n"
                    )
                    written_keys.add("Longitude")
                continue

            if key == "Columns" and "Columns" not in written_keys:
                f.write(f"{'Columns':<22} = " + ":".join(timeseries_vars) + "\n")
                written_keys.add("Columns")
                continue

            if attr_key in attrs and key not in written_keys:
                f.write(f"{key:<22} = {attrs[attr_key]}\n")
                written_keys.add(key)

            elif (
                key in ds
                and key not in written_keys
                and (np.isscalar(ds[key].values) or ds[key].size == 1)
            ):
                val = ds[key].values.item()
                f.write(f"{key:<22} = {val:.0f}\n")
                written_keys.add(key)

        # Write the data block
        data_array = np.column_stack([ds[var].values for var in timeseries_vars])
        np.savetxt(f, data_array, fmt=fmt)
