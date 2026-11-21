Developer guide
===============

This page collects how oceanarray code is written: the principles, which layer
owns what, the one idiom for each recurring job, the vocabulary, naming, and the
rules for docstrings, CHANGELOG, branches and pull requests. Where a convention
departs from common Python practice it is listed under :ref:`dev-deviations` with
its reason.
`CONTRIBUTING.md <https://github.com/ocean-uhh/oceanarray/blob/main/CONTRIBUTING.md>`__
covers licensing, credit and the development setup; this page is the code itself.

Some rules below are not yet true everywhere in the code. Each such place is named
with the release or piece of work that fixes it ("planned").

The guide is written for everyone who changes the code, people and AI coding
assistants alike. Each rule is stated so it can be followed without asking; where
a rule is unclear or does not fit a case, open an issue and the guide is updated.

.. contents:: On this page
   :local:
   :depth: 1


1. Purpose and principles
-------------------------

oceanarray takes raw mooring instrument files to QC-flagged, gridded CF-NetCDF
products and a report per mooring. It is a package meant to be used across
institutions: one project's paths, scripts and choices live in that project's own
repository, not here.

**Two ways of working, both kept.** Users either follow the prescribed
directory structure (``{proc_dir}/{name}/{name}.mooring.yaml``) and name a mooring
by its name, or keep their own directory structure and give full paths. Either
way, a mooring's name is its ``name:`` key, never read from the YAML's file
name or folder. Every change keeps both working, and the
tests cover both (:doc:`configuration_files`).

**Correct, then maintainable, then fast.** A slower function that is right beats
a faster one that is nearly right; clear code beats clever code. Shorter code is
preferred only when it is equally correct and equally clear.

**Consistent.** One name for one thing and one way to do each recurring job
(sections 3–5), so code from different contributors reads alike and a reader
learns each pattern once.

**Provenance: never change data silently.** Every value a stage changes, drops or
fills is recorded in the file: a ``history`` entry for what was done, attributes
for the settings that did it (thresholds, coefficients, the flag rule), so a file
can be reprocessed from itself. Never substitute a default when the right value
cannot be determined: store ``"UNK"``, or raise, or warn and record the
assumption. A plausible wrong number is worse than an error.

**The originator's data are kept.** Values, flags and attributes from the
originator's files are carried beside ours (``<var>_qc_originator_mapped``,
``originator_*``), never overwritten. An export may leave them out when the user
asks for that; the default keeps them.

**Real data in tests.** Fixtures are trimmed real instrument files, not synthetic
mocks. A numerical assertion checks a value that can be justified without running
the code (analytic case, invariant, a ``gsw`` cross-check).

**Against bloat.**

- Search for an existing helper and extend it before writing a new one; never a
  second copy.
- No defensive code for states the data model rules out; no ``try``/``except``
  that only re-logs.
- No compatibility aliases or shims except for released public API.
- No new module when an existing one fits; a table of cases over repeated
  ``if``/``elif``.
- Delete dead code found in a file you touch.


2. Layers and what each owns
----------------------------

.. list-table::
   :header-rows: 1
   :widths: 22 30 48

   * - Layer
     - Where
     - Owns
   * - API
     - ``oceanarray/__init__.py``
     - The verbs: ``read``, ``read_mooring``, ``stage1``–``stage3``, ``process``,
       ``stack``, ``grid``, ``run``, ``write``, ``write_mooring``, ``report``,
       ``export``. ``__all__`` is the public surface.
   * - Description
     - ``mooring.py``, ``instruments.py``, ``location.py``, ``paths.py``,
       ``catalog.py``
     - The ``*.mooring.yaml`` (load, check, template, save); instrument entries;
       positions; every file name and suffix; the sensor catalog.
   * - Readers
     - ``readers/``
     - Vendor file → canonical dataset, one module per format. Instrument formats
       come from ``seasenselib``; ``readers/`` keeps netCDF, OceanSITES, RODB and the
       Nortek and Sea-Bird header parsers.
   * - Pipeline
     - ``pipeline/``
     - The verbs' bodies, one file per verb (``stage1.py`` … ``grid.py``,
       ``concat.py``, ``export.py``), what they compose (``clock/``,
       ``calibration/``, ``qc/``, ``gridders/``) and the shared transforms
       (``units``, ``pressure``, ``coordinate``, ``masking``, ``dataset``). Stage
       functions take and return datasets; I/O is in ``process``/``run``.
   * - Writers
     - ``writers/``
     - Writing only: ``writers.write`` (encoding, compression, dtypes). Never
       changes values.
   * - Physics
     - ``physics.py``
     - Seawater and current formulas shared by the pipeline and the pages.
   * - Analysis
     - ``analysis/``
     - Physics on the products that is not a pipeline step: θ, σθ, speed and
       direction, spectra, a site's record over the years. Computed once per page
       and passed to every figure.
   * - Plotters
     - ``plotters/``
     - ``draw_<what>`` functions returning a matplotlib Figure. Know nothing about
       HTML.
   * - Reports
     - ``reports/``
     - Pages: context builders, panel registries, captions, templates. The shared
       layer in ``reports/_vendored/`` is copied byte-identical from ctdcast.
   * - Config
     - ``config/``
     - The variable registry and defaults (``parameters.py``), unit tables, flag
       translation table, mooring YAML validation.
   * - CLI
     - ``cli/``
     - One module per subcommand; argument parsing only. Nothing the CLI does is
       unavailable as a library function.
   * - Legacy
     - ``legacy/``
     - RODB/RAPID-era reference code. Shipped, not linted, not covered; nothing
       imports it.

**Data between functions is xarray.** Data passes between functions as an
``xarray.Dataset`` (or ``DataArray``), never a pandas DataFrame. pandas is fine
inside a function where it is the natural tool (parsing times, building a table
for a page).

**What may import what.** Checked by ``lint-imports`` (contracts in
``pyproject.toml``, run in the lint job) and, for ``legacy``, by
``tests/unit/test_package_boundaries.py``:

- **Layers.** Each part imports only the parts listed below it: ``cli`` →
  ``reports`` → ``plotters`` → ``analysis`` → ``pipeline`` → ``readers`` and
  ``writers`` → the mooring YAML and its helpers (``mooring``, ``catalog``,
  ``provenance``, ``logger``, ``paths``, ``instruments``, ``location``,
  ``physics``, ``inspect``) → ``config``, ``exceptions`` and ``_deprecated``. The
  shared report layer (``reports/_vendored/``) may be used by every part and
  imports none of them.
