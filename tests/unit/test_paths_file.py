"""``Paths`` and the paths file: where a project's mooring YAMLs, raw, processed and report folders are.

Run on a project tree built from the committed dune2_1_2026 and egM2 (OSNAP East)
mooring YAMLs.
"""

import dataclasses
import re
import shutil
import warnings
from pathlib import Path

import pytest

import oceanarray as oa
from oceanarray import _deprecated, mooring
from oceanarray.paths import PATHS_FIELDS, Paths

_PROC = Path(__file__).parents[1] / "fixtures" / "proc"
_YAMLS = {
    "dune2_1_2026": _PROC / "dune2_1_2026" / "dune2_1_2026.mooring.yaml",
    "egM2_2016": _PROC / "egM2_concat" / "egM2_2016" / "egM2_2016.mooring.yaml",
    "egM2_2018": _PROC / "egM2_concat" / "egM2_2018" / "egM2_2018.mooring.yaml",
}

_PATHS_YAML = """\
# relative paths are relative to this file
yaml_dir: config/moorings
raw_dir: data/raw
proc_dir: data/proc
report_dir: outputs/report
export_dir: outputs/export
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project folder: the mooring YAMLs in ``config/moorings/`` and a ``paths.yaml``."""
    yaml_dir = tmp_path / "config" / "moorings"
    yaml_dir.mkdir(parents=True)
    for source in _YAMLS.values():
        shutil.copy(source, yaml_dir / source.name)
    (tmp_path / "paths.yaml").write_text(_PATHS_YAML, encoding="utf-8")
    return tmp_path


def test_exported_at_the_top_level():
    """``oa.Paths`` is the class in ``oceanarray.paths``."""
    assert oa.Paths is Paths
    assert "Paths" in oa.__all__


def test_from_file_resolves_relative_folders_against_the_files_folder(
    project, monkeypatch
):
    """Relative folders in the paths file are relative to its folder, not the current one."""
    monkeypatch.chdir(project / "config")
    paths = Paths.from_file(project / "paths.yaml")
    assert paths.yaml_dir == project / "config" / "moorings"
    assert paths.raw_dir == project / "data" / "raw"
    assert paths.proc_dir == project / "data" / "proc"
    assert paths.report_dir == project / "outputs" / "report"
    assert paths.export_dir == project / "outputs" / "export"
    assert paths.caldip_dir is None
    assert dict(paths.sources) == dict.fromkeys(PATHS_FIELDS[:5], "file")
    assert paths.mooring_path("egM2_2016").read_text(encoding="utf-8") == _YAMLS[
        "egM2_2016"
    ].read_text(encoding="utf-8")


def test_a_yaml_from_the_paths_file_loads(project):
    """The path found is a mooring YAML that ``mooring.load`` reads, with its own name."""
    paths = Paths.from_file(project / "paths.yaml")
    assert mooring.load(paths.mooring_path("dune2_1_2026"))["name"] == "dune2_1_2026"


def test_standard_structure_under_one_root(tmp_path):
    """``Paths.standard`` puts each folder under one root; no caldip folder."""
    paths = Paths.standard(tmp_path)
    assert paths.yaml_dir == tmp_path / "yamls"
    assert paths.raw_dir == tmp_path / "raw"
    assert paths.proc_dir == tmp_path / "proc"
    assert paths.report_dir == tmp_path / "reports"
    assert paths.export_dir == tmp_path / "exports"
    assert paths.caldip_dir is None
    assert paths.mooring_dir("egM2_2016") == tmp_path / "proc" / "egM2_2016"
    assert set(paths.sources.values()) == {"standard"}


def test_relative_paths_in_python_stay_relative():
    """Given in Python, a relative folder stays relative to the current folder."""
    paths = Paths(raw_dir="raw", proc_dir=Path("proc"))
    assert paths.raw_mooring_dir("egM2_2016") == Path("raw") / "egM2_2016"
    assert paths.mooring_dir("egM2_2016") == Path("proc") / "egM2_2016"
    assert dict(paths.sources) == {"raw_dir": "given", "proc_dir": "given"}


