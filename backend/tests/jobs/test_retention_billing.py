"""Retention of billing data and of the daily cost rollups.

Provider cost lines and fetch runs are kept `PROVIDER_LINES_RETENTION_DAYS` (400) days whatever a
project's own retention is: an invoice outlives the traces it is compared with. Lines go first
(a line restricts the deletion of the run that wrote it); a run goes once it is past the cutoff
and no line still points at it. `cost_rollups_daily` is derived from the spans, so its days go
with each project's retention, like `user_stats_daily`.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from app.config import Settings
from app.db.rls import bypass_rls
from app.jobs.context import TaskContext
from app.jobs.queue import ClaimedJob
from app.jobs.tasks import retention
from app.jobs.tasks.retention import apply_daily_table_retention, run_retention
from app.reconciliation import retention as billing_retention
from app.reconciliation.retention import PROVIDER_LINES_RETENTION_DAYS, apply_billing_retention
from tests.jobs.bypass_support import (
    SessionFactory,
    execute,
    fetch,
    new_projects,
    seed_llm_spans,
)

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


async def _org_of(session_factory: SessionFactory, project_id: uuid.UUID) -> uuid.UUID:
    [row] = await fetch(
        session_factory, "SELECT org_id FROM projects WHERE id = :id", {"id": project_id}
    )
    org_id: uuid.UUID = row["org_id"]
    return org_id


async def _source(session_factory: SessionFactory, org_id: uuid.UUID) -> uuid.UUID:
    source_id = uuid.uuid4()
    await execute(
        session_factory,
        "INSERT INTO billing_sources (id, org_id, kind, provider, name) "
        "VALUES (:id, :org_id, 'csv_upload', 'anthropic', :name)",
        {"id": source_id, "org_id": org_id, "name": f"source-{source_id.hex[:8]}"},
    )
    return source_id


async def _run(
    session_factory: SessionFactory, source_id: uuid.UUID, org_id: uuid.UUID, started_at: datetime
) -> uuid.UUID:
    run_id = uuid.uuid4()
    day = started_at.date()
    await execute(
        session_factory,
        "INSERT INTO billing_fetch_runs (id, source_id, org_id, trigger, day_from, day_to, "
        "status, started_at, finished_at) "
        "VALUES (:id, :source_id, :org_id, 'csv_upload', :day, :day, 'ok', :started_at, "
        ":started_at)",
        {
            "id": run_id,
            "source_id": source_id,
            "org_id": org_id,
            "day": day,
            "started_at": started_at,
        },
    )
    return run_id


async def _line(
    session_factory: SessionFactory,
    source_id: uuid.UUID,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    day: date,
) -> uuid.UUID:
    line_id = uuid.uuid4()
    await execute(
        session_factory,
        "INSERT INTO provider_cost_lines (id, source_id, org_id, fetch_run_id, day, provider, "
        "line_key, line_kind, description, amount_usd, first_seen_at, last_seen_at) "
        "VALUES (:id, :source_id, :org_id, :run_id, :day, 'anthropic', :line_key, 'tokens', "
        "'line', 1.25, now(), now())",
        {
            "id": line_id,
            "source_id": source_id,
            "org_id": org_id,
            "run_id": run_id,
            "day": day,
            "line_key": (line_id.hex * 2),
        },
    )
    return line_id


async def _ids(session_factory: SessionFactory, table: str) -> set[uuid.UUID]:
    return {row["id"] for row in await fetch(session_factory, f"SELECT id FROM {table}")}


async def _rollup(session_factory: SessionFactory, project_id: uuid.UUID, day: date) -> None:
    await execute(
        session_factory,
        "INSERT INTO cost_rollups_daily (project_id, day, calls, priced_calls, unpriced_calls, "
        "usage_unreported_calls, cut_stream_calls, cache_hit_calls, unmodelled_modifier_calls, "
        "uncached_input_tokens, cache_read_tokens, cache_write_5m_tokens, "
        "cache_write_1h_tokens, output_tokens, web_search_requests) "
        "VALUES (:project_id, :day, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)",
        {"project_id": project_id, "day": day},
    )


async def test_lines_and_runs_older_than_400_days_are_purged_in_batches(
    session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two rows per batch, so each delete needs several batches to finish.
    monkeypatch.setattr(billing_retention, "BATCH_SIZE", 2)
    [project] = await new_projects(session_factory, 1)
    org_id = await _org_of(session_factory, project)
    source = await _source(session_factory, org_id)
    old_at, recent_at = NOW - timedelta(days=401), NOW - timedelta(days=399)
    old_day, recent_day = old_at.date(), recent_at.date()

    gone_run = await _run(session_factory, source, org_id, old_at)
    for _ in range(3):
        await _line(session_factory, source, org_id, gone_run, old_day)
    await _run(session_factory, source, org_id, old_at)  # an old run without lines
    # An old run that still wrote a recent line stays until that line goes.
    kept_old_run = await _run(session_factory, source, org_id, old_at)
    await _line(session_factory, source, org_id, kept_old_run, old_day)
    kept_by_old_run = await _line(session_factory, source, org_id, kept_old_run, recent_day)
    recent_run = await _run(session_factory, source, org_id, recent_at)
    recent_line = await _line(session_factory, source, org_id, recent_run, recent_day)

    deleted = await apply_billing_retention(session_factory, now=NOW)

    assert PROVIDER_LINES_RETENTION_DAYS == 400
    assert deleted == (4, 2)
    assert await _ids(session_factory, "provider_cost_lines") == {kept_by_old_run, recent_line}
    assert await _ids(session_factory, "billing_fetch_runs") == {kept_old_run, recent_run}


async def test_cost_rollups_purged_at_project_cutoff(session_factory: SessionFactory) -> None:
    short, long = await new_projects(session_factory, 2)
    await execute(
        session_factory, "UPDATE projects SET retention_days = 7 WHERE id = :id", {"id": short}
    )
    await execute(
        session_factory, "UPDATE projects SET retention_days = 30 WHERE id = :id", {"id": long}
    )
    # The cutoff day itself stays: spans of that day may still be behind it.
    for project, days in ((short, 7), (long, 30)):
        cutoff_day = (NOW - timedelta(days=days)).date()
        await _rollup(session_factory, project, cutoff_day - timedelta(days=1))
        await _rollup(session_factory, project, cutoff_day)
    await _rollup(session_factory, long, (NOW - timedelta(days=8)).date())

    deleted = await apply_daily_table_retention(session_factory, now=NOW)

    assert (deleted.user_stats, deleted.cost_rollups, deleted.attribution_rollups) == (0, 2, 0)
    rows = await fetch(
        session_factory, "SELECT project_id, day FROM cost_rollups_daily ORDER BY project_id, day"
    )
    assert {(row["project_id"], row["day"]) for row in rows} == {
        (short, (NOW - timedelta(days=7)).date()),
        (long, (NOW - timedelta(days=30)).date()),
        (long, (NOW - timedelta(days=8)).date()),
    }


async def _attribution_row(
    session_factory: SessionFactory, project_id: uuid.UUID, day: date
) -> None:
    await execute(
        session_factory,
        "INSERT INTO attribution_rollups_daily (project_id, day, group_kind, bucket, group_value, "
        "traces, llm_calls, errors, unpriced_calls, input_tokens, output_tokens) "
        "VALUES (:project_id, :day, 'trace_name', 'value', 'answer', 1, 1, 0, 0, 1, 1)",
        {"project_id": project_id, "day": day},
    )


async def test_retention_deletes_attribution_rollups_before_the_cutoff(
    session_factory: SessionFactory,
) -> None:
    short, long = await new_projects(session_factory, 2)
    await execute(
        session_factory, "UPDATE projects SET retention_days = 7 WHERE id = :id", {"id": short}
    )
    await execute(
        session_factory, "UPDATE projects SET retention_days = 30 WHERE id = :id", {"id": long}
    )
    # The cutoff day itself stays: traces of that day may still be behind it.
    for project, days in ((short, 7), (long, 30)):
        cutoff_day = (NOW - timedelta(days=days)).date()
        await _attribution_row(session_factory, project, cutoff_day - timedelta(days=1))
        await _attribution_row(session_factory, project, cutoff_day)
    await _attribution_row(session_factory, long, (NOW - timedelta(days=8)).date())

    deleted = await apply_daily_table_retention(session_factory, now=NOW)

    assert (deleted.user_stats, deleted.cost_rollups, deleted.attribution_rollups) == (0, 0, 2)
    rows = await fetch(session_factory, "SELECT project_id, day FROM attribution_rollups_daily")
    assert {(row["project_id"], row["day"]) for row in rows} == {
        (short, (NOW - timedelta(days=7)).date()),
        (long, (NOW - timedelta(days=30)).date()),
        (long, (NOW - timedelta(days=8)).date()),
    }


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))


async def test_billing_retention_is_independent_of_span_retention(
    session_factory: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(retention, "logger", recorder)
    now = datetime.now(UTC)
    [project] = await new_projects(session_factory, 1)
    await execute(
        session_factory, "UPDATE projects SET retention_days = 7 WHERE id = :id", {"id": project}
    )
    await seed_llm_spans(session_factory, project, 2, started_at=now - timedelta(days=10))
    org_id = await _org_of(session_factory, project)
    source = await _source(session_factory, org_id)
    month_old = now - timedelta(days=30)
    run = await _run(session_factory, source, org_id, month_old)
    line = await _line(session_factory, source, org_id, run, month_old.date())
    await _rollup(session_factory, project, month_old.date())
    old = now - timedelta(days=PROVIDER_LINES_RETENTION_DAYS + 1)
    old_run = await _run(session_factory, source, org_id, old)
    await _line(session_factory, source, org_id, old_run, old.date())
    job = ClaimedJob(id=1, kind="retention", payload={}, attempts=1, max_attempts=1, fence=1)
    context = TaskContext(job=job, settings=settings, session_factory=session_factory)

    await run_retention(context, {})

    # The traces and the rollups went with the project's 7 days; the provider data did not.
    assert await fetch(session_factory, "SELECT trace_id FROM traces") == []
    assert await fetch(session_factory, "SELECT id FROM cost_rollups_daily") == []
    assert await _ids(session_factory, "provider_cost_lines") == {line}
    assert await _ids(session_factory, "billing_fetch_runs") == {run}
    [(event, fields)] = recorder.events
    assert event == "retention_applied"
    assert fields["traces_deleted"] == 2
    assert fields["cost_rollups_deleted"] == 1
    assert fields["attribution_rollups_deleted"] == 0
    assert fields["provider_lines_deleted"] == 1
    assert fields["fetch_runs_deleted"] == 1


async def test_user_stats_batch_deletes_batch_size_rows_across_environments(
    session_factory: SessionFactory,
) -> None:
    """One user's old day in three environments (one of them none): a batch of 2 deletes 2 rows,
    not every environment row of the keys it picked, and retention still takes all three."""
    (project,) = await new_projects(session_factory, 1)
    old_day = (NOW - timedelta(days=60)).date()
    for environment in ("prod", "staging", None):
        await execute(
            session_factory,
            "INSERT INTO user_stats_daily (project_id, day, environment, external_user_id, "
            "traces, llm_calls, errors, unpriced_calls, tokens, computed_at) "
            "VALUES (:project_id, :day, :environment, 'u1', 1, 1, 0, 0, 1, now())",
            {"project_id": project, "day": old_day, "environment": environment},
        )
    params = {"project_id": project, "cutoff": NOW - timedelta(days=30), "batch_size": 2}
    async with session_factory() as db:
        await bypass_rls(db)
        result = await db.execute(retention.DELETE_USER_STATS, params)
        await db.commit()
    assert getattr(result, "rowcount", None) == 2

    deleted = await apply_daily_table_retention(session_factory, now=NOW)
    assert deleted.user_stats == 1
    assert await fetch(session_factory, "SELECT 1 FROM user_stats_daily") == []
