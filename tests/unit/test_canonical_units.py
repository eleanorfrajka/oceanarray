"""Unit tests for the units-canonicalisation machinery.

Covers :func:`oceanarray.pipeline.units.to_canonical_units` (the registry-scoped,
idempotent pass that rewrites a file's units to one canonical spelling per quantity),
oceanarray's view of the family tables (:mod:`oceanarray.config.unit_tables`) — its
density-basis invariant and its agreement with the registry — and
:func:`oceanarray.config.unit_tables.unit_label` (the plot-label derivation every
registered variable's display unit is derived from, via :func:`vunit`/:func:`vlabel`).
"""

from __future__ import annotations

import re
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from oceanarray.config import parameters as params
from oceanarray.config import unit_tables as ut
from oceanarray.pipeline import units as pu


def _ds(**vars_with_units):
    """Build a 1-D dataset; each kwarg is ``name=(values, units[, standard_name])``."""
    data = {}
    for name, spec in vars_with_units.items():
        values, units, *rest = spec
        attrs = {"units": units}
        if rest:
            attrs["standard_name"] = rest[0]
        data[name] = xr.Variable("time", np.asarray(values, dtype=float), attrs=attrs)
    return xr.Dataset(data)


# ---------------------------------------------------------------------------
# to_canonical_units — spelling variants (x1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("degC", "degree_Celsius"),
        ("degrees_C", "degree_Celsius"),
        ("Degrees C", "degree_Celsius"),
        ("degree_Celsius", "degree_Celsius"),
    ],
)
def test_temperature_spellings_rewrite_without_changing_values(spelling, canonical):
    ds = _ds(temperature=([1.0, 2.0, 3.0], spelling))
    out = pu.to_canonical_units(ds)
    assert out["temperature"].attrs["units"] == canonical
    np.testing.assert_array_equal(out["temperature"].values, [1.0, 2.0, 3.0])


def test_depth_meters_rewrites_to_m():
    ds = _ds(depth=([10.0, 20.0], "meters"))
    out = pu.to_canonical_units(ds)
    assert out["depth"].attrs["units"] == "m"


def test_conductivity_mS_slash_cm_rewrites_x1_without_provenance_stamp():
    ds = _ds(conductivity=([40.0], "mS/cm"))
    out = pu.to_canonical_units(ds)
    assert out["conductivity"].attrs["units"] == "mS cm-1"
    assert out["conductivity"].values[0] == 40.0
    # A pure spelling rewrite (x1) is not a conversion — no provenance attrs.
    assert "units_conversion_factor" not in out["conductivity"].attrs
    assert "units_converted_from" not in out["conductivity"].attrs


# ---------------------------------------------------------------------------
# to_canonical_units — the one factor conversion (S/m x10)
# ---------------------------------------------------------------------------


def test_conductivity_S_per_m_converts_x10():
    ds = _ds(conductivity=([4.0, 5.0], "S/m"))
    out = pu.to_canonical_units(ds)
    assert out["conductivity"].attrs["units"] == "mS cm-1"
    np.testing.assert_array_equal(out["conductivity"].values, [40.0, 50.0])
    assert out["conductivity"].attrs["units_conversion_factor"] == 10.0


# ---------------------------------------------------------------------------
# idempotency, logging, provenance
# ---------------------------------------------------------------------------


def test_already_canonical_is_noop_without_log():
    ds = _ds(conductivity=([40.0], "mS cm-1"))
    msgs: list[str] = []
    out = pu.to_canonical_units(ds, log_fn=msgs.append)
    assert out["conductivity"].values[0] == 40.0
    assert "units_converted_from" not in out["conductivity"].attrs
    assert msgs == []  # no change -> no log


def test_second_pass_is_idempotent():
    ds = _ds(conductivity=([4.0], "S/m"))
    once = pu.to_canonical_units(ds)
    twice = pu.to_canonical_units(once)
    assert twice["conductivity"].values[0] == 40.0  # not 400
    assert twice["conductivity"].attrs["units"] == "mS cm-1"


