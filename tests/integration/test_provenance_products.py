"""People and source through stage 1, the stack, the Stack cat and the grid; stale products.

Real files only: the RAPID RODB MicroCAT (no people, no source in the file), the
WHOI CF7 OceanSITES current meter (its own people, citation, license) and the egM2
2016/2018 stage-3 files.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

import oceanarray as oa
from oceanarray import mooring, paths, provenance
from oceanarray.exceptions import MooringError
from oceanarray.pipeline import stage1
from oceanarray.pipeline.concat import concat
from oceanarray.pipeline.grid import _grid_file, grid_stale_reason
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.pipeline.stack import _stack_file
from oceanarray.readers import read

FIX = Path(__file__).resolve().parents[1] / "fixtures"
RODB = FIX / "raw" / "osnap" / "rtwb1_01_2014_001.microcat"
CF7 = FIX / "raw" / "osnap" / "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc"
EGM2 = FIX / "proc" / "egM2_concat"
ADC_DOI = "10.18739/A2707WQ62"

PEOPLE = {
    "principal_investigator": "Penny Holliday",
    "contributor_name": "Penny Holliday; Stuart Cunningham",
    "contributor_role": "PI; PI",
    "people_source": "cruise report DY054",
}
SOURCE = {
    "doi": f"https://doi.org/{ADC_DOI}",
    "citation": "a citation the file does not give",
    "license": "CC-BY 4.0",
    "source_note": "Arctic Data Center package",
}


# ── stage 1 ───────────────────────────────────────────────────────────


def test_a_file_that_names_no_one_takes_the_yaml_people():
    ds = stage1._add_people(read(RODB), {"people": PEOPLE})
    assert ds.attrs["principal_investigator"] == "Penny Holliday"
    assert ds.attrs["contributor_role"] == "PI; PI"
    assert "people: from the mooring YAML (cruise report DY054" in ds.attrs["history"]


def test_a_file_that_names_its_people_keeps_them(caplog):
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        ds = stage1._add_people(read(CF7), {"people": PEOPLE})
    assert ds.attrs["principal_investigator"] == "Fiamma Straneo"
    assert "Penny Holliday" not in ds.attrs.get("contributor_name", "")
    assert any("people: block is not used" in r.message for r in caplog.records)


def test_source_comes_from_the_file_first_and_the_yaml_fills_the_rest(caplog):
    cf7 = read(CF7)
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        ds = stage1._add_source(cf7, {"source": SOURCE})
    assert ds.attrs["source_doi"] == ADC_DOI
    assert ds.attrs["source_citation"] == cf7.attrs["originator_citation"]
    assert ds.attrs["source_license"] == cf7.attrs["originator_license"]
    assert [r.message for r in caplog.records if "source" in r.message] == [
        "  source citation: the file's value is kept; the mooring YAML's "
        "'a citation the file does not give' is not used",
        "  source license: the file's value is kept; the mooring YAML's "
        "'CC-BY 4.0' is not used",
    ]
    assert (
        "source: citation, license, acknowledgement, references from the file | doi "
        "from the mooring YAML (Arctic Data Center package)"
    ) in ds.attrs["history"]


def test_an_rodb_file_takes_its_source_from_the_yaml():
    ds = stage1._add_source(read(RODB), {"source": SOURCE})
    assert ds.attrs["source_license"] == "CC-BY 4.0"
    assert ds.attrs["source_note"] == "Arctic Data Center package"


# ── stack, Stack cat, grid ────────────────────────────────────────────


def _stage3(name: str, source: dict) -> tuple[dict, dict]:
    cfg = mooring.load(EGM2 / name / f"{name}.mooring.yaml")
    data = read_mooring(cfg, stage=3, mooring_dir=EGM2 / name, allow_missing=True)
    for ds in data.values():
        ds.attrs.update(source)
    return cfg, data


@pytest.fixture(scope="module")
def stacks() -> list[xr.Dataset]:
    """egM2 2016 (NOC, CC-BY) and 2018 (Arctic Data Center, CLIVAR policy)."""
    out = []
    for name, source in (
        (
            "egM2_2016",
            {"source_citation": "NOC mooring data", "source_license": "CC-BY 4.0"},
        ),
        ("egM2_2018", {"source_doi": ADC_DOI, "source_license": "CLIVAR data policy"}),
    ):
        cfg, data = _stage3(name, source)
        out.append(oa.stack(data, cfg))
    return out


def test_the_stack_keeps_its_source_and_cites_it(stacks):
    s2016, s2018 = stacks
    assert s2016.attrs["source_license"] == "CC-BY 4.0"
    assert s2018.attrs["source_doi"] == ADC_DOI
    assert s2018.attrs["citation"] == f"doi:{ADC_DOI}"
    assert s2018.attrs["license"] == "CLIVAR data policy"
    assert "egM2_2018 stack built with oceanarray " in s2018.attrs["history"]
    assert s2018.attrs["references"] == f"doi:{ADC_DOI}"


def test_the_stack_cat_records_each_deployments_source(stacks, caplog):
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        site = concat(stacks, name="egM2")
    assert list(site["deployment_source_license"].values) == [
        "CC-BY 4.0",
        "CLIVAR data policy",
    ]
    assert list(site["deployment_source_doi"].values) == ["", ADC_DOI]
    assert site.attrs["source_license"] == "CC-BY 4.0; CLIVAR data policy"
    assert any("give 2 values of source_license" in r.message for r in caplog.records)
    assert site.attrs["citation"] == f"NOC mooring data; doi:{ADC_DOI}"
    # Each level's originator file is carried into the Stack cat.
    assert set(site["originator_file"].values) == {
        str(v) for st in stacks for v in st["originator_file"].values
    }
    assert site.attrs["license"] == "CC-BY 4.0; CLIVAR data policy"
    assert "egM2 site stack built with oceanarray" in site.attrs["history"]
    gridded = oa.grid(site, pmin=1800, pmax=2400, dp=50)
    assert gridded.attrs["citation"] == f"NOC mooring data; doi:{ADC_DOI}"
    assert "egM2 site grid built with oceanarray" in gridded.attrs["history"]


def test_the_stack_cat_deployments_table_links_each_doi(stacks, tmp_path):
    from oceanarray.reports._concat import CONCAT_PANELS, _deployment_rows

    site = concat(stacks, name="egM2")
    rows = _deployment_rows(site, tmp_path / "egM2_stack.nc", tmp_path)
    assert [str(r["doi"]) for r in rows] == [
        "",
        f'<a href="https://doi.org/{ADC_DOI}">{ADC_DOI}</a>',
    ]

    class _Context:
        deployment_rows = rows

    html = str(CONCAT_PANELS["deployments"].render(_Context()))
    assert "<th>Source DOI</th>" in html
    assert f'<a href="https://doi.org/{ADC_DOI}">' in html
    _Context.deployment_rows = [dict(r, doi="") for r in rows]
    assert "Source DOI" not in str(CONCAT_PANELS["deployments"].render(_Context()))


# ── stale products ────────────────────────────────────────────────────


@pytest.fixture
def egm2016(tmp_path) -> tuple[dict, Path]:
    folder = tmp_path / "egM2_2016"
    shutil.copytree(EGM2 / "egM2_2016", folder)
    return mooring.load(folder / "egM2_2016.mooring.yaml"), folder


def _grid(ds, cfg, folder, stack_path, **kw):
    return _grid_file(
        ds,
        cfg,
        folder,
        pmin=1800,
        pmax=2400,
        dp=50,
        stack_path=stack_path,
        force=False,
        **kw,
    )


def test_a_current_stack_and_grid_are_kept(egm2016, caplog):
    cfg, folder = egm2016
    stack_path, ds = _stack_file(cfg, folder, force=False)
    assert "stack rebuilt: no stack yet" in ds.attrs["history"]
    assert list(ds["stage_file"].values) == [
        f"{t}/egM2_2016_{s}_stage3.nc"
        for t, s in zip(ds["instrument_type"].values, ds["serial"].values, strict=True)
    ]
    assert ds["stage_sha256"].values[0] == provenance.sha256(
        folder / ds["stage_file"].values[0]
    )
    grid_path = _grid(ds, cfg, folder, stack_path)
    with xr.open_dataset(grid_path, engine="netcdf4") as g:
        assert g.attrs["stack_sha256"] == provenance.sha256(stack_path)
        assert provenance.LEGACY_COMMIT_ATTR not in g.attrs
    stamp = (stack_path.stat().st_mtime_ns, grid_path.stat().st_mtime_ns)
    with caplog.at_level(logging.INFO, logger="oceanarray"):
        _, again = _stack_file(cfg, folder, force=False)
        _grid(again, cfg, folder, stack_path)
    messages = [r.getMessage() for r in caplog.records]
    assert "egM2_2016: stack current (egM2_2016_stack.nc)" in messages
    assert "egM2_2016: grid current (egM2_2016_grid.nc)" in messages
    assert (stack_path.stat().st_mtime_ns, grid_path.stat().st_mtime_ns) == stamp


def test_a_grid_level_float32_cannot_hold_still_keeps_the_grid_current(egm2016, caplog):
    """pmin_dbar is stored as float32; 1800.3 read back still matches 1800.3."""
    cfg, folder = egm2016
    stack_path, ds = _stack_file(cfg, folder, force=False)
    kw = {"pmin": 1800.3, "pmax": 2400, "dp": 50, "stack_path": stack_path}
    grid_path = _grid_file(ds, cfg, folder, force=False, **kw)
    with xr.open_dataset(grid_path, engine="netcdf4") as g:
        assert g.attrs["pmin_dbar"].dtype == np.float32
    with caplog.at_level(logging.INFO, logger="oceanarray"):
        _grid_file(ds, cfg, folder, force=False, **kw)
    assert "egM2_2016: grid current (egM2_2016_grid.nc)" in [
        r.getMessage() for r in caplog.records
    ]


def test_a_grid_with_the_old_level_names_is_still_current(egm2016):
    """A grid written by 0.6.x names its levels p_start_dbar / p_end_dbar; it is kept."""
    cfg, folder = egm2016
    stack_path, ds = _stack_file(cfg, folder, force=False)
    grid_path = _grid(ds, cfg, folder, stack_path)
    with xr.open_dataset(grid_path, engine="netcdf4") as g:
        old = g.load()
    old.attrs["p_start_dbar"] = old.attrs.pop("pmin_dbar")
    old.attrs["p_end_dbar"] = old.attrs.pop("pmax_dbar")
    old.to_netcdf(grid_path, engine="netcdf4")
    assert grid_stale_reason(grid_path, stack_path, pmin=1800, pmax=2400, dp=50) is None
    assert (
        grid_stale_reason(grid_path, stack_path, pmin=1800, pmax=2500, dp=50)
        == "pmax_dbar changed"
    )


def test_a_new_time_step_rebuilds_the_stack_and_grid(egm2016):
    cfg, folder = egm2016
    stack_path, ds = _stack_file(cfg, folder, force=False)
    _grid(ds, cfg, folder, stack_path)
    cfg["stack"] = {**(cfg.get("stack") or {}), "dt_seconds": 1800}
    _, ds = _stack_file(cfg, folder, force=False, dt_seconds=1800)
    assert (
        "stack rebuilt: the mooring YAML's settings for the stack changed"
        in ds.attrs["history"]
    )
    grid_path = _grid(ds, cfg, folder, stack_path)
    with xr.open_dataset(grid_path, engine="netcdf4") as g:
        assert "grid rebuilt: egM2_2016_stack.nc changed" in g.attrs["history"]


def test_mask_flags_rebuild_only_the_grid(egm2016, caplog):
    cfg, folder = egm2016
    stack_path, ds = _stack_file(cfg, folder, force=False)
    _grid(ds, cfg, folder, stack_path)
    cfg["grid"] = {**(cfg.get("grid") or {}), "mask_flags": [3, 4, 9]}
    with caplog.at_level(logging.INFO, logger="oceanarray"):
        _, ds = _stack_file(cfg, folder, force=False)
    assert "egM2_2016: stack current (egM2_2016_stack.nc)" in [
        r.getMessage() for r in caplog.records
    ]
    grid_path = _grid(ds, cfg, folder, stack_path, mask_flags=[3, 4, 9])
    with xr.open_dataset(grid_path, engine="netcdf4") as g:
        assert "grid rebuilt: mask_flags changed" in g.attrs["history"]


def test_a_rewritten_stage_file_rebuilds_the_stack(egm2016):
    cfg, folder = egm2016
    _stack_file(cfg, folder, force=False)
    stage = folder / "microcat" / "egM2_2016_11331_stage3.nc"
    with xr.open_dataset(stage, engine="netcdf4") as ds:
        changed = ds.load()
    changed.attrs["comment"] = "rewritten"
    oa.write(changed, stage, overwrite=True)
    _, ds = _stack_file(cfg, folder, force=False)
    assert (
        "stack rebuilt: stage file changed: microcat/egM2_2016_11331_stage3.nc"
        in ds.attrs["history"]
    )


def test_force_rebuilds_a_current_stack(egm2016):
    cfg, folder = egm2016
    _stack_file(cfg, folder, force=False)
    _, ds = _stack_file(cfg, folder, force=True)
    assert "stack rebuilt: rebuild forced" in ds.attrs["history"]


def test_the_stack_path_is_the_mooring_product(egm2016):
    cfg, folder = egm2016
    path, _ = _stack_file(cfg, folder, force=False)
    assert path == paths.product_file(folder, "egM2_2016", "stack")


# ── sensor records ────────────────────────────────────────────────────

DUNE2 = FIX / "proc" / "dune2_1_2026"


@pytest.fixture(scope="module")
def dune2_run(tmp_path_factory) -> Path:
    """dune2 run from its raw files: a Sea-Bird MicroCAT (2941) and an Aquadopp (9920)."""
    folder = tmp_path_factory.mktemp("d2") / "dune2_1_2026"
    folder.mkdir()
    shutil.copy(DUNE2 / "dune2_1_2026.mooring.yaml", folder)
    oa.run(
        folder / "dune2_1_2026.mooring.yaml",
        oa.Paths(raw_dir=FIX / "raw", proc_dir=folder.parent),
        dt_seconds=3600,
    )
    return folder


def test_a_seabird_microcat_has_one_ctd_record(dune2_run):
    with xr.open_dataset(
        dune2_run / "microcat" / "dune2_1_2026_2941_stage1.nc", engine="netcdf4"
    ) as ds:
        assert [v for v in ds.variables if v.startswith("SENSOR_")] == [
            "SENSOR_CTD_2941"
        ]
        rec = ds["SENSOR_CTD_2941"].attrs
        assert ds["temperature"].attrs["sensor"] == "SENSOR_CTD_2941"
    assert rec["sensor_type"] == "CTD"
    assert rec["sensor_maker"] == "Sea-Bird Scientific"
    assert rec["sensor_model"] == "UNK"  # the header names no model; nothing inferred
    assert rec["sensor_calibration_date"] == "2023-03-28"
    assert (
        rec["sensor_calibration_dates"]
        == "temperature: 2023-03-28; conductivity: 2023-03-28"
    )
    assert rec["temperature_calibration_coefficients"].startswith("TA0=6.981772e-06")


def test_the_stack_keeps_the_records_and_links_each_level(dune2_run):
    with xr.open_dataset(dune2_run / "dune2_1_2026_stack.nc", engine="netcdf4") as st:
        assert st["SENSOR_CTD_2941"].dims == ()
        assert dict(zip(st["serial"].values, st["sensor"].values, strict=True)) == {
            "9920": "SENSOR_AQUADOPP_9920",
            "2941": "SENSOR_CTD_2941",
        }


def test_the_report_lists_each_calibrated_sensor(dune2_run):
    from oceanarray.reports._read import _read_sensor_info

    rows = _read_sensor_info(dune2_run, "microcat", "dune2_1_2026", "2941")
    assert [(r["sensor_type"], r["cal_date"]) for r in rows] == [
        ("TEMPERATURE", "2023-03-28"),
        ("CONDUCTIVITY", "2023-03-28"),
    ]


def test_the_export_takes_the_model_from_the_record(dune2_run):
    from oceanarray.pipeline.oceansites import _sensor_attrs

    with xr.open_dataset(
        dune2_run / "microcat" / "dune2_1_2026_2941_stage3.nc", engine="netcdf4"
    ) as ds:
        attrs = _sensor_attrs(ds.load(), "temperature", "a", "b")
    assert attrs["sensor_model"] == "UNK"
    assert attrs["sensor_manufacturer"] == "Sea-Bird Scientific"
    assert attrs["sensor_calibration_date"] == "2023-03-28"


def test_an_imported_file_keeps_its_sensor_text_and_the_yaml_corrects_the_model(caplog):
    from oceanarray.pipeline import sensors

    cf7 = read(CF7)
    file_model = cf7["east_velocity"].attrs["originator_sensor_model"]
    entry = {
        "instrument": "aquadopp",
        "sensor_model": "Nortek Aquadopp 3000 m",
        "sensor_model_source": "WHOI AR84-01 cruise report",
    }
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        ds = sensors.build_record(cf7, entry, "CM500")
    rec = ds["SENSOR_AQUADOPP_CM500"].attrs
    assert rec["sensor_model"] == "Nortek Aquadopp 3000 m"
    # The file describes each variable's sensor: both are kept, in variable order.
    assert rec["originator_sensor_model"] == f"Piezoresistive; {file_model}"
    assert rec["sensor_metadata_source"] == "mooring YAML: WHOI AR84-01 cruise report"
    assert any("replaces the file's" in r.message for r in caplog.records)


def test_an_rodb_file_has_no_model_unless_the_yaml_gives_one():
    from oceanarray.pipeline import sensors

    ds = sensors.build_record(read(RODB), {"instrument": "microcat"}, "001")
    assert ds["SENSOR_CTD_001"].attrs["sensor_model"] == "UNK"
    assert ds["SENSOR_CTD_001"].attrs["sensor_metadata_source"] == "none"


def test_a_yaml_model_needs_its_source(mooring_yaml):
    data = mooring.load(EGM2 / "egM2_2016" / "egM2_2016.mooring.yaml")
    data["clamp"][0]["sensor_model"] = "SBE 37-SMP"
    errors = [
        i.message for i in mooring.check(mooring_yaml(data)) if i.level == "ERROR"
    ]
    assert errors == [
        "microcat s/n 11331: sensor_model needs sensor_model_source (where the model "
        "came from, e.g. the cruise report)."
    ]


def _sensor_stacks() -> list[xr.Dataset]:
    """egM2 2016 and 2018 stacked from stage files given their sensor records."""
    from oceanarray.pipeline import sensors

    stacks = []
    for name in ("egM2_2016", "egM2_2018"):
        cfg, data = _stage3(name, {})
        for serial, ds in data.items():
            entry = next(
                e for e in cfg["clamp"] if str(e["serial"]).replace(" ", "") == serial
            )
            data[serial] = sensors.build_record(ds, entry, serial)
        stacks.append(oa.stack(data, cfg))
    return stacks


def test_the_stack_cat_names_each_record_by_deployment_code():
    """With egM2 2016's real start (2016-08-01), each record ends in its deployment code."""
    stacks = _sensor_stacks()
    stacks[0].attrs["deployment_time"] = "2016-08-01T00:00:00"
    site = concat(stacks, name="egM2")
    links = dict(zip(site["level_id"].values, site["sensor"].values, strict=True))
    assert links["egM2_2016/11331"] == "SENSOR_CTD_11331_201608"
    assert links["egM2_2018/13220"] == "SENSOR_CTD_13220_201806"
    assert all(str(v) in site.variables for v in site["sensor"].values if str(v))


