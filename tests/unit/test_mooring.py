"""Tests for :mod:`oceanarray.mooring` — the mooring description loader."""

from pathlib import Path

import pytest

from oceanarray import mooring
from oceanarray.exceptions import MooringError

FIXTURE = Path("tests/fixtures/proc/dune2_1_2026/dune2_1_2026.mooring.yaml")


def test_load_keeps_every_key() -> None:
    """``load`` returns the parsed mapping with every key as written."""
    data = mooring.load(FIXTURE)
    assert data["name"] == "dune2_1_2026"
    assert data["waterdepth"] == 745
    assert data["latitude"] == "65 43.913"  # kept as the raw string, not parsed
    assert "clamp" in data


def test_load_from_mapping_round_trips() -> None:
    """A mapping passed to ``load`` comes back equal (shallow copy, keys kept)."""
    data = mooring.load(FIXTURE)
    again = mooring.load(dict(data))
    assert again == data
    assert again is not data


def test_load_by_name_and_proc_dir() -> None:
    """A bare mooring name resolves against ``proc_dir``."""
    data = mooring.load("dune2_1_2026", proc_dir="tests/fixtures/proc")
    assert data["name"] == "dune2_1_2026"


def test_load_missing_file_raises() -> None:
    """A non-existent config raises ``MooringError`` naming the path."""
    with pytest.raises(MooringError, match="not found"):
        mooring.load("tests/fixtures/proc/nope/nope.mooring.yaml")


def test_load_malformed_yaml_raises(tmp_path: Path) -> None:
    """A syntactically invalid YAML file raises ``MooringError``, not a raw YAMLError."""
    bad = tmp_path / "broken.mooring.yaml"
    bad.write_text("invalid: yaml: content: [")
    with pytest.raises(MooringError, match="parse error"):
        mooring.load(bad)


def test_load_missing_required_key_raises_naming_it() -> None:
    """A missing required key raises ``MooringError`` and names the key."""
    bad = {"name": "x", "deployment_time": "2026-01-01T00:00"}
    # 'waterdepth' is required and absent.
    with pytest.raises(MooringError, match="waterdepth") as exc:
        mooring.load(bad)
    assert any(i.level == "ERROR" for i in exc.value.issues)


def test_check_returns_error_without_raising() -> None:
    """``check`` reports the ERROR for the same config and does not raise."""
    bad = {"name": "x", "deployment_time": "2026-01-01T00:00"}
    issues = mooring.check(bad)
    errors = [i for i in issues if i.level == "ERROR"]
    assert any("waterdepth" in i.message for i in errors)


def _export_warnings(**keys: object) -> list[str]:
    """Return the check messages that name an OceanSITES export key, for the fixture plus *keys*."""
    cfg = {**mooring.load(FIXTURE), **keys}
    return [
        i.message
        for i in mooring.check(cfg, dir_name="dune2_1_2026")
        if "platform_code" in i.message or "deployment_code" in i.message
    ]


def test_export_codes_that_fit_pass_quietly() -> None:
    """A platform code without '_' and the deployment month as YYYYMM raise nothing."""
    assert _export_warnings(platform_code="DUNE2", deployment_code=202607) == []


@pytest.mark.parametrize("code", ["DUNE2_1", 12])
def test_platform_code_with_separator_or_not_text_warns(code: object) -> None:
    """'_' is the OceanSITES file-name separator, so a platform code may not hold one."""
    assert any("platform_code" in m for m in _export_warnings(platform_code=code))


@pytest.mark.parametrize(
    ("code", "fragment"),
    [
        ("2026-07", "not YYYYMM"),
        (202600, "not YYYYMM"),
        (202613, "not YYYYMM"),
        (202608, "does not match"),
    ],
)
def test_deployment_code_off_pattern_or_month_warns(
    code: object, fragment: str
) -> None:
    """The deployment code is YYYYMM and agrees with deployment_time (2026-07-11)."""
    assert any(fragment in m for m in _export_warnings(deployment_code=code))


def test_missing_deployment_window_warns_not_errors() -> None:
    """An absent deployment/recovery time is a WARNING, so the config still loads."""
    cfg = {"name": "x", "waterdepth": 100}
    issues = mooring.check(cfg)
    assert not [i for i in issues if i.level == "ERROR"]
    assert any("deployment_time" in i.message for i in issues)
    assert mooring.load(cfg)["waterdepth"] == 100