def test_later_stage_change_is_annotated():
    ds = _ds(temperature=([1.0], "degC"))
    msgs: list[str] = []
    pu.to_canonical_units(ds, log_fn=msgs.append, stage=3)
    assert len(msgs) == 1
    assert "pre-0.5.0 stage 1" in msgs[0]


# ---------------------------------------------------------------------------
# standard_name matching, unregistered / inactive variables
# ---------------------------------------------------------------------------


def test_standard_name_match_for_non_registry_name():
    # 'eastward_current' is not a VARIABLES key, but its standard_name is.
    ds = _ds(eastward_current=([0.1], "m/s", "eastward_sea_water_velocity"))
    out = pu.to_canonical_units(ds)
    assert out["eastward_current"].attrs["units"] == "m s-1"


def test_unregistered_variable_untouched():
    ds = _ds(battery_voltage=([12.3], "volts"))
    out = pu.to_canonical_units(ds)
    assert out["battery_voltage"].attrs["units"] == "volts"  # unchanged


def test_oxygen_umol_per_kg_is_a_spelling_rewrite_not_a_conversion():
    # dissolved_oxygen's canonical is per-mass umol kg-1; the reader's 'umol/kg'
    # is the same basis, so x1 — never a density-dependent L<->kg conversion.
    ds = _ds(dissolved_oxygen=([220.0], "umol/kg"))
    out = pu.to_canonical_units(ds)
    assert out["dissolved_oxygen"].attrs["units"] == "umol kg-1"
    assert out["dissolved_oxygen"].values[0] == 220.0


def test_turbidity_dimensionless_one_is_canonical():
    # turbidity's canonical is now '1'; a file carrying '1' is already canonical.
    ds = _ds(turbidity=([5.0], "1", "sea_water_turbidity"))
    out = pu.to_canonical_units(ds)
    assert out["turbidity"].attrs["units"] == "1"


def test_variable_scoped_ntu_and_psu():
    # NTU is accepted for turbidity, PSU for salinity (both canonical '1')...
    assert (
        pu.to_canonical_units(_ds(turbidity=([5.0], "NTU", "sea_water_turbidity")))[
            "turbidity"
        ].attrs["units"]
        == "1"
    )
    assert (
        pu.to_canonical_units(
            _ds(salinity=([35.0], "PSU", "sea_water_practical_salinity"))
        )["salinity"].attrs["units"]
        == "1"
    )


def test_cross_variable_dimensionless_label_is_refused():
    # ...but a salinity file saying NTU (or turbidity saying PSU) must raise, not be
    # silently relabelled — the scoping keeps one quantity from borrowing another's.
    with pytest.raises(pu.UnitsError):
        pu.to_canonical_units(
            _ds(salinity=([35.0], "NTU", "sea_water_practical_salinity"))
        )
    with pytest.raises(pu.UnitsError):
        pu.to_canonical_units(_ds(turbidity=([5.0], "PSU", "sea_water_turbidity")))


def test_oxygen_ml_l_lowercase_spelling_accepted():
    # The hex reader writes 'ml l-1' (lowercase); it must canonicalise to 'mL L-1'.
    # dissolved_oxygen_ml_l matches by name (a registry key).
    ds = _ds(dissolved_oxygen_ml_l=([5.0], "ml l-1"))
    out = pu.to_canonical_units(ds)
    assert out["dissolved_oxygen_ml_l"].attrs["units"] == "mL L-1"


def test_coordinate_is_canonicalised():
    # depth is a coordinate in stack/grid files; the pass must walk ds.variables,
    # not ds.data_vars, or 'meters' on a depth coordinate survives.
    ds = xr.Dataset(
        {"temperature": ("depth", np.array([10.0, 11.0]), {"units": "degC"})},
        coords={"depth": ("depth", np.array([100.0, 200.0]), {"units": "meters"})},
    )
    out = pu.to_canonical_units(ds)
    assert out["depth"].attrs["units"] == "m"


