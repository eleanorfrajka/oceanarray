Processing stages
=================

Processing runs at three levels: each **instrument** through stages 1–3, each
**mooring** into a stack and a grid, and each **site** (several deployments at one
place) into a Stack cat and a Grid cat. Every stage reads the files the one before
wrote and records what it did in the file's ``history`` and attributes, so a file
says how it was made.

Each instrument
---------------

.. figure:: /_static/instrument_processing_v6.png
   :alt: Stages 1 to 3 of one instrument, then the OceanSITES export
   :align: center

   Stages 1–3 of one instrument, then the OceanSITES export.

.. list-table::
   :header-rows: 1
   :widths: 14 56 30

   * - Stage
     - What it does
     - Writes
   * - 1 — read
     - Reads the raw file (through ``seasenselib``) to CF-NetCDF, as recorded: no
       trimming, no QC. Units get one spelling each; Aquadopp beam velocities are
       rotated to the instrument frame (XYZ).
     - ``{mooring}_{serial}_stage1.nc``
   * - 2 — trim
     - Applies the clock offset and drift, and keeps the record between
       ``deployment_time`` and ``recovery_time``.
     - ``…_stage2.nc``
   * - 3 — QC
     - Pressure for instruments without a sensor (from their neighbors), salinity,
       velocity rotated to east/north/up with the magnetic declination, and QC flags
       (gross range, spike, flat line, tilt, ADCP seabed and quality).
     - ``…_stage3.nc``

The QC flags follow OceanSITES reference table 2; the thresholds used are written on
each ``*_qc`` variable. A calibration-dip correction at the front of stage 3 is
designed but not applied yet (:doc:`methods/calibration`).

Each mooring
------------

.. figure:: /_static/mooring_processing_v4.png
   :alt: The stack and the grid of one mooring
   :align: center

   The stack and the grid of one mooring.

- **Stack** — every instrument on one time axis (default hourly), each step taking
  the instrument's nearest sample within half a step; nothing is interpolated in
  time (``{mooring}_stack.nc``, on ``time × N_LEVELS``). Every value is kept, with
  our flags and the originator's beside it; ``stack: mask_flags`` (default 4) says
  which the pages hide and the grid leaves out. See
  :doc:`methods/time_gridding`.
- **Grid** — the stack interpolated linearly in pressure at each step, with no
  extrapolation (``{mooring}_grid.nc``, on ``time × pressure``). Values the stack's
  ``mask_flags`` hides (default 4) are left out first. See
  :doc:`methods/vertical_gridding`.

``oceanarray run`` does stages 1–3, the stack, the grid and the report in one go.

Each site
---------

Several deployments at one place are joined end to end into the **Stack cat**,
by depth, filling nothing across a turnaround, and gridded one mooring per
profile into the **Grid cat** (``oceanarray concat``). See
:doc:`methods/concatenation`.

Sharing
-------

``oceanarray export`` writes each instrument's stage-3 file and the grid as
OceanSITES 1.4 files. See :doc:`methods/oceansites_export`.
