================
Legacy Modules
================

.. note::
   The RODB/RAPID format modules in ``oceanarray/legacy/`` are **not used** in the
   active processing pipeline.  They are retained in the repository as a reference
   for the RAPID-MOC data format and as a migration aid for analysts working with
   older RAPID datasets.  No new features will be added to these modules.

Background
==========

The `RAPID-MOC array <https://rapid.ac.uk/>`_ stores its processed mooring data in
a proprietary binary format called RODB (RAPID Ocean DataBase).  The legacy modules
can read and parse that format.  They were the original processing functions
developed for RAPID-style workflows and predate the current CF-compliant pipeline.

``oceanarray`` now uses a fully YAML-driven, CF-NetCDF pipeline (Stages 1–3 +
Stack/Grid) that reads raw instrument files directly and does not require RODB as an
intermediate step.

Legacy module inventory
=======================

The following files live in ``oceanarray/legacy/`` and are **not imported** by any
active processing code:

``rodb.py``
   Low-level RODB binary reader (``RodbReader`` class).

``process_rodb.py``
   Per-instrument processing functions operating on RODB data.

``mooring_rodb.py``
   Mooring-level stacking and filtering for RODB data — superseded by
   :mod:`oceanarray.pipeline.stack` and :mod:`oceanarray.pipeline.grid`.

``convertOS.py``
   OceanSites format conversion from RODB — superseded by the Stage 1–3 pipeline.
   Its YAML inputs live beside it: ``OS1_variable_names.yaml``,
   ``OS1_variable_attributes.yaml`` and ``OS1_sensor_attributes.yaml``.

``rapid_interp.py`` (top-level)
   RAPID-specific interpolation helpers — not part of the active pipeline.

Demo notebooks for the legacy workflow live in ``notebooks/legacy/`` and are kept
for reference only.

Active pipeline
===============

For all current processing use:

.. code-block:: bash

   oceanarray process <mooring> --raw-dir /path/to/data/raw --proc-dir /path/to/data/proc --stage 1 2 3 stack grid
   oceanarray report  <mooring> --proc-dir /path/to/data/proc --instruments --stack --grid

See :doc:`processing_framework` for the full pipeline description.
