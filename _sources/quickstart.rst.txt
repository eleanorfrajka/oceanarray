.. _quickstart:

========================
Quickstart Guide
========================

This guide walks through processing a single mooring from raw instrument files
to a vertically gridded NetCDF and an HTML report.  The steps are intended
for oceanographers who are comfortable with a command line but do not need to
write Python code.

.. note::

   Output variable names and file format are not yet stable — see the
   :doc:`roadmap` before building downstream scripts that depend on specific
   variable names or file structure.

----

Prerequisites
-------------

**Python 3.11 or later** is required.  Install ``oceanarray`` from PyPI, ideally
into an isolated environment.

**Option A — conda**

.. code-block:: bash

   conda create -n oceanarray python=3.11
   conda activate oceanarray
   pip install oceanarray

**Option B — venv**

.. code-block:: bash

   python -m venv venv
   source venv/bin/activate        # macOS / Linux
   # venv\Scripts\activate         # Windows
   pip install oceanarray

For development, install from source instead:

.. code-block:: bash

   git clone https://github.com/ocean-uhh/oceanarray
   cd oceanarray
   pip install -e .

``oceanarray`` reads raw instrument files via the ``seasenselib`` library,
which is on PyPI and installed automatically with ``oceanarray``.

.. note::

   Without ``seasenselib``, stage 1 processing (raw file ingestion) cannot
   run.  Stages 2–3, stack, grid, and report generation work on existing
   NetCDF files without it.

RDI WorkHorse ADCP files (``file_type: rdi-raw``) need no extra install:
``seasenselib`` reads them via ``mhkit[dolfyn]``, pulled in automatically with
``oceanarray``.

----

Organise your files
-------------------

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

See :doc:`directory_structure` for a full description of the layout and how
file names are constructed.

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
just one instrument, add ``--serial 26261`` to reprocess only that
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
failures — check the processing logs if something looks wrong.

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

Where to go next
----------------

- :doc:`yaml_configuration` — full reference for all YAML fields, QC
  thresholds, clock correction, and inline instruments.
- :doc:`cli_reference` — every command-line option for all subcommands.
- :doc:`directory_structure` — detailed description of the file layout
  and naming conventions.
- :doc:`processing_framework` — description of what each processing stage
  does to the data.
