Calibration — applying caldip corrections at the front of Stage 3
=================================================================

.. note::

   **Status: in progress.** Reading and checking a caldip cast is implemented
   (:mod:`oceanarray.pipeline.calibration.caldip`); choosing stops, combining dips and applying
   the correction are not yet, and Stage 3 output is unchanged. This page is the specification
   for that work; open values are marked **TBD**.

The interface
-------------

``oceanarray.pipeline.calibration`` exports the shared contract — ``CALDIP_SUFFIX``,
``SUPPORTED_SCHEMA_VERSION``, ``CALDIP_FLAG_OK``, ``REQUIRED_PROVENANCE_GLOBALS``,
``PROVENANCE_PASSTHROUGH_GLOBALS`` and ``canonical_serial`` (the shared join-key rule) — and:

- ``read_caldip_cast(path)`` — open and check one ``{cast}_caldip.nc`` (implemented);
- ``instrument_record(cast, serial)`` — one instrument's per-stop record (implemented);
- ``find_caldip_casts``, ``assign_dip_role``, ``select_offsets``, ``apply_caldip`` — still
  raise ``NotImplementedError``.

The division of labour with caldip: **caldip measures, oceanarray decides.** caldip aligns
the clocks, finds the bottle stops and writes, per instrument and stop, the instrument and CTD
values and their difference. oceanarray picks the stops, reduces them to an offset or a slope,
pairs a deployment dip with a recovery dip, and applies the result.

Develop against the committed real cast ``tests/fixtures/caldip/castM4_caldip.nc`` (a
``.cnv``-input cast, so its ``ctd_*`` provenance is legitimately ``UNK``). Stage 3 already
exposes a ``caldip_dir`` argument as a null action that stamps ``caldip_applied`` on each
output.

Calibration applies post-deployment drift corrections to sensor data (temperature,
conductivity, pressure) from calibration-dip comparisons against a shipboard CTD. The
corrections are *computed by the separate* `caldip <https://github.com/ocean-uhh/caldip>`_
*package*; oceanarray only consumes caldip's output. See :doc:`../calibration_dips` for the
dip procedure.

Placement
---------

The correction is **not a separate stage**. It is the optional first step of Stage 3, applied
to the raw values *before* pressure interpolation, so every downstream step (interpolation,
salinity, QC, derived variables) sees corrected data. With no caldip input, Stage 3 output is
unchanged; there is a single ``_stage3.nc``.

**Insertion point.** :func:`oceanarray.stage3` is mooring-level: it receives every
instrument's stage-2 dataset and identifies each pressure variable *before* partitioning
instruments into pressure *sources* and interpolation *targets*. Apply the caldip offsets
**to each stage-2 dataset before the sources/targets partition**, for every
instrument. The correction is per-instrument, but it must run inside the mooring-level pass: a
sensorless target's pressure is interpolated from its neighbours, so those neighbours must
already be corrected. Applying per-instrument *after* the partition — or in a separate
per-instrument stage — gets the pressure ordering wrong.

Input
-----

``--caldip-dir`` gives the root directory of caldip output only. The mooring YAML names the
cast for each deployment end, so a mooring whose two dips were processed on different cruises
resolves correctly::

    caldip:
      deployment: {cruise: msm142_2026, cast: castM6}
      recovery:   {cruise: <later cruise>, cast: <cast>}

A mooring with a single dip is an ordinary case; the attributes record which end supplied the
correction.

**Path resolution** is TBD (it lands with the ``caldip:`` block): each end's file is
``{cast}_caldip.nc`` beneath the root, the YAML ``cruise`` disambiguating a recurring cast
name, and the resolved path is recorded in the attributes.

Reading a cast
--------------

caldip writes one netCDF per cast, ``{cast}_caldip.nc`` (``schema_version = 1``), with
dimensions ``instrument`` and ``stop``. Per instrument and stop: ``{temp,cond,press}_diff``
(**instrument − CTD**), ``{…}_std``, ``N``, ``{…}_flag`` (``ok``, ``no_data``, ``flagged``,
``missing``, ``unknown``) and ``inst_{temp,cond,press}``; per stop: ``ctd_{temp,cond,press}``,
``bl_press`` (a nominal stop label — select stops by the measured ``ctd_press``, never by
position or by ``bl_press``), ``time_start``, ``time_end``. Absent values are ``NaN``.

:func:`~oceanarray.pipeline.calibration.caldip.read_caldip_cast` decides as follows::

    open the file, load it, close it
    if any of REQUIRED_PROVENANCE_GLOBALS is missing      → refuse (CaldipProvenanceError)
    if schema_version ≠ SUPPORTED_SCHEMA_VERSION          → refuse (CaldipError)
    for each of temp, cond, press:
        for each of <x>_diff, <x>_std, inst_<x>, ctd_<x> present:
            resolve its units against the registry unit for the quantity
                (temperature: degree_Celsius, conductivity: mS cm-1, pressure: dbar)
            if the spelling is not recognised             → refuse (CaldipError)
            multiply by the conversion factor (1 for a pure spelling) and write the
                registry spelling
    serial ← canonical_serial(serial)        (filename-safe, leading zeros stripped)
    if two instruments share a serial                     → refuse (CaldipError)
    if an instrument_type is outside oceanarray's vocabulary → warn, keep going
    record the file path in attrs["source_path"]

