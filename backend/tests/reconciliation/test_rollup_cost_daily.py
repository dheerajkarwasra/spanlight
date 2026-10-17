"""`refresh_cost_rollups` and the cost rollup jobs (spec §6.8, §10.3; real Postgres).

Failure mode: the reconciliation report compares the provider's bill with a rollup that is wrong.
A cut stream counted as a cheap call, a cache read counted twice, a fee left out of `cost_usd`,
an OpenAI priority call that looks fully priced, a row grouped under a pattern the provider side
does not use, or a stale row left behind each move dollars into or out of the residual without
saying why.
"""

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.config import Settings
from app.db.rls import bind_project
from app.end_users.days import deep_days, midnight
from app.jobs.context import TaskContext
from app.jobs.queue import ClaimedJob, claim_next, enqueue
from app.jobs.scheduler import ROLLUP_COST_DAILY, ROLLUP_COST_DAILY_DEEP, periodic_jobs
from app.jobs.tasks import TASKS
from app.pricing.price_row import token_classes
from app.reconciliation.backfill import BACKFILL_JOB, backfill_payload
from app.reconciliation.queries import mapping_patterns
from app.reconciliation.rollup_jobs import (
    rollup_all,
    run_rollup_cost_daily,
    run_rollup_cost_daily_backfill,
    run_rollup_cost_daily_deep,
)
from app.reconciliation.rollups import refresh_cost_rollups
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects

DAY = date(2026, 10, 16)
AT = midnight(DAY) + timedelta(hours=10)
NOW = midnight(DAY) + timedelta(hours=12)
_SPAN_COUNTER = iter(range(1, 1_000_000))

_INSERT_SPAN = """
    WITH new_trace AS (
        INSERT INTO traces (project_id, trace_id, started_at, ended_at)
        VALUES (:project_id, :trace_id, :started_at, :started_at + interval '1 second')
    )
    INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at,
                       provider, model, service_tier, input_tokens, output_tokens, cached_tokens,
                       cache_write_tokens, cache_write_1h_tokens, web_search_requests, cost_usd,
                       fee_usd, pricing_version, attributes, usage_rollup)
    VALUES (:project_id, :trace_id, :span_id, CAST(:kind AS span_kind), 'call', 'ok',
            :started_at, :started_at + interval '1 second', :provider, :model, :service_tier,
            :input_tokens, :output_tokens, :cached_tokens, :cache_write_tokens,
            :cache_write_1h_tokens, :web_search_requests, :cost_usd, :fee_usd, :pricing_version,
            CAST(:attributes AS jsonb), :usage_rollup)
"""

_ROWS = """
    SELECT * FROM cost_rollups_daily
    ORDER BY day, provider NULLS FIRST, model NULLS FIRST, service_tier NULLS FIRST
"""

# Every column but the identity, the project and the write time.
_SKIP = {"id", "project_id", "computed_at"}


async def _span(
    session_factory: SessionFactory,
    project_id: uuid.UUID,
    *,
    at: datetime = AT,
    kind: str = "llm",
    provider: str | None = "openai",
    model: str | None = "gpt-4o",
    tier: str | None = None,
    input_tokens: int | None = 0,
    output_tokens: int | None = 0,
    cached: int | None = None,
    write: int | None = None,
    write_1h: int | None = None,
    searches: int | None = None,
    cost: str | None = None,
    fee: str | None = None,
    attributes: dict[str, Any] | None = None,
    rollup: bool = False,
) -> None:
    """One span in a trace of its own, stored as given (no pricing runs)."""
    number = next(_SPAN_COUNTER)
    await execute(
        session_factory,
        _INSERT_SPAN,
        {
            "project_id": project_id,
            "trace_id": f"{number:032x}",
            "span_id": f"{number:016x}",
            "kind": kind,
            "started_at": at,
            "provider": provider,
            "model": model,
            "service_tier": tier,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached,
            "cache_write_tokens": write,
            "cache_write_1h_tokens": write_1h,
            "web_search_requests": searches,
            "cost_usd": None if cost is None else Decimal(cost),
            "fee_usd": None if fee is None else Decimal(fee),
            "pricing_version": "2026-10-16" if cost is not None else None,
            "attributes": json.dumps(attributes or {}),
            "usage_rollup": rollup,
        },
    )


