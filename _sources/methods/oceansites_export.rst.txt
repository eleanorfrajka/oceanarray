OceanSITES export
=================

oceanarray writes its products as `OceanSITES 1.4
<https://www.ocean-ops.org/oceansites/docs/oceansites_data_format_reference_manual.pdf>`_
files (see :doc:`../oceanSITES_manual`): ``format_version = "1.4"``, ``Conventions``
naming CF, OceanSITES-1.4 and ACDD.  The files follow the format; they are not yet ready
to submit to a GDAC, which needs a ``site_code`` approved by the OceanSITES Project Office.

Running the export
------------------

After stage 3 (and the grid, if one is wanted), from the command line:

.. code-block:: bash

   oceanarray export /data/proc/dune2_1_2026 --array /data/proc/MIXSED2026.array.yaml
   oceanarray export dune2_1_2026 --proc-dir /data/proc -n     # list the file names only

or from Python:

.. code-block:: python

   import oceanarray as oa

   oa.export("/data/proc/dune2_1_2026/dune2_1_2026.mooring.yaml",
             array="/data/proc/MIXSED2026.array.yaml")

The files go to ``<mooring folder>/oceansites/`` (``-o DIR`` / ``out_dir=`` to change it).
An existing file is kept unless ``--force`` (``overwrite=True``) is given.  An instrument
that cannot be exported is listed and left out; the others are written.  ``--no-grid``
(``grid=False``) leaves the grid out.

The mooring YAML needs ``platform_code``; ``deployment_code`` (``YYYYMM``) is optional and
taken from ``deployment_time`` when absent — ``oceanarray validate`` warns when the two
disagree.  The array YAML (``--array``) supplies ``site_code`` and ``project``.  See
:doc:`../yaml_configuration`.

Products
--------

.. list-table::
   :header-rows: 1
   :widths: 20 25 55

   * - oceanarray file
     - OceanSITES product
     - file name
   * - ``{mooring}_{serial}_stage3.nc``
     - deployment data file (manual §4.1), one instrument, native sampling
     - ``OS_<platform>_<YYYYMM>_<mode>_<type>-<serial>-<depth>m.nc``
   * - ``{mooring}_grid.nc``
     - gridded file (§4.2), time and pressure grid
     - ``OS_<platform>-<YYYYMM>_<start>-<end>_GRD_<interval>-DP<step>.nc``

- ``<platform>`` is ``platform_code`` from the mooring YAML (the station, e.g. ``DUNE2``);
  it may not contain ``_``, the field separator.
- ``<YYYYMM>`` is the month of ``deployment_time``.
- ``<mode>`` is ``P``, ``D`` or ``M`` (mixed), from the data modes below.
- ``<type>`` is ``CTD`` (with pressure, measured or interpolated from neighbouring
  instruments and flagged 8), ``CT`` (no pressure at all), ``CTDO`` (CTD and oxygen), ``O`` (oxygen only), ``T``
  (temperature only), ``CM`` (current meter) or ``ADCP``; ``<depth>`` is the nominal depth in whole
  metres.
- ``<interval>`` is the time step in ISO 8601 (``PT10M``); ``<step>`` the pressure step in
  dbar.

An ADCP is one file with its bins on ``DEPTH``: the nominal transducer depth plus (down-looking)
or minus (up-looking) the distance to each bin, from the orientation in the mooring
description or, without one, the instrument header; with neither, the export refuses.
``PRES`` holds the pressure at each bin, computed from the transducer's measured
pressure, which the file also carries as ``TRANSDUCER_PRES(TIME)`` (its nominal depth in
``sensor_depth``): ``DEPTH`` is nominal, and the pressures show the knock-down.  The below-seabed flag that stage 3 keeps
apart (``seabed_qc``) is merged into each velocity's ``_QC`` where it failed or was
suspect, the worse flag winning; a passed seabed test leaves the flag as it was.

The stack (instruments on one time base) is not exported yet; it will be a gridded file
whose ``DEPTH`` levels each say how they reached the time base (nearest-neighbour
subsample or linear interpolation).

The grid stays on its pressure levels: ``PRES(DEPTH)`` holds the levels, and ``DEPTH`` is
each level converted with ``gsw.z_from_p`` at the mooring latitude, so nothing is
re-interpolated.  Its ``history`` names the grid and the stage files the stack was built
from (manual §4.2.1).  A gridded file carries no ``_QC`` variables (§4.2.1: only good
source data went into it).

Data mode
---------

``P`` (provisional) until the calibration-dip correction is applied.  ``D`` (delayed
mode) is set per variable after a manual review, recorded as ``data_mode`` on the
instrument entry (one letter, or a mapping from variable name to letter), and is refused
unless the stage-3 file records the calibration-dip correction.  In a deployment file
each variable carries ``<PARAM>:DM_indicator`` and the global ``data_mode`` is the
single mode, or ``M`` when the variables differ.

A grid records no per-input data modes, so only a provisional (``P``) grid is exported;
any other grid is refused.  ``<PARAM>_DM`` arrays, which a gridded file would need when
its levels mixed modes, are not written.
