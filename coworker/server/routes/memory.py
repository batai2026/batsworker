"""Memory — `/v1/memory`.

The curated memory store: list/add, the "settings" pair (declared before `/{item_id}` so
`settings` can never be parsed as an id), edit/delete, and the MEMORY.md round-trip —
export writes the store as an editable file, import makes that file the source of truth,
reveal opens its folder in the OS file manager.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..manager import SessionManager


def build_memory_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/memory")
    def memory() -> dict[str, Any]:
        return {"memory": manager.list_memory()}

    @router.post("/v1/memory")
    def add_memory(body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.add_memory(
            str(body.get("content", "")), str(body.get("scope", "workspace"))
        )

    # Declared before the /{item_id} routes so "settings" can never be parsed as an id.
    @router.get("/v1/memory/settings")
    def memory_settings() -> dict[str, Any]:
        return manager.get_memory_settings()

    @router.put("/v1/memory/settings")
    def memory_settings_put(body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.set_memory_settings(
            enabled=bool(body["enabled"]) if "enabled" in body else None,
            user_rules=str(body["user_rules"]) if "user_rules" in body else None,
        )

    @router.patch("/v1/memory/{item_id}")
    def memory_patch(item_id: int, body: dict) -> dict[str, Any]:
        return manager.update_memory(item_id, str((body or {}).get("content", "")))

    @router.delete("/v1/memory/{item_id}")
    def memory_delete(item_id: int) -> dict[str, Any]:
        return manager.delete_memory(item_id)

    @router.delete("/v1/memory")
    def memory_delete_all() -> dict[str, Any]:
        return manager.delete_all_memory()

    @router.post("/v1/memory/export")
    def memory_export(body: dict) -> dict[str, Any]:
        """Write the memory store as an editable MEMORY.md (plain-text memory)."""
        return manager.export_memory(str((body or {}).get("workspace", "")) or None)

    @router.post("/v1/memory/import")
    def memory_import(body: dict) -> dict[str, Any]:
        """Re-import the edited MEMORY.md — the file becomes the source of truth."""
        return manager.import_memory(str((body or {}).get("workspace", "")) or None)

    @router.post("/v1/memory/reveal")
    def memory_reveal(body: dict) -> dict[str, Any]:
        """Open the folder containing MEMORY.md in the OS file manager."""
        return manager.reveal_memory(str((body or {}).get("workspace", "")) or None)

    return router
