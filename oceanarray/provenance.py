"""Which oceanarray wrote a file: the version (and, for a source checkout, the commit) on every file.

Every file oceanarray writes carries ``oceanarray_version`` (and, when the
package runs from a git checkout, ``oceanarray_commit``: the short hash, with
``-dirty`` when the tree has uncommitted changes). Each stage also keeps the
version that wrote it as ``stage<n>_oceanarray_version``, so a stage-3 file
still says which code ran stage 1. :func:`requires_version` is the one check
of "written by an oceanarray that already did X".
"""

from __future__ import annotations

import functools
import subprocess
from collections.abc import Mapping
from pathlib import Path

from packaging.version import InvalidVersion, Version

VERSION_ATTR = "oceanarray_version"
COMMIT_ATTR = "oceanarray_commit"


def _git(*args: str) -> str | None:
    """Return a git command's output in the package's checkout, or None outside one."""
    root = Path(__file__).resolve().parent.parent
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


@functools.lru_cache(maxsize=1)
def version_attrs() -> dict[str, str]:
    """Return the attributes naming the running oceanarray.

    Returns
    -------
    dict of str to str
        ``oceanarray_version``, and ``oceanarray_commit`` for a git checkout.
    """
    from oceanarray import __version__

    attrs = {VERSION_ATTR: str(__version__)}
    commit = _git("rev-parse", "--short", "HEAD")
    if commit:
        dirty = _git("status", "--porcelain", "--untracked-files=no")
        attrs[COMMIT_ATTR] = commit + ("-dirty" if dirty else "")
    return attrs


def stage_version_attr(stage: int) -> str:
    """Return the name of the attribute holding the version that wrote *stage*."""
    return f"stage{int(stage)}_{VERSION_ATTR}"


def requires_version(
    attrs: Mapping[str, object],
    minimum: str,
    *,
    key: str = VERSION_ATTR,
) -> bool:
    """Return whether a file was written by oceanarray *minimum* or later.

    Parameters
    ----------
    attrs : Mapping
        The file's global attributes.
    minimum : str
        The first version that does what the caller needs.
    key : str, optional
        The attribute to read (``oceanarray_version``, or
        ``stage1_oceanarray_version`` for what stage 1 did).

    Returns
    -------
    bool
        False when the attribute is absent (written before versions were
        recorded), unreadable, or older than *minimum*.
    """
    value = attrs.get(key)
    if value in (None, ""):
        return False
    try:
        return Version(str(value)) >= Version(minimum)
    except InvalidVersion:
        return False
