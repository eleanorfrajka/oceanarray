"""MooringStacker: interpolate all instruments on a mooring onto a common time grid."""

from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray import parameters as params
from oceanarray import paths
from oceanarray.exceptions import MooringError
from oceanarray.pipeline.helpers import (
    STACK_VARS,
    _detect_interval_s,
    _linear_interp,
    _make_adcp_bin_ds,
    _make_adcp_head_ds,
    _nearest_subsample,
    _safe_serial,
    _worst_flag,
)
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.pipeline.units import UnitsError, to_canonical_units
from oceanarray.utilities import (
    _status,
    drop_all_zero_vars,
    instrument_entries,
    parse_latlon,
)
from oceanarray.writers import write
from oceanarray.writers.netcdf import as_stored

KNOWN_INSTRUMENT_TYPES: frozenset = params.KNOWN_INSTRUMENT_TYPES


class _LowPassedError(ValueError):
    """A low-passed instrument file reached the stack without ``stack: false``."""


_SIGMA_META = {
    0: (
        "sigma0",
        "Potential density anomaly referenced to surface (sigma-0)",
        "sea_water_sigma_t",
    ),
    1000: (
        "sigma1",
        "Potential density anomaly referenced to 1000 dbar (sigma-1)",
        "sea_water_sigma_1",
    ),
    2000: (
        "sigma2",
        "Potential density anomaly referenced to 2000 dbar (sigma-2)",
        "sea_water_sigma_2",
    ),
    3000: (
        "sigma3",
        "Potential density anomaly referenced to 3000 dbar (sigma-3)",
        "sea_water_sigma_3",
    ),
    4000: (
        "sigma4",
        "Potential density anomaly referenced to 4000 dbar (sigma-4)",
        "sea_water_sigma_4",
    ),
}


