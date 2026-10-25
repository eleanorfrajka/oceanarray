.. oceanarray documentation master file

===============================================================
Oceanarray: Methods and Workflows for Mooring Array Processing
===============================================================

This repository documents and demonstrates the processing steps required to
convert raw instrument data from oceanographic mooring arrays into scientifically
useful transport and circulation diagnostics such as the meridional overturning
circulation (MOC).

It provides methods, example code, and reference documentation for modular,
reproducible processing pipelines based on multi-mooring observational arrays.

.. warning::

   **Output format is not yet stable.**  Variable names in Stage 1–3, stack, and
   grid NetCDF files are being revised for CF and OceanSITES compliance before the
   1.0 release.  Do not build downstream scripts that depend on specific variable
   names until the renaming audit is complete.  See the :doc:`roadmap` for status.

Contents
--------

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   quickstart
   installation
   troubleshooting


.. toctree::
   :maxdepth: 2
   :caption: Processing framework

   processing_framework
   reports
   directory_structure
   file_naming_conventions

.. toctree::
   :maxdepth: 1
   :caption: Stage 1 2 3 - Instrument

   0. Data Acquisition <methods/acquisition>
   1. Standardisation <methods/standardisation>
   2. Clock Offset Analysis (optional) <clock_offset>
   2. Trim to Deployment   <methods/trimming>
   3. Apply Calibration  <methods/calibration>
   3. Automatic QC  <methods/auto_qc>
   3. Coordinate Transform   <methods/nortek_coordinate_transform>

.. toctree::
   :maxdepth: 1
   :caption: Stack & Grid - Moorings

   Stack into one NetCDF <methods/time_gridding>
   Grid onto pressure <methods/vertical_gridding>

.. toctree::
   :maxdepth: 1
   :caption: Caldips

   Calibration dips <calibration_dips>

.. toctree::
   :maxdepth: 1
   :caption: Reference

   GitHub Repo <https://github.com/ocean-uhh/oceanarray>
   Python API <oceanarray>
   cli_reference
   yaml_configuration
   project_structure
   oceanarray_format
   OceanSITES manual <oceanSITES_manual>
   Legacy modules <legacy>

.. toctree::
   :maxdepth: 1
   :caption: Changelog / Migration

   migration

.. toctree::
   :maxdepth: 1
   :caption: Development

   roadmap
   style_guide
   Convert to OceanSITES (Stage 4) <methods/conversion>
   Combine Deployments <methods/concatenation>
   Multi-site Merging <methods/multisite_merging>


Indices and Tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