def test_two_deployments_in_one_month_are_named_by_deployment():
    """The trimmed fixtures both start in June 2018: the deployment names keep them apart."""
    site = concat(_sensor_stacks(), name="egM2")
    links = dict(zip(site["level_id"].values, site["sensor"].values, strict=True))
    assert links["egM2_2016/11331"] == "SENSOR_CTD_11331_egM2_2016"
    assert links["egM2_2018/13220"] == "SENSOR_CTD_13220_egM2_2018"


def test_a_stack_from_older_stage_files_keeps_every_calibration():
    """A 0.6.0 stage file (one variable per sensor): one record, all its calibrations."""
    with xr.open_dataset(
        FIX / "proc" / "before_sensor_records" / "dune2_1_2026_2941_stage3.nc",
        engine="netcdf4",
    ) as ds:
        old = ds.load()
    assert {"SENSOR_TEMP_2941", "SENSOR_CNDC_2941"} <= set(old.variables)
    cfg = mooring.load(DUNE2 / "dune2_1_2026.mooring.yaml")
    st = oa.stack({"2941": old}, cfg)
    assert list(st["sensor"].values) == ["SENSOR_CTD_2941"]
    rec = st["SENSOR_CTD_2941"].attrs
    assert (
        rec["sensor_calibration_dates"]
        == "temperature: 2023-03-28; conductivity: 2023-03-28"
    )
    assert "sensor" not in st["temperature"].attrs


