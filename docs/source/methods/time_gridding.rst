Stack: one time axis
====================

The ``stack`` stage reads all instruments for one mooring deployment from their
best available stage file, places them on a common time axis, and writes a single
multi-instrument NetCDF file.

Command
-------

.. code-block:: bash

   oceanarray run {mooring} --proc-dir /data/proc --stage 3 [--dt-seconds 600]

Python API
----------

.. code-block:: python

   import oceanarray as oa

   # stage 3 (kept where it exists), then the stack and grid
   oa.run(yaml_path, stages=3, dt_seconds=600)

   # or on datasets: the stack itself, no files written
   cfg = oa.mooring.load(yaml_path)
   stacked = oa.stack(oa.read_mooring(yaml_path, stage=3), cfg, dt_seconds=600)

``dt_seconds`` comes from the call (or ``--dt-seconds``), else the YAML's
``stack: dt_seconds``, else ``parameters.STACK_DT_SECONDS`` (3600) — the same order
for every entry point.

Purpose
-------

Stack reads all instruments configured in the mooring YAML that have a ``hab`` (height
above bottom) value and at least one stage file (stage3 or stage2). It places each
instrument record on a common time axis and writes the result as a single
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

The common time axis runs through the deployment window at ``dt_seconds`` intervals
(default 3600 s). Its first step is ``deployment_time`` rounded up to a whole multiple of
``dt_seconds`` counted from 1970-01-01 UTC (on the hour at 3600 s), so deployments of one
site share their steps; the last step is the last multiple at or before
``recovery_time``.

Nearest sample
^^^^^^^^^^^^^^

Every instrument is placed the same way: each step takes the instrument's nearest sample
within a ±dt/2 window (half-open: a sample exactly half a step from two steps goes to the
later one; on equal distance the earlier sample wins), with that sample's QC flags. Nothing
is interpolated or averaged in time. A step with no sample in its window is NaN.

An instrument sampling slower than ``dt_seconds`` therefore fills only the steps nearest
its samples; the steps between are empty, and the stack prints a warning naming it. The
grid leaves those steps empty at that level too (gap filling up to the instrument's own
interval is not done yet). For a gap-free grid, set ``dt_seconds`` no finer than the
slowest instrument.

Each instrument's sampling interval is the median step of its time axis.

Ordering
^^^^^^^^

Instruments are sorted by ascending HAB (height above bottom), so the deepest instrument
is at index 0 of the ``N_LEVELS`` dimension.

Variables stacked
-----------------

Temperature, salinity, conductivity, pressure, the velocities and their direction
and speed, heading, pitch and roll, turbidity and oxygen, with their QC flags, when
an instrument has them (``stack.STACK_VARS``); a level without a variable is NaN
there. Every value is kept, flagged or not, with two flag sets from the same
sample: ours (``<var>_qc``) and the originator's translated to our table
(``<var>_qc_originator_mapped``). A level with a value but no flag variable gets 0
(not QC'd); a step with no value gets 9 (missing), so a present value is never 9.

``stack: mask_flags`` (default 4; ``--mask-flags``) decides what the stack and Stack
cat pages hide and the grid leaves out: a value is hidden when either of its flags is
in the rule. It is recorded as ``stack_mask_flags``. The velocity components are one
measurement: a flag in the rule on any of them (ours or the originator's, or
``velocity_flag``) hides all of them at that step, so a page never shows a speed or
direction built from one good and one bad component. The potential density and
``tilt_from_pressure`` use only values the rule does not hide.

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

``(time, N_LEVELS)`` following the OceanSITES convention. ``N_LEVELS`` is the instrument
index (deepest first); ``time`` is the common time axis at ``dt_seconds`` resolution.
The ``history`` attribute records the method in one line.

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
   * - ``density_reference``
     - Optional; reference pressure in dbar for potential density (default 0)

The stack's report page is described in :doc:`../reports`.

See also
--------

- :doc:`vertical_gridding` — interpolate the stacked file onto a regular pressure grid
- :doc:`../oceanarray` — full command reference
