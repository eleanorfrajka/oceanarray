.. _migrating-0-7-0:

=============================
Upgrading from 0.6.1 to 0.7.0
=============================

Stage files carry ``clock_offset`` only when applied
----------------------------------------------------

Stage 1 no longer writes a ``clock_offset`` of 0 (it applies no clock correction),
and stage 2 writes the ``clock_offset`` variable only when it applies a non-zero
offset. A file with no offset therefore has no ``clock_offset`` variable: read it
with ``ds.get("clock_offset", 0)`` (an absent variable means no offset), not
``ds["clock_offset"]``. Rerun stage 2 to update existing files.

The clock tools moved to ``analysis``
-------------------------------------

``oceanarray.pipeline.clock`` (``offsets``, ``deployment``) is now
``oceanarray.analysis.clock``, unchanged. The old path still imports, with a
``DeprecationWarning``, until 0.8.0; change ``from oceanarray.pipeline.clock import …``
to ``from oceanarray.analysis.clock import …``.

Depth and pressure conversion moved out of ``pipeline.pressure``
-----------------------------------------------------------------

``pressure_from_depth`` and ``depth_from_pressure`` are now in ``oceanarray.physics``,
and ``ORIGINATOR_BIN_PRESSURE_ATTRS`` and ``originator_bin_geometry`` in
``oceanarray.readers.adcp_geometry``, unchanged. Importing them from
``oceanarray.pipeline.pressure`` still works, with a ``DeprecationWarning``, until
0.8.0; change ``from oceanarray.pipeline.pressure import pressure_from_depth`` to
``from oceanarray.physics import pressure_from_depth``.

The mooring YAML modules moved into ``oceanarray.mooring``
----------------------------------------------------------

Everything about the mooring YAML is now in the ``oceanarray.mooring`` package,
unchanged. ``from oceanarray import mooring`` and ``oceanarray.mooring.load``,
``check``, ``template``, ``save`` and the other names work as before.

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - Old path
     - New path
   * - ``oceanarray.instruments``
     - ``oceanarray.mooring.instruments``
   * - ``oceanarray.location``
     - ``oceanarray.mooring.location``
   * - ``oceanarray.config.validation``
     - ``oceanarray.mooring.validation``
   * - ``oceanarray.catalog``
     - ``oceanarray.mooring.catalog``

The first three old paths still import, with a ``DeprecationWarning`` naming the
new one, until 0.8.0; change ``from oceanarray.instruments import instrument_entries``
to ``from oceanarray.mooring.instruments import instrument_entries``.
``oceanarray.catalog`` moved without an old path: import it as
``from oceanarray.mooring import catalog``.

Names moved without an old path
-------------------------------

These names moved, unchanged, and no longer import from their old module; change
the import, e.g. ``from oceanarray.pipeline.masking import stack_rule`` to
``from oceanarray.mask_rules import stack_rule``.

.. list-table::
   :header-rows: 1
   :widths: 60 40

   * - Old path
     - New path
   * - ``oceanarray.pipeline.masking``: ``resolve_mask_flags``, ``stack_rule``,
       ``combine``, ``union``, ``to_json``, ``rule_words``, ``Rule``,
       ``STACK_ATTR``, ``FLAGGED_VARIABLES``, ``FLAG_SOURCES``
     - ``oceanarray.mask_rules``
   * - ``oceanarray.pipeline.attribution``: ``check_people_block``,
       ``check_source_block``, ``PEOPLE_BLOCK_KEYS``, ``SOURCE_BLOCK_KEYS``
     - ``oceanarray.mooring.blocks``
   * - ``oceanarray.pipeline.qc.originator``: ``vocabulary``, ``OURS_ONLY``
     - ``oceanarray.originator_flags``
   * - ``oceanarray.provenance.changed_attr``
     - ``oceanarray.writers.dtypes.changed_attr``

``rule_words`` also takes a new keyword-only ``table=`` (default ``False``, the
old text).

Figure helpers ``masked`` and ``ruled``
---------------------------------------

The two figure helpers still work, with a ``DeprecationWarning``, until 0.8.0.

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - Old name
     - Use instead
   * - ``oceanarray.plotters.helpers.masked``
     - ``oceanarray.pipeline.masking.masked``
   * - ``oceanarray.plotters.helpers.ruled``
     - ``oceanarray.mask_rules.STACK_ATTR in ds.attrs``

``masked(ds, var)`` now gives what ``oceanarray.pipeline.masking.masked`` gives,
as the figures do: a file that records no ``mask_flags`` rule (an instrument file,
a grid) hides values by the default rule (flag 4) in ``<var>_qc`` and in the
originator's mapped flag, no longer by flags 3, 4 and 9 in ``<var>_qc``. A stack
or Stack cat with a recorded rule gives the same values as before.

