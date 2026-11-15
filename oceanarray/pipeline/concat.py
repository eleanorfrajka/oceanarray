"""Join one site's deployment stacks end to end in time: the pure :func:`concat` and its file verb."""

from __future__ import annotations

import hashlib
import os
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray import paths
from oceanarray.config import parameters as params
from oceanarray.exceptions import MooringError
from oceanarray.location import parse_coordinate
from oceanarray.pipeline.dataset import time_axis
from oceanarray.writers import write

#: Per-level coordinates every stack carries (``N_LEVELS``).
_LEVEL_COORDS = ("serial", "hab", "instrument_type", "source_stage")
#: Flag written outside a deployment's own steps (OceanSITES "missing value").
_QC_MISSING = 9


def _stack_settings(ds: xr.Dataset) -> dict:
    """Return the ``stack:`` block a stack was built with (from ``deployment_settings``)."""
    text = ds.attrs.get("deployment_settings", "")
    settings = _mooring.settings_from_yaml(text)
    if text and not settings:
        warnings.warn(
            f"{ds.attrs.get('mooring_name', 'a stack')}: its deployment_settings do not "
            "parse; the default slot rule is used for it.",
            stacklevel=2,
        )
    block = settings.get("stack")
    return block if isinstance(block, Mapping) else {}


def _slot_rule(stacks: Sequence[xr.Dataset], names: Sequence[str]) -> tuple[str, float]:
    """Return the one slot reference and tolerance every deployment agrees on."""
    rules = []
    for ds in stacks:
        block = _stack_settings(ds)
        rules.append(
            (
                str(block.get("slot_reference", params.CONCAT_SLOT_REFERENCE)),
                float(block.get("slot_tolerance_m", params.CONCAT_SLOT_TOLERANCE_M)),
            )
        )
    if len(set(rules)) > 1:
        listed = ", ".join(
            f"{n}: {r} ±{t:g} m" for n, (r, t) in zip(names, rules, strict=True)
        )
        raise MooringError(
            f"The deployments set different slot rules ({listed}); set the same "
            "stack: slot_reference and slot_tolerance_m in each mooring YAML."
        )
    reference, tolerance = rules[0]
    if reference not in ("depth", "hab"):
        raise MooringError(
            f"stack: slot_reference must be 'depth' or 'hab', not {reference!r}."
        )
    return reference, tolerance


def _check_time_steps(stacks: Sequence[xr.Dataset], names: Sequence[str]) -> int:
    """Return the shared ``dt_seconds``; refuse differing steps or an axis off whole steps.

    Parameters
    ----------
    stacks : sequence of xarray.Dataset
        The deployment stacks.
    names : sequence of str
        Their deployment names, for the error messages.

    Returns
    -------
    int
        The time step in seconds.

    Raises
    ------
    MooringError
        A stack without ``dt_seconds``, two different steps, or a time axis that
        does not start on a whole multiple of the step.
    """
    steps = {n: ds.attrs.get("dt_seconds") for n, ds in zip(names, stacks, strict=True)}
    if any(v is None for v in steps.values()) or len(set(steps.values())) > 1:
        listed = ", ".join(f"{n}: {v}" for n, v in steps.items())
        raise MooringError(
            f"The deployments' stacks have different time steps ({listed}); restack "
            "them with the same stack: dt_seconds."
        )
    dt = int(next(iter(steps.values())))
    step_ns = np.int64(dt) * 1_000_000_000
    for name, ds in zip(names, stacks, strict=True):
        t0 = ds["time"].values[0].astype("datetime64[ns]").astype(np.int64)
        if t0 % step_ns:
            raise MooringError(
                f"{name}: its stack's time axis is not on whole multiples of {dt} s "
                "(stacked by an older oceanarray); restack it before concatenating."
            )
    return dt


def _waterdepth(ds: xr.Dataset) -> float:
    """Return a stack's water depth (m), NaN when absent or not a number (``"UNK"``)."""
    try:
        return float(ds.attrs.get("waterdepth", "nan"))
    except (TypeError, ValueError):
        return float("nan")


