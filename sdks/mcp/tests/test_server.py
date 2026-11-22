"""Unit tests for the Spanlight MCP server."""

import io
import json

import httpx

from spanlight_mcp.server import McpSettings, SpanlightMcpClient, SpanlightMcpServer


def test_mcp_initialize_list_and_tool_calls() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if "/traces/tr_1" in str(request.url):
            return httpx.Response(200, json={"trace_id": "tr_1", "spans": []})
        if "/insights" in str(request.url):
            return httpx.Response(200, json={"items": [{"kind": "agent_ping_pong_loop"}]})
        return httpx.Response(200, json={"total_cost_usd": "1.23"})

    client = SpanlightMcpClient(
        McpSettings(host="https://spanlight.test", api_key="spl_live_k", project_id="proj_1"),
        transport=httpx.MockTransport(handler),
    )
    server = SpanlightMcpServer(client)

    init_reply = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_reply is not None and init_reply["result"]["serverInfo"]["name"] == "spanlight-mcp"

    listed = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed is not None and len(listed["result"]["tools"]) == 6

    res = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "spanlight_get_trace", "arguments": {"trace_id": "tr_1"}},
        }
    )
    assert res is not None and json.loads(res["result"]["content"][0]["text"])["trace_id"] == "tr_1"

    buf_in = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "spanlight_get_insights", "arguments": {}},
            }
        )
        + "\n"
    )
    buf_out = io.StringIO()
    server.serve_stdio(buf_in, buf_out)
    out = json.loads(buf_out.getvalue())
    assert json.loads(out["result"]["content"][0]["text"])["items"][0]["kind"] == "agent_ping_pong_loop"
    assert ("GET", "https://spanlight.test/api/v1/projects/proj_1/traces/tr_1?content=bounded") in seen
