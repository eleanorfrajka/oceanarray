"""Tests for oceanarray.paths — filename/path conventions.

Regression coverage for the serial-cleaning divergence: before consolidation,
``_safe_serial`` existed in four modules and two of them did not split on the
comma, so a serial like ``"16430, R01-024"`` produced ``"16430R01-024"`` in
stage1/stage3/report filenames but ``"16430"`` via the stack path — the same
instrument landing under two different filenames.
"""

from pathlib import Path

import pytest

from oceanarray.paths import (
    LegacyLayoutError,
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


class TestResolveReportDir:
    """Report output-dir resolution: outdir > report_dir/<mooring> > proc/report."""

    def test_outdir_wins(self) -> None:
        """An explicit outdir takes precedence over everything else."""
        assert resolve_report_dir("M1", "/out", "/central", "/proc") == Path("/out")

    def test_report_dir_nests_mooring(self) -> None:
        """With no outdir, a central report_dir nests each mooring below it."""
        assert resolve_report_dir("M1", None, "/central", "/proc") == Path(
            "/central/M1"
        )

    def test_default_is_proc_mooring_report(self) -> None:
        """With neither, the default is proc_root/<mooring>/report."""
        assert resolve_report_dir("M1", None, None, "/proc") == Path("/proc/M1/report")


class TestResolvePdfPath:
    """PDF output-path resolution: pdf_dir/<mooring>_report.pdf > beside the HTML."""

    def test_default_is_beside_html(self) -> None:
        """With no pdf_dir, the PDF sits beside the HTML pages."""
        assert resolve_pdf_path("M1", None, "/proc/M1/report") == Path(
            "/proc/M1/report/M1_report.pdf"
        )

    def test_pdf_dir_wins(self) -> None:
        """A pdf_dir collects every mooring's PDF under one directory."""
        assert resolve_pdf_path("M1", "/shared", "/proc/M1/report") == Path(
            "/shared/M1_report.pdf"
        )

    def test_pure_no_directory_created(self, tmp_path) -> None:
        """The resolver is side-effect-free: it never creates pdf_dir."""
        target = tmp_path / "does_not_exist"
        resolve_pdf_path("M1", target, tmp_path)
        assert not target.exists()


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


@pytest.mark.needs_seasenselib
def test_stage1_call_site_agrees(tmp_path):
    """Stage 1 names a comma serial's output by the primary serial, as every stage does.

    Separated from :func:`test_all_call_sites_agree` because importing stage1
    pulls in the optional ``seasenselib`` dependency.
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
        proc, "dune2_1_2026", "microcat", safe_serial("2941, R01-024"), 1
    )
    assert expected.name == "dune2_1_2026_2941_stage1.nc"
    assert expected.exists()


def test_mooring_and_raw_dirs():
    """Folder helpers append the mooring name to the cruise-level root."""
    assert mooring_proc_dir("/data/proc", "dsG3") == Path("/data/proc/dsG3")
    assert raw_mooring_dir("/data/raw", "dsG3") == Path("/data/raw/dsG3")
    # str and Path roots behave identically
    assert mooring_proc_dir(Path("/data/proc"), "dsG3") == mooring_proc_dir(
        "/data/proc", "dsG3"
    )


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
    assert stage_output_name(mooring, serial, stage, tag) == expected


def test_require_current_layout_ok_when_current(tmp_path):
    """No error when the current-layout mooring dir exists."""
    (tmp_path / "dsG3").mkdir()
    assert require_current_layout(tmp_path, "dsG3") is None


def test_require_current_layout_ok_when_empty(tmp_path):
    """No error when neither layout is present — an empty run is not this check's job."""
    assert require_current_layout(tmp_path, "dsG3") is None


def test_require_current_layout_warns_when_both_present(tmp_path, capsys):
    """Current + stale legacy dir both present → warn (don't fail); legacy ignored."""
    (tmp_path / "dsG3").mkdir()
    (tmp_path / "moor" / "proc" / "dsG3").mkdir(parents=True)
    assert require_current_layout(tmp_path, "dsG3") is None
    err = capsys.readouterr().err
    assert "both the current" in err and "legacy" in err


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
    p = stage_file(tmp_path, "dune2_1_2026", "microcat", "2941", 3)
    assert p == tmp_path / "microcat" / "dune2_1_2026_2941_stage3.nc"


def test_stage_file_cleans_the_serial_and_applies_the_tag(tmp_path):
    """The serial is cleaned via safe_serial and the adcp-matlab tag is inserted."""
    p = stage_file(tmp_path, "m", "adcp", "9920*", 1, tag="_000")
    assert p.name == "m_9920_000_stage1.nc"


def test_product_file_names_stack_and_grid(tmp_path):
    """product_file builds <mooring_dir>/<name>_<product>.nc for stack and grid."""
    assert product_file(tmp_path, "m", "stack") == tmp_path / "m_stack.nc"
    assert product_file(tmp_path, "m", "grid") == tmp_path / "m_grid.nc"


def test_product_file_rejects_an_unknown_product(tmp_path):
    """An unknown product name raises, naming the valid set."""
    with pytest.raises(ValueError, match="unknown product"):
        product_file(tmp_path, "m", "merge")


def test_stage_and_product_patterns_share_one_spelling():
    """The suffix, glob and full-path helpers agree, so a suffix changes in one place."""
    from oceanarray import paths

    stage_path = paths.stage_file("/m", "dune2_1_2026", "microcat", "2941", 3)
    assert stage_path.name.endswith(paths.stage_suffix(3))
    assert stage_path.match(paths.stage_glob(3))
    grid_path = paths.product_file("/m", "dune2_1_2026", "grid")
    assert grid_path.name == "dune2_1_2026_grid.nc"
    assert grid_path.match(paths.product_glob("grid"))
    with pytest.raises(ValueError, match="unknown product"):
        paths.product_glob("raw")
