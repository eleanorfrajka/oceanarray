"""Package-level defaults for oceanarray.

Matplotlib appearance (font sizes, figure size, DPI, grid style) belongs in
the vendored ``reports/_vendored/report.mplstyle`` — that is the right place for
anything that maps to a matplotlib rcParam.  There is a single style file: it is
applied both by the report encoder and by plotters that set their own style
context (via :data:`MPLSTYLE` below), so the two can never diverge.

This file holds the things the mplstyle *cannot* express: instrument
abbreviations, colorbar percentile clipping, downsample interval, and
figure sizes that differ from the per-plot mplstyle default.

Import and reassign any value here to override it globally, e.g.::

    import oceanarray.parameters as params
    params.DOWNSAMPLE_SECONDS = 60
    params.DEFAULT_COLORMAP = "viridis"

These values are read at call time, so assignment before calling a function
is sufficient.
"""

from pathlib import Path

import numpy as np

from oceanarray.config.unit_tables import unit_label

# ---------------------------------------------------------------------------
# Package identity (shown as the report masthead wordmark)
# ---------------------------------------------------------------------------
PACKAGE_NAME = "oceanarray"

#: Height (inches) of one gridded-section / time-series panel row at full width.
#: Matches the per-row height of the two-row "velocity at depth" figure (5" / 2),
#: so stacked grid panels and single-row section figures share one scale and do
#: not render over-tall.
GRID_PANEL_ROW_IN: float = 2.5

# ---------------------------------------------------------------------------
# Matplotlib style path (used by plotters via plt.style.use).  The single source
# of truth — the same vendored file the report encoder applies
# (design_tokens.MPLSTYLE_PATH).
# ---------------------------------------------------------------------------
MPLSTYLE = Path(__file__).parent.parent / "reports" / "_vendored" / "report.mplstyle"

# ---------------------------------------------------------------------------
# Figure sizes for plot types that differ from the mplstyle default (8×4)
# ---------------------------------------------------------------------------
FIGURE_SIZE_WIDE = (14, 6)  # multi-instrument overview (scatter / line)
FIGURE_SIZE_TALL = (12, 8)  # stacked single-instrument panels

# Report figure slot widths (W_FULL / W_TWOTHIRDS / W_HALF / W_THIRD, inches) now
# live in ``oceanarray.config.report_tokens`` as the single source of truth
# (derived from SLOTS; spec §11).  Import them from there, not from here.

# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------
DOWNSAMPLE_SECONDS = 120  # default resample interval for mooring plots

# ---------------------------------------------------------------------------
# Colormaps and colorbar clipping
# (these have no mplstyle equivalent)
# ---------------------------------------------------------------------------
DEFAULT_COLORMAP = "RdBu_r"  # red (warm) → blue (cold); good for temperature
COLORBAR_PLOW = 5  # lower percentile for vmin
COLORBAR_PHIGH = 95  # upper percentile for vmax

# ---------------------------------------------------------------------------
# Density reference pressure (dbar) for potential density anomaly at stack step.
# 0 → sigma-0 (suitable for shallow moorings < ~1000 m)
# 2000 → sigma-2 (suitable for deep moorings)
# Override per mooring in YAML: density_reference: 2000
# ---------------------------------------------------------------------------
DENSITY_REFERENCE = 0
DENSITY_COLORMAP = "BuPu"  # ColorBrewer sequential: light-cyan → dark-purple
# Iso-density contour lines overlaid on sigma pcolormesh plots.
# Set to [] to suppress.  Values are in kg m-3 (above 1000).
SIGMA_CONTOUR_LEVELS = [27.7, 27.8]
# Isopycnal tracking: default σ₀ target values for the grid report
# height-above-seabed time series.  Override via --sig-level on the CLI.
# Values are σ₀ (potential density anomaly referenced to 0 dbar, kg m⁻³).
# To track σ₂ surfaces instead, set sigma_var in the mooring YAML and supply
# the corresponding density values here.  (Future: --sig-ref flag.)
SIGMA_GRID: np.ndarray = np.array([27.7])

# Colors for instrument types in stack-level time series plots.
# Keys match the 'instrument' field in the mooring YAML.
INSTRUMENT_COLORS = {
    "microcat": "#1f77b4",  # blue
    "aquadopp": "#d62728",  # red
    "rbr": "#2ca02c",  # green
    "rbr-solo": "#2ca02c",
    "rbr-duet": "#17becf",  # teal
    "default": "#7f7f7f",  # gray for unknown types
}

