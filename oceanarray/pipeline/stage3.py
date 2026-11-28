"""Stage 3: pressure interpolation and QARTOD QC; writes ``_stage3.nc`` for every instrument.

Pressure is interpolated for instruments without it, or whose pressure the YAML marks
bad (``pressure_qc: 3`` or more): from the nearest source within ``HAB_THRESHOLD``,
else bracketing sources above and below, else extrapolated (with a warning); flagged 8.
Gross-range (``parameters.QC_GROSS_RANGE``, YAML ``qc_ranges``) and spike
(``parameters.QC_SPIKE``, ``qc_spike``) tests follow, mooring-level YAML overridden by
the instrument's entry. Flags merge as 9 > 4 > 3 > 8 > 2 > 1, so interpolated pressure
that fails a range test is 4.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from oceanarray import _deprecated, paths
from oceanarray import mooring as _mooring
from oceanarray.config import parameters as params
from oceanarray.exceptions import MooringError, OceanarrayError
from oceanarray.history import append_history
from oceanarray.logger import with_stage_handlers
from oceanarray.mooring.instruments import instrument_entries
from oceanarray.mooring.location import parse_latlon_with_source
from oceanarray.paths import safe_serial
from oceanarray.pipeline import _stage, sensors
from oceanarray.pipeline.calibration import CALDIP_STUB_APPLIED
from oceanarray.pipeline.coordinate import (
    apply_beam_to_enu,
    apply_declination_to_enu,
)
from oceanarray.pipeline.dataset import drop_all_zero_vars
from oceanarray.pipeline.pressure import (
    compute_adcp_bin_pressure,
    interpolate_pressure,
)
from oceanarray.pipeline.qc import (
    apply_enu_velocity_qc,
    apply_qc_tests,
    apply_tilt_qc,
    compute_salinity_data,
    derive_oxygen_saturation,
    load_qc_config,
    merge_salinity_parent_qc,
    originator,
    set_qc_attrs,
    unify_velocity_qc,
)
from oceanarray.pipeline.qc.adcp import (
    apply_adcp_seabed_qc,
    apply_adcp_surface_qc,
    apply_adcp_velocity_qc,
)
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.pipeline.units import to_canonical_units
from oceanarray.pipeline.write_mooring import write_mooring
from oceanarray.writers.netcdf import as_stored

log = logging.getLogger(__name__)

_ENU_KEYS = {"east_velocity", "north_velocity", "up_velocity"}


def _originator_salinity(ds: xr.Dataset) -> xr.DataArray | None:
    """Return an imported file's own salinity before stage 3 rederives it, or None."""
    if "salinity" not in ds.data_vars or not ds.attrs.get("originator_file"):
        return None
    if not {"temperature", "conductivity", "pressure"}.issubset(ds.data_vars):
        return None  # not rederived: theirs stays as salinity
    return ds["salinity"].copy()


def _keep_originator_salinity(
    ds: xr.Dataset, theirs: xr.DataArray
) -> tuple[xr.Dataset, str]:
    """Keep the originator's salinity as ``salinity_originator``; return a history note.

    The originator's description of its salinity (``originator_salinity_*`` on the
    sensor record since stage 1) moves back onto ``salinity_originator``, which it
    describes, and off the record.

    Parameters
    ----------
    ds : xarray.Dataset
        The stage-3 dataset, with oceanarray's ``salinity`` computed.
    theirs : xarray.DataArray
        The file's ``salinity`` before it was recomputed.

    Returns
    -------
    ds : xarray.Dataset
        *ds* with ``salinity_originator``.
    note : str
        The history line: the median and largest difference, ours minus theirs.
    """
    from oceanarray.pipeline import sensors

    attrs = {
        k: theirs.attrs[k]
        for k in ("units", "long_name", "standard_name", "original_name")
        if k in theirs.attrs
    }
    prefix = "originator_salinity_"
    for name, record in sensors.records(ds).items():
        moved = {k: v for k, v in record.items() if k.startswith(prefix)}
        for key, value in moved.items():
            if key != f"{prefix}units":
                attrs[key[len(prefix) :]] = value
            del ds[name].attrs[key]
    attrs["comment"] = (
        f"{attrs['comment']}; " if attrs.get("comment") else ""
    ) + "the originator's salinity, as delivered; salinity is recomputed from C, T, P"
    ds["salinity_originator"] = xr.Variable(theirs.dims, theirs.values, attrs=attrs)
    diff = ds["salinity"].values - theirs.values
    diff = diff[np.isfinite(diff)]
    if diff.size:
        worst = diff[np.argmax(np.abs(diff))]
        detail = f"(ours - theirs: median {np.median(diff):+.4f}, largest {worst:+.4f})"
    else:
        detail = "(no value in common)"
    note = (
        "salinity recomputed from C, T, P, the originator's kept as "
        f"salinity_originator {detail}"
    )
    return ds, note


