"""Utility functions, QC constants, base64 helpers, and NC metadata readers.

Nothing here imports matplotlib — these are pure Python / numpy / xarray helpers
used by both _plots.py and _mooring.py.
"""

from __future__ import annotations

import base64
import contextlib
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from oceanarray import paths

from ..paths import safe_serial
from ..utilities import (  # noqa: F401  (re-exported)
    _nice_colorbar_bounds,
    _safe_rel,
    _status,
    should_skip_regeneration,
)
from ._vendored.page_model import Panel

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_fig_panel(
    captions: Mapping[str, str],
) -> Callable[[str, str, str | None], Panel]:
    """Return a figure-panel factory for a dict-context page, bound to *captions*.

    The mooring and instrument pages build bare-``.fig`` panels that read a
    pre-built base64 image from the dict context by *fig_key*; they differ only
    in which caption dict they consult, so this binds that dict once.  *slot*
    must match the width the figure's adapter renders at (``render_slot``): a
    half-width adapter carries ``slot="half"``; a bare ``None`` is a full-width
    ``.fig``.  (The stack page uses a dataclass context, not a dict, so it keeps
    its own builder.)
    """

    def fig_panel(pid: str, fig_key: str, slot: str | None = None) -> Panel:
        return Panel(
            pid,
            render=lambda c, _k=fig_key: c.get(_k),
            slot=slot,
            caption=captions.get(pid),
            applies_to=lambda c, _k=fig_key: bool(c.get(_k)),
        )

    return fig_panel


def _safe_serial(serial: Any) -> str:
    """Return a filename-safe serial token (see :func:`oceanarray.paths.safe_serial`)."""
    return safe_serial(serial)