async def _refresh(session_factory: SessionFactory, project_id: uuid.UUID, days: list[date]) -> int:
    async with session_factory() as db:
        await bind_project(db, project_id)
        written = await refresh_cost_rollups(db, project_id, days)
        await db.commit()
    return written


async def _rows(session_factory: SessionFactory) -> list[dict[str, Any]]:
    return await fetch(session_factory, _ROWS)


def _figures(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in _SKIP}


def _row(
    provider: str | None,
    canonical: str | None,
    model: str | None,
    pattern: str | None,
    tier: str | None,
    **counts: Any,
) -> dict[str, Any]:
    """An expected row: the dimensions, then every count (0 unless given) and both sums."""
    figures: dict[str, Any] = {
        "day": DAY,
        "provider": provider,
        "provider_canonical": canonical,
        "model": model,
        "model_pattern": pattern,
        "service_tier": tier,
    }
    for column in (
        "calls",
        "priced_calls",
        "unpriced_calls",
        "usage_unreported_calls",
        "cut_stream_calls",
        "cache_hit_calls",
        "unmodelled_modifier_calls",
        "uncached_input_tokens",
        "cache_read_tokens",
        "cache_write_5m_tokens",
        "cache_write_1h_tokens",
        "output_tokens",
        "web_search_requests",
    ):
        figures[column] = counts.pop(column, 0)
    figures["cost_usd"] = counts.pop("cost_usd", None)
    figures["fee_usd"] = counts.pop("fee_usd", None)
    assert not counts, counts
    return figures


async def _seed_every_class(session_factory: SessionFactory, project: uuid.UUID) -> None:
    dated = "gpt-4o-2024-08-06"
    # Priced standard: 800 uncached + 200 cache reads.
    await _span(
        session_factory, project, model=dated, tier="standard",
        input_tokens=1000, output_tokens=100, cached=200, cost="0.00325000",
    )  # fmt: skip
    # A gateway cache hit: zero usage, cost 0.
    await _span(
        session_factory, project, model=dated, tier="standard", cost="0",
        attributes={"spanlight.cache": "hit"},
    )  # fmt: skip
    # Web search: the fee is inside cost_usd and also stored apart.
    await _span(
        session_factory, project, model=dated, tier="standard",
        input_tokens=500, output_tokens=50, searches=3, cost="0.03175000", fee="0.03000000",
    )  # fmt: skip
    # Fast mode: priced at the standard rate and flagged.
    await _span(
        session_factory, project, model=dated, tier="standard",
        input_tokens=100, output_tokens=10, cost="0.00035000",
        attributes={"usage.pricing_modifier_unknown": "fast_mode"},
    )  # fmt: skip
    # Usage not reported: the three stream reasons and one that is not a cut stream.
    for reason in ("client_disconnected", "stream_error", "stream_cut_by_fault", "no_final_usage"):
        await _span(
            session_factory, project, model=dated, tier="standard",
            input_tokens=300, output_tokens=None, attributes={"usage.unreported_reason": reason},
        )  # fmt: skip
    # Batch, half price.
    await _span(
        session_factory, project, tier="batch",
        input_tokens=1000, output_tokens=100, cost="0.00175000",
    )  # fmt: skip
    # OpenAI priority: priced at the standard rate, counted as an unmodelled modifier.
    await _span(
        session_factory, project, tier="priority",
        input_tokens=1000, output_tokens=100, cost="0.00350000",
    )  # fmt: skip
    # Cache writes of both lifetimes, and cache reads above the prompt (clamped to it).
    await _span(
        session_factory, project, provider="anthropic", model="claude-sonnet-4-6",
        input_tokens=2000, output_tokens=200, cached=500, write=800, write_1h=300,
        cost="0.00892500",
    )  # fmt: skip
    await _span(
        session_factory, project, provider="anthropic", model="claude-sonnet-4-6",
        input_tokens=1000, output_tokens=10, cached=5000, write=100, cost="0.00045000",
    )  # fmt: skip
    # A model with no price: both counts known, no cost.
    await _span(
        session_factory, project, model="my-model-x", tier="standard",
        input_tokens=100, output_tokens=10,
    )  # fmt: skip
    # Not an LLM span, and an LLM span of another day: neither counts.
    await _span(
        session_factory, project, kind="tool", input_tokens=999, output_tokens=999, cost="1",
    )  # fmt: skip
    await _span(
        session_factory, project, at=AT - timedelta(days=1), input_tokens=7, output_tokens=7,
    )  # fmt: skip


