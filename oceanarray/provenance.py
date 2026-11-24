"""Which oceanarray wrote a file: the version, with the commit for a source checkout, on every file.

Every file oceanarray writes carries ``oceanarray_version``. When the package
runs from a git checkout, the version names the commit that ran as a local
version label, ``0.6.0.post1.dev5+gf52121d`` (``.dirty`` appended when the tree
has uncommitted changes). The hash is read from git at run time: the installed
version string is written when the package is installed, so with an editable
install it would otherwise name the last reinstall. Each stage also keeps the
version that wrote it as ``stage<n>_oceanarray_version``, so a stage-3 file
still says which code ran stage 1. :func:`requires_version` is the one check
of "written by an oceanarray that already did X"; :func:`public_version`
compares versions without the commit.
"""

from __future__ import annotations

import functools
import hashlib
import subprocess
from collections.abc import Mapping
from pathlib import Path

from packaging.version import InvalidVersion, Version

VERSION_ATTR = "oceanarray_version"

#: The separate commit attribute development builds wrote before 0.6.1; the writer drops it, since
#: the commit now sits in :data:`VERSION_ATTR`.
LEGACY_COMMIT_ATTR = "oceanarray_commit"


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


def _with_commit(version: str, commit: str | None, dirty: bool) -> str:
    """Return *version* with *commit* as its local version label (``+g<hash>[.dirty]``)."""
    if not commit:
        return version
    public = version.split("+", 1)[0]
    return f"{public}+g{commit}" + (".dirty" if dirty else "")


@functools.lru_cache(maxsize=1)
def version_attrs() -> dict[str, str]:
    """Return the attributes naming the running oceanarray.

    Returns
    -------
    dict of str to str
        ``oceanarray_version``: the installed version, with the commit that is
        running as its local label (``+g<hash>``, ``.dirty`` for uncommitted
        changes) when the package runs from a git checkout.
    """
    from oceanarray import __version__

    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(commit) and bool(_git("status", "--porcelain", "--untracked-files=no"))
    return {VERSION_ATTR: _with_commit(str(__version__), commit, dirty)}


def public_version(value: object) -> str | None:
    """Return the version in *value* without its local label (the commit).

    Parameters
    ----------
    value : object
        An ``oceanarray_version`` attribute value.

    Returns
    -------
    str or None
        The public version (``0.6.0.post1.dev5`` for ``0.6.0.post1.dev5+gf52121d``),
        or None when *value* is empty or not a version.
    """
    if value in (None, ""):
        return None
    try:
        return Version(str(value)).public
    except InvalidVersion:
        return None


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
        recorded) or names a release older than *minimum*. A development build
        (``.devN``) passes: its number counts commits since the last tag, which
        differs between a branch and ``main`` (and is ``0.0`` in a checkout
        without tags), so it cannot be ordered against *minimum*; the attribute
        being there is the evidence.
    """
    value = attrs.get(key)
    if value in (None, ""):
        return False
    try:
        version = Version(str(value))
    except InvalidVersion:
        return False
    return version.is_devrelease or version >= Version(minimum)


def sha256(path: str | Path) -> str:
    """Return the SHA-256 of a file, as hex.

    Parameters
    ----------
    path : str or pathlib.Path
        The file.

    Returns
    -------
    str
        The 64-digit hex digest.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_change_reason(attrs: Mapping[str, object]) -> str | None:
    """Return why a product's oceanarray release differs from this one, or None.

    Only the public version counts: a commit hash or ``.dirty`` added in a git
    checkout does not make a product stale (rebuild with ``force`` after changing
    the code without a new version).

    Parameters
    ----------
    attrs : Mapping
        The product's global attributes.

    Returns
    -------
    str or None
        ``"oceanarray <was> wrote it, this is <now>"``, or None for the same release.
    """
    was = public_version(attrs.get(VERSION_ATTR))
    now = public_version(version_attrs()[VERSION_ATTR])
    if was != now:
        return f"oceanarray {was or 'unknown'} wrote it, this is {now}"
    return None
