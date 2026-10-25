==========================================
OceanArray Data Format Specification
==========================================

This specification defines the preferred final format for data output from the oceanarray processing pipeline. It builds upon the OceanSITES data format while incorporating elements from the OG1 (OceanGliders) format and making specific choices for oceanographic mooring data.

For detailed reference, see the complete :doc:`oceanSITES_manual`.

.. contents:: Table of Contents
   :local:
   :depth: 3

1. Overview & Context
=====================

The oceanarray format follows NetCDF-4 conventions with CF (Climate and Forecast) metadata standards. This format ensures interoperability while optimizing for oceanographic time series data from moored instruments.

1.1 Relationship to Standards
------------------------------

**OceanSITES Foundation**: This format builds upon the `OceanSITES Data Format Reference Manual v1.4 <https://www.ocean-ops.org/oceansites/docs/oceansites_data_format_reference_manual.pdf>`_. See our complete :doc:`oceanSITES_manual` for reference.

**OG1 Elements**: Incorporates select patterns from the OceanGliders format (OG1), particularly for contributor metadata and time formatting.

**CF Compliance**: Maintains strict adherence to `CF Conventions <http://cfconventions.org/>`_ and `UDUNITS-2 <https://docs.unidata.ucar.edu/udunits/current/>`_ standards.

2. Key Design Decisions
=======================

.. warning::
   The following choices differ from referenced standards and may need future revision based on community feedback.

2.1. Deviations from OceanSITES Standard
-----------------------------------------

.. list-table:: Format Deviations Summary
   :widths: 25 35 40
   :header-rows: 1

   * - Aspect
     - OceanSITES Standard
     - OceanArray Choice
   * - Vertical Dimension
     - ``DEPTH``
     - ``N_LEVELS`` (follows `CCHDO <https://cchdo.ucsd.edu>`_)
   * - Units Format
     - ``degrees_north``
     - ``degree_north`` (`UDUNITS <https://www.unidata.ucar.edu/software/udunits>`_)
   * - Time Format
     - ``YYYY-MM-DDThh:mm:ssZ``
     - ``YYYYmmddTHHMMss`` (`OG1 <https://oceangliderscommunity.github.io/OG-format-user-manual/OG_Format.html>`_ style)

**Rationale for N_LEVELS**: The `CCHDO <https://cchdo.ucsd.edu>`_ community uses ``N_LEVELS`` as the standard vertical dimension name, providing better alignment with hydrographic data processing workflows.

**Rationale for UDUNITS**: Strict adherence to UDUNITS ensures compatibility with scientific software packages and avoids ambiguity in unit interpretation.

**Rationale for Time Format**: The OG1 time format provides a more compact representation suitable for attribute strings while maintaining ISO 8601 compatibility.

3. File Organization & Naming
==============================

3.1. File Naming Convention
----------------------------

Files follow the OceanSITES naming pattern with oceanarray-specific modifications:

**Basic Pattern**: ``OS_[PLATFORM]_[DEPLOYMENT]_[MODE]_[PARAMS].nc``

**Components**:
- ``OS`` = OceanSITES prefix (maintains compatibility)
- ``[PLATFORM]`` = Platform identifier (e.g., "CIS-1")
- ``[DEPLOYMENT]`` = Deployment code (e.g., "200502" for Feb 2005)
- ``[MODE]`` = Data mode: R (real-time), P (provisional), D (delayed-mode)
- ``[PARAMS]`` = Parameter identifier (e.g., "CTD", "ADCP")

**Examples**:
- ``OS_CIS-1_200502_D_CTD.nc`` - Delayed-mode CTD data
- ``OS_PAPA_201505_D_ADCP.nc`` - Delayed-mode current data

**Reference**: See OceanSITES file naming in :ref:`oceansites-file-naming`.

4. Global Attributes
====================

**Requirement Status (RS)**: M = Mandatory, HD = Highly Desired, S = Suggested

**Reference**: See complete OceanSITES global attributes in :ref:`oceansites-global-attributes`.

