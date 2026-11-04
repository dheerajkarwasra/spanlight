"""Retry and account for one logical gateway span write."""

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import structlog

from app.budgets.reserve import settle_unstored
from app.core.db_errors import is_transient, sqlstate
from app.core.observability import INGESTED_SPANS, REJECTED_SPANS
from app.gateway.context import GatewayContext
from app.gateway.span_losses import lose
from app.gateway.write_evidence import WriteEvidence
from app.ingest.pipeline import IngestOutcome, IngestTarget

logger = structlog.get_logger(__name__)
METRIC_SOURCE = "gateway"


async def _record(
    context: GatewayContext,
    span: dict[str, Any],
    credential_ids: Sequence[uuid.UUID],
    *,
    slots: asyncio.Semaphore | None,
    prepared_truncated: bool | None,
    evidence: WriteEvidence,
    write: Callable[..., Awaitable[IngestOutcome]],
    retry_delays: Sequence[float],
) -> bool:
    """`record`, each try holding one of `slots` when given, for a span whose payloads may
    already be prepared (`prepared_truncated`, see `IngestTarget`)."""
    target = IngestTarget(
        project_id=context.project_id,
        capture_payloads=context.capture_payloads,
        org_id=context.org_id,
        redact_contact_info=context.redact_contact_info,
        source_key_id=context.key_id,
        prepared_truncated=prepared_truncated,
    )
    outcome = await _write_with_retries(
        context, target, span, credential_ids, slots, evidence, write, retry_delays
    )
    if outcome is None:
        return False
    INGESTED_SPANS.labels(METRIC_SOURCE).inc(outcome.accepted)
    if outcome.rejected:
        if evidence.unacknowledged_commit and not evidence.stored:
            evidence.abandoned(context.project_id)
            return False
        REJECTED_SPANS.labels(METRIC_SOURCE).inc(len(outcome.rejected))
        lose(context.project_id, "rejected")
        evidence.counted = True
        logger.warning(
            "gateway.span_rejected",
            span_id=span.get("span_id"),
            reason=outcome.rejected[0].reason,
        )
        return False
    return True


async def _write_with_retries(
    context: GatewayContext,
    target: IngestTarget,
    span: dict[str, Any],
    credential_ids: Sequence[uuid.UUID],
    slots: asyncio.Semaphore | None,
    evidence: WriteEvidence,
    write: Callable[..., Awaitable[IngestOutcome]],
    retry_delays: Sequence[float],
) -> IngestOutcome | None:
    """`write_span`, retried after each of `RECORD_RETRY_DELAYS_S`; None if the last try fails.

    Each try holds one of `slots` (when given), and none is held while waiting to retry. Only a
    transient failure (`is_transient`) is retried: any other would fail every retry too.
    """
    now = context.clock()  # every try counts the call in the same minute
    span_id = span.get("span_id")
    for tries, retry_in_s in enumerate((*retry_delays, None)):
        try:
            async with slots if slots is not None else contextlib.nullcontext():
                return await write(
                    context, target, span, credential_ids, now, retry=tries > 0, evidence=evidence
                )
        except Exception as failure:  # noqa: BLE001 - counted and logged, the client was answered
            if retry_in_s is None or not is_transient(failure):
                if evidence.stored:
                    return IngestOutcome(accepted=1)
                if evidence.unacknowledged_commit and not evidence.stored:
                    evidence.abandoned(context.project_id)
                    return None
                _write_lost(context.project_id, span, failure, tries=tries + 1)
                evidence.counted = True
                await settle_unstored(context.sessions, context.project_id, span)
                return None
            logger.warning(
                "gateway.record_retry",
                span_id=span_id,
                error_type=type(failure).__name__,
                retry_in_s=retry_in_s,
            )
            await context.sleep(retry_in_s)
    return None  # not reached: the last try returns from the loop


def _write_lost(
    project_id: uuid.UUID, span: dict[str, Any], failure: BaseException, *, tries: int
) -> None:
    """Count and log a lost span by error type and SQLSTATE: the message may quote the payload."""
    lose(project_id, "error")
    logger.error(
        "gateway.record_failed",
        span_id=span.get("span_id"),
        trace_id=span.get("trace_id"),
        stage="write",
        error_type=type(failure).__name__,
        sqlstate=sqlstate(failure),
        tries=tries,
    )
