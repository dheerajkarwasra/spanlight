"""The attribution refresh statement: the daily cap, the `other` fold and upsert-and-prune.

Each test seeds traces past row-level security, runs the refresh in a session bound to the
project (as the job does), then reads the rollup back past row-level security.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.attribution.aggregate_sql import grouped_cte, retry_cost_sum
from app.attribution.jobs import deep_days, pass_days, recent_days, refresh_all
from app.attribution.refresh import (
    VALUE_CAP,
    RefreshResult,
    refresh_attribution,
    refresh_group,
)
from app.core.observability import ATTRIBUTION_FOLDED_VALUES
from app.db.rls import bind_project, bypass_rls
from tests.attribution.support import (
    DAYS,
    WINDOW_END,
    WINDOW_START,
    SpanSeed,
    TraceSeed,
    at,
    seed_attribution_fixture,
    seed_traces,
)
from tests.gateway_support import metric_delta
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects

Row = dict[str, Any]
FIGURES = (
    "traces",
    "llm_calls",
    "errors",
    "unpriced_calls",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "retries",
    "retry_cost_usd",
)

# `count` traces started at `:started_at`, each with one LLM span. Trace `n` (1-based) carries
# `customer = c<n, five digits>` and costs `n * :unit` (NULL when `:unit` is NULL).
_SEED_TRACES = """
    INSERT INTO traces (project_id, trace_id, environment, metadata, started_at, ended_at)
    SELECT CAST(:project_id AS uuid), md5(CAST(:project_id AS text) || n::text), 'prod',
           jsonb_build_object('customer', 'c' || lpad(n::text, 5, '0')),
           :started_at, :started_at
    FROM generate_series(1, :count) AS n
"""
_SEED_SPANS = """
    INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at,
                       provider, model, input_tokens, output_tokens, cost_usd)
    SELECT CAST(:project_id AS uuid), md5(CAST(:project_id AS text) || n::text),
           left(md5(n::text), 16), 'llm', 'call', 'ok', :started_at,
           :started_at, 'openai', 'model-a', 10, 5, CAST(:unit AS numeric) * n
    FROM generate_series(1, :count) AS n
"""

_ROWS = """
    SELECT * FROM attribution_rollups_daily
    WHERE project_id = :project_id AND group_kind = :kind
      AND group_key IS NOT DISTINCT FROM CAST(:key AS text)
    ORDER BY day, environment, bucket, group_value
