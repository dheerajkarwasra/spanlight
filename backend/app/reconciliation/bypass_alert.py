"""The bypass alert (hard gateway budgets design, D10), the last step of a successful fetch.

For each closed UTC day of the fetched window whose report shows an unexplained residual
(provider USD minus Spanlight USD minus explained lines) above 10 % of the bill and above $1, the
organization's owners and admins get one email naming the likely causes: calls made outside the
gateway and the SDK, a project the source is not mapped to, or a leaked provider key. A day
whose Spanlight side is unknown (rollups pending, source history unavailable) has no residual and
is never reported.

Once per source and day: each row's `summary.digest_key` is `billing_bypass:<source>:<day>`,
checked under the digest's advisory lock through the outbox's key index (named for its first
user, the weekly digest), so the next day's fetch of the same seven days mails nothing twice.
That rests on settled outbox rows outliving the refetch window: `cleanup.FINISHED_OUTBOX_RETENTION`
(7 days) must stay at or above `fetch_policy.REFETCH_DAYS` (7), which a test pins. The same
day's report shows the day as `bypass_suspected` for the in-app view.

The check runs last in the fetch job, after the rollup refresh, and must end before the worker's
task timeout: it gets what is left of the rollups' hard stop (`fetch_steps.ROLLUP_HARD_STOP_S`,
measured from the job's start) and is skipped when nothing is left. A timeout or any other
failure here is logged and counted, never raised: the fetch is stored and the run is `ok`, so a
failed job would only fetch the provider again, and the next fetch covers the same days anyway.
"""

import asyncio
import time
import uuid
from datetime import UTC
from decimal import Decimal

import structlog

from app.alerts.digest_queries import digest_already_queued, lock_digest
from app.alerts.formatting import format_money
from app.alerts.subject import subject_safe
from app.api.schemas.reconciliation import ReconciliationOut, ReportDayOut
from app.api.window import TimeWindow
from app.core.decimals import decimal_to_str
from app.core.observability import BILLING_BYPASS_CHECKS
from app.db.models import Organization
from app.db.models.billing import BillingSource
from app.email.message import EmailMessage
from app.email.templates import _compose
from app.end_users.days import ONE_DAY, midnight
from app.jobs.context import TaskContext
from app.notifications.outbox import NotificationKind, enqueue
from app.reconciliation.fetch_policy import ChildRequest
from app.reconciliation.fetch_steps import ROLLUP_HARD_STOP_S
from app.reconciliation.job_queries import admin_recipients
from app.reconciliation.report import build_report

logger = structlog.get_logger(__name__)

EVENT = "billing_bypass"
# Seconds from the job's start by which the check must be over (the rollups' hard stop).
HARD_STOP_S = ROLLUP_HARD_STOP_S
PROVIDER_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "google": "Google"}
CAUSES = (
    "Likely causes: calls made outside the gateway and the SDK, a project this source is not "
    "mapped to, or a leaked provider key. Check the provider's usage by key, map every project "
    "that uses this account, and rotate the key if you cannot account for the spend."
)


def bypass_key(source_id: uuid.UUID, day: ReportDayOut) -> str:
    return f"{EVENT}:{source_id}:{day.day.isoformat()}"


def render_bypass_email(
    source: BillingSource, org_name: str, day: ReportDayOut, url: str
) -> EmailMessage:
    """The notice for one day; the caller fills in each recipient. Only a day whose provider,
    explained and residual figures are known can be a bypass, so none may be missing."""
    provider, explained, residual = day.provider_usd, day.explained_usd, day.residual_usd
    if provider is None or explained is None or residual is None:
        raise ValueError("a bypass notice needs the day's provider, explained and residual")
    # A known residual with no Spanlight figure means the mapped projects had no priced calls.
    recorded = (
        "no priced calls"
        if day.spanlight_usd is None
        else f"{format_money(day.spanlight_usd)} of calls"
    )
    share = (residual / provider * 100).quantize(Decimal(1)) if provider else Decimal(0)
    when = f"{day.day:%b} {day.day.day} (UTC)"
    return _compose(
        subject=subject_safe(
            f"{format_money(residual)} of provider spend unexplained on {source.name}, {when}"
        ),
        paragraphs=[
            f"{PROVIDER_NAMES.get(source.provider, source.provider)} billed "
            f"{format_money(provider)} for {when} on the "
            f"billing source {source.name}. Spanlight recorded {recorded} and known causes "
            f"explain {format_money(explained)}; {format_money(residual)} ({share} %) is "
            "unexplained.",
            CAUSES,
            ("Open the reconciliation report:", url),
            f"You get this email as an admin of {org_name}.",
        ],
    )


