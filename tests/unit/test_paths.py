"""Tests for oceanarray.paths — filename/path conventions.

Regression coverage for the serial-cleaning divergence: before consolidation,
``_safe_serial`` existed in four modules and two of them did not split on the
comma, so a serial like ``"16430, R01-024"`` produced ``"16430R01-024"`` in
stage1/stage3/report filenames but ``"16430"`` via the stack path — the same
instrument landing under two different filenames.
"""

import inspect
import logging
import re
import shutil
import warnings
from pathlib import Path

import pytest

from oceanarray import _deprecated
from oceanarray.paths import (
    LegacyLayoutError,
    Paths,
    mooring_proc_dir,
    product_file,
    raw_mooring_dir,
    require_current_layout,
    resolve_pdf_path,
    resolve_report_dir,
    safe_serial,
    stage_file,
    stage_output_name,
)

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_NAME = "dune2_1_2026"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A copy of the dune2_1_2026 raw and processed files, as ``raw/`` and ``proc/``."""
    shutil.copytree(_FIXTURES / "raw" / _NAME, tmp_path / "raw" / _NAME)
    shutil.copytree(_FIXTURES / "proc" / _NAME, tmp_path / "proc" / _NAME)
    return tmp_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("16430, R01-024", "16430"),  # comma → primary token only
        ("16430", "16430"),  # already clean, unchanged
        (12345, "12345"),  # int accepted
        ("ABC*123", "ABC123"),  # illegal filename char stripped
        ("4021, beacon", "4021"),  # comma + annotation dropped
        ("SN-77", "SN-77"),  # hyphen preserved
    ],
)
def test_safe_serial(raw, expected):
    """safe_serial takes the primary comma-token and strips illegal characters."""
    assert safe_serial(raw) == expected


def test_no_private_copies_of_safe_serial():
    """``safe_serial`` lives only in ``paths``: no module keeps a private wrapper of it."""
    import pathlib

    import oceanarray

    root = pathlib.Path(oceanarray.__file__).parent
    copies = [
        str(f.relative_to(root))
        for f in root.rglob("*.py")
        if "def _safe_serial(" in f.read_text(encoding="utf-8")
    ]
    assert copies == []


def test_stage1_call_site_agrees(tmp_path):
    """Stage 1 names a comma serial's output by the primary serial, as every stage does.

    Separated from :func:`test_all_call_sites_agree` because importing stage1
    pulls in ``seasenselib``.
    """
    pytest.importorskip("seasenselib")
    import shutil
    import warnings

    import yaml

    from oceanarray.pipeline.stage1 import MooringProcessor

    fixtures = Path(__file__).parent.parent / "fixtures"
    cfg = yaml.safe_load(
        (fixtures / "proc/dune2_1_2026/dune2_1_2026.mooring.yaml").read_text()
    )
    cfg["clamp"] = [cfg["clamp"][0]]
    cfg["clamp"][0]["serial"] = "2941, R01-024"
    raw = tmp_path / "raw" / "dune2_1_2026" / "microcat"
    raw.mkdir(parents=True)
    shutil.copy(fixtures / "raw/dune2_1_2026/microcat/2941_recovery.asc", raw)
    proc = tmp_path / "proc" / "dune2_1_2026"
    proc.mkdir(parents=True)
    (proc / "dune2_1_2026.mooring.yaml").write_text(yaml.safe_dump(cfg))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert MooringProcessor(
            raw_dir=str(tmp_path / "raw"), proc_dir=str(tmp_path / "proc")
        ).process_mooring("dune2_1_2026", force=True)
    expected = stage_file(
        proc,
        mooring_name="dune2_1_2026",
        instrument="microcat",
        serial=safe_serial("2941, R01-024"),
        stage=1,
    )
    assert expected.name == "dune2_1_2026_2941_stage1.nc"
    assert expected.exists()


@pytest.mark.parametrize(
    ("mooring", "serial", "stage", "tag", "expected"),
    [
        ("dsG3", "16430", 1, "", "dsG3_16430_stage1.nc"),
        ("dsG3", "16430, R01-024", 1, "", "dsG3_16430_stage1.nc"),  # serial cleaned
        ("dsG3", 7507, 2, "", "dsG3_7507_stage2.nc"),  # int serial
        ("wb2", "1234", 3, "_burst", "wb2_1234_burst_stage3.nc"),  # tag
    ],
)
def test_stage_output_name(mooring, serial, stage, tag, expected):
    """Output name follows {mooring}_{serial}{tag}_stage{N}.nc with a cleaned serial."""
    assert stage_output_name(mooring, serial=serial, stage=stage, tag=tag) == expected


def test_require_current_layout_ok_when_current(tmp_path):
    """No error when the current-layout mooring dir exists."""
    (tmp_path / "dsG3").mkdir()
    assert require_current_layout(tmp_path, "dsG3") is None


def test_require_current_layout_ok_when_empty(tmp_path):
    """No error when neither layout is present — an empty run is not this check's job."""
    assert require_current_layout(tmp_path, "dsG3") is None


