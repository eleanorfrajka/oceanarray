"""Tests for the pipeline registry and public process() API."""

import dataclasses

import pytest

from oceanarray.pipeline import STAGES, Stage, resolve_stage
from oceanarray.pipeline import _process_legacy as process


class TestStagesRegistry:
    def test_has_five_stages(self):
        assert len(STAGES) == 5

    def test_stage_names(self):
        names = [s.name for s in STAGES]
        assert names == ["stage1", "stage2", "stage3", "stack", "grid"]

    def test_numbered_stages(self):
        numbered = [(s.name, s.number) for s in STAGES if s.number is not None]
        assert numbered == [("stage1", 1), ("stage2", 2), ("stage3", 3)]

    def test_unnumbered_stages(self):
        unnumbered = [s.name for s in STAGES if s.number is None]
        assert unnumbered == ["stack", "grid"]

    def test_scopes(self):
        scopes = {s.name: s.scope for s in STAGES}
        assert scopes["stage1"] == "instrument"
        assert scopes["stage2"] == "instrument"
        assert scopes["stage3"] == "instrument"
        assert scopes["stack"] == "mooring"
        assert scopes["grid"] == "mooring"

    def test_all_stages_have_callable_run(self):
        for s in STAGES:
            assert callable(s.run), f"{s.name}.run is not callable"

    def test_stage_is_frozen(self):
        s = STAGES[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.name = "changed"  # type: ignore[misc]


class TestResolveStage:
    @pytest.mark.parametrize("stage", [1, 2, 3])
    def test_resolve_by_int(self, stage):
        s = resolve_stage(stage)
        assert s.number == stage

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("stage1", "stage1"),
            ("stage2", "stage2"),
            ("stage3", "stage3"),
            ("stack", "stack"),
            ("grid", "grid"),
        ],
    )
    def test_resolve_by_name(self, name, expected):
        assert resolve_stage(name).name == expected

    @pytest.mark.parametrize("name", ["STAGE1", "Stage2", "GRID", "Stack"])
    def test_resolve_case_insensitive(self, name):
        s = resolve_stage(name)
        assert s.name == name.lower()

    def test_integer_4_raises(self):
        with pytest.raises(ValueError, match="unknown stage"):
            resolve_stage(4)

    def test_integer_0_raises(self):
        with pytest.raises(ValueError, match="unknown stage"):
            resolve_stage(0)

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="unknown stage"):
            resolve_stage("bogus")

    def test_error_message_lists_valid_values(self):
        with pytest.raises(ValueError) as exc_info:
            resolve_stage(99)
        msg = str(exc_info.value)
        assert "1" in msg
        assert "grid" in msg

    def test_none_raises(self):
        # None == None matches stack/grid (number=None) without the guard.
        with pytest.raises(ValueError, match="unknown stage"):
            resolve_stage(None)  # type: ignore[arg-type]

    def test_true_raises(self):
        # True == 1 in Python; without the guard, True returns stage1.
        with pytest.raises(ValueError, match="unknown stage"):
            resolve_stage(True)  # type: ignore[arg-type]

    def test_false_raises(self):
        # False == 0 in Python; 0 is not a valid stage number.
        with pytest.raises(ValueError, match="unknown stage"):
            resolve_stage(False)  # type: ignore[arg-type]


def _make_spy(call_list: list, name: str, *, ok: bool = True):
    """Return a Stage.run-compatible callable that appends *name* to *call_list*."""

    def run(_mooring, _proc_dir, **_kw):
        call_list.append(name)
        return ok

    return run


def _patch_stages(monkeypatch, stages):
    from oceanarray import pipeline

    monkeypatch.setattr(pipeline, "STAGES", stages)