def stack(
    datasets: dict[str, xr.Dataset],
    mooring: dict,
    *,
    dt_seconds: int = 60,
    omitted: list[str] | None = None,
) -> xr.Dataset:
    """Stack a mooring's instruments onto a common time axis.

    Pure function over datasets: no file I/O. Each instrument is subsampled
    (nearest neighbour) when it samples at least as fast as *dt_seconds*, else
    interpolated linearly in time; ADCPs expand into one level per bin plus a head
    level. Instruments marked ``stack: false`` are left out and named in
    ``instruments_excluded``; instruments that cannot be stacked (non-canonical
    units, low-passed data) are left out and named in ``instruments_omitted``.

    Parameters
    ----------
    datasets : dict of str to xarray.Dataset
        Stage-3 (or stage-2) datasets keyed by cleaned serial.
    mooring : dict
        The loaded mooring description: the deployment window is the time axis, and
        each entry's ``hab`` and ``stack`` flag are used. Each instrument's sampling
        interval is detected from its time axis.
    dt_seconds : int, optional
        Spacing of the common time axis, in seconds.
    omitted : list of str or None, optional
        Instruments the caller already dropped (e.g. unreadable files), recorded
        in ``instruments_omitted`` with the ones dropped here.

    Returns
    -------
    xarray.Dataset
        The stack on ``(time, N_LEVELS)``, deepest level first.

    Raises
    ------
    oceanarray.exceptions.MooringError
        If the description has no deployment window, or no instrument can be stacked.
    """
    mooring_config = mooring
    mooring_name = mooring["name"]
    omitted = list(omitted or [])

    # The common time axis IS the deployment window, so stacking needs it.
    # Stage 2 writing a window detected from the records (when the YAML omits
    # it) is later work; until then a window-less config fails loud here
    # rather than with a bare KeyError.
    if "deployment_time" not in mooring_config or (
        "recovery_time" not in mooring_config
    ):
        raise MooringError(
            f"Cannot stack {mooring_name}: the mooring config has no "
            f"'deployment_time'/'recovery_time' (the common time axis is the "
            f"deployment window). A window detected from the records is not yet "
            f"implemented — add both to the YAML."
        )
    deploy_time = np.datetime64(mooring_config["deployment_time"], "ns")
    recover_time = np.datetime64(mooring_config["recovery_time"], "ns")

    common_time = np.arange(
        deploy_time.astype("datetime64[s]"),
        recover_time.astype("datetime64[s]") + np.timedelta64(1, "s"),
        np.timedelta64(dt_seconds, "s"),
    ).astype("datetime64[ns]")
    n_time = len(common_time)

    instrument_list = instrument_entries(mooring_config)

    # Collect instruments with known hab and an available stage2/stage3 file.
    # An instrument marked ``stack: false`` in the mooring YAML is processed
    # through stages 1-3 (reference data) but kept out of the stack, and named
    # in ``instruments_excluded`` — distinct from an error omission.
    instruments = []
    excluded: list[str] = []
    for entry in instrument_list:
        if not isinstance(entry, dict):
            continue
        serial = _safe_serial(entry.get("serial", ""))
        instr_type = entry.get("instrument", "unknown")
        if instr_type not in KNOWN_INSTRUMENT_TYPES:
            print(
                f"  WARNING: instrument '{instr_type}' (s/n {serial}) is not in "
                f"KNOWN_INSTRUMENT_TYPES {sorted(KNOWN_INSTRUMENT_TYPES)}; "
                "it will be stacked as a generic instrument (Aquadopp/ADCP-specific "
                "handling skipped)."
            )
        if entry.get("stack", True) is False:
            excluded.append(f"{instr_type} s/n {serial}")
            print(f"  EXCLUDE {instr_type} s/n {serial}: stack: false in the YAML")
            continue
        hab = entry.get("hab")
        if hab is None:
            continue
        if serial not in datasets:
            print(f"  SKIP {instr_type} s/n {serial}: no stage2/stage3 file")
            continue
        ds_in = datasets[serial]
        instruments.append(
            {
                "serial": serial,
                "instrument": instr_type,
                "hab": float(hab),
                "ds": ds_in,
                "stage_label": f"stage{ds_in.attrs.get('processing_stage', '')}",
            }
        )

    if not instruments:
        raise MooringError(
            f"{mooring_name}: no instruments with a stage-2/stage-3 dataset and a hab value"
        )

    # Sort deep-first (ascending hab: smallest hab = nearest bottom = deepest)
    instruments.sort(key=lambda x: x["hab"])

    # ── Expand ADCP instruments into per-bin pseudo-instruments ───────
    # Each ADCP bin becomes a separate row in the stack, with its own
    # bin_pressure, velocity, and QC time series sliced from the 2-D
    # (time, N_BINS) arrays in the parent stage3 file.
    # The parent file is loaded once and cached; _make_adcp_bin_ds slices it.
    _adcp_parent_datasets: dict[str, xr.Dataset] = {}
    expanded: list[dict] = []
    for info in instruments:
        if info["instrument"].lower() != "adcp":
            expanded.append(info)
            continue
        nc_key = info["serial"]
        _adcp_parent_datasets[nc_key] = info["ds"]
        ds_adcp = info["ds"]
        bin_dim = params.ADCP_BIN_DIM
        if bin_dim not in ds_adcp.dims:
            print(
                f"  WARNING: ADCP s/n {info['serial']} has no {bin_dim} dim "
                "— treating as point instrument"
            )
            expanded.append(info)
            continue
        n_bins = ds_adcp.sizes[bin_dim]
        range_vals = (
            ds_adcp["range"].values
            if "range" in ds_adcp.coords
            else np.arange(n_bins, dtype=float)
        )
        orientation = ds_adcp.attrs.get("orientation_yaml") or ds_adcp.attrs.get(
            "orientation_instrument"
        )
        if not orientation:
            orientation = "down"
            print(
                f"  WARNING: ADCP {info['serial']} has no orientation attr — "
                "assuming downward-looking. HAB offsets may be wrong for "
                "upward-looking instruments."
            )
        looking_down = str(orientation).lower() == "down"
        # Pre-compute seabed mask so always-submerged bins can be skipped.
        # seabed_qc has dims (time, N_BINS); a bin is permanently below the
        # seabed when every time step is flagged suspect or worse (>= 3).
        _seabed_qc_vals = (
            ds_adcp["seabed_qc"].values if "seabed_qc" in ds_adcp.data_vars else None
        )

        n_skipped = 0
        valid_bins = []
        for i in range(n_bins):
            if _seabed_qc_vals is not None and np.all(_seabed_qc_vals[:, i] >= 3):
                n_skipped += 1
            else:
                valid_bins.append(i)

        print(
            f"  ADCP s/n {info['serial']}: expanding {len(valid_bins)} of {n_bins} bins "
            f"({orientation}-looking) into stack + head entry"
            + (f" [{n_skipped} bins always below seabed, skipped]" if n_skipped else "")
        )

        # Head entry — transducer location carries temperature and orientation.
        # Uses the instrument HAB directly (range = 0 from the transducer).
        expanded.append(
            {
                **info,
                "serial": f"{info['serial']}_hd",
                "hab": float(info["hab"]),
                "_adcp_head": True,
                "_adcp_nc_key": nc_key,
            }
        )
        for i in valid_bins:
            r = float(range_vals[i])
            bin_hab = float(info["hab"]) - r if looking_down else float(info["hab"]) + r
            expanded.append(
                {
                    **info,
                    "serial": f"{info['serial']}_b{i:02d}",
                    "hab": bin_hab,
                    "_adcp_bin_idx": i,
                    "_adcp_nc_key": nc_key,
                }
            )
    instruments = expanded
    # Re-sort after expansion so ADCP bins interleave with other instruments by depth
    instruments.sort(key=lambda x: x["hab"])

    n_instr = len(instruments)

    print(
        f"Merging {n_instr} instruments onto {n_time}-point {dt_seconds}s time grid "
        f"({mooring_name})"
    )

    stacked: dict[str, np.ndarray] = {
        v: np.full((n_instr, n_time), np.nan) for v in STACK_VARS
    }
    var_attrs: dict[str, dict] = {v: {} for v in STACK_VARS}
    serials: list[str] = []
    habs: list[float] = []
    instr_types: list[str] = []
    stage_labels: list[str] = []
    # Per-instrument scalar metadata: {varname: [value_for_instr0, value_for_instr1, ...]}
    scalar_meta: dict[str, list] = {}  # populated during loop
    scalar_attrs: dict[str, dict] = {}
    # Stage-3 time-series variables not in STACK_VARS are dropped here; collect
    # them so the operator is told rather than losing data silently (D2).
    dropped_ts_vars: set = set()
    # Instruments dropped from the stack (bad units or unreadable file).  The
    # stack still builds (partial stacks are useful operationally), but each
    # omission is recorded loudly: in the summary, in an `instruments_omitted`
    # global attribute the grid inherits, and in the exit code.
    omitted_idx: list[int] = []  # enumerate positions of omitted instruments

    for i, info in enumerate(instruments):
        serials.append(info["serial"])
        habs.append(info["hab"])
        instr_types.append(info["instrument"])
        stage_labels.append(info["stage_label"])

        try:
            if "_adcp_bin_idx" in info:
                ds_parent = _adcp_parent_datasets[info["_adcp_nc_key"]]
                ds = _make_adcp_bin_ds(ds_parent, info["_adcp_bin_idx"])
            elif "_adcp_head" in info:
                ds_parent = _adcp_parent_datasets[info["_adcp_nc_key"]]
                ds = _make_adcp_head_ds(ds_parent)
            else:
                ds = info["ds"]
            # Canonicalise each instrument's units before stacking, so a
            # pre-0.5.0 input (degC, S/m, …) joins the stack already canonical.
            ds = to_canonical_units(ds, log_fn=print, stage="stack")
            # A low-passed file (e.g. RODB .lowedt) on a coarse step would be
            # interpolated onto the stack grid as if it were full-rate data.
            if "originator_low_passed" in ds.attrs:
                raise _LowPassedError(
                    f"low-passed reference data "
                    f"({ds.attrs['originator_low_passed']}); set stack: false "
                    f"on this instrument in the mooring YAML"
                )
        except Exception as e:  # noqa: BLE001  — one bad instrument must not abort the stack
            if isinstance(e, UnitsError):
                reason = f"units error: {e}"
            elif isinstance(e, _LowPassedError):
                reason = str(e)
            else:
                reason = f"could not stack it: {e}"
            print(
                f"  ERROR: omitting {info['instrument']} s/n {info['serial']} — {reason}"
            )
            omitted.append(f"{info['instrument']} s/n {info['serial']} ({reason})")
            omitted_idx.append(i)
            # Ensure scalar_meta lists stay length-consistent
            for lst in scalar_meta.values():
                lst.append(None)
            continue

        interval_s = _detect_interval_s(ds["time"].values)
        print(
            f"  [{i:2d}] {info['instrument']:10s} s/n {info['serial']:<8}  "
            f"hab={info['hab']:6.1f} m  dt={interval_s:.0f}s  "
            f"({stage_labels[-1]})"
        )

        half_window = dt_seconds / 2.0
        if interval_s <= dt_seconds:
            values = _nearest_subsample(ds, common_time, half_window)
        else:
            values = _linear_interp(ds, common_time)

        for vname in STACK_VARS:
            arr = values[vname]
            n = min(len(arr), n_time)
            stacked[vname][i, :n] = arr[:n]
            if not var_attrs[vname] and vname in ds.data_vars:
                var_attrs[vname] = dict(ds[vname].attrs)

        # Track any time-series stage-3 variables that STACK_VARS omits.
        dropped_ts_vars.update(
            v
            for v, da in ds.data_vars.items()
            if "time" in da.dims and v not in STACK_VARS
        )

        # Collect scalar (0-D) metadata variables — keep all instruments consistent
        for vname, da in ds.data_vars.items():
            if da.dims:  # skip time-series variables
                continue
            if vname not in scalar_meta:
                # Back-fill with None for instruments processed before this variable appeared
                scalar_meta[vname] = [None] * i
                scalar_attrs[vname] = dict(da.attrs)
            scalar_meta[vname].append(da.values.item())

        # Collect magnetic_declination from global attrs (set by stage3 BEAM→ENU).
        # Must come BEFORE the fill-None loop so the length is already i+1 when
        # the loop checks.
        if "magnetic_declination" not in scalar_meta:
            scalar_meta["magnetic_declination"] = [np.nan] * i
            scalar_attrs["magnetic_declination"] = {
                "units": "degrees_east",
                "long_name": "Magnetic declination (IGRF)",
            }
        decl_val = ds.attrs.get("magnetic_declination")
        scalar_meta["magnetic_declination"].append(
            float(decl_val) if decl_val is not None else np.nan
        )

        # Fill None for variables not present in this instrument
        for vname in scalar_meta:
            if len(scalar_meta[vname]) < i + 1:
                scalar_meta[vname].append(None)

    if dropped_ts_vars:
        print(
            f"  NOTE: {len(dropped_ts_vars)} stage-3 time-series variable(s) not in "
            f"STACK_VARS were dropped from the stack: "
            f"{', '.join(sorted(dropped_ts_vars))}. "
            f"Add them to STACK_VARS in mooring/helpers.py to retain them."
        )

    # Drop omitted instruments from every per-level array/list so the written
    # stack has no phantom all-NaN level — N_LEVELS and `instruments_omitted`
    # must agree.  The loop indexes `stacked`/`scalar_meta` by enumerate position,
    # so slice all of them to the kept positions here rather than restructuring it.
    _omitted_set = set(omitted_idx)
    kept = [i for i in range(n_instr) if i not in _omitted_set]
    if not kept:
        raise MooringError(
            f"every instrument was omitted — no stack ({mooring_name}): "
            + "; ".join(omitted)
        )
    if len(kept) < n_instr:
        serials = [serials[i] for i in kept]
        habs = [habs[i] for i in kept]
        instr_types = [instr_types[i] for i in kept]
        stage_labels = [stage_labels[i] for i in kept]
        for _v in stacked:
            stacked[_v] = stacked[_v][kept, :]
        for _k in scalar_meta:
            scalar_meta[_k] = [scalar_meta[_k][i] for i in kept]
        n_instr = len(kept)

    # Build output dataset; skip physics variables that are entirely NaN
    data_vars: dict = {}
    for vname in STACK_VARS:
        if not np.all(np.isnan(stacked[vname])):
            data_vars[vname] = xr.Variable(
                ("N_LEVELS", "time"), stacked[vname], attrs=var_attrs[vname]
            )

    # Compute potential density from stacked T, S, P
    ref_p = int(mooring_config.get("density_reference", params.DENSITY_REFERENCE))
    if (
        "temperature" in data_vars
        and "salinity" in data_vars
        and "pressure" in data_vars
    ):
        try:
            import gsw

            lat, lon = parse_latlon(mooring_config)
            T_arr = stacked["temperature"]
            SP_arr = stacked["salinity"]
            P_arr = stacked["pressure"]
            SA = gsw.SA_from_SP(SP_arr, P_arr, lon, lat)
            CT = gsw.CT_from_t(SA, T_arr, P_arr)
            _sigma_fn = {
                0: gsw.sigma0,
                1000: gsw.sigma1,
                2000: gsw.sigma2,
                3000: gsw.sigma3,
                4000: gsw.sigma4,
            }
            if ref_p in _sigma_fn:
                sigma_vals = _sigma_fn[ref_p](SA, CT)
            else:
                sigma_vals = gsw.pot_rho_t_exact(SA, T_arr, P_arr, ref_p) - 1000.0
            if not np.all(np.isnan(sigma_vals)):
                vname, long_name, std_name = _SIGMA_META.get(
                    ref_p,
                    (
                        f"sigma_{ref_p}",
                        f"Potential density anomaly referenced to {ref_p} dbar",
                        "",
                    ),
                )
                data_vars[vname] = xr.Variable(
                    ("N_LEVELS", "time"),
                    sigma_vals,
                    {
                        "units": "kg m-3",
                        "long_name": long_name,
                        "standard_name": std_name,
                        "reference_pressure_dbar": ref_p,
                    },
                )
        except Exception as exc:  # noqa: BLE001  — gsw failure must not abort stack
            print(f"  WARNING: could not compute potential density: {exc}")

    # velocity_flag: element-wise worst QC flag across east/north/up velocity.
    # Velocity is stored unmasked in the stack; this combined flag is what the
    # grid step (and users) should apply before using velocity values.
    _vel_qc_keys = [
        v
        for v in ("east_velocity_qc", "north_velocity_qc", "up_velocity_qc")
        if v in data_vars
    ]
    if _vel_qc_keys:
        n_lev_v, n_t_v = (
            stacked["east_velocity_qc"].shape
            if "east_velocity_qc" in stacked
            else (0, 0)
        )
        if n_lev_v > 0:
            # int8 so flag_values (also int8) matches the variable dtype (CF).
            vel_flag = np.ones((n_lev_v, n_t_v), dtype=np.int8)  # 1 = good
            for _qk in _vel_qc_keys:
                vel_flag = _worst_flag(vel_flag, stacked[_qk]).astype(np.int8)
            data_vars["velocity_flag"] = xr.Variable(
                ("N_LEVELS", "time"),
                vel_flag,
                {
                    "long_name": "Combined velocity QC flag (worst of east/north/up)",
                    "comment": (
                        "Worst OceanSITES flag across east/north/up_velocity; "
                        "apply to the velocity components before use."
                    ),
                    "flag_values": params.QC_FLAG_VALUES_I8,
                    "flag_meanings": params.QC_FLAG_MEANINGS,
                    "conventions": params.QC_CONVENTION,
                },
            )

    # Tilt estimated from pressure difference between two instrument levels.
    # For each level i, find the nearest level j above it that is ≥10 m away
    # in hab AND has at least some finite pressure data.  Instruments close
    # together (e.g. a microcat strapped to an Aquadopp frame) are skipped.
    #   rope_length = hab[j] - hab[i]            (m, from YAML)
    #   ΔP          = pressure[i,:] - pressure[j,:]   (dbar ≈ m, >0 when upright)
    #   tilt        = arccos(ΔP / rope_length)   (degrees from vertical; 0 = upright)
    _MIN_HAB_SEP = 10.0  # minimum hab separation (m) to use as reference
    if "pressure" in data_vars and len(habs) > 1:
        try:
            p_arr = stacked["pressure"]  # (N_LEVELS, time) numpy array
            n_lev = len(habs)
            tilt_p = np.full_like(p_arr, np.nan)
            ref_hab_arr = np.full(n_lev, np.nan)  # hab of the reference level
            ref_serial_arr = np.array([""] * n_lev, dtype=object)
            # pre-compute which levels have any valid pressure
            _has_p = np.array([np.any(np.isfinite(p_arr[k, :])) for k in range(n_lev)])
            _is_adcp = [t.lower() == "adcp" for t in instr_types]
            for i in range(n_lev):
                if not _has_p[i]:
                    continue
                if _is_adcp[i]:
                    continue  # ADCP transducer pressure is not suitable for tilt
                # Find nearest level above i that is ≥_MIN_HAB_SEP away with pressure,
                # excluding ADCP levels as reference (their bin pressures shift with tilt).
                ref_j = None
                for j in range(i + 1, n_lev):
                    if float(habs[j]) - float(habs[i]) < _MIN_HAB_SEP:
                        continue
                    if not _has_p[j]:
                        continue
                    if _is_adcp[j]:
                        continue
                    ref_j = j
                    break  # first valid j = nearest ≥10 m above
                if ref_j is None:
                    continue
                rope = float(habs[ref_j]) - float(habs[i])
                ref_hab_arr[i] = float(habs[ref_j])
                ref_serial_arr[i] = str(serials[ref_j])
                delta_p = p_arr[i, :] - p_arr[ref_j, :]
                ratio = np.clip(delta_p / rope, 0.0, 1.0)
                tilt_p[i, :] = np.degrees(np.arccos(ratio))
                tilt_p[i, ~np.isfinite(delta_p)] = np.nan
                print(
                    f"  tilt_from_pressure[{i}] s/n {serials[i]} ({habs[i]:.0f} m): "
                    f"ref s/n {serials[ref_j]} ({habs[ref_j]:.0f} m, "
                    f"rope={rope:.0f} m)"
                )
            data_vars["tilt_from_pressure"] = xr.Variable(
                ("N_LEVELS", "time"),
                tilt_p,
                {
                    "units": "degrees",
                    "long_name": "Tilt estimated from pressure difference",
                    "comment": (
                        "arccos(ΔP / rope_length) where ΔP = pressure[i] - pressure[ref] "
                        "and rope_length = hab[ref] - hab[i] from mooring YAML; "
                        "ref is the nearest instrument ≥10 m above i with valid pressure."
                    ),
                },
            )
            data_vars["tilt_pressure_ref_hab"] = xr.Variable(
                ("N_LEVELS",),
                ref_hab_arr,
                {
                    "units": "m",
                    "long_name": "Height above bottom of the pressure reference instrument used for tilt",
                },
            )
            data_vars["tilt_pressure_ref_serial"] = xr.Variable(
                ("N_LEVELS",),
                ref_serial_arr,
                {
                    "long_name": "Serial number of the pressure reference instrument used for tilt"
                },
            )
        except Exception as exc:  # noqa: BLE001  — optional derived var; must not abort stack
            print(f"  WARNING: could not compute tilt_from_pressure: {exc}")

    # Coordinate names — exclude these from scalar metadata to avoid name conflicts
    _coord_names = {"serial", "hab", "instrument_type", "instrument", "time"}

    # Add scalar metadata — collapse to 0-D when all non-NaN values are identical,
    # otherwise store as (N_LEVELS,) with one entry per instrument.
    for vname, values_list in scalar_meta.items():
        if vname in _coord_names:
            continue
        try:
            arr = np.array(
                [v if v is not None else np.nan for v in values_list],
                dtype=np.float64,
            )
            unique_finite = np.unique(arr[np.isfinite(arr)])
            if len(unique_finite) <= 1:
                val = float(unique_finite[0]) if len(unique_finite) == 1 else np.nan
                data_vars[vname] = xr.Variable((), val, attrs=scalar_attrs[vname])
            else:
                data_vars[vname] = xr.Variable(
                    ("N_LEVELS",), arr, attrs=scalar_attrs[vname]
                )
        except (ValueError, TypeError):
            arr = np.array([str(v) if v is not None else "" for v in values_list])
            unique_vals = np.unique(arr)
            if len(unique_vals) == 1:
                data_vars[vname] = xr.Variable(
                    (), unique_vals[0], attrs=scalar_attrs[vname]
                )
            else:
                data_vars[vname] = xr.Variable(
                    ("N_LEVELS",), arr, attrs=scalar_attrs[vname]
                )

    ds_out = xr.Dataset(
        data_vars,
        coords={
            "time": xr.Variable(
                "time",
                common_time,
                {"long_name": "time", "axis": "T", "standard_name": "time"},
            ),
            "serial": xr.Variable(
                "N_LEVELS",
                np.array(serials),
                {"long_name": "instrument serial number"},
            ),
            "hab": xr.Variable(
                "N_LEVELS",
                np.array(habs),
                {"units": "m", "long_name": "height above bottom"},
            ),
            "instrument_type": xr.Variable(
                "N_LEVELS",
                np.array(instr_types),
                {"long_name": "instrument type"},
            ),
            "source_stage": xr.Variable(
                "N_LEVELS",
                np.array(
                    [int(label.removeprefix("stage") or 0) for label in stage_labels],
                    dtype=np.int8,
                ),
                {
                    "long_name": "processing stage of the instrument file the level "
                    "came from",
                    "comment": "3 normally; 2 only in a keep-going run, where an "
                    "instrument without a stage-3 file is stacked from its stage-2 "
                    "file; 0 if unknown",
                },
            ),
        },
    )
    _lat_str = (
        mooring_config.get("seabed_latitude")
        or mooring_config.get("deployment_latitude")
        or mooring_config.get("planned_latitude")
        or mooring_config.get("latitude")
        or ""
    )
    _lon_str = (
        mooring_config.get("seabed_longitude")
        or mooring_config.get("deployment_longitude")
        or mooring_config.get("planned_longitude")
        or mooring_config.get("longitude")
        or ""
    )
    ds_out.attrs.update(
        {
            "mooring_name": mooring_name,
            "waterdepth": str(mooring_config.get("waterdepth", "")),
            "latitude": str(_lat_str),
            "longitude": str(_lon_str),
            "deployment_time": str(deploy_time),
            "recovery_time": str(recover_time),
            "dt_seconds": dt_seconds,
            "Conventions": "CF-1.13",
            "history": (
                f"Step 1 stack: {n_instr} instruments onto {dt_seconds}s grid; "
                f"fast instruments (dt<={dt_seconds}s) subsampled (nearest-neighbour in time), "
                f"slow instruments interpolated (linear in time)"
            ),
        }
    )

    # Beam velocities are superseded by ENU components in the stacked file.
    beam_vel_vars = [v for v in ds_out.data_vars if v.startswith("velocity_beam")]
    if beam_vel_vars:
        ds_out = ds_out.drop_vars(beam_vel_vars)
    ds_out = drop_all_zero_vars(ds_out, ["amplitude_beam", "analog_input_"])
    # OceanSITES convention: time is the unlimited (first) dimension.
    ds_out = ds_out.transpose("time", "N_LEVELS")
    ds_out = to_canonical_units(ds_out, log_fn=print, stage="stack")
    ds_out.attrs["data_mode"] = "P"
    at_stage2 = sorted(
        {s for s, label in zip(serials, stage_labels, strict=True) if label == "stage2"}
    )
    if at_stage2:
        ds_out.attrs["stage2_instruments"] = " ".join(at_stage2)
        print(
            f"  WARNING: stacked from stage 2 (no stage-3 file): {' '.join(at_stage2)}"
        )
    if excluded:
        ds_out.attrs["instruments_excluded"] = "; ".join(excluded)
    if omitted:
        # Record the omission in the file so the stack report and the grid
        # (which reads this file) inherit it, and summarise it loudly.
        ds_out.attrs["instruments_omitted"] = "; ".join(omitted)
        print(
            f"  WARNING: {len(omitted)} instrument(s) omitted from the stack "
            f"(recorded in instruments_omitted):"
        )
        for line in omitted:
            print(f"    - {line}")
    return as_stored(ds_out)


