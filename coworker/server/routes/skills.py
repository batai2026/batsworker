"""Skills — `/v1/skills`.

The skill store's REST surface: list (workspace-scoped), create/update/delete, move
between scopes, "Show folder" reveal, and the two-stage install (`/upload` stages and
returns a preview; `/upload/confirm` installs — nothing lands on disk before the confirm).
`/demo` stages a SKILL.md generated from a recorded demonstration through the same
stage→confirm handshake.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from fastapi import APIRouter

from ..manager import SessionManager


def build_skills_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/skills")
    def skills(workspace: str = "") -> dict[str, Any]:
        return {"skills": manager.list_skills(workspace or None)}

    @router.post("/v1/skills")
    def create_skill(body: dict) -> dict[str, Any]:
        return manager.create_skill(body or {})

    @router.patch("/v1/skills/{name}")
    def update_skill(name: str, body: dict) -> dict[str, Any]:
        return manager.update_skill(name, body or {})

    @router.delete("/v1/skills/{name}")
    def delete_skill(name: str, workspace: str = "") -> dict[str, Any]:
        return manager.delete_skill(name, workspace or None)

    @router.post("/v1/skills/{name}/move")
    def move_skill(name: str, body: dict) -> dict[str, Any]:
        return manager.move_skill(name, body or {})

    @router.post("/v1/skills/{name}/reveal")
    def reveal_skill(name: str, body: dict) -> dict[str, Any]:
        # §6 "Show folder": open the skill's folder in the OS file manager (local machine).
        return manager.reveal_skill(name, str((body or {}).get("workspace", "")) or None)

    @router.post("/v1/skills/upload")
    def stage_skill_upload(body: dict) -> dict[str, Any]:
        # Stage → preview; nothing is installed until /upload/confirm (SKILLS-SPEC §4.2).
        data_b64 = str((body or {}).get("data_b64", ""))
        if not data_b64:
            return {"ok": False, "error": "No archive supplied."}
        try:
            data = base64.b64decode(data_b64, validate=True)
        except (ValueError, binascii.Error):
            return {"ok": False, "error": "Invalid archive encoding."}
        return manager.stage_skill_upload(data, str((body or {}).get("filename", "")))

    @router.post("/v1/skills/upload/confirm")
    def confirm_skill_upload(body: dict) -> dict[str, Any]:
        return manager.confirm_skill_upload(body or {})

    @router.post("/v1/skills/demo")
    def stage_demo_skill(body: dict) -> dict[str, Any]:
        """Stage a skill generated from a pasted/recorded demonstration.

        Body: ``name``, ``description``, and either ``steps`` (list of step
        strings) or ``recording`` (a saved browser replay name). The generated
        SKILL.md is staged — the user reviews it and confirms via
        ``POST /v1/skills/upload/confirm`` before anything is installed.
        """
        return manager.stage_demo_skill(body or {})

    return router