# ---------------------------------------------------------------------------
# Instrument abbreviations  (instrument directory name → short label prefix)
#
# Used to build labels like "MC2942 868m" in multi-instrument plots.
# Add entries here to support new instrument types without editing plotters.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# OceanSITES QC flag table (Reference Table 2)
# Applied to *_qc companion variables in _stage2.nc and stage 3 output.
# ---------------------------------------------------------------------------
QC_CONVENTION = "OceanSITES reference table 2"
# Codes 5 (value_changed) and 6 are "not used" in OceanSITES table 2, so they are
# omitted here — declaring them legal invites flags nothing downstream understands.
# flag_meanings words correspond position-for-position with flag_values.
QC_FLAG_VALUES = [0, 1, 2, 3, 4, 7, 8, 9]
QC_FLAG_MEANINGS = (
    "unknown good_data probably_good_data potentially_correctable_bad_data "
    "bad_data nominal_value interpolated_value missing_value"
)

# Merge priority for combining two QC flags (worst wins). The single source of
# truth for flag ordering — both instrument.qc._merge_flags and
# mooring.helpers._worst_flag derive their lookup arrays from this dict.
# Weakest to strongest:
#   unknown(0) < good(1) < probably_good(2) < nominal(7) < interpolated(8)
#   < potentially_correctable_bad(3) < bad(4) < missing(9)
# unknown(0) is the weakest (opposite of QARTOD, which ranks its UNKNOWN=2 above
# GOOD): a point reads unknown only when nothing evaluated it. Unlisted codes
# (5, 6 — not used in OceanSITES) fall back to priority 0.
QC_MERGE_PRIORITY = {9: 7, 4: 6, 3: 5, 8: 4, 7: 3, 2: 2, 1: 1, 0: 0}

# Precomputed lookup arrays (immutable; built once at import) so the merge
# functions and flag-attr writers don't rebuild them on every call.
# QC_MERGE_PRIORITY_LUT[flag] gives the merge priority for flags 0-9.
QC_MERGE_PRIORITY_LUT = np.array(
    [QC_MERGE_PRIORITY.get(f, 0) for f in range(10)], dtype="int8"
)
# flag_values as int8 (CF wants it to match the int8 flag variable dtype).
QC_FLAG_VALUES_I8 = np.array(QC_FLAG_VALUES, dtype="int8")

# Flags whose values a plot leaves out: potentially_correctable_bad (3), bad (4) and
# missing (9). nominal (7) and interpolated (8) are kept — an interpolated pressure
# is the instrument's pressure, and dropping it hides a sensorless instrument.
QC_BAD_FLAGS = (3, 4, 9)

# ---------------------------------------------------------------------------
# QARTOD gross-range test thresholds (global ocean defaults).
#
# fail_span   : values outside this range are flagged 4 (bad).
# suspect_span: values outside this but inside fail_span → flag 3 (suspect).
#
# Stage 3 normalises conductivity to mS/cm before applying QC, so thresholds
# here are always interpreted in the units shown in the comments below.
# Override per mooring in the YAML top-level key ``qc_ranges``, or per
# instrument in each clamp entry's ``qc_ranges`` key.
# ---------------------------------------------------------------------------
QC_GROSS_RANGE: dict = {
    "temperature": {"fail_span": (-2.5, 40.0), "suspect_span": (-2.0, 35.0)},  # degC
    "conductivity": {
        "fail_span": (0.0, 75.0),
        "suspect_span": (0.0, 65.0),
    },  # mS/cm  (ocean: 20–60)
    "salinity": {
        "fail_span": (0.0, 40.0),
        "suspect_span": (2.0, 40.0),
    },  # PSU    (ocean: 30–38)
    "pressure": {"fail_span": (-5.0, 7000.0), "suspect_span": (-0.5, 7000.0)},  # dbar
    "east_velocity": {"fail_span": (-5.0, 5.0), "suspect_span": (-3.0, 3.0)},  # m/s
    "north_velocity": {"fail_span": (-5.0, 5.0), "suspect_span": (-3.0, 3.0)},  # m/s
    "up_velocity": {"fail_span": (-1.0, 1.0), "suspect_span": (-0.5, 0.5)},  # m/s
    # Turbidity: negative values are physically impossible (no lower bound in nature).
    # Upper bound is generous — coastal resuspension events can reach 1000 NTU.
    # Override per mooring if the sensor range is known to be tighter.
    "turbidity": {
        "fail_span": (-10.0, 4000.0),
        "suspect_span": (-5.0, 1000.0),
    },  # NTU; lower bounds unverified — sensors can read slightly negative near zero
    # Dissolved oxygen (SBE ODO sensor, µmol/L).  Deep North Atlantic: 200–320 µmol/L.
    # Negative values are physically impossible → fail.  >500 µmol/L is unrealistic
    # even near the surface (photosynthetic supersaturation ~350–400 µmol/L).
    # Override per mooring via qc_ranges YAML key.
    "dissolved_oxygen": {
        "fail_span": (0.0, 500.0),
        "suspect_span": (0.0, 450.0),
    },  # umol kg-1  (per-mass numbers differ from per-volume by ~2.5%, noise for a gross-range span)
    "dissolved_oxygen_2": {  # second per-mass oxygen sensor — same span as dissolved_oxygen
        "fail_span": (0.0, 500.0),
        "suspect_span": (0.0, 450.0),
    },  # umol kg-1
    # O2 % saturation: >200 % would indicate severe bubble entrainment or sensor fault.
    "oxygen_saturation_pct": {"fail_span": (0.0, 200.0), "suspect_span": (0.0, 150.0)},
}

