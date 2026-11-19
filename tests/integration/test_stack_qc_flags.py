"""The stack keeps flagged values; originator flags translated by meaning; mask_flags.

Real data: the OSNAP CF7 2020 MicroCAT (``PSAL_QC`` with the "appears_*" table and
two 3s) and the CF7 2020 current meter (no originator flags), from
``tests/fixtures/raw/osnap``.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
import yaml

import oceanarray as oa
from oceanarray.exceptions import MooringError
from oceanarray.pipeline import masking
from oceanarray.pipeline.qc import originator
from oceanarray.readers import read

pytest.importorskip("seasenselib", reason="seasenselib not installed")

OSNAP = Path(__file__).parent.parent / "fixtures" / "raw" / "osnap"
MCTD = "CF7_MCTD_2020_22225_50m.nc"
CM = "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc"
CF7 = {
    "waterdepth": 1901,
    "latitude": "59 55.576 N",
    "longitude": "041 25.885 W",
    "deployment_time": "2020-07-12T15:00",
    "recovery_time": "2020-07-17T14:00",
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


def _copy_with(tmp_path: Path, name: str, edit) -> Path:
    """Write a copy of the MicroCAT file with *edit* applied to the open dataset."""
    with xr.open_dataset(OSNAP / MCTD, engine="netcdf4") as ds:
        ds = ds.load()
    ds = edit(ds)
    out = tmp_path / name
    ds.to_netcdf(out)
    return out


@pytest.fixture(scope="module")
def cf7(tmp_path_factory):
    """The CF7 2020 mooring (MicroCAT + current meter), run at a 900 s step."""
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
    files = oa.run(yaml_path, raw_dir=tmp / "raw", pmin=40, pmax=520, dp=40)
    return yaml_path, proc, files


def _level(ds: xr.Dataset, serial: str) -> int:
    return list(ds["serial"].values).index(serial)


# ── reader and translation ────────────────────────────────────────────


def test_the_reader_names_the_flag_variable_it_kept():
    ds = read(OSNAP / MCTD)
    assert ds["salinity_qc_originator"].attrs["originator_flag_variable"] == "PSAL_QC"


EGM2 = "M2_MC_2018_2020_SN14644_2190m_cut.nc"


def test_a_flag_variable_named_flag_is_used_when_it_is_one(tmp_path, caplog):
    """egM2 2018: no PSAL_QC but a PSAL_flag with known words: used, and named."""
    ds = read(OSNAP / EGM2)
    assert ds["salinity_qc_originator"].attrs["originator_flag_variable"] == "PSAL_flag"
    flags, entries, counts = originator.translate(ds["salinity_qc_originator"])
    assert counts == {1: 172, 4: 2, 5: 12, 9: 4}
    assert (flags == 5).sum() == 12 and (flags == 4).sum() == 2
    out, note = originator.add_mapped(ds.copy(), label="14644")
    mapped = out["salinity_qc_originator_mapped"]
    assert mapped.attrs["originator_flag_variable"] == "PSAL_flag"
    assert "value_changed" in mapped.attrs["comment"]
    assert "salinity: 1->1 (172), 4->4 (2), 5->5 (12), 9->9 (4)" in note

    def unpaired(ds):
        ds = ds.rename({"PSAL_QC": "PSAL_flag"})  # the CF7 file, named as egM2's
        del ds["PSAL_flag"].attrs["flag_meanings"]
        return ds

    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        ds = read(_copy_with(tmp_path, "noflags.nc", unpaired))
    assert "salinity_qc_originator" not in ds
    assert "PSAL_flag not used as flags: no flag_values / flag_meanings" in caplog.text


def test_flags_are_translated_by_word_not_number(tmp_path, caplog):
    ds = read(OSNAP / MCTD)
    flags, entries, counts = originator.translate(ds["salinity_qc_originator"])
    assert counts == {1: 478, 2 + 1: 2}
    assert (flags == 3).sum() == 2
    assert (3, "appears_probably_bad", 3) in entries
    assert (5, "modified_as_a_result_of_qc", 5) in entries
    assert (7, "reseved", None) in entries

    # The same words under other numbers: 3 is now "appears_bad", so our 4.
    def reorder(ds):
        words = ds["PSAL_QC"].attrs["flag_meanings"].split()
        words[3], words[4] = words[4], words[3]
        ds["PSAL_QC"].attrs["flag_meanings"] = " ".join(words)
        return ds

    moved = read(_copy_with(tmp_path, "reordered.nc", reorder))
    flags, _, _ = originator.translate(moved["salinity_qc_originator"])
    assert (flags == 4).sum() == 2 and (flags == 3).sum() == 0

    # Counts that do not pair: no translation, 0 with a warning.
    def short(ds):
        ds["PSAL_QC"].attrs["flag_values"] = ds["PSAL_QC"].attrs["flag_values"][:-1]
        return ds

    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        flags, entries, _ = originator.translate(
            read(_copy_with(tmp_path, "short.nc", short))["salinity_qc_originator"],
            label="22225 salinity",
        )
    assert entries == [] and not flags.any()
    assert "22225 salinity: originator flags with no known meaning" in caplog.text


def test_the_vocabulary_never_gives_7_or_8():
    meanings, unmapped = originator.vocabulary()
    assert not {7, 8} & set(meanings.values())
    assert {"nominal_value", "interpolated_value", "reseved"} <= unmapped


# ── stage 3 ───────────────────────────────────────────────────────────


def test_stage3_writes_the_translated_flags_beside_ours(cf7):
    _, proc, _ = cf7
    path = proc / "microcat" / "cf7_2020_22225_stage3.nc"
    with xr.open_dataset(path, engine="netcdf4") as ds:
        ds = ds.load()
    mapped = ds["salinity_qc_originator_mapped"]
    assert (mapped.values == 3).sum() == 2
    assert 5 in mapped.attrs["flag_values"]
    assert "value_changed" in mapped.attrs["flag_meanings"]
    counts = json.loads(mapped.attrs["flag_translation_counts"])
    assert counts["3"] == 2 and set(counts) == {"1", "3"}  # stage 2 trimmed some 1s
    assert "3 appears_probably_bad -> 3" in mapped.attrs["flag_translation"]
    assert "originator flags translated by meaning" in ds.attrs["history"]
    assert (
        ds["salinity"].attrs["processing_level"] == "Ranges applied, bad data flagged"
    )
    with xr.open_dataset(
        proc / "currentmeter" / "cf7_2020_500_stage3.nc", engine="netcdf4"
    ) as cm:
        assert cm.attrs["originator_flag_meanings"] == "none"


# ── the stack ─────────────────────────────────────────────────────────


def test_the_stack_keeps_flagged_values_with_both_flags(cf7):
    _, proc, files = cf7
    with xr.open_dataset(files[-2], engine="netcdf4") as ds:
        st = ds.load()
    k = _level(st, "22225")
    mapped = st["salinity_qc_originator_mapped"].isel(N_LEVELS=k).values
    sal = st["salinity"].isel(N_LEVELS=k).values
    assert (mapped == 3).sum() == 2
    assert np.isfinite(sal[mapped == 3]).all()  # kept, not masked
    # value and flags from the same sample: the step at 2020-07-16 11:00
    step = np.flatnonzero(st["time"].values == np.datetime64("2020-07-16T11:00"))
    assert mapped[step] == 3
    with xr.open_dataset(
        proc / "microcat" / "cf7_2020_22225_stage3.nc", engine="netcdf4"
    ) as s3:
        sample = s3["salinity"].sel(time="2020-07-16T11:00:04", method="nearest")
        assert sal[step] == pytest.approx(float(sample))
    # No present value is ever 9; a value with no flag variable is 0.
    for flag in ("salinity_qc", "salinity_qc_originator_mapped", "temperature_qc"):
        parent = st[flag.removesuffix("_qc_originator_mapped").removesuffix("_qc")]
        assert not ((st[flag].values == 9) & np.isfinite(parent.values)).any(), flag
    cm = _level(st, "500")
    pres = st["pressure"].isel(N_LEVELS=cm).values
    p_mapped = st["pressure_qc_originator_mapped"].isel(N_LEVELS=cm).values
    assert np.isfinite(pres).any()
    assert set(np.unique(p_mapped[np.isfinite(pres)])) == {0}
    assert set(np.unique(p_mapped[~np.isfinite(pres)])) <= {9}
    assert st["salinity_flag_translation"].values[cm] == "none"
    assert "appears_probably_bad" in st["salinity_flag_translation"].values[k]
    assert json.loads(st.attrs["stack_mask_flags"]) == {"default": [4]}


def test_the_default_grids_flag_3_and_a_rule_can_hide_it(cf7):
    _, _, files = cf7
    with xr.open_dataset(files[-2], engine="netcdf4") as ds:
        st = ds.load()
    k = _level(st, "22225")
    st = st.transpose("N_LEVELS", "time")
    hidden = masking.masked(st, "salinity", {"default": (3, 4)})[k]
    shown = masking.masked(st, "salinity")[k]
    mapped = st["salinity_qc_originator_mapped"].isel(N_LEVELS=k).values
    assert np.isfinite(shown[mapped == 3]).all()
    assert np.isnan(hidden[mapped == 3]).all()
    assert (
        np.isfinite(hidden[mapped == 1]).sum() == np.isfinite(shown[mapped == 1]).sum()
    )


def test_a_grid_rule_may_add_flags_but_not_drop_them(cf7):
    _, _, files = cf7
    with xr.open_dataset(files[-2], engine="netcdf4") as ds:
        st = ds.load()
    st.attrs["stack_mask_flags"] = json.dumps({"default": [3, 4]})
    with pytest.raises(ValueError, match="the grid can only add flags"):
        oa.grid(st, pmin=40, pmax=520, dp=40, mask_flags=[4])
    out = oa.grid(st, pmin=40, pmax=520, dp=40, mask_flags=[3, 4, 9])
    assert json.loads(out.attrs["grid_mask_flags"]) == {"default": [3, 4, 9]}


def test_a_new_rule_rebuilds_the_stack(cf7, caplog):
    from oceanarray import mooring
    from oceanarray.pipeline.stack import _stack_file

    yaml_path, proc, _ = cf7
    cfg = mooring.load(yaml_path)
    with caplog.at_level(logging.INFO, logger="oceanarray"):
        _, ds = _stack_file(cfg, proc, force=False)
    assert "cf7_2020: stack current (cf7_2020_stack.nc)" in caplog.text
    _, ds = _stack_file(cfg, proc, force=False, mask_flags={"salinity": [3, 4]})
    assert "stack rebuilt: mask_flags changed" in ds.attrs["history"]
    assert json.loads(ds.attrs["stack_mask_flags"])["salinity"] == [3, 4]
    _stack_file(cfg, proc, force=False)  # back to the description's rule


# ── interpolated pressure ─────────────────────────────────────────────


def test_pressure_is_never_interpolated_from_a_bad_input():
    """Source samples flagged 4 are not used: those times come out missing."""
    from oceanarray.pipeline.pressure import _flag_summary, _source_pressure

    ds = read(OSNAP / MCTD)
    ds["pressure_qc"] = xr.Variable("time", np.ones(ds.sizes["time"], np.int8))
    ds["pressure_qc"].values[100:110] = 4
    ds["pressure_qc"].values[200] = 3
    src = {"ds": ds, "pressure_var": "pressure", "serial": "22225"}
    target = ds["time"].values
    p = _source_pressure(src, target)
    assert np.isnan(p[100:110]).all()
    assert np.isfinite(p[200]) and np.isfinite(p[:100]).all()
    assert _flag_summary(src) == "22225: 1 (469), 3 (1), 4 (10, not used)"


# ── concat ────────────────────────────────────────────────────────────


def test_concat_refuses_different_rules_unless_given(cf7):
    from oceanarray.pipeline.concat import concat

    _, _, files = cf7
    with xr.open_dataset(files[-2], engine="netcdf4") as ds:
        a = ds.load()
    b = a.copy()
    b["time"] = a["time"].values + np.timedelta64(10, "D")
    b.attrs = {
        **a.attrs,
        "mooring_name": "cf7_2020b",
        "stack_mask_flags": '{"default": [3, 4]}',
    }
    with pytest.raises(MooringError, match="hide different flags"):
        concat([a, b], name="CF7")
    site = concat([a, b], name="CF7", mask_flags=[3, 4])
    assert json.loads(site.attrs["stack_mask_flags"]) == {"default": [3, 4]}
    assert list(site["deployment_stack_mask_flags"].values) == [
        '{"default": [4]}',
        '{"default": [3, 4]}',
    ]
    # A deployment without the originator's flags: 0 where it has a value, 9 where not.
    c = b.drop_vars([v for v in b.data_vars if "originator" in v or "translation" in v])
    c.attrs["stack_mask_flags"] = '{"default": [4]}'
    site = concat([a, c], name="CF7")
    mapped = site["salinity_qc_originator_mapped"].values
    sal = site["salinity"].values
    assert not ((mapped == 9) & np.isfinite(sal)).any()
    assert (mapped[np.isfinite(sal)] == 0).any()
    assert "none" in site["salinity_flag_translation"].values


# ── the pages ─────────────────────────────────────────────────────────


def test_the_stack_page_has_the_quality_flags_section(cf7, tmp_path):
    from oceanarray.reports._qcflags import count_rows, translation_tables

    yaml_path, proc, files = cf7
    with xr.open_dataset(files[-2], engine="netcdf4") as ds:
        st = ds.load()
    rows, unflagged, quiet = count_rows(st)
    sal = next(r for r in rows if r["variable"] == "salinity")
    assert sal["level"] == "MicroCAT 22225 · 50 m"
    assert sal["hidden"] == 0
    three = next(p for p in sal["originator"]["parts"] if p["flag"] == 3)
    assert three["text"].endswith("(2)")
    assert sal["only_originator"] >= 2
    assert all(r["level"] not in unflagged for r in rows)
    assert quiet > 0
    hidden, _, _ = count_rows(st.assign_attrs(stack_mask_flags='{"default": [3, 4]}'))
    assert next(r for r in hidden if r["variable"] == "salinity")["hidden"] >= 2
    tables = translation_tables(st)
    row = next(r for t in tables for r in t["rows"] if r["number"] == 3)
    assert (row["word"], row["ours"]) == ("appears_probably_bad", 3)
    assert "22225" in row["used_by"]

    page = oa.report(yaml_path, out_dir=tmp_path, instruments=False, grid=False)
    html = (page.parent / "cf7_2020_stack_report.html").read_text(encoding="utf-8")
    assert "Quality flags" in html
    assert "leave out (rule 4)" in html
    assert "Values flagged bad, by the originator or oceanarray, are left out" in html
    assert "3 appears_probably_bad" in html


# ── review fixes ──────────────────────────────────────────────────────

DUNE2 = Path(__file__).parent.parent / "fixtures" / "proc" / "dune2_1_2026"


def _dune2_stage3() -> dict[str, xr.Dataset]:
    out = {}
    for path in sorted(DUNE2.glob("*/dune2_1_2026_*_stage3.nc")):
        with xr.open_dataset(path, engine="netcdf4") as ds:
            out[path.stem.split("_")[-2]] = ds.load()
    return out


def test_an_omitted_first_level_keeps_the_levels_aligned():
    """The Aquadopp (level 0) cannot be stacked; the MicroCAT's level is intact."""
    from oceanarray import mooring

    cfg = mooring.load(DUNE2 / "dune2_1_2026.mooring.yaml")
    data = _dune2_stage3()
    data["9920"]["temperature"].attrs["units"] = "Kelvin"
    st = oa.stack(data, cfg)
    assert "9920" in st.attrs["instruments_omitted"]
    assert list(st["serial"].values) == ["2941"]


