"""The `grouped` CTE against real traces and spans (spec §5.3 and §5.6).

The fixture (`tests.attribution.support`) spreads six traces over three UTC days; its docstring
has the table the expected figures below come from. The statements run past row-level security,
so the CTE's own `project_id` filter is what keeps the other project's trace out.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, get_args

from sqlalchemy import text

from app.attribution.aggregate_sql import (
    GROUP_EXPRESSIONS,
    GroupKind,
    grouped_cte,
    money_sum,
    retry_cost_sum,
)
from app.db.rls import bypass_rls
from app.ingest.store import _recompute_rollups
from tests.attribution.support import (
    DAYS,
    WINDOW_START,
    Seeded,
    SpanSeed,
    TraceSeed,
    at,
    seed_attribution_fixture,
    seed_traces,
)
from tests.jobs.bypass_support import SessionFactory, fetch, new_projects

Row = dict[str, Any]
FIGURES = ("traces", "llm_calls", "errors", "unpriced_calls", "input_tokens", "output_tokens")


async def _seed(session_factory: SessionFactory) -> Seeded:
    [project_id] = await new_projects(session_factory, 1)
    return await seed_attribution_fixture(session_factory, project_id)


async def _grouped(
    session_factory: SessionFactory,
    project_id: uuid.UUID,
    kind: GroupKind,
    *,
    by_day: bool = False,
    window: tuple[datetime, datetime] | None = None,
    key: str | None = None,
    environment: str | None = None,
) -> list[Row]:
    start, end = window or (WINDOW_START, at(3, 0))
    cte = grouped_cte(kind, by_day=by_day, with_environment=environment is not None)
    params: dict[str, Any] = {"project_id": project_id, "start": start, "end": end}
    if key is not None:
        params["key"] = key
    if environment is not None:
        params["environment"] = environment
    return await fetch(session_factory, f"WITH {cte} SELECT * FROM grouped", params)


def _key(kind: GroupKind) -> str | None:
    """The metadata key the kind-by-kind tests group by; the other kinds take none."""
    return "customer" if kind == "metadata" else None


def _by(rows: list[Row], *columns: str) -> dict[Any, Row]:
    keyed = {tuple(row[column] for column in columns): row for row in rows}
    assert len(keyed) == len(rows), "a group appears twice"
    return keyed


def _figures(row: Row) -> dict[str, Any]:
    return {name: row[name] for name in (*FIGURES, "cost_usd", "retries", "retry_cost_usd")}


async def test_trace_belongs_to_the_utc_day_it_started_whatever_its_spans_days(
    session_factory: SessionFactory,
) -> None:
    seeded = await _seed(session_factory)
    rows = _by(
        await _grouped(session_factory, seeded.project_id, "trace_name", by_day=True),
        "day",
        "environment",
        "group_value",
    )
    # t2 started at 23:50 on D0; its second call started on D1 and still counts on D0.
    assert _figures(rows[(DAYS[0], "prod", "answer")]) == {
        "traces": 2,
        "llm_calls": 4,
        "errors": 1,
        "unpriced_calls": 0,
        "input_tokens": 610,
        "output_tokens": 70,
        "cost_usd": Decimal("0.65"),
        "retries": 1,
        "retry_cost_usd": Decimal("0.05"),
    }
    assert set(rows) == {
        (DAYS[0], "prod", "answer"),
        (DAYS[1], None, None),
        (DAYS[1], "staging", "summarize"),
        (DAYS[2], "prod", "summarize"),
        (DAYS[2], "prod", "answer"),
    }
    # A window that ends before the second call started still counts it: the trace is in it.
    [only_d0] = await _grouped(
        session_factory, seeded.project_id, "trace_name", window=(at(0, 0), at(1, 0))
    )
    assert (only_d0["llm_calls"], only_d0["cost_usd"]) == (4, Decimal("0.65"))


async def test_figures_come_from_llm_spans_and_cost_from_every_span(
    session_factory: SessionFactory,
) -> None:
    seeded = await _seed(session_factory)
    # t4 alone: an unpriced llm call, a priced llm call and a priced retrieval span.
    [row] = await _grouped(session_factory, seeded.project_id, "release", environment="staging")
    assert row["group_value"] == "r2" and row["environment"] == "staging"
    assert _figures(row) == {
        "traces": 1,
        "llm_calls": 2,
        "errors": 0,
        "unpriced_calls": 1,
        "input_tokens": 480,
        "output_tokens": 45,
        "cost_usd": Decimal("0.43"),
        "retries": None,
        "retry_cost_usd": None,
    }
    assert "day" not in row


async def test_cost_counts_every_span_while_calls_count_llm_spans_only(
    session_factory: SessionFactory,
) -> None:
    seeded = await _seed(session_factory)
    rows = _by(
        await _grouped(session_factory, seeded.project_id, "trace_name"),
        "environment",
        "group_value",
    )
    # t3 has one priced tool span and no llm span.
    t3 = rows[(None, None)]
    assert (t3["traces"], t3["llm_calls"], t3["errors"], t3["unpriced_calls"]) == (1, 0, 0, 0)
    assert (t3["cost_usd"], t3["input_tokens"], t3["output_tokens"]) == (Decimal("0.01"), 7, 3)


async def test_none_bucket_holds_traces_without_the_dimension(
    session_factory: SessionFactory,
) -> None:
    seeded = await _seed(session_factory)
    expected: dict[GroupKind, int] = {"metadata": 2, "trace_name": 1, "user": 1, "release": 2}
    for kind, traces in expected.items():
        rows = await _grouped(session_factory, seeded.project_id, kind, key=_key(kind))
        none = [row for row in rows if row["group_value"] is None]
        assert sum(row["traces"] for row in none) == traces, kind
    # metadata `customer`: t3 (no metadata) and t6 (other keys only).
    rows = await _grouped(session_factory, seeded.project_id, "metadata", key="customer")
    none_by_env = _by([row for row in rows if row["group_value"] is None], "environment")
    assert none_by_env[(None,)]["cost_usd"] == Decimal("0.01")
    assert none_by_env[("prod",)]["cost_usd"] is None  # t6: nothing priced is unknown, not 0


async def test_grouped_cte_binds_a_key_with_dot_and_dash(session_factory: SessionFactory) -> None:
    for kind in get_args(GroupKind):
        assert "a.b-c" not in grouped_cte(kind, by_day=True, with_environment=True)
    assert "CAST(:key AS text)" in GROUP_EXPRESSIONS["metadata"]
    seeded = await _seed(session_factory)
    rows = await _grouped(session_factory, seeded.project_id, "metadata", key="a.b-c")
    traces: dict[str | None, int] = {}
    for row in rows:
        traces[row["group_value"]] = traces.get(row["group_value"], 0) + row["traces"]
    assert traces == {"x": 1, "y": 1, "z": 1, None: 3}
    by_value = _by([row for row in rows if row["group_value"]], "group_value")
    assert by_value[("x",)]["cost_usd"] == Decimal("0.15")
    assert by_value[("y",)]["environment"] == "staging"


async def test_retries_sum_bet_2_columns_and_stay_null_for_unknown_traces(
    session_factory: SessionFactory,
) -> None:
    seeded = await _seed(session_factory)
    rows = await _grouped(session_factory, seeded.project_id, "trace_name")
    by_name: dict[str | None, list[Row]] = {}
    for row in rows:
        by_name.setdefault(row["group_value"], []).append(row)
    # answer: t1 (1 retry, $0.05), t2 and t6 (0, $0), all prod.
    [answer] = by_name["answer"]
    assert (answer["retries"], answer["retry_cost_usd"]) == (1, Decimal("0.05"))
    # summarize: t4 unknown (NULL, NULL, staging); t5 two retries of unknown cost (prod).
    summarize = _by(by_name["summarize"], "environment")
    assert (summarize[("staging",)]["retries"], summarize[("staging",)]["retry_cost_usd"]) == (
        None,
        None,
    )
    assert (summarize[("prod",)]["retries"], summarize[("prod",)]["retry_cost_usd"]) == (2, None)
    # Without the environment split, the unknown trace adds nothing to the known one.
    rows = await _grouped(session_factory, seeded.project_id, "release")
    r2 = [row for row in rows if row["group_value"] == "r2"]
    assert sum(row["retries"] or 0 for row in r2) == 2
    # The none bucket is t3 only, never recomputed since 0700: unknown, not 0.
    [none] = by_name[None]
    assert (none["retries"], none["retry_cost_usd"]) == (None, None)


_WINDOW_TOTALS = """
    WITH w AS (
        SELECT * FROM traces
        WHERE project_id = :project_id AND started_at >= :start AND started_at < :end
    ), llm AS (
        SELECT s.* FROM spans AS s JOIN w USING (project_id, trace_id) WHERE s.kind = 'llm'
    )
    SELECT (SELECT count(*) FROM w) AS traces,
           (SELECT sum(cost_usd) FROM w) AS cost_usd,
           (SELECT sum(input_tokens) FROM w) AS input_tokens,
           (SELECT sum(output_tokens) FROM w) AS output_tokens,
           (SELECT sum(retry_calls) FROM w) AS retries,
           (SELECT count(*) FROM llm) AS llm_calls,
           (SELECT count(*) FROM llm WHERE status = 'error') AS errors,
           (SELECT count(*) FROM llm WHERE cost_usd IS NULL) AS unpriced_calls
