.. _cli_reference:

===================
CLI Reference
===================

The typical workflow is: ``oceanarray init`` a mooring description and fill it
in, ``oceanarray validate`` it, run ``oceanarray process`` stage by stage and look
at the stage files with ``oceanarray report``, then ``oceanarray run`` for the
whole pipeline (stages 1–3, stack, grid, report) in one command, and
``oceanarray export`` to write OceanSITES files.  ``python -m oceanarray`` works
as well as ``oceanarray``.

``validate``, ``process``, ``run``, ``report``, ``export``, ``plot`` and
``animate`` take the mooring as the first positional argument, in either of two
forms:

- the path to its ``*.mooring.yaml``, or to the folder holding it — the folder can
  have any name, and every file is named from the description's ``name:``;
- with ``--proc-dir``, the mooring's name: the folder is ``{proc_dir}/{name}``.

.. code-block:: bash

   oceanarray run /data/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml --raw-dir /data/raw
   oceanarray run dsG3_1_2026 --proc-dir /data/proc --raw-dir /data/raw   # the same

``--only SERIAL ...`` restricts a command to those instruments (``--serial`` is the
old spelling, accepted until 0.7.0).  ``-v`` (before or after the command name)
also prints the processing stages' debug messages.

Without ``--force``, the stages keep a file that already exists (stage 1 also
rewrites one older than its raw file) and the report pages keep one newer than its
inputs; add ``--force`` to rewrite.  ``oceanarray run``
rebuilds the stack and grid every time.

**Exit status**: 0 when the command did its work (``process`` and ``run`` also
when instruments were left out, unless ``--strict``); 1 when it failed, or
``validate`` found an ERROR; 2 for a usage error (an unknown flag, or a missing one
such as ``--raw-dir`` for stage 1).

The flag lists below are generated from the argument parser
(:func:`oceanarray.cli.build_parser`), so they cannot drift from the code.

----

``oceanarray validate``
------------------------

Check one or more mooring descriptions for missing fields, unrecognised values and
structural errors, including the QC override blocks and the OceanSITES codes (see
:doc:`yaml_configuration`).  Run it before any processing to catch typos in
``file_type``, a missing ``hab``, a malformed serial number or a misspelt QC
threshold.

.. argparse::
   :module: oceanarray.cli
   :func: build_parser
   :prog: oceanarray
   :path: validate
   :nodescription:
   :noepilog:

**Example**

.. code-block:: bash

   oceanarray validate /data/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml

Prints every ERROR and WARNING.  Exits 1 if any description has an ERROR, or with
``--strict`` a WARNING; 0 otherwise.

----

``oceanarray stamp``
---------------------

Mark stage files written before the ``processing_stage`` marker existed.  Each file
is read, the marker for the stage its filename states is added (with a ``history``
line saying it was declared, not earned by processing), and the file is rewritten
with its data unchanged.  A file whose name or existing marker says another stage is
refused; a file already carrying the marker is left alone.

.. argparse::
   :module: oceanarray.cli
   :func: build_parser
   :prog: oceanarray
   :path: stamp
   :nodescription:
   :noepilog:

**Example**