def test_a_present_value_is_never_9_in_the_stack(cf7):
    """An originator 9 on a value that is there reads 0 in the stack."""
    from oceanarray import mooring

    yaml_path, proc, _ = cf7
    cfg = mooring.load(yaml_path)
    data = {}
    for serial, inst in (("22225", "microcat"), ("500", "currentmeter")):
        path = proc / inst / f"cf7_2020_{serial}_stage3.nc"
        with xr.open_dataset(path, engine="netcdf4") as ds:
            data[serial] = ds.load()
    data["22225"]["salinity_qc_originator_mapped"].values[:5] = 9
    st = oa.stack(data, cfg).transpose("N_LEVELS", "time")
    k = _level(st, "22225")
    sal = st["salinity"].values[k]
    flags = st["salinity_qc_originator_mapped"].values[k]
    assert not ((flags == 9) & np.isfinite(sal)).any()


def test_mask_flags_from_the_call_adds_to_the_yaml_grid_rule():
    rule = masking.union({"default": (3, 4)}, {"default": (4, 9)})
    assert rule == {"default": (3, 4, 9)}
    assert masking.union({"default": (4,)}, {"velocity": (3,)})["velocity"] == (3, 4)


def test_the_caption_names_where_the_description_changes_thresholds(cf7):
    from oceanarray import mooring
    from oceanarray.reports._qcflags import threshold_note

    yaml_path, _, _ = cf7
    cfg = mooring.load(yaml_path)
    assert threshold_note(cfg) == ""
    cfg["qc_spike"] = {"salinity": {"suspect_threshold": 0.02}}
    cfg["clamp"][0]["qc_ranges"] = {"temperature": {"fail_span": [-2, 30]}}
    assert threshold_note(cfg) == "the mooring (qc_spike); 22225 (qc_ranges)"


