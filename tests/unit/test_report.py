"""Unit tests for oceanarray/reports/ package.

Three test classes:
  TestHtmlHelpers       — pure functions in _html_helpers.py
  TestMooringReport     — MooringReport context-building and end-to-end generation
  TestPageGenerators    — generate_instrument_pages / _stack / _grid with synthetic NC
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
import yaml

from oceanarray.paths import safe_serial
from oceanarray.reports._html_helpers import (
    _format_dt,
    _format_duration,
    _format_size,
    _parse_dt,
    _parse_history,
    _resolve_clock,
    _resolve_diagram_pdf,
    _stage_files,
)
from oceanarray.reports._mooring import MooringReport
from oceanarray.reports._read import _read_nc_metadata, _read_qc_summary

# ---------------------------------------------------------------------------
# NC helper factories (used by TestMooringReport and TestPageGenerators)
# ---------------------------------------------------------------------------


def _write_minimal_instrument_nc(path: Path) -> None:
    """Write a tiny stage1-compatible NC file to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    times = pd.date_range("2024-06-01", periods=100, freq="10min")
    ds = xr.Dataset(
        {
            "temperature": (
                "time",
                np.linspace(5.0, 10.0, 100),
                {
                    "units": "degrees_C",
                    "long_name": "Temperature",
                    "standard_name": "sea_water_temperature",
                },
            ),
            "temperature_qc": ("time", np.ones(100, dtype=np.int8)),
            "pressure": (
                "time",
                np.linspace(100.0, 110.0, 100),
                {"units": "dbar", "long_name": "Pressure"},
            ),
        },
        coords={"time": times},
        attrs={
            "history": "2024-06-01T00:00:00Z: stage1 applied",
            "mooring_name": "TEST_M1",
            "processing_level": "L1",
            "Conventions": "CF-1.13",
        },
    )
    ds.to_netcdf(path)


def _write_minimal_stack_nc(path: Path, n_levels: int = 2, n_time: int = 50) -> None:
    """Write a tiny stack NC file to *path*.

    The stack renderer indexes ``arr[::step, i]`` (time first, N_LEVELS second),
    so the 2-D variables must have dims ``[time, N_LEVELS]``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    times = pd.date_range("2024-06-01", periods=n_time, freq="1h")
    ds = xr.Dataset(
        {
            "temperature": (
                ["time", "N_LEVELS"],
                np.linspace(5.0, 10.0, n_time * n_levels).reshape(n_time, n_levels),
                {"units": "degrees_C"},
            ),
            "pressure": (
                ["time", "N_LEVELS"],
                np.tile(np.linspace(100.0, 500.0, n_levels), (n_time, 1)),
                {"units": "dbar"},
            ),
            "serial": ("N_LEVELS", np.array(["1234", "5678"])),
            "instrument_type": ("N_LEVELS", np.array(["microcat", "microcat"])),
            "hab": ("N_LEVELS", np.array([100.0, 500.0])),
        },
        coords={"time": times},
        attrs={"dt_seconds": 3600, "waterdepth": 1000},
    )
    ds.to_netcdf(path)


def _write_minimal_grid_nc(path: Path, n_pres: int = 5, n_time: int = 50) -> None:
    """Write a tiny grid NC file (pressure × time) to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    times = pd.date_range("2024-06-01", periods=n_time, freq="1h")
    pres = np.linspace(100.0, 900.0, n_pres)
    ds = xr.Dataset(
        {
            "temperature": (
                ["pressure", "time"],
                np.random.default_rng(0).uniform(5, 10, (n_pres, n_time)),
                {"units": "degrees_C", "long_name": "Temperature"},
            ),
        },
        coords={"time": times, "pressure": pres},
        attrs={"dt_seconds": 3600},
    )
    ds.to_netcdf(path)


# ---------------------------------------------------------------------------
# Class 1: pure helper functions
# ---------------------------------------------------------------------------


