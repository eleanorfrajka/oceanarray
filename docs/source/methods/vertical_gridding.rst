Grid: Vertical Pressure Grid
=============================

The ``grid`` stage linearly interpolates the stacked ``(time, N_LEVELS)``
dataset onto a regular pressure grid, producing ``(time, pressure)`` output suitable for
quick T-S section plots and density diagnostics.

.. note::

   Vertical gridding is **preliminary**.  The current implementation is a simple
   1-D linear interpolation at each time step with no objective mapping, error
   estimation, or gap-filling.  It is suitable for a first look at the data but
   should not be used as a substitute for a rigorous vertical interpolation scheme
   in scientific analysis.

Command
-------

.. code-block:: bash

   oceanarray run {mooring} --proc-dir /data/proc --stage 3 [--pmin 200] [--pmax 1000] [--dp 20]

Python API
----------

.. code-block:: python

   import oceanarray as oa

   # stage 3 (kept where it exists), then the stack and grid
   oa.run(yaml_path, stages=3, pmin=200.0, pmax=1000.0, dp=20.0)

   # or on a stack dataset: the grid itself, no files written
   gridded = oa.grid(stacked, pmin=200.0, pmax=1000.0, dp=20.0)

Purpose
-------

Grid reads the stacked ``{mooring}_stack.nc`` file and interpolates each variable from the
sparse instrument levels onto a uniform pressure axis. The result is a
``(time, pressure)`` dataset convenient for section plots, density cross-sections, and
further analysis.

``oceanarray run`` builds the stack and then grids it; the grid step requires the stack to
contain a ``pressure`` variable.

Input files
-----------

``proc/{mooring}/{mooring}_stack.nc``

Algorithm
---------

At each time step, the ``N_LEVELS`` pressure values and each variable are gathered. Only
levels with both a finite pressure value and a finite variable value contribute to the
interpolation. The available points are sorted by pressure and passed to
``numpy.interp`` onto the target pressure grid. Values at pressures outside the range of
finite instruments at that time step are set to NaN; there is no extrapolation.

Note on QC flags
^^^^^^^^^^^^^^^^

The stack step applies QC masking before gridding: when a companion ``*_qc`` variable
exists, samples flagged suspect (3), bad (4), or missing (9) are replaced with NaN so
they do not contribute to the vertical interpolation.  QC flag variables themselves are
not gridded.

Pressure grid axis
------------------

.. code-block:: python

   p_grid = numpy.arange(pmin, pmax + dp / 2, dp)   # dbar

Default: 200 to 1000 dbar in 20 dbar steps.

Output
------

Dimensions
^^^^^^^^^^

``(time, pressure)`` — OceanSITES convention with TIME as the first dimension.

Variables gridded
^^^^^^^^^^^^^^^^^

All ``(time, N_LEVELS)`` variables present in the stack file, except ``pressure`` itself,
are interpolated onto the pressure axis. This includes derived quantities such as
``sigma0`` or ``sigma2`` computed at the stack step.

Output file
^^^^^^^^^^^

``proc/{mooring}/{mooring}_grid.nc``

Global attributes are inherited from the stack file. The following attributes are added:
``p_start_dbar``, ``p_end_dbar``, ``dp_dbar``. The ``history`` attribute is extended.
Each gridded variable carries a ``vertical_interpolation`` note in its attributes.

Grid report
-----------

The command ``oceanarray report {mooring} --grid`` generates
``{mooring}_grid_report.html`` containing:

- Variable coverage table (name, long name, units, percentage non-NaN).
- Temperature pcolormesh and contourf (colormap ``RdYlBu_r``, 20 discrete levels).
- Practical salinity pcolormesh and contourf (colormap ``YlGnBu_r``, reversed so that
  low salinity maps to blue).
- Potential density (sigma0 or sigma2) pcolormesh and contourf (colormap BuPu) with
  iso-density contour lines overlaid (default 27.7 and 27.8 kg m\ :sup:`-3`,
  configurable via ``parameters.SIGMA_CONTOUR_LEVELS``).

All figures use 20 human-readable discrete colorbar levels computed by
``plotters.primitives.nice_colorbar_bounds(vmin, vmax, n=20)``, which rounds the step to one
significant figure and centres the range on the data midpoint.

See also
--------

- :doc:`time_gridding` — stack all instruments onto a common time axis first
- :doc:`../oceanarray` — full command reference
