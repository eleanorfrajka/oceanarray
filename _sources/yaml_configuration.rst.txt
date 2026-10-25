.. _yaml_configuration:

==============================
YAML Configuration Reference
==============================

Each mooring is configured with a single YAML file named
``{mooring_name}.mooring.yaml``, placed in the processed directory for that
mooring (see :doc:`directory_structure`).  This file tells ``oceanarray``
everything it needs to know: where the raw data files are, what instruments
were deployed, when the mooring was in the water, and how to quality-control
the data.

----

Minimal working example
-----------------------

The following YAML is sufficient to process a mooring that carries one
SeaBird microCAT and one Nortek Aquadopp.  Copy it and fill in the values
for your deployment before running any ``oceanarray`` command.

.. code-block:: yaml

   name: dsG3_1_2026
   waterdepth: 1800
   deployment_time: "2026-04-10T12:00:00"
   recovery_time: "2027-04-15T08:30:00"

   # Position — used for magnetic declination correction
   deployment_latitude: "65 29.84 N"
   deployment_longitude: "009 30.12 W"

   clamp:
     - serial: "26261"
       instrument: microcat
       filename: 26261_recovery.asc
       file_type: sbe-ascii
       hab: 450
       computer_clock_at_recovery: "2027-04-15T08:00:00"
       instrument_clock_at_recovery: "2027-04-15T07:59:48"

     - serial: "400115"
       instrument: aquadopp
       filename: A400115_dsG3.aqd
       file_type: nortek-raw
       header_file: A400115_dsG3.hdr
       hab: 460

----

Mooring-level keys
------------------

These fields appear at the top level of the YAML file and apply to the
mooring as a whole.

.. list-table::
   :header-rows: 1
   :widths: 25 10 15 50

   * - Field
     - Type
     - Required
     - Description
   * - ``name``
     - string
     - Required
     - Mooring identifier, e.g. ``dsG3_1_2026``.  Used in all output file
       names.
   * - ``waterdepth``
     - number
     - Required
     - Water depth at the anchor position in metres.
   * - ``deployment_time``
     - ISO-8601 string
     - Required
     - When the mooring was deployed (UTC).  Used by stage 2 to trim the
       record.  Format: ``"YYYY-MM-DDTHH:MM:SS"``, e.g.
       ``"2026-05-07T17:05:00"`` means 7 May 2026 at 17:05 UTC.
   * - ``recovery_time``
     - ISO-8601 string
     - Required
     - When the mooring was recovered (UTC).  Used by stage 2 to trim the
       record.  Same format as ``deployment_time``.
   * - ``year``
     - integer
     - Optional
     - Calendar year of deployment start; used in report headers.
   * - ``status``
     - string
     - Optional
     - Mooring lifecycle state: typically ``planned``, ``deployed``, or
       ``recovered``.  Used by ``moordiag`` to select which information to
       display on the diagram (e.g. ``planned`` uses
       ``planned_latitude/longitude`` for the nominal position).
   * - ``deployment_cruise``
     - string
     - Optional
     - Cruise identifier for the deployment leg.
   * - ``deployment_ship``
     - string
     - Optional
     - Vessel name for the deployment.
   * - ``recovery_cruise``
     - string
     - Optional
     - Cruise identifier for the recovery leg.
   * - ``recovery_ship``
     - string
     - Optional
     - Vessel name for the recovery.
   * - ``seabed_latitude`` / ``seabed_longitude``
     - string
     - Optional
     - Confirmed anchor position (most accurate).  See
       `Position and latitude/longitude priority`_ below.
   * - ``deployment_latitude`` / ``deployment_longitude``
     - string
     - Optional
     - Ship GPS fix at the time of deployment.
   * - ``planned_latitude`` / ``planned_longitude``
     - string
     - Optional
     - Pre-cruise planned position.  Used by ``moordiag`` only (for
       ``status: planned`` moorings before deployment); not used by
       ``oceanarray`` for magnetic declination.
   * - ``latitude`` / ``longitude``
     - string
     - Optional
     - Generic fallback position if none of the above are set.
   * - ``directory``
     - string
     - Deprecated
     - **Deprecated — do not use.**  The raw-data root is a run-time location,
       not part of the mooring description; pass it as ``raw_dir`` / ``--raw-dir``.
       ``validate`` warns on this key, and the stage-1 absolute-path override is
       removed in 0.7.0.
   * - ``qc_ranges``
     - mapping
     - Optional
     - Mooring-wide QC thresholds applied in stage 3 to all instruments
       unless overridden at the instrument level.  See `QC ranges`_ below.

