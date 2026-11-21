Stage 2: trim and clocks
========================

Stage 2 isolates the valid in-water period from a raw time series and applies
clock corrections.  Input is the Stage 1 output (``*_stage1.nc``); output is
``*_stage2.nc``.

This step typically needs to be run at least twice: once to produce an initial
inspection report, then again after adjusting ``deployment_time`` and
``recovery_time`` in the YAML.

Overview
--------

Raw mooring records contain data collected before deployment (deck testing, bench
soaking) and after recovery (handling, rinsing).  Stage 2 removes these segments and
corrects for any timing error in the instrument clock.

Operations applied at Stage 2:

- Clock offset and drift correction (linear)
- Trim to ``deployment_time`` / ``recovery_time`` from the YAML
- Removal of SeaBird CNV elapsed-time columns (``timeS``, ``timeQ``), which are
  redundant with the CF time coordinate
- Metadata enrichment (instrument depth, serial number, type)

Clock corrections
-----------------

Two types of timing error can be corrected.

**Clock offset** is a fixed error introduced at instrument setup, where the
instrument clock was set to the wrong time.

**Clock drift** is a slow accumulation of error over the deployment.  Any
instrument can drift; it is not a sign that the clock was set incorrectly.

Both are corrected using the same mechanism: you record the computer time and the
instrument time at the same moment (typically at recovery) and provide both to
Stage 2.  A linear correction is applied over the record.

YAML configuration:

.. code-block:: yaml

   clamp:
     - serial: "26261"
       instrument: microcat
       filename: 26261_recovery.asc
       file_type: sbe-ascii
       hab: 450
       computer_clock_at_recovery: "2027-04-15T08:00:00"
       instrument_clock_at_recovery: "2027-04-15T07:59:48"

If neither ``computer_clock_at_recovery`` nor ``instrument_clock_at_recovery`` is
set, no clock correction is applied. The original time is kept as ``time_orig``.

Instead of the two timestamps, the correction can be given directly:

The sign convention: **positive = instrument was slow (behind UTC), negative = instrument was fast (ahead of UTC)**. The value is *added* to the raw instrument time. For example, if an instrument was 15 seconds behind UTC, set ``clock_offset: 15``.

``clock_offset`` and ``clock_drift_seconds`` describe the **total correction** at two points in time:

- ``clock_offset`` — correction needed at the **start** of the deployment (instrument clock error when deployed).
- ``clock_drift_seconds`` — correction needed at the **end** of the deployment (instrument clock error at recovery).

Stage 2 applies a linear ramp between these two values, so every timestamp receives the
appropriate interpolated correction.  All values are **added** to instrument time
(positive = instrument was slow/behind UTC).

.. list-table::
   :header-rows: 1

   * - Fields set
     - Behaviour
   * - ``clock_offset`` only
     - Uniform constant shift throughout the record.
   * - ``clock_drift_seconds`` only
     - Ramps from 0 at deployment to ``clock_drift_seconds`` at recovery.
   * - Both
     - Ramps from ``clock_offset`` at deployment to ``clock_drift_seconds`` at recovery.

.. code-block:: yaml

   # Example: clock was 5 s slow at deployment and 8 s slow at recovery.
   # Stage 2 applies +5 s at deployment time, +8 s at recovery, linearly in between.
   clock_offset: 5
   clock_drift_seconds: 8

   # Alternative — two timestamps read off at recovery (preferred; avoids sign errors).
   # Compact YYYYMMDDTHH:MM:SS, full ISO-8601 datetime, or time-only HH:MM:SS are all accepted.
   computer_clock_at_recovery:    '20260711T10:23:30'   # or '2026-07-11T10:23:30' or '10:23:30'
   instrument_clock_at_recovery:  '20260711T10:23:22'
   # computer − instrument = +8 s → used as clock_drift_seconds (total correction at recovery)
   # clock_offset (if also set) remains the correction at deployment.

