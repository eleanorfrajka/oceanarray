"""Only a package's ``__init__.py`` defines ``__all__`` (developer guide, section 5)."""

import ast
from pathlib import Path

import oceanarray

_PACKAGE = Path(oceanarray.__file__).parent
# Not oceanarray's own layout: the RODB-era reference code and the shared report layer.
_SKIP = ("legacy", "reports/_vendored")


def test_no_plain_module_defines_all():
    offenders = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        rel = path.relative_to(_PACKAGE).as_posix()
        if path.name == "__init__.py" or rel.startswith(_SKIP):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else []
            if any(getattr(t, "id", None) == "__all__" for t in targets):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], (
        "a plain module defines __all__; only a package __init__.py lists its "
        f"public names: {offenders}"
    )
