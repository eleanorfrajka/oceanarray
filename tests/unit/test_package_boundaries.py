"""Package boundaries: nothing outside ``legacy`` imports it, and plotters never import reports."""

import ast
import pathlib

import oceanarray

_ROOT = pathlib.Path(oceanarray.__file__).parent


def _imports(path: pathlib.Path) -> list[str]:
    """Return the absolute module names *path* imports (relative ones resolved)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = list(path.relative_to(_ROOT.parent).with_suffix("").parts)
    if path.name != "__init__.py":
        package = package[:-1]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            mod = ".".join(base + ([node.module] if node.module else []))
            out += [mod] + [f"{mod}.{a.name}" for a in node.names]
    return out


def _owned(*, exclude: str) -> list[pathlib.Path]:
    return [
        p
        for p in _ROOT.rglob("*.py")
        if exclude not in p.relative_to(_ROOT).parts and "__pycache__" not in p.parts
    ]


def test_nothing_outside_legacy_imports_legacy():
    """``legacy`` is reference code: the package does not depend on it."""
    bad = [
        str(p.relative_to(_ROOT))
        for p in _owned(exclude="legacy")
        if any(m.startswith("oceanarray.legacy") for m in _imports(p))
    ]
    assert bad == []


def test_plotters_do_not_import_reports():
    """Figures do not depend on the pages (the shared design tokens excepted)."""
    bad = [
        f"{p.relative_to(_ROOT)}: {m}"
        for p in (_ROOT / "plotters").rglob("*.py")
        for m in _imports(p)
        if m.startswith("oceanarray.reports") and "_vendored" not in m
    ]
    assert bad == []
