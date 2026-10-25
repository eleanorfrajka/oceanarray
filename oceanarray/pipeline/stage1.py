"""Stage 1: raw instrument data → CF-NetCDF, faithful to the raw values.

:func:`stage1` takes raw datasets as :func:`~oceanarray.pipeline.read_mooring.read_mooring`
returns them (``stage=None``) and applies stage 1's transforms; it reads and writes no
files. :class:`MooringProcessor` reads each instrument's raw file, calls
:func:`stage1`, and writes ``<name>_<serial>_stage1.nc``.
"""

import logging
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray import parameters as params
from oceanarray import paths
from oceanarray.exceptions import MooringError
from oceanarray.logger import log_info, log_warning, with_stage_handlers
from oceanarray.paths import safe_serial
from oceanarray.pipeline import _stage
from oceanarray.pipeline.raw_files import read_raw_file, resolve_raw_file
from oceanarray.pipeline.read_mooring import join_raw
from oceanarray.pipeline.units import UnitsError, to_canonical_units
from oceanarray.utilities import (
    _status,
    extract_inline_instruments,
    instrument_entries,
    should_skip_regeneration,
)
from oceanarray.writers import write
from oceanarray.writers.netcdf import as_stored

# Suppress noisy INFO/WARNING messages from seasenselib/pycnv.
logging.getLogger("seasenselib").setLevel(logging.WARNING)
logging.getLogger("seasenselib.pipeline.derivation").setLevel(logging.ERROR)
logging.getLogger("pycnv").setLevel(logging.WARNING)


# Re-export for backward compatibility; canonical definition is in parameters.py.
ADCP_BIN_DIM: str = params.ADCP_BIN_DIM

# Variables and coordinates removed for specific file types.
VARS_TO_REMOVE = {
    "sbe-cnv": ["potential_temperature", "julian_days_offset", "density"],
    "sbe-ascii": ["potential_temperature", "julian_days_offset", "density"],
}
COORDS_TO_REMOVE = {
    "sbe-cnv": ["depth", "latitude", "longitude"],
    "sbe-ascii": ["depth", "latitude", "longitude"],
}

_NORTEK_TYPES = ("nortek-raw", "nortek-ascii", "nortek-csv")


def _log(message: str) -> None:
    """Log a stage-1 message, as a warning when it starts with ``WARNING``."""
    if str(message).lstrip().startswith("WARNING"):
        log_warning("%s", message)
    else:
        log_info("%s", message)


def _normalize_conductivity(dataset: xr.Dataset) -> xr.Dataset:
    """Rename the raw SBE CNV conductivity column to ``conductivity``.

    The raw column name encodes the unit (``cond0S/m`` or ``cond0mS/cm``);
    both are NetCDF-illegal.  This fixes only the name and records the unit
    in ``units`` (``S/m`` or ``mS/cm``) — the S/m → mS cm-1 ×10 conversion and
    the canonical spelling are applied later by
    :func:`oceanarray.pipeline.units.to_canonical_units`, the one place units
    are normalised.
    """
    for raw_name, raw_units in (("cond0S/m", "S/m"), ("cond0mS/cm", "mS/cm")):
        if raw_name in dataset:
            data = dataset[raw_name]
            data.attrs["units"] = raw_units
            dataset = dataset.drop_vars(raw_name)
            dataset["conductivity"] = data
            break
    return dataset


# Known SBE CNV variable names containing '/' and their CF-compatible replacements.
# Keys that are already handled by _normalize_conductivity are excluded.
# SBE's ``Mm/Kg`` suffix is *micro*moles per kg (umol kg-1), not millimoles — so
# the ``Mm/Kg`` columns map to the per-mass ``dissolved_oxygen`` (canonical
# umol kg-1) with no scaling, and the ``ML/L`` columns (mL/L, per volume) map to
# the separate ``dissolved_oxygen_ml_l`` family (canonical mL L-1); routing mL/L
# onto the per-mass variable would mix bases.  Verify the column meanings against
# the SBE Data Processing manual before merge.
_CNV_SLASH_RENAMES: dict[str, str] = {
    "sbeopoxMm/Kg": "dissolved_oxygen",  # SBE63 optode O2, umol/kg
    "sbeox0Mm/Kg": "dissolved_oxygen",  # SBE43 electrochemical O2, umol/kg
    "sbeox0ML/L": "dissolved_oxygen_ml_l",  # SBE43 electrochemical O2, mL/L
    "sbeox1Mm/Kg": "dissolved_oxygen_2",
    "sbeox1ML/L": "dissolved_oxygen_ml_l_2",
    "oxsatMm/Kg": "oxygen_saturation",
    "oxsatML/L": "oxygen_saturation",
}


