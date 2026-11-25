"""Survey mooring YAMLs for top-level keys not in ``oceanarray.mooring.KNOWN_KEYS``.

Walks a processed-data root, reads every ``*.mooring.yaml`` leniently, and prints
the top-level keys that are not yet recognised, with the file that carries each.
Run once over the drive before the unknown-key WARNING is promoted to an ERROR: a
key that is real joins ``KNOWN_KEYS`` with a one-line note on which mooring uses
it; a key that is a typo stays a warning.

Usage
-----
``python scripts/survey_mooring_keys.py PROC_ROOT``
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from oceanarray.mooring import KNOWN_KEYS
from oceanarray.mooring import yaml_file as mooring


def main(argv: list[str] | None = None) -> int:
    """Print the unrecognised top-level keys found under a processed-data root.

    Parameters
    ----------
    argv : list of str or None, optional
        Command-line arguments; defaults to :data:`sys.argv`.

    Returns
    -------
    int
        Process exit code (always ``0``).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "proc_root",
        type=Path,
        help="Processed-data root to walk for '*.mooring.yaml' files.",
    )
    args = parser.parse_args(argv)

    found: dict[str, list[Path]] = defaultdict(list)
    scanned = 0
    for path in sorted(args.proc_root.rglob("*.mooring.yaml")):
        data = mooring._parse(path)
        if not isinstance(data, dict):
            print(f"  (unreadable) {path}")
            continue
        scanned += 1
        for key in data:
            if key not in KNOWN_KEYS:
                found[key].append(path)

    print(f"Scanned {scanned} mooring config(s) under {args.proc_root}")
    if not found:
        print("No unknown top-level keys — KNOWN_KEYS covers the drive.")
        return 0

    print(f"{len(found)} unknown top-level key(s):")
    for key in sorted(found):
        print(f"  {key!r} ({len(found[key])} file(s)):")
        for path in found[key]:
            print(f"      {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
