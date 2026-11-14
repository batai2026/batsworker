"""FastAPI app — OpenAI-compatible endpoint + WS session API + REST.

The control plane every surface (GUI/IDE/messaging) rides on. The WS carries the engine
event stream and the approval channel; `/v1/chat/completions` is the OpenAI-compatible
proxy so any OpenAI-format client can use the runtime as a backend.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import (
    ALLOWED_ORIGIN_RE,
    request_authenticated,
    spa_owned_path as _spa_owned_path,
)
from .manager import SessionManager
from .routes.attachments_messaging import build_attachments_messaging_router
from .routes.automations import build_automations_router
from .routes.cloud import build_cloud_router
from .routes.connectors import build_connectors_router
from .routes.inbox import build_inbox_router
from .routes.mcp import build_mcp_router
from .routes.memory import build_memory_router
from .routes.observability import build_observability_router
from .routes.personas import build_personas_router
from .routes.plugins import build_plugins_router
from .routes.providers import build_providers_router
from .routes.sessions import build_sessions_router
from .routes.teams_board import build_teams_board_router
from .routes.workspaces import build_workspaces_router
from .routes.security import build_security_router
from .routes.settings import build_settings_router
from .routes.system import build_system_router
from .routes.ws import build_ws_router
from .routes.skills import build_skills_router


def create_app(manager: SessionManager) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            live = (
                await manager.start_gateway()
            )  # start messaging listeners (if configured)
            if live:
                print(f"[coworker] messaging gateway live: {', '.join(live)}")
        except Exception:  # never let a bad connector stop the server
            import traceback

            traceback.print_exc()
        yield
        await manager.aclose()  # stop gateway + close MCP connections on shutdown

    app = FastAPI(title="coworker", version="1.0.5", lifespan=lifespan)
    api_token = os.environ.get("COWORKER_API_TOKEN", "")
    tokenless_paths = {
        "/v1/health",
        "/auth/callback",
        "/mcp/oauth/callback",
        "/oauth/callback",
    }

    @app.middleware("http")
    async def require_sidecar_token(request: Request, call_next):
        # Preflights carry the requested header name, not its value. CORS checks the
        # Origin; the actual state-changing request still must authenticate.
        if (
            not api_token
            or request.method == "OPTIONS"
            or request.url.path in tokenless_paths
            # `/v1/board` carries its own, stronger auth: per-actor board tokens
            # (identity + access), designed to be handed to external harnesses and
            # other machines — which can never hold the machine-local sidecar token.
            or request.url.path.startswith("/v1/board/")
            # The desktop shell serves the SPA from this same origin, and a browser
            # navigation cannot carry the header; those paths must stay reachable.
            or (getattr(app.state, "serves_spa", False) and _spa_owned_path(request.url.path))
            or request_authenticated(request, api_token)
        ):
            return await call_next(request)
        return JSONResponse(
            {"error": "missing or invalid OpenWorker sidecar token"},
            status_code=401,
        )

    app.add_middleware(
        CORSMiddleware,
        # Pinned to the desktop webview + localhost (see ALLOWED_ORIGIN_RE): stops a random
        # website the user visits from reading local API responses cross-origin.
        allow_origin_regex=ALLOWED_ORIGIN_RE.pattern,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.manager = manager

    # -- personas ---------------------------------------------------------------
    app.include_router(build_personas_router(manager))

    # -- inbox, subscriptions, per-session rails --------------------------------
    app.include_router(build_inbox_router(manager))

    # -- skills ----------------------------------------------------------------
    app.include_router(build_skills_router(manager))

    # -- Creator 面（P1-4）：能力槽与插件 dry-run 预览 ---------------------------------
    app.include_router(build_plugins_router(manager))

    # -- workspaces (the folder the agent works in) ------------------------------
    app.include_router(build_workspaces_router(manager))

    # -- sessions (transcript, artifacts, checkpoints, fork/replay) -------------
    app.include_router(build_sessions_router(manager))

    # -- agent teams + the open board surface -----------------------------------
    app.include_router(build_teams_board_router(manager))

    # -- memory -----------------------------------------------------------------
    app.include_router(build_memory_router(manager))

    # -- security posture (threat model, findings, egress, sandbox) --------------
    app.include_router(build_security_router(manager))

    # -- system: health, agents, the OpenAI-compatible proxy ---------------------
    app.include_router(build_system_router(manager, api_token))

    # -- WebSocket: the session channel + the event stream ----------------------
    app.include_router(build_ws_router(manager, api_token))

    # -- MCP servers ------------------------------------------------------------
    app.include_router(build_mcp_router(manager))

    # -- connectors (Slack / Telegram / …) --------------------------------------
    app.include_router(build_connectors_router(manager))

    # -- OpenWorker Cloud: sign-in + managed one-click connect ------------------
    app.include_router(build_cloud_router(manager))

    # -- audit / browser observability ------------------------------------------
    app.include_router(build_observability_router(manager))

    # -- model providers (OpenAI, Ollama, …) ------------------------------------
    app.include_router(build_providers_router(manager))

    # -- settings (model API key) -----------------------------------------------
    app.include_router(build_settings_router(manager))

    # -- attachments, direct-message routing, dev injector ----------------------
    app.include_router(build_attachments_messaging_router(manager))

    # -- automations (scheduled tasks) ------------------------------------------
    app.include_router(build_automations_router(manager))

    return app


