"""Package code imports every moved module from its new home, never its old path.

Each ``oceanarray`` module is imported afresh with ``DeprecationWarning`` raised as
an error, so a module that still imports a deprecated alias (``oceanarray.instruments``
and the like) fails here instead of warning a user.
"""

import ast
import importlib
import pkgutil
import sys
import warnings
from pathlib import Path

import pytest

import oceanarray

_PACKAGE = Path(oceanarray.__file__).parent

# The deprecated aliases themselves: importing one (or a name from it) warns by design.
DEPRECATED_ALIASES = (
    "oceanarray.instruments",
    "oceanarray.location",
    "oceanarray.config.validation",
    "oceanarray.pipeline.clock",
)
# Shipped reference code that nothing imports (developer guide, section 2).
_SKIP_PREFIX = "oceanarray.legacy"


def _module_names() -> list[str]:
    """Return every importable ``oceanarray`` module except the aliases and legacy code."""
    with warnings.catch_warnings():
        # Walking imports each package once; the aliases warn when they are walked.
        warnings.simplefilter("ignore", DeprecationWarning)
        names = [
            info.name
            for info in pkgutil.walk_packages(oceanarray.__path__, "oceanarray.")
        ]
    return sorted(
        name
        for name in names
        if name not in DEPRECATED_ALIASES
        and not any(name.startswith(f"{alias}.") for alias in DEPRECATED_ALIASES)
        and not (name == _SKIP_PREFIX or name.startswith(f"{_SKIP_PREFIX}."))
    )


def _is_ours(name: str) -> bool:
    """Return True for ``oceanarray`` and its submodules."""
    return name == "oceanarray" or name.startswith("oceanarray.")


@pytest.mark.parametrize("module", _module_names())
def test_module_imports_without_deprecated_paths(module: str) -> None:
    """Importing *module* afresh raises no DeprecationWarning."""
    saved = {name: mod for name, mod in sys.modules.items() if _is_ours(name)}
    for name in saved:
        del sys.modules[name]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            importlib.import_module(module)
    finally:
        for name in [name for name in sys.modules if _is_ours(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


@pytest.mark.parametrize("alias", DEPRECATED_ALIASES)
def test_skipped_alias_is_deprecated(alias: str) -> None:
    """Each skipped module exists and says it is deprecated, so the list stays true."""
    rel = Path(*alias.split(".")[1:])
    path = _PACKAGE / rel / "__init__.py"
    if not path.exists():
        path = (_PACKAGE / rel).with_suffix(".py")
    docstring = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    assert docstring.startswith("Deprecated"), f"{alias} is not a deprecated alias"