- ``plotters/`` never imports a ``reports/`` module directly (the shared report
  layer excepted). Indirect chains are not checked yet: they still reach the pages
  through the mooring and pipeline cycle below.
- **Units have one door.** Pipeline code gets the unit tables through
  ``config/unit_tables.py``, never from ``reports/_vendored/`` directly.
- Nothing outside ``legacy/`` imports ``oceanarray.legacy``.

The imports that break a rule today are listed as exceptions in the contracts,
each marked "To change": the readers using pipeline helpers, the sensor catalog
and provenance using a pipeline or writer helper, and two **cycles**, where the
lower module imports back inside a function to avoid a circular import at load
time: ``mooring`` and ``pipeline`` (``pipeline.stack`` and ``pipeline.concat``
import ``mooring``; ``mooring`` uses ``pipeline.attribution``, ``masking`` and
``_run``), and ``mooring`` and ``config/validation.py``. A cycle is fixed by
moving the shared pieces below both modules, not by deleting a line. A pull
request that removes an exception deletes its line; a new one fails the lint job.
``pipeline/_run.py`` importing ``reports`` is allowed: ``run`` ends with the
report.

Not yet checked: **anything imported across subpackages is public** (no leading
``_``). A private helper is used inside its own subpackage only. Planned: about 20
places cross today (for example ``cli`` → ``reports._verb._resolve_*``,
``pipeline.raw_files`` → ``readers.nortek._parse_*``). For each, first check
whether the helper sits in the right subpackage or belongs in a module shared
across subpackages, then make it public or move it.


3. One idiom per recurring job
------------------------------

Each job below has one helper. If the helper does not do what you need, extend it;
do not write a second one.

3.1 Reading a file
~~~~~~~~~~~~~~~~~~

**Rule.** Raw instrument files go through ``oceanarray.read`` (``readers.read``).
The format comes from ``file_type`` or is sniffed from the content; the suffix is
never used.

**A new instrument format.** Instrument readers belong in ``seasenselib``, which
other packages share. Write the reader there when its maintainers can take it in;
otherwise write it in ``readers/`` and move it to ``seasenselib`` later, leaving a
thin call behind. Formats that only oceanarray reads (OceanSITES, RODB) stay in
``readers/``.

**Records without a time.** Some raw files contain records whose time cannot be
read (a corrupted line, a header repeated mid-file). The reader removes them, in
the reader only, and stores how many it removed in ``n_records_without_time``, so
no later stage has to check for missing times.

.. code-block:: python

   if file_type is None:
       file_type = _sniff_text(path)
       if file_type is None:
           raise ValueError(
               f"{path.name}: cannot determine the file type from its content; "
               f"pass file_type= (one of {sorted(params.ALL_FILE_TYPES)})."
           )

**Not.** Guessing the format from the extension; ``xr.open_dataset`` on a raw
file in pipeline code; ``print("WARNING: …")`` in a reader (see
:ref:`dev-deviations`).

3.2 Adding a variable
~~~~~~~~~~~~~~~~~~~~~

**Rule.** A variable exists once, in ``config/parameters.VARIABLES``: ``label``,
canonical CF ``units``, ``standard_name``, ``cmap``, and ``valid_min``/``valid_max``
only where a physical limit is known, with its source in a comment. Then, as
needed: QC thresholds (``QC_GROSS_RANGE``, ``QC_SPIKE``, ``QC_FLAT_LINE``), a color
(``VAR_COLORS``), and a non-canonical unit spelling in ``config/unit_tables`` with
evidence (a reader that emits it). Labels come from ``vlabel(var)`` and
``vunit(var)``.

.. code-block:: python

   "temperature": {
       "label": "Temperature",
       "units": "degree_Celsius",
       "standard_name": "sea_water_temperature",
       "cmap": "RdBu_r",
       "valid_min": -5.0,  # legacy/OS1_variable_attributes.yaml TEMP
       "valid_max": 42.0,
   },

**Not.** A unit string typed into a label or caption (``"m s⁻¹"``;
``test_unit_label_literals.py`` fails on it); ``valid_min``/``valid_max`` copied
from the gross-range spans (those are for flagging only); an unknown unit passed
through (``UnitsError``).

3.3 Recording what was done
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Rule.** Every processing step adds one ``history`` entry with
``pipeline._stage.append_history(ds, note)`` (timestamp and separator live there),
and writes the settings it used as structured attributes. The note is a sentence a
reader understands, with the values in it; it never contains ``"; "`` (the entry
separator; ``append_history`` cleans it).

``history`` is read back by one set of parsers, and only to display it: the
processing tables on the pages show its entries. Captions and settings never parse
``history``; what they need comes from structured attributes. If a table is wrong,
fix what the stage writes, not the page. If attributes ever become limiting, the
next step is a sidecar file the captions read (a small file written next to the
data file, such as ``M2_2016_grid.json``), not more parsing. A sidecar is for
convenience only: the provenance must still be complete in the NetCDF file, which
is what gets shared.

.. code-block:: python

   _stage.append_history(
       ds_out,
       f"flags: values flagged {masking.rule_words(flags)} left out before gridding "
       "(grid_mask_flags)",
   )

**Not.** Writing ``ds.attrs["history"]`` directly; a setting recorded only in the
``history`` text; a second ``history`` parser. Planned: the pages parse ``history``
in more than one module today (``reports/_captions.py``, ``reports/_grid.py``),
and one caption counts entries from it.

3.4 Raising, warning, logging
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Rule.**

- **Raise** when the input is wrong and continuing would write a wrong file:
  ``MooringError`` for a problem in the mooring YAML or the input, ``UnitsError`` for an unknown
  unit, ``StageError`` for a file at the wrong stage.
- **WARNING** (``log.warning``) for every data problem the user should act on:
  data dropped, a value assumed, a key ignored. Also recorded in the file when it
  changed the data.
