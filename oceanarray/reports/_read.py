"""Read the rows a report page shows from our own netCDF files: QC, timing, sensors."""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from oceanarray import paths
from oceanarray.pipeline import masking
from oceanarray.pipeline.dataset import time_step_seconds

from ..config import parameters as params

log = logging.getLogger(__name__)

#: Reader-facing words for the flag values, from the central flag table.
_QC_LABELS: dict[int, str] = params.QC_FLAG_LABEL


def is_identifier(ds: Any, name: str) -> bool:
    """Return True when variable *name* holds labels or codes rather than measurements.

    Serial numbers, indices, flag codes and ``cf_role`` variables are numbers only
    as labels: their minimum and maximum say nothing, so the variables table
    leaves them out.

    Parameters
    ----------
    ds : xarray.Dataset
        The open file.
    name : str
        A variable or coordinate name.

    Returns
    -------
    bool
    """
    v = ds[name]
    lowered = name.lower()
    if "serial" in lowered or lowered.endswith("_index") or lowered == "index":
        return True
    if "cf_role" in v.attrs:
        return True
    return v.dtype.kind in "iu" and (
        "flag_values" in v.attrs or "flag_meanings" in v.attrs
    )


def _blank_identifier_ranges(ds: Any, rows: list[dict[str, Any]]) -> None:
    """Replace the minimum and maximum of identifier-like rows with a dash."""
    for row in rows:
        if row.get("name") in ds.variables and is_identifier(ds, row["name"]):
            row["v_min"] = row["v_max"] = "—"
            row["v_min_raw"] = None


def _read_nc_metadata(nc_path: Path) -> dict[str, Any]:
    """Return the vendored-builder inventory rows/groups for a NetCDF file."""
    # SENSOR_* variables stay in the scalar table.  A malformed file returns the
    # same keys empty plus "error".
    import xarray as xr

    from . import design
    from ._vendored import nc_tables

    try:
        with xr.open_dataset(nc_path, engine="netcdf4", decode_timedelta=False) as ds:
            coords_rows, coords_src = nc_tables.coords_rows(ds)
            var_groups, var_src = nc_tables.variables_rows(
                ds, is_qc=design.is_qc, is_sensor=design.is_sensor
            )
            for _title, rows in var_groups:
                _blank_identifier_ranges(ds, rows)
            _blank_identifier_ranges(ds, coords_rows)
            scalar_rows, scalar_src = nc_tables.scalars_rows(ds)
            # `history` has its own dedicated processing-history panel, so exclude it from
            # the global-attributes table (the builder's `exclude=` drops the attr and any
            # group it empties) rather than show the long provenance string twice.
            global_groups, global_src = nc_tables.globals_rows(
                ds, extras=design.ATTR_GROUP_EXTRAS, exclude=("history",)
            )
            summary = nc_tables.summary_counts(
                ds, is_qc=design.is_qc, is_sensor=design.is_sensor
            )
            # Analog channels carrying non-zero data.
            analog_vars: list[str] = []
            for vname in sorted(ds.data_vars):
                v = ds[vname]
                if v.dims and "analog" in vname.lower() and v.dtype.kind in ("f", "c"):
                    finite = v.values[np.isfinite(v.values)]
                    if finite.size and not np.all(finite == 0.0):
                        analog_vars.append(vname)
        return {
            "coords_rows": coords_rows,
            "coords_src": coords_src,
            "var_groups": var_groups,
            "var_src": var_src,
            "scalar_rows": scalar_rows,
            "scalar_src": scalar_src,
            "global_groups": global_groups,
            "global_src": global_src,
            "summary": summary,
            "analog_vars": analog_vars,
        }
    except Exception as exc:
        return {
            "coords_rows": [],
            "coords_src": None,
            "var_groups": [],
            "var_src": None,
            "scalar_rows": [],
            "scalar_src": None,
            "global_groups": [],
            "global_src": None,
            "summary": {},
            "analog_vars": [],
            "error": str(exc)[:120],
        }