async def test_rollup_rows_match_hand_computed_totals(session_factory: SessionFactory) -> None:
    [project] = await new_projects(session_factory, 1)
    await _seed_every_class(session_factory, project)

    written = await _refresh(session_factory, project, [DAY])

    assert written == 5
    assert [_figures(row) for row in await _rows(session_factory)] == [
        _row(
            "anthropic", "anthropic", "claude-sonnet-4-6", "claude-sonnet-4-6", None,
            calls=2, priced_calls=2,
            # 2000 - 500 reads - 800 writes; 1000 - 1000 reads - 0 writes.
            uncached_input_tokens=700, cache_read_tokens=1500,
            cache_write_5m_tokens=500, cache_write_1h_tokens=300, output_tokens=210,
            cost_usd=Decimal("0.00937500"),
        ),
        _row(
            "openai", "openai", "gpt-4o", "gpt-4o", "batch",
            calls=1, priced_calls=1, uncached_input_tokens=1000, output_tokens=100,
            cost_usd=Decimal("0.00175000"),
        ),
        _row(
            "openai", "openai", "gpt-4o", "gpt-4o", "priority",
            calls=1, priced_calls=1, unmodelled_modifier_calls=1,
            uncached_input_tokens=1000, output_tokens=100, cost_usd=Decimal("0.00350000"),
        ),
        _row(
            "openai", "openai", "gpt-4o-2024-08-06", "gpt-4o", "standard",
            calls=8, priced_calls=4, usage_unreported_calls=4, cut_stream_calls=3,
            cache_hit_calls=1, unmodelled_modifier_calls=1,
            # 800 + 0 + 500 + 100 + 4 * 300 uncached.
            uncached_input_tokens=2600, cache_read_tokens=200, output_tokens=160,
            web_search_requests=3,
            cost_usd=Decimal("0.03535000"), fee_usd=Decimal("0.03000000"),
        ),
        _row(
            "openai", "openai", "my-model-x", None, "standard",
            calls=1, unpriced_calls=1, uncached_input_tokens=100, output_tokens=10,
        ),
    ]  # fmt: skip


async def test_rollup_parent_excluded_from_tokens_and_cost_but_counted(
    session_factory: SessionFactory,
) -> None:
    # A usage rollup (an outer span repeating its child's usage, migration 0900) is still a call
    # of its model, but its tokens and cost were already counted on the child.
    [project] = await new_projects(session_factory, 1)
    await _span(
        session_factory, project,
        input_tokens=1000, output_tokens=100, cached=200, searches=2,
        cost="0.03325000", fee="0.02000000", rollup=True,
    )  # fmt: skip
    await _span(
        session_factory, project,
        input_tokens=1000, output_tokens=100, cached=200, searches=2,
        cost="0.03325000", fee="0.02000000",
    )  # fmt: skip

    await _refresh(session_factory, project, [DAY])

    assert [_figures(row) for row in await _rows(session_factory)] == [
        _row(
            "openai", "openai", "gpt-4o", "gpt-4o", None,
            calls=2, priced_calls=2, uncached_input_tokens=800, cache_read_tokens=200,
            output_tokens=100, web_search_requests=2,
            cost_usd=Decimal("0.03325000"), fee_usd=Decimal("0.02000000"),
        ),
    ]  # fmt: skip


