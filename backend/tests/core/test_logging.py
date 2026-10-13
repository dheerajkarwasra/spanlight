"""`configure_logging` runs on every `create_app`; a second run must not strand earlier loggers.

Failure mode: loggers are cached on first use with the processor list that was configured then.
If a later `configure_logging` installs a new list, those cached module loggers keep the old one,
and anything that changes the configured chain in place (`structlog.testing.capture_logs`, which
email and alert tests rely on) no longer sees their events.
"""

import io
import logging
from collections.abc import Iterator

import pytest
import structlog
from structlog.testing import capture_logs

from app.core.logging import configure_logging


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level


def test_a_logger_used_before_a_reconfigure_still_follows_the_configured_chain() -> None:
    configure_logging(stream=io.StringIO())
    logger = structlog.get_logger("tests.core.test_logging")
    logger.info("warm_up")  # binds and caches this logger with the current chain

    configure_logging(stream=io.StringIO())
    with capture_logs() as logs:
        logger.info("after_reconfigure", answer=42)

    assert [(entry["event"], entry["answer"]) for entry in logs] == [("after_reconfigure", 42)]


def test_a_reconfigure_still_renders_to_the_new_stream() -> None:
    configure_logging(stream=io.StringIO())
    logger = structlog.get_logger("tests.core.test_logging")
    logger.info("warm_up")

    stream = io.StringIO()
    configure_logging(stream=stream)
    logger.info("rendered_here")

    assert '"event": "rendered_here"' in stream.getvalue()
