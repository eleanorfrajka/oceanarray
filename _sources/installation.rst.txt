.. _installation:

Installation
============

Requirements
------------

Python 3.11–3.13 is supported and tested in CI.

Install oceanarray
------------------

``oceanarray`` is on PyPI:

.. code-block:: bash

   pip install oceanarray

This pulls in its dependencies, including ``seasenselib`` — the reader
``oceanarray`` uses for raw instrument files in stage 1.

In an isolated environment (recommended)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Option A — conda
""""""""""""""""

.. code-block:: bash

   conda create -n oceanarray python=3.11
   conda activate oceanarray
   pip install oceanarray

Option B — venv
"""""""""""""""

.. code-block:: bash

   python -m venv venv
   source venv/bin/activate        # macOS / Linux
   # venv\Scripts\activate         # Windows
   pip install oceanarray

Option C — uv
"""""""""""""

`uv <https://docs.astral.sh/uv/>`_ is a fast drop-in replacement for ``pip`` and
``venv`` (add ``--python 3.11`` to :samp:`uv venv` to pin the interpreter):

.. code-block:: bash

   uv venv
   source .venv/bin/activate       # macOS / Linux
   uv pip install oceanarray

To install just the ``oceanarray`` command-line tool in its own isolated
environment (on your ``PATH``, no venv to activate):

.. code-block:: bash

   uv tool install oceanarray

Development install (from source)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   git clone https://github.com/ocean-uhh/oceanarray
   cd oceanarray
   pip install -e ".[dev]"        # or: uv pip install -e ".[dev]"

The ``-e`` flag installs in editable mode so that any local changes take effect
immediately without reinstalling.  The ``[dev]`` extra adds the test and docs
tooling plus ``ruff`` in one resolution.  To also work on PDF report output
(WeasyPrint, which needs native pango/cairo libraries) use ``pip install -e
".[all]"`` — everything, equivalent to ``.[dev,pdf]``.  See :doc:`troubleshooting`
if a dependency fails to build.

Stage 1 and seasenselib
-----------------------

Stage 1 (reading raw instrument files) needs ``seasenselib``, which is installed
automatically with ``oceanarray`` (above).  Without ``seasenselib``, stage 1
processing cannot run.  Stages 2–3 and mooring-level processing (stack, grid,
reports) can still run on existing NetCDF files.  See :doc:`troubleshooting` if
the install fails.

RDI ADCP support
----------------

Processing RDI WorkHorse ADCP files (``file_type: rdi-raw``) needs no extra
install: ``seasenselib`` reads them via ``mhkit[dolfyn]``, which is pulled in
automatically with ``oceanarray``.

Verify the installation
-----------------------

.. code-block:: bash

   oceanarray --version

If this prints a version string, the installation is complete.  See the
:doc:`quickstart` for the next steps.