def _sanitize_slash_vars(dataset: xr.Dataset) -> xr.Dataset:
    """Rename known SBE slash-named variables and warn about any remaining ones."""
    rename_map: dict[str, str] = {}
    for old, new in _CNV_SLASH_RENAMES.items():
        if old in dataset.data_vars:
            # Avoid clobbering a variable that already exists with the target name
            target = new
            if target in dataset.data_vars:
                target = f"{new}_raw"
            rename_map[old] = target

    if rename_map:
        dataset = dataset.rename_vars(rename_map)

    # Fallback: any remaining vars with '/' are not safe for NetCDF — replace with '_per_'
    remaining = [v for v in dataset.data_vars if "/" in v]
    if remaining:
        fallback = {v: v.replace("/", "_per_") for v in remaining}
        _log(
            f"WARNING: Unrecognised variable name(s) containing '/': "
            f"{', '.join(remaining)} — sanitising to: "
            f"{', '.join(fallback.values())}"
        )
        dataset = dataset.rename_vars(fallback)

    return dataset


def _normalize_sensor_var_names(
    dataset: xr.Dataset, instrument_config: dict[str, Any]
) -> xr.Dataset:
    """Rename SENSOR_PRES_{sensor_serial} to SENSOR_PRES_{instrument_serial}.

    seasenselib names the pressure sensor variable after the pressure sensor's
    own serial number (e.g. SENSOR_PRES_2075203), which differs from the
    instrument serial (e.g. 7507).  Rename so all three SENSOR_* variables
    for one instrument share the same serial suffix.
    """
    instr_serial = safe_serial(instrument_config.get("serial", ""))
    if not instr_serial:
        return dataset
    expected = f"SENSOR_PRES_{instr_serial}"
    to_rename = {
        v: expected
        for v in list(dataset.data_vars)
        if v.startswith("SENSOR_PRES_") and v != expected
    }
    if to_rename:
        dataset = dataset.rename_vars(to_rename)
    return dataset


def _apply_units_override(
    dataset: xr.Dataset, instrument_config: dict[str, Any]
) -> xr.Dataset:
    """Replace a variable's units attribute with the one given in the mooring YAML.

    For a file whose ``units`` attribute is known to be wrong (e.g. conductivity
    labelled ``S/m`` with values in mS/cm).  The YAML key is
    ``units_override: {variable: units}`` on the instrument; the stated value is
    kept in ``units_override_from`` so the correction is visible in the output.

    Parameters
    ----------
    dataset : xarray.Dataset
        The dataset as read.
    instrument_config : dict
        The instrument's entry from the mooring YAML.

    Returns
    -------
    xarray.Dataset
        *dataset*, with the overridden units.

    Raises
    ------
    ValueError
        If ``units_override`` names a variable the file does not hold.
    """
    overrides = instrument_config.get("units_override") or {}
    for var, units in overrides.items():
        if var not in dataset.data_vars:
            raise ValueError(
                f"units_override names {var!r}, which the file does not hold "
                f"(variables: {sorted(dataset.data_vars)})"
            )
        attrs = dict(dataset[var].attrs)
        attrs["units_override_from"] = str(attrs.get("units", ""))
        attrs["units"] = str(units)
        dataset[var].attrs = attrs
        _log(
            f"  {var}: units overridden {attrs['units_override_from']!r} -> "
            f"{units!r} (mooring YAML units_override)"
        )
    return dataset


def _check_value_ranges(dataset: xr.Dataset, stated_units: dict[str, Any]) -> None:
    """Refuse a variable whose values contradict its units.

    After the units pass, the median of each registered variable with a
    ``valid_min`` / ``valid_max`` in :data:`parameters.VARIABLES` must fall in
    that range.  A median outside it means the stated units are wrong (a file
    that says ``S/m`` but holds mS/cm becomes ten times too large).

    Parameters
    ----------
    dataset : xarray.Dataset
        The dataset after :func:`to_canonical_units`.
    stated_units : dict
        Each variable's units attribute as it was before the units pass.

    Raises
    ------
    UnitsError
        Naming the variable, its stated units and the remedy.
    """
    for var in dataset.data_vars:
        entry = params.VARIABLES.get(var, {})
        lo, hi = entry.get("valid_min"), entry.get("valid_max")
        if lo is None or hi is None:
            continue
        values = np.asarray(dataset[var].values, dtype=float)
        if not np.isfinite(values).any():
            continue
        median = float(np.nanmedian(values))
        if not lo <= median <= hi:
            raise UnitsError(
                f"{var!r}: median {median:g} {dataset[var].attrs.get('units')} is "
                f"outside the valid range [{lo:g}, {hi:g}]; the file's units "
                f"{stated_units.get(var)!r} look wrong. Set units_override: "
                f"{{{var}: <units>}} on this instrument in the mooring YAML."
            )


def _clean_dataset_variables(dataset: xr.Dataset, file_type: str) -> xr.Dataset:
    """Remove unwanted variables and coordinates for specific file types.

    Removals are driven by ``VARS_TO_REMOVE`` and ``COORDS_TO_REMOVE``, which
    are only populated for SBE file types (``sbe-cnv``, ``sbe-ascii``).
    For Nortek, RBR, and RDI ADCP file types this method is a no-op.
    """
    # Remove variables
    vars_to_remove = VARS_TO_REMOVE.get(file_type, [])
    for var in vars_to_remove:
        if var in dataset.variables:
            _log(f"Removing variable: {var}")
            dataset = dataset.drop_vars(var)

    # Remove coordinates
    coords_to_remove = COORDS_TO_REMOVE.get(file_type, [])
    for coord in coords_to_remove:
        if coord in dataset.coords:
            _log(f"Removing coordinate: {coord}")
            dataset = dataset.drop_vars(coord)

    return dataset


