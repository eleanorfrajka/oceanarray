"""Renamed CLI flags: the old spelling works for one release, with a warning."""

from __future__ import annotations

import argparse
import sys
import warnings

#: The release that removes the old spellings.
REMOVED_IN = "0.7.0"


class DeprecatedAlias(argparse.Action):
    """Accept a renamed flag, store its value on the new flag's destination, and warn.

    Use with ``new="--new-name"`` and ``help=argparse.SUPPRESS``, so the old spelling
    still works but is not advertised.
    """

    def __init__(self, *args: object, new: str, **kwargs: object) -> None:
        """Remember the new spelling."""
        self.new = new
        super().__init__(*args, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,  # noqa: ARG002  — argparse.Action signature
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        """Store the value and warn that *option_string* is now :attr:`new`."""
        msg = (
            f"{option_string} is deprecated and removed in {REMOVED_IN}; "
            f"use {self.new}."
        )
        warnings.warn(msg, DeprecationWarning, stacklevel=2)
        print(f"oceanarray: warning: {msg}", file=sys.stderr)
        # nargs=0 is a renamed on/off switch; otherwise forward the parsed values.
        setattr(namespace, self.dest, True if self.nargs == 0 else values)
