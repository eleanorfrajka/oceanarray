.. _quickstart:

========================
Quickstart
========================

This page takes one mooring from its raw files to the gridded file and its report,
from the command line. Stages 1 and 2 are run one at a time the first time, to check
the files are read and the deployment times are right.

You need oceanarray installed (:doc:`installation`) and the raw files of one
mooring.

----

Put the files in place
----------------------

Arrange your raw instrument files under a **raw directory** in the
mooring-first layout:

.. code-block:: text

   /data/cruise2026/raw/
   └── dsG3_1_2026/
       ├── microcat/
       │   ├── 5367_recovery.asc
       │   └── 26261_recovery.asc
       └── aquadopp/
           ├── A400115_dsG3.aqd
           └── A400115_dsG3.hdr

Create a **processed directory** alongside it:

.. code-block:: bash

   mkdir -p /data/cruise2026/proc/dsG3_1_2026

See :doc:`files` for the folders and file names.

----

Create the YAML configuration file
------------------------------------

Create a file named ``dsG3_1_2026.mooring.yaml`` in the processed directory
and fill in the details for your mooring.  A minimal example for two
instruments is shown below.

.. code-block:: yaml

   name: dsG3_1_2026
   waterdepth: 1800
   deployment_time: "2026-04-10T12:00:00"
   recovery_time: "2027-04-15T08:30:00"

   deployment_latitude: "65 29.84 N"
   deployment_longitude: "009 30.12 W"

   clamp:
     - serial: "26261"
       instrument: microcat
       filename: 26261_recovery.asc
       file_type: sbe-ascii
       hab: 450
       computer_clock_at_recovery: "2027-04-15T08:00:00"
       instrument_clock_at_recovery: "2027-04-15T07:59:48"

     - serial: "400115"
       instrument: aquadopp
       filename: A400115_dsG3.aqd
       file_type: nortek-raw
       header_file: A400115_dsG3.hdr
       hab: 460

Place this file at::

   /data/cruise2026/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml

See :doc:`yaml_configuration` for a complete description of all available
fields.

----

Validate the YAML
-----------------

Before running any processing, check the YAML for errors:

.. code-block:: bash

   oceanarray validate /data/cruise2026/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml

Fix any errors or warnings before proceeding.  The validator does not
check whether the raw data files exist — it only validates the YAML
structure.

----

Step 1 — run stage 1 and verify raw files are reachable
---------------------------------------------------------

Run stage 1 first to confirm that ``oceanarray`` can find and read all raw
files.  Stage 1 converts raw data to CF-NetCDF without any trimming or QC:

.. code-block:: bash

   oceanarray process dsG3_1_2026 \
       --raw-dir /data/cruise2026/raw \
       --proc-dir /data/cruise2026/proc \
       --stage 1

If any instrument fails, the error message identifies the missing file or
unsupported format.  Fix the YAML ``filename`` or ``file_type`` entries
and re-run with ``--force``.

Stage 1 output files appear under ``{proc_dir}/{mooring}/{instrument}/``,
e.g. ``dsG3_1_2026_26261_stage1.nc``.

----

Step 2 — inspect the raw time series and set deployment times
--------------------------------------------------------------

Generate per-instrument report pages from the stage 1 files:

.. code-block:: bash

   oceanarray report dsG3_1_2026 \
       --raw-dir /data/cruise2026/raw \
       --proc-dir /data/cruise2026/proc \
       --instruments

Open the reports in a browser:

.. code-block:: text

   /data/cruise2026/proc/dsG3_1_2026/report/instrument/dsG3_1_2026_26261_report.html
   /data/cruise2026/proc/dsG3_1_2026/report/instrument/dsG3_1_2026_400115_report.html

Look for:

- Whether each record starts and ends where expected.  Stage 1 includes
  everything in the raw file — pre-deployment bench time, in-water data,
  and post-recovery bench time all appear.
- Any obviously faulty sensors (a flat line, garbled values, or a very
  short record).
- Whether the timestamps look plausible.

Once you have identified the correct in-water window, update
``deployment_time`` and ``recovery_time`` in the YAML.

----

Step 3 — run stage 2 and verify trimming
-----------------------------------------

Run stage 2 to apply clock corrections and trim to the deployment window:

