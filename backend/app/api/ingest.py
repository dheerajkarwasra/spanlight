"""Telemetry ingestion: the native format and OTLP/HTTP (JSON and protobuf).

Authenticated with a project API key (`Authorization: Bearer spl_live_…`) that has the
`ingest:write` scope, rate limited per key, and bounded in body size and span count.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.deps import DbSession, require_key_scope
from app.api.principals import ApiKeyPrincipal
from app.core.body import (
    BodyTooLarge,
    ClientDisconnected,
    UndecodableBody,
    UnsupportedEncoding,
    read_bounded_body,
)
from app.core.errors import ProblemError, unauthorized
from app.core.observability import INGESTED_SPANS, OTLP_CONVENTION_SPANS, REJECTED_SPANS
from app.core.ratelimit import INGEST_LIMITER, enforce_rate_limit
from app.core.scopes import KeyScope
from app.db.models import Project
from app.ingest import otlp
from app.ingest.otlp_values import OtlpDecodeError
from app.ingest.pipeline import (
    IngestOutcome,
    IngestTarget,
    InvalidJsonError,
    ProjectGoneError,
    ingest_spans,
    parse_json_body,
)
from app.ingest.schemas import (
    MAX_BODY_BYTES,
    MAX_SPANS_PER_BATCH,
    ClientCountersIn,
    IngestBatchIn,
    IngestResponse,
)
from app.telemetry_losses.service import (
    check_client,
    record_ingest_rejections,
    record_sdk_counters,
)

router = APIRouter(tags=["ingest"])
logger = structlog.get_logger(__name__)

PROTOBUF_CONTENT_TYPE = "application/x-protobuf"


async def authenticate_ingest(
    request: Request,
    key: Annotated[ApiKeyPrincipal, Depends(require_key_scope(KeyScope.INGEST_WRITE))],
) -> ApiKeyPrincipal:
    """The key behind an ingest request, after its scope is checked and its rate limit applied.

    The limit comes last, so a key that may not ingest at all does not spend tokens on it.
    """
    await enforce_rate_limit(
        request,
        INGEST_LIMITER,
        f"ingest:key:{key.key_id}",
        scope="ingest",
        detail="Ingestion rate limit exceeded for this key.",
    )
    return key


AuthenticatedKey = Annotated[ApiKeyPrincipal, Depends(authenticate_ingest)]


def _payload_too_large() -> ProblemError:
    return ProblemError(
        413, "PAYLOAD_TOO_LARGE", f"Request bodies are limited to {MAX_BODY_BYTES} bytes."
    )


async def read_ingest_body(request: Request) -> bytes:
    """The body (decompressed), at most MAX_BODY_BYTES, with failures as problem+json."""
    try:
        return await read_bounded_body(request, MAX_BODY_BYTES)
    except BodyTooLarge as exc:
        raise _payload_too_large() from exc
    except UnsupportedEncoding as exc:
        raise ProblemError(
            415, "UNSUPPORTED_MEDIA_TYPE", f"Unsupported content-encoding {exc.encoding!r}."
        ) from exc
    except UndecodableBody as exc:
        raise ProblemError(
            400, "BAD_REQUEST", "The request body could not be decompressed."
        ) from exc
    except ClientDisconnected as exc:
        # Nobody reads this answer; it keeps a client that left from being logged as our error.
        raise ProblemError(400, "BAD_REQUEST", "The request body was not fully sent.") from exc


def _check_span_count(count: int) -> None:
    if count > MAX_SPANS_PER_BATCH:
        raise ProblemError(
            413,
            "PAYLOAD_TOO_LARGE",
            f"A batch may contain at most {MAX_SPANS_PER_BATCH} spans.",
        )


def parse_ingest_json(body: bytes) -> Any:
    """The body parsed strictly as JSON, with a parse failure as `400 INVALID_JSON`."""
    try:
        return parse_json_body(body)
    except InvalidJsonError as exc:
        raise ProblemError(400, "INVALID_JSON", str(exc)) from exc


async def _record_losses(
    db: DbSession,
    project_id: uuid.UUID,
    outcome: IngestOutcome,
    counters: ClientCountersIn | None,
) -> None:
    """The batch's rejections and the SDK's counter increases, in the batch's transaction.

    Each statement runs only when it has something to write: a clean batch without counters
    costs nothing.
    """
    now = datetime.now(UTC)
    if outcome.rejected:
        await record_ingest_rejections(db, project_id, outcome.rejected, now)
    if counters is not None and counters.dropped_total + counters.failed_total > 0:
        await record_sdk_counters(db, project_id, counters, now)


async def _store(
    db: DbSession,
    key: ApiKeyPrincipal,
    raw_spans: list[Any],
    source: str,
    client: Any = None,
) -> IngestOutcome:
    counters, client_ignored = check_client(client)
    project = (
        await db.execute(
            select(Project.capture_payloads, Project.org_id).where(Project.id == key.project_id)
        )
    ).one_or_none()
    if project is None:  # the project was deleted after the key was checked
        raise unauthorized("Missing, invalid or revoked API key.")
    target = IngestTarget(
        project_id=key.project_id, capture_payloads=project.capture_payloads, org_id=project.org_id
    )
    try:
        outcome = await ingest_spans(db, target, raw_spans)
    except ProjectGoneError:  # deleted while this request waited for the project's lock
        raise unauthorized("Missing, invalid or revoked API key.") from None
    await _record_losses(db, key.project_id, outcome, counters)
    await db.commit()

    INGESTED_SPANS.labels(source).inc(outcome.accepted)
    REJECTED_SPANS.labels(source).inc(len(outcome.rejected))
    # An ignored `client` object is named by its failing fields only, never its values.
    extra = {"client_ignored": client_ignored} if client_ignored else {}
    logger.info(
        "spans_ingested",
        source=source,
        project_id=str(key.project_id),
        accepted=outcome.accepted,
        rejected=len(outcome.rejected),
        **extra,
    )
    return outcome


@router.post("/v1/traces", response_model=IngestResponse)
async def ingest_native(request: Request, key: AuthenticatedKey, db: DbSession) -> IngestResponse:
    document = parse_ingest_json(await read_ingest_body(request))
    try:
        batch = IngestBatchIn.model_validate(document)
    except ValueError as exc:
        raise ProblemError(
            422, "VALIDATION_ERROR", "The body must be an object with a `spans` array."
        ) from exc
    _check_span_count(len(batch.spans))

    outcome = await _store(db, key, batch.spans, source="native", client=batch.client)
    return IngestResponse(accepted=outcome.accepted, rejected=outcome.rejected)


@router.post("/v1/otlp/traces")
async def ingest_otlp(request: Request, key: AuthenticatedKey, db: DbSession) -> Response:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    body = await read_ingest_body(request)

    try:
        if content_type == PROTOBUF_CONTENT_TYPE:
            spans = otlp.decode_protobuf(body)
        elif content_type == "application/json":
            spans = otlp.decode_json(parse_ingest_json(body))
        else:
            raise ProblemError(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                "Use application/json or application/x-protobuf.",
            )
    except OtlpDecodeError as exc:
        raise ProblemError(400, "INVALID_OTLP", str(exc)) from exc
    _check_span_count(len(spans))

    native_spans, dropped = otlp.to_native_spans(spans)
    if dropped.total:
        # Counts only: the values themselves may be long and are the client's data.
        logger.warning(
            "otlp_values_dropped",
            project_id=str(key.project_id),
            spans=len(spans),
            environment=dropped.environment,
            release=dropped.release,
            token_counts=dropped.token_counts,
            metadata=dropped.metadata,
            ttft=dropped.ttft,
            messages=dropped.messages,
        )
    outcome = await _store(db, key, native_spans, source="otlp")
    rejected_indexes = {rejection.index for rejection in outcome.rejected}
    for convention, count in otlp.count_conventions(native_spans, rejected_indexes).items():
        OTLP_CONVENTION_SPANS.labels(convention=convention).inc(count)
    rejected = len(outcome.rejected)
    notes = [f"span {r.span_id}: {r.reason}" for r in outcome.rejected[:5]]
    if warning := dropped.metadata_warning():
        notes.append(warning)
    message = "; ".join(notes) or None

    if content_type == PROTOBUF_CONTENT_TYPE:
        return Response(
            otlp.encode_protobuf_response(rejected, message), media_type=PROTOBUF_CONTENT_TYPE
        )
    return JSONResponse(otlp.json_response(rejected, message))
