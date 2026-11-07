Concatenate Deployments
============================

This step combines multiple mooring deployments at the same site (e.g., WB1, WB2)
into a single continuous time series on a regular time-pressure grid.

Purpose
-------
- Interpolate individual deployment periods onto a common pressure grid (e.g., every 20 dbar from 0–4820 dbar).
- Construct a consistent time axis (e.g., half-daily `jg` from April 1, 2004 to October 11, 2015).
- Interpolate all segments onto this unified time grid, filling short gaps between deployments.
- Output gridded `.mat` files for later use in AMOC transport code.

Process Overview
----------------
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
-----
- This process assumes that all individual deployment files have already been vertically interpolated
  (i.e., gridded in pressure).
- The output is suitable for subsequent merging across mooring sites and dynamic height calculation.
- Time interpolation smooths over deployment transitions and data gaps.

Original Source
---------------

Legacy Context (RAPID)
----------------------

Adapted from MATLAB routines by Kanzow (2005) and later modified.

The MATLAB script `mooring_interp_wb2_3_5.m` from the RAPID processing workflow interpolates individual deployments together in time using matlab's `interp1` function. It combines multiple mooring deployments at the same site (e.g., WB2) into a single continuous time series on a regular time-pressure grid.  Note that filenaming within the RAPID project has changed several times without changing the core functionality, but the legacy code provided here may *not* be consistent with the current RAPID processing.


.. _mooring_interp_wb2_3_5.m: ../_static/code/mooring_interp_wb2_3_5.m


.. literalinclude:: ../_static/code/mooring_interp_wb2_3_5.m
   :language: matlab
   :lines: 1-40
   :linenos:
   :caption: Excerpt from `mooring_interp_wb2_3_5.m`

