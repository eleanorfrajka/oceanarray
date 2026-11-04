"""Per-mooring cruise-report recovery table.

Generates a standalone, print-optimised HTML file
``{mooring}_recovery_table.html`` containing the instrument recovery table
used in cruise reports.  The table mirrors the format shown in BAS/RAPID-style
cruise reports (height above bottom, nominal depth, instrument, parameters,
sample interval, raw start/stop times, clock drift, first/last good record,
and any per-instrument comments).

Entry point: :func:`generate_recovery_table`.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from typing import Any

import yaml

from oceanarray import logger, paths
from oceanarray.instruments import instrument_entries

from ..paths import safe_serial
from ._env import render_template
from ._html_helpers import (
    _parse_dt,
    _resolve_clock,
)

# ---------------------------------------------------------------------------
# Instrument type → parameter abbreviations
# ---------------------------------------------------------------------------

_TYPE_PARAMS: dict[str, str] = {
    "microcat": "T, C, P",
    "aquadopp": "U, V, W, T, P",
    "rbrsolo": "T",
    "rbrsoloT": "T",
    "tr1050": "T",
    "seapoint": "Turb.",
    "adcp": "U, V, W, T, P",
    "ADCP": "U, V, W, T, P",
}

_NON_LOGGING = {"beacon", "release", "float", "swivel", "shackle"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_drift(offset_s: float | None) -> str:
    """Format a clock offset in seconds as ±HH:MM:SS."""
    if offset_s is None or offset_s == 0:
        return "+00:00:00"
    sign = "+" if offset_s >= 0 else "-"
    abs_s = abs(int(round(offset_s)))
    h = abs_s // 3600
    m = (abs_s % 3600) // 60
    s = abs_s % 60
    return f"{sign}{h:02d}:{m:02d}:{s:02d}"


def _read_stage1_times(
    proc_dir: Path, instr_type: str, mooring: str, serial: str
) -> tuple:
    """Return (t_start_str, t_end_str) from the stage-1 NC, or ('—', '—')."""
    stage1_path = proc_dir / instr_type / f"{mooring}_{serial}{paths.stage_suffix(1)}"
    if not stage1_path.exists():
        return "—", "—"
    try:
        import xarray as xr

        with xr.open_dataset(stage1_path, decode_timedelta=False) as ds:
            time = ds["time"].values
        if len(time) == 0:
            return "—", "—"
        t0 = str(time[0])[:16].replace("T", " ")
        t1 = str(time[-1])[:16].replace("T", " ")
    except Exception:  # noqa: BLE001
        return "—", "—"
    else:
        return t0, t1


def _read_stage2_times(
    proc_dir: Path, instr_type: str, mooring: str, serial: str
) -> tuple:
    """Return (t_start_str, t_end_str) from the stage-2 NC, or ('—', '—')."""
    stage2_path = proc_dir / instr_type / f"{mooring}_{serial}{paths.stage_suffix(2)}"
    if not stage2_path.exists():
        return "—", "—"
    try:
        import xarray as xr

        with xr.open_dataset(stage2_path, decode_timedelta=False) as ds:
            time = ds["time"].values
        if len(time) == 0:
            return "—", "—"
        t0 = str(time[0])[:16].replace("T", " ")
        t1 = str(time[-1])[:16].replace("T", " ")
    except Exception:  # noqa: BLE001
        return "—", "—"
    else:
        return t0, t1


def _instrument_label(instr_type: str, serial: str) -> str:
    """Return a human-readable instrument label, e.g. 'SBE37 26261'."""
    _display = {
        "microcat": "SBE37",
        "aquadopp": "Aquadopp",
        "rbrsolo": "RBRsoloT",
        "rbrsoloT": "RBRsoloT",
        "tr1050": "RBR TR-1050",
        "seapoint": "Seapoint",
        "adcp": "ADCP",
        "ADCP": "ADCP",
    }
    label = _display.get(instr_type, instr_type)
    return f"{label} {serial}" if serial else label


def _format_interval(seconds: Any) -> str:
    """Return sample_interval_seconds as a plain integer string."""
    if seconds is None:
        return ""
    try:
        return str(int(float(seconds)))
    except (TypeError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------


def _build_rows(
    proc_dir: Path,
    mooring_name: str,
    cfg: dict[str, Any],
) -> tuple:
    """Build ``(rows, comments)`` for the recovery table and its Notes section."""
    waterdepth = cfg.get("waterdepth")
    entries = instrument_entries(cfg)

    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        instr_type = entry.get("instrument", "")
        hab_raw = entry.get("hab") or entry.get("hab_bottom") or entry.get("hab_top")
        try:
            hab = float(hab_raw) if hab_raw is not None else None
        except (TypeError, ValueError):
            hab = None

        depth = (
            round(float(waterdepth) - hab)
            if (waterdepth is not None and hab is not None)
            else None
        )

        # Non-logging entries (beacons, releases, etc.)
        if instr_type.lower() in _NON_LOGGING:
            desc = entry.get("description") or instr_type
            comment = entry.get("comment", "")
            full = desc + (f"  [{comment}]" if comment else "")
            rows.append(
                {
                    "hab": int(hab) if hab is not None else None,
                    "depth": depth,
                    "nonlog": True,
                    "description": full,
                    "_hab_sort": hab if hab is not None else -1,
                }
            )
            continue

        serial = safe_serial(entry.get("serial", ""))
        skipped = bool(entry.get("skip"))
        comment = entry.get("comment", "") or ""

        # Stage 1: raw start/stop (instrument clock, uncorrected)
        if skipped:
            s1_start, s1_end = "—", "—"
            s2_start, s2_end = "—", "—"
        else:
            s1_start, s1_end = _read_stage1_times(
                proc_dir, instr_type, mooring_name, serial
            )
            s2_start, s2_end = _read_stage2_times(
                proc_dir, instr_type, mooring_name, serial
            )

        clock = _resolve_clock(entry)
        # Prefer the computed drift (Option B) over the simple offset
        drift_s = clock.get("drift_s")
        if drift_s is None:
            drift_s = clock.get("offset_s")
        drift_str = _format_drift(drift_s)

        start_stop = f"{s1_start}\n{s1_end}" if s1_start != "—" else "—"
        first_last = f"{s2_start}\n{s2_end}" if s2_start != "—" else "—"
        if skipped:
            first_last = entry.get("skip_reason") or "skipped"

        rows.append(
            {
                "hab": int(hab) if hab is not None else None,
                "depth": depth,
                "nonlog": False,
                "instrument": _instrument_label(instr_type, serial),
                "params": _TYPE_PARAMS.get(instr_type, ""),
                "interval_s": _format_interval(entry.get("sample_interval_seconds")),
                "start_stop": start_stop,
                "clock_drift": drift_str,
                "first_last": first_last,
                "comment": comment,
                "_hab_sort": hab if hab is not None else -1,
                "_serial": serial,
                "_instr_type": instr_type,
                "_depth": depth,
            }
        )

    # Sort descending by HAB (top of mooring first)
    rows.sort(key=lambda r: r["_hab_sort"], reverse=True)

    # Build notes list for instruments that have a comment
    comments = [
        {
            "serial": r["_serial"],
            "instrument": r["instrument"],
            "depth_str": f"{r['_depth']} m" if r["_depth"] is not None else "?",
            "comment": r["comment"],
        }
        for r in rows
        if not r.get("nonlog") and r.get("comment")
    ]

    return rows, comments


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_recovery_table(
    mooring_name: str,
    proc_dir: Path,
    yaml_path: Path | None = None,
    out_path: Path | None = None,
    force: bool = False,
) -> Path | None:
    """Generate a standalone HTML cruise-report recovery table.

    Parameters
    ----------
    mooring_name : str
        Mooring identifier, e.g. ``"dsG1_1_2026"``.
    proc_dir : Path
        Mooring-level processing directory (contains the mooring YAML and
        processed NetCDF subdirectories).
    yaml_path : Path, optional
        Path to the mooring YAML.  Defaults to
        ``proc_dir / "{mooring_name}.mooring.yaml"``.
    out_path : Path, optional
        Output HTML path.  Defaults to
        ``proc_dir / "{mooring_name}_recovery_table.html"``.
    force : bool
        Overwrite existing output file.

    Returns
    -------
    Path or None
        Path to the written HTML file, or ``None`` on failure.

    """
    from datetime import datetime

    from oceanarray.location import (
        format_latlon,
        is_unknown_latlon_source,
        parse_latlon_with_source,
    )

    from ._html_helpers import _format_duration

    proc_dir = Path(proc_dir)

    if yaml_path is None:
        yaml_path = proc_dir / f"{mooring_name}.mooring.yaml"
    if out_path is None:
        out_path = proc_dir / f"{mooring_name}_recovery_table.html"

    if out_path.exists() and not force:
        logger.status("skip", str(out_path))
        return out_path

    if not yaml_path.exists():
        print(f"ERROR: YAML not found: {yaml_path}")
        return None

    with yaml_path.open() as fh:
        cfg = yaml.safe_load(fh) or {}

    deploy_dt = _parse_dt(cfg.get("deployment_time"))
    recover_dt = _parse_dt(cfg.get("recovery_time"))

    # Latitude/longitude for the canonical meta fields, if available.  Route through
    # the shared parser (DMS-aware, pairs lat/lon from one key set, skips the (0, 0)
    # placeholder) then format with the hemisphere derived from the sign — never a
    # hardcoded suffix, which produced the malformed "-27.8 W" for signed inputs.
    lat_v, lon_v, source = parse_latlon_with_source(cfg)
    if is_unknown_latlon_source(source):
        latitude = longitude = ""
    else:
        latitude, longitude = format_latlon(lat_v, lon_v)

    rows, comments = _build_rows(proc_dir, mooring_name, cfg)

    ctx = {
        "mooring_name": mooring_name,
        "year": cfg.get("year", ""),
        "waterdepth": cfg.get("waterdepth", "?"),
        "latitude": latitude,
        "longitude": longitude,
        "deploy_time": deploy_dt.strftime("%Y-%m-%d %H:%M UTC") if deploy_dt else "?",
        "recover_time": recover_dt.strftime("%Y-%m-%d %H:%M UTC")
        if recover_dt
        else "?",
        "duration": _format_duration(deploy_dt, recover_dt),
        "rows": rows,
        "comments": comments,
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }

    html = render_template("recovery_table.html", **ctx)
    out_path.write_text(html, encoding="utf-8")
    logger.status("file", str(out_path))
    return out_path
