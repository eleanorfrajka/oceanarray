"""The verbs take ``(mooring_path, paths)``; each old form warns once and writes the same files.

Run on copies of the committed dune2_1_2026 mooring (its raw files and its processed
folder) and the MIXSED2026 array YAML. Each old form runs in the same folder as the
new form it stands for, so the files can be compared one by one.
"""

import re
import shutil
import warnings
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

import oceanarray as oa
from oceanarray import mooring
from oceanarray.exceptions import MooringError

pytest.importorskip("seasenselib", reason="seasenselib not installed")

_FIX = Path(__file__).parents[1] / "fixtures"
_RAW = _FIX / "raw"
_PROC = _FIX / "proc" / "dune2_1_2026"
_ARRAY = _FIX / "proc" / "MIXSED2026.array.yaml"
_NAME = "dune2_1_2026"

# Page parts that differ between two renders of the same files.
_VOLATILE = (
    (re.compile(r"(generated |&bull; )\d{4}-\d\d-\d\d \d\d:\d\d UTC"), r"\1<TIME>"),
    (re.compile(r"<td>\s*\d{4}-\d\d-\d\d \d\d:\d\d UTC\s*</td>"), "<td><MTIME></td>"),
    (re.compile(r"base64,[A-Za-z0-9+/=]+"), "base64,<PNG>"),
)


def _old_tree(root: Path) -> Path:
    """Copy the processed folder, its YAML inside (with a platform_code), to root/proc; return the YAML."""
    folder = root / "proc" / _NAME
    shutil.copytree(_PROC, folder)
    mooring_path = folder / f"{_NAME}.mooring.yaml"
    text = mooring_path.read_text(encoding="utf-8")
    mooring_path.write_text(
        text.replace(f"name: {_NAME}", f"name: {_NAME}\nplatform_code: DUNE2", 1),
        encoding="utf-8",
    )
    return mooring_path


def _paths(mooring_path: Path, raw_dir: Path | None = None, **dirs: Path) -> oa.Paths:
    """Return the Paths the old defaults meant for a folder holding its YAML."""
    folder = mooring_path.parent
    exact = {"report_dir": folder / "report", **dirs}
    return oa.Paths(
        raw_dir=raw_dir, proc_dir=folder.parent, export_dir=folder / "oceansites"
    ).with_mooring(_NAME, **exact)


def _outputs(folder: Path, *, pages: bool = True) -> dict[str, object]:
    """Return each netCDF and HTML file under *folder*: attribute names and data, or page text.

    Without *pages*, a page is listed by name only (its processing history has the
    times the files were written).
    """
    found: dict[str, object] = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if path.suffix == ".nc":
            with xr.open_dataset(path, decode_times=False) as ds:
                # A stage file's hash covers its history, which has the time it was written.
                found[rel] = (
                    sorted(ds.attrs),
                    {
                        v: np.asarray(ds[v].values).tobytes()
                        for v in ds.variables
                        if "sha256" not in v
                    },
                )
        elif path.suffix == ".html" and not pages:
            found[rel] = None
        elif path.suffix == ".html":
            text = path.read_text(encoding="utf-8")
            for pattern, repl in _VOLATILE:
                text = pattern.sub(repl, text)
            found[rel] = text
    return found


def _warned_once(call: Callable[[], object], key: str) -> object:
    """Run *call*; assert one oceanarray deprecation, for *key*, pointing at this file."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = call()
    ours = [w for w in caught if "removed in 0.8.0" in str(w.message)]
    assert [str(w.message) for w in ours] == [oa._deprecated.message(key)]
    assert ours[0].filename == __file__
    return result


def _quiet(call: Callable[[], object]) -> object:
    """Run *call*; assert no oceanarray deprecation."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = call()
    assert not [w for w in caught if "removed in 0.8.0" in str(w.message)]
    return result