def _read_qc_thresholds(nc_path: Path) -> list[dict[str, Any]]:
    """Return the QC thresholds actually applied, as stored in the stage3 NC file."""
    # One row per (variable, test) with the values stored in the file, whatever
    # their origin (package default, mooring YAML, per-instrument override).
    # Spike and flat-line rows appear only for stage3 files produced after 2026-07-24.
    try:
        import xarray as xr

        ds = xr.open_dataset(nc_path, decode_timedelta=False)
        rows = []
        for v in sorted(ds.data_vars):
            if not v.endswith("_qc") or v.endswith("_orig_qc"):
                continue
            base = v[:-3]
            if base not in ds.data_vars:
                continue
            attrs = ds[v].attrs
            fail_min = attrs.get("qc_gross_range_fail_min")
            fail_max = attrs.get("qc_gross_range_fail_max")
            susp_min = attrs.get("qc_gross_range_suspect_min")
            susp_max = attrs.get("qc_gross_range_suspect_max")
            if fail_min is not None or susp_min is not None:
                rows.append(
                    {
                        "var": base,
                        "test": "gross-range",
                        "suspect": (
                            f"[{susp_min}, {susp_max}]" if susp_min is not None else "—"
                        ),
                        "fail": (
                            f"[{fail_min}, {fail_max}]" if fail_min is not None else "—"
                        ),
                    }
                )
            sp_susp = attrs.get("qc_spike_suspect_threshold")
            sp_fail = attrs.get("qc_spike_fail_threshold")
            if sp_susp is not None or sp_fail is not None:
                rows.append(
                    {
                        "var": base,
                        "test": "spike",
                        "suspect": f"|Δ| > {sp_susp}" if sp_susp is not None else "—",
                        "fail": f"|Δ| > {sp_fail}" if sp_fail is not None else "—",
                    }
                )
            fl_susp = attrs.get("qc_flat_line_suspect_count")
            fl_fail = attrs.get("qc_flat_line_fail_count")
            if fl_susp is not None or fl_fail is not None:
                rows.append(
                    {
                        "var": base,
                        "test": "flat-line",
                        "suspect": (
                            f"stuck ≥ {fl_susp} samples" if fl_susp is not None else "—"
                        ),
                        "fail": (
                            f"stuck ≥ {fl_fail} samples" if fl_fail is not None else "—"
                        ),
                    }
                )
        # Tilt QC (Aquadopp): stored at dataset level
        t_susp = ds.attrs.get("tilt_suspect_threshold")
        t_fail = ds.attrs.get("tilt_fail_threshold")
        if t_susp is not None or t_fail is not None:
            rows.append(
                {
                    "var": "roll (tilt)",
                    "test": "tilt",
                    "suspect": f">{t_susp}°" if t_susp is not None else "—",
                    "fail": f">{t_fail}°" if t_fail is not None else "—",
                }
            )
        ds.close()
        return rows
    except (OSError, KeyError, ValueError) as e:
        log.warning("%s: QC thresholds not read (%s)", nc_path.name, e)
        return []


def _read_qc_summary(nc_path: Path) -> list[dict[str, Any]]:
    """Return the per-variable flag counts of a stage-3 file, for the deployment page.

    Parameters
    ----------
    nc_path : pathlib.Path
        The stage-3 file.

    Returns
    -------
    list of dict
        One per variable and flag set (oceanarray's ``<var>_qc``, the originator's
        ``<var>_qc_originator_mapped``): ``var``, ``by`` (``"oceanarray"`` or
        ``"originator"``), ``total`` (samples), ``flags`` (``_qcflags.flag_bar``)
        and ``good`` (``_qcflags.good_share``); empty when the file cannot be read.
        Character flags (an originator's ``"1"``, ``"4"``) are read as numbers; a
        variable whose flags are not all digits is left out with a warning.
    """
    import xarray as xr

    from oceanarray.pipeline.qc.originator import MAPPED_SUFFIX

    from ._qcflags import flag_bar, flag_counts, good_share

    try:
        ds = xr.open_dataset(nc_path, decode_timedelta=False, engine="netcdf4")
    except (OSError, ValueError):
        return []
    rows = []
    with ds:
        for v in sorted(ds.data_vars):
            for suffix, by in (("_qc", "oceanarray"), (MAPPED_SUFFIX, "originator")):
                base = v.removesuffix(suffix)
                if base == v or base.endswith("_orig") or base not in ds.data_vars:
                    continue
                flags = ds[v].values.ravel()
                if flags.dtype.kind in "SUO":
                    text = np.char.strip(
                        np.char.decode(flags)
                        if flags.dtype.kind == "S"
                        else flags.astype(str)
                    )
                    if not np.char.isdigit(text).all():
                        log.warning(
                            f"{nc_path.name}: {v} flags are not all digits; "
                            "left out of the QC summary"
                        )
                        continue
                    flags = text
                flags = flags.astype(int)
                if flags.size:
                    counts = flag_counts(flags)
                    rows.append(
                        {
                            "var": base,
                            "by": by,
                            "total": flags.size,
                            "flags": flag_bar(counts, flags.size),
                            "good": good_share(counts, flags.size),
                        }
                    )
    return rows


