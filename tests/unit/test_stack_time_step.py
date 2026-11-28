"""The stack's time step: one default, one resolution order, nearest sample only.

On the committed dune2 stage-3 fixtures (MicroCAT 2941 at 60 s, Aquadopp 9920 at
120 s) and copies of them edited in a temp directory.
"""

import copy
import logging
import shutil
import warnings
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
import yaml

import oceanarray as oa
from oceanarray import mooring as _mooring
from oceanarray import paths
from oceanarray.config import parameters as params
from oceanarray.exceptions import MooringError
from oceanarray.mooring.instruments import instrument_entries
from oceanarray.pipeline import stack as st
from oceanarray.pipeline.dataset import time_axis, time_step_seconds
from oceanarray.pipeline.pressure import _times_to_float
from oceanarray.pipeline.read_mooring import read_mooring

_FIX = Path(__file__).parent.parent / "fixtures" / "proc" / "dune2_1_2026"
_NAME = "dune2_1_2026"
_YAML = _FIX / f"{_NAME}.mooring.yaml"


@pytest.fixture(scope="module")
def cfg() -> dict:
    """The dune2 mooring YAML."""
    return _mooring.load(_YAML)


@pytest.fixture(scope="module")
def stage3(cfg) -> dict[str, xr.Dataset]:
    """The committed stage-3 datasets, loaded."""
    return {
        s: ds.load() for s, ds in read_mooring(cfg, stage=3, mooring_dir=_FIX).items()
    }


def _with_stack_block(cfg: dict, dt_seconds) -> dict:
    """Return a copy of *cfg* with ``stack: {dt_seconds: ...}``."""
    out = copy.deepcopy(cfg)
    out["stack"] = {"dt_seconds": dt_seconds}
    return out


def _tree(tmp_path: Path, extra_yaml: str = "") -> Path:
    """Copy the dune2 stage files to *tmp_path*/<name>/ and return the YAML path."""
    moor = tmp_path / _NAME
    shutil.copytree(
        _FIX,
        moor,
        ignore=shutil.ignore_patterns(
            "report", "processing_logs", ".DS_Store", "*_stack.nc", "*_grid.nc"
        ),
    )
    yaml_path = moor / f"{_NAME}.mooring.yaml"
    if extra_yaml:
        yaml_path.write_text(yaml_path.read_text() + extra_yaml)
    return yaml_path


def _loop_nearest(ds: xr.Dataset, common_time: np.ndarray, half_window_s: float):
    """The per-step loop the vectorized routine replaced, kept as the reference."""
    src_t = _times_to_float(ds["time"].values)
    tgt_t = _times_to_float(common_time)
    n = len(common_time)
    half_ns = half_window_s * 1e9
    idx = np.searchsorted(src_t, tgt_t)
    result = {}
    for vname in st.STACK_VARS:
        if vname not in ds.data_vars:
            result[vname] = np.full(n, np.nan)
            continue
        src_v = ds[vname].values.astype(np.float64)
        out = np.full(n, np.nan)
        for i, (t_tgt, k) in enumerate(zip(tgt_t, idx, strict=False)):
            best_dt = np.inf
            best_v = np.nan
            for j in [k - 1, k]:
                if 0 <= j < len(src_t):
                    dt = abs(src_t[j] - t_tgt)
                    if dt < best_dt and dt <= half_ns:
                        best_dt = dt
                        best_v = src_v[j]
            out[i] = best_v
        result[vname] = out
    return result


def _axis(cfg: dict, dt_seconds: int) -> np.ndarray:
    """Return the stack time axis for the mooring YAML's window at *dt_seconds*."""
    return time_axis(
        np.datetime64(cfg["deployment_time"], "ns"),
        np.datetime64(cfg["recovery_time"], "ns"),
        dt_seconds,
    )


# --- the default and the resolution order ------------------------------------