# ── process and run ─────────────────────────────────────────────────────────
#: (deprecation key, the old call on the YAML, the stages both forms run)
_PROCESS = [
    (
        "oceanarray.process(raw_dir=)",
        lambda y, s: oa.process(y, raw_dir=_RAW, stages=s, force=True),
        (1, 2, 3),
    ),
    (
        "oceanarray.process(mooring_dir=)",
        lambda y, s: oa.process(y, mooring_dir=y.parent, stages=s, force=True),
        (2, 3),
    ),
    (
        "oceanarray.process(caldip_dir=)",
        lambda y, s: oa.process(y, caldip_dir=y.parent, stages=s, force=True),
        (3,),
    ),
    (
        "a mooring folder in place of a verb's mooring_path",
        lambda y, s: oa.process(y.parent, stages=s, force=True),
        (2, 3),
    ),
    (
        "a mooring dict in place of a mooring_path",
        lambda y, s: oa.process(
            mooring.load(y), mooring_dir=y.parent, stages=s, force=True
        ),
        (2, 3),
    ),
    (
        "oceanarray.process(mooring=)",
        lambda y, s: oa.process(mooring=y, paths=_paths(y), stages=s, force=True),
        (2, 3),
    ),
    (
        "a verb called without paths",
        lambda y, s: oa.process(y, stages=s, force=True),
        (2, 3),
    ),
]


@pytest.mark.parametrize(
    ("key", "old", "stages"), _PROCESS, ids=[c[0] for c in _PROCESS]
)
def test_process_old_forms_write_the_same_files(tmp_path, key, old, stages):
    """Each old form of process warns once and writes what the new form writes."""
    mooring_path = _old_tree(tmp_path)
    paths = _paths(mooring_path, _RAW if "raw_dir" in key else None)
    if "caldip" in key:
        paths = oa.Paths(
            proc_dir=mooring_path.parent.parent, caldip_dir=mooring_path.parent
        )
    new = _quiet(lambda: oa.process(mooring_path, paths, stages=stages, force=True))
    expected = _outputs(mooring_path.parent)
    assert _warned_once(lambda: old(mooring_path, stages), key) == new
    assert _outputs(mooring_path.parent) == expected


_RUN = [
    (
        "oceanarray.run(raw_dir=)",
        lambda y, **kw: oa.run(y, raw_dir=_RAW, **kw),
        {"report": True},
    ),
    (
        "oceanarray.run(mooring_dir=)",
        lambda y, **kw: oa.run(y, mooring_dir=y.parent, from_stage=2, **kw),
        {},
    ),
    (
        "oceanarray.run(caldip_dir=)",
        lambda y, **kw: oa.run(y, caldip_dir=y.parent, from_stage=3, **kw),
        {"caldip_dir": True},
    ),
    (
        "a mooring folder in place of a verb's mooring_path",
        lambda y, **kw: oa.run(y.parent, from_stage=2, **kw),
        {},
    ),
    (
        "a mooring dict in place of a mooring_path",
        lambda y, **kw: oa.run(
            mooring.load(y), mooring_dir=y.parent, from_stage=2, **kw
        ),
        {},
    ),
    (
        "oceanarray.run(mooring=)",
        lambda y, **kw: oa.run(mooring=y, paths=_paths(y), from_stage=2, **kw),
        {},
    ),
    (
        "a verb called without paths",
        lambda y, **kw: oa.run(y, from_stage=2, **kw),
        {},
    ),
]


@pytest.mark.parametrize(("key", "old", "how"), _RUN, ids=[c[0] for c in _RUN])
def test_run_old_forms_write_the_same_files(tmp_path, key, old, how):
    """Each old form of run warns once and writes what the new form writes; report=True its pages too."""
    mooring_path = _old_tree(tmp_path)
    kw = {"force": True, "dt_seconds": 3600}
    if how.get("report"):
        kw.update(report=True, serials=["2941"])
    paths = _paths(mooring_path, _RAW if "raw_dir" in key else None)
    if how.get("caldip_dir"):
        paths = oa.Paths(
            proc_dir=mooring_path.parent.parent, caldip_dir=mooring_path.parent
        )
    stages = {} if "raw_dir" in key else {"from_stage": 3 if "caldip" in key else 2}
    new = _quiet(lambda: oa.run(mooring_path, paths, **stages, **kw))
    expected = _outputs(mooring_path.parent, pages=False)
    if how.get("report"):
        assert new[-1] == mooring_path.parent / "report" / f"{_NAME}_report.html"
    assert _warned_once(lambda: old(mooring_path, **kw), key) == new
    assert _outputs(mooring_path.parent, pages=False) == expected


