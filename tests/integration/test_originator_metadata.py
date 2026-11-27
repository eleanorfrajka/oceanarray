"""What an originator wrote about its data is kept: variable attributes, salinity.

Runs the CF7 2020 OSNAP mooring (a WHOI MicroCAT and current meter, OceanSITES)
through the pipeline from the trimmed fixtures in ``tests/fixtures/raw/osnap``.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
import yaml

pytest.importorskip("seasenselib", reason="seasenselib not installed")
import oceanarray as oa
from oceanarray.pipeline import sensors

OSNAP = Path(__file__).parent.parent / "fixtures" / "raw" / "osnap"
MCTD = "CF7_MCTD_2020_22225_50m.nc"
CM = "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc"
CF7 = {
    "waterdepth": 1901,
    "latitude": "59 55.576 N",
    "longitude": "041 25.885 W",
    "deployment_time": "2020-07-12T14:00",
    "recovery_time": "2021-11-12T00:00",
}
INSTRUMENTS = [
    {
        "serial": 22225,
        "instrument": "microcat",
        "file_type": "oceansites",
        "filename": MCTD,
        "hab": 1851,
        "units_override": {"conductivity": "mS/cm"},
    },
    {
        "serial": 500,
        "instrument": "currentmeter",
        "file_type": "oceansites",
        "filename": CM,
        "hab": 1401,
        "units_override": {"pressure": "dbar"},
    },
]


@pytest.fixture(scope="module")
def cf7(tmp_path_factory):
    """The CF7 2020 mooring run through stage 3, the stack and the grid."""
    tmp = tmp_path_factory.mktemp("cf7")
    name = "cf7_2020"
    raw = tmp / "raw" / name
    proc = tmp / "proc" / name
    proc.mkdir(parents=True)
    for inst in INSTRUMENTS:
        (raw / inst["instrument"]).mkdir(parents=True, exist_ok=True)
        shutil.copy(OSNAP / inst["filename"], raw / inst["instrument"])
    cfg = {"name": name, **CF7, "stack": {"dt_seconds": 900}, "clamp": INSTRUMENTS}
    yaml_path = proc / f"{name}.mooring.yaml"
    yaml_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    oa.run(
        yaml_path,
        oa.Paths(raw_dir=tmp / "raw", proc_dir=tmp / "proc"),
        pmin=40,
        pmax=520,
        dp=40,
    )
    return proc


def _stage(proc: Path, instrument: str, serial: object, n: int) -> xr.Dataset:
    path = proc / instrument / f"{proc.name}_{serial}_stage{n}.nc"
    with xr.open_dataset(path, engine="netcdf4") as ds:
        return ds.load()


def test_variable_attributes_move_to_the_sensor_record(cf7):
    s1 = _stage(cf7, "currentmeter", 500, 1)
    (record,) = sensors.records(s1).values()
    with xr.open_dataset(OSNAP / CM, engine="netcdf4") as src:
        ucur = dict(src["UCUR"].attrs)
    assert record["originator_east_velocity_comment"] == ucur["comment"]
    assert record["originator_east_velocity_uncertainty"] == ucur["uncertainty"]
    assert record["originator_east_velocity_accuracy"] == ucur["accuracy"]
    assert record["originator_east_velocity_units"] == ucur["units"]
    # The variable keeps only oceanarray's attributes.
    assert not [k for k in s1["east_velocity"].attrs if k.startswith("originator_")]


def test_the_originator_salinity_is_kept_beside_ours(cf7):
    s3 = _stage(cf7, "microcat", 22225, 3)
    with xr.open_dataset(OSNAP / MCTD, engine="netcdf4") as src:
        psal = dict(src["PSAL"].attrs)
        temp_max = float(src["TEMP"].attrs["data_max"])
    theirs = s3["salinity_originator"]
    assert theirs.attrs["units"] == "1"  # psu, in our units' name
    assert theirs.attrs["processing_note"] == psal["processing_note"]
    # Its description moved off the record onto the variable it describes.
    (record,) = sensors.records(s3).values()
    assert not [k for k in record if k.startswith("originator_salinity_")]
    assert record["originator_temperature_data_max"] == pytest.approx(temp_max)
    diff = s3["salinity"].values - theirs.values
    assert np.nanmax(np.abs(diff)) < 0.01
    assert "kept as salinity_originator (ours - theirs: median" in s3.attrs["history"]


def test_the_stack_names_each_levels_originator_file(cf7):
    from oceanarray import provenance

    with xr.open_dataset(cf7 / "cf7_2020_stack.nc", engine="netcdf4") as ds:
        st = ds.load()
    files = dict(zip(st["serial"].values, st["originator_file"].values, strict=True))
    assert files == {"500": CM, "22225": MCTD}
    level = list(st["serial"].values).index("22225")
    assert st["originator_sha256"].values[level] == provenance.sha256(OSNAP / MCTD)
    cm = list(st["serial"].values).index("500")
    with xr.open_dataset(OSNAP / CM, engine="netcdf4") as src:
        title, created = src.attrs["title"], str(src.attrs["date_created"])
    assert st["originator_date_created"].values[cm] == created
    # One value of what the files say of the deployment (the MicroCAT file has no title).
    assert st.attrs["originator_institution"].startswith("Scripps")
    assert st.attrs["originator_title"] == title


def test_a_product_is_cited_as_its_source_and_its_making_is_history(cf7):
    with xr.open_dataset(cf7 / "cf7_2020_stack.nc", engine="netcdf4") as ds:
        attrs = dict(ds.attrs)
    assert attrs["citation"] == attrs["source_citation"]
    assert "oceanarray" not in attrs["citation"]
    assert "cf7_2020 stack built with oceanarray" in attrs["history"]
    assert ".." not in attrs["history"]
    # The two files list the same references in another order: each once.
    assert attrs["references"] == "http://www.oceansites.org; http://www.o-snap.org"


CATALOG = Path(__file__).parent.parent / "fixtures" / "catalog" / "sensor_catalog.yaml"
IC1 = "os_osnap-ic1_201608_mctd_76m_cut.nc"
L22 = "https://vocab.nerc.ac.uk/collection/L22/current/"


def _ic1(tmp_path, **extra):
    """IC1 2016's 76 m MicroCAT, named MC76 as osnap-east names it, through stage 1."""
    raw = tmp_path / "raw" / "ic1_2016" / "microcat"
    raw.mkdir(parents=True)
    shutil.copy(OSNAP / IC1, raw)
    proc = tmp_path / "proc" / "ic1_2016"
    (proc / "config").mkdir(parents=True)
    shutil.copy(CATALOG, proc / "config")
    entry = {"serial": "MC76", "instrument": "microcat", "file_type": "oceansites"}
    cfg = {
        "name": "ic1_2016",
        "waterdepth": 2472,
        "latitude": 59.1,
        "longitude": -33.7,
        "deployment_time": "2016-08-09T14:20:00",
        "recovery_time": "2018-07-15T07:38:41",
        "clamp": [{**entry, "filename": IC1, "hab": 2396, **extra}],
        "sensor_catalog": "config/sensor_catalog.yaml",
    }
    yaml_path = proc / "ic1_2016.mooring.yaml"
    yaml_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    paths = oa.Paths(raw_dir=tmp_path / "raw", proc_dir=yaml_path.parent.parent)
    oa.process(yaml_path, paths, stages=1)
    return _stage(proc, "microcat", "MC76", 1)


