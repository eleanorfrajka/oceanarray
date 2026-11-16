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

- **Every level of every deployment is kept.** A level is one instrument (or one
  ADCP bin) in one deployment. Serials repeat across deployments, so ``level_id``
  (``<deployment>/<serial>``) names each level.
- **Stored by depth.** The time series are on ``(time, N_DEPTHS)``: a *depth* holds
  the levels at about the same depth across deployments (see Depths), so one
  depth is one column through the years. ``level_index(time, N_DEPTHS)`` names the
  level behind each value (−1: none); a depth is empty (NaN, QC flag 9) outside its
  levels' deployments.
- **Nothing is filled across a turnaround.** The steps between two deployments stay
  empty; ``deployment_index(time)`` is −1 there.
- **Overlaps lose nothing.** Where two deployments are in the water at once, the
  newer one fills the depth (``overlap="older"`` for the other way round) and the
  other's values go to the *overlap block* on ``N_OVERLAP``: ``overlap_time``,
  ``overlap_depth_index``, ``overlap_level_index`` and ``overlap_<variable>``.
- **One time step.** The stacks must share ``stack: dt_seconds`` and sit on whole
  steps (any stack made by oceanarray 0.6 or later); otherwise ``concat`` refuses.
- **A record per deployment** on the ``DEPLOYMENT`` dimension: name, deployment
  and recovery time, position, water depth, cruises and ships, the source stack's
  file name and SHA-256, its ``history``, the settings that built it
  (``deployment_settings``, YAML) and its people. A stack attribute the
  deployments agree on is kept once; one that differs becomes a
  ``deployment_<name>`` variable.
- **People.** ``principal_investigator*`` and ``contributor_*`` (OceanSITES 1.4:
  commas between PIs, semicolons between contributors, one entry per person and
  role in every parallel attribute) are the union of every deployment's, without
  repeats; a contributor with two roles has two entries. See :doc:`../oceanarray_format`.
- **Memory.** Each variable of each stack is read once and placed by integer
  slicing, so the peak is about the result plus one variable of one stack. The file
  is chunked by about a year along time.

Depths
------

A level joins the nearest depth of its family (hydrography: MicroCATs, temperature
loggers and CTDs; point velocity: Aquadopps and current meters; ADCP bins; ADCP
heads, which never share a depth with a bin) within the depth tolerance of that
depth's center (the mean of its levels so far), at most one level per deployment;
otherwise it opens a new depth. The tolerance is ``depth_tolerance_m`` (default
50 m), or half the bin size for ADCP bins. A level that ends up farther than the
tolerance from its depth's final center is named in a warning.

.. warning::

   Depth indices are not stable. Adding a deployment or changing the matching rule
   renumbers them, so nothing should read an index as a depth: a depth's value is
   ``nominal_depth`` (the mean nominal depth of its levels). The report names each
   depth by its value.

The rule is set in each mooring YAML's ``stack:`` block and must agree across the
deployments:

.. code-block:: yaml

   stack:
     dt_seconds: 3600
     depth_match: depth        # depth (water depth − hab) or hab; default depth
     depth_tolerance_m: 50     # default 50

The report
----------

The concatenated page mirrors the stack page: a Deployments table (each linking to
its own stack report), the processing history (the concat step, then each
deployment's), the instruments by depth (each serial linking to its instrument
page), hydrography and velocity with the turnarounds shaded, trajectories and
current speed per depth, T-S diagrams colored by deployment, and current roses with
one outline per deployment. Tilt, instrument spacing and the clock check stay in each
deployment's own report.

The links look for each deployment's reports in the ``report`` folder beside its
stack, or in ``<DIR>/<deployment>`` with ``--report-dir DIR`` (``report_dir=`` in
Python).

The site grid
-------------

``oceanarray concat`` then grids the Stack cat onto pressure levels with the same
gridder as a deployment's grid (``oceanarray.grid``), one mooring per profile: at
each step only the levels of the deployment ``deployment_index`` names are
interpolated, so a profile never mixes two moorings; the overlap block is not
gridded and turnarounds stay empty. The file, ``<site>_gridcat.nc``, keeps the
stack's time step, ``deployment_index`` and the ``DEPLOYMENT`` record. The
settings come from the call (``--pmin``, ``--pmax``, ``--dp``, ``--mask-flags``,
``--slope-angle``), not from the deployments' YAMLs, and are recorded in the file.

The site stack takes the deployments' ``mask_flags`` rule; when they differ,
``--mask-flags`` must give the site's (each deployment's own stays in
``deployment_stack_mask_flags``). A deployment without the originator's flags reads 0
where it has values. Before gridding, values the rule hides are left out (default 4;
``velocity=3,4`` adds suspect velocity). The
along-slope direction is ``--slope-angle``, else the direction of the record-mean,
depth-mean velocity, used only when the flow is steady enough to have one (the
speed of the mean daily velocity over the mean daily speed at least 0.3; otherwise
no along- and across-slope panels, and a warning). The pressure range is
``--pmin`` / ``--pmax``, else from the 5th percentile of the shallowest depth's
pressure to the 95th of the deepest's, so an instrument riding above or below its
usual depth stays on the grid. Each deployment's own ``grid:`` settings are not
used; a deployment whose settings differ is named in a warning. Current speed and
direction are computed from the gridded east and north velocity.

Its page, ``<site>_gridcat_report.html`` (the "Grid cat" pill beside "Stack cat"),
shows pressure × time sections of temperature and salinity with σ0 and σ2
isopycnals, their anomalies from the whole-record median at each level, the median
profile of each deployment, the velocity sections (east, north, along- and
across-slope, cropped to the pressures with velocity data at the same dbar per inch
as the T and S sections) and a T-S diagram of the median profiles. The sections are
averaged for display (daily for a record under a year, weekly under 3, 2-weekly
under 20, monthly beyond), in bins that restart at each deployment's start, so no
bin spans two deployments and a step between them shows as a sharp edge; the file
itself is not averaged.

RAPID
-----

RAPID gridded each deployment first and then interpolated the segments onto one
time axis, filling short gaps between deployments; oceanarray joins the stacks and
fills nothing. See :doc:`../rapid_heritage`.
