"""Figures: functions that draw a dataset and return a matplotlib Figure; no HTML, no files.

Three-tier architecture
-----------------------
Tier 1  primitives.py   Data-agnostic plot primitives (array-in / Figure-out).
Tier 2  current.py etc. Domain wrappers (xr.Dataset-in / Figure-out).
Tier 3  report/_plots.py Thin report wrappers (path-in / base64-out).

Separate modules (not part of the Tier pipeline)
-------------------------------------------------
animation.py    Animated GIF output via matplotlib.animation + pillow.
                Intentionally isolated so plotly/interactive.py can be
                added alongside it later without coupling to the static stack.

Public API
----------
Current-meter figures (current.py, animation.py):

  draw_temperature_trajectory  Lagrangian trajectory coloured by temperature
  draw_speed_boxplot           Current speed boxplot with percentile statistics
  draw_hodograph               Two-panel hodograph coloured by time
  animate_hodograph            Animated GIF hodograph (requires pillow)

Domain ``draw_*`` functions:

  Timeseries (timeseries.py):
    draw_grid, draw_grid_hydro, draw_grid_velocity_stacked,
    draw_grid_sigma, draw_grid_n2, draw_grid_timeseries,
    draw_analog_timeseries

  Current / velocity (current.py):
    draw_instrument_rose, draw_rose_grid, draw_grid_rose,
    draw_grid_trajectory, draw_adcp_velocity, draw_adcp_rose,
    draw_adcp_hodograph, draw_grid_hodograph

  Spectral (spectrum.py):
    draw_spectrum, draw_wavelet, draw_grid_rotary_spectrum

  Diagnostic (diagnostic.py):
    draw_windows, draw_data_histogram, draw_velocity_iqr_profile

  T-S (ts.py):
    draw_ts_diagram, draw_stack_ts_diagram, draw_grid_ts_diagram

  Hydrography (hydrography.py):
    draw_isopycnal_ts, draw_isopycnal_coverage,
    draw_overflow_temperature

CLI quick-look functions (_cli_legacy.py):

  draw_microcat_raw, draw_aquadopp_raw, draw_mooring_timeseries

kept for ``oceanarray plot`` and ``process --plot`` until those become a
file-oriented ``oceanarray plot <file>``.
"""

# ---------------------------------------------------------------------------
# CLI quick-look functions — stay until the file-oriented `oceanarray plot <file>`
# replaces them; _cli_legacy.py is deleted then. Do not add entries.
# ---------------------------------------------------------------------------
from oceanarray.plotters._cli_legacy import (
    draw_aquadopp_raw,
    draw_microcat_raw,
    draw_mooring_timeseries,
)
from oceanarray.plotters.animation import animate_hodograph

# ---------------------------------------------------------------------------
# draw_* functions — current / velocity
# ---------------------------------------------------------------------------
from oceanarray.plotters.current import (
    draw_adcp_hodograph,
    draw_adcp_rose,
    draw_adcp_velocity,
    draw_aquadopp_speed_profile,
    draw_grid_hodograph,
    draw_grid_rose,
    draw_grid_trajectory,
    draw_hodograph,
    draw_instrument_rose,
    draw_multi_aquadopp_trajectories,
    draw_rose_grid,
    draw_speed_boxplot,
    draw_temperature_trajectory,
)

# ---------------------------------------------------------------------------
# draw_* functions — diagnostic
# ---------------------------------------------------------------------------
from oceanarray.plotters.diagnostic import (
    draw_data_histogram,
    draw_velocity_iqr_profile,
    draw_windows,
)

# ---------------------------------------------------------------------------
# draw_* functions — isopycnal
# ---------------------------------------------------------------------------
from oceanarray.plotters.hydrography import (
    draw_isopycnal_coverage,
    draw_isopycnal_ts,
    draw_overflow_temperature,
)

# ---------------------------------------------------------------------------
# draw_* functions — spectral / section
# ---------------------------------------------------------------------------
from oceanarray.plotters.spectrum import (
    draw_grid_rotary_spectrum,
    draw_spectrum,
    draw_wavelet,
)

# ---------------------------------------------------------------------------
# draw_* functions — timeseries
# ---------------------------------------------------------------------------
from oceanarray.plotters.timeseries import (
    draw_analog_timeseries,
    draw_grid,
    draw_grid_hydro,
    draw_grid_n2,
    draw_grid_sigma,
    draw_grid_timeseries,
    draw_grid_velocity_stacked,
)

# ---------------------------------------------------------------------------
# draw_* functions — T-S
# ---------------------------------------------------------------------------
from oceanarray.plotters.ts import (
    draw_grid_ts_diagram,
    draw_stack_ts_diagram,
    draw_ts_diagram,
)

__all__ = [
    # draw_* — current meters (Figure per dataset)
    "draw_temperature_trajectory",
    "draw_speed_boxplot",
    "draw_multi_aquadopp_trajectories",
    "draw_aquadopp_speed_profile",
    "draw_hodograph",
    "animate_hodograph",
    # draw_* — timeseries
    "draw_grid",
    "draw_grid_hydro",
    "draw_grid_velocity_stacked",
    "draw_grid_sigma",
    "draw_grid_n2",
    "draw_grid_timeseries",
    "draw_analog_timeseries",
    # draw_* — current / velocity
    "draw_instrument_rose",
    "draw_rose_grid",
    "draw_grid_rose",
    "draw_grid_trajectory",
    "draw_adcp_velocity",
    "draw_adcp_rose",
    "draw_adcp_hodograph",
    "draw_grid_hodograph",
    # draw_* — spectral / section
    "draw_spectrum",
    "draw_wavelet",
    "draw_grid_rotary_spectrum",
    # draw_* — diagnostic
    "draw_windows",
    "draw_data_histogram",
    "draw_velocity_iqr_profile",
    # draw_* — T-S
    "draw_ts_diagram",
    "draw_stack_ts_diagram",
    "draw_grid_ts_diagram",
    # draw_* — isopycnal
    "draw_isopycnal_ts",
    "draw_isopycnal_coverage",
    "draw_overflow_temperature",
    # Legacy shim
    "draw_microcat_raw",
    "draw_aquadopp_raw",
    "draw_mooring_timeseries",
]