def test_a_stack_without_a_rule_is_gridded_with_the_default_and_says_so(cf7, caplog):
    _, _, files = cf7
    with xr.open_dataset(files[-2], engine="netcdf4") as ds:
        st = ds.load()
    del st.attrs["stack_mask_flags"]
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        out = oa.grid(st, pmin=40, pmax=520, dp=40)
    assert "the stack records no stack_mask_flags; the grid uses the default [4]" in (
        caplog.text
    )
    assert json.loads(out.attrs["grid_mask_flags"]) == {"default": [4]}


def test_a_grid_rule_that_drops_a_flag_stops_with_the_mooring_named(cf7):
    from oceanarray import mooring
    from oceanarray.pipeline.grid import _grid_file

    yaml_path, proc, files = cf7
    cfg = mooring.load(yaml_path)
    with xr.open_dataset(files[-2], engine="netcdf4") as ds:
        st = ds.load()
    st.attrs["stack_mask_flags"] = '{"default": [3, 4]}'
    before = (proc / "cf7_2020_grid.nc").read_bytes()
    with pytest.raises(MooringError, match="cf7_2020: .*the grid can only add flags"):
        _grid_file(st, cfg, proc, pmin=40, pmax=520, dp=40, mask_flags=[4])
    assert (proc / "cf7_2020_grid.nc").read_bytes() == before  # the grid is kept


