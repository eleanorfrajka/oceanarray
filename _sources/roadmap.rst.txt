===================
Development Roadmap
===================

.. contents::
   :local:
   :depth: 2

Status Overview
===============

✅ **Implemented & Working**

- Stage 1: Standardisation (``stage1.py``) — sbe-cnv, sbe-asc, sbe-ascii, nortek-ascii,
  nortek-csv, rbr-rsk, rbr-dat, rbr-hex, rdi-raw (via dolfyn)
- Stage 2: Clock correction + deployment trim (``stage2.py``)
- Stage 3: QARTOD gross-range + spike tests, tilt QC, ADCP seabed/surface QC,
  BEAM→ENU rotation (Aquadopp), magnetic declination correction, salinity + density
  (``stage3.py``)
- Stack: all instruments → common time axis → ``(N_LEVELS, time)`` NC (``mooring_level.py``)
- Grid: stacked data → regular pressure levels (``mooring_level.py``)
- HTML reports — mooring summary, per-instrument pages, stack report, grid report
  (``report/`` package: ``_mooring.py``, ``_instrument.py``, ``_stack.py``, ``_grid.py``)
- Clock offset analysis (``clock_offset.py``)
- Multi-instrument overview plots (``plotters/`` package)
- YAML validation (``validation.py``)
- Configurable logging system

🟡 **Partially Implemented**

- Caldip / calibration comparison: ``castB1_detailed_statistics.csv`` format confirmed
  (columns: serial, instrument_type, bl_press, temp_diff/std, cond_diff/std, press_diff/std,
  status, date, time_start/end, CTD and instrument values, N, label).
  Report integration and YAML linkage to cast files still needed — see Priority 1, item 3.

❌ **Not Yet Implemented**

- Calibration correction from caldip casts (optional step at the front of Stage 3)
- Stage 4: OceanSITES format conversion
- Concatenation of multiple deployments at a single location
- Multi-site merging for boundary profiles

---

Priority 1: Near-term
=====================

1. Stage 3: Additional QC tests
--------------------------------

**Currently implemented** (via ``ioos_qc``): gross-range and spike tests on scalar
variables; tilt QC, ADCP seabed/surface QC, and ENU velocity QC for multi-dimensional
variables.

**Planned additional tests:**

- Flat-line / stuck-sensor test
- Rate-of-change test
- Climatological range check (season-aware, e.g. from World Ocean Atlas)
- Spike threshold scaling by sampling interval

**Post-OdB refactor note**: as the number of QC functions in ``stage3.py`` grows,
consider splitting into a ``stage3/`` sub-package with a dedicated ``stage3/qc.py``
(or ``stage3/qc_scalar.py`` + ``stage3/qc_adcp.py``).  The public entry-point
``process_stage3(mooring_yaml, proc_dir)`` would remain in ``stage3/__init__.py``.
No change to the CLI or output format — purely an internal organisation change.

2. Calibration correction (caldip) — front of Stage 3, not a separate stage
-----------------------------------------------------------------------------

**Status**: planned — not yet implemented (``pipeline/caldip.py`` is a stub).

**Purpose**: apply per-instrument temperature / conductivity / pressure corrections derived
from caldip casts (pre/post-deployment CTD comparisons).

**Design (decided 2026-09-05):** not a separate stage.  When a mooring is configured with
caldip casts, the per-instrument offsets are applied to the raw values at the **front of
Stage 3**, before pressure interpolation, so every downstream step sees corrected data.
There is one ``_stage3.nc`` — calibrated when caldip is configured, otherwise byte-identical
to today's output.  Stack and grid are unchanged.

**Input**: ``--caldip-dir`` gives the root directory only; the mooring YAML names the cast
for each end, so a mooring whose two dips were processed on different cruises resolves
correctly::

    caldip:
      deployment: {cruise: msm142_2026, cast: castM6}
      recovery:   {cruise: <later cruise>, cast: <cast>}

A mooring with a single dip is an ordinary case — the attributes record which end supplied
the constant correction.

**Current state**: caldip produces ``castXX_detailed_statistics.csv`` (one per cast) with
``serial``, ``temp_diff``, ``cond_diff``, ``press_diff``, their ``_std``, ``*_status``,
``ctd_sensor_used``, window times, etc.  These files exist but are not yet read.

**Remaining steps:**

- Reader; per-variable stop selection (pressure nearest deployment depth, temperature deepest
  stop, conductivity a low-noise stop near deployment conductivity or a slope across stops).
- Per-variable application threshold (pressure ``> 5 dbar`` and low std; temperature a minimum
  threshold; conductivity by noise/range or fit quality).
- Pre/post-dip corrections applied as a linear time trend, with the linearity recorded as an
  assumption (a two-point fit cannot test it) — conductivity flagged as the case where a
  mid-deployment biofouling step is invisible to that fit.
- Sign/units guards: caldip ``*_diff`` is *instrument − CTD* (subtract to correct); ``cond_diff``
  is mS/cm — reconcile against the stored conductivity unit; record both instrument and CTD
  values so the sign stays verifiable by eye.
- Provenance in the ``_stage3.nc`` attributes; ``ctd_slope_adjusted`` recorded ``UNK`` until
  caldip emits it.
- A per-instrument report figure showing the conductivity offset-vs-slope choice; display of
  applied corrections in the HTML report (later work).

3. Test coverage
-----------------

**Current state**: tests exist for ``stage1``, ``stage2``, ``stage3``,
``time_gridding``, ``plotters``, ``readers``, ``writers``, ``tools``,
``utilities``, and ``logger``.

**Remaining gap**: ``test_report.py`` — end-to-end tests for the HTML report
generation pipeline.

---

Priority 2: Longer-term
========================

4. Stage 4: OceanSITES conversion and deployment concatenation
---------------------------------------------------------------

Lower priority.  The existing ``_stack.nc`` / ``_grid.nc`` outputs follow CF conventions
and can be converted to OceanSITES with a relatively thin wrapper once the earlier stages
are stable.

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
- ``ioos_qc``: QARTOD gross-range and spike tests
- ``gsw`` (TEOS-10): seawater property calculations
- ``xarray`` / ``netCDF4``: core data handling
- ``jinja2``: HTML report generation
- ``matplotlib``: all figures
- ``ppigrf``: IGRF magnetic declination for BEAM→ENU rotation
- ``seasenselib``: raw instrument format readers (sbe-cnv, sbe-ascii, nortek-ascii,
  nortek-csv, rbr-rsk, rbr-dat, rbr-hex, rdi-raw); pulls in ``mhkit[dolfyn]`` for
  the RDI reader
