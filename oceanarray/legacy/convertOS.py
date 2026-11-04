import datetime
from pathlib import Path
from typing import Optional, Union

import numpy as np
import xarray as xr
import yaml

import math
from typing import Any



def convert_rodb_to_oceansites(
    ds: xr.Dataset,
    metadata_txt: Union[str, Path],
    var_map_yaml: Union[str, Path],
    vocab_yaml: Union[str, Path],
    sensor_yaml: Optional[Union[str, Path]] = None,
    project_yaml: Optional[Union[str, Path]] = None,
) -> xr.Dataset:
    """Convert a dataset loaded from RODB format into OceanSITES-compliant format.

    Parameters
    ----------
    ds : xarray.Dataset
        Original dataset with RODB-style variables and TIME coordinate.
    metadata_txt : str or Path
        Path to RODB metadata text file (header block with key=value lines).
    var_map_yaml : str or Path
        YAML file mapping original variable names to OceanSITES names.
    vocab_yaml : str or Path
        YAML file providing OceanSITES variable attributes.
    sensor_yaml : str or Path, optional
        YAML file with instrument metadata and variable-instrument mapping.
    project_yaml : str or Path, optional
        YAML file with project-level global attributes.

    Returns
    -------
    xarray.Dataset
        OceanSITES-compliant dataset.

    """
    metadata, _ = parse_rodb_metadata(metadata_txt)
    var_map = load_yaml(var_map_yaml)
    vocab_attrs = load_yaml(vocab_yaml)

    # If present, remove variables YY, MM, DD, HH and coordinate N_MEASUREMENTS
    for var in ["YY", "MM", "DD", "HH", "N_MEASUREMENTS"]:
        if var in ds.data_vars:
            ds = ds.drop_vars(var)
        if var in ds.coords:
            ds = ds.drop_vars(var)

    # Step 1: Rename variables using mapping
    ds = ds.rename(var_map)

    # Step 2: Add coordinate variables for LATITUDE, LONGITUDE, DEPTH
    ds = add_fixed_coordinates(ds, metadata)

    # Step 3: Add variable attributes from vocab
    ds = add_variable_attributes(ds, vocab_attrs)

    # Step 4: Add global attributes
    ds = add_global_attributes(ds, metadata)

    # Step 5: Format TIME variable
    ds = format_time_variable(ds)

    # Step 6: Add instrument metadata (optional)
    if sensor_yaml is not None:
        sensor_dict = load_yaml(sensor_yaml)
        ds = add_instrument_metadata(ds, sensor_dict, metadata)
    else:
        # Fallback: assign default TOOL0018 for all known variables
        serial = metadata.get("SERIALNUMBER", "unknown")
        start = f"{metadata['START_DATE']}T{metadata['START_TIME']}:00Z"
        end = f"{metadata['END_DATE']}T{metadata['END_TIME']}:00Z"
        sensor_dict = {
            "instrument_1": {
                "sensor_model": "SBE 37 MicroCat SMP-CT",
                "sensor_manufacturer": "Sea-Bird",
                "sensor_serial_number": serial,
                "sensor_SeaVoX_L22_code": "SDN:L22::TOOL0018",
                "sensor_reference": "https://www.seabird.com/sbe37",
                "sensor_data_start_date": start,
                "sensor_data_end_date": end,
                "comment": "Default instrument (SMP-CT) assigned by fallback.",
            },
            "variables": {
                var: "instrument_1" for var in ds.data_vars if var not in ds.coords
            },
        }
        ds = add_instrument_metadata(ds, sensor_dict, metadata)

    # Step 7: Add project attributes (optional)
    project_attrs = load_yaml(project_yaml) if project_yaml is not None else {}
    ds = add_project_attributes(ds, project_attrs, metadata)

    # Rename "mooring" attribute to "internal_mooring_identifier" if present
    if "mooring" in ds.attrs:
        ds.attrs["internal_identifier"] = ds.attrs.pop("mooring")
    ds = sort_global_attributes(ds)

    lat = float(ds["LATITUDE"].values.flat[0])
    lon = float(ds["LONGITUDE"].values.flat[0])

    ds = ds.squeeze(dim=["LATITUDE", "LONGITUDE"], drop=True)
    ds = ds.assign_coords(LATITUDE=("TIME", [lat] * ds.sizes["TIME"]))
    ds = ds.assign_coords(LONGITUDE=("TIME", [lon] * ds.sizes["TIME"]))

    return ds


