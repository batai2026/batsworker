"""Creator-surface plugins — capability slots and the dry-run preview.

`/v1/plugins/slots` reports which extension points this build exposes; `/v1/plugins/preview`
parses a bundle and returns the unified consent summary **without installing anything**.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..manager import SessionManager


def build_plugins_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    # -- Creator 面（P1-4）：能力槽与插件 dry-run 预览 ---------------------------------
    @router.get("/v1/plugins/slots")
    def plugin_slots() -> dict[str, Any]:
        return manager.plugin_slots()

    @router.post("/v1/plugins/preview")
    def preview_plugin(body: dict) -> Any:
        # 只读：解析 bundle 并返回统一 consent 摘要，绝不安装。
        body = body or {}
        result = manager.preview_plugin(
            str(body.get("kind", "")),
            source=body.get("source") or None,
            name=body.get("name") or None,
        )
        if not result.get("ok"):
            return JSONResponse(
                {"error": result.get("error", "preview failed")},
                status_code=int(result.get("status") or 400),
            )
        return result

    return router
