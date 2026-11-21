.. _files:

=================
Files and folders
=================

oceanarray reads from a **raw folder** and writes into a **processed folder**, one
subfolder per mooring in each. Every file it writes is named from the mooring's
``name:`` in its YAML file.

Raw folder
----------

Given with ``--raw-dir`` (``raw_dir=`` in Python). oceanarray only reads it.

.. code-block:: text

   raw/
   └── dsG3_1_2026/                 one folder per mooring
       ├── microcat/                the instrument entry's `instrument:`
       │   ├── 5367_recovery.asc
       │   └── 26261_recovery.asc
       ├── aquadopp/
       │   ├── A400115_dsG3.aqd
       │   └── A400115_dsG3.hdr
       └── adcp/
           └── 12345_raw.000

Each instrument's ``filename:`` is relative to its instrument folder. Setup and
capture files (Sea-Bird ``.xmlcon`` and ``.cap``, Ruskin ``.log``, Nortek ``.prf``,
the RDI command log) can sit beside the data; they are not read but keep the record
complete.

At UHH a raw file is named ``{serial}_recovery.<ext>`` at recovery and
``{serial}_cal_dip_data.<ext>`` from a calibration dip.

Processed folder
----------------

Given with ``--proc-dir``, or found from the path of the mooring's YAML file, which
lives in it:

.. code-block:: text

   proc/
   └── dsG3_1_2026/
       ├── dsG3_1_2026.mooring.yaml        the description
       ├── microcat/
       │   ├── dsG3_1_2026_26261_stage1.nc   as read
       │   ├── dsG3_1_2026_26261_stage2.nc   trimmed, clock-corrected
       │   └── dsG3_1_2026_26261_stage3.nc   QC flags, derived variables
       ├── aquadopp/ ...
       ├── dsG3_1_2026_stack.nc            every instrument on one time axis
       ├── dsG3_1_2026_grid.nc             the stack on pressure levels
       ├── oceansites/                     oceanarray export: OS_*.nc
       ├── processing_logs/                one log per stage and run
       └── report/
           ├── dsG3_1_2026_report.html          summary: start here
           ├── dsG3_1_2026_stack_report.html
           ├── dsG3_1_2026_grid_report.html
           ├── dsG3_1_2026_report.pdf           with report --pdf
           └── instrument/
               └── dsG3_1_2026_26261_report.html

Keep the folder name, the YAML file name and its ``name:`` the same;
``oceanarray validate`` warns when the folder differs. A ``*`` at the end of a serial
is dropped from file names.

Several deployments of one site (:doc:`methods/concatenation`) are written to a
folder of their own beside the deployments:

.. code-block:: text

   proc/
   ├── egM2_2016/ ...
   ├── egM2_2018/ ...
   └── egM2/
       ├── egM2_stackcat.nc
       ├── egM2_gridcat.nc
       └── report/
           ├── egM2_stackcat_report.html
           └── egM2_gridcat_report.html

The OceanSITES files are named ``OS_{platform}_{YYYYMM}_{mode}_{type}-{serial}-{depth}m.nc``
(one per instrument) and ``OS_{platform}_{YYYYMM}_{mode}_GRD_{interval}-DP{step}.nc``
(the grid); see :doc:`methods/oceansites_export`.