.. note::

   The two-timestamp method measures ``computer − instrument`` at **recovery only**.
   If the clock was also set incorrectly at deployment, set ``clock_offset`` for that
   initial error and let the two timestamps capture the total at recovery.

To estimate an offset from the data — when each instrument entered the cold deep
water, and the lag that best lines up neighboring temperature records —
``oceanarray.pipeline.clock.offsets`` has the functions, shown in the notebook
``notebooks/legacy/demo_clock_offset.ipynb``.

CLI usage
---------

Run Stage 2 for all instruments:

.. code-block:: bash

   oceanarray process dsG3_1_2026 --proc-dir /data/proc --stage 2

Rerun after adjusting deployment times (single instrument):

.. code-block:: bash

   oceanarray process dsG3_1_2026 --proc-dir /data/proc --stage 2 --only 26261 --force

Output format
-------------

``{mooring}_{serial}_stage2.nc`` in ``{proc_dir}/{mooring}/{instrument}/``

The dataset is a trimmed slice of the Stage 1 output.  Key attributes added:

- ``yaml_deployment_time``, ``yaml_recovery_time`` — the trim bounds used
- ``suggested_deployment_time_utc``, ``suggested_recovery_time_utc`` — the window
  detected from the record (see below)
- ``history`` — the clock correction and the trim, in words

Validation
----------

Stage 2 checks for common problems and emits warnings:

- Trimming results in an empty dataset (deployment/recovery times outside the
  raw record)
- Clock correction exceeds a sanity threshold
- Deployment time is after recovery time

Processing continues past individual instrument failures; check
``{proc_dir}/{mooring}/processing_logs/`` for details.

Auto-detection of the deployment window
---------------------------------------

If ``deployment_time`` or ``recovery_time`` is absent from the mooring YAML (or
left as ``null``), Stage 2 attempts to detect the in-water period automatically
by looking for the pressure transition at deployment (instrument sinks from
surface to depth) and at recovery (instrument rises back to surface).

The suggested times are printed as a **WARNING** in the console output and are
also visible in the mooring summary HTML report under the *Deployment timing*
section.  They are stored as global attributes in the Stage 2 NetCDF file:

- ``suggested_deployment_time_utc``
- ``suggested_recovery_time_utc``

**Stage 2 never silently uses the auto-detected window as the trim boundary.**
You must copy the suggested times into the mooring YAML and rerun:

.. code-block:: text

   Recommended workflow
   --------------------
   1.  Run Stage 1:   oceanarray process MOORING --stage 1
   2.  Run Stage 2:   oceanarray process MOORING --stage 2
       → Read suggested deployment/recovery times from the console WARNING
         or from the mooring summary report (Deployment timing section).
   3.  Edit mooring YAML:
           deployment_time: "2026-05-01T18:23:00"   # ← paste suggested value
           recovery_time:   "2026-07-10T06:47:00"   # ← paste suggested value
   4.  Rerun Stage 2 (--force to overwrite):
           oceanarray process MOORING --stage 2 --force

**Interpreting the 6 h window plots** in the mooring summary report:

Each instrument shows two panels — the first and last 6 hours of data.
Two vertical lines are drawn:

- **Green** = value set in the YAML (``deployment_time`` / ``recovery_time``)
- **Orange** = Stage 2 auto-detected suggestion

If the orange line is absent from the end-window panel the suggested recovery
time falls outside the plotted range, most commonly because the YAML
``recovery_time`` was set earlier than the data transition.  Update
``recovery_time`` to the suggested value and rerun.

What comes next
---------------

Stage 2 output is the normal input to Stage 3 (QC, velocity rotation, salinity
derivation).  See :doc:`auto_qc` and :doc:`../processing_framework`.

RAPID's trimming script is on :doc:`../rapid_heritage`.

See also: :doc:`standardisation`, :doc:`time_gridding`