Keywords that say what they carry
---------------------------------

A keyword that takes the loaded mooring YAML (a dict) is now ``mooring_dict=``
(``mooring_dicts=`` for several), one that takes the path of a ``*.mooring.yaml``
file is ``yaml_paths=``, and one that takes either a path or a dict is
``mooring=``. A call that gives the argument by position is unchanged. The old
keyword still works, with a ``DeprecationWarning`` naming the new one, until
0.8.0; change ``resolve_spectrum_markers(description=d)`` to
``resolve_spectrum_markers(mooring_dict=d)`` or ``resolve_spectrum_markers(d)``.

.. list-table::
   :header-rows: 1
   :widths: 70 30

   * - Old keyword
     - New keyword
   * - ``oceanarray.mooring.resolve_report_sections(description=)``
     - ``mooring_dict=``
   * - ``oceanarray.mooring.resolve_spectrum_markers(description=)``
     - ``mooring_dict=``
   * - ``oceanarray.mooring.settings_yaml(cfg=)``
     - ``mooring_dict=``
   * - ``oceanarray.mooring.serial_collision_errors(mooring_config=)``
     - ``mooring=``
   * - ``oceanarray.mooring.location.parse_latlon(cfg=)``
     - ``mooring_dict=``
   * - ``oceanarray.mooring.location.parse_latlon_with_source(cfg=)``
     - ``mooring_dict=``
   * - ``oceanarray.pipeline.read_mooring.header_attrs(cfg=)``
     - ``mooring_dict=``
   * - ``oceanarray.pipeline.stack.mooring_attrs(mooring_config=)``
     - ``mooring_dict=``
   * - ``oceanarray.pipeline.concat.concat(descriptions=)``
     - ``mooring_dicts=``
   * - ``oceanarray.pipeline.concat.concat_moorings(descriptions=)``
     - ``yaml_paths=``
   * - ``oceanarray.pipeline.qc.load_qc_config(mooring_cfg=)``
     - ``mooring_dict=``
   * - ``oceanarray.reports.MooringReport.generate(cfg=)``
     - ``mooring_dict=``
   * - ``oceanarray.stage1(mooring=)``,
       ``oceanarray.stage2(mooring=)``,
       ``oceanarray.stage3(mooring=)``,
       ``oceanarray.stack(mooring=)``,
       ``oceanarray.pipeline.stack.stack_stale_reason(mooring=)``,
       ``oceanarray.pipeline.stack.people_source_sha256(mooring=)``,
       ``oceanarray.mask_rules.stack_rule(mooring=)``,
       ``oceanarray.mooring.catalog.lookup(mooring=)``,
       ``oceanarray.mooring.catalog.digest(mooring=)``,
       ``oceanarray.mooring.instruments.instrument_entries(mooring=)``,
       ``oceanarray.mooring.save(mooring=)``,
       ``oceanarray.pipeline.oceansites.to_oceansites(mooring=)``,
       ``oceanarray.pipeline.oceansites.deployment_code(mooring=)``,
       ``oceanarray.pipeline.oceansites.platform_code(mooring=)``,
       ``oceanarray.pipeline.raw_files.resolve_raw_file(mooring=)``,
       ``oceanarray.pipeline.read_mooring.join_raw(mooring=)``
     - ``mooring_dict=``

Mooring YAMLs in their own folder
---------------------------------

New in 0.7.0: a paths file, ``paths.yaml``, says where a project's folders are
(:ref:`paths-file`), and the mooring YAMLs live in its ``yaml_dir``, apart from the
processed files. ``oceanarray.Paths.yaml_path`` and ``yaml_paths`` still find a
mooring YAML in the processed-data folder, as in the standard structure until now,
with a ``DeprecationWarning``, until 0.8.0; move each YAML to its ``yaml_dir``.

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - Old location
     - New location
   * - ``a mooring YAML inside proc_dir/<mooring_name>/``
     - ``<yaml_dir>/<mooring_name>.mooring.yaml``

The verbs take ``yaml_path`` and ``paths``
------------------------------------------

``run``, ``process``, ``report`` and ``export`` take the mooring YAML's path and an
``oceanarray.Paths`` (:ref:`paths-file`), and nothing else about where files are;
``oceanarray.mooring.load`` and ``check`` take the YAML's path. Every folder comes
from ``paths``: the stage, stack and grid files from ``paths.mooring_dir(name)``,
the raw files from ``paths.raw_mooring_dir(name)``, the pages (and the PDF, beside
them) from ``paths.report_mooring_dir(name)``, the OceanSITES files from
``paths.export_dir``. A mooring whose folder is not ``<dir>/<mooring_name>`` gets it
from ``paths.with_mooring``. Change

