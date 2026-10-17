"""Synthetic rate-limit traffic for `rate_limit_pressure`: a share of 429 refusals among
ordinary calls, read as a requests-per-minute limit or as Anthropic's spend cap.

Real 429s are not reproducible on demand and the Lab's are excluded by design, so these are
synthetic spans (`POST /v1/traces`). The spend-cap refusals carry the `provider.error.*`
attributes the gateway and the SDK record for Anthropic's spend-cap body, so ingest classifies
them exactly as it classifies the real refusal (`tests/accuracy/test_synthetic.py`).
"""

import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from accuracy.doctor.common import HAIKU, ingest, now_utc
from accuracy.doctor.context import RunContext
from accuracy.doctor.spans import (
    SPEND_CAP_MESSAGE,
    llm_span,
    marked,
    spend_cap_attributes,
    spread,
)
from accuracy.doctor.traffic import MINUTES

REQUESTS_429 = (
    "429 rate_limit_error: This request would exceed your organization's rate limit of 50 "
    "requests per minute."
)


@dataclass(frozen=True, slots=True)
class Refusal:
    """How the planted 429s of a rate-limit batch read: their message and provider fields."""

    message: str
    attributes: Mapping[str, Any] = field(default_factory=dict)


def rate_limit_spans(
    *,
    seed: str,
    model: str,
    environment: str,
    now: datetime,
    calls: int,
    limited: int,
    refusal: Refusal,
) -> list[dict[str, Any]]:
    """`calls` llm spans over the ten minutes before `now`, `limited` of them 429 refusals that
    read as `refusal`; the rest are ordinary successes. Pure (seeded by `seed`)."""
    rnd = random.Random(seed)
    spans = []
    for index, started in enumerate(spread(now - 11 * MINUTES, now - 1 * MINUTES, calls)):
        hit = marked(index, limited, calls)
        spans.append(
            llm_span(
                started=started,
                duration_ms=rnd.randint(120, 400) if hit else rnd.randint(500, 1500),
                model=model,
                environment=environment,
                input_tokens=rnd.randint(300, 900),
                output_tokens=0 if hit else rnd.randint(40, 120),
                status="error" if hit else "ok",
                status_message=refusal.message if hit else None,
                http_status=429 if hit else None,
                finish_reason=None if hit else "end_turn",
                session_id=f"rl-{index % 8}",
                prompt=f"Draft a reply to ticket {index}.",
                attributes=refusal.attributes if hit else None,
            )
        )
    return spans


def spend_cap_spans(
    *, seed: str, model: str, environment: str, now: datetime, calls: int, limited: int
) -> list[dict[str, Any]]:
    """A rate-limit batch whose 429s are Anthropic's spend-cap refusal (no retry-after)."""
    refusal = Refusal(SPEND_CAP_MESSAGE, spend_cap_attributes())
    return rate_limit_spans(
        seed=seed,
        model=model,
        environment=environment,
        now=now,
        calls=calls,
        limited=limited,
        refusal=refusal,
    )


def _ingest_rate_limit_batch(ctx: RunContext, spans: list[dict[str, Any]]) -> None:
    ctx.record_spans(spans)
    ctx.note(f"synthetic: {ingest(ctx.project['ingest_key'], spans)} spans")


def run_rate_limit_synthetic(ctx: RunContext, *, calls: int, limited: int) -> None:
    """`limited` of `calls` spans in the last ten minutes are real-looking requests-per-minute
    429s."""
    spans = rate_limit_spans(
        seed=ctx.scenario_id,
        model=ctx.model(HAIKU),
        environment=ctx.environment,
        now=now_utc(),
        calls=calls,
        limited=limited,
        refusal=Refusal(REQUESTS_429),
    )
    _ingest_rate_limit_batch(ctx, spans)


def run_rate_limit_spend_cap(ctx: RunContext, *, calls: int, limited: int) -> None:
    """`limited` of `calls` spans in the last ten minutes are Anthropic spend-cap 429s, with the
    `provider.error.*` attributes the gateway and the SDK record for that body."""
    spans = spend_cap_spans(
        seed=ctx.scenario_id,
        model=ctx.model(HAIKU),
        environment=ctx.environment,
        now=now_utc(),
        calls=calls,
        limited=limited,
    )
    _ingest_rate_limit_batch(ctx, spans)