def test_require_current_layout_warns_when_both_present(tmp_path, caplog):
    """Current + stale legacy dir both present → warn (don't fail); legacy ignored."""
    (tmp_path / "dsG3").mkdir()
    (tmp_path / "moor" / "proc" / "dsG3").mkdir(parents=True)
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        assert require_current_layout(tmp_path, "dsG3") is None
    assert "both the current" in caplog.text and "legacy" in caplog.text


def test_require_current_layout_raises_on_moor_proc(tmp_path):
    """Legacy moor/proc/<mooring> present but current absent → error names the fix."""
    (tmp_path / "moor" / "proc" / "dsG3").mkdir(parents=True)
    with pytest.raises(LegacyLayoutError) as exc:
        require_current_layout(tmp_path, "dsG3")
    # The message must name the directory to point --proc-dir at.
    assert str(tmp_path / "moor" / "proc") in str(exc.value)


def test_require_current_layout_raises_on_proc(tmp_path):
    """The other legacy variant, proc/<mooring>, is also detected and named."""
    (tmp_path / "proc" / "dsG3").mkdir(parents=True)
    with pytest.raises(LegacyLayoutError) as exc:
        require_current_layout(tmp_path, "dsG3")
    assert str(tmp_path / "proc") in str(exc.value)


# ---------------------------------------------------------------------------
# stage_file / product_file
# ---------------------------------------------------------------------------


def test_stage_file_joins_mooring_dir_instrument_and_name(tmp_path):
    """stage_file builds <mooring_dir>/<instrument>/<name>_<serial>_stageN.nc."""
    p = stage_file(
        tmp_path,
        mooring_name="dune2_1_2026",
        instrument="microcat",
        serial="2941",
        stage=3,
    )
    assert p == tmp_path / "microcat" / "dune2_1_2026_2941_stage3.nc"


def test_stage_file_cleans_the_serial_and_applies_the_tag(tmp_path):
    """The serial is cleaned via safe_serial and the adcp-matlab tag is inserted."""
    p = stage_file(
        tmp_path,
        mooring_name="m",
        instrument="adcp",
        serial="9920*",
        stage=1,
        tag="_000",
    )
    assert p.name == "m_9920_000_stage1.nc"


def test_product_file_names_stack_and_grid(tmp_path):
    """product_file builds <mooring_dir>/<name>_<product>.nc for stack and grid."""
    assert (
        product_file(tmp_path, mooring_name="m", product="stack")
        == tmp_path / "m_stack.nc"
    )
    assert (
        product_file(tmp_path, mooring_name="m", product="grid")
        == tmp_path / "m_grid.nc"
    )


def test_product_file_rejects_an_unknown_product(tmp_path):
    """An unknown product name raises, naming the valid set."""
    with pytest.raises(ValueError, match="unknown product"):
        product_file(tmp_path, mooring_name="m", product="merge")


def test_stage_and_product_patterns_share_one_spelling():
    """The suffix, glob and full-path helpers agree, so a suffix changes in one place."""
    from oceanarray import paths

    stage_path = paths.stage_file(
        "/m", mooring_name="dune2_1_2026", instrument="microcat", serial="2941", stage=3
    )
    assert stage_path.name.endswith(paths.stage_suffix(3))
    assert stage_path.match(paths.stage_glob(3))
    grid_path = paths.product_file("/m", mooring_name="dune2_1_2026", product="grid")
    assert grid_path.name == "dune2_1_2026_grid.nc"


# ---------------------------------------------------------------------------
# Deprecated names, positional arguments and keywords (removed in 0.8.0)
# ---------------------------------------------------------------------------


def _warns_once(key: str):
    """Return a context catching exactly the deprecation *key*'s warning."""
    return pytest.warns(DeprecationWarning, match=re.escape(_deprecated.message(key)))


