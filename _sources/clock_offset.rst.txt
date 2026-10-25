Clock Offset Analysis
=====================

Clock offset analysis is a critical intermediate step in oceanographic data processing that occurs between Stage 1 (standardization) and Stage 2 (trimming to deployment) of the processing pipeline. This analysis identifies and corrects timing errors in instrument clocks to ensure accurate temporal alignment of data from multiple instruments on the same mooring.

Overview
--------

Oceanographic instruments deployed on moorings may have clock offsets due to incorrect setup, clock drift, or other timing issues. Since scientific analysis often requires precise temporal correlation between instruments at different depths, these timing errors must be identified and corrected.

The clock offset analysis provides two complementary methods to detect timing discrepancies:

1. **Deployment Period Detection**: Uses temperature profiles to identify when instruments were actually deployed on the seafloor
2. **Lag Correlation Analysis**: Compares temperature time series between instruments to detect systematic timing offsets

Methods
-------

Deployment Period Detection
~~~~~~~~~~~~~~~~~~~~~~~~~~~

This method uses the characteristic temperature signature that occurs when instruments transition from surface conditions to deep-water conditions during deployment:

- **Temperature Threshold**: Identifies "cold" water temperatures using statistical analysis (typically mean ± 3 standard deviations of deep values)
- **Start/End Times**: Determines when each instrument first and last recorded temperatures within the cold water range
- **Consensus Analysis**: Groups instruments with similar deployment timing to establish reference times
- **Offset Calculation**: Computes timing differences relative to the consensus group

This approach is effective for detecting large clock offsets (hours to days) and works well when instruments show clear temperature transitions during deployment and recovery.

Lag Correlation Analysis
~~~~~~~~~~~~~~~~~~~~~~~~

This method performs cross-correlation analysis between temperature time series from different instruments:

- **Reference Selection**: Uses one instrument as a temporal reference (typically the deepest or most reliable)
- **Subsampling**: Reduces computational load by subsampling time series while maintaining correlation structure
- **Cross-Correlation**: Computes lag correlations to find the temporal offset that maximizes correlation between instruments
- **Peak Detection**: Identifies the lag corresponding to maximum correlation as the estimated clock offset

This approach is sensitive to smaller timing errors (seconds to minutes) and works well when instruments record similar environmental variations.

Implementation
--------------

The analysis is implemented in the ``oceanarray.clock_offset`` module with the following key functions:

Core Functions
~~~~~~~~~~~~~~

- ``load_mooring_instruments()``: Load instrument data and enrich with YAML metadata
- ``analyze_deployment_timing()``: Perform temperature-based deployment period detection
- ``calculate_timing_offsets()``: Calculate offsets using consensus grouping approach
- ``perform_lag_correlation_analysis()``: Execute cross-correlation analysis
- ``print_timing_offset_summary()``: Generate human-readable offset summary

Workflow Functions
~~~~~~~~~~~~~~~~~~

- ``create_common_time_grid()``: Generate interpolation grid for temporal alignment
- ``interpolate_datasets_to_grid()``: Interpolate instrument data to common time base
- ``combine_interpolated_datasets()``: Merge data into multi-level dataset structure

Usage
-----

The analysis is demonstrated in the ``notebooks/legacy/demo_clock_offset.ipynb`` notebook, which covers the following steps:

1. **Configuration**: Specify mooring name and data paths
2. **Data Loading**: Load instrument datasets and YAML metadata
3. **Preprocessing**: Create common time grid and interpolate data
4. **Deployment Analysis**: Identify deployment periods using temperature profiles
5. **Visualization**: Plot temperature time series with deployment bounds
6. **Offset Calculation**: Compute timing offsets using both methods
7. **Results Summary**: Generate recommendations for YAML configuration

Output and Application
----------------------

The analysis produces:

- **Offset Estimates**: Recommended clock_offset values (in seconds) for each instrument
- **Quality Metrics**: Correlation coefficients and confidence measures
- **Visualizations**: Time series plots showing deployment periods and timing relationships
- **Summary Tables**: Tabular results comparing both analysis methods

**Important**: The sign convention for YAML clock fields is: **positive = instrument was slow (behind UTC), negative = instrument was fast (ahead of UTC)**. The value is *added* to the raw instrument time. For example, if an instrument was 15 seconds behind UTC, set ``clock_offset: 15``.

``clock_offset`` and ``clock_drift_seconds`` describe the **total correction** at two points in time:

- ``clock_offset`` — correction needed at the **start** of the deployment (instrument clock error when deployed).
- ``clock_drift_seconds`` — correction needed at the **end** of the deployment (instrument clock error at recovery).

Stage 2 applies a linear ramp between these two values, so every timestamp receives the
appropriate interpolated correction.  All values are **added** to instrument time
(positive = instrument was slow/behind UTC).

.. list-table::
   :header-rows: 1

   * - Fields set
     - Behaviour
   * - ``clock_offset`` only
     - Uniform constant shift throughout the record.
   * - ``clock_drift_seconds`` only
     - Ramps from 0 at deployment to ``clock_drift_seconds`` at recovery.
   * - Both
     - Ramps from ``clock_offset`` at deployment to ``clock_drift_seconds`` at recovery.

.. code-block:: yaml

   # Example: clock was 5 s slow at deployment and 8 s slow at recovery.
   # Stage 2 applies +5 s at deployment time, +8 s at recovery, linearly in between.
   clock_offset: 5
   clock_drift_seconds: 8

   # Alternative — two timestamps read off at recovery (preferred; avoids sign errors).
   # Compact YYYYMMDDTHH:MM:SS, full ISO-8601 datetime, or time-only HH:MM:SS are all accepted.
   computer_clock_at_recovery:    '20260711T10:23:30'   # or '2026-07-11T10:23:30' or '10:23:30'
   instrument_clock_at_recovery:  '20260711T10:23:22'
   # computer − instrument = +8 s → used as clock_drift_seconds (total correction at recovery)
   # clock_offset (if also set) remains the correction at deployment.

.. note::

   The two-timestamp method measures ``computer − instrument`` at **recovery only**.
   If the clock was also set incorrectly at deployment, set ``clock_offset`` for that
   initial error and let the two timestamps capture the total at recovery.

After updating the YAML file with clock correction values, Stage 2 processing applies these corrections (before trimming) and writes ``*_stage2.nc`` files. The original, uncorrected time is preserved as ``time_orig``, and the ``history`` attribute records what was applied. The clock offset analysis can then be re-run using the corrected data to verify that timing discrepancies have been resolved.

Best Practices
--------------

- **Method Comparison**: Always compare results from both deployment detection and lag correlation methods
- **Visual Inspection**: Examine temperature time series plots to validate automated detection
- **Iterative Refinement**: Re-run analysis after applying corrections to verify success
- **Documentation**: Record analysis decisions and unusual findings in processing logs
- **Validation**: Cross-check results with deployment/recovery logs when available

The clock offset analysis ensures that subsequent processing stages work with temporally aligned data, which is essential for accurate calculation of transport estimates and other derived quantities that depend on precise temporal relationships between measurements at different depths.