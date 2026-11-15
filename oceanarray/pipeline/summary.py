"""Collect one mooring run's warnings into a summary (console list + JSON file).

A :class:`RunSummary` is a logging handler attached to the ``oceanarray`` logger for
the length of a run. It counts each distinct WARNING-and-above message so the run can
end with a deduplicated "check these" list — each warning once, with how many times it
fired — and write the same as ``processing_logs/<name>_summary.json`` so a multi-mooring
script can combine runs later without parsing log text.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)


class RunSummary(logging.Handler):
    """Buffer the WARNING-and-above records emitted during one mooring's run.

    Attach to the ``oceanarray`` logger with :meth:`logging.Logger.addHandler` for
    the length of the run, then :meth:`finish` to log the deduplicated list and write
    the JSON. Each distinct ``(level, message)`` is counted, so an identical warning
    repeated (e.g. the same config gap across instruments) is listed once with its
    count; a warning that embeds an instrument serial stays a distinct entry.
    """

    def __init__(self) -> None:
        """Start with no records; handle WARNING and above."""
        super().__init__(level=logging.WARNING)
        self._counts: Counter[tuple[str, str]] = Counter()

    def emit(self, record: logging.LogRecord) -> None:
        """Count *record* by its level name and formatted message."""
        self._counts[(record.levelname, record.getMessage())] += 1

    @property
    def total(self) -> int:
        """The total number of WARNING-and-above records seen (with repeats)."""
        return sum(self._counts.values())

    def entries(self) -> list[dict]:
        """Return the distinct records as dicts, most frequent first."""
        return [
            {"level": level, "message": message, "count": count}
            for (level, message), count in self._counts.most_common()
        ]

    def log_check_these(self, name: str) -> None:
        """Log the deduplicated "check these" list for mooring *name*."""
        entries = self.entries()
        if not entries:
            log.info("Run summary %s: no warnings.", name)
            return
        # The heading is WARNING so it survives ``-q`` (and points at the JSON);
        # the per-warning detail is INFO (the warnings themselves already showed).
        log.warning("Run summary %s: %d warning(s) to check:", name, self.total)
        for entry in entries:
            times = f" (x{entry['count']})" if entry["count"] > 1 else ""
            log.info("  %s: %s%s", entry["level"], entry["message"], times)

    def write_json(self, path: Path, name: str) -> None:
        """Write the summary for mooring *name* as JSON to *path*."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "mooring": name,
                    "n_warnings": self.total,
                    "warnings": self.entries(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def finish(self, name: str, mooring_dir: Path) -> Path:
        """Log the check-these list and write ``<name>_summary.json``; return its path.

        Parameters
        ----------
        name : str
            The mooring name.
        mooring_dir : pathlib.Path
            The mooring's processing directory; the JSON goes under its
            ``processing_logs/``.

        Returns
        -------
        pathlib.Path
            The JSON summary file written.
        """
        self.log_check_these(name)
        path = Path(mooring_dir) / "processing_logs" / f"{name}_summary.json"
        self.write_json(path, name)
        return path