class TestHtmlHelpers:
    """Tests for pure functions in _html_helpers.py."""

    # safe_serial
    def test_safe_serial_strips_slash_and_space(self):
        assert safe_serial("AB/CD 12") == "ABCD12"

    def test_safe_serial_preserves_alnum_hyphen(self):
        assert safe_serial("SN-1234") == "SN-1234"

    def test_safe_serial_int_input(self):
        assert safe_serial(7518) == "7518"

    def test_safe_serial_dot_stripped(self):
        assert safe_serial("3.14") == "314"

    # _parse_dt
    def test_parse_dt_iso(self):
        dt = _parse_dt("2024-06-01T12:00:00")
        assert dt is not None
        assert dt.year == 2024 and dt.month == 6 and dt.day == 1

    def test_parse_dt_time_only(self):
        dt = _parse_dt("14:30:00")
        assert dt is not None
        assert dt.hour == 14 and dt.minute == 30

    def test_parse_dt_compact(self):
        dt = _parse_dt("20240601T12:00:00")
        assert dt is not None
        assert dt.year == 2024 and dt.month == 6

    def test_parse_dt_none_on_empty(self):
        assert _parse_dt("") is None
        assert _parse_dt(None) is None

    # _format_dt
    def test_fmt_dt_formats_correctly(self):
        from datetime import datetime

        dt = datetime(2024, 6, 1, 12, 30)
        result = _format_dt(dt)
        assert "2024-06-01" in result
        assert "12:30" in result
        assert "UTC" in result

    def test_fmt_dt_none_returns_dash(self):
        assert _format_dt(None) == "—"

    # _format_duration
    def test_duration_str_two_days(self):
        from datetime import datetime

        s = datetime(2024, 1, 1)
        e = datetime(2024, 1, 3, 6)
        result = _format_duration(s, e)
        assert "2d" in result

    def test_duration_str_none_inputs(self):
        assert _format_duration(None, None) == "—"
        from datetime import datetime

        assert _format_duration(datetime(2024, 1, 1), None) == "—"

    # _resolve_clock
    def test_resolve_clock_option_a_offset(self):
        result = _resolve_clock({"clock_offset": 300})
        assert result["offset_s"] == 300.0
        assert result["has_correction"] is True
        assert result["drift_s"] is None

    def test_resolve_clock_option_b_computer_instrument(self):
        result = _resolve_clock(
            {
                "computer_clock_at_recovery": "14:00:00",
                "instrument_clock_at_recovery": "14:01:30",
            }
        )
        assert result["method"] == "Option B"
        assert result["drift_s"] == pytest.approx(90.0)
        assert result["has_correction"] is True

    def test_resolve_clock_no_correction(self):
        result = _resolve_clock({})
        assert result["has_correction"] is False
        assert result["method"] == "none"

    # _parse_history
    def test_parse_history_empty(self):
        assert _parse_history("") == []

    def test_parse_history_splits_entries(self):
        h = "2024-06-01T00:00:00Z: stage1 applied; 2024-06-02T00:00:00Z: stage2 applied"
        entries = _parse_history(h)
        assert len(entries) == 2
        assert entries[0]["text"] == "stage1 applied"
        assert "2024-06-01" in entries[0]["timestamp"]

    def test_parse_history_no_colon_entry(self):
        entries = _parse_history("some note without timestamp")
        assert len(entries) == 1
        assert entries[0]["timestamp"] == ""

    # _format_size
    def test_fmt_size_bytes(self):
        assert _format_size(500) == "500 B"

    def test_fmt_size_kilobytes(self):
        result = _format_size(2048)
        assert "KB" in result

    def test_fmt_size_megabytes(self):
        result = _format_size(5 * 1024 * 1024)
        assert "MB" in result

    # _read_nc_metadata
    def test_read_nc_metadata_basic(self, tmp_path):
        nc = tmp_path / "test.nc"
        _write_minimal_instrument_nc(nc)
        meta = _read_nc_metadata(nc)
        assert "error" not in meta
        names = [r["name"] for _title, rows in meta["var_groups"] for r in rows]
        assert "temperature" in names
        assert "pressure" in names

    def test_read_nc_metadata_global_attrs(self, tmp_path):
        nc = tmp_path / "test.nc"
        _write_minimal_instrument_nc(nc)
        meta = _read_nc_metadata(nc)
        # The vendored builder returns each global attribute as an
        # (attribute, type, value) triple; a Python string reads as "string".
        triples = {
            attr: (typ, val)
            for _title, rows in meta["global_groups"]
            for attr, typ, val in rows
        }
        assert triples["processing_level"] == ("string", "L1")

    def test_read_nc_metadata_missing_file(self, tmp_path):
        meta = _read_nc_metadata(tmp_path / "nonexistent.nc")
        assert "error" in meta

    # _read_qc_summary
    def test_read_qc_summary_returns_row_for_qc_variable(self, tmp_path):
        nc = tmp_path / "test.nc"
        _write_minimal_instrument_nc(nc)
        rows = _read_qc_summary(nc)
        assert len(rows) == 1
        assert rows[0]["var"] == "temperature"
        assert rows[0]["total"] == 100

    def test_read_qc_summary_flag_counts(self, tmp_path):
        nc = tmp_path / "test.nc"
        _write_minimal_instrument_nc(nc)
        rows = _read_qc_summary(nc)
        flag1 = next(f for f in rows[0]["flags"] if f["flag"] == 1)
        assert flag1["n"] == 100  # all set to 1 in the fixture
        assert flag1["pct"] == 100.0

    def test_read_qc_summary_missing_file(self, tmp_path):
        rows = _read_qc_summary(tmp_path / "nonexistent.nc")
        assert rows == []

    # _stage_files
    def test_stage_files_all_missing(self, tmp_path):
        result = _stage_files(tmp_path, "microcat", "TEST_M1", "1234")
        assert result == {"stage1": False, "stage2": False, "stage3": False}

    def test_stage_files_stage1_present(self, tmp_path):
        nc = tmp_path / "microcat" / "TEST_M1_1234_stage1.nc"
        _write_minimal_instrument_nc(nc)
        result = _stage_files(tmp_path, "microcat", "TEST_M1", "1234")
        assert result["stage1"] is True
        assert result["stage2"] is False
        assert result["stage3"] is False

    def test_resolve_diagram_pdf_none_when_absent(self, tmp_path):
        assert _resolve_diagram_pdf(tmp_path, "TEST_M1") is None

    def test_resolve_diagram_pdf_prefers_canonical(self, tmp_path):
        canonical = tmp_path / "TEST_M1_diagram.pdf"
        canonical.write_bytes(b"%PDF-1.4")
        (tmp_path / "dsG1_single.pdf").write_bytes(b"%PDF-1.4")
        (tmp_path / "TEST_M1_hardware.pdf").write_bytes(b"%PDF-1.4")
        assert _resolve_diagram_pdf(tmp_path, "TEST_M1") == canonical

    def test_resolve_diagram_pdf_single_before_hardware(self, tmp_path):
        single = tmp_path / "dsG1_single.pdf"
        single.write_bytes(b"%PDF-1.4")
        (tmp_path / "TEST_M1_hardware.pdf").write_bytes(b"%PDF-1.4")
        assert _resolve_diagram_pdf(tmp_path, "TEST_M1") == single

    def test_resolve_diagram_pdf_hardware_fallback(self, tmp_path):
        hardware = tmp_path / "TEST_M1_hardware.pdf"
        hardware.write_bytes(b"%PDF-1.4")
        assert _resolve_diagram_pdf(tmp_path, "TEST_M1") == hardware

    def test_resolve_diagram_pdf_ignores_appledouble_sidecar(self, tmp_path):
        (tmp_path / "._dsG1_single.pdf").write_bytes(b"junk")
        real = tmp_path / "TEST_M1_hardware.pdf"
        real.write_bytes(b"%PDF-1.4")
        assert _resolve_diagram_pdf(tmp_path, "TEST_M1") == real