# ---------------------------------------------------------------------------
# QARTOD spike test thresholds (global ocean defaults).
#
# The spike test flags a point n if |x[n] - (x[n-2]+x[n+1])/2| exceeds the
# threshold.  Thresholds therefore scale with the *typical* variability at the
# sampling interval in use — these defaults assume a fixed mooring in the deep
# ocean sampled at O(60–120 s).  Shallower or more energetic moorings may
# need smaller suspect_threshold values set via YAML.
#
# Units match the NC variable units.
# ---------------------------------------------------------------------------
QC_SPIKE: dict = {
    "temperature": {"suspect_threshold": 2.0, "fail_threshold": 6.0},  # degC
    "conductivity": {
        "suspect_threshold": 2.0,
        "fail_threshold": 5.0,
    },  # mS/cm  (low spikes: biofouling)
    "salinity": {
        "suspect_threshold": 1,
        "fail_threshold": 2.0,
    },  # PSU    (timing issues on unpumped sensors)
    "pressure": {"suspect_threshold": 10.0, "fail_threshold": 50.0},  # dbar
    # Velocity spike test omitted: burst-mode Aquadopps generate false
    # positives at every burst boundary, and real oceanic events can produce
    # large velocity changes on short time scales.  Add per-instrument via
    # qc_spike: YAML key if needed for a specific deployment.
}

# ---------------------------------------------------------------------------
# QARTOD flat-line (stuck-value) test thresholds.
#
# Applied to pressure only by default.  A value is considered "stuck" when it
# does not change by more than ``tolerance`` over a contiguous window.
#
# suspect_n : flag SUSPECT when stuck for this many consecutive samples
# fail_n    : flag FAIL when stuck for this many consecutive samples
# tolerance : maximum allowed change that is still considered "flat" (default 0)
#
# In ``_apply_qc_tests`` (stage3.py), ``suspect_n`` / ``fail_n`` are multiplied
# by the median inter-sample interval (seconds) before being passed to
# ``ioos_qc.qartod.flat_line_test``, which takes its thresholds in seconds.
# Override per mooring via ``qc_flat_line`` YAML key.
# ---------------------------------------------------------------------------
QC_FLAT_LINE: dict = {
    # tolerance must be > 0: ioos_qc flags when range < tolerance over the
    # window, so tolerance=0 means range < 0 which is never true.
    # 0.001 dbar is below any real mooring pressure variability and catches
    # sensors returning the exact same value (e.g. 4959 stuck at 0 dbar).
    "pressure": {"suspect_n": 3, "fail_n": 10, "tolerance": 0.001},
}

# ---------------------------------------------------------------------------
# Tilt QC thresholds for Aquadopp (applied to velocity variables when |roll|
# exceeds these values; degrees).
#
# suspect_threshold : 3 (suspect/possibly bad)
# fail_threshold    : 4 (bad/probably bad)
#
# Override per mooring in ``tilt_qc`` YAML key, or per instrument.
# ---------------------------------------------------------------------------
QC_TILT: dict = {
    "suspect_threshold": 30.0,
    "fail_threshold": 50.0,
}

# ---------------------------------------------------------------------------
# ADCP-specific QC thresholds.
#
# percent_good_bad     : bins where percent_good column 3 (fraction of 4-beam
#                        solutions) falls below this threshold are flagged bad (4).
#                        Column 3 is used — NOT the mean across beams — because
#                        a cross-beam mean would give ~25 % even for perfect data.
# percent_good_suspect : bins between percent_good_bad and this threshold
#                        are flagged suspect (3).
# error_velocity_threshold : bins where |error_velocity| exceeds this value
#                        are flagged bad (4). m/s.
# ---------------------------------------------------------------------------
QC_ADCP: dict = {
    "percent_good_bad": 25.0,
    "percent_good_suspect": 50.0,
    "error_velocity_threshold": 0.3,
}