Global attribute names are case sensitive.

4.1. Discovery and Identification
----------------------------------

.. list-table:: Discovery and Identification Attributes
   :widths: 25 45 20 10
   :header-rows: 1

   * - Attribute
     - Definition
     - Example
     - RS
   * - ``site_code``
     - OceanSITES site name where platform installed
     - ``"CIS"``
     - M
   * - ``platform_code``
     - Unique platform identifier
     - ``"CIS-1"``
     - M
   * - ``data_mode``
     - Data quality mode: R/P/D (typically D for oceanarray) - see :ref:`oceanarray-data-mode`
     - ``"D"``
     - M
   * - ``title``
     - Free-format dataset description
     - ``"Processed CIS-1 Mooring Time Series Data"``
     - HD
   * - ``summary``
     - Detailed description (up to 100 words) for data discovery
     - ``"Oceanographic mooring data processed through oceanarray pipeline..."``
     - HD
   * - ``id``
     - Unique dataset identifier (often filename without .nc)
     - ``"OS_CIS-1_L2_200502_TS"``
     - HD
   * - ``naming_authority``
     - Organization managing dataset names
     - ``"OceanSITES"``
     - HD
   * - ``wmo_platform_code``
     - WMO identifier unique within OceanSITES project
     - ``"48409"``
     - HD
   * - ``source``
     - Platform type from `SeaVoX L06 vocabulary <https://vocab.nerc.ac.uk/collection/L06/current/>`_
     - ``"subsurface mooring"``
     - HD
   * - ``theme``
     - OceanSITES theme areas (comma-separated) - see :ref:`oceansites-organizational-model`
     - ``"Air/sea flux reference, Global Ocean Watch"``
     - HD
   * - ``format_version``
     - OceanArray format version
     - ``"OceanArray-1.0"``
     - M
   * - ``data_type``
     - OceanSITES data type (maintains compatibility)
     - ``"OceanSITES time-series data"``
     - M

4.2. Provenance
-----------------------------

.. warning::
   **DEVIATION from OceanSITES**: This section consolidates OceanSITES ``creator_*`` (creator_name, creator_email, creator_url, creator_institution) and ``principal_investigator_*`` (principal_investigator_name, principal_investigator_email, principal_investigator_institution) fields into unified ``contributor_*`` attributes. This follows the OG1 pattern which provides more flexible support for multiple contributors with defined roles, rather than having separate creator and PI fields. The OceanArray approach allows for clearer attribution of different contributor roles (PI, Data scientist, Operator, etc.) within a single, consistent metadata structure.

Consolidates OceanSITES ``creator_*`` and ``principal_investigator_*`` fields into unified ``contributor_*`` attributes supporting multiple contributors following OG1 patterns.

.. list-table:: Contributor Attributes
   :widths: 25 45 20 10
   :header-rows: 1

   * - Attribute
     - Definition
     - Example
     - RS
   * - ``contributor_name``
     - Name of the contributors to the oceanographic mission. Multiple contributors are separated by commas. PI name is mandatory.
     - ``"Jane Smith, John Doe"``
     - M
   * - ``contributor_email``
     - Email of the contributors to the oceanographic mission. Multiple contributors' emails are separated by commas. PI email is mandatory.
     - ``"jane.smith@whoi.edu, john.doe@noc.ac.uk"``
     - M
   * - ``contributor_id``
     - Unique id of the contributors to the oceanographic mission. Multiple contributors' ids are separated by commas.
     - ``"https://orcid.org/0000-0001-2345-6789, https://orcid.org/0000-0002-3456-7890"``
     - HD
   * - ``contributor_role``
     - Role of the contributors to the oceanographic mission. Multiple contributors' roles are separated by commas. PI vocabulary is mandatory.
     - ``"PI, Data scientist"``
     - M
   * - ``contributor_role_vocabulary``
     - Controlled vocabulary for the roles used in the "contributor_role". Multiple contributors' roles and vocabularies are separated by commas. PI vocabulary is mandatory.
     - ``"http://vocab.nerc.ac.uk/collection/W08/"``
     - M
   * - ``contributing_institutions``
     - Names of institutions involved in the oceanographic mission. Multiple institutions are separated by commas. Operator is mandatory.
     - ``"University of Hamburg Institute of Oceanography (IfM), National Oceanography Centre (Southampton) (NOC)"``
     - M
   * - ``contributing_institutions_vocabulary``
     - URL to the repository of the institution id. Multiple vocabularies are separated by commas.
     - ``"https://edmo.seadatanet.org/report/1156, https://edmo.seadatanet.org/report/17"``
     - HD
   * - ``contributing_institutions_role``
     - Role of the institutions involved in the oceanographic mission. Multiple institutions' roles are separated by commas. Operator role is mandatory.
     - ``"Operator, Owner"``
     - M
   * - ``contributing_institutions_role_vocabulary``
     - The controlled vocabulary of the role used in the institution's role. Multiple vocabularies are separated by commas. Operator vocabulary is mandatory.
     - ``"https://vocab.nerc.ac.uk/collection/W08/current/"``
     - M