async def test_rollup_counts_cut_streams_apart_from_unpriced(
    session_factory: SessionFactory,
) -> None:
    [project] = await new_projects(session_factory, 1)
    for reason in ("client_disconnected", "stream_error", "stream_cut_by_fault"):
        await _span(
            session_factory, project, model="claude-sonnet-4-6", provider="anthropic",
            input_tokens=400, output_tokens=None, attributes={"usage.unreported_reason": reason},
        )  # fmt: skip
    await _span(
        session_factory, project, model="claude-sonnet-4-6", provider="anthropic",
        input_tokens=400, output_tokens=None,
        attributes={"usage.unreported_reason": "include_usage_not_set"},
    )  # fmt: skip
    # Output unknown with no reason, or a reason outside the enum: not "usage unreported".
    await _span(
        session_factory, project, model="claude-sonnet-4-6", provider="anthropic",
        input_tokens=400, output_tokens=None,
    )  # fmt: skip
    await _span(
        session_factory, project, model="claude-sonnet-4-6", provider="anthropic",
        input_tokens=400, output_tokens=None, attributes={"usage.unreported_reason": "other"},
    )  # fmt: skip
    # A reason on a span whose output is known does not count either.
    await _span(
        session_factory, project, model="claude-sonnet-4-6", provider="anthropic",
        input_tokens=400, output_tokens=10, cost="0.00135000",
        attributes={"usage.unreported_reason": "stream_error"},
    )  # fmt: skip

    await _refresh(session_factory, project, [DAY])

    [row] = await _rows(session_factory)
    assert row["calls"] == 7
    assert row["usage_unreported_calls"] == 4
    assert row["cut_stream_calls"] == 3
    # The cut streams are unknown, not unpriced and not cheap: no cost, no priced call.
    assert row["unpriced_calls"] == 0
    assert row["priced_calls"] == 1
    assert row["cost_usd"] == Decimal("0.00135000")
    assert row["model_pattern"] == "claude-sonnet-4-6"


# (input, cache reads, cache writes, 1-hour writes): the clamp edges of `token_classes`.
_CLASS_CASES: list[tuple[int | None, int | None, int | None, int | None]] = [
    (1000, None, None, None),
    (1000, 200, 100, 50),
    (1000, 5000, 100, None),  # reads above the prompt
    (1000, 400, 900, 100),  # writes above what the reads leave
    (1000, 400, 900, 800),  # 1-hour writes above the writes the clamp leaves
    (0, 10, 10, 10),
    (None, 5, 5, 5),  # no input count: nothing to split
]


async def test_rollup_token_classes_match_the_price_book_split(
    session_factory: SessionFactory,
) -> None:
    """The rollup SQL is a copy of `price_row.token_classes`; a drift between the two moves
    tokens between classes the report compares with the provider's lines."""
    [project] = await new_projects(session_factory, 1)
    for number, (input_tokens, cached, write, write_1h) in enumerate(_CLASS_CASES):
        await _span(
            session_factory, project, model=f"split-{number}", input_tokens=input_tokens,
            output_tokens=1, cached=cached, write=write, write_1h=write_1h,
        )  # fmt: skip

    await _refresh(session_factory, project, [DAY])

    by_model = {row["model"]: row for row in await _rows(session_factory)}
    for number, (input_tokens, cached, write, write_1h) in enumerate(_CLASS_CASES):
        split = token_classes(input_tokens or 0, cached, write, write_1h)
        row = by_model[f"split-{number}"]
        assert (
            row["uncached_input_tokens"],
            row["cache_read_tokens"],
            row["cache_write_5m_tokens"],
            row["cache_write_1h_tokens"],
        ) == (split.uncached, split.reads, split.writes_5m, split.writes_1h), _CLASS_CASES[number]