def test_a_present_value_flagged_missing_is_reported(cf7, caplog):
    from oceanarray import mooring

    yaml_path, proc, _ = cf7
    cfg = mooring.load(yaml_path)
    data = {}
    for serial, inst in (("22225", "microcat"), ("500", "currentmeter")):
        with xr.open_dataset(
            proc / inst / f"cf7_2020_{serial}_stage3.nc", engine="netcdf4"
        ) as ds:
            data[serial] = ds.load()
    data["22225"]["salinity_qc_originator_mapped"].values[:5] = 9
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        oa.stack(data, cfg)
    assert (
        "salinity_qc_originator_mapped values that are there were flagged missing"
        in (caplog.text)
    )


def test_concat_fills_velocity_flag_from_any_velocity_component(cf7):
    """A deployment with north velocity but no east velocity or velocity_flag."""
    from oceanarray.pipeline.concat import concat

    _, _, files = cf7
    with xr.open_dataset(files[-2], engine="netcdf4") as ds:
        a = ds.load()
    b = a.copy()
    b["time"] = a["time"].values + np.timedelta64(10, "D")
    b.attrs = {**a.attrs, "mooring_name": "cf7_2020b"}
    b = b.drop_vars(["velocity_flag", "east_velocity"])
    site = concat([a, b], name="CF7")
    second = site["time"].values >= b["time"].values[0]
    north = site["north_velocity"].values[second]
    flag = site["velocity_flag"].values[second]
    assert np.isfinite(north).any()
    assert not ((flag == 9) & np.isfinite(north)).any()


