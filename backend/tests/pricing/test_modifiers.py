"""Pricing modifiers the provider reports in the response (G-010).

Failure modes: an Anthropic fast-mode call with US-only inference is billed at 2 x 1.1 the
standard rate on every token class, cache reads and writes included, but Spanlight stored the
standard figure; an OpenAI Ultrafast call (6x standard) normalised to no tier, was priced at
standard and was not even counted as unmodelled in the reconciliation.
"""

from datetime import timedelta
from decimal import Decimal

from app.ingest.normalize import normalize_span
from app.ingest.schemas import SpanIn
from app.pricing.explain import SpanCostInputs, explain_span
from app.pricing.prices import EFFECTIVE_FROM, PRICING_VERSION, seed_price_book
from app.pricing.tiers import multipliers_for
from tests.helpers import new_trace_id, span
from tests.jobs.bypass_support import SessionFactory, new_projects
from tests.reconciliation.test_rollup_cost_daily import DAY, _refresh, _rows, _span

START = EFFECTIVE_FROM + timedelta(hours=12)


def test_fast_mode_and_us_inference_stack_on_every_token_class() -> None:
    book = seed_price_book()
    raw = span(
        trace_id=new_trace_id(),
        start=START,
        provider="anthropic",
        model="claude-opus-5-5",
        # 3,000 uncached, 4,000 cache reads, 2,000 5-minute and 1,000 1-hour writes.
        usage={
            "input_tokens": 10_000,
            "output_tokens": 1_000,
            "cached_tokens": 4_000,
            "cache_write_tokens": 3_000,
            "cache_write_1h_tokens": 1_000,
        },
        attributes={
            "usage.speed": "fast",
            "usage.inference_geo": "us",
            # The request-side guess; the response fields above supersede it.
            "usage.pricing_modifier_unknown": "inference_geo_us",
        },
    )
    stored = normalize_span(
        SpanIn.model_validate(raw), capture_payloads=False, prices=book, now=START
    )

    # Standard: 3,000 x $4 + 4,000 x $0.20 + 2,000 x $5 + 1,000 x $8 + 1,000 x $20 = $0.0508.
    assert stored.cost_usd == Decimal("0.0508") * 2 * Decimal("1.1")
    assert stored.pricing_version == PRICING_VERSION
    assert "usage.pricing_modifier_unknown" not in stored.attributes

    inputs = SpanCostInputs(
        provider=stored.provider,
        model=stored.model,
        started_at=stored.started_at,
        input_tokens=stored.input_tokens,
        output_tokens=stored.output_tokens,
        cached_tokens=stored.cached_tokens,
        cache_write_tokens=stored.cache_write_tokens,
        cache_write_1h_tokens=stored.cache_write_1h_tokens,
        web_search_requests=None,
        service_tier=None,
        reasoning_tokens=None,
        usage_unreported_reason=None,
        stored_cost_usd=stored.cost_usd,
        stored_fee_usd=None,
        pricing_version=stored.pricing_version,
        is_cache_hit=False,
        modifiers=("inference_geo_us", "fast"),
    )
    price = book.find("anthropic", "claude-opus-5-5", START, prompt_tokens=10_000)
    explanation = explain_span(
        inputs, price, None, multipliers_for(PRICING_VERSION, START), "found"
    )
    assert explanation.status == "ok"
    assert {line.kind: line.multiplier for line in explanation.lines} == dict.fromkeys(
        ("uncached_input", "cache_read", "cache_write_5m", "cache_write_1h", "output"),
        Decimal("2.2"),
    )
    assert explanation.message is not None
    assert "Data residency \N{MULTIPLICATION SIGN}1.1" in explanation.message
    assert "Fast mode \N{MULTIPLICATION SIGN}2" in explanation.message


async def test_an_ultrafast_call_counts_as_unmodelled(session_factory: SessionFactory) -> None:
    [project] = await new_projects(session_factory, 1)
    # `ultrafast` normalises to no stored tier: the span keeps the raw spelling only.
    await _span(
        session_factory, project, model="gpt-6-astra", input_tokens=100, output_tokens=10,
        attributes={"usage.service_tier_raw": "ultrafast"},
    )  # fmt: skip

    await _refresh(session_factory, project, [DAY])

    [row] = await _rows(session_factory)
    assert (row["calls"], row["unmodelled_modifier_calls"]) == (1, 1)
