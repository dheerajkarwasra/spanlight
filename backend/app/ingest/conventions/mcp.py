"""Model Context Protocol (MCP) OTLP semantic-convention mapper.

Maps spans emitted by MCP clients and servers (`mcp.*`, `rpc.system="mcp"`) onto Spanlight's
canonical span shape so tool calls, resource reads, and prompt gets render natively and feed
Doctor's agent detectors. Any accompanying `gen_ai.*` attributes are filled afterwards by
`conventions/__init__.py`.
"""

from collections.abc import Mapping
from typing import Any

from app.ingest.conventions.kinds import mcp_kind
from app.ingest.conventions.read import as_text, first
from app.ingest.conventions.types import MappedFields
from app.ingest.otlp_values import parse_content

_CONSUMED_KEYS = (
    "mcp.method.name",
    "mcp.tool.name",
    "mcp.session.id",
    "mcp.server.name",
    "mcp.resource.uri",
    "mcp.input",
    "mcp.request.arguments",
    "mcp.output",
    "mcp.response.result",
)


def map_mcp(attributes: Mapping[str, Any]) -> MappedFields:
    """Extract kind, operation, input/output payloads, and metadata from MCP OTLP attributes."""
    consumed: set[str] = set()
    method_name = first(attributes, "mcp.method.name")
    tool_name = first(attributes, "mcp.tool.name")
    if method_name is not None:
        consumed.add("mcp.method.name")
    if tool_name is not None:
        consumed.add("mcp.tool.name")

    raw_input = first(attributes, "mcp.input", "mcp.request.arguments")
    raw_output = first(attributes, "mcp.output", "mcp.response.result")
    for key in ("mcp.input", "mcp.request.arguments", "mcp.output", "mcp.response.result"):
        if key in attributes:
            consumed.add(key)

    metadata: dict[str, object] = {}
    for attr_key, meta_key in (
        ("mcp.session.id", "mcp.session_id"),
        ("mcp.server.name", "mcp.server_name"),
        ("mcp.resource.uri", "mcp.resource_uri"),
    ):
        val = first(attributes, attr_key)
        if val is not None:
            metadata[meta_key] = val
            consumed.add(attr_key)

    operation = as_text(tool_name) or as_text(method_name) or "mcp"
    return MappedFields(
        input=parse_content(raw_input) if raw_input is not None else None,
        output=parse_content(raw_output) if raw_output is not None else None,
        kind=mcp_kind(method_name, attributes),
        operation=operation,
        metadata=metadata,
        consumed_keys=frozenset(consumed),
    )