.. code-block:: python

   oa.run("proc/M2_2016/M2_2016.mooring.yaml", raw_dir="raw")

to

.. code-block:: python

   paths = oa.Paths(raw_dir="raw", proc_dir="proc")
   oa.run("proc/M2_2016/M2_2016.mooring.yaml", paths)

The old forms still work, with one ``DeprecationWarning`` naming the new call, until
0.8.0, and write the same files to the same folders as before: a verb called without
``paths`` works in the folder holding the YAML (``mooring_dir=``, the mooring's own
folder, overrides it), its raw files in ``<raw_dir>/<that folder's name>``, its
pages in ``<folder>/report`` (the recovery table in the folder itself), its
OceanSITES files in ``<folder>/oceansites``. A mooring folder or a loaded mooring
YAML (a dict) given in place of the path, and the first argument given as
``mooring=``, warn the same way. The command line is unchanged.

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Old form
     - Use instead
   * - ``a verb called without paths``
     - ``an oceanarray.Paths as the second argument, e.g. oceanarray.run(yaml_path, paths)``
   * - ``a mooring folder in place of a verb's yaml_path``
     - ``the folder's <mooring_name>.mooring.yaml as yaml_path``
   * - ``a mooring dict in place of a yaml_path``
     - ``the mooring YAML's path as yaml_path``
   * - ``oceanarray.process(mooring=)``, ``oceanarray.run(mooring=)``,
       ``oceanarray.report(mooring=)``, ``oceanarray.export(mooring=)``,
       ``oceanarray.mooring.load(mooring=)``, ``oceanarray.mooring.check(mooring=)``
     - ``yaml_path=``
   * - ``oceanarray.process(mooring_dir=)``
     - ``paths.with_mooring(mooring_name, proc_dir=...) in oceanarray.process(yaml_path, paths)``
   * - ``oceanarray.process(raw_dir=)``
     - ``oceanarray.Paths(raw_dir=...) in oceanarray.process(yaml_path, paths)``
   * - ``oceanarray.process(caldip_dir=)``
     - ``oceanarray.Paths(caldip_dir=...) in oceanarray.process(yaml_path, paths)``
   * - ``oceanarray.run(mooring_dir=)``
     - ``paths.with_mooring(mooring_name, proc_dir=...) in oceanarray.run(yaml_path, paths)``
   * - ``oceanarray.run(raw_dir=)``
     - ``oceanarray.Paths(raw_dir=...) in oceanarray.run(yaml_path, paths)``
   * - ``oceanarray.run(caldip_dir=)``
     - ``oceanarray.Paths(caldip_dir=...) in oceanarray.run(yaml_path, paths)``
   * - ``oceanarray.report(mooring_dir=)``
     - ``paths.with_mooring(mooring_name, proc_dir=...) in oceanarray.report(yaml_path, paths)``
   * - ``oceanarray.report(proc_dir=)``
     - ``oceanarray.Paths(proc_dir=...) in oceanarray.report(array_yaml, paths, kind='array')``
   * - ``oceanarray.report(raw_dir=)``
     - ``oceanarray.Paths(raw_dir=...) in oceanarray.report(yaml_path, paths)``
   * - ``oceanarray.report(out_dir=)``
     - ``paths.with_mooring(mooring_name, report_dir=...) in oceanarray.report(yaml_path, paths)``
   * - ``oceanarray.report(report_dir=)``
     - ``oceanarray.Paths(report_dir=...) in oceanarray.report(yaml_path, paths)``
   * - ``oceanarray.report(pdf_dir=)``
     - ``oceanarray.report(yaml_path, paths, pdf=True), which writes the PDF beside the pages``
   * - ``oceanarray.export(mooring_dir=)``
     - ``paths.with_mooring(mooring_name, proc_dir=...) in oceanarray.export(yaml_path, paths)``
   * - ``oceanarray.export(out_dir=)``
     - ``oceanarray.Paths(export_dir=...) in oceanarray.export(yaml_path, paths)``
   * - ``oceanarray.export(array=)``
     - ``array_yaml=``
   * - ``oceanarray.mooring.load(proc_dir=)``
     - ``oceanarray.mooring.load(paths.yaml_path(mooring_name))``

The ``paths`` functions take ``mooring_name``
---------------------------------------------

