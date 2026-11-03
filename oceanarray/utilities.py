"""Shared utility helpers used across the oceanarray processing pipeline."""

import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from oceanarray import logger


def should_skip_regeneration(
    output: Path,
    force: bool,
    skip_existing: bool,
    *sources: Path,
) -> bool:
    """Decide whether regenerating *output* can be skipped.

    Three-mode logic (matching the report layer / ctd_report CLI design):

    - ``force=True``         → never skip (always regenerate).
    - ``skip_existing=True`` → skip whenever *output* exists, regardless of
      source modification times (the fast, mtime-agnostic behaviour).
    - default                → skip only if *output* exists and is newer than
      every path in *sources* (mtime-based staleness).  A source newer than
      *output* — for example an edited mooring YAML — forces regeneration.

    Parameters
    ----------
    output : Path
        The file that would be (re)generated.
    force : bool
        If True, never skip.
    skip_existing : bool
        If True, skip whenever *output* exists regardless of source mtimes.
    *sources : Path
        Input files *output* is derived from (raw data, previous-stage NetCDF,
        the mooring YAML).  Non-existent sources are ignored.

    Returns
    -------
    bool
        True if regeneration can be skipped; False if *output* must be rebuilt.

    """
    if force:
        return False
    if not output.exists():
        return False
    if skip_existing:
        return True
    out_mtime = output.stat().st_mtime
    return not any(s.exists() and s.stat().st_mtime > out_mtime for s in sources)


