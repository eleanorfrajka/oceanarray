.. oceanarray documentation master file

==========
oceanarray
==========

**oceanarray** processes moored instrument records, from the raw files to
quality-controlled CF-NetCDF, with an HTML report at each step:

- **Each instrument** — the raw file, or an OceanSITES or RAPID RODB file from
  another group, read to netCDF (stage 1), trimmed to the
  deployment with clock corrections (stage 2), then QC flags, pressure for
  instruments without a sensor, salinity and velocity in east/north/up (stage 3).
- **Each mooring** — every instrument on one time axis (the *stack*), then
  interpolated onto pressure levels (the *grid*).
- **Each site** — the deployments of one site joined end to end (the *Stack cat*),
  and gridded one mooring per profile (the *Grid cat*).
- **Sharing** — the stage-3 files and the grid as OceanSITES 1.4 files.

One command takes a mooring from its raw files to the grid and the report:

.. code-block:: bash

   oceanarray run /data/proc/dsG3_1_2026 --raw-dir /data/raw

What a mooring is made of is written in one YAML file per mooring
(:doc:`yaml_configuration`); every setting and every step is recorded in the files it
writes.

.. figure:: /_static/oceanarray_report_header.png
   :target: _static/demo/dune2/dune2_1_2026_report.html
   :alt: The summary page of the example report
   :align: center
   :width: 80%

   The example report for a short test mooring (click to open it). Every page works
   offline: open it in a browser, no server needed.

Start with :doc:`installation` and the :doc:`quickstart`. Until 1.0, variable names
and attributes in the files can still change between releases; the `changelog
<https://github.com/ocean-uhh/oceanarray/blob/main/CHANGELOG.md>`__ lists every change.

- **Getting started** — :doc:`installation`, the :doc:`quickstart`, and the
  :doc:`reports` with live examples.
- **How it works** — the :doc:`processing_framework` and the :doc:`files`.
- **Methods** — one page per step, from :doc:`methods/standardisation` to the
  :doc:`methods/oceansites_export`.
- **Inputs and outputs** — :doc:`yaml_configuration`, :doc:`array_configuration`,
  and the :doc:`oceanarray_format` of what oceanarray writes.
- **Interfaces** — every :doc:`command <cli_reference>` and the
  :doc:`Python API <oceanarray>`.

.. toctree::
   :maxdepth: 1
   :hidden:
   :caption: Getting started

   installation
   quickstart
   reports
   Example report <https://ocean-uhh.github.io/oceanarray/_static/demo/dune2/dune2_1_2026_report.html>
   troubleshooting

.. toctree::
   :maxdepth: 1
   :hidden:
   :caption: How it works

   processing_framework
   files

.. toctree::
   :maxdepth: 1
   :hidden:
   :caption: Methods

   Stage 1: read <methods/standardisation>
   Stage 2: trim and clocks <methods/trimming>
   methods/auto_qc
   Stage 3: Aquadopp velocity <methods/nortek_coordinate_transform>
   Calibration dips <calibration_dips>
   Stack <methods/time_gridding>
   Grid <methods/vertical_gridding>
   Several deployments of a site <methods/concatenation>
   OceanSITES export <methods/oceansites_export>

.. toctree::
   :maxdepth: 1
   :hidden:
   :caption: Inputs and outputs

   yaml_configuration
   array_configuration
   oceanarray_format
   OceanSITES manual <oceanSITES_manual>

.. toctree::
   :maxdepth: 1
   :hidden:
   :caption: Interfaces

   cli_reference
   Python API <oceanarray>

.. toctree::
   :maxdepth: 1
   :hidden:
   :caption: Upgrading

   From 0.6.0 to 0.6.1 <migrating_0_6_1>
   From 0.5 to 0.6 <migrating_0_6_0>
   Older versions <migration>

.. toctree::
   :maxdepth: 1
   :hidden:
   :caption: Background

   rapid_heritage
   Legacy modules <legacy>

.. toctree::
   :maxdepth: 1
   :hidden:
   :caption: Development

   roadmap
   project_structure
   style_guide
   Calibration correction (design) <methods/calibration>
   GitHub repository <https://github.com/ocean-uhh/oceanarray>