"""


def _total(rows: list[Row], column: str) -> Any:
    known = [row[column] for row in rows if row[column] is not None]
    return sum(known) if known else None


async def test_ledger_property_for_each_kind(session_factory: SessionFactory) -> None:
    seeded = await _seed(session_factory)
    window = {"project_id": seeded.project_id, "start": seeded.start, "end": seeded.end}
    [expected] = await fetch(session_factory, _WINDOW_TOTALS, window)
    assert expected["cost_usd"] == Decimal("1.79") and expected["traces"] == 6
    columns = (*FIGURES, "cost_usd", "retries")
    for kind in get_args(GroupKind):
        for by_day in (False, True):
            rows = await _grouped(
                session_factory, seeded.project_id, kind, by_day=by_day, key=_key(kind)
            )
            totals = {column: _total(rows, column) for column in columns}
            assert totals == {column: expected[column] for column in columns}, (kind, by_day)


async def test_a_group_sum_of_a_million_dollars_or_more_is_unknown(
    session_factory: SessionFactory,
) -> None:
    [project_id] = await new_projects(session_factory, 1)
    big = TraceSeed(
        at(0, 9),
        (SpanSeed("llm", at(0, 9), Decimal("600000"), 1, 1),),
        name="big",
        retries=1,
        retry_cost_usd=Decimal("600000"),
    )
    async with session_factory() as db:
        await bypass_rls(db)
        await seed_traces(db, project_id, {"a": big, "b": big})
        await db.commit()
    [row] = await _grouped(session_factory, project_id, "trace_name")
    # Each trace's $600,000 is stored; their $1.2M sum does not fit numeric(14, 8): unknown.
    assert (row["traces"], row["llm_calls"], row["retries"]) == (2, 2, 2)
    assert (row["cost_usd"], row["retry_cost_usd"]) == (None, None)


async def test_an_unpriced_retry_makes_the_group_retry_cost_unknown(
    session_factory: SessionFactory,
) -> None:
    [project_id] = await new_projects(session_factory, 1)
    priced = SpanSeed("llm", at(0, 9), Decimal("0.10"), 1, 1)

    def trace(name: str, retries: int | None, retry_cost: str | None) -> TraceSeed:
        cost = None if retry_cost is None else Decimal(retry_cost)
        return TraceSeed(at(0, 9), (priced,), name=name, retries=retries, retry_cost_usd=cost)

    seeds = {
        # t1's $0.05 beside a trace with two retries it could not price: unknown.
        "a": trace("unpriced", 1, "0.05"),
        "b": trace("unpriced", 2, None),
        # t1's $0.05 beside a trace not recomputed since 0700 (both NULL): it adds nothing.
        "c": trace("pre_0700", 1, "0.05"),
        "d": trace("pre_0700", None, None),
    }
    async with session_factory() as db:
        await bypass_rls(db)
        await seed_traces(db, project_id, seeds)
        await db.commit()
    rows = _by(await _grouped(session_factory, project_id, "trace_name"), "group_value")
    unpriced, pre_0700 = rows[("unpriced",)], rows[("pre_0700",)]
    assert (unpriced["retries"], unpriced["retry_cost_usd"]) == (3, None)
    assert (pre_0700["retries"], pre_0700["retry_cost_usd"]) == (1, Decimal("0.05"))


# A trace `nested` of two llm spans with usage, the second under the first, as a Vercel outer
# span over its provider call: the recompute flags the parent a usage rollup.
_NESTED_TRACE = """
    INSERT INTO traces (project_id, trace_id, name, environment, started_at, ended_at)
    VALUES (:project_id, :trace_id, 'nested', 'prod', :started_at, :started_at)
