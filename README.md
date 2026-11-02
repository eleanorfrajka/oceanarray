# OceanArray

[![PyPI](https://img.shields.io/pypi/v/oceanarray.svg)](https://pypi.org/project/oceanarray/)
[![Python versions](https://img.shields.io/pypi/pyversions/oceanarray.svg)](https://pypi.org/project/oceanarray/)
[![License: MIT](https://img.shields.io/pypi/l/oceanarray.svg)](https://github.com/ocean-uhh/oceanarray/blob/main/LICENSE)
[![Tests](https://github.com/ocean-uhh/oceanarray/actions/workflows/tests.yml/badge.svg)](https://github.com/ocean-uhh/oceanarray/actions/workflows/tests.yml)
[![Documentation](https://img.shields.io/badge/docs-ocean--uhh.github.io-blue.svg)](https://ocean-uhh.github.io/oceanarray/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21890564.svg)](https://doi.org/10.5281/zenodo.21890564)

Python tools for processing moored oceanographic array observations from raw instrument files to quality-controlled, CF-compliant NetCDF.

It covers a single mooring's instruments — stages 1–3, stacking, and pressure gridding, each documented in an HTML report. Deriving calibration-dip offsets is handled by `caldip`, and shipboard CTD processing by `ctdcast`.

**Documentation**: https://ocean-uhh.github.io/oceanarray/

## Installation

```bash
pip install oceanarray
```

Or, with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install oceanarray
```

This pulls in `seasenselib` (used to read raw instrument files in stage 1) automatically.

Python 3.11–3.13 is supported. See the [installation guide](https://ocean-uhh.github.io/oceanarray/installation.html) for isolated-environment (conda / venv / uv) and from-source options.

## Quick start

Create a skeleton YAML, fill in the deployment metadata, then process:

```bash
oceanarray init dsG3_1_2026 --proc-dir /data/proc
# edit /data/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml
oceanarray validate /data/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml
oceanarray run     /data/proc/dsG3_1_2026 --raw-dir /data/raw     # stages 1-3, stack, grid, report
```

See the [documentation](https://ocean-uhh.github.io/oceanarray/) for the recommended first-processing workflow, YAML reference, and CLI reference.

## Python API

```python
import oceanarray as oa

yaml_path = "/data/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml"
oa.process(yaml_path, raw_dir="/data/raw")  # stages 1-3
oa.run(yaml_path, from_stage=3, report=True)  # re-run stage 3, stack, grid, report
```

See the [API reference](https://ocean-uhh.github.io/oceanarray/oceanarray.html) for the full interface.

## Acknowledgements

Development was assisted by Claude Code (Anthropic) and GitHub Copilot code review.

The data-processing approach draws on methods developed for the RAPID mooring array programme (UK/US funded). `oceanarray` is developed toward the DFG Ocean Array infrastructure project (DFG project number 571027118), from which it takes its name, and was first applied in the AEI–DFG MIXSED project (DFG project number 541914507).

## License

MIT License