def test_units_metadata_stamped_on_temperature():
    ds = _ds(temperature=([1.0], "degC"))
    out = pu.to_canonical_units(ds)
    assert out["temperature"].attrs["units_metadata"] == "temperature: on_scale"


def test_conversion_drops_stale_integer_packing():
    # A ×10 conversion changes the value scale, so a packed integer encoding carried
    # from the reader is no longer valid and must be dropped (the writer re-encodes).
    ds = _ds(conductivity=([4.0], "S/m"))
    ds["conductivity"].encoding.update(
        {"dtype": "int16", "scale_factor": 0.001, "add_offset": 0.0}
    )
    out = pu.to_canonical_units(ds)
    for key in ("dtype", "scale_factor", "add_offset"):
        assert key not in out["conductivity"].encoding
    assert out["conductivity"].values[0] == 40.0  # 4 S/m -> 40 mS cm-1


def test_conversion_round_trips_after_integer_encoding_dropped(tmp_path):
    # The physical values must survive write/read once the stale int packing is gone.
    from oceanarray.writers import write

    ds = _ds(conductivity=([4.0, 5.0], "S/m"))
    ds["conductivity"].encoding["dtype"] = "int16"
    out = pu.to_canonical_units(ds)
    path = tmp_path / "cond.nc"
    write(out, path)
    back = xr.open_dataset(path, engine="netcdf4", decode_timedelta=False)
    np.testing.assert_allclose(back["conductivity"].values, [40.0, 50.0])
    back.close()


def test_standard_name_map_builds_and_has_no_conflicts():
    mapping = pu._canonical_by_standard_name()  # raises if two SNs disagree
    assert mapping["sea_water_electrical_conductivity"] == "mS cm-1"
    assert mapping["eastward_sea_water_velocity"] == "m s-1"


_DUNE2_STAGE1 = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "proc"
    / "dune2_1_2026"
    / "microcat"
    / "dune2_1_2026_2941_stage1.nc"
)


def test_canonicalise_real_dune2_stage1_file():
    """Every registered variable in a committed stage-1 file comes out canonical."""
    ds = xr.open_dataset(_DUNE2_STAGE1, engine="netcdf4", decode_timedelta=False)
    out = pu.to_canonical_units(ds)  # must not raise on real data
    for var in out.variables:
        canonical = pu._canonical_units_for(var, out)
        if canonical is not None:
            assert out[var].attrs["units"] == canonical, var
    # temperature was 'degC' in the file; the pass rewrites it and marks the scale.
    assert out["temperature"].attrs["units"] == "degree_Celsius"
    assert out["temperature"].attrs["units_metadata"] == "temperature: on_scale"
    ds.close()


# ---------------------------------------------------------------------------
# refuse to guess
# ---------------------------------------------------------------------------


def test_unknown_spelling_raises_naming_the_variable():
    ds = _ds(conductivity=([40.0], "millisiemens"))
    with pytest.raises(pu.UnitsError, match="conductivity.*millisiemens.*mS cm-1"):
        pu.to_canonical_units(ds)


def test_empty_units_on_registered_variable_raises():
    ds = _ds(temperature=([1.0], ""))
    with pytest.raises(pu.UnitsError, match="temperature.*no units"):
        pu.to_canonical_units(ds)


def test_empty_units_raises_for_untabled_canonical():
    # absolute_salinity's canonical (g kg-1) has no alias group, yet an empty
    # units attribute must still raise — every registered variable is checked.
    ds = _ds(absolute_salinity=([35.0], ""))
    with pytest.raises(pu.UnitsError, match="absolute_salinity.*no units"):
        pu.to_canonical_units(ds)


# ---------------------------------------------------------------------------
# the density-basis invariant on the table itself
# ---------------------------------------------------------------------------


