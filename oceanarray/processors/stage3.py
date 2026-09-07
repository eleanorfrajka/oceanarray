"""Stage 3: pressure interpolation + QARTOD QC (gross-range and spike tests).

Processing order per instrument
---------------------------------
1. Load ``_stage2.nc``.
2. **Pressure interpolation** (targets only — instruments lacking pressure or
   whose pressure is flagged bad in the YAML):
   a. Near-neighbour if any source has ``|Δhab|`` ≤ ``HAB_THRESHOLD``.
   b. Weighted bracketing from the closest source above and below.
   c. Extrapolation (with WARNING) when target is outside all source habs.
   Interpolated pressure gets ``pressure_qc = 8`` (interpolated_value).
3. **QARTOD gross-range test** on temperature, conductivity, pressure, and
   velocity components.  Flags 4 (bad) or 3 (suspect) based on thresholds in
   ``parameters.QC_GROSS_RANGE`` (overrideable per mooring / per instrument in
   YAML via a ``qc_ranges`` key).
4. **QARTOD spike test** on the same variables.  Flags from
   ``parameters.QC_SPIKE`` (overrideable via ``qc_spike`` in YAML).
5. Merge all QC flags using priority order: 9 > 4 > 3 > 8 > 2 > 1.
6. Write ``_stage3.nc`` for **all** instruments (not only pressure targets).

Flag combination priority
--------------------------
Missing (9) > Bad (4) > Suspect (3) > Interpolated (8) > Prob-good (2) > Good (1)

This means that if interpolated pressure also fails the range test it is
flagged 4 (bad), not 8 (interpolated).

YAML configuration keys
------------------------
Top-level (mooring-wide):

``qc_ranges``
    mapping of variable → {fail_span, suspect_span}
``qc_spike``
    mapping of variable → {suspect_threshold, fail_threshold}

Per-instrument (in a clamp entry):

``qc_ranges``
    same structure; overrides the mooring-level setting for the variables
    listed (others fall back to mooring/global defaults)
``qc_spike``
    same structure
``pressure_qc``
    int — mark this instrument's own pressure as bad (≥3) so stage3 replaces
    it with an interpolated value.
"""

from __future__ import annotations

import datetime
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import xarray as xr
import yaml

from oceanarray import paths
from oceanarray.paths import safe_serial
from oceanarray.utilities import (
    cast_output_dtypes,
    drop_all_zero_vars,
    extract_inline_instruments,
)

from oceanarray.processors.pressure import (
    compute_adcp_bin_pressure,
    interpolate_pressure,
)
from oceanarray.processors.qc import (
    apply_enu_velocity_qc,
    apply_qc_tests,
    apply_tilt_qc,
    compute_salinity_data,
    derive_oxygen_saturation,
    ensure_conductivity_units,
    load_qc_config,
    merge_salinity_parent_qc,
    set_qc_attrs,
    unify_velocity_qc,
)
from oceanarray.processors.coordinate import (
    apply_adcp_seabed_qc,
    apply_adcp_surface_qc,
    apply_adcp_velocity_qc,
    apply_beam_to_enu,
    apply_declination_to_enu,
)


def _safe_serial(serial: Any) -> str:
    """Return a filename-safe serial token (see :func:`oceanarray.paths.safe_serial`)."""
    return safe_serial(serial)


