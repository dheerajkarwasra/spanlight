"""Scenario traffic made of synthetic spans (`POST /v1/traces`): the backdated baselines, the
unknown model and the organization-wide 5xx storms (the rate-limit shares are in
`traffic_rate_limit`). Used only where no
real call can produce the condition (see the README)."""

import random
from collections.abc import Sequence
from typing import Any

from accuracy.doctor.common import HAIKU, PROVIDER, ingest, now_utc
from accuracy.doctor.context import RunContext
from accuracy.doctor.spans import llm_span, marked, spread
from accuracy.doctor.traffic import HOURS, MINUTES

BASELINE_HOURS = (40, 26)
"""Backdated baseline spans start between 40 and 26 hours ago: inside the previous-7-day
baseline period once the detectors run, and inside the 48-hour rollup recompute window."""

UNKNOWN_MODEL = "acme-llm-7b"

LAB_5XX_ATTRIBUTES: dict[str, Any] = {
    "spanlight.gateway.route": "doctor",
    "spanlight.gateway.attempts": 0,
    "spanlight.gateway.upstream_status": 503,
    "spanlight.fault.scenario": "provider_5xx",
    "spanlight.fault.profile_id": "00000000-0000-4000-8000-000000000001",
}
"""What the gateway records on a span the Lab's `provider_5xx` fault answered."""


def baseline_spans(
    ctx: RunContext,
    *,
    model: str = HAIKU,
    calls: int = 240,
    errors: int = 2,
    duration_ms: tuple[int, int] = (200, 500),
    tokens: tuple[int, int] = (900, 60),
) -> None:
    """Backdated, synthetic llm spans that give the project a 7-day baseline.

    `calls` spans spread over `BASELINE_HOURS` ago, `errors` of them failed, durations uniform
    in `duration_ms`, usage around `tokens`. The spans sit in the previous-7-day period of a
    detector run made in the next few hours, and in the 48-hour window `rollup_hourly`
    recomputes, so one rollup after ingest gives the detectors their `error_rate`, `p95_ms`
    and `cost_usd_per_hour` slices for the scenario's environment and model.
    """
    rnd = random.Random(ctx.scenario_id)
    now = now_utc()
    start = now - BASELINE_HOURS[0] * HOURS
    end = now - BASELINE_HOURS[1] * HOURS
    every = calls // errors if errors else 0
    spans = []
    for index, started in enumerate(spread(start, end, calls)):
        failed = bool(every) and index % every == 1
        spans.append(
            llm_span(
                started=started,
                duration_ms=rnd.randint(*duration_ms),
                model=ctx.model(model),
                environment=ctx.environment,
                input_tokens=tokens[0] + rnd.randint(-80, 80),
                output_tokens=tokens[1] + rnd.randint(-10, 10),
                status="error" if failed else "ok",
                status_message="529 overloaded_error: Overloaded" if failed else None,
                http_status=529 if failed else None,
                finish_reason=None if failed else "end_turn",
                session_id=f"base-{index // 6}",
                user_id=f"user-{index % 17}",
                prompt=f"Summarise ticket {index} for the weekly report.",
            )
        )
    accepted = ingest(ctx.project["ingest_key"], spans)
    ctx.note(f"baseline: {accepted} spans backdated {BASELINE_HOURS[0]}-{BASELINE_HOURS[1]} h")


def run_unpriced(ctx: RunContext, *, calls: int, usage: bool) -> None:
    """`calls` spans of a model no price book knows, with (`usage=True`) or without usage."""
    rnd = random.Random(ctx.scenario_id)
    now = now_utc()
    spans = [
        llm_span(
            started=started,
            duration_ms=rnd.randint(300, 900),
            model=UNKNOWN_MODEL,
            provider="acme",
            environment=ctx.environment,
            input_tokens=rnd.randint(200, 600),
            output_tokens=rnd.randint(30, 90),
            session_id=f"up-{index % 5}",
            prompt=f"Tag ticket {index}.",
            usage=usage,
        )
        for index, started in enumerate(spread(now - 2 * HOURS, now - 2 * MINUTES, calls))
    ]
    ctx.record_spans(spans)
    ctx.note(f"synthetic: {ingest(ctx.project['ingest_key'], spans)} spans")


def run_provider_incident(
    ctx: RunContext,
    *,
    provider: str,
    model: str,
    per_project: Sequence[tuple[int, int]],
    lab: bool = False,
) -> None:
    """Per project of the scenario, `(calls, errors)` spans in the last ten minutes on
    `provider`; `lab` marks the errors as Integration Lab faults."""
    rnd = random.Random(ctx.scenario_id)
    now = now_utc()
    for (name, entry), (calls, errors) in zip(ctx.projects.items(), per_project, strict=True):
        spans = []
        for index, started in enumerate(spread(now - 10 * MINUTES, now - 1 * MINUTES, calls)):
            failed = marked(index, errors, calls)
            spans.append(
                llm_span(
                    started=started,
                    duration_ms=rnd.randint(200, 700) if failed else rnd.randint(600, 1800),
                    model=ctx.model(model) if provider == PROVIDER else model,
                    provider=provider,
                    environment=ctx.environment,
                    input_tokens=rnd.randint(300, 900),
                    output_tokens=0 if failed else rnd.randint(40, 120),
                    status="error" if failed else "ok",
                    status_message="503 api_error: Service unavailable" if failed else None,
                    http_status=None if lab else (503 if failed else None),
                    finish_reason=None if failed else "end_turn",
                    session_id=f"pi-{index % 5}",
                    prompt=f"Summarise order {index}.",
                    attributes=LAB_5XX_ATTRIBUTES if failed and lab else None,
                )
            )
        ctx.record_spans(spans)
        ctx.note(f"{name}: {ingest(entry['ingest_key'], spans)} spans")
