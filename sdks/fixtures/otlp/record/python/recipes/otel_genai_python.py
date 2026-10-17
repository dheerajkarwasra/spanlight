"""Recipe `otel-genai-python`: `opentelemetry-instrumentation-openai-v2` over the OpenAI client.

Four scenarios against the fake provider: a chat completion, a chat completion answered with a
tool call, a streamed completion with usage in the last chunk, and a 429. The instrumentation
runs in its experimental GenAI mode with content on the span (`gen_ai.input.messages`,
`gen_ai.output.messages`); the legacy `true` mode writes content as log events only.
"""

import os
from typing import Any

PACKAGE = "opentelemetry-instrumentation-openai-v2"
# Every other package is pinned (preflight ruling P14); a change of pin re-records. The
# instrumentation at 2.4b0 imports `httpx`, which `openai` 3 dropped for `httpx2`, and
# `opentelemetry.util.genai.instruments`, which `opentelemetry-util-genai` 1.2b0 made private.
PACKAGES = {"openai": "2.54.0", "opentelemetry-util-genai": "1.1b0"}
SCENARIOS = ["chat", "chat_tool_call", "streaming", "error_429"]
# Native fields this exporter does not send, per scenario (printed on the compatibility page).
LOSES = {
    "chat": ["cached_tokens", "time_to_first_token_ms"],
    "chat_tool_call": ["cached_tokens", "time_to_first_token_ms"],
    # 2.4b0 drops the stream's `finish_reason` chunk (the usage chunk has no choices).
    "streaming": ["cached_tokens", "time_to_first_token_ms", "finish_reason"],
    "error_429": ["output", "usage"],
}
# The fake-provider body each scenario is served (responses/openai/<body>.json or .sse).
_BODIES = {
    "chat": "chat",
    "chat_tool_call": "chat_tools",
    "streaming": "chat_stream",
    "error_429": "error_429",
}
MODEL = "gpt-4.1-mini"
_HELLO = [{"role": "user", "content": "Say hello in five words."}]
_WEATHER = [{"role": "user", "content": "What is the weather in Paris? Use the tool."}]
_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def run(scenario: str, provider_url: str) -> None:
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "gen_ai_latest_experimental"
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "SPAN_ONLY"
    from openai import APIStatusError, OpenAI
    from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
    from recipes import _otel

    provider = _otel.configure(scenario)
    OpenAIInstrumentor().instrument()
    client = OpenAI(
        base_url=f"{provider_url}/v1",
        api_key="sk-fixture",
        max_retries=0,
        default_headers={"x-fixture-scenario": _BODIES[scenario]},
    )
    try:
        _call(client, scenario)
    except APIStatusError:
        if not scenario.startswith("error_"):
            raise
    finally:
        _otel.flush(provider)


def _call(client: Any, scenario: str) -> None:
    if scenario == "streaming":
        stream = client.chat.completions.create(
            model=MODEL, messages=_HELLO, stream=True, stream_options={"include_usage": True}
        )
        for _chunk in stream:
            pass
    elif scenario == "chat_tool_call":
        client.chat.completions.create(
            model=MODEL, messages=_WEATHER, tools=[_TOOL], tool_choice="required"
        )
    else:
        client.chat.completions.create(model=MODEL, messages=_HELLO)