def _add_global_attributes(
    dataset: xr.Dataset, yaml_data: dict[str, Any]
) -> xr.Dataset:
    """Add global attributes from YAML configuration."""
    global_attrs = {
        "mooring_name": yaml_data["name"],
        "waterdepth": yaml_data["waterdepth"],
        "longitude": yaml_data.get("longitude", 0.0),
        "latitude": yaml_data.get("latitude", 0.0),
        "deployment_latitude": yaml_data.get("deployment_latitude", "00 00.000 N"),
        "deployment_longitude": yaml_data.get("deployment_longitude", "000 00.000 W"),
        "deployment_time": yaml_data.get("deployment_time", "YYYY-mm-ddTHH:MM:ss"),
        "seabed_latitude": yaml_data.get("seabed_latitude", "00 00.000 N"),
        "seabed_longitude": yaml_data.get("seabed_longitude", "000 00.000 W"),
        "recovery_time": yaml_data.get("recovery_time", "YYYY-mm-ddTHH:MM:ss"),
    }

    for attr, value in global_attrs.items():
        dataset.attrs[attr] = value

    return dataset


def _add_instrument_metadata(
    dataset: xr.Dataset,
    instrument_config: dict[str, Any],
    yaml_data: dict[str, Any],
) -> xr.Dataset:
    """Add instrument-specific metadata to dataset."""
    dataset["serial_number"] = instrument_config.get("serial", 0)
    # Support both 'depth' (absolute) and 'hab' (height above bottom)
    if "depth" in instrument_config:
        depth = instrument_config["depth"]
    elif "hab" in instrument_config:
        waterdepth = yaml_data.get("waterdepth") or 0
        depth = waterdepth - instrument_config["hab"]
    else:
        depth = 0
    dataset["InstrDepth"] = depth
    dataset["instrument"] = instrument_config.get("instrument", "Unknown")
    dataset["clock_offset"] = instrument_config.get("clock_offset", 0)
    dataset["clock_offset"].attrs["units"] = "s"
    dataset["start_time"] = instrument_config.get(
        "start_time", dataset.attrs["deployment_time"]
    )
    dataset["end_time"] = instrument_config.get(
        "end_time", dataset.attrs["recovery_time"]
    )

    return dataset


