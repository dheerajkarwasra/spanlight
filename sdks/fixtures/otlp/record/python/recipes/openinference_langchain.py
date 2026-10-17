"""Recipe `openinference-langchain`: `openinference-instrumentation-langchain` over LangChain.

Three scenarios against the fake provider (spec §8.1), the model being `langchain-openai`'s
`ChatOpenAI`: a prompt → model → parser chain (`CHAIN` spans around one `LLM` span), an agent
that calls a tool and then the model again (`AGENT` → `LLM`, `TOOL`, `LLM`), and a retrieval
chain over an in-memory retriever (`RETRIEVER` → `LLM`). OpenInference records message content
on the span by default, so no opt-in variable is needed.

The agent is a runnable named `weather_agent` (the instrumentation labels any run whose name
contains "agent" as `AGENT`) that drives the tool loop itself: the fake provider serves one body
per client, so the first model call goes through a client served `chat_tools` and the second
through one served `chat`.
"""

from typing import Any

PACKAGE = "openinference-instrumentation-langchain"
# Every other package is pinned (preflight ruling P14) at the version `uv` resolved at first
# record; a change of pin re-records. `langchain` itself is not needed: chains, tools and
# retrievers all live in `langchain-core`.
PACKAGES = {"langchain-core": "1.6.12", "langchain-openai": "1.7.2", "openai": "3.33.0"}
SCENARIOS = ["chain_llm", "agent_tool_llm", "retriever"]
# Native fields this exporter does not send, per scenario (printed on the compatibility page).
# TTFT is exported as a metric only (spec §4.7).
LOSES: dict[str, list[str]] = {
    "chain_llm": ["time_to_first_token_ms"],
    "agent_tool_llm": ["time_to_first_token_ms"],
    "retriever": ["time_to_first_token_ms"],
}
MODEL = "gpt-4.1-mini"
_DOCS = [
    "Paris is the capital of France.",
    "The Eiffel Tower is 330 metres tall.",
]


def run(scenario: str, provider_url: str) -> None:
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from recipes import _otel

    provider = _otel.configure(scenario)
    LangChainInstrumentor().instrument(tracer_provider=provider)
    try:
        if scenario == "chain_llm":
            _chain_llm(provider_url)
        elif scenario == "agent_tool_llm":
            _agent_tool_llm(provider_url)
        else:
            _retriever(provider_url)
    finally:
        _otel.flush(provider)


def _model(provider_url: str, body: str) -> Any:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=MODEL,
        base_url=f"{provider_url}/v1",
        api_key="sk-fixture",
        max_retries=0,
        default_headers={"x-fixture-scenario": body},
    )


def _chain_llm(provider_url: str) -> None:
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages([("user", "Say hello in {count} words.")])
    chain = prompt | _model(provider_url, "chat") | StrOutputParser()
    chain.invoke({"count": "five"})


def _agent_tool_llm(provider_url: str) -> None:
    from langchain_core.messages import HumanMessage
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tools import tool

    @tool
    def get_weather(city: str, unit: str = "celsius") -> str:
        """Get the current weather for a city."""
        return "18 degrees and sunny"

    planner = _model(provider_url, "chat_tools").bind_tools([get_weather])
    answerer = _model(provider_url, "chat")

    def weather_agent(question: str) -> Any:
        messages: list[Any] = [HumanMessage(question)]
        reply = planner.invoke(messages)
        messages.append(reply)
        messages.extend(get_weather.invoke(call) for call in reply.tool_calls)
        return answerer.invoke(messages)

    agent = RunnableLambda(weather_agent, name="weather_agent")
    agent.invoke("What is the weather in Paris and Tokyo? Use the tool.")


def _retriever(provider_url: str) -> None:
    from langchain_core.callbacks import CallbackManagerForRetrieverRun
    from langchain_core.documents import Document
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.retrievers import BaseRetriever
    from langchain_core.runnables import RunnablePassthrough

    class FactsRetriever(BaseRetriever):
        """Returns the same in-memory documents for every query."""

        def _get_relevant_documents(
            self, query: str, *, run_manager: CallbackManagerForRetrieverRun
        ) -> list[Document]:
            return [Document(page_content=text) for text in _DOCS]

    def join(docs: list[Document]) -> str:
        return "\n".join(doc.page_content for doc in docs)

    prompt = ChatPromptTemplate.from_messages(
        [("system", "Answer from this context:\n{context}"), ("user", "{question}")]
    )
    chain = (
        {"context": FactsRetriever() | join, "question": RunnablePassthrough()}
        | prompt
        | _model(provider_url, "chat")
        | StrOutputParser()
    )
    chain.invoke("How tall is the Eiffel Tower?")
