"""Unit tests for pure helper functions in oceanarray.reports._array.

Tests cover _parse_coordinate, _resolve_lat_lon, _count_instruments, and
_build_type_summary — all functions with no I/O dependencies that can be
exercised without fixtures or seasenselib.
"""

import logging
from pathlib import Path

import pytest

from oceanarray.reports._array import (
    _array_map_payload,
    _build_array_masthead,
    _build_type_summary,
    _count_instruments,
    _parse_coordinate,
    _resolve_lat_lon,
)

# ---------------------------------------------------------------------------
# _parse_coordinate
# ---------------------------------------------------------------------------


class TestParseCoordinate:
    """Tests for decimal-degree parsing."""

    def test_none_returns_none(self):
        assert _parse_coordinate(None) is None

    def test_float_passthrough(self):
        assert _parse_coordinate(65.0) == pytest.approx(65.0)

    def test_int_passthrough(self):
        assert _parse_coordinate(-27) == pytest.approx(-27.0)

    def test_plain_decimal_string(self):
        assert _parse_coordinate("65.567") == pytest.approx(65.567)

    def test_negative_decimal_string(self):
        assert _parse_coordinate("-27.5") == pytest.approx(-27.5)

    def test_degrees_minutes_positive(self):
        # "65 43.913" → 65 + 43.913/60
        expected = 65.0 + 43.913 / 60.0
        assert _parse_coordinate("65 43.913") == pytest.approx(expected, rel=1e-6)

    def test_degrees_minutes_negative(self):
        # "-27 48.000" → -(27 + 48/60)
        expected = -(27.0 + 48.0 / 60.0)
        assert _parse_coordinate("-27 48.000") == pytest.approx(expected, rel=1e-6)

    def test_degrees_minutes_north_hemisphere(self):
        expected = 65.0 + 34.004 / 60.0
        assert _parse_coordinate("65 34.004 N") == pytest.approx(expected, rel=1e-6)

    def test_degrees_minutes_west_hemisphere(self):
        expected = -(29.0 + 25.878 / 60.0)
        assert _parse_coordinate("029 25.878 W") == pytest.approx(expected, rel=1e-6)

    def test_degrees_minutes_south_hemisphere(self):
        expected = -(65.0 + 43.913 / 60.0)
        assert _parse_coordinate("65 43.913 S") == pytest.approx(expected, rel=1e-6)

    def test_unparseable_returns_none(self):
        assert _parse_coordinate("not a number") is None


# ---------------------------------------------------------------------------
# _resolve_lat_lon
# ---------------------------------------------------------------------------


class TestLatLonFromCfg:
    """Tests for coordinate extraction from mooring YAML dicts."""

    def test_plain_float_lat_lon(self):
        cfg = {"latitude": 65.732, "longitude": -27.8}
        lat, lon = _resolve_lat_lon(cfg)
        assert lat == pytest.approx(65.732)
        assert lon == pytest.approx(-27.8)

    def test_dms_string_lat_lon(self):
        cfg = {"latitude": "65 43.913", "longitude": "-27 48.000"}
        lat, lon = _resolve_lat_lon(cfg)
        assert lat == pytest.approx(65.0 + 43.913 / 60.0, rel=1e-6)
        assert lon == pytest.approx(-(27.0 + 48.0 / 60.0), rel=1e-6)

    def test_seabed_pair_takes_priority_over_latitude(self):
        cfg = {
            "latitude": 10.0,
            "longitude": -27.0,
            "seabed_latitude": "65 43.913",
            "seabed_longitude": "-27 48.000",
        }
        lat, lon = _resolve_lat_lon(cfg)
        assert lat == pytest.approx(65.0 + 43.913 / 60.0, rel=1e-6)
        assert lon == pytest.approx(-(27.0 + 48.0 / 60.0), rel=1e-6)

    def test_lat_and_lon_come_from_one_key_pair(self):
        # A seabed latitude without a seabed longitude is not paired with the
        # plain longitude: the plain pair is used whole.
        cfg = {
            "latitude": 10.0,
            "longitude": -27.0,
            "seabed_latitude": "65 43.913",
        }
        assert _resolve_lat_lon(cfg) == (pytest.approx(10.0), pytest.approx(-27.0))

    def test_deployment_pair_takes_priority_over_planned(self):
        cfg = {
            "planned_latitude": 10.0,
            "planned_longitude": -20.0,
            "deployment_latitude": "65 34.004 N",
            "deployment_longitude": "029 25.878 W",
        }
        lat, _ = _resolve_lat_lon(cfg)
        assert lat == pytest.approx(65.0 + 34.004 / 60.0, rel=1e-6)

    def test_zero_placeholder_is_skipped(self):
        cfg = {
            "seabed_latitude": 0,
            "seabed_longitude": 0,
            "latitude": "57 28.240 N",
            "longitude": "012 42.300 W",
        }
        lat, _ = _resolve_lat_lon(cfg)
        assert lat == pytest.approx(57.0 + 28.240 / 60.0, rel=1e-6)

    def test_missing_lat_lon_returns_none(self):
        lat, lon = _resolve_lat_lon({})
        assert lat is None
        assert lon is None