def test_the_catalog_describes_the_sensor_the_file_gets_wrong(tmp_path, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        (record,) = sensors.records(_ic1(tmp_path)).values()
    # Found by the file's serial (12046), not the mooring YAML's label (MC76); the
    # file holds pressure (unflagged), so the "with pressure" (CTP) entry.
    assert record["sensor_model"] == (
        "Sea-Bird SBE 37 MicroCat SMP-CTP (submersible) CTD sensor"
    )
    assert record["sensor_model_vocabulary"] == f"{L22}TOOL1457/"
    assert record["sensor_maker"] == "Sea-Bird Scientific"
    assert record["sensor_maker_vocabulary"].endswith("/L35/current/MAN0013/")
    assert record["sensor_type_vocabulary"].endswith("/L05/current/130/")
    assert record["long_name"] == "Sea-Bird SBE 37-SMP MicroCAT"
    assert record["sensor_pumped"] == "true"
    assert record["originator_sensor_model"] == "SBE37-SM Microcat"
    assert record["sensor_model_source"].startswith(
        "2015_64PE400_NIOZ_cruise_report.pdf p.40: Instrument - MicroCat SBE 37-SMP"
    )
    assert record["sensor_metadata_source"].startswith(
        "sensor catalog: sensor_catalog.yaml (37-12046, pressure values, not flagged)"
    )
    assert any("sensor catalog's sensor_model" in r.message for r in caplog.records)
    # No calibration comes from the catalog.
    assert not [k for k in record if "calibration" in k]


def test_whether_an_instrument_measured_pressure_follows_its_flags(tmp_path):
    from oceanarray.mooring import catalog
    from oceanarray.readers import read

    def measured(name):
        return catalog.measured_pressure(read(OSNAP / name))

    assert measured(MCTD) == (True, "pressure flagged good")
    assert measured(CM) == (True, "pressure values, not flagged")
    # K7: pressure flagged 0 throughout (not quality-controlled) still counts.
    k7 = "OS_OSNAP-10633_KPO_1111_K7_201408_D_MCTD_OSNAPstyleOceanSITES.nc"
    assert measured(k7) == (True, "pressure values, not quality-controlled (flag 0)")
    ds = _ic1(tmp_path)
    no_p = ds.drop_vars([v for v in ds.data_vars if str(v).startswith("pressure")])
    assert catalog.measured_pressure(no_p) == (False, "no pressure values")
    found = catalog.lookup(
        {"sensor_catalog": str(CATALOG)}, {"instrument": "microcat", "serial": 12046}
    )
    assert (
        catalog.record_attrs(found, True)["sensor_model_vocabulary"]
        == f"{L22}TOOL1457/"
    )
    assert (
        catalog.record_attrs(found, False)["sensor_model_vocabulary"]
        == f"{L22}TOOL0018/"
    )


def test_the_description_beats_the_catalog(tmp_path):
    (record,) = sensors.records(
        _ic1(tmp_path, sensor_model="SBE 37-SM", sensor_model_source="logsheet")
    ).values()
    assert record["sensor_model"] == "SBE 37-SM"
    assert record["sensor_model_source"] == "logsheet"
    assert "sensor_model_vocabulary" not in record


def test_a_model_not_known_takes_the_series_entry():
    from oceanarray.mooring import catalog

    found = catalog.lookup(
        {"sensor_catalog": str(CATALOG)}, {"instrument": "microcat", "serial": 10227}
    )
    attrs = catalog.record_attrs(found, True)
    assert attrs["sensor_model_vocabulary"] == f"{L22}TOOL1393/"
    assert attrs["sensor_pumped"] == "true"
    assert (
        catalog.lookup(
            {"sensor_catalog": str(CATALOG)}, {"instrument": "adcp", "serial": 1}
        )
        == {}
    )


def test_validate_refuses_a_missing_or_unusable_catalog(tmp_path, mooring_yaml):
    from oceanarray import mooring

    issues = mooring.check(
        mooring_yaml({"sensor_catalog": str(tmp_path / "none.yaml")})
    )
    assert any(i.level == "ERROR" and "sensor_catalog" in i.message for i in issues)
    bad = tmp_path / "bad.yaml"
    bad.write_text('instruments:\n  "37-1": {model: SBE 37-XX}\n')
    issues = mooring.check(mooring_yaml({"sensor_catalog": str(bad)}))
    assert any("'SBE 37-XX' is not under 'models:'" in i.message for i in issues)


def test_the_export_keeps_the_license_and_citation(cf7):
    from oceanarray.pipeline.oceansites import to_oceansites

    # The current meter's file states its license (the MicroCAT's does not).
    s3 = _stage(cf7, "currentmeter", 500, 3)
    mooring = {**CF7, "name": "cf7_2020", "platform_code": "CF7", "clamp": INSTRUMENTS}
    out = to_oceansites(s3, mooring, array={"site_code": "OSNAP"})
    assert out.attrs["license"] == s3.attrs["source_license"]
    assert out.attrs["citation"].startswith(s3.attrs["source_citation"])
    with xr.open_dataset(cf7 / "cf7_2020_grid.nc", engine="netcdf4") as grid:
        attrs = dict(grid.attrs)
    assert attrs["license"] == s3.attrs["source_license"]
    assert "cf7_2020 grid built with oceanarray" in attrs["history"]


def test_the_mooring_page_lists_each_sensor_record(cf7, tmp_path):
    oa.report(
        cf7 / "cf7_2020.mooring.yaml",
        oa.Paths(proc_dir=cf7.parent).with_mooring("cf7_2020", report_dir=tmp_path),
        instruments=False,
        grid=False,
    )
    (page,) = [
        p for p in tmp_path.rglob("*.html") if "Sensor calibration" in p.read_text()
    ]
    html = page.read_text()
    assert "Originator&#39;s description" in html or "Originator's description" in html
    # The current meter's file names its model; its attributes are listed.
    assert "east_velocity_comment" in html


def test_no_clock_check_when_every_instrument_was_imported(cf7, tmp_path):
    """No figure: one line says why the check does not apply to originator data."""
    oa.report(
        cf7 / "cf7_2020.mooring.yaml",
        oa.Paths(proc_dir=cf7.parent).with_mooring("cf7_2020", report_dir=tmp_path),
        instruments=False,
        grid=False,
    )
    (page,) = tmp_path.rglob("*_stack_report.html")
    html = page.read_text()
    section = html[html.index('id="clock_check"') : html.index('id="quality_flags"')]
    assert "Not applicable: clocks were aligned by the originator" in section
    assert "<img" not in section
    assert 'id="quality_flags"' in html


def test_only_this_moorings_catalog_entries_ask_for_a_rerun(tmp_path):
    from oceanarray.mooring import catalog

    records = list(sensors.records(_ic1(tmp_path)).values())
    path = tmp_path / "sensor_catalog.yaml"
    shutil.copy(CATALOG, path)
    mooring = {"sensor_catalog": str(path)}
    before = catalog.digest(mooring, records)
    # Another instrument's entry (10227) changes: not this mooring's concern.
    path.write_text(path.read_text().replace("2022_AR69-01", "2022_AR69-01 (rev)"))
    assert catalog.digest(mooring, records) == before
    # This instrument's model entry changes: rerun.
    path.write_text(path.read_text().replace("TOOL1457", "TOOL1451"))
    assert catalog.digest(mooring, records) != before
    assert catalog.digest({}, records) == ""


def test_gaps_flagged_missing_do_not_hide_a_pressure_sensor():
    from oceanarray.mooring import catalog
    from oceanarray.readers import read

    # K7 2014: pressure never quality-controlled (flag 0); open a gap as the
    # originator would write one (no value, flag 9).
    ds = read(
        OSNAP / "OS_OSNAP-10633_KPO_1111_K7_201408_D_MCTD_OSNAPstyleOceanSITES.nc"
    )
    ds["pressure"][:10] = np.nan
    ds["pressure_qc_originator"][:10] = 9
    assert catalog.measured_pressure(ds) == (
        True,
        "pressure values, not quality-controlled (flag 0)",
    )


def test_a_catalog_entry_that_is_not_a_mapping_is_refused(tmp_path, mooring_yaml):
    from oceanarray import mooring

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        CATALOG.read_text().replace(
            '  "37-10227":\n    model: SBE 37\n',
            '  "37-9":\n  "37-10227":\n    model: SBE 37\n',
        )
    )
    issues = mooring.check(mooring_yaml({"sensor_catalog": str(bad)}))
    assert any("37-9: not a mapping" in i.message for i in issues)


