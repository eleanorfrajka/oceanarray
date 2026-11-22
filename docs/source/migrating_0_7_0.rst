.. _migrating-0-7-0:

=============================
Upgrading from 0.6.1 to 0.7.0
=============================

Stage files carry ``clock_offset`` only when applied
----------------------------------------------------

Stage 1 no longer writes a ``clock_offset`` of 0 (it applies no clock correction),
and stage 2 writes the ``clock_offset`` variable only when it applies a non-zero
offset. A file with no offset therefore has no ``clock_offset`` variable: read it
with ``ds.get("clock_offset", 0)`` (an absent variable means no offset), not
``ds["clock_offset"]``. Rerun stage 2 to update existing files.

The clock tools moved to ``analysis``
-------------------------------------

``oceanarray.pipeline.clock`` (``offsets``, ``deployment``) is now
``oceanarray.analysis.clock``, unchanged. The old path still imports, with a
``DeprecationWarning``, until 0.8.0; change ``from oceanarray.pipeline.clock import …``
to ``from oceanarray.analysis.clock import …``.

One base for the package's errors
---------------------------------

Every error oceanarray raises now derives from ``OceanarrayError``
(``MooringError``, ``ReaderError``, ``StageError``, ``UnitsError``, ``OceanSitesError``,
``CaldipError``, ``LegacyLayoutError``); each keeps its built-in base, so ``except ValueError``
still works. A file a reader cannot read raises ``ReaderError``, and a problem in the
mooring YAML found while processing raises ``MooringError``; both were a plain
``ValueError``. ``StageError`` is no longer a ``MooringError``: code that caught a
wrong-stage file with ``except MooringError`` should catch ``StageError`` or
``OceanarrayError``. ``StageError`` and ``ValidationIssue`` are defined in
``oceanarray.exceptions``; import them from there (or ``StageError`` from
``oceanarray``).

Data warnings are log records
-----------------------------

Problems found while reading or reporting (records dropped for a missing time,
unexpected units, a stage file without its marker, an attribute dropped on writing)
were Python ``UserWarning`` s; they are now ``WARNING`` records of the ``oceanarray``
logger, so they also reach the stage log files. Code that caught them with
``warnings.catch_warnings`` or ``pytest.warns`` should read the log instead
(``caplog`` in pytest). Deprecations are still ``DeprecationWarning`` s.

A missing folder or mooring YAML raises
---------------------------------------

``MooringReport.generate`` and ``generate_recovery_table`` returned ``None``, and
``Stage2Processor`` / ``Stage3Processor.process_mooring`` returned ``False``, when the
processing folder or the mooring YAML was missing, printing the reason. They now raise
``MooringError`` with the path in the message. Replace a check for ``None`` or
``False`` with ``except MooringError``. The ``oceanarray`` commands already stopped
with an error; only the message changes.

No pitch or roll flags
----------------------

Tilt QC is on the geometric tilt only (``QC_TILT``, overridden by ``tilt_qc``), so
``qc_ranges: pitch`` / ``roll`` are no longer applied and stage 3 writes no
``pitch_qc`` / ``roll_qc``; the stack no longer carries them. A mooring YAML that
still sets them gets a warning; remove them, and set ``tilt_qc`` to change the
thresholds.
