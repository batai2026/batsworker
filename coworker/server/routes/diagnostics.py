"""Webview diagnostics: deliver SPA-side failures to a file a human can read.

The desktop shell's release build has no devtools, so a `console.error` inside the
SPA is unreachable for anyone but a developer with a debugger attached. This route
gives those failures somewhere to land. See `coworker/ui_log.py` for the storage
contract (bounded, whitelisted, best-effort).
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Response

from ..manager import SessionManager


def build_diagnostics_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/ui/log")
    def ui_log_post(body: dict) -> dict[str, Any]:
        """Accept a small batch of client diagnostics.

        Always 200 with a count: logging is an observer, and a failing observer
        must not turn into a second error inside the page that is already
        broken. `kept` tells the caller whether anything was actually stored.
        """
        from ... import ui_log

        events = (body or {}).get("events")
        return {"ok": True, "kept": ui_log.record(events)}

    @router.get("/v1/ui/log")
    def ui_log_get(limit: int = 100) -> dict[str, Any]:
        """Newest-first, so a user can attach the file's tail to a bug report.

        The path is included because the file is otherwise in an app-data
        directory no user would think to look in -- "attach this file" is only
        actionable if we say which file.
        """
        from ... import ui_log

        return {"events": ui_log.read(limit=limit), "path": ui_log.path()}

    @router.get("/v1/ui/log/export")
    def ui_log_export() -> Response:
        """The log as a plain-text file, ready to attach to a bug report.

        Served with ``Content-Disposition: attachment`` so a browser build turns
        the click into a download; the desktop shell reads the same endpoint and
        writes the bytes through its native save dialog instead.

        Text means no filename fixups per platform, no zip, and nothing the user
        cannot open in Notepad -- which is the whole point of handing it over.
        """
        from ... import ui_log

        body = ui_log.report()
        # A dated name so two exports do not silently overwrite each other.
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return Response(
            content=body,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="openworker-diagnostics-{stamp}.txt"'
            },
        )

    return router
