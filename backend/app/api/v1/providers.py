"""Provider health: calls and failures per provider, the error timeseries and sample failures.

The routes only authorize, parse and serialize; what the numbers mean, and which store they are
read from, is in `app.providers.service`. Every route needs `project:read`; no API key.
"""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import Access, ClockDep, DbSession, require
from app.api.schemas.providers import ErrorBucketOut, HealthReportOut, SamplesPage
from app.api.window import Window
from app.core.errors import FieldError, ProblemError
from app.core.observability import feature_used
from app.core.pagination import decode_cursor, encode_cursor
from app.core.permissions import Permission
from app.ingest.error_class import ErrorClass
from app.ingest.rate_limit_kind import RateLimitKind
from app.providers import service
from app.providers.sample_queries import SampleCursor, SampleFilters
from app.providers.series import Bucket

router = APIRouter(prefix="/projects/{project_id}/providers", tags=["providers"])

ProjectReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
EnvironmentFilter = Annotated[str | None, Query(max_length=64)]
ProviderFilter = Annotated[str | None, Query(max_length=64)]

# A sample cursor's tiebreaker is `<span_id>:<trace_id>`: a span id is unique only in its trace.
_SAMPLE_TIEBREAKER = re.compile(r"^([0-9a-f]{1,64}):([0-9a-f]{1,64})$")


@router.get("/health", response_model=HealthReportOut, summary="Provider health")
async def get_health(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    clock: ClockDep,
    environment: EnvironmentFilter = None,
) -> HealthReportOut:
    """llm calls, failures by error class, 429 kinds, retries and status page incidents per
    provider seen in the window, busiest first.

    `error_rate` is a decimal string, null without calls. `retry_cost_usd` is null when any trace
    of the provider has an unknown retry cost (`retry_cost_unknown`); `retry_cost_note` says how
    retries are attributed. `approximate` is true when whole hours were read from the hourly
    rollups.
    """
    project = access.require_project()
    answer = await service.health(db, project.id, window, environment, clock())
    feature_used("provider_health")
    return answer


@router.get(
    "/errors/timeseries", response_model=list[ErrorBucketOut], summary="Errors by class over time"
)
async def get_error_timeseries(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    clock: ClockDep,
    bucket: Bucket = "hour",
    environment: EnvironmentFilter = None,
    provider: ProviderFilter = None,
) -> list[ErrorBucketOut]:
    """Failed llm calls per UTC bucket and error class, one bucket per hour or day from the one
    holding `from` up to `to`; classes with no failures are absent.

    Part of the provider health page, so it does not count a use of its own.
    """
    project = access.require_project()
    return await service.timeseries(
        db,
        project.id,
        window,
        bucket=bucket,
        environment=environment,
        provider=provider,
        now=clock(),
    )


@router.get("/errors/samples", response_model=SamplesPage, summary="Sample failed calls")
async def get_error_samples(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    provider: ProviderFilter = None,
    error_class: ErrorClass | None = None,
    rate_limit_kind: RateLimitKind | None = None,
    environment: EnvironmentFilter = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> SamplesPage:
    """The newest failed llm calls, with their provider error fields, newest first.

    A window longer than 24 hours is cut to its last 24 hours and the answer says
    `clamped: true`. `422 VALIDATION_ERROR` for an unknown error class or rate-limit kind.
    """
    project = access.require_project()
    sampled, clamped = service.clamp_to_sample_window(window)
    filters = SampleFilters(
        environment=environment,
        provider=provider,
        error_class=None if error_class is None else error_class.value,
        rate_limit_kind=None if rate_limit_kind is None else rate_limit_kind.value,
    )
    after = _decode_sample_cursor(cursor) if cursor else None
    items, last = await service.samples(db, project.id, sampled, filters, limit=limit, after=after)
    feature_used("provider_errors_samples")
    next_cursor = (
        encode_cursor(last.started_at, f"{last.span_id}:{last.trace_id}") if last else None
    )
    return SamplesPage(items=items, next_cursor=next_cursor, clamped=clamped)


def _decode_sample_cursor(cursor: str) -> SampleCursor:
    started_at, tiebreaker = decode_cursor(cursor)
    match = _SAMPLE_TIEBREAKER.match(tiebreaker)
    if match is None:
        raise ProblemError(
            422,
            "VALIDATION_ERROR",
            "The pagination cursor is invalid.",
            errors=[FieldError(field="cursor", message="Invalid cursor")],
        )
    return SampleCursor(started_at, match.group(1), match.group(2))
