"""A billing source's runs: refresh it now, upload its CSV, list what was fetched (spec §8.2).

Admins and owners refresh and upload (`billing_sources:manage`); members list the runs. A
refresh is throttled per source (one every 5 minutes, bursts of 2) and refused while a run of the
source is in progress, so a dashboard cannot hammer a provider's cost API. An upload is one
multipart `file` of at most 5 MB, six an hour per source, parsed whole before anything is
written; its bytes are never stored or logged, and an error names a row and a column, never a
cell.
"""

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from app.api.deps import Access, ClockDep, DbSession, SettingsDep, client_ip, require
from app.api.schemas import Page, UserOut
from app.api.schemas.billing_sources import BillingRunOut, CsvUploadOut, RefreshAccepted
from app.api.v1.billing_source_problems import NEEDS_KEYS
from app.core.crypto import CryptoNotConfigured
from app.core.errors import FieldError, ProblemError, conflict, not_configured, not_found
from app.core.observability import feature_used
from app.core.pagination import decode_uuid_cursor, encode_cursor
from app.core.permissions import Permission
from app.core.ratelimit import BILLING_REFRESH_LIMITER, BILLING_UPLOAD_LIMITER, enforce_rate_limit
from app.core.security import mark_uncacheable
from app.db.models import User
from app.db.models.billing import BillingFetchRun
from app.reconciliation import queries, source_actions
from app.reconciliation.csv_import import MAX_UPLOAD_BYTES
from app.reconciliation.errors import (
    BillingSourceNotFound,
    CsvRejected,
    NotApiKind,
    NotCsvKind,
    ProviderCallsOff,
    SourceDisabled,
)
from app.reconciliation.runs import RunInProgress

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/orgs/{org_id}/billing-sources", tags=["billing-sources"])

OrgReader = Annotated[Access, Depends(require(Permission.ORG_READ))]
SourceManager = Annotated[Access, Depends(require(Permission.BILLING_SOURCES_MANAGE))]
RunsLimit = Annotated[int, Query(ge=1, le=100)]

ACTION_ERRORS = (
    BillingSourceNotFound,
    NotApiKind,
    NotCsvKind,
    SourceDisabled,
    ProviderCallsOff,
    CryptoNotConfigured,
    RunInProgress,
    CsvRejected,
)
PROVIDER_CALLS_OFF = (
    "BILLING_SOURCES_ENABLED is off, so this server calls no provider's cost API. Ask your "
    "administrator to turn it on, or add a CSV source."
)
# The request body the upload reads itself (after authorization, and only as multipart), as the
# OpenAPI document should describe it.
UPLOAD_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "string",
                            "format": "binary",
                            "description": "The cost CSV, UTF-8, at most 5 MB.",
                        }
                    },
                }
            }
        },
    }
}


def _invalid(field: str, message: str) -> ProblemError:
    return ProblemError(
        422,
        "VALIDATION_ERROR",
        "The request is invalid.",
        errors=[FieldError(field=field, message=message)],
    )


def _action_problem(error: Exception) -> ProblemError:
    """The problem response for an error a refresh or an upload raises."""
    if isinstance(error, BillingSourceNotFound):
        return not_found()
    if isinstance(error, NotApiKind):
        return _invalid("kind", "a CSV source is updated by uploading a file, not refreshed")
    if isinstance(error, NotCsvKind):
        return _invalid("kind", "only a CSV source takes an uploaded file")
    if isinstance(error, SourceDisabled):
        return conflict(
            "BILLING_SOURCE_DISABLED",
            "This billing source is turned off. Turn it on to refresh it.",
        )
    if isinstance(error, ProviderCallsOff):
        return not_configured(PROVIDER_CALLS_OFF)
    if isinstance(error, CryptoNotConfigured):
        return not_configured(NEEDS_KEYS)
    if isinstance(error, RunInProgress):
        return conflict(
            "BILLING_FETCH_IN_PROGRESS",
            "A fetch of this billing source is in progress. Try again when it has finished.",
        )
    if isinstance(error, CsvRejected):
        return ProblemError(
            422,
            "VALIDATION_ERROR",
            "The file is not a valid cost CSV; nothing was stored.",
            errors=[
                FieldError(field="file", row=e.row, column=e.column, message=e.message)
                for e in error.errors
            ],
        )
    raise error


