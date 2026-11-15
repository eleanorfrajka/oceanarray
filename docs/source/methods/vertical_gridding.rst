Grid: pressure levels
=====================

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

QC flags
^^^^^^^^

Values whose QC flag is in ``mask_flags`` are left out first: the default leaves out
4 (bad) and 9 (missing) for every variable; ``velocity=3,4,9`` (or any variable
name) sets an exception. Set it with ``--mask-flags 4,9 velocity=3,4,9``,
``mask_flags=`` in Python, or ``grid: mask_flags`` in the YAML; the file records it
as ``grid_mask_flags``. The stack has already left out the non-velocity values
flagged 3, 4 or 9, so for those the setting can only leave out more. QC flag
variables themselves are not gridded.

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

All ``(time, N_LEVELS)`` variables of the stack except ``pressure``, the flags, and the
instrument-frame and diagnostic variables (beam and XYZ velocity, amplitude,
correlation, heading, pitch, roll), including ``sigma0`` or ``sigma2`` from the stack.
Current speed and direction are computed from the gridded east and north velocity,
not interpolated.

Output file
^^^^^^^^^^^

``proc/{mooring}/{mooring}_grid.nc``

Global attributes are inherited from the stack file. The following attributes are added:
``p_start_dbar``, ``p_end_dbar``, ``dp_dbar``. The ``history`` attribute is extended.
Each gridded variable carries a ``vertical_interpolation`` note in its attributes.

The grid's report page is described in :doc:`../reports`.

See also
--------

- :doc:`time_gridding` — stack all instruments onto a common time axis first
- :doc:`../oceanarray` — full command reference
