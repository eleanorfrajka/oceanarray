.. _migrating-0-7-0:

=============================
Upgrading from 0.6.1 to 0.7.0
=============================

No pitch or roll flags
----------------------

Tilt QC is on the geometric tilt only (``QC_TILT``, overridden by ``tilt_qc``), so
``qc_ranges: pitch`` / ``roll`` are no longer applied and stage 3 writes no
``pitch_qc`` / ``roll_qc``; the stack no longer carries them. A mooring YAML that
still sets them gets a warning; remove them, and set ``tilt_qc`` to change the
thresholds.
