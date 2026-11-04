"""Converting stage-3 datasets and grids to OceanSITES 1.4 datasets.

:func:`to_oceansites` returns one instrument's stage-3 dataset as an OceanSITES
deployment data file (OceanSITES Data Format Reference Manual 1.4, §4.1) and a
mooring's grid as a gridded file (§4.2): variables on ``(TIME, DEPTH)``,
``<PARAM>_QC`` flags from reference table 2, a data mode per variable (reference
table 4) and the global attributes of §2.2.  It writes nothing; the
:func:`oceanarray.export` verb finds a mooring's files, converts them and writes
each under :func:`oceansites_filename`.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from oceanarray import location
from oceanarray.config import parameters as params
from oceanarray.instruments import instrument_entries
from oceanarray.mooring import DEPLOYMENT_CODE_PATTERN
from oceanarray.paths import safe_serial
from oceanarray.pipeline.qc.calibration import CALDIP_STUB_APPLIED
from oceanarray.pipeline.stage1 import ADCP_BIN_DIM
from oceanarray.readers.oceansites import OCEANSITES_VARIABLES

logger = logging.getLogger(__name__)

#: oceanarray registry name → OceanSITES parameter code (reference table 6), the
#: inverse of the reader's table so reading and writing share one mapping.
PARAMETER_CODES: dict[str, str] = {
    name: code for code, name in OCEANSITES_VARIABLES.items()
}

#: Reference table 2, as oceanarray's own flags already carry it (5 and 6 unused).
QC_FLAG_VALUES = np.array([0, 1, 2, 3, 4, 7, 8, 9], dtype="int8")
QC_FLAG_MEANINGS = (
    "unknown good_data probably_good_data potentially_correctable_bad_data "
    "bad_data nominal_value interpolated_value missing_value"
)

#: Data modes this package can claim (reference table 4); ``R`` is real-time
#: telemetry, which a recovered instrument never is.
DATA_MODES = ("P", "D")

FORMAT_VERSION = "1.4"
CONVENTIONS = "CF-1.13, OceanSITES-1.4, ACDD-1.3"
TIME_UNITS = "days since 1950-01-01T00:00:00Z"
_EPOCH = np.datetime64("1950-01-01T00:00:00", "ns")

#: Attributes carried from a stage-3 variable onto its OceanSITES parameter.
_KEPT_ATTRS = ("standard_name", "units", "long_name", "valid_min", "valid_max")

#: Stage-3 ``SENSOR_*`` ``sensor_type`` → the variable it measured.
_SENSOR_VARIABLES = {
    "TEMPERATURE": "temperature",
    "CONDUCTIVITY": "conductivity",
    "PRESSURE": "pressure",
    "OXYGEN": "dissolved_oxygen",
}


#: The ADCP variables the below-seabed flag applies to: a bin below the seabed sees
#: the bottom, so its velocity is not water velocity; its computed pressure is
#: still a pressure.
_SEABED_VARIABLES = frozenset({"east_velocity", "north_velocity", "up_velocity"})


class OceanSitesError(ValueError):
    """Raised when a dataset or description cannot make a valid OceanSITES file."""


def _iso(t: Any) -> str:
    """Return an instant as ISO 8601 UTC, ``YYYY-MM-DDThh:mm:ssZ`` (manual §2.2)."""
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_duration(seconds: float) -> str:
    """Return a time step as an ISO 8601 duration, e.g. ``PT10M``."""
    seconds = round(float(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    out = "P" + (f"{days}D" if days else "")
    clock = "".join(
        f"{n}{u}" for n, u in ((hours, "H"), (minutes, "M"), (secs, "S")) if n
    )
    return out + ("T" + clock if clock else "") if (days or clock) else "PT0S"


def type_code(ds: xr.Dataset) -> str:
    """Return the instrument-type field of the file name for a stage-3 dataset.

    ``CM`` current meter; ``CTDO`` conductivity, temperature, pressure and
    oxygen; ``CTD`` conductivity, temperature and pressure (measured, or
    interpolated from neighbouring instruments); ``CT`` no pressure at all;
    ``O`` oxygen only; ``T`` temperature only; ``ADCP`` a profiler with bins.

    Raises
    ------
    OceanSitesError
        For a set of variables none of the codes describes.
    """
    names = set(ds.data_vars)
    if ADCP_BIN_DIM in ds.dims:
        return "ADCP"
    if {"east_velocity", "north_velocity"} <= names:
        return "CM"
    oxygen = "dissolved_oxygen" in names
    if "conductivity" in names:
        if oxygen:
            if "pressure" not in names:
                raise OceanSitesError(
                    "conductivity and oxygen without pressure has no type code"
                )
            return "CTDO"
        return "CTD" if "pressure" in names else "CT"
    if oxygen:
        return "O"
    if "temperature" in names:
        return "T"
    raise OceanSitesError(f"no type code for variables {sorted(names)}")


def _nominal_depth(ds: xr.Dataset) -> float:
    """Return the instrument's nominal depth (m): ``InstrDepth``, else water depth − HAB."""
    if "InstrDepth" in ds.variables:
        return float(ds["InstrDepth"].values)
    try:
        return float(ds.attrs["waterdepth"]) - float(ds.attrs["hab"])
    except KeyError as e:
        raise OceanSitesError(f"no nominal depth: stage-3 file lacks {e}") from None


