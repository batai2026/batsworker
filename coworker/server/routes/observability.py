"""Audit ledgers, the browser pane, and web-search settings.

Read-mostly observability: the tool/connector audit trail, the plugin consent ledger, the
browser automation pane's state/screenshot/recording handles, and the keyless-vs-keyed
web-search provider choice.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..manager import SessionManager


def build_observability_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    # -- audit / browser observability ------------------------------------------
    @router.get("/v1/audit")
    def audit_list(
        limit: int = 100,
        session_id: str | None = None,
        connector: str | None = None,
        tool: str | None = None,
    ) -> dict[str, Any]:
        return {
            "events": manager.list_audit(
                limit=limit, session_id=session_id, connector=connector, tool=tool
            )
        }

    @router.get("/v1/plugins/audit")
    def plugin_audit_list(limit: int = 100, id: str | None = None) -> dict[str, Any]:
        """P2-10: install/uninstall/permission-change ledger (newest first)."""
        from ...plugins import consent_audit

        return {"events": consent_audit.read(limit=limit, id=id)}

    @router.get("/v1/browser/state")
    def browser_state_get() -> dict[str, Any]:
        return manager.browser_state()

    @router.post("/v1/browser/screenshot")
    def browser_screenshot_post() -> dict[str, Any]:
        return manager.browser_screenshot()

    @router.post("/v1/browser/close")
    def browser_close_post() -> dict[str, Any]:
        return manager.browser_close()

    @router.get("/v1/browser/recordings")
    def browser_recordings_get() -> dict[str, Any]:
        """Saved browser replay scripts (the demo→skill door's recording source)."""
        return manager.browser_recordings()

    # -- web search -------------------------------------------------------------
    @router.get("/v1/web-search")
    def web_search_get() -> dict[str, Any]:
        return manager.get_web_search()

    @router.post("/v1/web-search")
    def web_search_set(body: dict) -> dict[str, Any]:
        provider = (body or {}).get("provider", "")
        if not provider:
            return {"ok": False, "error": "provider required"}
        return manager.set_web_search(provider, (body or {}).get("api_key"))

    # -- trace export (double opt-in: endpoint configured AND export called) -----
    @router.post("/v1/observability/export")
    def observability_export(body: dict) -> dict[str, Any]:
        return manager.export_trace_otlp(str((body or {}).get("session_id", "")))

    return router
