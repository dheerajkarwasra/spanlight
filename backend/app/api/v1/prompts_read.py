"""Prompt read by API key: `GET /v1/prompts/{name}`.

Authenticated like the ingestion routes: a project API key with the `prompts:read` scope, sharing
the per-key rate limit with them. Clients cache the answer: the `ETag` is the id of the version
served plus a hash of its labels, so a label that moves (onto another version, or onto or off
this one) changes it, and a matching `If-None-Match` gets an empty 304.
Unlike the dashboard routes, `label=latest` is accepted here as a read alias for the highest
version. Names travel as plain path segments (keys are not browsers, so no `~` escape).
"""

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from pydantic import Field

from app.api.deps import DbSession, require_key_scope
from app.api.principals import ApiKeyPrincipal
from app.api.schemas.common import ApiModel
from app.core.errors import ProblemError, not_found
from app.core.observability import PROMPT_READS
from app.core.ratelimit import INGEST_LIMITER, enforce_rate_limit
from app.core.scopes import KeyScope
from app.db.models import PromptKind, PromptVersion
from app.db.rls import bind_project
from app.prompts import queries

key_router = APIRouter(tags=["prompts"])

CACHE_CONTROL = "private, max-age=60"


class PromptReadOut(ApiModel):
    name: str
    version: int
    version_id: uuid.UUID
    kind: PromptKind
    template: dict[str, object]
    variables: list[str]
    # Wire name `model_config`; pydantic reserves it as an attribute name.
    advice: Annotated[dict[str, object], Field(serialization_alias="model_config")]
    labels: list[str]

    @classmethod
    def of(cls, name: str, version: PromptVersion, labels: list[str]) -> "PromptReadOut":
        return cls(
            name=name,
            version=version.version,
            version_id=version.id,
            kind=version.kind,
            template=version.template,
            variables=list(version.variables),
            advice=version.model_config,
            labels=labels,
        )


async def authenticate_prompt_read(
    request: Request,
    key: Annotated[ApiKeyPrincipal, Depends(require_key_scope(KeyScope.PROMPTS_READ))],
) -> ApiKeyPrincipal:
    """The key behind the request, after its scope is checked and the shared limit applied."""
    await enforce_rate_limit(
        request,
        INGEST_LIMITER,
        f"ingest:key:{key.key_id}",
        scope="ingest",
        detail="Request rate limit exceeded for this key.",
    )
    return key


def _etag(version_id: uuid.UUID, labels: list[str]) -> str:
    """The version id plus a short hash of its sorted labels: the body lists the labels, so
    moving a label onto or off the version must change the tag."""
    digest = hashlib.sha256("\n".join(sorted(labels)).encode()).hexdigest()[:12]
    return f'"{version_id}-{digest}"'


def _matches(if_none_match: str | None, etag: str) -> bool:
    """Whether the header lists this entity tag (weak or strong) or is the wildcard."""
    if if_none_match is None:
        return False
    candidates = [part.strip().removeprefix("W/") for part in if_none_match.split(",")]
    return "*" in candidates or etag in candidates


@key_router.get(
    "/v1/prompts/{name:path}",
    response_model=PromptReadOut,
    summary="Read a prompt version",
    responses={304: {"description": "The cached version is still the one served."}},
)
async def read_prompt(
    name: Annotated[str, Path(min_length=1, max_length=200)],
    key: Annotated[ApiKeyPrincipal, Depends(authenticate_prompt_read)],
    db: DbSession,
    response: Response,
    label: Annotated[str | None, Query(max_length=64)] = None,
    version: Annotated[int | None, Query(ge=1)] = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> PromptReadOut | Response:
    if label is not None and version is not None:
        raise ProblemError(422, "VALIDATION_ERROR", "Pass either label or version, not both.")
    await bind_project(db, key.project_id)
    found = await queries.resolve(db, key.project_id, name, label=label, version=version)
    if found is None:
        PROMPT_READS.labels(outcome="not_found").inc()
        raise not_found()
    rows = await queries.labels_of(db, [found.prompt_id])
    labels = [row.label for row in rows if row.version_id == found.id]
    headers = {"ETag": _etag(found.id, labels), "Cache-Control": CACHE_CONTROL}
    if _matches(if_none_match, headers["ETag"]):
        PROMPT_READS.labels(outcome="not_modified").inc()
        return Response(status_code=304, headers=headers)
    PROMPT_READS.labels(outcome="hit").inc()
    response.headers.update(headers)
    return PromptReadOut.of(name, found, labels)