def _normalize_rdi_raw(
    dataset: xr.Dataset,
    instrument_config: dict[str, Any],
) -> xr.Dataset:
    """Normalise the dataset produced by the ``rdi-raw`` seasenselib reader.

    The rdi-raw reader (via mhkit/dolfyn) returns:

    - ``vel(dir, range, time)`` with ``dir=['E','N','U','err']`` — already in
      Earth frame because ``coord_sys=earth`` was set in the RDI configuration.
      However, ``magnetic_var_deg=0.0`` is common, meaning **no magnetic
      declination was applied**; stage3 must apply the ppigrf correction.
    - ``amp``, ``corr``, ``prcnt_gd`` with dims ``(beam, range, time)``.
    - ``beam2inst_orientmat(x1, x2)`` — static beam→instrument rotation matrix.

    Steps applied:

    1. Rename the ``range`` spatial dimension to ``ADCP_BIN_DIM`` (``"N_BINS"``).
       The ``range`` coordinate (metres above transducer) is preserved under the
       new dimension name.
    2. Split ``vel`` into ``east_velocity``, ``north_velocity``, ``up_velocity``,
       and ``error_velocity``, each with dims ``(time, N_BINS)``.  Drops ``vel``
       and the ``dir`` coordinate.
    3. Transpose quality variables to ``(time, N_BINS, beam)``.
    4. Flatten ``beam2inst_orientmat`` to global attrs ``beam2inst_M{i}{j}``
       and drop the variable and its ``x1``/``x2`` dimension coordinates.
    5. Transpose ``orientmat`` to ``(time, earth, inst)`` (time-first).
    6. Extract instrument metadata from the ``raw_metadata`` JSON blob:
       cell size, blanking distance, beam angle, pings per ensemble, and
       ``instrument_magnetic_variation_deg``.
    7. Set ``coordinate_system = "ENU"``.
    8. Store ``orientation_instrument`` from the raw file and
       ``orientation_yaml`` from the YAML ``orientation`` key (if present);
       emit a WARNING if they disagree — a correction will be needed in stage3.

    This method is faithful to the raw data: no values are modified or removed.
    Pressure overflow values and other sensor artefacts are preserved as-is;
    flagging is deferred to stage3.

    Args:
        dataset: Dataset as returned by ``seasenselib.read(..., file_type="rdi-raw")``.
        instrument_config: Per-instrument YAML configuration dict.

    Returns
    -------
        Normalised dataset ready for metadata attachment and NetCDF writing.

    """
    import json

    # 1. Rename the spatial bin dimension: range → N_BINS
    if "range" in dataset.dims:
        dataset = dataset.rename_dims({"range": ADCP_BIN_DIM})

    # 2. Split vel(dir, N_BINS, time) into named components (time, N_BINS)
    _DIR_MAP = {
        "E": (
            "east_velocity",
            "East velocity (magnetic ENU; declination correction not applied)",
        ),
        "N": (
            "north_velocity",
            "North velocity (magnetic ENU; declination correction not applied)",
        ),
        "U": ("up_velocity", "Up velocity"),
        "err": ("error_velocity", "Error velocity"),
    }
    _enu_extracted: list[str] = []
    if "vel" in dataset.data_vars:
        vel = dataset["vel"]
        for dir_label, (var_name, long_name) in _DIR_MAP.items():
            if dir_label in vel.dir.values:
                component = vel.sel(dir=dir_label).transpose("time", ADCP_BIN_DIM)
                component.attrs.update({"units": "m s-1", "long_name": long_name})
                dataset[var_name] = component
                _enu_extracted.append(var_name)
        dataset = dataset.drop_vars("vel")
        if "dir" in dataset.coords:
            dataset = dataset.drop_vars("dir")

    # 3. Rename dolfyn quality variable names to oceanarray standard names,
    #    transpose to (time, N_BINS, beam), and set long_name.
    _QUAL_RENAME: dict[str, tuple[str, str]] = {
        "amp": ("amplitude", "Acoustic signal amplitude"),
        "corr": ("correlation", "Acoustic signal correlation"),
        "prcnt_gd": ("percent_good", "Percent good"),
    }
    for dolfyn_name, (std_name, long_name) in _QUAL_RENAME.items():
        if dolfyn_name in dataset.data_vars:
            da = dataset[dolfyn_name].transpose("time", ADCP_BIN_DIM, "beam")
            da.attrs.setdefault("long_name", long_name)
            dataset[std_name] = da
            dataset = dataset.drop_vars(dolfyn_name)

    # Rename c_sound → speed_of_sound (matches Aquadopp convention)
    if "c_sound" in dataset.data_vars:
        dataset = dataset.rename_vars({"c_sound": "speed_of_sound"})

    # 4. Store beam2inst rotation matrix as global attrs; drop variable + dim coords
    if "beam2inst_orientmat" in dataset.data_vars:
        mat = dataset["beam2inst_orientmat"].values
        for _i in range(mat.shape[0]):
            for _j in range(mat.shape[1]):
                dataset.attrs[f"beam2inst_M{_i + 1}{_j + 1}"] = float(mat[_i, _j])
        dataset = dataset.drop_vars("beam2inst_orientmat")
        for _dim_coord in ("x1", "x2"):
            if _dim_coord in dataset.coords:
                dataset = dataset.drop_vars(_dim_coord)

    # 5. Transpose orientmat to (time, earth, inst)
    if "orientmat" in dataset.data_vars:
        dataset["orientmat"] = dataset["orientmat"].transpose("time", "earth", "inst")

    # 6. Extract instrument metadata from the raw_metadata JSON blob
    try:
        _raw_meta = json.loads(dataset.attrs.get("raw_metadata", "{}"))
        _ga = _raw_meta.get("blocks", {}).get("other", {}).get("global_attributes", {})
        for _raw_key, _attr_name in [
            ("cell_size", "cell_size_m"),
            ("blank_dist", "blanking_distance_m"),
            ("beam_angle", "beam_angle_deg"),
            ("n_cells", "n_cells"),
            ("pings_per_ensemble", "n_pings_per_ensemble"),
            ("carrier_freq", "carrier_freq_khz"),
            # Recording-time coordinate frame and tilt bin-mapping (RDI EX command).
            # The Stage-3 ADCP bin-pressure tilt correction reads these: earth + bin
            # mapping means the firmware already tilt-placed the bins, so it must NOT
            # apply cos(tilt) again (see compute_adcp_bin_pressure).
            ("coord_sys", "firmware_coordinate_system"),
            ("bin_mapping", "firmware_bin_mapping"),
        ]:
            if _raw_key in _ga:
                dataset.attrs[_attr_name] = _ga[_raw_key]
        _mag_var = _ga.get("magnetic_var_deg")
        if _mag_var is not None:
            dataset.attrs["instrument_magnetic_variation_deg"] = float(_mag_var)
        _orient_raw = _ga.get("orientation")
        if _orient_raw:
            dataset.attrs["orientation_instrument"] = str(_orient_raw)
    except Exception:  # noqa: BLE001 — raw_metadata absent or malformed; proceed without
        pass

    # 7. Set coordinate system — only mark ENU if at least one ENU component was
    #    extracted.  If the RDI was configured in beam or instrument coords dolfyn
    #    uses different dir labels and _DIR_MAP matches nothing; calling the result
    #    "ENU" would be wrong and would confuse stage3.
    if _enu_extracted:
        dataset.attrs["coordinate_system"] = "ENU"
    else:
        dataset.attrs["coordinate_system"] = "UNK"
        _log(
            f"  WARNING: _normalize_rdi_raw extracted no ENU velocity components "
            f"for {instrument_config.get('serial', 'UNKNOWN')}.  "
            f"The RDI file may use beam or instrument coordinates rather than "
            f"earth frame.  'coordinate_system' set to 'UNK'; stage3 will not "
            f"apply beam→ENU rotation.  Re-process after converting to earth frame."
        )

    # 8. Store YAML orientation and warn on mismatch with raw file
    orientation_yaml = instrument_config.get("orientation")
    if orientation_yaml:
        dataset.attrs["orientation_yaml"] = str(orientation_yaml)
    _orient_instrument = dataset.attrs.get("orientation_instrument", "")
    if (
        orientation_yaml
        and _orient_instrument
        and orientation_yaml.lower() != _orient_instrument.lower()
    ):
        _log(
            f"WARNING: ADCP orientation mismatch — raw file says "
            f"'{_orient_instrument}' but YAML says '{orientation_yaml}'. "
            f"Velocity signs and beam geometry may be incorrect. "
            f"A correction must be applied in stage3."
        )

    return dataset