# ---------------------------------------------------------------------------
# ADCP bin dimension name.
# Never hard-code "N_BINS" — reference this constant everywhere so the
# dimension can be renamed to "N_LEVELS" later by changing one line.
# ---------------------------------------------------------------------------
ADCP_BIN_DIM: str = "N_BINS"

INSTRUMENT_ABBREV = {
    "microcat": "MC",
    "aquadopp": "AQ",
    "rbrsolo": "RS",
    "rbrduet": "RD",
    "sbe56": "SB",
    "sbe16": "SB",
    "tr1050": "TR",
    "adcp": "AD",
}

# ---------------------------------------------------------------------------
# Instrument type → allowed file_type values for the mooring YAML.
#
# ``instrument:``  is the human-readable name stored in instrument_type
#                  coordinates and used to dispatch Aquadopp-specific plots,
#                  declination correction, tilt QC, etc.
#
# ``file_type:``   is the seasenselib reader key that controls how the raw
#                  file is parsed.  Each instrument may have several variants.
#                  ``oceansites`` and ``rodb`` are read by oceanarray's own
#                  readers (``oceanarray.readers``): files already processed by
#                  their originator (OceanSITES netCDF, RAPID/BODC RODB text).
#
# To add a new instrument class: add an entry here.  Everything that checks
# instrument type (reports, mooring_level, plotters) reads KNOWN_INSTRUMENT_TYPES
# from this dict.
# ---------------------------------------------------------------------------
INSTRUMENT_FILE_TYPES: dict = {
    "microcat": ["sbe-cnv", "sbe-ascii", "sbe-hex", "oceansites", "rodb"],
    "sbe56": ["sbe-cnv", "sbe-ascii"],  # SeaBird SBE56 temperature logger
    "sbe16": ["sbe-cnv", "sbe-hex"],  # SeaBird SBE16 CTD
    "aquadopp": ["nortek-raw", "nortek-ascii", "nortek-csv"],
    # A point current meter delivered already processed (velocities in geographic
    # east/north), e.g. an Aanderaa RCM from an OceanSITES or RODB archive.  Read by
    # oceanarray's own readers (oceanarray.readers), not seasenselib.
    "currentmeter": ["oceansites", "rodb"],
    "tr1050": ["rbr-hex"],  # RBR TR-1050 thermistor chain
    "rbrsolo": ["rbr-rsk"],  # RBR soloT — single-channel temperature
    "rbrduet": ["rbr-rsk"],  # RBR duet — temperature + pressure or T+C
    "seapoint": ["rbr-rsk"],  # Seapoint turbidity sensor via RBR logger
    "ADCP": [
        "rdi-raw",  # RDI Workhorse / Sentinel via dolfyn (mhkit[dolfyn] required)
        "adcp-matlab",  # seasenselib auto-detects RD-ADCP vs UHHDS Matlab exports
    ],  # Generic ADCP (instrument: ADCP in YAML — note uppercase)
}

# Derived: set of allowed ``instrument:`` values in mooring YAML files.
KNOWN_INSTRUMENT_TYPES: frozenset = frozenset(INSTRUMENT_FILE_TYPES.keys())

# File types that seasenselib accepts but are deprecated, experimental, or
# not tied to a primary ``instrument:`` value above.  These remain valid in a
# mooring YAML but are not the recommended key for any current instrument class.
EXTRA_FILE_TYPES: dict = {
    "nortek-csv-oa": "aquadopp (DEPRECATED internal reader; use nortek-csv)",
    "rbr-dat": "RBR instruments (not in current seasenselib; use rbr-rsk)",
    "rbr-matlab": "RBR instruments (seasenselib Matlab reader)",
    "rbr-matlab-legacy": "RBR instruments (DEPRECATED legacy format)",
}

# Deprecated ``file_type:`` values that are silently remapped to a current type
# at read time.  These are intentionally *not* part of ``ALL_FILE_TYPES`` so they
# no longer appear in any public list (autodoc ``SUPPORTED_FILE_TYPES``, the
# validator's "known types" message).  A YAML using one still loads: stage1
# remaps it (with a DeprecationWarning) and the validator emits a deprecation
# WARNING pointing to the canonical type.  ``rbr-hex-oa`` predates seasenselib's
# ``rbr-hex`` support (its internal reader has been removed).
DEPRECATED_FILE_TYPE_ALIASES: dict = {
    "rbr-hex-oa": "rbr-hex",
    "sbe-asc": "sbe-ascii",
}

