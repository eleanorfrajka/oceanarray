Stage 1: read the raw files
===========================

Stage 1 reads each instrument's raw file into CF-NetCDF, keeping the values as
recorded: no trimming, no QC. Everything later starts from these files.

.. code-block:: bash

   oceanarray process dsG3_1_2026 --raw-dir /data/raw --proc-dir /data/proc --stage 1

.. code-block:: python

   import oceanarray as oa

   paths = oa.Paths(raw_dir="/data/raw", proc_dir="/data/proc")
   oa.process("/data/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml", paths, stages=1)

What it reads
-------------

Raw files are read through `seasenselib <https://pypi.org/project/seasenselib/>`_;
``seasenselib list`` prints the formats it reads. Each instrument entry names its
file and format (``filename:``, ``file_type:``; see :doc:`../yaml_configuration`).

Files already processed by another group are read too: OceanSITES netCDF
(``file_type: oceansites``) and RAPID RODB text (``file_type: rodb``), for
MicroCATs, current meters and Aquadopps. They go through the same stages as raw
files; their originator's attributes are kept as ``originator_*``, and a unit the
file gets wrong is fixed with ``units_override``:

.. code-block:: yaml

   clamp:
     - serial: "500"
       instrument: aquadopp
       file_type: oceansites
       filename: OS_OSNAP_WHOI_CF7_202007_CM_500m.nc
       hab: 1401

What it does
------------

- **Names** — one name per quantity (``temperature``, ``conductivity``,
  ``pressure``, ``east_velocity``, …; the list is ``parameters.VARIABLES``), and
  the reader's derived variables dropped.
- **Units** — one spelling per quantity (``degree_Celsius``, ``mS cm-1``,
  ``dbar``); a conversion is recorded in ``units_converted``. A unit the file gets
  wrong is fixed with ``units_override`` in the instrument entry, with a reason.
- **Aquadopp velocity** — rotated from the beams to the instrument frame (XYZ) with
  the header's transformation matrix; stage 3 rotates it on to east/north/up.
- **Metadata** — the mooring's name, position, water depth and times, the
  instrument's serial, type and nominal depth, and, for an imported file,
  its people as OceanSITES lists (:doc:`../oceanarray_format`).
- **Provenance** — the oceanarray version, the raw file's name, and
  ``processing_stage = 1``.

Output
------

``{proc_dir}/{mooring}/{instrument}/{mooring}_{serial}_stage1.nc``, on ``time``.
Instruments that fail are named in the log and the run carries on (``--strict``
stops instead).

Before trimming, open the instrument pages (``oceanarray report MOORING
--instruments``) to see where each record starts and ends; see
:doc:`trimming`.
