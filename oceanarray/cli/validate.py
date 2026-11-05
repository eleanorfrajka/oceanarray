"""``oceanarray validate`` — check mooring descriptions."""

from __future__ import annotations

import argparse
from pathlib import Path

from oceanarray import paths
from oceanarray.cli._common import _add_dir_args


def _description_path(given: str, proc_dir: str | None) -> Path:
    """Return the ``*.mooring.yaml`` for a path, a mooring folder, or a name with ``--proc-dir``."""
    path = Path(given)
    if path.is_dir():
        found = sorted(path.glob("*.mooring.yaml"))
        if len(found) == 1:
            return found[0]
        return path / f"{path.name}.mooring.yaml"
    if not path.exists() and proc_dir:
        return paths.mooring_proc_dir(Path(proc_dir), given) / f"{given}.mooring.yaml"
    return path


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate each mooring description; 1 on any error (or, with --strict, any warning)."""
    all_ok = True
    for given in args.mooring:
        ok = _print_report(
            str(_description_path(given, args.proc_dir)), strict=args.strict
        )
        all_ok = all_ok and ok
        print()
    return 0 if all_ok else 1


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``validate`` subcommand to *sub*."""
    p_validate = sub.add_parser(
        "validate",
        help="Check mooring descriptions (<name>.mooring.yaml) without processing anything.",
        description=(
            "Read and check one or more mooring descriptions: instrument types, serial\n"
            "numbers, file types, heights above bottom, deployment dates, inline\n"
            "instruments, QC override blocks and the OceanSITES codes. Prints every ERROR\n"
            "and WARNING. Exits 0 if no description has an ERROR (with --strict: no\n"
            "WARNING either), 1 otherwise."
        ),
        epilog=(
            "Examples:\n"
            "  # one description\n"
            "  oceanarray validate /data/proc/dsG3_1_2026/dsG3_1_2026.mooring.yaml\n"
            "  # every mooring of a cruise, failing on warnings too\n"
            "  oceanarray validate /data/proc/*/*.mooring.yaml --strict\n"
            "  # by name\n"
            "  oceanarray validate dsG3_1_2026 --proc-dir /data/proc"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_validate.add_argument(
        "mooring",
        nargs="+",
        help="Mooring description(s): a <name>.mooring.yaml, its folder, or a name with --proc-dir.",
    )
    _add_dir_args(p_validate, raw_needed=False)
    p_validate.add_argument(
        "--strict", action="store_true", help="Exit 1 on warnings as well as errors."
    )
    p_validate.set_defaults(func=cmd_validate)


def _print_report(yaml_path: str, *, strict: bool = False) -> bool:
    """Print a human-readable validation report; return True when the description passes."""
    from oceanarray import mooring as _mooring

    issues = _mooring.check(yaml_path)

    errors = [i for i in issues if i.level == "ERROR"]
    warnings = [i for i in issues if i.level == "WARNING"]

    print(f"Validating: {yaml_path}")

    if not issues:
        print("  OK — no issues found")
        return True

    for issue in issues:
        marker = "  ERROR  " if issue.level == "ERROR" else "  warn   "
        print(f"{marker}{issue.message}")

    summary = []
    if errors:
        summary.append(f"{len(errors)} error(s)")
    if warnings:
        summary.append(f"{len(warnings)} warning(s)")
    print(f"  {', '.join(summary)}")

    return not errors and not (strict and warnings)
