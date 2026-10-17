"""Scenario traffic for the provider-path behaviour: the gateway's own key limiter
(`rate_limit_pressure:gateway_limiter_negative`) and a provider incident with a matching
status-page incident (`provider_incident:with_incident`). The spend-cap scenario's traffic is
in `traffic_rate_limit`."""

import threading
from datetime import timedelta

import anthropic

from accuracy.doctor.common import HAIKU, PROVIDER, new_trace_id, now_utc
from accuracy.doctor.context import SEEDED_INCIDENT_TITLE, RunContext, parallel
from accuracy.doctor.incident_feed import fixture_feed, fixture_incident_id, seed_incident
from accuracy.doctor.traffic import SHORT_PROMPT, gw_chat
from accuracy.doctor.traffic_synthetic import run_provider_incident

INCIDENT_LEAD = timedelta(minutes=12)
"""The fixture incident starts this long before the run, just before its first 5xx span."""


def run_gateway_limiter_burst(ctx: RunContext, *, calls: int, workers: int) -> None:
    """`calls` real gateway calls at once on a key with an `rpm_limit` (set up by `--setup`).

    The key's bucket admits its limit and the gateway answers the rest itself with
    `429 RATE_LIMITED`; no provider is involved in those. Admitted calls are ordinary short
    answers, enough to pass the detector's 50-call minimum, so a detector that counted the
    gateway's refusals as provider 429s would see a share well over 10 % and fire. A burst that
    never reached the limit proves nothing, so the run fails loudly instead.
    """
    model = ctx.model(HAIKU)
    refused: list[int] = []
    lock = threading.Lock()

    def one(index: int) -> None:
        client = ctx.gateway(session_id=f"gl-{index % 4}")
        messages = [{"role": "user", "content": SHORT_PROMPT.format(ref=f"gl{index}")}]
        trace_id = new_trace_id()
        _, error = gw_chat(ctx, client, trace_id=trace_id, messages=messages, model=model)
        if isinstance(error, anthropic.RateLimitError):
            with lock:
                refused.append(index)

    parallel(one, range(calls), workers=workers)
    ctx.fact("refused_by_key_limit", str(len(refused)))
    ctx.note(f"{len(refused)} of {calls} calls refused by the key's own limit")
    if not refused:
        raise RuntimeError("the burst never reached the key's rpm_limit; rerun it")


def run_provider_incident_with_feed(
    ctx: RunContext, *, per_project: tuple[tuple[int, int], ...]
) -> None:
    """The `pos` incident traffic (anthropic 5xx in two projects), after seeding a resolved
    fixture incident that overlaps it through the product's status poller."""
    now = now_utc()
    title = f"Spanlight Doctor harness fixture: elevated errors on the Claude API ({ctx.nonce})"
    feed = fixture_feed(
        external_id=fixture_incident_id(ctx.nonce),
        title=title,
        started_at=now - INCIDENT_LEAD,
        resolved_at=now,
    )
    outcome, upserted = seed_incident(feed, now=now)
    if outcome != "fetched" or upserted < 1:
        raise RuntimeError(f"the fixture feed poll ended {outcome} with {upserted} incidents")
    ctx.fact(SEEDED_INCIDENT_TITLE, title)
    ctx.note(f"fixture incident seeded through the poller ({outcome})")
    run_provider_incident(ctx, provider=PROVIDER, model=HAIKU, per_project=per_project)
