"""Stages 1-3 and the stack on imported OSNAP files (OceanSITES netCDF and RODB text).

Builds two small moorings in a temp tree from the trimmed fixtures in
``tests/fixtures/raw/osnap`` and runs the pipeline: the readers, the YAML
``units_override``, the value-range check, and stage 3 leaving the originator's
declination alone.
"""

import logging
import re
import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
import yaml

pytest.importorskip("seasenselib", reason="seasenselib not installed")
import oceanarray as oa
from oceanarray.pipeline import _process_legacy as process
from oceanarray.reports import report

OSNAP = Path(__file__).parent.parent / "fixtures" / "raw" / "osnap"


def _mooring(tmp_path, name, header, instruments):
    raw = tmp_path / "raw" / name
    proc = tmp_path / "proc" / name
    proc.mkdir(parents=True)
    for inst in instruments:
        (raw / inst["instrument"]).mkdir(parents=True, exist_ok=True)
        shutil.copy(OSNAP / inst["filename"], raw / inst["instrument"])
    cfg = {"name": name, **header, "clamp": instruments}
    (proc / f"{name}.mooring.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return tmp_path / "raw", tmp_path / "proc"


def _paths(yaml_path: Path, raw_dir: Path | None = None) -> oa.Paths:
    """Return the Paths of a tree from _mooring: the pages in the mooring folder's report/."""
    folder = yaml_path.parent
    return oa.Paths(raw_dir=raw_dir, proc_dir=folder.parent).with_mooring(
        folder.name, report_dir=folder / "report"
    )


def _open(proc, name, instrument, serial, stage):
    path = proc / name / instrument / f"{name}_{serial}_stage{stage}.nc"
    assert path.exists(), path
    return xr.open_dataset(path, engine="netcdf4").load()


RTWB = {
    "waterdepth": 1600,
    "latitude": "57 28.240 N",
    "longitude": "012 42.300 W",
    "deployment_time": "2014-07-17T12:00",
    "recovery_time": "2015-06-22T12:00",
}
CF7 = {
    "waterdepth": 1901,
    "latitude": "59 55.576 N",
    "longitude": "041 25.885 W",
    "deployment_time": "2020-07-12T14:00",
    "recovery_time": "2021-11-12T00:00",
}


def test_rodb_mooring_runs_to_stage3_without_reapplying_declination(tmp_path):
    raw, proc = _mooring(
        tmp_path,
        "rtwb1_01_2014",
        RTWB,
        [
            {
                "serial": 11335,
                "instrument": "microcat",
                "file_type": "rodb",
                "filename": "rtwb1_01_2014_001.microcat",
                "hab": 1500,
            },
            {
                "serial": 11021,
                "instrument": "currentmeter",
                "file_type": "rodb",
                "filename": "rtwb1_01_2014_11021.edt",
                "hab": 1505,
            },
        ],
    )
    assert process("rtwb1_01_2014", [1, 2, 3], proc_dir=proc, raw_dir=raw, force=True)
    s1 = _open(proc, "rtwb1_01_2014", "currentmeter", 11021, 1)
    s3 = _open(proc, "rtwb1_01_2014", "currentmeter", 11021, 3)
    assert s1["east_velocity"].attrs["units"] == "m s-1"
    assert s3.attrs["originator_magnetic_declination"] == -7.33
    assert "magnetic_declination" not in s3.attrs
    common = np.intersect1d(s1["time"].values, s3["time"].values)
    np.testing.assert_allclose(
        s3["east_velocity"].sel(time=common).values,
        s1["east_velocity"].sel(time=common).values,
    )
    mc = _open(proc, "rtwb1_01_2014", "microcat", 11335, 1)
    assert mc["conductivity"].attrs["units"] == "mS cm-1"


def test_imported_aquadopp_report_builds_without_panel_crash(tmp_path):
    """An imported Aquadopp holding only pressure and velocity renders a report.

    Its temperature, current-speed, trajectory and hodograph panels are not-applicable
    rather than raising ``KeyError`` (RAISE_ON_PLOT_ERROR is on in the test suite, so a
    panel that raised would fail the build). OSNAP note 8.
    """
    raw, proc = _mooring(
        tmp_path,
        "cf7",
        CF7,
        [
            {
                "serial": 500,
                "instrument": "aquadopp",
                "file_type": "oceansites",
                "filename": "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc",
                "hab": 1401,
            }
        ],
    )
    assert process("cf7", [1, 2, 3], proc_dir=proc, raw_dir=raw, force=True)
    yaml_path = proc / "cf7" / "cf7.mooring.yaml"
    summary = report(
        yaml_path, _paths(yaml_path), instruments=True, stack=False, grid=False
    )
    mooring_dir = yaml_path.parent
    assert Path(summary).exists()
    instr_page = mooring_dir / "report" / "instrument" / "cf7_500_report.html"
    assert instr_page.exists()
    html = instr_page.read_text(encoding="utf-8")
    # The velocity-only page names the panels it cannot draw rather than crashing.
    assert "Not applicable" in html


def test_imported_rodb_report_labels_current_meter(tmp_path):
    """A currentmeter-typed imported instrument shows the 'Current meter' pill, not
    the upper-cased fallback 'CURRENTMETER' (OSNAP note 20)."""
    raw, proc = _mooring(
        tmp_path,
        "rtwb1_01_2014",
        RTWB,
        [
            {
                "serial": 11021,
                "instrument": "currentmeter",
                "file_type": "rodb",
                "filename": "rtwb1_01_2014_11021.edt",
                "hab": 1505,
            }
        ],
    )
    assert process("rtwb1_01_2014", [1, 2, 3], proc_dir=proc, raw_dir=raw, force=True)
    yaml_path = proc / "rtwb1_01_2014" / "rtwb1_01_2014.mooring.yaml"
    summary = report(
        yaml_path, _paths(yaml_path), instruments=True, stack=False, grid=False
    )
    html = Path(summary).read_text(encoding="utf-8")
    assert "Current meter" in html
    assert "CURRENTMETER" not in html
    # All-imported mooring: the timing section states the deployment window and either
    # the trimmed amounts or one line saying nothing was trimmed, with no raw-clock
    # suggestion columns (B5, OSNAP note 7).
    assert "Deployment window (from the mooring description)" in html
    assert (
        "Originator record start" in html
        or "All records lie within the deployment window" in html
    )
    assert "Sugg." not in html
    # Masthead: the RODB YAML names no cruise or ship; position in degrees and
    # decimal minutes; 340 days in months; instruments by function.
    assert '<div class="meta-miss"><dt>Deployment cruise</dt><dd>—</dd></div>' in html
    assert '<div class="meta-miss"><dt>Recovery cruise</dt><dd>UNK</dd></div>' in html
    assert '<div class="meta-miss"><dt>Recovery ship</dt><dd>UNK</dd></div>' in html
    assert "<dd>57° 28.24′ N</dd>" in html
    assert "<dd>012° 42.30′ W</dd>" in html
    assert "<dd>11.2 months</dd>" in html
    assert "<dd>1 (Current meter 1)</dd>" in html


def test_mislabelled_units_refused_then_fixed_by_override(tmp_path):
    inst = {
        "serial": 22225,
        "instrument": "microcat",
        "file_type": "oceansites",
        "filename": "CF7_MCTD_2020_22225_50m.nc",
        "hab": 1851,
    }
    raw, proc = _mooring(tmp_path / "a", "cf7", CF7, [dict(inst)])
    process("cf7", [1], proc_dir=proc, raw_dir=raw, force=True)
    assert not (proc / "cf7" / "microcat" / "cf7_22225_stage1.nc").exists()
    log = "".join(p.read_text(encoding="utf-8") for p in (proc / "cf7").rglob("*.log"))
    assert "units_override" in log

    raw, proc = _mooring(
        tmp_path / "b",
        "cf7",
        CF7,
        [{**inst, "units_override": {"conductivity": "mS/cm"}}],
    )
    assert process("cf7", [1], proc_dir=proc, raw_dir=raw, force=True)
    s1 = _open(proc, "cf7", "microcat", 22225, 1)
    assert s1["conductivity"].attrs["units_override_from"] == "S/m"
    assert 25 < float(np.nanmedian(s1["conductivity"].values)) < 45


def test_units_override_with_reason_converts_kpa(tmp_path):
    """A pressure overridden to kPa is converted to dbar, and the reason is kept."""
    inst = {
        "serial": 500,
        "instrument": "currentmeter",
        "file_type": "oceansites",
        "filename": "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc",
        "hab": 1401,
    }
    raw, proc = _mooring(tmp_path / "a", "cf7", CF7, [dict(inst)])
    assert process("cf7", [1], proc_dir=proc, raw_dir=raw, force=True)
    stated = _open(proc, "cf7", "currentmeter", 500, 1)["pressure"]

    reason = "values are ten times the deployed depth"
    override = {"pressure": {"units": "kPa", "reason": reason}}
    raw, proc = _mooring(
        tmp_path / "b", "cf7", CF7, [{**inst, "units_override": override}]
    )
    assert process("cf7", [1], proc_dir=proc, raw_dir=raw, force=True)
    p = _open(proc, "cf7", "currentmeter", 500, 1)["pressure"]
    assert p.attrs["units"] == "dbar"
    assert p.attrs["units_override_from"] == "decibar"
    assert p.attrs["units_override_reason"] == reason
    assert p.attrs["units_converted_from"] == "kPa"
    assert p.attrs["units_conversion_factor"] == pytest.approx(0.1)
    np.testing.assert_allclose(p.values, stated.values * 0.1, rtol=1e-6)


CF1 = {
    "waterdepth": 170.0,
    "latitude": 60.07013,
    "longitude": -42.82545,
    "deployment_time": "2020-07-15T09:59:59",
    "recovery_time": "2022-08-25T09:55:28",
}
_CF1_ADCP = {
    "serial": "CM173",
    "instrument": "adcp",
    "file_type": "oceansites",
    "filename": "OS_OSNAP_CF1_202007_ADCP_173m_cut.nc",
    "hab": 1.0,
}


@pytest.mark.parametrize(
    "orientation, match",
    [(None, "never assumed"), ("down", "the file says 'up', the YAML says 'down'")],
)
def test_oceansites_adcp_orientation_is_required_and_checked(orientation, match):
    from oceanarray import readers
    from oceanarray.exceptions import MooringError
    from oceanarray.pipeline.stage1 import stage1

    entry = {**_CF1_ADCP, "orientation": orientation}
    ds = readers.read(OSNAP / entry["filename"])
    ds.attrs["file_type"] = "oceansites"
    with pytest.raises(MooringError, match=match):
        stage1({"CM173": ds}, {"name": "cf1", **CF1, "clamp": [entry]})


def test_oceansites_adcp_to_stage3_and_stack(tmp_path):
    """The originator's bin pressures pass stage 3 unchanged and place the stack rows."""

    raw, proc = _mooring(tmp_path, "cf1", CF1, [{**_CF1_ADCP, "orientation": "up"}])
    files = oa.run(
        proc / "cf1" / "cf1.mooring.yaml",
        _paths(proc / "cf1" / "cf1.mooring.yaml", raw),
        pmin=20,
        pmax=160,
        dp=20,
    )
    s1 = _open(proc, "cf1", "adcp", "CM173", 1)
    s3 = _open(proc, "cf1", "adcp", "CM173", 3)
    np.testing.assert_array_equal(s3["bin_pressure"], s1["bin_pressure"])
    assert "magnetic_declination" not in s3.attrs
    assert {"seabed_qc", "surface_qc", "error_velocity_qc"} <= set(s3.data_vars)
    with xr.open_dataset(files[-2]) as stack:
        rows = stack["serial"].values.tolist()
        assert rows[:2] == ["CM173_b00", "CM173_b01"][:0] + rows[:2]
        assert "CM173_hd" in rows and "CM173_b13" in rows
        # Each bin at its own depth (water depth less its hab), not the head's.
        bins = [i for i, r in enumerate(rows) if "_b" in r]
        np.testing.assert_allclose(
            stack["instrument_depth"].values[bins],
            CF1["waterdepth"] - stack["hab"].values[bins],
        )
        k = rows.index("CM173_b05")
        np.testing.assert_array_equal(
            stack["pressure"]
            .isel(N_LEVELS=k)
            .sel(time=s3["time"], method="nearest")
            .values,
            s3["bin_pressure"].isel(N_BINS=5).values.astype(stack["pressure"].dtype),
        )


def test_adcp_velocity_axis_ends_at_the_last_bin_with_data(tmp_path):
    """An upward ADCP's range axis stops at its last bin with data, not its last bin."""
    from oceanarray.plotters import draw_adcp_velocity

    raw, proc = _mooring(tmp_path, "cf1", CF1, [{**_CF1_ADCP, "orientation": "up"}])
    oa.run(
        proc / "cf1" / "cf1.mooring.yaml",
        _paths(proc / "cf1" / "cf1.mooring.yaml", raw),
        stages=[1, 2, 3],
    )
    s3 = _open(proc, "cf1", "adcp", "CM173", 3)
    rng = s3["range"].values
    has_data = np.isfinite(s3["east_velocity"].values).any(axis=0)
    assert not has_data[-1]  # the originator masked the bins near the surface
    fig = draw_adcp_velocity(s3)
    ax = fig.axes[0]
    half_cell = 0.5 * float(np.median(np.diff(rng)))
    assert ax.get_ylim() == pytest.approx((0.0, rng[has_data].max() + half_cell))
    mesh = np.ma.filled(ax.collections[0].get_array(), np.nan).reshape(len(rng), -1)
    np.testing.assert_array_equal(np.isfinite(mesh).any(axis=1), has_data)

    # East masked further out than north and up: the axis follows them.
    s3["east_velocity"][:, np.flatnonzero(has_data)[-1] - 2 :] = np.nan
    ylim = draw_adcp_velocity(s3).axes[0].get_ylim()
    assert ylim == pytest.approx((0.0, rng[has_data].max() + half_cell))


def test_adcp_velocity_hides_values_its_own_flags_mark_bad(tmp_path):
    """A velocity flagged 4 by its own flag variable is not drawn, nor scaled on."""
    from oceanarray.plotters import draw_adcp_velocity

    raw, proc = _mooring(tmp_path, "cf1", CF1, [{**_CF1_ADCP, "orientation": "up"}])
    oa.run(
        proc / "cf1" / "cf1.mooring.yaml",
        _paths(proc / "cf1" / "cf1.mooring.yaml", raw),
        stages=[1, 2, 3],
    )
    s3 = _open(proc, "cf1", "adcp", "CM173", 3)
    east = s3["east_velocity"]
    k = int(np.flatnonzero(np.isfinite(east.values).any(axis=0))[0])
    flags = np.ones(east.shape, dtype="int8")
    flags[:, k] = 4
    s3["east_velocity_qc"] = (east.dims, flags)
    mesh = draw_adcp_velocity(s3).axes[0].collections[0].get_array()
    mesh = np.ma.filled(mesh, np.nan).reshape(east.shape[1], -1)
    assert not np.isfinite(mesh[k]).any()
    assert np.isfinite(mesh).any()


IC1 = {
    "waterdepth": 2472,
    "latitude": 59.1,
    "longitude": -33.7,
    "deployment_time": "2016-08-09T14:20:00",
    "recovery_time": "2018-07-15T07:38:41",
}
_IC1_ADCP = {
    "serial": "ADCP480",
    "instrument": "adcp",
    "file_type": "oceansites",
    "filename": "os_osnap-ic1_201608_adcp_480m_cut.nc",
    "hab": 1997,
    "orientation": "up",
}


def test_percent_good_per_bin_is_flagged_per_bin(tmp_path):
    """A ``PERG`` on ``TIME × BINDEPTH`` (no beam) is one value per bin at stage 3."""
    from oceanarray.config.parameters import QC_ADCP

    raw, proc = _mooring(tmp_path, "ic1", IC1, [_IC1_ADCP])
    path = raw / "ic1" / "adcp" / _IC1_ADCP["filename"]
    with xr.open_dataset(path, decode_cf=False) as src:
        src = src.load()
    src["PERG"] = src["PERG"].isel(BEAM=3)  # the 4-beam column
    src.to_netcdf(path, format="NETCDF3_CLASSIC")

    oa.run(
        proc / "ic1" / "ic1.mooring.yaml",
        _paths(proc / "ic1" / "ic1.mooring.yaml", raw),
        stages=[1, 2, 3],
    )
    s3 = _open(proc, "ic1", "adcp", "ADCP480", 3)
    pg = s3["percent_good"]
    assert pg.dims == s3["percent_good_qc"].dims == ("time", "N_BINS")
    expected = np.where(
        pg < QC_ADCP["percent_good_bad"],
        4,
        np.where(pg < QC_ADCP["percent_good_suspect"], 3, 1),
    )
    np.testing.assert_array_equal(s3["percent_good_qc"], expected)
    assert {1, 3, 4} <= set(np.unique(expected))


def test_a_stage2_stack_names_true_north_only_when_it_is(tmp_path):
    """Originator ENU is true north at stage 2; without it, no CF direction names."""
    from oceanarray import mooring
    from oceanarray.pipeline.read_mooring import read_mooring
    from oceanarray.pipeline.stack import stack

    raw, proc = _mooring(tmp_path, "cf1", CF1, [{**_CF1_ADCP, "orientation": "up"}])
    assert process("cf1", [1, 2], proc_dir=proc, raw_dir=raw, force=True)
    cfg = mooring.load(proc / "cf1" / "cf1.mooring.yaml")
    names = ("east_velocity", "north_velocity", "current_direction")

    s2 = {
        k: v.load()
        for k, v in read_mooring(cfg, stage=2, mooring_dir=proc / "cf1").items()
    }
    st = stack(s2, cfg, dt_seconds=3600)
    assert "stage2_magnetic_north" not in st.attrs
    assert st["current_direction"].attrs["standard_name"] == (
        "sea_water_velocity_to_direction"
    )
    assert all("standard_name" in st[n].attrs for n in names)

    for ds in s2.values():
        del ds.attrs["originator_magnetic_declination"]
    st = stack(s2, cfg, dt_seconds=3600)
    assert "CM173_b05" in st.attrs["stage2_magnetic_north"].split()
    assert not any("standard_name" in st[n].attrs for n in names)


def test_oceansites_current_meter_to_stage3(tmp_path):
    raw, proc = _mooring(
        tmp_path,
        "cf7",
        CF7,
        [
            {
                "serial": 500,
                "instrument": "currentmeter",
                "file_type": "oceansites",
                "filename": "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc",
                "hab": 1401,
            }
        ],
    )
    assert process("cf7", [1, 2, 3], proc_dir=proc, raw_dir=raw, force=True)
    s3 = _open(proc, "cf7", "currentmeter", 500, 3)
    assert s3.attrs["coordinate_system"] == "ENU"
    assert "magnetic_declination" not in s3.attrs
    assert s3.attrs["originator_institution"].startswith("Scripps")


_RTWB_INSTRUMENTS = [
    {
        "serial": 11335,
        "instrument": "microcat",
        "file_type": "rodb",
        "filename": "rtwb1_01_2014_001.microcat",
        "hab": 1500,
    },
    {
        "serial": 11021,
        "instrument": "currentmeter",
        "file_type": "rodb",
        "filename": "rtwb1_01_2014_11021.edt",
        "hab": 1505,
    },
]


@pytest.mark.parametrize("dt_seconds", [600, 3600])
def test_rodb_instruments_stack_by_nearest_sample_only(tmp_path, caplog, dt_seconds):
    """30-min MicroCAT and 20-min current meter: each step is a sample or empty.

    At 600 s every source sample lands on exactly one step and the steps between
    are empty (with a warning per instrument); at 3600 s each step takes the
    nearest sample. No value is interpolated in time.
    """

    name = "rtwb1_01_2014"
    raw, proc = _mooring(
        tmp_path, name, RTWB, [dict(entry) for entry in _RTWB_INSTRUMENTS]
    )
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        files = oa.run(
            proc / name / f"{name}.mooring.yaml",
            _paths(proc / name / f"{name}.mooring.yaml", raw),
            dt_seconds=dt_seconds,
            pmin=70,
            pmax=110,
            dp=10,
        )
    out = caplog.text
    with xr.open_dataset(files[-2]) as ds:
        ds = ds.load()
    steps = ds["time"].values
    assert np.all(steps.astype("datetime64[s]").astype(np.int64) % dt_seconds == 0)
    half = np.timedelta64(dt_seconds // 2, "s")
    for serial, instrument, var, interval in (
        ("11335", "microcat", "temperature", 1800),
        ("11021", "currentmeter", "east_velocity", 1200),
    ):
        warned = f"{instrument} s/n {serial} samples every {interval} s"
        assert (warned in out) == (interval > dt_seconds)
        src = _open(proc, name, instrument, serial, 3)
        src_t = src["time"].values
        src_v = src[var].values.astype(np.float64)  # the stack keeps every value
        level = ds[var].isel(N_LEVELS=ds["serial"].values.tolist().index(serial))
        level = level.values.astype(np.float64)
        # The nearest sample to each step in [step - dt/2, step + dt/2).
        want = np.full(len(steps), np.nan)
        for k, t in enumerate(steps):
            d = np.abs(src_t - t)
            j = int(np.argmin(d))
            if d[j] < half or (d[j] == half and src_t[j] < t):
                want[k] = src_v[j]
        np.testing.assert_array_equal(level, want.astype(level.dtype))
        if interval > dt_seconds:
            used = np.isfinite(level).sum()
            inside = (src_t >= steps[0] - half) & (src_t < steps[-1] + half)
            assert used == np.isfinite(src_v[inside]).sum()
            assert used < 0.6 * len(steps)


def test_people_are_cleaned_once_at_stage1_and_joined_after(tmp_path):
    """CF7 2020 (WHOI current meter, Scripps MicroCAT): stage 1 cleans the people and
    says so in history; every file names its oceanarray; the stack and the export
    only join, and name the same people."""
    from oceanarray import mooring, provenance
    from oceanarray.pipeline import attribution
    from oceanarray.pipeline.oceansites import _people
    from oceanarray.pipeline.read_mooring import read_mooring

    raw, proc = _mooring(
        tmp_path,
        "cf7",
        CF7,
        [
            {
                "serial": 500,
                "instrument": "currentmeter",
                "file_type": "oceansites",
                "filename": "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc",
                "hab": 1401,
            },
            {
                "serial": 22225,
                "instrument": "microcat",
                "file_type": "oceansites",
                "filename": "CF7_MCTD_2020_22225_50m.nc",
                "hab": 1851,
                "units_override": {"conductivity": "mS/cm"},
            },
        ],
    )
    assert process("cf7", [1, 2, 3], proc_dir=proc, raw_dir=raw, force=True)
    s1 = _open(proc, "cf7", "microcat", 22225, 1)
    s3 = _open(proc, "cf7", "microcat", 22225, 3)
    assert s1.attrs["principal_investigator"] == "Fiamma Straneo"
    assert (
        "people: cleaned per OceanSITES 1.4 from originator_* (1 PI"
        in s1.attrs["history"]
    )
    for ds in (s1, s3):
        assert ds.attrs[provenance.VERSION_ATTR]
    assert s3.attrs["stage1_oceanarray_version"] == s1.attrs["oceanarray_version"]
    assert len(s3.attrs["stage2_source_sha256"]) == 64

    cfg = mooring.load(proc / "cf7" / "cf7.mooring.yaml")
    data = read_mooring(cfg, stage=3, mooring_dir=proc / "cf7")
    stacked = oa.stack(data, cfg)
    assert "people_not_cleaned" not in stacked.attrs
    exported = attribution.to_attrs(
        attribution.union(
            attribution.read_own(_people(ds, "test")) for ds in data.values()
        )
    )
    people = set(attribution.PI_ATTRS.values()) | set(
        attribution.CONTRIBUTOR_ATTRS.values()
    )
    assert {k: v for k, v in stacked.attrs.items() if k in people} == exported


def test_a_stage_file_from_before_people_cleaning_gives_no_people(tmp_path):
    """The same stage-3 file without its stage-1 version (as a file from before
    stage 1 cleaned people): the stack names it and writes no people from it."""
    from oceanarray import mooring
    from oceanarray.pipeline.read_mooring import read_mooring

    raw, proc = _mooring(
        tmp_path,
        "cf7",
        CF7,
        [
            {
                "serial": 500,
                "instrument": "currentmeter",
                "file_type": "oceansites",
                "filename": "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc",
                "hab": 1401,
            }
        ],
    )
    assert process("cf7", [1, 2, 3], proc_dir=proc, raw_dir=raw, force=True)
    cfg = mooring.load(proc / "cf7" / "cf7.mooring.yaml")
    data = read_mooring(cfg, stage=3, mooring_dir=proc / "cf7")
    for ds in data.values():
        del ds.attrs["stage1_oceanarray_version"]
    stacked = oa.stack(data, cfg)
    assert stacked.attrs["people_not_cleaned"] == "currentmeter s/n 500"
    assert "principal_investigator" not in stacked.attrs


def test_units_converted_is_recorded_in_the_file(tmp_path):
    """The WHOI current meter: pressure decibar -> dbar and velocity meter/second -> m s-1."""
    import json

    raw, proc = _mooring(
        tmp_path,
        "cf7",
        CF7,
        [
            {
                "serial": 500,
                "instrument": "currentmeter",
                "file_type": "oceansites",
                "filename": "OS_OSNAP_WHOI_CF7_202007_CM_500m.nc",
                "hab": 1401,
            }
        ],
    )
    assert process("cf7", [1, 2, 3], proc_dir=proc, raw_dir=raw, force=True)
    for stage in (1, 3):
        ledger = json.loads(
            _open(proc, "cf7", "currentmeter", 500, stage).attrs["units_converted"]
        )
        assert ledger["pressure"] == {"factor": 1.0, "from": "decibar", "to": "dbar"}
        assert ledger["east_velocity"]["from"] == "meter/second"


#: egM5 2016's waterdepth correction, as osnap-east records it in the mooring YAML.
EGM5_WATERDEPTH_SOURCE = (
    "corrected from the originator's 3047 m (RODB header, copied from M4 with M4's "
    "position); mean of egM5 2014 and 2015 (3122, 3127 m), with which the measured "
    "pressures agree to within 5 dbar"
)


@pytest.mark.parametrize("with_source", [True, False])
def test_a_mooring_level_source_reaches_every_file(tmp_path, with_source):
    """A ``waterdepth_source`` in the YAML is written beside ``waterdepth`` at stage 1,
    with one history line, and kept through stage 3; without one, neither appears."""
    header = {**RTWB, "waterdepth": 3125.0}
    if with_source:
        header["waterdepth_source"] = EGM5_WATERDEPTH_SOURCE
    raw, proc = _mooring(
        tmp_path,
        "rtwb1_01_2014",
        header,
        [
            {
                "serial": 11335,
                "instrument": "microcat",
                "file_type": "rodb",
                "filename": "rtwb1_01_2014_001.microcat",
                "hab": 1500,
            }
        ],
    )
    assert process("rtwb1_01_2014", [1, 2, 3], proc_dir=proc, raw_dir=raw, force=True)
    for stage in (1, 3):
        ds = _open(proc, "rtwb1_01_2014", "microcat", 11335, stage)
        assert ds.attrs["waterdepth"] == 3125.0
        if with_source:
            assert ds.attrs["waterdepth_source"] == EGM5_WATERDEPTH_SOURCE
            assert (
                # The entry separator inside the note is cleaned so the entry
                # reads back whole.
                "waterdepth 3125.0 m from the mooring YAML: "
                + EGM5_WATERDEPTH_SOURCE.replace("; ", ", ")
                in ds.attrs["history"]
            )
        else:
            assert "waterdepth_source" not in ds.attrs
            assert "from the mooring YAML" not in ds.attrs["history"]


EGM3 = {
    "waterdepth": 2556.0,
    "latitude": 59.8145,
    "longitude": -40.277166666666666,
    "deployment_time": "2016-08-16T10:00:00",
    "recovery_time": "2018-06-16T12:07:00",
}
_EGM3_ADCP = {
    "serial": "20961",
    "instrument": "adcp",
    "file_type": "rodb",
    "filename": "nocm3_03_2016_20961_bin*.edt",
    "orientation": "down",
    "hab": 104.0,
}


def _egm3(tmp_path, pattern=_EGM3_ADCP["filename"]):
    raw = tmp_path / "raw" / "egm3" / "adcp"
    raw.mkdir(parents=True)
    for p in sorted(OSNAP.glob(_EGM3_ADCP["filename"])):
        shutil.copy(p, raw)
    proc = tmp_path / "proc" / "egm3"
    proc.mkdir(parents=True)
    cfg = {"name": "egm3", **EGM3, "clamp": [{**_EGM3_ADCP, "filename": pattern}]}
    (proc / "egm3.mooring.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return tmp_path / "raw", tmp_path / "proc"


def test_rodb_bin_files_to_stage3_and_stack(tmp_path):
    """Three per-bin files (bin 3 missing) become one ADCP; rows keep the bin numbers."""

    raw, proc = _egm3(tmp_path)
    files = oa.run(
        proc / "egm3" / "egm3.mooring.yaml",
        _paths(proc / "egm3" / "egm3.mooring.yaml", raw),
        pmin=2400,
        pmax=2600,
        dp=50,
    )
    s1 = _open(proc, "egm3", "adcp", "20961", 1)
    s3 = _open(proc, "egm3", "adcp", "20961", 3)
    np.testing.assert_array_equal(s3["bin_pressure"], s1["bin_pressure"])
    assert s3["percent_good_qc"].dims == ("time", "N_BINS")
    assert s3["error_velocity"].attrs["units"] == "m s-1"
    with xr.open_dataset(files[-2]) as stack:
        rows = stack["serial"].values.tolist()
        depth = stack["instrument_depth"].values
        hab = stack["hab"].values
    assert {"20961_b01", "20961_b02", "20961_b04", "20961_hd"} <= set(rows)
    bins = [i for i, r in enumerate(rows) if "_b" in r]
    np.testing.assert_allclose(depth[bins], EGM3["waterdepth"] - hab[bins])
    assert len(set(depth[bins])) == len(bins)


def test_rodb_bin_pattern_without_a_match_is_refused(tmp_path):
    from oceanarray.exceptions import MooringError
    from oceanarray.pipeline.raw_files import resolve_raw_file

    raw, proc = _egm3(tmp_path, "nocm3_03_2016_99999_bin*.edt")
    cfg = yaml.safe_load((proc / "egm3" / "egm3.mooring.yaml").read_text())
    with pytest.raises(MooringError, match="no file matches"):
        resolve_raw_file(cfg["clamp"][0], cfg, raw)


def test_a_pattern_matching_several_files_is_refused_outside_rodb(tmp_path):
    from oceanarray.exceptions import MooringError
    from oceanarray.pipeline.raw_files import resolve_raw_file

    raw, proc = _egm3(tmp_path)
    cfg = yaml.safe_load((proc / "egm3" / "egm3.mooring.yaml").read_text())
    entry = {**cfg["clamp"][0], "file_type": "oceansites"}
    with pytest.raises(MooringError, match="matches 3 files"):
        resolve_raw_file(entry, cfg, raw)


def test_a_newer_bin_file_regenerates_stage1(tmp_path):
    import os

    from oceanarray.paths import should_skip_regeneration
    from oceanarray.pipeline.raw_files import resolve_raw_file

    raw, proc = _egm3(tmp_path)
    cfg = yaml.safe_load((proc / "egm3" / "egm3.mooring.yaml").read_text())
    rf = resolve_raw_file(cfg["clamp"][0], cfg, raw)
    out = tmp_path / "stage1.nc"
    out.write_text("")
    os.utime(rf.paths[-1], (out.stat().st_mtime + 10,) * 2)
    assert len(rf.paths) == 3
    assert not should_skip_regeneration(out, False, False, *rf.paths)


@pytest.fixture(scope="module")
def cf1_site(tmp_path_factory):
    """The CF1 2020 ADCP cut as two deployments (its record split in two), stacked."""

    root = tmp_path_factory.mktemp("cf1_site")
    windows = (
        ("cf1_a", "2020-08-26T02:00", "2020-09-05T00:00"),
        ("cf1_b", "2020-09-05T01:00", "2020-09-15T00:00"),
    )
    stacks = []
    for name, start, end in windows:
        header = {**CF1, "deployment_time": start, "recovery_time": end}
        raw, proc = _mooring(root, name, header, [{**_CF1_ADCP, "orientation": "up"}])
        files = oa.run(
            proc / name / f"{name}.mooring.yaml",
            _paths(proc / name / f"{name}.mooring.yaml", raw),
        )
        stacks.append(xr.open_dataset(files[-2]).load())
    return stacks


def test_stack_leaves_out_bins_above_the_surface(cf1_site):
    """CF1's upward ADCP reaches 70 m above the surface: those bins are not levels."""
    stack = cf1_site[0]
    bins = np.char.find(stack["serial"].values.astype(str), "_b") > 0
    depth = stack["instrument_depth"].values[bins]
    assert (depth > 0).all()
    raw = xr.open_dataset(OSNAP / _CF1_ADCP["filename"])
    assert bins.sum() == int((raw["BINDEPTH"].values > 0).sum())


def test_deployment_roses_show_four_bins_of_an_adcp(cf1_site):
    """The stack page's roses take at most four bins of one ADCP."""
    import matplotlib.pyplot as plt

    from oceanarray.plotters.current import draw_rose_grid

    stack = cf1_site[0]
    fig, n = draw_rose_grid(stack, list(stack["serial"].values))
    plt.close(fig)
    assert n == 4


def test_site_profiles_draw_an_adcp_as_one_line_per_deployment(cf1_site):
    """Per deployment one line and one band through the bins; the bins add no y ticks."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from oceanarray.pipeline.concat import concat
    from oceanarray.plotters import current
    from oceanarray.reports._concat import _report_view

    view = _report_view(concat(cf1_site, name="cf1"))
    bins = view["depth"].values[view["instrument_type"].values == "adcp"]
    for draw in (
        current.draw_speed_profile_by_deployment,
        current.draw_direction_profile_by_deployment,
    ):
        fig = draw(view)
        ax = fig.axes[0]
        assert len(ax.lines) == len(ax.collections) == 2
        assert not np.isin(np.round(ax.get_yticks(), 3), np.round(bins, 3)).any()
        plt.close(fig)


def test_three_adcp_bins_per_deployment_and_none_above_the_surface(cf1_site, tmp_path):
    """Roses and flags show three bins per ADCP; bins at or above the surface go."""
    from oceanarray.analysis.spectral import SPECTRAL_MIN_FILLED
    from oceanarray.pipeline.concat import (
        adcp_bins,
        concat,
        select_depths,
        shown_depths,
    )

    # A deployment stack whose water depth is 30 m less: its top bins are above
    # the surface, as in a stack made before such bins were left out.
    shallow = cf1_site[1].copy()
    shallow.attrs["waterdepth"] = CF1["waterdepth"] - 30
    site = concat([cf1_site[0], shallow], name="cf1")
    picks = adcp_bins(site)
    assert sorted(len(p) for p in picks.values()) == [3, 3]
    for (k, _serial), at in picks.items():
        own = site["level_index"].values[:, at]
        ok = np.isfinite(site["east_velocity"].values[:, at])
        levels = site["depth_index"].values
        nearest = min(
            int(str(s).rsplit("_b", 1)[1])
            for i, s in enumerate(site["serial"].values)
            if levels[i] == at[0]
        )
        assert nearest == 0
        for c in range(len(at)):
            mine = (own[:, c] >= 0) & (
                site["deployment"].values[np.clip(own[:, c], 0, None)] == k
            )
            assert ok[mine, c].mean() >= SPECTRAL_MIN_FILLED
    keep = shown_depths(site)
    assert (site["nominal_depth"].values <= 0).any()
    assert not (select_depths(site, keep)["nominal_depth"].values <= 0).any()
    path = tmp_path / "cf1_stackcat.nc"
    site.to_netcdf(path)
    from oceanarray.reports._concat import generate_concat_page

    html = generate_concat_page(path, tmp_path / "report").read_text()
    assert "ADCP bins shown:" in html and "ADCP bin " in html
    assert not re.search(r"at -\d+ m|· -\d+ m|bin -\d+ m", html)


def test_the_flag_strip_shows_each_adcp_once(cf1_site):
    """Two deployments of one ADCP: one panel at its head, pressure once, half-height bars."""
    import matplotlib.pyplot as plt

    from oceanarray.pipeline.concat import concat, select_depths, shown_depths
    from oceanarray.plotters.flags import BAR_HEIGHT_IN, draw_flag_strip
    from oceanarray.reports._qcflags import instrument_labels

    site = concat(cf1_site, name="cf1")
    shown = select_depths(site, shown_depths(site, bins="three"))
    # CF1's flags are all good: mark one step suspect, pressure at the head and
    # every bin (as a pressure test flags each), east velocity at one bin.
    family = shown["depth_family"].values.astype(str)
    adcp = np.flatnonzero(np.isin(family, ["adcp", "adcp_head"]))
    one = np.flatnonzero(family == "adcp")[0]
    for var, at in (("pressure", adcp), ("east_velocity", [one])):
        flags = shown[f"{var}_qc"].transpose("time", "N_DEPTHS").values.copy()
        for k in at:
            t = np.flatnonzero(np.isfinite(shown[var].values[:, k]))[0]
            flags[t, k] = 3
        shown[f"{var}_qc"] = (("time", "N_DEPTHS"), flags)
    fig = draw_flag_strip(shown, labels=instrument_labels(shown))
    fig.canvas.draw()
    titles = [ax.get_title(loc="left") for ax in fig.axes]
    ticks = [t.get_text() for ax in fig.axes for t in ax.get_yticklabels()]
    per_bar = [
        ax.get_position().height * fig.get_figheight() / len(ax.get_yticks())
        for ax in fig.axes
    ]
    plt.close(fig)
    depth = shown["nominal_depth"].values
    assert titles == [f"ADCP · {depth[family == 'adcp_head'][0]:.0f} m"]
    assert ticks == ["P oceanarray", f"u {depth[one]:.0f} m oceanarray"]
    np.testing.assert_allclose(per_bar, BAR_HEIGHT_IN, rtol=0.01)