def test_used_by_says_all_only_when_every_user_has_the_number():
    from oceanarray.reports._qcflags import _used_by

    users = {"a": "microcat", "b": "microcat", "c": "microcat"}
    two = {"a": ["T", "S"], "b": ["T", "S"]}
    assert _used_by(two, users) == "a: T, S; b: T, S"
    three = {**two, "c": ["T", "S"]}
    assert _used_by(three, users) == "all 3 microcats: T, S"
    mixed = {**users, "c": "adcp"}
    assert _used_by(three, mixed) == "all 3 instruments: T, S"


# ── magnetic declination and the flag strip ───────────────────────────


def _cf7_stack(cf7) -> xr.Dataset:
    _, _, files = cf7
    with xr.open_dataset(files[-2], engine="netcdf4") as ds:
        return ds.load()


def test_the_stack_records_the_originators_declination(cf7):
    """The CF7 current meter came in true north: the stack says the originator did it."""
    st = _cf7_stack(cf7)
    var = st["originator_magnetic_declination"]
    assert "applied by the originator" in str(var.values[_level(st, "500")])
    assert str(var.values[_level(st, "22225")]) == ""
    assert {"long_name", "comment"} <= set(var.attrs)
    assert not np.isfinite(st["magnetic_declination"].values).any()