def test_the_report_reads_the_calibration_dates_of_older_files(tmp_path):
    """A 0.6.0 stage file: the sensor table still shows each sensor's date."""
    from oceanarray.reports._read import _read_sensor_info

    folder = tmp_path / "microcat"
    folder.mkdir()
    shutil.copy(
        FIX / "proc" / "before_sensor_records" / "dune2_1_2026_2941_stage3.nc",
        folder / "dune2_1_2026_2941_stage2.nc",
    )
    rows = _read_sensor_info(tmp_path, "microcat", "dune2_1_2026", "2941")
    assert {(r["sensor_type"], r["cal_date"]) for r in rows} == {
        ("TEMPERATURE", "2023-03-28"),
        ("CONDUCTIVITY", "2023-03-28"),
    }


# ── review fixes ──────────────────────────────────────────────────────

BEFORE = FIX / "proc" / "before_sensor_records" / "dune2_1_2026_2941_stage3.nc"


def test_a_changed_source_block_is_flagged_not_silently_kept(egm2016, caplog):
    """people:/source: reach the products through stage 1; the stack says so."""
    cfg, folder = egm2016
    _stack_file(cfg, folder, force=False)
    cfg["source"] = {"doi": ADC_DOI, "source_note": "corrected DOI"}
    with caplog.at_level(logging.INFO, logger="oceanarray"):
        _stack_file(cfg, folder, force=False)
    messages = [r.getMessage() for r in caplog.records]
    assert "egM2_2016: stack current (egM2_2016_stack.nc)" in messages
    assert any(
        "people:/source: changed after stage 1" in m and "--force" in m
        for m in messages
    )