def test_run_and_process_in_an_own_layout_and_the_standard_one(tmp_path):
    """The YAML in its own folder: nothing is written beside it, and both layouts hold the same files."""
    config = tmp_path / "own" / "config"
    config.mkdir(parents=True)
    mooring_path = config / f"{_NAME}.mooring.yaml"
    shutil.copy(_PROC / f"{_NAME}.mooring.yaml", mooring_path)
    own = oa.Paths(
        yaml_dir=config,
        raw_dir=_RAW,
        proc_dir=tmp_path / "own" / "processed",
        report_dir=tmp_path / "own" / "pages",
    )
    standard = oa.Paths.standard(tmp_path / "standard")
    shutil.copytree(_RAW / _NAME, standard.raw_mooring_dir(_NAME))
    standard.yaml_dir.mkdir(parents=True)
    shutil.copy(mooring_path, standard.mooring_path(_NAME))
    for paths in (own, standard):
        files = _quiet(
            lambda p=paths: oa.run(
                p.mooring_path(_NAME), p, dt_seconds=3600, report=True, serials=["2941"]
            )
        )
        assert files[-1] == paths.report_mooring_dir(_NAME) / f"{_NAME}_report.html"
        assert all(f.is_relative_to(paths.mooring_dir(_NAME)) for f in files[:-1])
    assert sorted(p.name for p in config.iterdir()) == [mooring_path.name]
    nc = {
        k: v for k, v in _outputs(own.mooring_dir(_NAME)).items() if k.endswith(".nc")
    }
    assert nc == {
        k: v
        for k, v in _outputs(standard.mooring_dir(_NAME)).items()
        if k.endswith(".nc")
    }


# ── report ──────────────────────────────────────────────────────────────────
_PAGES = {"stack": False, "grid": False, "instruments": False, "force": True}

#: (deprecation key, the old call on the YAML and a scratch folder, the new Paths)
_REPORT = [
    (
        "oceanarray.report(raw_dir=)",
        lambda y, _t: oa.report(y, raw_dir=_RAW, serials=["2941"], **_PAGES),
        lambda y, _t: _paths(y, _RAW),
    ),
    (
        "oceanarray.report(mooring_dir=)",
        lambda y, _t: oa.report(y, mooring_dir=y.parent, **_PAGES),
        lambda y, _t: _paths(y),
    ),
    (
        "oceanarray.report(out_dir=)",
        lambda y, t: oa.report(y, out_dir=t, **_PAGES),
        lambda y, t: _paths(y, report_dir=t),
    ),
    (
        "oceanarray.report(report_dir=)",
        lambda y, t: oa.report(y, report_dir=t, **_PAGES),
        lambda y, t: oa.Paths(proc_dir=y.parent.parent, report_dir=t),
    ),
    (
        "oceanarray.report(proc_dir=)",
        lambda y, t: oa.report(y, proc_dir=t, **_PAGES),
        lambda y, _t: _paths(y),
    ),
    (
        "oceanarray.report(pdf_dir=)",
        lambda y, t: oa.report(y, pdf_dir=t, **_PAGES),
        lambda y, _t: _paths(y),
    ),
    (
        "a mooring folder in place of a verb's mooring_path",
        lambda y, _t: oa.report(y.parent, **_PAGES),
        lambda y, _t: _paths(y),
    ),
    (
        "a mooring dict in place of a mooring_path",
        lambda y, _t: oa.report(mooring.load(y), mooring_dir=y.parent, **_PAGES),
        lambda y, _t: _paths(y),
    ),
    (
        "oceanarray.report(mooring=)",
        lambda y, _t: oa.report(mooring=y, paths=_paths(y), **_PAGES),
        lambda y, _t: _paths(y),
    ),
    (
        "a verb called without paths",
        lambda y, _t: oa.report(y, **_PAGES),
        lambda y, _t: _paths(y),
    ),
]