_PRIMARY_VARS = [
    ("temperature", "T"),
    ("conductivity", "C"),
    ("pressure", "P"),
    ("east_velocity", "U"),
    ("north_velocity", "V"),
]


def _read_instrument_info(
    proc_dir: Path,
    instr_type: str,
    mooring: str,
    serial: str,
    *,
    rule: dict | None = None,
) -> dict[str, Any]:
    """Read record statistics from the best available processed NetCDF file.

    The pressure range leaves out what *rule* hides: the mooring's
    ``masking.stack_rule``, like the figures; None for the rule the file records,
    else the default.
    """
    base = proc_dir / instr_type / f"{mooring}_{serial}"
    nc_path = None
    # Prefer stage 3, fall back to stage 2; raw stage-1 files are never read here.
    for suffix in (paths.stage_suffix(3), paths.stage_suffix(2)):
        p = Path(str(base) + suffix)
        if p.exists():
            nc_path = p
            break
    if nc_path is None:
        return {}

    try:
        import xarray as xr

        ds = xr.open_dataset(nc_path, decode_timedelta=False)
        time = ds["time"].values
        n = len(time)

        t_start = str(time[0])[:16].replace("T", " ") if n > 0 else "—"
        t_end = str(time[-1])[:16].replace("T", " ") if n > 0 else "—"
        t_end_raw = time[-1] if n > 0 else None

        dt_s = time_step_seconds(ds)  # the median step: gaps do not move it

        vars_present = {v for v in ds.data_vars if ds[v].dims != ()}
        has_beam = any(f"velocity_beam{i}" in vars_present for i in (1, 2, 3))

        # Check whether pressure was interpolated (any QC flag == 8) so the P pill
        # in the instrument summary can be coloured blue (interpolated) vs green (measured).
        pressure_interpolated = False
        if "pressure_qc" in ds.data_vars:
            with contextlib.suppress(Exception):
                pressure_interpolated = bool(
                    np.any(ds["pressure_qc"].values.astype(int) == 8)
                )

        # Min/max pressure over the trimmed record (stage2/stage3).
        # Exclude non-positive values (negative or stuck-at-zero sensors are
        # non-physical for a deployed mooring) and values the mask_flags rule hides.
        p_min: float | None = None
        p_max: float | None = None
        if "pressure" in ds.data_vars:
            try:
                pvals = masking.masked(ds, "pressure", rule=rule)
                # Exclude non-positive values (pre-deployment bench / stuck sensor)
                pvals = np.where(pvals > 0, pvals, np.nan)
                _pmin = float(np.nanmin(pvals))
                _pmax = float(np.nanmax(pvals))
                if np.isfinite(_pmin):
                    p_min = _pmin
                if np.isfinite(_pmax):
                    p_max = _pmax
            except (ValueError, TypeError):
                pass

        ds.close()

        shorthands = [(label, name in vars_present) for name, label in _PRIMARY_VARS]

        return {
            "t_start": t_start,
            "t_end": t_end,
            "t_end_raw": t_end_raw,
            "n_records": n,
            "dt_s": dt_s,
            "shorthands": shorthands,
            "has_beam": has_beam,
            "pressure_interpolated": pressure_interpolated,
            "p_min": p_min,
            "p_max": p_max,
        }
    except Exception as exc:
        return {"error": str(exc)[:80]}


