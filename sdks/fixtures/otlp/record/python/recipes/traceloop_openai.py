"""Recipe `traceloop-openai`: the Traceloop SDK (OpenLLMetry) over the OpenAI client.

Two scenarios against the fake provider: a `@workflow` that runs a `@task` making a chat
completion (`workflow -> task -> openai.chat`), and an `@agent` whose chat completion answers
with two tool calls that a `@tool` serves with a constant (`agent -> openai.chat, tool, tool`).
Message content is on the provider span as `gen_ai.input.messages` / `gen_ai.output.messages`
(0.62.4 no longer writes the flattened `gen_ai.prompt.N.*` keys; its only other mode,
`use_attributes=False`, moves content to log events), and on the decorator spans as
`traceloop.entity.input` / `.output`.

The SDK never leaves the machine: spans go only to the recorder's tracer provider (seeded ids,
OTLP to the capture server), the SDK's own exporter, metrics, logs, prompt sync and telemetry are
off, and its API endpoint is the capture server.

Blocked on the owner (preflight ruling P18): `embeddings` needs `responses/openai/embeddings.json`,
which only `capture_bodies.py` records; add it to `SCENARIOS` once that body exists.
"""

import json
import os
from typing import Any

PACKAGE = "opentelemetry-instrumentation-openai"
# Every other package is pinned (preflight ruling P14); a change of pin re-records.
PACKAGES = {"traceloop-sdk": "0.62.4", "openai": "2.54.0"}
SCENARIOS = ["workflow_task_chat", "agent_tool"]
# Native fields this exporter does not send, per scenario (printed on the compatibility page).
LOSES: dict[str, list[str]] = {
    "workflow_task_chat": ["time_to_first_token_ms"],
    "agent_tool": ["time_to_first_token_ms"],
}
# The fake-provider body each scenario is served (responses/openai/<body>.json).
_BODIES = {"workflow_task_chat": "chat", "agent_tool": "chat_tools"}
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
    os.environ["TRACELOOP_TELEMETRY"] = "false"
    os.environ["TRACELOOP_METRICS_ENABLED"] = "false"
    os.environ["TRACELOOP_LOGGING_ENABLED"] = "false"
    os.environ["TRACELOOP_TRACE_CONTENT"] = "true"
    os.environ.pop("TRACELOOP_API_KEY", None)
    capture_url = os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
    os.environ["TRACELOOP_BASE_URL"] = capture_url
    from openai import OpenAI
    from opentelemetry.sdk.trace import SpanProcessor
    from recipes import _otel
    from traceloop.sdk import Traceloop
    from traceloop.sdk.instruments import Instruments

    provider = _otel.configure(scenario)
    # Traceloop reuses the global provider; a no-op processor (instead of its own exporter)
    # keeps one export per scenario while its on_start hook still stamps the entity attributes.
    Traceloop.init(
        app_name="spanlight-fixture",
        api_endpoint=capture_url,
        telemetry_enabled=False,
        processor=SpanProcessor(),
        instruments={Instruments.OPENAI},
        traceloop_sync_enabled=False,
    )
    client = OpenAI(
        base_url=f"{provider_url}/v1",
        api_key="sk-fixture",
        max_retries=0,
        default_headers={"x-fixture-scenario": _BODIES[scenario]},
    )
    try:
        _SCENARIO_RUNNERS[scenario](client)
    finally:
        _otel.flush(provider)


def _workflow_task_chat(client: Any) -> None:
    from traceloop.sdk.decorators import task, workflow

    @task(name="ask_model")
    def ask_model(messages: list[dict[str, str]]) -> str:
        response = client.chat.completions.create(model=MODEL, messages=messages)
        return str(response.choices[0].message.content)

    @workflow(name="greeting")
    def greeting() -> str:
        return ask_model(_HELLO)

    greeting()


def _agent_tool(client: Any) -> None:
    from traceloop.sdk.decorators import agent, tool

    @tool(name="get_weather")
    def get_weather(city: str, unit: str) -> str:
        return f"Sunny, 21 degrees {unit} in {city}"

    @agent(name="weather_agent")
    def weather_agent() -> str:
        response = client.chat.completions.create(
            model=MODEL, messages=_WEATHER, tools=[_TOOL], tool_choice="required"
        )
        calls = response.choices[0].message.tool_calls
        return "; ".join(get_weather(**json.loads(c.function.arguments)) for c in calls)

    weather_agent()


_SCENARIO_RUNNERS = {"workflow_task_chat": _workflow_task_chat, "agent_tool": _agent_tool}