# Single source of truth for valid ``file_type:`` values in a mooring YAML.
# Union of every per-instrument reader key with the extra/deprecated keys above.
# ``validation.VALID_FILE_TYPES`` and ``stage1.SUPPORTED_FILE_TYPES`` both derive
# from this — add a new file type to ``INSTRUMENT_FILE_TYPES`` or
# ``EXTRA_FILE_TYPES``, never to those consumers.  Deprecated aliases are
# deliberately excluded (see ``DEPRECATED_FILE_TYPE_ALIASES``).
ALL_FILE_TYPES: frozenset = frozenset(
    ft for fts in INSTRUMENT_FILE_TYPES.values() for ft in fts
) | frozenset(EXTRA_FILE_TYPES)

# ---------------------------------------------------------------------------
# Variable display registry
# ---------------------------------------------------------------------------

#: Display metadata for each physical variable.
#:
#: Each entry has:
#:
#: ``label``
#:     human-readable name for plot titles and legends.
#: ``units``
#:     udunits-2 / CF-compliant ASCII units for NetCDF attributes (e.g.
#:     ``"degree_Celsius"``).  The display unit for an axis label is derived from
#:     this through :func:`vunit` / :func:`vlabel` (``degree_Celsius`` → ``°C``) —
#:     there is no separate ``label_units`` column, so the display spelling cannot
#:     drift from the stored one.
#: ``standard_name``
#:     CF standard name.  ``None`` when no standard name exists
#:     (e.g. turbidity in NTU, which has no udunits-2 unit).
#: ``cmap``
#:     default matplotlib colormap name, or ``None`` for variables
#:     without a natural diverging / sequential convention.
#: ``valid_min`` / ``valid_max``
#:     CF ``valid_min`` / ``valid_max`` attributes written to NetCDF output
#:     (physically valid range).  Present only where
#:     :file:`legacy/OS1_variable_attributes.yaml` provides an authoritative value;
#:     absent for other variables.
VARIABLES: dict[str, dict] = {
    "temperature": {
        "label": "Temperature",
        "units": "degree_Celsius",
        "standard_name": "sea_water_temperature",
        "cmap": "RdBu_r",
        "valid_min": -5.0,  # legacy/OS1_variable_attributes.yaml TEMP
        "valid_max": 42.0,
    },
    "conservative_temperature": {
        "label": "Conservative temperature",
        "units": "degree_Celsius",
        "standard_name": "sea_water_conservative_temperature",
        "cmap": "RdBu_r",
        "valid_min": -5.0,  # same physical range as temperature
        "valid_max": 42.0,
    },
    "potential_temperature": {
        "label": "Potential temperature",
        "units": "degree_Celsius",
        "standard_name": "sea_water_potential_temperature",
        "cmap": "RdBu_r",
        "valid_min": -5.0,  # same physical range as temperature
        "valid_max": 42.0,
    },
    "conductivity": {
        "label": "Conductivity",
        "units": "mS cm-1",
        "standard_name": "sea_water_electrical_conductivity",
        "cmap": None,
        "valid_min": 0.0,  # legacy/OS1_variable_attributes.yaml CNDC
        "valid_max": 80.0,
    },
    "salinity": {
        "label": "Salinity",
        "units": "1",  # PSS-78 is dimensionless per CF
        "standard_name": "sea_water_practical_salinity",
        "cmap": "YlGnBu_r",
    },
    "absolute_salinity": {
        "label": "Absolute salinity",
        "units": "g kg-1",
        "standard_name": "sea_water_absolute_salinity",
        "cmap": "YlGnBu_r",
    },
    "pressure": {
        "label": "Pressure",
        "units": "dbar",
        "standard_name": "sea_water_pressure",
        "cmap": None,
        "valid_min": 0.0,  # legacy/OS1_variable_attributes.yaml PRES
        "valid_max": 11000.0,
    },
    "depth": {
        "label": "Depth",
        "units": "m",
        "standard_name": "depth",
        "cmap": None,
        "valid_min": 0.0,  # legacy/OS1_variable_attributes.yaml DEPTH
        "valid_max": 12000.0,
    },
    "potential_density": {
        "label": "σ₀",
        "units": "kg m-3",
        # sea_water_sigma_theta = potential density anomaly referenced to 0 dbar
        # (i.e. rho(T_potential, S, p=0) − 1000 kg m-3)
        "standard_name": "sea_water_sigma_theta",
        "cmap": "BuPu",
    },
    "u": {
        "label": "Eastward velocity",
        "units": "m s-1",
        "standard_name": "eastward_sea_water_velocity",
        "cmap": "RdBu_r",
    },
    "v": {
        "label": "Northward velocity",
        "units": "m s-1",
        "standard_name": "northward_sea_water_velocity",
        "cmap": "RdBu_r",
    },
    "w": {
        "label": "Vertical velocity",
        "units": "m s-1",
        "standard_name": "upward_sea_water_velocity",
        "cmap": "RdBu_r",
    },
    "east_velocity": {
        "label": "Eastward velocity",
        "units": "m s-1",
        "standard_name": "eastward_sea_water_velocity",
        "cmap": "RdBu_r",
    },
    "north_velocity": {
        "label": "Northward velocity",
        "units": "m s-1",
        "standard_name": "northward_sea_water_velocity",
        "cmap": "RdBu_r",
    },
    "up_velocity": {
        "label": "Upward velocity",
        "units": "m s-1",
        "standard_name": "upward_sea_water_velocity",
        "cmap": "RdBu_r",
    },
    "speed": {
        "label": "Speed",
        "units": "m s-1",
        "standard_name": "sea_water_speed",
        "cmap": "plasma",
    },
    "dissolved_oxygen": {
        "label": "Dissolved oxygen",
        "units": "umol kg-1",
        # SBE 63/43 report dissolved oxygen per unit mass (…Mm/Kg columns); the
        # standard name's canonical units are mol kg-1 (molality).  A per-volume
        # oxygen, if ever read, is a different variable keeping the
        # mole_concentration_… name — mass↔volume is never a scalar unit alias.
        "standard_name": "moles_of_oxygen_per_unit_mass_in_sea_water",
        "cmap": "RdYlGn",
    },
    "dissolved_oxygen_2": {
        # Second per-mass oxygen sensor (SBE ``sbeox1Mm/Kg``); same as
        # dissolved_oxygen, registered so the units pass canonicalises it.
        "label": "Dissolved oxygen (sensor 2)",
        "units": "umol kg-1",
        "standard_name": "moles_of_oxygen_per_unit_mass_in_sea_water",
        "cmap": "RdYlGn",
    },
    "dissolved_oxygen_ml_l": {
        "label": "Dissolved oxygen",
        "units": "mL L-1",
        # mL/L is a volume ratio, not a molar concentration; no exact CF standard_name.
        # mole_concentration_* has canonical units mol m-3 and is dimensionally
        # incompatible with mL/L.
        "standard_name": None,
        "cmap": "RdYlGn",
    },
    "dissolved_oxygen_ml_l_2": {
        # Second oxygen sensor's mL/L reading (SBE ``sbeox1ML/L``); same units as
        # dissolved_oxygen_ml_l, registered so the units pass canonicalises it
        # instead of leaving it untouched.
        "label": "Dissolved oxygen (sensor 2)",
        "units": "mL L-1",
        "standard_name": None,
        "cmap": "RdYlGn",
    },
    "oxygen_saturation_pct": {
        "label": "O₂ percent saturation",
        "units": "%",
        # CF "fractional_saturation_of_oxygen_in_sea_water" is 0–1, not 0–100; no
        # unambiguous standard_name for the percent form.
        "standard_name": None,
        "cmap": "RdYlGn",
    },
    "apparent_oxygen_utilization": {
        "label": "Apparent oxygen utilization",
        "units": "umol kg-1",
        "standard_name": "apparent_oxygen_utilization",
        "cmap": None,
    },
    "turbidity": {
        "label": "Turbidity",
        "units": "1",  # CF-canonical dimensionless, as the FLNTU reader writes
        "standard_name": "sea_water_turbidity",
        "cmap": "YlOrBr",
    },
    "n2": {
        "label": "N²",
        "units": "s-2",
        "standard_name": "square_of_brunt_vaisala_frequency_in_sea_water",
        "cmap": "plasma",
    },
    # Attitude / engineering variables.  Canonical CF ``degree``; the readers write
    # ``degrees`` (an alias in unit_tables.UNIT_ALIAS_EXTRAS, evidenced on dune2).
    # Registered so the units pass canonicalises them and ``vunit`` labels them ``°``.
    "pitch": {
        "label": "Pitch",
        "units": "degree",
        "standard_name": "platform_pitch_angle",
        "cmap": None,
    },
    "roll": {
        "label": "Roll",
        "units": "degree",
        "standard_name": "platform_roll_angle",
        "cmap": None,
    },
    "heading": {
        "label": "Heading",
        "units": "degree",
        "standard_name": "platform_heading_angle",
        "cmap": None,
    },
    "tilt": {
        # Derived from pitch/roll (arccos, in _plots._augment_tilt); no CF standard name.
        "label": "Tilt",
        "units": "degree",
        "standard_name": None,
        "cmap": None,
    },
    "current_direction": {
        "label": "Current direction",
        "units": "degree",
        "standard_name": "direction_of_sea_water_velocity",
        "cmap": None,
    },
    "bin_pressure": {
        # ADCP per-bin pressure (dbar).  Matched by name, so no standard_name:
        # sea_water_pressure already maps to "pressure" — a second entry would clash
        # in the standard-name → canonical map.
        "label": "Bin pressure",
        "units": "dbar",
        "standard_name": None,
        "cmap": None,
    },
}