def _bin_depths(ds: xr.Dataset, head: float) -> np.ndarray:
    """Return an ADCP's nominal bin depths: transducer depth ± ``range``, by orientation.

    The orientation is never assumed: without ``orientation_yaml`` or
    ``orientation_instrument`` the export refuses.
    """
    orientation = ds.attrs.get("orientation_yaml") or ds.attrs.get(
        "orientation_instrument"
    )
    if str(orientation).lower() not in ("up", "down"):
        raise OceanSitesError(f"ADCP orientation {orientation!r} is not 'up' or 'down'")
    if "range" not in ds.variables:
        raise OceanSitesError("ADCP stage-3 file has no 'range' (distance to each bin)")
    sign = 1.0 if str(orientation).lower() == "down" else -1.0
    return head + sign * np.asarray(ds["range"].values, dtype="float64")


def _position(mooring: Mapping) -> tuple[float, float]:
    """Return the mooring's position; refused when the description has none (no 0, 0)."""
    lat, lon, source = location.parse_latlon_with_source(dict(mooring))
    if location.is_unknown_latlon_source(source):
        raise OceanSitesError(
            "the mooring description has no position (latitude/longitude)"
        )
    return lat, lon


def _entry(mooring: Mapping, serial: str) -> Mapping:
    """Return the mooring description's entry for instrument *serial* ({} if absent)."""
    for entry in instrument_entries(dict(mooring)):
        if safe_serial(entry.get("serial")) == serial:
            return entry
    return {}


def _caldip_applied(ds: xr.Dataset) -> bool:
    """Return True if the stage-3 file records a real calibration-dip correction."""
    stamp = str(ds.attrs.get("caldip_applied", ""))
    return bool(stamp) and stamp != CALDIP_STUB_APPLIED and not stamp.startswith("none")


def _data_modes(ds: xr.Dataset, entry: Mapping, names: list[str]) -> dict[str, str]:
    """Return the data mode of each variable in *names*.

    The mode comes from the instrument entry's ``data_mode`` (one letter for all
    its variables, or a mapping from variable name to letter), recorded after a
    manual review; the default is ``P``.  ``D`` is refused unless the stage-3
    file records the calibration-dip correction.
    """
    given = entry.get("data_mode", "P")
    modes = {
        n: (given.get(n, "P") if isinstance(given, Mapping) else given) for n in names
    }
    for name, mode in modes.items():
        if mode not in DATA_MODES:
            raise OceanSitesError(
                f"data_mode {mode!r} for {name}: use one of {DATA_MODES}"
            )
        if mode == "D" and not _caldip_applied(ds):
            raise OceanSitesError(
                f"data_mode D for {name} needs the calibration-dip correction, "
                f"which this stage-3 file does not record"
            )
    return modes


def _sensor_attrs(ds: xr.Dataset, name: str, start: str, end: str) -> dict[str, str]:
    """Return the ``sensor_*`` attributes (manual §2.5) for variable *name*."""
    attrs = {
        "sensor_model": str(ds.attrs.get("source", ds.attrs.get("instrument", ""))),
        "sensor_serial_number": str(ds.attrs.get("serial", "")),
    }
    for var in ds.variables.values():
        meta = var.attrs
        if _SENSOR_VARIABLES.get(str(meta.get("sensor_type", ""))) == name:
            attrs["sensor_model"] = str(meta.get("sensor_model", attrs["sensor_model"]))
            attrs["sensor_serial_number"] = str(
                meta.get("sensor_serial_number", attrs["sensor_serial_number"])
            )
            if "sensor_calibration_date" in meta:
                attrs["sensor_calibration_date"] = str(meta["sensor_calibration_date"])
    attrs["sensor_mount"] = "mounted_on_mooring_line"
    attrs["sensor_data_start_date"] = start
    attrs["sensor_data_end_date"] = end
    return attrs


