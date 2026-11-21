"""Join one site's deployment stacks end to end in time: the pure :func:`concat` and its file verb.

The site stack stores its time series by *depth* (``time, N_DEPTHS``). Depth
indices are reassigned whenever a deployment is added or the depth-matching rule changes,
so nothing may read a depth index as a depth: a depth's value is ``nominal_depth``.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import warnings
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from oceanarray import mooring as _mooring
from oceanarray import paths, provenance
from oceanarray.config import parameters as params
from oceanarray.exceptions import MooringError
from oceanarray.location import parse_coordinate
from oceanarray.pipeline import _stage, attribution, masking, sensors
from oceanarray.pipeline.dataset import time_axis
from oceanarray.pipeline.qc.originator import MAPPED_SUFFIX
from oceanarray.provenance import sha256
from oceanarray.writers import write

log = logging.getLogger(__name__)

#: Per-level coordinates every stack carries (``N_LEVELS``).
_LEVEL_COORDS = ("serial", "hab", "instrument_type", "source_stage")
#: Flag written outside a deployment's own steps (OceanSITES "missing value").
_QC_MISSING = params.QC_MISSING


def _stack_settings(ds: xr.Dataset) -> dict:
    """Return the ``stack:`` block a stack was built with (from ``deployment_settings``)."""
    text = ds.attrs.get("deployment_settings", "")
    settings = _mooring.settings_from_yaml(text)
    if text and not settings:
        warnings.warn(
            f"{ds.attrs.get('mooring_name', 'a stack')}: its deployment_settings do not "
            "parse; the default depth-matching rule is used for it.",
            stacklevel=2,
        )
    block = settings.get("stack")
    return block if isinstance(block, Mapping) else {}


def _depth_rule(
    stacks: Sequence[xr.Dataset], names: Sequence[str]
) -> tuple[str, float]:
    """Return the one depth reference and tolerance every deployment agrees on."""
    rules = []
    for ds in stacks:
        block = _stack_settings(ds)
        rules.append(
            (
                str(block.get("depth_match", params.CONCAT_DEPTH_MATCH)),
                float(block.get("depth_tolerance_m", params.CONCAT_DEPTH_TOLERANCE_M)),
            )
        )
    if len(set(rules)) > 1:
        listed = ", ".join(
            f"{n}: {r} ±{t:g} m" for n, (r, t) in zip(names, rules, strict=True)
        )
        raise MooringError(
            f"The deployments set different depth-matching rules ({listed}); set the same "
            "stack: depth_match and depth_tolerance_m in each mooring YAML."
        )
    reference, tolerance = rules[0]
    if reference not in ("depth", "hab"):
        raise MooringError(
            f"stack: depth_match must be 'depth' or 'hab', not {reference!r}."
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
        does not start on a whole multiple of the step or skips a step.
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
        times = ds["time"].values.astype("datetime64[ns]").astype(np.int64)
        if np.any(times % step_ns):
            raise MooringError(
                f"{name}: its stack's time axis is not on whole multiples of {dt} s "
                "(stacked by an older oceanarray); restack it before concatenating."
            )
        if np.any(np.diff(times) != step_ns):
            raise MooringError(
                f"{name}: its stack's time axis skips steps; concat places each "
                "stack as one run of whole steps, so restack it."
            )
    return dt


def _check_people_cleaned(stacks: Sequence[xr.Dataset], names: Sequence[str]) -> None:
    """Refuse a stack whose stage files' people were not cleaned at stage 1.

    Raises
    ------
    MooringError
        A stack written by an oceanarray older than
        :data:`~oceanarray.pipeline.attribution.CLEANED_SINCE`, or built from
        stage files whose stage 1 did not clean people (``people_not_cleaned``).
    """
    stale = [
        n
        for n, ds in zip(names, stacks, strict=True)
        if ds.attrs.get("people_not_cleaned")
        or not provenance.requires_version(ds.attrs, attribution.CLEANED_SINCE)
    ]
    if stale:
        raise MooringError(
            f"{', '.join(stale)}: built from stage files whose people were not "
            "cleaned at stage 1; rerun from stage 1, then stack, before "
            "concatenating."
        )


def _waterdepth(ds: xr.Dataset) -> float:
    """Return a stack's water depth (m), NaN when absent or not a number (``"UNK"``)."""
    try:
        return float(ds.attrs.get("waterdepth", "nan"))
    except (TypeError, ValueError):
        return float("nan")


def _level_depth(ds: xr.Dataset) -> np.ndarray:
    """Return each level's nominal depth (water depth − hab), NaN without a water depth."""
    return _waterdepth(ds) - ds["hab"].values.astype(float)


def _family(instrument_type: str, serial: str = "") -> str:
    """Return a level's depth family; an unlisted type is its own family.

    An ADCP head (``<serial>_hd``: pressure and temperature, no velocity) is a
    family of its own, ``adcp_head``, so it never shares a depth with a bin.
    """
    itype = str(instrument_type).lower()
    family = params.INSTRUMENT_FAMILIES.get(itype, itype)
    if family == "adcp" and str(serial).endswith("_hd"):
        return "adcp_head"
    return family


