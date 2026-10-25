"""Logging configuration and helpers for the oceanarray processing pipeline."""

import datetime
import functools
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Global logger instance (will be configured by setup_logger)
log = logging.getLogger("oceanarray")
log.setLevel(logging.DEBUG)  # capture everything; handlers filter later

# Global logging flag
# Set to True to enable logging, False to disable
LOGGING_ENABLED = True


def enable_logging() -> None:
    """Enable logging globally."""
    global LOGGING_ENABLED
    LOGGING_ENABLED = True


def disable_logging() -> None:
    """Disable logging globally."""
    global LOGGING_ENABLED
    LOGGING_ENABLED = False


def log_info(message: str, *args: Any) -> None:
    """Log an info message, if logging is enabled."""
    if LOGGING_ENABLED:
        log.info(message, *args, stacklevel=2)


def log_warning(message: str, *args: Any) -> None:
    """Log a warning message, if logging is enabled."""
    if LOGGING_ENABLED:
        log.warning(message, *args, stacklevel=2)


def log_error(message: str, *args: Any) -> None:
    """Log an error message, if logging is enabled."""
    if LOGGING_ENABLED:
        log.error(message, *args, stacklevel=2)


def log_debug(message: str, *args: Any) -> None:
    """Log a debug message, if logging is enabled."""
    if LOGGING_ENABLED:
        log.debug(message, *args, stacklevel=2)


def attach_stage_handlers(log_file: Path | None) -> list[logging.Handler]:
    """Send the package's INFO-and-above messages to stdout and *log_file*.

    A processing stage calls this when it starts, so the messages its pure
    functions log reach the console and the stage log as they did when the stage
    printed them. Detach with :func:`detach_handlers`.

    Parameters
    ----------
    log_file : pathlib.Path or None
        The stage log file; None sends to stdout only.

    Returns
    -------
    list of logging.Handler
        The handlers attached, for :func:`detach_handlers`.
    """
    formatter = logging.Formatter("%(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for handler in handlers:
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        log.addHandler(handler)
    return handlers


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


def setup_logger(array_name: str, output_dir: str = "logs") -> None:
    """Configure the global logger to output to a file for the given array.

    Parameters
    ----------
    array_name : str
        Name of the observing array (e.g., 'move', 'rapid', etc.).
    output_dir : str
        Directory to save log files.

    """
    if not LOGGING_ENABLED:
        return
    # Resolve output directory to project root
    project_root = Path(__file__).resolve().parent.parent
    output_path = project_root / output_dir
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%dT%H")
    log_filename = f"{array_name.upper()}_{timestamp}_read.log"
    log_path = output_path / log_filename

    # Prevent duplicate handlers in case of multiple calls
    if not any(
        isinstance(h, logging.FileHandler) and h.baseFilename == log_path
        for h in log.handlers
    ):
        file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="w")
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(funcName)s %(message)s",
            datefmt="%Y%m%dT%H%M%S",
        )
        file_handler.setFormatter(formatter)

        # Optional: console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        # Clear existing handlers first
        log.handlers.clear()

        log.addHandler(file_handler)
        log.addHandler(console_handler)

        log.info(f"Logger initialized for array: {array_name}, writing to {log_path}")


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
