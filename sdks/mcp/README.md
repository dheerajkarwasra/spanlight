# Spanlight MCP Server (`spanlight-mcp`)

> **Release preview.** The stdio server and the installation steps below are a release preview. The implementation is awaiting publication.

Give Claude Code, Cursor, Codex, and Antigravity direct read access to your Spanlight traces, Doctor insights, spend overview, and prompts over stdio JSON-RPC.

## Install from a checkout

From the repository root, run `python -m pip install ./sdks/mcp` with Python 3.11 or newer. Configure your MCP client to run the installed `spanlight-mcp` executable over stdio.

## Configuration

```json
{
  "mcpServers": {
    "spanlight": {
      "command": "spanlight-mcp",
      "env": {
        "SPANLIGHT_HOST": "http://localhost:8080",
        "SPANLIGHT_API_KEY": "spl_live_...",
        "SPANLIGHT_PROJECT_ID": "<project-uuid>"
      }
    }
  }
}
```

Required API key scopes: `traces:read` and `prompts:read`.