.. code-block:: bash

   oceanarray stamp /data/proc/dsG3_1_2026/*/dsG3_1_2026_*_stage3.nc --declare 3

Exits with a non-zero status if any file was refused.

----

``oceanarray init``
-------------------

Create a skeleton mooring YAML file with commented template fields for all
mandatory metadata and one example entry per instrument type.  Edit the file
to fill in real values and delete unused instrument blocks.  Typically run
once, before ``oceanarray validate``.

.. argparse::
   :module: oceanarray.cli
   :func: build_parser
   :prog: oceanarray
   :path: init
   :nodescription:
   :noepilog:

**Example**

.. code-block:: bash

   oceanarray init dsG3_1_2026 --proc-dir /data/proc

Creates ``/data/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml`` (the directory
is created if it does not exist).

----

``oceanarray process``
-----------------------

Run one or more per-instrument processing stages.  Stage 2 requires that
stage 1 has already run; stage 3 requires stage 2.

The recommended sequence for a new mooring is:

1. ``--stage 1``: verify that all raw files are reachable.
2. ``--stage 2``: check that trimming looks right; adjust
   ``deployment_time`` / ``recovery_time`` in the YAML if needed.
3. ``--stage 3``: apply QC and velocity rotation once stages 1 and 2 are
   confirmed.

.. argparse::
   :module: oceanarray.cli
   :func: build_parser
   :prog: oceanarray
   :path: process
   :nodescription:
   :noepilog:

**Stage descriptions**

- **Stage 1**: reads raw instrument files via seasenselib and writes
  ``{mooring}_{serial}_stage1.nc``.  Data are stored as-is — no QC, no
  trimming.
- **Stage 2**: trims to ``deployment_time`` / ``recovery_time`` from the
  YAML and applies clock drift correction.  Writes
  ``{mooring}_{serial}_stage2.nc``.
- **Stage 3**: runs the QC tests (gross range, spike, flat line, tilt; see
  :doc:`methods/auto_qc`), derives salinity,
  interpolates pressure for instruments without a pressure port, rotates
  Aquadopp velocities to earth coordinates, and applies magnetic declination
  correction.  Writes ``{mooring}_{serial}_stage3.nc``.

``oceanarray run`` then builds the two mooring-level products:

- **stack**: interpolates all instruments onto a common time axis and writes
  ``{mooring}_stack.nc``.  Instruments sampling faster than ``--dt-seconds`` are
  subsampled by nearest-neighbour; slower instruments are interpolated linearly.
  The 60-second default suits most mooring data; for high-frequency analysis
  (e.g. internal waves from fast thermistors) work from the ``_stage3.nc`` files
  or build a finer stack with ``--dt-seconds``.
- **grid**: interpolates the stack onto a regular pressure grid and writes
  ``{mooring}_grid.nc``.  Values outside the instrument depth range at each time
  step are set to NaN.  QC flags from stage 3 are not consulted — data flagged
  suspect or bad are treated the same as good unless already NaN.

**Output created**

Per-instrument: ``{mooring}_{serial}_stage{N}.nc`` in ``{proc_dir}/{mooring}/{instrument}/``.
Mooring-level: ``{mooring}_stack.nc`` and ``{mooring}_grid.nc`` in ``{proc_dir}/{mooring}/``.

**Examples**

Run stage 1 for all instruments:

.. code-block:: bash

   oceanarray process dsG3_1_2026 --raw-dir /data/raw --proc-dir /data/proc --stage 1

Rerun stage 2 for a single instrument after updating the YAML:

.. code-block:: bash

   oceanarray process dsG3_1_2026 --raw-dir /data/raw --proc-dir /data/proc --stage 2 --only 26261 --force

See what each stage would write, without writing anything:

.. code-block:: bash

   oceanarray process /data/proc/dsG3_1_2026 --dry-run

The stack and grid are built by ``oceanarray run`` (``process --stage stack grid``
still works, with a note, until 0.7.0):

.. code-block:: bash

   oceanarray run dsG3_1_2026 --proc-dir /data/proc --stage 3 --dp 10 --pmin 100 --pmax 2000

----

``oceanarray report``
----------------------

Generate HTML reports for a mooring.  Without any report-type flags,
generates only the mooring summary page.  See :doc:`reports` for a
description of what each report page contains.

.. argparse::
   :module: oceanarray.cli
   :func: build_parser
   :prog: oceanarray
   :path: report
   :nodescription:
   :noepilog:

**Output created**

``{mooring}_report.html`` (always), plus optional
``{mooring}_stack_report.html``, ``{mooring}_grid_report.html``, and
``report/instrument/{mooring}_{serial}_report.html``.

**Examples**

Summary and per-instrument pages:

.. code-block:: bash

   oceanarray report dsG3_1_2026 --raw-dir /data/raw --proc-dir /data/proc --instruments

All report types (equivalent to ``--stack --grid --instruments --pdf``):

.. code-block:: bash

   oceanarray report dsG3_1_2026 --raw-dir /data/raw --proc-dir /data/proc --all

Array-level index across all moorings:

.. code-block:: bash

   oceanarray report cruise.array.yaml --array --report-dir /data/reports

Cruise recovery table:

.. code-block:: bash

   oceanarray report dsG3_1_2026 --raw-dir /data/raw --proc-dir /data/proc --cruise-table

----

``oceanarray run``
------------------

Run the complete processing pipeline in one command: stages 1, 2, and 3,
followed by stack, grid, and all report pages.  Continues past individual
instrument failures — check the processing logs in
``{proc_dir}/{mooring}/processing_logs/`` for details.

Use ``oceanarray run`` only after confirming that stage 1 can read all raw
files and that the ``deployment_time`` / ``recovery_time`` values in the
YAML produce the correct trim (verified with ``oceanarray process --stage 2``
and a quick report inspection).

.. argparse::
   :module: oceanarray.cli
   :func: build_parser
   :prog: oceanarray
   :path: run
   :nodescription:
   :noepilog:

**Output created**

All stage NC files, ``{mooring}_stack.nc``, ``{mooring}_grid.nc``,
mooring summary report, stack report, grid report, and per-instrument
reports.

**Example**

.. code-block:: bash

   oceanarray run dsG3_1_2026 --raw-dir /data/raw --proc-dir /data/proc --force

----

``oceanarray export``
---------------------

Write a mooring's stage-3 files and grid as OceanSITES 1.4 files (see
:doc:`methods/oceansites_export`): one deployment file per instrument and one
gridded file.  The mooring description needs ``platform_code``; ``--array`` adds
the array's ``site_code`` and ``project``.  An instrument that cannot be exported
is listed and left out; the command exits 1 only when nothing was written.

.. argparse::
   :module: oceanarray.cli
   :func: build_parser
   :prog: oceanarray
   :path: export
   :nodescription:
   :noepilog:

**Output created**

``OS_<platform>_<YYYYMM>_<mode>_<type>-<serial>-<depth>m.nc`` per instrument and
``OS_<platform>-<YYYYMM>_<start>-<end>_GRD_<interval>-DP<step>.nc`` for the grid, in
``{proc_dir}/{mooring}/oceansites/`` (or ``-o DIR``).

**Example**

.. code-block:: bash

   oceanarray export dsG3_1_2026 --proc-dir /data/proc --array /data/proc/MIXSED2026.array.yaml

----

``oceanarray list``
--------------------

Print a reference table of allowed ``instrument:`` and ``file_type:`` values
for mooring YAML configuration.  Use this to look up the correct reader name
for a given instrument type, or to check which ``instrument:`` values are
recognised.

Does not require a mooring name, ``--raw-dir``, or ``--proc-dir``.

.. argparse::
   :module: oceanarray.cli
   :func: build_parser
   :prog: oceanarray
   :path: list
   :nodescription:
   :noepilog:

**Example**

.. code-block:: bash

   oceanarray list

----

Preliminary visualisation commands
------------------------------------

The following commands produce standalone plots and animations.  They are
available for quick inspection but are considered preliminary — the output
style, variable names, and flag set may change in a future release.

``oceanarray plot``
~~~~~~~~~~~~~~~~~~~~

Show an interactive overview plot of processed data, or save it to a file.

.. argparse::
   :module: oceanarray.cli
   :func: build_parser
   :prog: oceanarray
   :path: plot
   :nodescription:
   :noepilog:

**Example**

.. code-block:: bash

   oceanarray plot dsG3_1_2026 --proc-dir /data/proc --var-y pressure --var-color temperature --output overview.png

----

``oceanarray animate``
~~~~~~~~~~~~~~~~~~~~~~~

Generate an animated hodograph (tip of the velocity vector tracing a path
over time) for Aquadopp or ADCP instruments.  Requires ``east_velocity``
and ``north_velocity`` in the stage 3 output.  The ``Pillow`` library must
be installed for GIF output.

.. argparse::
   :module: oceanarray.cli
   :func: build_parser
   :prog: oceanarray
   :path: animate
   :nodescription:
   :noepilog:

**Example**

.. code-block:: bash

   oceanarray animate dsG3_1_2026 --proc-dir /data/proc --only 400115 --output dsG3_400115_hodograph.gif

----

Deprecated flags
----------------

Renamed in 0.6.0
~~~~~~~~~~~~~~~~

The old spellings still work until 0.7.0, with a warning naming the new one.

.. list-table::
   :header-rows: 1

   * - Command
     - Old
     - New
   * - ``process``, ``run``, ``report``, ``animate``
     - ``--serial``
     - ``--only``
   * - ``run``, ``process``
     - ``--dt``
     - ``--dt-seconds``
   * - ``report``
     - ``-i``, ``-s``, ``-g``, ``-A``
     - ``--instruments``, ``--stack``, ``--grid``, ``--all``
   * - ``process``
     - ``--report``
     - ``--summary``
   * - ``stamp``
     - ``--stage``
     - ``--declare``
   * - ``export``
     - ``--overwrite``
     - ``--force``
   * - ``plot``
     - ``--downsample``
     - ``--downsample-seconds``
   * - ``plot``
     - ``--var_y``, ``--var_color``
     - ``--var-y``, ``--var-color``

``--basedir DIR`` (removed)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``--basedir`` flag has been **removed**.  Passing it prints a migration
message naming the replacement and exits without processing.  Use
``--raw-dir`` and ``--proc-dir`` with the mooring-first layout instead.

See :doc:`migration` for step-by-step migration instructions.
