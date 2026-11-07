# Project Style Guide

> This guide explains the coding and formatting conventions used in the project, and how to keep things consistent when adding or editing code.

It combines Python code conventions, documentation standards, and metadata formatting guidelines.

---

## Python Coding Conventions

### Function Definitions
- Use **type hints** for all parameters and return types.
```python
def convert_units(
    values: xr.DataArray,
    current: str,
    target: str,
) -> xr.DataArray:
    ...
```

### Naming
- Functions and variables: `snake_case`
- Constants (e.g., in xarray datasets): `ALL_CAPS`

### Docstrings
- Use **NumPy-style** docstrings
- Required for all functions.
- Sections include: `Parameters`, `Returns`, `Notes`, (optionally `Examples`, `References`)

Example:
```python
def convert_units_var(
    var_values: xr.DataArray,
    current_unit: str,
    new_unit: str,
    unit_conversion: dict = unit_conversion,
) -> xr.DataArray:
    """
    Convert variable values from one unit to another.

    Parameters
    ----------
    var_values : xr.DataArray
        The numerical values to convert.
    current_unit : str
        Unit of the original values.
    new_unit : str
        Desired unit for the output values.
    unit_conversion : dict, optional
        Dictionary containing conversion factors between units.

    Returns
    -------
    xr.DataArray
        Converted values in the desired unit.

    Notes
    -----
    If no valid conversion is found, the original values are returned unchanged.
    """
```

> NumPy-style docstrings render cleanly in Sphinx documentation (e.g. on Read the Docs). See [NumPy docstring style](https://numpydoc.readthedocs.io/en/latest/format.html).
> 
> Alternatives like [Google-style](https://sphinxcontrib-napoleon.readthedocs.io/en/latest/example_google.html) and [reST-style](https://www.sphinx-doc.org/en/master/usage/restructuredtext/domains.html#info-field-lists) docstrings are also valid with Sphinx if configured properly.

---

## Dataset & Metadata Conventions

### Variable Names
- Variable naming conventions are currently inconsistent across the codebase
- We need to verify and align with [OceanSites variable naming recommendations](https://www.ocean-ops.org/oceansites/data/index.html)
- Mix of `ALL_CAPS` (`TIME`, `DEPTH`) and lowercase (`temperature`, `pressure`) currently in use
- Keep names short and unambiguous

### Attributes
- Follow [OceanSites format conventions](https://www.ocean-ops.org/oceansites/data/index.html)
- Use `units`, `long_name`, `comment`
- Avoid placing units in variable names — use attributes instead

### Consistency
- Units are checked and converted using helper functions
- Time is standardized using utility functions across the project

---

## Formatting and linting

The project uses `ruff` for both linting and formatting, and `pytest` for tests. CI runs the same checks, so run them before pushing:

```bash
ruff check . --fix              # lint, fixing what it can
ruff format                     # format (ruff format --check to verify only)
pytest                          # tests
```

---

## Optional: VSCode setup

To format on save with ruff, install the Ruff extension and add this to your **Workspace Settings** (`.vscode/settings.json`):
```json
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
```

---

By following these conventions, your code will integrate smoothly with the rest of the project.
