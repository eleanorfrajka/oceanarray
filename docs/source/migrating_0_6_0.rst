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
  ``--dt`` → ``--dt-seconds``, ``process --report`` → ``--summary``,
  ``plot --downsample`` → ``--downsample-seconds``, ``export --overwrite`` →
  ``--force``, ``stamp --stage`` → ``--declare``, ``plot --var_y`` / ``--var_color`` → ``--var-y`` /
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
- The stack's default time step is now 3600 s (was 60 s). A mooring stacked at 60 s
  before (MIXSED, ``odb2026``) sets ``stack: {dt_seconds: 60}`` in its mooring YAML.
  Every instrument is now placed by its nearest sample within ±dt/2 (a slow one is no
  longer interpolated in time), and the time axis starts on a whole multiple of the step.
  A sample exactly half a step from two steps now fills the later one only (before:
  both), so a sparse or phase-offset instrument can show an empty boundary step where
  an older stack had a value.
- Every stage-3, stack and grid file carries ``data_mode = "P"`` (provisional).
- The grid report's **Overflow** section is now off by default; turn it on with a
  ``report_sections`` block (``report_sections: {grid: [overflow]}``) in the mooring
  YAML. An overflow mooring whose YAML does not set it loses the section — add the
  block to each overflow mooring's YAML to keep it.

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

Moved modules (no alias)
------------------------

Nothing outside oceanarray imported these, so they moved without a shim.
``oceanarray.processors`` is now ``oceanarray.pipeline`` (every
``oceanarray.processors.*`` import becomes ``oceanarray.pipeline.*``), and
``oceanarray.tools``, ``oceanarray.utilities``, ``oceanarray.pipeline.helpers``
and ``oceanarray.legacy.tools`` are gone. ``oceanarray.tools.readers`` moved to the
``oceanarray.readers`` package (``read_oceansites``, ``read_rodb``,
``load_nortek_csv``; ``readers.read`` picks the format).

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - Old
     - New
   * - ``oceanarray.utilities`` (``instrument_entries``, ``extract_inline_instruments``)
     - ``oceanarray.instruments``
   * - ``oceanarray.utilities`` (``parse_latlon``, ``parse_latlon_with_source``, ``format_latlon``, …)
     - ``oceanarray.location``
   * - ``oceanarray.utilities`` (``find_best_dtype``, ``cast_output_dtypes``)
     - ``oceanarray.writers.dtypes``
   * - ``oceanarray.utilities`` (``should_skip_regeneration``)
     - ``oceanarray.paths``
   * - ``oceanarray.utilities`` (``drop_all_zero_vars``)
     - ``oceanarray.pipeline.dataset``
   * - ``oceanarray.utilities`` (``nice_colorbar_ticks``, ``period_axis_ticks``; ``_nice_colorbar_bounds`` → ``nice_colorbar_bounds``)
     - ``oceanarray.plotters.primitives``
   * - ``oceanarray.utilities._status``, ``_format_rel_path``
     - ``oceanarray.logger.status``, ``logger.format_rel_path``
   * - ``oceanarray.tools.rapid_interp``
     - ``oceanarray.pipeline.gridders.rapid_interp``
   * - ``oceanarray.tools.process_dataset``, ``analysis.science.process_dataset``, ``analysis.temporal.downsample_to_sparse``
     - ``oceanarray.pipeline.gridders.climatology``
   * - ``oceanarray.tools`` / ``analysis.science`` re-exports ``calc_psal``, ``calc_ds_difference``, ``find_cold_entry_exit``, ``isopycnal_dataset``, ``isopycnal_pressure_series``
     - ``oceanarray.analysis.hydrography``
   * - ``oceanarray.tools`` / ``analysis.science`` re-exports ``welch_psd``, ``welch_psd_gapaware``, ``compute_cwt``
     - ``oceanarray.analysis.spectral``
   * - ``oceanarray.tools`` re-exports ``lag_correlation``, ``split_value``, ``downsample_to_sparse``
     - ``oceanarray.pipeline.clock.offsets`` (``lag_correlation``, ``split_value``), ``oceanarray.pipeline.gridders.climatology`` (``downsample_to_sparse``)
   * - ``oceanarray.tools`` re-exports ``flag_salinity_outliers``, ``flag_temporal_spikes``, ``flag_vertical_inconsistencies``, ``run_qc``
     - ``oceanarray.legacy.qc_rodb``
   * - ``oceanarray.pipeline.helpers`` (was ``processors.helpers``): ``STACK_VARS`` and the stack helpers
     - ``oceanarray.pipeline.stack``; the QC mask and worst-flag merge → ``oceanarray.pipeline.qc``
   * - ``oceanarray.analysis.temporal.filter_sigma_tukey``
     - ``oceanarray.pipeline.gridders.filters``
   * - ``oceanarray.analysis.clock`` (``analyze_deployment_timing`` → ``deployment_timing``; + ``analysis.temporal.lag_correlation``, ``split_value``)
     - ``oceanarray.pipeline.clock.offsets``
   * - ``oceanarray.analysis.vector.progressive_vector``
     - ``oceanarray.plotters.current.progressive_vector``
   * - ``oceanarray.utilities`` (``get_dims``, ``check_necessary_variables``, ``get_time_key``)
     - ``oceanarray.pipeline.gridders.rapid_interp``
   * - ``oceanarray.utilities`` (``concat_with_scalar_vars``)
     - ``oceanarray.legacy.mooring_rodb``
   * - ``oceanarray.utilities`` (``iso8601_duration_from_seconds``, ``is_iso8601_utc``)
     - ``oceanarray.legacy.convertOS``
   * - ``oceanarray.config.validation.print_validation_report``
     - removed (``oceanarray validate`` prints the report)
   * - ``oceanarray.legacy.find_deployment``
     - ``oceanarray.pipeline.clock.deployment``
   * - ``oceanarray.pipeline.caldip`` (``normalize_serial`` → ``canonical_serial``)
     - ``oceanarray.pipeline.calibration``
   * - ``oceanarray.pipeline.coordinate.apply_adcp_seabed_qc`` / ``_surface_qc`` / ``_velocity_qc``
     - ``oceanarray.pipeline.qc.adcp``
   * - ``oceanarray.analysis.hydrographic``
     - ``oceanarray.analysis.hydrography``
   * - ``oceanarray.analysis.science`` QC flaggers (``flag_salinity_outliers``, ``run_qc``, …)
     - ``oceanarray.legacy.qc_rodb``
   * - ``oceanarray.analysis.vector.xyz_to_enu_2d``
     - ``oceanarray.pipeline.coordinate.xyz_to_enu`` (same values; returns ``up`` too)
   * - ``oceanarray.config.validation.serial_collision_errors``
     - ``oceanarray.mooring.serial_collision_errors``
   * - ``oceanarray.pipeline._resolve_mooring``
     - ``oceanarray.pipeline.resolve_mooring`` (public)