"""


async def _project(session_factory: SessionFactory) -> uuid.UUID:
    [project_id] = await new_projects(session_factory, 1)
    return project_id


async def _seed(
    session_factory: SessionFactory, project_id: uuid.UUID, seeds: dict[str, TraceSeed]
) -> None:
    async with session_factory() as db:
        await bypass_rls(db)
        await seed_traces(db, project_id, seeds)
        await db.commit()


async def _seed_values(
    session_factory: SessionFactory, project_id: uuid.UUID, count: int, unit: str | None
) -> None:
    params = {"project_id": project_id, "count": count, "started_at": at(0, 12), "unit": unit}
    await execute(session_factory, _SEED_TRACES, params)
    # Fresh statistics, so the foreign key check of each span looks its trace up by primary key.
    # With the stale ones of an almost empty table it can pick the (project_id, started_at) index
    # and scan every trace of the project per span: minutes for 50,000 spans.
    await execute(session_factory, "ANALYZE traces")
    await execute(session_factory, _SEED_SPANS, params)


async def _refresh_group(
    session_factory: SessionFactory,
    project_id: uuid.UUID,
    kind: Any,
    key: str | None,
    days: Sequence[date] = (DAYS[0],),
    **options: int,
) -> RefreshResult:
    async with session_factory() as db:
        await bind_project(db, project_id)
        result = await refresh_group(db, project_id, kind, key, days, **options)
        await db.commit()
    return result


async def _refresh_all(
    session_factory: SessionFactory,
    project_id: uuid.UUID,
    promoted: Sequence[str],
    days: Sequence[date] = DAYS,
) -> RefreshResult:
    async with session_factory() as db:
        await bind_project(db, project_id)
        result = await refresh_attribution(db, project_id, promoted, days)
        await db.commit()
    return result


async def _rows(
    session_factory: SessionFactory, project_id: uuid.UUID, kind: str, key: str | None = None
) -> list[Row]:
    return await fetch(session_factory, _ROWS, {"project_id": project_id, "kind": kind, "key": key})


def _named(name: str, cost: str | None, *, calls: int = 1, day: int = 0) -> TraceSeed:
    """A trace named `name` with `calls` LLM spans, the first costing `cost`, the rest unpriced."""
    spans = tuple(
        SpanSeed("llm", at(day, 12), Decimal(cost) if cost and i == 0 else None, 1, 1)
        for i in range(calls)
    )
    return TraceSeed(at(day, 12), spans, name=name, metadata={"customer": name})


async def test_1003_values_on_one_day_yield_1000_rows_and_other_with_folded_values_3(
    session_factory: SessionFactory,
) -> None:
    project_id = await _project(session_factory)
    await _seed_values(session_factory, project_id, 1003, "0.001")

    result = await _refresh_group(session_factory, project_id, "metadata", "customer")

    rows = await _rows(session_factory, project_id, "metadata", "customer")
    values = [row for row in rows if row["bucket"] == "value"]
    [other] = [row for row in rows if row["bucket"] == "other"]
    assert len(values) == VALUE_CAP == 1000 and len(rows) == 1001
    # The three cheapest (c00001 to c00003) fold; the top 1,000 by cost stay.
    assert min(row["group_value"] for row in values) == "c00004"
    assert other["group_value"] is None and other["folded_values"] == 3
    assert (other["traces"], other["llm_calls"], other["cost_usd"]) == (3, 3, Decimal("0.006"))
    assert result == RefreshResult(written=1001, removed=0, folded=3)


async def test_rank_ties_at_null_cost_use_calls_then_value(
    session_factory: SessionFactory,
) -> None:
    project_id = await _project(session_factory)
    # d is priced; a, b and c are not: a has two calls, b and c one each, so c ranks last.
    await _seed(
        session_factory,
        project_id,
        {
            "c": _named("c", None),
            "b": _named("b", None),
            "a": _named("a", None, calls=2),
            "d": _named("d", "0.01"),
        },
    )

    await _refresh_group(session_factory, project_id, "metadata", "customer", value_cap=3)

    rows = await _rows(session_factory, project_id, "metadata", "customer")
    assert {row["group_value"] for row in rows if row["bucket"] == "value"} == {"a", "b", "d"}
    [other] = [row for row in rows if row["bucket"] == "other"]
    # c alone folded, and it was never priced: the fold's cost is unknown, not 0.
    assert (other["folded_values"], other["llm_calls"], other["cost_usd"]) == (1, 1, None)


async def test_a_value_that_leaves_the_top_1000_leaves_no_stale_row(
    session_factory: SessionFactory,
) -> None:
    project_id = await _project(session_factory)
    await _seed(
        session_factory,
        project_id,
        {"a": _named("a", "0.03"), "b": _named("b", "0.02"), "c": _named("c", "0.01")},
    )
    await _refresh_group(session_factory, project_id, "trace_name", None, value_cap=2)
    # c overtakes both, so b moves into `other`.
    await _seed(session_factory, project_id, {"c2": _named("c", "0.05")})

    result = await _refresh_group(session_factory, project_id, "trace_name", None, value_cap=2)

    rows = await _rows(session_factory, project_id, "trace_name")
    assert [(row["bucket"], row["group_value"]) for row in rows] == [
        ("other", None),
        ("value", "a"),
        ("value", "c"),
    ]
    other = rows[0]
    assert (other["folded_values"], other["cost_usd"]) == (1, Decimal("0.02"))
    assert result.removed == 1  # b's value row


async def test_two_refreshes_of_traces_without_environment_keep_one_row_per_group(
    session_factory: SessionFactory,
) -> None:
    project_id = await _project(session_factory)
    unnamed = TraceSeed(at(0, 9), (SpanSeed("llm", at(0, 9), Decimal("0.01"), 1, 1),))
    await _seed(
        session_factory,
        project_id,
        {
            "x": TraceSeed(at(0, 10), unnamed.spans, environment=None, name="x"),
            "y": TraceSeed(at(0, 11), unnamed.spans, environment=None, name="y"),
            "none": TraceSeed(at(0, 12), unnamed.spans, environment=None),
        },
    )

    await _refresh_all(session_factory, project_id, [], [DAYS[0]])
    first = await _rows(session_factory, project_id, "trace_name")
    second_result = await _refresh_all(session_factory, project_id, [], [DAYS[0]])
    second = await _rows(session_factory, project_id, "trace_name")

    assert [(row["environment"], row["bucket"], row["group_value"]) for row in second] == [
        (None, "none", None),
        (None, "value", "x"),
        (None, "value", "y"),
    ]
    # The second pass updated the same rows (NULLS NOT DISTINCT), it did not add any.
    assert [row["id"] for row in second] == [row["id"] for row in first]
    assert second_result.removed == 0


async def test_rollup_rows_equal_the_grouped_cte_for_the_same_day(
    session_factory: SessionFactory,
) -> None:
    project_id = await _project(session_factory)
    seeded = await seed_attribution_fixture(session_factory, project_id)
    promoted = ["customer", "a.b-c"]

    await _refresh_all(session_factory, project_id, promoted)

    cases: list[tuple[Any, str | None]] = [
        ("trace_name", None),
        ("user", None),
        ("release", None),
        *(("metadata", key) for key in promoted),
    ]
    for kind, key in cases:
        params = {"project_id": project_id, "start": WINDOW_START, "end": WINDOW_END, "key": key}
        cte = grouped_cte(kind, by_day=True, with_environment=False)
        expected = await fetch(session_factory, f"WITH {cte} SELECT * FROM grouped", params)
        stored = await _rows(session_factory, project_id, kind, key)
        assert {_group(row): _figures(row) for row in stored} == {
            _group(row): _figures(row) for row in expected
        }, (kind, key)
        assert len(stored) == len(expected)
    # The ledger across the write path: each day's rows add up to its traces' cost.
    by_day = await fetch(
        session_factory,
        "SELECT day, sum(cost_usd) AS cost FROM attribution_rollups_daily "
        "WHERE project_id = :p AND group_kind = 'trace_name' GROUP BY day ORDER BY day",
        {"p": seeded.project_id},
    )
    traces = await fetch(
        session_factory,
        "SELECT (started_at AT TIME ZONE 'UTC')::date AS day, sum(cost_usd) AS cost FROM traces "
        "WHERE project_id = :p AND started_at >= :s AND started_at < :e GROUP BY 1 ORDER BY 1",
        {"p": seeded.project_id, "s": WINDOW_START, "e": WINDOW_END},
    )
    assert by_day == traces


def _group(row: Row) -> tuple[Any, ...]:
    return (row["day"], row["environment"], row["group_value"])


def _figures(row: Row) -> tuple[Any, ...]:
    return tuple(row[name] for name in FIGURES)


async def test_a_group_or_fold_of_a_million_dollars_or_more_reads_null(
    session_factory: SessionFactory,
) -> None:
    project_id = await _project(session_factory)
    # Each trace fits numeric(14, 8); two of them in one group do not.
    await _seed(
        session_factory,
        project_id,
        {
            "big1": _named("a", "600000"),
            "big2": _named("a", "600000"),
            "c": _named("c", "600000"),
            "b": _named("b", "500000"),
            "small": _named("s", "0.01"),
        },
    )

    await _refresh_group(session_factory, project_id, "trace_name", None, value_cap=1)
    await _refresh_group(session_factory, project_id, "metadata", "customer", value_cap=2)

    [other, kept] = await _rows(session_factory, project_id, "trace_name")
    # `a` ($1.2M over two traces) reads unknown and ranks last, so the one slot goes to the
    # priced `c` ($600k); `a`, `b` and `s` fold, and `a`'s unknown cost is absent from the fold.
    assert (kept["group_value"], kept["cost_usd"]) == ("c", Decimal("600000"))
    assert (other["folded_values"], other["traces"]) == (3, 4)
    assert other["cost_usd"] == Decimal("500000.01")
    rows = await _rows(session_factory, project_id, "metadata", "customer")
    [fold] = [row for row in rows if row["bucket"] == "other"]
    # c ($600k) and b ($500k) stay; a (unknown) and s fold.
    assert {row["group_value"] for row in rows if row["bucket"] == "value"} == {"b", "c"}
    assert (fold["folded_values"], fold["cost_usd"]) == (2, Decimal("0.01"))


async def test_a_fold_of_a_million_dollars_or_more_reads_null(
    session_factory: SessionFactory,
) -> None:
    project_id = await _project(session_factory)
    await _seed(
        session_factory,
        project_id,
        {
            "a": _named("a", "700000"),
            "b": _named("b", "600000"),
            "c": _named("c", "500000"),
        },
    )

    await _refresh_group(session_factory, project_id, "trace_name", None, value_cap=1)

    [other, value] = await _rows(session_factory, project_id, "trace_name")
    assert (value["group_value"], value["cost_usd"]) == ("a", Decimal("700000"))
    # b + c = $1.1M does not fit: unknown, and the refresh did not fail.
    assert (other["folded_values"], other["traces"], other["cost_usd"]) == (2, 2, None)


async def test_two_days_of_one_group_with_an_unpriced_retry_sum_to_an_unknown_retry_cost(
    session_factory: SessionFactory,
) -> None:
    project_id = await _project(session_factory)
    span = (SpanSeed("llm", at(0, 12), Decimal("0.10"), 1, 1),)
    later = (SpanSeed("llm", at(1, 12), Decimal("0.10"), 1, 1),)
    await _seed(
        session_factory,
        project_id,
        {
            "d0": TraceSeed(at(0, 12), span, name="n", retries=1, retry_cost_usd=None),
            "d1": TraceSeed(at(1, 12), later, name="n", retries=1, retry_cost_usd=Decimal("0.02")),
        },
    )

    await _refresh_group(session_factory, project_id, "trace_name", None, DAYS[:2])

    rows = await _rows(session_factory, project_id, "trace_name")
    assert [(row["day"], row["retries"], row["retry_cost_usd"]) for row in rows] == [
        (DAYS[0], 1, None),
        (DAYS[1], 1, Decimal("0.02")),
    ]
    [total] = await fetch(
        session_factory,
        "SELECT sum(r.retries) AS retries, "
        f"{retry_cost_sum('r.retries', 'r.retry_cost_usd')} AS retry_cost "
        "FROM attribution_rollups_daily AS r WHERE r.project_id = :p",
        {"p": project_id},
    )
    assert (total["retries"], total["retry_cost"]) == (2, None)


async def test_folded_values_counter_increments(session_factory: SessionFactory) -> None:
    project_id = await _project(session_factory)
    await _seed(
        session_factory,
        project_id,
        {"a": _named("a", "0.03"), "b": _named("b", "0.02"), "c": _named("c", "0.01")},
    )
    folded = metric_delta(ATTRIBUTION_FOLDED_VALUES)

    await _refresh_group(session_factory, project_id, "trace_name", None, value_cap=1)

    assert folded.value == 2


async def test_a_project_with_50000_distinct_values_yields_1001_rows_per_day(
    session_factory: SessionFactory,
) -> None:
    project_id = await _project(session_factory)
    await _seed_values(session_factory, project_id, 50_000, "0.0001")

    result = await _refresh_group(session_factory, project_id, "metadata", "customer")

    rows = await _rows(session_factory, project_id, "metadata", "customer")
    assert len(rows) == 1001 and {row["day"] for row in rows} == {DAYS[0]}
    [other] = [row for row in rows if row["bucket"] == "other"]
    assert (other["folded_values"], other["traces"]) == (49_000, 49_000)
    assert result.folded == 49_000


def test_recent_pass_covers_yesterday_and_today_and_the_first_pass_of_a_day_covers_seven_more() -> (
    None
):
    today = date(2026, 10, 16)
    day = timedelta(days=1)
    midday = datetime(2026, 10, 16, 12, 7, tzinfo=UTC)
    assert recent_days(midday) == [today - day, today]
    assert deep_days(midday) == [today - offset * day for offset in range(7, 1, -1)]
    assert pass_days(midday) == recent_days(midday)
    # The pass whose 15-minute period starts at 00:00 UTC also recomputes [today - 7, today - 2].
    first_of_day = datetime(2026, 10, 16, 0, 3, tzinfo=UTC)
    assert pass_days(first_of_day) == [today - offset * day for offset in range(7, -1, -1)]
    assert pass_days(datetime(2026, 10, 16, 0, 16, tzinfo=UTC)) == recent_days(midday)
    assert pass_days(datetime(2026, 10, 15, 23, 59, tzinfo=UTC)) == [today - 2 * day, today - day]
    # The day is the UTC day whatever zone the clock answers in: 02:03+02:00 is 00:03 UTC.
    ahead = datetime(2026, 10, 16, 2, 3, tzinfo=timezone(timedelta(hours=2)))
    assert recent_days(ahead) == [today - day, today]
    assert pass_days(ahead) == pass_days(first_of_day)


async def _promote(session_factory: SessionFactory, project_id: uuid.UUID, keys: list[str]) -> None:
    await execute(
        session_factory,
        "UPDATE projects SET promoted_metadata_keys = CAST(:keys AS text[]) WHERE id = :id",
        {"keys": keys, "id": project_id},
    )


async def _days_of(
    session_factory: SessionFactory, project_id: uuid.UUID, kind: str, key: str | None = None
) -> set[date]:
    return {row["day"] for row in await _rows(session_factory, project_id, kind, key)}


async def test_promoted_key_backfills_seven_days_and_removal_deletes_rows(
    session_factory: SessionFactory,
) -> None:
    project_id = await _project(session_factory)
    # "Today" is DAYS[2]; 20:30 is not the first pass of the day, so the pass covers yesterday
    # and today only, and a promoted key with no rows yet gets [today - 6, today].
    now = at(2, 20, 30)
    await _seed(
        session_factory,
        project_id,
        {
            "eight_days_ago": _named("acme", "0.01", day=-5),
            "six_days_ago": _named("acme", "0.02", day=-4),
            "yesterday": _named("acme", "0.03", day=1),
            "today": _named("acme", "0.04", day=2),
        },
    )
    await _promote(session_factory, project_id, ["customer"])

    first = await refresh_all(session_factory, now=now)

    assert (first.projects, first.projects_failed) == (1, 0)
    seven_days = {at(-4, 0).date(), DAYS[1], DAYS[2]}
    assert await _days_of(session_factory, project_id, "metadata", "customer") == seven_days
    assert await _days_of(session_factory, project_id, "trace_name") == {DAYS[1], DAYS[2]}

    # The key now has rows: the next pass recomputes the window only and keeps the older day.
    second = await refresh_all(session_factory, now=now)
    assert second.rows < first.rows
    assert await _days_of(session_factory, project_id, "metadata", "customer") == seven_days

    await _promote(session_factory, project_id, [])
    await refresh_all(session_factory, now=now)
    assert await _rows(session_factory, project_id, "metadata", "customer") == []
    assert await _days_of(session_factory, project_id, "trace_name") == {DAYS[1], DAYS[2]}