def _read_best_stage(
    mooring: dict, proc_dir: Path
) -> tuple[dict[str, xr.Dataset], list[str]]:
    """Read each stackable instrument at stage 3, falling back to stage 2.

    Returns the datasets keyed by serial and an omission line for each instrument
    whose file could not be read.
    """
    name = mooring["name"]
    datasets: dict[str, xr.Dataset] = {}
    omitted: list[str] = []
    for entry in instrument_entries(mooring):
        if not isinstance(entry, dict):
            continue
        if entry.get("stack", True) is False or entry.get("hab") is None:
            continue
        serial = _safe_serial(entry.get("serial", ""))
        instr_type = entry.get("instrument", "unknown")
        for stage_n in (3, 2):
            path = paths.stage_file(proc_dir, name, instr_type, serial, stage_n)
            if not path.exists():
                continue
            try:
                ds = read_mooring(
                    mooring, stage=stage_n, serials=[serial], mooring_dir=proc_dir
                )[serial]
                datasets[serial] = ds.load()
            except Exception as e:  # noqa: BLE001  — one bad file must not abort the stack
                reason = f"could not load {path.name}: {e}"
                print(f"  ERROR: omitting {instr_type} s/n {serial} — {reason}")
                omitted.append(f"{instr_type} s/n {serial} ({reason})")
            break
    return datasets, omitted


