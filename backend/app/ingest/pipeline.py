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
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import without_nul
from app.db.rls import bind_project
from app.ingest.jsondepth import MAX_JSON_DEPTH, exceeds_depth
from app.ingest.normalize import NormalizedSpan, SpanRejectedError, normalize_span
from app.ingest.schemas import RejectedSpan, SpanIn
from app.ingest.store import write_spans
from app.pricing.cost import load_price_book


@dataclass(frozen=True)
class IngestTarget:
    project_id: uuid.UUID
    capture_payloads: bool
    org_id: uuid.UUID  # whose price overrides price the spans
    # The gateway key the calls came through. Only the gateway sets it; native and OTLP
    # ingestion leave it None, since a project API key is not a gateway key.
    source_key_id: uuid.UUID | None = None


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
    """Parse JSON strictly (no NaN/Infinity) and strip NUL characters.

    Postgres rejects both in jsonb and text columns, and one bad value would
    otherwise abort the whole batch's transaction.
    """

    def reject_constant(name: str) -> Any:
        raise InvalidJsonError(f"{name} is not valid JSON")

    try:
        parsed = json.loads(body, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidJsonError("request body is not valid JSON") from exc
    except RecursionError as exc:  # nested deeper than the parser can follow
        raise InvalidJsonError(_TOO_DEEP) from exc
    if exceeds_depth(parsed):
        raise InvalidJsonError(_TOO_DEEP)
    return without_nul(parsed)


_TOO_DEEP = f"request body is nested more than {MAX_JSON_DEPTH} levels deep"


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
        try:
            validated = SpanIn.model_validate(raw)
            normalized = normalize_span(
                validated, capture_payloads=target.capture_payloads, prices=prices, now=now
            )
        except ValidationError as exc:
            outcome.rejected.append(
                RejectedSpan(index=index, span_id=_raw_span_id(raw), reason=_first_error(exc))
            )
            continue
        except SpanRejectedError as exc:
            outcome.rejected.append(
                RejectedSpan(index=index, span_id=_raw_span_id(raw), reason=str(exc))
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
    outcome.accepted = len(spans)
    outcome.rejected.sort(key=lambda rejection: rejection.index)
    return outcome


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