def test_a_stack_records_its_people_and_source_blocks(egm2016, caplog):
    cfg, folder = egm2016
    cfg["source"] = {"doi": ADC_DOI, "source_note": "Arctic Data Center package"}
    _stack_file(cfg, folder, force=False)
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        _stack_file(cfg, folder, force=False)
    assert not any("people:/source:" in r.getMessage() for r in caplog.records)


def test_a_stage_file_that_could_not_be_read_is_tried_again(egm2016):
    cfg, folder = egm2016
    stage = folder / "microcat" / "egM2_2016_11331_stage3.nc"
    good = stage.read_bytes()
    stage.write_bytes(b"not a netCDF file")
    _, ds = _stack_file(cfg, folder, force=False, keep_going=True)
    assert "11331" in ds.attrs["instruments_omitted"]
    assert "11331" not in ds.attrs.get("stage_files_not_stacked", "")
    stage.write_bytes(good)
    _, ds = _stack_file(cfg, folder, force=False, keep_going=True)
    assert (
        "stack rebuilt: new stage file: microcat/egM2_2016_11331_stage3.nc"
        in ds.attrs["history"]
    )


def test_coefficients_are_matched_to_their_sensor():
    from oceanarray.pipeline import sensors

    with xr.open_dataset(BEFORE, engine="netcdf4") as ds:
        old = ds.load()
    # A temperature variable that also carries the conductivity coefficients.
    old["SENSOR_TEMP_2941"].attrs["CONDUCTIVITY_calibration_coefficients"] = old[
        "SENSOR_CNDC_2941"
    ].attrs["CONDUCTIVITY_calibration_coefficients"]
    old["SENSOR_TEMP_2941"].attrs = dict(
        reversed(list(old["SENSOR_TEMP_2941"].attrs.items()))
    )
    rec = sensors.build_record(old, {"instrument": "microcat"}, "2941")[
        "SENSOR_CTD_2941"
    ].attrs
    assert rec["temperature_calibration_coefficients"].startswith("TA0=")
    assert rec["conductivity_calibration_coefficients"].startswith("G=")


