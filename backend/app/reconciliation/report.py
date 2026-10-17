"""The reconciliation report: one billing source's bill against Spanlight's figures (§7.5, §8.3).

The window is widened to whole UTC days, at most `MAX_REPORT_DAYS` of them, ending today at the
latest. The reads come in short transactions: one filtered by the organization (the mapped
projects, the lines of the latest successful run covering each day and when that run finished),
then one per mapped project, bound to it, that builds the rollups no pass has built yet and reads
the project's rollups of the window. Each day is then reconciled by `reconcile_day`.

Both sides are grouped by the pattern their model maps to under the organization's patterns as
they are now (`mapping_patterns`, read once per report). The `model_pattern` stored on a line and
on a rollup row is a cache of that mapping from when it was written: a line keeps it until it is
fetched or uploaded again, a rollup row until the next refresh, so after an override is created or
deleted the stored values of the two sides can disagree, and grouping by them would split one
model into a group the provider billed and a group Spanlight recorded.

Lazy build (§10.4): a day on which a project has LLM spans and no rollup row is rebuilt inline,
at most `LAZY_BUILD_MAX_PROJECT_DAYS` project-days per request, so one request never scans more
than a month of one project's spans. Each project's remaining days are queued as one
`rollup_cost_daily_backfill` job (deduplicated per project, window and UTC date of the request,
so a backfill that failed is queued again the next day), and those days have no Spanlight side
yet: `spanlight_usd null, incomplete true, note "rollup pending"`.

Unknown is never 0. A day no successful run covers has no provider side: its provider figures,
the explained lines computed from provider amounts and the residual are null, and the lines it
keeps carry no USD (whether the provider bills priority calls is unknown too). A day whose rollups
are pending has no Spanlight side: its Spanlight figures, explained lines and residual are null.
A day with neither provider lines nor Spanlight rows is left out.
"""

import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence, Set
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.reconciliation import (
    ReconciliationOut,
    ReportDayOut,
    ReportGroupOut,
    ReportProjectOut,
    ReportSourceOut,
    ReportWindowOut,
)
from app.api.window import TimeWindow
from app.db.models import Project
from app.db.models.billing import BillingSource
from app.db.rls import bind_project
from app.end_users.days import ONE_DAY, day_bounds, midnight
from app.jobs.queue import enqueue
from app.reconciliation import queries
from app.reconciliation.arithmetic import SETTLE_LAG, GroupReconciliation, reconcile_day
from app.reconciliation.backfill import BACKFILL_ATTEMPTS, BACKFILL_JOB, backfill_payload
from app.reconciliation.explained import ExplainedKind, provider_unknown
from app.reconciliation.line_items import model_pattern_for
from app.reconciliation.report_out import charges_out, explained_out, summarize, tokens_out
from app.reconciliation.rollups import refresh_cost_rollups
from app.reconciliation.types import CostRollupRow, MappedLine

MAX_REPORT_DAYS = 31
LAZY_BUILD_MAX_PROJECT_DAYS = 31
ROLLUP_PENDING_NOTE = "rollup pending"
# Explained lines computed from the provider's amounts: unknown while no run covers the day.
_PROVIDER_KINDS: frozenset[ExplainedKind] = frozenset(
    {"batch_spend", "server_tool_fees", "untraced_traffic"}
)
_ZERO = Decimal(0)


