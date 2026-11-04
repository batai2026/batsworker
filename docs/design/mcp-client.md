# MCP client

> Source: `coworker/mcp/` — `config.py`, `client.py`, `tools.py`,
> `oauth.py`. Uses the official `mcp` Python SDK (pinned `>=1.28.1,<2` — the
> floor avoids PYSEC-2026-3481/3482/3483, the ceiling because 2.0.0 removed
> `streamablehttp_client`).

Any MCP server plugs into OpenWorker as a tool source, with per-tool control.

## Configuration

Standard `mcpServers` JSON — Claude-Desktop-paste-compatible. Layered:

- **Global**: `~/.config/coworker/mcp.json`
- **Workspace**: `<workspace>/.coworker/mcp.json` — only honored for *trusted*
  workspaces; the global file wins on a name clash.

`MCPServerDef` fields: transport (stdio or streamable-http), plus per-server
`include_tools` / `exclude_tools`, `requires_approval`, and `${VAR}` secret
resolution from the environment. A workspace config can narrow a server's
tool surface but never silently widen trust — the same direction rule as the
permission config.

## Lifecycle

`client.py::MCPManager` keeps **one persistent asyncio task per server**:

- **stdio** — `stdio_client`, with stderr captured to a temp file so a crash
  leaves evidence for the user (and for `request_tool`-style diagnosis).
- **streamable-http** — `streamablehttp_client`.

API: `ensure()` (start/idempotent), `tools()` (list), `verify()` — a real
health check that round-trips a `tools/list`, not just a socket check — and
`call()`.

## Tool bridging

`tools.py::build_callables` wraps each MCP tool as a **synchronous registry
callable** named `mcp__<server>__<tool>`, bridged into the server's asyncio
task via `run_coroutine_threadsafe` (the engine executes tools in worker
threads). The OpenAI function schema is derived from the MCP `inputSchema`.
From there, MCP tools are ordinary tools: same permission pipeline, same
approval cards, `requires_approval` maps onto the standing-rule machinery.

## OAuth (MCP servers requiring auth)

`oauth.py` implements **OAuth 2.1 + PKCE + Dynamic Client Registration** on
top of the SDK's `OAuthClientProvider`:

- Tokens live in the `SecretStore` under profile `mcp-oauth:<server>`.
- The browser callback lands on the sidecar's loopback
  `GET /mcp/oauth/callback`.
- Non-interactive contexts (automation runs, relay) refuse to pop a browser
  sign-in (`InteractiveAuthRequired`) but may silently refresh a valid token.
