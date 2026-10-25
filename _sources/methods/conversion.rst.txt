4. Conversion to OS format
=======================================

This document outlines the standardised conversion step from raw or partially processed instrument data to the AC1 format. This format builds on the OceanSITES specification but provides additional structure and vocabulary enforcement to support AMOC array interoperability.

.. contents::
   :local:
   :depth: 2

1. Overview
-----------

The AC1 format is produced by the ``amocarray.convert.to_AC1()`` function and is designed to ensure consistency across RAPID, OSNAP, MOVE, and SAMBA arrays.

It is based on OceanSITES NetCDF conventions (see `OceanSITES reference manual <https://www.ocean-ops.org/oceansites/docs/oceansites_data_format_reference_manual.pdf>`_), with adaptations for AMOC-specific use.

See also: :doc:`../oceanarray_format`.

2. File Format
--------------

- **Format**: NetCDF4
- **Structure**: `xarray.Dataset`
- **Dimensions**: `TIME`, `N_LEVELS`, `N_PROF`, `N_COMPONENT` (optional)
- **Coordinates**: `TIME`, optionally `DEPTH` or `PRESSURE`, `LATITUDE`, `LONGITUDE`
- **Encoding**: float32, compressed NetCDF, optionally chunked

CF conventions recommend dimensions ordered as: `T`, `Z`, `Y`, `X`.

3. Variables
------------

.. list-table:: Required and Optional Variables
   :widths: 20 25 20 20 5
   :header-rows: 1

   * - Name
     - Dimensions
     - Units
     - Description
     - RS
   * - TIME
     - (TIME,)
     - seconds since 1970-01-01
     - Timestamps in UTC
     - **M**
   * - LONGITUDE
     - scalar or (N_PROF,)
     - degrees_east
     - Mooring or array longitude
     - S
   * - LATITUDE
     - scalar or (N_PROF,)
     - degrees_north
     - Mooring or array latitude
     - S
   * - DEPTH or PRESSURE
     - (N_LEVELS,)
     - m
     - Depth levels if applicable
     - S
   * - TEMPERATURE
     - (TIME, ...)
     - degree_Celsius
     - In situ or potential temperature
     - S
   * - SALINITY
     - (TIME, ...)
     - psu
     - Practical or absolute salinity
     - S
   * - TRANSPORT
     - (TIME,)
     - Sv
     - Overturning transport estimate
     - S

4. Global Attributes
--------------------

.. list-table:: Required Global Attributes
   :widths: 20 20 25 5
   :header-rows: 1

   * - Attribute
     - Example
     - Description
     - RS
   * - title
     - "RAPID-MOCHA Transport Time Series"
     - Descriptive dataset title
     - **M**
   * - platform
     - "moorings"
     - Platform type
     - **M**
   * - featureType
     - "timeSeries"
     - NetCDF featureType
     - **M**
   * - id
     - "RAPID_20231231_<orig>.nc"
     - File identifier
     - **M**
   * - contributor_name
     - "Dr. Jane Doe"
     - Dataset PI
     - **M**
   * - contributor_email
     - "jane.doe@example.org"
     - Contact email
     - **M**
   * - contributor_role
     - "principalInvestigator"
     - Controlled vocab role
     - **M**
   * - contributing_institutions
     - "University of Hamburg"
     - Data owners
     - **M**
   * - source_doi
     - "https://doi.org/..."
     - Original data DOI
     - **M**
   * - amocarray_version
     - "0.2.1"
     - Software version
     - **M**
   * - date_created
     - "20240419T130000"
     - UTC file creation timestamp
     - **M**

5. Validation Rules
-------------------

- Must include TIME.
- Must include one scientific variable (e.g. TEMP, SAL).
- Must define global attributes listed above.
- File should pass CF-checks where applicable.

6. Usage Example
----------------

.. code-block:: python

   from amocarray.convert import to_AC1
   ds_ac1 = to_AC1(ds_std)

This function:

- Validates inputs
- Applies variable attributes
- Adds global metadata
- Returns CF-compliant AC1 dataset

7. Notes
--------

This format is extensible and version-controlled. All AC1 files should include provenance fields like `source`, `source_doi`, `source_acknowledgement`, `amocarray_version`, and `history`.

For full definitions and examples, see the `format_AC1.rst` file.