@pytest.mark.parametrize(
    ("key", "old", "new_paths"), _REPORT, ids=[c[0] for c in _REPORT]
)
def test_report_old_forms_write_the_same_pages(tmp_path, key, old, new_paths):
    """Each old form of report warns once and writes the pages the new form writes, where it writes them."""
    mooring_path = _old_tree(tmp_path)
    target = tmp_path / "pages"
    paths = new_paths(mooring_path, target)
    serials = {"serials": ["2941"]} if "raw_dir" in key else {}
    page = _quiet(lambda: oa.report(mooring_path, paths, **serials, **_PAGES))
    assert page.parent == paths.report_mooring_dir(_NAME)
    where = page.parent
    expected = _outputs(where)
    if "raw_dir" in key:
        assert "raw file: dune2_1_2026/microcat/" in expected[page.name]
    assert _warned_once(lambda: old(mooring_path, target), key) == page
    assert _outputs(where) == expected


def test_report_recovery_and_array_old_forms_write_the_same_pages(tmp_path):
    """The recovery table beside the YAML and the array index under the processed folder, as before."""
    mooring_path = _old_tree(tmp_path)
    folder = mooring_path.parent
    table = _quiet(
        lambda: oa.report(
            mooring_path, _paths(mooring_path, report_dir=folder), kind="recovery"
        )
    )
    assert table == folder / f"{_NAME}_recovery_table.html"
    expected = table.read_text(encoding="utf-8")
    again = _warned_once(
        lambda: oa.report(mooring_path, kind="recovery", force=True),
        "a verb called without paths",
    )
    assert again == table and table.read_text(encoding="utf-8") == expected

    array_path = Path(shutil.copy(_ARRAY, tmp_path / "proc"))
    proc = tmp_path / "proc"
    index = _quiet(
        lambda: oa.report(
            paths=oa.Paths(proc_dir=proc), kind="array", array_path=array_path
        )
    )
    assert index.parent == proc
    # The array YAML as the first argument is the same call.
    assert (
        _quiet(
            lambda: oa.report(
                array_path, oa.Paths(proc_dir=proc), kind="array", force=True
            )
        )
        == index
    )
    expected = _outputs(proc)
    for key, call in (
        (
            "a verb called without paths",
            lambda: oa.report(array_path, kind="array", force=True),
        ),
        (
            "oceanarray.report(proc_dir=)",
            lambda: oa.report(array_path, kind="array", proc_dir=proc, force=True),
        ),
    ):
        assert _warned_once(call, key) == index
        assert _outputs(proc) == expected
    with pytest.raises(TypeError, match="twice"):
        oa.report(
            array_path, oa.Paths(proc_dir=proc), kind="array", array_path=array_path
        )
    with pytest.raises(TypeError, match="not array_path"):
        oa.report(mooring_path, kind="mooring", array_path=array_path)
    elsewhere = tmp_path / "index"
    new = _quiet(
        lambda: oa.report(
            array_path, oa.Paths(proc_dir=proc, report_dir=elsewhere), kind="array"
        )
    )
    old = _warned_once(
        lambda: oa.report(array_path, kind="array", report_dir=elsewhere, force=True),
        "oceanarray.report(report_dir=)",
    )
    assert new == old == elsewhere / new.name