def _level_depth(ds: xr.Dataset) -> np.ndarray:
    """Return each level's nominal depth (water depth − hab), NaN without a water depth."""
    return _waterdepth(ds) - ds["hab"].values.astype(float)


def _assign_slots(
    levels: list[dict], reference: str, tolerance: float
) -> tuple[np.ndarray, list[dict]]:
    """Give each point-instrument level a slot: same family, within *tolerance*.

    Deployments are taken in time order; a level joins the nearest slot of its
    family whose center (the mean of its members so far) is within the tolerance and that has
    no level of the same deployment yet, else it opens a new slot. ADCP and
    unknown types get slot -1.

    Parameters
    ----------
    levels : list of dict
        One dict per level, in deployment order: ``deployment``,
        ``instrument_type``, ``depth``, ``hab``.
    reference : {"depth", "hab"}
        Which key the tolerance is measured on.
    tolerance : float
        The largest distance (m) from a slot's center that still joins it.

    Returns
    -------
    tuple of (numpy.ndarray, list of dict)
        Each level's slot (-1: none), and each slot's ``family``, ``center`` and
        ``deployments``.
    """
    slots: list[dict] = []
    out = np.full(len(levels), -1, dtype=np.int32)
    for i, lev in enumerate(levels):
        family = params.INSTRUMENT_FAMILIES.get(lev["instrument_type"].lower())
        value = lev[reference]
        if family is None or not np.isfinite(value):
            continue
        best, best_dist = None, None
        for k, slot in enumerate(slots):
            if slot["family"] != family or lev["deployment"] in slot["deployments"]:
                continue
            dist = abs(slot["center"] - value)
            if dist <= tolerance and (best_dist is None or dist < best_dist):
                best, best_dist = k, dist
        if best is None:
            slots.append(
                {
                    "family": family,
                    "center": value,
                    "values": [value],
                    "deployments": {lev["deployment"]},
                }
            )
            best = len(slots) - 1
        else:
            slot = slots[best]
            slot["deployments"].add(lev["deployment"])
            slot["values"].append(value)
            # The center follows its members, so an instrument re-hung a little
            # deeper each deployment stays in one slot.
            slot["center"] = float(np.mean(slot["values"]))
        out[i] = best
    for k, slot in enumerate(slots):
        far = [v for v in slot["values"] if abs(v - slot["center"]) > tolerance]
        if far:
            warnings.warn(
                f"slot {k} ({slot['family']}, center {slot['center']:.0f} m) has "
                f"levels more than {tolerance:g} m from its center: "
                f"{', '.join(f'{v:.0f}' for v in far)} m; check the grouping in the "
                "report's instrument table.",
                stacklevel=2,
            )
    return out, slots


def _per_level(ds: xr.Dataset, name: str) -> np.ndarray | None:
    """Return a per-level (``N_LEVELS``) or scalar variable broadcast to the levels."""
    if name not in ds.variables or "time" in ds[name].dims:
        return None
    values = ds[name].values
    n = ds.sizes["N_LEVELS"]
    return np.full(n, values.item(), dtype=values.dtype) if values.ndim == 0 else values


def _join_level_values(parts: list[np.ndarray | None], sizes: list[int]) -> np.ndarray:
    """Join one per-level variable across deployments; fill a missing one."""
    present = [p for p in parts if p is not None]
    numeric = all(p.dtype.kind in "fiub" for p in present)
    if numeric:
        filled = [
            p.astype(np.float64) if p is not None else np.full(n, np.nan)
            for p, n in zip(parts, sizes, strict=True)
        ]
    else:
        filled = [
            p.astype(str) if p is not None else np.full(n, "", dtype=object)
            for p, n in zip(parts, sizes, strict=True)
        ]
        filled = [f.astype(object) for f in filled]
    return np.concatenate(filled)