"""
_NESTED_SPANS = """
    INSERT INTO spans (project_id, trace_id, span_id, parent_span_id, kind, name, status,
                       started_at, ended_at, provider, model, input_tokens, output_tokens, cost_usd)
    VALUES (:project_id, :trace_id, :parent, NULL, 'llm', 'call', 'ok',
            :started_at, :started_at, 'openai', 'model-a', 100, 10, 1.00),
           (:project_id, :trace_id, :child, :parent, 'llm', 'call', 'ok',
            :started_at, :started_at, 'openai', 'model-a', 50, 5, 0.50)
"""


async def test_a_usage_rollup_parent_is_counted_but_not_summed(
    session_factory: SessionFactory,
) -> None:
    seeded = await _seed(session_factory)
    params = {"project_id": seeded.project_id, "trace_id": "0" * 32, "started_at": at(0, 9)}
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(text(_NESTED_TRACE), params)
        await db.execute(text(_NESTED_SPANS), {**params, "parent": "a" * 16, "child": "b" * 16})
        await _recompute_rollups(db, seeded.project_id, [params["trace_id"]])
        await db.commit()

    rows = _by(
        await _grouped(session_factory, seeded.project_id, "trace_name"),
        "environment",
        "group_value",
    )
    nested = _figures(rows[("prod", "nested")])
    assert (nested["llm_calls"], nested["cost_usd"]) == (2, Decimal("0.50"))
    assert (nested["input_tokens"], nested["output_tokens"]) == (50, 5)
    # The ledger property holds: the group reads what the trace's own totals store.
    [trace] = await fetch(
        session_factory,
        "SELECT cost_usd, input_tokens, output_tokens FROM traces WHERE trace_id = :trace_id",
        {"trace_id": params["trace_id"]},
    )
    assert trace == {"cost_usd": Decimal("0.50"), "input_tokens": 50, "output_tokens": 5}


def test_retry_cost_sum_is_one_rule_for_traces_and_rollup_rows() -> None:
    expression = retry_cost_sum("r.retries", "r.retry_cost_usd")
    assert "bool_or(r.retries IS NOT NULL AND r.retry_cost_usd IS NULL)" in expression
    assert money_sum("r.retry_cost_usd") in expression
