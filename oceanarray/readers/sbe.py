"""Sea-Bird (SBE) raw-file helpers."""

import calendar
import re
import tempfile
from pathlib import Path


def repair_sbe_ascii(file_path: Path) -> Path:
    """Return path to file with SBE ASCII format issues fixed.

    Fixes two issues produced by some SBE software versions:
    - Dates in MM-DD-YYYY format instead of DD Mon YYYY
    - Missing space after comma when a value is negative (e.g. '1.23,-0.01')
    """
    MONTHS = {f"{i:02d}": calendar.month_abbr[i] for i in range(1, 13)}
    content = file_path.read_text()

    needs_fix = re.search(r"\b\d{2}-\d{2}-\d{4}\b", content) or re.search(
        r",(?! )", content
    )
    if not needs_fix:
        return file_path

    def _replace_date(m: re.Match) -> str:
        mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
        return f"{dd} {MONTHS[mm]} {yyyy}"

    normalized = re.sub(r"\b(\d{2})-(\d{2})-(\d{4})\b", _replace_date, content)
    normalized = re.sub(r",(?! )", ", ", normalized)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".asc", delete=False) as tmp:
        tmp.write(normalized)
        tmp_name = tmp.name
    return Path(tmp_name)