Other changes
-------------

- **A stage file made under a different mooring name is refused.**  Reading a stage
  file whose ``mooring_name`` differs from the description's ``name:`` by more than
  case raises; reprocess it from stage 1.  A case-only difference is corrected.
- **Stacking no longer reads ``sample_interval_seconds``.**  Each instrument's
  sampling interval is detected from its time stamps.
- **Stack and grid files change.** Besides the hourly default above, a ``clamp``
  ADCP whose height is given only as ``hab_bottom`` / ``hab_top`` is now placed by
  its ``orientation`` and stacked (before: silently left out). Rebuild stacks and
  grids with 0.6.
- **QC override blocks are checked.** A ``qc_ranges``, ``qc_spike``, ``tilt_qc`` or
  ``qc_flat_line`` block with an unknown field, a span that is not two increasing
  numbers, a threshold that is not a number, or a new variable without a
  ``fail_span`` is refused by ``load`` and is an ERROR in ``oceanarray validate``
  (before: dropped, and QC ran with the defaults). Run ``oceanarray validate`` on
  each description.
- **An orientation needs its hab key.** An instrument with ``orientation: up`` and
  no ``hab_top`` (or ``down`` and no ``hab_bottom``), and no ``hab``, is refused by
  ``load`` and is an ERROR in ``oceanarray validate``. Add the missing key.
- **The writer does not overwrite.** ``oceanarray.writers.write(ds, path)`` refuses
  an existing file unless ``overwrite=True`` and returns the path written.
- **Exit codes.** ``oceanarray run`` and ``process`` exit 0 when they finish with
  instruments left out (before: 1); ``--strict`` stops at the first failure with
  exit 1. A usage error exits 2.
- **Output names** come from the description's ``name:``, not the folder's name.
- ``current_direction`` carries ``standard_name = "sea_water_velocity_to_direction"``
  (CF deprecated ``direction_of_sea_water_velocity``).
- Stage files without a ``processing_stage`` marker are read with a warning until
  0.7.0; mark them once with ``oceanarray stamp FILE... --declare N``.
