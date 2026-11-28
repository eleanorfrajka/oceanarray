"""Unit tests for pure helper functions in oceanarray.reports._array.

Tests cover _parse_coordinate, _resolve_lat_lon, the type summary (on the dune2
fixture), the header, the map payload and the year page (egM2 with ETOPO1).
"""

import logging
import re
from pathlib import Path

import pytest

from oceanarray.mooring.location import parse_coordinate as _parse_coordinate
from oceanarray.reports._array import (
    _array_map_payload,
    _build_array_masthead,
    _build_type_summary,
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
# _build_type_summary
# ---------------------------------------------------------------------------

_DUNE2_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "proc" / "dune2_1_2026"


def _dune2_instruments(tmp_path, edit=None, recover=None):
    """The dune2 MicroCAT and Aquadopp status records, from a copy of the fixture.

    *edit* changes the copy's mooring YAML text; *recover* replaces its recovery time.
    """
    import shutil

    import yaml

    from oceanarray.reports._array import _build_mooring_instruments

    shutil.copytree(_DUNE2_DIR, tmp_path / "dune2_1_2026")
    text = (_DUNE2_DIR / "dune2_1_2026.mooring.yaml").read_text()
    cfg = yaml.safe_load(edit(text) if edit else text)
    return _build_mooring_instruments(tmp_path, "dune2_1_2026", cfg, recover)


class TestBuildTypeSummary:
    """The per-type table, from the dune2 MicroCAT 2941 and Aquadopp 9920."""

    def test_empty_list_returns_empty(self):
        assert _build_type_summary([]) == []

    def test_each_type_deployed_and_processed(self, tmp_path):
        rows = _build_type_summary(_dune2_instruments(tmp_path))
        assert [r["label"] for r in rows] == ["CTD", "current meter"]
        assert all(r["deployed"] == r["processed"] == 1 for r in rows)
        assert all(r["skipped"] == r["stopped_early"] == 0 for r in rows)
        assert all(r["notes"] == "" for r in rows)

    def test_a_skipped_instrument_is_counted_with_its_reason(self, tmp_path):
        def skip(text):
            return text.replace(
                "  instrument: aquadopp\n",
                "  instrument: aquadopp\n  skip: true\n  skip_reason: flooded\n",
            )

        rows = {
            r["label"]: r
            for r in _build_type_summary(_dune2_instruments(tmp_path, skip))
        }
        assert (
            rows["current meter"]["skipped"] == rows["current meter"]["not_here"] == 1
        )
        assert rows["current meter"]["processed"] == 0
        assert rows["current meter"]["notes"] == "flooded"

    def test_inline_instruments_counted_and_non_mapping_entries_skipped(self, tmp_path):
        """The Aquadopp moved to ``inline:`` beside a shackle, a bare string in ``clamp:``."""

        def inline(text):
            return text.replace(
                "\n- serial: 9920*",
                "\n- not a mapping\ninline:\n- name: shackle\n- serial: 9920*",
            )

        rows = _build_type_summary(_dune2_instruments(tmp_path, inline))
        assert [r["label"] for r in rows] == ["CTD", "current meter"]
        assert all(r["deployed"] == r["processed"] == 1 for r in rows)

    def test_records_ending_before_recovery_stopped_early(self, tmp_path):
        from datetime import UTC, datetime

        late = datetime(2026, 8, 25, tzinfo=UTC)
        rows = _build_type_summary(_dune2_instruments(tmp_path, recover=late))
        assert all(r["stopped_early"] == 1 for r in rows)

    def test_a_mixed_case_type_finds_its_stage_files(self, tmp_path):
        """Stage 1 names the folder as the YAML writes the type: ``MicroCAT/``."""

        def mixed(text):
            return text.replace("instrument: microcat", "instrument: MicroCAT")

        _dune2_instruments(tmp_path)  # the copy
        folder = tmp_path / "dune2_1_2026"
        (folder / "microcat").rename(folder / "MicroCAT")
        import yaml

        from oceanarray.reports._array import _build_mooring_instruments

        cfg = yaml.safe_load(mixed((folder / "dune2_1_2026.mooring.yaml").read_text()))
        instrs = _build_mooring_instruments(tmp_path, "dune2_1_2026", cfg, None)
        microcat = next(i for i in instrs if i["serial"] == "2941")
        assert microcat["itype"] == "microcat"
        assert microcat["processed"] and not microcat["skipped"]


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

        mooring_path = tmp_path / "arr.array.yaml"
        mooring_path.write_text("name: arr\n")
        central = tmp_path / "central"
        args = argparse.Namespace(
            mooring=str(mooring_path),
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

    def test_bathymetry_reaches_the_year_page(self, tmp_path):
        """``report --array --bathymetry FILE`` draws the file's contours on the map."""
        import warnings

        from oceanarray.cli import build_parser

        fix = Path(__file__).resolve().parents[1] / "fixtures"
        config = fix / "proc" / "egM2_concat" / "egM2_2016" / "egM2_2016.mooring.yaml"
        mooring_path = tmp_path / "EG.array.yaml"
        mooring_path.write_text(
            "name: EG\nyear: 2016\nmoorings:\n"
            f"- mooring: egM2_2016\n  config: {config}\n",
            encoding="utf-8",
        )
        args = build_parser().parse_args(
            [
                "report",
                str(mooring_path),
                "--array",
                "--proc-dir",
                str(tmp_path),
                "--bathymetry",
                str(fix / "bathymetry" / "etopo1_osnap_east_8min.nc"),
            ]
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert args.func(args) == 0
        html = (tmp_path / "EG_array_report.html").read_text(encoding="utf-8")
        assert "L.geoJSON(CONTOURS" in html and "from ETOPO1." in html


class TestArrayHeader:
    """The year page's header: the ruled fields, in order, and the status."""

    @staticmethod
    def _row(recovered):
        from datetime import UTC, datetime

        return {
            "waterdepth": 800,
            "_deploy_dt": datetime(2016, 7, 10, tzinfo=UTC),
            "_recover_dt": recovered,
            "group": "other",
            "deployed_by": ("DY054", "RRS Discovery"),
            "recovered_by": (None, None),
        }

    def test_fields_in_order(self, tmp_path):
        from datetime import UTC, datetime

        done = datetime(2018, 7, 24, tzinfo=UTC)
        summary = _build_type_summary(
            _dune2_instruments(
                tmp_path,
                lambda text: text.replace(
                    "  instrument: aquadopp\n", "  instrument: aquadopp\n  skip: true\n"
                ),
            )
        )
        facts = _build_array_masthead(
            {"year": 2016, "project": "OSNAP East"},
            [self._row(done), self._row(done)],
            summary,
        )
        labels = [f[0] for f in facts]
        assert labels == [
            "Year deployed",
            "Project",
            "Moorings",
            "Status",
            "Deployed",
            "Recovered",
            "Water depth",
            "Instruments",
            "Instruments not processed",
        ]
        assert facts[3][1] == "recovered"
        assert facts[4][1] == "10 Jul 2016<small>DY054 (RRS Discovery)</small>"
        assert facts[5][1] == "24 Jul 2018"
        assert facts[7][1] == "2<small>CTD 1 · current meter 1</small>"
        assert facts[-1][1] == "1"

    def test_status_some_recovered_and_in_the_water(self):
        from datetime import UTC, datetime

        now = datetime(2017, 1, 1, tzinfo=UTC)
        rows = [self._row(datetime(2016, 12, 1, tzinfo=UTC)), self._row(None)]
        facts = {f[0]: f[1] for f in _build_array_masthead({}, rows, [], now)}
        assert facts["Status"] == "1 of 2 recovered"
        facts = {
            f[0]: f[1] for f in _build_array_masthead({}, [self._row(None)], [], now)
        }
        assert facts["Status"] == "in the water"


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
            "report_exists": False,
            "report_href": "",
        }
        assert _array_map_payload([row]) == ("[]", "null", "null")


class TestBathymetryContours:
    """Depth contours from the ETOPO1 subset of OSNAP East (56–64°N, 45–26°W)."""

    _ETOPO = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "bathymetry"
        / "etopo1_osnap_east_8min.nc"
    )
    _EAST_GREENLAND = (-44.0, 57.0, -38.0, 62.0)

    def test_north_to_south_files_give_the_same_contours(self, tmp_path):
        import xarray as xr

        from oceanarray.reports._array import bathymetry_contours

        flipped = tmp_path / "flipped.nc"
        with xr.open_dataset(self._ETOPO, engine="netcdf4") as ds:
            ds.isel(latitude=slice(None, None, -1)).to_netcdf(flipped)
        ascending = bathymetry_contours(
            self._ETOPO, self._EAST_GREENLAND, tmp_path / "a.json"
        )
        assert '"coordinates":[[' in ascending
        assert (
            bathymetry_contours(flipped, self._EAST_GREENLAND, tmp_path / "b.json")
            == ascending
        )

    def test_an_extent_the_file_does_not_cover_draws_no_contours(
        self, tmp_path, caplog
    ):
        from oceanarray.reports._array import bathymetry_contours

        with caplog.at_level(logging.WARNING, logger="oceanarray.reports._array"):
            out = bathymetry_contours(
                self._ETOPO, (10.0, -40.0, 20.0, -30.0), tmp_path / "c.json"
            )
        assert out is None
        assert "does not cover" in caplog.text
        assert not (tmp_path / "c.json").exists()

    def test_the_cache_follows_the_file_not_its_name(self, tmp_path):
        """Another file of the same name, or the file rewritten, draws afresh."""
        import shutil

        import xarray as xr

        from oceanarray.reports._array import bathymetry_contours

        cache = tmp_path / "contours.json"
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        here = shutil.copy(self._ETOPO, tmp_path / "a" / "etopo.nc")
        with xr.open_dataset(self._ETOPO, engine="netcdf4") as ds:
            (ds - 500).to_netcdf(tmp_path / "b" / "etopo.nc")
        first = bathymetry_contours(here, self._EAST_GREENLAND, cache)
        assert bathymetry_contours(here, self._EAST_GREENLAND, cache) == first
        other = bathymetry_contours(
            tmp_path / "b" / "etopo.nc", self._EAST_GREENLAND, cache
        )
        assert other != first
        assert bathymetry_contours(here, self._EAST_GREENLAND, cache) == first
        shutil.copy(tmp_path / "b" / "etopo.nc", here)
        assert bathymetry_contours(here, self._EAST_GREENLAND, cache) == other


class TestDatasetName:
    """The map caption names the bathymetry dataset from the file's title."""

    _ETOPO = TestBathymetryContours._ETOPO

    def test_etopo1_and_a_gebco_title(self, tmp_path):
        import xarray as xr

        from oceanarray.reports._array import dataset_name

        assert dataset_name(self._ETOPO) == "ETOPO1"
        gebco = tmp_path / "gebco.nc"
        with xr.open_dataset(self._ETOPO, engine="netcdf4") as ds:
            ds.attrs = {"title": "The GEBCO_2025 Grid - a continuous terrain model"}
            ds.to_netcdf(gebco)
        assert dataset_name(gebco) == "GEBCO 2025"


class TestYearPage:
    """The year page on the egM2 2016 and 2018 deployments, with ETOPO1 depth contours."""

    _FIX = Path(__file__).resolve().parents[1] / "fixtures"

    @pytest.fixture(scope="class")
    def page(self, tmp_path_factory):
        import warnings

        from oceanarray.reports._array import generate_array_report

        root = tmp_path_factory.mktemp("year")
        # egM2_2018 has no subarray: it is grouped as "other".
        moorings = "".join(
            f"- mooring: {n}\n  config: {self._FIX / 'proc' / 'egM2_concat' / n / f'{n}.mooring.yaml'}\n"
            + extra
            for n, extra in (("egM2_2016", "  subarray: eg\n"), ("egM2_2018", ""))
        )
        array_path = root / "EG.array.yaml"
        array_path.write_text(
            "name: EG\nyear: 2016\nproject: OSNAP East\ncruise: DY054\n"
            "subarrays:\n  eg: {name: East Greenland Array, short: East Greenland}\n"
            f"moorings:\n{moorings}",
            encoding="utf-8",
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = generate_array_report(
                array_path,
                root,
                force=True,
                bathymetry=self._FIX / "bathymetry" / "etopo1_osnap_east_8min.nc",
            )
        return root, out.read_text(encoding="utf-8")

    def test_header_fields_in_the_ruled_order(self, page):
        _, html = page
        labels = [
            "Year deployed",
            "Project",
            "Moorings",
            "Status",
            "Deployed",
            "Recovered",
            "Water depth",
            "Instruments",
        ]
        at = [html.index(f"<dt>{label}</dt>") for label in labels]
        assert at == sorted(at)
        assert "<dt>Position</dt>" not in html
        assert ">OSNAP East 2016<" in html and ">Deployment year<" in html
        # The deployment cruises from the mooring YAMLs, grouped by ship.
        assert "DY054 (RRS Discovery) · AR30-03 (RV Neil Armstrong)" in html
        assert "AR46 (RV Neil Armstrong)" in html

    def test_map_has_depth_contours_and_subarrays(self, page):
        root, html = page
        assert "L.geoJSON(CONTOURS" in html
        assert (root / "_EG_contours.json").exists()
        assert "Moorings deployed in 2016, coloured by array" in html
        # The dataset is named from the file's title, not the file name.
        assert "from ETOPO1." in html and "etopo1_osnap_east_8min.nc" not in html
        legend = html.split('<div class="legend">')[1].split("</div>")[0]
        assert "East Greenland</span>" in legend and "other</span>" in legend
        assert "East Greenland Array</td>" in html and "(East Greenland)" not in html

    def test_unused_array_yaml_keys_warn_once(self, tmp_path, caplog):
        """``cruise`` and a mooring's ``line`` are named in one warning."""
        from oceanarray.reports._array import generate_array_report

        config = (
            self._FIX / "proc" / "egM2_concat" / "egM2_2016" / "egM2_2016.mooring.yaml"
        )
        array_path = tmp_path / "EG.array.yaml"
        array_path.write_text(
            "name: EG\ncruise: DY054\nvocab: []\nmoorings:\n"
            f"- mooring: egM2_2016\n  config: {config}\n  line: East Greenland\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="oceanarray.reports._array"):
            generate_array_report(array_path, tmp_path)
        unused = [r.message for r in caplog.records if "not used" in r.message]
        assert unused == [
            "EG.array.yaml: keys not used by the year page: cruise, vocab, "
            "moorings: line (now subarray)"
        ]

    def test_one_moorings_table_with_instruments_and_pages(self, page):
        _, html = page
        assert 'id="completeness"' not in html
        assert "Overview</span>" in html and "Time series</span>" in html
        assert "Gridded</span>" in html
        # No stage files here: every instrument is counted, none processed.
        assert re.search(r"<td>0 of \d+ \(", html)
        # One unit, in the header; the cells are numbers.
        assert re.search(
            r'Duration<span class="th-unit">(years|months|days)</span>', html
        )
        assert re.findall(r'<td class="num">\d+\.\d</td>', html)
        assert not re.search(r"<td[^>]*>[\d.]+ (years|months|days)</td>", html)
        assert "Instruments by function</h2>" in html and "by type" not in html

    def test_a_water_depth_given_as_text_is_shown_as_given(self, tmp_path):
        """egM2_2016 with ``waterdepth: unknown`` (fails validation, read unvalidated)."""
        import shutil
        import warnings

        from oceanarray.reports._array import generate_array_report

        src = self._FIX / "proc" / "egM2_concat" / "egM2_2016"
        shutil.copytree(src, tmp_path / "egM2_2016")
        cfg = tmp_path / "egM2_2016" / "egM2_2016.mooring.yaml"
        cfg.write_text(
            cfg.read_text().replace("waterdepth: 2459.0", "waterdepth: unknown")
        )
        moorings = "".join(
            f"- mooring: {n}\n  config: {d / n / f'{n}.mooring.yaml'}\n"
            for n, d in (
                ("egM2_2016", tmp_path),
                ("egM2_2018", self._FIX / "proc" / "egM2_concat"),
            )
        )
        array_path = tmp_path / "EG.array.yaml"
        array_path.write_text(f"name: EG\nmoorings:\n{moorings}", encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = generate_array_report(array_path, tmp_path)
        html = out.read_text(encoding="utf-8")
        assert '<td class="num">unknown</td>' in html
        assert '<td class="num">2436</td>' in html
        assert "2436–2436" in html  # the header's range, from the numeric depth

    def test_without_subarrays_or_covering_bathymetry(self, tmp_path, caplog):
        """One colour per mooring, and a file north of the moorings: no contours."""
        import warnings

        import xarray as xr

        from oceanarray.reports._array import generate_array_report

        north = tmp_path / "north.nc"
        with xr.open_dataset(
            self._FIX / "bathymetry" / "etopo1_osnap_east_8min.nc", engine="netcdf4"
        ) as ds:
            ds.sel(latitude=slice(63.5, 64.0)).to_netcdf(north)
        moorings = "".join(
            f"- mooring: {n}\n  config: {self._FIX / 'proc' / 'egM2_concat' / n / f'{n}.mooring.yaml'}\n"
            for n in ("egM2_2016", "egM2_2018")
        )
        array_path = tmp_path / "EG.array.yaml"
        array_path.write_text(f"name: EG\nmoorings:\n{moorings}", encoding="utf-8")
        with (
            warnings.catch_warnings(),
            caplog.at_level(logging.WARNING, logger="oceanarray.reports._array"),
        ):
            warnings.simplefilter("ignore")
            out = generate_array_report(array_path, tmp_path, bathymetry=north)
        html = out.read_text(encoding="utf-8")
        assert "one colour per mooring" in html and "coloured by array" not in html
        assert "<th>Array</th>" not in html
        assert "L.geoJSON(CONTOURS" not in html and "Depth contours" not in html
        assert "north.nc does not cover" in caplog.text
