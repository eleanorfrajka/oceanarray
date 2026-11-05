"""The CLI on a mooring given by path, the renamed flags, -v and init, on the dune2 fixtures."""

import logging
import shutil
from pathlib import Path

import pytest
import yaml

from oceanarray import paths
from oceanarray.cli import build_parser

_PROC = Path(__file__).parent.parent / "fixtures" / "proc"
_NAME = "dune2_1_2026"


def _cli(args: list[str]) -> int:
    """Run the oceanarray CLI with *args*; return its exit code."""
    ns = build_parser().parse_args(args)
    return ns.func(ns)


@pytest.fixture
def folder(tmp_path) -> Path:
    """A copy of the processed dune2 tree, in a folder not named after the mooring."""
    dest = tmp_path / "MIXSED_mooring_2"
    shutil.copytree(_PROC / _NAME, dest)
    return dest


def test_report_dry_run_takes_the_description_path(folder, capsys):
    """No --proc-dir: the positional is the *.mooring.yaml, and the folder can have any name."""
    assert _cli(["report", str(folder / f"{_NAME}.mooring.yaml"), "--dry-run"]) == 0
    assert str(folder / "report" / f"{_NAME}_report.html") in capsys.readouterr().out


def test_process_stage3_on_a_folder_path(folder):
    """process FOLDER --stage 3 runs the verb and rewrites both stage-3 files."""
    before = {p: p.stat().st_mtime for p in folder.rglob(paths.stage_glob(3))}
    assert len(before) == 2
    assert _cli(["process", str(folder), "--stage", "3", "--force"]) == 0
    assert all(p.stat().st_mtime > t for p, t in before.items())


def test_only_and_the_old_serial_spelling(folder, capsys):
    """--only picks the instrument; --serial still works for one release and says so."""
    target = next(folder.rglob(f"*2941{paths.stage_suffix(3)}"))
    other = next(folder.rglob(f"*9920{paths.stage_suffix(3)}"))
    for flag in ("--only", "--serial"):
        t_other = other.stat().st_mtime
        assert (
            _cli(["process", str(folder), "--stage", "3", "--force", flag, "2941"]) == 0
        )
        assert other.stat().st_mtime == t_other
        assert target.exists()
    assert (
        "--serial is deprecated and removed in 0.7.0; use --only"
        in capsys.readouterr().err
    )


def test_process_stack_without_proc_dir_points_at_run(folder, capsys):
    """The stack and grid through process need --proc-dir; the message names run."""
    with pytest.raises(SystemExit) as exit_info:
        _cli(["process", str(folder), "--stage", "stack"])
    assert exit_info.value.code == 2  # a usage error
    assert "oceanarray run" in capsys.readouterr().err


def test_a_path_that_does_not_exist_is_refused(tmp_path):
    """A positional that is neither a path nor a name under --proc-dir stops with a message."""
    with pytest.raises(SystemExit, match="mooring.yaml or a mooring folder"):
        _cli(["report", str(tmp_path / "nowhere")])


def test_old_plot_flag_spellings_still_parse(capsys):
    """--var_y / --var_color parse into the same place as --var-y / --var-color."""
    ns = build_parser().parse_args(
        [
            "plot",
            "m",
            "--proc-dir",
            ".",
            "--var_y",
            "pressure",
            "--var_color",
            "temperature",
        ]
    )
    assert (ns.var_y, ns.var_color) == ("pressure", "temperature")
    assert (
        "--var_y is deprecated and removed in 0.7.0; use --var-y"
        in capsys.readouterr().err
    )


def test_verbose_lowers_the_stage_console_level(monkeypatch):
    """-v makes the stage console handler pass DEBUG messages."""
    from oceanarray import logger
    from oceanarray.cli import main

    monkeypatch.setattr(logger, "CONSOLE_LEVEL", logging.INFO)
    monkeypatch.setattr("sys.argv", ["oceanarray", "-v", "list"])
    with pytest.raises(SystemExit):
        main()
    handlers = logger.attach_stage_handlers(None)
    try:
        assert handlers[0].level == logging.DEBUG
    finally:
        logger.detach_handlers(handlers)


def test_init_writes_the_stack_and_grid_blocks_commented(tmp_path):
    """The init stub shows the optional blocks, commented out, so the YAML has neither."""
    assert _cli(["init", "dsX_1_2026", "--proc-dir", str(tmp_path)]) == 0
    stub = tmp_path / "dsX_1_2026" / "dsX_1_2026.mooring.yaml"
    text = stub.read_text()
    assert "# stack:" in text and "# grid:" in text
    assert yaml.safe_load(text).keys().isdisjoint({"stack", "grid"})


def test_animate_finds_the_files_from_the_description(folder, monkeypatch, capsys):
    """animate picks each instrument's stage-3 file from the description; --only matches exactly."""
    import oceanarray.plotters as plotters

    seen = []

    def _fake(_ds, out, **_k):
        seen.append(out.name)
        return out

    monkeypatch.setattr(plotters, "animate_hodograph", _fake)
    assert _cli(["animate", str(folder)]) == 0
    assert sorted(seen) == [
        f"{_NAME}_2941_hodograph.gif",
        f"{_NAME}_9920_hodograph.gif",
    ]
    seen.clear()
    assert _cli(["animate", str(folder), "--only", "9920"]) == 0
    assert seen == [f"{_NAME}_9920_hodograph.gif"]
    assert _cli(["animate", str(folder), "--only", "99"]) == 1
    assert "serial 99 is not in dune2_1_2026's description" in capsys.readouterr().out


