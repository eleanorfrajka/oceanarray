"""Grid a mooring's stack onto regular pressure levels: the pure :func:`grid` and MooringGridder."""

import json
import warnings
from collections.abc import Iterable, Mapping
from pathlib import Path

import numpy as np
import xarray as xr

from oceanarray import logger, paths
from oceanarray.config import parameters as params
from oceanarray.pipeline._run import DEFAULTS
from oceanarray.pipeline.concat import chunks, is_site_stack, own_levels, sha256
from oceanarray.pipeline.units import to_canonical_units
from oceanarray.writers import write
from oceanarray.writers.netcdf import as_stored

#: The velocity variables: one ``mask_flags`` key, ``velocity``, sets the flags
#: masked in all of them (by ``velocity_flag`` and the ADCP seabed, percent-good and
#: error-velocity flags).
VELOCITY_GROUP: frozenset[str] = frozenset(
    {
        "east_velocity",
        "north_velocity",
        "up_velocity",
        "current_speed",
        "current_direction",
    }
)

#: Derived from the gridded east and north velocity rather than interpolated: a
#: direction interpolated across 0/360° would point the wrong way.
_FROM_COMPONENTS: frozenset[str] = frozenset({"current_speed", "current_direction"})

#: The ADCP flags that mask the velocity group, as well as ``velocity_flag``.
_ADCP_VELOCITY_QC: tuple[str, ...] = (
    "seabed_qc",
    "percent_good_qc",
    "error_velocity_qc",
)

#: Variables on the levels that are not gridded. Instrument-frame and beam
#: velocities are excluded because the rotation to east/north/up is already
#: applied; diagnostics (amplitude, correlation, battery, orientation) have no
#: meaning between instruments; ``velocity_flag`` is a flag, not physics.
_GRID_EXCLUDE: frozenset[str] = frozenset(
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
        "velocity_flag",
        "tilt_from_pressure",
        "tilt_pressure_ref_hab",
        "heading",
        "pitch",
        "roll",
    }
)


def _flag_list(key: str, flags: object) -> tuple[int, ...]:
    """Return *flags* as a sorted tuple of OceanSITES flag values, or raise ValueError."""
    if isinstance(flags, int) and not isinstance(flags, bool):
        flags = (flags,)
    if isinstance(flags, str) or not isinstance(flags, Iterable):
        raise ValueError(
            f"mask_flags {key!r}: expected a list of flag values, got {flags!r}"
        )
    out = []
    for flag in flags:
        if (
            isinstance(flag, bool)
            or not isinstance(flag, int | np.integer)
            or int(flag) not in params.QC_FLAG_VALUES
        ):
            raise ValueError(
                f"mask_flags {key!r}: {flag!r} is not an OceanSITES flag value "
                f"({', '.join(map(str, params.QC_FLAG_VALUES))})"
            )
        out.append(int(flag))
    return tuple(sorted(set(out)))


def resolve_mask_flags(
    mask_flags: Iterable[int] | Mapping[str, Iterable[int]] | None = None,
) -> dict[str, tuple[int, ...]]:
    """Return the flags masked before gridding: a default and per-variable exceptions.

    Parameters
    ----------
    mask_flags : iterable of int, or Mapping of str to iterable of int, or None
        ``None`` for the default (``parameters.GRID_MASK_FLAGS``: 4 and 9); a list
        of flags for every variable; or a mapping with an optional ``"default"``
        and one key per exception: a variable name (masked by its ``<var>_qc``) or
        ``"velocity"`` for the velocity group (:data:`VELOCITY_GROUP`).

    Returns
    -------
    dict of str to tuple of int
        Always holds ``"default"``; the other keys as given.

    Raises
    ------
    ValueError
        An unknown key (or a member of the velocity group named on its own), or a
        value that is not a list of OceanSITES flag values.
    """
    out = {"default": tuple(params.GRID_MASK_FLAGS)}
    if mask_flags is None:
        return out
    given = (
        dict(mask_flags) if isinstance(mask_flags, Mapping) else {"default": mask_flags}
    )
    known = (set(params.VARIABLES) - VELOCITY_GROUP) | {"default", "velocity"}
    for key, flags in given.items():
        if key in VELOCITY_GROUP:
            raise ValueError(
                f"mask_flags {key!r}: the velocity variables share one key, 'velocity'"
            )
        if key not in known:
            raise ValueError(
                f"mask_flags {key!r} is not a variable; expected 'default', "
                "'velocity' or a variable name (e.g. 'temperature', 'dissolved_oxygen')"
            )
        out[key] = _flag_list(key, flags)
    return out