class ReportWindowInvalid(Exception):  # noqa: N818 - reads as the outcome, like errors.py
    """The window is longer than `MAX_REPORT_DAYS` whole days, or ends after today (UTC)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class _SpanlightSide:
    rows: list[CostRollupRow] = field(default_factory=list)
    pending: set[date] = field(default_factory=set)


@dataclass(frozen=True)
class _Context:
    provider: str
    now: datetime
    fetched: Mapping[date, datetime]
    pending: Set[date]
    cutoffs: Mapping[uuid.UUID, date]


def report_days(window: TimeWindow, now: datetime) -> tuple[date, date]:
    """The window as whole UTC days: the first, and the day after the last."""
    first, end = day_bounds(window)
    if (end - first).days > MAX_REPORT_DAYS:
        raise ReportWindowInvalid(f"the window may span at most {MAX_REPORT_DAYS} whole UTC days")
    if end > now.astimezone(UTC).date() + ONE_DAY:
        raise ReportWindowInvalid("the window may not end after today (UTC)")
    return first, end


def first_retained_day(retention_days: int, now: datetime) -> date:
    """The first UTC day all of whose spans are still kept: retention deletes the spans that
    started before `now - retention_days`, so the day holding that moment is already partial."""
    cutoff = now.astimezone(UTC) - timedelta(days=retention_days)
    return cutoff.date() if cutoff.time() == time(0) else cutoff.date() + ONE_DAY


async def build_report(
    db: AsyncSession, source: BillingSource, window: TimeWindow, *, now: datetime
) -> ReconciliationOut:
    """The source's report over `window`. Raises `ReportWindowInvalid`; commits as it goes."""
    first, end = report_days(window, now)
    org_id, source_id = source.org_id, source.id
    [view] = await queries.source_views(db, org_id, [source])
    lines = await queries.lines_for_window(db, org_id, source_id, first, end)
    fetched = await queries.settled_runs_by_day(db, org_id, source_id, first, end)
    patterns = await queries.mapping_patterns(db, org_id)
    await db.commit()
    side = await _spanlight_side(db, view.projects, first, end, now)
    context = _Context(
        provider=source.provider,
        now=now,
        fetched=fetched,
        pending=side.pending,
        cutoffs={p.id: first_retained_day(p.retention_days, now) for p in view.projects},
    )
    lines_by_day = _by_day(_remapped_lines(lines, patterns))
    rows_by_day = _by_day(_remapped_rows(side.rows, patterns))
    days = [
        entry
        for day in _each_day(first, end)
        if (entry := _day(day, lines_by_day.get(day, []), rows_by_day.get(day, []), context))
    ]
    return ReconciliationOut(
        source=ReportSourceOut(id=source_id, provider=source.provider, kind=source.kind),
        window=ReportWindowOut(start=midnight(first), end=midnight(end)),
        projects=[ReportProjectOut(id=p.id, name=p.name) for p in view.projects],
        days=days,
        summary=summarize(days),
    )


def _remapped_lines(lines: Iterable[MappedLine], patterns: Sequence[str]) -> list[MappedLine]:
    """The lines with the pattern their model maps to now, not the one stored with them."""
    return [
        replace(line, model_pattern=model_pattern_for(line.model_raw, patterns)) for line in lines
    ]


def _remapped_rows(rows: Iterable[CostRollupRow], patterns: Sequence[str]) -> list[CostRollupRow]:
    """The rollup rows with the pattern their model maps to now (the lines' rule)."""
    return [replace(row, model_pattern=model_pattern_for(row.model, patterns)) for row in rows]


async def _spanlight_side(
    db: AsyncSession, projects: Sequence[Project], first: date, end: date, now: datetime
) -> _SpanlightSide:
    """Each project's rollups of the window, in one transaction bound to it; missing days are
    built inline while the request's budget lasts, and queued past it."""
    side = _SpanlightSide()
    budget = LAZY_BUILD_MAX_PROJECT_DAYS
    for project in projects:
        await bind_project(db, project.id)
        missing = await queries.days_without_rollups(db, project.id, first, end)
        built, later = missing[:budget], missing[budget:]
        budget -= len(built)
        for stretch in _stretches(built):
            await refresh_cost_rollups(db, project.id, stretch)
        if later:
            await _queue_backfill(db, project.id, later, now)
            side.pending.update(later)
        side.rows.extend(await queries.rollup_rows_for_project_window(db, project.id, first, end))
        await db.commit()
    return side


