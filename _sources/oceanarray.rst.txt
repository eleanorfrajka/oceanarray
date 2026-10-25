:mod:`oceanarray` API reference
================================

Load and process moored oceanographic time series data from raw instrument
format to array-integrated products.

Public entry point
-------------------

process
^^^^^^^
Top-level ``process()`` function, ``STAGES`` registry, and ``resolve_stage()``
dispatcher.  These are the primary public API for driving the pipeline from
Python code.

.. automodule:: oceanarray.pipeline
   :members:
   :undoc-members:

mooring
^^^^^^^
``oceanarray.mooring`` is the single seam for reading one mooring's description
(a ``*.mooring.yaml`` file or the dict parsed from one): ``load`` (validates,
raises ``MooringError`` on an invalid config), ``check`` (never raises), and
``template`` / ``save`` for the ``init`` stub. Every stage and report receives the
loaded dict rather than re-reading the file. Interpreting that dict — which
entries to process, the position — is the caller's job, through
``utilities.instrument_entries`` and ``utilities.parse_latlon_with_source``.

.. automodule:: oceanarray.mooring
   :members:
   :undoc-members:

I/O and shared tools
---------------------

readers
^^^^^^^
``oceanarray.readers.read(path)`` reads one raw instrument file to a canonical
dataset: it sniffs the file content (never the suffix) to pick the format, finds
the Nortek ``.hdr`` beside a data file, and dispatches to the per-format module
(``sbe``, ``nortek``, ``rodb``, ``oceansites``, ``netcdf``). An oceanarray netCDF
is returned unchanged; an OceanSITES netCDF is read to oceanarray names. A known
``file_type=`` overrides the sniffer.

.. automodule:: oceanarray.readers
   :members:
   :undoc-members:

.. automodule:: oceanarray.readers.oceansites
   :members:
   :undoc-members:

.. automodule:: oceanarray.readers.rodb
   :members:
   :undoc-members:

netcdf writer
^^^^^^^^^^^^^
``oceanarray.writers.write(ds, path)`` is the single write seam used by every
processing stage: zlib-compressed CF-NetCDF, cleaned attributes (``None`` dropped
with a warning), a pinned float64 time encoding, and an atomic write. A bare
``ds.to_netcdf()`` skips all of this — no compression, no attribute cleaning, no
pinned time — so use ``write`` for pipeline output.

.. automodule:: oceanarray.writers.netcdf
   :members:
   :undoc-members:

rapid interpolation
^^^^^^^^^^^^^^^^^^^
Physics-informed vertical interpolation (RAPID array scheme).

.. automodule:: oceanarray.tools.rapid_interp
   :members:
   :undoc-members:

utilities
^^^^^^^^^
General utilities for file management, logging, and parsing ASCII metadata.

.. automodule:: oceanarray.utilities
   :members:
   :undoc-members:

paths
^^^^^
Path resolution helpers for raw and processed directory trees.

.. automodule:: oceanarray.paths
   :members:
   :undoc-members:

Instrument processing
----------------------

stage 1 — standardisation
^^^^^^^^^^^^^^^^^^^^^^^^^^
Convert raw instrument files (SeaBird, RBR, Nortek, RDI) to CF-NetCDF.
Faithful to raw data; no QC.

.. automodule:: oceanarray.pipeline.stage1
   :members:
   :undoc-members:

raw files
^^^^^^^^^
Find and read one instrument's raw file as stage 1 does: the recovery-file naming
convention for a missing ``filename``, the Nortek header, and the calibration metadata
only the raw header holds.

.. automodule:: oceanarray.pipeline.raw_files
   :members:

stage 2 — trimming and clock correction
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Trim to the deployment window; apply linear clock-offset/drift correction.

.. automodule:: oceanarray.pipeline.stage2
   :members:
   :undoc-members:

stage 3 — QC, rotation, and derived variables
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Apply QC flags, rotate ADCP velocities to ENU, apply magnetic declination
correction, and compute derived quantities (salinity, density, speed/direction).

.. automodule:: oceanarray.pipeline.stage3
   :members:
   :undoc-members:

pressure
^^^^^^^^
Pressure interpolation helpers (HAB computation, gap-filling).

.. automodule:: oceanarray.pipeline.pressure
   :members:
   :undoc-members:

qc
^^
QARTOD quality-control tests.

.. automodule:: oceanarray.pipeline.qc
   :members:
   :undoc-members:

coordinate
^^^^^^^^^^
Coordinate system transformations (BEAM → XYZ → ENU, magnetic declination).