class TestProcess:
    def test_process_single_stage_by_int(self, tmp_path, monkeypatch):
        calls = []
        orig = STAGES
        _patch_stages(
            monkeypatch,
            (
                orig[0],
                Stage("stage2", 2, "instrument", _make_spy(calls, "stage2")),
                orig[2],
                orig[3],
                orig[4],
            ),
        )
        result = process("test_mooring", stage=2, proc_dir=tmp_path)
        assert result is True
        assert calls == ["stage2"]

    def test_process_all_stages_calls_each_in_order(self, tmp_path, monkeypatch):
        call_order = []
        patched = tuple(
            Stage(s.name, s.number, s.scope, _make_spy(call_order, s.name))
            for s in STAGES
        )
        _patch_stages(monkeypatch, patched)

        result = process("my_mooring", proc_dir=tmp_path)
        assert result is True
        assert call_order == ["stage1", "stage2", "stage3", "stack", "grid"]

    def test_process_returns_false_if_any_stage_fails(self, tmp_path, monkeypatch):
        calls = []
        patched = tuple(
            Stage(
                s.name,
                s.number,
                s.scope,
                _make_spy(calls, s.name, ok=(s.name != "stage2")),
            )
            for s in STAGES
        )
        _patch_stages(monkeypatch, patched)

        result = process("my_mooring", proc_dir=tmp_path)
        assert result is False

    def test_process_stage1_requires_raw_dir_via_registry(self, tmp_path):
        """stage1 wrapper raises ValueError when raw_dir is not provided."""
        from oceanarray.pipeline import _run_stage1

        with pytest.raises(ValueError, match="raw_dir"):
            _run_stage1("test_mooring", tmp_path, raw_dir=None, force=False)

    def test_instrument_stage_failure_does_not_skip_later_stages(
        self, tmp_path, monkeypatch
    ):
        """Non-stack failure continues — only stack failure gates grid."""
        call_order = []
        patched = (
            Stage("stage1", 1, "instrument", _make_spy(call_order, "stage1", ok=False)),
            Stage("stage2", 2, "instrument", _make_spy(call_order, "stage2")),
            Stage("stage3", 3, "instrument", _make_spy(call_order, "stage3")),
            Stage("stack", None, "mooring", _make_spy(call_order, "stack")),
            Stage("grid", None, "mooring", _make_spy(call_order, "grid")),
        )
        _patch_stages(monkeypatch, patched)

        result = process("my_mooring", proc_dir=tmp_path)
        assert result is False
        assert call_order == ["stage1", "stage2", "stage3", "stack", "grid"]

    def test_stack_failure_skips_grid(self, tmp_path, monkeypatch):
        """Grid is skipped when stack fails — stack.nc is the input to grid."""
        call_order = []
        patched = (
            Stage("stage1", 1, "instrument", _make_spy(call_order, "stage1")),
            Stage("stage2", 2, "instrument", _make_spy(call_order, "stage2")),
            Stage("stage3", 3, "instrument", _make_spy(call_order, "stage3")),
            Stage("stack", None, "mooring", _make_spy(call_order, "stack", ok=False)),
            Stage("grid", None, "mooring", _make_spy(call_order, "grid")),
        )
        _patch_stages(monkeypatch, patched)

        result = process("my_mooring", proc_dir=tmp_path)
        assert result is False
        assert "grid" not in call_order
        assert call_order == ["stage1", "stage2", "stage3", "stack"]

    def test_process_by_stage_name(self, tmp_path, monkeypatch):
        calls = []
        patched = (
            *STAGES[:3],
            Stage("stack", None, "mooring", _make_spy(calls, "stack")),
            STAGES[4],
        )
        _patch_stages(monkeypatch, patched)

        result = process("my_mooring", stage="stack", proc_dir=tmp_path)
        assert result is True
        assert calls == ["stack"]

    def test_process_with_list(self, tmp_path, monkeypatch):
        """stage=[1, 2] runs only stages 1 and 2."""
        calls = []
        patched = tuple(
            Stage(s.name, s.number, s.scope, _make_spy(calls, s.name)) for s in STAGES
        )
        _patch_stages(monkeypatch, patched)

        result = process("my_mooring", stage=[1, 2], proc_dir=tmp_path)
        assert result is True
        assert calls == ["stage1", "stage2"]

    def test_process_with_list_of_names(self, tmp_path, monkeypatch):
        """stage=['stack', 'grid'] runs only stack and grid."""
        calls = []
        patched = tuple(
            Stage(s.name, s.number, s.scope, _make_spy(calls, s.name)) for s in STAGES
        )
        _patch_stages(monkeypatch, patched)

        result = process("my_mooring", stage=["stack", "grid"], proc_dir=tmp_path)
        assert result is True
        assert calls == ["stack", "grid"]

    def test_process_list_preserves_stages_order(self, tmp_path, monkeypatch):
        """Input order is ignored — stages always run in STAGES order."""
        calls = []
        patched = tuple(
            Stage(s.name, s.number, s.scope, _make_spy(calls, s.name)) for s in STAGES
        )
        _patch_stages(monkeypatch, patched)

        result = process("my_mooring", stage=[2, 1], proc_dir=tmp_path)
        assert result is True
        assert calls == ["stage1", "stage2"]

    def test_process_empty_list_runs_nothing(self, tmp_path, monkeypatch):
        """stage=[] runs no stages and returns True."""
        calls = []
        patched = tuple(
            Stage(s.name, s.number, s.scope, _make_spy(calls, s.name)) for s in STAGES
        )
        _patch_stages(monkeypatch, patched)

        result = process("my_mooring", stage=[], proc_dir=tmp_path)
        assert result is True
        assert calls == []

    def test_process_list_stack_failure_skips_grid(self, tmp_path, monkeypatch):
        """stack failure in a list run still skips grid."""
        calls = []
        patched = (
            Stage("stage1", 1, "instrument", _make_spy(calls, "stage1")),
            Stage("stage2", 2, "instrument", _make_spy(calls, "stage2")),
            Stage("stage3", 3, "instrument", _make_spy(calls, "stage3")),
            Stage("stack", None, "mooring", _make_spy(calls, "stack", ok=False)),
            Stage("grid", None, "mooring", _make_spy(calls, "grid")),
        )
        _patch_stages(monkeypatch, patched)

        result = process(
            "my_mooring", stage=[1, 2, 3, "stack", "grid"], proc_dir=tmp_path
        )
        assert result is False
        assert "grid" not in calls
        assert calls == ["stage1", "stage2", "stage3", "stack"]