.. code-block:: bash

   oceanarray process dsG3_1_2026 \
       --raw-dir /data/cruise2026/raw \
       --proc-dir /data/cruise2026/proc \
       --stage 2

Without ``--force``, ``oceanarray`` skips any instrument whose stage 2
output already exists.  To reprocess after updating the YAML times:

.. code-block:: bash

   oceanarray process dsG3_1_2026 \
       --raw-dir /data/cruise2026/raw \
       --proc-dir /data/cruise2026/proc \
       --stage 2 --force

Regenerate the instrument reports and check that the start and end of each
record now match the expected deployment window.  If you need to adjust
just one instrument, add ``--only 26261`` to reprocess only that
instrument.

----

Run the full pipeline
----------------------

Once stage 1 and stage 2 are working correctly and the deployment times
are confirmed, run all stages in one command:

.. code-block:: bash

   oceanarray run dsG3_1_2026 \
       --raw-dir /data/cruise2026/raw \
       --proc-dir /data/cruise2026/proc \
       --force

``oceanarray run`` executes stages 1, 2, and 3, then stacks and grids the
result, and generates all reports.  It continues past individual instrument
failures — check the processing logs if something looks wrong.  What it left
out is recorded in the stack and grid (``instruments_omitted``,
``keep_going_failures``), and every stage-3, stack and grid file is marked
provisional (``data_mode = "P"``).  Add ``--strict`` to stop at the first
failure instead.

``--from 2`` re-runs stages 2 and 3 without the raw files; ``--dry-run`` lists what
a run would write. The stack's time step (default hourly) and the grid's levels
(default 200–1000 dbar every 20) are set with ``--dt-seconds``, ``--pmin``,
``--pmax`` and ``--dp``, or in the YAML's ``stack:`` and ``grid:`` blocks
(:doc:`yaml_configuration`).

The mooring can also be given by path, with no ``--proc-dir``; the folder can
have any name:

.. code-block:: bash

   oceanarray run /data/cruise2026/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml \
       --raw-dir /data/cruise2026/raw

Processing logs are written to::

   /data/cruise2026/proc/dsG3_1_2026/processing_logs/

----

Open the report
---------------

After the run completes, open the HTML report in a browser:

.. code-block:: text

   /data/cruise2026/proc/dsG3_1_2026/report/dsG3_1_2026_report.html

Stack, grid, and per-instrument reports are cross-linked from the main
report header.  Their paths follow the same convention:

.. code-block:: text

   /data/cruise2026/proc/dsG3_1_2026/report/dsG3_1_2026_stack_report.html
   /data/cruise2026/proc/dsG3_1_2026/report/dsG3_1_2026_grid_report.html
   /data/cruise2026/proc/dsG3_1_2026/report/instrument/dsG3_1_2026_26261_report.html

See :doc:`reports` for a description of what each report contains.

----

Export to OceanSITES
--------------------

To share the products as OceanSITES 1.4 files, add ``platform_code`` (the station,
e.g. ``DSG3``) to the mooring YAML, then:

.. code-block:: bash

   oceanarray export dsG3_1_2026 --proc-dir /data/cruise2026/proc -n   # list the names
   oceanarray export dsG3_1_2026 --proc-dir /data/cruise2026/proc

Each instrument's stage-3 file and the grid are written to
``/data/cruise2026/proc/dsG3_1_2026/oceansites/``.  See :doc:`methods/oceansites_export`.

----

From Python
-----------

The same steps from Python:

.. code-block:: python

   import oceanarray as oa

   yaml_path = "/data/cruise2026/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml"
   oa.process(yaml_path, stages=1, raw_dir="/data/cruise2026/raw")  # stage 1
   oa.run(yaml_path, raw_dir="/data/cruise2026/raw", report=True)   # everything
   oa.export(yaml_path)                                              # OceanSITES files

See :doc:`oceanarray` for the full API and :doc:`migrating_0_6_0` when updating
code written for 0.5.

----

Next
----

- Several deployments of one site: ``oceanarray concat`` (:doc:`methods/concatenation`).
- Every YAML field: :doc:`yaml_configuration`; every option: :doc:`cli_reference`.
- What each stage does: :doc:`processing_framework`.
