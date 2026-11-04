"""Guard: mooring-config loading lives in one place.

``yaml.safe_load`` on a mooring config may appear only in a small allowed set of
modules. Every stage and report receives the loaded dict from
:func:`oceanarray.mooring.load` instead of re-reading the file, so a new
``safe_load`` site in a stage is a regression this test catches.

All mooring-config loading goes through :func:`oceanarray.mooring.load` /
``mooring._parse``; the stages, reports and CLI receive the dict. A new
``safe_load`` on a mooring config in any other module is a regression.
"""

from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "oceanarray"

# Files allowed to call ``yaml.safe_load`` at the end of the branch:
#   mooring.py            — the one mooring-config loader
#   logger.py             — loads config/logging.yaml, not a mooring config
#   reports/_array.py     — array-level config (no array loader planned; stays)
#   reports/_recovery_table.py — recovery-table config
#   pipeline/export.py    — array-level config (site_code, project), not a mooring config
# Everything under legacy/ is excluded wholesale (vendored/managed upstream).
ALLOWED = {
    "mooring.py",
    "logger.py",
    "reports/_array.py",
    "reports/_recovery_table.py",
    "pipeline/export.py",
}


def _safe_load_sites() -> set[str]:
    """Return package-relative paths of .py files that call ``yaml.safe_load``."""
    sites: set[str] = set()
    for path in PKG.rglob("*.py"):
        rel = path.relative_to(PKG)
        if rel.parts and rel.parts[0] == "legacy":
            continue
        if "safe_load" in path.read_text(encoding="utf-8"):
            sites.add(rel.as_posix())
    return sites


def test_mooring_config_loaded_in_one_place() -> None:
    """No module outside the allowed set calls ``yaml.safe_load``."""
    stray = _safe_load_sites() - ALLOWED
    assert not stray, f"unexpected yaml.safe_load sites: {sorted(stray)}"
