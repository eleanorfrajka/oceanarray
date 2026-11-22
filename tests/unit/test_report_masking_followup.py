"""The instrument tables' pressure range follows the mooring's flag rule."""

from pathlib import Path

import numpy as np
import xarray as xr

from oceanarray import mask_rules
from oceanarray.reports._read import _read_instrument_info

FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "proc"
    / "egM2_concat"
    / "egM2_2018"
    / "microcat"
    / "egM2_2018_13220_stage3.nc"
)


def test_pressure_range_leaves_out_what_the_mooring_rule_hides(tmp_path):
    """With the deepest pressure flagged suspect, a rule hiding 3 leaves it out."""
    folder = tmp_path / "microcat"
    folder.mkdir()
    with xr.open_dataset(FIXTURE) as opened:
        ds = opened.load()
    p = ds["pressure"].values.astype(float)
    deepest = int(np.nanargmax(p))
    flags = ds["pressure_qc"].values.copy()
    flags[deepest] = 3
    ds["pressure_qc"].values = flags
    ds.to_netcdf(folder / FIXTURE.name)

    default = _read_instrument_info(tmp_path, "microcat", "egM2_2018", "13220")
    strict = _read_instrument_info(
        tmp_path,
        "microcat",
        "egM2_2018",
        "13220",
        rule=mask_rules.resolve_mask_flags([3, 4]),
    )
    kept = np.isfinite(p) & (p > 0) & ~np.isin(flags, (3, 4))
    assert default["p_max"] == float(p[deepest])
    assert strict["p_max"] == float(p[kept].max())
    assert strict["p_max"] < default["p_max"]