# ---------------------------------------------------------------------------
# _count_instruments
# ---------------------------------------------------------------------------


class TestCountInstruments:
    """Tests for instrument counting from mooring YAML dicts."""

    def test_empty_config_returns_zero(self):
        assert _count_instruments({}) == 0

    def test_clamp_list_counted(self):
        cfg = {
            "clamp": [
                {"instrument": "microcat", "serial": "2941"},
                {"instrument": "aquadopp", "serial": "9920"},
            ]
        }
        assert _count_instruments(cfg) == 2

    def test_instruments_key_fallback(self):
        cfg = {
            "instruments": [
                {"instrument": "microcat", "serial": "2941"},
            ]
        }
        assert _count_instruments(cfg) == 1

    def test_skipped_entries_excluded(self):
        cfg = {
            "clamp": [
                {"instrument": "microcat", "serial": "2941"},
                {"instrument": "aquadopp", "serial": "9920", "skip": True},
            ]
        }
        assert _count_instruments(cfg) == 1

    def test_inline_instruments_counted(self):
        cfg = {
            "clamp": [{"instrument": "microcat", "serial": "2941"}],
            "inline": [
                {"name": "shackle"},
                {"instrument": "adcp", "serial": "16430, R01-024", "filename": "a.000"},
            ],
        }
        assert _count_instruments(cfg) == 2

    def test_non_dict_entries_ignored(self):
        # YAML inline sections sometimes have non-dict entries
        cfg = {"clamp": [{"instrument": "microcat"}, "not a dict"]}
        assert _count_instruments(cfg) == 1


# ---------------------------------------------------------------------------
# _build_type_summary
# ---------------------------------------------------------------------------


class TestBuildTypeSummary:
    """Tests for the per-type aggregation table."""

    def _make_instrument(
        self, itype, complete=True, skipped=False, stopped_early=False
    ):
        return {
            "itype": itype,
            "complete": complete,
            "skipped": skipped,
            "stopped_early": stopped_early,
        }

    def test_empty_list_returns_empty(self):
        result = _build_type_summary([])
        assert result == []

    def test_single_type_counted(self):
        instruments = [self._make_instrument("microcat") for _ in range(3)]
        result = _build_type_summary(instruments)
        assert len(result) == 1
        row = result[0]
        assert row["itype"] == "microcat"
        assert row["deployed"] == 3
        assert row["complete"] == 3
        assert row["skipped"] == 0

    def test_incomplete_instrument_counted_separately(self):
        instruments = [
            self._make_instrument("microcat", complete=True),
            self._make_instrument("microcat", complete=False),
        ]
        result = _build_type_summary(instruments)
        assert result[0]["deployed"] == 2
        assert result[0]["complete"] == 1

    def test_canonical_type_order_respected(self):
        # microcat should appear before aquadopp in canonical order
        instruments = [
            self._make_instrument("aquadopp"),
            self._make_instrument("microcat"),
        ]
        result = _build_type_summary(instruments)
        itypes = [r["itype"] for r in result]
        assert itypes.index("microcat") < itypes.index("aquadopp")

    def test_unknown_type_appended_alphabetically(self):
        instruments = [
            self._make_instrument("microcat"),
            self._make_instrument("rbrsolo"),
        ]
        result = _build_type_summary(instruments)
        itypes = [r["itype"] for r in result]
        assert "microcat" in itypes
        assert "rbrsolo" in itypes

    def test_skipped_flag_counted(self):
        instruments = [
            self._make_instrument("microcat", skipped=True),
            self._make_instrument("microcat", skipped=False),
        ]
        result = _build_type_summary(instruments)
        assert result[0]["skipped"] == 1

    def test_stopped_early_flag_counted(self):
        instruments = [
            self._make_instrument("aquadopp", stopped_early=True),
        ]
        result = _build_type_summary(instruments)
        assert result[0]["stopped_early"] == 1

    def test_notes_key_present(self):
        instruments = [self._make_instrument("microcat")]
        result = _build_type_summary(instruments)
        assert "notes" in result[0]