def test_home_folder_is_expanded(tmp_path, monkeypatch):
    """``~`` is expanded in the paths file and in Python."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "paths.yaml").write_text("raw_dir: ~/raw\n", encoding="utf-8")
    assert Paths.from_file(tmp_path / "paths.yaml").raw_dir == tmp_path / "raw"
    assert Paths(proc_dir="~/proc").proc_dir == tmp_path / "proc"
    assert Paths.standard("~").yaml_dir == tmp_path / "yamls"


def test_unknown_key_names_the_allowed_keys(project):
    """A key the paths file does not take is an error naming the allowed keys."""
    (project / "paths.yaml").write_text(
        _PATHS_YAML + "output_dir: outputs\n", encoding="utf-8"
    )
    with pytest.raises(oa.MooringError, match="output_dir") as excinfo:
        Paths.from_file(project / "paths.yaml")
    for key in (*PATHS_FIELDS, "moorings"):
        assert key in str(excinfo.value)


def test_unknown_mooring_override_key_is_an_error(project):
    """A key under ``moorings:`` must be one of the six folders."""
    (project / "paths.yaml").write_text(
        _PATHS_YAML + "moorings:\n  egM2_2016:\n    raw_folder: elsewhere\n",
        encoding="utf-8",
    )
    with pytest.raises(oa.MooringError, match="raw_folder"):
        Paths.from_file(project / "paths.yaml")
    with pytest.raises(ValueError, match="raw_folder"):
        Paths(moorings={"egM2_2016": {"raw_folder": "elsewhere"}})


def test_a_missing_paths_file_is_an_error(tmp_path):
    """A paths file that does not exist names its path."""
    with pytest.raises(oa.MooringError, match="not found"):
        Paths.from_file(tmp_path / "paths.yaml")


def test_mooring_override_is_the_exact_folder(project):
    """A mooring under ``moorings:`` gets its folder as written, no name appended."""
    elsewhere = project.parent / "elsewhere" / "proc_M2"  # absolute on every system
    (project / "paths.yaml").write_text(
        _PATHS_YAML
        + "moorings:\n  egM2_2016:\n    raw_dir: data/raw/2016_recovery\n"
        + f"    proc_dir: {elsewhere.as_posix()}\n",
        encoding="utf-8",
    )
    paths = Paths.from_file(project / "paths.yaml")
    assert paths.raw_mooring_dir("egM2_2016") == project / "data/raw/2016_recovery"
    assert paths.mooring_dir("egM2_2016") == elsewhere
    assert paths.raw_mooring_dir("egM2_2018") == project / "data/raw/egM2_2018"
    assert paths.report_mooring_dir("egM2_2016") == (
        project / "outputs" / "report" / "egM2_2016"
    )


def test_with_mooring_returns_a_copy(project):
    """``with_mooring`` gives a new Paths and leaves the original as it was."""
    paths = Paths.from_file(project / "paths.yaml")
    moved = paths.with_mooring("egM2_2018", raw_dir=project / "usb", report_dir="r")
    assert moved is not paths
    assert moved.raw_mooring_dir("egM2_2018") == project / "usb"
    assert moved.report_mooring_dir("egM2_2018") == Path("r")
    assert paths.raw_mooring_dir("egM2_2018") == project / "data/raw/egM2_2018"
    assert paths.moorings == {}
    assert moved != paths
    again = moved.with_mooring("egM2_2018", proc_dir="p")
    assert again.raw_mooring_dir("egM2_2018") == project / "usb"
    assert again.mooring_dir("egM2_2018") == Path("p")
    assert dict(again.sources) == dict(paths.sources)
    with pytest.raises(TypeError, match="raw_folder"):
        paths.with_mooring("egM2_2018", raw_folder="x")


def test_mooring_path_finds_the_file_in_yaml_dir(project):
    """The YAML is ``<yaml_dir>/<mooring_name>.mooring.yaml``."""
    paths = Paths.from_file(project / "paths.yaml")
    assert paths.mooring_path("egM2_2018") == (
        project / "config" / "moorings" / "egM2_2018.mooring.yaml"
    )


def test_yaml_in_the_old_proc_folder_is_found_with_a_deprecation_warning(tmp_path):
    """A YAML still in ``<proc_dir>/<name>/`` is returned, warning at the caller."""
    old = tmp_path / "proc" / "dune2_1_2026" / "dune2_1_2026.mooring.yaml"
    old.parent.mkdir(parents=True)
    shutil.copy(_YAMLS["dune2_1_2026"], old)
    message = re.escape(
        _deprecated.message("a mooring YAML inside proc_dir/<mooring_name>/")
    )
    for paths in (Paths.standard(tmp_path), Paths(proc_dir=tmp_path / "proc")):
        with pytest.warns(DeprecationWarning, match=message) as record:
            found = paths.mooring_path("dune2_1_2026")
        assert found == old
        assert record[0].filename == __file__
        assert "0.8.0" in str(record[0].message)


def test_yaml_dir_file_wins_over_the_old_location(project):
    """With the YAML in both places, the yaml_dir one is used and nothing warns."""
    paths = Paths.from_file(project / "paths.yaml")
    old = paths.mooring_dir("egM2_2016") / "egM2_2016.mooring.yaml"
    old.parent.mkdir(parents=True)
    shutil.copy(_YAMLS["egM2_2016"], old)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert paths.mooring_path("egM2_2016") == paths.yaml_dir / old.name


def test_missing_field_error_names_the_field():
    """A folder that was not given is named in the error."""
    paths = Paths(proc_dir="proc")
    with pytest.raises(oa.MooringError, match="raw_dir"):
        paths.raw_mooring_dir("egM2_2016")
    with pytest.raises(oa.MooringError, match="report_dir"):
        paths.report_mooring_dir("egM2_2016")
    with pytest.raises(oa.MooringError, match="proc_dir"):
        Paths().mooring_dir("egM2_2016")
    with pytest.raises(oa.MooringError, match="yaml_dir"):
        Paths().mooring_path("egM2_2016")
    with pytest.raises(oa.MooringError, match="yaml_dir"):
        Paths(raw_dir="raw").mooring_paths()


def test_mooring_paths_lists_every_mooring_yaml_sorted(project):
    """``mooring_paths`` gives the mooring YAMLs of yaml_dir in sorted order."""
    paths = Paths.from_file(project / "paths.yaml")
    (paths.yaml_dir / "notes.yaml").write_text("x: 1\n", encoding="utf-8")
    assert paths.mooring_paths() == [
        paths.yaml_dir / f"{name}.mooring.yaml" for name in sorted(_YAMLS)
    ]


def test_mooring_paths_in_the_old_proc_folders_warn(tmp_path):
    """Without a yaml_dir, the YAMLs still in ``<proc_dir>/*/`` are listed, with a warning."""
    for name, source in _YAMLS.items():
        (tmp_path / "proc" / name).mkdir(parents=True)
        shutil.copy(source, tmp_path / "proc" / name / source.name)
    with pytest.warns(DeprecationWarning, match="proc_dir") as record:
        found = Paths(proc_dir=tmp_path / "proc").mooring_paths()
    assert record[0].filename == __file__
    assert found == [
        tmp_path / "proc" / name / f"{name}.mooring.yaml" for name in sorted(_YAMLS)
    ]


def test_paths_is_frozen(project):
    """A Paths cannot be changed in place, nor can its per-mooring folders."""
    paths = Paths.from_file(project / "paths.yaml").with_mooring(
        "egM2_2016", raw_dir="x"
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        paths.raw_dir = Path("elsewhere")
    with pytest.raises(TypeError):
        paths.moorings["egM2_2018"] = {}
    with pytest.raises(TypeError):
        paths.moorings["egM2_2016"]["raw_dir"] = Path("y")
