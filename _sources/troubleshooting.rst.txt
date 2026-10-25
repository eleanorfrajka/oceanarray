Troubleshooting
===============

This page collects common installation and runtime problems and their solutions.
It was assembled from real installation notes on macOS (Python 3.11, Homebrew).

.. contents:: On this page
   :local:
   :depth: 1

----

Installation problems
---------------------

seasenselib / pyproj build failures
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``seasenselib`` (the raw-file reader, installed automatically with
``oceanarray``) and its dependency ``pyproj`` occasionally fail to build from
source on some platforms or pip versions.

**If pyproj fails to install**, force a pre-built binary wheel, then re-run the
install:

.. code-block:: bash

   pip install --upgrade \
       --force-reinstall \
       --no-cache-dir \
       --only-binary=:all: \
       "pyproj>=3.7.0"

   pip install oceanarray

If all else fails, recreate the virtual environment with Python 3.11 exactly
(``python3.11 -m venv venv``) and start from scratch.

----

Environment problems
--------------------

Commands not found after closing the terminal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The virtual environment must be activated in every new terminal session.
If you use a **venv**:

.. code-block:: bash

   source venv/bin/activate        # macOS / Linux
   # venv\Scripts\activate         # Windows

If you use **conda**:

.. code-block:: bash

   conda activate oceanarray

If you also use a local shell script to set ``DATA_BASE`` or similar
environment variables, source that too before running ``oceanarray``
commands:

.. code-block:: bash

   source shlocal/run_mooring.sh   # example — path will differ

----

Runtime problems
----------------

``oceanarray`` prints no output / exits silently
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Check that the ``--proc-dir`` path exists and that the mooring YAML is
present at ``{proc_dir}/{mooring_name}/{mooring_name}.mooring.yaml``.
Run with ``--verbose`` or ``-v`` for more detail.

Stage 1 fails with "no reader found"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``seasenselib`` is not installed, or the ``file_type:`` field in the mooring
YAML does not match a recognised reader.  Run ``oceanarray list`` to see
accepted ``file_type`` strings.

Stage 3 velocity shows all NaN / coordinate_system is BEAM
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For Nortek Aquadopps the BEAM→ENU transformation requires a transformation
matrix parsed from the ``.hdr`` file.  If the ``.hdr`` file was not found at
stage 1, ``coordinate_system`` is stored as ``"BEAM"`` and stage 3 skips the
rotation.  Verify that the ``.hdr`` file is in the same directory as the
``.aqd`` data file and that the ``header:`` field in the mooring YAML points
to it.

----

Getting help
------------

If none of the above solves your problem:

* Open an issue at https://github.com/ocean-uhh/oceanarray/issues and include
  the full traceback and the output of ``oceanarray --version``.
* Check that your branch is up to date: ``git pull``.
