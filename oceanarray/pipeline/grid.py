"""Grid a mooring's stack onto regular pressure levels: the pure :func:`grid` and MooringGridder."""

from pathlib import Path

import numpy as np
import xarray as xr

from oceanarray import paths
from oceanarray.pipeline.units import to_canonical_units
from oceanarray.utilities import _status
from oceanarray.writers import write


def grid(
    ds_stack: xr.Dataset,
    *,
    p_start: float = 200.0,
    p_end: float = 1000.0,
    dp: float = 20.0,
) -> xr.Dataset:
    """Interpolate a mooring stack onto a regular pressure grid.

    Pure function: no file I/O. At each time step every variable is interpolated
    linearly in pressure from the instruments with valid pressure and values onto
    ``p_start:dp:p_end`` dbar, with no extrapolation. Velocity flagged suspect or
    worse (``velocity_flag`` and the ADCP seabed, percent-good and error-velocity
    flags) is masked first.

    Parameters
    ----------
    ds_stack : xarray.Dataset
        The stack, as :func:`oceanarray.pipeline.stack.stack` returns it.
    p_start, p_end, dp : float, optional
        The pressure grid, in dbar.

    Returns
    -------
    xarray.Dataset
        The gridded product on ``(time, pressure)``.

    Raises
    ------
    ValueError
        If the stack has no ``pressure`` variable.
    """
    p_grid = np.arange(p_start, p_end + dp * 0.5, dp)
    n_p = len(p_grid)

    # Canonicalise the stack's units (idempotent on a 0.5.0 stack; rewrites a
    # pre-0.5.0 one) so the grid inherits one spelling per quantity.
    ds = to_canonical_units(ds_stack, log_fn=print, stage="grid")
    n_time = ds.sizes["time"]
    n_instr = ds.sizes["N_LEVELS"]

    print(
        f"Gridding {n_instr} instruments → {n_p} pressure levels "
        f"({p_start:.0f}:{dp:.0f}:{p_end:.0f} dbar) × {n_time} time steps"
    )

    if "pressure" not in ds.data_vars:
        raise ValueError("'pressure' not found in the stack — cannot grid vertically")

    pressure = ds["pressure"].values.astype(np.float64)  # (time, N_LEVELS)
    # Variables that are meaningful at the stacked per-instrument level but
    # should not be interpolated onto the pressure grid.  Instrument-frame
    # and beam-frame velocities are excluded because the XYZ→ENU rotation
    # has already been applied; gridding the pre-rotation components would
    # be misleading.  Diagnostic quantities (amplitude, correlation,
    # battery) are per-sensor and do not have a physical meaning on a
    # spatially-interpolated grid.
    _GRID_EXCLUDE: frozenset = frozenset(
        {
            "velocity_x",
            "velocity_y",
            "velocity_z",
            "velocity_beam1",
            "velocity_beam2",
            "velocity_beam3",
            "amplitude",
            "correlation",
            "battery_voltage",
            "velocity_flag",  # flag array, not a gridded physics variable
            "tilt_from_pressure",  # per-instrument diagnostic, not gridded
            "tilt_pressure_ref_hab",
            "heading",  # instrument-frame orientation — not meaningful on a pressure grid
            "pitch",
            "roll",
        }
    )
    # Variables eligible for gridding: physics vars on the (time, N_LEVELS)
    # grid, excluding pressure and QC flags.  Compute once, then split into
    # kept vs excluded so the two lists cannot drift (D2).
    _grid_candidates = [
        v
        for v in ds.data_vars
        if v != "pressure"
        and ds[v].dims == ("time", "N_LEVELS")
        and not v.endswith("_qc")
    ]
    grid_vars = [v for v in _grid_candidates if v not in _GRID_EXCLUDE]

    # Tell the operator which present physics variables were excluded from the
    # grid rather than dropping them silently (D2).
    _excluded = sorted(v for v in _grid_candidates if v in _GRID_EXCLUDE)
    if _excluded:
        print(
            f"  NOTE: {len(_excluded)} variable(s) excluded from gridding "
            f"(listed in _GRID_EXCLUDE): {', '.join(_excluded)}"
        )

    stacked: dict[str, np.ndarray] = {
        v: np.full((n_p, n_time), np.nan) for v in grid_vars
    }
    var_data = {v: ds[v].values.astype(np.float64) for v in grid_vars}

    # QC masking before vertical interpolation.
    # T/S/P: NaN any non-finite values (already masked at stack time).
    # Velocity: use velocity_flag from the stack to NaN suspect/bad samples.
    #   Flag 3 (suspect), 4 (bad), 9 (missing) → NaN before interpolation.
    _GRIDDER_TSQC = {"temperature", "conductivity", "salinity"}
    for _v in grid_vars:
        if _v in _GRIDDER_TSQC:
            var_data[_v][~np.isfinite(var_data[_v])] = np.nan

    _vel_vars = {
        "east_velocity",
        "north_velocity",
        "up_velocity",
        "current_speed",
        "current_direction",
    }
    if "velocity_flag" in ds.data_vars:
        _vflag = ds["velocity_flag"].values.astype(np.float64)
        _bad_vel = np.isin(np.round(_vflag).astype(np.int8), [3, 4, 9])
        for _v in _vel_vars:
            if _v in var_data:
                var_data[_v][_bad_vel] = np.nan
    else:
        for _v in _vel_vars:
            if _v in var_data:
                var_data[_v][~np.isfinite(var_data[_v])] = np.nan

    # ADCP standalone QC variables — mask velocity before gridding.
    # seabed_qc removes bins at/below the seafloor; percent_good_qc and
    # error_velocity_qc remove poor-quality pings.  All three use flag >= 3
    # (suspect or worse) as the NaN threshold, consistent with how the
    # rose diagram and stack report mask these variables.
    for _adcp_qc in ("seabed_qc", "percent_good_qc", "error_velocity_qc"):
        if _adcp_qc not in ds.data_vars:
            continue
        _qc = ds[_adcp_qc].values  # (time, N_LEVELS) after stack transpose
        _bad = np.isin(np.round(_qc).astype(np.int8), [3, 4, 9])
        for _v in _vel_vars:
            if _v in var_data:
                var_data[_v][_bad] = np.nan

    for t in range(n_time):
        p_col = pressure[t, :]
        p_valid_mask = np.isfinite(p_col)
        if p_valid_mask.sum() < 2:
            continue

        for vname in grid_vars:
            v_col = var_data[vname][t, :]
            both_valid = p_valid_mask & np.isfinite(v_col)
            if both_valid.sum() < 2:
                continue
            p_v = p_col[both_valid]
            v_v = v_col[both_valid]
            sort_idx = np.argsort(p_v)
            stacked[vname][:, t] = np.interp(
                p_grid,
                p_v[sort_idx],
                v_v[sort_idx],
                left=np.nan,
                right=np.nan,
            )

    # Build output dataset; skip variables that are entirely NaN
    data_vars: dict = {}
    for vname in grid_vars:
        if not np.all(np.isnan(stacked[vname])):
            a = dict(ds[vname].attrs)
            a["vertical_interpolation"] = (
                f"linear in pressure; no extrapolation outside {p_start:.0f}–{p_end:.0f} dbar"
            )
            data_vars[vname] = xr.Variable(
                ("time", "pressure"), stacked[vname].T, attrs=a
            )

    ds_out = xr.Dataset(
        data_vars,
        coords={
            "time": ds["time"],
            "pressure": xr.Variable(
                "pressure",
                p_grid,
                {
                    "units": "dbar",
                    "long_name": "sea water pressure",
                    "standard_name": "sea_water_pressure",
                    "axis": "Z",
                    "positive": "down",
                },
            ),
        },
    )

    prior_history = ds.attrs.get("history", "")
    ds_out.attrs.update(
        {
            **{k: v for k, v in ds.attrs.items() if k not in ("history",)},
            "p_start_dbar": p_start,
            "p_end_dbar": p_end,
            "dp_dbar": dp,
            "history": (
                prior_history
                + f"; Step 2 grid: linear interpolation in pressure onto {dp:.0f} dbar pressure grid "
                f"({p_start:.0f}–{p_end:.0f} dbar); no extrapolation"
            ),
        }
    )

    return to_canonical_units(ds_out, log_fn=print, stage="grid")


