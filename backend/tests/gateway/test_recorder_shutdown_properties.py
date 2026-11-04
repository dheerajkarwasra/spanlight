"""Bounded cancellation schedules preserve exactly one loss per abandoned write."""

import asyncio

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.core.observability import GATEWAY_RECORD_FAILURES
from app.gateway import recorder
from app.gateway.execute import execute
from tests.gateway.support import chat_request
from tests.gateway_fixtures import GatewayContextFactory
from tests.gateway_support import FakeProvider, fixture_text, metric_delta


@given(count=st.integers(1, 3), cancellations=st.integers(0, 3))
@settings(
    max_examples=12,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
async def test_cancelled_queued_writes_have_one_loss_each(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    cancellations: int,
) -> None:
    monkeypatch.setattr(recorder, "_writers", None)
    context = await gateway_context(keyed=False)
    writers = recorder._writers_for(context)
    for _ in range(context.settings.gateway_record_concurrency):
        await writers.slots.acquire()
    errors = metric_delta(GATEWAY_RECORD_FAILURES, reason="error")
    for _ in range(count):
        fake_provider.reply_json(fixture_text("openai/chat.json").encode())
        assert (await execute(chat_request(stream=False), context)).status == 200
    drain = asyncio.create_task(recorder.drain(timeout_s=0))
    await asyncio.sleep(0)
    for _ in range(cancellations):
        drain.cancel()
        await asyncio.sleep(0)
    await asyncio.gather(drain, return_exceptions=True)
    await recorder.drain(timeout_s=0)
    assert errors.value == count
    assert recorder.pending_tasks() == ()
    assert writers.held_bytes == 0