def _parse_dt(s: str | None) -> datetime | None:
    """Parse clock timestamp: HH:MM:SS, YYYYMMDDTHH:MM:SS, or standard ISO."""
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00").replace(" UTC", "")
    if re.match(r"^\d{2}:\d{2}:\d{2}", s) and "T" not in s:
        s = f"2000-01-01T{s}"
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2}:\d{2}:\d{2}.*)$", s)
    if m:
        s = f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _fmt_dt(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M UTC") -> str:
    return dt.strftime(fmt) if dt else "—"


def _duration_str(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "—"
    delta = end - start
    days = delta.days
    hours, _ = divmod(delta.seconds, 3600)
    return f"{days}d {hours}h"


def _fmt_clock_str(s: str | None) -> str | None:
    """Reformat an ISO-8601 timestamp to 'YYYY-MM-DD HH:MM' (minute precision).

    Handles both compact ISO (``20260710T21:03:00``) and spaced ISO
    (``2026-07-10T21:03:00``) inputs.  Returns *None* if *s* is falsy.

    The compact date form (``YYYYMMDD``) is normalised to extended
    (``YYYY-MM-DD``) before parsing: Python 3.10's ``datetime.fromisoformat``
    rejects the compact form (3.11+ accepts it), so without this, 3.10 fell
    through to the raw ``str(s)[:16]`` fallback and rendered malformed
    timestamps (e.g. ``20260725T13:13:2``) in reports.
    """
    if not s:
        return None
    txt = str(s).replace("T", " ").strip()
    txt = re.sub(r"^(\d{4})(\d{2})(\d{2})(?=[ ]|$)", r"\1-\2-\3", txt)
    try:
        return datetime.fromisoformat(txt).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(s)[:16]


def _resolve_clock(entry: dict[str, Any]) -> dict[str, Any]:
    """Extract clock correction info from one YAML instrument entry."""
    offset_s = float(entry.get("clock_offset", 0) or 0)
    drift_s: float | None = None
    method = "none"

    comp_str = entry.get("computer_clock_at_recovery")
    inst_str = entry.get("instrument_clock_at_recovery")
    if comp_str and inst_str:
        comp = _parse_dt(str(comp_str))
        inst = _parse_dt(str(inst_str))
        if comp and inst:
            drift_s = (inst - comp).total_seconds()
            method = "Option B"
    elif entry.get("clock_drift_seconds") not in (None, 0, ""):
        drift_s = float(entry["clock_drift_seconds"])
        method = "Option A"

    return {
        "offset_s": offset_s,
        "drift_s": drift_s,
        "method": method,
        "computer_time": _fmt_clock_str(str(comp_str)) if comp_str else None,
        "instrument_time": _fmt_clock_str(str(inst_str)) if inst_str else None,
        "has_correction": offset_s != 0 or (drift_s is not None and drift_s != 0),
    }


def _check_readable(file_path: Path, file_type: str) -> tuple[bool, str]:
    """Quick format sanity check — reads a few bytes/lines, not the full file."""
    if not file_path.exists():
        return False, "file missing"
    try:
        size = file_path.stat().st_size
        if size == 0:
            return False, "empty file"

        if file_type in ("sbe-cnv", "sbe-ascii"):
            with file_path.open(errors="ignore") as f:
                head = f.read(800)
            if any(
                line.lstrip().startswith(("*", "#")) for line in head.splitlines()[:5]
            ):
                return True, "ok"
            return False, "no SeaBird header markers"

        if file_type in ("nortek-ascii", "nortek-raw"):
            hdr = file_path.with_suffix(".hdr")
            if not hdr.exists():
                candidates = list(file_path.parent.glob(file_path.stem + "*.hdr"))
                if not candidates:
                    return True, "ok (no .hdr found; may fail)"
            return True, "ok"

        if file_type == "nortek-csv-oa":
            with file_path.open(errors="ignore") as f:
                first = f.readline()
            if ";" in first:
                return True, "ok"
            return False, "expected semicolon delimiter"

        if file_type in ("rbr-rsk", "rbr-dat"):
            with file_path.open("rb") as f:
                magic = f.read(16)
            if magic[:6] == b"SQLite":
                return True, "ok"
            return False, "not SQLite format"

        return True, "ok (format not deeply checked)"

    except Exception as exc:
        return False, str(exc)[:80]


# ---------------------------------------------------------------------------
# QC colours (OceanSITES Reference Table 2)
# ---------------------------------------------------------------------------

# Canonical definitions live in plotters/helpers.py (Tier 2) so both plotters/
# and report/ can use them without a plotters→report circular import.
from ..plotters.helpers import QC_COLORS as _QC_COLORS  # noqa: E402

# Short display glosses of parameters.QC_FLAG_MEANINGS (OceanSITES table 2).
# Keep the codes and wording consistent with that table.
_QC_LABELS: dict[int, str] = {
    0: "unknown",
    1: "good",
    2: "prob. good",
    3: "corr. bad",
    4: "bad",
    7: "nominal",
    8: "interp.",
    9: "missing",
}


def _should_skip(
    output: Path,
    force: bool,
    skip_existing: bool,
    *sources: Path,
) -> bool:
    """Decide whether to skip regenerating *output* (see :func:`should_skip_regeneration`)."""
    return should_skip_regeneration(output, force, skip_existing, *sources)


def _load_pdf_b64(path: Path | None) -> str | None:
    """Return base64-encoded PDF bytes if *path* exists, else None."""
    if path is not None and path.is_file():
        return base64.b64encode(path.read_bytes()).decode("ascii")
    return None


def _resolve_diagram_pdf(proc_dir: Path, mooring_name: str) -> Path | None:
    """Locate the mooring-diagram PDF for a mooring in *proc_dir*.

    Resolution order, first match wins:

    1. The canonical ``{mooring}_diagram.pdf``.
    2. Any ``*_single.pdf`` (single-mooring output of the ``mooring_diagram``
       package, whose stem may use a short prefix, e.g. ``dsG1_single.pdf``).
    3. Any ``*_hardware.pdf`` (hardware diagram output).

    macOS AppleDouble sidecars (``._*``) are ignored.  Returns ``None`` when
    no diagram is present.
    """
    preferred = proc_dir / f"{mooring_name}_diagram.pdf"
    if preferred.is_file():
        return preferred
    for pattern in ("*_single.pdf", "*_hardware.pdf"):
        candidates = sorted(
            p for p in proc_dir.glob(pattern) if not p.name.startswith("._")
        )
        if candidates:
            return candidates[0]
    return None


# ---------------------------------------------------------------------------
# NC metadata readers
# ---------------------------------------------------------------------------


def _parse_history(history_str: str) -> list[dict[str, str]]:
    """Split a semicolon-delimited NC history attribute into timestamped entries."""
    if not history_str:
        return []
    entries = []
    for part in history_str.split("; "):
        part = part.strip()
        if not part:
            continue
        if ": " in part:
            ts, _, text = part.partition(": ")
            entries.append({"timestamp": ts.strip(), "text": text.strip()})
        else:
            entries.append({"timestamp": "", "text": part})
    return entries


#: A dated history entry's timestamp starts with an ISO date (``2026-07-29T12:10Z``);
#: a header item instead carries a key (``Reader``, ``Stages``) or the processed-by line.
_HISTORY_TS_RE = re.compile(r"^\d{4}-\d\d-\d\d")
_HISTORY_HEADER_KEYS = ("Reader", "Format", "Source file", "Stages")


def _history_tables(
    entries: list[dict[str, str]],
) -> tuple[list[tuple[str, str, str]], list[tuple[int, str, str]]]:
    """Split parsed history entries into a header key/value list and dated events.

    Returns ``(header_rows, event_rows)``.  *header_rows* are ``(label, value, kind)``
    triples for the vendored ``properties_table`` (the processed-by line, Reader, Format,
    Source file, Stages).  *event_rows* are ``(n, time, event)`` triples for a three-column
    #/Time/Event table: ``n`` is the row's 1-based position in the ``history`` attribute, so
    the original sequence survives a sort by any column (events sharing one timestamp would
    otherwise be unorderable).  A ``|``-joined dated entry becomes several rows with the same
    time, and a ``name_qc=[...]`` summary that the ``;`` split left without a time carries the
    time of the dated entry it follows, rather than an orphan row with no time.

    Parameters
    ----------
    entries : list of dict
        The ``{"timestamp", "text"}`` entries from :func:`_parse_history`.

    Returns
    -------
    tuple of (list, list)
        ``(header_rows, event_rows)``.
    """
    header_rows: list[tuple[str, str, str]] = []
    events: list[tuple[str, str]] = []
    current_time = ""
    for e in entries:
        ts = e.get("timestamp", "")
        text = e.get("text", "")
        if _HISTORY_TS_RE.match(ts):  # a dated entry
            current_time = ts
            for ev in text.split(" | "):
                events.append((current_time, ev.strip()))
        elif ts in _HISTORY_HEADER_KEYS:
            header_rows.append((ts, text, "code" if ts == "Source file" else "text"))
        elif not ts and "Processed by" in text:
            header_rows.append(("Processed", text, "text"))
        elif not ts:  # an orphan continuation (a qc summary) -> carry the current time
            events.append((current_time, text.strip()))
        else:  # any other "key: value" header item
            header_rows.append((ts, text, "text"))
    event_rows = [(n, t, ev) for n, (t, ev) in enumerate(events, start=1)]
    return header_rows, event_rows


def _read_nc_metadata(nc_path: Path) -> dict[str, Any]:
    """Return the vendored-builder inventory rows/groups for a NetCDF file.

    Opens the dataset and runs the vendored :mod:`oceanarray.reports._vendored.nc_tables`
    builders — coordinates, dimensioned variables grouped by dimension set, 0-d
    scalars, grouped global attributes, and the summary counts — plus oceanarray's
    own ``analog_vars`` list (analog channels carrying non-zero data).  Each builder
    returns ``(rows_or_groups, source)``; the source is the caption shown under the
    table when ``show_source`` is on.  QC companions and sensor variables are
    classified by :func:`oceanarray.reports.design.is_qc` / ``is_sensor``, and
    oceanarray's own global-attribute names are merged into the canonical groups via
    :data:`~oceanarray.reports.design.ATTR_GROUP_EXTRAS`.  ``SENSOR_*`` variables stay
    in the scalar table.  A malformed file returns the same keys empty plus ``error``.
    """
    import xarray as xr

    from . import design
    from ._vendored import nc_tables

    try:
        with xr.open_dataset(nc_path, engine="netcdf4", decode_timedelta=False) as ds:
            coords_rows, coords_src = nc_tables.coords_rows(ds)
            var_groups, var_src = nc_tables.variables_rows(
                ds, is_qc=design.is_qc, is_sensor=design.is_sensor
            )
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
    """Return the QC thresholds actually applied, as stored in the stage3 NC file.

    Reads four types of threshold from the ``{var}_qc`` variable attrs and from
    dataset-level attrs:

    * ``qc_gross_range_*`` — gross-range fail/suspect min/max
    * ``qc_spike_*`` — QARTOD spike-test suspect/fail thresholds
    * ``qc_flat_line_*`` — QARTOD flat-line (stuck-value) suspect/fail sample counts
    * ``tilt_suspect_threshold`` / ``tilt_fail_threshold`` — Aquadopp tilt QC

    Returns one dict per (variable, test) combination, with the actual values
    stored in the file — regardless of whether they came from package defaults,
    mooring-level YAML, or per-instrument overrides.  Spike and flat-line rows only
    appear for stage3 files produced after 2026-07-24.
    """
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
    except Exception:
        return []


def _read_qc_summary(nc_path: Path) -> list[dict[str, Any]]:
    """Return per-variable QC flag breakdown from a stage3 NC file."""
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
            flags = ds[v].values.astype(int).ravel()
            total = len(flags)
            if total == 0:
                continue
            flag_rows = []
            for fval in (1, 2, 3, 4, 8, 9):
                n = int(np.sum(flags == fval))
                flag_rows.append(
                    {
                        "flag": fval,
                        "label": _QC_LABELS.get(fval, str(fval)),
                        "color": _QC_COLORS.get(fval, "#999"),
                        "n": n,
                        "pct": round(100.0 * n / total, 1),
                    }
                )
            rows.append({"var": base, "total": total, "flags": flag_rows})
        ds.close()
        return rows
    except Exception:
        return []


# Fixed 5 primary variables always shown in the instrument summary table
_PRIMARY_VARS = [
    ("temperature", "T"),
    ("conductivity", "C"),
    ("pressure", "P"),
    ("east_velocity", "U"),
    ("north_velocity", "V"),
]


def _read_instrument_info(
    proc_dir: Path, instr_type: str, mooring: str, serial: str
) -> dict[str, Any]:
    """Read record statistics from the best available processed NetCDF file.

    Prefers stage-3 output (QC and coordinate-transformed); falls back to
    stage-2 (deployment-trimmed, clock-corrected) if stage-3 is absent.
    Stage-1 (raw) files are never read here.

    Returns a dict with keys:

    * ``t_start`` / ``t_end``: first and last timestamp (ISO-8601 string,
      truncated to the minute).
    * ``t_end_raw``: last timestamp as ``numpy.datetime64`` (for arithmetic).
    * ``n_records``: total number of time steps.
    * ``dt_s``: 90th-percentile inter-sample interval in seconds (robust
      against occasional data gaps).
    * ``shorthands``: list of ``(label, bool)`` pairs indicating which of
      T / C / P / U / V variables are present.
    * ``pressure_interpolated``: True if any ``pressure_qc == 8``, meaning
      pressure was filled by interpolation from neighbouring instruments.
    * ``p_min`` / ``p_max``: minimum and maximum pressure (dbar) over the
      trimmed record, excluding non-positive values (stuck-at-zero sensors,
      pre-deployment bench readings) and QC-bad/missing samples where stage-3
      flags are available.  ``None`` if no valid pressure data exist.

    Returns ``{}`` if no processed file is found, or ``{"error": str}`` if
    the file cannot be opened.
    """
    base = proc_dir / instr_type / f"{mooring}_{serial}"
    nc_path = None
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

        if n > 1:
            dt_arr = np.diff(time.astype("datetime64[s]").astype(float))
            dt_s = float(np.percentile(dt_arr, 90))
        else:
            dt_s = float("nan")

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
        # non-physical for a deployed mooring) and QC-bad/missing flag values.
        p_min: float | None = None
        p_max: float | None = None
        if "pressure" in ds.data_vars:
            try:
                pvals = ds["pressure"].values.astype(float)
                # Mask QC=4 (bad) and QC=9 (missing) if stage3 QC is available
                if "pressure_qc" in ds.data_vars:
                    qc = ds["pressure_qc"].values.astype(int)
                    pvals = np.where(np.isin(qc, [4, 9]), np.nan, pvals)
                # Exclude non-positive values (pre-deployment bench / stuck sensor)
                pvals = np.where(pvals > 0, pvals, np.nan)
                _pmin = float(np.nanmin(pvals))
                _pmax = float(np.nanmax(pvals))
                if np.isfinite(_pmin):
                    p_min = _pmin
                if np.isfinite(_pmax):
                    p_max = _pmax
            except Exception:
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
    """Read deployment-timing attrs from the stage 2 NC file.

    Always reads from stage 2 (not stage 3) because timing metadata is written
    during stage 2 processing.  Returns an empty dict if no stage 2 file exists.

    The dict keys are:

    stage1_start / stage1_end
        First and last timestamps in the stage 1 record (raw instrument clock).
    stage1_nonnull_start / stage1_nonnull_end
        First/last non-NaN timestamps in the priority variable (raw clock).
    sugg_start / sugg_end
        Auto-detected deployment window in the raw instrument clock.
    sugg_start_utc / sugg_end_utc
        Clock-corrected suggested times (safe to paste into the YAML).
    stage2_start / stage2_end
        Actual first/last record in the stage 2 file (corrected, trimmed).
    sugg_start_differs / sugg_end_differs
        True when the suggested time differs from the YAML time by more than
        2 × Δt (two sample intervals) — amber-highlight trigger in the timing
        table.
    dt_s
        Median sample interval in seconds (p90 of first-differences).
    """
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

        if n > 1:
            dt_arr = np.diff(time.astype("datetime64[s]").astype(float))
            dt_s = float(np.percentile(dt_arr, 90))
        else:
            dt_s = float("nan")

        stage1_start = ds.attrs.get("stage1_time_start")
        stage1_end = ds.attrs.get("stage1_time_end")
        stage1_nonnull_start = ds.attrs.get("stage1_first_nonnull")
        stage1_nonnull_end = ds.attrs.get("stage1_last_nonnull")
        sugg_start = ds.attrs.get("suggested_deployment_time")
        sugg_end = ds.attrs.get("suggested_recovery_time")
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
            except Exception:
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
            except Exception:
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
            except Exception:
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
    except Exception:
        return {}


def _read_sensor_info(
    proc_dir: Path, instr_type: str, mooring: str, serial: str
) -> list[dict[str, Any]]:
    """Return one dict per SENSOR_* variable found in the stage2 NC file."""
    nc_path = proc_dir / instr_type / f"{mooring}_{serial}{paths.stage_suffix(2)}"
    if not nc_path.exists():
        return []
    try:
        import xarray as xr

        ds = xr.open_dataset(nc_path, decode_timedelta=False)
        rows = []
        for vname in sorted(v for v in ds.data_vars if v.startswith("SENSOR_")):
            attrs = dict(ds[vname].attrs)
            stype = attrs.get("sensor_type", "")
            coeff_key = f"{stype}_calibration_coefficients"
            rows.append(
                {
                    "var_name": vname,
                    "sensor_type": stype,
                    "sensor_model": attrs.get("sensor_model", "—"),
                    "sensor_serial": attrs.get("sensor_serial_number", "—"),
                    "cal_date": attrs.get("sensor_calibration_date", "—"),
                    "coefficients": attrs.get(coeff_key, ""),
                }
            )
        ds.close()
        return rows
    except Exception:
        return []


def _stage_files(
    proc_dir: Path, instr_type: str, mooring: str, serial: str
) -> dict[str, bool]:
    base = proc_dir / instr_type / f"{mooring}_{serial}"
    return {
        "stage1": Path(str(base) + paths.stage_suffix(1)).exists(),
        "stage2": Path(str(base) + paths.stage_suffix(2)).exists(),
        "stage3": Path(str(base) + paths.stage_suffix(3)).exists(),
    }


def _fmt_size(n_bytes: int) -> str:
    """Return a human-readable file size string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.0f} {unit}"
        n_bytes /= 1024  # type: ignore[assignment]
    return f"{n_bytes:.1f} GB"


def _file_info(path: Path) -> dict[str, str]:
    """Return size and mtime for a file path, or empty strings if it doesn't exist."""
    if not path.exists():
        return {"exists": False, "name": path.name, "size": "", "mtime": ""}
    import datetime as _dt

    st = path.stat()
    mtime = _dt.datetime.fromtimestamp(st.st_mtime, tz=_dt.UTC)
    return {
        "exists": True,
        "name": path.name,
        "size": _fmt_size(st.st_size),
        "mtime": mtime.strftime("%Y-%m-%d %H:%M UTC"),
    }


def _stage_file_details(
    proc_dir: Path, instr_type: str, mooring: str, serial: str
) -> list:
    """Return per-stage file info dicts for template rendering."""
    base = proc_dir / instr_type / f"{mooring}_{serial}"
    rows = []
    for stage in ("stage1", "stage2", "stage3"):
        p = Path(str(base) + f"_{stage}.nc")
        info = _file_info(p)
        info["label"] = stage.capitalize()
        rows.append(info)
    return rows


def _instrument_report_href(
    mooring_name: str,
    serial: str,
    in_instrument_subdir: bool = False,
) -> str:
    """Return the relative href for a per-instrument HTML report.

    Instrument reports live at ``report/instrument/{mooring}_{serial}_report.html``.
    The prefix differs depending on which report is linking to them:

    - Summary / Stack / Grid (in ``report/``): prefix is ``instrument/``
    - Another instrument report (in ``report/instrument/``): no prefix needed

    Parameters
    ----------
    mooring_name : str
        Mooring identifier used in the report filename.
    serial : str
        Instrument serial number.
    in_instrument_subdir : bool
        ``True`` when linking from a per-instrument report.
        ``False`` (default) when linking from summary, stack, or grid reports.

    Returns
    -------
    str
        Relative href string, e.g. ``"instrument/dsG3_1_2026_26261_report.html"``.

    """
    import html as _html

    prefix = "" if in_instrument_subdir else "instrument/"
    return (
        f"{prefix}{_html.escape(mooring_name)}_{_html.escape(str(serial))}_report.html"
    )


def _instrument_report_exists(out_dir: Path, mooring_name: str, serial: str) -> bool:
    """Return True if the per-instrument report file already exists on disk.

    Parameters
    ----------
    out_dir : Path
        The report output directory (i.e. ``proc_dir / "report"``).
    mooring_name : str
        Mooring identifier used in the report filename.
    serial : str
        Instrument serial number.

    """
    return (out_dir / "instrument" / f"{mooring_name}_{serial}_report.html").exists()


def _find_array_report_href(
    out_dir: Path, in_instrument_subdir: bool = False
) -> str | None:
    """Return a relative href to the array report if one exists in the top-level proc dir.

    Parameters
    ----------
    out_dir : Path
        Directory where the current report will be written.  Expected to be
        ``{proc_dir}/{mooring}/report/`` (default) or ``{proc_dir}/{mooring}/``.
    in_instrument_subdir : bool
        True when the caller is a per-instrument report one level deeper.

    Returns
    -------
    str or None
        Relative href string (e.g. ``"../../dsG3_2026_array_report.html"``),
        or None if no ``*_array_report.html`` exists.

    """
    if out_dir is None:
        return None
    # Navigate up to the top-level proc dir (above the mooring dir)
    candidate = out_dir
    # go up past report/ (if present) and mooring dir
    for _ in range(3 if in_instrument_subdir else 2):
        candidate = candidate.parent
    matches = sorted(
        p for p in candidate.glob("*_array_report.html") if not p.name.startswith(".")
    )
    if not matches:
        return None
    depth = 3 if in_instrument_subdir else 2
    prefix = "../" * depth
    return f"{prefix}{matches[0].name}"