def test_unknown_key_warns() -> None:
    """An unrecognised top-level key is a WARNING, not an ERROR."""
    cfg = {
        "name": "x",
        "waterdepth": 100,
        "deployment_time": "2026-01-01T00:00",
        "recovery_time": "2026-02-01T00:00",
        "wibble": 3,
    }
    issues = mooring.check(cfg)
    warnings = [i for i in issues if i.level == "WARNING"]
    assert any("wibble" in i.message for i in warnings)
    assert not [i for i in issues if i.level == "ERROR"]
    # A WARNING-only config still loads.
    assert mooring.load(cfg)["wibble"] == 3


def test_template_and_comment_keys_are_known() -> None:
    """The template and its field comments stay within ``KNOWN_KEYS`` (no drift)."""
    from oceanarray.mooring import _FIELD_COMMENTS, KNOWN_KEYS

    assert set(_FIELD_COMMENTS) <= KNOWN_KEYS
    assert set(mooring.template("dsX_1_2026")) <= KNOWN_KEYS


def test_template_has_required_keys_and_one_block_per_type() -> None:
    """``template`` returns the skeleton: name set, required keys present, 4 blocks."""
    doc = mooring.template("dsX_1_2026")
    assert doc["name"] == "dsX_1_2026"
    assert "waterdepth" in doc and doc["waterdepth"] is None
    assert [e["instrument"] for e in doc["clamp"]] == [
        "aquadopp",
        "microcat",
        "rbrsolo",
        "ADCP",
    ]


def test_save_matches_init_golden(tmp_path: Path) -> None:
    """``save(template(...))`` reproduces the hand-written ``init`` stub byte-for-byte."""
    golden = Path("tests/fixtures/init_template_golden.yaml").read_text()
    out = mooring.save(mooring.template("dsX_1_2026"), tmp_path / "x.mooring.yaml")
    assert out.read_text() == golden


def test_save_refuses_existing_without_overwrite(tmp_path: Path) -> None:
    """``save`` will not clobber an existing file unless ``overwrite=True``."""
    p = tmp_path / "x.mooring.yaml"
    mooring.save(mooring.template("dsX_1_2026"), p)
    with pytest.raises(FileExistsError):
        mooring.save(mooring.template("dsX_1_2026"), p)
    mooring.save(mooring.template("dsX_1_2026"), p, overwrite=True)  # no raise


def test_save_is_lenient_on_incomplete_draft(tmp_path: Path) -> None:
    """``save`` writes a draft whose required instrument serials are still null."""
    # The template's serials are None — an ERROR for load, a warning for save.
    p = mooring.save(mooring.template("dsX_1_2026"), tmp_path / "x.mooring.yaml")
    assert p.exists()


def test_init_writes_the_optional_blocks_commented_out(tmp_path: Path) -> None:
    """The stub carries stack:/grid: commented out; uncommented, they are valid and the defaults."""
    import yaml

    from oceanarray.pipeline._run import DEFAULTS, _resolve_settings

    out = mooring.save(
        mooring.template("dsX_1_2026"),
        tmp_path / "x.mooring.yaml",
        optional_blocks=True,
    )
    text = out.read_text()
    assert yaml.safe_load(text).keys().isdisjoint({"stack", "grid"})
    lines = text.splitlines()
    start = lines.index("# stack:")
    uncommented = [
        ln[2:] if i in range(start, start + 6) else ln for i, ln in enumerate(lines)
    ]
    doc = yaml.safe_load("\n".join(uncommented))
    assert doc["stack"] == DEFAULTS["stack"]
    assert doc["grid"] == DEFAULTS["grid"]
    assert _resolve_settings(doc) == {**DEFAULTS["stack"], **DEFAULTS["grid"]}
    assert not [
        i for i in mooring.check(doc) if "stack" in i.message or "grid" in i.message
    ]


def _qc_issues(**keys: object) -> list[tuple[str, str]]:
    """Return (level, message) for the QC-override findings on the fixture plus *keys*."""
    cfg = {**mooring.load(FIXTURE), **keys}
    return [
        (i.level, i.message)
        for i in mooring.check(cfg, dir_name="dune2_1_2026")
        if any(
            b in i.message for b in ("qc_ranges", "qc_spike", "tilt_qc", "qc_flat_line")
        )
    ]