#: Colormap lookup derived from :data:`VARIABLES` — single source of truth so
#: the two cannot drift.  Excludes entries where ``cmap`` is ``None``.
CMAPS_BY_VARIABLE: dict[str, str] = {
    k: v["cmap"] for k, v in VARIABLES.items() if v.get("cmap") is not None
}

#: Colormaps for colouring *lines* (one per instrument, deep-first) — distinct
#: from the pcolormesh field maps in :data:`CMAPS_BY_VARIABLE`, because a field
#: map that is fine for a filled panel can be wrong for overlaid lines (e.g. a
#: diverging map's pale midpoint washes lines out).  Sampled deep→shallow with
#: washed-out colours skipped by luminance (see
#: :func:`oceanarray.plotters.helpers.ordered_line_colors`).  Directions are
#: chosen so the deepest instrument gets the darkest/most-saturated colour:
#: temperature blue(cold/deep)→red(warm/shallow); pressure dark→lighter blue
#: (bathymetry convention, deep = dark); salinity starts at the blue end.
LINE_CMAPS_BY_VARIABLE: dict[str, str] = {
    "temperature": "RdBu_r",
    "pressure": "Blues_r",
    "salinity": "YlGnBu_r",
    # Velocity lines are ordered by depth, so shade them like pressure (dark =
    # deep) — the field map's red/blue diverging scale means north/south, which is
    # meaningless for a per-instrument line ordering.
    "east_velocity": "Blues_r",
    "north_velocity": "Blues_r",
    "up_velocity": "Blues_r",
}

