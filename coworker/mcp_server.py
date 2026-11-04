"""`openworker-mcp` — OpenWorker's core capabilities as an MCP server on stdio.

The way an external agent (Claude Code, Cursor, any MCP client) drives OpenWorker:
its MCP config runs `openworker mcp --url http://127.0.0.1:8765 --token <token>`,
it sees the session/artifact/approval/automation tools, and it can create tasks,
read results, manage approvals, and trigger automations.

This is a thin HTTP bridge to the running OpenWorker sidecar — it holds no state,
makes no decisions, and never touches the filesystem directly. Every call goes
through the sidecar's permission engine (same approval flow, same Inbox parking).
An external agent gets the same safety guarantees as the GUI.

Tool results are JSON — raw data for the calling agent, not prose.
"""

from __future__ import annotations

import json
from typing import Any


def build(base_url: str, token: str, *, timeout: float = 120.0):
    """Assemble the FastMCP server for one OpenWorker sidecar connection."""
    from mcp.server.fastmcp import FastMCP

    import httpx

    client = httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=timeout,
        headers={"X-OpenWorker-Token": token},
    )

    mcp = FastMCP(
        "openworker",
        instructions=(
            "Drive an OpenWorker instance: create and run agent tasks, read the "
            "files they produce, manage approvals, and manage automations. "
            "OpenWorker's permission engine (approval cards, hard floors, Inbox) "
            "governs every action — you get the same safety guarantees as the GUI. "
            "Typical loop: automation_create (or automation_run) → wait for the "
            "run to complete → artifact_list → artifact_read the results."
        ),
    )

    def _get(path: str, params: dict = {}) -> Any:
        resp = client.get(path, params={k: v for k, v in params.items() if v is not None})
        resp.raise_for_status()
        return resp.json()

    def _post(path: str, body: dict = {}) -> Any:
        resp = client.post(path, json=body)
        resp.raise_for_status()
        return resp.json()

    def _safe(func, *args, **kwargs) -> Any:
        import httpx

        try:
            return func(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json()
                return {"error": detail.get("error", exc.response.text), "status": exc.response.status_code}
            except Exception:
                return {"error": str(exc), "status": exc.response.status_code}
        except Exception as exc:
            return {"error": str(exc)}

    # -- sessions -----------------------------------------------------------------

    @mcp.tool()
    def session_list() -> Any:
        """List all OpenWorker sessions with status, workspace, agent, and metadata."""
        return _safe(_get, "/v1/sessions")

    @mcp.tool()
    def session_get(session_id: str) -> Any:
        """One session's full record: workspace, agent, model, usage, last status."""
        return _safe(_get, f"/v1/sessions/{session_id}")

    # -- artifacts (files the agent produced) ---------------------------------------

    @mcp.tool()
    def artifact_list(session_id: str) -> Any:
        """List files produced by a session: path, kind, size, modified time."""
        return _safe(_get, f"/v1/sessions/{session_id}/artifacts")

    @mcp.tool()
    def artifact_read(session_id: str, path: str) -> Any:
        """Read a file produced by a session. Returns the text content (or base64
        for binary files)."""
        return _safe(_get, f"/v1/sessions/{session_id}/artifacts/read", {"path": path})

    # -- approvals (Inbox) ------------------------------------------------------------

    @mcp.tool()
    def inbox_list(kind: str = "", state: str = "pending") -> Any:
        """List pending Inbox items (approvals, questions, notifications) across "
        "all sessions. Filter by kind or state."""
        params = {"state": state}
        if kind:
            params["kind"] = kind
        return _safe(_get, "/v1/inbox", params)

    @mcp.tool()
    def inbox_resolve(item_id: str, resolution: str) -> Any:
        """Resolve an Inbox item: 'allow', 'deny', 'allow_once', or a text answer "
        "for questions. First responder wins."""
        return _safe(_post, f"/v1/inbox/{item_id}/resolve", {"resolution": resolution})

    # -- automations ------------------------------------------------------------------

    @mcp.tool()
    def automation_list() -> Any:
        """List all automations (scheduled tasks and standing goals) with status, "
        "schedule/trigger, run count, and budget."""
        return _safe(_get, "/v1/automations")

    @mcp.tool()
    def automation_create(
        title: str,
        instructions: str,
        cron: str = "",
        fire_at: str = "",
        trigger_kind: str = "",
        trigger_params: str = "",
        budget_tokens: int = 0,
    ) -> Any:
        """Create an automation. Schedule via cron (e.g. '10 19 * * *'), one-time via "
        "fire_at (ISO datetime), or event-triggered via trigger_kind+params "
        "(file/board_state/inbox). Set budget_tokens to cap lifetime usage."""
        body: dict[str, Any] = {"title": title, "instructions": instructions}
        if cron:
            body["cron"] = cron
        if fire_at:
            body["fire_at"] = fire_at
        if trigger_kind:
            body["trigger"] = {"kind": trigger_kind, "params": json.loads(trigger_params or "{}")}
        if budget_tokens:
            body["budget_tokens"] = budget_tokens
        return _safe(_post, "/v1/automations", body)

    @mcp.tool()
    def automation_run(task_id: str) -> Any:
        """Run an automation now. Returns the run's session_id — the results are "
        "in that session's transcript and artifacts."""
        return _safe(_post, f"/v1/automations/{task_id}/run")

    @mcp.tool()
    def automation_delete(task_id: str) -> Any:
        """Delete an automation and its run history."""
        return _safe(_post, f"/v1/automations/{task_id}/delete", {})

    # -- board (delegates to the existing board dialect) --------------------------------

    @mcp.tool()
    def board_list(state: str = "", assignee: str = "") -> Any:
        """List work items on the team board, optionally filtered by state or assignee."""
        return _safe(_get, "/v1/board", {"state": state, "assignee": assignee})

    @mcp.tool()
    def board_item(item: int) -> Any:
        """One board item in full: description, acceptance criteria, status, comments."""
        return _safe(_get, f"/v1/board/{item}")

    # -- audit ----------------------------------------------------------------------------

    @mcp.tool()
    def audit_list(session_id: str = "", tool: str = "", limit: int = 50) -> Any:
        """Recent tool activity (sanitized). Filter by session, tool, or connector."""
        params: dict[str, Any] = {"limit": limit}
        if session_id:
            params["session_id"] = session_id
        if tool:
            params["tool"] = tool
        return _safe(_get, "/v1/audit", params)

    # -- timeline ----------------------------------------------------------------------------

    @mcp.tool()
    def session_timeline(session_id: str, limit: int = 100) -> Any:
        """Span-level execution timeline for a session: tool durations, status, "
        "and metadata. For observability and debugging."""
        return _safe(_get, f"/v1/sessions/{session_id}/timeline", {"limit": limit})

    return mcp


def serve(base_url: str, token: str, *, timeout: float = 120.0) -> None:
    build(base_url, token, timeout=timeout).run("stdio")