class Stage3Processor:
    """Pressure interpolation + QARTOD QC for all mooring instruments."""

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
            ``/{mooring}/`` internally (see :func:`oceanarray.paths.mooring_proc_dir`).

        """
        self._proc_dir = Path(proc_dir)
        self.log_file = None

    def _setup_logging(self, mooring_name: str, output_path: Path) -> None:
        from oceanarray.logger import setup_stage_logging

        self.log_file = setup_stage_logging(mooring_name, "stage3", output_path)

    def _log(self, *args: Any, **kwargs: Any) -> None:
        print(*args, **kwargs)
        if self.log_file:
            try:
                with open(self.log_file, "a") as f:
                    print(*args, **kwargs, file=f)
            except OSError:
                pass

    def _get_proc_dir(self, mooring_name: str) -> Path:
        return paths.mooring_proc_dir(self._proc_dir, mooring_name)

    # ------------------------------------------------------------------
    def process_mooring(
        self,
        mooring_name: str,
        serials: Optional[List[str]] = None,
        force: bool = False,
        dry_run: bool = False,
        caldip_dir: Optional[str] = None,
    ) -> bool:
        """Run Stage 3 QC and pressure interpolation for all instruments on a mooring.

        When ``caldip_dir`` is ``None`` (the default) no calibration-dip correction is
        applied and the output is unchanged. Supplying ``caldip_dir`` is the planned entry
        point for the front-of-Stage-3 caldip correction; the per-cast reader exists
        (:mod:`oceanarray.processors.caldip`) but the application step is not yet wired, so a
        non-``None`` value raises rather than silently skip a requested correction.
        """
        if caldip_dir is not None:
            msg = (
                "caldip correction at the front of Stage 3 is not yet implemented; "
                "the per-cast reader is available via oceanarray.processors.caldip. "
                "See the caldip-apply plan for the remaining application work."
            )
            raise NotImplementedError(msg)
        proc_dir = self._get_proc_dir(mooring_name)
        if not proc_dir.exists():
            print(f"ERROR: Processing directory not found: {proc_dir}")
            return False

        self._setup_logging(mooring_name, proc_dir)
        mode = "DRY RUN — " if dry_run else ""
        self._log(f"{mode}Stage 3 (pressure interpolation + QC) for: {mooring_name}")

        config_file = proc_dir / f"{mooring_name}.mooring.yaml"
        if not config_file.exists():
            self._log(f"ERROR: Config not found: {config_file}")
            return False

        with open(config_file) as f:
            mooring_config = yaml.safe_load(f)

        instrument_list = list(
            mooring_config.get("clamp", mooring_config.get("instruments", []))
        )
        instrument_list += extract_inline_instruments(mooring_config.get("inline", []))

        # ── Mooring location for BEAM→ENU declination ──────────────────
        from oceanarray.utilities import parse_latlon_with_source

        _mooring_lat, _mooring_lon, _latlon_source = parse_latlon_with_source(
            mooring_config
        )

        _water_depth_m = float(mooring_config.get("waterdepth") or 0.0)

        # ── Build instrument table ──────────────────────────────────────
        instruments: List[Dict[str, Any]] = []
        for entry in instrument_list:
            if not isinstance(entry, dict):
                continue
            serial = _safe_serial(entry.get("serial", ""))
            instr_type = entry.get("instrument", "unknown")
            if entry.get("skip"):
                reason = entry.get("skip_reason", "marked skip:true in YAML")
                self._log(f"  SKIP {instr_type} {serial}: {reason}")
                continue
            hab = entry.get("hab")
            if hab is None:
                self._log(f"  WARNING: serial {serial} has no 'hab' — skipping")
                continue
            nc_path = proc_dir / instr_type / f"{mooring_name}_{serial}_stage2.nc"
            if not nc_path.exists():
                continue
            qc_flags = {
                key[:-3]: int(val)
                for key, val in entry.items()
                if key.endswith("_qc") and isinstance(val, int)
            }
            gr_cfg, sp_cfg, tilt_cfg, fl_cfg = load_qc_config(mooring_config, entry)
            # Parse optional hab_segments: list of {from: ISO, hab: float}
            # Stored as sorted list of (np.datetime64, float) breakpoints.
            raw_segs = entry.get("hab_segments", [])
            hab_segments: List[tuple] = []
            for seg in raw_segs:
                try:
                    bp = np.datetime64(seg["from"], "ns")
                    hab_segments.append((bp, float(seg["hab"])))
                except (KeyError, ValueError) as _e:
                    self._log(
                        f"  WARNING: serial {serial} hab_segments entry invalid"
                        f" ({seg}): {_e} — skipped"
                    )
            hab_segments.sort(key=lambda x: x[0])

            instruments.append(
                {
                    "serial": serial,
                    "instrument": instr_type,
                    "hab": float(hab),
                    "hab_segments": hab_segments,
                    "nc_path": nc_path,
                    "qc_flags": qc_flags,
                    "gross_range": gr_cfg,
                    "spike": sp_cfg,
                    "tilt": tilt_cfg,
                    "flat_line": fl_cfg,
                    "entry": entry,
                    "lat": _mooring_lat,
                    "lon": _mooring_lon,
                    "latlon_source": _latlon_source,
                    "water_depth_m": _water_depth_m,
                }
            )

        if not instruments:
            self._log("ERROR: No _stage2.nc files found with hab values")
            return False

        # Filter by serial early for targeted reruns
        if serials:
            safe_req = {_safe_serial(s) for s in serials}
            instruments = [i for i in instruments if i["serial"] in safe_req]
            if not instruments:
                self._log(
                    f"  No instruments found matching serial(s): {', '.join(serials)}"
                )
                return True

        # ── Scan what pressure variables each instrument has ────────────
        def _find_pressure_var(data_vars: set) -> Optional[str]:
            if "pressure" in data_vars:
                return "pressure"
            for cand in sorted(
                v
                for v in data_vars
                if v.startswith("pressure_") and not v.endswith("_qc")
            ):
                return cand
            return None

        for info in instruments:
            try:
                with xr.open_dataset(info["nc_path"], decode_timedelta=False) as _ds:
                    info["data_vars"] = set(_ds.data_vars)
            except Exception as e:  # noqa: BLE001  — missing file skipped; mooring continues
                self._log(f"  WARNING: Could not open {info['nc_path'].name}: {e}")
                info["data_vars"] = set()
            info["pressure_var"] = _find_pressure_var(info["data_vars"])
            info["has_pressure"] = info["pressure_var"] is not None
            info["ds"] = None

            for varname, qc_val in info["qc_flags"].items():
                if varname not in info["data_vars"]:
                    self._log(
                        f"  WARNING: serial {info['serial']}: "
                        f"'{varname}_qc: {qc_val}' in YAML but '{varname}' "
                        f"not found in {info['nc_path'].name} — ignored"
                    )

        def pressure_bad(info: Dict[str, Any]) -> bool:
            return info["qc_flags"].get("pressure", 0) >= 3

        sources = [i for i in instruments if i["has_pressure"] and not pressure_bad(i)]
        targets = [i for i in instruments if not i["has_pressure"] or pressure_bad(i)]

        if not sources and targets:
            self._log(
                "WARNING: No reliable pressure sources found — pressure interpolation skipped"
            )

        self._log(
            f"  Pressure sources ({len(sources)}): "
            + ", ".join(
                f"{s['instrument']} {s['serial']} hab={s['hab']:.1f}m" for s in sources
            )
        )
        if targets:
            self._log(
                f"  Pressure targets ({len(targets)}): "
                + ", ".join(
                    f"{t['instrument']} {t['serial']} hab={t['hab']:.1f}m"
                    + (
                        f" [pressure_qc={t['qc_flags']['pressure']}→replace]"
                        if pressure_bad(t)
                        else " [no pressure]"
                    )
                    for t in targets
                )
            )

        if dry_run:
            for info in instruments:
                is_target = info in targets
                l3 = info["nc_path"].with_name(
                    info["nc_path"].name.replace("_stage2.nc", "_stage3.nc")
                )
                action = "interpolate pressure + QC" if is_target else "QC only"
                self._log(
                    f"    {info['serial']} ({info['instrument']}): {action} → {l3.name}"
                )
            self._log("DRY RUN complete")
            return True

        # ── Load source datasets for pressure interpolation ─────────────
        for src in sources:
            try:
                src["ds"] = xr.open_dataset(
                    src["nc_path"], decode_timedelta=False
                ).load()
            except Exception as e:  # noqa: BLE001  — one bad source must not abort pressure interp
                self._log(
                    f"  WARNING: Could not load source {src['nc_path'].name}: {e}"
                )
                src["ds"] = None

        # ── Process every instrument ────────────────────────────────────
        success_count = 0
        for info in instruments:
            ok = self._process_instrument(info, sources, targets, force=force)
            if ok:
                success_count += 1

        for info in instruments:
            if info.get("ds") is not None:
                info["ds"].close()

        self._log(
            f"Stage 3 complete: {success_count}/{len(instruments)} instruments written"
        )
        if not instruments:
            self._log("No instruments matched — nothing processed.")
            return False
        # Partial success is still success: instruments that processed are written
        # and the record summary reports which succeeded and which failed.
        return success_count > 0

    # ------------------------------------------------------------------
    def _process_instrument(
        self,
        info: Dict[str, Any],
        sources: List[Dict[str, Any]],
        targets: List[Dict[str, Any]],
        force: bool = False,
    ) -> bool:
        """Apply Stage 3 processing to one instrument's Stage 2 NetCDF.

        Steps applied (where applicable to the instrument type):

        1. Pressure interpolation — targets without a reliable pressure sensor
           have pressure interpolated from *sources* (neighbours on the mooring).
        2. Conductivity unit normalisation and practical salinity computation
           (CTD/microcat instruments only).
        3. QARTOD QC tests (gross-range, spike) using thresholds from the config.
        4. BEAM→ENU or XYZ→ENU coordinate transformation with magnetic declination
           correction (current meters / Aquadopp).
        5. Tilt QC — velocity flagged suspect/bad when pitch or roll exceed
           configured thresholds.
        6. History attribute updated with all processing steps applied.

        Writes ``{mooring}_{serial}_stage3.nc`` alongside the Stage 2 file.
        Skips if the output already exists and *force* is False.

        Returns True on success or skip, False on error.
        """
        nc_path = info["nc_path"]
        serial = info["serial"]
        l3_path = nc_path.with_name(nc_path.name.replace("_stage2.nc", "_stage3.nc"))

        if l3_path.exists():
            if not force:
                self._log(f"  SKIP (exists): {l3_path.name}  (--force to overwrite)")
                return True
            try:
                l3_path.unlink()
            except OSError as e:
                self._log(f"  ERROR: cannot remove existing {l3_path.name}: {e}")
                return False

        is_target = info in targets
        pressure_bad_flag = info["qc_flags"].get("pressure", 0) >= 3
        is_adcp = info.get("instrument", "").lower() == "adcp"

        self._log(
            f"-->   Processing {info.get('instrument', 'unknown')}: {nc_path.name}"
        )

        try:
            from oceanarray import parameters as params

            ds = xr.open_dataset(nc_path, decode_timedelta=False).load()

            # Record stage2 source-file provenance as named global attrs
            _st2_stat = nc_path.stat()
            _st2_mtime = datetime.datetime.utcfromtimestamp(
                _st2_stat.st_mtime
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            with open(nc_path, "rb") as _f:
                _st2_sha = hashlib.sha256(_f.read()).hexdigest()[:8]
            ds.attrs["stage2_source_file"] = nc_path.name
            ds.attrs["stage2_source_mtime"] = _st2_mtime
            ds.attrs["stage2_source_sha256"] = _st2_sha

            target_time = ds["time"].values
            history_notes = []

            # ── Pressure interpolation (targets only) ──────────────────
            if is_target and sources:
                ds, method = interpolate_pressure(
                    ds,
                    info,
                    sources,
                    target_time,
                    pressure_bad_flag,
                    log_fn=self._log,
                )
                history_notes.append(
                    f"stage3 pressure interpolated (pressure_qc=8) — {method}"
                )

            # ── Ensure conductivity is in mS/cm before QC ─────────────
            ds = ensure_conductivity_units(ds, log_fn=self._log)

            # ── Compute salinity data before QC so it gets range-tested ─
            ds = compute_salinity_data(ds, log_fn=self._log)

            # ── QARTOD QC tests (temperature, conductivity, salinity, …) ─
            gr_cfg = info["gross_range"]
            sp_cfg = info["spike"]
            fl_cfg = info["flat_line"]
            # For ADCP, exclude 2-D velocity vars from the ioos_qc path
            # (gross_range_test expects 1-D input); velocity QC is handled
            # below by _apply_adcp_velocity_qc.
            if is_adcp:
                _ENU_KEYS = {"east_velocity", "north_velocity", "up_velocity"}
                gr_cfg_scalar = {k: v for k, v in gr_cfg.items() if k not in _ENU_KEYS}
                ds = apply_qc_tests(ds, gr_cfg_scalar, sp_cfg, flat_line=fl_cfg)
            else:
                ds = apply_qc_tests(ds, gr_cfg, sp_cfg, flat_line=fl_cfg)

            # ── Post-QC pressure check ─────────────────────────────────
            # If QC (e.g. flat-line test) just flagged ALL pressure values as
            # bad and this instrument was not already a pressure target, treat
            # it as one now: interpolate from the other sources on the mooring.
            # This catches stuck-sensor faults that only become visible after
            # the flat-line / gross-range tests run — which is why the pre-scan
            # source/target classification cannot catch them.
            # Note: if this instrument was already used as a source for a
            # previously processed target, those targets will have received bad
            # pressure values.  To fix that, re-run stage 3 on the full mooring
            # once this instrument's fault is known (e.g. add pressure_qc: 4 to
            # the YAML so it is excluded from sources at the outset).
            if (
                not is_target
                and "pressure_qc" in ds.data_vars
                and "pressure" in ds.data_vars
            ):
                _pqc = ds["pressure_qc"].values.astype(np.int8)
                _n_total = len(_pqc)
                _n_bad = int(np.sum(np.isin(_pqc, [4, 9])))
                _frac_bad = _n_bad / _n_total if _n_total > 0 else 0.0
                # Trigger interpolation when > 70% of values are bad.  The flat-line
                # test leaves the first fail_n samples as flag 1/3 (window startup),
                # so strict all-bad checking would never fire for a fully stuck sensor.
                if _frac_bad > 0.70:
                    _other_sources = [s for s in sources if s["serial"] != serial]
                    if _other_sources:
                        self._log(
                            f"  WARNING: {serial} — {_frac_bad:.0%} of pressure values "
                            f"flagged bad by QC (flat-line or gross-range); replacing "
                            f"entire pressure record with interpolation from "
                            f"{len(_other_sources)} source(s). "
                            f"Re-run stage 3 on the full mooring with "
                            f"'pressure_qc: 4' in the YAML entry for {serial} "
                            f"so it is excluded from sources from the start."
                        )
                        # Temporarily set YAML pressure flag to 4 so that
                        # interpolate_pressure stores pressure_orig_qc = 4
                        # (the actual QC reason), not the YAML default of 0.
                        info["qc_flags"]["pressure"] = 4
                        ds, method = interpolate_pressure(
                            ds,
                            info,
                            _other_sources,
                            target_time,
                            pressure_bad_flag=True,
                            log_fn=self._log,
                        )
                        history_notes.append(
                            f"stage3 pressure interpolated post-QC "
                            f"({_frac_bad:.0%} pressure_qc=4/9 after QC) — {method}"
                        )
                    else:
                        self._log(
                            f"  WARNING: {serial} — {_frac_bad:.0%} of pressure flagged "
                            f"bad but no other pressure sources on this mooring; "
                            f"pressure remains bad-flagged."
                        )

            # ── Fold T/C/P parent QC into salinity_qc ─────────────────
            ds = merge_salinity_parent_qc(ds)

            # ── BEAM / XYZ → ENU coordinate transform ─────────────────
            # Must run before tilt QC so east/north/up_velocity exist to be flagged.
            coord_sys_before = ds.attrs.get("coordinate_system", "ENU")
            ds = apply_beam_to_enu(
                ds,
                info["entry"],
                info["lat"],
                info["lon"],
                latlon_source=info.get("latlon_source", "unknown"),
                log_fn=self._log,
            )
            # If instrument was already in ENU (e.g. configured internally),
            # still apply the magnetic declination rotation.
            if coord_sys_before == "ENU":
                ds = apply_declination_to_enu(
                    ds,
                    info["lat"],
                    info["lon"],
                    latlon_source=info.get("latlon_source", "unknown"),
                    log_fn=self._log,
                )

            # ── ENU velocity QC + up→east/north flag propagation ──────
            if ds.attrs.get("coordinate_system") == "ENU":
                if is_adcp:
                    adcp_qc = params.QC_ADCP
                    ds = apply_adcp_velocity_qc(
                        ds,
                        gr_cfg=gr_cfg,
                        prcnt_gd_bad=adcp_qc["percent_good_bad"],
                        prcnt_gd_suspect=adcp_qc["percent_good_suspect"],
                        error_vel_threshold=adcp_qc["error_velocity_threshold"],
                        log_fn=self._log,
                    )
                    ds = compute_adcp_bin_pressure(ds, info["lat"], log_fn=self._log)
                    ds = apply_adcp_seabed_qc(
                        ds,
                        water_depth_m=info.get("water_depth_m", 0.0),
                        lat=info["lat"],
                        log_fn=self._log,
                    )
                    ds = apply_adcp_surface_qc(
                        ds,
                        lat=info["lat"],
                        log_fn=self._log,
                    )
                else:
                    ds = apply_enu_velocity_qc(ds, gr_cfg)

            # ── Tilt QC ────────────────────────────────────────────────
            # Flags all velocity variables when pitch+roll tilt exceeds threshold.
            # Skipped for ADCP: percent_good (col 3) and error_velocity capture
            # ensemble-level quality without a separate tilt threshold step.
            # Note: this does NOT mean tilt is checked per-bin — it is not.
            tilt_cfg = info["tilt"]
            if is_adcp:
                n_tilt_susp, n_tilt_bad = 0, 0
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

            # ── Unify ENU velocity QC ──────────────────────────────────
            # After tilt + gross-range QC, combine worst flag across all three
            # ENU components and write it back to all three so masking on any
            # single component (east, north, or up) gives an identical result.
            # This catches e.g. large east/north currents (>3 m/s suspect) that
            # would otherwise leave up_velocity_qc unflagged.
            if ds.attrs.get("coordinate_system") == "ENU":
                ds = unify_velocity_qc(ds)

            if ds.attrs.get("coordinate_system") == "ENU" and coord_sys_before != "ENU":
                _ba = ds.attrs.get("nortek_beam_angle", "?")
                _ba_src = ds.attrs.get("nortek_beam_angle_source", "")
                _assumed = "ASSUMED DEFAULT" in _ba_src
                history_notes.append(
                    f"BEAM→ENU rotation applied: "
                    f"beam_angle={_ba}° "
                    f"({'ASSUMED DEFAULT — not from datasheet' if _assumed else 'from YAML'}), "
                    f"declination={ds.attrs.get('magnetic_declination', 0.0):.2f}° "
                    f"({'ppigrf IGRF' if 'magnetic_declination' in ds.attrs else 'assumed 0'})"
                )

            # Report which variables got QC flags and their flag counts
            qc_summary = []
            for v in sorted(ds.data_vars):
                if v.endswith("_qc") and not v.endswith("_orig_qc"):
                    counts = np.bincount(
                        ds[v].values.astype(np.int8).clip(0, 9).ravel(), minlength=10
                    )
                    n_good = int(counts[1])
                    n_susp = int(counts[3])
                    n_bad = int(counts[4])
                    n_interp = int(counts[8])
                    n_miss = int(counts[9])
                    parts = [f"good={n_good}"]
                    if n_susp:
                        parts.append(f"suspect={n_susp}")
                    if n_bad:
                        parts.append(f"bad={n_bad}")
                    if n_interp:
                        parts.append(f"interp={n_interp}")
                    if n_miss:
                        parts.append(f"missing={n_miss}")
                    qc_summary.append(f"{v}=[{', '.join(parts)}]")
            if qc_summary:
                history_notes.append(
                    "QARTOD gross-range+spike: " + "; ".join(qc_summary)
                )

            # ── History ────────────────────────────────────────────────
            stamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ")
            entry = f"{stamp}: " + " | ".join(history_notes)
            existing = ds.attrs.get("history", "")
            ds.attrs["history"] = f"{existing}; {entry}" if existing else entry

            # Record the QC treatment so it can be reconstructed from the output
            # file alone: the flag convention, the worst-wins merge ordering, and
            # the QARTOD -> OceanSITES ingest remap applied to every _qc variable.
            from oceanarray import parameters as params

            _priority_order = " < ".join(
                str(f)
                for f, _ in sorted(
                    params.QC_MERGE_PRIORITY.items(), key=lambda kv: kv[1]
                )
            )
            ds.attrs["qc_flag_convention"] = params.QC_CONVENTION
            ds.attrs["qc_merge_rule"] = (
                "worst flag wins; priority weakest to strongest: " + _priority_order
            )
            ds.attrs["qc_ingest_remap"] = (
                "QARTOD not_evaluated(2) -> OceanSITES unknown(0) at test ingest"
            )

            # Derive O2 % saturation and AOU when dissolved_oxygen + T/S/P are present.
            ds = derive_oxygen_saturation(ds)

            # Normalize CF attrs for known physics variables.
            # Derived from params.VARIABLES — single source of truth for standard_name,
            # long_name (label), and units.  Only fills missing attrs; never
            # overwrites values already set by the reader or derive_* functions.
            for _vname, _entry in params.VARIABLES.items():
                if _vname not in ds.data_vars:
                    continue
                _vatrs = ds[_vname].attrs
                if "standard_name" not in _vatrs and _entry.get("standard_name"):
                    _vatrs["standard_name"] = _entry["standard_name"]
                if "long_name" not in _vatrs and _entry.get("label"):
                    _vatrs["long_name"] = _entry["label"]
                if "units" not in _vatrs and _entry.get("units"):
                    _vatrs["units"] = _entry["units"]
                if "valid_min" not in _vatrs and "valid_min" in _entry:
                    _vatrs["valid_min"] = _entry["valid_min"]
                if "valid_max" not in _vatrs and "valid_max" in _entry:
                    _vatrs["valid_max"] = _entry["valid_max"]

            # Normalize every _qc variable onto the OceanSITES flag table (single
            # source of truth) and attach the status_flag standard_name — done
            # after the CF pass above so each parent's standard_name is available.
            # Skip *_orig_qc: those preserve the pre-interpolation flag (e.g. a bad
            # original pressure = 4) and must stay distinct from the interpolated
            # {var}_qc (e.g. 8), so they keep their own attrs.
            for _qv in [
                v
                for v in ds.data_vars
                if v.endswith("_qc") and not v.endswith("_orig_qc")
            ]:
                set_qc_attrs(ds, _qv[:-3])

            ds = drop_all_zero_vars(ds, ["amplitude_beam", "analog_input_"])
            cast_output_dtypes(ds).to_netcdf(l3_path)
            ds.close()
            self._log(
                f"  Creating output file: {l3_path.name}  ({'; '.join(qc_summary)})"
            )

        except Exception as e:  # noqa: BLE001  — log and continue; one instrument must not abort mooring
            self._log(f"  ERROR processing {serial}: {e}")
            import traceback

            self._log(traceback.format_exc())
            return False
        return True
