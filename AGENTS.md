# Notes for coding assistants

Read `docs/source/developer.rst` (the developer guide) before changing code. It sets out the layers, the one idiom for each recurring job, the vocabulary and the naming rules; follow it, and update it when a change renames, moves or fixes something it names.

- Test fixtures are trimmed real instrument files (`tests/fixtures/`), never synthetic or mocked data. An expected value is one you can justify without running the code.
- Code, docstrings and comments describe the code. They never refer to tools, local notes, plan or handoff files, or paths on a particular machine.
- Docstrings are NumPy style with type information; data passes between functions as xarray objects.
- One logical change per pull request; keep pull requests small.
- Before handing over a change, run `ruff format --check .`, `ruff check .`, `lint-imports` and `pytest`.
- Pull request titles and text follow `CONTRIBUTING.md`: `[TYPE] title`, then Summary, What's changed, Breaking changes (only if any) and Tests.
- Never substitute a default for a value that cannot be determined: raise, or warn and record the assumption in the file.