# ---------------------------------------------------------------------------
# Class 2: MooringReport context and generation
# ---------------------------------------------------------------------------


class TestMooringReport:
    """Tests for MooringReport._build_context and .generate()."""

    _YAML = {
        "name": "TEST_M1",
        "deployment_time": "2024-06-01T00:00:00",
        "recovery_time": "2024-09-01T00:00:00",
        "waterdepth": 1000,
        "latitude": 60.0,
        "longitude": -30.0,
        "deployment_cruise": "TEST01",
        "deployment_ship": "RV Test",
        "instruments": [
            {
                "instrument": "microcat",
                "serial": "1234",
                "hab": 100.0,
                "filename": "test.cnv",
                "file_type": "sbe-cnv",
                "clock_offset": 60,
            }
        ],
    }

    @pytest.fixture
    def proc_dir(self, tmp_path):
        """Set up a minimal proc directory with YAML and return the mooring proc path."""
        mooring_proc = tmp_path / "proc" / "TEST_M1"
        mooring_proc.mkdir(parents=True)
        yaml_path = mooring_proc / "TEST_M1.mooring.yaml"
        yaml_path.write_text(yaml.dump(self._YAML))
        return tmp_path

    def test_build_context_has_required_keys(self, proc_dir):
        report = MooringReport(proc_dir=str(proc_dir / "proc"))
        mooring_proc = proc_dir / "proc" / "TEST_M1"
        yaml_path = mooring_proc / "TEST_M1.mooring.yaml"
        ctx = report._build_context("TEST_M1", self._YAML, mooring_proc, yaml_path)
        for key in ("instruments", "stack_exists", "grid_exists"):
            assert key in ctx, f"missing key: {key}"

    def test_build_context_instrument_count(self, proc_dir):
        report = MooringReport(proc_dir=str(proc_dir / "proc"))
        mooring_proc = proc_dir / "proc" / "TEST_M1"
        yaml_path = mooring_proc / "TEST_M1.mooring.yaml"
        ctx = report._build_context("TEST_M1", self._YAML, mooring_proc, yaml_path)
        assert len(ctx["instruments"]) == 1

    def test_build_context_no_stack_or_grid(self, proc_dir):
        report = MooringReport(proc_dir=str(proc_dir / "proc"))
        mooring_proc = proc_dir / "proc" / "TEST_M1"
        yaml_path = mooring_proc / "TEST_M1.mooring.yaml"
        ctx = report._build_context("TEST_M1", self._YAML, mooring_proc, yaml_path)
        assert ctx["stack_exists"] is False
        assert ctx["grid_exists"] is False

    def test_generate_creates_file(self, proc_dir):
        report = MooringReport(proc_dir=str(proc_dir / "proc"))
        out = report.generate("TEST_M1")
        assert out is not None
        assert out.exists()
        assert out.stat().st_size > 0

    def test_generate_html_contains_mooring_name(self, proc_dir):
        report = MooringReport(proc_dir=str(proc_dir / "proc"))
        out = report.generate("TEST_M1")
        assert out is not None
        html = out.read_text(encoding="utf-8")
        assert "TEST_M1" in html

    def test_generate_html_is_valid_html(self, proc_dir):
        report = MooringReport(proc_dir=str(proc_dir / "proc"))
        out = report.generate("TEST_M1")
        assert out is not None
        html = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html or "<html" in html

    def test_generate_skips_if_exists(self, proc_dir):
        report = MooringReport(proc_dir=str(proc_dir / "proc"))
        out1 = report.generate("TEST_M1")
        assert out1 is not None
        mtime1 = out1.stat().st_mtime
        report.generate("TEST_M1", force=False)
        assert out1.stat().st_mtime == mtime1

    def test_generate_force_overwrites(self, proc_dir):
        import time

        report = MooringReport(proc_dir=str(proc_dir / "proc"))
        out = report.generate("TEST_M1")
        assert out is not None
        mtime1 = out.stat().st_mtime
        time.sleep(0.05)
        report.generate("TEST_M1", force=True)
        assert out.stat().st_mtime > mtime1

    def test_generate_missing_yaml_returns_none(self, tmp_path):
        empty_proc = tmp_path / "proc"
        empty_proc.mkdir()
        (empty_proc / "NO_MOORING").mkdir()
        report = MooringReport(proc_dir=str(empty_proc))
        result = report.generate("NO_MOORING")
        assert result is None

    def test_generate_missing_proc_dir_returns_none(self, tmp_path):
        report = MooringReport(proc_dir=str(tmp_path / "proc"))
        result = report.generate("NONEXISTENT")
        assert result is None


