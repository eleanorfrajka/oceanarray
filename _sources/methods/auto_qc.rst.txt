Automatic QC flagging (Stage 3)
================================

Stage 3 applies QARTOD-style quality control tests to the instrument data and writes
``*_qc`` flag variables alongside each measured parameter.  The tests are implemented
using the `ioos_qc <https://github.com/ioos/ioos_qc>`_ Python package.

The flag convention follows OceanSITES (see table below).

QC flag values
--------------

.. raw:: html

   <table class="docutils align-default" style="font-size:0.9em;">
   <thead><tr>
     <th style="width:60px">Flag</th>
     <th>OceanSITES meaning</th>
     <th>QARTOD meaning</th>
     <th>Notes</th>
   </tr></thead>
   <tbody>
   <tr>
     <td><span style="background:#27ae60;color:#fff;border-radius:4px;padding:2px 8px;font-weight:bold">1</span></td>
     <td>good_data</td>
     <td>Pass</td>
     <td>All tests passed</td>
   </tr>
   <tr>
     <td><span style="background:#2980b9;color:#fff;border-radius:4px;padding:2px 8px;font-weight:bold">2</span></td>
     <td>probably_good_data</td>
     <td>Not evaluated</td>
     <td><strong>Not used</strong> — conflict between OceanSITES and QARTOD meanings;
         see note below</td>
   </tr>
   <tr>
     <td><span style="background:#e67e22;color:#fff;border-radius:4px;padding:2px 8px;font-weight:bold">3</span></td>
     <td>potentially_correctable_bad_data</td>
     <td>Suspect</td>
     <td>Fails soft threshold; treat with caution</td>
   </tr>
   <tr>
     <td><span style="background:#c0392b;color:#fff;border-radius:4px;padding:2px 8px;font-weight:bold">4</span></td>
     <td>bad_data</td>
     <td>Fail</td>
     <td>Fails hard threshold; should not be used</td>
   </tr>
   <tr>
     <td><span style="background:#7f8c8d;color:#fff;border-radius:4px;padding:2px 8px;font-weight:bold">7</span></td>
     <td>nominal_value</td>
     <td>—</td>
     <td>Not used in oceanarray (OceanSITES only)</td>
   </tr>
   <tr>
     <td><span style="background:#2c3e50;color:#fff;border-radius:4px;padding:2px 8px;font-weight:bold">8</span></td>
     <td>interpolated_value</td>
     <td>—</td>
     <td>Pressure interpolated from HAB + neighbouring sensors</td>
   </tr>
   <tr>
     <td><span style="background:#7f8c8d;color:#fff;border-radius:4px;padding:2px 8px;font-weight:bold">9</span></td>
     <td>missing_value</td>
     <td>Missing</td>
     <td>No data; fill value</td>
   </tr>
   </tbody>
   </table>

.. note::

   **Flag 2 is not used.**  OceanSITES flag 2 means "probably_good_data"
   (a positive quality judgement), while QARTOD flag 2 means "test not evaluated"
   (no information).  To avoid this ambiguity, ``oceanarray`` assigns only flags
   1, 3, 4, 8, and 9.  Data that pass all tests receive flag 1; data that fail the
   suspect threshold receive flag 3; data that fail the hard threshold receive flag 4.

See :doc:`../yaml_configuration` for the ``qc_ranges`` configuration block.

Tests applied
-------------

Stage 3 applies the following tests, in order:

1. **Gross-range test**: values outside ``[fail_span[0], fail_span[1]]`` are flagged
   4 (bad); values outside ``[suspect_span[0], suspect_span[1]]`` are flagged 3
   (suspect).  Note: ``fail_span`` defines the *pass range* — values outside it fail.

2. **Spike test**: detects single-sample spikes larger than the ``qc_spike`` threshold.
   Flagged 3 (suspect).

3. **Tilt QC** (Aquadopp only): velocity variables are flagged when the combined
   pitch/roll angle exceeds a threshold.  ADCP instruments use ``error_velocity`` QC
   instead (not a tilt test).

