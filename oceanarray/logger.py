"""Logging configuration and helpers for the oceanarray processing pipeline."""

import contextlib
import datetime
import functools
import logging
import sys
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pandas as pd

#: The package logger. Modules log through ``logging.getLogger(__name__)`` (e.g.
#: ``oceanarray.pipeline.stack``); those loggers propagate here, where the stage
#: handlers and the console live. A :class:`~logging.NullHandler` keeps library
#: use silent until a caller (or the CLI) configures logging.
log = logging.getLogger("oceanarray")
log.setLevel(logging.DEBUG)  # capture everything; handlers filter later
log.addHandler(logging.NullHandler())

#: Level of the console handler a processing stage attaches (``oceanarray -v``: DEBUG).
CONSOLE_LEVEL = logging.INFO

#: Deprecation text for the ``log_fn=`` parameter on the public transform functions.
LOG_FN_DEPRECATED = (
    "log_fn= is deprecated and removed in 0.7.0; oceanarray logs through the "
    "'oceanarray' logger hierarchy — configure logging instead of passing a callback."
)


class _CallbackHandler(logging.Handler):
    """Forward each record's message to a plain callback (the deprecated ``log_fn``)."""

    def __init__(self, callback: Callable[[str], None]) -> None:
        """Store *callback* and handle every level."""
        super().__init__(level=logging.DEBUG)
        self._callback = callback
        self._in_callback = False

    def emit(self, record: logging.LogRecord) -> None:
        """Hand the record's message to the callback.

        A legacy ``log_fn`` may itself log to the ``oceanarray`` logger (the old
        wrappers did); the reentrancy guard stops that re-emitted record from
        being forwarded again, which would otherwise loop.
        """
        if self._in_callback:
            return
        self._in_callback = True
        try:
            self._callback(record.getMessage())
        finally:
            self._in_callback = False


def deprecated_log_fn(
    log_fn: Callable[[str], None] | None,
) -> contextlib.AbstractContextManager:
    """Warn on a legacy ``log_fn`` and forward the package's records to it in the block.

    The public transform functions (``to_canonical_units``, ``interpolate_pressure``,
    the ADCP-QC functions, …) used to take a ``log_fn`` callback. They now log through
    their module logger; this keeps a caller that still passes ``log_fn`` working for
    one release — every record is also handed to the callback — while raising a
    :class:`DeprecationWarning`. A ``None`` callback warns nothing and forwards nothing.

    Parameters
    ----------
    log_fn : callable or None
        The legacy callback, called with each log message; ``None`` is a no-op.

    Returns
    -------
    contextlib.AbstractContextManager
        A context manager to wrap the function body.
    """
    if log_fn is not None:
        warnings.warn(LOG_FN_DEPRECATED, DeprecationWarning, stacklevel=3)
    return _forward_to_callback(log_fn)


@contextlib.contextmanager
def _forward_to_callback(log_fn: Callable[[str], None] | None) -> Iterator[None]:
    """Forward the ``oceanarray`` logger's records to *log_fn* for the length of the block."""
    if log_fn is None:
        yield
        return
    handler = _CallbackHandler(log_fn)
    log.addHandler(handler)
    try:
        yield
    finally:
        log.removeHandler(handler)