# ---------------------------------------------------------------------------
# Class 3: page generators with synthetic NC
# ---------------------------------------------------------------------------


class TestPageGenerators:
    """Smoke tests for generate_instrument_pages, _stack, and _grid."""

    @pytest.fixture
    def mooring_setup(self, tmp_path):
        """Proc directory with YAML + stage1 NC for one instrument."""
        mooring_proc = tmp_path / "proc" / "TEST_M1"
        mooring_proc.mkdir(parents=True)
        out_dir = mooring_proc / "report"
        out_dir.mkdir()

        cfg = {
            "name": "TEST_M1",
            "deployment_time": "2024-06-01T00:00:00",
            "recovery_time": "2024-09-01T00:00:00",
            "waterdepth": 1000,
            "instruments": [
                {
                    "instrument": "microcat",
                    "serial": "1234",
                    "hab": 100.0,
                    "filename": "test.cnv",
                    "file_type": "sbe-cnv",
                }
            ],
        }
        (mooring_proc / "TEST_M1.mooring.yaml").write_text(yaml.dump(cfg))

        nc_path = mooring_proc / "microcat" / "TEST_M1_1234_stage1.nc"
        _write_minimal_instrument_nc(nc_path)

        return {
            "tmp_path": tmp_path,
            "proc_dir": mooring_proc,
            "out_dir": out_dir,
            "cfg": cfg,
        }

    # generate_instrument_pages
    def test_generate_instrument_pages_creates_html(self, mooring_setup):
        from oceanarray.reports._instrument import generate_instrument_pages

        setup = mooring_setup
        report = MooringReport(proc_dir=str(setup["proc_dir"].parent))
        ctx = report._build_context(
            "TEST_M1",
            setup["cfg"],
            setup["proc_dir"],
            setup["proc_dir"] / "TEST_M1.mooring.yaml",
            setup["out_dir"],
        )
        generate_instrument_pages(
            "TEST_M1",
            ctx["instruments"],
            setup["cfg"],
            setup["proc_dir"],
            setup["out_dir"],
            False,
        )
        pages = list((setup["out_dir"] / "instrument").glob("*.html"))
        assert len(pages) >= 1

    def test_generate_instrument_pages_html_contains_serial(self, mooring_setup):
        from oceanarray.reports._instrument import generate_instrument_pages

        setup = mooring_setup
        report = MooringReport(proc_dir=str(setup["proc_dir"].parent))
        ctx = report._build_context(
            "TEST_M1",
            setup["cfg"],
            setup["proc_dir"],
            setup["proc_dir"] / "TEST_M1.mooring.yaml",
            setup["out_dir"],
        )
        generate_instrument_pages(
            "TEST_M1",
            ctx["instruments"],
            setup["cfg"],
            setup["proc_dir"],
            setup["out_dir"],
            False,
        )
        pages = list((setup["out_dir"] / "instrument").glob("*.html"))
        assert any("1234" in p.name for p in pages)

    def test_generate_instrument_pages_no_nc(self, tmp_path):
        """Should complete without error even when no NC files exist."""
        from oceanarray.reports._instrument import generate_instrument_pages

        proc_dir = tmp_path / "proc" / "TEST_M1"
        proc_dir.mkdir(parents=True)
        out_dir = proc_dir / "report"
        out_dir.mkdir()
        cfg = {
            "name": "TEST_M1",
            "deployment_time": "2024-06-01T00:00:00",
            "recovery_time": "2024-09-01T00:00:00",
            "waterdepth": 1000,
            "instruments": [
                {
                    "instrument": "microcat",
                    "serial": "9999",
                    "hab": 100.0,
                    "filename": "x.cnv",
                    "file_type": "sbe-cnv",
                }
            ],
        }
        report = MooringReport(proc_dir=str(proc_dir.parent))
        (proc_dir / "TEST_M1.mooring.yaml").write_text(yaml.dump(cfg))
        ctx = report._build_context(
            "TEST_M1",
            cfg,
            proc_dir,
            proc_dir / "TEST_M1.mooring.yaml",
            out_dir,
        )
        generate_instrument_pages(
            "TEST_M1",
            ctx["instruments"],
            cfg,
            proc_dir,
            out_dir,
            False,
        )

    # generate_stack_page
    def test_generate_stack_page_creates_html(self, mooring_setup):
        from oceanarray.reports._stack import generate_stack_page

        setup = mooring_setup
        stack_nc = setup["proc_dir"] / "TEST_M1_stack.nc"
        _write_minimal_stack_nc(stack_nc)
        report = MooringReport(proc_dir=str(setup["proc_dir"].parent))
        ctx = report._build_context(
            "TEST_M1",
            setup["cfg"],
            setup["proc_dir"],
            setup["proc_dir"] / "TEST_M1.mooring.yaml",
            setup["out_dir"],
        )
        generate_stack_page(
            "TEST_M1", stack_nc, ctx, setup["out_dir"], False, setup["tmp_path"]
        )
        out = setup["out_dir"] / "TEST_M1_stack_report.html"
        assert out.exists()
        assert out.stat().st_size > 0

    def test_generate_stack_page_html_structure(self, mooring_setup):
        from oceanarray.reports._stack import generate_stack_page

        setup = mooring_setup
        stack_nc = setup["proc_dir"] / "TEST_M1_stack.nc"
        _write_minimal_stack_nc(stack_nc)
        report = MooringReport(proc_dir=str(setup["proc_dir"].parent))
        ctx = report._build_context(
            "TEST_M1",
            setup["cfg"],
            setup["proc_dir"],
            setup["proc_dir"] / "TEST_M1.mooring.yaml",
            setup["out_dir"],
        )
        generate_stack_page(
            "TEST_M1", stack_nc, ctx, setup["out_dir"], False, setup["tmp_path"]
        )
        html = (setup["out_dir"] / "TEST_M1_stack_report.html").read_text(
            encoding="utf-8"
        )
        assert "TEST_M1" in html

    # generate_grid_page
    def test_generate_grid_page_creates_html(self, mooring_setup):
        from oceanarray.reports._grid import generate_grid_page

        setup = mooring_setup
        grid_nc = setup["proc_dir"] / "TEST_M1_grid.nc"
        _write_minimal_grid_nc(grid_nc)
        report = MooringReport(proc_dir=str(setup["proc_dir"].parent))
        ctx = report._build_context(
            "TEST_M1",
            setup["cfg"],
            setup["proc_dir"],
            setup["proc_dir"] / "TEST_M1.mooring.yaml",
            setup["out_dir"],
        )
        generate_grid_page(
            "TEST_M1", grid_nc, ctx, setup["out_dir"], False, setup["tmp_path"]
        )
        out = setup["out_dir"] / "TEST_M1_grid_report.html"
        assert out.exists()
        assert out.stat().st_size > 0

    def test_generate_grid_page_html_contains_mooring(self, mooring_setup):
        from oceanarray.reports._grid import generate_grid_page

        setup = mooring_setup
        grid_nc = setup["proc_dir"] / "TEST_M1_grid.nc"
        _write_minimal_grid_nc(grid_nc)
        report = MooringReport(proc_dir=str(setup["proc_dir"].parent))
        ctx = report._build_context(
            "TEST_M1",
            setup["cfg"],
            setup["proc_dir"],
            setup["proc_dir"] / "TEST_M1.mooring.yaml",
            setup["out_dir"],
        )
        generate_grid_page(
            "TEST_M1", grid_nc, ctx, setup["out_dir"], False, setup["tmp_path"]
        )
        html = (setup["out_dir"] / "TEST_M1_grid_report.html").read_text(
            encoding="utf-8"
        )
        assert "TEST_M1" in html