def test_default_is_hourly_on_whole_hours(stage3, cfg):
    """No dt_seconds anywhere: 3600 s, the committed stack's axis moved onto the hour."""
    assert params.STACK_DT_SECONDS == 3600
    ds = oa.stack(stage3, cfg)
    assert ds.attrs["dt_seconds"] == 3600
    with xr.open_dataset(_FIX / f"{_NAME}_stack.nc") as committed:
        old = committed["time"].values  # 20:30 ... 11:30, built before the change
    # The committed axis started at deployment_time (20:30); the new one starts at
    # the next whole hour and so ends one step earlier, before recovery (11:44).
    np.testing.assert_array_equal(
        ds["time"].values, old[:-1] + np.timedelta64(1800, "s")
    )
    secs = ds["time"].values.astype("datetime64[s]").astype(np.int64)
    assert np.all(secs % 3600 == 0)


@pytest.mark.parametrize("dt_seconds", [60, 600, 1800, 3600, 7200])
def test_axis_starts_on_a_whole_multiple_of_the_step(cfg, dt_seconds):
    """First step = deployment_time rounded up to a multiple of dt (UTC epoch)."""
    t = _axis(cfg, dt_seconds)
    secs = t.astype("datetime64[s]").astype(np.int64)
    assert np.all(secs % dt_seconds == 0)
    deploy = np.datetime64(cfg["deployment_time"], "ns")
    assert deploy <= t[0] < deploy + np.timedelta64(dt_seconds, "s")
    assert t[-1] <= np.datetime64(cfg["recovery_time"], "ns")


def test_a_window_ending_on_a_whole_step_keeps_that_step():
    """FLMB 2016–2018 (OSNAP East): 16 756 hours, the last on the window's end."""
    t = time_axis(
        np.datetime64("2016-07-13T16:00", "ns"),
        np.datetime64("2018-06-11T19:00", "ns"),
        3600,
    )
    assert t.size == 16756
    assert t[-1] == np.datetime64("2018-06-11T19:00", "ns")


def test_a_window_with_no_whole_step_is_refused(stage3, cfg):
    """20:30–20:50 holds no whole hour: the stack says so instead of writing nothing."""
    short = copy.deepcopy(cfg)
    short["recovery_time"] = "2026-07-11T20:50"
    with pytest.raises(MooringError, match="no whole multiple"):
        st.stack(stage3, short, dt_seconds=3600)


def test_the_yaml_block_applies_unless_the_call_gives_one(stage3, cfg):
    """oa.stack: call > the mooring YAML's stack: block > the default."""
    cfg2 = _with_stack_block(cfg, 1800)
    assert oa.stack(stage3, cfg2).attrs["dt_seconds"] == 1800
    assert oa.stack(stage3, cfg2, dt_seconds=3600).attrs["dt_seconds"] == 3600


@pytest.mark.parametrize("bad", [0, -60, 1.5, True, "600"])
def test_a_bad_time_step_is_refused(stage3, cfg, bad):
    """Bool, float, string and non-positive steps are refused, from the YAML or the call."""
    with pytest.raises(MooringError, match="dt_seconds"):
        st.stack(stage3, _with_stack_block(cfg, bad))
    with pytest.raises(MooringError, match="dt_seconds"):
        st.stack(stage3, cfg, dt_seconds=bad)


