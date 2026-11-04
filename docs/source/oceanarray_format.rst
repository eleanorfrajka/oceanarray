==============================
OceanArray Data Format
==============================

oceanarray's shareable output is `OceanSITES 1.4
<https://www.ocean-ops.org/oceansites/docs/oceansites_data_format_reference_manual.pdf>`_
(:doc:`oceanSITES_manual`), with no departures from it.  This page lists what the export
writes; :doc:`methods/oceansites_export` lists the products and file names.  The stage,
stack and grid files the pipeline works with (``*_stage3.nc``, ``*_stack.nc``,
``*_grid.nc``) are oceanarray's own working files and are not OceanSITES files.

.. contents::
   :local:
   :depth: 1

Dimensions and coordinates
--------------------------

Manual §2.3–2.4.  No coordinate holds a fill value.

.. list-table::
   :header-rows: 1
   :widths: 15 25 60

   * - Name
     - Units
     - Value
   * - ``TIME``
     - ``days since 1950-01-01T00:00:00Z``
     - UTC; ``axis = "T"``.
   * - ``DEPTH``
     - ``m``, ``positive = "down"``
     - Deployment file: the nominal instrument depth (water depth minus height above
       bottom), or for an ADCP the nominal bin depths.  Gridded file: the pressure levels
       converted with ``gsw.z_from_p``.  The ``comment`` says which.  ``PRES`` carries the
       pressure, so the knock-down is in the data, not in ``DEPTH``.
   * - ``LATITUDE``, ``LONGITUDE``
     - ``degrees_north``, ``degrees_east``
     - WGS84 (``EPSG::4326``), one value, from the mooring description.

Data variables
--------------

Manual §2.5 and reference table 6.  Every parameter is on ``(TIME, DEPTH)``, with
``coordinates = "TIME DEPTH LATITUDE LONGITUDE"``, the stage-3 ``standard_name``,
``units`` (oceanarray's canonical spelling), ``long_name`` and valid range, and
``DM_indicator``.  Deployment files add ``ancillary_variables``, ``processing_level`` and
the ``sensor_*`` attributes (model, serial number, calibration date, mount, orientation,
data start and end).

.. list-table::
   :header-rows: 1
   :widths: 15 30 55

   * - Code
     - oceanarray variable
     - Note
   * - ``TEMP``
     - ``temperature``
     -
   * - ``CNDC``
     - ``conductivity``
     -
   * - ``PSAL``
     - ``salinity``
     -
   * - ``PRES``
     - ``pressure`` (ADCP: ``bin_pressure``)
     - measured, or interpolated from neighbouring instruments (flag 8); an ADCP also
       carries ``TRANSDUCER_PRES(TIME)``
   * - ``UCUR``, ``VCUR``, ``WCUR``
     - ``east_velocity``, ``north_velocity``, ``up_velocity``
     - ``WCUR`` is not in reference table 6; it is the common name
   * - ``CSPD``, ``CDIR``
     - ``current_speed``, ``current_direction``
     -
   * - ``DOX2``
     - ``dissolved_oxygen``
     - per unit mass
   * - ``TURB``
     - ``turbidity``
     - not in reference table 6

Variables without a code (beam velocities, amplitude, tilt, ``sigma0``) stay in the stage
files and are not exported.

Quality control
---------------

Manual §2.6 and reference table 2.  oceanarray's flags already use table 2, so a
deployment file's ``<PARAM>_QC`` holds them unchanged (byte, ``conventions = "OceanSITES
QC Flags"``), with 9 wherever the value is missing.  An ADCP's below-seabed flag, kept
apart in stage 3, is merged into each velocity flag where it failed or was suspect (the
worse flag is kept).  Gridded
files carry no ``_QC`` variables (§4.2.1).

.. list-table::
   :header-rows: 1
   :widths: 10 40

   * - Flag
     - Meaning
   * - 0
     - unknown
   * - 1
     - good_data
   * - 2
     - probably_good_data
   * - 3
     - potentially_correctable_bad_data
   * - 4
     - bad_data
   * - 7
     - nominal_value
   * - 8
     - interpolated_value
   * - 9
     - missing_value

Data mode
---------

Reference table 4.  ``P`` (provisional) unless the instrument entry in the mooring
description records ``D`` after a manual review, which is accepted only when the stage-3
file records the calibration-dip correction.  ``data_mode`` on the instrument entry is
one letter or a mapping from variable name to letter.  The global ``data_mode`` is the
single mode, or ``M`` when variables differ.  A grid records no per-input modes, so only
a ``P`` grid is exported.

Global attributes
-----------------

Manual §2.2.

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Attribute
     - Source
   * - ``platform_code``
     - ``platform_code`` in the mooring description (required; no ``_``)
   * - ``site_code``
     - ``site_code`` in the array description; left out when absent, never invented
   * - ``data_mode``
     - as above
   * - ``id``
     - the file name without ``.nc``; ``naming_authority = "OceanSITES"``
   * - ``format_version``, ``Conventions``
     - ``"1.4"``; ``"CF-1.13, OceanSITES-1.4, ACDD-1.3"``
   * - ``data_type``, ``cdm_data_type``, ``featureType``, ``source``
     - ``"OceanSITES time-series data"``, ``"Station"``, ``"timeSeries"``,
       ``"subsurface mooring"``
   * - ``geospatial_*``
     - position from the mooring description; vertical range from ``DEPTH``
   * - ``time_coverage_start``, ``_end``, ``_resolution``
     - from ``TIME`` (``YYYY-MM-DDThh:mm:ssZ``) and the sampling interval (ISO 8601)
   * - ``platform_deployment_*``, ``platform_recovery_*``
     - date, ship and cruise from the mooring description
   * - ``sea_floor_depth_below_sea_surface``
     - ``waterdepth``
   * - ``project``
     - the array description
   * - ``history``
     - the stage-3 or grid file's history, then a line naming the source file (and,
       for a gridded file, the stage files it was built from)

Attribution (``creator_*``, ``principal_investigator_*``, ``institution``,
``citation``, ``license``, ``references``) is not written yet; it will come from the
array description.