async def test_a_pricing_modifier_counts_only_on_calls_with_usage(
    session_factory: SessionFactory,
) -> None:
    """A call that failed before any usage came back was never priced at any rate, so its
    modifier attribute does not make it a call "priced at the standard rate with a modifier"."""
    [project] = await new_projects(session_factory, 1)
    modifier = {"usage.pricing_modifier_unknown": "fast_mode"}
    await _span(
        session_factory, project, input_tokens=100, output_tokens=10, cost="0.00035000",
        attributes=modifier,
    )  # fmt: skip
    await _span(
        session_factory, project, input_tokens=None, output_tokens=None, attributes=modifier
    )

    await _refresh(session_factory, project, [DAY])

    [row] = await _rows(session_factory)
    assert (row["calls"], row["unmodelled_modifier_calls"]) == (2, 1)


async def test_model_pattern_from_model_name(session_factory: SessionFactory) -> None:
    [project] = await new_projects(session_factory, 1)
    await _span(
        session_factory, project, model="gpt-4o-2024-08-06",
        input_tokens=10, output_tokens=1, cost="0.00003500",
    )  # fmt: skip
    # Cost unknown on every call of the row: still grouped under its pattern.
    await _span(
        session_factory, project, provider="anthropic", model="claude-sonnet-4-6",
        input_tokens=10, output_tokens=None,
        attributes={"usage.unreported_reason": "client_disconnected"},
    )  # fmt: skip
    await _span(session_factory, project, model="my-model-x", input_tokens=10, output_tokens=1)
    await _span(session_factory, project, model=None, input_tokens=10, output_tokens=1)

    await _refresh(session_factory, project, [DAY])

    patterns = {row["model"]: row["model_pattern"] for row in await _rows(session_factory)}
    assert patterns == {
        "gpt-4o-2024-08-06": "gpt-4o",
        "claude-sonnet-4-6": "claude-sonnet-4-6",
        "my-model-x": None,
        None: None,
    }


async def _org_of(session_factory: SessionFactory, project_id: uuid.UUID) -> uuid.UUID:
    [row] = await fetch(
        session_factory, "SELECT org_id FROM projects WHERE id = :id", {"id": project_id}
    )
    org_id: uuid.UUID = row["org_id"]
    return org_id


async def _override(
    session_factory: SessionFactory, org_id: uuid.UUID, pattern: str, effective_from: datetime
) -> None:
    await execute(
        session_factory,
        "INSERT INTO price_overrides (id, org_id, provider, model_pattern, input_per_mtok, "
        "output_per_mtok, effective_from) "
        "VALUES (:id, :org_id, 'openai', :pattern, 1, 2, :effective_from)",
        {
            "id": uuid.uuid4(),
            "org_id": org_id,
            "pattern": pattern,
            "effective_from": effective_from,
        },
    )


async def test_override_mid_day_gives_one_pattern(session_factory: SessionFactory) -> None:
    [project] = await new_projects(session_factory, 1)
    org_id = await _org_of(session_factory, project)
    model = "gpt-4o-2024-08-06"
    await _span(
        session_factory, project, model=model, at=midnight(DAY) + timedelta(hours=9),
        input_tokens=10, output_tokens=1, cost="0.00003500",
    )  # fmt: skip
    await _override(session_factory, org_id, model, midnight(DAY) + timedelta(hours=12))
    await _span(
        session_factory, project, model=model, at=midnight(DAY) + timedelta(hours=15),
        input_tokens=10, output_tokens=1, cost="0.00001200",
    )  # fmt: skip

    await _refresh(session_factory, project, [DAY])

    [row] = await _rows(session_factory)
    assert (row["model"], row["model_pattern"], row["calls"]) == (model, model, 2)


async def test_mapping_patterns_are_the_seed_and_the_orgs_overrides(
    session_factory: SessionFactory,
) -> None:
    mine, theirs = await new_projects(session_factory, 2)
    await _override(session_factory, await _org_of(session_factory, mine), "acme-1", AT)
    await _override(session_factory, await _org_of(session_factory, theirs), "other-1", AT)

    async with session_factory() as db:
        patterns = await mapping_patterns(db, await _org_of(session_factory, mine))

    assert "gpt-4o" in patterns and "claude-sonnet-4-6" in patterns
    assert "acme-1" in patterns and "other-1" not in patterns
    assert len(patterns) == len(set(patterns))