----

Instrument entries (the ``clamp`` list)
----------------------------------------

Each physical instrument on the mooring appears as one entry under the
``clamp`` key.  Entries are processed in the order they appear; ``oceanarray``
does not re-order them by depth.

Fields required for processing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 25 10 65

   * - Field
     - Type
     - Description
   * - ``serial``
     - string
     - Instrument serial number.  Used in all output filenames, NetCDF
       attributes, and report links.  Use a bare number string, e.g.
       ``"26261"``.  A trailing ``*`` is allowed (stripped from filenames).
       See `Inline hardware and instruments`_ for the special comma
       convention used for ADCPs on floats.
   * - ``instrument``
     - string
     - Instrument type.  Controls the raw-file subdirectory, the output
       subdirectory, which processing code paths are activated, and the
       instrument-type column in reports.  See `The instrument field`_ for
       the full list of valid values.
   * - ``filename``
     - string or ``null``
     - Raw data filename, relative to
       ``{raw_dir}/{mooring}/{instrument}/``.
       **Recommended naming convention**: ``{serial}_recovery.<ext>``
       (e.g. ``26261_recovery.asc``, ``9415_recovery.dat``).
       If omitted or set to ``null``, ``oceanarray`` will try to locate the
       file automatically using the conventions in the table below — if a
       match is found, processing proceeds; if not, the instrument is skipped
       with a diagnostic message listing the paths tried.

       .. list-table:: Auto-detected filename conventions
          :header-rows: 1
          :widths: 25 30 20 25

          * - ``instrument`` value
            - Condition
            - Filename tried
            - ``file_type``
          * - ``microcat``
            - serial < 6000
            - ``{serial}_recovery.asc``
            - ``sbe-ascii``
          * - ``microcat``
            - serial ≥ 6000
            - ``{serial}_recovery.hex``
            - ``sbe-hex``
          * - ``tr1050``
            - any
            - ``{serial}_recovery.hex``
            - ``rbr-hex``
          * - ``rbrsolo``, ``rbrduet``, ``seapoint``
            - any
            - ``{serial}_recovery.rsk``
            - ``rbr-rsk``
          * - ``aquadopp`` / ``nortek``
            - serial < 400000
            - ``{serial}_recovery.dat`` (+ ``.hdr``)
            - ``nortek-ascii``
          * - ``aquadopp`` / ``nortek``
            - serial ≥ 400000
            - not auto-detected — set ``filename`` explicitly
            - —
          * - ``ADCP``
            - any
            - ``{serial}_{mooring}_recovery.000``
            - ``rdi-raw``

       A trailing ``*`` in the serial field is stripped before building the
       filename (e.g. ``serial: 13560*`` → tries ``13560_recovery.asc``).
       Searched directories (in order):
       ``{raw_dir}/{mooring}/{instrument}/``, then ``{raw_dir}/{mooring}/``.

   * - ``file_type``
     - string
     - Reader type passed to seasenselib.  See
       `Valid file_type values`_ below.
       Can be omitted when ``filename`` is also omitted — the auto-detection
       logic sets ``file_type`` to match the discovered file.
   * - ``hab``
     - number
     - Height above bottom in metres.  Used for pressure interpolation,
       mooring diagram positioning, and vertical ordering in the stack and
       grid.  See `HAB — height above bottom`_ for what happens if this
       field is missing.

