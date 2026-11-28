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

from oceanarray import _deprecated
from oceanarray.exceptions import MooringError
from oceanarray.mooring.check_yaml import _check_data, _resolve_relative_paths


def _resolve_path(mooring: str | PathLike, proc_dir: str | PathLike | None) -> Path:
    """Resolve a reference (a YAML path, or a bare name + *proc_dir*) to a YAML path."""
    path = Path(mooring)
    if path.suffix in {".yaml", ".yml"} or path.exists():
        return path
    if proc_dir is not None:
        name = str(mooring)
        return Path(proc_dir) / name / f"{name}.mooring.yaml"
    return path


def folder_mooring_path(folder: str | PathLike, caller: str) -> Path:
    """Return the path of the one ``*.mooring.yaml`` in *folder*, else raise MooringError.

    Parameters
    ----------
    folder : str or os.PathLike
        The mooring's folder.
    caller : str
        The function or command the error names.
    """
    found = sorted(Path(folder).glob("*.mooring.yaml"))
    if len(found) != 1:
        raise MooringError(
            f"{folder} holds {len(found)} '*.mooring.yaml' files; "
            f"{caller} needs exactly one (or point at the file)."
        )
    return found[0]


def load(
    mooring_path: str | PathLike | Mapping | None = None,
    *,
    proc_dir: str | PathLike | None = None,
    mooring: str | PathLike | Mapping | None = None,
) -> dict:
    """Load and validate one mooring YAML.

    Parameters
    ----------
    mooring_path : str or os.PathLike
        The ``*.mooring.yaml`` file. A bare mooring name (with *proc_dir*) or a
        loaded mapping (returned as a shallow copy) is deprecated; removed in 0.8.0.
    proc_dir : str or os.PathLike or None, optional
        Deprecated: the folder a bare name is looked up in; removed in 0.8.0.
    mooring : str or os.PathLike or Mapping, optional
        Deprecated name of *mooring_path*; removed in 0.8.0.

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
    renamed = "oceanarray.mooring.load(mooring=)"
    mooring_path = _deprecated.resolve_keyword(
        renamed, mooring_path, mooring, required="mooring_path", quiet=True
    )
    old_forms = [renamed] if mooring is not None else []
    if isinstance(mooring_path, Mapping):
        old_forms.append(_deprecated.DICT_GIVEN)
    elif proc_dir is not None:
        old_forms.append("oceanarray.mooring.load(proc_dir=)")
    if old_forms:
        # One warning for the call, the first old form it uses.
        _deprecated.warn(old_forms[0])
    if isinstance(mooring_path, Mapping):
        data = dict(mooring_path)
        dir_name = None
    else:
        path = _resolve_path(mooring_path, proc_dir)
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

    issues = _check_data(data, dir_name)
    errors = [i for i in issues if i.level == "ERROR"]
    if errors:
        summary = "; ".join(i.message for i in errors)
        raise MooringError(f"Invalid mooring config: {summary}", issues)
    return data
