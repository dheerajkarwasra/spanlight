"""Recipe `litellm`: LiteLLM's own OpenTelemetry callback over `litellm.completion`.

Four scenarios against the fake provider (OpenAI route): a chat completion in LiteLLM's legacy
attribute names (`litellm_request` span plus its `raw_gen_ai_request` child), the same call with
`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` (GenAI names, no raw child), a 429,
and a call made inside a caller's span, which LiteLLM joins: it writes its attributes onto the
caller's span instead of opening `litellm_request`. Content is captured on the span
(`capture_message_content="SPAN_ONLY"`). The callback reuses the global tracer provider that
`_otel.configure` installs.

Blocked on the owner (preflight ruling P18): `anthropic_cache_write` and `anthropic_cache_read`
need `responses/anthropic/messages_cache_{write,read}.json`, which only `capture_bodies.py` can
record; add them to `SCENARIOS` once those bodies exist.
"""

import os
from typing import Any

PACKAGE = "litellm"
# Every other package is pinned (preflight ruling P14); a change of pin re-records. `openai` is
# the client LiteLLM calls the OpenAI route with; 2.54.0 is what `uv` resolved at first record.
PACKAGES = {"openai": "2.54.0"}
SCENARIOS = ["chat_legacy", "chat_genai_latest", "error_429", "joined_parent_span"]
# Native fields this exporter does not send, per scenario (printed on the compatibility page).
# LiteLLM exports time to first token as a metric only, and the 429 carries no usage. On the
# OpenAI route the cached prompt tokens appear only inside the `metadata.usage_object` text:
# `gen_ai.usage.cache_read.input_tokens` is written from Anthropic-style usage keys alone.
_OK = ["cached_tokens", "time_to_first_token_ms"]
LOSES = {
    "chat_legacy": _OK,
    "chat_genai_latest": _OK,
    "error_429": ["usage", "time_to_first_token_ms"],
    "joined_parent_span": _OK,
}
# The fake-provider body each scenario is served (responses/openai/<body>.json).
_BODIES = {
    "chat_legacy": "chat",
    "chat_genai_latest": "chat",
    "error_429": "error_429",
    "joined_parent_span": "chat",
}
MODEL = "openai/gpt-4.1-mini"
_HELLO = [{"role": "user", "content": "Say hello in five words."}]


def run(scenario: str, provider_url: str) -> None:
    if scenario == "chat_genai_latest":
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "gen_ai_latest_experimental"
    # The bundled price map: no fetch from GitHub at import time.
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    import litellm
    from litellm.integrations.opentelemetry import OpenTelemetry, OpenTelemetryConfig
    from litellm.litellm_core_utils.thread_pool_executor import executor
    from opentelemetry import trace
    from recipes import _otel

    provider = _otel.configure(scenario)
    litellm.telemetry = False
    litellm.callbacks = [
        OpenTelemetry(config=OpenTelemetryConfig(capture_message_content="SPAN_ONLY"))
    ]
    try:
        if scenario == "joined_parent_span":
            with trace.get_tracer("fixture-app").start_as_current_span("handle_request"):
                _call(litellm, scenario, provider_url)
                # The success callback runs on LiteLLM's logging thread; it must write onto the
                # caller's span before that span ends.
                executor.shutdown(wait=True)
        else:
            _call(litellm, scenario, provider_url)
    finally:
        executor.shutdown(wait=True)
        _otel.flush(provider)


def _call(litellm: Any, scenario: str, provider_url: str) -> None:
    try:
        litellm.completion(
            model=MODEL,
            messages=_HELLO,
            api_base=f"{provider_url}/v1",
            api_key="sk-fixture",
            extra_headers={"x-fixture-scenario": _BODIES[scenario]},
            max_retries=0,
            num_retries=0,
        )
    except litellm.RateLimitError:
        if scenario != "error_429":
            raise