def test_no_alias_group_mixes_mass_and_volume_basis():
    """A per-mass and a per-volume spelling in one group would imply a scalar
    factor between kg-1 and L-1, which needs in-situ density — forbidden."""
    litre = re.compile(r"\bl-1\b|/l\b|litre|liter")
    groups = {
        **{f"aliases[{c!r}]": [c, *sp] for c, sp in ut.TABLES.aliases.items()},
        **{
            f"scoped[{sn!r}]": [ut.TABLES.canonical.get(sn, ""), *sp]
            for sn, sp in ut.TABLES.scoped.items()
        },
        **{f"kept[{sn!r}]": list(sp) for sn, sp in ut.UNIT_SPELLINGS_KEPT.items()},
    }
    for where, group in groups.items():
        has_mass = any("kg" in s.lower() for s in group)
        has_volume = any(litre.search(s.lower()) for s in group)
        assert not (has_mass and has_volume), (
            f"{where} mixes mass- and volume-basis spellings"
        )


def test_registry_units_agree_with_the_tables():
    """Every registry entry's ``units`` is the tables' canonical for its standard_name.

    The pass stores the registry's spelling, while plot labels and the accepted
    spellings come from the tables; the two must name the same canonical or storage and
    display would disagree.
    """
    for var, entry in params.VARIABLES.items():
        sn = entry.get("standard_name")
        if sn in ut.TABLES.canonical:
            assert entry["units"] == ut.TABLES.canonical[sn], (
                f"{var}: registry units {entry['units']!r} != tables canonical "
                f"{ut.TABLES.canonical[sn]!r} for {sn!r}"
            )


# ---------------------------------------------------------------------------
# unit_label — derivation and the no-drift guarantee
# ---------------------------------------------------------------------------


def test_every_registry_var_gets_a_display_label():
    """Every registered variable yields an axis label with a non-empty unit.

    Replaces the old ``label_units`` drift guard (the column is gone): :func:`vlabel`
    derives the unit from the canonical ``units`` through :func:`unit_label`, so the
    check is that every variable produces ``"Name (unit)"`` with a real unit — or the
    CF dimensionless ``"(1)"`` — never a bare name.
    """
    for var in params.VARIABLES:
        label = params.vlabel(var)
        assert "(" in label and label.endswith(")"), f"{var}: no unit in {label!r}"
        unit = label[label.rfind("(") + 1 : -1]
        assert unit, f"{var}: empty unit in vlabel {label!r}"


def test_warn_if_not_canonical_silent_on_canonical_and_aliases():
    """The derived-quantity guard stays silent for the unit the derivation assumes.

    A canonical unit, an accepted alias (factor 1), and a variable with no ``units``
    attribute must not warn — else every oceanarray report (all canonical) would be
    noisy.
    """
    for units, expected in [("m s-1", "m s-1"), ("degrees", "degree"), (None, "dbar")]:
        attrs = {"units": units} if units is not None else {}
        da = xr.DataArray([1.0], attrs=attrs)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            ut.warn_if_not_canonical(da, expected, quantity="x")  # must not raise


def test_warn_if_not_canonical_warns_on_real_mismatch():
    """A source unit that is not the expected canonical (nor an alias) warns loudly."""
    da = xr.DataArray([1.0], attrs={"units": "furlong"})
    with pytest.warns(UserWarning, match="may be wrong"):
        ut.warn_if_not_canonical(da, "dbar", quantity="instrument spacing (dbar)")


def test_warn_if_not_canonical_rejects_cross_quantity_standard_name():
    """A standard_name from a different quantity must not silence the guard.

    ``units='deg'`` with ``standard_name='latitude'`` resolves to ``('dbar', 1.0)``
    through latitude-scoped aliases; the guard ignores a standard_name whose canonical
    is not the expected quantity, so a foreign/mislabelled variable still warns.
    """
    da = xr.DataArray([1.0], attrs={"units": "deg", "standard_name": "latitude"})
    with pytest.warns(UserWarning, match="may be wrong"):
        ut.warn_if_not_canonical(da, "dbar", quantity="instrument spacing (dbar)")


