"""Exception types for oceanarray.

Every error the package raises about the user's input (the mooring YAML, a file,
its data or stage) derives from :class:`OceanarrayError`, so ``except
OceanarrayError`` catches them all; each also keeps its built-in base (``ValueError``,
``RuntimeError``), so existing ``except`` clauses still work. A bug in the package
itself, such as an inconsistent table shipped with it, raises a built-in error and
shows a traceback.
"""

from typing import NamedTuple


class ValidationIssue(NamedTuple):
    """A single validation finding with a severity level and human-readable message."""

    level: str  # "ERROR" or "WARNING"
    message: str


class OceanarrayError(Exception):
    """Base class of the errors oceanarray raises."""


class MooringError(OceanarrayError, ValueError):
    """Raised when a mooring description fails validation.

    Carries the full list of validation issues so a caller can inspect every
    finding, not only the summary message.

    Parameters
    ----------
    message : str
        A human-readable summary of why the description was rejected.
    issues : list of ValidationIssue, optional
        The individual validation findings (errors and warnings) that led to
        the failure. Defaults to an empty list.
    """

    def __init__(
        self, message: str, issues: list[ValidationIssue] | None = None
    ) -> None:
        super().__init__(message)
        self.issues: list[ValidationIssue] = list(issues) if issues else []


class StageError(OceanarrayError, ValueError):
    """Raised when a dataset is at the wrong processing stage for the step requested."""


class ReaderError(OceanarrayError, ValueError):
    """Raised when a reader cannot read a file: unknown format, missing header, column or time."""
