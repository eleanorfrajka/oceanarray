.. _configuration_files:

===================
Configuration files
===================

oceanarray reads a few YAML files. Each says *what* is being processed, not
*where* files are on a machine, so the same files work with any directory layout:
the raw-data folder, the processed folder and the bathymetry file come from the
call or the command line. Two keys of the mooring YAML still name a file relative
to the YAML, ``sensor_catalog`` and ``caldip.root``; both are planned to move to
the call and the command line.

.. list-table::
   :header-rows: 1
   :widths: 24 12 38 26

   * - File
     - One per
     - Holds
     - Read by
   * - ``{array}.array.yaml`` (:doc:`array_configuration`)
     - array
     - The moorings of one deployment year, their subarrays, the year, the
       project and the OceanSITES ``site_code``
     - the year page and ``export``; never processing
   * - ``{mooring}.mooring.yaml`` (:doc:`yaml_configuration`)
     - mooring
     - Everything needed to process the mooring, including one entry per
       instrument (the ``clamp`` list)
     - every stage and every page
   * - sensor catalog (:doc:`sensor_catalog`)
     - lab or project
     - What each instrument is, by serial: model, maker, sensor vocabularies
     - stage 1, for the sensor record

The package also ships tables of its own (``config/logging.yaml``,
``flag_meanings.yaml``); they are not edited by users.

Finding a mooring
-----------------

A mooring's name is always its ``name:`` key: every file it produces is named from
it. The name is never taken from the YAML's file name or its folder; those only
help find the file.
oceanarray supports two ways of working, and both are kept working:

- **Your own directory structure, by path.** Give the mooring YAML's path (``oceanarray run
  /data/configs/my_mooring.yaml --raw-dir /data/raw``, or ``read_mooring(path)``;
  ``--raw-dir`` is needed whenever stage 1 runs). The file can have
  any name; the stage files are written in the folder that holds it.
- **The prescribed directory structure, by name.** Keep each mooring's files in
  ``{proc_dir}/{name}/``, with its YAML as ``{name}.mooring.yaml``; then the name
  alone is enough (``oceanarray run M2_2016 --proc-dir proc``). A folder holding
  exactly one ``*.mooring.yaml`` can also be given instead of the name.

An array YAML lists its moorings by name. Each is found as ``{name}.mooring.yaml``
beside the array file or in its processed folder; a mooring whose YAML is named
differently gives that file name as ``config:`` (a file name only, no folders).

Each mooring YAML stands alone
------------------------------

A mooring YAML holds every setting for its mooring, so one file is enough to
reprocess it. Nothing is inherited at run time from the array YAML or any other
file: a setting shared by several moorings is written into each mooring's file.

The array YAML groups moorings that are already described. It decides what the
year page shows and supplies ``site_code`` and ``project`` when the moorings are
exported to OceanSITES; it never changes how a mooring is processed.

Subarrays
---------

A subarray is a named group of moorings within an array (for example the Cape
Farewell moorings of OSNAP East). It exists only in the array YAML, as a key of
``subarrays`` that each mooring entry may name, and is used only for display: the
year page colors and groups moorings by subarray. There is no subarray file, and
subarrays are not written into the data files.

Which value wins
----------------

A setting can come from four places. From lowest to highest priority:

1. the package default (``oceanarray.config.parameters``);
2. the mooring YAML;
3. the instrument's entry in the mooring YAML;
4. the call (a keyword argument) or the command-line flag.

For example, the stack time step is 3600 s by default; ``stack: {dt_seconds: 1800}``
in a mooring YAML sets 1800 s for that mooring, and ``--dt-seconds 600`` on the
command line overrides both for that run.

.. toctree::
   :maxdepth: 1

   yaml_configuration
   array_configuration
   sensor_catalog
