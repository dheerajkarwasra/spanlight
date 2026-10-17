"""Reads reconciliation shares between its two sides, and the reads of billing sources.

`mapping_patterns` is the one pattern set both sides group by (spec §7.3): the daily cost rollup
maps a span's model name with it, and the provider mappers map a line's model name with it, so a
model lands in the same group on both sides whether or not Spanlight could price it.

Billing sources are not under row-level security: every query below filters `org_id`. The
report's reads of a window take whole UTC days, `first` included and `end` excluded. A day's
provider lines are those of the latest successful run covering it (spec §13): a line a later
fetch no longer returns keeps its old `fetch_run_id` and drops out of the figures, though it stays
in the table. The rollup reads run in a session bound to the project (row-level security).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, User
from app.db.models.billing import (
    BillingFetchRun,
    BillingProvider,
    BillingSource,
    BillingSourceProject,
)
from app.end_users.days import ONE_DAY
from app.reconciliation.types import CostRollupRow, MappedLine

# The seed patterns of every snapshot, and the organization's own override patterns. Neither
# table is under row-level security; overrides are filtered by `org_id` here.
_MAPPING_PATTERNS = text(
    """
    SELECT model_pattern FROM model_prices
    UNION
    SELECT model_pattern FROM price_overrides WHERE org_id = :org_id
    ORDER BY 1
    """
)


async def mapping_patterns(db: AsyncSession, org_id: uuid.UUID) -> list[str]:
    """Every distinct seed pattern plus `org_id`'s live override patterns, sorted.

    `model_pattern_for(model, patterns)` then picks the longest one matching a model name.
    """
    return list((await db.execute(_MAPPING_PATTERNS, {"org_id": org_id})).scalars().all())


@dataclass(frozen=True)
class SourceView:
    """A source with what its API shape embeds: its mapped projects (by name) and its creator."""

    source: BillingSource
    projects: list[Project]
    creator: User | None


async def sources_for_org(db: AsyncSession, org_id: uuid.UUID) -> list[BillingSource]:
    """The organization's sources, by name. An organization has a handful: no pagination."""
    rows = await db.scalars(
        select(BillingSource).where(BillingSource.org_id == org_id).order_by(BillingSource.name)
    )
    return list(rows)