@contextlib.contextmanager
def count_warnings(name: str) -> Iterator[list[logging.LogRecord]]:
    """Collect the WARNING-or-worse records logger *name* logs in the block.

    The records still go to every handler as usual; this only collects them, so a
    caller can count them. Records logged by child loggers are not included.

    Parameters
    ----------
    name : str
        Logger name, e.g. ``"oceanarray.pipeline.attribution"``.

    Yields
    ------
    list of logging.LogRecord
        The warnings so far; ``len()`` is their count.

    Examples
    --------
    >>> import logging
    >>> with count_warnings("oceanarray.example") as warned:
    ...     logging.getLogger("oceanarray.example").warning("odd value")
    >>> len(warned)
    1
    """
    warned: list[logging.LogRecord] = []

    def collect(record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            warned.append(record)
        return True  # never drops the record

    target = logging.getLogger(name)
    target.addFilter(collect)
    try:
        yield warned
    finally:
        target.removeFilter(collect)


def enable_logging() -> None:
    """Re-enable the ``oceanarray`` logger.

    .. deprecated:: 0.6.1
        Removed in 0.7.0. The library no longer gates logging on a global flag;
        the ``oceanarray`` logger is enabled by default. Configure it (or
        :func:`logging.disable`) directly instead.
    """
    warnings.warn(
        "enable_logging() is deprecated and removed in 0.7.0; the 'oceanarray' "
        "logger is enabled by default — configure it directly.",
        DeprecationWarning,
        stacklevel=2,
    )
    logging.getLogger("oceanarray").disabled = False


def disable_logging() -> None:
    """Silence the ``oceanarray`` logger.

    .. deprecated:: 0.6.1
        Removed in 0.7.0. Set ``logging.getLogger("oceanarray").disabled = True``
        (or use :func:`logging.disable`) instead.
    """
    warnings.warn(
        "disable_logging() is deprecated and removed in 0.7.0; set "
        "logging.getLogger('oceanarray').disabled = True (or logging.disable).",
        DeprecationWarning,
        stacklevel=2,
    )
    logging.getLogger("oceanarray").disabled = True


def configure_console(level: int | None = None) -> logging.Handler:
    """Attach a console handler (stderr, level-tagged) to the package logger.

    The CLI calls this once so every command's messages reach the terminal; the
    Python API configures logging itself (the library adds only a
    :class:`~logging.NullHandler`). Progress goes to stderr so a command's result
    on stdout stays pipeable. Idempotent: it replaces the console handler a prior
    call attached, so ``-q`` / ``-v`` can set the level without stacking handlers.

    Parameters
    ----------
    level : int, optional
        The console level; defaults to :data:`CONSOLE_LEVEL`.

    Returns
    -------
    logging.Handler
        The console handler attached.
    """
    for handler in [
        h for h in log.handlers if getattr(h, "_oceanarray_console", False)
    ]:
        log.removeHandler(handler)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(CONSOLE_LEVEL if level is None else level)
    console.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
    console._oceanarray_console = True  # type: ignore[attr-defined]  # tag for replacement
    log.addHandler(console)
    return console


def attach_stage_handlers(log_file: Path | None) -> list[logging.Handler]:
    """Attach a DEBUG file handler for a processing stage's own log.

    The stage log keeps the full DEBUG detail with the bare message. The console
    is configured separately (:func:`configure_console`, by the CLI or an API
    caller), so a stage does not add its own console handler and running one never
    doubles console output. Detach with :func:`detach_handlers`.

    Parameters
    ----------
    log_file : pathlib.Path or None
        The stage log file; None attaches nothing.

    Returns
    -------
    list of logging.Handler
        The handlers attached, for :func:`detach_handlers`.
    """
    if log_file is None:
        return []
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(file_handler)
    return [file_handler]


def detach_handlers(handlers: list[logging.Handler]) -> None:
    """Remove and close handlers attached by :func:`attach_stage_handlers`."""
    for handler in handlers:
        log.removeHandler(handler)
        handler.close()


class StageLog:
    """One stage's log, open for the length of a ``with`` block.

    On entry the package handlers are attached (:func:`attach_stage_handlers`);
    calling the instance prints to stdout and appends the same line to the log
    file; on exit the handlers are detached.

    Parameters
    ----------
    mooring_name : str
        The mooring's name, used in the log filename.
    stage_name : str
        The stage, e.g. ``"stage2"``, used in the log filename.
    mooring_dir : pathlib.Path
        The mooring's own directory; the log goes under it.

    Attributes
    ----------
    file : pathlib.Path
        The log file.
    """

    def __init__(self, mooring_name: str, stage_name: str, mooring_dir: Path) -> None:
        """Choose the log file; nothing is attached until the block is entered."""
        self.file = setup_stage_logging(mooring_name, stage_name, mooring_dir)
        self._handlers: list[logging.Handler] = []

    def __enter__(self) -> "StageLog":
        """Attach the package handlers."""
        self._handlers = attach_stage_handlers(self.file)
        return self

    def __exit__(self, *exc: object) -> None:
        """Detach the package handlers."""
        detach_handlers(self._handlers)
        self._handlers = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Print to stdout and append to the log file."""
        print(*args, **kwargs)
        with self.file.open("a", encoding="utf-8") as f:
            print(*args, **kwargs, file=f)


def with_stage_handlers(method: Callable) -> Callable:
    """Detach a stage's log handlers (``self._log_handlers``) when *method* returns."""

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        finally:
            detach_handlers(getattr(self, "_log_handlers", []))
            self._log_handlers = []

    return wrapper


def load_logging_config() -> dict:
    """Load the global logging configuration from config/logging.yaml.

    Returns
    -------
    dict
        Logging configuration dictionary

    Raises
    ------
    FileNotFoundError
        If logging.yaml is not found
    yaml.YAMLError
        If logging.yaml cannot be parsed

    """
    import yaml

    config_path = Path(__file__).parent / "config" / "logging.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Logging config not found: {config_path}")

    with config_path.open() as f:
        config = yaml.safe_load(f)

    return config.get("logging", {})


