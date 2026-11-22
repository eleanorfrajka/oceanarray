"""Sensor records: one OG1-style ``SENSOR_<type>_<serial>`` variable per instrument.

Each instrument is described by one dimensionless variable, as the OG1 glider
format does: ``sensor_type`` (from :data:`SENSOR_TYPES`), ``sensor_maker``,
``sensor_model``, ``sensor_serial_number`` and ``sensor_calibration_date``, and
every data variable names its record in a ``sensor`` attribute. An instrument
whose sensors were calibrated on different dates (a MicroCAT's temperature,
conductivity and pressure) keeps one record: ``sensor_calibration_date`` is the
latest, ``sensor_calibration_dates`` lists each (``"temperature: 2018-04-12;
conductivity: 2018-04-10"``), and each sensor's coefficients are a
``<sensor>_calibration_coefficients`` attribute. Files that join deployments
(the Stack cat) end each record name with the deployment code (``_201806``), so
an instrument recalibrated between deployments has a record per deployment.

The originator's own ``sensor_*`` attributes (an imported OceanSITES file) are
kept verbatim on the record as ``originator_sensor_*``, and its other variable
attributes (``comment``, ``uncertainty``, ``accuracy`` ...) as
``originator_<variable>_<attribute>`` with ``originator_<variable>_units``; the
data variables keep only oceanarray's attributes. A ``sensor_model`` in the
instrument's mooring-YAML entry, with its ``sensor_model_source``, fills or
corrects the model; nothing is inferred from the data.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import numpy as np
import xarray as xr

from oceanarray.exceptions import MooringError

log = logging.getLogger(__name__)

#: The OG1 sensor-type word for each instrument type. Every MicroCAT is a CTD,
#: with or without a pressure sensor. Types not listed use the instrument type in
#: capitals until a term is chosen.
SENSOR_TYPES: dict[str, str] = {"microcat": "CTD"}

#: The vocabulary the sensor-type word comes from (NERC L05, as OG1 uses).
SENSOR_TYPE_VOCABULARY = "https://vocab.nerc.ac.uk/collection/L05/current/"

#: The per-sensor calibration variables stage 1 reads from a raw Sea-Bird header,
#: ``SENSOR_<code>_<serial>``, and the sensor each names.
_CALIBRATED = {"TEMP": "temperature", "CNDC": "conductivity", "PRES": "pressure"}

_RECORD = re.compile(r"^SENSOR_")


def sensor_type(instrument: str) -> str:
    """Return the sensor-type word for an instrument type.

    Parameters
    ----------
    instrument : str
        The instrument entry's ``instrument`` (``microcat``, ``aquadopp``, ...).

    Returns
    -------
    str
        ``CTD`` for a MicroCAT; otherwise the type in capitals, without characters
        a variable name cannot hold.
    """
    key = str(instrument).strip().lower()
    return SENSOR_TYPES.get(key) or re.sub(r"\W+", "_", key.upper()).strip("_") or "UNK"


def record_name(
    instrument: str, serial: str, deployment_code: str | None = None
) -> str:
    """Return the record variable's name: ``SENSOR_<type>_<serial>[_<deployment code>]``.

    Parameters
    ----------
    instrument : str
        The instrument type.
    serial : str
        The instrument's (cleaned) serial.
    deployment_code : str, optional
        ``YYYYMM`` of the deployment, for files that join deployments.

    Returns
    -------
    str
        The variable name.
    """
    name = f"SENSOR_{sensor_type(instrument)}_{serial}"
    return f"{name}_{deployment_code}" if deployment_code else name


def is_record(name: object) -> bool:
    """Return whether *name* is a sensor-record variable.

    Parameters
    ----------
    name : object
        A variable name.

    Returns
    -------
    bool
        True for a ``SENSOR_*`` name.
    """
    return bool(_RECORD.match(str(name)))


def _iso(value: object) -> str | None:
    """Return *value* if it is an ISO date (``YYYY-MM-DD``), else None."""
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _originator_sensor_attrs(ds: xr.Dataset) -> dict[str, str]:
    """Return the originator's ``sensor_*`` variable attributes, one value per key.

    Values that differ between variables are joined with ``"; "`` in variable order.
    """
    found: dict[str, list[str]] = {}
    for var in ds.data_vars.values():
        for key, value in var.attrs.items():
            if key.startswith("originator_sensor_"):
                text = str(value).strip()
                if text and text not in found.setdefault(key, []):
                    found[key].append(text)
    return {key: "; ".join(values) for key, values in found.items()}


def _originator_variable_attrs(ds: xr.Dataset) -> dict[str, Any]:
    """Return the originator's other variable attributes as record keys.

    Each ``originator_<attribute>`` on a data variable (other than
    ``originator_sensor_*``, which :func:`_originator_sensor_attrs` joins) becomes
    ``originator_<variable>_<attribute>``, with ``originator_<variable>_units`` (the
    variable's units as read) so a number such as an uncertainty keeps its unit.
    """
    out: dict[str, Any] = {}
    for name, var in ds.data_vars.items():
        if not _links(var):
            continue
        own = {
            k[len("originator_") :]: v
            for k, v in var.attrs.items()
            if k.startswith("originator_") and not k.startswith("originator_sensor_")
        }
        if not own:
            continue
        for key, value in own.items():
            out[f"originator_{name}_{key}"] = value
        if var.attrs.get("units"):
            out[f"originator_{name}_units"] = str(var.attrs["units"])
    return out


def build_record(
    ds: xr.Dataset,
    entry: Mapping[str, Any],
    serial: str,
    *,
    maker: str | None = None,
    catalog: Mapping[str, Any] | None = None,
) -> xr.Dataset:
    """Replace a stage-1 dataset's sensor variables with its one record, and link to it.

    ``sensor_model`` comes from the instrument's entry, else the sensor
    catalog, else the file; each replacement of the file's value is a WARNING.

    Parameters
    ----------
    ds : xarray.Dataset
        The instrument's dataset at stage 1: any per-sensor calibration variables
        (``SENSOR_TEMP_<serial>`` ... from a raw Sea-Bird header) and any
        ``originator_sensor_*`` variable attributes (an imported file).
    entry : Mapping
        The instrument's mooring-YAML entry (``instrument``; optional
        ``sensor_model`` and ``sensor_model_source``).
    serial : str
        The instrument's cleaned serial.
    maker : str, optional
        The maker when the file format says it (a Sea-Bird file).
    catalog : Mapping, optional
        What the sensor catalog says of this instrument
        (:func:`oceanarray.catalog.lookup`). Its model's attributes and
        vocabularies are written, the "with pressure" entry when the instrument
        measured pressure (:func:`oceanarray.catalog.measured_pressure`).

    Returns
    -------
    xarray.Dataset
        *ds* with one ``SENSOR_<type>_<serial>`` variable, the per-sensor variables
        removed, and ``sensor`` on every data variable on ``time``.

    Raises
    ------
    oceanarray.exceptions.MooringError
        When *serial* is empty: two such instruments would share one record name.
    """
    if not str(serial or "").strip():
        raise MooringError(
            f"{entry.get('instrument', 'an instrument')}: no serial; a sensor record "
            "is named by the instrument's serial"
        )
    name = record_name(entry.get("instrument", ""), serial)
    attrs: dict[str, str] = {
        "long_name": "",
        "sensor_type": sensor_type(entry.get("instrument", "")),
        "sensor_type_vocabulary": SENSOR_TYPE_VOCABULARY,
        "sensor_serial_number": serial,
    }
    dates: dict[str, str] = {}
    old = [v for v in ds.variables if is_record(v)]
    for var in old:
        meta = ds[var].attrs
        code = str(var).split("_")[1] if str(var).count("_") >= 2 else ""
        sensor = _CALIBRATED.get(code)
        if sensor is None:
            continue
        date = str(meta.get("sensor_calibration_date") or "").strip()
        if date and date != "—":
            # Kept as written when it is not an ISO date (only ISO dates are compared).
            dates[sensor] = date
        # The coefficients named for this sensor (TEMPERATURE_calibration_...),
        # never another sensor's that the variable might also carry.
        coefficients = next(
            (
                v
                for k, v in meta.items()
                if k.lower() == f"{sensor}_calibration_coefficients"
            ),
            "",
        )
        if coefficients:
            attrs[f"{sensor}_calibration_coefficients"] = str(coefficients)
        own_serial = str(meta.get("sensor_serial_number", "") or "")
        if own_serial and own_serial != serial:
            attrs[f"{sensor}_sensor_serial_number"] = own_serial
    iso_dates = [d for d in dates.values() if _iso(d)]
    if iso_dates:
        attrs["sensor_calibration_date"] = max(iso_dates)
    if dates:
        attrs["sensor_calibration_dates"] = "; ".join(
            f"{k}: {v}" for k, v in dates.items()
        )

    originator = _originator_sensor_attrs(ds)
    attrs.update(originator)
    attrs.update(_originator_variable_attrs(ds))
    file_model = originator.get("originator_sensor_model", "")
    file_maker = originator.get("originator_sensor_manufacturer", "") or originator.get(
        "originator_sensor_maker", ""
    )
    cat: dict[str, str] = {}
    if catalog:
        from oceanarray import catalog as _catalog

        measured, why = _catalog.measured_pressure(ds)
        cat = _catalog.record_attrs(catalog, measured)
        if "pressure" in ds.data_vars and not measured:
            log.warning(
                f"  {name}: described without pressure by the sensor catalog: {why}"
            )
    yaml_model = str(entry.get("sensor_model") or "").strip()
    if yaml_model:
        if file_model and file_model != yaml_model:
            log.warning(
                f"  {name}: the mooring YAML's sensor_model {yaml_model!r} replaces the "
                f"file's {file_model!r} (kept as originator_sensor_model)"
            )
        if cat.get("sensor_model") and cat["sensor_model"] != yaml_model:
            log.warning(
                f"  {name}: the mooring YAML's sensor_model {yaml_model!r} is used, "
                f"not the sensor catalog's {cat['sensor_model']!r}"
            )
        attrs["sensor_model"] = yaml_model
        attrs["sensor_model_source"] = str(entry.get("sensor_model_source") or "")
        attrs["sensor_metadata_source"] = (
            f"mooring YAML: {attrs['sensor_model_source']}"
        )
    elif cat.get("sensor_model"):
        if file_model and file_model != cat["sensor_model"]:
            log.warning(
                f"  {name}: the sensor catalog's sensor_model {cat['sensor_model']!r} "
                f"replaces the file's {file_model!r} (kept as originator_sensor_model)"
            )
        attrs.update(cat)
        attrs["sensor_metadata_source"] = (
            f"sensor catalog: {catalog['catalog_file']} ({catalog['catalog_key']}, "
            f"{why})"
        )
    elif file_model:
        attrs["sensor_model"] = file_model
        attrs["sensor_metadata_source"] = "originator file"
    else:
        attrs["sensor_model"] = "UNK"
        attrs["sensor_metadata_source"] = (
            "raw file header (calibration only)" if dates else "none"
        )
    if "sensor_pumped" in cat:
        # Known even when the model is not ("SBE 37-SM or -SMP").
        attrs["sensor_pumped"] = cat["sensor_pumped"]
    if (maker or file_maker) and "sensor_maker" not in attrs:
        attrs["sensor_maker"] = maker or file_maker
    if not attrs.get("long_name"):
        attrs["long_name"] = " ".join(
            p
            for p in (
                attrs.get("sensor_maker", ""),
                attrs["sensor_model"],
                f"s/n {serial}",
            )
            if p and p != "UNK"
        )
    attrs["cf_role"] = "sensor_id"
    attrs["coverage_content_type"] = "auxiliaryInformation"

    # A shallow copy: the attributes removed below are not removed from the caller's.
    ds = ds.drop_vars(old).copy()
    # The originator's descriptions now live on the record; the variables keep ours.
    for var in ds.data_vars.values():
        if _links(var):
            for key in [k for k in var.attrs if str(k).startswith("originator_")]:
                del var.attrs[key]
    ds[name] = xr.Variable(
        (), np.int8(0), attrs={k: v for k, v in attrs.items() if v != ""}
    )
    for var in ds.data_vars.values():
        if _links(var):
            var.attrs["sensor"] = name
    return ds


def records(ds: xr.Dataset) -> dict[str, dict[str, Any]]:
    """Return a dataset's sensor records.

    Parameters
    ----------
    ds : xarray.Dataset
        A stage file, stack or Stack cat.

    Returns
    -------
    dict of str to dict
        ``{variable name: attributes}`` for each dimensionless ``SENSOR_*`` variable.
    """
    return {
        str(v): dict(ds[v].attrs)
        for v in ds.variables
        if is_record(v) and ds[v].ndim == 0
    }


def _links(var: xr.DataArray) -> bool:
    """Return whether *var* is a data variable that names its sensor record."""
    return "time" in var.dims and not str(var.name).endswith(
        ("_qc", "_qc_originator", "_qc_originator_mapped", "_flag")
    )


def _borrowed(var: xr.DataArray) -> bool:
    """Return whether *var* came from other instruments (``*_source_serials``)."""
    return any(str(k).endswith("_source_serials") for k in var.attrs)


def record_of(ds: xr.Dataset, name: str) -> dict[str, Any]:
    """Return the attributes of the sensor record that measured variable *name*.

    Parameters
    ----------
    ds : xarray.Dataset
        A single-instrument file (stage 1-3).
    name : str
        A data variable.

    Returns
    -------
    dict
        The record its ``sensor`` attribute names; else, when the file holds exactly
        one record and the variable was not taken from other instruments, that
        record (a variable derived after stage 1); else ``{}``.
    """
    own = records(ds)
    linked = str(ds[name].attrs.get("sensor", "")) if name in ds else ""
    if linked in own:
        return own[linked]
    if len(own) != 1 or (name in ds and _borrowed(ds[name])):
        return {}
    return next(iter(own.values()))


def link_derived(ds: xr.Dataset) -> xr.Dataset:
    """Name the file's one sensor record on data variables derived after stage 1.

    Salinity, ENU velocities and the other variables stage 3 makes come from the
    instrument's own measurements. A variable taken from other instruments (pressure
    interpolated from its neighbors, marked by a ``*_source_serials`` attribute) is
    not linked, and a file with several records is left as it is.

    Parameters
    ----------
    ds : xarray.Dataset
        A single-instrument file.

    Returns
    -------
    xarray.Dataset
        *ds*, with ``sensor`` on every time-series data variable that lacked it.
    """
    own = list(records(ds))
    if len(own) != 1:
        return ds
    for var in ds.data_vars.values():
        if _links(var) and "sensor" not in var.attrs and not _borrowed(var):
            var.attrs["sensor"] = own[0]
    return ds


def sensor_record(ds: xr.Dataset, instrument: str, serial: str) -> tuple[str, dict]:
    """Return the name and attributes of an instrument's one sensor record.

    A stage file written with sensor records names its record in its data
    variables' ``sensor``; one written before them (per-sensor ``SENSOR_TEMP_`` /
    ``_CNDC_`` / ``_PRES_<serial>`` variables, or none) gets its record built here,
    so every calibration it holds is kept.

    Parameters
    ----------
    ds : xarray.Dataset
        The instrument's stage file.
    instrument : str
        The instrument type.
    serial : str
        The instrument's cleaned serial.

    Returns
    -------
    tuple of (str, dict)
        The record's variable name and its attributes.
    """
    own = records(ds)
    linked = {
        str(v.attrs["sensor"]) for v in ds.data_vars.values() if "sensor" in v.attrs
    }
    for name in linked:
        if name in own:
            return name, own[name]
    built = build_record(ds, {"instrument": instrument}, serial)
    name = record_name(instrument, serial)
    return name, dict(built[name].attrs)


def calibration_rows(record: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return one row per calibrated sensor of a record: sensor, serial, date, coefficients.

    Parameters
    ----------
    record : Mapping
        A record's attributes.

    Returns
    -------
    list of dict of str to str
        ``sensor``, ``serial``, ``date`` and ``coefficients`` per sensor named in
        ``sensor_calibration_dates`` or with ``<sensor>_calibration_coefficients``.
    """
    dates = dict(
        part.split(": ", 1)
        for part in str(record.get("sensor_calibration_dates", "")).split("; ")
        if ": " in part
    )
    sensors = list(dates)
    for key in record:
        if key.endswith("_calibration_coefficients"):
            sensor = key.removesuffix("_calibration_coefficients")
            if sensor not in sensors:
                sensors.append(sensor)
    serial = str(record.get("sensor_serial_number", ""))
    return [
        {
            "sensor": sensor,
            "serial": str(record.get(f"{sensor}_sensor_serial_number", serial)),
            "date": dates.get(sensor, "—"),
            "coefficients": str(record.get(f"{sensor}_calibration_coefficients", "")),
        }
        for sensor in sensors
    ]