async def test_provider_canonical(session_factory: SessionFactory) -> None:
    [project] = await new_projects(session_factory, 1)
    await _span(session_factory, project, provider="vertex_ai", model="gemini-2.5-flash")
    await _span(session_factory, project, provider="Azure", model="gpt-4o")
    await _span(session_factory, project, provider="acme", model="gpt-4o")
    await _span(session_factory, project, provider=None, model="gpt-4o")

    await _refresh(session_factory, project, [DAY])

    canonical = {row["provider"]: row["provider_canonical"] for row in await _rows(session_factory)}
    # Azure is not an OpenAI alias: it bills its own rates, so it keeps its own canonical name.
    assert canonical == {"vertex_ai": "google", "Azure": "azure", "acme": "acme", None: None}


async def test_openai_priority_rows_count_as_unmodelled(session_factory: SessionFactory) -> None:
    [project] = await new_projects(session_factory, 1)
    await _span(
        session_factory, project, tier="priority",
        input_tokens=1000, output_tokens=100, cost="0.00350000",
    )  # fmt: skip
    # Also flagged on the span: still one call.
    await _span(
        session_factory, project, tier="priority",
        input_tokens=1000, output_tokens=100, cost="0.00350000",
        attributes={"usage.pricing_modifier_unknown": "fast_mode"},
    )  # fmt: skip
    await _span(
        session_factory, project, tier="standard",
        input_tokens=1000, output_tokens=100, cost="0.00350000",
    )  # fmt: skip
    # Anthropic priority is commitment-billed, not unmodelled.
    await _span(
        session_factory, project, provider="anthropic", model="claude-sonnet-4-6",
        tier="priority", input_tokens=10, output_tokens=1, cost="0.00004500",
    )  # fmt: skip

    await _refresh(session_factory, project, [DAY])

    counts = {
        (row["provider"], row["service_tier"]): row["unmodelled_modifier_calls"]
        for row in await _rows(session_factory)
    }
    assert counts == {
        ("anthropic", "priority"): 0,
        ("openai", "priority"): 2,
        ("openai", "standard"): 0,
    }


async def test_idempotent(session_factory: SessionFactory) -> None:
    [project] = await new_projects(session_factory, 1)
    await _seed_every_class(session_factory, project)

    await _refresh(session_factory, project, [DAY])
    first = await _rows(session_factory)
    await _refresh(session_factory, project, [DAY])
    second = await _rows(session_factory)

    assert [{k: v for k, v in row.items() if k != "computed_at"} for row in second] == [
        {k: v for k, v in row.items() if k != "computed_at"} for row in first
    ]


async def test_fee_counts_only_on_spans_whose_cost_is_known(
    session_factory: SessionFactory,
) -> None:
    [project] = await new_projects(session_factory, 1)
    # Priced, with a fee inside its cost.
    await _span(
        session_factory, project, input_tokens=500, output_tokens=50, searches=3,
        cost="0.03175000", fee="0.03000000",
    )  # fmt: skip
    # A cut stream that still counted searches: fee known, token cost (so cost_usd) unknown.
    await _span(
        session_factory, project, input_tokens=500, output_tokens=None, searches=2,
        fee="0.02000000", attributes={"usage.unreported_reason": "client_disconnected"},
    )  # fmt: skip
    # A row whose only fee is on a span with no cost.
    await _span(
        session_factory, project, model="gpt-4o-mini", input_tokens=500, output_tokens=None,
        searches=1, fee="0.01000000",
        attributes={"usage.unreported_reason": "stream_error"},
    )  # fmt: skip

    await _refresh(session_factory, project, [DAY])

    rows = {row["model"]: row for row in await _rows(session_factory)}
    # cost_usd - fee_usd is exactly the token part of the priced spans.
    assert (rows["gpt-4o"]["cost_usd"], rows["gpt-4o"]["fee_usd"]) == (
        Decimal("0.03175000"),
        Decimal("0.03000000"),
    )
    assert rows["gpt-4o"]["web_search_requests"] == 5
    assert (rows["gpt-4o-mini"]["cost_usd"], rows["gpt-4o-mini"]["fee_usd"]) == (None, None)