4. **Pressure interpolation flag**: pressure values that were not measured but
   interpolated from neighbouring instruments are flagged 8 (interpolated).

QC is applied **after** pressure interpolation so that interpolated pressures also
receive appropriate QC flags.

Default thresholds
------------------

The values below are the package-wide defaults from ``oceanarray.config.parameters``
(``QC_GROSS_RANGE``, ``QC_SPIKE``, ``QC_FLAT_LINE``, ``QC_TILT``, ``QC_ADCP``).
They assume a fixed mooring in the deep ocean sampled at O(60–120 s); shallower or more
energetic deployments may need tighter suspect thresholds.  Any entry can be overridden
per instrument in the mooring YAML — see `Configuration`_ below.

**Gross-range test** — values outside ``fail_span`` → flag 4; outside ``suspect_span`` → flag 3:

.. list-table::
   :header-rows: 1
   :widths: 25 12 18 18 27

   * - Variable
     - Unit
     - Fail span
     - Suspect span
     - Notes
   * - ``temperature``
     - °C
     - −2.5 to 40.0
     - −2.0 to 35.0
     -
   * - ``conductivity``
     - mS cm⁻¹
     - 0.0 to 75.0
     - 0.0 to 65.0
     - ocean: 20–60
   * - ``salinity``
     - PSU
     - 0.0 to 40.0
     - 2.0 to 40.0
     - open ocean: 30–38
   * - ``pressure``
     - dbar
     - −5.0 to 7000.0
     - −0.5 to 7000.0
     -
   * - ``east_velocity``
     - m s⁻¹
     - −5.0 to 5.0
     - −3.0 to 3.0
     -
   * - ``north_velocity``
     - m s⁻¹
     - −5.0 to 5.0
     - −3.0 to 3.0
     -
   * - ``up_velocity``
     - m s⁻¹
     - −1.0 to 1.0
     - −0.5 to 0.5
     -
   * - ``turbidity``
     - NTU
     - −10.0 to 4000.0
     - −5.0 to 1000.0
     - coastal resuspension events can reach 1000 NTU
   * - ``dissolved_oxygen``
     - µmol L⁻¹
     - 0.0 to 500.0
     - 0.0 to 450.0
     - SBE ODO; deep North Atlantic: 200–320
   * - ``oxygen_saturation_pct``
     - %
     - 0.0 to 200.0
     - 0.0 to 150.0
     - > 200 % implies bubble entrainment or sensor fault

**Spike test** — point *n* is spiked when ``|x[n] − (x[n-1]+x[n+1])/2|`` exceeds the threshold → flag 3:

.. list-table::
   :header-rows: 1
   :widths: 25 12 22 22 19

   * - Variable
     - Unit
     - Suspect threshold
     - Fail threshold
     - Notes
   * - ``temperature``
     - °C
     - 2.0
     - 6.0
     -
   * - ``conductivity``
     - mS cm⁻¹
     - 2.0
     - 5.0
     - low spikes typical of biofouling
   * - ``salinity``
     - PSU
     - 1.0
     - 2.0
     - timing artefacts on unpumped sensors
   * - ``pressure``
     - dbar
     - 10.0
     - 50.0
     -
   * - velocity variables
     - m s⁻¹
     - —
     - —
     - spike test not applied; burst-mode instruments generate false positives at
       every burst boundary

**Flat-line test** (pressure only) — value is considered stuck when it does not change
by more than the tolerance over a contiguous window:

.. list-table::
   :header-rows: 1
   :widths: 25 25 25 25

   * - Variable
     - Suspect (samples)
     - Fail (samples)
     - Tolerance (dbar)
   * - ``pressure``
     - 3
     - 10
     - 0.001

**Tilt QC** (Aquadopp only) — velocity variables are flagged when the instrument tilt
exceeds the threshold:

.. list-table::
   :header-rows: 1
   :widths: 40 20 20 20

   * - Test
     - Suspect (°)
     - Fail (°)
     - Override key
   * - Pitch/roll tilt
     - 30.0
     - 50.0
     - ``tilt_qc`` in instrument YAML

