"""Counting the gateway spans this process could not store, and the one loss outside the recorder.

Every lost span counts three ways, in `lose`: in `spanlight_gateway_record_failures_total` by
reason, in `spans_lost()` (which `/health/ready` reports as `gateway_spans_lost`), and in the
process's `LossCounter` by project, reason and hour, which `app.telemetry_losses.flush` writes
to `telemetry_losses_hourly` every minute. The recorder counts the spans it could not write,
refused or dropped (`app.gateway.recorder`); `span_not_built` counts a call whose span could not
even be built, so that loss is not only a log line either.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog

from app.core.observability import GATEWAY_RECORD_FAILURES
from app.rollups.compute import floor_hour
from app.telemetry_losses.counter import LossCounter

logger = structlog.get_logger(__name__)

# `app.telemetry_losses.reasons.GATEWAY_REASONS`, as a type.
type GatewayLossReason = Literal["overflow", "error", "rejected"]

SOURCE = "gateway"

_spans_lost = 0  # since the process started
_counter = LossCounter()


def spans_lost() -> int:
    """Gateway spans this process could not store since it started: lost, refused or dropped."""
    return _spans_lost


def loss_counter() -> LossCounter:
    """The process's loss counter, which the flush drains."""
    return _counter


def lose(project_id: uuid.UUID, reason: GatewayLossReason) -> None:
    """Count one span of `project_id` lost for `reason`, in the hour it was lost."""
    global _spans_lost  # noqa: PLW0603 - one process-wide count, read by the readiness probe
    GATEWAY_RECORD_FAILURES.labels(reason).inc()
    _spans_lost += 1
    _counter.add(project_id, SOURCE, reason, floor_hour(datetime.now(UTC)))


def span_not_built(request_id: str, project_id: uuid.UUID, failure: BaseException) -> None:
    """Count a call whose span could not be built as `reason="error"`, and log it.

    Only the error's type is logged: its message, or a traceback's locals, may quote the payload.
    """
    lose(project_id, "error")
    logger.error(
        "gateway.span_failed",
        request_id=request_id,
        stage="build",
        error_type=type(failure).__name__,
    )