async def source_for_org(
    db: AsyncSession, org_id: uuid.UUID, source_id: uuid.UUID, *, lock: bool = False
) -> BillingSource | None:
    """The organization's source with this id; None for another organization's. `lock` takes
    the row `FOR UPDATE`, after the organization's write lock, and reloads a source the session
    already holds, so the caller decides on the row as it is under the lock."""
    query = select(BillingSource).where(
        BillingSource.org_id == org_id, BillingSource.id == source_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    found: BillingSource | None = await db.scalar(query)
    return found


async def projects_of_source(
    db: AsyncSession, org_id: uuid.UUID, source_id: uuid.UUID
) -> list[uuid.UUID]:
    """The ids of the projects the source is mapped to, sorted."""
    rows = await db.scalars(
        select(BillingSourceProject.project_id)
        .where(BillingSourceProject.org_id == org_id, BillingSourceProject.source_id == source_id)
        .order_by(BillingSourceProject.project_id)
    )
    return list(rows)


async def source_views(
    db: AsyncSession, org_id: uuid.UUID, sources: list[BillingSource]
) -> list[SourceView]:
    """Each source with its projects and creator, in the order given: two queries in all."""
    ids = [source.id for source in sources]
    mapped = await db.execute(
        select(BillingSourceProject.source_id, Project)
        .join(Project, Project.id == BillingSourceProject.project_id)
        .where(BillingSourceProject.org_id == org_id, BillingSourceProject.source_id.in_(ids))
        .order_by(Project.name, Project.id)
    )
    projects: dict[uuid.UUID, list[Project]] = {source_id: [] for source_id in ids}
    for source_id, project in mapped:
        projects[source_id].append(project)
    creator_ids = {source.created_by for source in sources if source.created_by is not None}
    creators = {
        user.id: user for user in await db.scalars(select(User).where(User.id.in_(creator_ids)))
    }
    return [
        SourceView(
            source=source,
            projects=projects[source.id],
            creator=creators.get(source.created_by) if source.created_by else None,
        )
        for source in sources
    ]


async def org_project_ids(
    db: AsyncSession, org_id: uuid.UUID, project_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Those of `project_ids` that are projects of the organization."""
    if not project_ids:
        return set()
    rows = await db.scalars(
        select(Project.id).where(Project.org_id == org_id, Project.id.in_(project_ids))
    )
    return set(rows)


async def source_names_taking_projects(
    db: AsyncSession,
    org_id: uuid.UUID,
    provider: BillingProvider,
    project_ids: list[uuid.UUID],
    *,
    except_source: uuid.UUID | None = None,
) -> dict[uuid.UUID, str]:
    """Project id → the name of the organization's other source of `provider` mapped to it."""
    if not project_ids:
        return {}
    query = (
        select(BillingSourceProject.project_id, BillingSource.name)
        .join(BillingSource, BillingSource.id == BillingSourceProject.source_id)
        .where(
            BillingSourceProject.org_id == org_id,
            BillingSourceProject.provider == provider,
            BillingSourceProject.project_id.in_(project_ids),
        )
    )
    if except_source is not None:
        query = query.where(BillingSourceProject.source_id != except_source)
    return {project_id: name for project_id, name in await db.execute(query)}


async def runs_page(
    db: AsyncSession,
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[tuple[BillingFetchRun, User | None]]:
    """The source's runs, newest first (`started_at desc, id desc`), with who asked for each.

    Up to `limit + 1` rows, so the caller can tell whether another page follows; `after` is the
    `(started_at, id)` of the last run of the previous page.
    """
    query = (
        select(BillingFetchRun, User)
        .outerjoin(User, User.id == BillingFetchRun.requested_by)
        .where(BillingFetchRun.org_id == org_id, BillingFetchRun.source_id == source_id)
        .order_by(BillingFetchRun.started_at.desc(), BillingFetchRun.id.desc())
        .limit(limit + 1)
    )
    if after is not None:
        query = query.where(tuple_(BillingFetchRun.started_at, BillingFetchRun.id) < tuple_(*after))
    return [(run, user) for run, user in await db.execute(query)]


# The latest successful run covering each day of the window, by finish time.
_LATEST_OK_RUNS = """
    SELECT d.day::date AS day, latest.id AS run_id, latest.finished_at
    FROM generate_series(CAST(:first AS date), CAST(:last AS date), interval '1 day') AS d(day)
    CROSS JOIN LATERAL (
        SELECT r.id, r.finished_at
        FROM billing_fetch_runs AS r
        WHERE r.org_id = :org_id AND r.source_id = :source_id AND r.status = 'ok'
          AND r.day_from <= d.day::date AND r.day_to >= d.day::date
        ORDER BY r.finished_at DESC NULLS LAST, r.id DESC
        LIMIT 1
    ) AS latest
"""
_LINES_OF_LATEST_RUNS = text(
    f"""
    WITH latest AS ({_LATEST_OK_RUNS})
    SELECT l.day, l.provider, l.line_key, l.line_kind, l.model_raw, l.model_pattern,
           l.token_type, l.service_tier, l.context_window, l.provider_scope, l.description,
           l.quantity, l.amount_usd
    FROM latest
    JOIN provider_cost_lines AS l
      ON l.org_id = :org_id AND l.source_id = :source_id
     AND l.day = latest.day AND l.fetch_run_id = latest.run_id
    ORDER BY l.day, l.line_key
    """
)
_ROLLUP_ROWS = text(
    """
    SELECT project_id, day, provider, provider_canonical, model, model_pattern, service_tier,
           calls, priced_calls, unpriced_calls, usage_unreported_calls, cut_stream_calls,
           cache_hit_calls, unmodelled_modifier_calls, uncached_input_tokens, cache_read_tokens,
           cache_write_5m_tokens, cache_write_1h_tokens, output_tokens, web_search_requests,
           cost_usd, fee_usd
    FROM cost_rollups_daily
    WHERE project_id = :project_id AND day >= :first AND day < :end
    ORDER BY day, provider, model, service_tier
    """
)
# A day with no rollup row and at least one LLM span; each EXISTS stops at its first row.
_DAYS_WITHOUT_ROLLUPS = text(
    """
    SELECT d.day::date AS day
    FROM generate_series(CAST(:first AS date), CAST(:last AS date), interval '1 day') AS d(day)
    WHERE NOT EXISTS (
        SELECT 1 FROM cost_rollups_daily AS c
        WHERE c.project_id = :project_id AND c.day = d.day::date
    )
      AND EXISTS (
        SELECT 1 FROM spans AS s
        WHERE s.project_id = :project_id AND s.kind = 'llm'
          AND s.started_at >= d.day AT TIME ZONE 'UTC'
          AND s.started_at < (d.day + interval '1 day') AT TIME ZONE 'UTC'
    )
    ORDER BY 1
    """
)


def _window(org_id: uuid.UUID, source_id: uuid.UUID, first: date, end: date) -> dict[str, object]:
    return {"org_id": org_id, "source_id": source_id, "first": first, "last": end - ONE_DAY}


async def lines_for_window(
    db: AsyncSession, org_id: uuid.UUID, source_id: uuid.UUID, first: date, end: date
) -> list[MappedLine]:
    """The source's lines of each day in `[first, end)` written by the latest ok run covering it."""
    result = await db.execute(_LINES_OF_LATEST_RUNS, _window(org_id, source_id, first, end))
    return [MappedLine(**row) for row in result.mappings()]


async def settled_runs_by_day(
    db: AsyncSession, org_id: uuid.UUID, source_id: uuid.UUID, first: date, end: date
) -> dict[date, datetime]:
    """When the latest ok run covering each day of `[first, end)` finished. A day no ok run
    covers is absent; the report calls a day settled from this time (`is_provisional`)."""
    result = await db.execute(text(_LATEST_OK_RUNS), _window(org_id, source_id, first, end))
    return {
        row.day: row.finished_at.astimezone(UTC) for row in result if row.finished_at is not None
    }


async def rollup_rows_for_project_window(
    db: AsyncSession, project_id: uuid.UUID, first: date, end: date
) -> list[CostRollupRow]:
    """The project's daily cost rollups of `[first, end)`. The session must be bound to it."""
    params = {"project_id": project_id, "first": first, "end": end}
    result = await db.execute(_ROLLUP_ROWS, params)
    return [CostRollupRow(**row) for row in result.mappings()]


async def days_without_rollups(
    db: AsyncSession, project_id: uuid.UUID, first: date, end: date
) -> list[date]:
    """The days of `[first, end)`, ascending, on which the project has LLM spans and no rollup
    row: the days no rollup pass has built. The session must be bound to the project."""
    params = {"project_id": project_id, "first": first, "last": end - ONE_DAY}
    return list((await db.execute(_DAYS_WITHOUT_ROLLUPS, params)).scalars())