def concat(
    stacks: Sequence[xr.Dataset],
    *,
    name: str,
    overlap: str = "newer",
    sources: Sequence[tuple[str, str]] | None = None,
    descriptions: Sequence[Mapping] | None = None,
) -> xr.Dataset:
    """Join one site's deployment stacks end to end on one time axis.

    The result holds every level of every deployment (ADCP bins included), each
    empty outside its own deployment's steps; nothing is interpolated or filled
    across a turnaround. Point instruments get a depth *slot* (same family, within
    the slot tolerance) so one depth can be followed across years.

    Parameters
    ----------
    stacks : sequence of xarray.Dataset
        Two or more deployment stacks (``oceanarray.stack`` output), in any order;
        they are put in time order.
    name : str
        The site's name, used for the file stem and ``site_name``.
    overlap : {"newer", "older"}, optional
        Which deployment's levels count at a step where two are in the water
        (``deployment_index``); both are kept in the result either way.
    sources : sequence of (str, str), optional
        ``(file path, sha256)`` of each stack, in the order of *stacks*.
    descriptions : sequence of Mapping, optional
        Each deployment's mooring description, in the order of *stacks*: its
        cruises and ships go into the ``DEPLOYMENT`` record.

    Returns
    -------
    xarray.Dataset
        On ``(time, N_LEVELS)`` with a ``DEPLOYMENT`` record and a
        ``deployment_index(time)``.

    Raises
    ------
    MooringError
        Fewer than two stacks, differing ``dt_seconds`` or slot rules, or a time
        axis not on whole steps.
    """
    if len(stacks) < 2:
        raise MooringError("concat needs at least two deployment stacks.")
    if overlap not in ("newer", "older"):
        raise MooringError(f"overlap must be 'newer' or 'older', not {overlap!r}.")
    order = np.argsort([ds["time"].values[0] for ds in stacks], kind="stable")
    stacks = [stacks[i] for i in order]
    sources = [sources[i] for i in order] if sources is not None else None
    descriptions = (
        [descriptions[i] for i in order] if descriptions is not None else None
    )
    names = [
        str(ds.attrs.get("mooring_name", f"deployment {i}"))
        for i, ds in enumerate(stacks)
    ]
    dt = _check_time_steps(stacks, names)
    reference, tolerance = _slot_rule(stacks, names)

    # The joined axis is the stack axis of the whole site window: every stack sits
    # on whole steps of the same dt, so each one's steps are a run of it.
    time = time_axis(
        stacks[0]["time"].values[0], max(ds["time"].values[-1] for ds in stacks), dt
    )
    n_time = time.size
    sizes = [ds.sizes["N_LEVELS"] for ds in stacks]
    level_ids = [
        f"{names[k]}/{serial}"
        for k, ds in enumerate(stacks)
        for serial in ds["serial"].values.astype(str)
    ]
    if len(set(level_ids)) != len(level_ids):
        raise MooringError(
            "Two levels share a deployment and serial; the deployments need distinct "
            "names (mooring_name) and serials."
        )

    # Join the (time, N_LEVELS) variables with xarray: each level has its own name,
    # so no cell is written twice, even where two deployments overlap in time.
    ts_names: list[str] = []
    for ds in stacks:
        ts_names += [
            v
            for v in ds.data_vars
            if ds[v].dims == ("time", "N_LEVELS") and v not in ts_names
        ]
    first_of = {v: next(ds[v] for ds in stacks if v in ds.data_vars) for v in ts_names}
    # Only a flag gets the "missing" flag outside its deployment; any other variable
    # becomes float so its gaps can be NaN.
    flags = {
        v
        for v, da in first_of.items()
        if da.dtype.kind in "iu" and v.endswith(("_qc", "_flag"))
    }
    parts = []
    offset = 0
    for ds in stacks:
        n = ds.sizes["N_LEVELS"]
        parts.append(
            xr.Dataset(
                {
                    v: (
                        ("time", "N_LEVELS"),
                        ds[v].values.astype(np.float32),
                    )
                    for v in ts_names
                    if v in ds.data_vars
                },
                coords={
                    "time": ds["time"].values.astype("datetime64[ns]"),
                    "N_LEVELS": level_ids[offset : offset + n],
                },
            )
        )
        offset += n
    # Merge as floats, so an empty cell is NaN and "no conflicts" only compares
    # cells two parts both hold; flags get their "missing" value afterwards.
    joined = xr.merge(parts, join="outer", compat="no_conflicts")
    joined = joined.reindex(time=time, N_LEVELS=level_ids)

    data_vars: dict[str, xr.Variable] = {
        v: xr.Variable(
            ("time", "N_LEVELS"),
            np.where(np.isnan(joined[v].values), _QC_MISSING, joined[v].values).astype(
                first_of[v].dtype
            )
            if v in flags
            else joined[v].values.astype(np.float32),
            attrs=dict(first_of[v].attrs),
        )
        for v in ts_names
    }

    level_names = sorted(
        {
            v
            for ds in stacks
            for v in ds.variables
            if v not in _LEVEL_COORDS
            and "time" not in ds[v].dims
            and ds[v].dims in ((), ("N_LEVELS",))
        }
    )
    for var in level_names:
        parts = [_per_level(ds, var) for ds in stacks]
        first = next(ds[var] for ds in stacks if var in ds.variables)
        data_vars[var] = xr.Variable(
            ("N_LEVELS",), _join_level_values(parts, sizes), attrs=dict(first.attrs)
        )

    deployment = np.repeat(np.arange(len(stacks), dtype=np.int16), sizes)
    serial = np.concatenate([ds["serial"].values.astype(str) for ds in stacks])
    instrument_type = np.concatenate(
        [ds["instrument_type"].values.astype(str) for ds in stacks]
    )
    hab = np.concatenate([ds["hab"].values.astype(float) for ds in stacks])
    depth = np.concatenate([_level_depth(ds) for ds in stacks])
    level_id = np.array(
        [f"{names[d]}/{s}" for d, s in zip(deployment, serial, strict=True)],
        dtype=object,
    )
    levels = [
        {
            "deployment": int(d),
            "instrument_type": t,
            "depth": float(z),
            "hab": float(h),
        }
        for d, t, z, h in zip(deployment, instrument_type, depth, hab, strict=True)
    ]
    slot, slot_list = _assign_slots(levels, reference, tolerance)
    # Number the slots deep-first (largest depth, or smallest hab, first) so the
    # numbers read in order down the mooring.
    slot_order = sorted(
        range(len(slot_list)),
        key=lambda k: (
            -slot_list[k]["center"] if reference == "depth" else slot_list[k]["center"]
        ),
    )
    renumber = {old: new for new, old in enumerate(slot_order)}
    slot = np.array([renumber.get(int(s), -1) for s in slot], dtype=np.int32)
    slot_list = [slot_list[k] for k in slot_order]

    data_vars["deployment"] = xr.Variable(
        ("N_LEVELS",), deployment, {"long_name": "index into DEPLOYMENT of this level"}
    )
    data_vars["level_id"] = xr.Variable(
        ("N_LEVELS",),
        level_id,
        {
            "long_name": "deployment/serial: unique per level (serials repeat across deployments)"
        },
    )
    data_vars["depth"] = xr.Variable(
        ("N_LEVELS",),
        depth.astype(np.float32),
        {"long_name": "nominal depth (water depth − hab) of the level", "units": "m"},
    )
    data_vars["slot"] = xr.Variable(
        ("N_LEVELS",),
        slot,
        {
            "long_name": "depth slot shared across deployments (-1: none)",
            "comment": f"same instrument family, {reference} within ±{tolerance:g} m",
        },
    )
    data_vars["slot_family"] = xr.Variable(
        ("SLOT",), np.array([s["family"] for s in slot_list], dtype=object)
    )
    data_vars["slot_reference_value"] = xr.Variable(
        ("SLOT",),
        np.array([s["center"] for s in slot_list], dtype=np.float32),
        {
            "long_name": f"mean {reference} of the slot's levels",
            "units": "m",
        },
    )

    # Which deployment counts at each step: -1 in gaps; the overlap rule decides
    # where two are in the water.
    index = np.full(n_time, -1, dtype=np.int16)
    sequence = (
        range(len(stacks)) if overlap == "newer" else reversed(range(len(stacks)))
    )
    for k in sequence:
        index[np.isin(time, stacks[k]["time"].values.astype("datetime64[ns]"))] = k
    data_vars["deployment_index"] = xr.Variable(
        ("time",),
        index,
        {
            "long_name": "deployment whose levels count at this step (-1: none in the water)",
            "comment": f"where two deployments overlap, the {overlap} one counts",
        },
    )

    def _deployment_var(
        values: list, attrs: dict | None = None, dtype: type = object
    ) -> xr.Variable:
        return xr.Variable(("DEPLOYMENT",), np.array(values, dtype=dtype), attrs or {})

    lats = [parse_coordinate(ds.attrs.get("latitude")) for ds in stacks]
    lons = [parse_coordinate(ds.attrs.get("longitude")) for ds in stacks]
    data_vars.update(
        {
            "deployment_name": _deployment_var(names),
            "deployment_time": _deployment_var(
                [str(ds.attrs.get("deployment_time", "")) for ds in stacks]
            ),
            "recovery_time": _deployment_var(
                [str(ds.attrs.get("recovery_time", "")) for ds in stacks]
            ),
            "deployment_latitude": _deployment_var(
                [np.nan if v is None else v for v in lats],
                {"units": "degrees_north"},
                np.float64,
            ),
            "deployment_longitude": _deployment_var(
                [np.nan if v is None else v for v in lons],
                {"units": "degrees_east"},
                np.float64,
            ),
            "deployment_waterdepth": _deployment_var(
                [_waterdepth(ds) for ds in stacks],
                {"units": "m"},
                np.float64,
            ),
            "deployment_settings": _deployment_var(
                [str(ds.attrs.get("deployment_settings", "")) for ds in stacks],
                {"comment": "the settings that built each stack, as YAML"},
            ),
            "deployment_history": _deployment_var(
                [str(ds.attrs.get("history", "")) for ds in stacks]
            ),
            # What each stack recorded about the instruments it left out or
            # flagged, so the record survives the join.
            **{
                f"deployment_{attr}": _deployment_var(
                    [str(ds.attrs.get(attr, "")) for ds in stacks]
                )
                for attr in (
                    "instruments_omitted",
                    "instruments_excluded",
                    "instruments_slower_than_step",
                    "stage2_instruments",
                )
                if any(attr in ds.attrs for ds in stacks)
            },
        }
    )
    if descriptions is not None:
        # The legacy single cruise/ship keys are the deployment values only.
        for side, keys in (
            ("deployment_cruise", ("deployment_cruise", "cruise")),
            ("deployment_ship", ("deployment_ship", "ship")),
            ("recovery_cruise", ("recovery_cruise",)),
            ("recovery_ship", ("recovery_ship",)),
        ):
            data_vars[side] = _deployment_var(
                [
                    str(next((d[k] for k in keys if d.get(k) not in (None, "")), ""))
                    for d in descriptions
                ]
            )
    if sources is not None:
        data_vars["source_file"] = _deployment_var([f for f, _ in sources])
        data_vars["source_sha256"] = _deployment_var([h for _, h in sources])

    out = xr.Dataset(
        data_vars,
        coords={
            "time": xr.Variable(
                "time",
                time,
                {"long_name": "time", "axis": "T", "standard_name": "time"},
            ),
            "serial": xr.Variable("N_LEVELS", serial),
            "hab": xr.Variable(
                "N_LEVELS", hab, {"units": "m", "long_name": "height above bottom"}
            ),
            "instrument_type": xr.Variable("N_LEVELS", instrument_type),
            **(
                {
                    "source_stage": xr.Variable(
                        "N_LEVELS",
                        _join_level_values(
                            [_per_level(ds, "source_stage") for ds in stacks], sizes
                        ),
                        dict(
                            next(ds for ds in stacks if "source_stage" in ds)[
                                "source_stage"
                            ].attrs
                        ),
                    )
                }
                if any("source_stage" in ds.variables for ds in stacks)
                else {}
            ),
        },
    )
    finite = [v for v in lats + lons if v is not None]
    out.attrs.update(
        {
            "site_name": name,
            "mooring_name": name,
            "deployments": " ".join(names),
            "n_deployments": len(stacks),
            "dt_seconds": dt,
            "slot_reference": reference,
            "slot_tolerance_m": tolerance,
            "overlap_rule": overlap,
            "latitude": f"{np.nanmedian([v for v in lats if v is not None]):.5f}"
            if finite
            else "",
            "longitude": f"{np.nanmedian([v for v in lons if v is not None]):.5f}"
            if finite
            else "",
            "deployment_time": str(stacks[0].attrs.get("deployment_time", "")),
            "recovery_time": str(stacks[-1].attrs.get("recovery_time", "")),
            "featureType": "timeSeries",
            "Conventions": "CF-1.13",
            "data_mode": "P",
            "history": (
                f"concat: {len(stacks)} deployment stacks ({', '.join(names)}) "
                f"joined on one {dt} s time axis, each level empty outside its "
                f"deployment, nothing filled across a turnaround, the {overlap} "
                "deployment counting where two overlap"
            ),
        }
    )
    return out


