"""Recipe `openinference-openai`: `openinference-instrumentation-openai` over the OpenAI client.

Four scenarios against the fake provider (spec §8.1): a chat completion, a streamed completion
with usage in the last chunk, a chat completion answered with a tool call, and a 429. OpenInference
records message content on the span by default (`llm.input_messages.*`, `llm.output_messages.*`),
so no opt-in variable is needed.
"""

from typing import Any

PACKAGE = "openinference-instrumentation-openai"
# Every other package is pinned (preflight ruling P14) at the version `uv` resolved at first
# record; a change of pin re-records. 0.1.65 requires `openai >= 2.8.0` and ships an `httpx2`
# shim, so the current `openai` 3 line works.
PACKAGES = {"openai": "3.33.0"}
SCENARIOS = ["chat", "streaming", "tool_call", "error"]
# Native fields this exporter does not send, per scenario (printed on the compatibility page).
# The stream does send TTFT, as a `First Token Stream Event` span event the mapper does not read
# (spec §4.7), so it is not listed there. On a failed call 0.1.65 sets no `llm.model_name`; the
# request model is only inside the `llm.invocation_parameters` JSON, which is not a model key.
LOSES = {
    "chat": ["time_to_first_token_ms"],
    "streaming": [],
    "tool_call": ["time_to_first_token_ms"],
    "error": ["model", "usage"],
}
# The fake-provider body each scenario is served (responses/openai/<body>.json or .sse).
_BODIES = {
    "chat": "chat",
    "streaming": "chat_stream",
    "tool_call": "chat_tools",
    "error": "error_429",
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
    from openai import APIStatusError, OpenAI
    from openinference.instrumentation.openai import OpenAIInstrumentor
    from recipes import _otel

    provider = _otel.configure(scenario)
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    client = OpenAI(
        base_url=f"{provider_url}/v1",
        api_key="sk-fixture",
        max_retries=0,
        default_headers={"x-fixture-scenario": _BODIES[scenario]},
    )
    try:
        _call(client, scenario)
    except APIStatusError:
        if scenario != "error":
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
    elif scenario == "tool_call":
        client.chat.completions.create(
            model=MODEL, messages=_WEATHER, tools=[_TOOL], tool_choice="required"
        )
    else:
        client.chat.completions.create(model=MODEL, messages=_HELLO)
