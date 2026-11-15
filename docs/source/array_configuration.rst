.. _array_configuration:

===================
The array YAML file
===================

An array is several moorings deployed together on one cruise. Its YAML file,
``{array}.array.yaml``, lists them and the facts they share; each mooring keeps its
own :doc:`mooring YAML file <yaml_configuration>`. Two commands read it:

- ``oceanarray report ARRAY.array.yaml --array`` builds the array page: a map and
  one row per mooring, each linking to the mooring's report.
- ``oceanarray export MOORING --array ARRAY.array.yaml`` takes ``site_code`` and
  ``project`` from it for the OceanSITES files.

.. code-block:: yaml

   name: MIXSED
   year: 2026
   cruise: msm142
   ship: Merian
   project: AEI-DFG MIXSED
   site_code: MIXSED                # OceanSITES site; needed by the GDAC
   deployment_time: '2026-05-05'
   recovery_time: '2026-07-31'
   latitude: 65 34.004 N
   longitude: 29 25.878 W
   waterdepth_min: 700
   waterdepth_max: 1000
   moorings:
     - mooring: dsG1_1_2026
       config: dsG1_1_2026.mooring.yaml   # default: {mooring}.mooring.yaml
       position: '1'
     - mooring: dsK1_1_2026
       position: '2'

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Key
     - Use
   * - ``name``
     - The array's name: the page's title (default: the file name).
   * - ``moorings``
     - One entry per mooring: ``mooring`` (its name), ``config`` (its YAML file,
       relative to the array file's folder) and ``position`` (its label on the
       page, default its order).
   * - ``year``, ``cruise``, ``ship``, ``project``
     - Shown at the top of the array page; ``project`` is also written into the
       OceanSITES files.
   * - ``site_code``
     - The OceanSITES site code, written into the exported files (left out, with a
       warning, when absent).
   * - ``deployment_time``, ``recovery_time``, ``latitude``, ``longitude``,
       ``waterdepth_min``, ``waterdepth_max``
     - The array's span and position, shown at the top of the array page.
   * - ``vocab``
     - Short names for instrument types on the array page, e.g.
       ``{instrument_in: MicroCAT, instrument_out: MC}``.

Other keys (``y_offset``, ``plot_config``, …) are read by the mooring-diagram
tool, not by oceanarray.