.. automodule:: oceanarray.pipeline.coordinate
   :members:
   :undoc-members:

caldip
^^^^^^
Calibration-dip correction contract and interface, applied at the front of Stage 3.
Currently a stub: the interface functions raise ``NotImplementedError`` pending the
implementation (see :doc:`methods/calibration`).

.. automodule:: oceanarray.pipeline.caldip
   :members:
   :undoc-members:

stamp
^^^^^
Mark stage files written before the ``processing_stage`` marker existed
(``oceanarray stamp FILE... --stage N``).

.. automodule:: oceanarray.pipeline.stamp
   :members:

Mooring processing
-------------------

stack
^^^^^
Interpolate multiple instruments onto a common time grid and stack into a
single mooring dataset (``oceanarray process MOORING --stage stack``).

.. automodule:: oceanarray.pipeline.stack
   :members:
   :undoc-members:

grid
^^^^
Interpolate stacked mooring data onto a regular pressure grid
(``oceanarray process MOORING --stage grid``).

.. automodule:: oceanarray.pipeline.grid
   :members:
   :undoc-members:

mooring helpers
^^^^^^^^^^^^^^^
Shared helpers for position parsing, HAB computation, and instrument metadata.

.. automodule:: oceanarray.pipeline.helpers
   :members:
   :undoc-members:

Configuration and validation
-----------------------------

parameters
^^^^^^^^^^
Global processing parameters (QC thresholds, grid defaults, file paths,
variable registry).

.. automodule:: oceanarray.config.parameters
   :members:
   :undoc-members:

validation
^^^^^^^^^^
Validate mooring YAML configuration files and check instrument type names.

.. automodule:: oceanarray.config.validation
   :members:
   :undoc-members:

Analysis
---------

science
^^^^^^^
QC routines: ``flag_salinity_outliers``, ``flag_temporal_spikes``,
``flag_vertical_inconsistencies``, ``run_qc``.

.. automodule:: oceanarray.analysis.science
   :members:
   :undoc-members:

hydrographic
^^^^^^^^^^^^
Salinity calculation, isopycnal tracking, cold-regime detection, and dataset
differencing.

.. automodule:: oceanarray.analysis.hydrographic
   :members:
   :undoc-members:

temporal
^^^^^^^^
Lag correlation, histogram-based split value, T/S downsampling, and Tukey
time-series filtering.

.. automodule:: oceanarray.analysis.temporal
   :members:
   :undoc-members:

spectral
^^^^^^^^
Gonella rotary spectra, Welch PSD, and continuous wavelet transforms.

.. automodule:: oceanarray.analysis.spectral
   :members:
   :undoc-members:

vector
^^^^^^
XYZ→ENU rotation and progressive-vector trajectory computation.

.. automodule:: oceanarray.analysis.vector
   :members:
   :undoc-members:

Plotters
---------

primitives
^^^^^^^^^^
Tier-1 data-agnostic plot primitives (array-in / Figure-out).

.. automodule:: oceanarray.plotters.primitives
   :members:
   :undoc-members:

helpers
^^^^^^^
Shared colormap, normalisation, and style helpers used across Tier-2 modules.

.. automodule:: oceanarray.plotters.helpers
   :members:
   :undoc-members:

current
^^^^^^^
ADCP velocity plots: hodographs, current roses, stick plots, depth-time panels.

.. automodule:: oceanarray.plotters.current
   :members:
   :undoc-members:

timeseries
^^^^^^^^^^
Grid and mooring time-series figures: T/S/density pcolormesh, velocity panels,
N² sections.

.. automodule:: oceanarray.plotters.timeseries
   :members:
   :undoc-members:

diagnostic
^^^^^^^^^^
Diagnostic plots: boxplots, clock-alignment checks, deployment-boundary windows.

.. automodule:: oceanarray.plotters.diagnostic
   :members:
   :undoc-members:

hydrography
^^^^^^^^^^^
T-S diagrams and isopycnal overlay figures.

.. automodule:: oceanarray.plotters.hydrography
   :members:
   :undoc-members:

spectrum
^^^^^^^^
Power spectra and rotary spectrum figures.

.. automodule:: oceanarray.plotters.spectrum
   :members:
   :undoc-members:

ts
^^
T-S scatter and density-coloured scatter figures.

.. automodule:: oceanarray.plotters.ts
   :members:
   :undoc-members:

animation
^^^^^^^^^
Animated GIF output via ``matplotlib.animation``.

.. automodule:: oceanarray.plotters.animation
   :members:
   :undoc-members:
