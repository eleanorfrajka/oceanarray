1. Standardisation (Internally-consistent format)
=================================================

This document describes the **standardisation** step in the oceanarray processing workflow. It defines how instrument data—regardless of its original format or naming—are converted to a consistent internal structure using `xarray.Dataset`. This enables all subsequent processing steps (e.g. calibration, filtering, transport calculations) to run on semantically uniform data.

It corresponds to **Stage 1** from RAPID data processing and management, which uses the RDB format.

1. Overview
-----------

Standardisation occurs **immediately after raw data are loaded** and before any scientific transformations. Its goal is to normalize:

- Variable names
- Dimensions
- Coordinate names
- Minimal core attributes (e.g., instrument, mooring, location)

This creates a uniform structure suitable for later trimming, filtering, and conversion.

2. Purpose
----------

- Remove variability in legacy file formats and naming conventions
- Enable consistent handling across deployments and years
- Attach minimal metadata needed for downstream tracking (e.g., serial number, water depth, instrument depth, start and end times, location and mooring names)
- Provide a clean, consistent internal structure with raw data

3. Current Implementation (Stage 1)
-----------------------------------

The standardisation process is implemented in the :mod:`oceanarray.pipeline.stage1` module, which provides automated conversion from native instrument formats to standardised NetCDF files.

3.1. Input Formats Supported
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The :class:`oceanarray.pipeline.stage1.MooringProcessor` class supports multiple instrument formats:

- **Sea-Bird CNV files** (`sbe-cnv`): Standard SBE 37 MicroCAT output
- **Sea-Bird ASCII files** (`sbe-ascii`): Alternative SBE ASCII format
- **Nortek AquaDopp** (`nortek-raw`): Current meter data with header files
- **RBR RSK files** (`rbr-rsk`): RBR logger binary format
- **RBR ASCII files** (`rbr-dat`): RBR text output

Each format is handled by specialized readers from the `seasenselib` library that parse instrument-specific headers and data structures.

3.2. Processing Workflow
^^^^^^^^^^^^^^^^^^^^^^^^^

The standardisation process follows these steps:

1. **Configuration Loading**: Read YAML configuration files containing:

   - Mooring metadata (location, water depth, deployment times)

   - Instrument specifications (serial numbers, depths, file locations)

   - Clock offsets and timing corrections (not applied in stage 1)

2. **Data Reading**: Use appropriate readers to parse native instrument files:

   - Extract time series data

   - Parse instrument metadata from headers

   - Handle instrument-specific data structures

3. **Variable Standardisation**: Convert to consistent naming:

   - Remove derived variables (e.g., potential temperature, density)

   - Standardise coordinate names

   - Apply consistent units and metadata

4. **Metadata Integration**: Add standardised attributes:

   - Global mooring information

   - Instrument-specific metadata

   - Deployment and recovery information

   - Quality control flags and processing history

5. **NetCDF Output**: Write to compressed, chunked NetCDF files with:

   - Optimised data types (float32, uint8 for flags)

   - Time-based chunking for efficient access

   - CF-compliant metadata structure

3.3. Configuration Format
^^^^^^^^^^^^^^^^^^^^^^^^^^

Mooring configurations are defined in YAML files with the following structure:

.. code-block:: yaml

   name: mooring_name
   waterdepth: 1000
   longitude: -30.0
   latitude: 60.0
   deployment_time: '2018-05-01T12:00:00'
   recovery_time: '2019-05-01T12:00:00'
   directory: 'moor/raw/deployment_name/'
   instruments:
     - instrument: microcat
       serial: 12345
       depth: 100
       filename: 'data_file.cnv'
       file_type: 'sbe-cnv'
       clock_offset: 0

