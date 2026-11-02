"""``oceanarray.report`` and ``reports.summary`` on a copy of the committed dune2 processed tree."""

import shutil
from pathlib import Path

import numpy as np
import pytest

import oceanarray as oa
from oceanarray import paths
from oceanarray.exceptions import MooringError
from oceanarray.reports import report, report_paths, summary

_PROC = Path(__file__).parent.parent / "fixtures" / "proc"
_NAME = "dune2_1_2026"


@pytest.fixture
def tree(tmp_path) -> Path:
    """A copy of the processed fixture tree; returns the mooring directory."""
    shutil.copytree(_PROC / _NAME, tmp_path / _NAME)
    shutil.copy(_PROC / "MIXSED2026.array.yaml", tmp_path)
    return tmp_path / _NAME


def test_report_builds_every_page_where_report_paths_says(tree):
    """The summary, stack, grid and instrument pages land at the paths report_paths gives."""
    page = report(tree / f"{_NAME}.mooring.yaml")
    where = report_paths(tree)
    assert page == where["summary"] == tree / "report" / f"{_NAME}_report.html"
    for key in ("summary", "stack", "grid"):
        assert where[key].exists(), key
    assert sorted(p.name for p in where["instrument_dir"].glob("*.html")) == [
        f"{_NAME}_2941_report.html",
        f"{_NAME}_9920_report.html",
    ]


@pytest.mark.parametrize("where", ["out_dir", "report_dir"])
def test_report_paths_follow_out_dir_and_report_dir(tree, tmp_path, where):
    """With out_dir or a central report_dir the pages and report_paths agree."""
    target = tmp_path / "elsewhere"
    page = report(tree, stack=False, grid=False, instruments=False, **{where: target})
    assert page == report_paths(tree, **{where: target})["summary"]
    expected = target if where == "out_dir" else target / _NAME
    assert page.parent == expected


def test_cli_dry_run_prints_the_paths_report_writes(tree, capsys):
    """oceanarray report --dry-run lists the same page paths report() writes to."""
    from oceanarray.cli import build_parser

    args = build_parser().parse_args(
        ["report", _NAME, "--proc-dir", str(tree.parent), "--all", "--dry-run"]
    )
    assert args.func(args) == 0
    out = capsys.readouterr().out
    where = report_paths(tree)
    for key in ("summary", "stack", "grid", "pdf"):
        assert str(where[key]) in out, key
    assert str(where["instrument_dir"] / f"{_NAME}_2941_report.html") in out


def test_a_fresh_summary_does_not_stop_the_stack_page(tree):
    """The summary page is newer than its inputs, the stack page is missing: it is still built."""
    report(tree, stack=False, grid=False, instruments=False)
    where = report_paths(tree)
    assert not where["stack"].exists()
    report(tree, stack=True, grid=False, instruments=False)
    assert where["stack"].exists()


def test_recovery_table_goes_beside_the_description(tree):
    """kind="recovery" writes the recovery table in the mooring directory by default."""
    page = report(tree, kind="recovery")
    assert page == tree / f"{_NAME}_recovery_table.html"
    assert page.exists()


def test_array_index_from_an_array_description(tree):
    """kind="array" builds the array index from an *.array.yaml under the processed root."""
    page = report(tree.parent / "MIXSED2026.array.yaml", kind="array")
    assert page.exists()
    assert page.name.endswith("_array_report.html")


def test_report_refuses_an_unknown_kind_and_a_pdf_it_cannot_make(tree):
    """A bad kind is a ValueError; a PDF without the pdf extra is a MooringError."""
    with pytest.raises(ValueError, match="kind="):
        report(tree, kind="index")  # type: ignore[arg-type]
    try:
        import weasyprint  # noqa: F401
    except ImportError:
        with pytest.raises(MooringError, match="no PDF"):
            report(tree, stack=False, grid=False, instruments=False, pdf=True)


def test_sig_level_is_restored_after_the_report(tree):
    """A sig_level override applies to the call only."""
    import oceanarray.reports._mooring as mooring_page
    from oceanarray.config import parameters as params

    before = params.SIGMA_GRID.copy()
    seen = []
    real = mooring_page.generate_grid_page

    def _spy(*a, **k):
        seen.append(params.SIGMA_GRID.copy())
        return real(*a, **k)

    mooring_page.generate_grid_page = _spy
    try:
        report(tree, stack=False, grid=True, instruments=False, sig_level=[27.8, 27.6])
    finally:
        mooring_page.generate_grid_page = real
    assert [list(v) for v in seen] == [[27.6, 27.8]]
    assert (before == params.SIGMA_GRID).all()


def test_skip_existing_keeps_a_fresh_summary(tree):
    """With every page there, skip_existing rewrites nothing, the summary included."""
    report(tree, stack=False, grid=False)
    page = report_paths(tree)["summary"]
    page.write_text("kept", encoding="utf-8")
    report(tree, stack=False, grid=False, skip_existing=True)
    assert page.read_text(encoding="utf-8") == "kept"


