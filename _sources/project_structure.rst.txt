Project structure
=================

Where each part of oceanarray lives. The :doc:`developer` says which layer owns
what and how code in each is written.

Package layout
--------------

.. code-block:: text

   oceanarray/
   ├── oceanarray/                    # the Python package
   │   ├── __init__.py                # public API: the verbs (read, read_mooring, stage1–stage3,
   │   │                              #   process, stack, grid, run, write, write_mooring, report, export)
   │   ├── _version.py                # package version (written by setuptools-scm)
   │   ├── _deprecated.py             # the one table of deprecated names and their replacements
   │   ├── mooring.py                 # the *.mooring.yaml: load / check / template / save
   │   ├── instruments.py             # instrument entries of a description, inline-serial parsing
   │   ├── location.py                # latitude/longitude parsing and formatting
   │   ├── paths.py                   # file names, suffixes and folder layout
   │   ├── catalog.py                 # the sensor catalog: what each instrument is, by serial
   │   ├── physics.py                 # seawater and current formulas shared by pipeline and pages
   │   ├── provenance.py              # which oceanarray wrote a file (version, commit)
   │   ├── inspect.py                 # quick dataset inspection for interactive use
   │   ├── logger.py                  # logging configuration, status lines
   │   ├── exceptions.py              # package exceptions
   │   ├── cli/                       # command-line interface: one module per subcommand
   │   │
   │   ├── pipeline/                  # the verbs' bodies
   │   │   ├── stage1.py              # stage 1: raw file → CF-NetCDF (*_stage1.nc)
   │   │   ├── stage2.py              # stage 2: clock correction and deployment trim (*_stage2.nc)
   │   │   ├── stage3.py              # stage 3: pressure interpolation, QC, ENU (*_stage3.nc)
   │   │   ├── stack.py               # stack(): instruments → common time axis (*_stack.nc)
   │   │   ├── grid.py                # grid(): stack → pressure levels (*_grid.nc)
   │   │   ├── concat.py              # concat(): a site's stacks end to end (*_stackcat.nc)
   │   │   ├── export.py              # export(): stage-3 files and grid → OceanSITES 1.4 files
   │   │   ├── oceansites.py          # to_oceansites(): the conversion export uses
   │   │   ├── read_mooring.py        # read_mooring(): every instrument of a mooring, by stage
   │   │   ├── write_mooring.py       # write_mooring(): a dict of datasets → stage files
   │   │   ├── _run.py                # the standard chain for one mooring: stages 1–3, stack, grid
   │   │   ├── _stage.py              # the stage marker; append_history
   │   │   ├── raw_files.py           # finding and reading each instrument's raw file
   │   │   ├── stamp.py               # marking pre-0.6 stage files with their stage
   │   │   ├── masking.py             # which flagged values the pages and the grid hide
   │   │   ├── pressure.py            # pressure interpolation for stage 3
   │   │   ├── coordinate.py          # BEAM → XYZ → ENU transforms
   │   │   ├── units.py               # canonical units
   │   │   ├── sensors.py             # sensor records, one per instrument
   │   │   ├── attribution.py         # investigators, contributors, DOIs, licenses of a product
   │   │   ├── summary.py             # one run's warnings, collected
   │   │   ├── dataset.py             # small xarray conveniences the stages share
   │   │   ├── clock/                 # clock offsets between instruments; deployment window
   │   │   ├── calibration/           # calibration dips (caldip) at the front of stage 3
   │   │   ├── qc/                    # QARTOD tests and OceanSITES flags; ADCP QC; originator flags
   │   │   └── gridders/              # vertical gridding methods: rapid_interp, climatology, filters
   │   │
   │   ├── readers/                   # vendor file → canonical dataset, one module per format
   │   ├── writers/                   # the one netCDF writer; storage dtypes
   │   │
   │   ├── analysis/                  # physics on the products, computed once per page
   │   │   ├── derived.py             # quantities a report's figures need
   │   │   ├── hydrography.py         # salinity, isopycnals, dataset differences
   │   │   ├── site.py                # a site's gridded record over the years
   │   │   └── spectral.py            # spectra of time series
   │   │
   │   ├── plotters/                  # draw_* functions returning a matplotlib Figure
   │   │   ├── primitives.py          # data-agnostic plotting primitives, colorbar bounds
   │   │   ├── current.py             # currents and velocity
   │   │   ├── diagnostic.py          # knockdown, clock-offset and QC diagnostics
   │   │   ├── flags.py               # quality-flag strip
   │   │   ├── hydrography.py         # sections and isopycnals
   │   │   ├── timeseries.py          # time series and gridded sections
   │   │   ├── site.py                # a site's record over the years
   │   │   ├── spectrum.py            # spectral diagnostics
   │   │   ├── ts.py                  # T-S diagrams
   │   │   ├── animation.py           # animations
   │   │   ├── helpers.py             # styles, roses, labels (to be split by content)
   │   │   └── _cli_legacy.py         # old CLI plots (removed with the `oceanarray plot` redesign)
   │   │
   │   ├── reports/                   # HTML/PDF report pages
   │   │   ├── _verb.py               # the report verb: a mooring's report, an array index
   │   │   ├── _mooring.py            # mooring summary page
   │   │   ├── _instrument.py         # instrument page
   │   │   ├── _stack.py              # stack page
   │   │   ├── _grid.py               # grid page
   │   │   ├── _concat.py             # Stack cat page (a site's moorings on one time axis)
   │   │   ├── _gridcat.py            # Grid cat page (a site's gridded record)
   │   │   ├── _array.py              # array page (the year page)
   │   │   ├── _captions.py           # generated text of the stack and grid pages
   │   │   ├── _plots.py              # figure adapters for the pages (base64 PNGs)
   │   │   ├── _read.py               # reading stage files and products for the pages
   │   │   ├── _pdf.py                # HTML pages → one A4 PDF (WeasyPrint)
   │   │   ├── design.py              # oceanarray's configuration of the vendored layer
   │   │   ├── templates/             # Jinja templates
   │   │   └── _vendored/             # report layer shared with ctdcast (byte-identical)
   │   │
   │   ├── config/
   │   │   ├── parameters.py          # the variable registry and every default
   │   │   ├── unit_tables.py         # units: oceanarray's additions to the shared tables
   │   │   ├── validation.py          # mooring YAML validation
   │   │   ├── flag_meanings.yaml     # originator flag translation table
   │   │   ├── logging.yaml           # logging configuration
   │   │   └── legacy/                # RAPID/RODB configuration files
   │   │
   │   └── legacy/                    # RODB/RAPID reference code (nothing imports it; not linted)
   │
   ├── tests/
   │   ├── unit/                      # unit tests, including the golden page tests
   │   ├── integration/               # golden pipeline tests
   │   ├── fixtures/                  # trimmed real data: dune2_1_2026, M2 (egM2_concat), OSNAP cuts
   │   └── legacy/                    # tests of legacy/ (not run in CI)
   │
   ├── notebooks/                     # demo notebooks
   ├── docs/source/                   # this documentation (Sphinx)
   ├── CHANGELOG.md
   ├── CITATION.cff
   ├── CONTRIBUTING.md
   ├── pyproject.toml
   └── README.md

