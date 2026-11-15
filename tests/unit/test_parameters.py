"""Tests for oceanarray.config.parameters: VARIABLES registry and vlabel()."""

import pytest

from oceanarray.config import parameters as params


class TestVariablesRegistry:
    def test_variables_is_dict(self):
        assert isinstance(params.VARIABLES, dict)

    def test_all_entries_have_required_keys(self):
        required = {"label", "units", "standard_name", "cmap"}
        for var, entry in params.VARIABLES.items():
            missing = required - entry.keys()
            assert not missing, f"{var!r} missing keys: {missing}"

    def test_all_entries_have_label(self):
        for var, entry in params.VARIABLES.items():
            assert isinstance(entry["label"], str) and entry["label"], (
                f"{var!r} empty label"
            )

    def test_vunit_derives_a_display_unit(self):
        """vunit formats the canonical units through the display pipeline, never bare.

        The ``label_units`` column is gone; the display unit is derived from ``units``
        via :func:`vunit`.  Every registered variable with a non-empty ``units`` yields
        a non-empty display unit that does not carry the variable's label text.
        """
        for var, entry in params.VARIABLES.items():
            if not entry.get("units"):
                continue
            u = params.vunit(var)
            assert isinstance(u, str) and u, f"{var!r} empty vunit"
            assert entry["label"] not in u, (
                f"{var!r} vunit {u!r} contains the label; units only"
            )

    def test_units_field_is_ascii(self):
        for var, entry in params.VARIABLES.items():
            u = entry["units"]
            assert isinstance(u, str), f"{var!r} units is not a str"
            assert u.isascii(), (
                f"{var!r} units {u!r} contains non-ASCII; use udunits-2 form"
            )

    def test_standard_name_is_str_or_none(self):
        for var, entry in params.VARIABLES.items():
            sn = entry["standard_name"]
            assert sn is None or isinstance(sn, str), (
                f"{var!r} standard_name wrong type"
            )

    def test_cmap_is_str_or_none(self):
        for var, entry in params.VARIABLES.items():
            cm = entry["cmap"]
            assert cm is None or isinstance(cm, str), f"{var!r} cmap wrong type"

    @pytest.mark.parametrize(
        "var",
        [
            "temperature",
            "salinity",
            "pressure",
            "depth",
            "conservative_temperature",
            "absolute_salinity",
            "east_velocity",
            "north_velocity",
            "up_velocity",
        ],
    )
    def test_core_variables_present(self, var):
        assert var in params.VARIABLES

    @pytest.mark.parametrize(
        "var,vmin,vmax",
        [
            ("temperature", -5.0, 42.0),
            ("conservative_temperature", -5.0, 42.0),
            ("conductivity", 0.0, 80.0),
            ("pressure", 0.0, 11000.0),
            ("depth", 0.0, 12000.0),
        ],
    )
    def test_os1_valid_range_populated(self, var, vmin, vmax):
        entry = params.VARIABLES[var]
        assert entry["valid_min"] == vmin, f"{var!r} valid_min mismatch"
        assert entry["valid_max"] == vmax, f"{var!r} valid_max mismatch"

    def test_cmaps_by_variable_excludes_none(self):
        assert all(v is not None for v in params.CMAPS_BY_VARIABLE.values())

    def test_cmaps_by_variable_subset_of_variables(self):
        assert params.CMAPS_BY_VARIABLE.keys() <= params.VARIABLES.keys()


class TestVlabel:
    def test_known_variable_returns_label(self):
        assert params.vlabel("temperature") == "Temperature (°C)"

    def test_known_variable_with_prefix(self):
        assert params.vlabel("pressure", prefix="Gridded ") == "Gridded Pressure (dbar)"

    def test_unknown_variable_returns_varname(self):
        assert params.vlabel("nonexistent_variable") == "nonexistent_variable"

    def test_unknown_variable_with_prefix(self):
        assert params.vlabel("foo", prefix="My ") == "My foo"

    def test_empty_prefix_is_default(self):
        assert params.vlabel("salinity") == params.vlabel("salinity", prefix="")

    def test_prefix_applies_to_label_not_units(self):
        result = params.vlabel("temperature", prefix="Δ")
        assert result == "ΔTemperature (°C)"
        assert result.startswith("ΔT")  # prefix before label, not before "("

    def test_conservative_temperature_label(self):
        assert (
            params.vlabel("conservative_temperature") == "Conservative temperature (°C)"
        )

    def test_absolute_salinity_label(self):
        assert params.vlabel("absolute_salinity") == "Absolute salinity (g/kg)"

    def test_return_type_is_str(self):
        assert isinstance(params.vlabel("temperature"), str)
        assert isinstance(params.vlabel("unknown_key"), str)


class TestIsKnownInstrumentType:
    def test_known_lower_case_type(self):
        assert params.is_known_instrument_type("microcat")

    def test_adcp_matches_either_case(self):
        # The YAML spelling is upper-case; matching ignores case so neither warns.
        assert params.is_known_instrument_type("ADCP")
        assert params.is_known_instrument_type("adcp")

    def test_other_type_matches_regardless_of_case(self):
        assert params.is_known_instrument_type("AQUADOPP")

    def test_thermistor_is_known(self):
        # OSNAP WHOI/NIOZ thermistors, delivered as OceanSITES "MT" files.
        assert params.is_known_instrument_type("thermistor")
        assert params.INSTRUMENT_FILE_TYPES["thermistor"] == ["oceansites"]
        assert params.INSTRUMENT_FAMILIES["thermistor"] == "hydrography"

    def test_unknown_type_is_rejected(self):
        assert not params.is_known_instrument_type("xbt")