def _apply_nortek_xyz(dataset: xr.Dataset) -> xr.Dataset:
    """Rotate Nortek BEAM velocities to the instrument XYZ frame with the header's T matrix.

    Applies when the raw read recorded a transformation matrix (``nortek_T_M11`` …)
    and the instrument reports in BEAM coordinates. ``velocity_x/y/z`` are added and
    the beam velocities kept for verification; stage 3 then skips BEAM→XYZ. A
    downward-pointing instrument (``nortek_pointing_down``) flips rows 2 and 3.
    """
    keys = [f"nortek_T_M{r}{c}" for r in range(1, 4) for c in range(1, 4)]
    if not all(k in dataset.attrs for k in keys):
        return dataset
    if dataset.attrs.get("coordinate_system") != "BEAM":
        return dataset
    if "velocity_beam1" not in dataset.data_vars:
        return dataset
    t_matrix = np.array(
        [
            [dataset.attrs[f"nortek_T_M{r}{c}"] for c in range(1, 4)]
            for r in range(1, 4)
        ],
        dtype=float,
    )
    if dataset.attrs.get("nortek_pointing_down") == "True":
        t_matrix[1, :] = -t_matrix[1, :]
        t_matrix[2, :] = -t_matrix[2, :]
    b1 = dataset["velocity_beam1"].values.astype(float)
    b2 = dataset["velocity_beam2"].values.astype(float)
    b3 = dataset["velocity_beam3"].values.astype(float)
    valid = np.isfinite(b1) & np.isfinite(b2) & np.isfinite(b3)
    vx = np.full_like(b1, np.nan)
    vy = np.full_like(b1, np.nan)
    vz = np.full_like(b1, np.nan)
    if valid.any():
        xyz = t_matrix @ np.stack([b1[valid], b2[valid], b3[valid]])
        vx[valid], vy[valid], vz[valid] = xyz[0], xyz[1], xyz[2]
    tdim = dataset["velocity_beam1"].dims[0]
    vel_attrs = {"units": "m s-1"}
    dataset["velocity_x"] = xr.Variable(
        tdim, vx, {**vel_attrs, "long_name": "X velocity (instrument XYZ frame)"}
    )
    dataset["velocity_y"] = xr.Variable(
        tdim, vy, {**vel_attrs, "long_name": "Y velocity (instrument XYZ frame)"}
    )
    dataset["velocity_z"] = xr.Variable(
        tdim, vz, {**vel_attrs, "long_name": "Z velocity (instrument XYZ frame)"}
    )
    dataset.attrs["coordinate_system"] = "XYZ"
    return dataset


def _apply_analog_and_unit_hygiene(
    dataset: xr.Dataset, entry: dict[str, Any]
) -> xr.Dataset:
    """Label analog channels from the entry, drop placeholder units, and drop duplicate attrs."""
    # Enrich analog channel attributes from YAML analog_input_N / analog_input_N_units keys.
    # seasenselib outputs these as "analog_input_1" / "analog_input_2".
    for n in (1, 2):
        vname = f"analog_input_{n}"
        if vname not in dataset.data_vars:
            continue
        long_name = entry.get(f"analog_input_{n}")
        units = entry.get(f"analog_input_{n}_units")
        sn = entry.get(f"analog_input_{n}_serial_number")
        if long_name:
            dataset[vname].attrs["long_name"] = str(long_name)
        if units:
            dataset[vname].attrs["units"] = str(units)
        if sn:
            dataset[vname].attrs["sensor_serial_number"] = str(sn)

    # Strip placeholder units written by the RSK reader when a channel's units
    # field is absent from the instrument database; callers that need units must
    # verify against the sensor spec.
    bad_units = {"MISSING", "missing", ""}
    for vname, da in dataset.data_vars.items():
        units = da.attrs.get("units")
        if units in bad_units:
            del dataset[vname].attrs["units"]
            warnings.warn(
                f"Variable '{vname}': units attr was '{units}' (placeholder from "
                f"instrument file) and has been removed.  Verify the sensor "
                f"specification and set units manually if required.",
                UserWarning,
                stacklevel=2,
            )
    # Turbidity often has no units at all (Seapoint: no units in the RSK table).
    if "turbidity" in dataset.data_vars and "units" not in dataset["turbidity"].attrs:
        warnings.warn(
            "Variable 'turbidity' has no units attribute.  Typical units are "
            "'FTU' (Seapoint) or 'NTU' (WET Labs).  Verify the sensor spec and "
            "add a units attribute to the output file if required.",
            UserWarning,
            stacklevel=2,
        )

    # seasenselib attrs that duplicate our own provenance fields.
    for redundant in ("processor_level", "source_format_name"):
        dataset.attrs.pop(redundant, None)
    return dataset


