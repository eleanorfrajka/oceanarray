===================
Development Roadmap
===================

.. contents::
   :local:
   :depth: 2

Status Overview
===============

**Implemented**

- Stage 1: raw file → CF-NetCDF (``pipeline/stage1.py``); formats read through
  ``seasenselib`` (run ``seasenselib list`` for the current list), RDI through dolfyn
- Stage 2: deployment trim and linear clock-drift correction (``pipeline/stage2.py``)
- Stage 3: QARTOD gross-range, spike and flat-line tests, tilt QC, ADCP seabed/surface QC,
  BEAM→ENU rotation (Aquadopp), magnetic declination correction, salinity and density
  (``pipeline/stage3.py``, ``pipeline/qc/``)
- Stack: all instruments on a common time axis, ``(time, N_LEVELS)`` (``pipeline/stack.py``)
- Grid: the stack on regular pressure levels, ``(time, pressure)`` (``pipeline/grid.py``)
- OceanSITES 1.4 export of the stage-3 files and the grid (``pipeline/export.py``,
  ``pipeline/oceansites.py``; ``oceanarray export``)
- HTML reports — mooring summary, per-instrument, stack and grid pages, and one A4 PDF
  (``reports/``)
- Clock offset analysis (``pipeline/clock/offsets.py``)
- Multi-instrument overview plots (``plotters/``)
- Mooring description validation (``oceanarray validate``, ``mooring.check``)

**Partly implemented**

- Calibration correction from caldip casts: the interface and a null-action hook are in
  ``pipeline/calibration/``; applying the offsets is not yet written.

**Not yet implemented**

- OceanSITES export of the stack file
- Concatenation of multiple deployments at a single location
- Multi-site merging for boundary profiles

---

Priority 1: Near-term
=====================

1. Calibration correction (caldip) — front of Stage 3, not a separate stage
-----------------------------------------------------------------------------

**Status**: interface only — ``pipeline/calibration/`` defines the functions and
the stage-3 hook, which does nothing yet.

**Purpose**: apply per-instrument temperature / conductivity / pressure corrections derived
from caldip casts (pre/post-deployment CTD comparisons).

**Design:** not a separate stage.  When a mooring is configured with caldip casts, the
per-instrument offsets are applied to the raw values at the **front of Stage 3**, before
pressure interpolation, so every downstream step sees corrected data.  There is one
``_stage3.nc`` — calibrated when caldip is configured, otherwise byte-identical to the
uncorrected output.  Stack and grid are unchanged.

**Input**: the separate ``caldip`` tool writes one netCDF per cast
(``{cast}_caldip.nc``).  The caldip folder (``caldip_dir``) gives the root only; the
mooring YAML names the cast for each end, so a mooring whose two dips were processed on
different cruises resolves correctly::

    caldip:
      deployment: {cruise: msm142_2026, cast: castM6}
      recovery:   {cruise: <later cruise>, cast: <cast>}

A mooring with a single dip is an ordinary case — the attributes record which end supplied
the constant correction.  The file contract is in :doc:`methods/calibration`.

**Remaining steps:**

- Per-variable stop selection (pressure nearest deployment depth, temperature deepest
  stop, conductivity a low-noise stop near deployment conductivity or a slope across stops).
- Per-variable application threshold (pressure ``> 5 dbar`` and low std; temperature a minimum
  threshold; conductivity by noise/range or fit quality).
- Pre/post-dip corrections applied as a linear time trend, with the linearity recorded as an
  assumption (a two-point fit cannot test it) — conductivity flagged as the case where a
  mid-deployment biofouling step is invisible to that fit.
- Sign/units guards: caldip differences are *instrument − CTD* (subtract to correct);
  reconcile the conductivity unit against the stored one and refuse an unknown unit;
  record both instrument and CTD values so the sign stays verifiable by eye.
- Provenance in the ``_stage3.nc`` attributes.
- Display of the applied corrections in the HTML report.

2. Stage 3: further QC tests
-----------------------------

**Implemented** (via ``ioos_qc``): gross-range, spike and flat-line tests on scalar
variables; tilt QC, ADCP seabed/surface QC, and ENU velocity QC for multi-dimensional
variables.  See :doc:`methods/auto_qc`.

**Candidates:**

- Rate-of-change test
- Climatological range check (season-aware, e.g. from World Ocean Atlas)
- Spike threshold scaling by sampling interval
- Aquadopp tilt thresholds set from an analysis of how tilt degrades the velocities
  (the 30°/50° defaults are placeholders)

3. Test coverage
-----------------

Tests cover the stages, stack, grid, export, reports, plotters, readers, writer and the
mooring description.  The stage-3 and stack orchestration paths are the thinnest; new
work there adds tests as it goes.

---

Priority 2: Longer-term
========================

4. Gridding
-----------

- Apply tilt to ADCP bin pressures (knocked-down bins currently sit at nominal depth).
- RAPID climatological-gradient gridding (``pipeline/gridders/rapid_interp.py``) as a
  selectable method, once the climatology is built.

5. Multi-site merging for boundary profiles
-------------------------------------------

Merge records from multiple mooring sites (e.g. WB2, WB3, WBH2) at each time step to
construct a single merged boundary profile.  Requires static-stability checking and
site-specific weighting strategies.

6. Deployment concatenation
----------------------------

Join successive deployments at the same location into a continuous time series after
clock corrections and QC are confirmed stable.

---

Dependencies
============

- ``scipy``: Welch PSD for spectral figures in the grid report
- ``ioos_qc``: QARTOD gross-range, spike and flat-line tests
- ``gsw`` (TEOS-10): seawater property calculations
- ``xarray`` / ``netCDF4``: core data handling
- ``jinja2``: HTML report generation
- ``matplotlib``: all figures
- ``ppigrf``: IGRF magnetic declination for BEAM→ENU rotation
- ``seasenselib``: raw instrument format readers; pulls in ``mhkit[dolfyn]`` for
  the RDI reader
