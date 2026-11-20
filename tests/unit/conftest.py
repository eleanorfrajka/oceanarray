"""Report pages built once per session from the committed fixtures, for reading only.

The dune2 mooring (deployment, stack, grid and both instrument pages) and the egM2
2016 and 2018 deployments of OSNAP East (their stacks, the site's Stack cat and
Grid cat) are rendered once; a test that needs a variant builds its own.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import warnings
from pathlib import Path

import pytest

_PROC = Path(__file__).resolve().parents[1] / "fixtures" / "proc"
DUNE2 = "dune2_1_2026"
EGM2 = ("egM2_2016", "egM2_2018")


def _quiet(fn, *args, **kwargs):
    """Call *fn* with warnings and stdout silenced."""
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


@pytest.fixture(scope="session")
def dune2_pages(tmp_path_factory) -> Path:
    """The folder of the dune2 report pages (instrument pages in ``instrument/``)."""
    from oceanarray.reports import MooringReport
    from oceanarray.reports._vendored import design_tokens

    out = tmp_path_factory.mktemp("dune2_pages")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(design_tokens, "RAISE_ON_PLOT_ERROR", True)
        _quiet(
            MooringReport(proc_dir=str(_PROC)).generate,
            DUNE2,
            force=True,
            outdir=str(out),
            instruments=True,
            grid=True,
            stack=True,
        )
    return out


@pytest.fixture(scope="session")
def egm2_site(tmp_path_factory) -> Path:
    """The egM2 site folder written by ``oceanarray concat`` (pages in ``report/``)."""
    import oceanarray as oa
    from oceanarray import mooring, paths
    from oceanarray.cli import build_parser
    from oceanarray.pipeline.read_mooring import read_mooring
    from oceanarray.reports._vendored import design_tokens

    root = tmp_path_factory.mktemp("osnap")
    for name in EGM2:
        shutil.copytree(_PROC / "egM2_concat" / name, root / name)
        cfg = mooring.load(root / name / f"{name}.mooring.yaml")
        data = _quiet(
            read_mooring, cfg, stage=3, mooring_dir=root / name, allow_missing=True
        )
        oa.write(
            _quiet(oa.stack, data, cfg), paths.product_file(root / name, name, "stack")
        )
    yamls = [str(root / n / f"{n}.mooring.yaml") for n in EGM2]
    args = build_parser().parse_args(
        ["concat", *yamls, "--name", "egM2", "--dp", "50", "--force"]
    )
    with (
        pytest.MonkeyPatch.context() as mp,
        warnings.catch_warnings(),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        mp.setattr(design_tokens, "RAISE_ON_PLOT_ERROR", True)
        warnings.simplefilter("error", UserWarning)  # a page that fails warns
        assert args.func(args) == 0
    return root / "egM2"