def _stage1_instrument(
    dataset: xr.Dataset, entry: dict[str, Any], mooring: dict[str, Any]
) -> xr.Dataset:
    """Apply stage 1's transforms to one instrument's raw dataset, in the order they run."""
    file_type = dataset.attrs.get("file_type") or entry.get("file_type", "")

    if file_type == "rdi-raw":
        dataset = _normalize_rdi_raw(dataset, entry)
    if file_type in _NORTEK_TYPES:
        dataset = _apply_nortek_xyz(dataset)

    dataset = _normalize_conductivity(dataset)
    dataset = _sanitize_slash_vars(dataset)
    # The SBE hex reader outputs ODO oxygen as 'oxygen' (umol/L) and 'oxygen_ml_l'
    # (mL/L); rename to the canonical names used by every file type.
    if "oxygen" in dataset.data_vars:
        dataset = dataset.rename({"oxygen": "dissolved_oxygen"})
    if "oxygen_ml_l" in dataset.data_vars:
        dataset = dataset.rename({"oxygen_ml_l": "dissolved_oxygen_ml_l"})
    # SBE ascii/hex temperature is ITS-90; the ASCII reader does not record it.
    if file_type in ("sbe-ascii", "sbe-hex") and "temperature" in dataset.data_vars:
        dataset["temperature"].attrs.setdefault("scale", "ITS-90")

    dataset = _normalize_sensor_var_names(dataset, entry)
    dataset = _clean_dataset_variables(dataset, file_type)
    dataset = _add_global_attributes(dataset, mooring)
    dataset = _add_instrument_metadata(dataset, entry, mooring)
    dataset = _apply_analog_and_unit_hygiene(dataset, entry)

    # A file whose units attribute is known to be wrong is corrected from the
    # mooring YAML (``units_override``) before the units pass reads it.
    dataset = _apply_units_override(dataset, entry)
    stated_units = {v: dataset[v].attrs.get("units") for v in dataset.data_vars}
    # Raises UnitsError on an unknown or missing unit for a registered variable.
    dataset = to_canonical_units(dataset, log_fn=_log, stage=1)
    # An imported file's units are the originator's word; check the values agree.
    if file_type in ("oceansites", "rodb"):
        _check_value_ranges(dataset, stated_units)
    return dataset


def stage1(
    datasets: dict[str, xr.Dataset], mooring: dict[str, Any]
) -> dict[str, xr.Dataset]:
    """Turn a mooring's raw datasets into stage-1 datasets.

    Pure function over datasets: no file I/O. Normalises names and units, adds the
    mooring and instrument metadata, rotates Nortek BEAM velocities to XYZ when the
    header's matrix is known, and marks each result ``processing_stage = 1``.

    Parameters
    ----------
    datasets : dict of str to xarray.Dataset
        Raw datasets keyed by cleaned serial, as ``read_mooring(mooring,
        stage=None, raw_dir=...)`` returns them.
    mooring : dict
        The loaded mooring description.

    Returns
    -------
    dict of str to xarray.Dataset
        Stage-1 datasets keyed by serial, as their file will hold them (storage
        dtypes, time at the file's resolution), so each equals its file read back.

    Raises
    ------
    oceanarray.pipeline._stage.StageError
        If a dataset already carries a ``processing_stage`` marker.
    oceanarray.exceptions.MooringError
        If a dataset's serial has no entry in the description.
    oceanarray.pipeline.units.UnitsError
        If a registered variable has an unknown or missing unit.
    """
    by_serial = {
        safe_serial(e.get("serial", "")): e
        for e in instrument_entries(mooring)
        if isinstance(e, dict)
    }
    out: dict[str, xr.Dataset] = {}
    for serial, ds in datasets.items():
        marker = ds.attrs.get(_stage.STAGE_ATTR)
        if marker is not None:
            raise _stage.StageError(
                f"stage 1 expects raw datasets; {serial!r} is already stage "
                f"{int(marker)}."
            )
        entry = by_serial.get(serial)
        if entry is None:
            raise MooringError(
                f"{mooring['name']}: dataset {serial!r} has no entry in the mooring "
                "description; stage 1 needs the entry's metadata."
            )
        result = _stage1_instrument(ds.copy(deep=True), entry, mooring)
        out[serial] = _stage.stamp(as_stored(result), 1)
    return out