- **INFO**: one line per step. **DEBUG**: per instrument.
- Each module logs through ``logging.getLogger(__name__)``. Configuring logging
  belongs to whoever runs the code: the CLI sets up the console once
  (``logger.configure_console``, stderr), and a Python user does the same or uses
  their own setup. Library code never sets levels or adds console handlers.
  Results go to stdout.
- Stages 1–3 also write their own log file with full DEBUG detail, next to their
  output, whether they are run from the CLI or from Python.
- ``warnings.warn`` only with ``DeprecationWarning``, for deprecated names.

Message style: what (instrument and serial), what is wrong, what to do; lazy
``%`` arguments in log calls.

.. code-block:: python

   raise MooringError(
       f"{entry.get('instrument', 'unknown')} s/n "
       f"{paths.safe_serial(entry.get('serial', 'unknown'))}: {n} record(s) "
       "with no time in the stage-1 file; re-run stage 1 (the reader now "
       "drops them)"
   )

**Not.** ``print`` in library code; a broad ``except`` that swallows;
``UserWarning`` for a data problem. Planned: ``UserWarning`` remains in stage 1
(units) and ``concat`` (settings).

*Planned for 0.7.0:* one base exception, ``OceanarrayError``. ``MooringError``
(still a ``ValueError``, so ``except ValueError`` keeps working) is kept for
mooring YAML problems only; ``StageError`` stops being a ``MooringError``.

3.5 Old file names and layouts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Rule.** Files written by a released version keep working. Renamed variables are
mapped on read in one table per rename, through one helper; e.g. the identity
scalars renamed in 0.6.1 (``OLD_IDENTITY_NAMES``) through
``pipeline._stage.normalize_identity_names`` (to be renamed
``canonical_identity_names``, see :ref:`dev-naming`). A compatibility branch names
the version it serves and goes when that version is no longer supported.

.. code-block:: python

   def nominal_depth_text(ds: xr.Dataset) -> str:
       ds = normalize_identity_names(ds)
       if "instrument_depth" in ds.variables:
           return f"{float(ds['instrument_depth'].values):.0f} m"
       return "?"

**Not.** ``ds.get("InstrDepth", ds.get("instrument_depth"))`` at a call site;
compatibility for unreleased development builds.

3.6 Display formatting
~~~~~~~~~~~~~~~~~~~~~~

**Rule.** One helper per displayed quantity:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Shown
     - Helper
   * - Nominal depth of one instrument
     - ``plotters.helpers.nominal_depth_text(ds)`` → ``"1500 m"``
   * - Serial
     - ``plotters.helpers.serial_text(ds)``
   * - Durations in a table column
     - ``reports._html_helpers.duration_cells(seconds)`` (unit in the header) or
       ``format_durations``
   * - Position
     - ``location.format_ddm`` (degrees, decimal minutes)
   * - Axis label / unit
     - ``parameters.vlabel(var)``, ``parameters.vunit(var)``;
       ``config.unit_tables.unit_label(units, standard_name)`` for a raw ``units``
   * - Flag words
     - ``parameters.QC_FLAG_LABEL[flag]`` ("good", "suspect", …), never the
       ``flag_meanings`` tokens

**Not.** ``f"{depth:.0f} m"`` or a unit written at the call site. User-facing text
says "depth", never "slot" or an index.

3.7 Figures
~~~~~~~~~~~

**Rule.** ``draw_<what>(ds, …, width_in=W) -> Figure | None`` in ``plotters/``:
drawn at the width of its place on the page, never resized after; ``None`` means
"not applicable to this file". The report adapter ``_make_<what>_b64`` calls
``render_b64``, which applies the report style, encodes and closes the figure. A
plotter may use pyplot (``plt.subplots``); ``render_b64`` closes every figure it
encodes, and the test suite checks that none is left open. A plotter does not call
``plt.style.use``. Physics (θ, σθ, speed) comes from ``analysis/``, computed once
per page and passed in. Choices the caption must state (levels, windows, limits)
are made once, in the page context, and passed to both the figure and its caption;
the caption never recomputes them.

**Why two steps.** ``draw_*`` knows nothing about where the figure goes; the
``_b64`` adapter embeds it in the page, so a report is one HTML file that can be
passed around whole. Writing PNG files instead is another adapter around the same
``draw_*`` functions.

**One masking rule.** Every figure hides flagged values through
``pipeline/masking.py`` (default: flag 4 hidden, suspect kept), so the pages, the
grid and the captions agree on what was left out.

**Hiding is not showing.** Masking decides which values are left out of a line,
a statistic or the grid. A figure that *shows* flags (colored markers on suspect
points, the flag rows) is a different job: it reads the flag variable through
``marker_flags`` and draws every flagged point, including the ones masking hides
elsewhere.

.. code-block:: python

   def test_temperature_trajectory_none_without_temperature():
       """No temperature -> the trajectory panel is not applicable (None), not a KeyError."""
       assert draw_temperature_trajectory(_velocity_only_ds()) is None

**Not.** A ``draw_*_fig`` name (``test_private_names.py``); a colormap chosen in the
plotter (``design_tokens.CMAPS`` by quantity); a caption that parses ``history``; a
figure that drops flags itself. Planned: ``plotters/helpers.py``,
``plotters/current.py``, ``plotters/spectrum.py``, ``plotters/diagnostic.py`` and
``reports/_read.py`` still drop ``QC_BAD_FLAGS`` (3, 4, 9) directly; the fix moves
them to ``masking`` and retires ``QC_BAD_FLAGS``.

*Planned:* ``draw_*`` returns the figure plus a small record of what it drew, and
captions read that record.

3.8 Pages
~~~~~~~~~

**Rule.** A page's layout is an ordered list of sections, each naming the panels
(figures and tables) under its heading. The shared layer calls this list a
``Profile``, made of ``Section`` entries that name ``Panel`` ids
(``reports/_vendored/page_model``). Each page module (``reports/_mooring.py``,
``reports/_stack.py``, …) defines its own panels. A panel that applies but has
nothing to show says why (``unavailable_if`` returns the reason). Templates lay out what the context built; they do not compute. Captions
sit above the figure or table, the ``source:`` line below; a highlighted cell has
its criterion in the caption.

.. code-block:: python

   "steps": Panel(
       "steps",
       render=lambda c: render_template("_history_steps.html", rows=c.steps),
       kind="html",
       caption=STACK_CAPTIONS["steps"],
       unavailable_if=lambda c: None if c.steps else _html_helpers.NO_HISTORY,
   ),

