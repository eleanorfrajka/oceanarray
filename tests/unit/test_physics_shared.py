"""The pipeline writers and the report pages share one set of seawater and current formulas.

Every value the writers produce is checked bit for bit against the formula they
used to inline, on real records: the dune2 Aquadopp 9920 (XYZ to ENU at stage 3),
the dsG2 ADCP (one stack level per bin), the dune2 and egM2 2016 / 2018 stacks (σ
at 0, 2000 and 1500 dbar) and the grid of the dune2 stack.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import io
import pathlib
import warnings

import gsw
import numpy as np
import pytest
import xarray as xr

import oceanarray as oa
from oceanarray import mooring, physics
from oceanarray.location import parse_latlon
from oceanarray.pipeline import masking
from oceanarray.pipeline.coordinate import apply_beam_to_enu
from oceanarray.pipeline.grid import _speed_direction as _grid_speed_direction
from oceanarray.pipeline.grid import grid
from oceanarray.pipeline.qc.originator import MAPPED_SUFFIX
from oceanarray.pipeline.read_mooring import read_mooring
from oceanarray.pipeline.stack import _build_adcp_bin_ds

_FIX = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
_DUNE2 = _FIX / "proc" / "dune2_1_2026"
_ROOT = pathlib.Path(oa.__file__).parent


def _old_direction(east: np.ndarray, north: np.ndarray) -> np.ndarray:
    """The direction formula the writers inlined."""
    return np.degrees(np.arctan2(east, north)) % 360.0


def _old_sigma(sa, ct, t, p, ref_p: int) -> np.ndarray:
    """The σ dispatch the stack inlined."""
    fn = {
        0: gsw.sigma0,
        1000: gsw.sigma1,
        2000: gsw.sigma2,
        3000: gsw.sigma3,
        4000: gsw.sigma4,
    }
    if ref_p in fn:
        return fn[ref_p](sa, ct)
    return gsw.pot_rho_t_exact(sa, t, p, ref_p) - 1000.0


def _same_bytes(a: np.ndarray, b: np.ndarray) -> bool:
    a, b = np.asarray(a), np.asarray(b)
    return a.dtype == b.dtype and a.shape == b.shape and a.tobytes() == b.tobytes()


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out.add("." * node.level + (node.module or ""))
    return out


def test_physics_imports_nothing_from_the_package() -> None:
    """The shared module sits below both the pipeline and the analysis."""
    mods = _imports(_ROOT / "physics.py")
    assert not any(m.startswith(("oceanarray", ".")) for m in mods)


def test_pipeline_does_not_import_analysis() -> None:
    """Analysis imports the pipeline (pressure, concat); never the other way."""
    bad = [
        f"{p.relative_to(_ROOT)}: {m}"
        for p in (_ROOT / "pipeline").rglob("*.py")
        if p != _ROOT / "pipeline" / "clock" / "__init__.py"  # deprecated alias, 0.7.0
        for m in _imports(p)
        if m.startswith(("oceanarray.analysis", "..analysis"))
    ]
    assert bad == []


def test_stage3_enu_speed_and_direction_are_unchanged() -> None:
    """XYZ to ENU on the dune2 Aquadopp: direction from the shared helper, same bits."""
    with xr.open_dataset(
        _DUNE2 / "aquadopp" / "dune2_1_2026_9920_stage2.nc", engine="netcdf4"
    ) as ds:
        ds = ds.load()
    with contextlib.redirect_stdout(io.StringIO()):
        out = apply_beam_to_enu(ds, {"serial": "9920"}, 65.73, -30.2, "yaml")
    e, n = out["east_velocity"].values, out["north_velocity"].values
    both = np.isfinite(e) & np.isfinite(n)
    assert both.sum() > 1000
    assert _same_bytes(
        out["current_direction"].values, np.where(both, _old_direction(e, n), np.nan)
    )
    assert _same_bytes(
        out["current_speed"].values, np.where(both, np.sqrt(e**2 + n**2), np.nan)
    )


def test_adcp_bin_speed_and_direction_are_unchanged() -> None:
    """Each ADCP bin level of a stack: same bits as the inline formulas."""
    with xr.open_dataset(
        _FIX / "adcp" / "dsG2_adcp_stage3_knockdown.nc", engine="netcdf4"
    ) as ds:
        ds = ds.load()
    for b in (0, ds.sizes["N_BINS"] // 2, ds.sizes["N_BINS"] - 1):
        out = _build_adcp_bin_ds(ds, b)
        e = out["east_velocity"].values.astype(float)
        n = out["north_velocity"].values.astype(float)
        assert np.isfinite(e).any()
        assert _same_bytes(
            out["current_direction"].values, _old_direction(e, n).astype(np.float32)
        )
        assert _same_bytes(
            out["current_speed"].values, np.hypot(e, n).astype(np.float32)
        )


def test_grid_speed_and_direction_are_unchanged() -> None:
    """The grid of the dune2 stack: speed and direction of the gridded components."""
    with (
        xr.open_dataset(_DUNE2 / "dune2_1_2026_stack.nc", engine="netcdf4") as ds,
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore")
        g = grid(ds.load(), pmin=100, pmax=800, dp=50)
    assert np.isfinite(g["current_direction"].values).sum() > 100
    # The grid writer's own step, on the gridded components as it holds them
    # (float64, on (pressure, time)), before the file's float32 encoding.
    stacked = {
        v: g[v].values.astype(float).T for v in ("east_velocity", "north_velocity")
    }
    out = _grid_speed_direction(g, stacked, 100.0, 800.0)
    e, n = stacked["east_velocity"].T, stacked["north_velocity"].T
    assert _same_bytes(out["current_direction"].values, _old_direction(e, n))
    assert _same_bytes(out["current_speed"].values, np.hypot(e, n))


@pytest.mark.parametrize(
    ("folder", "name"),
    [
        (_DUNE2, "dune2_1_2026"),
        (_FIX / "proc" / "egM2_concat" / "egM2_2016", "egM2_2016"),
        (_FIX / "proc" / "egM2_concat" / "egM2_2018", "egM2_2018"),
    ],
)
def test_stack_potential_density_is_unchanged(folder: pathlib.Path, name: str) -> None:
    """σ at 0, 2000 and 1500 dbar (the ``pot_rho_t_exact`` path): same bits as before."""
    cfg0 = mooring.load(folder / f"{name}.mooring.yaml")
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        data = read_mooring(cfg0, stage=3, mooring_dir=folder, allow_missing=True)
    lat, lon = parse_latlon(cfg0)
    for ref_p, vname in ((0, "sigma0"), (2000, "sigma2"), (1500, "sigma_1500")):
        cfg = copy.deepcopy(cfg0)
        cfg["density_reference"] = ref_p
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            st = oa.stack({k: v.copy(deep=True) for k, v in data.items()}, cfg)
        rule = masking.from_attrs(st.attrs)

        def usable(var: str, st: xr.Dataset = st, rule: dict = rule) -> np.ndarray:
            values = st[var].transpose("N_LEVELS", "time").values.astype(float)
            for flag in (f"{var}_qc", f"{var}{MAPPED_SUFFIX}"):
                if flag in st:
                    hide = np.isin(
                        st[flag].transpose("N_LEVELS", "time").values,
                        masking.flags_for(rule, var),
                    )
                    values[hide] = np.nan
            return values

        t, s, p = usable("temperature"), usable("salinity"), usable("pressure")
        sa = gsw.SA_from_SP(s, p, lon, lat)
        ct = gsw.CT_from_t(sa, t, p)
        expected = _old_sigma(sa, ct, t, p, ref_p).astype(st[vname].dtype)
        got = st[vname].transpose("N_LEVELS", "time").values
        assert np.isfinite(got).sum() > 100
        np.testing.assert_array_equal(got, expected)
        assert _same_bytes(got, expected)


def test_report_and_pipeline_use_the_same_helpers() -> None:
    """The figures' speed and direction is the pipeline's, not a copy."""
    from oceanarray.analysis import derived

    assert derived.speed_direction is physics.speed_direction
    with xr.open_dataset(_DUNE2 / "dune2_1_2026_stack.nc", engine="netcdf4") as ds:
        s = ds["salinity"].values.astype(float)
        t = ds["temperature"].values.astype(float)
    with pytest.raises(ValueError, match="1500 dbar"):
        physics.potential_density_anomaly(s, t, 1500)


@pytest.mark.parametrize(
    "path, rdi",
    [
        (_FIX / "adcp" / "dsG2_adcp_stage3_knockdown.nc", True),
        (_DUNE2 / "aquadopp" / "dune2_1_2026_9920_stage2.nc", False),
    ],
)
def test_instrument_tilt_is_the_report_panel_tilt(
    path: pathlib.Path, rdi: bool
) -> None:
    """RDI pitch is converted to true pitch; Nortek pitch is used as recorded."""
    from oceanarray.reports._plots import _add_tilt

    with xr.open_dataset(path, decode_timedelta=False) as src:
        ds = src[["pitch", "roll"]].load()
        ds.attrs = dict(src.attrs)
    pitch, roll = ds["pitch"].values.astype(float), ds["roll"].values.astype(float)
    if rdi:
        pitch = np.degrees(
            np.arctan(np.tan(np.radians(pitch)) * np.cos(np.radians(roll)))
        )
    got = physics.instrument_tilt(ds)

    np.testing.assert_array_equal(got, physics.tilt(pitch, roll))
    np.testing.assert_array_equal(_add_tilt(ds)["tilt"].values, got)
