Calibration — applying caldip corrections at the front of Stage 3
=================================================================

.. note::

   **Status: in progress.** Reading and checking a caldip cast
   (:mod:`oceanarray.pipeline.calibration.caldip`), finding the dips the mooring YAML names
   (:mod:`oceanarray.pipeline.calibration.dips`) and reducing a dip's stops to an offset or a
   slope (:mod:`oceanarray.pipeline.calibration.stops`) are implemented; combining dips and applying
   the correction are not yet, and Stage 3 output is unchanged. This page is the specification
   for that work; open values are marked **TBD**.

The interface
-------------

``oceanarray.pipeline.calibration`` exports the shared contract — ``CALDIP_SUFFIX``,
``SUPPORTED_SCHEMA_VERSION``, ``CALDIP_FLAG_OK``, ``REQUIRED_PROVENANCE_GLOBALS``,
``PROVENANCE_PASSTHROUGH_GLOBALS`` and ``canonical_serial`` (the shared join-key rule) — and:

- ``read_caldip_cast(path)`` — open and check one ``{cast}_caldip.nc`` (implemented);
- ``instrument_record(cast, serial)`` — one instrument's per-stop record (implemented);
- ``caldip_ends(block, caldip_dir=, yaml_dir=)``, ``caldip_file``, ``caldip_root`` — the file
  for each end the YAML names (implemented);
- ``assign_dip_role(cast_time, deployment_time, recovery_time)`` and ``cast_time(cast)`` —
  which end a cast is for an instrument (implemented);
- ``reduce_dip(record, variable, scheme=, deployment_pressure=, slope=)`` — one dip's offset
  or slope for one variable (implemented);
- ``apply_caldip`` — still raises ``NotImplementedError``.

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

The mooring YAML names the cast for each end of the deployment, so a mooring whose two dips
were processed on different cruises resolves both (see :doc:`../yaml_configuration`,
*Calibration dips*)::

    caldip:
      deployment: {cruise: msm142_2026, cast: castM4}
      recovery:   {cruise: odb2026, cast: castX}
      scheme: standard

**A. Which dips apply** (:mod:`oceanarray.pipeline.calibration.dips`)::

    root ← caldip_dir= (the run-time root) if given,
           else caldip.root (relative to the YAML's folder)
    if no root                                   → error ("no caldip root")
    for each end in (deployment, recovery) the block names:
        file ← <root>/<cruise>/<cast>_caldip.nc  if it exists,
               else the one <cast>_caldip.nc beneath root with <cruise> on its path
        if more than one such file                → error (ambiguous)
        if none                                   → end unavailable
    for each instrument:                                                (lands with the apply)
        if its serial is not in an end's cast     → that end unavailable for it
        role ← pre  if cast time < deployment_time
               post if cast time > recovery_time
               none otherwise            (cast time = middle of the cast's stops)
        if role disagrees with the YAML end       → warning, the YAML end is used
        if override use = pre / post              → drop the other end
        if no end is available                    → no correction ("no usable dip")

A mooring with a single dip is an ordinary case; the attributes record which end supplied the
correction.

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

Reducing a dip to one number
----------------------------

One dip's bottle stops become one offset per variable — or, for conductivity under
``standard``, one Sea-Bird AN31 slope (:mod:`oceanarray.pipeline.calibration.stops`,
:func:`~oceanarray.pipeline.calibration.stops.reduce_dip`). The mooring YAML picks the scheme
(``caldip.scheme``); ``standard`` is the default, ``rapid`` reproduces the RAPID MicroCAT
processing. Pressures are the CTD's (``ctd_press``), in dbar; values marked *provisional* are
defaults still to be confirmed.

**B. Usable and stable stops** (per instrument, per variable)::

    usable ← stops whose <var>_diff and ctd_press are finite and whose <var>_flag is
             ok                    (caldip schema 2: flags record facts only)
             ok or flagged         (schema 1: "flagged" only means the instrument reads
                                    off by more than caldip's tolerance — what the
                                    correction is for)
    stable (standard only; rapid has no stability test, as RAPID had none):
        temperature:  usable stops with ctd_temp_std ≤ 0.002 °C
        conductivity: usable stops with ctd_cond_std ≤ 0.003 mS/cm
        pressure:     every usable stop (its scatter at a stop is ship heave)
        a cast without ctd_<var>_std (schema 1): every usable stop, recorded as
        "stability not checked"

The stability limits test the **CTD**, the reference: a stop is unusable when the water there
was too variable to compare, not because the instrument reads off. On castM4 the CTD standard
deviations over caldip's 121-s windows are, for T and C, ≤ 0.0015 at 2029 and 1522 dbar and
0.004–0.010 at 909, 355 and 52 dbar — mostly ship heave moving the CTD through the vertical
gradient.

**C. Temperature — standard**::

    if no stable stop                    → none ("no stable stop" / "no usable stop")
    offset ← temp_diff at the stable stop with the largest ctd_press

**D. Conductivity — standard (AN31 slope)**::

    deep ← stable stops deeper than 500 dbar
    if fewer than 2:  deep ← stable stops deeper than 200 dbar
    if none           → none ("no stable stop below 200 dbar"); never a shallower stop
    α ← inst_cond, β ← ctd_cond over deep
    w ← N / cond_std²   (equal weights if any std is missing or zero)
    slope ← Σ w·α·β / Σ w·α²
    σ_slope ← √( Σ w·r² / (n − 1) / Σ w·α² ),  r = β − slope·α   (undefined for one stop)
    record the stops, n, σ_slope, the RMS of r and the conductivity range of the stops

    with a mean or ramp override: offset ← the mean cond_diff over the same stops

The slope is through the origin (zero offset), following Sea-Bird Application Note 31:
conductivity cells drift in span, and an offset error is a sign of a faulty sensor. On castM4
the stop differences also change with pressure, by about the size of a cell-compressibility
term; the deep-stop rule keeps the fit near the deployment pressures until the cause is
settled.

**G. Pressure — standard**::

    near ← usable stops within 200 dbar of the deployment pressure        (provisional)
    if none:  near ← the single usable stop nearest the deployment pressure
    offset ← mean press_diff over near

**H. The rapid scheme** (RAPID's ``microcat_insitu_cal.m``)::

    temperature, conductivity:
        interval ← the deepest 500 dbar of the usable stops                (provisional)
        hold the deepest stop's difference 100 dbar below it
        interpolate the differences linearly in pressure onto a 20-dbar grid over interval
        offset ← mean over the grid points the stops reach
    pressure:
        if the deployment pressure is shallower than every stop → none
        within the stops (or ≤ 100 dbar below the deepest): linear interpolation
        deeper still: a polynomial in pressure through all usable stops (cubic from four)

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

- Without a ``caldip:`` block, ``_stage3.nc`` is **byte-identical** to today's output.
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
