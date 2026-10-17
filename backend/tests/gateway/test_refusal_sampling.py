"""Key-limit refusal spans are sampled: at most one per gateway key per second.

Failure modes pinned here: a refusal flood from one key turning every 429 into a span write (the
limiter would then amplify writes instead of bounding them); suppressed refusals that vanish from
the counts instead of riding on the key's next span; one key's sampling silencing another key;
and a sampler whose memory grows with the number of keys it has seen.
"""

import uuid
from collections.abc import Sequence
from typing import Any

import pytest

from app.gateway import refusal_span
from app.gateway.context import GatewayRequest
from app.gateway.errors import rate_limited
from app.gateway.refusal_sampler import RefusalSampler
from app.gateway.refusal_span import record_refusal
from tests.gateway_fixtures import GatewayContextFactory
from tests.gateway_support import ManualClock

SUPPRESSED = "spanlight.gateway.refusals_suppressed"
FLOOD = 10_000


class _Spans:
    """A `SpanSink` that keeps every span it is given."""

    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []

    def add(self, span: dict[str, Any], credential_ids: Sequence[uuid.UUID]) -> None:
        self.spans.append(span)


@pytest.fixture(autouse=True)
def own_sampler(monkeypatch: pytest.MonkeyPatch) -> RefusalSampler:
    sampler = RefusalSampler()
    monkeypatch.setattr(refusal_span, "_sampler", sampler)
    return sampler


def _refusal() -> GatewayRequest:
    return GatewayRequest(surface="chat_completions", body={}, stream=False)


# --- the sampler (pure) ------------------------------------------------------------------------


def test_first_refusal_of_a_key_gets_a_span_then_the_key_waits_a_second() -> None:
    sampler = RefusalSampler(interval_s=1.0)
    key = uuid.uuid4()

    assert sampler.admit(key, 100.0) == 0
    assert [sampler.admit(key, 100.0 + i / 10) for i in range(1, 10)] == [None] * 9
    assert sampler.pending(key) == 9
    assert sampler.admit(key, 101.0) == 9  # the next span carries the nine in between
    assert sampler.pending(key) == 0


def test_keys_are_sampled_independently() -> None:
    sampler = RefusalSampler(interval_s=1.0)
    first, second = uuid.uuid4(), uuid.uuid4()

    assert sampler.admit(first, 5.0) == 0
    assert sampler.admit(second, 5.0) == 0
    assert sampler.admit(first, 5.5) is None
    assert sampler.admit(second, 5.5) is None


def test_memory_is_bounded_by_the_key_cap() -> None:
    sampler = RefusalSampler(interval_s=1.0, max_keys=100)

    for i in range(1_000):
        assert sampler.admit(uuid.uuid4(), float(i) / 1_000) == 0

    assert len(sampler) == 100


def test_a_forgotten_key_starts_over() -> None:
    sampler = RefusalSampler(interval_s=1.0, max_keys=2)
    old, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sampler.admit(old, 0.0)
    sampler.admit(old, 0.1)  # one suppressed, held for the key's next span
    sampler.admit(a, 0.2)
    sampler.admit(b, 0.3)  # the map is full: `old`, whose last span is oldest, is forgotten

    assert sampler.pending(old) == 0
    assert sampler.admit(old, 0.4) == 0  # its held count is lost: the counts are a lower bound


# --- record_refusal ----------------------------------------------------------------------------


async def test_refusal_flood_from_one_key_writes_about_one_span_a_second(
    gateway_context: GatewayContextFactory,
) -> None:
    clock = ManualClock()
    sink = _Spans()
    context = await gateway_context(keyed=True, clock=clock, span_sink=sink)
    error = rate_limited("rpm", 1, 30, key_prefix="abc")
    step_s = 0.0005  # 10,000 refusals over 5 seconds: 2,000 a second

    for _ in range(FLOOD):
        record_refusal(_refusal(), context, error)
        clock.advance(step_s)

    elapsed_s = FLOOD * step_s
    assert len(sink.spans) <= elapsed_s + 1
    assert len(sink.spans) >= elapsed_s - 1
    suppressed = [span["attributes"][SUPPRESSED] for span in sink.spans]
    assert suppressed[0] == 0
    assert all(n > 0 for n in suppressed[1:])
    # Every refusal is a span, a count carried by a later span, or still held for the next one.
    assert context.key_id is not None
    held = refusal_span.refusal_sampler().pending(context.key_id)
    assert len(sink.spans) + sum(suppressed) + held == FLOOD


async def test_refusal_after_the_interval_carries_the_suppressed_count(
    gateway_context: GatewayContextFactory,
) -> None:
    clock = ManualClock()
    sink = _Spans()
    context = await gateway_context(keyed=True, clock=clock, span_sink=sink)
    error = rate_limited("rpm", 1, 30, key_prefix="abc")

    for _ in range(5):
        record_refusal(_refusal(), context, error)
    clock.advance(1.0)
    record_refusal(_refusal(), context, error)

    assert [span["attributes"][SUPPRESSED] for span in sink.spans] == [0, 4]