# ── export ──────────────────────────────────────────────────────────────────
_EXPORT = [
    (
        "oceanarray.export(out_dir=)",
        lambda y, t: oa.export(y, out_dir=t, overwrite=True),
        lambda y, t: oa.Paths(proc_dir=y.parent.parent, export_dir=t),
    ),
    (
        "oceanarray.export(mooring_dir=)",
        lambda y, _t: oa.export(y, mooring_dir=y.parent, overwrite=True),
        lambda y, _t: _paths(y),
    ),
    (
        "oceanarray.export(array=)",
        lambda y, _t: oa.export(y, _paths(y), array=_ARRAY, overwrite=True),
        lambda y, _t: _paths(y),
    ),
    (
        "a mooring folder in place of a verb's mooring_path",
        lambda y, _t: oa.export(y.parent, overwrite=True),
        lambda y, _t: _paths(y),
    ),
    (
        "a mooring dict in place of a mooring_path",
        lambda y, _t: oa.export(mooring.load(y), mooring_dir=y.parent, overwrite=True),
        lambda y, _t: _paths(y),
    ),
    (
        "oceanarray.export(mooring=)",
        lambda y, _t: oa.export(mooring=y, paths=_paths(y), overwrite=True),
        lambda y, _t: _paths(y),
    ),
    (
        "a verb called without paths",
        lambda y, _t: oa.export(y, overwrite=True),
        lambda y, _t: _paths(y),
    ),
]


@pytest.mark.parametrize(
    ("key", "old", "new_paths"), _EXPORT, ids=[c[0] for c in _EXPORT]
)
def test_export_old_forms_write_the_same_files(tmp_path, key, old, new_paths):
    """Each old form of export warns once and writes the files the new form writes."""
    mooring_path = _old_tree(tmp_path)
    paths = new_paths(mooring_path, tmp_path / "os")
    array = {"array_path": _ARRAY} if key.endswith("(array=)") else {}
    written = _quiet(lambda: oa.export(mooring_path, paths, overwrite=True, **array))
    assert {p.parent for p in written} == {paths.export_dir}
    expected = _outputs(paths.export_dir)
    assert _warned_once(lambda: old(mooring_path, tmp_path / "os"), key) == written
    assert _outputs(paths.export_dir) == expected


# ── folders missing from paths, and paths with the old keywords ─────────────
@pytest.mark.parametrize(
    ("field", "call"),
    [
        ("proc_dir", lambda y: oa.process(y, oa.Paths(raw_dir=_RAW), stages=2)),
        (
            "raw_dir",
            lambda y: oa.process(y, oa.Paths(proc_dir=y.parent.parent), stages=1),
        ),
        ("proc_dir", lambda y: oa.run(y, oa.Paths(raw_dir=_RAW))),
        ("report_dir", lambda y: oa.report(y, oa.Paths(proc_dir=y.parent.parent))),
        (
            "report_dir",
            lambda y: oa.report(y, oa.Paths(proc_dir=y.parent.parent), kind="recovery"),
        ),
        ("proc_dir", lambda _y: oa.report(_ARRAY, oa.Paths(), kind="array")),
        ("export_dir", lambda y: oa.export(y, oa.Paths(proc_dir=y.parent.parent))),
        ("proc_dir", lambda y: oa.export(y, oa.Paths(export_dir=y.parent))),
    ],
)
def test_a_folder_missing_from_paths_is_named(tmp_path, field, call):
    """A verb that needs a folder paths does not give raises a MooringError naming the field."""
    mooring_path = _old_tree(tmp_path)
    with pytest.raises(MooringError, match=rf"no {field} given"):
        _quiet(lambda: call(mooring_path))


@pytest.mark.parametrize(
    "call",
    [
        lambda y: oa.process(y, _paths(y), raw_dir=_RAW),
        lambda y: oa.run(y, _paths(y), mooring_dir=y.parent),
        lambda y: oa.report(y, _paths(y), out_dir=y.parent),
        lambda y: oa.report(_ARRAY, _paths(y), kind="array", proc_dir=y.parent),
        lambda y: oa.export(y, _paths(y), out_dir=y.parent),
    ],
)
def test_paths_and_an_old_folder_keyword_are_refused(tmp_path, call):
    """The folders come from paths or from the old keywords, never both."""
    mooring_path = _old_tree(tmp_path)
    with pytest.raises(TypeError, match="give every folder in paths"):
        call(mooring_path)