def load_yaml(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def parse_rodb_metadata(txt_path):
    """Parse RODB metadata from a .raw or .use file header.

    Parameters
    ----------
    txt_path : str or Path
        Path to the RODB file.

    Returns
    -------
    tuple
        (header_dict, data_start_index)

    """
    txt_path = Path(txt_path)
    with open(txt_path, "r") as f:
        lines = f.readlines()

    header = {}
    data_start_index = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if "=" in line:
            key, val = map(str.strip, line.split("=", 1))
            header[key.upper()] = val
        else:
            data_start_index = i
            break
    return header, data_start_index


def add_fixed_coordinates(ds, metadata):
    """Add fixed spatial coordinates LATITUDE, LONGITUDE, and DEPTH from metadata."""

    def dms_to_decimal(coord_str):
        parts = coord_str.split()
        deg = float(parts[0])
        min_ = float(parts[1])
        hemi = parts[2].upper()
        decimal = deg + min_ / 60
        if hemi in ["S", "W"]:
            decimal *= -1
        return decimal

    lat = dms_to_decimal(metadata["LATITUDE"])
    lon = dms_to_decimal(metadata["LONGITUDE"])
    depth = float(metadata["INSTRDEPTH"])

    ds = ds.expand_dims({"DEPTH": [depth], "LATITUDE": [lat], "LONGITUDE": [lon]})

    ds = ds.assign_coords(
        {
            "DEPTH": (
                "DEPTH",
                [depth],
                {
                    "units": "m",
                    "standard_name": "depth",
                    "long_name": "Instrument depth",
                    "positive": "down",
                    "axis": "Z",
                },
            ),
            "LATITUDE": (
                "LATITUDE",
                [lat],
                {
                    "units": "degrees_north",
                    "standard_name": "latitude",
                    "long_name": "Latitude of instrument",
                    "axis": "Y",
                },
            ),
            "LONGITUDE": (
                "LONGITUDE",
                [lon],
                {
                    "units": "degrees_east",
                    "standard_name": "longitude",
                    "long_name": "Longitude of instrument",
                    "axis": "X",
                },
            ),
        }
    )

    # Reorder coordinates on all variables to TIME, DEPTH, LATITUDE, LONGITUDE order if present
    coord_order = [
        c for c in ["TIME", "DEPTH", "LATITUDE", "LONGITUDE"] if c in ds.dims
    ]
    for var in ds.data_vars:
        dims = list(ds[var].dims)
        # Only reorder if all coord_order dims are present in variable
        if all(dim in dims for dim in coord_order):
            # Place coord_order dims first, then any remaining dims
            new_order = coord_order + [d for d in dims if d not in coord_order]
            ds[var] = ds[var].transpose(*new_order)

    return ds


def add_variable_attributes(ds, vocab_attrs):
    for var in ds.data_vars:
        if var in vocab_attrs:
            ds[var].attrs.update(vocab_attrs[var])
    return ds


def add_global_attributes(ds, metadata):
    """Add OceanSITES-compliant global attributes to the dataset."""
    start = f"{metadata['START_DATE']}T{metadata['START_TIME']}:00Z"
    end = f"{metadata['END_DATE']}T{metadata['END_TIME']}:00Z"
    start = start.replace("/", "-")
    end = end.replace("/", "-")
    is_iso8601_utc(start)
    is_iso8601_utc(end)

    lat = metadata["LATITUDE"]
    lon = metadata["LONGITUDE"]
    instr_depth = float(metadata["INSTRDEPTH"])

    ds.attrs.update(
        {
            "title": f"Time series from {metadata['MOORING']}, instrument {metadata['SERIALNUMBER']}",
            "summary": "CTD mooring time series processed to OceanSITES standards.",
            "Conventions": "CF-1.7, ACDD-1.3, OceanSITES-1.4",
            "deployment_code": metadata["MOORING"],
            "time_coverage_start": start,
            "time_coverage_end": end,
            "geospatial_lat_min": lat,
            "geospatial_lat_max": lat,
            "geospatial_lon_min": lon,
            "geospatial_lon_max": lon,
            "geospatial_vertical_min": instr_depth,
            "geospatial_vertical_max": instr_depth,
            "history": "converted to OceanSITES by convert_rodb_to_oceansites",
        }
    )

    # remove if in the attributes ["start_time", "end_time"]
    if "start_time" in ds.attrs:
        del ds.attrs["start_time"]
    if "end_time" in ds.attrs:
        del ds.attrs["end_time"]

    return ds


def format_time_variable(ds):
    # Ensure TIME is CF-compliant: units, calendar, etc.
    ds["TIME"].attrs.update(
        {
            "standard_name": "time",
            "long_name": "Time of measurement",
            "units": "seconds since 1970-01-01T00:00:00Z",
            "calendar": "gregorian",
            "axis": "T",
        }
    )
    return ds


def add_instrument_metadata(ds, sensor_dict, metadata):
    """Add centralized instrument metadata and link variables via the 'instrument' attribute.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset to modify.
    sensor_dict : dict
        Dictionary with 'instrument_<N>' keys and/or TOOL### templates, plus 'variables'.
    metadata : dict
        Header metadata to extract instrument-specific fields like serial and dates.

    """
    variables = sensor_dict.get("variables", {})
    templates = {k: v for k, v in sensor_dict.items() if k.startswith("TOOL")}

    sensor_type = "CTD"  # for RAPID core measurements (TEMP, CNDC, PRES)
    serial = metadata.get("SERIALNUMBER", "unknown")
    inst_depth = float(metadata.get("INSTRDEPTH", 0))
    start = f"{metadata['START_DATE']}T{metadata['START_TIME']}:00Z"
    end = f"{metadata['END_DATE']}T{metadata['END_TIME']}:00Z"

    for var, _ in variables.items():
        if var in ds.data_vars:
            sensor_name = f"SENSOR_{sensor_type}_{serial}"
            ds[var].attrs["instrument"] = sensor_name

    sensor_name = f"SENSOR_{sensor_type}_{serial}"
    if sensor_name not in ds.data_vars:
        # Fallback: determine base template
        template_key = "TOOL0018"
        for attr in ds[var].attrs.values():
            if isinstance(attr, str) and attr.startswith("SDN:L22::TOOL"):
                template_key = attr.split("::")[-1]
                break
        base = templates.get(template_key, templates.get("TOOL0018", {})).copy()

        # Fill dynamic fields
        base.setdefault("sensor_serial_number", serial)
        base.setdefault("sensor_data_start_date", start)
        base.setdefault("sensor_data_end_date", end)

    # If inst_depth is an integer (i.e., no values smaller than 1), convert to int
    if isinstance(inst_depth, (int, float)) and inst_depth.is_integer():
        inst_depth = int(inst_depth)

    # Attach as scalar variable with attributes only (no dims, no coords)
    ds[sensor_name] = xr.DataArray(inst_depth, attrs=base)

    return ds


def infer_data_mode(source_filename=None, history=None):
    """Infer data_mode (D=delayed, R=real-time, P=provisional) from filename or processing history.

    Parameters
    ----------
    source_filename : str or Path, optional
        Original filename (e.g., 'example.raw', 'data.microcat').
    history : str, optional
        Global attribute describing the file's processing history.

    Returns
    -------
    str
        One of "D", "R", or "P". Defaults to "D" (delayed mode) if no clear match found.

    """
    # Priority 1: filename extension
    if source_filename:
        fname = str(source_filename).lower()
        if fname.endswith((".raw", ".use")):
            return "P"
        elif fname.endswith(".microcat"):
            return "D"

    # Priority 2: clues from history
    if history:
        text = history.lower()
        if "realtime" in text or "real-time" in text:
            return "R"
        elif "prelim" in text:
            return "P"
        elif "delayed" in text:
            return "D"

    # Fallback
    return "P"  # Default to preliminary mode if no clues found


def add_project_attributes(ds, project_attrs=None, metadata=None):
    """Merge project-level attributes into dataset, with derived and dynamic values."""
    if project_attrs is None:
        project_attrs = {}
    if metadata is None:
        metadata = {}
    attrs = project_attrs.copy()

    # Platform and deployment from mooring name
    mooring = metadata.get("MOORING", "unknown")
    if "_" in mooring:
        platform_code, deployment_code = mooring.split("_", 1)
    else:
        platform_code = mooring
        deployment_code = "unknown"

    attrs.setdefault("platform_code", platform_code)
    attrs.setdefault("deployment_code", deployment_code)
    attrs.setdefault(
        "title",
        f"Time series from {mooring}, instrument {metadata.get('SERIALNUMBER', '')}",
    )
    attrs.setdefault(
        "summary", "CTD mooring time series processed to OceanSITES standards."
    )

    # Determine serial and data mode
    serial = metadata.get("SERIALNUMBER", "unknown")
    if "data_mode" not in attrs:
        datamode = infer_data_mode(
            source_filename=ds.attrs.get("source_file", None),
            history=ds.attrs.get("history", None),
        )
        attrs["data_mode"] = datamode
    else:
        datamode = attrs["data_mode"]

    # Generate unique ID
    attrs["id"] = f"OS_{platform_code}_{deployment_code}_{serial}_{datamode}"

    # Duration and resolution from TIME coordinate
    time = ds["TIME"].values
    if len(time) > 1:
        duration_days = (time[-1] - time[0]) / np.timedelta64(1, "D")
        mean_spacing = np.median(np.diff(time)) / np.timedelta64(1, "s")
        attrs.setdefault(
            "time_coverage_resolution", iso8601_duration_from_seconds(mean_spacing)
        )

    # Add date_created / date_modified
    now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    if "date_created" not in ds.attrs:
        attrs["date_created"] = now
    else:
        attrs["date_modified"] = now

    attrs.setdefault("QC_indicator", "unknown")

    # Merge into dataset attributes
    ds.attrs.update(attrs)

    return ds


OCEANSITES_ATTR_ORDER = [
    # Identifiers and project-level
    "site_code",
    "platform_code",
    "deployment_code",
    "data_mode",
    "title",
    "theme",
    "summary",
    "naming_authority",
    "id",
    "internal_identifier",
    "wmo_platform_code",
    "source",
    "source_vocabulary",
    "principal_investigator",
    "principal_investigator_email",
    "principal_investigator_id",
    "creator_name",
    "creator_email",
    "creator_url",
    "creator_institution",
    "creator_id",
    "creator_type",
    "array",
    "network",
    "keywords",
    "keywords_vocabulary",
    "comment",
    # Geospatial and temporal
    "sea_area",
    "sea_area_vocabulary",
    "geospatial_lat_min",
    "geospatial_lat_max",
    "geospatial_lon_min",
    "geospatial_lon_max",
    "geospatial_vertical_min",
    "geospatial_vertical_max",
    "geospatial_vertical_units",
    "geospatial_vertical_positive",
    "time_coverage_start",
    "time_coverage_end",
    "time_coverage_duration",
    "time_coverage_resolution",
    # Platform and feature metadata
    "cdm_data_type",
    "featureType",
    "platform_deployment_date",
    "platform_deployment_ship_name",
    "platform_deployment_cruise_name",
    "platform_recovery_date",
    "platform_recovery_ship_name",
    "platform_recovery_cruise_name",
    # File format and publisher
    "data_type",
    "format_version",
    "Conventions",
    "publisher_name",
    "publisher_email",
    "publisher_id",
    "publisher_institution",
    "publisher_institution_vocabulary",
    # Links and credits
    "references",
    "update_interval",
    "license",
    "citation",
    "acknowledgement",
    # History and QA
    "date_created",
    "date_modified",
    "history",
    "processing_level",
    "QC_indicator",
    # Contributor info
    "contributor_name",
    "contributor_email",
    "contributor_id",
    "contributor_role",
    "contributor_role_vocabulary",
]


def sort_global_attributes(ds, attr_order=None):
    if attr_order is None:
        attr_order = OCEANSITES_ATTR_ORDER

    ordered = {k: ds.attrs[k] for k in attr_order if k in ds.attrs}
    remaining = {k: v for k, v in ds.attrs.items() if k not in ordered}
    ds.attrs = {**ordered, **remaining}
    return ds


def iso8601_duration_from_seconds(seconds: float) -> str:
    """Convert a duration in seconds to an ISO 8601 duration string.

    Parameters
    ----------
    seconds : float
        Duration in seconds.

    Returns
    -------
    str
        ISO 8601 duration string, e.g., 'PT1H', 'PT30M', 'PT15S'.

    """
    seconds = int(round(seconds))
    if seconds >= 86400:
        return f"P{seconds // 86400}D"
    if seconds >= 3600:
        return f"PT{seconds // 3600}H"
    if seconds >= 60:
        return f"PT{seconds // 60}M"
    return f"PT{seconds}S"


def is_iso8601_utc(timestr: str) -> bool:
    """Validate whether a string is in ISO8601 UTC format: YYYY-MM-DDTHH:MM:SSZ.

    Parameters
    ----------
    timestr : str
        Input time string.

    Returns
    -------
    bool
        True if valid ISO8601 UTC format, False otherwise.

    """
    try:
        datetime.datetime.strptime(timestr, "%Y/%m/%dT%H:%M:%SZ")  # RODB-style
    except ValueError:
        try:
            datetime.datetime.strptime(timestr, "%Y-%m-%dT%H:%M:%SZ")  # ISO8601 style
        except ValueError:
            return False
    return True