def _stack_file(
    mooring: dict,
    mooring_dir: Path,
    *,
    dt_seconds: int = 60,
    keep_going: bool = False,
    failed: Mapping[str, str] | None = None,
    stage3_failed: bool = False,
    failures: Sequence[str] = (),
) -> tuple[Path, xr.Dataset]:
    """Stack a mooring's stage-3 files and write the stack file; return its path and dataset.

    Every instrument that is not marked ``skip`` or ``stack: false`` and has a
    ``hab`` must have a stage-3 file. With *keep_going* an instrument without one
    is stacked from its stage-2 file (recorded in ``source_stage`` and
    ``stage2_instruments``), and one with neither, or whose file cannot be read,
    is left out and named in ``instruments_omitted``.

    Parameters
    ----------
    mooring : dict
        The loaded mooring description.
    mooring_dir : pathlib.Path
        The mooring's own directory.
    dt_seconds : int, optional
        Spacing of the stack's time axis.
    keep_going : bool, optional
        Stack what exists instead of raising (see above).
    failed : Mapping of str to str or None, optional
        Serials that failed in stages 1–3 of this run, with the reason. Their files
        on disk are older than this run, so they are left out and named in
        ``instruments_omitted`` rather than stacked.
    stage3_failed : bool, optional
        Stage 3 failed as a whole in this run: any stage-3 file on disk is older
        than it, so every instrument is stacked from its stage-2 file.
    failures : sequence of str, optional
        What failed in this run, recorded in the stack's ``keep_going_failures``
        attribute (the grid inherits it).

    Returns
    -------
    tuple of pathlib.Path and xarray.Dataset
        The stack file written and the stack.

    Raises
    ------
    oceanarray.exceptions.MooringError
        Without *keep_going*: if an instrument has no readable stage-3 file. With
        or without it: if nothing can be stacked.
    """
    name = mooring["name"]
    failed = dict(failed or {})
    if stage3_failed:
        stages: tuple[int, ...] = (2,)
    else:
        stages = (3, 2) if keep_going else (3,)
    datasets: dict[str, xr.Dataset] = {}
    missing: list[str] = []
    omitted: list[str] = []
    for entry in instrument_entries(mooring):
        if not isinstance(entry, dict) or entry.get("skip"):
            continue
        if entry.get("stack", True) is False or entry.get("hab") is None:
            continue
        serial = _safe_serial(entry.get("serial", ""))
        instr_type = entry.get("instrument", "unknown")
        if serial in failed:
            omitted.append(
                f"{instr_type} s/n {serial} ({failed[serial]} this run; its older "
                "files are not used)"
            )
            continue
        found = [
            (n, path)
            for n in stages
            if (
                path := paths.stage_file(mooring_dir, name, instr_type, serial, n)
            ).exists()
        ]
        if not found:
            missing.append(f"{instr_type} s/n {serial}")
            continue
        stage_n, path = found[0]
        try:
            datasets[serial] = read_mooring(
                mooring, stage=stage_n, serials=[serial], mooring_dir=mooring_dir
            )[serial].load()
        except Exception as e:
            if not keep_going:
                raise MooringError(f"{name}: could not read {path.name}: {e}") from e
            omitted.append(
                f"{instr_type} s/n {serial} (could not load {path.name}: {e})"
            )
    if missing and not keep_going:
        raise MooringError(
            f"{name}: no stage-3 file for {', '.join(missing)}. Run stage 3, mark "
            "the instrument 'skip: true', or use keep_going=True to stack what exists."
        )
    omitted += [
        f"{m} (no stage-{'2' if stage3_failed else '2 or stage-3'} file)"
        for m in missing
    ]
    ds = stack(datasets, mooring, dt_seconds=dt_seconds, omitted=omitted)
    if failures:
        ds.attrs["keep_going_failures"] = "; ".join(failures)
    return write(ds, paths.product_file(mooring_dir, name, "stack"), overwrite=True), ds