def _sha256(path: Path) -> str:
    """Return the SHA-256 of a file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def concat_moorings(
    descriptions: Sequence[str | Path],
    *,
    name: str,
    out_dir: str | Path | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> Path:
    """Concatenate the stacks of the moorings named, and write the site stack.

    Parameters
    ----------
    descriptions : sequence of str or Path
        Each mooring's ``*.mooring.yaml``; its stack is read from the YAML's folder
        (``<name>_stack.nc``).
    name : str
        The site's name: the output is ``<out_dir>/<name>_stackcat.nc``.
    out_dir : str or Path, optional
        Where to write; default ``<parent of the first mooring's folder>/<name>``.
    overwrite : bool, optional
        Replace an existing site stack.
    dry_run : bool, optional
        Check and report, write nothing.

    Returns
    -------
    Path
        The site stack's path (written unless *dry_run*).

    Raises
    ------
    MooringError
        A stack is missing, the output exists without *overwrite*, or
        :func:`concat` refuses.
    """
    first_dir = Path(descriptions[0]).parent
    target_dir = Path(out_dir) if out_dir is not None else first_dir.parent / name
    out_path = paths.product_file(target_dir, name, "stackcat")
    # A deployment's own folder holds its description and its stack; the site
    # stack goes elsewhere, so it can never replace a deployment's files.
    if any(target_dir.glob("*.mooring.yaml")):
        raise MooringError(
            f"{target_dir} holds a mooring description; write the site stack to "
            "its own folder (out_dir= / -o)."
        )
    if out_path.exists() and not overwrite and not dry_run:
        raise MooringError(f"{out_path} exists; pass overwrite=True (CLI --force).")
    stacks, sources, cfgs = [], [], []
    for desc in descriptions:
        yaml_path = Path(desc)
        cfg = _mooring.load(yaml_path)
        stack_path = paths.product_file(yaml_path.parent, cfg["name"], "stack")
        if not stack_path.exists():
            raise MooringError(
                f"{cfg['name']}: no stack at {stack_path}; run its stack first."
            )
        with xr.open_dataset(stack_path, engine="netcdf4") as opened:
            stacks.append(opened.load())
        # Relative to the site folder, so the site report can link each deployment.
        rel = os.path.relpath(stack_path.resolve(), target_dir.resolve())
        sources.append((Path(rel).as_posix(), _sha256(stack_path)))
        cfgs.append(cfg)
    ds = concat(stacks, name=name, sources=sources, descriptions=cfgs)
    if dry_run:
        print(
            f"would write {out_path} ({ds.sizes['time']} steps × {ds.sizes['N_LEVELS']} levels)"
        )
        return out_path
    target_dir.mkdir(parents=True, exist_ok=True)
    write(ds, out_path, overwrite=overwrite)
    print(f"wrote {out_path}")
    return out_path
