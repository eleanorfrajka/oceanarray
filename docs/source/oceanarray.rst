:mod:`oceanarray` API reference
================================

Load and process moored oceanographic time series data from raw instrument
format to array-integrated products.

Public API
----------

``import oceanarray as oa`` gives every name below (``oceanarray.__all__``); each
loads on first use. A mooring is given as its ``*.mooring.yaml`` path, the folder
holding it, or a loaded description with ``mooring_dir=``.

.. code-block:: python

   import oceanarray as oa

   yaml_path = "proc/dune2_1_2026/dune2_1_2026.mooring.yaml"
   oa.run(yaml_path, raw_dir="raw/", report=True)   # stages 1-3, stack, grid, report

   # or step by step, on datasets
   cfg = oa.mooring.load(yaml_path)
   ds2 = oa.read_mooring(yaml_path, stage=2)
   ds3 = oa.stage3(ds2, cfg)
   stacked = oa.stack(ds3, cfg, dt_seconds=600)

The verbs
^^^^^^^^^

.. autofunction:: oceanarray.run
   :no-index:

.. autofunction:: oceanarray.process
   :no-index:

.. autofunction:: oceanarray.report
   :no-index:

On datasets
^^^^^^^^^^^

.. autofunction:: oceanarray.read
   :no-index:

.. autofunction:: oceanarray.read_mooring
   :no-index:

.. autofunction:: oceanarray.stage1
   :no-index:

.. autofunction:: oceanarray.stage2
   :no-index:

.. autofunction:: oceanarray.stage3
   :no-index:

.. autofunction:: oceanarray.stack
   :no-index:

.. autofunction:: oceanarray.grid
   :no-index:

.. autofunction:: oceanarray.write
   :no-index:

.. autofunction:: oceanarray.write_mooring
   :no-index:

Errors
^^^^^^

.. autoexception:: oceanarray.MooringError
   :no-index:

.. autoexception:: oceanarray.StageError
   :no-index:

Pipeline internals
------------------

process
^^^^^^^
The ``pipeline`` package: ``process`` and ``run``, the ``STAGES`` registry and
``resolve_stage()`` used by the pre-0.6 call form.

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
``instruments.instrument_entries`` and ``location.parse_latlon_with_source``.

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

.. automodule:: oceanarray.pipeline.gridders.rapid_interp
   :members:
   :undoc-members:

climatology
^^^^^^^^^^^
Hydrographic profiles prepared as a climatology for ``rapid_interp``.

.. automodule:: oceanarray.pipeline.gridders.climatology
   :members:
   :undoc-members:

filters
^^^^^^^
Time filters applied to a series before it is gridded.

.. automodule:: oceanarray.pipeline.gridders.filters
   :members:
   :undoc-members:

instruments
^^^^^^^^^^^
The instruments a mooring description lists: its entries and inline hardware serials.

.. automodule:: oceanarray.instruments
   :members:
   :undoc-members:

location
^^^^^^^^
Geographic position of a mooring: parse and format latitude and longitude.

.. automodule:: oceanarray.location
   :members:
   :undoc-members:

storage dtypes
^^^^^^^^^^^^^^
The smallest dtype that holds each variable when a file is written.

.. automodule:: oceanarray.writers.dtypes
   :members:
   :undoc-members:


logger
^^^^^^
Logging set-up, the stage log files and the console status lines.

.. automodule:: oceanarray.logger
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

calibration (caldip)
^^^^^^^^^^^^^^^^^^^^
Calibration-dip correction contract and interface, applied at the front of Stage 3.
Currently a stub: the interface functions raise ``NotImplementedError`` pending the
implementation (see :doc:`methods/calibration`).

.. automodule:: oceanarray.pipeline.qc.calibration
   :members:
   :undoc-members:

ADCP QC
^^^^^^^
ADCP QC steps: bins in the seabed or surface sidelobe zone, and velocity checks.

.. automodule:: oceanarray.pipeline.qc.adcp
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
single mooring dataset (``oceanarray run``, or :func:`oceanarray.stack` on datasets).

.. automodule:: oceanarray.pipeline.stack
   :members:
   :undoc-members:

grid
^^^^
Interpolate stacked mooring data onto a regular pressure grid
(``oceanarray run MOORING``, or :func:`oceanarray.grid` on a stack).

.. automodule:: oceanarray.pipeline.grid
   :members:
   :undoc-members:

OceanSITES export
^^^^^^^^^^^^^^^^^
``oceanarray.export(mooring)`` (``oceanarray export``) writes a mooring's stage-3 files
and grid as OceanSITES 1.4 files; see :doc:`methods/oceansites_export`.
``to_oceansites`` is the conversion it uses, for a dataset already in hand; it writes
nothing.

.. automodule:: oceanarray.pipeline.export
   :members:

.. automodule:: oceanarray.pipeline.oceansites
   :members:

dataset
^^^^^^^
xarray conveniences the pipeline stages share.

.. automodule:: oceanarray.pipeline.dataset
   :members:
   :undoc-members:

clock offsets
^^^^^^^^^^^^^
Clock offsets and lag correlation across a mooring's instruments.

.. automodule:: oceanarray.pipeline.clock.offsets
   :members:
   :undoc-members:

deployment window
^^^^^^^^^^^^^^^^^
When each instrument entered and left the water, from its temperature record.

.. automodule:: oceanarray.pipeline.clock.deployment
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

hydrography
^^^^^^^^^^^
Salinity calculation, isopycnal tracking, cold-regime detection, and dataset
differencing.

.. automodule:: oceanarray.analysis.hydrography
   :members:
   :undoc-members:

spectral
^^^^^^^^
Gonella rotary spectra, Welch PSD, and continuous wavelet transforms.

.. automodule:: oceanarray.analysis.spectral
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