**Not.** Arithmetic or filtering in a template (do it in the context builder);
hand-written ``<table>`` HTML in a new oceanarray template (use the table macros
the shared layer already provides); a panel that shows nothing without saying why.
Nothing in ``reports/_vendored/`` is edited here: a change to the shared layer is
made in ctdcast and copied back. If a macro you need is missing, ask for it there.

3.9 Mooring YAML keys
~~~~~~~~~~~~~~~~~~~~~

**Rule.** Every top-level key is listed in ``KNOWN_KEYS`` in
``oceanarray/mooring.py`` and documented in :doc:`yaml_configuration`.
``mooring.check`` reports an unknown top-level key as a WARNING; inside a block
(``caldip``, ``qc_*``, ``stack``, ``grid``) every shape mistake is an ERROR naming
the allowed set. The YAML key, the Python keyword and the CLI flag are the same
word (``pmin``, ``--pmin``).

**Each mooring YAML stands alone.** A setting shared by many moorings is written
into each mooring's YAML, never inherited from an array file or a parent file at
run time, so one file is enough to reprocess its mooring. *Planned:* a command
that writes such a setting into every mooring YAML of an array at once.

**The YAML says what, the run says where (paths).** The mooring YAML holds facts
and settings, never a path. Input locations (raw-data root, sensor catalog, caldip
set, bathymetry) come from the call or a CLI flag, optionally from a project or
user configuration file (planned). Keeping paths out of the YAML makes it
portable: the same file works on any machine and with any directory layout.
Planned: two keys still hold a path relative to the YAML, ``sensor_catalog`` and
``caldip.root``; until they move to the run, a new key never adds a third.

**Not.** A key read by the pipeline but missing from ``KNOWN_KEYS``; a path of
any kind in the YAML. Planned: ``caldip.root`` and ``sensor_catalog`` are still
YAML keys. *Planned for 0.7.0:* an unknown top-level key becomes an ERROR, after
a survey of real mooring YAMLs.

3.10 Configuration defaults
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Rule.** A default is a constant in ``config/parameters.py`` with a ``#:`` comment
saying what it is and its unit. The defaults are read-only: nothing assigns to
``params.X`` at run time (modules import it as
``from oceanarray.config import parameters as params``). A value is overridden, from lowest to highest priority,
by:

1. the default in ``config/parameters.py``;
2. the mooring YAML;
3. the instrument's entry in the YAML;
4. the call (keyword argument) or the CLI flag.

Where more than one of these can give a value, one ``resolve_<what>`` returns it in
that order and names where it came from in its error. What was used is recorded in
the output (``deployment_settings``, ``stack_mask_flags``). A function signature
defaults to ``None`` and reads the constant, rather than repeating its value.

.. code-block:: python

   if given is not None:
       value, where = given, "the call"
   else:
       from_yaml = (mooring.get("stack") or {}).get("dt_seconds")
       ...

Environment switches (:ref:`dev-debug`) are read when they are used, never at
import.

**Not.** A literal default in a function signature that duplicates a constant;
defaults in a second table; reassigning a parameter to change one call's behavior.
*Planned for 0.7.0:* the grid's ``pmin``/``pmax``/``dp`` move from
``pipeline/_run.DEFAULTS`` to ``config/parameters.py``, and the three signatures
that repeat 200/1000/20 read them; ``reports/_verb.py`` passes ``SIGMA_GRID`` as an
argument instead of swapping the module value; ``OCEANARRAY_RAISE_ON_PLOT_ERROR``
is read at render time instead of at import.

3.11 Deprecations
~~~~~~~~~~~~~~~~~

**Rule.** Only released public API is deprecated rather than changed. A renamed
Python name goes in ``_deprecated.RENAMED`` and is wrapped with
``_deprecated.alias``; a renamed CLI flag uses ``cli._deprecate.DeprecatedAlias``
with ``help=argparse.SUPPRESS``. The removal release is one constant,
``_deprecated.REMOVED_IN``; the migration page lists every entry (a test checks
it). A name deprecated during 0.7.x (0.7.0, 0.7.1, …) is removed in 0.8.0: the
next minor release, never a patch release.

.. code-block:: python

   vars = _deprecated.alias("oceanarray.inspect.vars", variables)
   attrs = _deprecated.alias("oceanarray.inspect.attrs", attributes)

**Not.** An alias for something never released; a second spelling of the removal
version. Planned: ``REMOVED_IN`` is also defined in ``cli/_deprecate.py``, and
``logger.LOG_FN_DEPRECATED`` writes "0.7.0" out.

3.12 Tests
~~~~~~~~~~

**Rule.**

