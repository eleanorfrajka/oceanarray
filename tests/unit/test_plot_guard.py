"""Smoke tests for the plot silent-failure guard and instrument figures.

The guard (``RAISE_ON_PLOT_ERROR``) is turned on globally by ``conftest`` for
the duration of every test, so a figure function that fails raises instead of
returning ``None`` — a broken figure that would otherwise vanish silently shows
up here as a failure. Each figure function is paired with the real stage-3
fixture that carries its required variables: scalar T/S functions run on the
microcat (serial 2941), velocity functions on the aquadopp (serial 9920).
"""

import matplotlib.pyplot as plt
import pytest

from oceanarray.reports import _plots
from oceanarray.reports._vendored import design_tokens as report_tokens

# ---------------------------------------------------------------------------
# render_b64 — the project-wide figure-failure envelope
# ---------------------------------------------------------------------------


def _make_trivial_fig() -> plt.Figure:
    """Return a minimal single-axes Figure for use in render_b64 tests."""
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    return fig


def test_render_b64_returns_string_for_good_draw(monkeypatch):
    """``render_b64`` returns a non-empty base64 string when *draw* succeeds."""
    monkeypatch.setattr(report_tokens, "RAISE_ON_PLOT_ERROR", False)
    result = _plots.render_b64(_make_trivial_fig)
    assert isinstance(result, str) and len(result) > 0


def test_render_b64_returns_none_when_draw_returns_none(monkeypatch):
    """``render_b64`` propagates ``None`` from *draw* without error."""
    monkeypatch.setattr(report_tokens, "RAISE_ON_PLOT_ERROR", False)
    assert _plots.render_b64(lambda: None) is None


def test_render_b64_swallows_exception_when_guard_off(monkeypatch):
    """``render_b64`` swallows errors and returns ``None`` when guard is off."""
    monkeypatch.setattr(report_tokens, "RAISE_ON_PLOT_ERROR", False)

    def bad_draw():
        raise RuntimeError("broken")

    assert _plots.render_b64(bad_draw) is None


def test_render_b64_reraises_when_guard_on(monkeypatch):
    """``render_b64`` re-raises when guard is on (test mode)."""
    monkeypatch.setattr(report_tokens, "RAISE_ON_PLOT_ERROR", True)

    def bad_draw():
        raise RuntimeError("broken")

    with pytest.raises(RuntimeError, match="broken"):
        _plots.render_b64(bad_draw)


def test_render_b64_closes_figure_on_success(monkeypatch):
    """``render_b64`` closes the Figure after encoding so it does not leak."""
    monkeypatch.setattr(report_tokens, "RAISE_ON_PLOT_ERROR", False)
    fig = _make_trivial_fig()
    fig_num = fig.number
    _plots.render_b64(lambda: fig)
    assert fig_num not in plt.get_fignums()


def test_render_b64_none_optional_false_guard_on_raises(monkeypatch):
    """``render_b64`` raises ``ValueError`` when *draw* returns ``None``, guard on, optional=False."""
    monkeypatch.setattr(report_tokens, "RAISE_ON_PLOT_ERROR", True)
    with pytest.raises(RuntimeError, match="returned None"):
        _plots.render_b64(lambda: None, optional=False)


def test_render_b64_none_optional_true_guard_on_returns_none(monkeypatch):
    """``render_b64`` silently returns ``None`` when *draw* returns ``None`` and optional=True."""
    monkeypatch.setattr(report_tokens, "RAISE_ON_PLOT_ERROR", True)
    assert _plots.render_b64(lambda: None, optional=True) is None


def test_render_b64_none_optional_false_guard_off_returns_none(monkeypatch):
    """``render_b64`` returns ``None`` (no raise) when guard is off regardless of optional."""
    monkeypatch.setattr(report_tokens, "RAISE_ON_PLOT_ERROR", False)
    assert _plots.render_b64(lambda: None, optional=False) is None


# ---------------------------------------------------------------------------
# Instrument figures on real stage-3 data (guard on via conftest)
# ---------------------------------------------------------------------------

#: nc_path-taking figure functions valid for a scalar (T/S/P) microcat record.
MICROCAT_FUNCS = [
    "_make_data_histogram_b64",
    "_make_ts_diagram_b64",
]

#: nc_path-taking figure functions valid for a single-point velocity record.
#: ``_make_temperature_trajectory_b64`` is a velocity-driven Lagrangian track
#: coloured by temperature (an Aquadopp figure despite its name).
AQUADOPP_FUNCS = [
    "_make_data_histogram_b64",
    "_make_instrument_rose_b64",
    "_make_speed_boxplot_b64",
    "_make_hodograph_b64",
    "_make_temperature_trajectory_b64",
]


@pytest.mark.parametrize("fn_name", MICROCAT_FUNCS)
def test_microcat_figure_functions(microcat_stage3_path, fn_name):
    """Each microcat figure builds without tripping the guard."""
    result = getattr(_plots, fn_name)(str(microcat_stage3_path))
    assert result is not None


