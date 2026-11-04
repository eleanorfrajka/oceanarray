"""``oceanarray validate`` — check mooring descriptions."""

from __future__ import annotations

import argparse


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate one or more mooring YAML files."""
    all_ok = True
    for yaml_path in args.yaml:
        ok = _print_report(yaml_path)
        if not ok:
            all_ok = False
        print()

    return 0 if all_ok else 1


def build_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the ``validate`` subcommand to *sub*."""
    p_validate = sub.add_parser(
        "validate",
        help="Validate mooring YAML configuration file(s).",
        description=(
            "Parse and validate one or more .mooring.yaml files.\n"
            "Checks instrument types, serial numbers, file_type values, HAB fields,\n"
            "deployment dates, and inline instrument conventions.\n"
            "Exits 0 if all files are valid, 1 if any file has errors or warnings."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_validate.add_argument("yaml", nargs="+", help="Path(s) to .mooring.yaml file(s)")
    p_validate.set_defaults(func=cmd_validate)


def _print_report(yaml_path: str) -> bool:
    """Print a human-readable validation report; return True when there are no errors."""
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

    return len(errors) == 0