def _report_url(app_base_url: str, org_id: uuid.UUID, report: ReconciliationOut) -> str:
    """The source's reconciliation page, under its first mapped project; the app's root when
    the source maps no project (the report then shows every billed dollar as unexplained)."""
    base = app_base_url.rstrip("/")
    if not report.projects:
        return base
    return f"{base}/{org_id}/{report.projects[0].id}/settings/billing-sources/{report.source.id}"


async def notify_bypass(
    context: TaskContext, source: BillingSource, request: ChildRequest, started: float
) -> None:
    """Mail the admins about every closed day of `request`'s window with an unexplained residual
    not yet reported, within what is left of the job's time since `started` (monotonic). Never
    raises: the fetch is stored and the next one covers the days again."""
    remaining = HARD_STOP_S - (time.monotonic() - started)
    if remaining <= 0:
        logger.warning("billing_bypass_check_skipped", source_id=str(source.id))
        BILLING_BYPASS_CHECKS.labels(outcome="skipped").inc()
        return
    try:
        async with asyncio.timeout(remaining):
            queued = await _notify(context, source, request)
    except TimeoutError:
        logger.warning("billing_bypass_check_timeout", source_id=str(source.id), budget_s=remaining)
        BILLING_BYPASS_CHECKS.labels(outcome="timeout").inc()
        return
    except Exception as exc:  # see the module docstring
        logger.error("billing_bypass_check_failed", source_id=str(source.id), exc_info=exc)
        BILLING_BYPASS_CHECKS.labels(outcome="failed").inc()
        return
    BILLING_BYPASS_CHECKS.labels(outcome="ok").inc()
    if queued:
        logger.info("billing_bypass_notified", source_id=str(source.id), rows=queued)


async def _notify(context: TaskContext, source: BillingSource, request: ChildRequest) -> int:
    now = context.clock()
    today = now.astimezone(UTC).date()
    window = TimeWindow(start=midnight(request.day_from), end=midnight(request.day_to) + ONE_DAY)
    queued = 0
    async with context.session_factory() as db:
        report = await build_report(db, source, window, now=now)
        suspected = [day for day in report.days if day.bypass_suspected and day.day < today]
        if not suspected:
            return 0
        org = await db.get(Organization, source.org_id)
        recipients = await admin_recipients(db, source.org_id)
        if org is None or not recipients:
            return 0
        url = _report_url(context.settings.app_base_url, source.org_id, report)
        for day in suspected:
            key = bypass_key(source.id, day)
            await lock_digest(db, key)
            # A notice for a day is only ever queued after that day closed.
            if await digest_already_queued(db, key, midnight(day.day)):
                await db.rollback()
                continue
            message = render_bypass_email(source, org.name, day, url)
            summary = {
                "event": EVENT,
                "digest_key": key,
                "source_id": str(source.id),
                "day": day.day.isoformat(),
                "provider_usd": _money(day.provider_usd),
                "spanlight_usd": _money(day.spanlight_usd),
                "residual_usd": _money(day.residual_usd),
            }
            for address in recipients:
                await enqueue(
                    db,
                    NotificationKind.EMAIL,
                    {"to": address},
                    {
                        "subject": message.subject,
                        "text": message.text,
                        "html": message.html,
                        "summary": summary,
                    },
                )
            await db.commit()
            queued += len(recipients)
    return queued


def _money(value: Decimal | None) -> str | None:
    return None if value is None else decimal_to_str(value)