A refused cast is never applied. :func:`~oceanarray.pipeline.calibration.caldip.instrument_record`
then returns one instrument's slice over ``stop``, or ``None`` when the serial is not in the
cast.

Sign convention — the highest-risk line
----------------------------------------

``*_diff = instrument − CTD``. To correct an instrument toward the CTD reference, **subtract**
the offset::

    corrected = measured − diff

Getting this backwards doubles the error (``measured + diff`` lands ``2 × diff`` from truth) and
is silent. Derive the subtraction direction from this documented column semantics, state it in
the module docstring, and **verify it against caldip before applying**. Record both the raw and
the reference value at the stop (``caldip_inst_<var>`` and ``caldip_ctd_<var>``) so the sign is
checkable by eye after the fact — the same both-sides pattern the clock correction uses.

Units guard
-----------

The reader resolves every temperature, conductivity and pressure variable against the
registry unit (above), so a future units change in caldip either converts exactly or fails
loudly — it cannot scale the data by ten. caldip writes ``degree_C``, ``mS cm-1`` and ``dbar``
today. Before applying, the target variable in the stage-2 dataset is in the same registry
unit, because every stage writes canonical units.

Per-variable stop selection
---------------------------

Stop selection is **per variable** — not one policy applied to three variables:

.. list-table::
   :header-rows: 1

   * - Variable
     - Stop selection
     - Shape
   * - pressure
     - stop nearest the instrument's deployment pressure (``waterdepth − hab``, dbar)
     - single offset
   * - temperature
     - deepest stop
     - single offset
   * - conductivity
     - deepest stop, or a fit across stops
     - offset, or a slope ``a·C + b`` when the stops span a wide conductivity range

Application thresholds
----------------------

Apply a correction only when it exceeds its own noise; otherwise record it, warn, and leave the
value unchanged (``caldip_applied = "none — below <var> threshold"``):

- **Pressure:** apply only when ``|offset| > 5 dbar`` **and** the stop std is relatively low.
- **Temperature:** apply only above a minimum threshold — **value TBD, do not invent.**
- **Conductivity:** the low-std / near-deployment-C test gates the single offset; the slope path
  is gated by fit quality — **slope fit form and thresholds TBD, do not invent.**

These are **apply** thresholds. Do not confuse them with the pre-deployment **acceptance**
tolerances in :doc:`../calibration_dips` (0.05 mS/cm, 0.005 °C, 5 dbar), which decide whether an
instrument is fit to deploy — a different purpose with different numbers.

Pre- and post-dip: linear time trend
-------------------------------------

One dip gives a constant correction. Two dips bracketing a deployment give a value at each end,
applied by **linear interpolation in time** across the deployment (interpolate the offset, or a
slope's parameters, between the two dips) — the same offset-vs-drift pattern oceanarray uses for
clocks. Record ``ramp_basis = two_endpoint_linear_interpolation_in_time`` and a ``ramp_note``
that the linearity is **assumed, not measured** (two endpoints always fit a line perfectly).
Conductivity is the exception worth surfacing: biofouling can step mid-deployment, invisible to a
two-point fit.

Provenance attributes
---------------------

Per corrected variable, on the ``_stage3.nc`` output, record at least: the applied offset (or
slope parameters) with units; the offset std the threshold was tested against; the stop-selection
policy named; the stop(s) used; ``ctd_sensor_used`` (or ``UNK``); the resolved source filename;
both ``caldip_inst_<var>`` and ``caldip_ctd_<var>`` at the stop; and ``caldip_ctd_slope_adjusted``
= ``UNK`` until caldip emits whether the CTD conductivity was slope-adjusted for bottle salts. A
skipped correction is an explicit ``caldip_applied = "none — <reason>"``, never a missing
attribute. Never silently substitute a default.

Acceptance criteria
-------------------

- Without ``--caldip-dir``, ``_stage3.nc`` is **byte-identical** to today's output.
- A sensorless instrument's interpolated pressure is derived from **corrected** neighbour
  pressures, not raw — verifiable against a hand-computed value.
- Both the raw instrument value and the CTD reference value are recorded per corrected variable,
  so the applied offset is reproducible by subtraction.

Legacy reference (RAPID)
------------------------

The RAPID MATLAB script ``microcat_apply_cal_plus.m`` implements the same idea (pre/post-cruise
offsets, pressure and conductivity–pressure drift, diagnostic plots, ``.microcat.txt`` provenance
logs) and is kept as prior art:

.. literalinclude:: ../_static/code/microcat_apply_cal_plus.m
   :language: matlab
   :lines: 1-40
   :linenos:
   :caption: Excerpt from ``microcat_apply_cal_plus.m``
