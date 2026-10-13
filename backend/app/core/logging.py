"""Structured logging: JSON lines on stdout, with the request id bound per request."""

import logging
import sys
from typing import TextIO

import structlog
from structlog.typing import EventDict, WrappedLogger

from app.core.request_context import current_request_id


def _add_request_id(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    request_id = current_request_id()
    if request_id is not None:
        event_dict.setdefault("request_id", request_id)
    return event_dict


_SHARED_PROCESSORS: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    _add_request_id,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]
# One list for the life of the process. Loggers are cached on first use together with the list
# configured at that moment, so every `configure_logging` (one per `create_app`) must hand
# structlog this same instance: a fresh list would leave earlier module loggers on a stale chain,
# out of reach of anything that changes the configured chain in place, such as
# `structlog.testing.capture_logs`.
_STRUCTLOG_PROCESSORS: list[structlog.typing.Processor] = [
    *_SHARED_PROCESSORS,
    structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
]


def configure_logging(
    level: str = "INFO", *, json: bool = True, stream: TextIO | None = None
) -> None:
    """Route structlog and stdlib logging to `stream` (stdout by default).

    Safe to call more than once: the level, format and stream live on the stdlib handler, which
    is replaced on every call, while structlog's processor chain stays the same list.

    A command that prints a result on stdout passes `sys.stderr`, so the result stays parseable.
    """
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=_STRUCTLOG_PROCESSORS,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy, alembic) through the same renderer.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout if stream is None else stream)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Our middleware emits one access line per request; uvicorn's would duplicate it.
    logging.getLogger("uvicorn.access").disabled = True
    # httpx logs "HTTP Request: POST <full url>" at INFO after every call. An alert channel's
    # Slack webhook URL is a secret, so neither library may log requests.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