A mooring's folders come from an ``oceanarray.Paths``; the module functions that
built them still work, with a ``DeprecationWarning``, until 0.8.0. The file-name
functions keep their first argument (the folder, or for ``stage_output_name`` the
mooring's name) and take the rest by keyword, the name as ``mooring_name=``;
change ``stage_file(folder, name, "microcat", serial, 3)`` to
``stage_file(folder, mooring_name=name, instrument="microcat", serial=serial, stage=3)``.
The old order and the old keyword still work, with one ``DeprecationWarning``,
until 0.8.0.

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Old form
     - Use instead
   * - ``oceanarray.paths.mooring_proc_dir``
     - ``oceanarray.Paths(proc_dir=...).mooring_dir(mooring_name)``
   * - ``oceanarray.paths.raw_mooring_dir``
     - ``oceanarray.Paths(raw_dir=...).raw_mooring_dir(mooring_name)``
   * - ``oceanarray.paths.resolve_report_dir``
     - ``oceanarray.Paths(report_dir=...).report_mooring_dir(mooring_name)``
       (``report_dir/<mooring_name>``), or
       ``paths.with_mooring(mooring_name, report_dir=folder)`` for an exact folder
       such as ``<mooring_dir>/report``
   * - ``oceanarray.paths.resolve_pdf_path``
     - ``paths.report_mooring_dir(mooring_name), where oceanarray.report(yaml_path, paths, pdf=True) writes <mooring_name>_report.pdf``
   * - ``oceanarray.paths.stage_file(positional arguments)``
     - ``keyword arguments: mooring_name=, instrument=, serial=, stage=``
   * - ``oceanarray.paths.product_file(positional arguments)``
     - ``keyword arguments: mooring_name=, product=``
   * - ``oceanarray.paths.stage_output_name(positional arguments)``
     - ``keyword arguments: serial=, stage=, tag=``
   * - ``oceanarray.paths.stage_file(name=)``,
       ``oceanarray.paths.product_file(name=)``,
       ``oceanarray.paths.stage_output_name(mooring=)``,
       ``oceanarray.paths.require_current_layout(mooring=)``
     - ``mooring_name=``
   * - ``oceanarray.paths.require_current_layout(proc_root=)``
     - ``proc_dir=``

One base for the package's errors
---------------------------------

Every error oceanarray raises now derives from ``OceanarrayError``
(``MooringError``, ``ReaderError``, ``StageError``, ``UnitsError``, ``OceanSitesError``,
``CaldipError``, ``LegacyLayoutError``); each keeps its built-in base, so ``except ValueError``
still works. A file a reader cannot read raises ``ReaderError``, and a problem in the
mooring YAML found while processing raises ``MooringError``; both were a plain
``ValueError``. ``StageError`` is no longer a ``MooringError``: code that caught a
wrong-stage file with ``except MooringError`` should catch ``StageError`` or
``OceanarrayError``. ``StageError`` and ``ValidationIssue`` are defined in
``oceanarray.exceptions``; import them from there (or ``StageError`` from
``oceanarray``).

Data warnings are log records
-----------------------------

Problems found while reading or reporting (records dropped for a missing time,
unexpected units, a stage file without its marker, an attribute dropped on writing)
were Python ``UserWarning`` s; they are now ``WARNING`` records of the ``oceanarray``
logger, so they also reach the stage log files. Code that caught them with
``warnings.catch_warnings`` or ``pytest.warns`` should read the log instead
(``caplog`` in pytest). Deprecations are still ``DeprecationWarning`` s.

A missing folder or mooring YAML raises
---------------------------------------

``MooringReport.generate`` and ``generate_recovery_table`` returned ``None``, and
``Stage2Processor`` / ``Stage3Processor.process_mooring`` returned ``False``, when the
processing folder or the mooring YAML was missing, printing the reason. They now raise
``MooringError`` with the path in the message. Replace a check for ``None`` or
``False`` with ``except MooringError``. The ``oceanarray`` commands already stopped
with an error; only the message changes.

No pitch or roll flags
----------------------

Tilt QC is on the geometric tilt only (``QC_TILT``, overridden by ``tilt_qc``), so
``qc_ranges: pitch`` / ``roll`` are no longer applied and stage 3 writes no
``pitch_qc`` / ``roll_qc``; the stack no longer carries them. A mooring YAML that
still sets them gets a warning; remove them, and set ``tilt_qc`` to change the
thresholds.

Grid levels are ``pmin_dbar`` and ``pmax_dbar``
-----------------------------------------------

A grid file now records its pressure range as ``pmin_dbar`` and ``pmax_dbar`` (with
``dp_dbar``, unchanged), the same words as ``pmin``/``pmax`` in the YAML, the call and
the CLI. Grids written before carry ``p_start_dbar`` / ``p_end_dbar``; oceanarray still
reads them (pages, the check whether a grid is current), but code of your own that reads
these attributes should read the new names, falling back to the old ones for older
files. The default levels (200 to 1000 dbar every 20 dbar) moved from
``oceanarray.pipeline._run.DEFAULTS``, which is gone, to
``oceanarray.config.parameters``: ``GRID_PMIN_DBAR``, ``GRID_PMAX_DBAR`` and
``GRID_DP_DBAR`` (and ``STACK_DT_SECONDS`` for the stack's time step).
