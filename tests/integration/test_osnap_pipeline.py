"""Stages 1-3 and the stack on imported OSNAP files (OceanSITES netCDF and RODB text).

Builds two small moorings in a temp tree from the trimmed fixtures in
``tests/fixtures/raw/osnap`` and runs the pipeline: the readers, the YAML
``units_override``, the value-range check, and stage 3 leaving the originator's
declination alone.
"""

import logging
import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
import yaml

pytest.importorskip("seasenselib", reason="seasenselib not installed")
pytestmark = pytest.mark.needs_seasenselib

from oceanarray.pipeline import _process_legacy as process  # noqa: E402
from oceanarray.reports import report  # noqa: E402

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
    mooring_dir = proc / "cf7"
    summary = report(mooring_dir, instruments=True, stack=False, grid=False)
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
    mooring_dir = proc / "rtwb1_01_2014"
    summary = report(mooring_dir, instruments=True, stack=False, grid=False)
    html = Path(summary).read_text(encoding="utf-8")
    assert "Current meter" in html
    assert "CURRENTMETER" not in html
    # All-imported mooring: the timing table is the trimmed "Record first/last" form,
    # with no raw-clock suggestion columns (B5, OSNAP note 7).
    assert "Record first" in html and "Record last" in html
    assert "Records were trimmed by the originator" in html
    assert "Sugg." not in html
    # Masthead: the RODB YAML names no cruise or ship; position in degrees and
    # decimal minutes; 340 days in months; instruments by type.
    assert '<div class="meta-miss"><dt>Deployment cruise</dt><dd>—</dd></div>' in html
    assert '<div class="meta-miss"><dt>Recovery cruise</dt><dd>UNK</dd></div>' in html
    assert '<div class="meta-miss"><dt>Recovery ship</dt><dd>UNK</dd></div>' in html
    assert "<dd>57° 28.24′ N</dd>" in html
    assert "<dd>012° 42.30′ W</dd>" in html
    assert "<dd>11.2mon</dd>" in html
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
    import oceanarray as oa
    from oceanarray.pipeline.qc import _apply_qc_mask

    name = "rtwb1_01_2014"
    raw, proc = _mooring(
        tmp_path, name, RTWB, [dict(entry) for entry in _RTWB_INSTRUMENTS]
    )
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        files = oa.run(
            proc / name / f"{name}.mooring.yaml",
            raw_dir=raw,
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
        src_v = src[var].values.astype(np.float64)
        if var == "temperature":
            src_v = _apply_qc_mask(src_v, src, var)
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
    import oceanarray as oa
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
    import oceanarray as oa
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
                f"waterdepth 3125.0 m from the mooring YAML: {EGM5_WATERDEPTH_SOURCE}"
                in ds.attrs["history"]
            )
        else:
            assert "waterdepth_source" not in ds.attrs
            assert "from the mooring YAML" not in ds.attrs["history"]
