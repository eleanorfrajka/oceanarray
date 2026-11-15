Concatenate Deployments
============================

``oceanarray concat`` joins the stacks of one site's deployments (e.g. egM2 2016–2018
and 2018–2020) end to end on one time axis. Each deployment keeps its own stage files,
stack, grid and report; the concatenated stack (``<site>_stackcat.nc``) and its page
(``<site>_stackcat_report.html``) are a layer on top.

.. code-block:: bash

   oceanarray concat egM2_2016 egM2_2018 --name egM2 --proc-dir /data/proc

.. code-block:: python

   import oceanarray as oa

   site = oa.concat([stack_2016, stack_2018], name="egM2")

What it does
------------

- **Every level of every deployment is kept.** A level is one instrument in one
  deployment; it is empty (NaN, QC flag 9) outside its own deployment's steps.
  Serials repeat across deployments, so ``level_id`` (``<deployment>/<serial>``)
  names each level.
- **Nothing is filled across a turnaround.** The steps between two deployments stay
  empty; ``deployment_index(time)`` is −1 there.
- **Overlaps keep both.** Where two deployments are in the water at once, both are
  kept; ``deployment_index`` names the newer one as the one that counts.
- **One time step.** The stacks must share ``stack: dt_seconds`` and sit on whole
  steps (any stack made by oceanarray 0.6 or later); otherwise ``concat`` refuses.
- **A record per deployment** on the ``DEPLOYMENT`` dimension: name, deployment
  and recovery time, position, water depth, cruises and ships, the source stack's
  file name and SHA-256, its ``history`` and the settings that built it
  (``deployment_settings``, YAML).

Slots
-----

A *slot* labels the levels at about the same depth across deployments, so one depth
can be followed through the years. A level joins the nearest slot of its family
(hydrography: MicroCATs, temperature loggers and CTDs; point velocity: Aquadopps and
current meters) within the slot tolerance of the slot's center (the mean of its
levels so far), at most one level per deployment; otherwise it opens a new slot.
A level that ends up farther than the tolerance from its slot's final center is
named in a warning. ADCP levels get no slot. Slots are numbered deep-first.

Slots are a label only: they group the levels on the report page (the instrument
table, line colors, roses, speed boxes) and in the file (``slot``, ``slot_family``,
``slot_reference_value``); the data are the same whatever the slot rule. The rule is
set in each mooring YAML's ``stack:`` block and must agree across the deployments:

.. code-block:: yaml

   stack:
     dt_seconds: 3600
     slot_reference: depth      # depth (water depth − hab) or hab; default depth
     slot_tolerance_m: 50       # default 50

The report
----------

The concatenated page mirrors the stack page: a Deployments table (each linking to
its own stack report), the processing history (the concat step, then each
deployment's), the instruments by depth slot (each serial linking to its instrument
page), hydrography and velocity with the turnarounds shaded, trajectories and
current speed per slot, T-S diagrams colored by deployment, and current roses with
one outline per deployment. Tilt, instrument spacing and the clock check stay in each
deployment's own report.

Legacy Context (RAPID)
----------------------

The RAPID MATLAB route gridded each deployment first and then interpolated the
segments onto one time grid, filling short gaps between deployments. oceanarray
joins the stacks instead and fills nothing.

This step combines multiple mooring deployments at the same site (e.g., WB1, WB2)
into a single continuous time series on a regular time-pressure grid.

Purpose
~~~~~~~
- Interpolate individual deployment periods onto a common pressure grid (e.g., every 20 dbar from 0–4820 dbar).
- Construct a consistent time axis (e.g., half-daily `jg` from April 1, 2004 to October 11, 2015).
- Interpolate all segments onto this unified time grid, filling short gaps between deployments.
- Output gridded `.mat` files for later use in AMOC transport code.

Process Overview
~~~~~~~~~~~~~~~~
1. Define a global pressure grid `PG` for all moorings.
2. Load gridded temperature and salinity profiles from each deployment, e.g.:
   - `wb1_2_200527_grid.mat`
   - `wb1_3_200607_grid.mat`
   - ...
3. Interpolate each deployment’s `TGfs` (temperature) and `SGfs` (salinity)
   to the common pressure grid `PG`.
4. Collect and concatenate the corresponding `jd` (Julian day) values for each deployment.
5. Linearly interpolate all values onto the global time grid `jg`.
6. Save the final combined profiles for each mooring site (e.g., `TG_wb1`, `SG_wb1`, etc.)
   along with metadata like location and pressure grid.

Notes
~~~~~
- This process assumes that all individual deployment files have already been vertically interpolated
  (i.e., gridded in pressure).
- The output is suitable for subsequent merging across mooring sites and dynamic height calculation.
- Time interpolation smooths over deployment transitions and data gaps.

Original Source
~~~~~~~~~~~~~~~

Adapted from MATLAB routines by Kanzow (2005) and later modified.

The MATLAB script `mooring_interp_wb2_3_5.m` from the RAPID processing workflow interpolates individual deployments together in time using matlab's `interp1` function. It combines multiple mooring deployments at the same site (e.g., WB2) into a single continuous time series on a regular time-pressure grid.  Note that filenaming within the RAPID project has changed several times without changing the core functionality, but the legacy code provided here may *not* be consistent with the current RAPID processing.


.. _mooring_interp_wb2_3_5.m: ../_static/code/mooring_interp_wb2_3_5.m


.. literalinclude:: ../_static/code/mooring_interp_wb2_3_5.m
   :language: matlab
   :lines: 1-40
   :linenos:
   :caption: Excerpt from `mooring_interp_wb2_3_5.m`