def test_oceanarray_unit_aliases_reach_vunit():
    """oceanarray's unit_tables customisation reaches the display entry points.

    The readers write angles as ``degrees`` and voltage as ``volts``; UNIT_ALIAS_EXTRAS
    maps them to the CF canonicals (``degree`` / ``V``), which the family rule labels
    ``°`` / ``V``.  Confirm both the registry path (:func:`vunit`, via the canonical) and
    the raw reader spelling (:func:`unit_label`) land on the symbol.
    """
    assert params.vunit("pitch") == "°"
    assert params.vunit("bin_pressure") == "dbar"
    assert ut.unit_label("degrees") == "°"
    assert ut.unit_label("volts") == "V"


def test_unit_label_specials():
    assert ut.unit_label("degree_Celsius") == "°C"
    assert ut.unit_label("mS cm-1") == "mS/cm"  # slash form
    assert ut.unit_label("kg m-3") == "kg/m³"  # exponent in denominator
    assert ut.unit_label("umol kg-1") == "µmol/kg"
    assert ut.unit_label("s-2") == "s⁻²"  # bare negative exponent, no numerator
    assert ut.unit_label("1", "sea_water_practical_salinity") == "PSU"
    assert ut.unit_label("1", "sea_water_turbidity") == "NTU"
    assert ut.unit_label("") == ""


@pytest.mark.parametrize("spelling", ["s-2", "rad2 s-2"])
def test_n2_spelling_is_kept_as_found(spelling):
    """N² is not forced between ``s-2`` and ``rad2 s-2``; each stays as it arrived."""
    ds = _ds(n2=([1e-5], spelling, "square_of_brunt_vaisala_frequency_in_sea_water"))
    out = pu.to_canonical_units(ds)
    assert out["n2"].attrs["units"] == spelling
    assert "units_converted_from" not in out["n2"].attrs


def test_n2_leaked_display_label_is_still_rewritten():
    ds = _ds(n2=([1e-5], "s⁻²"))
    assert pu.to_canonical_units(ds)["n2"].attrs["units"] == "s-2"


def test_registered_variable_scope_comes_from_the_registry():
    """A reader's standard_name does not change which scoped spellings apply."""
    ds = _ds(salinity=([35.0], "PSU", "sea_water_salinity"))
    assert pu.to_canonical_units(ds)["salinity"].attrs["units"] == "1"
    with pytest.raises(pu.UnitsError):
        pu.to_canonical_units(
            _ds(turbidity=([5.0], "PSU", "sea_water_practical_salinity"))
        )


def test_missing_units_message_points_at_the_reader():
    with pytest.raises(pu.UnitsError, match="reader dropped"):
        pu.to_canonical_units(_ds(temperature=([10.0], "")))


def test_units_layer_imports_no_plotting_or_templating():
    """The storage pass reaches into the vendored layer for its tables only."""
    code = (
        "import sys, oceanarray.pipeline.units; "
        "bad = [m for m in ('matplotlib', 'jinja2') if m in sys.modules]; "
        "print(','.join(bad))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "", f"pipeline.units imports {out.stdout.strip()}"


def test_degree_symbol_accepted_and_bare_deg_refused():
    """The storage pass accepts the ``°`` symbol as ``degree`` for an attitude var; ``deg`` is not.

    ``deg`` is scoped to latitude/longitude in the vendored tables, so it is deliberately
    not a package-level ``degree`` alias — a registered attitude variable carrying ``deg``
    is refused rather than silently shadowing the lat/lon scoping.
    """
    good = _ds(pitch=([1.0, 2.0], "°", "platform_pitch_angle"))
    assert pu.to_canonical_units(good)["pitch"].attrs["units"] == "degree"
    bad = _ds(pitch=([1.0, 2.0], "deg", "platform_pitch_angle"))
    with pytest.raises(pu.UnitsError):
        pu.to_canonical_units(bad)