async def _read_file(request: Request) -> bytes:
    """The bytes of the multipart field `file`: 415 for another body, 413 past 5 MB."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "multipart/form-data":
        raise ProblemError(
            415,
            "UNSUPPORTED_MEDIA_TYPE",
            "Send the CSV as multipart/form-data, in a field named file.",
        )
    try:
        # File parts are spooled to disk past 1 MB; only the read below holds the bytes.
        form = await request.form(max_files=1, max_fields=10)
    except HTTPException as exc:
        if exc.status_code != 400:  # the API body limit's 413 passes through
            raise
        raise _invalid("file", "the body is not a readable multipart form") from None
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise _invalid("file", "attach the CSV in a field named file")
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await form.close()
    if len(data) > MAX_UPLOAD_BYTES:
        raise ProblemError(413, "PAYLOAD_TOO_LARGE", "The file is larger than 5 MB.")
    return data


def _run_out(run: BillingFetchRun, requester: User | None) -> BillingRunOut:
    return BillingRunOut(
        id=run.id,
        trigger=run.trigger,
        day_from=run.day_from,
        day_to=run.day_to,
        status=run.status,
        error=run.error,
        lines_upserted=run.lines_upserted,
        pages=run.pages,
        requested_by=UserOut.model_validate(requester) if requester else None,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.post(
    "/{source_id}/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RefreshAccepted,
    summary="Fetch a billing source now",
)
async def refresh_source(
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    request: Request,
    access: SourceManager,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> RefreshAccepted:
    """Queues a fetch of the provider's cost report and answers `202` with the new run's id.

    The fetch covers the last seven whole UTC days, or the last 31 while the source has never
    been fetched successfully. Follow the run with `GET …/runs`.

    Errors: `422 VALIDATION_ERROR` on `kind` for a CSV source. `409 BILLING_SOURCE_DISABLED` when
    the source is turned off. `409 NOT_CONFIGURED` when `BILLING_SOURCES_ENABLED` is off or
    `CREDENTIALS_KEYS` is not set. `429 RATE_LIMITED` with `Retry-After` past the limit of one
    refresh per source every 5 minutes, with a burst of 2. `409 BILLING_FETCH_IN_PROGRESS`
    while another run of the source is in progress; this refusal still counts towards the limit.
    """
    try:
        source = await source_actions.check_refresh(db, access.org.id, source_id, settings=settings)
        await enforce_rate_limit(
            request,
            BILLING_REFRESH_LIMITER,
            f"billing_refresh:{source.id}",
            scope="billing_refresh",
            detail="This billing source was refreshed recently. Try again after Retry-After.",
        )
        run = await source_actions.start_refresh(
            db, source, access.user_id, now=clock(), ip=client_ip(request)
        )
    except ACTION_ERRORS as error:
        raise _action_problem(error) from None
    run_id = run.id
    await db.commit()
    feature_used("billing_source_refresh")
    logger.info(
        "billing_source_refresh_queued",
        org_id=str(access.org.id),
        source_id=str(source_id),
        run_id=str(run_id),
    )
    return RefreshAccepted(run_id=run_id)


@router.post(
    "/{source_id}/upload",
    response_model=CsvUploadOut,
    summary="Upload a billing source's cost CSV",
    openapi_extra=UPLOAD_BODY,
)
async def upload_csv(
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    request: Request,
    access: SourceManager,
    db: DbSession,
    clock: ClockDep,
) -> CsvUploadOut:
    """Stores the lines of a cost CSV as one run of a CSV source.

    Send the file as multipart/form-data in a field named `file`: UTF-8, at most 5 MB and 100,000
    data rows, with a header that lists every column of the CSV format. The whole file is checked
    before anything is stored. The run covers every day from the file's first day to its last,
    and for those days the report reads only this file's lines, so a file must hold every line of
    each day it covers. A line already stored for its day takes the new figure. Every day must lie
    in the 400 days up to today (UTC), today included. A source that is turned off still accepts
    uploads.

    Errors: `422 VALIDATION_ERROR` on `kind` for a source that fetches from a provider. `415
    UNSUPPORTED_MEDIA_TYPE` when the body is not multipart/form-data. `413 PAYLOAD_TOO_LARGE` over
    5 MB. `429 RATE_LIMITED` with `Retry-After` past six uploads of the source in an hour; a
    refused file counts. `409 BILLING_FETCH_IN_PROGRESS` while another run of the source is in
    progress. `422 VALIDATION_ERROR` with up to 20 `errors[]` of `{field: "file", row, column,
    message}` when the file is refused, in which case nothing is stored.
    """
    try:
        source = await source_actions.csv_source(db, access.org.id, source_id)
        # End the transaction of the checks before the client streams the file, so a slow upload
        # holds no connection; the upload re-reads the source under its locks.
        await db.commit()
        await enforce_rate_limit(
            request,
            BILLING_UPLOAD_LIMITER,
            f"billing_upload:{source.id}",
            scope="billing_upload",
            detail="This billing source received too many uploads. Try again after Retry-After.",
        )
        data = await _read_file(request)
        uploaded = await source_actions.upload_csv(
            db, source, data, access.user_id, now=clock(), ip=client_ip(request)
        )
    except ACTION_ERRORS as error:
        raise _action_problem(error) from None
    await db.commit()
    await source_actions.refresh_upload_rollups(db, uploaded)
    feature_used("billing_csv_upload")
    logger.info(
        "billing_csv_uploaded",
        org_id=str(access.org.id),
        source_id=str(source_id),
        run_id=str(uploaded.run_id),
        lines=uploaded.lines_upserted,
        days=len(uploaded.days),
    )
    return CsvUploadOut(
        run_id=uploaded.run_id, lines_upserted=uploaded.lines_upserted, days=uploaded.days
    )


@router.get("/{source_id}/runs", response_model=Page[BillingRunOut], summary="List a source's runs")
async def list_runs(
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    access: OrgReader,
    db: DbSession,
    response: Response,
    limit: RunsLimit = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[BillingRunOut]:
    """Lists the source's fetches and uploads, newest first by start time.

    Keyset pagination: `limit` from 1 to 100 (default 20), and `cursor` from the previous page's
    `next_cursor`. A failed run's `error` is an HTTP status line or a short fixed phrase, never a
    provider's response body.
    """
    after = decode_uuid_cursor(cursor) if cursor else None
    if await queries.source_for_org(db, access.org.id, source_id) is None:
        raise not_found()
    rows = await queries.runs_page(db, access.org.id, source_id, after=after, limit=limit)
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1][0]
        next_cursor = encode_cursor(last.started_at, str(last.id))
    mark_uncacheable(response)
    return Page(items=[_run_out(run, user) for run, user in page], next_cursor=next_cursor)