async def test_blank_provider_and_model_fold_into_the_unknown_one(
    session_factory: SessionFactory,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await _span(session_factory, project, provider="", model="", input_tokens=1, output_tokens=1)
    await _span(
        session_factory, project, provider=None, model=None, input_tokens=1, output_tokens=1
    )

    await _refresh(session_factory, project, [DAY])

    [row] = await _rows(session_factory)
    assert (row["provider"], row["model"], row["calls"]) == (None, None, 2)


async def test_refreshes_of_one_project_run_one_at_a_time(session_factory: SessionFactory) -> None:
    [project] = await new_projects(session_factory, 1)
    for offset in range(8):
        for model in ("gpt-4o", "gpt-4o-mini", "gpt-4.1"):
            await _span(
                session_factory, project, model=model, at=AT - timedelta(days=offset),
                input_tokens=1, output_tokens=1,
            )  # fmt: skip
    old_day, recent = DAY - timedelta(days=7), [DAY - timedelta(days=2), DAY]

    # Disjoint days, so no rollup row is shared: only the per-project lock makes the second wait.
    async with session_factory() as first:
        await bind_project(first, project)
        await refresh_cost_rollups(first, project, [old_day])
        second = asyncio.create_task(_refresh(session_factory, project, recent))
        await asyncio.sleep(0.5)
        assert not second.done()
        await first.commit()
    assert await asyncio.wait_for(second, timeout=10) == 9

    # Overlapping ranges at once (the 15-minute and the nightly pass): both succeed.
    deep = [DAY - timedelta(days=offset) for offset in range(8)]
    results = await asyncio.gather(
        _refresh(session_factory, project, deep), _refresh(session_factory, project, recent)
    )
    assert results == [24, 9]
    assert len(await _rows(session_factory)) == 24


async def test_refresh_of_a_deleted_project_writes_nothing(session_factory: SessionFactory) -> None:
    [project] = await new_projects(session_factory, 1)
    await _span(session_factory, project, input_tokens=1, output_tokens=1)
    await execute(session_factory, "DELETE FROM projects WHERE id = :id", {"id": project})

    assert await _refresh(session_factory, project, [DAY]) == 0
    assert await _rows(session_factory) == []


async def test_deletes_stale_rows(session_factory: SessionFactory) -> None:
    [project] = await new_projects(session_factory, 1)
    before = DAY - timedelta(days=5)
    await _span(session_factory, project, model="gpt-4o", input_tokens=1, output_tokens=1)
    await _span(session_factory, project, model="gpt-4o-mini", input_tokens=1, output_tokens=1)
    await _span(
        session_factory, project, at=midnight(before), model="gpt-4o", input_tokens=1,
        output_tokens=1,
    )  # fmt: skip
    await _refresh(session_factory, project, [before, DAY])
    assert len(await _rows(session_factory)) == 3

    # The mini spans go (retention, say); the old day is outside the next refresh's range.
    await execute(
        session_factory, "DELETE FROM spans WHERE model = 'gpt-4o-mini' OR started_at < :day",
        {"day": midnight(DAY)},
    )  # fmt: skip
    await _refresh(session_factory, project, [DAY - timedelta(days=1), DAY])

    assert [(row["day"], row["model"]) for row in await _rows(session_factory)] == [
        (before, "gpt-4o"),
        (DAY, "gpt-4o"),
    ]


async def test_three_day_window_and_deep_window(
    session_factory: SessionFactory, settings: Settings
) -> None:
    [project] = await new_projects(session_factory, 1)
    for offset in range(10):
        await _span(
            session_factory, project, at=AT - timedelta(days=offset),
            input_tokens=1, output_tokens=1,
        )  # fmt: skip
    context = await _context(settings, session_factory, ROLLUP_COST_DAILY.kind)

    await run_rollup_cost_daily(context, {})
    days = [row["day"] for row in await _rows(session_factory)]
    assert days == [DAY - timedelta(days=offset) for offset in (2, 1, 0)]

    await execute(session_factory, "DELETE FROM cost_rollups_daily")
    await run_rollup_cost_daily_deep(context, {})
    days = [row["day"] for row in await _rows(session_factory)]
    assert days == deep_days(NOW) == [DAY - timedelta(days=offset) for offset in range(7, -1, -1)]


def test_rollup_jobs_are_registered_and_always_scheduled(settings: Settings) -> None:
    assert (ROLLUP_COST_DAILY.kind, ROLLUP_COST_DAILY.every) == (
        "rollup_cost_daily",
        timedelta(minutes=15),
    )
    assert ROLLUP_COST_DAILY.max_attempts == 1
    assert (ROLLUP_COST_DAILY_DEEP.kind, ROLLUP_COST_DAILY_DEEP.every) == (
        "rollup_cost_daily_deep",
        timedelta(days=1),
    )
    assert ROLLUP_COST_DAILY_DEEP.max_attempts == 1
    assert ROLLUP_COST_DAILY_DEEP.run_after_offset == timedelta(hours=2, minutes=30)
    assert TASKS["rollup_cost_daily"] is run_rollup_cost_daily
    assert TASKS["rollup_cost_daily_deep"] is run_rollup_cost_daily_deep
    assert TASKS[BACKFILL_JOB] is run_rollup_cost_daily_backfill
    quiet = settings.model_copy(update={"user_stats_enabled": False, "detectors_enabled": False})
    assert ROLLUP_COST_DAILY in periodic_jobs(quiet)
    assert ROLLUP_COST_DAILY_DEEP in periodic_jobs(quiet)


async def test_backfill_payload(session_factory: SessionFactory, settings: Settings) -> None:
    [project, other] = await new_projects(session_factory, 2)
    for offset in range(5):
        await _span(
            session_factory, project, at=AT - timedelta(days=offset),
            input_tokens=1, output_tokens=1,
        )  # fmt: skip
    await _span(session_factory, other, input_tokens=1, output_tokens=1)
    payload = backfill_payload(project, DAY - timedelta(days=3), DAY - timedelta(days=1))
    assert payload == {
        "project_id": str(project),
        "day_from": "2026-10-13",
        "day_to": "2026-10-15",
    }

    await run_rollup_cost_daily_backfill(
        await _context(settings, session_factory, BACKFILL_JOB), payload
    )

    rows = await _rows(session_factory)
    assert {row["project_id"] for row in rows} == {project}
    assert [row["day"] for row in rows] == [DAY - timedelta(days=offset) for offset in (3, 2, 1)]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"project_id": "not-a-uuid", "day_from": "2026-10-01", "day_to": "2026-10-02"},
        {"project_id": str(uuid.uuid4()), "day_from": "2026-10-02", "day_to": "2026-10-01"},
        {"project_id": str(uuid.uuid4()), "day_from": "2026-09-01", "day_to": "2026-10-02"},
    ],
)
async def test_backfill_rejects_a_bad_payload(
    session_factory: SessionFactory, settings: Settings, payload: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="backfill"):
        await run_rollup_cost_daily_backfill(
            await _context(settings, session_factory, BACKFILL_JOB), payload
        )


async def test_rollup_all_skips_projects_without_llm_spans(
    session_factory: SessionFactory,
) -> None:
    active, quiet = await new_projects(session_factory, 2)
    await _span(session_factory, active, input_tokens=1, output_tokens=1)
    await _span(session_factory, quiet, kind="tool")

    run = await rollup_all(session_factory, now=NOW)

    assert (run.projects, run.projects_failed, run.rows) == (1, 0, 1)


async def _context(settings: Settings, session_factory: SessionFactory, kind: str) -> TaskContext:
    """A claimed job of `kind` (its heartbeat needs a real lease), on a clock pinned to `NOW`."""
    async with session_factory() as db:
        await enqueue(db, kind, max_attempts=1)
        await db.commit()
        job = await claim_next(db)
    assert isinstance(job, ClaimedJob)
    return TaskContext(
        job=job, settings=settings, session_factory=session_factory, clock=lambda: NOW
    )
