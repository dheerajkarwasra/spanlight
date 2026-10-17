"""The ingestion pipeline: validate → normalize → upsert → recompute rollups.

Validation and normalization are here; the writes are `app.ingest.store`.

Idempotent by construction: spans are upserted on their primary key and every
trace rollup is recomputed from the stored spans, so replaying a batch (SDK
retries, OTLP re-export) leaves exactly the same rows behind.

Concurrency: the trace upsert takes a row lock on each affected trace, so two
batches touching the same trace serialize; rows are written in sorted key
order so batches touching several traces cannot deadlock each other.
"""

import json
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import SPAN_NORMALIZATION_FAILURES, feature_used
from app.core.text import clean_json
from app.db.rls import bind_project
from app.ingest.jsondepth import MAX_JSON_DEPTH, exceeds_depth
from app.ingest.normalize import NormalizedSpan, SpanRejectedError, normalize_span
from app.ingest.schemas import RejectedSpan, SpanIn
from app.ingest.store import write_spans
from app.pricing.cost import PriceBook, load_price_book

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class IngestTarget:
    project_id: uuid.UUID
    capture_payloads: bool
    org_id: uuid.UUID  # whose price overrides price the spans
    # The gateway key the calls came through. Only the gateway sets it; native and OTLP
    # ingestion leave it None, since a project API key is not a gateway key.
    source_key_id: uuid.UUID | None = None
    # Set only by a caller that ran its spans through `normalize.prepare_span_payloads` (the
    # gateway recorder, one span per target): their payloads are stored as they are, and this
    # is their `truncated` flag. None: payloads are redacted and truncated here.
    prepared_truncated: bool | None = None


@dataclass
class IngestOutcome:
    accepted: int = 0
    rejected: list[RejectedSpan] = field(default_factory=list)


class InvalidJsonError(ValueError):
    pass


class ProjectGoneError(LookupError):
    """The target project was deleted before the batch could be written."""


# A shared lock on the project row, taken before any trace or span is written. The span INSERT
# needs it anyway (its foreign key check takes it at the end of the statement), but by then the
# trace upsert holds row locks on existing traces, which a project or organization deletion
# cascades into. The deletion holds the project row exclusively and waits for those traces, the
# batch holds the traces and waits for the project: a deadlock that fails one of the two requests.
# Taking the project first follows the lock order in `app.services.deletion`: a running deletion
# makes the batch wait and then find no project, and a running batch makes the deletion wait.
_KEY_SHARE_PROJECT = text("SELECT id FROM projects WHERE id = :project_id FOR KEY SHARE")


def parse_json_body(body: bytes) -> Any:
    """Parse JSON strictly (no NaN/Infinity), stripping NUL and replacing lone surrogates.

    Postgres rejects NUL in jsonb and text columns, and a lone surrogate (a `\\ud800` escape)
    cannot be encoded as UTF-8; either would otherwise fail the whole batch.
    """

    def reject_constant(name: str) -> Any:
        raise InvalidJsonError(f"{name} is not valid JSON")

    def finite_float(literal: str) -> float:
        number = float(literal)
        if math.isinf(number):  # `1e999` is valid JSON, but past a double and jsonb has no infinity
            raise InvalidJsonError("a number is too large to store")
        return number

    try:
        parsed = json.loads(body, parse_constant=reject_constant, parse_float=finite_float)
    except InvalidJsonError:  # NaN, Infinity or a number past a double
        raise
    except ValueError as exc:  # also a number with more digits than Python converts (4300)
        raise InvalidJsonError("request body is not valid JSON") from exc
    except RecursionError as exc:  # nested deeper than the parser can follow
        raise InvalidJsonError(_TOO_DEEP) from exc
    if exceeds_depth(parsed):
        raise InvalidJsonError(_TOO_DEEP)
    return clean_json(parsed)


_TOO_DEEP = f"request body is nested more than {MAX_JSON_DEPTH} levels deep"
_UNPROCESSABLE = "span: could not be processed"


async def ingest_spans(
    session: AsyncSession,
    target: IngestTarget,
    raw_spans: Sequence[Any],
    *,
    now: datetime | None = None,
) -> IngestOutcome:
    """Validate and store a batch of spans in one transaction. The caller commits.

    Raises `ProjectGoneError` when the project no longer exists; nothing is written then.
    """
    now = now or datetime.now(UTC)
    await bind_project(session, target.project_id)
    project = await session.execute(_KEY_SHARE_PROJECT, {"project_id": target.project_id})
    if project.scalar_one_or_none() is None:
        raise ProjectGoneError(str(target.project_id))
    prices = await load_price_book(session, target.org_id)

    outcome = IngestOutcome()
    accepted: dict[tuple[str, str], tuple[int, NormalizedSpan]] = {}

    for index, raw in enumerate(raw_spans):
        normalized = _prepare(raw, index, target, prices, now)
        if isinstance(normalized, str):
            outcome.rejected.append(
                RejectedSpan(index=index, span_id=_raw_span_id(raw), reason=normalized)
            )
            continue

        key = (normalized.trace_id, normalized.span_id)
        previous = accepted.get(key)
        if previous is not None:
            outcome.rejected.append(
                RejectedSpan(
                    index=previous[0],
                    span_id=normalized.span_id,
                    reason="duplicate span_id in batch; the later occurrence was kept",
                )
            )
        accepted[key] = (index, normalized)

    spans = [span for _, span in sorted(accepted.values(), key=lambda item: item[0])]
    if spans:
        await write_spans(session, target.project_id, spans, target.source_key_id)
        if any(span.trace.metadata for span in spans):
            feature_used("metadata_set")
    outcome.accepted = len(spans)
    outcome.rejected.sort(key=lambda rejection: rejection.index)
    return outcome


def _prepare(
    raw: Any, index: int, target: IngestTarget, prices: PriceBook, now: datetime
) -> NormalizedSpan | str:
    """The span ready to store, or the reason it is rejected.

    Any failure is this span's alone. One that is not a validation or semantic check is a bug:
    counted, and logged by its type, origin and the span's index, never its message, which may
    quote the payload.
    """
    try:
        validated = SpanIn.model_validate(raw)
        prepared = target.prepared_truncated
        capture = target.capture_payloads
        normalized = normalize_span(
            validated, capture_payloads=capture and prepared is None, prices=prices, now=now
        )
        if capture and prepared is not None:
            # Already redacted and cut by `prepare_span_payloads`: stored as they are.
            normalized = replace(
                normalized, input=validated.input, output=validated.output, truncated=prepared
            )
        return normalized
    except ValidationError as exc:
        return _first_error(exc)
    except SpanRejectedError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - a bug must cost this span, not the batch
        error_type = type(exc).__name__
        SPAN_NORMALIZATION_FAILURES.labels(error_type).inc()
        logger.error(
            "span_normalization_failed",
            project_id=str(target.project_id),
            index=index,
            error_type=error_type,
            origin=_origin(exc),
        )
        return _UNPROCESSABLE


def _origin(exc: BaseException) -> str:
    """`module:line` of the frame that raised `exc`: where the bug is, without its message."""
    frame = exc.__traceback__
    if frame is None:
        return "unknown"
    while frame.tb_next is not None:
        frame = frame.tb_next
    return f"{frame.tb_frame.f_globals.get('__name__', '?')}:{frame.tb_lineno}"


def _raw_span_id(raw: Any) -> str | None:
    if isinstance(raw, dict):
        span_id = raw.get("span_id")
        if isinstance(span_id, str):
            return span_id[:64]
    return None


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "span"
    return f"{location}: {error['msg']}"