Here the ``name`` is the unique mooring identifier (for example, for mooring "DS E" deployed in 2018 for the first time, we have "dsE_1_2018".

The ``waterdepth`` is the nominal water depth at the mooring site in metres.

The ``longitude`` and ``latitude`` are the mooring coordinates in decimal degrees.

The ``deployment_time`` and ``recovery_time`` are the UTC timestamps for the mooring deployment and recovery, with format `YYYY-MM-DDTHH:MM:SS`.

The ``directory`` is the path to the raw data files for this deployment.  According to our convention, this is in a directory named `moor/raw/deployment_name/`, where `deployment_name` is a unique identifier for the cruise on which the mooring was recovered.

The ``instruments`` list contains one entry per instrument on the mooring, with:

- ``instrument``: The type of instrument (e.g., ``microcat``, ``aquadopp``, ``rbr``)
- ``serial``: The instrument's serial number
- ``depth``: The instrument's nominal depth in metres
- ``filename``: The name of the raw data file
- ``file_type``: The type of the raw data file (e.g., ``sbe-cnv``, ``rbr-rsk``)
- ``clock_offset`` (optional, defaults to zero): The clock offset for the instrument in seconds.

3.4. Usage Example
^^^^^^^^^^^^^^^^^^

.. code-block:: python

   import oceanarray

   # Process a single mooring through Stage 1
   oceanarray.process('mooring_name', stage=1,
                      raw_dir='/path/to/raw/', proc_dir='/path/to/proc/')

   # Process multiple moorings
   for mooring in ['mooring1', 'mooring2', 'mooring3']:
       oceanarray.process(mooring, stage=1,
                          raw_dir='/path/to/raw/', proc_dir='/path/to/proc/')

4. Output Format
----------------

The standardised output is a **raw-equivalent** `xarray.Dataset` with:

- **Dimensions**: `time` (primary coordinate)
- **Variables**: Standardised names (e.g., `temperature`, `salinity`, `pressure`)
- **Coordinates**: `time`, plus instrument metadata variables
- **Attributes**: Metadata including instrument, mooring, and deployment information

Example output structure:

.. code-block:: python

   <xarray.Dataset>
   Dimensions:        (time: 124619)
   Coordinates:
     * time           (time) datetime64[ns] 2018-08-12T08:00:01 ... 2018-08-26T20:47:24
   Data variables:
       temperature    (time) float32 ...
       salinity       (time) float32 ...
       pressure       (time) float32 ...
       serial_number  int64 7518
       InstrDepth     int64 100
       instrument     <U8 'microcat'
       clock_offset   int64 0
       start_time     <U19 '2018-08-12T08:00:00'
       end_time       <U19 '2018-08-26T20:47:24'
   Attributes:
       mooring_name:           test_mooring
       waterdepth:             1000
       longitude:              -30.0
       latitude:               60.0
       deployment_time:        2018-08-12T08:00:00
       recovery_time:          2018-08-26T20:47:24

5. Quality Control and Error Handling
--------------------------------------

The standardisation process includes error handling:

- **Missing Files**: Graceful handling with detailed logging
- **Format Errors**: Reader-specific error catching and reporting
- **Metadata Validation**: Checks for required configuration fields
- **Output Verification**: Ensures NetCDF files are created successfully

All processing activities are logged to timestamped log files for debugging and audit trails.

6. Historical Context: RAPID RDB Format
----------------------------------------

This standardisation step evolved from the RAPID programme's RDB format, which provided a similar function for historical mooring data. An example of the original RDB format structure:

.. code-block:: text

    Mooring              = wb2_16_2020
    SerialNumber         = 5768
    WaterDepth           = 3916
    InstrDepth           = 1700
    Start_Date           = 2021/01/05
    Start_Time           = 20:00
    End_Date             = 2023/02/25
    End_Time             = 17:00
    Latitude             = 26 31.000 N
    Longitude            = 076 44.460 W
    Columns              = YY:MM:DD:HH:T:C:P
    2021   01   05   20.00056   3.9239   33.2233  1726.4
    2021   01   05   21.00056   3.9389   33.2389  1725.4
    2021   01   05   22.00056   3.9389   33.2405  1725.3

The NetCDF-based approach provides several advantages over the historical RDB format:

- **Self-describing metadata**: CF-compliant attributes and coordinate information
- **Efficient storage**: Compression and chunking for large datasets
- **Software compatibility**: Wide support across analysis tools and languages
- **Extensibility**: Easy addition of new variables and metadata

7. Legacy Processing Scripts
----------------------------

The original RAPID processing chain used MATLAB scripts to convert instrument data to RDB format. The key script was:

- `microcat2rodb_3.m <../_static/code/microcat2rodb_3.m>`__

This script performed similar functions to the current Python implementation:

.. literalinclude:: ../_static/code/microcat2rodb_3.m
   :language: matlab
   :lines: 1-40
   :linenos:
   :caption: Excerpt from `microcat2rodb_3.m`

The Python implementation in :mod:`oceanarray.pipeline.stage1` provides equivalent functionality with improved:

- **Error handling**: Logging and graceful failure modes
- **Format support**: Multiple instrument types through pluggable readers
- **Metadata management**: YAML-based configuration with validation
- **Output formats**: NetCDF with CF conventions for interoperability

8. Integration with Processing Chain
------------------------------------

Standardised files from Stage 1 serve as input to subsequent processing steps:

- **Stage 2**: :doc:`trimming` - Remove pre/post deployment data
- **Stage 3**: :doc:`calibration` - Apply post-cruise calibration offsets
- **Later stages**: :doc:`time_gridding`, :doc:`vertical_gridding`, :doc:`concatenation` for array products

The consistent structure created during standardisation ensures that all downstream processing tools can operate on any instrument dataset without format-specific modifications.

See also: :doc:`../oceanarray`, :doc:`trimming`, :doc:`calibration`
