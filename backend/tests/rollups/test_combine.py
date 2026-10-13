"""Merging hourly rollups for the overview charts, where an unknown cost must stay unknown.

Failure modes pinned here: hours whose calls were all unpriced merge to a cost of `0` instead of
`null` (the spend chart then claims the hours were free); money drifts through binary floats;
an hour that has traces but no spans loses its traces, or gains a cost or calls it never had.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.rollups.buckets import BUCKET_COUNT
from app.rollups.queries import HourlyRollup, combine, read_rollups
from tests.jobs.bypass_support import SessionFactory, execute, new_projects

START = datetime(2026, 10, 13, 9, tzinfo=UTC)


def _hour(
    *,
    cost_usd: Decimal | None,
    traces: int = 1,
    llm_calls: int = 1,
    latency_buckets: list[int] | None = None,
) -> HourlyRollup:
    return HourlyRollup(
        bucket_start=START,
        traces=traces,
        llm_calls=llm_calls,
        llm_errors=0,
        unpriced_calls=0 if cost_usd is not None else llm_calls,
        cost_usd=cost_usd,
        input_tokens=10,
        output_tokens=5,
        latency_buckets=latency_buckets or [0] * BUCKET_COUNT,
    )


def test_hours_without_any_priced_cost_combine_to_an_unknown_cost() -> None:
    merged = combine(START, [_hour(cost_usd=None), _hour(cost_usd=None)])

    assert merged.cost_usd is None
    assert merged.unpriced_calls == 2


def test_nothing_to_combine_is_an_unknown_cost_and_zero_counts() -> None:
    merged = combine(START, [])

    assert merged.cost_usd is None
    assert (merged.traces, merged.llm_calls, merged.tokens) == (0, 0, 0)
    assert merged.latency_buckets == [0] * BUCKET_COUNT


def test_costs_add_as_exact_decimals() -> None:
    merged = combine(START, [_hour(cost_usd=Decimal("0.1")), _hour(cost_usd=Decimal("0.2"))])

    assert merged.cost_usd == Decimal("0.3")
    assert isinstance(merged.cost_usd, Decimal)


def test_one_priced_hour_and_one_unpriced_hour_give_the_priced_cost() -> None:
    merged = combine(START, [_hour(cost_usd=None), _hour(cost_usd=Decimal("0.25"))])

    assert merged.cost_usd == Decimal("0.25")


def test_counts_tokens_and_histograms_add() -> None:
    first = [0] * BUCKET_COUNT
    first[3] = 2
    second = [0] * BUCKET_COUNT
    second[3], second[7] = 1, 4

    merged = combine(
        START,
        [
            _hour(cost_usd=None, traces=2, llm_calls=2, latency_buckets=first),
            _hour(cost_usd=None, traces=3, llm_calls=5, latency_buckets=second),
        ],
    )

    assert (merged.traces, merged.llm_calls, merged.tokens) == (5, 7, 30)
    expected = [0] * BUCKET_COUNT
    expected[3], expected[7] = 3, 4
    assert merged.latency_buckets == expected


async def test_an_hour_with_traces_but_no_spans_has_an_unknown_cost_and_no_calls(
    session_factory: SessionFactory,
) -> None:
    (project,) = await new_projects(session_factory, 1)
    with_spans = START
    trace_only = START + timedelta(hours=1)
    await execute(
        session_factory,
        "INSERT INTO span_rollups_hourly (project_id, bucket_start, kind, span_count, errors, "
        "input_tokens, output_tokens, cached_tokens, unpriced_calls, latency_buckets, "
        "ttft_buckets) VALUES (:project, :at, 'llm', 2, 0, 10, 5, 0, 2, "
        "array_fill(0, ARRAY[32]), array_fill(0, ARRAY[32]))",
        {"project": project, "at": with_spans},
    )
    for at, traces in ((with_spans, 2), (trace_only, 4)):
        await execute(
            session_factory,
            "INSERT INTO trace_rollups_hourly (project_id, bucket_start, traces, errored_traces) "
            "VALUES (:project, :at, :traces, 0)",
            {"project": project, "at": at, "traces": traces},
        )

    async with session_factory() as db:
        await bind_project(db, project)
        series = await read_rollups(
            db, project, TimeWindow(START, START + timedelta(hours=2)), None
        )

    spans_hour, traces_hour = series.hours
    assert (spans_hour.bucket_start, spans_hour.traces, spans_hour.llm_calls) == (with_spans, 2, 2)
    assert spans_hour.cost_usd is None  # every call was unpriced
    assert traces_hour.bucket_start == trace_only
    assert (traces_hour.traces, traces_hour.llm_calls, traces_hour.tokens) == (4, 0, 0)
    assert traces_hour.cost_usd is None
    assert traces_hour.latency_buckets == [0] * BUCKET_COUNT
