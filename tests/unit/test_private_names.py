"""Private helpers follow the naming table in CONTRIBUTING.md: no retired prefixes or suffixes."""

import ast
import pathlib
import re

import oceanarray

_ROOT = pathlib.Path(oceanarray.__file__).parent
# Retired: variant spellings and vague verbs (each replaced by a verb that says what
# comes back), and the `_fig` suffix (Figure-returners are `draw_<what>`).
_RETIRED = re.compile(
    r"^_(fmt|normalize|sanitize|get|find|load|clean|plot)_"
    r"|^_(draw|make)_\w*_fig(_b64s?)?$"
)


def _module_level_defs(tree: ast.Module):
    """Yield module functions and class methods, not nested closures."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node
        elif isinstance(node, ast.ClassDef):
            yield from (
                n
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            )


def test_no_retired_private_names():
    """No owned module defines a private helper with a retired prefix or suffix."""
    bad = []
    for path in sorted(_ROOT.rglob("*.py")):
        if {"_vendored", "legacy"} & set(path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bad += [
            f"{path.relative_to(_ROOT)}:{node.lineno} {node.name}"
            for node in _module_level_defs(tree)
            if node.name.startswith("_")
            and not node.name.startswith("__")
            and _RETIRED.search(node.name)
        ]
    assert bad == []


def test_no_public_draw_fig_names():
    """Public Figure-returners are ``draw_<what>``, without a ``_fig`` suffix."""
    bad = []
    for path in sorted(_ROOT.rglob("*.py")):
        if {"_vendored", "legacy"} & set(path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bad += [
            f"{path.relative_to(_ROOT)}:{node.lineno} {node.name}"
            for node in _module_level_defs(tree)
            if node.name.startswith("draw_") and node.name.endswith("_fig")
        ]
    assert bad == []