def _tolerances(levels: list[dict], tolerance: float) -> np.ndarray:
    """Return each level's depth tolerance (m): half the bin size for ADCP bins.

    An ADCP's bin size is the spacing of its bins (the median step of their
    ``hab``); an ADCP with fewer than two bins, and an ADCP head, keep *tolerance*.
    """
    out = np.full(len(levels), float(tolerance))
    groups: dict[tuple[int, str], list[int]] = {}
    for i, lev in enumerate(levels):
        if _family(lev["instrument_type"], lev["serial"]) == "adcp":
            groups.setdefault(
                (lev["deployment"], paths.adcp_parent_serial(lev["serial"])), []
            ).append(i)
    for members in groups.values():
        bins = sorted(levels[i]["hab"] for i in members)
        steps = np.abs(np.diff(bins))
        steps = steps[np.isfinite(steps) & (steps > 0)]
        if steps.size:
            out[members] = float(np.median(steps)) / 2
    return out


def _assign_depths(
    levels: list[dict], reference: str, tolerance: float
) -> tuple[np.ndarray, list[dict]]:
    """Give every level a depth: same family, within the tolerance, one level per deployment.

    Deployments are taken in time order; a level joins the nearest depth of its
    family whose center (the mean of its members so far) is within the tolerance
    and that has no level of the same deployment yet, else it opens a new depth.
    The tolerance is *tolerance*, or half the bin size for ADCP levels (the larger
    of the level's and the depth's when they differ). A level without a reference
    value (no water depth for a depth reference) has a depth of its own.

    Parameters
    ----------
    levels : list of dict
        One dict per level, in deployment order: ``deployment``, ``serial``,
        ``instrument_type``, ``depth``, ``hab``.
    reference : {"depth", "hab"}
        Which key the tolerance is measured on.
    tolerance : float
        The largest distance (m) from a depth's center that still joins it.

    Returns
    -------
    tuple of (numpy.ndarray, list of dict)
        Each level's depth index, and each depth's ``family``, ``center``,
        ``tolerance``, ``values`` and ``deployments``.
    """
    depth_groups: list[dict] = []
    tolerances = _tolerances(levels, tolerance)
    out = np.full(len(levels), -1, dtype=np.int32)
    for i, lev in enumerate(levels):
        family = _family(lev["instrument_type"], lev["serial"])
        value = float(lev[reference])
        tol = float(tolerances[i])
        best, best_dist = None, None
        if np.isfinite(value):
            for k, depth_group in enumerate(depth_groups):
                if (
                    depth_group["family"] != family
                    or lev["deployment"] in depth_group["deployments"]
                ):
                    continue
                dist = abs(depth_group["center"] - value)
                if dist <= max(tol, depth_group["tolerance"]) and (
                    best_dist is None or dist < best_dist
                ):
                    best, best_dist = k, dist
        if best is None:
            depth_groups.append(
                {
                    "family": family,
                    "center": value,
                    "tolerance": tol,
                    "values": [value],
                    "deployments": {lev["deployment"]},
                }
            )
            best = len(depth_groups) - 1
        else:
            depth_group = depth_groups[best]
            depth_group["deployments"].add(lev["deployment"])
            depth_group["values"].append(value)
            depth_group["tolerance"] = max(depth_group["tolerance"], tol)
            # The center follows its members, so an instrument re-hung a little
            # deeper each deployment stays in one depth.
            depth_group["center"] = float(np.mean(depth_group["values"]))
        out[i] = best
    for depth_group in depth_groups:
        far = [
            v
            for v in depth_group["values"]
            if np.isfinite(v)
            and abs(v - depth_group["center"]) > depth_group["tolerance"]
        ]
        if far:
            warnings.warn(
                f"a {depth_group['family']} depth (center {depth_group['center']:.0f} m) has "
                f"levels more than {depth_group['tolerance']:g} m from its center: "
                f"{', '.join(f'{v:.0f}' for v in far)} m; check the grouping in the "
                "report's instrument table.",
                stacklevel=2,
            )
    return out, depth_groups


def _time_text(value: object) -> str:
    """Return a stack's deployment or recovery time as ISO 8601, or as given when blank."""
    try:
        return _stage.iso_time(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)


def _deployment_code(ds: xr.Dataset) -> str:
    """Return a stack's deployment code, ``YYYYMM`` of its deployment time ("" if unknown)."""
    try:
        return pd.Timestamp(str(ds.attrs["deployment_time"])).strftime("%Y%m")
    except (KeyError, ValueError):
        return ""


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
            p.astype(str).astype(object)
            if p is not None
            else np.full(n, "", dtype=object)
            for p, n in zip(parts, sizes, strict=True)
        ]
    return np.concatenate(filled)