class MooringProcessor:
    """Stage 1 for a mooring: read each raw file, apply :func:`stage1`, write."""

    # Supported format keys, single-sourced from ``parameters.ALL_FILE_TYPES`` so the
    # stage1 gate and the YAML validator cannot drift.
    SUPPORTED_FILE_TYPES = params.ALL_FILE_TYPES

    def __init__(self, *, raw_dir: str, proc_dir: str) -> None:
        """Initialize the processor with cruise-level raw and proc directories.

        Parameters
        ----------
        raw_dir : str
            Cruise-level raw data directory. The pipeline appends ``/{mooring}/``
            internally (see :func:`oceanarray.paths.raw_mooring_dir`).
        proc_dir : str
            Cruise-level processed output directory. The pipeline appends
            ``/{mooring}/`` internally (see :func:`oceanarray.paths.mooring_proc_dir`).

        """
        self._raw_dir = Path(raw_dir)
        self._proc_dir = Path(proc_dir)
        self.log_file = None

    def _setup_logging(self, mooring_name: str, output_path: Path) -> None:
        """Set up the stage log, and send the readers' library logs to it only."""
        from oceanarray.logger import attach_stage_handlers, setup_stage_logging

        self.log_file = setup_stage_logging(mooring_name, "stage1", output_path)
        self._log_handlers = attach_stage_handlers(self.log_file)
        _attach_reader_logs(self.log_file)

    def _log_print(self, *args: Any, **kwargs: Any) -> None:
        """Print to both console and log file."""
        print(*args, **kwargs)
        if self.log_file:
            with self.log_file.open("a", encoding="utf-8") as f:
                print(*args, **kwargs, file=f)

    def _rel(self, path: Path) -> str:
        """Return a short display path relative to proc_dir or raw_dir."""
        roots = [r for r in (self._proc_dir, self._raw_dir) if r]
        for root in roots:
            try:
                return str(path.relative_to(root))
            except ValueError:
                continue
        return path.name

    @with_stage_handlers
    def process_mooring(
        self,
        mooring_name: str,
        output_path: str | None = None,
        serials: list[str] | None = None,
        force: bool = False,
    ) -> bool:
        """Process a single mooring's data.

        Args:
            mooring_name: Name of the mooring to process
            output_path: Optional custom output path. If None, uses default structure.
            serials: Optional list of serial numbers to process; if None, process all.
            force: Re-process even if output already exists.

        Returns
        -------
            bool: True if processing completed successfully, False otherwise

        """
        if output_path is None:
            output_path = paths.mooring_proc_dir(self._proc_dir, mooring_name)
        else:
            output_path = Path(output_path) / mooring_name
        output_path.mkdir(parents=True, exist_ok=True)

        self._setup_logging(mooring_name, output_path)
        self._log_print(f"Processing mooring: {mooring_name}")

        config_file = output_path / f"{mooring_name}.mooring.yaml"
        if not config_file.exists():
            self._log_print(f"ERROR: Configuration file not found: {config_file}")
            return False
        try:
            mooring_config = _mooring.load(config_file)
        except (OSError, KeyError, ValueError) as e:
            self._log_print(f"ERROR: Failed to load configuration: {e}")
            return False

        try:
            run = _stage1_files(
                mooring_config,
                output_path,
                self._raw_dir,
                folder=mooring_name,
                serials=serials,
                force=force,
                log=self._log_print,
                rel=self._rel,
            )
        except MooringError as e:
            self._log_print(f"ERROR: {e}")
            return False
        # Partial success is still success: instruments that processed are written
        # and the record summary reports which succeeded and which failed.
        return run.matched > len(run.failed)


_ReaderLogState = tuple[logging.Logger, int, bool, logging.Handler | None]


def _attach_reader_logs(log_file: Path) -> list[_ReaderLogState]:
    """Send the raw readers' library logs (seasenselib, pycnv) to *log_file* only.

    Returns each logger's previous level and propagation and the handler added
    (None when one was already there), for :func:`_detach_reader_logs`.
    """
    added: list[_ReaderLogState] = []
    for lib_logger_name in ("seasenselib", "pycnv"):
        lib_log = logging.getLogger(lib_logger_name)
        state = (lib_log, lib_log.level, lib_log.propagate)
        lib_log.setLevel(logging.INFO)
        lib_log.propagate = False
        if not any(
            isinstance(h, logging.FileHandler) and h.baseFilename == str(log_file)
            for h in lib_log.handlers
        ):
            fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
            lib_log.addHandler(fh)
            added.append((*state, fh))
        else:
            added.append((*state, None))
    return added


def _detach_reader_logs(states: list[_ReaderLogState]) -> None:
    """Undo :func:`_attach_reader_logs`: remove the handlers, restore level and propagation."""
    for lib_log, level, propagate, handler in states:
        if handler is not None:
            lib_log.removeHandler(handler)
            handler.close()
        lib_log.setLevel(level)
        lib_log.propagate = propagate