def _read_timing_info(
    proc_dir: Path, instr_type: str, mooring: str, serial: str
) -> dict[str, Any]:
    """Read deployment-timing attrs from the stage 2 NC file."""
    # Stage 2, not stage 3: the timing metadata is written during stage 2.
    nc_path = proc_dir / instr_type / f"{mooring}_{serial}{paths.stage_suffix(2)}"
    if not nc_path.exists():
        return {}
    try:
        import xarray as xr

        ds = xr.open_dataset(nc_path, decode_timedelta=False)
        time = ds["time"].values
        n = len(time)

        stage2_start = str(time[0])[:16].replace("T", " ") if n > 0 else None
        stage2_end = str(time[-1])[:16].replace("T", " ") if n > 0 else None

        dt_s = time_step_seconds(ds)  # the median step: gaps do not move it

        stage1_start = ds.attrs.get("stage1_time_start")
        stage1_end = ds.attrs.get("stage1_time_end")
        stage1_nonnull_start = ds.attrs.get("stage1_first_nonnull")
        stage1_nonnull_end = ds.attrs.get("stage1_last_nonnull")
        sugg_start = ds.attrs.get("suggested_deployment_time")
        sugg_end = ds.attrs.get("suggested_recovery_time")
        # sugg_* are in the raw instrument clock; the *_utc pair is clock-corrected
        # and is what is safe to paste into the YAML.
        sugg_start_utc = ds.attrs.get("suggested_deployment_time_utc")
        sugg_end_utc = ds.attrs.get("suggested_recovery_time_utc")
        yaml_recovery = ds.attrs.get("yaml_recovery_time")
        ds.close()

        # Amber: sugg UTC differs from YAML/stage2 bound by > 2 sample intervals
        sugg_start_differs = False
        sugg_end_differs = False
        if sugg_start_utc and stage2_start and np.isfinite(dt_s) and dt_s > 0:
            try:
                diff_s = abs(
                    (
                        datetime.fromisoformat(sugg_start_utc)
                        - datetime.fromisoformat(stage2_start)
                    ).total_seconds()
                )
                sugg_start_differs = diff_s > 2 * dt_s
            except (ValueError, TypeError):
                pass
        if sugg_end_utc and stage2_end and np.isfinite(dt_s) and dt_s > 0:
            try:
                diff_s = abs(
                    (
                        datetime.fromisoformat(sugg_end_utc)
                        - datetime.fromisoformat(stage2_end)
                    ).total_seconds()
                )
                sugg_end_differs = diff_s > 2 * dt_s
            except (ValueError, TypeError):
                pass

        # Orange-amber: stage1 last record ends more than 2 sample intervals before
        # the YAML recovery time — indicates the instrument stopped recording early.
        stage1_end_early = False
        if stage1_end and yaml_recovery and np.isfinite(dt_s) and dt_s > 0:
            try:
                diff_s = (
                    datetime.fromisoformat(yaml_recovery[:16])
                    - datetime.fromisoformat(stage1_end[:16])
                ).total_seconds()
                stage1_end_early = diff_s > 2 * dt_s
            except (ValueError, TypeError):
                pass

        def _fmt(s: str | None) -> str | None:
            if not s:
                return None
            return str(s)[:16].replace("T", " ")

        return {
            "stage1_start": _fmt(stage1_start),
            "stage1_end": _fmt(stage1_end),
            "stage1_nonnull_start": _fmt(stage1_nonnull_start),
            "stage1_nonnull_end": _fmt(stage1_nonnull_end),
            "sugg_start": _fmt(sugg_start),
            "sugg_end": _fmt(sugg_end),
            "sugg_start_utc": _fmt(sugg_start_utc),
            "sugg_end_utc": _fmt(sugg_end_utc),
            "stage2_start": stage2_start,
            "stage2_end": stage2_end,
            "sugg_start_differs": sugg_start_differs,
            "sugg_end_differs": sugg_end_differs,
            "stage1_end_early": stage1_end_early,
            "dt_s": dt_s,
        }
    except (OSError, KeyError, ValueError) as e:
        log.warning("%s s/n %s: timing not read (%s)", instr_type, serial, e)
        return {}