#: One colourblind-safe colour per variable, for **single-instrument** panels
#: where each variable is drawn as one line (not the multi-instrument stack, which
#: uses :data:`LINE_CMAPS_BY_VARIABLE`).  Physics use the Okabe-Ito palette (Wong,
#: Nature Methods 8:441, 2011); biogeochemistry uses the Paul Tol palette.  Look up
#: with :func:`var_color` (single fallback, no scattered literals).  Mirrors
#: ctdcast's ``VAR_COLORS``.
VAR_COLORS: dict[str, str] = {
    # Physics — Okabe-Ito
    "temperature": "#56B4E9",  # sky blue
    "conservative_temperature": "#56B4E9",
    "salinity": "#E69F00",  # orange
    "absolute_salinity": "#E69F00",
    "conductivity": "#44AA99",  # teal (Paul Tol) — readable stand-in
    "potential_density": "#009E73",  # bluish green
    "pressure": "#000000",  # black
    "depth": "#000000",
    "n2": "#000000",
    "east_velocity": "#D55E00",  # vermillion (U)
    "u": "#D55E00",
    "north_velocity": "#0072B2",  # blue (V)
    "v": "#0072B2",
    "up_velocity": "#CC79A7",  # reddish purple (W)
    "w": "#CC79A7",
    "speed": "#D55E00",
    # Biogeochemistry — Paul Tol
    "dissolved_oxygen": "#332288",  # indigo
    "dissolved_oxygen_2": "#332288",
    "dissolved_oxygen_ml_l": "#332288",
    "dissolved_oxygen_ml_l_2": "#332288",
    "oxygen_saturation_pct": "#332288",
    "turbidity": "#661100",  # dark red
}

#: Colour for a variable with no :data:`VAR_COLORS` entry — one canonical fallback
#: so callers don't scatter their own literal defaults (which drift).
VAR_COLOR_DEFAULT: str = "#000000"


