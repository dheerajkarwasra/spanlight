"""Zero-dependency-bloat stdio MCP server for Spanlight (JSON-RPC 2.0 / MCP 2024-11-05+).

Gives coding agents (Claude Code, Cursor, Codex, Antigravity) direct read tools over your
Spanlight traces, Doctor findings, cost overviews, and prompt versions over `httpx`.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TextIO
from urllib.parse import quote

import httpx

PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "spanlight_list_traces",
        "description": "List recent LLM/agent traces in a Spanlight project with optional filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Spanlight project UUID"},
                "status": {"type": "string", "enum": ["ok", "error", "unset"]},
                "model": {"type": "string"},
                "search": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "spanlight_get_trace",
        "description": "Fetch full trace metadata and span waterfall for a trace ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "trace_id": {"type": "string", "description": "32-hex W3C trace id or UUID"},
            },
            "required": ["project_id", "trace_id"],
        },
    },
    {
        "name": "spanlight_get_insights",
        "description": "List active Doctor findings (loops, cost spikes, cache misses, failures).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "kind": {"type": "string"},
                "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "spanlight_get_health",
        "description": "Fetch the project's Doctor health score and severity breakdown.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
    {
        "name": "spanlight_get_spend",
        "description": "Fetch overview call counts, cost USD, latency p95, and unpriced call count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "environment": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "spanlight_get_prompt",
        "description": "Fetch a prompt version (text or chat template) by name and label.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "label": {"type": "string", "default": "production"},
            },
            "required": ["name"],
        },
    },
]


@dataclass(frozen=True)
class McpSettings:
    host: str
    api_key: str
    project_id: str | None = None
    timeout: float = 15.0

    @classmethod
    def from_env(cls) -> McpSettings:
        host = os.environ.get("SPANLIGHT_HOST", "http://localhost:8080").rstrip("/")
        api_key = os.environ.get("SPANLIGHT_API_KEY", "")
        project_id = os.environ.get("SPANLIGHT_PROJECT_ID") or None
        return cls(host=host, api_key=api_key, project_id=project_id)


class SpanlightMcpClient:
    """HTTP client executing Spanlight MCP tool calls against the REST API."""

    def __init__(
        self,
        settings: McpSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        headers: dict[str, str] = {"Accept": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"
        self._http = httpx.Client(
            base_url=settings.host,
            headers=headers,
            timeout=settings.timeout,
            transport=transport,
        )

    def _project_id(self, args: Mapping[str, Any]) -> str:
        pid = args.get("project_id") or self._settings.project_id
        if not isinstance(pid, str) or not pid.strip():
            raise ValueError("project_id is required (pass argument or set SPANLIGHT_PROJECT_ID)")
        return quote(pid.strip(), safe="")

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if name == "spanlight_list_traces":
            pid = self._project_id(args)
            params: dict[str, Any] = {"limit": int(args.get("limit", 20))}
            for key in ("status", "model", "search"):
                if args.get(key):
                    params[key] = str(args[key])
            return self._get_json(f"/api/v1/projects/{pid}/traces", params=params)
        if name == "spanlight_get_trace":
            pid = self._project_id(args)
            tid = quote(str(args["trace_id"]), safe="")
            return self._get_json(
                f"/api/v1/projects/{pid}/traces/{tid}", params={"content": "bounded"}
            )
        if name == "spanlight_get_insights":
            pid = self._project_id(args)
            params = {"limit": int(args.get("limit", 25))}
            for key in ("kind", "severity"):
                if args.get(key):
                    params[key] = str(args[key])
            return self._get_json(f"/api/v1/projects/{pid}/insights", params=params)
        if name == "spanlight_get_health":
            pid = self._project_id(args)
            return self._get_json(f"/api/v1/projects/{pid}/health")
        if name == "spanlight_get_spend":
            pid = self._project_id(args)
            params = {"environment": str(args["environment"])} if args.get("environment") else {}
            return self._get_json(f"/api/v1/projects/{pid}/metrics/overview", params=params)
        if name == "spanlight_get_prompt":
            prompt_name = "/".join(quote(part, safe="") for part in str(args["name"]).split("/"))
            label = str(args.get("label") or "production")
            return self._get_json(f"/v1/prompts/{prompt_name}", params={"label": label})
        raise KeyError(f"Unknown MCP tool: {name}")

    def _get_json(self, path: str, *, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        resp = self._http.get(path, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"items": data}

    def close(self) -> None:
        self._http.close()


class SpanlightMcpServer:
    """Minimal JSON-RPC 2.0 stdio server implementing MCP initialize, tools/list, tools/call."""

    def __init__(self, client: SpanlightMcpClient) -> None:
        self._client = client

    def handle_message(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        req_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if req_id is None:
            return None  # notifications require no response
        try:
            if method == "initialize":
                return self._ok(
                    req_id,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "spanlight-mcp", "version": "0.1.0"},
                    },
                )
            if method == "ping":
                return self._ok(req_id, {})
            if method == "tools/list":
                return self._ok(req_id, {"tools": TOOLS})
            if method == "tools/call":
                tool_name = str(params.get("name", ""))
                tool_args = params.get("arguments") or {}
                data = self._client.call_tool(tool_name, tool_args)
                return self._ok(
                    req_id,
                    {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]},
                )
            return self._err(req_id, -32601, f"Method not found: {method}")
        except KeyError as exc:
            return self._err(req_id, -32602, str(exc))
        except (ValueError, httpx.HTTPError) as exc:
            return self._ok(
                req_id,
                {"isError": True, "content": [{"type": "text", "text": f"Error: {exc}"}]},
            )

    @staticmethod
    def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def serve_stdio(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                stdout.write(json.dumps(self._err(None, -32700, f"Parse error: {exc}")) + "\n")
                stdout.flush()
                continue
            reply = self.handle_message(msg)
            if reply is not None:
                stdout.write(json.dumps(reply) + "\n")
                stdout.flush()


def main() -> None:
    client = SpanlightMcpClient(McpSettings.from_env())
    try:
        SpanlightMcpServer(client).serve_stdio()
    finally:
        client.close()
