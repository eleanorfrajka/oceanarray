.. _migrating-0-6-1:

=============================
Upgrading from 0.6.0 to 0.6.1
=============================

0.6.1 changes what the output files hold. Reprocess files you distribute
(``oceanarray run --force``); each change below says what else to do.

The stack keeps flagged values
------------------------------

Before 0.6.1 the stack left out temperature, salinity, pressure and the other
non-velocity values flagged 3, 4 or 9. It now keeps every value, with two flag
sets beside it: ours (``<var>_qc``) and the originator's translated to our table
(``<var>_qc_originator_mapped``). One rule, ``stack: mask_flags`` (default 4),
decides what the stack and Stack cat pages hide and the grid leaves out.

What changes for you:

- **Suspect (3) values are now shown and gridded by default**, for every variable.
  To keep the old behavior, set ``stack: mask_flags: [3, 4]`` in the mooring YAML
  (or ``--mask-flags 3,4``).
- **A level with a value but no flag variable reads 0** (not QC'd), not 9; 9 marks a
  missing value only. Code that treated 9 as "keep" can drop that exception.
- **Read values through the rule** when you use a stack yourself:
  ``oceanarray.pipeline.masking.masked(ds, "salinity")`` returns the values with
  the hidden ones as NaN.
- ``--mask-flags`` on ``run`` now sets the **stack's** rule (what the pages hide and
  what the grid leaves out); before, it set the grid's only. A ``grid: mask_flags`` in
  the YAML adds to it.
- ``grid: mask_flags`` now only **adds** to the stack's rule; one that drops a flag
  the stack hides is refused. The default changed from ``[4, 9]`` to the stack's.
- ``concat`` refuses deployments whose rules differ unless ``--mask-flags`` gives the
  site's.
- Flag tables gain **5 value_changed** (an originator flag saying its QC modified the
  value).

Rebuild existing stacks and grids with ``oceanarray run --force``.

An ADCP needs ``orientation``
-----------------------------

Every ``instrument: ADCP`` entry that is processed now needs ``orientation: up`` or
``orientation: down``. Before, a missing orientation was taken as ``down`` in stage 3,
the stack and the plots, which put an upward-looking ADCP's bins below it. Now
``validate`` reports it as an error and stage 1 refuses the instrument. An RDI header
that disagrees with the YAML is logged and the YAML is used; an imported file that
disagrees is refused.

Instrument ``depth:`` key no longer read
----------------------------------------

An instrument's depth (``instrument_depth``) is its ``nominal_depth``, else the water
depth minus its HAB; a ``depth:`` key on an instrument entry is not read. Rename it to
``nominal_depth`` (m, positive down) or give ``hab``. ``validate`` warns on ``depth:``,
and reports an error when the entry has neither ``nominal_depth`` nor a HAB; stage 1
logs the same warning.

One sensor record per instrument
--------------------------------

A Sea-Bird stage file holds one ``SENSOR_<type>_<serial>`` record (MicroCATs
``SENSOR_CTD_<serial>``) instead of ``SENSOR_TEMP_``, ``SENSOR_CNDC_`` and
``SENSOR_PRES_<serial>``; each data variable names its record in ``sensor``. Code
that reads the old names should follow ``sensor`` instead.

Renamed output attributes
-------------------------

``InstrDepth`` is now ``instrument_depth`` (m) and ``serial_number`` is now
``instrument_serial``, always text. Older stage files are read under the new names;
code that reads the attributes needs the new names.

Positions and times
-------------------

``latitude``, ``longitude``, ``waterdepth`` and the deployment and seabed positions
are numbers, not text; ``deployment_time`` / ``recovery_time`` are ISO 8601 UTC with a
``Z`` (``2026-07-11T20:30:00Z``). Code that parsed the old strings can use the values
directly.

``valid_min`` / ``valid_max``
-----------------------------

These are written only where a physical limit is known (the table in
:doc:`methods/auto_qc`), no longer from the gross-range limits. Values outside the
gross range are flagged 3 or 4 and kept; read the ``_qc`` variable for them.

Tilt QC flags more
------------------

Velocity is flagged on the geometric tilt, arccos(cos pitch · cos roll), at
``QC_TILT`` 30° (suspect) and 50° (bad), even where ``pitch_qc`` / ``roll_qc`` exist,
so a tilted current meter gets more 3s and 4s. To change the thresholds, set
``tilt_qc`` in the mooring YAML. Velocity flags come from the geometric tilt only;
configured pitch / roll gross-range flags are kept on ``pitch_qc`` / ``roll_qc`` but no
longer reach the velocities.

The rotation angle is not in the grid file
------------------------------------------

Site grids no longer record ``slope_angle_deg`` / ``slope_angle_source``; the Grid cat
page rotates at page time. Use ``concat --rotate-to DEG`` (or
``generate_gridcat_page(rotate_to=)``); ``--slope-angle`` and ``grid(slope_angle=)``
warn until 0.7.0. ``site.slope_components`` is now ``site.rotated_components``.

The array YAML
--------------

The year page takes cruises, ships, dates, positions and water depths from the mooring
YAMLs and ignores those keys (and ``vocab``, ``bathymetry``) in the array YAML, with
one warning naming them. Rename ``line:`` on a mooring entry to ``subarray:`` and name
the subarrays in a ``subarrays:`` table; give the bathymetry file as
``report --array --bathymetry FILE``.

Logging
-------

``log_fn=`` on the transforms and ``oceanarray.logger.enable_logging`` /
``disable_logging`` warn and are removed in 0.7.0. Configure the ``oceanarray`` logger
with :mod:`logging` instead, or use ``oceanarray -q`` / ``-v`` on the command line.