def test_process_carries_on_and_strict_stops(folder, capsys):
    """A broken stage-2 file: process logs it and exits 0; --strict exits 1."""
    broken = next(folder.rglob(f"*9920{paths.stage_suffix(2)}"))
    broken.write_bytes(b"not a netCDF file")
    assert _cli(["process", str(folder), "--stage", "3", "--force"]) == 0
    assert _cli(["process", str(folder), "--stage", "3", "--force", "--strict"]) == 1
    assert "stage 3" in capsys.readouterr().out.lower()


def test_verbose_prints_each_stage_file(folder, monkeypatch, capsys):
    """oceanarray -v process names every stage file it wrote or kept; without -v it does not."""
    from oceanarray import logger
    from oceanarray.cli import main

    monkeypatch.setattr(logger, "CONSOLE_LEVEL", logging.INFO)
    for argv, shown in ((["process"], False), (["-v", "process"], True)):
        monkeypatch.setattr(
            "sys.argv", ["oceanarray", *argv, str(folder), "--stage", "3"]
        )
        with pytest.raises(SystemExit):
            main()
        out = capsys.readouterr().out
        assert ("stage 3 kept:" in out) is shown


def test_report_dry_run_honours_only_and_cruise_table(folder, capsys):
    """The dry run lists only the --only instrument pages, and the recovery table when asked."""
    argv = ["report", str(folder), "--instruments", "--cruise-table", "--dry-run"]
    assert _cli([*argv, "--only", "2941"]) == 0
    out = capsys.readouterr().out
    assert f"{_NAME}_2941_report.html" in out
    assert f"{_NAME}_9920_report.html" not in out
    assert str(folder / f"{_NAME}_recovery_table.html") in out


def test_plot_takes_the_folder_path(folder):
    """plot FOLDER --output writes the overview into the folder, whatever it is called."""
    import matplotlib

    matplotlib.use("Agg")
    assert _cli(["plot", str(folder), "--output", "overview.png"]) == 0
    assert (folder / "overview.png").exists()


def test_process_report_on_a_broken_description_is_an_error_line(tmp_path, capsys):
    """process --report on a description missing waterdepth: an ERROR line and exit 1."""
    moor = tmp_path / "m"
    moor.mkdir()
    (moor / "m.mooring.yaml").write_text("name: m\nclamp: []\n")
    assert _cli(["process", str(moor), "--report"]) == 1
    assert "ERROR" in capsys.readouterr().out


def test_animate_output_dir_and_the_old_minus_o_file(
    folder, tmp_path, monkeypatch, capsys
):
    """-o is the GIF directory (as on report/run/plot); an old `-o x.gif` still names the file."""
    import oceanarray.plotters as plotters

    seen = []

    def _fake(_ds, out, **_k):
        seen.append(out)
        return out

    monkeypatch.setattr(plotters, "animate_hodograph", _fake)
    gifs = tmp_path / "gifs"
    assert _cli(["animate", str(folder), "--only", "9920", "-o", str(gifs)]) == 0
    assert seen == [gifs / f"{_NAME}_9920_hodograph.gif"]
    seen.clear()
    one = tmp_path / "one.gif"
    assert _cli(["animate", str(folder), "--only", "9920", "-o", str(one)]) == 0
    assert seen == [one]
    assert "now --output" in capsys.readouterr().err


def test_report_short_flags_are_old_spellings(folder, capsys):
    """-i/-s/-g/-A still switch their pages on for a release, with the long name on stderr."""
    ns = build_parser().parse_args(["report", str(folder), "-i", "-s", "-g", "-A"])
    assert (ns.instruments, ns.stack, ns.grid, ns.all_reports) == (True,) * 4
    err = capsys.readouterr().err
    for new in ("--instruments", "--stack", "--grid", "--all"):
        assert f"use {new}" in err


def test_dt_is_the_old_spelling_of_dt_seconds(capsys):
    """--dt still sets the stack time step, and says --dt-seconds."""
    ns = build_parser().parse_args(["run", "m", "--proc-dir", ".", "--dt", "600"])
    assert ns.dt_seconds == 600
    assert (
        "--dt is deprecated and removed in 0.7.0; use --dt-seconds"
        in capsys.readouterr().err
    )


def test_process_dry_run_lists_every_stage_and_the_interpolated_pressure(
    folder, capsys
):
    """process --dry-run covers stages 1-3 by path, names the pressure targets, writes nothing."""
    before = {p: p.stat().st_mtime for p in folder.rglob("*.nc")}
    assert _cli(["process", str(folder), "--dry-run"]) == 0
    out = capsys.readouterr().out
    for n in (1, 2, 3):
        assert f"Stage {n}:" in out
    line = next(ln for ln in out.splitlines() if "2941_stage3" in ln)
    assert "pressure interpolated" in line  # the microcat takes its pressure from 9920
    assert "pressure interpolated" not in next(
        ln for ln in out.splitlines() if "9920_stage3" in ln
    )
    assert {p: p.stat().st_mtime for p in before} == before


def test_process_strict_with_stack_points_to_run(tmp_path, capsys):
    """process --stage stack --strict is refused, naming oceanarray run --strict."""
    from oceanarray.cli import build_parser

    (tmp_path / "m").mkdir()
    ns = build_parser().parse_args(
        ["process", "m", "--proc-dir", str(tmp_path), "--stage", "stack", "--strict"]
    )
    with pytest.raises(SystemExit) as exit_info:
        ns.func(ns)
    assert exit_info.value.code == 2
    assert "oceanarray run MOORING --strict" in capsys.readouterr().err
