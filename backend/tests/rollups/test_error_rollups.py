"""The hourly error rollups: written by `compute_rollups`, backfilled by the job, read back.

Failure modes pinned here: an error rollup that disagrees with a raw count of the same spans;
fault-injected spans counted as provider errors; a re-sent span leaving its old group behind; a
project whose span rollups are complete never getting its error history (Postgres `least`
ignores NULL, so an empty error table looked like no gap); a history without failures re-scanned
on every run; the reader mixing providers or dimensions it was told to filter.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.rollups.compute import floor_hour
from app.rollups.error_queries import ErrorHour, read_error_rollups
from app.rollups.filtered import RollupFilters
from app.rollups.jobs import roll_up_all
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects
from tests.rollups.error_support import FailedSpan, compute, seed_spans

_ROLLUP_ROWS = (
    "SELECT bucket_start, environment, provider, model, error_class, rate_limit_kind, errors, "
    "with_retry_after FROM error_rollups_hourly WHERE project_id = :p"
)
# Written by hand from the rollup's definition, independently of the statement under test.
_RAW_GROUPS = """
    SELECT date_trunc('hour', s.started_at, 'UTC') AS bucket_start, t.environment, s.provider,
           s.model, s.error_class, s.rate_limit_kind, count(*) AS errors,
           count(*) FILTER (
               WHERE jsonb_typeof(s.attributes -> 'spanlight.gateway.retry_after_s') = 'number'
                  OR jsonb_typeof(s.attributes -> 'provider.retry_after_s') = 'number'
           ) AS with_retry_after
    FROM spans AS s
    JOIN traces AS t ON t.project_id = s.project_id AND t.trace_id = s.trace_id
    WHERE s.project_id = :p AND s.kind = 'llm' AND s.status = 'error'
      AND s.started_at >= :start AND s.started_at < :end
    GROUP BY 1, 2, 3, 4, 5, 6
