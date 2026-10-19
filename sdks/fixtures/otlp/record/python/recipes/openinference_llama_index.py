"""Recipe `openinference-llama-index`: `openinference-instrumentation-llama-index` over LlamaIndex.

One scenario against the fake provider: a LlamaIndex `FunctionAgent` asks the model, runs the tool
the model called, and asks the model again for the answer. The model is
`llama-index-llms-openai`'s `OpenAI`. The fake provider serves `chat_tools` for the first model
call and `chat` for the call after the tool result (a scripted `x-fixture-scenario`).
"""

import asyncio

PACKAGE = "openinference-instrumentation-llama-index"
# Every other package is pinned (preflight ruling P14) at the version `uv` resolved at first
# record; a change of pin re-records. `llama-index-llms-openai` 0.8.3 caps `openai` below 3.
PACKAGES = {"llama-index-core": "0.14.25", "llama-index-llms-openai": "0.8.3", "openai": "2.54.0"}
SCENARIOS = ["agent_tool_llm"]
# Native fields this exporter does not send, per scenario (printed on the compatibility page).
LOSES: dict[str, list[str]] = {"agent_tool_llm": ["time_to_first_token_ms"]}
MODEL = "gpt-4.1-mini"


def run(scenario: str, provider_url: str) -> None:
    from openinference.instrumentation.llama_index import LlamaIndexInstrumentor
    from recipes import _otel

    provider = _otel.configure(scenario)
    LlamaIndexInstrumentor().instrument(tracer_provider=provider)
    try:
        asyncio.run(_agent_tool_llm(provider_url))
    finally:
        _otel.flush(provider)


async def _agent_tool_llm(provider_url: str) -> None:
    from llama_index.core.agent.workflow import FunctionAgent
    from llama_index.llms.openai import OpenAI

    def get_weather(city: str, unit: str = "celsius") -> str:
        """Get the current weather for a city."""
        return "18 degrees and sunny"

    llm = OpenAI(
        model=MODEL,
        api_base=f"{provider_url}/v1",
        api_key="sk-fixture",
        max_retries=0,
        default_headers={"x-fixture-scenario": "chat_tools,chat"},
    )
    agent = FunctionAgent(
        name="weather_agent",
        tools=[get_weather],
        llm=llm,
        system_prompt="Answer weather questions with the get_weather tool.",
        streaming=False,  # the fake provider has no streamed tool-call body
    )
    await agent.run("What is the weather in Paris and Tokyo? Use the tool.")
