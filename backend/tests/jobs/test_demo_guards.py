"""The demo job's guards: the project budget the gateway enforces, and spend it cannot record.

A paid job gets one attempt, so each case runs the job once on the shared `fake_provider` and
reads what reached the provider and what was stored.
"""

from typing import Any

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import Span, SpanKind
from app.db.rls import bypass_rls
from app.gateway import call, recorder, span_write
from app.jobs.outcome import JobOutcome
from app.jobs.tasks import demo_traffic
from app.jobs.tasks.demo_flow import DemoSpendUnrecordedError
from tests.gateway_support import FakeProvider, ManualClock
from tests.jobs.demo_support import (
    SessionFactory,
    anthropic_reply,
    fire_demo_block_budget,
    keyed,
    run_context,
    script_flow,
)


async def _spans(session_factory: SessionFactory) -> list[Span]:
    async with session_factory() as db:
        await bypass_rls(db)
        return list((await db.scalars(select(Span).order_by(Span.started_at))).all())


async def test_demo_traffic_skips_at_a_firing_project_block_budget(
    session_factory: SessionFactory,
    settings: Settings,
    demo_provider: FakeProvider,
    app_clock: ManualClock,
) -> None:
    # The demo goes through the same budget guard as every other in-process caller: a firing
    # `block` budget on the demo project ends the call before the provider is reached. That is a
    # stop, not a failure: the job ends `skipped`, like the missing-key skip, and asks again later.
    configured = keyed(settings)
    await fire_demo_block_budget(session_factory, configured, app_clock)

    outcome = await demo_traffic.run_demo_traffic(
        await run_context(session_factory, configured, app_clock), {}
    )

    assert outcome is JobOutcome.SKIPPED_BUDGET
    assert demo_provider.requests == []
    spans = await _spans(session_factory)
    (blocked,) = [span for span in spans if span.name.startswith("messages ")]
    assert blocked.status.value == "error"
    assert blocked.attributes["spanlight.budget.blocked"] is True
    assert blocked.cost_usd is None
    assert [span.name for span in spans if span.kind is SpanKind.CHAIN] == ["support-bot"]


async def test_demo_traffic_unwritten_span_is_spend_it_cannot_account_for(
    session_factory: SessionFactory,
    settings: Settings,
    demo_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first call is paid, but the recorder cannot store its span: the month's cost then
    # misses that spend, so the flow must not make a second paid call.
    script_flow(demo_provider)

    async def broken_ingest(*_: Any, **__: Any) -> None:
        raise ConnectionError("database went away")

    monkeypatch.setattr(span_write, "ingest_spans", broken_ingest)
    # Every retry fails the same way; no need to wait the real backoff.
    monkeypatch.setattr(recorder, "RECORD_RETRY_DELAYS_S", (0.0, 0.0, 0.0))

    with pytest.raises(DemoSpendUnrecordedError, match="demo call 1 was not stored"):
        await demo_traffic.run_demo_traffic(await run_context(session_factory, keyed(settings)), {})

    assert len(demo_provider.requests) == 1
    assert [span.name for span in await _spans(session_factory)] == ["support-bot"]


async def test_demo_traffic_call_with_no_span_is_spend_it_cannot_account_for(
    session_factory: SessionFactory,
    settings: Settings,
    demo_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The span of a paid call could not even be built, so nothing was handed to the recorder.
    # "Nothing to write" must not read as "written": no second paid call.
    demo_provider.reply_json(anthropic_reply("billing", 40, 1))

    def no_span(*_: Any, **__: Any) -> dict[str, Any]:
        raise ValueError("span could not be built")

    monkeypatch.setattr(call, "build_span", no_span)

    with pytest.raises(DemoSpendUnrecordedError, match="demo call 1 was not stored"):
        await demo_traffic.run_demo_traffic(await run_context(session_factory, keyed(settings)), {})

    assert len(demo_provider.requests) == 1
    assert [span.name for span in await _spans(session_factory)] == ["support-bot"]