def extract_inline_instruments(
    inline_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract processable instrument entries from the mooring YAML ``inline`` list.

    Most ``inline`` entries describe passive hardware (ropes, shackles, floats).
    This function filters for entries that have an ``instrument`` field and either
    a ``filename`` (to be processed) or ``skip: true`` (to be reported as skipped).

    Normalisation applied to each matching entry:

    - ``hab`` is set from ``hab_bottom`` if ``hab`` is not already present.
      For a downward-looking instrument the transducer is at the bottom of the
      housing (``hab_bottom``); for an upward-looking one use ``hab_top``.
    - ``serial`` is split on the first comma and the first token is used as the
      primary serial number.  Any remaining tokens are joined and stored under
      ``beacon_id`` in the returned dict.
    - ``source: "inline"`` is added to distinguish these entries from ``clamp``
      entries in downstream logging.

    **Serial parsing is fragile**: the convention ``serial: 16430, R01-024``
    assumes the *first* comma-separated token is the instrument serial and the
    rest is a transponder/beacon ID.  If a YAML author places the beacon serial
    first, the wrong value will be used as the output filename stem.  The
    validator emits a WARNING for any inline entry whose serial contains a comma
    so operators can confirm the ordering is correct.

    Args:
        inline_list: The raw list parsed from the ``inline`` YAML key.

    Returns
    -------
        List of normalised instrument-config dicts ready for stage processing.

    """
    result: list[dict[str, Any]] = []
    for entry in inline_list:
        if not isinstance(entry, dict):
            continue
        if "instrument" not in entry:
            continue
        if not entry.get("filename") and not entry.get("skip"):
            continue

        normalized: dict[str, Any] = dict(entry)

        if "hab" not in normalized and "hab_bottom" in normalized:
            normalized["hab"] = normalized["hab_bottom"]

        serial_raw = str(entry.get("serial", ""))
        if "," in serial_raw:
            parts = [p.strip() for p in serial_raw.split(",")]
            normalized["serial"] = parts[0]
            normalized["beacon_id"] = ", ".join(parts[1:])

        normalized["source"] = "inline"
        result.append(normalized)
    return result


def instrument_entries(mooring: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a mooring's processable instruments: clamp (or legacy) plus inline.

    The one place the clamp/``instruments`` fallback and the inline merge live:
    the ``clamp`` list (falling back to the legacy ``instruments`` key) followed
    by the normalised, processable entries from the ``inline`` hardware list. A
    present-but-null ``clamp:`` / ``inline:`` (YAML ``None``) is treated as empty.

    Parameters
    ----------
    mooring : dict
        A loaded mooring description.

    Returns
    -------
    list of dict
        One entry per instrument, in clamp-then-inline order.
    """
    clamp = mooring.get("clamp")
    if clamp is None:
        clamp = mooring.get("instruments")
    return list(clamp or []) + extract_inline_instruments(mooring.get("inline") or [])


def concat_with_scalar_vars(
    datasets: list[xr.Dataset],
    dim: str,
    scalar_vars: list[str] | None = None,
) -> xr.Dataset:
    """Concatenate datasets along a dimension, preserving scalar variables.

    Scalar (0-D) variables would normally be broadcast to the concatenation
    dimension by ``xr.concat``; this helper strips them beforehand and
    re-attaches the first occurrence after the concat so they remain 0-D.

    Parameters
    ----------
    datasets : list of xarray.Dataset
        Datasets to concatenate.
    dim : str
        Dimension along which to concatenate.
    scalar_vars : list of str, optional
        List of variable names to treat as scalars. If None, auto-detect
        scalar variables (those with ndim == 0 in any dataset).

    Returns
    -------
    xarray.Dataset
        Concatenated dataset with scalar variables re-attached as 0-D DataArrays.

    """
    scalar_storage = {}

    # Auto-detect scalar variables if not provided
    if scalar_vars is None:
        scalar_vars = set()
        for ds in datasets:
            scalar_vars.update([v for v in ds.data_vars if ds[v].ndim == 0])
        scalar_vars = list(scalar_vars)

    # Strip scalar variables and store them
    cleaned = []
    for _i, ds in enumerate(datasets):
        ds_copy = ds.copy()
        for var in scalar_vars:
            if var in ds_copy and ds_copy[var].ndim == 0:
                scalar_storage[(_i, var)] = ds_copy[var]
                del ds_copy[var]
        cleaned.append(ds_copy)

    # Concatenate non-scalar parts
    combined = xr.concat(cleaned, dim=dim)

    # Re-attach scalar variables as 0-D DataArrays
    for (_i, var), da in scalar_storage.items():
        combined[var] = da  # safe: 0-D, no coords

    return combined


def check_necessary_variables(ds: xr.Dataset, vars: list) -> None:
    """Check that all required variables are present in a dataset.

    Parameters
    ----------
    ds: xarray.Dataset
        Dataset that should be checked
    vars: list
        List of variables

    Raises
    ------
    KeyError:
        Raises an error if all vars not present in ds

    Notes
    -----
    Original Author: Callum Rollo

    """
    missing_vars = set(vars).difference(set(ds.variables))
    if missing_vars:
        msg = f"Required variables {list(missing_vars)} do not exist in the supplied dataset."
        raise KeyError(msg)


def get_time_key(ds: xr.Dataset) -> str:
    """Return the name of the time coordinate or variable in an xarray.Dataset.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset to inspect.

    Returns
    -------
    str
        The name of the time coordinate or variable.

    Raises
    ------
    ValueError
        If no time dimension or coordinate is found.

    """
    # Common time names to check
    time_candidates = ["TIME", "time", "Time", "DATETIME", "datetime", "date"]
    for name in time_candidates:
        if name in ds.coords or name in ds.variables:
            return name

    # Fallback: look for any variable or coordinate with datetime dtype
    for name in ds.coords:
        if np.issubdtype(ds.coords[name].dtype, np.datetime64):
            logger.log_warning(
                "No standard time coordinate found. Using first datetime coordinate: %s",
                name,
            )
            return name

    for name in ds.variables:
        if np.issubdtype(ds.variables[name].dtype, np.datetime64):
            logger.log_warning(
                "No standard time coordinate found. Using first datetime coordinate: %s",
                name,
            )
            return name

    raise ValueError("No valid time coordinate found in dataset.")


def get_dims(
    ds_gridded: xr.Dataset,
) -> tuple:
    """Extract pressure key, time key, and their dimensions from a dataset.

    Parameters
    ----------
    ds_gridded : xarray.Dataset
        Dataset containing the variables and dimensions.

    Returns
    -------
    pres_key : str or None
        Key for the pressure (or depth) variable; None if not found.
    time_key : str
        Key for the time variable.
    pres_dim : str or None
        Dimension associated with the pressure variable; None if not found.
    time_dim : str or None
        Dimension associated with the time variable.

    """
    if isinstance(ds_gridded, xr.DataArray):
        ds_gridded = ds_gridded.to_dataset()

    # Determine pressure key
    pressure_candidates = [
        "PRES_ADJUSTED",
        "PRESSURE",
        "pressure",
        "PRES",
        "PRES_INTERPOLATED",
    ]
    pres_key = None
    for name in pressure_candidates:
        if name in ds_gridded.coords or name in ds_gridded.variables:
            pres_key = name
            break

    # Fall back to depth if no pressure variable found
    dpth_key = None
    if pres_key is None:
        depth_candidates = [
            "DEPTH_ADJUSTED",
            "DEPTH",
            "depth",
            "Depth",
            "DEPTH_INTERPOLATED",
        ]
        for name in depth_candidates:
            if name in ds_gridded.coords or name in ds_gridded.variables:
                dpth_key = name
                break

    # Get time key using helper
    time_key = get_time_key(ds_gridded)

    # Determine dimensions
    time_dim = ds_gridded[time_key].dims[0] if ds_gridded[time_key].dims else None
    pres_dim = None
    if pres_key is None and dpth_key is not None:
        pres_dims = ds_gridded[dpth_key].dims
        if len(pres_dims) > 1:
            pres_dim = next(dim for dim in pres_dims if dim != time_dim)
        else:
            pres_dim = pres_dims[0]

    elif pres_key is not None:
        pres_dims = ds_gridded[pres_key].dims
        if len(pres_dims) > 1:
            pres_dim = next(dim for dim in pres_dims if dim != time_dim)
        else:
            pres_dim = pres_dims[0]

    return pres_key or dpth_key, time_key, pres_dim, time_dim


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
        datetime.strptime(timestr, "%Y/%m/%dT%H:%M:%SZ")  # RODB-style
    except ValueError:
        try:
            datetime.strptime(timestr, "%Y-%m-%dT%H:%M:%SZ")  # ISO8601 style
        except ValueError:
            return False
    return True


def _status(kind: str, msg: str, *, char: str = "=") -> None:
    """Print a formatted status line (section, instr, file, skip or error) to stdout."""
    # char only affects "section" borders: e.g. char="*" distinguishes mooring runs in
    # a multi-mooring script ("*** Stage 1: dsG3_2_2026 ***").
    if kind == "section":
        border = char * 3
        print(f"\n{border} {msg} {border}")
    elif kind == "instr":
        print(f"\n--- {msg} ---")
    elif kind == "file":
        print(f"Creating output file: {msg}")
    elif kind == "skip":
        print(f"  OUTFILE EXISTS: {msg}  (use --force to overwrite)")
    elif kind == "error":
        print(f"ERROR: {msg}")


def _format_rel_path(path: Path, root: Path) -> str:
    """Return *path* relative to *root* as a string, or its bare name if unrelated."""
    # Report output in a custom --output-dir is not under root; relative_to's
    # ValueError must never abort report generation from a logging call.
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _nice_colorbar_bounds(vmin: float, vmax: float, n: int = 20) -> np.ndarray:
    """Return a boundary array for a discrete colorbar with about *n* levels."""
    # Snap the step to the 1/2/2.5/5 family (as MaxNLocator does) and align bounds to
    # integer multiples of it spanning [vmin, vmax], so round tick labels (0.2, 100)
    # land on color-step boundaries rather than between them (as a 0.03 or 30 step
    # would). E.g. T 0.87-7.23 -> step 0.25, bounds 0.75-7.25; S 34.782-35.139 ->
    # step 0.02, bounds 34.78-35.14.
    span = vmax - vmin
    if span <= 0:
        return np.linspace(vmin - 1, vmin + 1, n + 1)
    raw_step = span / n
    mag = 10.0 ** math.floor(math.log10(raw_step))
    frac = raw_step / mag  # in [1, 10)
    nice_frac = min((1.0, 2.0, 2.5, 5.0, 10.0), key=lambda c: abs(c - frac))
    nice_step = nice_frac * mag
    lo = math.floor(vmin / nice_step) * nice_step
    hi = math.ceil(vmax / nice_step) * nice_step
    n_steps = int(round((hi - lo) / nice_step))
    return lo + nice_step * np.arange(n_steps + 1)


def nice_colorbar_ticks(vmin: float, vmax: float, *, max_ticks: int = 6) -> np.ndarray:
    """Return at most *max_ticks* nicely-rounded tick positions in ``[vmin, vmax]``.

    Decoupled from the colorbar's colour discretisation: a 20-level
    ``BoundaryNorm`` bar can still show ~6 round labels (e.g. 34.8, 34.9, … 35.2)
    instead of one label per boundary.  Uses :class:`matplotlib.ticker.MaxNLocator`
    with round step multiples so labels land on clean values.

    Parameters
    ----------
    vmin, vmax : float
        Data range of the colorbar.
    max_ticks : int
        Maximum number of ticks (approximate; the locator may return a few
        fewer).  Default 6.

    Returns
    -------
    numpy.ndarray
        Tick positions, clipped to ``[vmin, vmax]``.

    """
    import matplotlib.ticker as mticker

    ticks = mticker.MaxNLocator(nbins=max_ticks, steps=[1, 2, 2.5, 5, 10]).tick_values(
        vmin, vmax
    )
    return ticks[(ticks >= vmin) & (ticks <= vmax)]


# ---------------------------------------------------------------------------
# Output dtype helpers
# ---------------------------------------------------------------------------


def find_best_dtype(var_name: str, da: xr.DataArray) -> type:
    """Determine the optimal storage dtype for a variable.

    Parameters
    ----------
    var_name : str
        Variable name.
    da : xr.DataArray
        Data array to inspect.

    Returns
    -------
    type
        Recommended numpy dtype.

    Notes
    -----
    Rules applied in order:

    - String / datetime / timedelta / object variables: unchanged.
    - ``time`` in name: unchanged (preserve datetime64 / float encoding).
    - ``*_qc`` suffix or ``flag`` in name: ``int8``.
    - ``serial_number`` or ``serial``: ``int32``.
    - ``latitude`` / ``longitude`` in name: ``float64``.
    - Integer input: downsize to ``int32`` if stored as ``int64``, else unchanged.
    - ``float64`` input: ``float32``.
    - Anything else: unchanged.

    """
    input_dtype = da.dtype.type
    if da.dtype.kind in ("U", "S", "O", "M", "m"):
        return input_dtype
    if "time" in var_name.lower():
        return input_dtype
    if var_name.endswith("_qc") or "flag" in var_name:
        return np.int8
    if var_name in ("serial_number", "serial"):
        return np.int32
    if "latitude" in var_name.lower() or "longitude" in var_name.lower():
        return np.float64
    if da.dtype.kind in ("i", "u") and da.dtype.itemsize == 8:
        return np.int32
    if input_dtype == np.float64:
        return np.float32
    return input_dtype


def drop_all_zero_vars(ds: xr.Dataset, prefixes: list[str]) -> xr.Dataset:
    """Drop variables whose finite values are all zero.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset to filter.
    prefixes : list of str
        Variable name prefixes to check (e.g. ``["amplitude_beam", "analog_input_"]``).
        Any variable whose name starts with one of these prefixes and whose
        finite values are all zero (or has no finite values) is dropped.

    Returns
    -------
    xr.Dataset
        Dataset with all-zero prefix-matched variables removed.

    """
    to_drop = []
    for vname in ds.data_vars:
        if not any(vname.startswith(p) for p in prefixes):
            continue
        arr = ds[vname].values
        finite = arr[np.isfinite(arr)] if np.issubdtype(arr.dtype, np.floating) else arr
        if finite.size == 0 or np.all(finite == 0):
            to_drop.append(vname)
    return ds.drop_vars(to_drop) if to_drop else ds


def period_axis_ticks(
    p_min_days: float, p_max_days: float
) -> tuple[list[float], list[str]]:
    """Return human-readable period tick values and labels for a log-period axis.

    Canonical tick list covers 1 min through 1 yr.  Only ticks within
    ``[p_min_days, p_max_days]`` are returned.  Intended for spectral and
    wavelet period axes (both x and y) so that all figures share the same tick
    positions.

    Parameters
    ----------
    p_min_days:
        Minimum visible period in days (Nyquist end of the axis).
    p_max_days:
        Maximum visible period in days (long-period end of the axis).

    Returns
    -------
    tuple[list[float], list[str]]
        Pair of (values_in_days, labels) filtered to the visible range.

    """
    _candidates: list[tuple[float, str]] = [
        (1.0 / 1440, "1min"),
        (2.0 / 1440, "2min"),
        (5.0 / 1440, "5min"),
        (10.0 / 1440, "10min"),
        (30.0 / 1440, "30min"),
        (1.0 / 24, "1h"),
        (2.0 / 24, "2h"),
        (6.0 / 24, "6h"),
        (12.0 / 24, "12h"),
        (1.0, "1d"),
        (2.0, "2d"),
        (4.0, "4d"),
        (7.0, "7d"),
        (14.0, "14d"),
        (20.0, "20d"),
        (30.0, "30d"),
        (90.0, "90d"),
        (180.0, "180d"),
        (365.0, "1yr"),
    ]
    filtered = [(v, lbl) for v, lbl in _candidates if p_min_days <= v <= p_max_days]
    if not filtered:
        return [], []
    return [v for v, _ in filtered], [lbl for _, lbl in filtered]


def cast_output_dtypes(ds: xr.Dataset) -> xr.Dataset:
    """Cast every variable in *ds* to its optimal storage dtype.

    Calls :func:`find_best_dtype` per variable and rebuilds only those that
    change.  Attributes are preserved.  The input dataset is not modified.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset to cast.

    Returns
    -------
    xr.Dataset
        New dataset with optimised dtypes, ready for NetCDF output.

    """
    updates: dict[str, xr.Variable] = {}
    for vname in ds.data_vars:
        var = ds[vname]
        target = find_best_dtype(vname, var)
        if np.dtype(target) != var.dtype:
            if np.issubdtype(np.dtype(target), np.integer) and np.issubdtype(
                var.dtype, np.floating
            ):
                # NaN cannot be represented as an integer; replace before cast.
                # QC variables use 9 (CF "missing value"); other integer vars use 0.
                fill_val = 9 if (vname.endswith("_qc") or "flag" in vname) else 0
                safe_vals = np.where(np.isfinite(var.values), var.values, fill_val)
                updates[vname] = xr.Variable(
                    var.dims, safe_vals.astype(target), attrs=var.attrs
                )
            else:
                updates[vname] = xr.Variable(
                    var.dims, var.values.astype(target), attrs=var.attrs
                )
    if not updates:
        return ds
    return ds.assign(updates)


# ---------------------------------------------------------------------------
# Geographic coordinate helpers
# ---------------------------------------------------------------------------


def _parse_dms(s: str) -> float:
    """Parse 'DD MM.mmm N/S/E/W', signed 'DD MM.mmm' or a float to decimal degrees."""
    # The sign applies to the whole magnitude: "-27 48.000" is -(27 + 48/60) = -27.8,
    # not -27 + 48/60. A leading minus and a trailing S/W are equivalent markers.
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        pass
    parts = s.upper().split()
    hemi = parts[-1] if parts[-1] in ("N", "S", "E", "W") else None
    nums = parts[:-1] if hemi else parts
    negative = nums[0].startswith("-") or hemi in ("S", "W")
    magnitude = sum(abs(float(x)) / 60.0**i for i, x in enumerate(nums))
    return -magnitude if negative else magnitude


def format_latlon(lat: float, lon: float, *, ndp: int = 4) -> tuple[str, str]:
    """Return (lat, lon) as hemisphere-suffixed strings from signed decimal degrees.

    The sign selects the hemisphere and the magnitude is shown unsigned, so a
    formatted value can never carry both a minus sign and a hemisphere letter
    (e.g. the malformed ``"-27.8 W"``).  Latitude uses ``N``/``S``, longitude
    ``E``/``W``; a value of exactly 0 is rendered on the positive hemisphere.

    Parameters
    ----------
    lat, lon : float
        Latitude and longitude in signed decimal degrees.
    ndp : int, optional
        Number of decimal places for the magnitude (default 4 ≈ 11 m).

    Returns
    -------
    tuple[str, str]
        ``(lat_str, lon_str)``, e.g. ``("65.7319° N", "27.8000° W")``.

    """
    lat_hemi = "N" if lat >= 0 else "S"
    lon_hemi = "E" if lon >= 0 else "W"
    return (
        f"{abs(lat):.{ndp}f}° {lat_hemi}",
        f"{abs(lon):.{ndp}f}° {lon_hemi}",
    )


def parse_latlon(cfg: dict) -> tuple[float, float]:
    """Return (lat, lon) in decimal degrees from a mooring config dict.

    Parameters
    ----------
    cfg : dict
        Mooring configuration dictionary (from a ``.mooring.yaml`` file).

    Returns
    -------
    tuple[float, float]
        ``(latitude, longitude)`` in decimal degrees.

    """
    lat, lon, _ = parse_latlon_with_source(cfg)
    return lat, lon


#: The ``source`` value :func:`parse_latlon_with_source` returns when no position
#: key pair is found.  Test it with :func:`is_unknown_latlon_source`, never by
#: string-matching the literal, so the wording lives in one place.
LATLON_UNKNOWN_SOURCE = "unknown (defaulting to 0, 0)"


def is_unknown_latlon_source(source: str) -> bool:
    """Return True if *source* is the no-position sentinel from :func:`parse_latlon_with_source`."""
    return source == LATLON_UNKNOWN_SOURCE


def parse_latlon_with_source(cfg: dict) -> tuple[float, float, str]:
    """Return (lat, lon, source_key) from a mooring config dict.

    source_key is the YAML key pair used, e.g. ``'seabed_latitude/seabed_longitude'``,
    or :data:`LATLON_UNKNOWN_SOURCE` if none found.

    Zero values (lat == 0 and lon == 0) are treated as unfilled placeholders and
    skipped so that a later key with a real location is used instead.

    Parameters
    ----------
    cfg : dict
        Mooring configuration dictionary (from a ``.mooring.yaml`` file).

    Returns
    -------
    tuple[float, float, str]
        ``(latitude, longitude, source_key)`` where source_key identifies which
        YAML key pair was used.

    """
    for lat_key, lon_key in [
        ("seabed_latitude", "seabed_longitude"),
        ("deployment_latitude", "deployment_longitude"),
        ("planned_latitude", "planned_longitude"),
        ("latitude", "longitude"),
    ]:
        lat_s = cfg.get(lat_key)
        lon_s = cfg.get(lon_key)
        if lat_s is not None and lon_s is not None:
            try:
                lat_v = _parse_dms(str(lat_s))
                lon_v = _parse_dms(str(lon_s))
            except Exception:  # noqa: BLE001  — _parse_dms parse failure; try next key
                continue
            if lat_v == 0.0 and lon_v == 0.0:
                continue  # treat (0, 0) as an unfilled placeholder
            return lat_v, lon_v, f"{lat_key}/{lon_key}"
    return 0.0, 0.0, LATLON_UNKNOWN_SOURCE