def var_color(var: str) -> str:
    """Return the line/marker colour for *var* from :data:`VAR_COLORS`.

    Falls back to :data:`VAR_COLOR_DEFAULT` for an unregistered variable, so every
    caller shares one default rather than hard-coding its own.

    Parameters
    ----------
    var : str
        Variable name (key in :data:`VARIABLES` / :data:`VAR_COLORS`).

    Returns
    -------
    str
        A hex colour string.

    """
    return VAR_COLORS.get(var, VAR_COLOR_DEFAULT)


def vlabel(var: str, prefix: str = "") -> str:
    """Return a matplotlib axis label for *var* from the :data:`VARIABLES` registry.

    Format is ``"{prefix}Label (units)"``, the units coming from :func:`vunit` (the
    registry's canonical CF units formatted through the display pipeline), or
    ``"{prefix}Label (1)"`` for a dimensionless quantity without a scale label — ``1``
    is the CF / UDUNITS unit string for dimensionless, so the label matches the stored
    metadata rather than leaving the reader to wonder whether a unit was forgotten.  The
    ``prefix`` is prepended to the label component only, not the units, so
    ``vlabel("temperature", prefix="Δ")`` produces ``"ΔTemperature (°C)"``.

    Parameters
    ----------
    var : str
        Variable name (key in :data:`VARIABLES`).
    prefix : str, optional
        Text prepended to the label component — e.g. ``"Gridded "`` or ``"Δ"``.

    Returns
    -------
    str
        Ready-to-use axis label.  Falls back to ``"{prefix}{var}"`` when *var*
        is not in the registry so callers always get something useful.

    """
    entry = VARIABLES.get(var)
    if entry is None:
        # Unknown variable: return the bare name.  Do NOT append "( )" — that
        # would falsely assert the quantity is dimensionless when we simply have
        # no registry entry for it.
        return f"{prefix}{var}"
    lbl = f"{prefix}{entry.get('label', var)}"
    u = vunit(var)
    return f"{lbl} ({u})" if u else f"{lbl} (1)"


def vunit(var: str) -> str:
    """Return the display units for *var*, formatted through the units pipeline.

    The registry's canonical CF ``units`` for *var* is formatted by
    :func:`oceanarray.config.unit_tables.unit_label` (oceanarray's view of the family
    tables), so a colorbar/axis unit is produced by the same rule as every other label
    — ``degree_Celsius`` → ``°C``, ``dbar`` → ``dbar``, ``1`` with the salinity standard
    name → ``PSU``.  Reading from the registry (not from a dataset) means the storage
    pass is the single guarantee of unit correctness; a non-canonical stored unit is
    refused on write, not silently relabelled here.  Returns an empty string for a
    dimensionless-without-label or unknown variable.

    Parameters
    ----------
    var : str
        Variable name (key in :data:`VARIABLES`).

    Returns
    -------
    str
        The display unit string, or ``""`` when none is registered.

    """
    entry = VARIABLES.get(var)
    if entry is None:
        return ""
    cf = entry.get("units", "")
    return unit_label(cf, entry.get("standard_name")) if cf else ""


def __getattr__(name: str) -> dict[str, dict[str, float]]:
    """Serve the deprecated tables ``UNIT_ALIASES`` and ``VARIABLE_UNIT_ALIASES``.

    Both moved to the shared units tables
    (:data:`oceanarray.config.unit_tables.TABLES`) and are removed in 0.6.0.  Until then
    each name warns and returns a copy built from those tables: ``UNIT_ALIASES`` as
    ``{canonical: {spelling: factor}}``, and ``VARIABLE_UNIT_ALIASES`` as the scoped
    spellings of each registry variable, keyed by variable name.

    Parameters
    ----------
    name : str
        Attribute looked up on this module.

    Returns
    -------
    dict of str to dict of str to float
        The table, rebuilt from :data:`oceanarray.config.unit_tables.TABLES`.

    Raises
    ------
    AttributeError
        For any other name.
    """
    if name not in ("UNIT_ALIASES", "VARIABLE_UNIT_ALIASES"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import warnings

    from oceanarray.config.unit_tables import TABLES

    warnings.warn(
        f"parameters.{name} is deprecated; use oceanarray.config.unit_tables.TABLES "
        f"(removed in 0.6.0).",
        DeprecationWarning,
        stacklevel=2,
    )
    if name == "UNIT_ALIASES":
        return {canonical: dict(group) for canonical, group in TABLES.aliases.items()}
    return {
        var: dict(TABLES.scoped[entry["standard_name"]])
        for var, entry in VARIABLES.items()
        if entry.get("standard_name") in TABLES.scoped
    }
