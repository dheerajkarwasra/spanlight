"""Unit tests for MCP OTLP semantic convention detection and mapping."""

from app.db.models.telemetry import SpanKind
from app.ingest.conventions import Convention, detect, map_fields


def test_detect_mcp_by_tool_method_or_rpc_system() -> None:
    assert detect({"mcp.tool.name": "search_docs"}) is Convention.MCP
    assert detect({"mcp.method.name": "resources/read"}) is Convention.MCP
    assert detect({"rpc.system": "mcp", "mcp.method.name": "tools/call"}) is Convention.MCP


def test_map_mcp_tool_call_and_resource_read() -> None:
    conv, mapped = map_fields(
        {
            "rpc.system": "mcp",
            "mcp.method.name": "tools/call",
            "mcp.tool.name": "search_docs",
            "mcp.request.arguments": '{"query": "refund"}',
            "mcp.response.result": '{"hits": 3}',
            "mcp.session.id": "sess_1",
        },
        client_span=True,
    )
    assert conv is Convention.MCP
    assert mapped.kind is SpanKind.TOOL
    assert mapped.operation == "search_docs"
    assert mapped.input == {"query": "refund"}
    assert mapped.output == {"hits": 3}
    assert mapped.metadata["mcp.session_id"] == "sess_1"

    _, retrieval = map_fields(
        {"mcp.method.name": "resources/read", "mcp.resource.uri": "file:///README.md"},
        client_span=True,
    )
    assert retrieval.kind is SpanKind.RETRIEVAL
    assert retrieval.metadata["mcp.resource_uri"] == "file:///README.md"
