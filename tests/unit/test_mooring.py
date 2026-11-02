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

    from oceanarray.pipeline._run import DEFAULTS, _settings

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
    assert _settings(doc) == {**DEFAULTS["stack"], **DEFAULTS["grid"]}
    assert not [
        i for i in mooring.check(doc) if "stack" in i.message or "grid" in i.message
    ]