def test_run_stacker_and_process_obey_the_yaml(tmp_path):
    """run, MooringStacker and process --stage stack all read stack: dt_seconds."""
    from oceanarray.cli import build_parser
    from oceanarray.pipeline.stack import MooringStacker

    yaml_path = _tree(tmp_path, "stack:\n  dt_seconds: 1800\n")
    stack_nc = paths.product_file(yaml_path.parent, mooring_name=_NAME, product="stack")

    files = oa.run(yaml_path, oa.Paths(proc_dir=yaml_path.parent.parent), stages=3)
    with xr.open_dataset(files[-2]) as ds:
        assert ds.attrs["dt_seconds"] == 1800
    stack_nc.unlink()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        MooringStacker(proc_dir=str(tmp_path)).stack(_NAME, force=True)
    with xr.open_dataset(stack_nc) as ds:
        assert ds.attrs["dt_seconds"] == 1800
    stack_nc.unlink()

    args = ["process", _NAME, "--stage", "stack", "--proc-dir", str(tmp_path)]
    ns = build_parser().parse_args(args)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        ns.func(ns)
    with xr.open_dataset(stack_nc) as ds:
        assert ds.attrs["dt_seconds"] == 1800

    stack_nc.unlink()
    ns = build_parser().parse_args([*args, "--dt-seconds", "3600"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        ns.func(ns)
    with xr.open_dataset(stack_nc) as ds:
        assert ds.attrs["dt_seconds"] == 3600


# --- nearest sample, no interpolation -----------------------------------------


@pytest.mark.parametrize("dt_seconds", [600, 3600])
def test_every_value_is_a_source_sample_within_half_a_step(stage3, cfg, dt_seconds):
    """No made-up values: every finite stacked value is a sample within ±dt/2 of its step."""
    ds = st.stack(stage3, cfg, dt_seconds=dt_seconds)
    tgt = ds["time"].values
    half = np.timedelta64(dt_seconds // 2, "s")
    checked = 0
    for lev, serial in enumerate(ds["serial"].values.tolist()):
        src = stage3[serial]
        src_t = src["time"].values
        lo = np.searchsorted(src_t, tgt - half, side="left")
        hi = np.searchsorted(src_t, tgt + half, side="right")
        for vname in st.STACK_VARS:
            if vname not in ds.data_vars or vname not in src.data_vars:
                continue
            level = ds[vname].isel(N_LEVELS=lev).values.astype(np.float64)
            src_v = src[vname].values.astype(np.float64)
            for k in np.flatnonzero(np.isfinite(level)):
                window = src_v[lo[k] : hi[k]]
                assert np.any(np.isclose(window, level[k], rtol=1e-6, atol=0)), (
                    f"{serial} {vname} step {k}: {level[k]} is no sample in ±dt/2"
                )
                checked += 1
    assert checked > 1000


@pytest.mark.parametrize("serial", ["2941", "9920"])
@pytest.mark.parametrize("dt_seconds", [60, 600, 3600])
def test_vectorized_nearest_matches_the_loop(stage3, cfg, serial, dt_seconds):
    """The vectorized routine gives identical arrays to the per-step loop."""
    ds = stage3[serial]
    common = _axis(cfg, dt_seconds)
    want = _loop_nearest(ds, common, dt_seconds / 2.0)
    got = st._nearest_subsample(ds, common, dt_seconds / 2.0)
    for vname in st.STACK_VARS:
        assert np.array_equal(got[vname], want[vname], equal_nan=True), vname


def test_a_sample_half_a_step_from_two_steps_lands_on_one(stage3, cfg):
    """Half-open window: a sample exactly dt/2 from two steps goes to the later one."""
    ds = stage3["2941"]
    secs = ds["time"].values.astype("datetime64[s]").astype(np.int64)
    exact = ds["time"].values.astype("datetime64[s]") == ds["time"].values
    mid = ds.isel(time=np.flatnonzero((secs % 3600 == 1800) & exact))
    assert mid.sizes["time"] > 100  # hourly samples at hh:30:00
    common = _axis(cfg, 3600)
    out = st._nearest_subsample(mid, common, 1800.0)["temperature"]
    src_v = mid["temperature"].values.astype(np.float64)
    by_time = dict(zip(mid["time"].values.tolist(), src_v.tolist(), strict=True))
    half = np.timedelta64(1800, "s")
    for k, t in enumerate(common):
        earlier = by_time.get((t - half).astype("datetime64[ns]").tolist())
        if earlier is None or not np.isfinite(earlier):
            assert np.isnan(out[k]), k
        else:
            assert out[k] == earlier, k
    assert np.isfinite(out).sum() > 100
    # The closed window of the old loop put such a sample on both steps where the
    # earlier one had no sample of its own.
    old = _loop_nearest(mid, common, 1800.0)["temperature"]
    assert np.isfinite(old).sum() > np.isfinite(out).sum()


def test_a_slower_instrument_warns_and_leaves_its_gaps(stage3, cfg, caplog):
    """At 60 s the 120 s Aquadopp fills every other step and is named once."""
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        ds = st.stack(stage3, cfg, dt_seconds=60)
    assert caplog.text.count("aquadopp s/n 9920 samples every 120 s") == 1
    assert "s/n 2941 samples every" not in caplog.text
    lev = ds["serial"].values.tolist().index("9920")
    p = ds["pressure"].isel(N_LEVELS=lev).values
    assert 0.4 < np.isfinite(p).mean() < 0.6
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        st.stack(stage3, cfg, dt_seconds=3600)
    assert "samples every" not in caplog.text


def test_the_grid_warns_on_a_level_sampled_every_other_step(stage3, cfg, caplog):
    """Gridding the 60 s stack names the 120 s Aquadopp the stack recorded, not the MicroCAT."""
    ds = st.stack(stage3, cfg, dt_seconds=60)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="oceanarray"):
        oa.grid(ds, pmin=400, pmax=440, dp=20)
    assert caplog.text.count("aquadopp s/n 9920 (120 s)") == 1
    assert "2941" not in caplog.text
    assert ds.attrs["instruments_slower_than_step"] == "aquadopp s/n 9920 (120 s)"


def test_no_whole_step_message_does_not_name_the_yaml_key(cfg):
    """The legacy stacker re-raises errors naming deployment_time; this one must not."""
    short = copy.deepcopy(cfg)
    short["recovery_time"] = "2026-07-11T20:50"
    with pytest.raises(MooringError) as err:
        st.stack({}, short, dt_seconds=3600)
    assert "deployment_time" not in str(err.value)


def test_sample_interval_is_nan_for_fewer_than_two_samples(stage3):
    """One sample has no interval; nothing is assumed."""
    one = stage3["2941"].isel(time=slice(0, 1))
    assert np.isnan(st._sample_interval_s(one["time"].values))
    assert st._sample_interval_s(stage3["2941"]["time"].values) == 60.0


def test_history_names_the_method_once(stage3, cfg):
    """One history sentence on the method; no per-level or global method variable."""
    ds = st.stack(stage3, cfg, dt_seconds=600)
    hist = ds.attrs["history"]
    assert hist.count("stack: 2 instruments placed on a 600 s time axis") == 1
    assert "nearest sample within ±300 s" in hist
    assert "no interpolation or averaging in time" in hist
    assert "interpolated (linear" not in hist
    assert not any("method" in v or "sampling" in v for v in ds.variables)
    assert not any("sampling" in a for a in ds.attrs)


# --- readers of the step ------------------------------------------------------


def test_readers_take_the_step_from_the_file_else_the_median():
    """time_step_seconds: dt_seconds, else the median step; NaN with one time."""
    from oceanarray.plotters.spectrum import draw_grid_rotary_spectrum
    from oceanarray.reports._grid import build_grid_context

    grid_nc = _FIX / f"{_NAME}_grid.nc"
    with xr.open_dataset(grid_nc) as g:
        g = g.load()
    assert time_step_seconds(g) == 3600.0
    bare = g.copy()
    del bare.attrs["dt_seconds"]
    assert time_step_seconds(bare) == 3600.0
    assert np.isnan(time_step_seconds(bare.isel(time=slice(0, 1))))
    assert build_grid_context(bare, grid_nc).dt_s == 3600.0
    assert draw_grid_rotary_spectrum(bare, 50.0) is not None
    assert draw_grid_rotary_spectrum(bare.isel(time=slice(0, 1)), 50.0) is None


def test_a_non_numeric_dt_attr_falls_back_to_the_time_axis(stage3, cfg):
    """A hand-edited ``dt_seconds: "1h"`` reads as the median step, not a crash."""
    ds = st.stack(stage3, cfg, dt_seconds=3600)
    ds.attrs["dt_seconds"] = "1h"
    assert time_step_seconds(ds) == 3600.0


def test_the_stack_records_the_settings_that_built_it(stage3, cfg):
    """``deployment_settings`` is YAML holding the stack block and every instrument."""
    ds = st.stack(stage3, _with_stack_block(cfg, 1800))
    settings = yaml.safe_load(ds.attrs["deployment_settings"])
    assert settings["stack"] == {"dt_seconds": 1800}
    serials = [str(e["serial"]) for e in settings["instruments"]]
    assert serials == [str(e["serial"]) for e in instrument_entries(cfg)]
    assert all("label" not in e and "image" not in e for e in settings["instruments"])


def test_the_recorded_settings_carry_the_step_actually_used(stage3, cfg):
    """A ``dt_seconds`` from the call wins over the mooring YAML's in the record too."""
    ds = st.stack(stage3, _with_stack_block(cfg, 1800), dt_seconds=3600)
    assert ds.attrs["dt_seconds"] == 3600
    assert yaml.safe_load(ds.attrs["deployment_settings"])["stack"] == {
        "dt_seconds": 3600
    }