def grid(
    ds_stack: xr.Dataset,
    *,
    pmin: float = 200.0,
    pmax: float = 1000.0,
    dp: float = 20.0,
    mask_flags: Iterable[int] | Mapping[str, Iterable[int]] | None = None,
    slope_angle: float | None = None,
) -> xr.Dataset:
    """Interpolate a mooring stack, or a site stack, onto a regular pressure grid.

    Pure function: no file I/O. At each time step every variable is interpolated
    linearly in pressure from the instruments with valid pressure and values onto
    ``pmin:dp:pmax`` dbar, with no extrapolation. Values whose QC flag is in
    *mask_flags* are left out first: each variable by its ``<var>_qc``, the
    velocity group by ``velocity_flag`` and the ADCP seabed, percent-good and
    error-velocity flags. The stack has already left out temperature, salinity,
    pressure and the other non-velocity variables flagged 3, 4 or 9, so for those
    *mask_flags* can only leave out more (a 9 beside one of their values marks a
    level with no QC variable, and is not masked).

    A site stack (:func:`oceanarray.pipeline.concat.concat` output) is gridded one
    mooring per profile: at each step only the levels of the deployment
    ``deployment_index`` names; the overlap block is not gridded, and turnarounds
    stay empty. Its grid also carries ``deployment_index``, the ``DEPLOYMENT``
    record, and the along-slope angle.

    Parameters
    ----------
    ds_stack : xarray.Dataset
        The stack, as :func:`oceanarray.pipeline.stack.stack` returns it, or a site
        stack.
    pmin, pmax, dp : float, optional
        The pressure grid, in dbar.
    mask_flags : iterable of int, or Mapping of str to iterable of int, optional
        The flags left out before gridding (see :func:`resolve_mask_flags`);
        default 4 and 9 for every variable. Recorded as ``grid_mask_flags``.
    slope_angle : float, optional
        Site stack only: the along-slope direction, in degrees clockwise from
        north. Default: the direction of the record-mean, depth-mean velocity,
        when the flow is steady enough (``parameters.SLOPE_STEADINESS_MIN``);
        otherwise none, with a warning. Recorded as ``slope_angle_deg`` with
        ``slope_angle_source``.

    Returns
    -------
    xarray.Dataset
        The gridded product on ``(time, pressure)``.

    Raises
    ------
    ValueError
        If the stack has no ``pressure`` variable, or *mask_flags* is invalid.
    """
    flags = resolve_mask_flags(mask_flags)
    if slope_angle is not None and not np.isfinite(float(slope_angle)):
        raise ValueError(f"slope_angle={slope_angle!r} is not a finite number")
    p_grid = np.arange(pmin, pmax + dp * 0.5, dp)
    n_p = len(p_grid)

    # Canonicalise the stack's units (idempotent on a 0.5.0 stack; rewrites a
    # pre-0.5.0 one) so the grid inherits one spelling per quantity.
    ds = to_canonical_units(ds_stack, log_fn=print, stage="grid")
    site = is_site_stack(ds)
    level_dim = "N_DEPTHS" if site else "N_LEVELS"
    n_time = ds.sizes["time"]
    n_instr = ds.sizes[level_dim]

    print(
        f"Gridding {n_instr} {'depths' if site else 'instruments'} → {n_p} pressure "
        f"levels ({pmin:.0f}:{dp:.0f}:{pmax:.0f} dbar) × {n_time} time steps"
    )

    _warn_slow_instruments(ds)

    if "pressure" not in ds.data_vars:
        raise ValueError("'pressure' not found in the stack — cannot grid vertically")

    own = own_levels(ds) if site else None

    def _values(name: str) -> np.ndarray:
        values = ds[name].values.astype(np.float64)
        if own is not None:
            values[~own] = np.nan
        return values

    def _bad(name: str) -> np.ndarray:
        # A non-velocity value the stack kept is not flagged 9 (the stack left
        # those out), so a 9 beside one marks a level with no QC variable, as
        # the stack and concat write it: keep the value.
        qc = ds[f"{name}_qc"].values
        return np.isin(qc, flags.get(name, flags["default"])) & (qc != 9)

    pressure = _values("pressure")  # (time, levels)
    if "pressure_qc" in ds.data_vars:
        pressure[_bad("pressure")] = np.nan

    # Physics variables on the (time, levels) grid, excluding pressure and QC
    # flags; the excluded ones present are named, not dropped silently.
    candidates = [
        v
        for v in ds.data_vars
        if v not in ("pressure", "level_index")
        and ds[v].dims == ("time", level_dim)
        and not v.endswith("_qc")
    ]
    grid_vars = [
        v for v in candidates if v not in _GRID_EXCLUDE and v not in _FROM_COMPONENTS
    ]
    excluded = sorted(v for v in candidates if v in _GRID_EXCLUDE)
    if excluded:
        print(
            f"  NOTE: {len(excluded)} variable(s) not gridded (instrument-frame, "
            f"diagnostic or flag): {', '.join(excluded)}"
        )

    stacked: dict[str, np.ndarray] = {
        v: np.full((n_p, n_time), np.nan) for v in grid_vars
    }
    var_data = {v: _values(v) for v in grid_vars}

    # Flags masked before vertical interpolation: each variable by its own QC
    # flag, the velocity group by velocity_flag and the ADCP flags.
    velocity_flags = flags.get("velocity", flags["default"])
    bad_velocity = np.zeros((n_time, n_instr), dtype=bool)
    for q in ("velocity_flag", *_ADCP_VELOCITY_QC):
        if q in ds.data_vars:
            bad_velocity |= np.isin(ds[q].values, velocity_flags)
    for v in grid_vars:
        if v in VELOCITY_GROUP:
            var_data[v][bad_velocity] = np.nan
            if f"{v}_qc" in ds.data_vars:
                var_data[v][np.isin(ds[f"{v}_qc"].values, velocity_flags)] = np.nan
        elif f"{v}_qc" in ds.data_vars:
            var_data[v][_bad(v)] = np.nan

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
                f"linear in pressure; no extrapolation outside {pmin:.0f}–{pmax:.0f} dbar"
            )
            data_vars[vname] = xr.Variable(
                ("time", "pressure"), stacked[vname].T, attrs=a
            )
    if "east_velocity" in data_vars and "north_velocity" in data_vars:
        data_vars.update(_speed_direction(ds, stacked, pmin, pmax))
    if site:
        data_vars["deployment_index"] = ds["deployment_index"].variable
        data_vars.update(
            {v: ds[v].variable for v in ds.data_vars if ds[v].dims == ("DEPLOYMENT",)}
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

    masked = ", ".join(
        f"{k} {'/'.join(map(str, v)) or 'none'}" for k, v in flags.items()
    )
    prior_history = ds.attrs.get("history", "")
    ds_out.attrs.update(
        {
            **{k: v for k, v in ds.attrs.items() if k not in ("history",)},
            "data_mode": "P",
            "p_start_dbar": pmin,
            "p_end_dbar": pmax,
            "dp_dbar": dp,
            "grid_mask_flags": json.dumps({k: list(v) for k, v in flags.items()}),
            "history": (
                prior_history
                + f"; grid: flags {masked} masked first, then linear interpolation "
                f"in pressure onto {dp:g} dbar levels ({pmin:g}–{pmax:g} dbar), no "
                "extrapolation"
                + (
                    ", one mooring per profile (the one `deployment_index` names), "
                    "the overlap block not gridded, turnarounds empty"
                    if site
                    else ""
                )
            ),
        }
    )
    if site:
        ds_out.attrs.update(_slope_attrs(ds_out, slope_angle))

    return as_stored(to_canonical_units(ds_out, log_fn=print, stage="grid"))


def _speed_direction(
    ds: xr.Dataset, stacked: Mapping[str, np.ndarray], pmin: float, pmax: float
) -> dict[str, xr.Variable]:
    """Return current speed and direction from the gridded east and north velocity."""
    east, north = stacked["east_velocity"].T, stacked["north_velocity"].T
    note = (
        "from the gridded east_velocity and north_velocity (each linear in pressure; "
        f"no extrapolation outside {pmin:.0f}–{pmax:.0f} dbar)"
    )
    defaults = {
        "current_speed": {"units": "m s-1", "standard_name": "sea_water_speed"},
        "current_direction": {
            "units": "degree",
            "standard_name": "sea_water_velocity_to_direction",
        },
    }
    values = {
        "current_speed": np.hypot(east, north),
        "current_direction": np.degrees(np.arctan2(east, north)) % 360.0,
    }
    return {
        name: xr.Variable(
            ("time", "pressure"),
            values[name],
            attrs={
                **(dict(ds[name].attrs) if name in ds else defaults[name]),
                "vertical_interpolation": note,
            },
        )
        for name in values
    }


def _slope_attrs(ds: xr.Dataset, slope_angle: float | None) -> dict[str, object]:
    """Return the along-slope angle attributes of a site grid.

    Without a given angle, the direction of the record-mean, depth-mean velocity,
    when the flow is steady enough to have one: its steadiness (the mean velocity's
    speed over the mean of the daily speeds) must reach
    ``parameters.SLOPE_STEADINESS_MIN``. Otherwise no angle is recorded and the
    reason is.
    """
    if slope_angle is not None:
        return {
            "slope_angle_deg": float(slope_angle) % 360.0,
            "slope_angle_source": "given",
        }
    if "east_velocity" not in ds or "north_velocity" not in ds:
        return {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # an empty step's mean
        daily = (
            ds[["east_velocity", "north_velocity"]]
            .mean("pressure", skipna=True)
            .resample(time="1D")
            .mean()
        )
    east = daily["east_velocity"].values
    north = daily["north_velocity"].values
    ok = np.isfinite(east) & np.isfinite(north)
    if not ok.any():
        return {}
    mean_east, mean_north = east[ok].mean(), north[ok].mean()
    mean_speed = float(np.hypot(east[ok], north[ok]).mean())
    # No flow at all has no direction (and would divide by zero).
    steadiness = (
        float(np.hypot(mean_east, mean_north)) / mean_speed if mean_speed > 0 else 0.0
    )
    if not steadiness >= params.SLOPE_STEADINESS_MIN:
        print(
            f"  WARNING: no along-slope direction: the mean flow is weak against its "
            f"variability (steadiness {steadiness:.2f} < "
            f"{params.SLOPE_STEADINESS_MIN:g}); give slope_angle (--slope-angle)."
        )
        return {
            "slope_angle_source": (
                f"none: steadiness {steadiness:.2f} of the daily-mean, depth-mean "
                f"velocity is below {params.SLOPE_STEADINESS_MIN:g}"
            )
        }
    angle = float(np.degrees(np.arctan2(mean_east, mean_north)) % 360.0)
    return {
        "slope_angle_deg": round(angle, 1),
        "slope_angle_source": (
            "direction of the record-mean, depth-mean velocity "
            f"(steadiness {steadiness:.2f})"
        ),
    }


def _grid_file(
    ds_stack: xr.Dataset,
    mooring: dict,
    mooring_dir: Path,
    *,
    pmin: float,
    pmax: float,
    dp: float,
    mask_flags: Mapping[str, Iterable[int]] | None = None,
) -> Path:
    """Grid *ds_stack* and write ``<name>_grid.nc``; return its path."""
    ds = grid(ds_stack, pmin=pmin, pmax=pmax, dp=dp, mask_flags=mask_flags)
    return write(
        ds, paths.product_file(mooring_dir, mooring["name"], "grid"), overwrite=True
    )


def site_levels(ds: xr.Dataset, dp: float) -> tuple[float, float]:
    """Return the default pressure range of a site grid: its instruments' span, to whole *dp*.

    Parameters
    ----------
    ds : xarray.Dataset
        A site stack.
    dp : float
        The grid spacing, in dbar.

    Returns
    -------
    tuple of float
        ``(pmin, pmax)``: a low percentile of the shallowest depth's pressure and a
        high percentile of the deepest depth's (``parameters.SITE_GRID_PERCENTILES``,
        5 and 95), so the times an instrument rides above or below its usual
        depth stay on the grid; rounded outward to a multiple of *dp*, ``pmin``
        not below 0.

    Raises
    ------
    ValueError
        No depth has a finite pressure.
    """
    pressure = ds["pressure"].values.astype(float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # an empty depth's median
        median = np.nanmedian(pressure, axis=0)
    if not np.isfinite(median).any():
        raise ValueError("the site stack has no finite pressure")
    low, high = params.SITE_GRID_PERCENTILES
    lo = float(np.nanpercentile(pressure[:, np.nanargmin(median)], low))
    hi = float(np.nanpercentile(pressure[:, np.nanargmax(median)], high))
    return max(0.0, np.floor(lo / dp) * dp), float(np.ceil(hi / dp) * dp)


def _warn_other_settings(ds: xr.Dataset, used: Mapping[str, object]) -> None:
    """Warn for each deployment whose own ``grid:`` settings differ from the site grid's."""
    from oceanarray import mooring

    if "deployment_settings" not in ds:
        return
    for name, text in zip(
        ds["deployment_name"].values, ds["deployment_settings"].values, strict=True
    ):
        own = mooring.settings_from_yaml(str(text)).get("grid") or {}
        if not isinstance(own, Mapping):
            continue
        differ = []
        for key, value in own.items():
            if key == "mask_flags":
                try:
                    same = resolve_mask_flags(value) == used[key]
                except ValueError:
                    same = False
            else:
                try:
                    same = key in used and float(value) == float(used[key])
                except (TypeError, ValueError):
                    same = False
            if not same:
                differ.append(f"{key} {value}")
        if differ:
            print(
                f"  WARNING: {name}'s own grid: settings ({', '.join(differ)}) differ "
                "from the site grid's; the site grid uses the call's (recorded in the file)."
            )


def grid_site(
    stackcat: str | Path,
    *,
    pmin: float | None = None,
    pmax: float | None = None,
    dp: float | None = None,
    mask_flags: Iterable[int] | Mapping[str, Iterable[int]] | None = None,
    slope_angle: float | None = None,
    overwrite: bool = False,
) -> Path:
    """Grid a site stack and write ``<site>_gridcat.nc`` beside it.

    Parameters
    ----------
    stackcat : str or Path
        The site stack, ``<site>_stackcat.nc``.
    pmin, pmax : float, optional
        The pressure range, in dbar. Default: :func:`site_levels`. The
        deployments' own ``grid:`` settings are not used; a deployment whose
        settings differ from the site grid's is named in a warning.
    dp : float, optional
        The grid spacing, in dbar; default 20.
    mask_flags : iterable of int, or Mapping of str to iterable of int, optional
        As for :func:`grid`.
    slope_angle : float, optional
        As for :func:`grid`.
    overwrite : bool, optional
        Replace an existing site grid.

    Returns
    -------
    Path
        The site grid's path.

    Raises
    ------
    oceanarray.exceptions.MooringError
        The file is not a site stack, the output exists without *overwrite*, or a
        setting is invalid.
    """
    from oceanarray.exceptions import MooringError

    stackcat = Path(stackcat)
    with xr.open_dataset(stackcat, engine="netcdf4") as opened:
        ds = opened.load()
    if not is_site_stack(ds):
        raise MooringError(
            f"{stackcat} is not a site stack (oceanarray concat output)."
        )
    site = str(ds.attrs.get("site_name") or stackcat.name.removesuffix("_stackcat.nc"))
    out_path = paths.product_file(stackcat.parent, site, "gridcat")
    if out_path.exists() and not overwrite:
        raise MooringError(f"{out_path} exists; pass overwrite=True (CLI --force).")
    step = float(dp) if dp is not None else DEFAULTS["grid"]["dp"]
    try:
        if not step > 0:
            raise ValueError(f"dp={step:g} must be > 0")
        lo, hi = site_levels(ds, step)
        top = float(pmin) if pmin is not None else lo
        bottom = float(pmax) if pmax is not None else hi
        if bottom <= top:
            raise ValueError(f"pmax={bottom:g} must be above pmin={top:g}")
        flags = resolve_mask_flags(mask_flags)
        _warn_other_settings(
            ds, {"pmin": top, "pmax": bottom, "dp": step, "mask_flags": flags}
        )
        out = grid(
            ds,
            pmin=top,
            pmax=bottom,
            dp=step,
            mask_flags=flags,
            slope_angle=slope_angle,
        )
    except ValueError as exc:
        raise MooringError(f"{site}: cannot grid the site stack: {exc}") from exc
    out.attrs["stackcat_file"] = stackcat.name
    out.attrs["stackcat_sha256"] = sha256(stackcat)
    write(out, out_path, overwrite=overwrite, chunks=chunks(out))
    print(f"wrote {out_path}")
    return out_path


class MooringGridder:
    """Step 2: vertically interpolate stacked instruments onto a pressure grid → ``_grid.nc``.

    .. deprecated:: 0.6.0
        Removed in 0.7.0; see the migration page.
    """

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
        from oceanarray import _deprecated

        _deprecated.warn("oceanarray.pipeline.grid.MooringGridder")
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
            logger.status("skip", self._rel(output_path))
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
            ds_out = grid(ds_stack, pmin=p_start, pmax=p_end, dp=dp)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return False
        # write() replaces atomically (temp file then rename), so the previous
        # output survives a failed write; do not unlink it first.
        write(ds_out, output_path, overwrite=True)
        logger.status("file", self._rel(output_path))
        return True


def _warn_slow_instruments(ds: xr.Dataset) -> None:
    """Warn for the instruments the stack recorded as sampling slower than its step."""
    slow = ds.attrs.get("instruments_slower_than_step")
    if slow:
        print(
            f"  WARNING: sampled slower than the stack step: {slow}; the grid leaves "
            "the steps between their samples empty."
        )
