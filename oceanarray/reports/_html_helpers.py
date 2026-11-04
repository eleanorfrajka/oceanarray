"""Utility functions, QC constants, base64 helpers, and NC metadata readers.

Nothing here imports matplotlib — these are pure Python / numpy / xarray helpers
used by both _plots.py and _mooring.py.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from oceanarray import paths

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


def _format_dt(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M UTC") -> str:
    return dt.strftime(fmt) if dt else "—"


def _format_duration(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "—"
    delta = end - start
    days = delta.days
    hours, _ = divmod(delta.seconds, 3600)
    return f"{days}d {hours}h"


def _format_clock(s: str | None) -> str | None:
    """Reformat an ISO-8601 timestamp to 'YYYY-MM-DD HH:MM' (minute precision)."""
    if not s:
        return None
    txt = str(s).replace("T", " ").strip()
    # Normalize compact YYYYMMDD to YYYY-MM-DD: Python 3.10's fromisoformat rejects
    # the compact form, and the [:16] fallback then rendered malformed timestamps.
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
        "computer_time": _format_clock(str(comp_str)) if comp_str else None,
        "instrument_time": _format_clock(str(inst_str)) if inst_str else None,
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


def _read_pdf_b64(path: Path | None) -> str | None:
    """Return base64-encoded PDF bytes if *path* exists, else None."""
    if path is not None and path.is_file():
        return base64.b64encode(path.read_bytes()).decode("ascii")
    return None


def _resolve_diagram_pdf(proc_dir: Path, mooring_name: str) -> Path | None:
    """Locate the mooring-diagram PDF for a mooring in *proc_dir*, or None."""
    # First match wins: canonical {mooring}_diagram.pdf, then *_single.pdf
    # (mooring_diagram package output, whose stem may be a short prefix such as
    # dsG1_single.pdf), then *_hardware.pdf.
    preferred = proc_dir / f"{mooring_name}_diagram.pdf"
    if preferred.is_file():
        return preferred
    for pattern in ("*_single.pdf", "*_hardware.pdf"):
        # Skip macOS AppleDouble sidecars (._*).
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


def _build_history_tables(
    entries: list[dict[str, str]],
) -> tuple[list[tuple[str, str, str]], list[tuple[int, str, str]]]:
    """Split parsed history entries into header ``(label, value, kind)`` and event rows."""
    header_rows: list[tuple[str, str, str]] = []
    events: list[tuple[str, str]] = []
    current_time = ""
    for e in entries:
        ts = e.get("timestamp", "")
        text = e.get("text", "")
        if _HISTORY_TS_RE.match(ts):  # a dated entry
            current_time = ts
            # A |-joined dated entry becomes several rows sharing one time.
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
    # n is the 1-based position in `history`, so the original order survives a sort
    # by any column (events sharing one timestamp would otherwise be unorderable).
    event_rows = [(n, t, ev) for n, (t, ev) in enumerate(events, start=1)]
    return header_rows, event_rows


# Fixed 5 primary variables always shown in the instrument summary table
def _stage_files(
    proc_dir: Path, instr_type: str, mooring: str, serial: str
) -> dict[str, bool]:
    base = proc_dir / instr_type / f"{mooring}_{serial}"
    return {
        "stage1": Path(str(base) + paths.stage_suffix(1)).exists(),
        "stage2": Path(str(base) + paths.stage_suffix(2)).exists(),
        "stage3": Path(str(base) + paths.stage_suffix(3)).exists(),
    }


def _format_size(n_bytes: int) -> str:
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
        "size": _format_size(st.st_size),
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
    """Return the relative href for a per-instrument HTML report."""
    import html as _html

    # Instrument reports live in report/instrument/: pages in report/ need the
    # instrument/ prefix, sibling instrument pages need none.
    prefix = "" if in_instrument_subdir else "instrument/"
    return (
        f"{prefix}{_html.escape(mooring_name)}_{_html.escape(str(serial))}_report.html"
    )


def _instrument_report_exists(out_dir: Path, mooring_name: str, serial: str) -> bool:
    """Return True if the per-instrument report file already exists on disk."""
    return (out_dir / "instrument" / f"{mooring_name}_{serial}_report.html").exists()


def _resolve_array_report_href(
    out_dir: Path, in_instrument_subdir: bool = False
) -> str | None:
    """Return a relative href to the top-level array report, or None if absent."""
    # out_dir is expected to be {proc_dir}/{mooring}/report/; a per-instrument
    # report sits one level deeper.
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