class MooringStacker:
    """Step 1: stack all instruments onto a common time axis → ``_stack.nc``."""

    def __init__(
        self,
        *,
        proc_dir: str,
    ) -> None:
        """Resample all instruments from a mooring onto a common time axis.

        Reads ``{mooring}_{serial}_stage3.nc`` (or ``_stage2.nc`` as fallback) for
        every instrument listed in the mooring YAML, resamples each to a uniform
        sampling interval, and writes a single multi-instrument file
        ``{mooring}_stack.nc`` in the mooring proc directory.

        Parameters
        ----------
        proc_dir : str
            Cruise-level processed output directory. The pipeline appends
            ``/{mooring}/`` internally (see :func:`oceanarray.paths.mooring_proc_dir`).

        """
        self._proc_dir = Path(proc_dir)

    def _resolve_proc_dir(self, mooring_name: str) -> Path:
        """Return the mooring-level proc directory."""
        return paths.mooring_proc_dir(self._proc_dir, mooring_name)

    def _rel(self, path: Path) -> str:
        """Return a short display path relative to proc_dir."""
        if self._proc_dir:
            try:
                return str(path.relative_to(self._proc_dir))
            except ValueError:
                pass
        return path.name

    def stack(
        self,
        mooring_name: str,
        dt_seconds: int = 60,
        force: bool = False,
    ) -> bool:
        """Stack all processed instruments for *mooring_name* onto a common time grid.

        Reads ``_stage3.nc`` (falling back to ``_stage2.nc``) for every instrument
        listed in the mooring YAML, resamples each to *dt_seconds* resolution, and
        writes ``{mooring}_stack.nc`` under the mooring proc directory.

        Returns True on success; False if no instruments could be stacked, or if any
        instrument was omitted (the partial stack is still written).
        """
        proc_dir = self._resolve_proc_dir(mooring_name)
        try:
            proc_dir_exists = proc_dir.exists()
        except (TimeoutError, OSError) as exc:
            print("ERROR: Cannot access data drive — is it connected?")
            print(f"       Path: {proc_dir}")
            print(f"       ({type(exc).__name__}: {exc})")
            return False

        if not proc_dir_exists:
            print(f"ERROR: Processing directory not found: {proc_dir}")
            return False

        output_path = paths.product_file(proc_dir, mooring_name, "stack")
        if output_path.exists() and not force:
            _status("skip", self._rel(output_path))
            return True

        config_file = proc_dir / f"{mooring_name}.mooring.yaml"
        if not config_file.exists():
            print(f"ERROR: Config not found: {config_file}")
            return False

        mooring_config = _mooring.load(config_file)
        datasets, read_omitted = _read_best_stage(mooring_config, proc_dir)
        try:
            ds_out = stack(
                datasets, mooring_config, dt_seconds=dt_seconds, omitted=read_omitted
            )
        except MooringError as e:
            if "deployment_time" in str(e):
                raise
            print(f"ERROR: {e}")
            return False
        write(ds_out, output_path, overwrite=True)
        _status("file", self._rel(output_path))
        # Return False when anything was omitted so the CLI exits 1 ("something
        # failed") even though the partial stack file was written; process() only
        # skips the grid stage when no stack file exists, so the grid still runs.
        return "instruments_omitted" not in ds_out.attrs