class MooringGridder:
    """Step 2: vertically interpolate stacked instruments onto a pressure grid → ``_grid.nc``."""

    def __init__(
        self,
        *,
        proc_dir: str,
    ) -> None:
        """Interpolate a mooring stack onto a regular pressure grid.

        Reads ``{mooring}_stack.nc`` (produced by :class:`MooringStacker`) and
        interpolates all scalar variables onto a uniform pressure axis defined by
        ``p_start``, ``p_end``, and ``dp`` (in dbar).  Writes
        ``{mooring}_grid.nc`` in the mooring proc directory.

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

    def grid(
        self,
        mooring_name: str,
        p_start: float = 200.0,
        p_end: float = 1000.0,
        dp: float = 20.0,
        force: bool = False,
    ) -> bool:
        """Interpolate the stacked mooring dataset onto a regular pressure grid.

        Reads ``{mooring}_stack.nc``, interpolates all variables onto a pressure
        axis from *p_start* to *p_end* in steps of *dp* dbar, and writes
        ``{mooring}_grid.nc``.

        Returns True on success, False on error.
        """
        proc_dir = self._resolve_proc_dir(mooring_name)
        merge_path = paths.product_file(proc_dir, mooring_name, "stack")
        output_path = paths.product_file(proc_dir, mooring_name, "grid")

        try:
            stack_found = merge_path.exists()
            output_found = output_path.exists()
        except (TimeoutError, OSError) as exc:
            print("ERROR: Cannot access data drive — is it connected?")
            print(f"       Path: {merge_path.parent}")
            print(f"       ({type(exc).__name__}: {exc})")
            return False

        if not stack_found:
            print(f"ERROR: Stack file not found: {merge_path}")
            print("       Run 'oceanarray process MOORING --stage stack' first.")
            return False

        if output_found and not force:
            _status("skip", self._rel(output_path))
            return True

        try:
            with xr.open_dataset(
                merge_path, engine="netcdf4", decode_timedelta=False
            ) as ds:
                ds_stack = ds.load()
        except (TimeoutError, OSError) as exc:
            print("ERROR: Cannot read stack file — is the data drive connected?")
            print(f"       ({type(exc).__name__}: {exc})")
            return False

        try:
            ds_out = grid(ds_stack, p_start=p_start, p_end=p_end, dp=dp)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return False
        # write() replaces atomically (temp file then rename), so the previous
        # output survives a failed write; do not unlink it first.
        write(ds_out, output_path, overwrite=True)
        _status("file", self._rel(output_path))
        return True