@pytest.mark.parametrize("fn_name", AQUADOPP_FUNCS)
def test_aquadopp_figure_functions(aquadopp_stage3_path, fn_name):
    """Each aquadopp figure builds without tripping the guard."""
    result = getattr(_plots, fn_name)(str(aquadopp_stage3_path))
    assert result is not None


def test_make_instrument_fig_microcat(microcat_stage3_path):
    """The (paginated) instrument figure list builds for a microcat."""
    imgs = _plots._make_instrument_b64s(
        str(microcat_stage3_path), "microcat", show_qc=True
    )
    assert isinstance(imgs, list) and len(imgs) >= 1


def test_make_instrument_fig_aquadopp(aquadopp_stage3_path):
    """The (paginated) instrument figure list builds for an aquadopp."""
    imgs = _plots._make_instrument_b64s(
        str(aquadopp_stage3_path), "aquadopp", show_qc=True
    )
    assert isinstance(imgs, list) and len(imgs) >= 1


def test_build_fig_from_ds_microcat(microcat_stage3):
    """``_draw_instrument_panels`` returns a Figure for a microcat dataset."""
    assert _plots._draw_instrument_panels(microcat_stage3, "microcat") is not None


def test_windows_fig_microcat(microcat_stage3_path):
    """``_make_windows_b64s`` builds a (paginated) deployment-window figure list."""
    imgs = _plots._make_windows_b64s(microcat_stage3_path, "microcat")
    assert isinstance(imgs, list) and len(imgs) >= 1


# ---------------------------------------------------------------------------
# render_b64 — constrained_layout detection
# ---------------------------------------------------------------------------


def test_render_b64_skips_tight_layout_for_constrained_layout_fig(monkeypatch):
    """``render_b64`` does not call tight_layout on a constrained_layout figure.

    Calling tight_layout on a figure with colorbars that used constrained_layout
    raises RuntimeError.  ``render_b64`` must detect the layout engine and skip
    the call to avoid this.
    """
    monkeypatch.setattr(report_tokens, "RAISE_ON_PLOT_ERROR", True)

    def _make_constrained_with_colorbar():
        import numpy as np

        fig, ax = plt.subplots(constrained_layout=True)
        pc = ax.pcolormesh(np.random.default_rng(0).random((4, 4)))
        fig.colorbar(pc, ax=ax)
        return fig

    # Would raise RuntimeError if render_b64 incorrectly called tight_layout
    result = _plots.render_b64(_make_constrained_with_colorbar)
    assert result is not None


# ---------------------------------------------------------------------------
# Grid-level figures on real gridded data
# ---------------------------------------------------------------------------

#: Dataset-in grid figure functions that take only ``ds`` (no extra args).
GRID_DS_FUNCS = [
    "_make_grid_hydro_b64",
    "_make_grid_velocity_stacked_b64",
    "_make_grid_sigma_b64",
    "_make_grid_rose_b64",
    "_make_grid_trajectory_b64",
    "_make_grid_timeseries_b64",
    "_make_velocity_iqr_profile_b64",
    "_make_grid_rotary_spectrum_b64",
]


@pytest.mark.parametrize("fn_name", GRID_DS_FUNCS)
def test_grid_figure_functions(grid_ds, fn_name):
    """Each grid figure function builds without tripping the guard."""
    result = getattr(_plots, fn_name)(grid_ds)
    assert result is not None


def test_grid_n2_b64(grid_ds):
    """``_make_grid_n2_b64`` builds the N² profile figure."""
    result = _plots._make_grid_n2_b64(grid_ds, lat=57.0)
    assert result is not None


def test_grid_ts_diagram_returns_tuple(grid_ds):
    """``_make_grid_ts_diagram_b64`` returns a (b64_str, bounds_dict) tuple."""
    b64, bounds = _plots._make_grid_ts_diagram_b64(grid_ds)
    assert b64 is not None
    assert "t_lim" in bounds and "s_lim" in bounds


def test_spectrum_fig_from_grid(grid_ds):
    """``_make_spectrum_b64`` builds a power-spectrum figure from grid temperature."""
    import numpy as np

    dt_seconds = float(
        np.median(
            np.diff(grid_ds["time"].values).astype("timedelta64[s]").astype(float)
        )
    )
    result = _plots._make_spectrum_b64(grid_ds["temperature"], dt_seconds)
    assert result is not None


# ---------------------------------------------------------------------------
# Stack-level figures on real stacked data
# ---------------------------------------------------------------------------


def test_stack_ts_diagram(stack_ds):
    """``_make_stack_ts_diagram_b64`` builds a T-S figure from stacked mooring data."""
    assert _plots._make_stack_ts_diagram_b64(stack_ds) is not None


def test_rose_grid_returns_tuple(stack_ds):
    """``_make_rose_grid_b64`` returns a (b64_str, n_panels) tuple."""
    serial_list = list(stack_ds["serial"].values)
    b64, n = _plots._make_rose_grid_b64(stack_ds, serial_list)
    assert b64 is not None
    assert n > 0
