"""MCP servers — `/v1/mcp` plus the OAuth loopback landing.

The agent's MCP client config: list/add/patch/delete servers, read the tool catalog a
server exposes, connect (`auth: oauth` servers open the system browser and finish on
`/mcp/oauth/callback`), sign out, and hot-reload after a config edit.

`PATCH` is the consent boundary: widening a server's permissions needs an explicit
re-consent (`confirm`), which is a request control and never persisted to `mcp.json`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from ..manager import SessionManager
from ..pages import _browser_page


def build_mcp_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    # -- MCP servers ------------------------------------------------------------
    @router.get("/v1/mcp")
    def mcp_list() -> dict[str, Any]:
        return {"servers": manager.list_mcp()}

    @router.post("/v1/mcp")
    def mcp_add(body: dict) -> dict[str, Any]:
        name = body.get("name")
        config = body.get("config")
        if not name or not isinstance(config, dict):
            return {"ok": False, "error": "name and config required"}
        return manager.add_mcp(name, config)

    @router.patch("/v1/mcp/{name}")
    def mcp_patch(name: str, body: dict) -> Any:
        body = dict(body or {})
        # consent v2: a permission widening needs an explicit re-consent; `confirm`
        # is a request control, never persisted into mcp.json.
        confirm = bool(body.pop("confirm", False))
        result = manager.patch_mcp(name, body, confirm=confirm)
        if result.get("status"):
            return JSONResponse(result, status_code=result["status"])
        return result

    @router.delete("/v1/mcp/{name}")
    def mcp_delete(name: str) -> dict[str, Any]:
        return manager.delete_mcp(name)

    @router.get("/v1/mcp/{name}/tools")
    async def mcp_tools(name: str) -> dict[str, Any]:
        return await manager.mcp_tools(name)

    @router.post("/v1/mcp/{name}/connect")
    async def mcp_connect(name: str) -> dict[str, Any]:
        # Connect now. For `auth: oauth` servers the first connect opens the system
        # browser and waits on the loopback callback — that can take minutes, so it
        # runs as a background task; the GUI polls /v1/mcp for the status flip
        # (authorizing → connected | needs_auth + last_error).
        manager.begin_mcp_connect(name)  # authorizing shows on the very next poll
        asyncio.create_task(manager.connect_mcp(name))
        return {"ok": True, "started": True}

    @router.post("/v1/mcp/{name}/signout")
    async def mcp_signout(name: str) -> dict[str, Any]:
        return await manager.signout_mcp(name)

    @router.get("/mcp/oauth/callback")
    async def mcp_oauth_callback(
        code: str = "", state: str = "", error: str = ""
    ) -> Any:
        # Loopback landing for the MCP OAuth browser flow (mcp/oauth.py). Browser-facing:
        # returns the same styled page as the managed-connector callbacks.
        from ...mcp import oauth as mcp_oauth

        if error:
            return HTMLResponse(
                _browser_page(
                    "Sign-in failed",
                    "The service reported an error. Return to OpenWorker and try again.",
                    ok=False,
                    error=error,
                ),
                status_code=400,
            )
        if not code or not mcp_oauth.deliver_callback(code, state or None):
            return HTMLResponse(
                _browser_page(
                    "Nothing waiting for this sign-in",
                    "The sign-in may have timed out. Return to OpenWorker and start it again.",
                    ok=False,
                ),
                status_code=400,
            )
        return HTMLResponse(
            _browser_page(
                "Connected",
                "Sign-in complete. You can close this tab and return to OpenWorker.",
                ok=True,
            )
        )

    @router.post("/v1/mcp/reload")
    async def mcp_reload() -> dict[str, Any]:
        return await manager.reload_mcp()

    return router
