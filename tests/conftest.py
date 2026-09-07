"""Shared fixtures and helpers for the test suite."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend so figure tests run without a display

import numpy as np
import pandas as pd
import pytest
import xarray as xr

#: Root of the committed real-data fixtures (trimmed dune2_1_2026 mooring).
FIXTURE_PROC = Path(__file__).parent / "fixtures" / "proc" / "dune2_1_2026"

#: Stage-3 NetCDF fixtures — real UHH instruments, one scalar (microcat) and
#: one velocity (aquadopp), committed so plot tests run on real data in CI.
MICROCAT_STAGE3 = FIXTURE_PROC / "microcat" / "dune2_1_2026_2941_stage3.nc"
AQUADOPP_STAGE3 = FIXTURE_PROC / "aquadopp" / "dune2_1_2026_9920_stage3.nc"

#: Gridded and stacked mooring fixtures — small real outputs from dune2_1_2026.
GRID_NC = FIXTURE_PROC / "dune2_1_2026_grid.nc"
STACK_NC = FIXTURE_PROC / "dune2_1_2026_stack.nc"

#: Real caldip per-cast netCDF (msm142 castM4, a .cnv-input cast: ctd_* provenance is UNK).
CALDIP_CAST = Path(__file__).parent / "fixtures" / "caldip" / "castM4_caldip.nc"


@pytest.fixture(autouse=True)
def _raise_on_plot_error(monkeypatch):
    """Make figure-generation failures raise during tests instead of vanishing.

    Flips :data:`oceanarray.config.report_tokens.RAISE_ON_PLOT_ERROR` on for the
    duration of every test so a broken figure surfaces as a test failure rather
    than a silently-absent panel. The encoder (``_encode.render_b64``) reads this
    token flag. Reverted automatically by ``monkeypatch``.
    """
    from oceanarray.config import report_tokens

    monkeypatch.setattr(report_tokens, "RAISE_ON_PLOT_ERROR", True)


@pytest.fixture
def microcat_stage3_path() -> Path:
    """Path to the committed microcat (serial 2941) stage-3 NetCDF fixture."""
    return MICROCAT_STAGE3


@pytest.fixture
def aquadopp_stage3_path() -> Path:
    """Path to the committed aquadopp (serial 9920) stage-3 NetCDF fixture."""
    return AQUADOPP_STAGE3


@pytest.fixture
def microcat_stage3():
    """Opened microcat stage-3 dataset; closed on teardown."""
    ds = xr.open_dataset(MICROCAT_STAGE3)
    yield ds
    ds.close()


@pytest.fixture
def aquadopp_stage3():
    """Opened aquadopp stage-3 dataset; closed on teardown."""
    ds = xr.open_dataset(AQUADOPP_STAGE3)
    yield ds
    ds.close()


def make_ts_dataset(
    n: int = 50,
    freq: str = "10min",
    stuck_pressure: bool = False,
    with_salinity: bool = True,
    with_conductivity: bool = False,
    seed: int = 42,
) -> xr.Dataset:
    """Minimal synthetic Dataset for unit tests that need time-series data.

    Args:
        n: Number of time steps.
        freq: pandas date_range frequency string.
        stuck_pressure: If True, pressure is all zeros (for flat-line QC tests).
        with_salinity: Include a salinity variable.
        with_conductivity: Include a conductivity variable.
        seed: Random seed for reproducibility.

    Returns:
        xr.Dataset with time coordinate and T/P (and optional S/C) variables.

    """
    rng = np.random.default_rng(seed)
    time = pd.date_range("2026-01-01", periods=n, freq=freq)
    data: dict = {
        "temperature": ("time", rng.uniform(2.0, 15.0, n)),
        "pressure": (
            "time",
            np.zeros(n) if stuck_pressure else rng.uniform(500.0, 600.0, n),
        ),
    }
    if with_salinity:
        data["salinity"] = ("time", rng.uniform(34.5, 35.5, n))
    if with_conductivity:
        data["conductivity"] = ("time", rng.uniform(30.0, 40.0, n))
    return xr.Dataset(data, coords={"time": time})


@pytest.fixture
def grid_nc() -> Path:
    """Path to the committed dune2_1_2026 grid NetCDF fixture."""
    return GRID_NC


@pytest.fixture
def grid_ds():
    """Opened dune2_1_2026 grid dataset; closed on teardown."""
    ds = xr.open_dataset(GRID_NC)
    yield ds
    ds.close()


@pytest.fixture
def stack_nc() -> Path:
    """Path to the committed dune2_1_2026 stack NetCDF fixture."""
    return STACK_NC


@pytest.fixture
def stack_ds():
    """Opened dune2_1_2026 stack dataset; closed on teardown."""
    ds = xr.open_dataset(STACK_NC)
    yield ds
    ds.close()


@pytest.fixture
def ts_dataset():
    """Standard synthetic time-series dataset (50 samples, 10 min interval)."""
    return make_ts_dataset()


@pytest.fixture
def caldip_cast_path() -> Path:
    """Path to the committed castM4 caldip per-cast NetCDF fixture."""
    return CALDIP_CAST


@pytest.fixture
def caldip_cast():
    """The castM4 caldip cast, read and serial-normalised by the reader."""
    from oceanarray.processors import caldip

    return caldip.read_caldip_cast(CALDIP_CAST)
