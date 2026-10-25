# ruff: noqa: RUF001
"""What each kind of insight says: its label, layer, certainty and the copy for a person.

`title` and `summary` are `str.format` templates over `Finding.params`; `render` fills them
and is what `insights` stores. A placeholder a detector did not supply renders as "—". A count
that can be 1 arrives with its noun (`1 burst`, `3 bursts`, see `format.count_noun`), so no
template has to pluralise.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter

from app.insights.format import UNKNOWN
from app.insights.schemas import Certainty, FailureLayer, Finding, Severity

L = FailureLayer
C = Certainty
S = Severity


@dataclass(frozen=True, slots=True)
class KindInfo:
    kind: str
    label: str
    failure_layer: FailureLayer
    certainty: Certainty
    default_severity: Severity
    title: str
    summary: str
    suggested_fix: str
    verification: str


@dataclass(frozen=True, slots=True)
class RenderedCopy:
    title: str
    summary: str
    suggested_fix: str
    verification: str
    label: str
    failure_layer: FailureLayer
    certainty: Certainty


class _Params(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return UNKNOWN


KIND_INFO: dict[str, KindInfo] = {
    info.kind: info
    for info in (
        KindInfo(
            "error_spike",
            "Error spike",
            L.TRAFFIC,
            C.MEASURED,
            S.WARNING,
            "Error rate jumped to {error_rate} on {model} in {environment}",
            "{errors} of {calls} LLM calls failed in the last 15 minutes, against "
            "{baseline_error_rate} over the 7 days before the last 24 hours.",
            "Open the example traces and filter by error class; one class usually names the "
            "cause: `rate_limit` → slow down or raise limits, `auth` → rotate the key, "
            "`provider_5xx` → add a fallback target to the gateway route, `context_length` → "
            "trim the input.",
            "The error rate on Overview stays under the threshold; the insight resolves after "
            "24 hours without a recurrence.",
        ),
        KindInfo(
            "latency_regression",
            "Latency regression",
            L.TRAFFIC,
            C.MEASURED,
            S.WARNING,
            "p95 latency on {model} rose to {p95}",
            "p95 over the last hour is {p95} across {calls} successful calls, against "
            "{baseline_p95} over the 7 days before the last 24 hours.",
            "Compare a slow trace with a fast one. Longer inputs or outputs, a new release, or "
            "a provider slowdown are the usual causes. Cap `max_tokens`, trim context, or route "
            "latency-sensitive calls to a faster model.",
            "p95 for the model returns under 1.5× its baseline on Overview's models table.",
        ),
        KindInfo(
            "cost_spike",
            "Cost spike",
            L.TRAFFIC,
            C.MEASURED,
            S.WARNING,
            "Spend in {environment} is {ratio}× the usual hourly rate",
            "{spend} was spent in the last hour over {priced_calls} priced calls, against a "
            "baseline of {baseline_hourly} per hour.",
            "Check the models table for the model driving the spend and the Users page for a "
            "single user behind it. Add a budget with action `block` on the gateway key to cap "
            "it.",
            "Hourly spend returns under 3× the baseline; a blocking budget on the key holds.",
        ),
        KindInfo(
            "retry_storm",
            "Retry storm",
            L.CLIENT,
            C.MEASURED,
            S.WARNING,
            "Identical failing requests repeated up to {largest_storm} times",
            "{storms} in the last 15 minutes sent the same request 5 or more times "
            "within 60 seconds after it failed ({hashes}).",
            "Retry with exponential backoff and jitter, at most 3 attempts, and only on "
            "retryable errors (429, 5xx, timeouts). Never retry 400, 401 or 403. The official "
            "SDKs do this with `max_retries=2` (three attempts in all).",
            "Run the `provider_5xx` lab scenario on a test key: one round shows at most 3 "
            "attempts with growing gaps.",
        ),
        KindInfo(
            "retry_after_ignored",
            "Retry-After ignored",
            L.CLIENT,
            C.MEASURED,
            S.WARNING,
            "Retries start before the provider's Retry-After delay",
            "{pairs} retries in the last hour were sent a median {median_early_by} before the "
            "delay the provider asked for had passed.",
            "On a 429, read `Retry-After` (or `retry-after-ms`) and wait at least that long "
            "before retrying.",
            "Run the `rate_limited` lab scenario: the retry starts no earlier than "
            "`retry_after_s` after the 429.",
        ),
        KindInfo(
            "rate_limit_pressure",
            "Rate-limit pressure",
            L.PROVIDER,
            C.MEASURED,
            S.WARNING,
            "{share} of calls to {provider} are rate limited",
            "{rate_limited} of {calls} calls in {environment} got a 429 in the last 15 minutes.",
            "Smooth bursts with a client-side queue or token bucket, spread load with a "
            "fallback target on the gateway route, or ask the provider for a higher limit.",
            "Filter Traces by error class `rate_limit`: the share stays under 10 %.",
        ),
        KindInfo(
            "truncated_outputs",
            "Truncated outputs",
            L.REQUEST,
            C.MEASURED,
            S.WARNING,
            "{share} of {model} responses stop at the token limit",
            "{truncated} of {calls} calls with a known finish reason ended with `length` in "
            "the last hour.",
            "Raise `max_tokens` for this call site, ask for shorter output in the prompt, or "
            'detect `finish_reason == "length"` and continue the generation.',
            "The share of `length` finish reasons for the model stays under 10 %.",
        ),
        KindInfo(
            "context_growth",
            "Context growth",
            L.REQUEST,
            C.INFERRED,
            S.WARNING,
            "Conversation history grows without bound",
            "{sessions} sent longer inputs call after call, reaching "
            "{max_last_input_tokens} input tokens.",
            "Keep a sliding window of recent turns, summarise older turns, or move reference "
            "material to retrieval instead of the message history.",
            "Open a long session in Sessions: input tokens per call level off instead of climbing.",
        ),
        KindInfo(
            "cache_opportunity",
            "Prompt-cache opportunity",
            L.REQUEST,
            C.INFERRED,
            S.INFO,
            "Repeated long prompts on {model} are not cached",
            "{repeated_hashes} of 2,000+ input tokens went out {repeated_calls} "
            "times in 24 hours with no cached tokens (about {estimated_input_tokens} input "
            "tokens).",
            "Put the stable prefix (system prompt, tools, documents) first. On Anthropic mark "
            'it with `cache_control: {"type": "ephemeral"}`; OpenAI caches identical '
            "prefixes of 1,024+ tokens automatically. For exact repeats, enable the gateway "
            "cache on the key.",
            "The next calls show cached tokens in the trace's usage card.",
        ),
        KindInfo(
            "tool_loop",
            "Tool loop",
            L.AGENT,
            C.MEASURED,
            S.WARNING,
            "The agent repeats `{name}` with the same arguments",
            "{traces} called `{name}` up to {max_repeats} times in a row with identical input.",
            "Pass each tool result back to the model, cap tool iterations per turn, and stop "
            "when a call repeats with the same arguments.",
            "No trace shows 4 or more identical consecutive calls to the tool.",
        ),
        KindInfo(
            "truncated_stream_accepted",
            "Truncated stream accepted",
            L.CLIENT,
            C.MEASURED,
            S.CRITICAL,
            "A cut-off stream was treated as a complete answer",
            "In {rounds} the stream ended without a finish reason and the client "
            "carried on instead of retrying or failing.",
            "Treat a stream as complete only when its terminal event arrives (a "
            "`finish_reason` on OpenAI, `message_stop` on Anthropic); otherwise retry the "
            "request or raise.",
            "Run the `truncated_stream` lab scenario: the client retries the same request or "
            "raises.",
        ),
        KindInfo(
            "unsupported_parameter_retried",
            "Rejected parameter dropped",
            L.CLIENT,
            C.MEASURED,
            S.WARNING,
            "The client drops a rejected parameter and resends",
            "In {rounds} in the last hour, a 400 for an unsupported parameter was followed "
            "by a different request within 60 seconds.",
            "Treat a 400 as a bug: fail loudly, log the parameter and fix the call for that "
            "model instead of changing the request at run time.",
            "Run the `unsupported_parameter` lab scenario: the client raises and sends "
            "nothing further.",
        ),
        KindInfo(
            "client_timeout_misconfigured",
            "Client timeout too short",
            L.CLIENT,
            C.INFERRED,
            S.WARNING,
            "Client timeout of about {clustered_at} cuts off {model} calls",
            "{aborts} calls were abandoned by the client at about {clustered_at}, while "
            "successful calls take {success_p95} at p95.",
            "Set the client timeout above the model's p99 (60 s is a safe start for chat) and "
            "stream long generations so the first token arrives early.",
            "Run the `slow_response` lab scenario (5 s delay): the call completes instead of "
            "aborting.",
        ),
        KindInfo(
            "unpriced_spend",
            "Unpriced spend",
            L.PLATFORM,
            C.MEASURED,
            S.INFO,
            "{model} has usage but no price",
            "{calls} calls to {provider}/{model} in 24 hours used {input_tokens} input and "
            "{output_tokens} output tokens with no price, so their cost shows as —.",
            "Add an organization price override for the model, or check that the model name "
            "matches the provider's (snapshot suffixes like `-20250101` match automatically).",
            "New calls to the model show a cost on the trace detail.",
        ),
        KindInfo(
            "provider_incident",
            "Provider incident",
            L.PROVIDER,
            C.MEASURED,
            S.WARNING,
            "{provider} is failing across the organization",
            "{org_errors} 5xx responses ({org_share} of calls) from {provider} in the last 30 "
            "minutes across {projects_affected}. {incident_note}",
            "Check the provider's status page, add a fallback target on another provider to "
            "the gateway route, and keep retries bounded.",
            "The 5xx share for the provider falls under 20 %; the insight resolves after 24 "
            "hours without a recurrence.",
        ),
        KindInfo(
            "model_retiring",
            "Model retiring",
            L.PROVIDER,
            C.MEASURED,
            S.WARNING,
            "{model} retires on {retires_on}",
            "{calls} in 24 hours went to {model}, which {provider} retires on {retires_on} "
            "({retires_in}); calls to it fail after that date. The provider recommends "
            "{replacement}. Routes: {routes}. Traces: {trace_names}.",
            "Move these calls to the recommended replacement before the date, and run an "
            "experiment on a dataset first to compare quality and cost.",
            "No calls to the model in the next 24 hours.",
        ),
    )
}


def _fill(template: str, params: Mapping[str, str]) -> str:
    return Formatter().vformat(template, (), _Params(params))


def render(finding: Finding) -> RenderedCopy:
    """The copy for a finding: templates filled from its params, the rest from the catalogue."""
    info = KIND_INFO[finding.kind]
    return RenderedCopy(
        title=_fill(info.title, finding.params),
        summary=_fill(info.summary, finding.params),
        suggested_fix=info.suggested_fix,
        verification=info.verification,
        label=info.label,
        failure_layer=info.failure_layer,
        certainty=info.certainty,
    )