def _place(
    starts: Sequence[int],
    lengths: Sequence[int],
    level_depth_index: np.ndarray,
    level_deployment: np.ndarray,
    n_time: int,
    n_depths: int,
    overlap: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Decide which level fills each step of each depth, and which steps are overlap.

    A level occupies its deployment's steps in its depth. Where two levels occupy
    the same step of one depth (two deployments in the water at once), the one the
    overlap rule picks fills it and the other's step goes to the overlap block.

    Returns
    -------
    tuple of numpy.ndarray
        ``level_index`` (time, N_DEPTHS; -1 empty), then the overlap block's rows
        (time index), depth indices and levels, sorted by time and depth.
    """
    level_index = np.full((n_time, n_depths), -1, dtype=np.int32)
    ov_rows, ov_depths, ov_levels = [], [], []
    for lvl in range(level_depth_index.size):
        k = int(level_deployment[lvl])
        s = int(level_depth_index[lvl])
        rows = np.arange(starts[k], starts[k] + lengths[k])
        current = level_index[rows, s]
        taken = current >= 0
        if overlap == "newer":
            # Placed in time order, so this level is the newer one: it takes the
            # step and the level it displaces goes to the overlap block.
            ov_rows.append(rows[taken])
            ov_depths.append(np.full(taken.sum(), s))
            ov_levels.append(current[taken])
            level_index[rows, s] = lvl
        else:
            ov_rows.append(rows[taken])
            ov_depths.append(np.full(taken.sum(), s))
            ov_levels.append(np.full(taken.sum(), lvl))
            level_index[rows[~taken], s] = lvl
    rows = np.concatenate(ov_rows) if ov_rows else np.array([], dtype=np.int64)
    overlap_depth_index = (
        np.concatenate(ov_depths) if ov_depths else np.array([], dtype=np.int64)
    )
    levels = np.concatenate(ov_levels) if ov_levels else np.array([], dtype=np.int64)
    order = np.lexsort((overlap_depth_index, rows))
    return level_index, rows[order], overlap_depth_index[order], levels[order]


def _same_value(a: object, b: object) -> bool:
    """Return whether two attribute values agree: as numbers when both are, else as text."""
    try:
        return float(a) == float(b)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


#: The DEPLOYMENT record variable of each field in ``mooring.SOURCE_FIELDS``
#: whose name differs from the field's: its source is ``<record variable>_source``.
_SOURCE_RECORD: dict[str, str] = {
    "waterdepth": "deployment_waterdepth",
    "latitude": "deployment_latitude",
    "longitude": "deployment_longitude",
}

#: Global attributes concat writes itself (or as its own DEPLOYMENT variables);
#: any other stack attribute is kept once when every deployment agrees, else it
#: becomes a ``deployment_<name>`` variable.
_OWN_ATTRS = frozenset(
    {
        "mooring_name",
        "site_name",
        "waterdepth",
        "latitude",
        "longitude",
        "deployment_time",
        "recovery_time",
        "dt_seconds",
        "deployment_settings",
        "history",
        "Conventions",
        *(f"{field}_source" for field in _mooring.SOURCE_FIELDS),
        "featureType",
        "data_mode",
        "oceanarray_version",
        "oceanarray_commit",  # development builds before 0.6.1; never carried over
        *(f"source_{f}" for f in attribution.CITATION_FIELDS),
        "source_note",
        "citation",
        "license",
        "references",
        "people_not_cleaned",
        masking.STACK_ATTR,
        *attribution.PI_ATTRS.values(),
        *attribution.CONTRIBUTOR_ATTRS.values(),
    }
)


def concat(
    stacks: Sequence[xr.Dataset],
    *,
    name: str,
    overlap: str = "newer",
    sources: Sequence[tuple[str, str]] | None = None,
    descriptions: Sequence[Mapping] | None = None,
    mask_flags: Iterable[int] | Mapping[str, Iterable[int]] | None = None,
) -> xr.Dataset:
    """Join one site's deployment stacks end to end on one time axis, by depth.

    Every level of every deployment (ADCP bins included) gets a *depth*: same
    instrument family, within the depth tolerance, at most one level per
    deployment. The time series are stored on ``(time, N_DEPTHS)``, with
    ``level_index`` naming the level behind each value; a depth is empty outside its
    levels' deployments, and nothing is interpolated or filled across a
    turnaround. Where two deployments are in the water at once, the overlap rule
    picks the level that fills a depth's step and the other's values go to the
    overlap block (``N_OVERLAP``), so nothing is lost.

    Depth indices are not stable: adding a deployment or changing the depth-matching rule
    renumbers them. A depth's value is ``nominal_depth`` (or
    ``depth_match_value``), never its number.

    Parameters
    ----------
    stacks : sequence of xarray.Dataset
        Two or more deployment stacks (``oceanarray.stack`` output), in any order;
        they are put in time order. They may be lazily opened: each variable of
        each stack is read once, so the peak memory is about the result plus one
        variable of one stack.
    name : str
        The site's name, used for the file stem and ``site_name``.
    overlap : {"newer", "older"}, optional
        Which deployment's level fills a depth's step where two are in the water
        (also ``deployment_index``); the other's values go to the overlap block.
    sources : sequence of (str, str), optional
        ``(file path, sha256)`` of each stack, in the order of *stacks*.
    descriptions : sequence of Mapping, optional
        Each deployment's mooring description, in the order of *stacks*: its
        cruises and ships go into the ``DEPLOYMENT`` record.
    mask_flags : iterable of int, or Mapping, optional
        The Stack cat's rule (``stack_mask_flags``). By default the deployments'
        rule, which must then be the same for all of them; each deployment's own is
        kept in ``deployment_stack_mask_flags``.

    Returns
    -------
    xarray.Dataset
        Time series on ``(time, N_DEPTHS)`` with ``level_index``; the level table on
        ``N_LEVELS``; the depth table on ``N_DEPTHS``; the ``DEPLOYMENT`` record and the
        overlap block on ``N_OVERLAP``.

    Raises
    ------
    MooringError
        Fewer than two stacks, differing ``dt_seconds`` or depth-matching rules,
        differing ``mask_flags`` rules with none given, or a time axis not on whole
        steps.
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
    reference, tolerance = _depth_rule(stacks, names)
    own_rules = [masking.to_json(masking.from_attrs(ds.attrs)) for ds in stacks]
    if mask_flags is not None:
        try:
            site_rule = masking.to_json(masking.resolve_mask_flags(mask_flags))
        except ValueError as exc:
            raise MooringError(f"{name}: {exc}") from exc
    elif len(set(own_rules)) > 1:
        raise MooringError(
            f"{name}: the deployments hide different flags ("
            + "; ".join(f"{n} {r}" for n, r in zip(names, own_rules, strict=True))
            + "); give mask_flags (CLI --mask-flags) for the site."
        )
    else:
        site_rule = own_rules[0]

    # The joined axis is the stack axis of the whole site window: every stack sits
    # on whole steps of the same dt, so each one's steps are a run of it.
    t_first = [ds["time"].values[0].astype("datetime64[ns]") for ds in stacks]
    t_last = [ds["time"].values[-1].astype("datetime64[ns]") for ds in stacks]
    time = time_axis(t_first[0], max(t_last), dt)
    n_time = time.size
    step = np.timedelta64(dt, "s").astype("timedelta64[ns]")
    starts = [int((t - time[0]) // step) for t in t_first]
    lengths = [ds.sizes["time"] for ds in stacks]
    sizes = [ds.sizes["N_LEVELS"] for ds in stacks]

    # ── The level table ───────────────────────────────────────────────
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
    if len(set(level_id)) != level_id.size:
        raise MooringError(
            "Two levels share a deployment and serial; the deployments need distinct "
            "names (mooring_name) and serials."
        )
    levels = [
        {
            "deployment": int(d),
            "serial": s,
            "instrument_type": t,
            "depth": float(z),
            "hab": float(h),
        }
        for d, s, t, z, h in zip(
            deployment, serial, instrument_type, depth, hab, strict=True
        )
    ]
    depth_index, depth_list = _assign_depths(levels, reference, tolerance)
    # Number the depths deep-first (largest depth, or smallest hab, first); a depth
    # with no known reference value goes last.
    depth_order = sorted(
        range(len(depth_list)),
        key=lambda k: (
            not np.isfinite(depth_list[k]["center"]),
            -depth_list[k]["center"]
            if reference == "depth"
            else depth_list[k]["center"],
        ),
    )
    renumber = {old: new for new, old in enumerate(depth_order)}
    depth_index = np.array([renumber[int(s)] for s in depth_index], dtype=np.int32)
    depth_list = [depth_list[k] for k in depth_order]
    n_depths = len(depth_list)

    level_index, ov_rows, ov_depths, ov_levels = _place(
        starts, lengths, depth_index, deployment, n_time, n_depths, overlap
    )

    # ── The time series, one stack variable at a time ─────────────────
    ts_names: list[str] = []
    for ds in stacks:
        ts_names += [
            v
            for v in ds.data_vars
            if ds[v].dims == ("time", "N_LEVELS") and v not in ts_names
        ]
    first_of = {v: next(ds[v] for ds in stacks if v in ds.data_vars) for v in ts_names}
    # Only a flag gets the "missing" flag where its depth is empty; any other
    # variable becomes float so its gaps can be NaN.
    flags = {
        v
        for v, da in first_of.items()
        if da.dtype.kind in "iu" and v.endswith(("_qc", "_flag", MAPPED_SUFFIX))
    }
    level_offset = np.cumsum([0, *sizes])
    grid = {
        v: np.full(
            (n_time, n_depths),
            _QC_MISSING if v in flags else np.nan,
            dtype=first_of[v].dtype if v in flags else np.float32,
        )
        for v in ts_names
    }
    over = {
        v: np.full(
            ov_rows.size,
            _QC_MISSING if v in flags else np.nan,
            dtype=first_of[v].dtype if v in flags else np.float32,
        )
        for v in ts_names
    }
    for k, ds in enumerate(stacks):
        a, n = starts[k], lengths[k]
        rows = slice(a, a + n)
        for v in ts_names:
            if v not in ds.data_vars:
                parents = (
                    ["east_velocity", "north_velocity", "up_velocity"]
                    if v == "velocity_flag"
                    else [v.removesuffix(MAPPED_SUFFIX).removesuffix("_qc")]
                )
                parents = [p for p in parents if p in ds.data_vars]
                if v not in flags or not parents:
                    continue
                # A deployment without this flag variable: 0 (not QC'd) where it
                # has a value (any velocity component, for velocity_flag), 9 where
                # it has none.
                present = np.zeros((ds.sizes["time"], ds.sizes["N_LEVELS"]), dtype=bool)
                for p in parents:
                    present |= np.isfinite(ds[p].transpose("time", "N_LEVELS").values)
                values = np.where(present, params.QC_NOT_EVALUATED, _QC_MISSING).astype(
                    first_of[v].dtype
                )
            else:
                values = ds[v].transpose("time", "N_LEVELS").values
            for j in range(sizes[k]):
                lvl = level_offset[k] + j
                s = depth_index[lvl]
                mine = np.flatnonzero(level_index[rows, s] == lvl)
                grid[v][a + mine, s] = values[mine, j]
                spill = np.flatnonzero(ov_levels == lvl)
                if spill.size:
                    over[v][spill] = values[ov_rows[spill] - a, j]
            del values

    data_vars: dict[str, xr.Variable] = {
        v: xr.Variable(("time", "N_DEPTHS"), grid[v], attrs=dict(first_of[v].attrs))
        for v in ts_names
    }
    data_vars["level_index"] = xr.Variable(
        ("time", "N_DEPTHS"),
        level_index.astype(np.int16),
        {
            "long_name": "index into N_LEVELS of the level behind each value (-1: none)",
        },
    )

    level_names = sorted(
        {
            v
            for ds in stacks
            for v in ds.variables
            if v not in _LEVEL_COORDS
            and not sensors.is_record(v)
            and "time" not in ds[v].dims
            and ds[v].dims in ((), ("N_LEVELS",))
        }
    )
    for var in level_names:
        parts = [_per_level(ds, var) for ds in stacks]
        first = next(ds[var] for ds in stacks if var in ds.variables)
        joined = _join_level_values(parts, sizes)
        if var.endswith(("_flag_translation", "_flag_translation_counts")):
            # A deployment whose instruments had no originator flags.
            empty = "none" if var.endswith("_translation") else "{}"
            joined = np.where(joined == "", empty, joined)
        data_vars[var] = xr.Variable(("N_LEVELS",), joined, attrs=dict(first.attrs))
    # Each deployment's sensor records, named with its deployment code so an
    # instrument recalibrated between deployments keeps a record per deployment;
    # the level links follow the new names.
    renamed: list[dict[str, str]] = []
    codes = [_deployment_code(ds) for ds in stacks]
    if "" in codes or len(set(codes)) < len(codes):
        # Two deployments in one month, or one without a deployment time: the
        # deployment names keep the records apart.
        codes = [re.sub(r"\W+", "_", n) for n in names]
    for ds, code in zip(stacks, codes, strict=True):
        names_k = {}
        for rec, attrs in sensors.records(ds).items():
            names_k[rec] = f"{rec}_{code}" if code else rec
            data_vars[names_k[rec]] = xr.Variable((), np.int8(0), attrs=attrs)
        renamed.append(names_k)
    if "sensor" in data_vars:
        offsets = np.cumsum([0, *sizes])
        links = np.array(data_vars["sensor"].values, dtype=object)
        for k, names_k in enumerate(renamed):
            for j in range(offsets[k], offsets[k + 1]):
                links[j] = names_k.get(str(links[j]), str(links[j]))
        data_vars["sensor"] = xr.Variable(
            ("N_LEVELS",), links, attrs=dict(data_vars["sensor"].attrs)
        )
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
    data_vars["depth_index"] = xr.Variable(
        ("N_LEVELS",),
        depth_index,
        {
            "long_name": "index into N_DEPTHS of the level: its depth, shared across deployments",
            "comment": (
                f"same instrument family, {reference} within the depth tolerance; depth "
                "numbers change when a deployment is added or the depth-matching rule changes, "
                "so a depth's value is nominal_depth, never its number"
            ),
        },
    )

    # ── The depth table ────────────────────────────────────────────────
    nominal_depth = np.array(
        [
            float(np.nanmean(depth[depth_index == k]))
            if np.isfinite(depth[depth_index == k]).any()
            else np.nan
            for k in range(n_depths)
        ]
    )
    data_vars["depth_family"] = xr.Variable(
        ("N_DEPTHS",), np.array([s["family"] for s in depth_list], dtype=object)
    )
    data_vars["depth_match_value"] = xr.Variable(
        ("N_DEPTHS",),
        np.array([s["center"] for s in depth_list], dtype=np.float32),
        {"long_name": f"mean {reference} of the levels at this depth", "units": "m"},
    )
    data_vars["nominal_depth"] = xr.Variable(
        ("N_DEPTHS",),
        nominal_depth.astype(np.float32),
        {"long_name": "mean nominal depth of the levels at this depth", "units": "m"},
    )
    data_vars["depth_tolerance"] = xr.Variable(
        ("N_DEPTHS",),
        np.array([s["tolerance"] for s in depth_list], dtype=np.float32),
        {
            "long_name": f"largest {reference} distance from the depth's center that joined it",
            "comment": "half the bin size for ADCP depths",
            "units": "m",
        },
    )

    # ── The overlap block ─────────────────────────────────────────────
    data_vars["overlap_time"] = xr.Variable(
        ("N_OVERLAP",), time[ov_rows], {"long_name": "step of the overlapping value"}
    )
    data_vars["overlap_depth_index"] = xr.Variable(
        ("N_OVERLAP",),
        ov_depths.astype(np.int32),
        {"long_name": "depth index of the value"},
    )
    data_vars["overlap_level_index"] = xr.Variable(
        ("N_OVERLAP",),
        ov_levels.astype(np.int32),
        {"long_name": "index into N_LEVELS of the level the value is from"},
    )
    for v in ts_names:
        data_vars[f"overlap_{v}"] = xr.Variable(
            ("N_OVERLAP",), over[v], attrs=dict(first_of[v].attrs)
        )

    # Which deployment counts at each step: -1 in gaps; the overlap rule decides
    # where two are in the water.
    index = np.full(n_time, -1, dtype=np.int16)
    sequence = (
        range(len(stacks)) if overlap == "newer" else reversed(range(len(stacks)))
    )
    for k in sequence:
        index[starts[k] : starts[k] + lengths[k]] = k
    data_vars["deployment_index"] = xr.Variable(
        ("time",),
        index,
        {
            "long_name": "deployment whose levels count at this step (-1: none in the water)",
            "comment": (
                f"where two deployments overlap, the {overlap} one counts; a depth "
                "that deployment has no level in keeps the other's (see level_index)"
            ),
        },
    )

    # ── The DEPLOYMENT record ─────────────────────────────────────────
    def _deployment_var(
        values: list, attrs: dict | None = None, dtype: type = object
    ) -> xr.Variable:
        return xr.Variable(("DEPLOYMENT",), np.array(values, dtype=dtype), attrs or {})

    lats = [parse_coordinate(ds.attrs.get("latitude")) for ds in stacks]
    lons = [parse_coordinate(ds.attrs.get("longitude")) for ds in stacks]
    people = [attribution.read_own(ds.attrs) for ds in stacks]
    data_vars.update(
        {
            "deployment_name": _deployment_var(names),
            "deployment_stack_mask_flags": _deployment_var(own_rules),
            "deployment_time": _deployment_var(
                [_time_text(ds.attrs.get("deployment_time")) for ds in stacks]
            ),
            "recovery_time": _deployment_var(
                [_time_text(ds.attrs.get("recovery_time")) for ds in stacks]
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
        }
    )
    # Each deployment's people, as its stack names them.
    for attr in (
        *attribution.PI_ATTRS.values(),
        *attribution.CONTRIBUTOR_ATTRS.values(),
    ):
        listed = [attribution.to_attrs(p).get(attr, "") for p in people]
        if any(listed):
            data_vars[f"deployment_{attr}"] = _deployment_var(listed)
    # Where each deployment's mooring-level value came from, beside its record
    # variable (empty for a deployment that does not say).
    for field in _mooring.SOURCE_FIELDS:
        key = f"{field}_source"
        if any(ds.attrs.get(key) for ds in stacks):
            record = _SOURCE_RECORD.get(field, field)
            data_vars[f"{record}_source"] = _deployment_var(
                [str(ds.attrs.get(key) or "") for ds in stacks]
            )
    # Any other stack attribute: once when every deployment agrees, else one
    # value per deployment.
    shared: dict[str, object] = {}
    for key in sorted({k for ds in stacks for k in ds.attrs} - _OWN_ATTRS):
        values = [ds.attrs.get(key) for ds in stacks]
        if all(v is not None for v in values) and all(
            _same_value(v, values[0]) for v in values
        ):
            shared[key] = values[0]
        else:
            data_vars[f"deployment_{key}"] = _deployment_var(
                ["" if v is None else str(v) for v in values]
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
        data_vars["stack_file"] = _deployment_var(
            [f for f, _ in sources],
            {
                "long_name": "the deployment's stack file, relative to the site directory"
            },
        )
        data_vars["stack_sha256"] = _deployment_var(
            [h for _, h in sources], {"long_name": "SHA-256 of the stack file"}
        )
    # Where each deployment's data came from (its stack's source_*), always one
    # value per deployment; the joined, de-duplicated lists go in the globals.
    for key in attribution.SOURCE_ATTRS:
        values = [str(ds.attrs.get(key) or "") for ds in stacks]
        if any(values):
            data_vars[f"deployment_{key}"] = _deployment_var(values)
    # Deployments may come from different packages (DOIs differ by year); only a
    # second license draws a warning, since a user must follow each.
    joined_sources = attribution.join_sources(
        [ds.attrs for ds in stacks],
        what=f"{name}: the deployments",
        warn=("source_license",),
    )

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
    finite_lats = [v for v in lats if v is not None]
    finite_lons = [v for v in lons if v is not None]
    out.attrs.update(shared)
    out.attrs.pop("stack_mask_flag_sources", None)
    out.attrs.update(attribution.to_attrs(attribution.union(people)))
    out.attrs.update(joined_sources)
    sources = attribution.deployment_sources(
        {},
        {
            k: v.values
            for k, v in data_vars.items()
            if k.startswith("deployment_source_")
        },
    )
    out.attrs.update(
        {
            "site_name": name,
            "mooring_name": name,
            "deployments": " ".join(names),
            "n_deployments": len(stacks),
            "dt_seconds": dt,
            "depth_match": reference,
            "depth_tolerance_m": tolerance,
            "overlap_rule": overlap,
            masking.STACK_ATTR: site_rule,
            "stack_mask_flag_sources": masking.FLAG_SOURCES,
            **(
                {
                    "latitude": float(np.nanmedian(finite_lats)),
                    "longitude": float(np.nanmedian(finite_lons)),
                }
                if finite_lats and finite_lons
                else {}
            ),
            "deployment_time": _time_text(stacks[0].attrs.get("deployment_time")),
            "recovery_time": _time_text(stacks[-1].attrs.get("recovery_time")),
            "featureType": "timeSeries",
            "Conventions": "CF-1.13",
            "data_mode": "P",
            "history": "",
        }
    )
    # One dated entry per step, so the page's processing table reads them back.
    _stage.append_history(
        out,
        f"join: {len(stacks)} deployment stacks ({', '.join(names)}) on one {dt} s "
        f"time axis in {n_depths} depths, each depth empty outside its levels' "
        "deployments, nothing filled across a turnaround",
    )
    _stage.append_history(
        out,
        f"overlaps: where two deployments overlap the {overlap} one fills the depth, "
        f"the other's {ov_rows.size} depth steps are in the overlap block"
        if ov_rows.size
        else "overlaps: no deployments overlap",
    )
    _stage.append_history(
        out,
        f"flags: values flagged {masking.rule_words(masking.from_attrs(out.attrs))} "
        "by oceanarray or the originator hidden on the site pages and left out of "
        "the site grid "
        f"({masking.STACK_ATTR})",
    )
    return attribution.apply_product_citation(out, f"{name} site stack", sources)


def is_site_stack(ds: xr.Dataset) -> bool:
    """Return whether *ds* is a site stack (:func:`concat` output) rather than a deployment's."""
    return "N_DEPTHS" in ds.dims and "level_index" in ds and "deployment_index" in ds


def cell_deployment(ds: xr.Dataset) -> np.ndarray:
    """Return, on ``(time, N_DEPTHS)``, the deployment behind each value of a site stack (-1 none).

    Parameters
    ----------
    ds : xarray.Dataset
        A site stack.

    Returns
    -------
    numpy.ndarray of int
        ``deployment`` of the level ``level_index`` names, -1 where the depth is empty.
    """
    level = ds["level_index"].values.astype(np.int64)
    deployment = ds["deployment"].values.astype(np.int64)
    return np.where(level >= 0, deployment[np.clip(level, 0, None)], -1)


def depth_types(ds: xr.Dataset) -> list[str]:
    """Return each depth's instrument type: the most common among its levels.

    Parameters
    ----------
    ds : xarray.Dataset
        A site stack.

    Returns
    -------
    list of str
        One type per ``N_DEPTHS``, ``""`` for a depth with no level.
    """
    from collections import Counter

    index = ds["depth_index"].values
    types = [str(t) for t in ds["instrument_type"].values]
    out = []
    for k in range(ds.sizes["N_DEPTHS"]):
        kinds = Counter(types[i] for i in np.flatnonzero(index == k))
        out.append(kinds.most_common(1)[0][0] if kinds else "")
    return out


def own_levels(ds: xr.Dataset) -> np.ndarray:
    """Return, on ``(time, N_DEPTHS)``, where a site stack's value is from the deployment that counts.

    At each step only the levels of the deployment ``deployment_index`` names are
    kept, so a profile is never made of two moorings; a turnaround (index -1)
    keeps nothing.

    Parameters
    ----------
    ds : xarray.Dataset
        A site stack.

    Returns
    -------
    numpy.ndarray of bool
        True where the value counts.
    """
    counts = ds["deployment_index"].values.astype(np.int64)[:, None]
    return (counts >= 0) & (cell_deployment(ds) == counts)


def adcp_bins(ds: xr.Dataset) -> dict[tuple[int, str], list[int]]:
    """Return the three ADCP bins a per-depth figure of a site stack shows, by ADCP.

    For each ADCP of each deployment, from its bins below the surface with east
    velocity for at least
    :data:`~oceanarray.config.parameters.SPECTRAL_MIN_FILLED` of their own steps
    (all its bins with any, when none has that much): the bin nearest the
    instrument, the farthest, and the one midway between.

    Parameters
    ----------
    ds : xarray.Dataset
        A site stack.

    Returns
    -------
    dict of (int, str) to list of int
        ``(deployment, ADCP serial)`` → depth indices, nearest the instrument first.
    """
    if "east_velocity" not in ds:
        return {}
    serials = [str(s) for s in ds["serial"].values]
    types = ds["instrument_type"].values
    depth_index = ds["depth_index"].values
    level = ds["level_index"].values
    finite = np.isfinite(ds["east_velocity"].transpose("time", "N_DEPTHS").values)
    nominal = ds["nominal_depth"].values
    bins: dict[tuple[int, str], list[tuple[int, int, float]]] = {}
    for i, serial in enumerate(serials):
        s = int(depth_index[i])
        if _family(types[i], serial) != "adcp" or "_b" not in serial or s < 0:
            continue
        if nominal[s] <= 0:
            continue
        own = level[:, s] == i
        filled = float(finite[own, s].mean()) if own.any() else 0.0
        key = (int(ds["deployment"].values[i]), paths.adcp_parent_serial(serial))
        bins.setdefault(key, []).append((int(serial.rsplit("_b", 1)[1]), s, filled))
    out = {}
    for key, members in bins.items():
        full = [m for m in members if m[2] >= params.SPECTRAL_MIN_FILLED]
        pool = sorted(full or [m for m in members if m[2] > 0])
        if pool:
            picks = [pool[0], pool[len(pool) // 2], pool[-1]]
            out[key] = list(dict.fromkeys(s for _n, s, _f in picks))
    return out


def shown_depths(ds: xr.Dataset, *, bins: str = "all") -> np.ndarray:
    """Return which depths of a site stack a figure draws.

    An ADCP bin at or above the surface (nominal depth ≤ 0 m) is never drawn.

    Parameters
    ----------
    ds : xarray.Dataset
        A site stack.
    bins : {"all", "three"}, optional
        Every ADCP bin below the surface, or only the three :func:`adcp_bins` picks.

    Returns
    -------
    numpy.ndarray of bool
        One per ``N_DEPTHS``.
    """
    family = ds["depth_family"].values.astype(str)
    is_bin = family == "adcp"
    keep = ~(is_bin & (ds["nominal_depth"].values <= 0))
    if bins == "three":
        chosen = np.zeros_like(keep)
        for picks in adcp_bins(ds).values():
            chosen[picks] = True
        keep &= ~is_bin | chosen
    return keep


def select_depths(ds: xr.Dataset, keep: np.ndarray) -> xr.Dataset:
    """Return a site stack with only the depths *keep* marks, and their levels.

    Parameters
    ----------
    ds : xarray.Dataset
        A site stack.
    keep : numpy.ndarray of bool
        One per ``N_DEPTHS`` (:func:`shown_depths`).

    Returns
    -------
    xarray.Dataset
        The depths, their levels and their overlap values, renumbered.
    """
    keep = np.asarray(keep, bool)
    if keep.all():
        return ds
    depth_index = ds["depth_index"].values
    levels = np.flatnonzero(keep[depth_index])
    renumber = {
        "depth": np.cumsum(keep) - 1,
        "level": np.full(ds.sizes["N_LEVELS"], -1),
    }
    renumber["level"][levels] = np.arange(levels.size)
    sel = {"N_DEPTHS": np.flatnonzero(keep), "N_LEVELS": levels}
    if "overlap_depth_index" in ds:
        sel["N_OVERLAP"] = np.flatnonzero(keep[ds["overlap_depth_index"].values])
    out = ds.isel(sel)
    for name, kind in (
        ("depth_index", "depth"),
        ("overlap_depth_index", "depth"),
        ("level_index", "level"),
        ("overlap_level_index", "level"),
    ):
        if name in out:
            old = out[name].values
            new = np.where(old >= 0, renumber[kind][np.clip(old, 0, None)], -1)
            out[name] = out[name].copy(data=new.astype(old.dtype))
    return out


#: Seconds per chunk along time in the site stack file: about one year.
_CHUNK_SECONDS = 365 * 86400


def chunks(ds: xr.Dataset) -> dict[str, int]:
    """Return the site stack's chunk lengths: about one year along time, whole along the rest.

    A reader asking for one year, or one depth over the whole record, then
    decompresses about a year of the file at a time rather than all of it.

    Parameters
    ----------
    ds : xarray.Dataset
        A site stack (:func:`concat` output), with its ``dt_seconds`` attribute.

    Returns
    -------
    dict of str to int
        ``{"time": steps per chunk}``, for :func:`oceanarray.writers.write`.

    Raises
    ------
    MooringError
        *ds* has no ``dt_seconds``.
    """
    if "dt_seconds" not in ds.attrs:
        raise MooringError("the site stack has no dt_seconds attribute.")
    dt = max(int(ds.attrs["dt_seconds"]), 1)
    return {"time": max(1, min(ds.sizes["time"], _CHUNK_SECONDS // dt))}


def concat_moorings(
    descriptions: Sequence[str | Path],
    *,
    name: str,
    out_dir: str | Path | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    mask_flags: Iterable[int] | Mapping[str, Iterable[int]] | None = None,
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
    mask_flags : iterable of int, or Mapping, optional
        The site's rule, needed when the deployments' rules differ (see
        :func:`concat`).

    Returns
    -------
    Path
        The site stack's path (written unless *dry_run*).

    Raises
    ------
    MooringError
        A stack is missing, the output exists without *overwrite*, a stack was
        written by an oceanarray that did not clean people at stage 1 (rerun
        from stage 1), or :func:`concat` refuses.
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
    with contextlib.ExitStack() as opened:
        stacks, sources, cfgs = [], [], []
        for desc in descriptions:
            yaml_path = Path(desc)
            cfg = _mooring.load(yaml_path)
            stack_path = paths.product_file(yaml_path.parent, cfg["name"], "stack")
            if not stack_path.exists():
                raise MooringError(
                    f"{cfg['name']}: no stack at {stack_path}; run its stack first."
                )
            # Opened, not loaded: concat reads one variable of one stack at a time.
            stacks.append(
                opened.enter_context(xr.open_dataset(stack_path, engine="netcdf4"))
            )
            # Relative to the site folder, so the site report can link each deployment.
            rel = os.path.relpath(stack_path.resolve(), target_dir.resolve())
            sources.append((Path(rel).as_posix(), sha256(stack_path)))
            cfgs.append(cfg)
        _check_people_cleaned(stacks, [c["name"] for c in cfgs])
        ds = concat(
            stacks,
            name=name,
            sources=sources,
            descriptions=cfgs,
            mask_flags=mask_flags,
        )
    if dry_run:
        log.info(
            "would write %s (%d steps × %d depths, %d levels, %d overlap steps)",
            out_path,
            ds.sizes["time"],
            ds.sizes["N_DEPTHS"],
            ds.sizes["N_LEVELS"],
            ds.sizes["N_OVERLAP"],
        )
        return out_path
    target_dir.mkdir(parents=True, exist_ok=True)
    write(ds, out_path, overwrite=overwrite, chunks=chunks(ds))
    log.info("wrote %s", out_path)
    return out_path