# ── the pages read the raw folder stage 1 reads ─────────────────────────────
def _raw_file_row(page: Path) -> str:
    """Return the instrument page's row for its raw file (2941), tags and all."""
    text = (page.parent / "instrument" / f"{_NAME}_2941_report.html").read_text(
        encoding="utf-8"
    )
    found = re.search(r"2941_recovery\.asc</td>\s*<td[^>]*>[^<]*</td>", text)
    assert found, "no raw-file row on the instrument page"
    return found.group(0)


def test_report_reads_a_moorings_own_raw_folder(tmp_path):
    """With an exact raw folder for the mooring, the pages find the file stage 1 read."""
    mooring_path = _old_tree(tmp_path)
    moved = tmp_path / "raw_moved" / "dune2_recovered"
    shutil.copytree(_RAW / _NAME, moved)
    paths = oa.Paths(
        proc_dir=mooring_path.parent.parent, report_dir=tmp_path / "pages"
    ).with_mooring(_NAME, raw_dir=moved)
    _quiet(lambda: oa.process(mooring_path, paths, stages=1, serials=["2941"]))
    page = _quiet(lambda: oa.report(mooring_path, paths, serials=["2941"], **_PAGES))
    assert re.search(r"\d+ KB", _raw_file_row(page))
    assert "raw file: dune2_recovered/microcat/2941_recovery.asc" in page.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("how", ["api", "cli"])
def test_report_from_inside_the_mooring_folder_finds_the_raw_file(
    tmp_path, monkeypatch, how
):
    """An old call made in the mooring's folder, with a relative raw_dir, names the file it found."""
    from oceanarray.cli import build_parser

    mooring_path = _old_tree(tmp_path)
    shutil.copytree(_RAW / _NAME, tmp_path / "raw" / _NAME)
    monkeypatch.chdir(mooring_path.parent)
    if how == "api":
        _warned_once(
            lambda: oa.report(
                mooring_path.name, raw_dir="../../raw", serials=["2941"], **_PAGES
            ),
            "oceanarray.report(raw_dir=)",
        )
    else:
        args = build_parser().parse_args(
            ["report", ".", "--raw-dir", "../../raw", "--only", "2941"]
        )
        assert args.func(args) == 0
    page = mooring_path.parent / "report" / f"{_NAME}_report.html"
    assert re.search(r"\d+ KB", _raw_file_row(page))


# ── a second argument that is not a Paths ───────────────────────────────────
_VERBS = {
    "process": lambda y, p: oa.process(y, p, stages=3),
    "run": lambda y, p: oa.run(y, p, from_stage=3),
    "report": lambda y, p: oa.report(y, p),
    "report_array": lambda _y, p: oa.report(_ARRAY, p, kind="array"),
    "export": lambda y, p: oa.export(y, p, dry_run=True),
}


@pytest.mark.parametrize("verb", list(_VERBS))
@pytest.mark.parametrize("kind", ["Path", "str", "dict"])
def test_a_second_argument_that_is_not_paths_is_refused(tmp_path, verb, kind):
    """A folder or a dict where paths goes is a TypeError naming what was given."""
    mooring_path = _old_tree(tmp_path)
    given = {"Path": tmp_path, "str": str(tmp_path), "dict": {"raw_dir": "raw"}}[kind]
    name = verb.partition("_")[0]
    expected = f"{name}() paths must be an oceanarray.Paths, got {type(given).__name__}"
    with pytest.raises(TypeError, match=re.escape(expected)):
        _VERBS[verb](mooring_path, given)


def test_process_still_takes_the_pre_0_6_stage_second(tmp_path):
    """process(name, 3, proc_dir=...) is still the pre-0.6.0 form, with its own warning."""
    mooring_path = _old_tree(tmp_path)
    with pytest.warns(DeprecationWarning, match=r"process\(name, stage, proc_dir"):
        assert oa.process(_NAME, 3, proc_dir=mooring_path.parent.parent)