**ADCP percent-good and error-velocity QC** (RDI WorkHorse only) — applied per depth bin:

.. list-table::
   :header-rows: 1
   :widths: 40 30 30

   * - Test
     - Suspect threshold
     - Fail threshold
   * - Percent good (column 3, 4-beam solutions)
     - < 50 %
     - < 25 %
   * - Error velocity
     - —
     - > 0.3 m s⁻¹

Configuration
-------------

QC ranges are configured in the ``qc_ranges`` block of the mooring YAML, under each
instrument entry.  If absent, global defaults are used.

.. code-block:: yaml

   clamp:
     - serial: "26261"
       instrument: microcat
       ...
       qc_ranges:
         temperature:
           fail_span: [-2.0, 35.0]
           suspect_span: [-1.5, 32.0]
         salinity:
           fail_span: [20.0, 45.0]
           suspect_span: [28.0, 40.0]
         pressure:
           fail_span: [0.0, 6000.0]
           suspect_span: [0.0, 5000.0]

The ``fail_span`` defines the *pass range* — values outside this range are flagged 4
(fail).  Similarly for ``suspect_span`` → flag 3.

See :doc:`../yaml_configuration` for the full ``qc_ranges`` YAML reference.

Variables flagged
-----------------

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Variable
     - QC variable
     - Notes
   * - ``temperature``
     - ``temperature_qc``
     - Gross-range + spike
   * - ``conductivity``
     - ``conductivity_qc``
     - Gross-range + spike
   * - ``salinity``
     - ``salinity_qc``
     - Gross-range + spike (derived variable; flagged after derivation)
   * - ``pressure``
     - ``pressure_qc``
     - Gross-range; flag 8 if interpolated
   * - ``east_velocity``
     - ``east_velocity_qc``
     - Tilt QC (Aquadopp) or error_velocity QC (ADCP)
   * - ``north_velocity``
     - ``north_velocity_qc``
     - Same
   * - ``up_velocity``
     - ``up_velocity_qc``
     - Same
   * - ``error_velocity``
     - ``error_velocity_qc``
     - ADCP 4-beam only; bins with high error velocity are flagged

Output structure
----------------

Each QC variable has the same dimensions as its parent variable and carries
these attributes:

.. code-block:: python

   ds["temperature_qc"].attrs == {
       "long_name": "quality flag for temperature",
       "flag_values": [1, 3, 4, 8, 9],
       "flag_meanings": "good_data potentially_correctable_bad_data bad_data interpolated_value missing_value",
       "fail_span": [-2.0, 35.0],
       "suspect_span": [-1.5, 32.0],
   }

The applied thresholds are stored as attributes so the exact QC configuration can be
recovered from the output file.

.. note::

   Variable names are currently lowercase (``temperature``, ``temperature_qc``).
   These names are subject to change in the variable renaming audit; see the
   warning in the :doc:`../index`.

CLI usage
---------

.. code-block:: bash

   oceanarray process dsG3_1_2026 --raw-dir /data/raw --proc-dir /data/proc --stage 3

Stage 3 requires that stage 2 has already run.  Run stages sequentially:

.. code-block:: bash

   oceanarray process dsG3_1_2026 --raw-dir /data/raw --proc-dir /data/proc --stage 1 2 3

Implementation notes
--------------------

- QARTOD tests are run via ``from ioos_qc import qartod``.
- Multi-dimensional ADCP variables (``east_velocity``, ``north_velocity`` with a
  ``N_BINS`` dimension) are handled separately from scalar time series.
- Tilt QC for Aquadopps uses pitch and roll recorded at each timestep.
- All QC flags default to 9 (missing) and are only set to 1/3/4 where data are
  present.

FAIR considerations
--------------------

- Data values are never modified; only flag variables are added.
- Applied QC thresholds are stored as variable attributes for full reproducibility.
- Flag 2 is explicitly avoided to prevent ambiguity between OceanSITES and QARTOD
  conventions.

See also: :doc:`calibration`, :doc:`nortek_coordinate_transform`