def test_an_instrument_without_a_serial_gets_no_record():
    from oceanarray.pipeline import sensors

    with xr.open_dataset(BEFORE, engine="netcdf4") as ds:
        old = ds.load()
    with pytest.raises(MooringError, match="no serial"):
        sensors.build_record(old, {"instrument": "microcat"}, "")


def test_stage3_variables_name_the_instruments_record():
    """Derived at stage 3 → the file's record; pressure from a neighbor → none."""
    from oceanarray.pipeline import sensors
    from oceanarray.pipeline.oceansites import _sensor_attrs

    with xr.open_dataset(
        DUNE2 / "aquadopp" / "dune2_1_2026_9920_stage3.nc", engine="netcdf4"
    ) as ds:
        adcp = sensors.link_derived(ds.load())
    assert adcp["east_velocity"].attrs["sensor"] == "SENSOR_AQUADOPP_9920"
    assert "sensor" not in adcp["east_velocity_qc"].attrs
    with xr.open_dataset(
        DUNE2 / "microcat" / "dune2_1_2026_2941_stage3.nc", engine="netcdf4"
    ) as ds:
        ctd = ds.load()
    assert sensors.record_of(ctd, "salinity")["sensor_serial_number"] == "2941"
    assert sensors.record_of(ctd, "pressure") == {}
    ctd = sensors.link_derived(ctd)
    assert ctd["salinity"].attrs["sensor"] == "SENSOR_CTD_2941"
    assert "sensor" not in ctd["pressure"].attrs
    attrs = _sensor_attrs(ctd, "salinity", "2026-01-01", "2026-02-01")
    assert attrs["sensor_calibration_date"] == "2023-03-28"