class TestRunWrappersNoSeasenselib:
    """Wrapper bodies whose stage module does not import seasenselib.

    These exercise the lazy import + processor instantiation + call inside
    ``_run_stage3`` and ``_run_stack`` — the code the ``_patch_stages`` tests
    above deliberately bypass — and run in CI (no seasenselib needed).
    """

    def test_run_stage3_instantiates_and_forwards(self, tmp_path, monkeypatch):
        from unittest import mock

        from oceanarray.pipeline import _run_stage3

        inst = mock.MagicMock()
        inst.process_mooring.return_value = True
        cls = mock.MagicMock(return_value=inst)
        monkeypatch.setattr("oceanarray.pipeline.stage3.Stage3Processor", cls)

        assert (
            _run_stage3("m", tmp_path, serials=["9"], force=True, dry_run=True) is True
        )
        cls.assert_called_once_with(proc_dir=str(tmp_path))
        inst.process_mooring.assert_called_once_with(
            "m", serials=["9"], force=True, dry_run=True
        )

    def test_run_stage3_dry_run_defaults_false(self, tmp_path, monkeypatch):
        from unittest import mock

        from oceanarray.pipeline import _run_stage3

        inst = mock.MagicMock()
        inst.process_mooring.return_value = True
        cls = mock.MagicMock(return_value=inst)
        monkeypatch.setattr("oceanarray.pipeline.stage3.Stage3Processor", cls)

        _run_stage3("m", tmp_path)
        inst.process_mooring.assert_called_once_with(
            "m", serials=None, force=False, dry_run=False
        )

    def test_run_stack_instantiates_and_forwards(self, tmp_path, monkeypatch):
        from unittest import mock

        from oceanarray.pipeline import _run_stack

        inst = mock.MagicMock()
        inst.stack.return_value = True
        cls = mock.MagicMock(return_value=inst)
        monkeypatch.setattr("oceanarray.pipeline.stack.MooringStacker", cls)

        assert _run_stack("m", tmp_path, dt_seconds=120, force=True) is True
        cls.assert_called_once_with(proc_dir=str(tmp_path))
        inst.stack.assert_called_once_with("m", dt_seconds=120, force=True)


@pytest.mark.needs_seasenselib
class TestRunWrappersSeasenselib:
    """Wrapper bodies whose stage module imports seasenselib (local only).

    stage1/stage2/grid modules ``import seasenselib`` at module top, so these
    are marked and additionally guarded with ``importorskip`` to skip cleanly
    when seasenselib is absent (e.g. the CI unit job).
    """

    def test_run_stage1_instantiates_and_forwards(self, tmp_path, monkeypatch):
        pytest.importorskip("seasenselib")
        from unittest import mock

        from oceanarray.pipeline import _run_stage1

        inst = mock.MagicMock()
        inst.process_mooring.return_value = True
        cls = mock.MagicMock(return_value=inst)
        monkeypatch.setattr("oceanarray.pipeline.stage1.MooringProcessor", cls)

        raw = tmp_path / "raw"
        out = _run_stage1("m", tmp_path, raw_dir=raw, serials=["1"], force=False)
        assert out is True
        cls.assert_called_once_with(raw_dir=str(raw), proc_dir=str(tmp_path))
        inst.process_mooring.assert_called_once_with("m", serials=["1"], force=False)

    def test_run_stage2_instantiates_and_forwards(self, tmp_path, monkeypatch):
        pytest.importorskip("seasenselib")
        from unittest import mock

        from oceanarray.pipeline import _run_stage2

        inst = mock.MagicMock()
        inst.process_mooring.return_value = True
        cls = mock.MagicMock(return_value=inst)
        monkeypatch.setattr("oceanarray.pipeline.stage2.Stage2Processor", cls)

        assert _run_stage2("m", tmp_path, serials=["2"], force=True) is True
        cls.assert_called_once_with(proc_dir=str(tmp_path))
        inst.process_mooring.assert_called_once_with("m", serials=["2"], force=True)

    def test_run_grid_instantiates_and_forwards(self, tmp_path, monkeypatch):
        pytest.importorskip("seasenselib")
        from unittest import mock

        from oceanarray.pipeline import _run_grid

        inst = mock.MagicMock()
        inst.grid.return_value = True
        cls = mock.MagicMock(return_value=inst)
        monkeypatch.setattr("oceanarray.pipeline.grid.MooringGridder", cls)

        out = _run_grid("m", tmp_path, p_start=100.0, p_end=500.0, dp=25.0, force=True)
        assert out is True
        cls.assert_called_once_with(proc_dir=str(tmp_path))
        inst.grid.assert_called_once_with(
            "m", p_start=100.0, p_end=500.0, dp=25.0, force=True
        )