**Standard Contributor Roles**: ``Data scientist``, ``Manufacturer``, ``PI``, ``Technical Coordinator``, ``Operator``, ``Owner``

**Provenance and Data History**

.. list-table:: Provenance Attributes
   :widths: 25 45 20 10
   :header-rows: 1

   * - Attribute
     - Definition
     - Example
     - RS
   * - ``date_created``
     - ``YYYYmmddTHHMMss`` (creation timestamp)
     - ``"20241024T143000"``
     - M
   * - ``date_modified``
     - ``YYYYmmddTHHMMss`` (last modification)
     - ``"20241024T143000"``
     - HD
   * - ``history``
     - Provides an audit trail for modifications to the original data. Each line should begin with a timestamp, and include user name, modification name, and modification arguments. The timestamp should follow YYYYmmddTHHMMss format.
     - ``"2012-04-11T08:35:00Z data collected, A. Meyer. 2013-04-12T10:11:00Z OceanSITES file with provisional data compiled and sent to DAC, A. Meyer."``
     - HD
   * - ``processing_level``
     - Processing level from :ref:`oceansites-processing-levels`
     - ``"Data verified against model or other contextual information"``
     - HD
   * - ``QC_indicator``
     - A value valid for the whole dataset, one of: 'unknown' – no QC done, no known problems 'excellent' - no known problems, all important QC done 'probably good' - validation phase 'mixed' - some problems, see variable attributes
     - ``"excellent"``
     - HD

4.3. Geospatial-Temporal Attributes
------------------------------------