def _read_sensor_record(
    proc_dir: Path, instr_type: str, mooring: str, serial: str
) -> dict[str, Any] | None:
    """Return what an instrument's sensor record says of it, from its latest stage file.

    Parameters
    ----------
    proc_dir : pathlib.Path
        The processing root.
    instr_type, mooring, serial : str
        Which instrument.

    Returns
    -------
    dict or None
        ``model``, ``maker``, ``type`` (each with its ``*_vocabulary`` link, or
        ``""``), ``pumped``, ``source`` (where the model came from) and
        ``originator`` (the originator's own description: a list of
        ``(key, value)``); None when the record names no model and the originator
        said nothing (a raw file read as is).
    """
    for stage in (3, 2, 1):
        nc_path = (
            proc_dir / instr_type / f"{mooring}_{serial}{paths.stage_suffix(stage)}"
        )
        if nc_path.exists():
            break
    else:
        return None
    try:
        import xarray as xr

        from oceanarray.pipeline import sensors

        with xr.open_dataset(nc_path, engine="netcdf4", decode_timedelta=False) as ds:
            records = list(sensors.records(ds).values())
    except (OSError, ValueError):
        return None
    if not records:
        return None
    attrs = records[0]
    originator = sorted(
        (k.removeprefix("originator_"), str(v))
        for k, v in attrs.items()
        if str(k).startswith("originator_")
    )
    model = str(attrs.get("sensor_model", "UNK"))
    if model == "UNK" and not originator:
        return None
    return {
        "model": model,
        "model_vocabulary": str(attrs.get("sensor_model_vocabulary", "")),
        "maker": str(attrs.get("sensor_maker", "")),
        "maker_vocabulary": str(attrs.get("sensor_maker_vocabulary", "")),
        "type": str(attrs.get("sensor_type", "")),
        "type_vocabulary": str(attrs.get("sensor_type_vocabulary", "")),
        "pumped": str(attrs.get("sensor_pumped", "")),
        "source": str(attrs.get("sensor_metadata_source", "")),
        "model_source": str(attrs.get("sensor_model_source", "")),
        "originator": originator,
    }


def _read_sensor_info(
    proc_dir: Path, instr_type: str, mooring: str, serial: str
) -> list[dict[str, Any]]:
    """Return one dict per SENSOR_* variable found in the stage2 NC file."""
    nc_path = proc_dir / instr_type / f"{mooring}_{serial}{paths.stage_suffix(2)}"
    if not nc_path.exists():
        return []
    try:
        import xarray as xr

        from oceanarray.pipeline import sensors

        ds = xr.open_dataset(nc_path, decode_timedelta=False)
        rows = []
        for vname in sorted(v for v in ds.data_vars if v.startswith("SENSOR_")):
            attrs = dict(ds[vname].attrs)
            if "sensor_type_vocabulary" in attrs:
                # One record per instrument: a row per calibrated sensor (none for
                # an instrument without calibrations).
                per_sensor = sensors.calibration_rows(attrs)
                rows += [
                    {
                        "var_name": vname,
                        "sensor_type": row["sensor"].upper(),
                        "sensor_model": attrs.get("sensor_model", "—"),
                        "sensor_serial": row["serial"] or "—",
                        "cal_date": row["date"],
                        "coefficients": row["coefficients"],
                    }
                    for row in per_sensor
                ]
                continue
            # A file written before sensor records: one variable per sensor.
            stype = attrs.get("sensor_type", "")
            rows.append(
                {
                    "var_name": vname,
                    "sensor_type": stype,
                    "sensor_model": attrs.get("sensor_model", "—"),
                    "sensor_serial": attrs.get("sensor_serial_number", "—"),
                    "cal_date": attrs.get("sensor_calibration_date", "—"),
                    "coefficients": attrs.get(f"{stype}_calibration_coefficients", ""),
                }
            )
        ds.close()
        return rows
    except (OSError, KeyError, ValueError) as e:
        log.warning(
            "%s s/n %s: sensor information not read (%s)", instr_type, serial, e
        )
        return []
