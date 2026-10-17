"""The labelled scenarios of the Doctor accuracy harness.

One project per (detector, case), so fingerprints never collide across cases. Each scenario
is a positive (the detector should fire) or a negative (it should not): a near miss just under
a threshold, or a legitimate pattern the detector must leave alone. Three control projects run
ordinary application traffic and should produce no finding at all. Every scenario names its
mode (`scenario_types.MODES`), so the table is honest about where its spans come from.

The traffic each scenario sends lives in the `traffic*` modules; this module is the list. The
list is laid out as a table (one scenario per block, `fmt: off`) so it reads row by row.
"""

import functools
import os
from collections.abc import Sequence
from decimal import Decimal

from accuracy.doctor.common import HAIKU, HAIKU45, PROVIDER
from accuracy.doctor.scenario_types import Expectation, Fault, Scenario, scenario
from accuracy.doctor.traffic import HOURS, MINUTES, RATE_LIMIT_RETRY_AFTER_S, UNSUPPORTED_PARAM
from accuracy.doctor.traffic_controls import (
    run_control_agent,
    run_control_chat,
    run_control_gateway,
)
from accuracy.doctor.traffic_errors import (
    run_error_spike,
    run_error_spike_lab,
    run_rate_limit_lab,
    run_retry_after,
    run_retry_fanout,
    run_retry_storm,
    run_truncated_stream,
    run_unsupported_parameter,
)
from accuracy.doctor.traffic_provider_path import (
    run_gateway_limiter_burst,
    run_provider_incident_with_feed,
)
from accuracy.doctor.traffic_rate_limit import run_rate_limit_spend_cap, run_rate_limit_synthetic
from accuracy.doctor.traffic_synthetic import baseline_spans, run_provider_incident, run_unpriced
from accuracy.doctor.traffic_volume import (
    GROWTH_SHAPE,
    TRIMMED_SHAPE,
    run_cache_opportunity,
    run_client_timeout,
    run_context_growth,
    run_cost_normal,
    run_cost_spike,
    run_latency,
    run_tool_loop,
    run_truncated_outputs,
)

RUN_SUFFIX = os.environ.get("DR_SUFFIX", "")
"""Appended to every project name, so a second run can use fresh projects (`DR_SUFFIX=-2`)."""

P = functools.partial
_s = functools.partial(scenario, suffix=RUN_SUFFIX)
M15, M30, H1, H24 = 15 * MINUTES, 30 * MINUTES, 1 * HOURS, 24 * HOURS
BASE = P(baseline_spans)
LAB_5XX = Fault("provider_5xx", {"status": 503})
LAB_429 = Fault("rate_limited", {"retry_after_s": RATE_LIMIT_RETRY_AFTER_S})
LAB_CUT = Fault("truncated_stream", {"after_chunks": 3})
LAB_PARAM = Fault("unsupported_parameter", {"param": UNSUPPORTED_PARAM})


def _second(detector: str, case: str) -> tuple[str, ...]:
    """The second project of an organization-wide scenario."""
    return (f"dr-{detector}-{case}-b{RUN_SUFFIX}",)