.. list-table:: Geospatial and Temporal Coverage Attributes
   :widths: 25 45 20 10
   :header-rows: 1

   * - Attribute
     - Definition
     - Example
     - RS
   * - ``geospatial_lat_min``
     - Minimum latitude of dataset coverage
     - ``"26.5"``
     - M
   * - ``geospatial_lat_max``
     - Maximum latitude of dataset coverage
     - ``"26.5"``
     - M
   * - ``geospatial_lon_min``
     - Minimum longitude of dataset coverage
     - ``"-75.2"``
     - M
   * - ``geospatial_lon_max``
     - Maximum longitude of dataset coverage
     - ``"-75.2"``
     - M
   * - ``geospatial_vertical_min``
     - Minimum depth/elevation of dataset coverage
     - ``"10.0"``
     - M
   * - ``geospatial_vertical_max``
     - Maximum depth/elevation of dataset coverage
     - ``"4000.0"``
     - M
   * - ``geospatial_vertical_positive``
     - Indicates which direction is positive; "up" means that z represents height, while "down" means that z represents pressure or depth. If not specified then "down" is assumed. (ACDD)
     - ``"down"``
     - HD
   * - ``geospatial_vertical_units``
     - Units of depth, pressure, or height. If not specified then "meter" is assumed. (ACDD)
     - ``"meter"``
     - S
   * - ``time_coverage_start``
     - ``YYYYmmddTHHMMss`` (OG1 format) **[DEVIATION from OceanSITES]**
     - ``"20050201T120000"``
     - M
   * - ``time_coverage_end``
     - ``YYYYmmddTHHMMss`` (OG1 format) **[DEVIATION from OceanSITES]**
     - ``"20060201T120000"``
     - M
   * - ``time_coverage_duration``
     - ISO 8601 duration format (examples: P1Y, P3M, P415D, P1Y1M3D)
     - ``"P1Y1M3D"``
     - HD
   * - ``featureType``
     - CF Discrete Sampling Geometry type (timeSeries or timeSeriesProfile)
     - ``"timeSeries"``
     - HD
   * - ``platform_deployment_date``
     - Date and time in ISO format of platform deployment
     - ``"2010-02-20T00:00:00Z"``
     - S
   * - ``platform_deployment_ship_name``
     - Ship name for deployment (see https://ocean.ices.dk/codes/ShipCodes.aspx)
     - ``"R/V Melville"``
     - S
   * - ``platform_deployment_cruise_name``
     - Cruise name for deployment (may be found on operators' sites or rvdata.us)
     - ``"MV1406"``
     - S
   * - ``platform_deployment_ship_ICES_code``
     - ICES ship code for deployment vessel
     - ``"318M"``
     - S
   * - ``platform_deployment_cruise_ExpoCode``
     - ICES ship code plus cruise start date for deployment
     - ``"318M20100220"``
     - S
   * - ``platform_recovery_date``
     - Date and time in ISO format of platform recovery
     - ``"2012-01-13T00:00:00Z"``
     - S
   * - ``platform_recovery_ship_name``
     - Ship name for recovery (see https://ocean.ices.dk/codes/ShipCodes.aspx)
     - ``"R/V Endeavor"``
     - S
   * - ``platform_recovery_cruise_name``
     - Cruise name for recovery (may be found on operators' sites or rvdata.us)
     - ``"EN472"``
     - S
   * - ``platform_recovery_ship_ICES_code``
     - ICES ship code for recovery vessel
     - ``"32EV"``
     - S
   * - ``platform_recovery_cruise_ExpoCode``
     - ICES ship code plus cruise start date for recovery
     - ``"32EV20120113"``
     - S

**Time Format Rationale**: The compact ``YYYYmmddTHHMMss`` format reduces attribute string length while maintaining human readability and ISO 8601 compatibility.

**File dates**: The file dates, date_created and date_modified, are our interpretation of the file dates as defined by ACDD. Date_created is the time stamp on the file, date_modified may be used to represent the 'version date' of the geophysical data in the file. The date_created may change when e.g. metadata is added or the file format is updated, and the optional date_modified MAY be earlier.

**Geospatial extents**: (geospatial_lat_min, max, and lon_min, max) are preferred to be stored as strings for use in the GDAC software, however numeric fields are acceptable. This information is linked to the site information, and may not be specific to the platform deployment.

4.4. Publication Information
-----------------------------

.. list-table:: Publication and Update Attributes
   :widths: 25 45 20 10
   :header-rows: 1

   * - Attribute
     - Definition
     - Example
     - RS
   * - ``update_interval``
     - Frequency of dataset updates
     - ``"void"``
     - M
   * - ``references``
     - Published or web-based references that describe the data or methods used to produce it. Include a reference to OceanSITES and a project-specific reference if appropriate.
     - ``"http://www.oceansites.org, http://www.noc.soton.ac.uk/animate/index.php"``
     - HD
   * - ``license``
     - A statement describing the data distribution policy; it may be a project- or DAC-specific statement, but must allow free use of data. OceanSITES has adopted the CLIVAR data policy. (ACDD)
     - ``"Follows CLIVAR (Climate Variability and Predictability) standards, cf. http://www.clivar.org/resources/data/data-policy. Data available free of charge. User assumes all risk for use of data. User must display citation in any publication or product using data. User must contact PI prior to any commercial use of data."``
     - S
   * - ``citation``
     - The citation to be used in publications using the dataset; should include a reference to OceanSITES, the name of the PI, the site name, platform code, data access date, time, and URL, and, if available, the DOI of the dataset.
     - ``"These data were collected and made freely available by the OceanSITES program and the national programs that contribute to it."``
     - S
   * - ``acknowledgement``
     - A place to acknowledge various types of support for the project that produced this data. (ACDD)
     - ``"Principal funding for the NTAS experiment is provided by the US NOAA Climate Observation Division."``
     - S

5. Dimensions
=============

NetCDF dimensions provide information on the size of the data variables, and additionally tie coordinate variables to data. CF recommends that if any or all of the dimensions of a variable have the interpretations of "date or time" (T), "height or depth" (Z), "latitude" (Y), or "longitude" (X) then those dimensions should appear in the relative order T, Z, Y, X in the variable's definition (in the CDL).

**Reference**: See OceanSITES dimensions in :ref:`oceansites-dimensions`.

.. warning::
   **DEVIATION from OceanSITES**: OceanArray uses ``N_LEVELS`` instead of the OceanSITES standard ``DEPTH`` dimension to align with `CCHDO <https://cchdo.ucsd.edu>`_ (CLIVAR and Carbon Hydrographic Data Office) conventions used in hydrographic data processing workflows. This reduces confusion between a dimension of DEPTH when the instruments are recording pressure.

.. list-table:: OceanArray Dimensions
   :widths: 20 20 50 10
   :header-rows: 1

   * - Name
     - Type
     - Definition
     - RS
   * - ``TIME``
     - Unlimited
     - Number of time steps. Example: for a mooring with one value per day and a mission length of one year, TIME contains 365 time steps.
     - M
   * - ``N_LEVELS``
     - Fixed
     - **[DEVIATION from OceanSITES]** Number of depth levels. Used instead of OceanSITES ``DEPTH`` to align with `CCHDO <https://cchdo.ucsd.edu>`_ conventions. Example: for a mooring with measurements at nominal depths of 0.25, 10, 50, 100 and 200 meters, N_LEVELS=5.
     - M
   * - ``LATITUDE``
     - Fixed (=1)
     - Dimension of the LATITUDE coordinate variable.
     - M
   * - ``LONGITUDE``
     - Fixed (=1)
     - Dimension of the LONGITUDE coordinate variable.
     - M

**Dimension Ordering**: Following OceanSITES convention, dimensions should be ordered as ``(TIME, N_LEVELS, Y, X)``. CF recommends T,Z,Y,X order in variable definitions.

6. Coordinate Variables
=======================

NetCDF coordinates are a special subset of variables. Coordinate variables orient the data in time and space; they may be dimension variables or auxiliary coordinate variables (identified by the 'coordinates' attribute on a data variable).

**Reference**: See OceanSITES coordinate variables in :ref:`oceansites-coordinates`.

.. list-table:: OceanArray Coordinate Variable Specifications
   :widths: 15 15 60 10
   :header-rows: 1

   * - Variable
     - Dimension
     - Attributes and Requirements
     - RS
   * - ``TIME``
     - ``TIME``
     - | **data type**: double
       | **long_name** = "Time elapsed since 1970-01-01T00:00:00Z"
       | **calendar** = "gregorian"
       | **units** = "seconds since 1970-01-01T00:00:00Z"
       | **axis** = "T"
       | **_FillValue** = -1.0
       | **valid_min** = 1e9, **valid_max** = 4e9
       | **ancillary_variables** = "TIME_QC"
       | **interpolation_methodology** = ""
     - M
   * - ``LONGITUDE``
     - ``LONGITUDE``
     - | **data type**: double
       | **long_name** = "longitude of measurement location"
       | **standard_name** = "longitude"
       | **units** = "degrees_east"
       | **axis** = "X"
       | **_FillValue** = -9999.9
       | **valid_min** = -180.0, **valid_max** = 180.0
       | **ancillary_variables** = "LONGITUDE_QC"
       | **interpolation_methodology** = ""
     - HD
   * - ``LATITUDE``
     - ``LATITUDE``
     - | **data type**: double
       | **long_name** = "Latitude north (WGS84)"
       | **standard_name** = "latitude"
       | **units** = "degrees_north"
       | **axis** = "Y"
       | **_FillValue** = -9999.9
       | **valid_min** = -90.0, **valid_max** = 90.0
       | **ancillary_variables** = "LATITUDE_QC"
       | **interpolation_methodology** = ""
     - HD
   * - ``PRESSURE``
     - ``N_LEVELS``
     - | **data type**: double
       | **long_name** = "Pressure below surface of the water body"
       | **standard_name** = "sea_water_pressure"
       | **units** = "dbar"
       | **axis** = "Z"
       | **positive** = "down"
       | **_FillValue** = -9999.9
       | **valid_min** = 0.0, **valid_max** = 10000.0
       | **ancillary_variables** = "PRESSURE_QC"
       | **interpolation_methodology** = ""
     - HD

**Note**: ``PRESSURE`` coordinate variable uses the ``N_LEVELS`` dimension, following oceanographic convention where instruments typically measure pressure rather than depth directly.

7. Data Variables & Quality Control
====================================

7.1. Data Variable Structure
-----------------------------

Data variables contain the actual measurements and information about their quality, uncertainty, and mode by which they were obtained.

**Reference**: See :ref:`OceanSITES data variables <oceansites-25-data-variables>` in the OceanSITES manual.

**Standard Structure**:

.. code-block:: none

   Float <PARAM>(TIME, N_LEVELS);
   <PARAM>:standard_name = <CF_NAME>;
   <PARAM>:units = <UDUNITS_STRING>;
   <PARAM>:_FillValue = <VALUE>;
   <PARAM>:long_name = <DESCRIPTION>;

7.2. Quality Control Variables
-------------------------------

Each measured parameter should have an associated quality control variable with suffix "_QC".

**Reference**: See :ref:`OceanSITES QC variables <oceansites-26-qc-variables>` in the OceanSITES manual.

**QC Structure**:

.. code-block:: none

   Byte <PARAM>_QC(TIME, N_LEVELS);
   <PARAM>_QC:long_name = "quality flag for <PARAM>";
   <PARAM>_QC:flag_values = 0, 1, 2, 3, 4, 7, 8, 9;
   <PARAM>_QC:flag_meanings = "unknown good_data probably_good_data potentially_correctable_bad_data bad_data nominal_value interpolated_value missing_value";

8. Reference Tables
===================

8.1. Units
-----------

All units must follow the UDUNITS-2 standard:

.. list-table:: Common Unit Specifications
   :widths: 25 25 50
   :header-rows: 1

   * - Quantity
     - UDUNITS Format
     - Notes
   * - Latitude
     - ``degree_north``
     - **[DEVIATION]** OceanSITES uses ``degrees_north`` (plural)
   * - Longitude
     - ``degree_east``
     - **[DEVIATION]** OceanSITES uses ``degrees_east`` (plural)
   * - Temperature
     - ``degree_Celsius``
     - Preferred over ``degC``
   * - Pressure
     - ``decibar``
     - Standard oceanographic unit
   * - Depth
     - ``meter``
     - Standard SI unit
   * - Salinity
     - ``1`` or ``psu``
     - Dimensionless or practical salinity units
   * - Current Speed
     - ``meter second-1``
     - SI derived unit
   * - Current Direction
     - ``degree``
     - Angular measurement
   * - Ocean Transport
     - ``sverdrup``
     - 1 Sv = 10^6 m³/s (note: not ``sv`` to avoid conflict with sievert)

**Reference**: See :ref:`OceanSITES reference tables <oceansites-3-reference-tables>` in the OceanSITES manual.

8.2. QC Flag Values
--------------------

**Reference**: See :ref:`OceanSITES QC flags <oceansites-32-qc-flags>` in the OceanSITES manual.

.. list-table:: QC Flag Meanings
   :widths: 10 30 60
   :header-rows: 1

   * - Flag
     - Meaning
     - Description
   * - 0
     - unknown
     - No QC was performed
   * - 1
     - good_data
     - All QC tests passed
   * - 2
     - probably_good_data
     - Data are probably good
   * - 3
     - potentially_correctable_bad_data
     - Data are not to be used without scientific correction
   * - 4
     - bad_data
     - Data have failed one or more tests
   * - 7
     - nominal_value
     - Data were not observed but reported (e.g. instrument target depth)
   * - 8
     - interpolated_value
     - Missing data interpolated from neighboring data
   * - 9
     - missing_value
     - Fill value

8.3. Processing Levels
-----------------------

**Reference**: See :ref:`OceanSITES processing levels <oceansites-processing-levels>` in the OceanSITES manual.

Standard processing level descriptions for the ``processing_level`` attribute:

* Raw instrument data
* Instrument data that has been converted to geophysical values
* Post-recovery calibrations have been applied
* Data has been scaled using contextual information
* Known bad data has been replaced with null values
* Data manually reviewed
* Data verified against model or other contextual information

.. _oceanarray-data-mode:

8.4. OceanSITES 1.4 Reference Table 4: Data Mode
--------------------------------------------------

The values for the variables ``<PARAM>_DM``, the global attribute ``data_mode``, and variable attributes ``<PARAM>:DM_indicator`` are defined as follows:

.. list-table:: Data Mode Values
   :widths: 10 20 70
   :header-rows: 1

   * - Value
     - Meaning
     - Description
   * - R
     - Real-time data
     - Data coming from the (typically remote) platform through a communication channel without physical access to the instruments, disassembly or recovery of the platform. Example: for a mooring with a radio communication, this would be data obtained through the radio.
   * - P
     - Provisional data
     - Data obtained after instruments have been recovered or serviced; some calibrations or editing may have been done, but the data is not thought to be fully processed. Refer to the history attribute for more detailed information.
   * - D
     - Delayed-mode data
     - Data published after all calibrations and quality control procedures have been applied on the internally recorded or best available original data. This is the best possible version of processed data.
   * - M
     - Mixed
     - This value is only allowed in the global attribute "data_mode" or in attributes to variables in the form "<PARAM>:DM_indicator". It indicates that the file contains data in more than one of the above states. In this case, the variable(s) <PARAM>_DM specify which data is in which data mode.

9. Implementation Guidelines
============================

9.1. Technical Compliance
--------------------------

**UDUNITS Compliance**: Strict adherence to `UDUNITS-2 <https://docs.unidata.ucar.edu/udunits/current/>`_ ensures compatibility with analysis tools like Python's cf_units, NCO, and CDO.

**CF Compliance**: All files must validate against `CF Checker <https://github.com/cedadev/cf-checker>`_.

9.2. Validation Checklist
--------------------------

.. list-table:: Validation Requirements
   :widths: 70 30
   :header-rows: 1

   * - Requirement
     - Status
   * - CF Convention compliance (cf-checker passes)
     - ☐ Required
   * - All required global attributes present
     - ☐ Required
   * - Coordinate variables have required attributes
     - ☐ Required
   * - Data variables have QC companions
     - ☐ Required
   * - Units follow UDUNITS standard
     - ☐ Required
   * - QC flags use standard values (0,1,2,3,4,7,8,9)
     - ☐ Required
   * - File naming follows OS_[PLATFORM]_[DEPLOYMENT]_[MODE]_[PARAMS].nc
     - ☐ Required

9.3. References
----------------

* **OceanSITES Manual**: :doc:`oceanSITES_manual`
* **OceanSITES Website**: https://www.ocean-ops.org/oceansites/
* **CF Conventions**: http://cfconventions.org/
* **UDUNITS-2**: https://docs.unidata.ucar.edu/udunits/current/
* **CF Checker**: https://github.com/cedadev/cf-checker