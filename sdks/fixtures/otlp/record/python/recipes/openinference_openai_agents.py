"""Recipe `openinference-openai-agents`: `openinference-instrumentation-openai-agents` over the
OpenAI Agents SDK.

One scenario against the fake provider: an agent that asks the model, runs the tool the model
called, and asks the model again for the answer (`AGENT` → `LLM`, `TOOL`, `LLM`). The model is the
SDK's Chat Completions model, because the fake provider has no tool-calling Responses body. The
instrumentation replaces the SDK's own trace processors by default, so nothing is sent to OpenAI's
trace endpoint. The fake provider serves `chat_tools` for the first model call and `chat` for the
call after the tool result (a scripted `x-fixture-scenario`).
"""

import asyncio

PACKAGE = "openinference-instrumentation-openai-agents"
# Every other package is pinned (preflight ruling P14) at the version `uv` resolved at first
# record; a change of pin re-records.
PACKAGES = {"openai-agents": "0.24.0", "openai": "3.35.1"}
SCENARIOS = ["agent_tool_llm"]
# Native fields this exporter does not send, per scenario (printed on the compatibility page).
# 2.5.3 sends only the prompt and completion counts and no finish reason on `generation` spans.
LOSES: dict[str, list[str]] = {
    "agent_tool_llm": ["cached_tokens", "finish_reason", "time_to_first_token_ms"]
}
MODEL = "gpt-4.1-mini"


def run(scenario: str, provider_url: str) -> None:
    from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
    from recipes import _otel

    provider = _otel.configure(scenario)
    OpenAIAgentsInstrumentor().instrument(tracer_provider=provider)
    try:
        asyncio.run(_agent_tool_llm(provider_url))
    finally:
        _otel.flush(provider)


async def _agent_tool_llm(provider_url: str) -> None:
    from agents import Agent, OpenAIChatCompletionsModel, Runner, function_tool
    from openai import AsyncOpenAI

    @function_tool
    def get_weather(city: str, unit: str = "celsius") -> str:
        """Get the current weather for a city."""
        return "18 degrees and sunny"

    client = AsyncOpenAI(
        base_url=f"{provider_url}/v1",
        api_key="sk-fixture",
        max_retries=0,
        default_headers={"x-fixture-scenario": "chat_tools,chat"},
    )
    agent = Agent(
        name="weather_agent",
        instructions="Answer weather questions with the get_weather tool.",
        model=OpenAIChatCompletionsModel(model=MODEL, openai_client=client),
        tools=[get_weather],
    )
    await Runner.run(agent, "What is the weather in Paris and Tokyo? Use the tool.")
