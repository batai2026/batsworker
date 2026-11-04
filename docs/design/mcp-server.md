# MCP server (full capability exposure)

> Source: `coworker/mcp_server.py` (FastMCP server + HTTP bridge),
> `coworker/cli.py` (`--mcp` flag), `coworker/teams/mcp_server.py` (board-only
> server, already existed).

OpenWorker's core capabilities — sessions, artifacts, approvals, automations,
audit, timeline — exposed as MCP tools on stdio. External agents (Claude Code,
Cursor, any MCP client) can drive an OpenWorker instance through the same
permission engine and Inbox flow as the GUI.

## Launch

```bash
# The sidecar must be running.
openworker mcp --mcp-url http://127.0.0.1:8765 --mcp-token <token>
# Or let it read the token from the sidecar token file.
openworker mcp
```

MCP client config (e.g. Claude Desktop):
```json
{
  "mcpServers": {
    "openworker": {
      "command": "openworker",
      "args": ["--mcp"]
    }
  }
}
```

## Tools exposed

| Tool | Method | What it does |
|---|---|---|
| `session_list` | GET | All sessions with status, workspace, agent |
| `session_get` | GET | One session's full record |
| `artifact_list` | GET | Files produced by a session |
| `artifact_read` | GET | Read a file's content (text or base64) |
| `inbox_list` | GET | Pending approvals/questions/notifications |
| `inbox_resolve` | POST | Approve/deny/answer an Inbox item |
| `automation_list` | GET | All automations with status and schedule |
| `automation_create` | POST | Create a scheduled/event-triggered task |
| `automation_run` | POST | Run an automation now (returns session_id) |
| `automation_delete` | POST | Delete an automation |
| `board_list` | GET | Team board items |
| `board_item` | GET | One board item in full |
| `audit_list` | GET | Recent tool activity (sanitized) |
| `session_timeline` | GET | Span-level execution timeline |

## Safety

The MCP server is a **thin HTTP bridge** — it holds no state, makes no
decisions, and never touches the filesystem directly. Every call goes through
the sidecar's permission engine (same approval flow, same Inbox parking). An
external agent gets the same safety guarantees as the GUI: approval cards for
writes, hard floors for deferred execution, Inbox parking for unattended work.

Tool results are JSON — raw data for the calling agent, not prose.

## Architecture

- The server communicates with the OpenWorker sidecar via HTTP (like the
  `RemoteDialect` pattern in the board MCP server).
- It runs as a separate stdio process — the MCP client spawns it, and it
  proxies to the running sidecar.
- No authentication bypass: the sidecar token is required (read from the
  token file or passed explicitly).
