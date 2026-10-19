"""Recipe `openinference-langgraph`: `openinference-instrumentation-langchain` over LangGraph.

One scenario against the fake provider: LangGraph's prebuilt ReAct agent asks the model, runs the
tool the model called, and asks the model again for the answer. The model is `langchain-openai`'s
`ChatOpenAI`. LangGraph runs on LangChain's callbacks, so the LangChain instrumentation traces it;
the pinned package is `langgraph`, which is what this fixture tracks week to week. The fake
provider serves `chat_tools` for the first model call and `chat` for the call after the tool
result (a scripted `x-fixture-scenario`).
"""

PACKAGE = "langgraph"
# Every other package is pinned (preflight ruling P14) at the version `uv` resolved at first
# record; a change of pin re-records.
PACKAGES = {
    "openinference-instrumentation-langchain": "0.1.79",
    "langchain-core": "1.6.12",
    "langchain-openai": "1.7.2",
    "openai": "3.35.1",
}
SCENARIOS = ["agent_tool_llm"]
# Native fields this exporter does not send, per scenario (printed on the compatibility page).
# TTFT is exported as a metric only (spec §4.7).
LOSES: dict[str, list[str]] = {"agent_tool_llm": ["time_to_first_token_ms"]}
MODEL = "gpt-4.1-mini"


def run(scenario: str, provider_url: str) -> None:
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from recipes import _otel

    provider = _otel.configure(scenario)
    LangChainInstrumentor().instrument(tracer_provider=provider)
    try:
        _agent_tool_llm(provider_url)
    finally:
        _otel.flush(provider)


def _agent_tool_llm(provider_url: str) -> None:
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent

    @tool
    def get_weather(city: str, unit: str = "celsius") -> str:
        """Get the current weather for a city."""
        return "18 degrees and sunny"

    model = ChatOpenAI(
        model=MODEL,
        base_url=f"{provider_url}/v1",
        api_key="sk-fixture",
        max_retries=0,
        default_headers={"x-fixture-scenario": "chat_tools,chat"},
    )
    agent = create_react_agent(model, [get_weather], name="weather_agent")
    agent.invoke({"messages": [("user", "What is the weather in Paris and Tokyo? Use the tool.")]})
