Stack: Common Time Axis
=======================

The ``stack`` stage reads all instruments for one mooring deployment from their
best available stage file, resamples them onto a common time axis, and writes a single
multi-instrument NetCDF file.

Command
-------

.. code-block:: bash

   oceanarray process {mooring} --proc-dir /data/proc --stage stack [--dt 60] [--force]

Python API
----------

.. code-block:: python

   from oceanarray.pipeline.stack import MooringStacker

   MooringStacker(proc_dir=proc_dir).stack(mooring_name, dt_seconds=60, force=False)

Purpose
-------

Stack reads all instruments configured in the mooring YAML that have a ``hab`` (height
above bottom) value and at least one stage file (stage3 or stage2). It resamples each
instrument record onto a common time axis and writes the result as a single
``{mooring}_stack.nc`` file with an ``N_LEVELS`` dimension ordered deepest first.

Input files
-----------

``proc/{mooring}/{instr_type}/{mooring}_{serial}_stage3.nc`` is used if present; otherwise
``proc/{mooring}/{instr_type}/{mooring}_{serial}_stage2.nc`` is used. Instruments without any
stage2 or stage3 file are skipped with a warning. Instruments that have a stage file but no
``hab`` value in the YAML are also excluded.

Algorithm
---------

Common time axis
^^^^^^^^^^^^^^^^

The common time axis spans ``deployment_time`` to ``recovery_time`` at ``dt_seconds``
intervals (default 60 s).

Resampling
^^^^^^^^^^

- Instruments with a native sampling interval <= ``dt_seconds``: nearest-neighbour
  subsampling within a +/- dt/2 window.
- Instruments with a native sampling interval > ``dt_seconds``: linear interpolation.
  Values are not extrapolated beyond the instrument's own record.

If ``sample_interval_seconds`` is absent from the YAML, it is auto-detected from the data.

Ordering
^^^^^^^^

Instruments are sorted by ascending HAB (height above bottom), so the deepest instrument
is at index 0 of the ``N_LEVELS`` dimension.

Variables stacked
-----------------

The following variables are included when present in a stage file. Variables absent for a
given instrument are filled with NaN for that level:

- ``temperature``
- ``salinity``
- ``conductivity``
- ``pressure``
- ``east_velocity``
- ``north_velocity``
- ``up_velocity``
- ``heading``
- ``pitch``
- ``roll``

Potential density
-----------------

Potential density is computed at the stack step from the stacked temperature, salinity, and
pressure fields. The reference pressure is controlled by the ``density_reference`` key in
the mooring YAML (integer, dbar). If absent, the package default from
``parameters.DENSITY_REFERENCE`` is used (0 dbar, giving sigma-0).

Computation uses the GSW Toolbox:

1. ``gsw.SA_from_SP`` — convert practical salinity to Absolute Salinity
2. ``gsw.CT_from_t`` — convert in-situ temperature to Conservative Temperature
3. ``gsw.sigma0``, ``gsw.sigma2``, etc. — potential density anomaly relative to the
   chosen reference pressure

The output variable is named ``sigma0``, ``sigma2``, and so on. Each carries
``standard_name``, ``long_name``, and ``reference_pressure_dbar`` attributes.

To use a non-default reference pressure, add ``density_reference`` to the mooring YAML:

.. code-block:: yaml

   density_reference: 2000   # deep mooring; produces sigma2

Output
------

Dimensions
^^^^^^^^^^

``(N_LEVELS, time)`` following the OceanSITES convention. ``N_LEVELS`` is the instrument
index (deepest first); ``time`` is the common time axis at ``dt_seconds`` resolution.

Coordinates
^^^^^^^^^^^

- ``time`` — common time axis
- ``serial`` (N_LEVELS,) — instrument serial numbers
- ``hab`` (N_LEVELS,) — height above bottom in metres
- ``instrument_type`` (N_LEVELS,) — instrument type string from YAML

Scalar metadata
^^^^^^^^^^^^^^^

Per-instrument scalar variables (such as ``InstrDepth`` and ``serial_number``) carried in
the stage files are preserved as ``(N_LEVELS,)`` arrays in the stack file.

Output file
^^^^^^^^^^^

``proc/{mooring}/{mooring}_stack.nc``

Global attributes include ``mooring_name``, ``waterdepth``, ``deployment_time``,
``recovery_time``, ``dt_seconds``, ``Conventions: CF-1.13``, and ``history``.

YAML fields used
----------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Field
     - Description
   * - ``deployment_time``
     - Start of the common time axis
   * - ``recovery_time``
     - End of the common time axis
   * - ``clamp[].hab``
     - Height above bottom; required for inclusion; sets sort order
   * - ``clamp[].instrument``
     - Sets the input subdirectory
   * - ``clamp[].serial``
     - Instrument serial number
   * - ``clamp[].sample_interval_seconds``
     - Optional; auto-detected from data if absent
   * - ``density_reference``
     - Optional; reference pressure in dbar for potential density (default 0)

Stack report
------------

The command ``oceanarray report {mooring} --stack`` generates
``{mooring}_stack_report.html`` containing:

- Instrument table (type, serial, HAB, approximate depth, stage file used)
- Variable coverage table (variable name, units, percentage non-NaN)
- Pressure time series with all instruments on one plot, inverted y-axis, each instrument
  as a separate coloured line; legend groups by instrument type using colours from
  ``parameters.INSTRUMENT_COLORS``
- Temperature time series with the same colour scheme

Time series are downsampled to approximately 5000 points for display.

See also
--------

- :doc:`vertical_gridding` — interpolate the stacked file onto a regular pressure grid
- :doc:`../oceanarray` — full command reference