def setup_stage_logging(mooring_name: str, stage_name: str, proc_dir: Path) -> Path:
    """Set up logging for a processing stage using global configuration.

    This creates simple file-based logging for processing stages (stage1, stage2, etc.)
    using the configuration from config/logging.yaml.

    Parameters
    ----------
    mooring_name : str
        Name of the mooring (e.g., 'dsE_1_2018')
    stage_name : str
        Name of the processing stage (e.g., 'stage1', 'stage2', 'stack')
    proc_dir : Path
        Processing directory for the mooring

    Returns
    -------
    Path
        Full path to the log file.  When the logging configuration file
        (``config/logging.yaml``) is missing or unparseable, falls back to
        ``{proc_dir}/processing_logs/{mooring_name}_{timestamp}_{stage_name}.mooring.log``.
        This function does not raise; config failures are handled silently.

    """
    import yaml

    try:
        config = load_logging_config()
    except (FileNotFoundError, yaml.YAMLError):
        # Fallback: place logs in processing_logs/ subdir
        log_time = datetime.datetime.now().strftime("%Y%m%dT%H")
        log_dir = proc_dir / "processing_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{mooring_name}_{log_time}_{stage_name}.mooring.log"

    # Extract configuration values with defaults
    log_directory = config.get("directory", "processing_logs")
    filename_pattern = config.get(
        "filename_pattern", "{mooring_name}_{timestamp}_{stage}.log"
    )
    timestamp_format = config.get("timestamp_format", "%Y%m%dT%H")
    create_directory = config.get("create_directory", True)

    # Generate timestamp
    timestamp = datetime.datetime.now().strftime(timestamp_format)

    # Determine log directory path
    if Path(log_directory).is_absolute():
        # Absolute path specified
        log_dir = Path(log_directory)
    else:
        # Relative path - create within mooring proc directory
        log_dir = proc_dir / log_directory

    # Create directory if requested and it doesn't exist
    if create_directory:
        log_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename using pattern
    filename = filename_pattern.format(
        mooring_name=mooring_name, timestamp=timestamp, stage=stage_name
    )

    return log_dir / filename


def status(kind: str, msg: str, *, char: str = "=") -> None:
    """Log a formatted status line through the package logger.

    A section/instrument heading or an output-kept note is INFO; an output file
    being created is DEBUG (one per file); an error is ERROR. The console shows
    these when a stage or the CLI has configured the ``oceanarray`` logger.

    Parameters
    ----------
    kind : str
        ``"section"`` (a bordered heading), ``"instr"`` (an instrument sub-heading),
        ``"file"`` (an output file being created), ``"skip"`` (an output kept because it
        exists) or ``"error"``.
    msg : str
        The text of the line.
    char : str, optional
        Border character for ``"section"`` lines; ``"*"`` tells mooring runs apart in a
        multi-mooring script (``*** Stage 1: dsG3_2_2026 ***``).
    """
    if kind == "section":
        border = char * 3
        log.info("\n%s %s %s", border, msg, border)
    elif kind == "instr":
        log.info("\n--- %s ---", msg)
    elif kind == "file":
        log.debug("Creating output file: %s", msg)
    elif kind == "skip":
        log.info("OUTFILE EXISTS: %s  (use --force to overwrite)", msg)
    elif kind == "error":
        log.error("%s", msg)


def format_rel_path(path: Path, root: Path) -> str:
    """Return *path* relative to *root* (forward slashes) for display, or its name if not under *root*.

    Parameters
    ----------
    path : pathlib.Path
        The path to show.
    root : pathlib.Path
        The folder it is shown relative to.

    Returns
    -------
    str

    Examples
    --------
    >>> from pathlib import Path
    >>> format_rel_path(Path("/proc/m1/report/m1.html"), Path("/proc/m1"))
    'report/m1.html'
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def format_time(value: Any) -> str:
    """Return *value* as ISO-8601 rounded to whole seconds, for log messages (``"NaT"`` if missing).

    Parameters
    ----------
    value : Any
        A timestamp :class:`pandas.Timestamp` accepts.

    Returns
    -------
    str

    Examples
    --------
    >>> import numpy as np
    >>> format_time(np.datetime64("2016-08-11T09:59:59.999996672"))
    '2016-08-11T10:00:00'
    """
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return "NaT"
    return ts.round("s").isoformat()
