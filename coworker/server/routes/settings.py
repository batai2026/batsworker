"""`/v1/settings` — the persisted preference surface.

One `POST` per switch, all thin wrappers over `SessionManager.set_*`: model key and model
list, onboarding, experimental connectors, UI layout knobs, and the feature flags
(auto-approve and its shadow evaluator, PDF fallback thresholds, compaction overrides).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..manager import SessionManager


def build_settings_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/settings")
    def settings_get() -> dict[str, Any]:
        return manager.get_settings()

    @router.post("/v1/settings/model-key")
    def settings_set_model_key(body: dict) -> dict[str, Any]:
        return manager.set_model_key((body or {}).get("api_key", ""))

    @router.post("/v1/settings/default-model")
    def settings_set_default_model(body: dict) -> dict[str, Any]:
        return manager.set_default_model((body or {}).get("model", ""))

    @router.post("/v1/settings/models/add")
    def settings_models_add(body: dict) -> dict[str, Any]:
        return manager.add_model((body or {}).get("model", ""))

    @router.post("/v1/settings/models/remove")
    def settings_models_remove(body: dict) -> dict[str, Any]:
        return manager.remove_model((body or {}).get("model", ""))

    @router.post("/v1/settings/onboarded")
    def settings_set_onboarded(body: dict) -> dict[str, Any]:
        return manager.set_onboarded(bool((body or {}).get("value", True)))

    @router.post("/v1/settings/experimental-connectors")
    def settings_set_experimental(body: dict) -> dict[str, Any]:
        return manager.set_experimental_connectors(bool((body or {}).get("value")))

    @router.post("/v1/settings/surfaces")
    def settings_set_surfaces(body: dict) -> dict[str, Any]:
        b = body or {}
        return manager.set_surfaces(chat=b.get("chat"), code=b.get("code"))

    @router.post("/v1/settings/scratch-base")
    def settings_set_scratch_base(body: dict) -> dict[str, Any]:
        return manager.set_scratch_base(str((body or {}).get("path", "")))

    @router.post("/v1/settings/nav-layout")
    def settings_set_nav_layout(body: dict) -> dict[str, Any]:
        return manager.set_nav_layout(str((body or {}).get("nav_layout", "")))

    @router.post("/v1/settings/sessions-peek")
    def settings_set_sessions_peek(body: dict) -> dict[str, Any]:
        # Sidebar: sessions shown per group before "Show more" (owner ask, 2026-07-03).
        return manager.set_sessions_peek((body or {}).get("sessions_peek", 5))

    @router.post("/v1/settings/context-bar")
    def settings_set_context_bar(body: dict) -> dict[str, Any]:
        # Composer: show the context-window fill bar, or just the popover (owner ask).
        return manager.set_context_bar((body or {}).get("context_bar", True))

    @router.post("/v1/settings/auto-approve")
    def settings_set_auto_approve(body: dict) -> dict[str, Any]:
        # Auto-Approve feature flag (spec §1.5): when on, Mode.AUTO_APPROVE gets an LLM
        # reviewer. Takes effect on the next session build. Turning it off leaves any
        # shadow-eval setting alone (they are independent switches).
        return manager.set_auto_approve((body or {}).get("auto_approve", False))

    @router.post("/v1/settings/auto-approve-shadow")
    def settings_set_auto_approve_shadow(body: dict) -> dict[str, Any]:
        # Shadow evaluation (Part 6 step 3): the reviewer records what it WOULD decide on
        # every approval card while the human still decides. Independent of the live flag.
        return manager.set_auto_approve_shadow(
            (body or {}).get("auto_approve_shadow", False)
        )

    @router.post("/v1/settings/pdf")
    def settings_set_pdf(body: dict) -> dict[str, Any]:
        # Token savings (owner ask, 2026-07-17): fallback mode for models without native
        # PDF support + attach-time page/size thresholds.
        b = body or {}
        return manager.set_pdf_settings(
            fallback=b.get("pdf_fallback"),
            max_pages=b.get("pdf_max_pages"),
            max_mb=b.get("pdf_max_mb"),
        )

    @router.post("/v1/settings/compaction")
    def settings_set_compaction(body: dict) -> dict[str, Any]:
        # Auto-compaction overrides (OPE-27): threshold % of the context window, the
        # absolute token cap, and the summarizer-model pin ("" → session's own model).
        b = body or {}
        return manager.set_compaction_settings(
            threshold_pct=b.get("compaction_threshold_pct"),
            cap_tokens=b.get("compaction_cap_tokens"),
            model=b.get("compaction_model"),
        )

    return router
