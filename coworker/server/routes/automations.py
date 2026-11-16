"""Automations (scheduled tasks) — `/v1/automations`.

The scheduler itself lives in `coworker/server/manager_scheduler.py`; this module is only
the REST surface the GUI's Automations tab drives: list, create, edit, delete, mark seen,
plus the manual-run handshake (`prepare_manual_run` returns a live session the GUI opens
and drives, `finalize` closes the run record).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..manager import SessionManager


def build_automations_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/automations")
    def automations_list() -> dict[str, Any]:
        return manager.list_automations()

    @router.post("/v1/automations")
    def automations_create(body: dict) -> dict[str, Any]:
        return manager.create_automation(body or {})

    @router.get("/v1/automations/templates")
    def automation_templates_list() -> dict[str, Any]:
        """The automation template marketplace catalog (backend-served, extensible)."""
        return manager.list_automation_templates()

    @router.post("/v1/automations/templates/{key}/instantiate")
    def automation_template_instantiate(key: str, body: dict) -> dict[str, Any]:
        """Create an automation from a marketplace template in one step."""
        return manager.instantiate_automation_template(key, body or {})

    @router.get("/v1/automations/{task_id}")
    def automation_get(task_id: str) -> dict[str, Any]:
        return manager.get_automation(task_id)

    @router.patch("/v1/automations/{task_id}")
    def automation_update(task_id: str, body: dict) -> dict[str, Any]:
        return manager.update_automation(task_id, body or {})

    @router.delete("/v1/automations/{task_id}")
    def automation_delete(task_id: str) -> dict[str, Any]:
        return manager.delete_automation(task_id)

    @router.post("/v1/automations/{task_id}/seen")
    def automations_seen(task_id: str) -> dict[str, Any]:
        return manager.mark_automation_seen(task_id)

    @router.post("/v1/automations/{task_id}/run")
    def automation_run(task_id: str) -> dict[str, Any]:
        # Prepare a live manual run; the GUI opens the returned session and drives it.
        return manager.prepare_manual_run(task_id)

    @router.post("/v1/automations/{task_id}/runs/{run_id}/finalize")
    def automation_run_finalize(task_id: str, run_id: str) -> dict[str, Any]:
        return manager.finalize_manual_run(task_id, run_id)

    return router