def test_isopycnal_panel_warns_and_stubs_on_failure(monkeypatch):
    """A failure inside the isopycnal renderer warns, naming the sigma variable,
    and returns None (the stub path) instead of swallowing the exception.
    """
    from oceanarray.reports import _grid

    def _boom(*_args, **_kwargs):
        raise ValueError("sigma computation failed")

    import oceanarray.analysis.hydrography as hydrography

    monkeypatch.setattr(hydrography, "isopycnal_dataset", _boom)
    panel = _grid._isopycnal_ts_panel("sigma0")
    ctx = type("Ctx", (), {"ds": None})()
    with pytest.warns(UserWarning, match="sigma0"):
        assert panel.render(ctx) is None


def test_render_nc_tables_tolerate_empty_meta():
    """An instrument with no processed NetCDF yields ``nc_meta={}``; the inventory
    renderers must return ``""`` rather than raise ``KeyError`` (which the page
    pipeline swallows to a blank page)."""
    from oceanarray.reports._env import (
        render_nc_coords,
        render_nc_globals,
        render_nc_scalars,
        render_nc_variables,
    )

    for render in (
        render_nc_variables,
        render_nc_coords,
        render_nc_scalars,
        render_nc_globals,
    ):
        assert render({}) == ""


def test_seam_history_excluded_and_stdname_column_drops(tmp_path):
    """Pin the owned re-vendor call-site adoptions against a future vendored re-sync.

    Opens a real dune2 stage-3 file and removes two things: ``standard_name`` and
    ``long_name`` from every variable, and every ``Provenance & processing`` global
    except ``history`` (so ``history`` is that group's only surviving member).  The
    owned renderers must then show the two paths the dune2 goldens never exercise
    (the goldens keep standard names and keep other provenance attributes):

    * ``_read_nc_metadata`` passes ``exclude=("history",)`` to ``globals_rows``, so
      ``history`` is gone from the globals table **and** the group it alone filled
      renders no heading.
    * ``render_nc_scalars`` passes ``"stdname"`` in ``optional``, so an all-empty
      Standard / long name column drops entirely.
    """
    from oceanarray.reports._env import render_nc_globals, render_nc_scalars
    from oceanarray.reports._vendored.nc_tables import ATTR_GROUPS
    from oceanarray.reports.design import ATTR_GROUP_EXTRAS

    src = (
        Path(__file__).resolve().parents[1]
        / "fixtures/proc/dune2_1_2026/microcat/dune2_1_2026_2941_stage3.nc"
    )
    prov_title = "Provenance & processing"
    prov_attrs = set(dict(ATTR_GROUPS)[prov_title]) | set(ATTR_GROUP_EXTRAS[prov_title])
    marker = "SEAM-TEST-HISTORY-PROVENANCE-ONLY"

    stripped = tmp_path / "stripped_stage3.nc"
    with xr.open_dataset(src, engine="netcdf4") as ds:
        ds = ds.load()
        for name in ds.variables:
            ds[name].attrs.pop("standard_name", None)
            ds[name].attrs.pop("long_name", None)
        for key in [k for k in ds.attrs if k in prov_attrs]:
            del ds.attrs[key]
        ds.attrs["history"] = marker
        ds.to_netcdf(stripped, engine="netcdf4")

    meta = _read_nc_metadata(stripped)
    globals_html = render_nc_globals(meta)
    scalars_html = render_nc_scalars(meta)

    # exclude=("history",): the history value never reaches the globals table, and the
    # group it alone filled renders no heading (the "&" is HTML-escaped in the output).
    assert marker not in globals_html
    assert "Provenance &amp; processing" not in globals_html
    # A group still populated by other attributes renders, so it is specifically the
    # emptied group that vanished, not the whole table.
    assert "Identity &amp; discovery" in globals_html
    # optional includes "stdname": with no standard_name/long_name on any scalar, the
    # Standard / long name column drops rather than rendering empty.
    assert "Standard / long name" not in scalars_html
