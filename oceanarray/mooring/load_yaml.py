"""Load one mooring YAML (``*.mooring.yaml``) and refuse it if it has an ERROR.

The one seam for reading a mooring YAML: every stage and report receives the loaded
dict. Imported in the package as ``from oceanarray import mooring as _mooring`` so a
``mooring`` parameter does not shadow it. The file is a cross-package contract
(moordiag reads it), so :func:`load` keeps every key and value as written.
"""

from collections.abc import Mapping
from os import PathLike
from pathlib import Path

import yaml

from oceanarray.exceptions import MooringError
from oceanarray.mooring.check_yaml import _resolve_relative_paths, check


def _resolve_path(mooring: str | PathLike, proc_dir: str | PathLike | None) -> Path:
    """Resolve a reference (a YAML path, or a bare name + *proc_dir*) to a YAML path."""
    path = Path(mooring)
    if path.suffix in {".yaml", ".yml"} or path.exists():
        return path
    if proc_dir is not None:
        name = str(mooring)
        return Path(proc_dir) / name / f"{name}.mooring.yaml"
    return path


def load(
    mooring: str | PathLike | Mapping,
    *,
    proc_dir: str | PathLike | None = None,
) -> dict:
    """Load and validate one mooring YAML.

    Parameters
    ----------
    mooring : str or os.PathLike or Mapping
        A path to a ``*.mooring.yaml`` file, a bare mooring name resolved
        against *proc_dir*, or an already-parsed mapping (returned as a shallow
        copy, keys unchanged).
    proc_dir : str or os.PathLike or None, optional
        The processing directory a bare mooring name is resolved against.

    Returns
    -------
    dict
        The parsed mooring YAML, every key kept as written.

    Raises
    ------
    MooringError
        If the file is missing, is not a mapping, or fails validation with any
        ERROR-level issue. WARNING-level issues are kept on the exception's
        ``issues`` attribute but do not raise.
    """
    if isinstance(mooring, Mapping):
        data = dict(mooring)
        dir_name = None
    else:
        path = _resolve_path(mooring, proc_dir)
        if not path.exists():
            raise MooringError(f"Mooring config not found: {path}")
        try:
            with path.open() as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise MooringError(f"YAML parse error in {path}: {e}") from e
        dir_name = path.parent.name
        _resolve_relative_paths(data, path)

    if not isinstance(data, dict):
        raise MooringError("Mooring YAML root must be a mapping")

    issues = check(data, dir_name=dir_name)
    errors = [i for i in issues if i.level == "ERROR"]
    if errors:
        summary = "; ".join(i.message for i in errors)
        raise MooringError(f"Invalid mooring config: {summary}", issues)
    return data
