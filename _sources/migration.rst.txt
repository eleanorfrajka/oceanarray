.. _migration:

========================================================
Older upgrades: 0.4 to 0.5, and the ``--basedir`` layout
========================================================

For 0.6.0 to 0.6.1, see :doc:`migrating_0_6_1`. For 0.5 to 0.6, see :doc:`migrating_0_6_0`.

----

Upgrading from 0.4 to 0.5
-------------------------

0.5.0 is a breaking release.  If you are upgrading an existing install:

- **Reprocess from stage 1 for files you distribute.**  Old stage files still
  load — the canonical-units pass rewrites the reader spellings (``degC`` →
  ``degree_Celsius``, ``mS/cm`` → ``mS cm-1``, …) on the way in and logs one line
  per variable — but files you *share* should be regenerated so they carry the
  canonical strings
  (`#102 <https://github.com/ocean-uhh/oceanarray/pull/102>`__).
- **``save_dataset`` / ``save_OS_instrument`` are gone.**  Call
  ``oceanarray.writers.write`` instead
  (`#99 <https://github.com/ocean-uhh/oceanarray/pull/99>`__).
- **Time encoding is pinned** to ``seconds since 1970-01-01`` (float64).
  Re-encoding introduces at most 0.13 µs of rounding in the decoded instants
  (≤ 119 ns at the 2026 epoch), but the on-disk representation changes, so a
  reader using ``decode_times=False`` / ``decode_cf=False`` sees different raw
  units and numbers
  (`#99 <https://github.com/ocean-uhh/oceanarray/pull/99>`__).
- **``oceanarray process`` runs stages 1–3 by default.**  Pass ``--stage 1 2``
  for the old raw→trim-only behaviour
  (`#102 <https://github.com/ocean-uhh/oceanarray/pull/102>`__).

----

This page describes how to move from the older instrument-first raw
directory layout (used with the ``--basedir`` flag) to the current
mooring-first layout (used with ``--raw-dir`` and ``--proc-dir``).

----

What changed
------------

**Raw directory layout**

In the old layout, raw files were organised by instrument type first, with
mooring subdirectories beneath:

.. code-block:: text

   {basedir}/raw/
   └── microcat/
       └── dsG3_1_2026/
           ├── 5367_recovery.asc
           └── 26261_recovery.asc

In the new layout, mooring directories come first and instrument
subdirectories appear beneath them:

.. code-block:: text

   {raw_dir}/
   └── dsG3_1_2026/
       ├── microcat/
       │   ├── 5367_recovery.asc
       │   └── 26261_recovery.asc
       └── aquadopp/
           └── A400115_dsG3.aqd

**CLI flags**

The ``--basedir`` flag is replaced by two separate flags:

- ``--raw-dir`` — points to the cruise-level directory that holds raw
  instrument data.
- ``--proc-dir`` — points to the cruise-level directory that holds
  processed data and YAML files.

For a cruise where ``basedir`` was ``/data/cruise2026``, the equivalent
new flags are:

.. code-block:: bash

   # Old
   oceanarray process dsG3_1_2026 --basedir /data/cruise2026

   # New
   oceanarray process dsG3_1_2026 \
       --raw-dir /data/cruise2026/raw \
       --proc-dir /data/cruise2026/proc

----

The YAML file stays where it was
---------------------------------

The YAML was already on the processed side in the old layout:
``{basedir}/proc/{mooring}/{mooring}.mooring.yaml``.  The new layout
keeps it in the same relative location:
``{proc_dir}/{mooring}/{mooring}.mooring.yaml``.

No changes to the YAML file itself are needed for migration.

If your YAML contains a ``directory`` key pointing to a raw-file override
path under the old ``{basedir}/raw/`` tree, you may remove it — in the
new layout the directory is determined automatically from
``{raw_dir}/{mooring}/{instrument}/``.  If you leave the ``directory``
key in place and it points to a valid path, it will still be used as an
absolute-path override.

----

Step-by-step migration
-----------------------

The steps below assume your old layout was::

   /data/cruise2026/raw/{instrument}/{mooring}/filename
   /data/cruise2026/proc/{mooring}/{mooring}.mooring.yaml

and you want to migrate to::

   /data/cruise2026/raw/{mooring}/{instrument}/filename
   /data/cruise2026/proc/{mooring}/{mooring}.mooring.yaml    ← no change

1. **Create the new mooring directory inside raw:**

   .. code-block:: bash

      mkdir -p /data/cruise2026/raw/dsG3_1_2026

2. **Move raw files for each instrument:**

   .. code-block:: bash

      mkdir -p /data/cruise2026/raw/dsG3_1_2026/microcat
      mv /data/cruise2026/raw/microcat/dsG3_1_2026/* \
         /data/cruise2026/raw/dsG3_1_2026/microcat/

      mkdir -p /data/cruise2026/raw/dsG3_1_2026/aquadopp
      mv /data/cruise2026/raw/aquadopp/dsG3_1_2026/* \
         /data/cruise2026/raw/dsG3_1_2026/aquadopp/

   Repeat for each instrument type present on the mooring.

3. **The YAML does not move** — it stays in
   ``/data/cruise2026/proc/dsG3_1_2026/``.

4. **Update shell scripts** to use ``--raw-dir`` and ``--proc-dir``
   instead of ``--basedir``:

   .. code-block:: bash

      # Replace
      oceanarray run dsG3_1_2026 --basedir /data/cruise2026

      # With
      oceanarray run dsG3_1_2026 \
          --raw-dir /data/cruise2026/raw \
          --proc-dir /data/cruise2026/proc

----

``--basedir`` has been removed
------------------------------

The ``--basedir`` flag no longer runs.  Passing it prints a migration message
naming the replacement and exits without processing, so existing scripts that
use it must be updated to ``--raw-dir`` + ``--proc-dir`` and the raw data moved
to the mooring-first layout (see the mapping above).

----

Verify after migration
-----------------------

After moving the files, confirm that the YAML is still valid and that stage
1 can read the raw files in their new location:

.. code-block:: bash

   oceanarray validate \
       /data/cruise2026/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml

   oceanarray process dsG3_1_2026 \
       --raw-dir /data/cruise2026/raw \
       --proc-dir /data/cruise2026/proc \
       --stage 1 --only 26261 --force

If stage 1 completes without errors, the migration was successful.  The
output file is the same as before — the only change is where ``oceanarray``
looked for the input.