def test_a_dict_description_takes_its_catalog_from_the_mooring_directory(tmp_path):
    from oceanarray.pipeline import resolve_mooring

    (tmp_path / "config").mkdir()
    shutil.copy(CATALOG, tmp_path / "config" / "sensor_catalog.yaml")
    cfg = {
        "name": tmp_path.name,
        "waterdepth": 2472,
        "latitude": 59.1,
        "longitude": -33.7,
        "deployment_time": "2016-08-09T14:20:00",
        "recovery_time": "2018-07-15T07:38:41",
        "clamp": [
            {
                "serial": "MC76",
                "instrument": "microcat",
                "file_type": "oceansites",
                "filename": IC1,
                "hab": 2396,
            }
        ],
        "sensor_catalog": "config/sensor_catalog.yaml",
    }
    loaded, _ = resolve_mooring(cfg, tmp_path)
    assert loaded["sensor_catalog"] == str(
        (tmp_path / "config" / "sensor_catalog.yaml").resolve()
    )


def test_pumped_must_be_true_or_false(tmp_path, mooring_yaml):
    from oceanarray import mooring

    bad = tmp_path / "bad.yaml"
    bad.write_text(CATALOG.read_text().replace("pumped: true", 'pumped: "false"', 1))
    issues = mooring.check(mooring_yaml({"sensor_catalog": str(bad)}))
    assert any(
        "37-12046: pumped 'false' is not true or false" in i.message for i in issues
    )