async def _queue_backfill(
    db: AsyncSession, project_id: uuid.UUID, days: Sequence[date], now: datetime
) -> None:
    """One backfill of the project from its first to its last pending day (at most 31 days).

    The key ends with the request's UTC date: a key is kept until cleanup prunes its job, so
    without the date a backfill that failed would block its window for a week.
    """
    day_from, day_to = days[0], days[-1]
    today = now.astimezone(UTC).date()
    await enqueue(
        db,
        BACKFILL_JOB,
        backfill_payload(project_id, day_from, day_to),
        dedupe_key=f"{BACKFILL_JOB}:{project_id}:{day_from.isoformat()}:{day_to.isoformat()}:"
        f"{today.isoformat()}",
        max_attempts=BACKFILL_ATTEMPTS,
    )


def _stretches(days: Sequence[date]) -> list[list[date]]:
    """Ascending days as runs of consecutive days: each is refreshed alone, so a gap between
    two missing days is not recomputed."""
    stretches: list[list[date]] = []
    for day in days:
        if stretches and day - stretches[-1][-1] == ONE_DAY:
            stretches[-1].append(day)
        else:
            stretches.append([day])
    return stretches


def _day(
    day: date, lines: list[MappedLine], rows: list[CostRollupRow], context: _Context
) -> ReportDayOut | None:
    fetched_at = context.fetched.get(day)
    pending = day in context.pending
    settled = fetched_at is not None and fetched_at >= midnight(day + ONE_DAY) + SETTLE_LAG
    spanlight = [] if pending else rows
    result = reconcile_day(
        day, context.provider, lines, spanlight, context.now, settled, context.cutoffs
    )
    if not pending and not (
        result.groups or result.credits or result.other or result.unattributed_calls
    ):
        return None
    provider_known, spanlight_known = fetched_at is not None, not pending
    both = provider_known and spanlight_known
    groups = [_group(group, provider_known, spanlight_known) for group in result.groups]
    return ReportDayOut(
        day=day,
        provisional=result.provisional,
        fetched_at=fetched_at,
        incomplete=result.incomplete or pending,
        note=ROLLUP_PENDING_NOTE if pending else None,
        provider_usd=result.provider_usd if provider_known else None,
        spanlight_usd=result.spanlight_usd if spanlight_known else None,
        explained_usd=_sum(g.explained_usd for g in groups) if both else None,
        residual_usd=result.residual_usd if both else None,
        variance_ratio=result.variance_ratio if both else None,
        credits_usd=result.credits_usd if provider_known else None,
        other_usd=result.other_usd if provider_known else None,
        unattributed_calls=result.unattributed_calls,
        groups=groups,
        credits=charges_out(result.credits),
        other=charges_out(result.other),
    )


def _group(
    group: GroupReconciliation, provider_known: bool, spanlight_known: bool
) -> ReportGroupOut:
    both = provider_known and spanlight_known
    kept = [
        line if provider_known else provider_unknown(line)
        for line in group.explained
        if spanlight_known and (provider_known or line.kind not in _PROVIDER_KINDS)
    ]
    return ReportGroupOut(
        group=group.group,
        model_pattern=group.model_pattern,
        provider_models=list(group.provider_models),
        spanlight_models=list(group.spanlight_models),
        provider_usd=group.provider_usd if provider_known else None,
        spanlight_usd=group.spanlight_usd if spanlight_known else None,
        explained_usd=_sum(line.usd for line in kept) if both else None,
        residual_usd=group.residual_usd if both else None,
        variance_ratio=group.variance_ratio if both else None,
        provider_tokens=tokens_out(group.provider_tokens if provider_known else None),
        spanlight_tokens=tokens_out(group.spanlight_tokens if spanlight_known else None),
        explained=[explained_out(line) for line in kept],
        # "No provider line" is only true of a day the provider's side was read for.
        note=group.note if provider_known else None,
    )


def _by_day[T: (MappedLine, CostRollupRow)](items: Iterable[T]) -> dict[date, list[T]]:
    grouped: dict[date, list[T]] = {}
    for item in items:
        grouped.setdefault(item.day, []).append(item)
    return grouped


def _each_day(first: date, end: date) -> Iterator[date]:
    day = first
    while day < end:
        yield day
        day += ONE_DAY


def _sum(values: Iterable[Decimal | None]) -> Decimal:
    return sum((value for value in values if value is not None), _ZERO)