- Fixtures are trimmed real files: the ``dune2_1_2026`` raw/proc set, the M2
  OSNAP East moorings (``egM2_concat``, the site's former name), OSNAP cuts (``tests/fixtures/``, 8 MB).
- Report pages are compared with golden HTML (``tests/fixtures/golden/``), a
  saved copy of each page that a new render must match. The embedded figures
  (each a PNG image stored in the page as base64 text), the timestamps and the
  version number are blanked out before comparing, because they differ between
  machines and runs. Re-baseline with
  ``REBASELINE_GOLDEN=1 pytest`` only after looking at the change; the golden diff
  is part of the review.
- ``dune2_pages`` and ``egm2_site`` (``tests/unit/conftest.py``) build pages once
  per session; read them, never write into them.
- ``conftest`` forces ``RAISE_ON_PLOT_ERROR`` on, so a broken figure fails a test
  instead of disappearing.
- Markers: ``slow`` (more than about 5 s) and ``local`` (needs a developer's
  machine), both excluded from CI; no test uses them today.
- Convention tests: ``test_unit_label_literals``, ``test_filename_literals``,
  ``test_private_names``, ``test_package_boundaries``, ``test_doc_package_refs``,
  ``test_vendored``, ``test_base_css_no_vendored_dup``, ``test_plot_guard``,
  ``test_stub_reason``. A settled idiom gets such a test.

**Not.** ``assert result is not None`` on a function that returns ``None`` on
failure; a value copied from the code's own output as the expected value; a mock of
a stage when a fixture exists.

.. _dev-debug:

3.13 Debug switches
~~~~~~~~~~~~~~~~~~~

Environment variables for looking at a rendered page; all off unless noted.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Variable
     - Effect
   * - ``OCEANARRAY_UNIT_DEBUG_MARK=1``
     - Every label made by ``unit_label`` ends in ``*``, so a page shows which
       labels went through the unit tables.
   * - ``OCEANARRAY_REPORT_DEBUG=1``
     - Each figure records its draw function, ``figsize`` and PNG size, printed
       under the figure.
   * - ``OCEANARRAY_FIGURE_SOURCE=0``
     - Turns off the ``source:`` line under each figure (on by default).
   * - ``OCEANARRAY_RAISE_ON_PLOT_ERROR=1``
     - A figure that fails raises instead of rendering a stub.


4. Vocabulary
-------------

One meaning per term. Rows marked **two meanings** are still used two ways in the
code; they are settled in the module naming review.

The places, from largest to smallest:

.. list-table::
   :header-rows: 1
   :widths: 20 58 22

   * - Term
     - Meaning
     - In the code
   * - array
     - The set of moorings one year page shows (OSNAP East 2016), from
       ``*.array.yaml``. Not the OceanSITES ``array`` attribute, which names
       the program ("OSNAP"); that is an external standard and keeps its name.
     - ``*.array.yaml``
   * - subarray
     - A named group of moorings within an array (the Cape Farewell array).
     - ``subarrays:``
   * - site
     - One position, across all the moorings placed there (M2, CF5); joined by
       ``concat``.
     - ``<site>_stackcat.nc``
   * - mooring
     - One physical mooring, from deployment to recovery (``M2_2016``). The code,
       the CLI and the pages use this word (planned: the pages still label it
       "Deployment").
     - ``*.mooring.yaml``, ``mooring_name``
   * - deployment
     - Only the act of deploying or the period in the water ("deployed
       2016-08-05", "the 2016–2018 deployment"). Never one mooring. The
       OceanSITES ``deployment_code`` keeps its name.
     - ``deployment_code``

The rest:

.. list-table::
   :header-rows: 1
   :widths: 20 58 22

   * - Term
     - Meaning
     - In the code
   * - originator
     - Whoever produced the data files we read (a data center, a PI). Their
       values, flags and attributes are kept beside ours.
     - ``originator_*``, ``*_qc_originator_mapped``
   * - oceanarray
     - Our processing, where a flag source is named ("oceanarray flags", not
       "processing flags").
     - ``<var>_qc``
   * - source
     - What a page or product was made from (stage files, the YAML). Not the data
       provider. **Two meanings:** the YAML ``source:`` block and ``source_*``
       attributes describe the data provider (citation, license); the figure
       ``source:`` line names the function that drew it; ``<field>_source`` is
       where a YAML value came from.
     - ``source_*``, ``bin_pressure_source``
   * - serial
     - The instrument's serial number. **Two meanings:** the ``serial`` attribute
       ("9920") and the ``instrument_serial`` variable / YAML key (may carry a
       ``*`` marker, or ``serial, beacon_id``).
     - ``serial_text``
   * - nominal depth
     - The design depth from the mooring YAML (m, positive down).
     - ``instrument_depth``, YAML ``nominal_depth``
   * - depth
     - **Two meanings:** depth computed from pressure (``depth`` variable,
       ``gsw``) and, in a Stack cat, a group of levels matched across moorings
       (``N_DEPTHS``, value ``nominal_depth``). The YAML ``depth:`` key is not
       read.
     - ``depth``, ``N_DEPTHS``
   * - pressure / bin pressure
     - Measured (or stage-3 interpolated, flag 8) sea pressure in dbar; an ADCP
       bin's pressure from the head pressure, range and tilt.
     - ``pressure``, ``bin_pressure``
   * - HAB
     - Height above bottom (m) of the sensor: write ``hab`` for every
       instrument. ``hab_bottom`` / ``hab_top`` give the bottom and top of an
       inline element that has a height, mainly for the mooring diagram;
       oceanarray reads them only to fill a missing ``hab`` for an ADCP, from the
       transducer end (``hab_top`` when looking up, ``hab_bottom`` when looking
       down).
     - ``hab``
   * - stage 1 / 2 / 3
     - Read to CF-NetCDF / clock and trim / QC, rotation, derived. A file's stage
       is its ``processing_stage`` (not the OceanSITES ``processing_level``).
     - ``*_stage1.nc``
   * - level
     - One row of a stack: an instrument, or one ADCP bin. Not a grid pressure
       level.
     - ``N_LEVELS``
   * - stack
     - One mooring on a common time axis (nearest sample within ±dt/2).
     - ``_stack.nc``
   * - grid
     - One mooring's stack interpolated in pressure.
     - ``_grid.nc``
   * - Stack cat
     - A site's stacks joined end to end in time, levels matched by depth.
     - ``_stackcat.nc``
   * - Grid cat
     - The Stack cat interpolated in pressure. Built from the Stack cat, not by
       joining the moorings' grids.
     - ``_gridcat.nc``
   * - flags
     - Unqualified, "flags" means oceanarray's flags (``<var>_qc``). The
       originator's flags are kept separately, translated into the same table
       (``<var>_qc_originator_mapped``). Both use OceanSITES reference table 2: 0 no QC, 1 good, 2 probably good,
       3 suspect, 4 bad, 5 value changed (originator's QC changed it; our one
       extension), 7 nominal, 8 interpolated, 9 missing. 8 counts as good in the QC
       tables. **Two meanings of "bad":** flag 4, and ``QC_BAD_FLAGS`` (3, 4, 9)
       used by some figures (to be retired, see 3.7).
     - ``QC_FLAG_LABEL``
   * - mask flags
     - The flags a stack page hides and the grid leaves out (default 4).
     - ``stack_mask_flags``
   * - valid_min / valid_max
     - CF attributes on each data variable in the output files, set from
       ``VARIABLES``: physical limits, only where known.
     - ``VARIABLES``
   * - gross range
     - QC test spans (fail / suspect); for flagging only.
     - ``QC_GROSS_RANGE``
   * - slot
     - The width class of a figure on a page (``W_FULL``, ``slot-half``). Never a
       depth. There is no matching height class: each ``draw_*`` sets the
       height from its content (number of levels or panels), within
       ``MIN_SECTION_H``/``MAX_SECTION_H`` for depth sections.
     - ``reports/_slots.py``


.. _dev-naming:

5. Naming
---------

- **Modules**: named for what they hold (``mooring.py``, ``masking.py``); a
  verb's body has the verb's name in ``pipeline/``; a set of alternatives is a
  plural agent noun in its own subpackage (``readers/``, ``gridders/``). Never
  named for a kind of code: no ``tools``, ``utilities``, ``utils``, ``helpers``,
  ``misc``.
- **Public functions** are named by kind:

  .. list-table::
     :header-rows: 1
     :widths: 30 32 38

     * - Kind
       - Name
       - Example
     * - read or write a file
       - ``read_<what>``, ``write_<what>``
       - ``read_mooring``
     * - a processing verb
       - the verb
       - ``stack``, ``grid``
     * - a computed quantity
       - a bare noun (as in ``gsw``)
       - ``potential_temperature(ds)``
     * - convert form
       - ``to_<form>``, ``from_<form>``
       - ``to_oceansites``
     * - the one accepted form
       - ``canonical_<thing>``
       - ``canonical_identity_names``
     * - pick among sources by priority
       - ``resolve_<what>``
       - ``resolve_mooring``
     * - text for display
       - ``<what>_text``
       - ``serial_text``
     * - cells of a table
       - ``<what>_cells``
       - ``duration_cells``
     * - yes or no
       - ``is_<state>``, ``has_<thing>``
       - ``is_path_component``
     * - problems found
       - ``check_<what>``, returning a list of problems
       - ``mooring.check``
     * - a figure
       - ``draw_<what>``, returning a Figure or None
       - ``draw_instrument_rose``
     * - a page
       - ``generate_<what>_page``
       - ``generate_gridcat_page``
- **No word with two spellings** (British and American) in a public name:
  ``canonical_``, ``_rename_``, not ``normalize``/``normalise``. ``color`` is the
  one exception.
- **Private helpers**: leading ``_``, one verb per job. The docstring is one line
  plus a typed signature; a reason the code cannot show goes in a ``#`` comment at
  the line it explains.

  .. list-table::
     :header-rows: 1
     :widths: 40 60

     * - Name
       - Returns
     * - ``_read_<what>(path)``
       - an object read from a file
     * - ``_parse_<what>(text)``
       - a typed value from a string in hand, no I/O
     * - ``_resolve_<what>(…)``
       - the one value that applies, from several sources by priority
     * - ``_format_<what>(value)``
       - a display string
     * - ``_build_<what>(…)``
       - rows, a dict or a dataset
     * - ``_render_<what>(…)``
       - an HTML fragment
     * - ``_make_<what>_b64(…)``
       - a base64 PNG for a page (``_b64s`` for a list)
     * - ``_has_<what>``, ``_is_<what>``
       - a bool
     * - ``_apply_<what>(ds, …)``
       - the dataset after one processing step
     * - ``_add_<what>(…)``
       - the target with variables, attributes or flags added
     * - ``_rename_<what>(ds)``, ``_to_canonical_<form>(ds)``
       - renamed variables; a foreign layout in ours
     * - a noun (``_time_axis_bounds``)
       - a computed result

  Retired prefixes ``_fmt_``, ``_normalize_``, ``_sanitize_``, ``_get_``,
  ``_find_``, ``_load_``, ``_clean_``, ``_plot_`` fail ``test_private_names.py``.
  A private helper is used inside its own subpackage only (section 2).
- **Constants and tables**: ``UPPER_SNAKE``, whole words, no type suffix.
- **Files**: every stage and product suffix comes from ``paths``
  (``test_filename_literals.py``); YAML files are ``.yaml``.
- **CLI**: verbs with one meaning each (``init``, ``validate``, ``process``,
  ``run``, ``report``, ``list``, ``concat``, ``export``, ``stamp``, ``plot``,
  ``animate``); short flags only ``-o``, ``-n``, ``-v``; ``--stage`` is a list.
  CLI verbs and flags follow conventions shared with the sibling packages:
  propose a new verb or flag in an issue before adding it.
- **Units in names.** Data variables never carry a unit in their name (the unit is
  their ``units`` attribute). Settings, keywords and scalar attributes that hold a
  number with a unit carry it as a suffix, spelled one way: ``_seconds``,
  ``_hours``, ``_days``, ``_m``, ``_dbar``, ``_deg``, ``_percent``. Pressure is
  always in dbar (never Pa, never m) and depth and heights in m. Pressure and
  depth keys and flags may leave the suffix out because their unit never varies
  (``pmin``, ``--pmin``); a stored attribute always carries it (``pmin_dbar``).
  Times always carry it (``--dt-seconds``).
- **The same word everywhere**: the CLI flag, the Python keyword, the YAML key and
  the file attribute name the same thing the same way (``--pmin``, ``pmin=``,
  ``pmin:``, ``pmin_dbar``).
- **YAML keys**: lowercase snake for our own words, no spaces or hyphens; a key naming a format's entity
  is spelled as the format spells it (``platform_code``).

**Planned renames.**

- ``plotters/helpers.py`` is split by what it holds: ``plotters/styles.py``
  (mooring and level styling, legends, axes), ``plotters/roses.py`` (current
  roses), ``plotters/_data.py`` (reading a dataset for a figure: ``masked``,
  ``ruled``, ``as_dataset``, ``marker_flags``, ``nominal_depths``,
  ``tilt_limits``) and ``plotters/_labels.py`` (``serial_text``,
  ``nominal_depth_text``, ``duration_words``, ``short_names``); ``tukey_smooth``
  moves to ``analysis/``.
- ``normalize_identity_names`` becomes ``canonical_identity_names``; the old name
  stays as a deprecated alias until 0.8.0.
- 0.7.0: everything about the data provider is named ``originator_*``:
  ``source_citation``, ``source_doi``, ``source_license``,
  ``source_acknowledgement`` become ``originator_*``, and ``source_file`` merges
  into ``originator_file`` (files with the old names are still read). "Source"
  keeps only ``<field>_source`` (where a value came from). The YAML ``source:``
  block becomes ``originator:`` (the old key still accepted, with a warning).
- With the shared report layer: the line under a figure or table that names the
  function that made it changes from ``source:`` to ``code:``; the page model's
  ``Profile`` and ``Section`` become ``Layout`` and ``Part``.
- 0.7.0: the grid attributes ``p_start_dbar`` / ``p_end_dbar`` become
  ``pmin_dbar`` / ``pmax_dbar`` (files with the old names are still read), and the
  ``p_start`` / ``p_end`` keywords become ``pmin`` / ``pmax`` (old keywords
  deprecated).

**Names that do not follow the table yet** (renamed in the module naming review):
``compute_*`` and ``calc_*`` become bare nouns (``compute_adcp_bin_pressure``,
``calc_psal``); ``format_*`` and ``*_label`` display helpers become ``*_text``
(``format_ddm``, ``format_durations``, ``unit_label``, ``cruise_label``);
``generate_*_report`` becomes ``generate_*_page`` (array); ``plot_title``,
``plot_climatology`` and ``plot_deployment_boundaries`` become ``draw_*``; the
``_s`` and ``_pct`` suffixes become ``_seconds`` and ``_percent``
(``dt_s``, ``offset_s``, ``oxygen_saturation_pct``, …).


6. Docstrings, comments, CHANGELOG, branches, pull requests
-----------------------------------------------------------

**Docstrings** (numpy style, rendered by Sphinx with the Read the Docs theme):

- Public functions: one-line summary, ``Parameters`` and ``Returns`` with types,
  ``Raises`` when it raises; a "why" sentence only when the code cannot show it.
- Private helpers: one line saying what comes back; the signature carries the
  types. A reason the code cannot show goes in a ``#`` comment at that line.
- Never restate the code. A docstring longer than its function is a sign to cut.
- Module docstring: what the module holds, in two or three lines.

.. code-block:: python

   def format_ddm(value: float, *, is_lat: bool) -> str:
       """Return a position as degrees and decimal minutes with a hemisphere letter.

       The value is rounded once, to the nearest hundredth of a minute, and only then
       split into degrees and minutes, so the minutes never read ``60.00′``.

       Parameters
       ----------
       value : float
           Latitude or longitude in signed decimal degrees.
       is_lat : bool
           True for a latitude (two-digit degrees, ``N``/``S``); False for a
           longitude (three-digit degrees, ``E``/``W``).

       Returns
       -------
       str
           For example ``"65° 43.91′ N"`` or ``"027° 48.00′ W"``.
       """

**Comments and docstrings make no references to tools**, local notes, plan or
handoff files, or machine paths: they describe the code. No filler adjectives.
Never "slot" for a depth in user-facing text.

**CHANGELOG**: one ``CHANGELOG.md``, edited in each pull request: one line per
user-visible change, under 200 characters, with the PR number; a short why only
when the reader must act. Breaking changes also go in the migration page.

**Branches**: every branch starts from ``main``. Keep few branches open at once.
Stack a branch on another only when it needs that branch's code; then merge from
the bottom up, and after each squash merge rebase the next branch with
``git rebase --onto main <old base>``. Two branches that touch the same files
without depending on each other are not open together: merge one before starting
the other.

**Pull requests**: one logical change each. Title ``[TYPE] title`` with one of
``FEAT``, ``FIX``, ``REFACTOR``, ``DOC``, ``TEST``, ``CI``, ``CLEANUP``
(``CLEANUP`` for maintenance: dead code, docstrings, releases). Pull requests are
squash-merged, and the squash commit's title is the PR title, so commit messages on
the branch follow no fixed form. Body:

- ``## Summary``: two or three sentences.
- ``## What's changed``: short one-line bullets.
- ``## Breaking changes``: only if there are any.
- ``## Tests``: what was run.

Each paragraph and bullet is one line (GitHub wraps it). No references to local
notes, rulings, patch names or tags.

A pull request that renames, moves or fixes something this guide names also
updates the guide: the name, the "Planned" note, or the row in
:ref:`dev-deviations`.

**Review**: request a Copilot review on every pull request before merging. Each
comment is fixed or resolved; one resolved without a fix gets a one-line reply
saying why, so the next reader knows it was weighed. The review adds to CI and to
a human reviewer; it replaces neither.


7. Tests and CI
---------------

.. list-table::
   :header-rows: 1
   :widths: 20 50 30

   * - Job
     - Runs
     - Notes
   * - unit
     - ``pytest tests/unit -m "not slow and not local"`` on Ubuntu 3.11–3.13,
       macOS and Windows 3.11; 3.14 allowed to fail; then the docstring examples
       (``pytest --doctest-modules oceanarray --ignore=oceanarray/legacy``)
     - coverage gate 58 % (Ubuntu 3.11); an example that needs files on disk is
       marked ``# doctest: +SKIP`` with the reason above it
   * - integration
     - ``pytest tests/integration -m "not local"``, Ubuntu 3.11
     - golden pipeline tests with ``seasenselib``
   * - wheel
     - builds the wheel, installs it in a fresh environment and opens every
       packaged data file
     - catches files missing from the package
   * - lint
     - ``ruff check .`` and ``ruff format --check .`` over the whole repository;
       ``lint-imports`` (the import contracts)
     - ``legacy/``, ``notebooks/``, ``docs/`` excluded
   * - docs
     - executes ``notebooks/demo_mooring.ipynb``, ``make clean html``
     - pull requests touching ``.py``/``.rst``/``.md``/``.ipynb``
   * - pypi
     - build, ``twine check``, publish
     - on a published GitHub Release

``tests/legacy/`` does not run in CI: it covers the reference code in ``legacy/``.

**Python versions.** A Python version is supported for at least 3 years after its
release (SPEC 0). The floor (``requires-python``, now 3.11) is raised only in a
minor release, and only when that version stops working with a core dependency
(numpy, xarray, gsw) or something breaks on it. CI tests the floor and the newest
release.

Locally, before handing over a change: ``ruff format --check .``, ``ruff check .``
(whole repository), ``lint-imports`` and ``pytest``. ``AGENTS.md`` at the top of
the repository points coding assistants to this guide. The full local run (unit + integration, not
``local``) is about 1900 tests in 5 minutes; the first test to build the session
pages takes about 20 s.

To format on save in VS Code, install the Ruff extension and add to
``.vscode/settings.json``:

.. code-block:: json

   {
     "[python]": {
       "editor.defaultFormatter": "charliermarsh.ruff",
       "editor.formatOnSave": true,
       "editor.codeActionsOnSave": {
         "source.fixAll": "explicit",
         "source.organizeImports": "explicit"
       }
     }
   }

*Planned:* a type checker (pyright, basic mode) on ``pipeline/``, ``readers/`` and
``writers/``, advisory for one release before it can fail a build; the docs built
with ``-W --keep-going`` once the current warnings are cleared. The coverage gate
stays at 58 % until coverage has been measured per module.


.. _dev-deviations:

8. Known deviations from common practice
----------------------------------------

Two tables: conventions kept on purpose, with the reason, and conventions that
are decided to change. A row of the second table is deleted by the pull request
that makes the change.

**Kept on purpose**

.. list-table::
   :header-rows: 1
   :widths: 25 25 50

   * - Our convention
     - Common practice
     - Reason
   * - Vendored report layer: ``reports/_vendored/`` copied byte-identical from
       ctdcast, checked by ``MANIFEST.txt``
     - A shared dependency
     - The shared layer still changes often, and a separate package would need a
       release for every change. Revisited once it has been stable for a few
       months.
   * - Golden HTML tests on the rendered pages
     - Assert on structure, not on whole rendered output
     - The page is the product; a structural change must keep it green and a
       visual change is a reviewable diff. PNGs masked (cross-platform).
   * - CI unit tests run on an editable install; version from git tags via
       setuptools-scm
     - Test the built wheel
     - setuptools-scm keeps the version in one place; the ``wheel`` job checks the
       packaging. Tags are annotated.
   * - Deprecation shims for one release (``_deprecated.RENAMED``,
       ``DeprecatedAlias``)
     - Same
     - Released public API only.
   * - ``legacy/`` shipped in the package, unlinted, uncovered
     - Delete, or move to an archive repository
     - Reference for the RAPID heritage; nothing imports it.
   * - Project scripts in each project's repository, not entry points
     - Entry points in the package
     - oceanarray serves many institutions and holds no single project's paths,
       scripts or choices.
   * - Notebooks not linted; only ``demo_mooring`` executed (docs job)
     - Notebooks tested
     - Exploratory.
   * - One ``CHANGELOG.md`` edited in each PR
     - Changelog fragments (towncrier)
     - One regular contributor; one line per entry. Revisited when a second regular
       contributor appears.
   * - Squash merges; PR titles ``[TYPE]``
     - Conventional commits on ``main``
     - One reviewable unit per PR; the squash title is the PR title (section 6).
   * - Older oceanarray templates with hand-written tables
     - All tables from the shared macros
     - New templates use the shared macros (section 3.8); the older ones move
       over when they are next reworked.
   * - Coverage gate 58 %; ``pipeline/clock/`` omitted
     - Higher gate, nothing omitted
     - Held until coverage is measured per module; the clock code is omitted
       until its use is settled.
   * - Large plotter modules (``plotters/current.py``)
     - Modules of a few hundred lines
     - Splitting a plotter module only moves code; its figures share one set of
       styles and helpers.
   * - Defaults in ``config/parameters.py`` are plain module names
     - Immutable defaults
     - Read-only by rule: settings are passed in through the override order
       (section 3.10), never by reassigning a default.
   * - A broad ``except`` around each report panel
     - Catch specific exceptions
     - A page renders with a stub that names the cause instead of failing whole
       (``render_b64``, ``as_panel``).

**To change**

.. list-table::
   :header-rows: 1
   :widths: 25 20 37 18

   * - Our convention now
     - Common practice
     - What it becomes
     - When
   * - One registry module ``config/parameters.py``
     - One module per domain
     - A package ``config/parameters/``, one module per group, re-exported so
       ``params.X`` and every import keep working
     - naming review
   * - ``stack()`` as one very long function
     - Short functions
     - A ``pipeline/stackers/`` subpackage, as ``pipeline/gridders/``
     - after 0.7.0
   * - Private helpers imported across subpackages
     - Private means module-internal
     - Public, or moved to the subpackage that uses them (section 2)
     - naming review
   * - Annotations checked by ruff ``ANN`` only
     - A type checker in CI
     - pyright on ``pipeline/``, ``readers/``, ``writers/`` (section 7)
     - planned
   * - An unknown top-level YAML key is a WARNING
     - Unknown keys rejected
     - An ERROR, after a survey of real mooring YAMLs
     - 0.7.0
   * - ``reports/_verb.py`` reassigns ``SIGMA_GRID``
     - Settings passed in
     - Passed as an argument (section 3.10)
     - planned
   * - Import-time side effects: ``OCEANARRAY_RAISE_ON_PLOT_ERROR`` read at
       import; the package logger level set at import
     - No side effects at import
     - The switch read at render time (3.13); the level set when a log file or
       console is attached (3.4)
     - 0.7.0
   * - ``except`` blocks that swallow an error silently
     - Catch specific exceptions
     - Narrowed or removed
     - planned
   * - Docstrings longer than the code
     - Proportionate docstrings
     - Trimmed (section 6)
     - planned
   * - ``print`` in library code
     - ``logging``
     - ``logging`` (section 3.4)
     - planned
   * - ``warnings.warn(UserWarning)`` for some data problems
     - ``logging`` for run-time events
     - ``log.warning``; ``warnings`` for deprecations only (section 3.4)
     - planned
   * - Some figures drop ``QC_BAD_FLAGS`` (3, 4, 9) themselves
     - One rule
     - Every figure masks through ``pipeline/masking.py`` (section 3.7)
     - planned
   * - Paths in the YAML: ``caldip.root``, ``sensor_catalog``
     - Configuration separate from location
     - From the call, a CLI flag or a user configuration file (section 3.9)
     - planned
   * - Exceptions without a common base
     - One package base exception
     - ``OceanarrayError`` as the base (section 3.4)
     - 0.7.0
   * - Defaults in ``config/parameters.py`` and ``pipeline/_run.DEFAULTS``
     - One place
     - All in ``config/parameters.py`` (section 3.10)
     - 0.7.0
   * - Docs build without ``-W``
     - Warnings as errors
     - ``-W --keep-going``, once the current warnings are cleared
     - planned
