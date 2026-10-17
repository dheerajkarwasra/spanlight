"""A billing source's reconciliation report, as JSON and as CSV (spec §8.3).

Members read it (`org:read`). `from` and `to` are widened to whole UTC days, at most 31 of them,
ending today at the latest; by default the report covers the last 14 whole days. Building the
report may roll up days no pass has built (at most 31 project-days per request) and queue a
backfill for the rest, each project in a short transaction bound to it. Both responses are
marked uncacheable: they carry the organization's bill.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse

from app.api.deps import Access, ClockDep, DbSession, require
from app.api.schemas import ReconciliationOut
from app.api.window import TimeWindow
from app.core.errors import FieldError, ProblemError, not_found
from app.core.observability import feature_used
from app.core.permissions import Permission
from app.core.security import mark_uncacheable
from app.end_users.days import midnight
from app.reconciliation import queries
from app.reconciliation.report import ReportWindowInvalid, build_report
from app.reconciliation.report_csv import report_csv_rows, report_filename

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/orgs/{org_id}/billing-sources", tags=["billing-sources"])

OrgReader = Annotated[Access, Depends(require(Permission.ORG_READ))]

DEFAULT_REPORT_DAYS = 14
WINDOW_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"description": "The window is invalid (`VALIDATION_ERROR`)."}
}


def _invalid(field: str, message: str) -> ProblemError:
    return ProblemError(
        422,
        "VALIDATION_ERROR",
        "The request is invalid.",
        errors=[FieldError(field=field, message=message)],
    )


def _as_utc(value: datetime) -> datetime:
    # A time without an offset is UTC, as for every other window the API accepts.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def report_window(
    clock: ClockDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
) -> TimeWindow:
    """`from` and `to` as sent, or the last 14 whole UTC days before today. The report widens
    them to whole days and checks their length (`build_report`)."""
    try:
        end = _as_utc(to) if to else midnight(clock().astimezone(UTC).date())
        start = _as_utc(from_) if from_ else end - timedelta(days=DEFAULT_REPORT_DAYS)
    except OverflowError:
        raise _invalid("from", "outside the supported date range") from None
    if start >= end and to is None:
        # `to` defaults to the start of today, so the `from` sent asks for the future.
        raise _invalid("from", "must be earlier than today (UTC) when `to` is not sent")
    if start >= end:
        raise _invalid("from", "must be earlier than `to`")
    return TimeWindow(start=start, end=end)


ReportWindow = Annotated[TimeWindow, Depends(report_window)]


async def _report(
    db: DbSession, access: Access, source_id: uuid.UUID, window: TimeWindow, now: datetime
) -> ReconciliationOut:
    source = await queries.source_for_org(db, access.org.id, source_id)
    if source is None:
        raise not_found()
    try:
        report = await build_report(db, source, window, now=now)
    except ReportWindowInvalid as error:
        raise _invalid("to", error.message) from None
    logger.info(
        "reconciliation_report_built",
        org_id=str(access.org.id),
        source_id=str(source_id),
        days=len(report.days),
        pending_days=sum(1 for day in report.days if day.note is not None),
    )
    return report


@router.get(
    "/{source_id}/reconciliation",
    response_model=ReconciliationOut,
    responses=WINDOW_ERRORS,
    summary="Reconcile a billing source with Spanlight's figures",
)
async def get_reconciliation(
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    access: OrgReader,
    db: DbSession,
    clock: ClockDep,
    window: ReportWindow,
    response: Response,
) -> ReconciliationOut:
    """Compares the provider's bill with Spanlight's figures, per UTC day and model group.

    Spanlight's side covers the projects mapped to the source. `from` and `to` are widened to
    whole UTC days; the window is at most 31 days and ends today at the latest, and without them
    the report covers the last 14 whole days. Each day and group shows the provider's amount,
    Spanlight's amount, the explained lines and the signed residual (provider minus Spanlight
    minus explained). Credits and charges Spanlight cannot see are listed apart and never enter
    the residual. A day is provisional until 48 hours after it ends and until a successful fetch
    or upload that covers it finishes after that point.

    Errors: `422 VALIDATION_ERROR` on `to` when the window is longer than 31 days or ends after
    today (UTC), including a `from` later than the start of today with no `to`, and on `from`
    when `from` is not earlier than `to`.
    """
    report = await _report(db, access, source_id, window, clock())
    feature_used("reconciliation_report")
    mark_uncacheable(response)
    return report


@router.get(
    "/{source_id}/reconciliation.csv",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/csv": {}}, "description": "The report, one row per group."},
        **WINDOW_ERRORS,
    },
    summary="Download a billing source's reconciliation as CSV",
)
async def get_reconciliation_csv(
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    access: OrgReader,
    db: DbSession,
    clock: ClockDep,
    window: ReportWindow,
) -> StreamingResponse:
    """Returns the same report as `GET …/reconciliation` as a CSV file.

    The file has one row per day and model group. The window rules and errors are those of the
    JSON report.
    """
    report = await _report(db, access, source_id, window, clock())
    feature_used("reconciliation_csv")
    return StreamingResponse(
        report_csv_rows(report),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{report_filename(report)}"',
            "Cache-Control": "no-store",
        },
    )
