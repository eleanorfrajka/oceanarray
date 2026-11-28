.. _array_configuration:

===================
The array YAML file
===================

An array is several moorings deployed together on one cruise. Its YAML file,
``{array}.array.yaml``, lists them and the facts they share; each mooring keeps its
own :doc:`mooring YAML file <yaml_configuration>`. Two commands read it:

- ``oceanarray report ARRAY.array.yaml --array [--bathymetry FILE]`` builds the year
  page: a map (with depth contours from a GEBCO or ETOPO file), one row per mooring
  linking to its pages, and the instruments by function.
- ``oceanarray export MOORING --array ARRAY.array.yaml`` takes ``site_code`` and
  ``project`` from it for the OceanSITES files.

.. code-block:: yaml

   name: MIXSED
   year: 2026
   project: AEI-DFG MIXSED
   site_code: MIXSED                # OceanSITES site; needed by the GDAC
   subarrays:
     sill: {name: Denmark Strait sill array, short: Sill}
   moorings:
     - mooring: dsG1_1_2026
       config: dsG1_1_2026.yaml           # only when not {mooring}.mooring.yaml
       subarray: sill                     # moorings are coloured by subarray
     - mooring: dsK1_1_2026
       subarray: sill

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Key
     - Use
   * - ``name``
     - The array's name: the page's file name (default: the YAML file name).
   * - ``moorings``
     - One entry per mooring: ``mooring`` (its name), ``config`` (its YAML's file
       name, only when it is not ``{mooring}.mooring.yaml``; looked for beside the
       array file, then in the mooring's processed folder; a path, relative to the
       array file or absolute, is still accepted and is planned to be deprecated in
       0.7.0) and ``subarray`` (a key of
       ``subarrays``: the map colours moorings by subarray; moorings without one
       are grouped as "other"; without any, one colour per mooring).
   * - ``subarrays``
     - Each subarray's ``name`` (the moorings table's Array column) and ``short``
       name (the map legend). Not written to the OceanSITES ``array`` attribute.
   * - ``year``, ``project``
     - The page's title ("OSNAP East 2016") and header; ``project`` is also
       written into the OceanSITES files.
   * - ``site_code``
     - The OceanSITES site code, written into the exported files (left out, with a
       warning, when absent).

Other keys are not used by the year page, which takes cruises, ships, dates,
positions and depths from each mooring's own YAML file; ``cruise``, ``ship``,
``deployment_time``, ``recovery_time``, ``latitude``, ``longitude``, ``position``,
``waterdepth_min``, ``waterdepth_max``, ``vocab`` and ``bathymetry`` get one warning
naming them. The bathymetry file (GEBCO or ETOPO, one elevation variable on latitude
and longitude) is given on the command line (``--bathymetry``) or as
``oa.report(array_path, paths, kind="array", bathymetry=...)``: the map draws its 500, 1000, 2000
and 2500 m contours, cached beside the page, and names the dataset from the file's
``title`` (e.g. "ETOPO1", "GEBCO 2025").
