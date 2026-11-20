.. _array_configuration:

===================
The array YAML file
===================

An array is several moorings deployed together on one cruise. Its YAML file,
``{array}.array.yaml``, lists them and the facts they share; each mooring keeps its
own :doc:`mooring YAML file <yaml_configuration>`. Two commands read it:

- ``oceanarray report ARRAY.array.yaml --array`` builds the array page: a map, one
  row per mooring linking to its pages, and the instruments by type.
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
   bathymetry: ../bathymetry/GEBCO_2025.nc   # depth contours on the map
   moorings:
     - mooring: dsG1_1_2026
       config: dsG1_1_2026.mooring.yaml   # default: {mooring}.mooring.yaml
       line: Denmark Strait sill          # moorings are coloured by line
     - mooring: dsK1_1_2026
       line: Denmark Strait sill

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Key
     - Use
   * - ``name``
     - The array's name: the page's title (default: the file name).
   * - ``moorings``
     - One entry per mooring: ``mooring`` (its name), ``config`` (its YAML file,
       relative to the array file's folder) and ``line`` (the line it belongs to:
       the map colours moorings by line; without lines, one colour per mooring).
   * - ``year``, ``project``
     - Shown at the top of the array page (the year the moorings were deployed);
       ``project`` is also written into the OceanSITES files.
   * - ``bathymetry``
     - A bathymetry file (GEBCO or ETOPO, one elevation variable on latitude and
       longitude), relative to the array file: the map draws its 500, 1000, 2000
       and 2500 m contours, cached beside the page.
   * - ``site_code``
     - The OceanSITES site code, written into the exported files (left out, with a
       warning, when absent).
Other keys (``cruise``, ``ship``, ``deployment_time``, ``recovery_time``,
``latitude``, ``longitude``, ``waterdepth_min``, ``waterdepth_max``, ``vocab``,
``position``, ``y_offset``, ``plot_config``, …) are not used by the array page,
which takes dates, positions and depths from each mooring's own YAML file.