@pytest.mark.parametrize(
    "extra", [{"stage": 3}, {"proc_dir": "proc"}, {"serial": ["2941"]}]
)
def test_process_refuses_the_pre_0_6_keywords_with_paths(tmp_path, extra):
    """stage=, proc_dir= or an unknown keyword beside paths is a TypeError, not the old form."""
    mooring_path = _old_tree(tmp_path)
    with pytest.raises(
        TypeError, match=f"process\\(\\) got paths and {next(iter(extra))}="
    ):
        oa.process(mooring_path, _paths(mooring_path), **extra)


# ── each old call warns once ────────────────────────────────────────────────
_ONCE = [
    (
        "oceanarray.process(mooring=)",
        lambda y: oa.process(mooring=y, stages=3),
    ),
    ("oceanarray.run(mooring=)", lambda y: oa.run(mooring=y, from_stage=3)),
    ("oceanarray.report(mooring=)", lambda y: oa.report(mooring=y, **_PAGES)),
    (
        "oceanarray.report(mooring=)",
        lambda y: oa.report(mooring=_ARRAY, kind="array", proc_dir=y.parent.parent),
    ),
    (
        "oceanarray.export(array=)",
        lambda y: oa.export(y, array=_ARRAY, dry_run=True),
    ),
    (
        "oceanarray.export(mooring=)",
        lambda y: oa.export(mooring=y, array=_ARRAY, dry_run=True),
    ),
    (
        "oceanarray.mooring.load(mooring=)",
        lambda y: mooring.load(mooring=mooring.load(y)),
    ),
    (
        "oceanarray.mooring.check(mooring=)",
        lambda y: mooring.check(mooring=mooring.load(y)),
    ),
    (
        "oceanarray.mooring.load(mooring=)",
        lambda y: mooring.load(mooring=_NAME, proc_dir=y.parent.parent),
    ),
]
_ONCE_IDS = [
    "process",
    "run",
    "report",
    "report-array",
    "export-array",
    "export-mooring-array",
    "load-dict",
    "check-dict",
    "load-name",
]


@pytest.mark.parametrize(("key", "call"), _ONCE, ids=_ONCE_IDS)
def test_each_old_call_warns_once(tmp_path, key, call):
    """A call using several old forms warns once, for the first, pointing at the caller."""
    mooring_path = _old_tree(tmp_path)
    _warned_once(lambda: call(mooring_path), key)


# ── a mooring dict with paths, and the folders report and run need ──────────
def test_a_mooring_dict_with_paths_uses_the_yaml_in_yaml_dir(tmp_path):
    """report(mooring_dict, paths) reads the mooring YAML from paths.yaml_dir."""
    mooring_path = _old_tree(tmp_path)
    yaml_dir = tmp_path / "yamls"
    yaml_dir.mkdir()
    mooring_path = Path(shutil.move(mooring_path, yaml_dir / mooring_path.name))
    paths = oa.Paths(
        yaml_dir=yaml_dir, proc_dir=tmp_path / "proc", report_dir=tmp_path / "pages"
    )
    table = _warned_once(
        lambda: oa.report(mooring.load(mooring_path), paths, kind="recovery"),
        "a mooring dict in place of a mooring_path",
    )
    assert table == tmp_path / "pages" / _NAME / f"{_NAME}_recovery_table.html"
    assert table.is_file()


def test_report_makes_its_folder(tmp_path):
    """The recovery table is written into a report folder that did not exist."""
    mooring_path = _old_tree(tmp_path)
    paths = oa.Paths(proc_dir=tmp_path / "proc", report_dir=tmp_path / "new" / "pages")
    table = _quiet(lambda: oa.report(mooring_path, paths, kind="recovery"))
    assert table.parent == tmp_path / "new" / "pages" / _NAME
    assert table.is_file()


def test_run_with_report_and_no_report_folder_fails_before_processing(tmp_path):
    """run(report=True) without a report folder raises before writing a file."""
    mooring_path = _old_tree(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in mooring_path.parent.rglob("*")}
    with pytest.raises(MooringError, match="no report_dir given"):
        oa.run(mooring_path, oa.Paths(proc_dir=tmp_path / "proc"), report=True)
    assert {p: p.stat().st_mtime_ns for p in mooring_path.parent.rglob("*")} == before
