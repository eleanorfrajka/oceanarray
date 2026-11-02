# Contributing to oceanarray

Thanks for taking the time to contribute.

All contributions are welcome: bug reports, feature suggestions, documentation improvements, and code.

## Table of Contents

- [Licensing of contributions](#licensing-of-contributions)
- [Credit and authorship](#credit-and-authorship)
- [I Have a Question](#i-have-a-question)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Enhancements](#suggesting-enhancements)
- [Your First Code Contribution](#your-first-code-contribution)
- [Improving The Documentation](#improving-the-documentation)

---

## Licensing of contributions

oceanarray is MIT licensed, and contributions are accepted **under the same licence** — what GitHub calls "inbound = outbound". By opening a pull request you confirm that:

1. you wrote the contribution, or you have the right to submit it; and
2. you agree to license it to the project under the MIT licence.

This does not transfer your copyright, which remains yours.

**If your contribution contains code from somewhere else** — another repository, a paper's supplementary material, a Stack Overflow answer, a colleague's script, or generated output you did not review — say so in the pull request and name the source and its licence. This is the single most useful thing you can tell a reviewer. Code from an unlicensed source cannot be merged until its author agrees, so flagging it early avoids the work being wasted.

---

## Credit and authorship

Three separate things.

**Copyright** stays with whoever wrote the code. You are not asked to assign it.

**Contributor credit** is automatic: everyone whose pull request is merged appears in the git history. If your name or preferred email in the git log is wrong, add a `.mailmap` entry via a pull request — that is the correct fix.

**Citation authorship** is the author list in `CITATION.cff`, which propagates into the Zenodo DOI for every release and therefore into other people's bibliographies. It reflects *substantial* contribution to the software — its design, a significant body of its implementation, its test suite, or its documentation architecture — and is decided by the maintainer at release time. Funding or supervision alone does not qualify. There is no line count that guarantees it or excludes it. If you believe your contribution crosses that line and it has not been reflected, please say so in an issue — being asked is better than being resented.

Where a contribution is adapted from someone else's work rather than written from scratch, we credit it **in the docstring of the code itself**, so the attribution travels with the code rather than living in a file nobody reads (see the existing examples in `utilities.py` and `plotters/current.py`).

---

## I Have a Question

Read the [documentation](https://ocean-uhh.github.io/oceanarray/) first.
Then search [existing issues](https://github.com/ocean-uhh/oceanarray/issues) — your question may already be answered.

If you still need help, [open an issue](https://github.com/ocean-uhh/oceanarray/issues/new) and include:

- What you were trying to do and what you expected to happen.
- A minimal reproducible example (e.g. a short script or notebook cell).
- Your platform, Python version, and `pip show oceanarray seasenselib` output.

---

## Reporting Bugs

Security vulnerabilities must be reported by email to [eleanorfrajka@gmail.com](mailto:eleanorfrajka@gmail.com) — do not use the public issue tracker.

For all other bugs:

1. Confirm you are on the latest release (`pip show oceanarray`).
2. [Open an issue](https://github.com/ocean-uhh/oceanarray/issues/new) with:
   - Steps to reproduce (minimal script if possible).
   - Expected vs. actual behaviour.
   - Full traceback.
   - OS, Python version, `oceanarray` and `seasenselib` versions.

---

## Suggesting Enhancements

[Open an issue](https://github.com/ocean-uhh/oceanarray/issues/new) describing:

- What you want to do that is currently impossible or inconvenient.
- Why it would be useful to other users (not just your specific cruise).
- Whether you are willing to implement it yourself.

### Scope

oceanarray **processes moored-array observations**: raw instrument files from a mooring (MicroCATs, RBRs, Nortek and RDI current meters and ADCPs, SBE 16/56, Seapoint, …) → CF-NetCDF at each stage — stage 1 (raw → NetCDF), stage 2 (deployment window, clock), stage 3 (QC, velocity rotation, calibration-dip offsets applied) — then a stack on a common time axis and a grid on pressure levels, each step documented in an HTML report. The OceanSITES export of the stacked and gridded products is the next in-scope step.

**Fixed — not open to variation:** the output contract. Variable names, CF standard names and the canonical unit spellings come from one registry (`config/parameters.py`) and are the same in every file the package writes; QC flags follow the QARTOD/OceanSITES vocabulary; every processing parameter is recorded in the output's attributes so a file can be reprocessed from itself. Contributions that change these change every user's files, and are discussed first, as an issue.

**Open — alternative methods welcome, as selectable options with the default unchanged:**

- **Gridding.** The grid step is where the method choices are made — today linear interpolation in pressure onto a fixed `dp` grid, with σ₀ targets for isopycnal products. Alternatives: a **PCHIP spline**; the **RAPID climatological-gradient interpolation** (`tools/rapid_interp.py`, written but **not yet integrated** into `grid.py`); and bounds handling above the shallowest instrument — **extrapolate to the surface**, clip, or mask. All welcome as named methods the user picks in the config, each recording its name and parameters in the output.
- **Corrections to individual instruments.** How calibration-dip offsets and other per-instrument corrections (drift, pressure-sensor offset) are applied in stage 3 — constant, linear in time, pre/post weighted — is a method choice; the *derivation* of caldip offsets stays in `caldip`, oceanarray applies its output.
- **QC tests.** New QARTOD-style tests or thresholds, as long as the flags land in the existing `_qc` companions with their thresholds recorded.
- **Diagnostic figures and report panels.** New figures are welcome, and the report is meant to let a user choose their own selection of panels per page (a config-driven profile); contributions that make that selection easier are in scope.
- **Readers.** Instrument readers live in `seasenselib`; oceanarray's stage 1 dispatches on the file type (`config/parameters.INSTRUMENT_FILE_TYPES`) and keeps only a small set of its own legacy readers (`tools/readers.py`: RODB and the old Nortek CSV). A reader for an instrument `seasenselib` does not cover is welcome — but write it in `seasenselib` first and add the file type here; a reader that only oceanarray would ever use can live in `tools/readers.py`. (Where exactly that line is drawn is not written down yet — ask.)

**Out of scope, because a sibling owns it:** shipboard CTD processing (`ctdcast`); deriving calibration-dip offsets (`caldip`); glider data (`glidertest`, `seagliderOG1`); transport and AMOC estimates from the gridded products (`AMOCatlas`); general-purpose plotting. Also out: features that need cruise-specific raw data in the repository, heavy new dependencies, or that break the CF-NetCDF contract. If you are unsure whether an idea fits, open an issue describing it rather than deciding alone.

---

## Your First Code Contribution

### Architecture overview

| Layer | Modules | Role |
|-------|---------|------|
| Tier 1 | `tools/`, `writers/`, `utilities.py`, `inspect.py` | Low-level I/O and pure functions — raw-file readers, the single netCDF writer, dataset inspection |
| Tier 2 | `pipeline/stage1.py` … `pipeline/grid.py` | Processing orchestrators — read, transform, write CF-NetCDF |
| Tier 3 | `cli.py` | Entry point — argument parsing, subcommand dispatch |
| — | `plotters/`, `reports/` | Visualisation and HTML report generation |
| — | `analysis/` | Science utilities (filtering, isopycnals, QC) |
| — | `config/` | Parameters, variable registry, YAML validation |

Processing flows from raw files → Stage 1 (CF-NetCDF) → Stage 2 (trimming + clock) → Stage 3 (QC + ENU) → stack → grid.

### Development setup

```bash
git clone https://github.com/ocean-uhh/oceanarray.git
cd oceanarray
python -m venv venv && source venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

This installs the runtime stack (including `seasenselib` for raw-file reading),
plus the test and docs tooling and `ruff`, in one resolution. To also work on
PDF report output (WeasyPrint, which needs native pango/cairo libraries), use
`pip install -e ".[all]"` (everything, equivalent to `.[dev,pdf]`).

### Workflow

1. Fork the repository and create a feature branch from `main`.
2. Make your changes, following the conventions below.
3. Run `ruff check . --fix` and `ruff format .`, then `pytest` — all tests must pass. (`ruff format` owns line wrapping, which is why `E501` is ignored in `pyproject.toml`.)
4. Open a pull request against `main`. Prefix the title with a bracket tag: `[FEAT]`, `[FIX]`, `[REFACTOR]`, `[DOC]`, `[TEST]`, `[CI]`, or `[CLEANUP]`. (PR titles use bracket tags; individual **commit messages** use conventional-commit prefixes — see below.)

Keep each pull request to one logical change. A rename PR that also fixes a bug is a PR nobody can review — split them. (This matters most once more than one person is working on the code.)

### Commit messages

Use conventional-commit prefixes (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `ci:`, `chore:`) with an imperative subject. If you worked with someone, or adapted their code, add a trailer:

```
Co-authored-by: Name <email@example.com>
```

### Code conventions

- **Docstrings**: NumPy style on every public function and class. Include `Parameters`, `Returns`, and `Raises` sections. Period at the end.
- **Type annotations**: all public function arguments and return values.
- **Lint**: `ruff check .` must be clean. No bare `except:`. No suppressed warnings without a comment explaining why.
- **Units**: every registered variable carries the registry's canonical spelling (`config/parameters.VARIABLES[var]["units"]`), and that spelling is identical in every file the package writes — except N², kept as `s-2` or `rad2 s-2` as it arrives (`config/unit_tables.UNIT_SPELLINGS_KEPT`). The pipeline rewrites known spellings through the shared units tables (`config/unit_tables.TABLES`) and refuses unknown ones. A new spelling is added **with evidence** — a reader that emits it — never from memory: to `config/unit_tables.UNIT_ALIAS_EXTRAS` if only oceanarray's readers emit it, or upstream in ctdcast's vendored `units_tables.py` if it belongs to the family. A mass↔volume conversion is never a scalar alias. Use `gsw` for all seawater property calculations.
- **Plot style**: discrete colorbars (`BoundaryNorm`, ≤ 20 levels). Report figures are styled at one choke point — `reports/_encode.render_b64` applies the vendored `report.mplstyle` and `reports/_slots.render` draws each figure at its slot width — so a report plotter does not call `plt.style.use` itself.
- **Private helpers**: a leading underscore keeps a helper out of the public API; it may be shared inside its own subpackage (`reports/`, `cli/`, `pipeline/`). Its docstring is **one line** plus a typed signature; a reason the code cannot show (a units or flag-table detail, a precedence order, an edge case) goes in a `#` comment at the line it explains. The name says what comes back, with one verb per job:

  | verb | returns |
  |---|---|
  | `_read_<what>(path)` | an object read from a file |
  | `_parse_<what>(text)` | a typed value from a string in hand, no I/O |
  | `_resolve_<what>(…)` | the one value that applies, from several sources by precedence |
  | `_format_<what>(value)` | a display string |
  | `_build_<what>(…)` | rows, a dict or a dataset |
  | `_render_<what>(…)` | an HTML fragment |
  | `_draw_<what>(…)` | a matplotlib Figure (public: `draw_<what>`; no `_fig` suffix) |
  | `_make_<what>_b64(…)` | a base64 PNG for a report slot (`_b64s` for a list) |
  | `_has_<what>` / `_is_<what>` | a bool |
  | `_apply_<what>(ds, …)` | the dataset after one processing step |
  | `_add_<what>(…)` | the target with variables, attributes or flags added |
  | `_rename_<what>(ds)` / `_to_canonical_<form>(ds)` | renamed variables / a foreign layout in ours |
  | noun (`_time_axis_bounds`) | a computed result |

  Words with a British/American variant are avoided where a neutral word exists (`_rename_`, not `_normalize_`); `color` is the exception. `tests/unit/test_private_names.py` fails on a retired prefix.
- **Data provenance**: store all processing parameters (thresholds, coefficients) in NC output attributes so any file can be reprocessed exactly from itself.
- **No silent defaults**: never substitute a default, guess, or approximation when the correct value cannot be determined. Raise, or warn loudly and record what was assumed in the output's metadata. A plausible wrong number is worse than an error, because nothing downstream can detect it.

### Testing

Tests live in `tests/unit/` and `tests/integration/`. Run the full suite:

```bash
pytest                                                # all tests
pytest tests/unit -m "not slow and not needs_seasenselib"  # without seasenselib
pytest --cov=oceanarray --cov-report=term-missing -q  # with coverage
```

New or changed code must have a test. For anything numerical, assert a **value** you can justify independently of the code — `pytest.approx` or `numpy.testing.assert_allclose` against an analytic case, an invariant that must hold for any input, or a cross-check against `gsw` or another implementation — rather than a value produced by running the code (which proves only that the code has not changed). Do not add tests that pass when nothing happened: `assert result is not None` on a function that returns `None` on failure is the shape to avoid.

Fixtures are **real instrument data** — the committed `dune2` raw/proc set in `tests/fixtures/`, trimmed down — not synthetic mocks; a new reader path gets a small real file. Report HTML is **golden-tested** (`tests/fixtures/golden/`, rendered and compared by `tests/unit/test_report_golden.py`): a change to the rendered output is re-baselined (`REBASELINE_GOLDEN=1 pytest`) only after a before/after look, never silently, so the intended change *is* the reviewable diff.

---

## Improving The Documentation

Documentation is built with Sphinx from `docs/source/`. To build locally:

```bash
pip install -e ".[docs]"
cd docs && make clean html
open build/html/index.html
```

Docstring changes are picked up automatically. For narrative docs, edit `.rst` files in `docs/source/`. The API reference is auto-generated from docstrings via `autodoc`.