# fmt: off
SCENARIOS: tuple[Scenario, ...] = (
    # error_spike: 15 min, >= 50 non-fault calls per (env, model), rate >= max(3b, b + 0.05)
    _s("error_spike", "pos", "pos", "real", "0.002", M15,
       "60 calls, 12 real 400s (20 %) on a ~1 % baseline",
       P(run_error_spike, calls=60, errors=12), baseline=BASE),
    _s("error_spike", "neg-nearmiss", "neg", "real", "0.002", M15,
       "60 calls, 2 real 400s (3.3 % < 5 % floor)",
       P(run_error_spike, calls=60, errors=2), baseline=BASE),
    _s("error_spike", "neg-lab5xx", "neg", "lab", "0.002", M15,
       "100 gateway calls, 40 % Lab 5xx (excluded), real calls fine",
       P(run_error_spike_lab, calls=100), baseline=BASE, gateway=True,
       fault=Fault("provider_5xx", {"status": 503}, probability=0.4)),
    # latency_regression: 1 h, >= 100 ok calls per model, p95 >= max(1.5b, b + 500 ms)
    _s("latency_regression", "pos", "pos", "real", "0.040", H1,
       "110 slow real calls (~450-word answers) on a 200-500 ms baseline",
       P(run_latency, calls=110), baseline=P(baseline_spans, duration_ms=(200, 500))),
    _s("latency_regression", "neg-nearmiss", "neg", "real", "0.036", H1,
       "99 slow real calls (one under the 100 minimum)",
       P(run_latency, calls=99), baseline=P(baseline_spans, duration_ms=(200, 500))),
    _s("latency_regression", "neg-baseline-high", "neg", "real", "0.040", H1,
       "110 slow real calls on a 6-10 s baseline (as slow as usual)",
       P(run_latency, calls=110), baseline=P(baseline_spans, duration_ms=(6_000, 10_000))),
    # cost_spike: 1 h, >= 20 priced calls and >= $1 per env, spend >= max(3b, b + $1)
    _s("cost_spike", "pos", "pos", "real", "1.120", H1,
       "20 real claude-haiku-4-5 calls of ~55k tokens (~$1.10) on a ~$0.002/h baseline",
       P(run_cost_spike, calls=20, prompt_tokens=55_000, model_name=HAIKU45), baseline=BASE),
    _s("cost_spike", "neg-nearmiss", "neg", "real", "0.960", H1,
       "20 real claude-haiku-4-5 calls of ~47k tokens (~$0.94 < $1 floor)",
       P(run_cost_spike, calls=20, prompt_tokens=47_000, model_name=HAIKU45), baseline=BASE),
    _s("cost_spike", "neg-normal", "neg", "real", "0.002", H1,
       "25 ordinary real calls (~$0.002, normal traffic)",
       P(run_cost_normal, calls=25), baseline=BASE),
    # retry_storm: 15 min, >= 20 hashed spans per (env, model), same hash >= 5 in 60 s in a flow
    _s("retry_storm", "pos", "pos", "lab", "0", M15,
       "naive client: 5 rounds x 5 immediate resends on Lab 5xx",
       P(run_retry_storm, rounds=5, attempts=5), gateway=True, fault=LAB_5XX),
    _s("retry_storm", "neg-nearmiss", "neg", "lab", "0", M15,
       "6 rounds x 4 resends (runs of 4 < 5)",
       P(run_retry_storm, rounds=6, attempts=4), gateway=True, fault=LAB_5XX),
    _s("retry_storm", "neg-fanout", "neg", "lab", "0", M15,
       "24 unrelated traces send the same failing request once each",
       P(run_retry_fanout, traces=24), gateway=True, fault=LAB_5XX),
    # retry_after_ignored: 1 h, >= 3 pairs (429 with Retry-After, same hash resent sooner)
    _s("retry_after_ignored", "pos", "pos", "lab", "0", H1,
       "naive client: 3 rounds x 4 immediate resends on Lab 429 (9 pairs)",
       P(run_retry_after, rounds=3, attempts=4, honour=False), gateway=True, fault=LAB_429),
    _s("retry_after_ignored", "neg-nearmiss", "neg", "lab", "0", H1,
       "1 round x 3 immediate resends (2 pairs < 3)",
       P(run_retry_after, rounds=1, attempts=3, honour=False), gateway=True, fault=LAB_429),
    _s("retry_after_ignored", "neg-corrected", "neg", "lab", "0", H1,
       "corrected client: waits Retry-After + 0.1 s, 3 rounds x 3",
       P(run_retry_after, rounds=3, attempts=3, honour=True), gateway=True, fault=LAB_429),
    # rate_limit_pressure: 15 min, >= 50 non-fault calls per (env, provider), 429 share >= 10 %
    _s("rate_limit_pressure", "pos", "pos", "synthetic", "0", M15,
       "60 synthetic calls, 12 real-looking 429s (20 %)",
       P(run_rate_limit_synthetic, calls=60, limited=12)),
    _s("rate_limit_pressure", "neg-nearmiss", "neg", "synthetic", "0", M15,
       "60 synthetic calls, 5 429s (8.3 % < 10 %)",
       P(run_rate_limit_synthetic, calls=60, limited=5)),
    _s("rate_limit_pressure", "neg-lab429", "neg", "lab", "0", M15,
       "60 gateway calls all Lab 429 (faults excluded)",
       P(run_rate_limit_lab, calls=60), gateway=True, fault=LAB_429),
    _s("rate_limit_pressure", "spend_cap", "pos", "synthetic", "0", M15,
       "60 synthetic calls, 12 Anthropic spend-cap 429s (20 %): evidence reads kind spend_cap",
       P(run_rate_limit_spend_cap, calls=60, limited=12),
       expect=Expectation(rate_limit_kind="spend_cap")),
    _s("rate_limit_pressure", "gateway_limiter_negative", "neg", "real", "0.005", M15,
       "80-call gateway burst on a key limited to 50/min: the key limiter's 429s are not "
       "provider pressure",
       P(run_gateway_limiter_burst, calls=80, workers=16), gateway=True, key_rpm_limit=50),
    # truncated_outputs: 1 h, >= 50 calls with a finish reason per model, length share >= 10 %
    _s("truncated_outputs", "pos", "pos", "real", "0.004", H1,
       "50 real calls, 8 cut by max_tokens=5 (16 %)",
       P(run_truncated_outputs, calls=50, truncated=8)),
    _s("truncated_outputs", "neg-nearmiss", "neg", "real", "0.004", H1,
       "50 real calls, 4 cut (8 % < 10 %)",
       P(run_truncated_outputs, calls=50, truncated=4)),
    _s("truncated_outputs", "neg-undermin", "neg", "real", "0.004", H1,
       "49 real calls, 10 cut (one under the 50 minimum)",
       P(run_truncated_outputs, calls=49, truncated=10)),
    # context_growth: 24 h, session >= 8 calls rising on >= 80 % of pairs, last >= 3x first, >= 20k
    _s("context_growth", "pos", "pos", "real", "0.030", H24,
       "3 real sessions growing 2.5k -> 21k input tokens over 8 turns",
       P(run_context_growth, sessions=3, shape=GROWTH_SHAPE)),
    _s("context_growth", "neg-nearmiss", "neg", "real", "0.020", H24,
       "2 such sessions (needs 3, none reaches 100k)",
       P(run_context_growth, sessions=2, shape=GROWTH_SHAPE)),
    _s("context_growth", "neg-trimmed", "neg", "real", "0.020", H24,
       "3 sessions whose context is trimmed every few turns (rises on 5/7 pairs)",
       P(run_context_growth, sessions=3, shape=TRIMMED_SHAPE)),
    # cache_opportunity: 24 h, claude-*, same hash >= 10 calls over >= 2 traces, input >= 2000
    _s("cache_opportunity", "pos", "pos", "real", "0.004", H24,
       "12 real traces repeating a 3k-token prompt without caching",
       P(run_cache_opportunity, calls=12, cached=False)),
    _s("cache_opportunity", "neg-nearmiss", "neg", "real", "0.003", H24,
       "9 such traces (needs 10)",
       P(run_cache_opportunity, calls=9, cached=False)),
    _s("cache_opportunity", "neg-cached", "neg", "real", "0.002", H24,
       "12 traces with a cache breakpoint (cache reads after the first)",
       P(run_cache_opportunity, calls=12, cached=True)),
    # tool_loop: 1 h, >= 4 consecutive tool spans with the same name and input in a trace
    _s("tool_loop", "pos", "pos", "real", "0.001", H1,
       "3 agent traces each calling search_docs 5 times with the same input",
       P(run_tool_loop, traces=3, repeats=5, changing=False)),
    _s("tool_loop", "neg-nearmiss", "neg", "real", "0.001", H1,
       "3 traces with 3 identical consecutive calls (needs 4)",
       P(run_tool_loop, traces=3, repeats=3, changing=False)),
    _s("tool_loop", "neg-polling", "neg", "real", "0.001", H1,
       "3 traces polling poll_job 6 times with a changing attempt number",
       P(run_tool_loop, traces=3, repeats=6, changing=True)),
    # truncated_stream_accepted: 1 h, Lab cut stream, then another request in the trace in 5 min
    _s("truncated_stream_accepted", "pos", "pos", "lab", "0.002", H1,
       "naive client: accepts the cut stream, asks the follow-up in the same trace",
       P(run_truncated_stream, rounds=3, policy="naive"), gateway=True, fault=LAB_CUT),
    _s("truncated_stream_accepted", "neg-newtrace", "neg", "lab", "0.002", H1,
       "follow-up under a new trace id (a new request, not a continuation)",
       P(run_truncated_stream, rounds=3, policy="newtrace"), gateway=True, fault=LAB_CUT),
    _s("truncated_stream_accepted", "neg-corrected", "neg", "lab", "0.002", H1,
       "corrected client: resends the same request, never continues",
       P(run_truncated_stream, rounds=3, policy="corrected"), gateway=True, fault=LAB_CUT),
    # unsupported_parameter_retried: 1 h, Lab 400, then another request in the trace in 60 s
    _s("unsupported_parameter_retried", "pos", "pos", "lab", "0", H1,
       "naive client: drops top_k and resends at once, 3 rounds",
       P(run_unsupported_parameter, rounds=3, policy="naive"), gateway=True, fault=LAB_PARAM),
    _s("unsupported_parameter_retried", "neg-late", "neg", "lab", "0", H1,
       "drops top_k but resends after 61 s (outside the 60 s horizon)",
       P(run_unsupported_parameter, rounds=1, policy="naive", delay_s=61.0),
       gateway=True, fault=LAB_PARAM),
    _s("unsupported_parameter_retried", "neg-corrected", "neg", "lab", "0", H1,
       "corrected client: a 400 ends the round, 3 rounds",
       P(run_unsupported_parameter, rounds=3, policy="corrected"), gateway=True, fault=LAB_PARAM),
    # client_timeout_misconfigured: 24 h, >= 10 SDK timeouts per model within 10 %, slower oks
    _s("client_timeout_misconfigured", "pos", "pos", "real", "0.012", H24,
       "12 real SDK timeouts at 1.5 s on ~700-word answers, 4 successes at 120 s",
       P(run_client_timeout, timeouts=(1.5,) * 12, successes=4)),
    _s("client_timeout_misconfigured", "neg-nearmiss", "neg", "real", "0.010", H24,
       "9 such timeouts (needs 10), 4 successes",
       P(run_client_timeout, timeouts=(1.5,) * 9, successes=4)),
    _s("client_timeout_misconfigured", "neg-scattered", "neg", "real", "0.012", H24,
       "12 timeouts spread 1.0-3.2 s (no fixed deadline), 4 successes",
       P(run_client_timeout, timeouts=tuple(round(1.0 + 0.2 * i, 1) for i in range(12)),
         successes=4)),
    # unpriced_spend: 24 h, >= 20 spans of an unknown model with usage and no cost
    _s("unpriced_spend", "pos", "pos", "synthetic", "0", H24,
       "25 synthetic spans of acme-llm-7b with usage",
       P(run_unpriced, calls=25, usage=True)),
    _s("unpriced_spend", "neg-nearmiss", "neg", "synthetic", "0", H24,
       "19 such spans (needs 20)",
       P(run_unpriced, calls=19, usage=True)),
    _s("unpriced_spend", "neg-nousage", "neg", "synthetic", "0", H24,
       "25 spans of an unknown model that reports no usage (nothing to price)",
       P(run_unpriced, calls=25, usage=False)),
    # provider_incident: org, 30 min, >= 20 5xx and >= 20 % of the org's calls, >= 2 projects
    _s("provider_incident", "pos", "pos", "synthetic", "0", M30,
       "2 projects, 24 anthropic 5xx of 30 calls (80 %)",
       P(run_provider_incident, provider=PROVIDER, model=HAIKU,
         per_project=((15, 12), (15, 12))),
       org="incident-pos", extra_projects=_second("provider_incident", "pos")),
    _s("provider_incident", "neg-nearmiss", "neg", "synthetic", "0", M30,
       "2 projects, 19 openai 5xx of 25 calls (needs 20)",
       P(run_provider_incident, provider="openai", model="gpt-4.1-mini",
         per_project=((13, 10), (12, 9))),
       org="incident-neg-nearmiss", extra_projects=_second("provider_incident", "neg-nearmiss")),
    _s("provider_incident", "neg-lowshare", "neg", "synthetic", "0", M30,
       "2 projects, 24 google 5xx of 200 calls (12 % < 20 %: background errors at volume)",
       P(run_provider_incident, provider="google", model="gemini-2.5-flash",
         per_project=((100, 12), (100, 12))),
       org="incident-neg-lowshare", extra_projects=_second("provider_incident", "neg-lowshare")),
    _s("provider_incident", "neg-lab", "neg", "synthetic", "0", M30,
       "2 projects, 24 mistral 5xx of 30 calls tagged as Lab faults (excluded)",
       P(run_provider_incident, provider="mistral", model="mistral-large-latest",
         per_project=((15, 12), (15, 12)), lab=True),
       org="incident-neg-lab", extra_projects=_second("provider_incident", "neg-lab")),
    _s("provider_incident", "with_incident", "pos", "synthetic", "0", M30,
       "as pos, with an overlapping incident polled from a local fixture feed: finding cites it",
       P(run_provider_incident_with_feed, per_project=((15, 12), (15, 12))),
       org="incident-with_incident", extra_projects=_second("provider_incident", "with_incident"),
       expect=Expectation(incident_cited=True)),
    # controls: clean real applications, no finding expected
    _s("control", "chat", "neg", "real", "0.003", H24,
       "5 chat sessions x 3 turns on claude-haiku-5-5",
       P(run_control_chat, sessions=5, turns=3)),
    _s("control", "agent", "neg", "real", "0.002", H24,
       "4 agent traces: plan, two different tools, answer",
       P(run_control_agent, traces=4)),
    _s("control", "gateway", "neg", "real", "0.003", H24,
       "12 gateway calls without a fault profile, 4 streamed, with sessions",
       P(run_control_gateway, calls=12), gateway=True),
)
# fmt: on

DETECTORS: tuple[str, ...] = (
    "error_spike",
    "latency_regression",
    "cost_spike",
    "retry_storm",
    "retry_after_ignored",
    "rate_limit_pressure",
    "truncated_outputs",
    "context_growth",
    "cache_opportunity",
    "tool_loop",
    "truncated_stream_accepted",
    "unsupported_parameter_retried",
    "client_timeout_misconfigured",
    "unpriced_spend",
    "provider_incident",
)
"""Every Doctor detector, in catalogue order (`app/insights/engine.py`)."""

CONTROL = "control"


def by_id(scenario_id: str) -> Scenario:
    for candidate in SCENARIOS:
        if candidate.id == scenario_id:
            return candidate
    raise KeyError(scenario_id)


def total_est_cost(scenarios: Sequence[Scenario] = SCENARIOS) -> Decimal:
    return sum((s.est_cost_usd for s in scenarios), Decimal(0))
