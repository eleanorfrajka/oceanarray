============
Output files
============

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

Stage 1 cleans the people an imported file names (its ``originator_*``
attributes stay as given) and writes them as OceanSITES 1.4 lists:
``principal_investigator``, ``_email``, ``_url``, ``_id`` separated by commas, and
``contributor_name``, ``_role``, ``_email`` separated by semicolons. Every parallel
attribute has one entry per person and role (empty entries kept), and a contributor with two
roles takes two entries. Reading an originator's lists:

- each field is split on its own separator (``;`` when it has one, else ``,``);
- titles before or after a name are dropped (``"Dr. Fiamma Straneo, Professor"``
  is one person, ``Fiamma Straneo``); ``"Surname, Given"`` is written ``"Given
  Surname"``;
- a single role for several names goes to the first name, the others take a
  blank role (a warning; a role is never copied to every name); any other role,
  email, url or id without one entry per name is left out, with a warning (never
  guessed); a role joined by "and" is two roles, each its own entry (``"data
  processing and interpretation"``), and a principal-investigator role reads ``PI``;
- the file's creator and publisher join the contributors (roles ``originator`` and
  ``publisher``); a file with no ``principal_investigator`` takes its PIs from the
  contributors whose role says principal investigator or PI;
- across files, two entries are one person when they share an id, or a name
  (titles, initials, case, dots and spaces aside), so a changed or mistyped email
  does not split a person; the newest email is kept; a blank-role entry merges
  into the same person's other entry.

``contributor_name``, ``_role`` and ``_email`` are written whenever there is a
contributor, an empty field as empty entries (``"; ; "``), so the three always
show the same count.

The stack and the concatenated stack join these lists without repeats; they read
them strictly and never clean them again. A stage file whose stage 1 predates this
cleaning (no ``stage1_oceanarray_version``) gives no people: the stack names it in
``people_not_cleaned`` and asks for a rerun from stage 1, and ``oceanarray concat``
refuses such a stack. An empty list from a newer stage 1 means the source named no
one (the RODB files); the mooring YAML's ``people:`` block then gives them, with a
history line naming its ``people_source``.

Where the data came from
~~~~~~~~~~~~~~~~~~~~~~~~

``source_doi`` (bare), ``source_citation``, ``source_license``,
``source_acknowledgement``, ``source_references`` and ``source_note`` say where the
data came from: the imported file's own values, filled field by field from the
mooring YAML's ``source:`` block. Stage 1 writes them and every product carries them.
The Stack cat keeps one value per deployment (``deployment_source_*`` on
``DEPLOYMENT``) and the joined, de-duplicated values as globals; deployments under
different licenses are all kept, with a warning. Each stack, Stack cat, grid and Grid
cat has a ``citation`` of its own — the product, the oceanarray version, "Derived
from:" each source (its DOI, else its citation) and "Users must also abide by the
conditions of the source licenses." — and ``references`` lists the source DOIs
(``doi:``) and reference URLs.

Sensor records
~~~~~~~~~~~~~~

Each instrument is described by one dimensionless variable,
``SENSOR_<type>_<serial>`` (every MicroCAT ``SENSOR_CTD_<serial>``), with the OG1
attributes ``sensor_type`` (and ``sensor_type_vocabulary``), ``sensor_maker``,
``sensor_model``, ``sensor_serial_number`` and ``sensor_calibration_date``; each
data variable names its record in ``sensor``. A Sea-Bird header's calibrations are
kept per sensor: ``sensor_calibration_date`` is the latest, ``sensor_calibration_dates``
lists each (``"temperature: 2023-03-28; conductivity: 2023-03-28"``), and
``<sensor>_calibration_coefficients`` holds each sensor's coefficients. An imported
file's own ``sensor_*`` attributes are kept on the record as ``originator_sensor_*``
(values that differ between its variables joined by ``"; "``). The model comes from
the mooring YAML's ``sensor_model`` (with its ``sensor_model_source``), else the file,
else ``UNK``; ``sensor_metadata_source`` says which. Nothing is inferred from the data.
The stack keeps the records and names each level's in ``sensor(N_LEVELS)``; the Stack
cat ends each record's name with the deployment code (``SENSOR_CTD_13220_201806``),
so an instrument recalibrated between deployments has a record per deployment.

Files behind a product
~~~~~~~~~~~~~~~~~~~~~~

The stack records each level's stage file and its SHA-256 (``stage_file``,
``stage_sha256`` on ``N_LEVELS``, paths relative to the mooring directory); the grid
records its stack (``stack_file``, ``stack_sha256``); the Stack cat records each
deployment's stack (``stack_file``, ``stack_sha256`` on ``DEPLOYMENT``) and the Grid
cat its Stack cat (``stackcat_file``, ``stackcat_sha256``). ``oceanarray run``
compares these to keep a stack or grid that is still current. A stack built from a
mooring YAML with a ``people:`` or ``source:`` block also records
``people_source_sha256``, the SHA-256 of the two blocks, so a later edit is noticed.

Which oceanarray wrote a file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every file oceanarray writes (stage files, stack, grid, Stack cat, OceanSITES
export) carries ``oceanarray_version``. When the package runs from a git checkout,
the version names the commit that ran: ``0.6.0.post1.dev5+gf52121d`` (``.dirty``
appended with uncommitted changes); development builds before 0.6.1 wrote it in a
separate ``oceanarray_commit``. Each stage also keeps
``stage<n>_oceanarray_version``, so a stage-3 file still says which code ran
stage 1. ``stage2_source_sha256`` is the full SHA-256 of the stage-2 file.

Every units change (a new spelling or a conversion) is recorded in the file's
``units_converted`` attribute, JSON ``{variable: {"from", "to", "factor"}}``,
written at stage 1 and carried forward.

Where a mooring-level value came from, or why it was corrected, is written beside
it when the mooring YAML says so: ``waterdepth_source``, ``deployment_time_source``,
``recovery_time_source``, ``latitude_source``, ``longitude_source`` (stage 1 adds a
history line, e.g. ``waterdepth 3125.0 m from the mooring YAML: <source>``). The
stack carries them, and the Stack cat lists them per deployment beside the record
variable (``deployment_waterdepth_source``, ``deployment_time_source``, ...; an
empty string for a deployment that gives none). The values themselves are the
YAML's, as without a source.

Every grid records the QC flags it left out before gridding in ``grid_mask_flags``
(JSON, ``{"default": [4, 9], "velocity": [3, 4, 9]}``). A site grid
(``<site>_gridcat.nc``) also records its site stack (``stackcat_file``,
``stackcat_sha256``) and the along-slope direction (``slope_angle_deg``, degrees
clockwise from north, with ``slope_angle_source``). The rest
of the attribution (``creator_*``, ``institution``, ``citation``, ``license``,
``references``) is not written yet; it will come from the array description.