Optional fields
~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 10 60

   * - Field
     - Type
     - Description
   * - ``header_file``
     - string
     - Path to the header file, relative to the instrument raw directory.
       **Required for Nortek Aquadopps** — used to recover the
       beam-to-XYZ transformation matrix.  For older Aquadopp units supply
       the ``.hdr`` file (e.g. ``9920_recovery.hdr``); for 400### serial
       units supply the string-data CSV (e.g.
       ``converted_raw/A400115005_400115_dsG3/String Data.csv``).
   * - ``clock_offset``
     - number
     - Constant time shift in seconds applied to the entire record.
       Positive means the instrument clock was slow (behind UTC), so
       the correction adds time.  Omit if the offset is negligible or
       unknown.  For linear drift correction, use
       ``computer_clock_at_recovery`` / ``instrument_clock_at_recovery``
       instead.
   * - ``computer_clock_at_recovery``
     - ISO-8601 string or ``unknown``
     - GPS/computer time read at the moment of recovery comparison.
   * - ``instrument_clock_at_recovery``
     - ISO-8601 string or ``unknown``
     - Instrument's internal clock time read at the same moment.
       If either value is ``unknown``, clock drift correction is skipped
       for this instrument.  See `Clock correction`_ below.
   * - ``pressure_qc``
     - integer
     - QARTOD flag (1–4) forced onto all pressure values for this
       instrument.  Set to ``4`` to mark pressure as bad/absent (e.g.
       the pressure sensor failed or the instrument type has no pressure
       sensor).
   * - ``qc_ranges``
     - mapping
     - Per-instrument QC overrides.  Same structure as mooring-level
       ``qc_ranges``; instrument-level values override mooring-level
       values for the same variable.
   * - ``skip``
     - boolean
     - Set to ``true`` to skip this instrument entirely during processing.
       The entry is retained in the YAML for documentation purposes.
   * - ``skip_reason``
     - string
     - Free-text explanation for why the instrument is skipped.  Displayed
       in reports alongside the skipped indicator, e.g.
       ``"pressure sensor failed at deployment"``.
   * - ``file_type: TBD``
     - string
     - Special sentinel value indicating no data file exists yet.
       Processing is skipped; the instrument appears in reports as pending.
   * - ``stack``
     - boolean
     - Set to ``false`` to keep this instrument out of the stack and grid
       while still processing it through Stages 1–3 and its instrument
       report (reference data, e.g. a low-passed RODB ``.lowedt`` file).
       The stack records it in its ``instruments_excluded`` attribute.
       Default ``true``.  A low-passed file reaching the stack without it is
       omitted with an error naming this key.
   * - ``units_override``
     - mapping
     - Corrects a file whose ``units`` attribute is wrong, as
       ``{variable: units}``, e.g. ``{conductivity: mS/cm}`` for a file that
       labels mS/cm values ``S/m``.  Applied in Stage 1 before the units pass;
       the stated value is kept in the variable's ``units_override_from``
       attribute.  For ``oceansites`` and ``rodb`` files Stage 1 checks each
       variable's median against the registry's valid range and refuses the
       instrument, naming this key, when the stated units look wrong.

Additional fields
~~~~~~~~~~~~~~~~~

``sample_interval_seconds``
   Nominal sampling interval in seconds.  Used by ``oceanarray`` in HTML
   reports (shown in instrument metadata cards and compared against the
   actual computed sampling interval) and by ``moordiag`` for the mooring
   diagram.  Does not affect any data processing.

``label``
   Short human-readable name for the instrument, e.g. ``"SM-p"`` or
   ``"Aquadopp"``.  Used by ``moordiag`` for diagram labels and may be used
   by ``oceanarray`` as a legend label in plots.

Fields used only by moordiag (ignored by oceanarray)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following fields are read by the ``moordiag`` mooring diagram package
and are ignored by ``oceanarray``: ``image``, ``clamp_id``, ``position``,
``hardware_type``, ``category``, ``component_id``, ``length``, ``repeat``.
They may safely remain in the YAML alongside the processing fields.

