"""Recipe `pydantic-ai`: Pydantic AI's built-in OpenTelemetry instrumentation.

Two scenarios against the fake provider, both an agent that asks the model, runs the tool the
model called, and asks the model again for the answer (`invoke_agent` → `chat`, `execute_tool`,
`chat`). The instrumentation is Pydantic AI's own (`Agent.instrument_all(InstrumentationSettings(
...))`); Logfire is not used. `agent_tool_llm` keeps its defaults (message content on, GenAI
attribute version 5, the agent span's run total under `gen_ai.aggregated_usage.*`);
`agent_tool_llm_plain_usage` sets `use_aggregated_usage_attribute_names=False`, so the agent span
repeats its children's usage under `gen_ai.usage.*` and Spanlight's usage-rollup rule applies.
The fake provider serves `chat_tools` for the first model call and `chat` for the call after the
tool result (a scripted `x-fixture-scenario`).
"""

PACKAGE = "pydantic-ai-slim"
# Every other package is pinned (preflight ruling P14) at the version `uv` resolved at first
# record; a change of pin re-records. `pydantic-ai-slim[openai]` would pull `openai` unpinned.
PACKAGES = {"openai": "3.35.1"}
SCENARIOS = ["agent_tool_llm", "agent_tool_llm_plain_usage"]
# Native fields this exporter does not send, per scenario (printed on the compatibility page).
LOSES: dict[str, list[str]] = {
    "agent_tool_llm": ["time_to_first_token_ms"],
    "agent_tool_llm_plain_usage": ["time_to_first_token_ms"],
}
MODEL = "gpt-4.1-mini"


def run(scenario: str, provider_url: str) -> None:
    from recipes import _otel

    provider = _otel.configure(scenario)
    try:
        _agent_tool_llm(provider_url, provider, aggregated=scenario == "agent_tool_llm")
    finally:
        _otel.flush(provider)


def _agent_tool_llm(provider_url: str, tracer_provider: object, *, aggregated: bool) -> None:
    from openai import AsyncOpenAI
    from pydantic_ai import Agent, InstrumentationSettings
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    Agent.instrument_all(
        InstrumentationSettings(
            tracer_provider=tracer_provider, use_aggregated_usage_attribute_names=aggregated
        )
    )
    client = AsyncOpenAI(
        base_url=f"{provider_url}/v1",
        api_key="sk-fixture",
        max_retries=0,
        default_headers={"x-fixture-scenario": "chat_tools,chat"},
    )
    agent = Agent(
        OpenAIChatModel(MODEL, provider=OpenAIProvider(openai_client=client)),
        name="weather_agent",
        instructions="Answer weather questions with the get_weather tool.",
    )

    @agent.tool_plain
    def get_weather(city: str, unit: str = "celsius") -> str:
        """Get the current weather for a city."""
        return "18 degrees and sunny"

    agent.run_sync("What is the weather in Paris and Tokyo? Use the tool.")
