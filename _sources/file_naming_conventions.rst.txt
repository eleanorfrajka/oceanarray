.. _file_naming_conventions:

==============================
File Naming Conventions
==============================

This page covers two sets of naming conventions:

1. **Raw instrument files** — files downloaded from the instruments at sea, whose
   names are fixed (or conventionally chosen) at the time of download.
2. **Processed output files** — NetCDF, log, and report files written by
   ``oceanarray``.

----

Raw instrument files
--------------------

Instruments are downloaded twice per deployment cycle: once during a
calibration-dip (caldip) cast and once at mooring recovery.  UHH uses the
following filename conventions for the downloaded data files.

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Context
     - Filename pattern
   * - Mooring recovery download
     - ``{serial}_recovery.<ext>``
   * - Caldip cast download
     - ``{serial}_cal_dip_data.<ext>``

where ``{serial}`` is the instrument serial number and ``<ext>`` depends on
the instrument type and firmware:

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Instrument
     - Extension(s)
     - Notes
   * - SBE MicroCAT (old Seaterm)
     - ``.asc``
     - firmware below the ``microcat_firmware_sentinel`` in
       ``logsheet_config.yaml``
   * - SBE MicroCAT (SeatermV2)
     - ``.xml``
     - firmware at or above the sentinel, or any MicroCAT with dissolved oxygen
   * - Nortek Aquadopp
     - ``.aqd``, ``.hdr``, ``.sen``, ``.prf``
     - all four files are downloaded together; ``filename:`` in the YAML
       points to the ``.aqd`` file
   * - RBR soloT / RBRduet
     - ``.rsk``
     - RSK SQLite database downloaded via Ruskin
   * - RBR TR-1050
     - ``.hex``
     - hex format
   * - RDI WorkHorse ADCP
     - ``.000``, ``.001``, …
     - binary raw files; ``filename:`` points to the first file

These conventions are recorded in the ``filename_conventions:`` section of
``logsheet_config.yaml`` and are used by the standalone
`logsheet <https://github.com/ocean-uhh/logsheet>`_ package to pre-fill
expected filenames on the download logsheets.

----

Processed output files
----------------------

All output files use the mooring name and, where applicable, the instrument
serial number from the YAML.

.. list-table::
   :header-rows: 1
   :widths: 38 62

   * - File
     - Description
   * - ``{mooring}_{serial}_stage1.nc``
     - Raw-faithful CF-NetCDF; no QC applied
   * - ``{mooring}_{serial}_stage2.nc``
     - Trimmed to deployment window; clock correction applied
   * - ``{mooring}_{serial}_stage3.nc``
     - QC flags added; derived variables (e.g. salinity); Aquadopp velocity in
       earth coordinates; magnetic declination correction applied
   * - ``{mooring}_stack.nc``
     - All instruments resampled onto a common time grid and stacked into a
       single file ordered by depth
   * - ``{mooring}_grid.nc``
     - Stack file interpolated onto a regular pressure grid
   * - ``{mooring}_{timestamp}_{stage}.mooring.log``
     - Processing log for one run of one stage; timestamp format is
       ``YYYYMMDD_HHMMSS``
   * - ``{mooring}_report.html``
     - HTML summary report for the whole mooring
   * - ``{mooring}_stack_report.html``
     - HTML report for the stacked dataset
   * - ``{mooring}_grid_report.html``
     - HTML report for the gridded dataset
   * - ``{mooring}_{serial}_report.html``
     - Per-instrument HTML report

The ``{serial}`` component uses the value of the ``serial`` field in the
YAML instrument entry.  If the serial contains a trailing ``*`` (used in
some instrument identifiers), the ``*`` is stripped from the filename.

The ``{mooring}`` component is the value of the ``name`` field at the top
of the mooring YAML, which must also match the directory name under
``{proc_dir}`` and the YAML filename itself::

   {proc_dir}/{mooring}/{mooring}.mooring.yaml

----

See also
--------

- :doc:`directory_structure` — where these files live on disk
- :doc:`yaml_configuration` — YAML fields that control filenames (``name:``,
  ``serial:``, ``filename:``)
- `logsheet package <https://github.com/ocean-uhh/logsheet>`_ — how ``logsheet_config.yaml``
  filename conventions connect raw-file naming to the logsheet workflow