Processing chain
----------------

Each step writes a CF-NetCDF file. ``oceanarray.process(yaml_path, stages=…,
raw_dir=…)`` runs any of stages 1–3; ``oceanarray.run`` adds the stack and grid,
and the report with ``report=True``.

.. code-block:: text

   raw files
      │  stage 1   read, no QC, no trimming                       *_stage1.nc
      │  stage 2   clock correction, deployment window            *_stage2.nc
      │  stage 3   pressure, QC flags, ENU velocity, salinity     *_stage3.nc
      ▼
   stack           every instrument on one time axis              {mooring}_stack.nc
      ▼
   grid            the stack on pressure levels                   {mooring}_grid.nc
      ▼
   concat          a site's stacks end to end (Stack cat),        {site}_stackcat.nc
                   and gridded (Grid cat)                         {site}_gridcat.nc
      ▼
   export          OceanSITES 1.4: one file per instrument,       OS_*.nc
                   and the grid

Report pages
------------

.. list-table::
   :header-rows: 1
   :widths: 25 30 45

   * - Page
     - Module
     - File
   * - Mooring summary
     - ``reports/_mooring.py``
     - ``{mooring}_report.html``
   * - Instrument
     - ``reports/_instrument.py``
     - ``instrument/{mooring}_{serial}_report.html``
   * - Stack
     - ``reports/_stack.py``
     - ``{mooring}_stack_report.html``
   * - Grid
     - ``reports/_grid.py``
     - ``{mooring}_grid_report.html``
   * - Stack cat
     - ``reports/_concat.py``
     - ``{site}_stackcat_report.html``
   * - Grid cat
     - ``reports/_gridcat.py``
     - ``{site}_gridcat_report.html``
   * - Array
     - ``reports/_array.py``
     - ``{array}_array_report.html``

Figures are drawn by ``plotters/`` and turned into base64 PNGs by the adapters in
``reports/_plots.py``.