"""
# The third is what the gateway stores when the provider sent no Retry-After: the key, as null.
_RETRY_AFTER = (
    {"spanlight.gateway.retry_after_s": 2},
    {"provider.retry_after_s": 1.5},
    {"spanlight.gateway.retry_after_s": None, "provider.retry_after_s": None},
    {},
)
_CLASSES = ("rate_limit", "provider_5xx", "timeout")


def _as_set(rows: list[dict[str, Any]]) -> set[tuple[Any, ...]]:
    return {tuple(row.values()) for row in rows}


def _failed(index: int, base: datetime) -> FailedSpan:
    provider = ("openai", "anthropic")[index % 2]
    error_class = _CLASSES[index % 3]
    return FailedSpan(
        started_at=base + timedelta(hours=index % 3, minutes=index % 50),
        provider=provider,
        model=f"{provider}-{index % 4 // 2}",
        environment=None if index % 5 == 0 else "production",
        error_class=error_class,
        rate_limit_kind=("requests", "tokens")[index // 3 % 2]
        if error_class == "rate_limit"
        else None,
        attributes=_RETRY_AFTER[index % 4],
    )


async def test_error_rollup_aggregates_match_a_raw_query(session_factory: SessionFactory) -> None:
    (project,) = await new_projects(session_factory, 1)
    base = floor_hour(datetime.now(UTC)) - timedelta(hours=6)
    failed = [_failed(index, base) for index in range(60)]
    ok = [
        FailedSpan(started_at=base + timedelta(hours=i % 3), status="ok", error_class=None)
        for i in range(40)
    ]
    # Failed spans that are not llm calls, and a failed call outside the range, are not counted.
    tools = [FailedSpan(started_at=base, kind="tool", error_class="timeout") for _ in range(5)]
    outside = [FailedSpan(started_at=base + timedelta(hours=3))]
    await seed_spans(session_factory, project, failed + ok + tools + outside)

    await compute(session_factory, project, base, base + timedelta(hours=3))

    params = {"p": project, "start": base, "end": base + timedelta(hours=3)}
    rolled = await fetch(session_factory, _ROLLUP_ROWS, params)
    raw = await fetch(session_factory, _RAW_GROUPS, params)
    assert _as_set(rolled) == _as_set(raw)
    assert len(rolled) > 12  # two providers, three classes, two kinds, three hours: many groups
    assert sum(row["errors"] for row in rolled) == 60
    assert sum(row["with_retry_after"] for row in rolled) == 30  # indexes 0 and 1 modulo 4
    assert {row["rate_limit_kind"] for row in rolled} == {"requests", "tokens", None}


async def test_fault_injected_spans_are_excluded(session_factory: SessionFactory) -> None:
    (project,) = await new_projects(session_factory, 1)
    hour = floor_hour(datetime.now(UTC)) - timedelta(hours=2)
    fault = {"spanlight.fault.scenario": "rate_limited"}
    spans = [
        FailedSpan(started_at=hour, error_class="rate_limit", rate_limit_kind="unknown"),
        *[
            FailedSpan(
                started_at=hour,
                error_class="rate_limit",
                rate_limit_kind="unknown",
                attributes=fault,
            )
            for _ in range(3)
        ],
    ]
    await seed_spans(session_factory, project, spans)

    await compute(session_factory, project, hour, hour + timedelta(hours=1))

    rows = await fetch(session_factory, _ROLLUP_ROWS, {"p": project})
    assert [(row["error_class"], row["errors"]) for row in rows] == [("rate_limit", 1)]


async def test_resent_span_moves_group_delete_then_insert(session_factory: SessionFactory) -> None:
    (project,) = await new_projects(session_factory, 1)
    hour = floor_hour(datetime.now(UTC)) - timedelta(hours=2)
    (trace_id,) = await seed_spans(session_factory, project, [FailedSpan(started_at=hour)])
    await compute(session_factory, project, hour, hour + timedelta(hours=1))

    # The span is sent again with another model and class: its old group must disappear.
    await execute(
        session_factory,
        "UPDATE spans SET model = 'gpt-4.1-mini', error_class = 'rate_limit', "
        "rate_limit_kind = 'tokens' WHERE project_id = :p AND trace_id = :t",
        {"p": project, "t": trace_id},
    )
    await compute(session_factory, project, hour, hour + timedelta(hours=1))

    rows = await fetch(session_factory, _ROLLUP_ROWS, {"p": project})
    assert [(r["model"], r["error_class"], r["rate_limit_kind"], r["errors"]) for r in rows] == [
        ("gpt-4.1-mini", "rate_limit", "tokens", 1)
    ]


async def test_backfill_detects_a_project_missing_error_rollups(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(days=5)
    (project,) = await new_projects(session_factory, 1)
    await seed_spans(
        session_factory,
        project,
        [FailedSpan(started_at=old), FailedSpan(started_at=now - timedelta(hours=1))],
    )
    await roll_up_all(session_factory, now=now)
    # The state of an install upgraded with complete span rollups: no error history at all.
    await execute(
        session_factory, "DELETE FROM error_rollups_hourly WHERE project_id = :p", {"p": project}
    )

    # The recompute writes the recent hour first, so the table is no longer empty when the
    # backfill looks for gaps: the old failure must still be found.
    _, backfilled = await roll_up_all(session_factory, now=now)

    assert backfilled > 0
    rows = await fetch(
        session_factory,
        "SELECT bucket_start, errors FROM error_rollups_hourly WHERE project_id = :p "
        "ORDER BY bucket_start",
        {"p": project},
    )
    assert [(row["bucket_start"], row["errors"]) for row in rows] == [
        (floor_hour(old), 1),
        (floor_hour(now - timedelta(hours=1)), 1),
    ]


async def test_backfill_of_error_free_history_is_not_repeated(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(days=5)
    (project,) = await new_projects(session_factory, 1)
    # Old spans that never make an error rollup row: a success, a failed tool call, a fault, and
    # a failed call stored before error classes existed. None of them may open an error gap.
    await seed_spans(
        session_factory,
        project,
        [
            FailedSpan(started_at=old, status="ok", error_class=None),
            FailedSpan(started_at=old, kind="tool"),
            FailedSpan(started_at=old, attributes={"spanlight.fault.scenario": "timeout"}),
            FailedSpan(started_at=old, error_class=None),
        ],
    )
    _, first = await roll_up_all(session_factory, now=now)
    _, second = await roll_up_all(session_factory, now=now)

    assert first > 0
    assert second == 0
    assert await fetch(session_factory, _ROLLUP_ROWS, {"p": project}) == []


async def test_read_error_rollups_filters_and_merges_providers(
    session_factory: SessionFactory,
) -> None:
    project, other = await new_projects(session_factory, 2)
    hour = floor_hour(datetime.now(UTC)) - timedelta(hours=3)
    spans = [
        FailedSpan(
            started_at=hour,
            provider="openai",
            error_class="rate_limit",
            rate_limit_kind="tokens",
            attributes={"provider.retry_after_s": 1},
        ),
        FailedSpan(
            started_at=hour,
            provider="anthropic",
            error_class="rate_limit",
            rate_limit_kind="tokens",
        ),
        FailedSpan(started_at=hour + timedelta(hours=1), provider="openai"),
        FailedSpan(started_at=hour, provider="openai", environment="staging"),
    ]
    await seed_spans(session_factory, project, spans)
    await seed_spans(session_factory, other, [FailedSpan(started_at=hour)])
    for owner in (project, other):
        await compute(session_factory, owner, hour, hour + timedelta(hours=2))

    window = TimeWindow(start=hour + timedelta(minutes=30), end=hour + timedelta(hours=2))
    production = RollupFilters(environment="production")
    async with session_factory() as db:
        await bind_project(db, project)
        merged = await read_error_rollups(db, project, window, production, provider_breakdown=False)
        split = await read_error_rollups(db, project, window, production, provider_breakdown=True)
        openai = await read_error_rollups(
            db, project, window, RollupFilters(provider="openai"), provider_breakdown=True
        )

    later = hour + timedelta(hours=1)
    assert merged == [
        ErrorHour(hour, None, "rate_limit", "tokens", errors=2, with_retry_after=1),
        ErrorHour(later, None, "provider_5xx", None, errors=1, with_retry_after=0),
    ]
    assert split == [
        ErrorHour(hour, "anthropic", "rate_limit", "tokens", errors=1, with_retry_after=0),
        ErrorHour(hour, "openai", "rate_limit", "tokens", errors=1, with_retry_after=1),
        ErrorHour(later, "openai", "provider_5xx", None, errors=1, with_retry_after=0),
    ]
    assert [(row.provider, row.error_class, row.errors) for row in openai] == [
        ("openai", "provider_5xx", 1),
        ("openai", "rate_limit", 1),
        ("openai", "provider_5xx", 1),
    ]