def _check(record, key: str) -> None:
    """One warning, at the caller, naming 0.8.0 and the replacement."""
    assert len(record) == 1
    text = str(record[0].message)
    assert "0.8.0" in text and _deprecated.RENAMED_NEXT[key] in text
    assert record[0].filename == __file__


#: (deprecation key, old call, new call) on the project copy.
_FOLDERS = [
    (
        "oceanarray.paths.mooring_proc_dir",
        lambda root: mooring_proc_dir(root / "proc", _NAME),
        lambda root: Paths(proc_dir=root / "proc").mooring_dir(_NAME),
    ),
    (
        "oceanarray.paths.raw_mooring_dir",
        lambda root: raw_mooring_dir(root / "raw", _NAME),
        lambda root: Paths(raw_dir=root / "raw").raw_mooring_dir(_NAME),
    ),
    (
        "oceanarray.paths.resolve_report_dir",
        lambda root: resolve_report_dir(_NAME, None, root / "reports", root / "proc"),
        lambda root: Paths(report_dir=root / "reports").report_mooring_dir(_NAME),
    ),
    (
        "oceanarray.paths.resolve_pdf_path",
        lambda root: resolve_pdf_path(_NAME, None, root / "reports" / _NAME),
        lambda root: (
            Paths(report_dir=root / "reports").report_mooring_dir(_NAME)
            / f"{_NAME}_report.pdf"
        ),
    ),
]


@pytest.mark.parametrize(("key", "old", "new"), _FOLDERS)
def test_an_old_folder_function_warns_and_gives_the_paths_methods_result(
    project, key, old, new
):
    """Each old folder function warns once at the caller and gives what Paths gives."""
    with _warns_once(key) as record:
        given = old(project)
    _check(record, key)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert given == new(project)


def test_the_old_folders_are_the_fixtures_folders(project):
    """The proc and raw folders the old functions give hold the mooring's files."""
    with pytest.warns(DeprecationWarning):
        assert (
            mooring_proc_dir(project / "proc", _NAME) / f"{_NAME}_stack.nc"
        ).is_file()
        assert (raw_mooring_dir(project / "raw", _NAME) / "microcat").is_dir()


@pytest.mark.parametrize(
    ("args", "kwargs", "expected"),
    [
        (("/out", "/central", "/proc"), {}, "/out"),
        ((None, "/central", "/proc"), {}, "/central/M1"),
        ((None, None, "/proc"), {}, "/proc/M1/report"),
        ((None, None), {"mooring_dir": "/m"}, "/m/report"),
    ],
)
def test_resolve_report_dir_keeps_its_old_precedence(args, kwargs, expected):
    """outdir, then report_dir/<mooring>, then <mooring_dir>/report, then proc_root/<mooring>/report."""
    with pytest.warns(DeprecationWarning):
        assert resolve_report_dir("M1", *args, **kwargs) == Path(expected)


def test_resolve_pdf_path_keeps_its_pdf_dir(tmp_path):
    """A pdf_dir still collects the PDF, and nothing is created."""
    with pytest.warns(DeprecationWarning):
        assert (
            resolve_pdf_path("M1", tmp_path / "pdf", "/r")
            == tmp_path / "pdf/M1_report.pdf"
        )
    assert not (tmp_path / "pdf").exists()


#: (deprecation key, function, old positional arguments after the folder, old
#: keywords, the same call in the new form's keywords)
_NAMES = [
    (
        "oceanarray.paths.stage_file(positional arguments)",
        stage_file,
        (_NAME, "microcat", "2941", 3),
        {},
        {"mooring_name": _NAME, "instrument": "microcat", "serial": "2941", "stage": 3},
    ),
    (
        "oceanarray.paths.stage_file(name=)",
        stage_file,
        (),
        {"name": _NAME, "instrument": "aquadopp", "serial": "9920", "stage": 2},
        {"mooring_name": _NAME, "instrument": "aquadopp", "serial": "9920", "stage": 2},
    ),
    (
        "oceanarray.paths.product_file(positional arguments)",
        product_file,
        (_NAME, "grid"),
        {},
        {"mooring_name": _NAME, "product": "grid"},
    ),
    (
        "oceanarray.paths.product_file(name=)",
        product_file,
        (),
        {"name": _NAME, "product": "stack"},
        {"mooring_name": _NAME, "product": "stack"},
    ),
]


