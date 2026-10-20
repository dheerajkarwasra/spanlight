"""Creating and reading trace exports. The routes are thin wrappers over these functions."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.attribution.service import check_request
from app.config import Settings
from app.core.errors import conflict, not_configured
from app.core.observability import feature_used
from app.core.pagination import decode_uuid_cursor, encode_cursor
from app.db.models import Export, ExportFormat, ExportKind, ExportStatus, Project
from app.exports.schemas import (
    AttributionExportFilters,
    AttributionExportIn,
    ExportCreateIn,
    ExportFilters,
    ExportOut,
)
from app.jobs.queue import enqueue
from app.storage.object_store import ObjectStore, get_object_store

CREATE_EXPORT_JOB = "create_export"
# More matching traces than this and the export fails with `EXPORT_TOO_LARGE`.
MAX_EXPORT_TRACES = 100_000
# How long after completion the file is kept, and how long a download link works.
EXPORT_RETENTION = timedelta(days=7)
DOWNLOAD_URL_LIFETIME_SECONDS = 3600
# A failed attempt is retried, so a transient storage error does not fail the export. Each
# attempt writes the file again under a key of its own (see `storage_key`), so two attempts that
# overlap never touch each other's file.
EXPORT_JOB_ATTEMPTS = 3
# Exports one project may have queued or running at once. Each can read 100,000 traces and
# write a file to object storage, so an unbounded loop of requests would flood both.
MAX_EXPORTS_IN_PROGRESS = 5

FORMAT_EXTENSIONS: dict[ExportFormat, str] = {ExportFormat.JSONL: "jsonl", ExportFormat.CSV: "csv"}
CONTENT_TYPES: dict[ExportFormat, str] = {
    ExportFormat.JSONL: "application/x-ndjson",
    ExportFormat.CSV: "text/csv; charset=utf-8",
}


def export_prefix(project_id: uuid.UUID, export_id: uuid.UUID) -> str:
    """The key prefix shared by every object any attempt of this export wrote."""
    return f"exports/{project_id}/{export_id}"


def storage_key(
    project_id: uuid.UUID, export_id: uuid.UUID, export_format: ExportFormat, attempt: int
) -> str:
    """The key one attempt of the export job writes to.

    `attempt` is the job's fencing token, which changes every time a worker claims the job. Two
    attempts that overlap (a lease taken over from a worker that is still running) therefore never
    share a key, so a worker can delete its own object without touching the other's. The
    export row's `storage_key` names the one that won.
    """
    return f"{export_prefix(project_id, export_id)}-{attempt}.{FORMAT_EXTENSIONS[export_format]}"


def require_object_store(settings: Settings) -> ObjectStore:
    """The object store, or 409 `NOT_CONFIGURED` naming the setting that turns it on."""
    store = get_object_store(settings)
    if store is None:
        raise not_configured(
            "Exports need object storage: set S3_BUCKET, S3_ACCESS_KEY and S3_SECRET_KEY "
            "(and S3_ENDPOINT for a service other than AWS S3)."
        )
    return store


async def create_export(
    db: AsyncSession, *, project: Project, user_id: uuid.UUID, body: ExportCreateIn
) -> Export:
    """Insert a queued export and its job in one transaction. The caller commits.

    An attribution request is checked as the attribution read would check it (422 before
    anything is queued). A project with `MAX_EXPORTS_IN_PROGRESS` exports queued or running is
    refused with 409 `TOO_MANY_EXPORTS`. The session must already be bound to the project
    (row-level security checks the insert).
    """
    project_id = project.id
    if isinstance(body, AttributionExportIn):
        window = TimeWindow(body.filters.from_, body.filters.to)
        check_request(body.group_by, body.key, window, project.promoted_metadata_keys)
        feature_used("attribution_export")
    # Serializes concurrent requests for the project, so the count below cannot be raced past.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"exports:{project_id}"},
    )
    in_progress = await db.scalar(
        select(func.count())
        .select_from(Export)
        .where(
            Export.project_id == project_id,
            Export.status.in_((ExportStatus.QUEUED, ExportStatus.RUNNING)),
        )
    )
    if in_progress is not None and in_progress >= MAX_EXPORTS_IN_PROGRESS:
        raise conflict(
            "TOO_MANY_EXPORTS",
            f"This project already has {MAX_EXPORTS_IN_PROGRESS} exports in progress. "
            "Wait for one to finish, then try again.",
        )
    export = Export(
        project_id=project_id,
        kind=body.kind,
        format=body.format,
        filters=body.stored(),
        status=ExportStatus.QUEUED,
        created_by=user_id,
    )
    db.add(export)
    await db.flush()
    await enqueue(
        db,
        CREATE_EXPORT_JOB,
        {"export_id": str(export.id), "project_id": str(project_id)},
        dedupe_key=f"{CREATE_EXPORT_JOB}:{export.id}",
        max_attempts=EXPORT_JOB_ATTEMPTS,
    )
    await db.refresh(export)
    return export


async def list_exports(
    db: AsyncSession, project_id: uuid.UUID, *, limit: int, cursor: str | None
) -> tuple[list[Export], str | None]:
    """One page of the project's exports, newest first, and the cursor for the next page."""
    query = (
        select(Export)
        .where(Export.project_id == project_id)
        .order_by(Export.created_at.desc(), Export.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        created_at, export_id = decode_uuid_cursor(cursor)
        query = query.where(
            or_(
                Export.created_at < created_at,
                and_(Export.created_at == created_at, Export.id < export_id),
            )
        )
    rows = list((await db.scalars(query)).all())
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1].created_at, str(page[-1].id)) if len(rows) > limit else None
    )
    return page, next_cursor


async def get_export(
    db: AsyncSession, project_id: uuid.UUID, export_id: uuid.UUID
) -> Export | None:
    return await db.scalar(
        select(Export).where(Export.id == export_id, Export.project_id == project_id)
    )


def export_out(export: Export, store: ObjectStore | None) -> ExportOut:
    """The API shape. A download link exists only for a `done` export that has not expired yet."""
    download_url = None
    if (
        export.status is ExportStatus.DONE
        and export.storage_key is not None
        and export.expires_at is not None
        and export.expires_at > datetime.now(UTC)
        and store is not None
    ):
        download_url = store.presigned_get_url(export.storage_key, DOWNLOAD_URL_LIFETIME_SECONDS)
    attribution = export.kind is ExportKind.ATTRIBUTION
    filters: ExportFilters | AttributionExportFilters = (
        AttributionExportFilters.model_validate(export.filters["filters"])
        if attribution
        else ExportFilters.model_validate(export.filters)
    )
    return ExportOut(
        id=export.id,
        kind=export.kind,
        format=export.format,
        filters=filters,
        group_by=export.filters["group_by"] if attribution else None,
        key=export.filters["key"] if attribution else None,
        status=export.status,
        row_count=export.row_count,
        size_bytes=export.size_bytes,
        error_code=export.error_code,
        created_at=export.created_at,
        completed_at=export.completed_at,
        expires_at=export.expires_at,
        download_url=download_url,
    )