def test_no_declination_warning_when_the_originator_applied_it(cf7, tmp_path):
    """Even labelled an Aquadopp (as CF2 2014's are), originator ENU warns of nothing."""
    from oceanarray.reports._stack import declination_unapplied

    st = _cf7_stack(cf7)
    assert declination_unapplied(st) == []
    types = st["instrument_type"].values.copy()
    types[_level(st, "500")] = "aquadopp"
    st["instrument_type"] = ("N_LEVELS", types)
    assert declination_unapplied(st) == []
    # A stack written before the variable: the originator's file stands for it.
    old = st.drop_vars("originator_magnetic_declination")
    assert declination_unapplied(old) == []
    # Neither oceanarray nor the originator: named, with why.
    none = old.drop_vars("originator_file")
    assert declination_unapplied(none) == [
        ("Aquadopp s/n 500", "its stage-3 file records no declination")
    ]
    yaml_path, _, _ = cf7
    page = oa.report(yaml_path, out_dir=tmp_path, instruments=False, grid=False)
    html = (page.parent / "cf7_2020_stack_report.html").read_text(encoding="utf-8")
    assert "as provided by the originator" in html
    assert "declination was not applied" not in html
    assert "latitude/longitude are missing" not in html


def test_the_declination_warning_names_an_instrument_with_no_position():
    """dune2: oceanarray applied it to 9920; with no position at stage 3, a warning."""
    from oceanarray import mooring
    from oceanarray.reports._env import render_template
    from oceanarray.reports._stack import declination_unapplied

    cfg = mooring.load(DUNE2 / "dune2_1_2026.mooring.yaml")
    data = _dune2_stage3()
    assert declination_unapplied(oa.stack(data, cfg)) == []
    data["9920"].attrs["magnetic_declination"] = "UNK"
    unapplied = declination_unapplied(oa.stack(data, cfg))
    assert unapplied == [("Aquadopp s/n 9920", "no mooring position at stage 3")]
    html = render_template("_stack_roses_note.html", note=None, unapplied=unapplied)
    assert "Aquadopp s/n 9920 (no mooring position at stage 3)" in html
    assert "seabed_latitude" in html


def test_a_flag_set_the_file_lacks_gets_no_bar(cf7):
    """oceanarray runs no conductivity QC: an originator-flagged C has its bar alone."""
    from oceanarray.plotters.flags import _rows, flagged_rows

    st = _cf7_stack(cf7).transpose("N_LEVELS", "time")
    assert "conductivity_qc" not in st.data_vars
    k = _level(st, "22225")
    flags = st["conductivity_qc_originator_mapped"].values.copy()
    flags[k, 10:12] = 3
    st["conductivity_qc_originator_mapped"] = (("N_LEVELS", "time"), flags)
    entry = next(e for e in flagged_rows(st)[k] if e[0] == "conductivity")
    assert entry[2] is None
    labels = [label for label, _row in _rows(flagged_rows(st)[k])]
    assert "C originator" in labels and "C oceanarray" not in labels
    assert "S oceanarray" in labels and not any("processing" in s for s in labels)
    salinity = next(e for e in flagged_rows(st)[k] if e[0] == "salinity")
    here = np.isfinite(st["salinity"].values[k])
    assert not (salinity[2][here] == 9).any()  # a value is never "missing"
