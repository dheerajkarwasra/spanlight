"""The count of gateway spans this process could not store, and the one loss outside the recorder.

`/health/ready` reports `spans_lost()` as `gateway_spans_lost`. The recorder counts the spans it
could not write, refused or dropped (`app.gateway.recorder`); `span_not_built` counts a call
whose span could not even be built, so that loss is not only a log line either.
"""

import structlog

from app.core.observability import GATEWAY_RECORD_FAILURES

logger = structlog.get_logger(__name__)

_spans_lost = 0  # since the process started


def spans_lost() -> int:
    """Gateway spans this process could not store since it started: lost, refused or dropped."""
    return _spans_lost


def lose() -> None:
    global _spans_lost  # noqa: PLW0603 - one process-wide count, read by the readiness probe
    _spans_lost += 1


def span_not_built(request_id: str, failure: BaseException) -> None:
    """Count a call whose span could not be built as `reason="error"`, and log it.

    Only the error's type is logged: its message, or a traceback's locals, may quote the payload.
    """
    GATEWAY_RECORD_FAILURES.labels("error").inc()
    lose()
    logger.error(
        "gateway.span_failed",
        request_id=request_id,
        stage="build",
        error_type=type(failure).__name__,
    )