def test_a_new_stack_file_rebuilds_the_summary(tree):
    """The summary shows what is in the stack, so a newer stack file makes it stale."""
    import os

    page = report(tree, stack=False, grid=False, instruments=False)
    page.write_text("old", encoding="utf-8")
    stack_nc = paths.product_file(tree, _NAME, "stack")
    later = page.stat().st_mtime + 10
    os.utime(stack_nc, (later, later))
    report(tree, stack=False, grid=False, instruments=False)
    assert page.read_text(encoding="utf-8") != "old"


def test_a_loaded_description_is_the_one_reported(tree):
    """A dict is used as given, not replaced by the file on disk."""
    from oceanarray import mooring as _mooring

    report(tree, stack=False, grid=False, instruments=False)  # a fresh page first
    cfg = _mooring.load(tree / f"{_NAME}.mooring.yaml")
    cfg["waterdepth"] = 4321
    page = report(cfg, mooring_dir=tree, stack=False, grid=False, instruments=False)
    assert "4321" in page.read_text(encoding="utf-8")


def test_a_folder_named_differently_from_the_mooring(tree, capsys):
    """The folder can be called anything: pages are named, and files found, from name:."""
    from oceanarray.cli import build_parser

    folder = tree.rename(tree.parent / "MIXSED_mooring_2")
    page = report(folder / f"{_NAME}.mooring.yaml")
    where = report_paths(folder)
    assert page == where["summary"] == folder / "report" / f"{_NAME}_report.html"
    for key in ("summary", "stack", "grid"):
        assert where[key].exists(), key
    assert len(list(where["instrument_dir"].glob(f"{_NAME}_*_report.html"))) == 2
    assert "s/n 2941" in summary(folder)
    assert report(folder, kind="recovery") == folder / f"{_NAME}_recovery_table.html"

    args = build_parser().parse_args(
        ["report", folder.name, "--proc-dir", str(folder.parent), "--all", "--dry-run"]
    )
    capsys.readouterr()
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert str(where["summary"]) in out
    assert str(where["instrument_dir"] / f"{_NAME}_2941_report.html") in out


def test_summary_lists_each_instrument_from_the_description(tree):
    """One line per instrument, from its stage-3 file, with the stage-1 count beside it."""
    text = summary(tree)
    assert "s/n 2941" in text and "s/n 9920" in text
    assert text.count("stage3") == 2
    assert "? raw" not in text


def test_run_with_report_returns_the_summary_page_last(tree, monkeypatch):
    """run(report=True) builds the report after the grid and returns its summary page."""
    pages = []

    def _fake_report(*_a, **k):
        pages.append((k.get("mooring_dir"), k.get("serials")))
        return report_paths(tree)["summary"]

    import oceanarray.reports as reports

    monkeypatch.setattr(reports, "report", _fake_report)
    files = oa.run(
        tree, stages=3, serials=(s for s in ["2941"]), dt_seconds=3600, report=True
    )
    assert files[-1] == report_paths(tree)["summary"]
    assert files[-3:-1] == [
        paths.product_file(tree, _NAME, "stack"),
        paths.product_file(tree, _NAME, "grid"),
    ]
    assert pages == [(tree, ["2941"])]  # a generator of serials is read once


def test_a_stage_2_stack_says_so_in_the_amber_note(tree):
    """A stack built from stage-2 files: the page names them, and why the aquadopp has no velocity."""
    oa.run(tree, stages=2, force=True, dt_seconds=3600, stack_stage=2)
    report(tree, stack=True, grid=False, instruments=False, force=True)
    html = report_paths(tree)["stack"].read_text(encoding="utf-8")
    assert "Stacked from stage 2 (stage 2 requested with stack_stage=2)" in html
    assert "s/n&nbsp;2941, 9920" in html
    # the dune2 aquadopp is in beam coordinates: no east/north in the stack
    assert "instrument coordinates (beam or XYZ)" in html
    report(tree, stack=False, grid=True, instruments=False, force=True)
    grid_html = report_paths(tree)["grid"].read_text(encoding="utf-8")
    assert "stage 2 requested with stack_stage=2" in grid_html
    assert "instrument coordinates (beam or XYZ)" in grid_html  # same note as the stack


def test_stage_2_velocities_are_flagged_as_magnetic_north(tree):
    """A stage-2 level with east/north velocity: the note says no declination, not 'position missing'."""
    import xarray as xr

    stack_nc = paths.product_file(tree, _NAME, "stack")
    with xr.open_dataset(stack_nc) as ds:
        ds = ds.load()
    # as if 9920 had been stacked from stage 2 with east/north velocities
    ds.attrs["stage2_instruments"] = "9920"
    ds.attrs["stage2_magnetic_north"] = "9920"
    level = ds["serial"].values.tolist().index("9920")
    decl = ds["magnetic_declination"].values.copy()
    decl[level] = np.nan  # stage 2 has no declination
    ds["magnetic_declination"] = ("N_LEVELS", decl, ds["magnetic_declination"].attrs)
    stack_nc.unlink()
    ds.to_netcdf(stack_nc)
    report(tree, stack=True, grid=False, instruments=False, force=True)
    html = report_paths(tree)["stack"].read_text(encoding="utf-8")
    assert "no magnetic declination correction" in html
    assert "Magnetic declination not applied to s/n&nbsp;9920" in html
    assert "latitude/longitude are missing" not in html