def _resolve_pressure_var(data_vars: set) -> str | None:
    """Return the dataset's pressure variable name, or None if it has none."""
    if "pressure" in data_vars:
        return "pressure"
    for cand in sorted(
        v for v in data_vars if v.startswith("pressure_") and not v.endswith("_qc")
    ):
        return cand
    return None


def _parse_hab_segments(entry: dict[str, Any], serial: str) -> list[tuple]:
    """Return the entry's ``hab_segments`` as sorted ``(np.datetime64, hab)`` breakpoints."""
    hab_segments: list[tuple] = []
    for seg in entry.get("hab_segments", []):
        try:
            bp = np.datetime64(seg["from"], "ns")
            hab_segments.append((bp, float(seg["hab"])))
        except (KeyError, ValueError) as e:
            log.warning(
                f"serial {serial} hab_segments entry invalid ({seg}): {e} — skipped"
            )
    hab_segments.sort(key=lambda x: x[0])
    return hab_segments


def _build_instrument_table(
    datasets: dict[str, xr.Dataset], mooring: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return one info dict per dataset (mooring YAML order) with its QC config."""
    lat, lon, latlon_source = parse_latlon_with_source(mooring)
    water_depth_m = float(mooring.get("waterdepth") or 0.0)

    instruments: list[dict[str, Any]] = []
    for entry in instrument_entries(mooring):
        if not isinstance(entry, dict):
            continue
        serial = safe_serial(entry.get("serial", ""))
        if serial not in datasets:
            continue
        instr_type = entry.get("instrument", "unknown")
        if entry.get("skip"):
            reason = entry.get("skip_reason", "marked skip:true in YAML")
            log.info(f"SKIP {instr_type} {serial}: {reason}")
            continue
        hab = entry.get("hab")
        if hab is None:
            log.warning(f"serial {serial} has no 'hab' — skipping")
            continue
        ds = datasets[serial]
        qc_flags = {
            key[:-3]: int(val)
            for key, val in entry.items()
            if key.endswith("_qc") and isinstance(val, int)
        }
        gr_cfg, sp_cfg, tilt_cfg, fl_cfg = load_qc_config(mooring, entry)
        data_vars = set(ds.data_vars)
        pressure_var = _resolve_pressure_var(data_vars)
        for varname, qc_val in qc_flags.items():
            if varname not in data_vars:
                log.warning(
                    f"serial {serial}: '{varname}_qc: {qc_val}' in YAML but "
                    f"'{varname}' not found in its stage-2 dataset — ignored"
                )
        instruments.append(
            {
                "serial": serial,
                "instrument": instr_type,
                "hab": float(hab),
                "hab_segments": _parse_hab_segments(entry, serial),
                "qc_flags": qc_flags,
                "gross_range": gr_cfg,
                "spike": sp_cfg,
                "tilt": tilt_cfg,
                "flat_line": fl_cfg,
                "entry": entry,
                "lat": lat,
                "lon": lon,
                "latlon_source": latlon_source,
                "water_depth_m": water_depth_m,
                "data_vars": data_vars,
                "pressure_var": pressure_var,
                "has_pressure": pressure_var is not None,
                "ds": ds,
            }
        )
    return instruments


def _is_pressure_bad(info: dict[str, Any]) -> bool:
    """Return True when the mooring YAML flags the instrument's own pressure as bad."""
    return info["qc_flags"].get("pressure", 0) >= 3


def _split_pressure_sources(
    instruments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a mooring's instruments into pressure sources and interpolation targets."""
    sources = [i for i in instruments if i["has_pressure"] and not _is_pressure_bad(i)]
    targets = [i for i in instruments if not i["has_pressure"] or _is_pressure_bad(i)]

    if not sources and targets:
        log.warning(
            "No reliable pressure sources found — pressure interpolation skipped"
        )
    log.info(
        f"Pressure sources ({len(sources)}): "
        + ", ".join(
            f"{s['instrument']} {s['serial']} hab={s['hab']:.1f}m" for s in sources
        )
    )
    if targets:
        log.info(
            f"Pressure targets ({len(targets)}): "
            + ", ".join(
                f"{t['instrument']} {t['serial']} hab={t['hab']:.1f}m"
                + (
                    f" [pressure_qc={t['qc_flags']['pressure']}→replace]"
                    if _is_pressure_bad(t)
                    else " [no pressure]"
                )
                for t in targets
            )
        )
    return sources, targets


def _interpolate_if_stuck(
    ds: xr.Dataset,
    info: dict[str, Any],
    sources: list[dict[str, Any]],
    target_time: np.ndarray,
    history_notes: list[str],
) -> xr.Dataset:
    """Interpolate a source's pressure when QC has flagged most of it bad."""
    # Catches stuck-sensor faults visible only after the flat-line and gross-range
    # tests, which the pre-scan source/target split cannot see. A source already used
    # for an earlier target passed it bad pressure; the remedy is pressure_qc: 4.
    if "pressure_qc" not in ds.data_vars or "pressure" not in ds.data_vars:
        return ds
    serial = info["serial"]
    pqc = ds["pressure_qc"].values.astype(np.int8)
    n_total = len(pqc)
    n_bad = int(np.sum(np.isin(pqc, [4, 9])))
    frac_bad = n_bad / n_total if n_total > 0 else 0.0
    # 70 %, not 100 %: the flat-line test leaves the first fail_n samples unflagged.
    if frac_bad <= 0.70:
        return ds
    other_sources = [s for s in sources if s["serial"] != serial]
    if not other_sources:
        log.warning(
            f"{serial} — {frac_bad:.0%} of pressure flagged bad but no other "
            f"pressure sources on this mooring; pressure remains bad-flagged."
        )
        return ds
    log.warning(
        f"{serial} — {frac_bad:.0%} of pressure values flagged bad by QC "
        f"(flat-line or gross-range); replacing entire pressure record with "
        f"interpolation from {len(other_sources)} source(s). Re-run stage 3 on the full "
        f"mooring with 'pressure_qc: 4' in the YAML entry for {serial} so it is "
        f"excluded from sources from the start."
    )
    # interpolate_pressure records pressure_orig_qc from the entry's flag: set it to the
    # actual QC reason (4), not the mooring YAML's default of 0.
    info["qc_flags"]["pressure"] = 4
    ds, method = interpolate_pressure(
        ds, info, other_sources, target_time, pressure_bad_flag=True
    )
    history_notes.append(
        f"stage3 pressure interpolated post-QC "
        f"({frac_bad:.0%} pressure_qc=4/9 after QC) — {method}"
    )
    return ds


def _format_qc_summary(ds: xr.Dataset) -> list[str]:
    """Return one ``var=[good=…, suspect=…]`` count line per QC variable."""
    summary = []
    for v in sorted(ds.data_vars):
        if v.endswith("_qc") and not v.endswith("_orig_qc"):
            counts = np.bincount(
                ds[v].values.astype(np.int8).clip(0, 9).ravel(), minlength=10
            )
            parts = [f"good={int(counts[1])}"]
            for label, flag in (
                ("suspect", 3),
                ("bad", 4),
                ("interp", 8),
                ("missing", 9),
            ):
                if counts[flag]:
                    parts.append(f"{label}={int(counts[flag])}")
            summary.append(f"{v}=[{', '.join(parts)}]")
    return summary


def _fill_cf_attrs(ds: xr.Dataset) -> None:
    """Fill missing CF attributes from the registry and normalize QC attributes."""
    for vname in params.VARIABLES:
        if vname in ds.data_vars:
            for key, value in params.cf_attrs(vname).items():
                ds[vname].attrs.setdefault(key, value)
    # After the CF pass, so each parent's standard_name is available. *_orig_qc (the
    # pre-interpolation flag) is skipped here: interpolate_pressure sets its flag attrs.
    for qv in [
        v for v in ds.data_vars if v.endswith("_qc") and not v.endswith("_orig_qc")
    ]:
        set_qc_attrs(ds, qv[:-3])


def _declination_clause(value: Any) -> str:
    """Return the history clause for a ``magnetic_declination`` attribute value."""
    if value is None:
        return "declination=0.00° (assumed 0)"
    if isinstance(value, str):
        # "UNK": no mooring position, so no declination was applied.
        return "declination not applied (position unknown)"
    return f"declination={float(value):.2f}° (ppigrf IGRF)"


def _stage3_instrument(
    info: dict[str, Any],
    sources: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    *,
    caldip_dir: str | None = None,
) -> xr.Dataset:
    """Run pressure interpolation, QC, rotation and derivations on one instrument."""
    # Steps run in the order listed in the module docstring.
    is_target = any(t is info for t in targets)
    pressure_bad_flag = _is_pressure_bad(info)
    is_adcp = info.get("instrument", "").lower() == "adcp"
    log.info(f"-->   Processing {info.get('instrument', 'unknown')} {info['serial']}")

    # Canonicalise units on the way in: a pre-0.5.0 stage-2 file carries the reader's
    # spellings (degC, S/m, …); this rewrites them to the canonical (and converts S/m
    # conductivity ×10) so QC thresholds and salinity see one unit.
    ds = to_canonical_units(info["ds"].copy(deep=True), stage=3)

    target_time = ds["time"].values
    history_notes: list[str] = []
    if caldip_dir is not None:
        # Front-of-Stage-3 caldip correction is the first step; record the null action
        # in the timestamped history so the stub → real transition is dated.
        history_notes.append(
            "caldip: correction requested but not implemented (stub), "
            "science data unchanged"
        )

    if is_target and sources:
        ds, method = interpolate_pressure(
            ds, info, sources, target_time, pressure_bad_flag
        )
        history_notes.append(f"stage3 pressure interpolated (pressure_qc=8) — {method}")

    # Salinity before QC so it gets range-tested. An imported file's own salinity is
    # kept beside ours, with the originator's description of it.
    theirs = _originator_salinity(ds)
    ds = compute_salinity_data(ds)
    if theirs is not None and "salinity" in ds.data_vars:
        ds, note = _keep_originator_salinity(ds, theirs)
        history_notes.append(note)

    gr_cfg = info["gross_range"]
    # ADCP velocity is 2-D; gross_range_test expects 1-D, so the ENU keys are left to
    # the ADCP velocity QC below.
    scalar_gr = (
        {k: v for k, v in gr_cfg.items() if k not in _ENU_KEYS} if is_adcp else gr_cfg
    )
    ds = apply_qc_tests(ds, scalar_gr, info["spike"], flat_line=info["flat_line"])

    if not is_target:
        ds = _interpolate_if_stuck(ds, info, sources, target_time, history_notes)

    ds = merge_salinity_parent_qc(ds)

    # BEAM / XYZ → ENU must run before tilt QC so the ENU velocities exist to flag.
    coord_sys_before = ds.attrs.get("coordinate_system", "ENU")
    latlon_source = info.get("latlon_source", "unknown")
    ds = apply_beam_to_enu(
        ds,
        info["entry"],
        info["lat"],
        info["lon"],
        latlon_source=latlon_source,
    )
    if coord_sys_before == "ENU":
        # Already ENU (configured internally): still apply the declination rotation.
        ds = apply_declination_to_enu(
            ds,
            info["lat"],
            info["lon"],
            latlon_source=latlon_source,
        )

    if ds.attrs.get("coordinate_system") == "ENU":
        if is_adcp:
            adcp_qc = params.QC_ADCP
            ds = apply_adcp_velocity_qc(
                ds,
                gr_cfg=gr_cfg,
                prcnt_gd_bad=adcp_qc["percent_good_bad"],
                prcnt_gd_suspect=adcp_qc["percent_good_suspect"],
                error_vel_threshold=adcp_qc["error_velocity_threshold"],
            )
            ds = compute_adcp_bin_pressure(ds, info["lat"])
            ds = apply_adcp_seabed_qc(
                ds,
                water_depth_m=info.get("water_depth_m", 0.0),
                lat=info["lat"],
            )
            ds = apply_adcp_surface_qc(ds, lat=info["lat"])
        else:
            ds = apply_enu_velocity_qc(ds, gr_cfg)

    # Tilt QC flags every velocity variable when pitch+roll tilt exceeds the threshold
    # (not per bin). ADCP skips it: percent_good and error_velocity carry ensemble quality.
    tilt_cfg = info["tilt"]
    if is_adcp:
        history_notes.append(
            "tilt QC skipped for ADCP (percent_good+error_velocity QC applied instead)"
        )
    else:
        ds, n_tilt_susp, n_tilt_bad = apply_tilt_qc(ds, tilt_cfg)
        if n_tilt_susp or n_tilt_bad:
            history_notes.append(
                f"tilt QC (tilt≥{tilt_cfg['suspect_threshold']}°→suspect, "
                f"tilt≥{tilt_cfg['fail_threshold']}°→bad): "
                f"suspect={n_tilt_susp}, bad={n_tilt_bad}"
            )

    # Worst flag across the three ENU components, written back to all three, so masking
    # on any one component gives the same result.
    if ds.attrs.get("coordinate_system") == "ENU":
        ds = unify_velocity_qc(ds)

    if ds.attrs.get("coordinate_system") == "ENU" and coord_sys_before != "ENU":
        history_notes.append(
            f"BEAM→ENU rotation applied ({ds.attrs['coordinate_system_source']}), "
            + _declination_clause(ds.attrs.get("magnetic_declination"))
        )
    elif ds.attrs.get("magnetic_declination") == "UNK":
        # No rotation happened (native ENU, or a BEAM dataset that stayed BEAM), but
        # declination was not applied — record it so the history matches the "UNK" attr.
        history_notes.append(_declination_clause("UNK"))

    # The originator's flags, translated to our table by meaning (kept separate
    # from ours: neither overwrites the other).
    ds, note = originator.add_mapped(ds, label=str(info["serial"]))
    if note:
        history_notes.append(note)

    qc_summary = _format_qc_summary(ds)
    if qc_summary:
        history_notes.append("QARTOD gross-range+spike: " + " | ".join(qc_summary))
    append_history(ds, " | ".join(history_notes))

    # The QC treatment, reconstructible from the file alone: flag convention, worst-wins
    # merge order, and the QARTOD → OceanSITES ingest remap.
    priority_order = " < ".join(
        str(f)
        for f, _ in sorted(params.QC_MERGE_PRIORITY.items(), key=lambda kv: kv[1])
    )
    ds.attrs["qc_flag_convention"] = params.QC_CONVENTION
    ds.attrs["qc_merge_rule"] = (
        "worst flag wins; priority weakest to strongest: " + priority_order
    )
    ds.attrs["qc_ingest_remap"] = (
        "QARTOD not_evaluated(2) -> OceanSITES unknown(0) at test ingest"
    )

    ds = derive_oxygen_saturation(ds)
    _fill_cf_attrs(ds)

    # caldip stub provenance: a machine-readable marker beside the dated history note.
    if caldip_dir is not None:
        ds.attrs["caldip_applied"] = CALDIP_STUB_APPLIED

    # OceanSITES data_mode: provisional until delayed mode is earned (caldip applied
    # and final); nothing here earns it yet.
    ds.attrs["data_mode"] = "P"
    ds = drop_all_zero_vars(ds, ["amplitude_beam", "analog_input_"])
    # Canonical units on everything stage 3 derived (salinity, ENU velocities, oxygen).
    ds = to_canonical_units(ds, stage=3)
    # Salinity, ENU velocities, oxygen saturation: measured by the same instrument.
    ds = sensors.link_derived(ds)
    _set_processing_level(ds)
    log.info(f"{info['serial']}: {'; '.join(qc_summary)}")
    return ds


#: OceanSITES reference table 3 (``processing_level``) for what stage 3 did: QC
#: tests applied and flagged, values kept; pressure taken from other instruments.
PROCESSING_LEVEL_QC = "Ranges applied, bad data flagged"
PROCESSING_LEVEL_INTERPOLATED = "Data interpolated"


def _set_processing_level(ds: xr.Dataset) -> None:
    """Write ``processing_level`` (OceanSITES reference table 3) on each QC'd variable.

    A variable with its own ``<var>_qc`` was range-tested and flagged; pressure
    with any interpolated value (8) says so instead. The originator's level, when
    the file had one, stays in ``originator_processing_level``.
    """
    for name in list(ds.data_vars):
        if f"{name}_qc" not in ds.data_vars or ds[name].dtype.kind not in "fiu":
            continue
        level = PROCESSING_LEVEL_QC
        if name == "pressure" and np.any(ds["pressure_qc"].values == 8):
            level = PROCESSING_LEVEL_INTERPOLATED
        ds[name].attrs["processing_level"] = level


def stage3(
    datasets: dict[str, xr.Dataset],
    mooring_dict: dict[str, Any] | None = None,
    *,
    mooring: dict[str, Any] | None = None,
    caldip_dir: str | None = None,
) -> dict[str, xr.Dataset]:
    """Apply pressure interpolation, QC, coordinate rotation and derived variables to a mooring.

    Pure function over datasets: no file I/O. Pressure is interpolated onto
    instruments that lack it (or whose pressure the mooring YAML flags bad) from the
    mooring's other instruments, so the whole mooring is processed together.

    Parameters
    ----------
    datasets : dict of str to xarray.Dataset
        Stage-2 datasets keyed by cleaned serial, as :func:`read_mooring` returns them.
    mooring_dict : dict
        The loaded mooring YAML.
    caldip_dir : str or None, optional
        Calibration-dip directory. The correction is not yet implemented: supplying it
        leaves the science data unchanged and stamps ``caldip_applied`` on each output.
    mooring : dict, optional
        Deprecated name of *mooring_dict*; removed in 0.8.0.

    Returns
    -------
    dict of str to xarray.Dataset
        Stage-3 datasets keyed by serial, each marked ``processing_stage = 3`` and
        holding the values its file will hold, so each equals its file read back.
        Entries marked ``skip`` or without ``hab`` are left out.

    Raises
    ------
    oceanarray.exceptions.StageError
        If any dataset is not at stage 2.
    """
    mooring_dict = _deprecated.resolve_keyword(
        "oceanarray.stage3(mooring=)", mooring_dict, mooring, required="mooring_dict"
    )
    _stage.require(datasets, 2, 3)
    instruments = _build_instrument_table(datasets, mooring_dict)
    # The caldip correction, when implemented, applies here — before the split — so a
    # sensorless target interpolates from already-corrected neighbours.
    sources, targets = _split_pressure_sources(instruments)
    return {
        info["serial"]: _stage.stamp(
            as_stored(
                _stage3_instrument(info, sources, targets, caldip_dir=caldip_dir)
            ),
            3,
        )
        for info in instruments
    }


def _source_provenance(nc_path: Path) -> dict[str, str]:
    """Return the stage-2 file's name, modification time and SHA-256 as attributes."""
    mtime = datetime.datetime.fromtimestamp(
        nc_path.stat().st_mtime, datetime.UTC
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with nc_path.open("rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    return {
        "stage2_source_file": nc_path.name,
        "stage2_source_mtime": mtime,
        "stage2_source_sha256": sha,
    }


class Stage3Processor:
    """Pressure interpolation + QARTOD QC for all mooring instruments.

    .. deprecated:: 0.6.0
        Removed in 0.7.0; see the migration page.
    """

    def __init__(
        self,
        *,
        proc_dir: str,
    ) -> None:
        """Apply QC flags, coordinate rotation, and derived variables to mooring data.

        Reads stage2 CF-NetCDF files and applies: QARTOD gross-range and spike QC
        flags for scalar sensors; pressure interpolation for instruments that lack a
        pressure sensor; BEAM→XYZ→ENU coordinate rotation for Aquadopp current meters
        (using the instrument T matrix and heading/pitch/roll); and magnetic declination
        correction so velocities are referenced to true north.  Writes
        ``{mooring}_{serial}_stage3.nc``.

        Parameters
        ----------
        proc_dir : str
            Cruise-level processed output directory. The pipeline appends
            ``/{mooring}/`` internally (see :meth:`oceanarray.Paths.mooring_dir`).

        """
        from oceanarray import _deprecated

        _deprecated.warn("oceanarray.pipeline.stage3.Stage3Processor")
        self._proc_dir = Path(proc_dir).expanduser()
        self.log_file = None

    def _setup_logging(self, mooring_name: str, output_path: Path) -> None:
        from oceanarray.logger import attach_stage_handlers, setup_stage_logging

        self.log_file = setup_stage_logging(mooring_name, "stage3", output_path)
        self._log_handlers = attach_stage_handlers(self.log_file)

    def _log(self, *args: Any, **kwargs: Any) -> None:
        print(*args, **kwargs)
        if self.log_file:
            try:
                with self.log_file.open("a", encoding="utf-8") as f:
                    print(*args, **kwargs, file=f)
            except OSError:
                pass

    @with_stage_handlers
    def process_mooring(
        self,
        mooring_name: str,
        serials: list[str] | None = None,
        force: bool = False,
        dry_run: bool = False,
        caldip_dir: str | None = None,
    ) -> bool:
        """Run Stage 3 QC and pressure interpolation for all instruments on a mooring.

        ``caldip_dir`` is the entry point for the planned front-of-Stage-3 calibration-dip
        correction. It is **not yet implemented** (:mod:`oceanarray.pipeline.calibration` is a
        stub): supplying it logs a warning and continues, so the science data is unchanged, but
        each output stamps a ``caldip_applied`` attribute recording that only the stub ran, so
        the first real correction is visible as a change in that attribute. A caldip request
        also reprocesses existing ``_stage3.nc`` outputs (like ``force``) so the marker reaches
        every instrument, not only freshly-written ones. ``None`` (the default) leaves the
        output exactly as before.
        """
        proc_dir = paths.Paths(proc_dir=self._proc_dir).mooring_dir(mooring_name)
        if not proc_dir.exists():
            raise MooringError(
                f"{mooring_name}: processing directory not found: {proc_dir}"
            )

        self._setup_logging(mooring_name, proc_dir)
        mode = "DRY RUN — " if dry_run else ""
        self._log(f"{mode}Stage 3 (pressure interpolation + QC) for: {mooring_name}")
        if caldip_dir is not None:
            self._log(
                "  WARNING: caldip correction requested but not implemented; Stage 3 "
                "science data is unchanged (see the caldip_applied attribute)."
            )

        mooring_path = proc_dir / f"{mooring_name}.mooring.yaml"
        if not mooring_path.exists():
            raise MooringError(
                f"{mooring_name}: mooring YAML not found: {mooring_path}"
            )

        try:
            mooring_dict = _mooring.load(mooring_path)
            if dry_run:
                datasets = read_mooring(
                    mooring_dict,
                    stage=2,
                    serials=serials,
                    allow_missing=True,
                    mooring_dir=proc_dir,
                )
                if not datasets:
                    if serials:
                        self._log(
                            "  No instruments found matching serial(s): "
                            + ", ".join(serials)
                        )
                        return True
                    self._log("ERROR: No _stage2 datasets found for this mooring")
                    return False
                instruments = _build_instrument_table(datasets, mooring_dict)
                _, targets = _split_pressure_sources(instruments)
                for info in instruments:
                    action = (
                        "interpolate pressure + QC"
                        if any(t is info for t in targets)
                        else "QC only"
                    )
                    self._log(f"    {info['serial']} ({info['instrument']}): {action}")
                self._log("DRY RUN complete")
                return True
            _stage3_files(
                mooring_dict,
                proc_dir,
                serials=serials,
                force=force,
                caldip_dir=caldip_dir,
                log=self._log,
            )
        except OceanarrayError as e:
            self._log(f"ERROR: {e}")
            return False
        except Exception as e:  # noqa: BLE001  — intentional broad catch at I/O boundary
            self._log(f"ERROR: Stage 3 failed: {e}")
            return False
        return True


def _stage3_files(
    mooring: dict[str, Any],
    mooring_dir: Path,
    *,
    serials: list[str] | None = None,
    force: bool = False,
    caldip_dir: str | Path | None = None,
    exclude: set[str] | frozenset[str] = frozenset(),
    log: Callable[..., None] = print,
) -> _stage.StageRun:
    """Run stage 3 over one mooring's stage-2 files and write the stage-3 files."""
    run = _stage.StageRun()
    # Every stage-2 file is read, whatever *serials* asks for, because an instrument
    # without its own pressure takes it from the others; only *serials* are written.
    datasets = read_mooring(
        mooring, stage=2, allow_missing=True, mooring_dir=mooring_dir
    )
    # Failed earlier in this run: neither written nor used as a pressure source.
    for serial in sorted(exclude & set(datasets)):
        log(f"  SKIP {serial}: failed earlier in this run; not a pressure source")
        datasets.pop(serial).close()
    if not datasets:
        raise MooringError("No _stage2 datasets found for this mooring")

    # No per-instrument catch: stage 3 runs over the whole mooring, so any failure of
    # the transform propagates.
    results = stage3(datasets, mooring, caldip_dir=caldip_dir)
    if not results:
        raise MooringError("No stage-2 datasets with hab values")
    wanted = {safe_serial(s) for s in serials} if serials else set(results)
    if not wanted & set(results):
        log(f"  No instruments found matching serial(s): {', '.join(serials or [])}")
        return run
    name = mooring["name"]

    def _stage2_path(serial: str) -> Path:
        """Return *serial*'s stage-2 file."""
        return paths.stage_file(
            mooring_dir,
            mooring_name=name,
            instrument=results[serial].attrs["instrument"],
            serial=serial,
            stage=2,
        )

    to_write: dict[str, xr.Dataset] = {}
    for serial, ds in results.items():
        out_path = paths.stage_file(
            mooring_dir,
            mooring_name=name,
            instrument=ds.attrs["instrument"],
            serial=serial,
            stage=3,
        )
        # Provenance: a target records the stage-2 files its pressure came from; a
        # kept output whose sources have since changed gets a WARNING.
        sources = _pressure_source_files(ds, _stage2_path)
        if serial not in wanted:
            _warn_if_sources_changed(out_path, sources, log)
            continue
        run.matched += 1
        # A caldip request invalidates an existing output like force, so the
        # (stub) marker reaches every output.
        if out_path.exists() and not force and caldip_dir is None:
            log(f"  SKIP (exists): {out_path.name}  (--force to overwrite)")
            _warn_if_sources_changed(out_path, sources, log)
            run.files.append(out_path)
            continue
        ds.attrs.update(_source_provenance(_stage2_path(serial)))
        if sources:
            ds["pressure"].attrs["pressure_source_files"] = sources
        to_write[serial] = ds
        run.files.append(out_path)
    run.written = write_mooring(to_write, mooring_dir, overwrite=True)
    log(
        f"Stage 3 complete: {len(results)} instrument(s) processed, "
        f"{len(to_write)} written"
    )
    return run


def _pressure_source_files(ds: xr.Dataset, stage2_path: Callable[[str], Path]) -> str:
    """Return the stage-2 files *ds*'s pressure was interpolated from, with hashes."""
    if "pressure" not in ds:
        return ""
    serials = ds["pressure"].attrs.get("pressure_source_serials", "").split()
    entries = []
    for serial in serials:
        prov = _source_provenance(stage2_path(serial))
        entries.append(
            f"{prov['stage2_source_file']} sha256={prov['stage2_source_sha256']} "
            f"mtime={prov['stage2_source_mtime']}"
        )
    return "; ".join(entries)


def _warn_if_sources_changed(
    out_path: Path, sources: str, log: Callable[..., None]
) -> None:
    """WARN when a kept stage-3 file's pressure came from sources other than today's."""
    if not out_path.exists():
        return
    with xr.open_dataset(out_path, decode_times=False) as kept:
        recorded = (
            kept["pressure"].attrs.get("pressure_source_files", "")
            if "pressure" in kept
            else ""
        )
    if recorded == sources:
        return
    log(
        f"  WARNING: {out_path.name}: its pressure was interpolated from "
        f"{recorded or 'sources not recorded'}; today it would come from "
        f"{sources or 'its own sensor'}. Re-run stage 3 for this instrument "
        "(force) or the whole mooring."
    )
