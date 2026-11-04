"""Guard: notebooks and docs must not reference removed package paths.

The ``processors`` → ``pipeline`` rename and the ``tools.readers`` removal were
swept through ``.py`` files, but the sed missed ``.ipynb`` and some ``.rst``/``.md``
prose — which only surfaced when the notebook-build CI job executed the demo and
hit ``ModuleNotFoundError: oceanarray.processors``. This test greps the
user-facing docs and notebooks for every package path removed in 0.6.0.

The CHANGELOG and the migration guide are excluded: they document the old → new
mapping and must name the removed paths.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: Dotted module paths removed in 0.6.0; none may appear in executable notebooks
#: or user-facing docs (outside the old→new mapping files).
_REMOVED_PATHS = (
    "oceanarray.processors",
    "oceanarray.tools",
    "oceanarray.utilities",
    "oceanarray.pipeline.helpers",
    "oceanarray.pipeline.caldip",
    "oceanarray.analysis.clock",
    "oceanarray.analysis.temporal",
    "oceanarray.analysis.science",
    "oceanarray.analysis.hydrographic",
    "oceanarray.legacy.find_deployment",
)

#: Files that legitimately name the removed paths (the old→new mapping).
_ALLOWED = {"CHANGELOG.md", "migration.rst"}


def _doc_and_notebook_files() -> list[Path]:
    """Return the notebook and doc-source files to scan."""
    files: list[Path] = []
    files += (_ROOT / "notebooks").rglob("*.ipynb")
    docs = _ROOT / "docs" / "source"
    if docs.is_dir():
        files += docs.rglob("*.rst")
        files += docs.rglob("*.md")
    files += [_ROOT / "README.md", _ROOT / "CONTRIBUTING.md"]
    return [f for f in files if f.is_file() and f.name not in _ALLOWED]


@pytest.mark.parametrize("path", _doc_and_notebook_files(), ids=lambda p: p.name)
def test_no_removed_package_path(path: Path) -> None:
    """No notebook or doc names a module path removed in 0.6.0."""
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = [p for p in _REMOVED_PATHS if p in text]
    assert not hits, f"{path.relative_to(_ROOT)} references removed path(s) {hits}"
