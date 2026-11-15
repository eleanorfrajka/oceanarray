import logging

import numpy as np
import pytest

from oceanarray import logger


def test_configure_console_is_idempotent():
    """A second call replaces the first console handler, not stacks it."""

    def consoles():
        return [
            h for h in logger.log.handlers if getattr(h, "_oceanarray_console", False)
        ]

    logger.configure_console(logging.INFO)
    logger.configure_console(logging.WARNING)
    assert len(consoles()) == 1
    assert consoles()[0].level == logging.WARNING
    logger.log.removeHandler(consoles()[0])
    assert consoles() == []


def test_attach_stage_handlers_writes_the_stage_log(tmp_path):
    """The stage file handler records DEBUG-and-up with the bare message."""
    log_file = tmp_path / "stage.log"
    handlers = logger.attach_stage_handlers(log_file)
    try:
        logging.getLogger("oceanarray.test").debug("a debug line")
        for handler in handlers:
            handler.flush()
        assert log_file.read_text().strip() == "a debug line"
    finally:
        logger.detach_handlers(handlers)


def test_attach_stage_handlers_none_attaches_nothing():
    assert logger.attach_stage_handlers(None) == []


def test_disable_logging_is_deprecated_and_disables():
    try:
        with pytest.warns(DeprecationWarning):
            logger.disable_logging()
        assert logging.getLogger("oceanarray").disabled is True
    finally:
        logging.getLogger("oceanarray").disabled = False


def test_enable_logging_is_deprecated_and_enables():
    logging.getLogger("oceanarray").disabled = True
    with pytest.warns(DeprecationWarning):
        logger.enable_logging()
    assert logging.getLogger("oceanarray").disabled is False


def test_format_time_rounds_to_whole_seconds():
    # Instrument clocks carry sub-second float noise; the log shows whole seconds.
    noisy = np.datetime64("2016-08-11T09:59:59.999996672")
    assert logger.format_time(noisy) == "2016-08-11T10:00:00"


def test_format_time_handles_nat():
    assert logger.format_time(np.datetime64("NaT")) == "NaT"