----

The ``instrument`` field
------------------------

The ``instrument`` field in each ``clamp`` entry does four things:

1. **Raw-file subdirectory**: ``oceanarray`` looks for raw files in
   ``{raw_dir}/{mooring}/{instrument}/``.
2. **Output subdirectory**: stage NC files are written to
   ``{proc_dir}/{mooring}/{instrument}/``.
3. **Processing code path**: the value activates instrument-specific
   processing.  For example, ``aquadopp`` triggers velocity rotation and
   tilt QC; ``microcat`` triggers salinity derivation from conductivity,
   temperature, and pressure.
4. **Report rendering**: the value appears as the instrument-type label in
   HTML reports.

Valid values and their descriptions:

.. list-table::
   :header-rows: 1
   :widths: 20 25 55

   * - Value
     - Instrument
     - Reader families accepted by ``file_type``
   * - ``microcat``
     - SeaBird SBE37 CTD
     - ``sbe-cnv``, ``sbe-ascii``, ``sbe-hex``, ``oceansites``, ``rodb``
   * - ``sbe56``
     - SeaBird SBE56 temperature logger
     - ``sbe-cnv``
   * - ``sbe16``
     - SeaBird SBE16plus CTD
     - ``sbe-cnv``, ``sbe-hex``
   * - ``rbrsolo``
     - RBR Solo temperature logger
     - ``rbr-rsk``, ``rbr-dat``
   * - ``rbrduet``
     - RBR Duet CT (conductivity + temperature)
     - ``rbr-rsk``, ``rbr-dat``
   * - ``aquadopp``
     - Nortek Aquadopp current meter
     - ``nortek-raw``, ``nortek-ascii``, ``nortek-csv``
   * - ``currentmeter``
     - Point current meter delivered already processed (e.g. an Aanderaa RCM
       from an OceanSITES or RODB archive)
     - ``oceansites``, ``rodb``
   * - ``ADCP``
     - RDI WorkHorse broadband ADCP
     - ``rdi-raw``
   * - ``tr1050``
     - Turner TR-1050 fluorometer/turbidity
     - ``rbr-matlab``, ``rbr-hex``
   * - ``seapoint``
     - Seapoint turbidity sensor
     - ``rbr-rsk``, ``rbr-dat``

Custom values are allowed if used consistently: the string becomes a
subdirectory name, so avoid spaces and special characters.  If you use
a custom value, ensure every instrument entry in the YAML that shares the
same instrument type uses the same string.

----

Valid ``file_type`` values
--------------------------

