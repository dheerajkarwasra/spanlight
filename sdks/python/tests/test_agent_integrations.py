"""Unit tests for one-line agent framework and MCP instrumentation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

import spanlight
from spanlight import (
    SpanlightCallbackHandler,
    SpanlightTracingProcessor,
    instrument_langgraph,
    instrument_mcp,
    instrument_openai_agents,
    instrument_pydantic_ai,
)


def _capturing_client(spans: list[dict[str, Any]]) -> None:
    import gzip
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        body = gzip.decompress(request.content)
        payload = json.loads(body)
        spans.extend(payload.get("spans", []))
        return httpx.Response(202, json={"accepted": len(payload.get("spans", [])), "rejected": []})

    spanlight.init(
        api_key="spl_live_test",
        host="https://collector.example.com",
        transport=httpx.MockTransport(handler),
    )


@dataclass
class _TraceObj:
    name: str
    trace_id: str
    group_id: str | None = None


@dataclass
class _SpanObj:
    span_id: str
    span_data: Any
    error: str | None = None


def test_openai_agents_processor_emits_nested_agent_llm_tool_and_handoff_spans() -> None:
    spans: list[dict[str, Any]] = []
    _capturing_client(spans)
    proc = instrument_openai_agents(register=False)
    assert isinstance(proc, SpanlightTracingProcessor)

    trace = _TraceObj(name="triage", trace_id="trace_ext_1", group_id="sess_9")
    proc.on_trace_start(trace)
    llm_span = _SpanObj(
        "s_llm",
        {
            "type": "generation",
            "model": "gpt-5",
            "input": "hi",
            "output": "ok",
            "usage": {"input_tokens": 12, "output_tokens": 5},
        },
    )
    proc.on_span_start(llm_span)
    proc.on_span_end(llm_span)
    tool_span = _SpanObj("s_tool", {"type": "function", "name": "lookup_order", "input": {"id": 1}})
    proc.on_span_start(tool_span)
    proc.on_span_end(tool_span)
    handoff_span = _SpanObj(
        "s_hf", {"type": "handoff", "from_agent": "triage", "to_agent": "billing"}
    )
    proc.on_span_start(handoff_span)
    proc.on_span_end(handoff_span)
    proc.on_trace_end(trace)
    spanlight.flush()

    by_name = {s["name"]: s for s in spans}
    root = by_name["triage"]
    assert root["kind"] == "agent"
    assert root["trace"]["session_id"] == "sess_9"
    assert by_name["chat gpt-5"]["kind"] == "llm"
    assert by_name["chat gpt-5"]["parent_span_id"] == root["span_id"]
    assert by_name["chat gpt-5"]["usage"]["input_tokens"] == 12
    assert by_name["lookup_order"]["kind"] == "tool"
    assert by_name["handoff -> billing"]["kind"] == "agent"


def test_langgraph_callback_handler_and_instrument_langgraph() -> None:
    spans: list[dict[str, Any]] = []
    _capturing_client(spans)

    class _FakeGraph:
        def with_config(self, config: dict[str, Any]) -> Any:
            return config

    cfg = instrument_langgraph(_FakeGraph())
    handler = cfg["callbacks"][0]
    assert isinstance(handler, SpanlightCallbackHandler)

    handler.on_chain_start(
        {"name": "planner"}, {"q": "help"}, run_id="r1", metadata={"langgraph_node": "planner"}
    )
    handler.on_llm_start(
        {"name": "ChatOpenAI"}, ["hi"], run_id="r2", invocation_params={"model": "gpt-5"}
    )
    handler.on_llm_end(
        {"llm_output": {"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}}}, run_id="r2"
    )
    handler.on_tool_start({"name": "search"}, "refunds", run_id="r3")
    handler.on_tool_end("docs", run_id="r3")
    handler.on_chain_end({"done": True}, run_id="r1")
    spanlight.flush()

    by_name = {s["name"]: s for s in spans}
    assert by_name["planner"]["kind"] == "agent"
    assert by_name["chat gpt-5"]["parent_span_id"] == by_name["planner"]["span_id"]
    assert by_name["search"]["kind"] == "tool"


def test_pydantic_ai_configures_otlp_env() -> None:
    spans: list[dict[str, Any]] = []
    _capturing_client(spans)

    class _FakeAgent:
        instrument = False

    agent = _FakeAgent()
    res = instrument_pydantic_ai(
        agent=agent, host="https://collector.example.com", api_key="spl_live_k"
    )
    assert agent.instrument is True
    assert (
        res["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == "https://collector.example.com/v1/otlp/traces"
    )


async def test_instrument_mcp_wraps_client_call_and_propagates_traceparent() -> None:
    spans: list[dict[str, Any]] = []
    _capturing_client(spans)

    class _FakeMcpClient:
        seen_meta: dict[str, Any] | None = None

        async def call_tool(
            self, name: str, params: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            self.seen_meta = (params or {}).get("_meta")
            return {"ok": True}

    client = instrument_mcp(_FakeMcpClient())
    with spanlight.span("orchestrator", kind="agent"):
        await client.call_tool("fetch_ticket", params={"id": 42})
    spanlight.flush()

    assert client.seen_meta is not None and "traceparent" in client.seen_meta
    tool_span = next(s for s in spans if s["name"] == "fetch_ticket")
    assert tool_span["kind"] == "tool"
    assert tool_span["attributes"]["mcp.tool.name"] == "fetch_ticket"
