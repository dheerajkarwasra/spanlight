"""Score ingestion by API key: `POST /v1/scores`.

Authenticated like the span ingestion routes: a project API key with the `scores:write` scope,
sharing the per-key rate limit with them. Each score is checked on its own, so one bad score is
reported in `rejected` with its index and never fails the batch; only an empty or oversize batch
does (422). The write path is shared with the dashboard and the evaluators
(`app.scores.service.write_scores`).
"""

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text

from app.api.deps import DbSession, require_key_scope
from app.api.ingest import parse_ingest_json, read_ingest_body
from app.api.principals import ApiKeyPrincipal
from app.core.errors import FieldError, ProblemError, unauthorized
from app.core.ratelimit import INGEST_LIMITER, enforce_rate_limit
from app.core.scopes import KeyScope
from app.db.rls import bind_project
from app.scores.schemas import ScoreIn
from app.scores.service import write_scores
from app.scores.types import ScoreSource

router = APIRouter(tags=["scores"])
logger = structlog.get_logger(__name__)

MAX_SCORES_PER_BATCH = 1000

_KEY_SHARE_PROJECT = text("SELECT id FROM projects WHERE id = :project_id FOR KEY SHARE")


class ScoreBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Validated one by one below, so a malformed score is rejected alone.
    scores: Annotated[list[Any], Field(min_length=1, max_length=MAX_SCORES_PER_BATCH)]


class RejectedScoreOut(BaseModel):
    index: int
    reason: str


class ScoreIngestResponse(BaseModel):
    accepted: int
    rejected: list[RejectedScoreOut]


async def authenticate_score_ingest(
    request: Request,
    key: Annotated[ApiKeyPrincipal, Depends(require_key_scope(KeyScope.SCORES_WRITE))],
) -> ApiKeyPrincipal:
    """The key behind the request, after its scope is checked and the shared limit applied."""
    await enforce_rate_limit(
        request,
        INGEST_LIMITER,
        f"ingest:key:{key.key_id}",
        scope="ingest",
        detail="Ingestion rate limit exceeded for this key.",
    )
    return key


def _parse(raw: list[Any]) -> tuple[list[tuple[int, ScoreIn]], list[RejectedScoreOut]]:
    """The scores that fit the wire format, with their batch indexes, and those that do not."""
    valid: list[tuple[int, ScoreIn]] = []
    rejected: list[RejectedScoreOut] = []
    for index, item in enumerate(raw):
        try:
            valid.append((index, ScoreIn.model_validate(item)))
        except ValidationError as error:
            first = error.errors()[0]
            field = ".".join(str(part) for part in first["loc"]) or "score"
            rejected.append(RejectedScoreOut(index=index, reason=f"{field}: {first['msg']}"))
    return valid, rejected


def _batch(document: Any) -> ScoreBatchIn:
    """The parsed body as a batch; an empty, oversize or misshapen one is `422`."""
    try:
        return ScoreBatchIn.model_validate(document)
    except ValidationError as error:
        fields = [
            FieldError(
                field=".".join(str(part) for part in item["loc"]) or "body", message=item["msg"]
            )
            for item in error.errors()
        ]
        raise ProblemError(
            422,
            "VALIDATION_ERROR",
            "The body must be an object with a `scores` array.",
            errors=fields,
        ) from None


def _decode(body: bytes) -> tuple[list[tuple[int, ScoreIn]], list[RejectedScoreOut]]:
    """Parse and validate the body. CPU-bound, so it runs in a worker thread."""
    return _parse(_batch(parse_ingest_json(body)).scores)


@router.post(
    "/v1/scores",
    response_model=ScoreIngestResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": ScoreBatchIn.model_json_schema()}},
        }
    },
)
async def ingest_scores(
    request: Request,
    key: Annotated[ApiKeyPrincipal, Depends(authenticate_score_ingest)],
    db: DbSession,
) -> ScoreIngestResponse:
    """Store a batch of 1 to 1000 scores. The body is read after the key is checked, bounded
    like span ingestion and may be gzip or deflate encoded."""
    valid, rejected = await run_in_threadpool(_decode, await read_ingest_body(request))
    await bind_project(db, key.project_id)
    project = await db.execute(_KEY_SHARE_PROJECT, {"project_id": key.project_id})
    if project.scalar_one_or_none() is None:  # deleted after the key was checked
        raise unauthorized("Missing, invalid or revoked API key.")
    outcome = await write_scores(
        db, key.project_id, [score for _, score in valid], source=ScoreSource.API
    )
    await db.commit()
    rejected.extend(
        RejectedScoreOut(index=valid[item.index][0], reason=item.reason)
        for item in outcome.rejected
    )
    rejected.sort(key=lambda item: item.index)
    return ScoreIngestResponse(accepted=outcome.accepted, rejected=rejected)
