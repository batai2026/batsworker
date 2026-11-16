"""Model providers (OpenAI, Ollama, Bedrock, …) and the Codex sign-in flow.

`/v1/providers` is the credential table the Settings → Models pane edits; `verify` is a
live read-only credential probe; the `openai-codex/*` trio drives the ChatGPT-plan sign-in
that has to run as a background task (it waits on a loopback callback, which can take
minutes — the GUI polls `status` for the flip instead of holding the request open).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from ..manager import SessionManager


def build_providers_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/providers")
    def providers_get() -> list[dict[str, Any]]:
        return manager.get_providers()

    @router.post("/v1/providers")
    def providers_set(body: dict) -> dict[str, Any]:
        name = (body or {}).get("name", "")
        if not name:
            return {"ok": False, "error": "name required"}
        return manager.set_provider(name, (body or {}).get("fields"))

    @router.delete("/v1/providers/{name}")
    def providers_remove(name: str) -> dict[str, Any]:
        return manager.remove_provider(name)

    @router.post("/v1/providers/verify")
    async def providers_verify(body: dict) -> dict[str, Any]:
        # Live read-only credential check (sync httpx) — run off the event loop.
        name = (body or {}).get("name", "") or "openai"
        return await asyncio.to_thread(
            manager.verify_provider, name, (body or {}).get("fields")
        )

    @router.post("/v1/providers/openai-codex/signin")
    async def codex_signin() -> dict[str, Any]:
        # Opens the system browser and waits on the loopback callback — that can
        # take minutes, so it runs as a background task; the GUI polls the status
        # route for the flip (authorizing → signed_in | last_error). Same shape as
        # the MCP OAuth connect route.
        manager.begin_codex_signin()
        asyncio.create_task(manager.codex_signin())
        return {"ok": True, "started": True}

    @router.get("/v1/providers/openai-codex/status")
    def codex_status() -> dict[str, Any]:
        return manager.codex_status()

    @router.post("/v1/providers/openai-codex/signout")
    def codex_signout() -> dict[str, Any]:
        return manager.codex_signout()

    return router