.. list-table::
   :header-rows: 1
   :widths: 20 25 20 35

   * - Value
     - Reader
     - Typical instrument
     - Notes
   * - ``sbe-cnv``
     - SeaBird CNV
     - SBE37, SBE56, SBE16
     -
   * - ``sbe-ascii``
     - SeaBird ASCII export
     - SBE37
     -
   * - ``sbe-hex``
     - SeaBird HEX
     - SBE16
     -
   * - ``nortek-raw``
     - Nortek binary AQD
     - Aquadopp
     - Requires a matching ``.hdr`` file; supply path with ``header_file``
   * - ``nortek-ascii``
     - Nortek ASCII export
     - Aquadopp
     -
   * - ``nortek-csv``
     - Nortek CSV string-data export
     - Aquadopp (400### series)
     -
   * - ``oceansites``
     - OceanSITES netCDF (oceanarray reader)
     - MicroCAT, current meter
     - A single-instrument time series already processed by its originator:
       OceanSITES 1.3 (variables on ``TIME, DEPTH``) or the OSNAP variant
       (variables on ``TIME``).  ``TEMP``, ``PSAL``, ``CNDC``, ``PRES``,
       ``UCUR``, ``VCUR``, ``WCUR`` are read; a ``<CODE>_QC`` variable is kept
       as ``<name>_qc_originator``.  Velocities are geographic, so Stage 3
       applies no rotation and no magnetic declination.
   * - ``rodb``
     - RODB text (oceanarray reader)
     - MicroCAT, current meter
     - RAPID/BODC ``.microcat``, ``.edt`` and ``.lowedt`` files.  Units are
       those documented for each column code (``U``/``V``/``W`` in cm/s,
       converted to m s-1).  An ``.edt`` header's ``MagDeviation`` is recorded
       as ``originator_magnetic_declination`` and not applied again.
   * - ``rbr-rsk``
     - RBR Ruskin SQLite
     - RBR Solo, Duet, etc.
     -
   * - ``rbr-dat``
     - RBR legacy DAT format
     - RBR instruments
     -
   * - ``rbr-matlab``
     - RBR MATLAB export
     - Turner TR-1050 via RBR logger
     -
   * - ``rbr-hex``
     - RBR HEX binary
     - Turner TR-1050, Seapoint via RBR logger
     -
   * - ``rdi-raw``
     - RDI raw binary
     - WorkHorse ADCP
     - Read via ``seasenselib`` (``mhkit[dolfyn]``); no extra install needed.
   * - ``adcp-matlab``
     - ADCP MATLAB export
     - RDI WorkHorse
     -

----

Clock correction
----------------

Two methods are available for correcting instrument clock drift.

**Two-timestamp method (recommended)**

At recovery, record the GPS/computer time and the instrument's internal
clock time at the same moment.  Enter both values in the YAML:

.. code-block:: yaml

   computer_clock_at_recovery: "2027-04-15T08:00:00"
   instrument_clock_at_recovery: "2027-04-15T07:59:48"

Stage 2 computes ``drift = computer_time − instrument_time`` and applies a
linear correction over the full deployment record (zero drift at deployment
time, full drift value at recovery time).

**Sign convention**

.. list-table::
   :header-rows: 1
   :widths: 35 15 50

   * - Situation
     - Drift sign
     - Effect
   * - Instrument clock was slow (behind UTC)
     - Positive
     - Stage 2 adds time to make records later
   * - Instrument clock was fast (ahead of UTC)
     - Negative
     - Stage 2 subtracts time to make records earlier

**Constant offset method**

If you know a fixed offset (e.g. the clock was set incorrectly at the
start) but have no recovery comparison, supply ``clock_offset`` in seconds:

.. code-block:: yaml

   clock_offset: 12    # instrument was 12 seconds slow; add 12 s to all times

**Skipping clock correction**

If neither ``clock_offset`` nor the two-timestamp pair is present in the
YAML, no clock correction is applied.  If either clock timestamp is the
string ``unknown``, the drift correction is also skipped and a warning is
logged.

----

QC ranges
---------

Quality control is applied in stage 3, **after** pressure interpolation —
so instruments that receive an interpolated pressure value participate in
the QC tests alongside instruments with native pressure sensors.

The gross-range test uses two nested spans.  **The spans define the
acceptable (non-flagged) range, not the flagged range.**

- Values **within** ``suspect_span`` → flag 1 (pass / good data)
- Values **outside** ``suspect_span`` but **within** ``fail_span`` → flag 3
  (suspect / probably bad)
- Values **outside** ``fail_span`` → flag 4 (fail / bad data)
- NaN values → flag 9 (missing)

For example, ``fail_span: [-2.0, 35.0]`` means temperatures below −2 °C
or above 35 °C are flagged 4 (bad); a temperature of 40 °C would receive
flag 4.

QC flag values follow the OceanSITES / QARTOD convention:

.. list-table::
   :header-rows: 1
   :widths: 10 25 25 40

   * - Flag
     - OceanSITES name
     - QARTOD name
     - When applied
   * - 1
     - Good data
     - Pass
     - Within ``suspect_span`` (or no QC configured for this variable)
   * - 2
     - Probably good data
     - Not evaluated
     - Not used by ``oceanarray``
   * - 3
     - Probably bad data
     - Suspect
     - Outside ``suspect_span`` but within ``fail_span``; also tilt suspect
   * - 4
     - Bad data
     - Fail
     - Outside ``fail_span``; also tilt fail; forced via ``pressure_qc: 4``
   * - 8
     - Interpolated
     - —
     - Pressure values filled by interpolation (``pressure_qc = 8``)
   * - 9
     - Missing value
     - Missing
     - NaN in the underlying data

Define thresholds under the ``qc_ranges`` key as a mapping of variable
name to span pair:

.. code-block:: yaml

   qc_ranges:
     temperature:
       fail_span: [-2.0, 35.0]
       suspect_span: [-1.5, 30.0]
     salinity:
       fail_span: [0.0, 50.0]
       suspect_span: [0.0, 35.5]
     pressure:
       fail_span: [-5.0, 1050.0]
       suspect_span: [-0.5, 1020.0]
     pitch:
       fail_span: [-30.0, 30.0]
       suspect_span: [-20.0, 20.0]
     roll:
       fail_span: [-30.0, 30.0]
       suspect_span: [-20.0, 20.0]
     up_velocity:
       fail_span: [-0.5, 0.5]
       suspect_span: [-0.3, 0.3]

**Priority**: if ``qc_ranges`` appears both at the mooring level and within
an instrument ``clamp`` entry, the instrument-level values override the
mooring-level values for that specific variable.  Other variables not
overridden at the instrument level still use the mooring-level thresholds.

Applied thresholds are stored as attributes on each ``*_qc`` variable in
the stage 3 NetCDF file so that the exact configuration can be recovered
from the output file alone.

Commonly useful variables to configure:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Variable name
     - Notes
   * - ``temperature``
     - Adjust for your deployment depth and region (e.g. deep water
       narrows the expected range)
   * - ``salinity``
     - Upper bound near 36.5 for Atlantic; lower in Baltic or Arctic
   * - ``pressure``
     - Upper bound should exceed instrument nominal depth by ~10 %
   * - ``pitch``, ``roll``
     - Controls Aquadopp tilt QC.  Symmetric spans are normal;
       ``fail_span: [-30, 30]`` flags bad when ``|tilt| > 30°``
   * - ``up_velocity``
     - Aquadopp/ADCP vertical velocity; useful for detecting stuck instruments
   * - ``error_velocity``
     - RDI ADCP only (4-beam instruments).  The error velocity is the
       residual from the overdetermined beam solution; large values indicate
       beam contamination, fish echoes, or instrument problems.  Flagged
       bins also propagate to ``east_velocity_qc`` and
       ``north_velocity_qc``.

----

HAB — height above bottom
--------------------------

``hab`` (height above bottom, in metres) is the vertical distance from
the seabed to the instrument's pressure port or measurement point.

It is used for:

- **Pressure interpolation** (stage 3): instruments without an integrated
  pressure sensor (e.g. some Aquadopp configurations) receive a pressure
  value interpolated from the nearest instruments that do have pressure.
  ``hab`` is needed to know where in the water column to interpolate.
- **Mooring diagram positioning** (``moordiag`` package): controls where
  the instrument icon appears on the schematic.
- **Vertical ordering in stack and grid**: instruments are sorted by depth
  (derived from ``hab`` and ``waterdepth``) when building the stack file.

If ``hab`` is absent from an instrument entry:

- Stages 1 and 2 complete normally.
- Stage 3 will skip pressure interpolation for that instrument if it has
  no native pressure sensor.  A warning is logged identifying the
  instrument by serial number.  The instrument will not contribute to the
  vertically resolved stack and grid.

----

Inline hardware and instruments
--------------------------------

The ``inline`` list describes hardware elements attached to the mooring
line but not in the ``clamp`` list: acoustic releases, floats, rope
sections, shackles, and anchors.  These entries are used by the
``moordiag`` diagram package; ``oceanarray`` ignores hardware-only inline
entries.

An inline entry that also carries ``instrument``, ``filename``, and
``file_type`` fields is treated as an **inline instrument** — typically an
ADCP mounted on a float.  These are processed identically to ``clamp``
instruments.

**Serial with beacon ID**: when an instrument on a float carries both an
instrument serial number and a float beacon ID, enter them separated by a
comma:

.. code-block:: yaml

   inline:
     - serial: "16430, R01-024"
       instrument: ADCP
       filename: 16430_raw.000
       file_type: rdi-raw
       hab_bottom: 12

The first comma-separated token (``16430``) is used as the primary serial
number for filenames and output.  The remainder (``R01-024``) is stored as
the beacon ID.  ``oceanarray validate`` emits a WARNING for any inline
instrument with a comma in the serial so the operator can confirm the
ordering is correct.

For the HAB of an inline instrument, use ``hab_bottom`` if the instrument
faces downward (transducer at the bottom of the housing) or ``hab_top``
if it faces upward.

----

Pending and skipped instruments
--------------------------------

To mark an instrument as not yet available (data file not yet downloaded,
instrument still at sea), use the sentinel value ``file_type: TBD``:

.. code-block:: yaml

   - serial: "99999"
     instrument: microcat
     filename: 99999_recovery.asc
     file_type: TBD
     hab: 200

Processing is skipped for this instrument; it appears in reports as pending.

To skip an instrument that has a data file but should not be processed
(e.g. it was recovered with a fault), set ``skip: true``:

.. code-block:: yaml

   - serial: "26265"
     instrument: microcat
     filename: 26265_recovery.asc
     file_type: sbe-ascii
     hab: 380
     skip: true

----

Position and latitude/longitude priority
-----------------------------------------

Latitude and longitude are used to compute the magnetic declination applied
during Aquadopp coordinate rotation (stage 3).  When multiple position
fields are present, ``oceanarray`` uses the most accurate one:

1. ``seabed_latitude`` / ``seabed_longitude`` — confirmed anchor position;
   highest accuracy.
2. ``deployment_latitude`` / ``deployment_longitude`` — GPS fix from the
   ship at the time of deployment.
3. ``planned_latitude`` / ``planned_longitude`` — pre-cruise planned
   position.
4. ``latitude`` / ``longitude`` — generic fallback.

Only one pair needs to be provided.  Coordinates should be given in
degrees-decimal-minutes format, e.g. ``"65 29.84 N"`` and
``"009 30.12 W"``.  The degrees component for longitude must be
zero-padded to three digits (``009``, not ``9``).

----

Validate your YAML
------------------

Before running any processing, check the YAML for missing fields and
common mistakes:

.. code-block:: bash

   oceanarray validate /path/to/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml

The validator checks that required fields are present, that ``instrument``
and ``file_type`` values are recognised, and that the YAML can be parsed
without errors.  It also emits warnings for inline instruments with commas
in the serial number so you can confirm the ordering.

You can validate multiple files at once:

.. code-block:: bash

   oceanarray validate /path/to/proc/*/  *.mooring.yaml

----

From Python
-----------

The same file is read, written and validated from Python through
``oceanarray.mooring``:

.. code-block:: python

   from oceanarray import mooring

   # Load one mooring's description (a path, a name + proc_dir, or a dict).
   m = mooring.load("proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml")
   m = mooring.load("dsG3_1_2026", proc_dir="proc")

   # Validate without raising (returns a list of issues).
   issues = mooring.check("proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml")

   # Write a commented stub (what ``oceanarray init`` does).
   mooring.save(mooring.template("dsG3_1_2026"), "proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml")

``load`` keeps every key exactly as written and raises ``MooringError`` on an
invalid config; ``check`` never raises.  ``name`` and ``waterdepth`` are
required; ``deployment_time`` and ``recovery_time`` are recommended — if absent,
Stage 2 uses the deployment window detected from the records.