@pytest.mark.parametrize(("key", "func", "args", "kwargs", "new"), _NAMES)
def test_an_old_file_name_call_warns_once_and_finds_the_same_file(
    project, key, func, args, kwargs, new
):
    """An old positional or keyword call warns once and names the same fixture file."""
    folder = project / "proc" / _NAME
    with _warns_once(key) as record:
        given = func(folder, *args, **kwargs)
    _check(record, key)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert given == func(folder, **new)
    assert given.is_file()


@pytest.mark.parametrize(
    ("key", "args", "kwargs"),
    [
        (
            "oceanarray.paths.stage_output_name(positional arguments)",
            (_NAME, "2941", 3),
            {},
        ),
        (
            "oceanarray.paths.stage_output_name(mooring=)",
            (),
            {"mooring": _NAME, "serial": "2941", "stage": 3},
        ),
    ],
)
def test_an_old_stage_output_name_call_warns_once(key, args, kwargs):
    """The old order (mooring, serial, stage) and mooring= still give the stage file name."""
    with _warns_once(key) as record:
        given = stage_output_name(*args, **kwargs)
    _check(record, key)
    assert given == stage_output_name(_NAME, serial="2941", stage=3)
    assert (_FIXTURES / "proc" / _NAME / "microcat" / given).is_file()


def test_old_positional_arguments_before_new_keywords_warn_once(project):
    """Old positional arguments followed by keywords are read in the old order."""
    folder = project / "proc" / _NAME
    key = "oceanarray.paths.stage_file(positional arguments)"
    with _warns_once(key) as record:
        given = stage_file(folder, _NAME, "microcat", serial="2941", stage=1)
    _check(record, key)
    assert given.is_file()


def test_an_old_call_is_never_misread(project):
    """An argument given twice, or one too many, is a TypeError, not a guess."""
    folder = project / "proc" / _NAME
    with pytest.raises(TypeError, match="multiple"):
        product_file(folder, _NAME, mooring_name=_NAME, product="stack")
    with pytest.raises(TypeError, match="multiple"):
        product_file(folder, name=_NAME, mooring_name=_NAME, product="stack")
    with pytest.raises(TypeError, match="at most"):
        product_file(folder, _NAME, "stack", "grid")


def test_the_file_name_functions_take_keywords_after_the_first():
    """The documented signatures are the new ones."""
    for func in (stage_file, product_file, stage_output_name):
        params = list(inspect.signature(func).parameters.values())
        assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params[1:])
        assert "mooring_name" in inspect.signature(func).parameters


@pytest.mark.parametrize(
    ("key", "kwargs"),
    [
        (
            "oceanarray.paths.require_current_layout(proc_root=)",
            {"mooring_name": _NAME},
        ),
        ("oceanarray.paths.require_current_layout(mooring=)", {"proc_dir": None}),
        ("oceanarray.paths.require_current_layout(proc_root=)", {}),
    ],
)
def test_require_current_layout_old_keywords_warn_and_check_the_same(
    tmp_path, key, kwargs
):
    """proc_root= and mooring=, alone or together, warn once and find the old layout as before."""
    shutil.copytree(_FIXTURES / "proc" / _NAME, tmp_path / "moor" / "proc" / _NAME)
    old = {}
    if "proc_dir" not in kwargs:
        old["proc_root"] = tmp_path
    if "mooring_name" not in kwargs:
        old["mooring"] = _NAME
    given = {k: v if v is not None else tmp_path for k, v in kwargs.items()}
    with _warns_once(key) as record, pytest.raises(LegacyLayoutError) as old_error:
        require_current_layout(**given, **old)
    _check(record, key)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        with pytest.raises(LegacyLayoutError) as new_error:
            require_current_layout(tmp_path, _NAME)
    assert str(old_error.value) == str(new_error.value)


def test_cli_names_the_old_layout_under_a_home_folder(tmp_path, monkeypatch):
    """--proc-dir ~ finds the old layout under the home folder, as for its full path."""
    from oceanarray.cli import build_parser
    from oceanarray.cli._common import _mooring_location

    shutil.copytree(_FIXTURES / "proc" / _NAME, tmp_path / "moor" / "proc" / _NAME)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    args = build_parser().parse_args(["report", _NAME, "--proc-dir", "~"])
    with pytest.raises(LegacyLayoutError, match=re.escape(str(tmp_path / "moor"))):
        _mooring_location(args)
