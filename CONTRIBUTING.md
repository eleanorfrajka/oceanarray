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

Where a contribution is adapted from someone else's work rather than written from scratch, we credit it **in the docstring of the code itself**, so the attribution travels with the code rather than living in a file nobody reads (see the existing examples in `pipeline/gridders/rapid_interp.py` and `plotters/current.py`).

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

oceanarray **processes moored-array observations**: raw instrument files → CF-NetCDF at stages 1–3 → a stack on a common time axis → a grid on pressure levels, each documented in an HTML report, with an OceanSITES export.

- **Fixed:** the output contract — variable names, standard names and unit spellings from one registry, OceanSITES/QARTOD flags, every processing setting recorded in the file. Changes to these change every user's files and are discussed in an issue first.
- **Open, as selectable options with the default unchanged:** gridding methods (PCHIP, the RAPID climatological-gradient method, handling above the shallowest instrument); how per-instrument corrections are applied in stage 3; new QC tests; new figures and report panels; readers (written in `seasenselib` where its maintainers can take them, see the developer guide).
- **Out of scope, because a sibling owns it:** shipboard CTD processing (`ctdcast`), deriving calibration-dip offsets (`caldip`), glider data (`glidertest`, `seagliderOG1`), transport and AMOC estimates (`AMOCatlas`), general-purpose plotting. Also out: cruise-specific raw data in the repository, heavy new dependencies.

If you are unsure whether an idea fits, open an issue first.

---

## Your First Code Contribution

How the code is organized and written (layers, the one idiom for each recurring job, vocabulary, naming, tests) is in the [developer guide](https://ocean-uhh.github.io/oceanarray/developer.html). Read it before your first pull request.

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
3. Run `ruff check . --fix`, `ruff format .` and `lint-imports`, then `pytest` — all tests must pass. (`ruff format` owns line wrapping, which is why `E501` is ignored in `pyproject.toml`.)
4. Open a pull request against `main`. Prefix the title with a bracket tag: `[FEAT]`, `[FIX]`, `[REFACTOR]`, `[DOC]`, `[TEST]`, `[CI]`, or `[CLEANUP]`. Pull requests are squash-merged, and the squash commit's title is the PR title.

Keep each pull request to one logical change. A rename PR that also fixes a bug is a PR nobody can review — split them. (This matters most once more than one person is working on the code.)

### Commit messages

Commit messages on your branch follow no fixed form: the squash merge replaces them with the PR title. If you worked with someone, or adapted their code, add a trailer to the PR description or a commit:

```
Co-authored-by: Name <email@example.com>
```

### Code conventions

All of them are in the [developer guide](https://ocean-uhh.github.io/oceanarray/developer.html). The ones a first pull request most often misses:

- NumPy-style docstrings and type annotations on every public function.
- Every processing setting is stored in the output file, so it can be reprocessed from itself.
- Never a silent default: when the right value cannot be determined, raise, or warn and record what was assumed.
- `ruff check .` and `ruff format --check .` clean.

### Testing

Tests live in `tests/unit/` and `tests/integration/`. Run the full suite:

```bash
pytest                                                # all tests
pytest tests/unit -m "not slow and not local"         # as the CI unit job
pytest --cov=oceanarray --cov-report=term-missing -q  # with coverage
```

New or changed code must have a test, with fixtures from real instrument files (not mocks) and expected values you can justify without running the code. Report pages are compared with saved copies; re-baseline them (`REBASELINE_GOLDEN=1 pytest`) only after looking at the change. Details: the developer guide, *Tests*.

---

## Improving The Documentation

Documentation is built with Sphinx from `docs/source/`. To build locally:

```bash
pip install -e ".[docs]"
cd docs && make clean html
open build/html/index.html
```

Docstring changes are picked up automatically. For narrative docs, edit `.rst` files in `docs/source/`. The API reference is auto-generated from docstrings via `autodoc`.