def test_qc_overrides_as_documented_pass() -> None:
    """The documented qc_ranges example and well-formed spike/tilt/flat-line blocks raise nothing."""
    assert (
        _qc_issues(
            qc_ranges={
                "temperature": {
                    "fail_span": [-2.0, 35.0],
                    "suspect_span": [-1.5, 30.0],
                },
                "pitch": {"fail_span": [-30.0, 30.0], "suspect_span": [-20.0, 20.0]},
                "tilt": {"suspect_span": [-20, 20], "fail_span": [-30, 30]},
            },
            qc_spike={"pressure": {"suspect_threshold": 10.0, "fail_threshold": 50.0}},
            tilt_qc={"suspect_threshold": 20, "fail_threshold": 30},
            qc_flat_line={
                "pressure": {"suspect_n": 3, "fail_n": 10, "tolerance": 0.001}
            },
        )
        == []
    )


@pytest.mark.parametrize(
    ("keys", "fragment"),
    [
        (
            {"qc_ranges": {"temperature": {"suspect_spn": [0, 1]}}},
            "unknown field 'suspect_spn'",
        ),
        ({"qc_ranges": {"salinity": {"fail_span": [40, 0]}}}, "two increasing numbers"),
        (
            {"qc_ranges": {"salinity": {"fail_span": [0, 40, 1]}}},
            "two increasing numbers",
        ),
        ({"qc_spike": {"pressure": {"fail_threshold": "ten"}}}, "must be a number"),
        ({"qc_flat_line": {"pressure": {"fail_n": 2.5}}}, "whole number"),
        ({"tilt_qc": {"fail": 30}}, "unknown field 'fail'"),
        ({"tilt_qc": [20, 30]}, "must be a mapping"),
        ({"qc_ranges": {"sigma0": {"suspect_span": [20, 30]}}}, "needs a fail_span"),
    ],
)
def test_qc_override_shape_mistakes_are_errors(keys: dict, fragment: str) -> None:
    """A misspelt field or malformed value is an ERROR, so load() refuses the description."""
    issues = _qc_issues(**keys)
    assert any(level == "ERROR" and fragment in msg for level, msg in issues), issues
    with pytest.raises(MooringError):
        mooring.load({**mooring.load(FIXTURE), **keys})


def test_qc_override_unknown_variable_only_warns() -> None:
    """A variable outside the registry is a WARNING (likely a typo, but allowed)."""
    issues = _qc_issues(qc_spike={"temprature": {"fail_threshold": 5.0}})
    assert [level for level, _ in issues] == ["WARNING"]


def test_instrument_level_qc_overrides_are_checked() -> None:
    """The same checks run on each instrument entry, naming its serial."""
    cfg = mooring.load(FIXTURE)
    cfg["clamp"][0] = {
        **cfg["clamp"][0],
        "qc_ranges": {"temperature": {"fail_spn": [0, 1]}},
    }
    serial = cfg["clamp"][0]["serial"]
    messages = [i.message for i in mooring.check(cfg, dir_name="dune2_1_2026")]
    assert any(f"instrument {serial}: qc_ranges.temperature" in m for m in messages)


def test_instrument_qc_ranges_inherit_the_mooring_fail_span() -> None:
    """A fail_span set at mooring level counts for an instrument that adds only a suspect_span."""
    cfg = mooring.load(FIXTURE)
    cfg["qc_ranges"] = {"sigma0": {"fail_span": [20.0, 30.0]}}
    cfg["clamp"][0] = {
        **cfg["clamp"][0],
        "qc_ranges": {"sigma0": {"suspect_span": [25.0, 28.0]}},
    }
    messages = [i.message for i in mooring.check(cfg, dir_name="dune2_1_2026")]
    assert not [m for m in messages if "needs a fail_span" in m], messages
    del cfg["qc_ranges"]
    messages = [i.message for i in mooring.check(cfg, dir_name="dune2_1_2026")]
    assert any("needs a fail_span" in m for m in messages)


def test_mooring_level_qc_blocks_are_known_keys() -> None:
    """qc_spike, tilt_qc and qc_flat_line are read by the QC pass, so not 'unknown'."""
    cfg = {
        **mooring.load(FIXTURE),
        "qc_spike": {},
        "tilt_qc": {},
        "qc_flat_line": {},
    }
    assert not [
        i for i in mooring.check(cfg, dir_name="dune2_1_2026") if "Unknown" in i.message
    ]