def _stage1_file(
    entry: dict[str, Any],
    mooring: dict[str, Any],
    mooring_dir: Path,
    raw_dir: Path | None,
    *,
    folder: str,
    force: bool,
    log: Callable[..., None],
    rel: Callable[[Path], str],
) -> tuple[str, Any]:
    """Read, transform and write one instrument; return ``(status, detail)``.

    *status* is ``"written"`` or ``"kept"`` (detail: the path; kept means it was
    already there and newer than its raw file), ``"skipped"`` (marked ``skip``) or
    ``"failed"`` (detail: the message). One failed instrument is logged and does not abort the
    mooring; a ``RuntimeError`` (a registry inconsistency, a package bug) does.
    """
    instrument = entry.get("instrument", "unknown")
    serial = entry.get("serial", "unknown")
    if entry.get("skip"):
        reason = entry.get("skip_reason", "marked skip:true in YAML")
        log(f"SKIP {instrument} {serial}: {reason}")
        return "skipped", None

    try:
        raw = resolve_raw_file(entry, mooring, raw_dir, folder=folder)
    except MooringError:
        log(
            f"FILENAME MISSING: Skipping {instrument}:{serial}. "
            f"YAML is missing 'filename' and no standard file was found."
        )
        return "failed", f"{instrument} {serial}: no raw file named or found"

    _status("instr", f"{instrument} {serial}")
    output_dir = mooring_dir / raw.instrument_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    clean = safe_serial(serial)
    output_file = paths.stage_file(
        mooring_dir, mooring["name"], raw.instrument_dir, clean, 1, tag=raw.tag
    )
    # Skip only if the output is present and newer than its raw input. The
    # mooring YAML is deliberately not a staleness source; force regenerates.
    if should_skip_regeneration(output_file, force, False, raw.path):
        _status("skip", rel(output_file))
        return "kept", output_file

    try:
        dataset = read_raw_file(raw)
    except Exception as e:  # noqa: BLE001 — seasenselib wraps any load failure (corrupt file, missing header) in RuntimeError; one unreadable file must not abort the mooring
        log(f"EXCEPT: Error reading file {rel(raw.path)}: {e}")
        return "failed", f"{instrument} {serial}: {e}"

    try:
        dataset = join_raw(dataset, raw.entry, mooring)
        dataset = stage1({clean: dataset}, mooring)[clean]
        _status("file", rel(output_file))
        return "written", write(dataset, output_file, overwrite=True)
    except RuntimeError:
        # A registry inconsistency (to_canonical_units raises RuntimeError, not
        # UnitsError) is a package bug, not a bad instrument — abort the run.
        raise
    except Exception as e:  # noqa: BLE001 — UnitsError (bad units) and any seasenselib read error: log the file and skip this one instrument
        log(f"ERROR: Failed to process {rel(raw.path)}: {e}")
        return "failed", f"{instrument} {serial}: {e}"


def _stage1_files(
    mooring: dict[str, Any],
    mooring_dir: Path,
    raw_dir: Path | None,
    *,
    folder: str,
    serials: list[str] | None = None,
    force: bool = False,
    log: Callable[..., None] = print,
    rel: Callable[[Path], str] = lambda p: p.name,
) -> _stage.StageRun:
    """Run stage 1 over one mooring's raw files, writing each instrument's stage-1 file.

    One instrument's failure is logged and recorded; the rest are still written.
    *folder* is the mooring's folder under *raw_dir*.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If the raw-data directory is not found.
    """
    # Raw root: {raw_dir}/{folder}/; an absolute YAML 'directory:' overrides it.
    override = mooring.get("directory")
    if override and Path(override).is_absolute():
        log(
            f"WARNING: using 'directory: {override}' from YAML "
            f"as raw root override (ignoring --raw-dir)"
        )
        if not Path(override).exists():
            raise MooringError(f"Input directory not found: {override}")
    else:
        if raw_dir is None:
            raise MooringError("stage 1 needs raw_dir= to find the raw files.")
        raw_mooring_dir = paths.raw_mooring_dir(raw_dir, folder)
        if not raw_mooring_dir.exists():
            raise MooringError(f"Raw mooring directory not found: {raw_mooring_dir}")

    instrument_list = instrument_entries(mooring)
    inline_instruments = extract_inline_instruments(mooring.get("inline") or [])
    if inline_instruments:
        log(
            f"Found {len(inline_instruments)} instrument(s) in 'inline' list: "
            + ", ".join(str(ic.get("serial", "?")) for ic in inline_instruments)
        )
    if serials:
        wanted = {safe_serial(s) for s in serials}
        instrument_list = [
            ic for ic in instrument_list if safe_serial(ic.get("serial", "")) in wanted
        ]
        log(
            f"Filtered to {len(instrument_list)} instrument(s) matching serial(s): "
            f"{', '.join(serials)}"
        )

    run = _stage.StageRun()
    for entry in instrument_list:
        run.matched += 1
        status, detail = _stage1_file(
            entry,
            mooring,
            mooring_dir,
            raw_dir,
            folder=folder,
            force=force,
            log=log,
            rel=rel,
        )
        if status == "written":
            run.written.append(detail)
            run.files.append(detail)
        elif status == "kept":
            run.files.append(detail)
        elif status == "failed":
            run.failed.append(detail)

    log(
        f"Completed processing: {run.matched - len(run.failed)}/{run.matched} "
        "instruments successful"
    )
    if run.matched == 0:
        log("No instruments matched — nothing processed.")
    return run
