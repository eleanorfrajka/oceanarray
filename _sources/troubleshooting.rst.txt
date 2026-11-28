Troubleshooting
===============

**pip fails to build pyproj** (a dependency of ``seasenselib``)
   Install a ready-built wheel, then oceanarray:

   .. code-block:: bash

      pip install --upgrade --force-reinstall --no-cache-dir --only-binary=:all: "pyproj>=3.7.0"
      pip install oceanarray

   If that fails too, start a new environment with Python 3.11.

**"command not found" in a new terminal**
   The environment is active only in the terminal that activated it: run
   ``source venv/bin/activate`` (``venv\Scripts\activate`` on Windows) or
   ``conda activate oceanarray`` again.

**oceanarray prints nothing, or stops at once**
   Check that the mooring's YAML file is where oceanarray looks
   (``{proc_dir}/{mooring}/{mooring}.mooring.yaml``, or give its path), run
   ``oceanarray validate`` on it, and add ``-v`` for more output.

**Stage 1: "no reader found"**
   The instrument's ``file_type:`` is not one oceanarray knows; ``oceanarray
   list`` prints the ones it accepts.

**Stage 3: Aquadopp velocity all NaN, coordinate_system "BEAM"**
   Stage 1 did not find the ``.hdr`` file with the transformation matrix, so
   stage 3 could not rotate the velocity. Put the ``.hdr`` beside the ``.aqd``,
   or name it with ``header_file:`` in the instrument entry, and run again from
   stage 1.

Still stuck? Open an issue at https://github.com/ocean-uhh/oceanarray/issues with
the full error and ``oceanarray --version``.
