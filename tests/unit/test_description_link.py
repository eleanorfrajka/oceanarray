"""A mooring description reached through a link resolves its relative paths from the real file.

A processing folder may hold a link to a description kept with the project's other
descriptions; a relative ``sensor_catalog`` in it is relative to that real file.
Built from the dune2 fixture description and the sensor-catalog fixture.
"""

from __future__ import annotations

import os
import pathlib
import shutil

import pytest
import yaml

from oceanarray import mooring
from oceanarray.pipeline import resolve_mooring
from oceanarray.pipeline.read_mooring import read_mooring

_FIX = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
_NAME = "dune2_1_2026"


@pytest.fixture
def linked(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """``proc/<name>/<name>.mooring.yaml`` a link to ``config/moorings/<name>.mooring.yaml``.

    The real description names ``../sensor_catalog.yaml`` (``config/sensor_catalog.yaml``);
    ``proc/`` holds no catalog, so a path taken from the link's folder fails.
    """
    if not hasattr(os, "symlink"):
        pytest.skip("no symbolic links on this system")
    proc = tmp_path / "proc" / _NAME
    shutil.copytree(_FIX / "proc" / _NAME, proc)
    real_dir = tmp_path / "config" / "moorings"
    real_dir.mkdir(parents=True)
    shutil.copy(_FIX / "catalog" / "sensor_catalog.yaml", tmp_path / "config")
    link = proc / f"{_NAME}.mooring.yaml"
    data = yaml.safe_load(link.read_text(encoding="utf-8"))
    data["sensor_catalog"] = "../sensor_catalog.yaml"
    real = real_dir / link.name
    real.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    link.unlink()
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("cannot create symbolic links here")
    return link, tmp_path / "config" / "sensor_catalog.yaml"


def test_load_resolves_the_catalog_from_the_real_file(linked) -> None:
    """``load`` and ``check`` read ``../`` from ``config/moorings/``, not ``proc/<name>/``."""
    link, catalog = linked
    cfg = mooring.load(link)
    assert pathlib.Path(cfg["sensor_catalog"]) == catalog.resolve()
    assert not [i for i in mooring.check(link) if i.level == "ERROR"]


def test_the_link_folder_still_names_the_mooring_and_holds_its_files(linked) -> None:
    """The stage files are found beside the link; the name is checked against its folder."""
    link, catalog = linked
    cfg, folder = resolve_mooring(link, None)
    assert folder == link.parent
    assert pathlib.Path(cfg["sensor_catalog"]) == catalog.resolve()
    data = read_mooring(cfg, stage=3, mooring_dir=folder, allow_missing=True)
    assert {"2941", "9920"} <= set(data)


def test_a_dict_takes_the_catalog_from_the_linked_description(linked) -> None:
    """A dict with ``mooring_dir=``: the relative path is taken where the description really is."""
    link, catalog = linked
    data = yaml.safe_load(link.read_text(encoding="utf-8"))
    cfg, folder = resolve_mooring(data, link.parent)
    assert folder == link.parent
    assert pathlib.Path(cfg["sensor_catalog"]) == catalog.resolve()