def _worst(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Return the element-wise worse of two reference-table-2 flag arrays."""
    rank = params.QC_MERGE_PRIORITY_LUT
    a = np.asarray(a, dtype="int8").clip(0, 9)
    b = np.broadcast_to(np.asarray(b, dtype="int8").clip(0, 9), a.shape)
    return np.where(rank[b] > rank[a], b, a).astype("int8")


def _as_levels(da: xr.DataArray) -> np.ndarray:
    """Return a stage-3 variable as a ``(time, level)`` array (one level for a point)."""
    values = np.asarray(da.values)
    return values[:, None] if values.ndim == 1 else values


def _flags(ds: xr.Dataset, name: str, values: np.ndarray) -> np.ndarray:
    """Return the ``(time, level)`` flags of *name*: its stage-3 flags, 9 where missing.

    An ADCP's seabed flag is kept apart from the velocity flags in stage 3
    (``seabed_qc``); OceanSITES has one flag per value, so for the velocities the
    worse of the two is written.
    """
    if f"{name}_qc" in ds:
        flags = np.broadcast_to(_as_levels(ds[f"{name}_qc"]), values.shape).astype(
            "int8"
        )
    else:
        flags = np.zeros(values.shape, dtype="int8")
    if "seabed_qc" in ds and name in _SEABED_VARIABLES:
        # Only a failed or suspect seabed test is merged in: a passed one must not
        # promote an unchecked (0) value to good.
        seabed = np.broadcast_to(_as_levels(ds["seabed_qc"]), flags.shape)
        flags = np.where(seabed >= 3, _worst(flags, seabed), flags).astype("int8")
    return np.where(np.isfinite(values), flags, 9).astype("int8")


def _qc_variable(flags: np.ndarray, long_name: str) -> xr.DataArray:
    """Return a ``<PARAM>_QC`` variable on ``(TIME, DEPTH)``."""
    return xr.DataArray(
        flags,
        dims=("TIME", "DEPTH"),
        attrs={
            "long_name": f"quality flag of {long_name}",
            "conventions": "OceanSITES QC Flags",
            "flag_values": QC_FLAG_VALUES,
            "flag_meanings": QC_FLAG_MEANINGS,
            "coordinates": "TIME DEPTH LATITUDE LONGITUDE",
        },
    )


def _first(mooring: Mapping, *keys: str) -> str | None:
    """Return the first non-empty value among *keys* in the description, as text."""
    for key in keys:
        if mooring.get(key):
            return str(mooring[key])
    return None


def deployment_code(mooring: Mapping) -> str:
    """Return the deployment code: ``deployment_code``, else ``YYYYMM`` of ``deployment_time``."""
    code = mooring.get("deployment_code")
    if code is None:
        if not mooring.get("deployment_time"):
            raise OceanSitesError(
                "no deployment_code and no deployment_time to derive it"
            )
        code = pd.Timestamp(str(mooring["deployment_time"])).strftime("%Y%m")
    code = str(code)
    if not re.fullmatch(DEPLOYMENT_CODE_PATTERN, code):
        raise OceanSitesError(f"deployment_code {code!r} is not YYYYMM")
    return code


def platform_code(mooring: Mapping) -> str:
    """Return ``platform_code``; refused if absent or holding ``_`` (the file-name separator)."""
    code = mooring.get("platform_code")
    if code is None or code == "":
        raise OceanSitesError("the mooring description has no platform_code")
    if not isinstance(code, str):
        # YAML reads an unquoted 12 as a number; the validator warns, and a number
        # is not taken as a code.
        raise OceanSitesError(
            f"platform_code {code!r} is not text; quote it in the YAML"
        )
    if "_" in code:
        raise OceanSitesError(
            f"platform_code {code!r} contains '_', the file-name separator"
        )
    return code


def _coordinates(
    times: np.ndarray, levels: np.ndarray, level_comment: str, lat: float, lon: float
) -> xr.Dataset:
    """Return the four OceanSITES coordinate variables (manual §2.4) as a dataset."""
    days = (times.astype("datetime64[ns]") - _EPOCH) / np.timedelta64(1, "D")
    out = xr.Dataset(
        coords={
            "TIME": (
                "TIME",
                days.astype("float64"),
                {
                    "standard_name": "time",
                    "units": TIME_UNITS,
                    "axis": "T",
                    "long_name": "time of measurement",
                    "calendar": "gregorian",
                },
            ),
            "DEPTH": (
                "DEPTH",
                np.asarray(levels).astype("float32"),
                {
                    "standard_name": "depth",
                    "units": "m",
                    "positive": "down",
                    "axis": "Z",
                    "reference": "sea_level",
                    "coordinate_reference_frame": "urn:ogc:def:crs:EPSG::5831",
                    "long_name": "Depth of measurement",
                    "comment": level_comment,
                },
            ),
            "LATITUDE": (
                "LATITUDE",
                np.array([lat]),
                {
                    "standard_name": "latitude",
                    "units": "degrees_north",
                    "axis": "Y",
                    "long_name": "latitude of measurement",
                    "reference": "WGS84",
                    "coordinate_reference_frame": "urn:ogc:def:crs:EPSG::4326",
                },
            ),
            "LONGITUDE": (
                "LONGITUDE",
                np.array([lon]),
                {
                    "standard_name": "longitude",
                    "units": "degrees_east",
                    "axis": "X",
                    "long_name": "longitude of measurement",
                    "reference": "WGS84",
                    "coordinate_reference_frame": "urn:ogc:def:crs:EPSG::4326",
                },
            ),
        }
    )
    # TIME is stored as numbers already in the manual's units, so xarray must not
    # re-encode it as datetime; no coordinate may carry a fill value (manual §2.4).
    for name in ("TIME", "DEPTH", "LATITUDE", "LONGITUDE"):
        out[name].encoding = {"_FillValue": None}
    out["TIME"].encoding["dtype"] = "float64"
    return out


def _globals(
    out: xr.Dataset,
    mooring: Mapping,
    array: Mapping,
    *,
    file_id: str,
    title: str,
    data_mode: str,
    lat: float,
    lon: float,
    history: str,
    note: str,
) -> dict[str, Any]:
    """Return the §2.2 global attributes shared by every file this module writes."""
    now = dt.datetime.now(dt.UTC)
    history = history.rstrip()
    history += ("\n" if history else "") + f"{_iso(now)} {note}"
    times = out["TIME"].values
    attrs: dict[str, Any] = {
        "title": title,
        "platform_code": platform_code(mooring),
        "data_mode": data_mode,
        "id": file_id,
        "naming_authority": "OceanSITES",
        "data_type": "OceanSITES time-series data",
        "format_version": FORMAT_VERSION,
        "Conventions": CONVENTIONS,
        "cdm_data_type": "Station",
        "featureType": "timeSeries",
        "source": "subsurface mooring",
        "geospatial_lat_min": lat,
        "geospatial_lat_max": lat,
        "geospatial_lon_min": lon,
        "geospatial_lon_max": lon,
        "geospatial_vertical_min": float(out["DEPTH"].values.min()),
        "geospatial_vertical_max": float(out["DEPTH"].values.max()),
        "geospatial_vertical_positive": "down",
        "geospatial_vertical_units": "meter",
        "time_coverage_start": _iso(
            _EPOCH + np.timedelta64(int(round(times[0] * 864e11)), "ns")
        ),
        "time_coverage_end": _iso(
            _EPOCH + np.timedelta64(int(round(times[-1] * 864e11)), "ns")
        ),
        "date_created": _iso(now),
        "history": history,
    }
    if mooring.get("waterdepth") is not None:
        attrs["sea_floor_depth_below_sea_surface"] = float(mooring["waterdepth"])
    if array.get("site_code"):
        attrs["site_code"] = str(array["site_code"])
    else:
        # site_code is required by the GDAC and approved by the OceanSITES Project
        # Office; it is never invented here.
        logger.warning("%s: no site_code in the array description; left out", file_id)
    if array.get("project"):
        attrs["project"] = str(array["project"])
    for when in ("deployment", "recovery"):
        if mooring.get(f"{when}_time"):
            attrs[f"platform_{when}_date"] = _iso(str(mooring[f"{when}_time"]))
        ship = _first(mooring, f"{when}_ship", "ship")
        cruise = _first(mooring, f"{when}_cruise", "cruise")
        if ship:
            attrs[f"platform_{when}_ship_name"] = ship
        if cruise:
            attrs[f"platform_{when}_cruise_name"] = cruise
    return attrs


def _source_name(ds: xr.Dataset, given: str | None, fallback: str) -> str:
    """Return the name of the file *ds* came from, for ``history``."""
    return given or Path(str(ds.encoding.get("source", ""))).name or fallback


def to_oceansites(
    ds: xr.Dataset,
    mooring: Mapping,
    *,
    array: Mapping | None = None,
    stack: xr.Dataset | None = None,
    source_file: str | None = None,
) -> xr.Dataset:
    """Return a stage-3 or grid dataset as an OceanSITES 1.4 file.

    A stage-3 dataset (one instrument, a ``time`` dimension) becomes a deployment
    data file (manual §4.1); a grid (``time`` × ``pressure``) becomes a gridded
    file (§4.2, content type ``GRD``).

    Parameters
    ----------
    ds : xarray.Dataset
        A stage-3 dataset for one instrument, or a mooring's grid.
    mooring : Mapping
        The loaded mooring description; supplies ``platform_code``,
        ``deployment_code`` (default: ``YYYYMM`` of ``deployment_time``), the
        position, the ships and cruises, and each variable's data mode.
    array : Mapping or None, optional
        The array description; ``site_code`` and ``project`` are taken from it
        when present.  Without ``site_code`` the attribute is left out.
    stack : xarray.Dataset or None, optional
        For a grid: the stack it was made from, whose instruments are listed in
        ``history`` as the grid's source files (§4.2.1).
    source_file : str or None, optional
        Name of the input file, recorded in ``history``; defaults to the file the
        dataset was opened from.

    Returns
    -------
    xarray.Dataset
        Dimensions ``TIME``, ``DEPTH``, ``LATITUDE``, ``LONGITUDE``; each
        parameter on ``(TIME, DEPTH)``; ``id`` is the file name without ``.nc``.
        A deployment file carries ``<PARAM>_QC`` and a data mode per variable.

    Raises
    ------
    OceanSitesError
        If ``platform_code`` is missing or holds ``_``, the deployment code is
        not ``YYYYMM``, a data mode is invalid or unearned, or the instrument has
        no type code.
    """
    if "pressure" in ds.dims:
        return _grid_file(ds, mooring, array or {}, stack, source_file)
    return _deployment_file(ds, mooring, array or {}, source_file)


def _deployment_file(
    ds: xr.Dataset, mooring: Mapping, array: Mapping, source_file: str | None
) -> xr.Dataset:
    """Return one instrument's stage-3 dataset as a deployment data file (§4.1)."""
    platform = platform_code(mooring)
    deployment = deployment_code(mooring)
    kind = type_code(ds)
    serial = safe_serial(ds.attrs.get("serial", ""))
    if not serial or "_" in serial:
        raise OceanSitesError(f"serial {serial!r} cannot form a file-name field")
    depth = _nominal_depth(ds)
    lat, lon = _position(mooring)
    profiler = kind == "ADCP"
    if profiler:
        levels = _bin_depths(ds, depth)
        level_dims = ("time", ADCP_BIN_DIM)
        level_comment = (
            "nominal bin depth: nominal transducer depth (water depth minus height above "
            f"bottom, from the mooring description) {'plus' if levels[-1] > depth else 'minus'} "
            "the distance from the transducer to each bin; PRES gives the pressure at "
            "each bin in time, including knock-down"
        )
        # The bin pressure is the ADCP's pressure on the bins; the transducer's own
        # pressure and temperature are not on DEPTH and stay in the stage-3 file.
        sources = {"bin_pressure": "pressure"}
    else:
        levels = np.array([depth])
        level_dims = ("time",)
        level_comment = (
            "nominal instrument depth: water depth minus height above bottom, from the "
            "mooring description; PRES gives the measured (or interpolated) pressure "
            "in time, including knock-down"
        )
        sources = {}
    exported = {
        sources.get(v, v): v
        for v in ds.data_vars
        if sources.get(v, v) in PARAMETER_CODES and ds[v].dims == level_dims
    }
    names = [n for n in PARAMETER_CODES if n in exported]
    modes = _data_modes(ds, _entry(mooring, serial), names)
    distinct = sorted(set(modes.values()))
    data_mode = distinct[0] if len(distinct) == 1 else "M"

    times = ds["time"].values.astype("datetime64[ns]")
    start, end = _iso(times[0]), _iso(times[-1])
    out = _coordinates(times, levels, level_comment, lat, lon)

    for name in names:
        code = PARAMETER_CODES[name]
        src = ds[exported[name]]
        attrs = {k: src.attrs[k] for k in _KEPT_ATTRS if k in src.attrs}
        long_name = str(attrs.get("long_name", name))
        attrs.update(
            {
                "coordinates": "TIME DEPTH LATITUDE LONGITUDE",
                "ancillary_variables": f"{code}_QC",
                "DM_indicator": modes[name],
                "processing_level": "Ranges applied, bad data flagged",
            }
        )
        attrs.update(_sensor_attrs(ds, name, start, end))
        if name == "pressure" and "pressure_source" in src.attrs:
            attrs["comment"] = f"pressure source: {src.attrs['pressure_source']}"
        elif src.name == "bin_pressure":
            attrs.setdefault("standard_name", "sea_water_pressure")
            attrs["comment"] = (
                f"pressure at each bin: {src.attrs.get('comment', '')}".rstrip(": ")
            )
        if profiler:
            attrs["sensor_orientation"] = "downward" if levels[-1] > depth else "upward"
        values = _as_levels(src).astype("float64")
        out[code] = xr.DataArray(values, dims=("TIME", "DEPTH"), attrs=attrs)
        # The bin pressure is computed from the transducer pressure, so it carries
        # the transducer's flags (stage 3 keeps no bin_pressure_qc).
        flag_source = "pressure" if src.name == "bin_pressure" else exported[name]
        out[f"{code}_QC"] = _qc_variable(_flags(ds, flag_source, values), long_name)

    if profiler and "pressure" in ds.data_vars and ds["pressure"].dims == ("time",):
        # The transducer's measured pressure: the record of knock-down that the bin
        # pressures are computed from.  It is at no bin, so it is on TIME only, with
        # the transducer's nominal depth as sensor_depth (as GDAC files carry it).
        src = ds["pressure"]
        values = np.asarray(src.values, dtype="float64")
        out["TRANSDUCER_PRES"] = xr.DataArray(
            values,
            dims=("TIME",),
            attrs={
                "standard_name": "sea_water_pressure",
                "units": "dbar",
                **_sensor_attrs(ds, "pressure", start, end),
                "long_name": "sea water pressure at the ADCP transducer",
                "coordinates": "TIME LATITUDE LONGITUDE",
                "sensor_depth": depth,
                "sensor_orientation": "downward" if levels[-1] > depth else "upward",
                "ancillary_variables": "TRANSDUCER_PRES_QC",
                "DM_indicator": modes.get("pressure", "P"),
                "comment": (
                    "measured at the transducer; PRES (per bin) is computed from it"
                ),
            },
        )
        flags = _flags(ds, "pressure", values[:, None])[:, 0]
        qc = _qc_variable(flags[:, None], "sea water pressure at the ADCP transducer")
        out["TRANSDUCER_PRES_QC"] = xr.DataArray(
            flags,
            dims=("TIME",),
            attrs={**qc.attrs, "coordinates": "TIME LATITUDE LONGITUDE"},
        )

    file_id = f"OS_{platform}_{deployment}_{data_mode}_{kind}-{serial}-{round(depth)}m"
    source = _source_name(ds, source_file, "stage-3 dataset")
    out.attrs = _globals(
        out,
        mooring,
        array,
        file_id=file_id,
        title=f"{platform} deployment {deployment}: {kind} {serial} at {round(depth)} m",
        data_mode=data_mode,
        lat=lat,
        lon=lon,
        history=str(ds.attrs.get("history", "")),
        note=f"OceanSITES {FORMAT_VERSION} deployment file written by oceanarray from {source}",
    )
    if "sample_interval_seconds" in ds.attrs:
        out.attrs["time_coverage_resolution"] = _iso_duration(
            ds.attrs["sample_interval_seconds"]
        )
    return out


def _stack_sources(stack: xr.Dataset | None, mooring: Mapping) -> str:
    """Return the stage files a stack was built from, for a gridded file's ``history``."""
    if stack is None or "serial" not in stack.variables:
        return "the mooring's stack (source files not given)"
    name = mooring.get("name", "")
    stages = (
        [str(s) for s in stack["source_stage"].values]
        if "source_stage" in stack.variables
        else ["3"] * stack.sizes.get("N_LEVELS", 0)
    )
    files = sorted(
        {
            f"{name}_{safe_serial(str(s).split('_')[0])}_stage{stage}.nc"
            for s, stage in zip(stack["serial"].values, stages, strict=False)
        }
    )
    return ", ".join(files)


def _grid_file(
    ds: xr.Dataset,
    mooring: Mapping,
    array: Mapping,
    stack: xr.Dataset | None,
    source_file: str | None,
) -> xr.Dataset:
    """Return a mooring's grid as a gridded file (§4.2, ``GRD``)."""
    import gsw

    platform = platform_code(mooring)
    deployment = deployment_code(mooring)
    lat, lon = _position(mooring)
    pressure = np.asarray(ds["pressure"].values, dtype="float64")
    # The data stay on the pressure levels; DEPTH is those levels as depths, not a
    # re-interpolation.
    levels = -gsw.z_from_p(pressure, lat)
    level_comment = (
        f"depth of each grid pressure level, gsw.z_from_p at latitude {lat:.4f}; the "
        "data are on the pressure levels (PRES), not re-interpolated onto depth"
    )
    times = ds["time"].values.astype("datetime64[ns]")
    out = _coordinates(times, levels, level_comment, lat, lon)
    out["PRES"] = xr.DataArray(
        pressure.astype("float32"),
        dims=("DEPTH",),
        attrs={
            "standard_name": "sea_water_pressure",
            "units": "dbar",
            "long_name": "pressure of the grid level",
            "comment": "the pressure levels the grid is interpolated onto",
        },
    )
    # A grid value is built from several instruments; it is P while any of them is
    # (no stage-3 file can be D before the calibration-dip correction exists).
    data_mode = str(ds.attrs.get("data_mode", "P"))
    if data_mode != "P":
        # The grid does not record its inputs' data modes, so it cannot apply the
        # least-final-mode rule; until it does, only an all-P grid is exported.
        raise OceanSitesError(
            f"grid data_mode {data_mode!r}: a grid records no per-input data modes, "
            "so only a provisional (P) grid can be exported"
        )
    for name in PARAMETER_CODES:
        if name not in ds.data_vars or ds[name].dims != ("time", "pressure"):
            continue
        code = PARAMETER_CODES[name]
        attrs = {k: ds[name].attrs[k] for k in _KEPT_ATTRS if k in ds[name].attrs}
        attrs.update(
            {
                "coordinates": "TIME DEPTH LATITUDE LONGITUDE",
                "DM_indicator": data_mode,
                "cell_methods": "TIME: point DEPTH: point",
                "processing_level": "Data interpolated",
            }
        )
        out[code] = xr.DataArray(
            np.asarray(ds[name].values, dtype="float64"),
            dims=("TIME", "DEPTH"),
            attrs=attrs,
        )

    interval = (
        _iso_duration(float(ds.attrs.get("dt_seconds", np.nan)))
        if "dt_seconds" in ds.attrs
        else None
    )
    step = ds.attrs.get("dp_dbar")
    partx = "-".join(
        p for p in (interval, f"DP{float(step):g}" if step is not None else None) if p
    )
    span = f"{pd.Timestamp(times[0]):%Y%m%d}-{pd.Timestamp(times[-1]):%Y%m%d}"
    file_id = f"OS_{platform}-{deployment}_{span}_GRD" + (f"_{partx}" if partx else "")
    source = _source_name(ds, source_file, "grid dataset")
    out.attrs = _globals(
        out,
        mooring,
        array,
        file_id=file_id,
        title=f"{platform} deployment {deployment}: gridded in time and pressure",
        data_mode=data_mode,
        lat=lat,
        lon=lon,
        history=str(ds.attrs.get("history", "")),
        note=(
            f"OceanSITES {FORMAT_VERSION} gridded file written by oceanarray from "
            f"{source}; source files: {_stack_sources(stack, mooring)}"
        ),
    )
    if interval:
        out.attrs["time_coverage_resolution"] = interval
    return out


def oceansites_filename(ds: xr.Dataset) -> str:
    """Return the file name of a dataset made by :func:`to_oceansites` (its ``id`` + ``.nc``)."""
    return f"{ds.attrs['id']}.nc"