class TestArrayModeOutputDir:
    """``report --array`` output-location handling.

    ``--report-dir`` sets the array index location; ``-o/--output-dir`` does not
    apply to a multi-mooring index and must be reported (not silently ignored)
    while the index is still generated.
    """

    def test_outdir_warns_but_report_dir_is_honoured(
        self, tmp_path, caplog, monkeypatch
    ):
        import argparse

        from oceanarray import cli
        from oceanarray.reports import _array

        seen = {}

        def _fake_generate(**kwargs):
            seen["report_dir"] = kwargs["report_dir"]
            out = tmp_path / "arr_array_report.html"
            out.write_text("<html></html>")
            return out

        monkeypatch.setattr(_array, "generate_array_report", _fake_generate)

        yaml_path = tmp_path / "arr.array.yaml"
        yaml_path.write_text("name: arr\n")
        central = tmp_path / "central"
        args = argparse.Namespace(
            mooring=str(yaml_path),
            array=True,
            outdir="/tmp/ignored",
            report_dir=str(central),
            proc_dir=str(tmp_path),
            raw_dir=None,
            force=False,
        )

        with caplog.at_level(logging.ERROR, logger="oceanarray"):
            rc = cli.cmd_report(args)

        assert rc == 0
        assert "-o/--output-dir is ignored in --array mode" in caplog.text
        # The flag that DOES apply in array mode is honoured, not dropped.
        assert seen["report_dir"] == Path(str(central))


class TestArrayMastheadExtent:
    """The masthead extent labels each endpoint from its coordinate sign.

    Regression guard: the extent once hard-coded ``°N``/``°E``, so a western
    longitude (MIXSED sits at ~29 °W) rendered as east.  It must read the
    hemisphere from the sign, as ``position`` does via ``location.format_ddm``.
    """

    @staticmethod
    def _row(lat, lon):
        return {
            "lat": lat,
            "lon": lon,
            "waterdepth": 800,
            "n_instruments": 3,
            "_deploy_dt": None,
            "_recover_dt": None,
            "report_exists": False,
            "stack_exists": False,
            "grid_exists": False,
        }

    def test_western_longitude_reads_west_not_east(self):
        rows = [self._row(65.0, -31.0), self._row(66.0, -27.0)]
        masthead = _build_array_masthead(
            {"waterdepth_min": 700, "waterdepth_max": 1000},
            rows,
            [],
            None,
            None,
            Path("MIXSED2026.array.yaml"),
        )
        extent = masthead["extent"]
        # Western longitudes are labelled W, never E; northern latitudes N.  The old
        # buggy form hard-coded °E, so a leaked signed decimal would also fail "W" here.
        assert "W" in extent
        assert "E" not in extent
        assert "N" in extent


class TestNanPosition:
    """A YAML position of nan is unknown: no crash, no map marker."""

    _DUNE2 = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "proc"
        / "dune2_1_2026"
        / "dune2_1_2026.mooring.yaml"
    )

    def test_nan_coordinate_is_none(self):
        assert _parse_coordinate("nan") is None
        assert _parse_coordinate(float("nan")) is None
        assert _parse_coordinate("inf") is None

    def test_nan_latitude_resolves_to_unknown_and_is_not_mapped(self, tmp_path):
        import yaml

        text = self._DUNE2.read_text().replace("latitude: 65 43.913", "latitude: nan")
        edited = tmp_path / "dune2_1_2026.mooring.yaml"
        edited.write_text(text)
        cfg = yaml.safe_load(edited.read_text())
        lat, lon = _resolve_lat_lon(cfg)
        assert (lat, lon) == (None, None)
        row = {
            "mooring": "dune2_1_2026",
            "lat": lat,
            "lon": lon,
            "waterdepth": cfg["waterdepth"],
            "deploy_time": "2026-07-11",
            "recover_time": "2026-07-25",
            "n_instruments": 2,
            "report_exists": False,
            "report_href": "",
        }
        assert _array_map_payload([row]) == ("[]", "null", "null")
