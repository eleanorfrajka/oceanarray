.. _migrating-0-6-0:

=========================
Upgrading from 0.5 to 0.6
=========================

0.6.0 is a breaking release. Most old names still work for one release: they warn
(``DeprecationWarning``) and are removed in 0.7.0. The changes below are grouped by
what you have to do.

The verbs
---------

A mooring is processed from its ``*.mooring.yaml``, wherever that file is; its
folder can have any name, and every file is named from ``name:``.

.. code-block:: python

   import oceanarray as oa

   yaml_path = "proc/dune2_1_2026/dune2_1_2026.mooring.yaml"
   oa.run(yaml_path, raw_dir="raw/", report=True)   # stages 1-3, stack, grid, report
   oa.process(yaml_path, stages=3)                  # stages only
   oa.report(yaml_path, pdf=True)                   # the report only

- ``run`` carries on past a failed instrument by default (``keep_going=True``) and
  records what it left out in the stack and grid. ``keep_going=False`` (CLI
  ``--strict``) stops at the first failure.
- ``run`` rebuilds the stack from the stage-3 files, so its stages run through
  stage 3: ``run(yaml_path, stages=(2, 3))`` or ``run(yaml_path, from_stage=2)``
  (CLI ``--stage 2 3`` or ``--from 2``). ``stages=2`` alone is refused. Run one
  stage on its own with ``process(yaml_path, stages=N)``.
- Renamed CLI flags, accepted with a note until 0.7.0: ``--serial`` → ``--only``,
  ``--dt`` → ``--dt-seconds``, ``plot --var_y`` / ``--var_color`` → ``--var-y`` /
  ``--var-color``; the short ``-i``, ``-s``, ``-g``, ``-A`` on ``report`` →
  ``--instruments``, ``--stack``, ``--grid``, ``--all``. ``animate -o`` is now the
  output directory (``--output`` names one GIF).
- ``run(..., stack_stage=2)`` (CLI ``--stack-stage 2``) stacks the stage-2 files
  for a quick look at sea: no QC flags, no interpolated pressure, no magnetic
  declination — the report says so in amber.
- ``run`` rebuilds the stack and grid every time. A stack or grid that no longer
  matches its stage files is renamed ``<name>_stack.stale.nc`` /
  ``<name>_grid.stale.nc`` (one copy, replaced by the next rename).
- The stack and grid settings can live in the description, in ``stack:``
  (``dt_seconds``) and ``grid:`` (``pmin``, ``pmax``, ``dp``) blocks.
- Every stage-3, stack and grid file carries ``data_mode = "P"`` (provisional).

Renamed names (removed in 0.7.0)
--------------------------------

Each old name warns and calls the new one.

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Old
     - Use instead
   * - ``oceanarray.plotters.timeseries.draw_grid``
     - xarray's DataArray.plot.pcolormesh (or .plot.contourf) on the grid variable
   * - ``oceanarray.parameters``
     - ``oceanarray.config.parameters``
   * - ``oceanarray.config.validation.validate_mooring_yaml``
     - ``oceanarray.mooring.check``
   * - ``oceanarray.pipeline.stage1.MooringProcessor``
     - oceanarray.process(yaml_path, stages=1), or oceanarray.stage1 on datasets
   * - ``oceanarray.pipeline.stage2.Stage2Processor``
     - oceanarray.process(yaml_path, stages=2), or oceanarray.stage2 on datasets
   * - ``oceanarray.pipeline.stage3.Stage3Processor``
     - oceanarray.process(yaml_path, stages=3), or oceanarray.stage3 on datasets
   * - ``oceanarray.pipeline.stack.MooringStacker``
     - oceanarray.run(yaml_path, stages=3), or oceanarray.stack on datasets
   * - ``oceanarray.pipeline.grid.MooringGridder``
     - oceanarray.run(yaml_path, stages=3), or oceanarray.grid on a stack
   * - ``oceanarray.inspect.vars``
     - ``oceanarray.inspect.variables``
   * - ``oceanarray.inspect.attrs``
     - ``oceanarray.inspect.attributes``
   * - ``oceanarray.legacy.process_rodb.normalize_by_middle_percent``
     - ``oceanarray.legacy.process_rodb.to_zscore``
   * - ``oceanarray.legacy.process_rodb.normalize_dataset_by_middle_percent``
     - ``oceanarray.legacy.process_rodb.to_zscore_dataset``

The old call form ``oceanarray.process(name, stage, proc_dir=...)`` (all five stages,
returns a bool) also warns and is removed in 0.7.0: pass the ``*.mooring.yaml``.

Removed in 0.6.0
----------------

- The plotting functions that return a Figure are renamed ``draw_<what>``, with
  no alias: ``plot_knockdown_pressure`` / ``_hab`` / ``_anomaly`` /
  ``_displacement``, ``plot_clock_offset_check``, ``plot_temperature_trajectory``,
  ``plot_speed_boxplot``, ``plot_multi_aquadopp_trajectories``, ``plot_hodograph``,
  ``plot_aquadopp_speed_profile``, ``plot_adcp_trajectories``, ``plot_trajectory``,
  ``plot_microcat_raw``, ``plot_aquadopp_raw``, ``plot_mooring_timeseries`` become
  ``draw_*``; ``draw_isopycnal_ts_fig``, ``draw_overflow_temperature_fig`` and
  ``draw_grid_fig`` become ``draw_isopycnal_ts``, ``draw_overflow_temperature`` and
  ``draw_grid``.

- ``oceanarray.pipeline.units.canonicalise_units`` — use ``to_canonical_units``.
- ``oceanarray.pipeline.units.format_units`` — use
  ``oceanarray.config.unit_tables.unit_label``.
- ``oceanarray.config.parameters.UNIT_ALIASES`` and ``VARIABLE_UNIT_ALIASES`` — use
  ``oceanarray.config.unit_tables.TABLES``.
- ``TimeGriddingProcessor``, ``time_gridding_mooring`` and
  ``process_multiple_moorings_time_gridding`` (with their low-pass, de-tiding and
  band-pass filters) — the stack is the one way to put a mooring on a common time
  axis.

Other changes
-------------


