"""FastAPI app — OpenAI-compatible endpoint + WS session API + REST.

The control plane every surface (GUI/IDE/messaging) rides on. The WS carries the engine
event stream and the approval channel; `/v1/chat/completions` is the OpenAI-compatible
proxy so any OpenAI-format client can use the runtime as a backend.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import secrets
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Origins allowed to talk to the local sidecar. It binds to 127.0.0.1, but a page in the
# user's own browser can still reach loopback — so without an origin gate, any website they
# visit could read `GET /v1/sessions` (CORS was `*`) and drive a session over the WS (which
# CORS never covers) into shell/file tools. We pin to the desktop webview's own origins
# (`tauri://localhost`, Windows' `http(s)://tauri.localhost`) and localhost dev/browser
# builds. Requests with NO Origin header (curl, native clients, tests, server-to-server) are
# allowed — the gate targets browsers, which always attach an unforgeable Origin.
_ALLOWED_ORIGIN_RE = re.compile(
    r"^(tauri://localhost"
    r"|https?://localhost(:\d+)?"
    r"|https?://127\.0\.0\.1(:\d+)?"
    r"|https?://tauri\.localhost)$"
)


def _origin_allowed(origin: str | None) -> bool:
    """True if a browser Origin may use the API. Missing Origin (non-browser) passes."""
    return origin is None or bool(_ALLOWED_ORIGIN_RE.match(origin))


# Paths that keep the sidecar token requirement when the SPA is served from the sidecar.
# Everything else belongs to the bundled UI: the shell, `assets/`, `icons/`, the service
# worker and client-side routes. A top-level navigation cannot attach
# `x-openworker-token`, so gating those means the desktop window loads 401 forever.
# The API prefixes stay gated; these paths only serve the app's own build output.
_SPA_GATED_PREFIXES = ("/v1", "/ws", "/docs", "/redoc", "/openapi.json")


def _spa_owned_path(path: str) -> bool:
    """True for a path the bundled SPA serves (shell, assets, client-side route)."""
    return not path.startswith(_SPA_GATED_PREFIXES)


# Caps on inbound WebSocket traffic. The loopback socket is unauthenticated (any local
# process can reach it), so bound frames, messages, and per-connection request rate before
# building model content or starting a turn.
_WS_MAX_FRAME_BYTES = 16 * 1024 * 1024
_WS_RATE_LIMIT_COUNT = 30
_WS_RATE_LIMIT_WINDOW_SECONDS = 10.0
_MAX_MESSAGE_TEXT_CHARS = 200_000
_MAX_ATTACHMENTS_BYTES = 15_000_000  # leaves JSON overhead below the 16 MiB frame cap


def _json_value_size(value: Any) -> int:
    """Conservative UTF-8 size of parsed JSON without allocating another giant string."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, dict):
        return sum(_json_value_size(k) + _json_value_size(v) for k, v in value.items())
    if isinstance(value, list):
        return sum(_json_value_size(v) for v in value)
    return 8  # numbers, booleans, null, separators


from ..attachments import (
    MAX_ATTACHMENTS as _MAX_ATTACHMENTS,
    MAX_IMAGE_CHARS,
    MAX_PDF_CHARS,
    MAX_TEXT_CHARS,
    build_user_content,
)
from ..engine import ApprovalOutcome
from ..inbox import VIS_INBOX, VIS_INLINE, args_preview
from ..permissions import Mode
from ..providers import AssistantTurn
from .. import toolchain
from ..teams.model import AuthorityError as TeamsAuthorityError
from ..teams.model import BoardError as TeamsBoardError
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
from .routes.security import build_security_router
from .routes.settings import build_settings_router
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

    def _request_authenticated(request: Request) -> bool:
        provided = request.headers.get("x-openworker-token", "")
        return bool(
            api_token
            and provided
            and secrets.compare_digest(provided, api_token)
        )

    def _websocket_authenticated(ws: WebSocket) -> bool:
        if not api_token:
            return True
        protocols = {
            part.strip()
            for part in ws.headers.get("sec-websocket-protocol", "").split(",")
            if part.strip()
        }
        return any(secrets.compare_digest(part, api_token) for part in protocols)

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
            or _request_authenticated(request)
        ):
            return await call_next(request)
        return JSONResponse(
            {"error": "missing or invalid OpenWorker sidecar token"},
            status_code=401,
        )

    app.add_middleware(
        CORSMiddleware,
        # Pinned to the desktop webview + localhost (see _ALLOWED_ORIGIN_RE): stops a random
        # website the user visits from reading local API responses cross-origin.
        allow_origin_regex=_ALLOWED_ORIGIN_RE.pattern,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.manager = manager

    @app.get("/v1/health")
    def health(request: Request) -> dict[str, Any]:
        if api_token and not _request_authenticated(request):
            return {"status": "ok"}
        return {
            "status": "ok",
            "default_workspace": manager.default_workspace,
            "model": manager.model,
        }

    @app.get("/v1/agents")
    def agents() -> dict[str, Any]:
        return {"agents": manager.list_agents()}

    # -- personas ---------------------------------------------------------------
    app.include_router(build_personas_router(manager))

    # -- inbox, subscriptions, per-session rails --------------------------------
    app.include_router(build_inbox_router(manager))

    # -- skills ----------------------------------------------------------------
    app.include_router(build_skills_router(manager))

    # -- Creator 面（P1-4）：能力槽与插件 dry-run 预览 ---------------------------------
    app.include_router(build_plugins_router(manager))

    @app.get("/v1/workspaces/recent")
    def recent_workspaces() -> dict[str, Any]:
        return {"workspaces": manager.recent_workspaces()}

    @app.post("/v1/workspaces/open")
    def open_workspace(body: dict) -> dict[str, Any]:
        return manager.open_workspace(
            body.get("path", ""), create=bool(body.get("create"))
        )

    @app.get("/v1/workspaces/trusted")
    def trusted_workspaces() -> dict[str, Any]:
        return {"workspaces": manager.trusted_workspaces()}

    @app.post("/v1/workspaces/trust")
    def set_workspace_trust(body: dict) -> dict[str, Any]:
        return manager.set_workspace_trust(
            str((body or {}).get("path", "")),
            trusted=bool((body or {}).get("trusted", False)),
        )

    @app.post("/v1/workspaces/temp")
    def provision_temp_workspace(body: dict) -> dict[str, Any]:
        # UX-029: a code-family session starting "in a temporary folder" — created only
        # at send time, with git ready. Knowledge families keep their auto-provisioned dir.
        return manager.provision_temp_workspace(
            str((body or {}).get("session_id", "")),
            git=bool((body or {}).get("git", True)),
        )

    @app.post("/v1/sessions/{session_id}/save-as-project")
    def save_session_as_project(session_id: str, body: dict) -> dict[str, Any]:
        # UX-029 "Save as project…": move the temporary folder somewhere real. The GUI
        # reconnects afterwards so the engine rebinds to the new path.
        return manager.save_temp_as_project(session_id, str((body or {}).get("path", "")))

    @app.post("/v1/workspaces/pick")
    async def pick_workspace() -> dict[str, Any]:
        # Native folder picker opened by the LOCAL sidecar (browser GUIs can't get absolute
        # paths from web file dialogs). Off the event loop: blocks until pick/cancel.
        return await asyncio.to_thread(manager.pick_native_folder)

    @app.get("/v1/sessions")
    def sessions(workspace: str | None = None) -> dict[str, Any]:
        return {"sessions": manager.list_sessions(workspace)}

    @app.get("/v1/sessions/{session_id}/messages")
    def session_messages(session_id: str) -> dict[str, Any]:
        return {"messages": manager.session_messages(session_id)}

    @app.patch("/v1/sessions/{session_id}")
    def session_patch(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        if "pinned" in body or "archived" in body:
            return manager.set_session_flags(
                session_id,
                pinned=bool(body["pinned"]) if "pinned" in body else None,
                archived=bool(body["archived"]) if "archived" in body else None,
            )
        return manager.rename_session(session_id, str(body.get("title", "")))

    @app.delete("/v1/sessions/{session_id}")
    def session_delete(session_id: str) -> dict[str, Any]:
        return manager.delete_session(session_id)

    @app.get("/v1/sessions/{session_id}/roots")
    def session_roots(session_id: str) -> dict[str, Any]:
        return {"roots": manager.get_roots(session_id)}

    @app.post("/v1/sessions/{session_id}/roots")
    def session_add_root(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.add_root(
            session_id, str(body.get("path", "")), bool(body.get("writable", False))
        )

    @app.delete("/v1/sessions/{session_id}/roots")
    def session_remove_root(session_id: str, path: str) -> dict[str, Any]:
        return manager.remove_root(session_id, path)

    @app.get("/v1/sessions/{session_id}/artifacts")
    def session_artifacts(session_id: str) -> dict[str, Any]:
        return {"artifacts": manager.list_artifacts(session_id)}

    @app.get("/v1/sessions/{session_id}/artifacts/read")
    def session_artifact_read(session_id: str, path: str) -> dict[str, Any]:
        return manager.read_artifact(session_id, path)

    @app.post("/v1/sessions/{session_id}/artifacts/reveal")
    def session_artifact_reveal(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.reveal_artifact(
            session_id, str(body.get("path", "")), str(body.get("mode", "reveal"))
        )

    # Turn checkpoints ("time travel", coworker/checkpoints.py): the transcript's
    # per-turn file undo. Refused while a turn is running (manager + tracker both
    # guard); the surface posts a transcript notice on success.
    @app.get("/v1/sessions/{session_id}/checkpoints")
    def session_checkpoints(session_id: str) -> dict[str, Any]:
        return manager.list_checkpoints(session_id)

    @app.post("/v1/sessions/{session_id}/checkpoints/{seq}/rollback")
    def session_checkpoint_rollback(session_id: str, seq: int) -> dict[str, Any]:
        return manager.rollback_checkpoint(session_id, seq)

    # Observability (coworker/observability.py): span timeline + manual OTLP export
    # (double opt-in — the endpoint must be configured AND the export called).
    @app.get("/v1/sessions/{session_id}/timeline")
    def session_timeline(session_id: str, limit: int = 500) -> dict[str, Any]:
        return {"spans": manager.session_timeline(session_id, limit=limit)}

    # Turn ledger (coworker/trajectory.py): append-only, redacted metadata events.
    @app.get("/v1/sessions/{session_id}/trajectory")
    def session_trajectory(
        session_id: str, limit: int = 500, since_seq: int = 0
    ) -> dict[str, Any]:
        return manager.session_trajectory(session_id, limit=limit, since_seq=since_seq)

    # Session fork / replay（建议路线 P0-2）：turn 边界复制会话、后台重跑输入、轨迹对比。
    # 错误按 manager 返回的 status/detail 映射 HTTP 状态码（400/403/404/409）。
    def _fork_error(result: dict[str, Any]) -> JSONResponse:
        return JSONResponse(
            {"detail": result.get("detail", "fork/replay failed")},
            status_code=int(result.get("status") or 400),
        )

    @app.post("/v1/sessions/{session_id}/fork")
    def session_fork(session_id: str, body: Optional[dict] = None) -> Any:
        body = body or {}
        turn = body.get("turn")
        result = manager.fork_session(
            session_id, turn=int(turn) if turn is not None else None
        )
        if not result.get("ok"):
            return _fork_error(result)
        return result

    @app.post("/v1/sessions/{session_id}/replay")
    async def session_replay(session_id: str, body: Optional[dict] = None) -> Any:
        body = body or {}
        replay_input = body.get("input")
        result = await manager.replay_session(
            session_id,
            input=str(replay_input) if replay_input is not None else None,
        )
        if not result.get("ok"):
            return _fork_error(result)
        return JSONResponse(result, status_code=202)

    @app.get("/v1/sessions/{session_id}/trajectory/diff")
    def session_trajectory_diff(
        session_id: str,
        other: str,
        from_turn: int = 1,
        other_from_turn: int = 1,
    ) -> dict[str, Any]:
        return manager.trajectory_diff(
            session_id,
            other,
            from_turn=from_turn,
            other_from_turn=other_from_turn,
        )

    @app.post("/v1/observability/export")
    def observability_export(body: dict) -> dict[str, Any]:
        return manager.export_trace_otlp(str((body or {}).get("session_id", "")))

    # Agent teams (OPE-96): the session's board (workspace-keyed space) + journal
    # overview. Mutations act as the USER — the human side of the gates.
    @app.get("/v1/sessions/{session_id}/board")
    def session_board(session_id: str) -> dict[str, Any]:
        return manager.session_board(session_id)

    @app.get("/v1/sessions/{session_id}/board/item")
    def session_board_item(session_id: str, id: int) -> dict[str, Any]:
        return manager.board_item_detail(session_id, int(id))

    @app.get("/v1/sessions/{session_id}/board/attachment")
    def session_board_attachment(session_id: str, name: str):
        from fastapi.responses import Response

        try:
            path = manager.attachment_store.path_for(name)
        except TeamsBoardError as error:
            return JSONResponse({"error": str(error)}, status_code=404)
        return Response(
            content=path.read_bytes(),
            media_type=manager.attachment_store.mime_for(name),
        )

    @app.post("/v1/sessions/{session_id}/board/comment")
    def session_board_comment(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.board_comment(
            session_id, int(body.get("item", 0)), str(body.get("body", ""))
        )

    @app.post("/v1/sessions/{session_id}/board/transition")
    def session_board_transition(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.board_transition(
            session_id,
            int(body.get("item", 0)),
            str(body.get("to", "")),
            comment=str(body.get("comment", "")),
        )

    @app.get("/v1/teams/{team_id}/chat")
    def team_chat(team_id: str) -> dict[str, Any]:
        return manager.team_chat(team_id)

    @app.post("/v1/teams/{team_id}/chat")
    def team_chat_post(team_id: str, body: dict) -> dict[str, Any]:
        return manager.post_team_chat(team_id, str((body or {}).get("text", "")))

    @app.get("/v1/teams/journal")
    def teams_journal() -> dict[str, Any]:
        return {"cases": manager.journal_overview()}

    # ---- The open board surface (OPE-100): token-authenticated `/v1/board` API.
    # Identity is the TOKEN (actor+role bound at mint, resolved per request, never
    # client-asserted); authority is the STORE — the same double gate in-app agents
    # get. This is the one wire protocol every external front door rides:
    # RemoteDialect (the `ocw` CLI, the team-board MCP server, headless instances)
    # today, a hosted board service later. Tokens are required even on loopback —
    # they carry identity, not just access.

    def _board_actor(request: Request):
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        return manager.board_tokens.resolve(token)

    def _board(request: Request, handler):
        actor = _board_actor(request)
        if actor is None:
            return JSONResponse(
                {"error": "board token required (Authorization: Bearer …) — mint"
                          " one with `ocw board token` on the serving machine"},
                status_code=401,
            )
        try:
            return handler(actor)
        except TeamsAuthorityError as error:
            return JSONResponse({"error": str(error)}, status_code=403)
        except (TeamsBoardError, ValueError) as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    @app.get("/v1/board/whoami")
    def board_whoami(request: Request):
        return _board(
            request, lambda actor: {"actor": actor.id, "role": actor.role.value}
        )

    @app.get("/v1/board/spaces")
    def board_spaces(request: Request):
        return _board(request, lambda actor: {"spaces": manager.team_store.spaces()})

    @app.get("/v1/board/items")
    def board_list_items(
        request: Request, space: str, state: str = "", assignee: str = ""
    ):
        return _board(
            request,
            lambda actor: {
                "items": manager.team_store.list_items(
                    space, actor, state=state or None, assignee=assignee or None
                )
            },
        )

    @app.get("/v1/board/item")
    def board_get_item(request: Request, space: str, id: int):
        return _board(
            request, lambda actor: manager.team_store.get_item(space, int(id))
        )

    @app.post("/v1/board/items")
    def board_create_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            item = manager.team_store.create_item(
                str(body.get("space", "")),
                actor,
                title=str(body.get("title", "")),
                criteria=str(body.get("criteria", "")),
                description=str(body.get("description", "")),
                parent=(
                    int(body["parent"]) if body.get("parent") is not None else None
                ),
                case=str(body.get("case") or "") or None,
            )
            manager.kick_team_tick()  # a new filing is lead-subscription news
            return item

        return _board(request, run)

    @app.post("/v1/board/items/transition")
    def board_transition_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            item = manager.team_store.transition(
                str(body.get("space", "")),
                actor,
                int(body.get("id", 0)),
                str(body.get("to", "")),
                comment=str(body.get("comment", "")),
                refs=[str(ref) for ref in body.get("refs") or []],
            )
            manager.kick_team_tick()  # review/blocked should reach the lead now
            return item

        return _board(request, run)

    @app.post("/v1/board/items/comment")
    def board_comment_item(request: Request, body: dict):
        body = body or {}
        return _board(
            request,
            lambda actor: manager.team_store.comment(
                str(body.get("space", "")),
                actor,
                int(body.get("id", 0)),
                str(body.get("body", "")),
                refs=[str(ref) for ref in body.get("refs") or []],
            ),
        )

    @app.post("/v1/board/items/assign")
    def board_assign_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            item = manager.team_store.assign(
                str(body.get("space", "")),
                actor,
                int(body.get("id", 0)),
                str(body.get("assignee", "")),
            )
            manager.kick_team_tick()  # the assignee's queue has news
            return item

        return _board(request, run)

    @app.post("/v1/board/items/claim")
    def board_claim_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            item = manager.team_store.claim(
                str(body.get("space", "")), actor, int(body.get("id", 0))
            )
            manager.kick_team_tick()  # claims land in the lead's feed
            return item

        return _board(request, run)

    @app.post("/v1/board/link")
    def board_link_items(request: Request, body: dict):
        body = body or {}
        return _board(
            request,
            lambda actor: manager.team_store.link(
                str(body.get("space", "")),
                actor,
                int(body.get("src", 0)),
                str(body.get("kind", "")),
                int(body.get("dst", 0)),
            ),
        )

    @app.post("/v1/board/items/attach")
    def board_attach(request: Request, body: dict):
        body = body or {}

        def run(actor):
            raw = str(body.get("data_b64", ""))
            # Cheap pre-decode bound: base64 is ~4/3 of the payload, so anything
            # multiples over the cap is refused before allocating the decode.
            if len(raw) > 15 * 1024 * 1024:
                return JSONResponse(
                    {"error": "attachment exceeds 10MB"}, status_code=400
                )
            try:
                data = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                return JSONResponse(
                    {"error": "data_b64 is not valid base64"}, status_code=400
                )
            ref = manager.attachment_store.put(
                data, str(body.get("filename", ""))
            )
            filename = str(body.get("filename", ""))
            event = manager.team_store.comment(
                str(body.get("space", "")),
                actor,
                int(body.get("id", 0)),
                str(body.get("caption", "")) or f"attached {filename}",
                refs=[ref],
            )
            return {"ref": ref, "seq": event["seq"]}

        return _board(request, run)

    @app.get("/v1/board/attachment")
    def board_attachment(request: Request, name: str):
        def run(actor):
            from fastapi.responses import Response

            path = manager.attachment_store.path_for(name)
            return Response(
                content=path.read_bytes(),
                media_type=manager.attachment_store.mime_for(name),
            )

        return _board(request, run)

    @app.get("/v1/board/policy")
    def board_get_policy(request: Request, space: str):
        return _board(request, lambda actor: manager.team_store.policy(space))

    @app.post("/v1/board/policy")
    def board_set_policy(request: Request, body: dict):
        body = body or {}
        return _board(
            request,
            lambda actor: manager.team_store.set_policy(
                str(body.get("space", "")), actor, claims=str(body.get("claims", ""))
            ),
        )

    @app.get("/v1/board/pending")
    def board_pending(request: Request, space: str, limit: int = 200):
        # The actor's FEED: events on its slice since its cursor — interest
        # follows the assignment relation, same projection in-app workers use.
        return _board(
            request,
            lambda actor: {
                "events": manager.team_store.feed_for(
                    space, actor.id, limit=int(limit)
                )
            },
        )

    @app.post("/v1/board/consume")
    def board_consume(request: Request, body: dict):
        body = body or {}

        def run(actor):
            manager.team_store.consume_feed(
                str(body.get("space", "")), actor.id, int(body.get("upto_seq", 0))
            )
            return {"ok": True}

        return _board(request, run)

    @app.get("/v1/board/journal/cases")
    def board_journal_cases(request: Request):
        return _board(
            request, lambda actor: {"cases": manager.journal_store.overview(actor)}
        )

    @app.get("/v1/board/journal")
    def board_journal_read(
        request: Request,
        case: str,
        item: Optional[int] = None,
        author: str = "",
        kind: str = "",
        entity: str = "",
        include_raw: str = "",
        limit: int = 100,
    ):
        return _board(
            request,
            lambda actor: {
                "entries": manager.journal_store.read(
                    actor,
                    case,
                    item=item,
                    author=author or None,
                    kind=kind or None,
                    entity=entity or None,
                    include_raw=bool(include_raw),
                    limit=int(limit),
                )
            },
        )

    @app.post("/v1/board/journal")
    def board_journal_append(request: Request, body: dict):
        body = body or {}
        return _board(
            request,
            lambda actor: manager.journal_store.append(
                actor,
                str(body.get("case", "")),
                str(body.get("body", "")),
                kind=str(body.get("kind") or "note"),
                space=str(body.get("space") or "") or None,
                item=int(body["item"]) if body.get("item") is not None else None,
                entities=[str(e) for e in body.get("entities") or []],
                refs=[str(ref) for ref in body.get("refs") or []],
            ),
        )

    # -- memory -----------------------------------------------------------------
    app.include_router(build_memory_router(manager))

    # -- security posture (threat model, findings, egress, sandbox) --------------
    app.include_router(build_security_router(manager))

    # -- project bindings (pass 20 / UX-044) --------------------------------------

    @app.get("/v1/sessions/{session_id}/project-menu")
    def project_menu(session_id: str, kind: str = "memory") -> dict[str, Any]:
        return manager.project_menu(session_id, kind)

    @app.put("/v1/sessions/{session_id}/bindings")
    def put_binding(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        name = body.get("name")
        return manager.set_binding(
            session_id, str(body.get("kind", "")), str(name) if name else None
        )

    @app.post("/v1/sessions/{session_id}/project-name")
    def name_project(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.name_current_project(
            session_id, str(body.get("kind", "")), str(body.get("name", ""))
        )

    @app.post("/v1/chat/completions")
    def chat_completions(body: dict) -> dict[str, Any]:
        model = body.get("model", manager.model)
        turn = manager.provider_complete(
            model, body.get("messages", []), body.get("tools")
        )
        return _openai_response(model, turn)

    # -- MCP servers ------------------------------------------------------------
    app.include_router(build_mcp_router(manager))

    # -- connectors (Slack / Telegram / …) --------------------------------------
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

    @app.websocket("/ws/session/{session_id}")
    async def ws_session(ws: WebSocket, session_id: str) -> None:
        if not _websocket_authenticated(ws):
            await ws.close(code=1008)
            return
        # CORS never gates WebSockets, so a cross-site page could otherwise open this socket
        # and drive the session into tool calls. Reject a disallowed browser Origin before
        # accepting the handshake (1008 = policy violation).
        if not _origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol="openworker" if api_token else None)
        agent = ws.query_params.get("agent") or "code"

        # All four interactive prompts (approval / question / directory / plan) are parked as Inbox
        # items and awaited via inbox.wait — so they survive a dropped socket (redelivered on
        # reconnect) and can be resolved from any surface. `visibility` decides where they SHOW:
        # Unattended → the cross-session Inbox; attended → inline in this session only. The agent
        # stays blocked until the item is resolved (live WS response, REST, or a bound channel).
        def _visibility() -> str:
            return (
                VIS_INBOX
                if manager.unattended.is_unattended(session_id)
                else VIS_INLINE
            )

        async def _mirror(item) -> None:
            # Unattended items mirror to a bound channel as buttons (see mirror_inbox_item).
            await manager.mirror_inbox_item(item)

        def _route() -> str:
            return manager.inbox_routing.route_for(session_id, agent)

        async def approver(_request) -> ApprovalOutcome:
            # The engine has already emitted PERMISSION_REQUIRED (the live inline card). Park the
            # item so the answer can also come from the Inbox / a reconnect / after a restart.
            item = manager.inbox.add_approval(
                session_id,
                f"Run `{_request.tool_name}`?",
                body="\n".join(
                    p
                    for p in (
                        (getattr(_request, "reason", "") or "").strip(),
                        args_preview(getattr(_request, "arguments", None)),
                    )
                    if p
                ),
                inbox=_route(),
                visibility=_visibility(),
                # Automation-run context (manual "Run now" rides this socket): lets the
                # card offer the task-persistent "Allow every time" (§25). {} elsewhere.
                data=manager.approval_prompt_data(session_id, _request),
                tool_call_id=getattr(_request, "tool_call_id", None),
            )
            if (
                item.state == "pending"
            ):  # freshly raised (not a durable-resume re-raise)
                manager.persist_session(
                    session_id
                )  # the pending tool call is now on disk
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resolution = await manager.inbox.wait(item.id)
            # Accept every vocabulary: the live card sends once/always_tool/always_command/
            # always_task/deny; the Inbox / a channel send allow/always/deny.
            return manager.approval_outcome(resolution, _request, session_id)

        async def question_asker(args: dict, tool_call_id=None) -> dict:
            # ask_user (engine does NOT emit the event — we do, only when attended).
            from ..tools.ask import answer_result, question_item_fields

            fields = question_item_fields(args)
            if fields is None:  # engine guards too; belt-and-braces
                return {"answer": "", "error": "no question"}
            item = manager.inbox.add_question(
                session_id,
                inbox=_route(),
                visibility=_visibility(),
                tool_call_id=tool_call_id,
                **fields,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
                else:
                    await ws.send_json(
                        {
                            "type": "question_requested",
                            "data": {
                                "question": item.title,
                                "options": item.options,
                                "allow_text": item.allow_text,
                                "multi": item.multi,
                                "header": item.header,
                                "questions": item.questions,
                            },
                        }
                    )
            return answer_result(item.questions, await manager.inbox.wait(item.id))

        async def tool_requester(args: dict, tool_call_id=None) -> dict:
            """Park a TOOL_REQUESTED prompt, then install the PINNED build if approved.

            Declining is a first-class outcome: the agent is told to fall back and disclose
            the gap rather than drop the check (OPE-85). Installs only ever come from the
            pinned registry with its digest verified — an approval is consent to install
            THAT artifact, not licence to fetch whatever a prompt asked for.
            """
            name = str(args.get("name", "")).strip()
            info = toolchain.describe(name)
            if not info:
                # Not in the pinned catalog: never show an install card that can only
                # end in "no pinned build" AFTER approval (owner-hit 2026-08-20 — agents
                # routed ordinary brew/pip installs through the card). Steer to the
                # shell, which has its own approval flow.
                return {
                    "installed": False,
                    "error": (
                        f"'{name}' is not in the pinned tool catalog "
                        f"({', '.join(sorted(toolchain.MANAGED))}). Install it yourself "
                        "with the shell (brew/pip/…, subject to the normal command "
                        "approval), or proceed without it and note the gap."
                    ),
                }
            item = manager.inbox.add_tool_request(
                session_id,
                f"Install {name}?" if name else "Install a tool?",
                body=str(args.get("reason", "")),
                inbox=_route(),
                visibility=_visibility(),
                data={
                    "tool": name,
                    "installable": bool(info and info.get("installable")),
                    "version": (info or {}).get("version", ""),
                    "summary": (info or {}).get("summary", ""),
                    "url": (info or {}).get("url", ""),
                    "source": (info or {}).get("source", ""),
                },
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(await manager.inbox.wait(item.id))  # {approved}
            if not resp.get("approved"):
                return {
                    "installed": False,
                    "reason": "the user declined to install it",
                }
            if not info or not info.get("installable"):
                return {
                    "installed": False,
                    "error": f"no pinned build of {name} is available for this platform",
                }
            try:
                path = await asyncio.to_thread(toolchain.install, name)
            except Exception as exc:  # noqa: BLE001 - surfaced to the agent verbatim
                return {"installed": False, "error": str(exc)}
            return {"installed": True, "path": path, "version": info["version"]}

        async def directory_requester(args: dict, tool_call_id=None) -> dict:
            # The engine has already emitted DIRECTORY_REQUESTED. Park, await, then apply the grant.
            item = manager.inbox.add_directory(
                session_id,
                "Grant access to a folder?",
                body=str(args.get("reason", "")),
                inbox=_route(),
                visibility=_visibility(),
                data={
                    "path": str(args.get("path", "")),
                    "writable": bool(args.get("writable", False)),
                    "primary": bool(args.get("primary", False)),
                },
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(
                await manager.inbox.wait(item.id)
            )  # {granted, path, writable}
            if not resp.get("granted"):
                return {"granted": False, "reason": "the user declined the request"}
            path = (resp.get("path") or args.get("path") or "").strip()
            if not path:
                return {"granted": False, "error": "no directory was provided"}
            writable = bool(resp.get("writable", args.get("writable", False)))
            if bool(args.get("primary", False)):
                # Root promotion (workspace-scratch-design.md §5) — the shell cd inside
                # is blocking, keep it off the event loop.
                promo = await asyncio.to_thread(
                    manager.promote_workspace, session_id, path
                )
                if promo.get("ok"):
                    return {
                        "granted": True,
                        "path": promo["path"],
                        "writable": True,
                        "primary": True,
                        "note": (
                            "This folder is now the session's workspace. For the rest "
                            "of this turn, address it by absolute path."
                        ),
                    }
                # Promotion refused (e.g. the session already has a workspace): still
                # honor the grant as a plain additional folder.
                res = manager.add_root(session_id, path, writable)
                if not res.get("ok"):
                    return {
                        "granted": False,
                        "error": promo.get("error", "could not promote"),
                    }
                return {
                    "granted": True,
                    "path": path,
                    "writable": writable,
                    "primary": False,
                    "note": promo.get("error", "")
                    + " — granted as an additional folder instead",
                }
            res = manager.add_root(session_id, path, writable)
            if not res.get("ok"):
                return {
                    "granted": False,
                    "error": res.get("error", "could not grant access"),
                }
            primary = next(
                (
                    r
                    for r in res.get("roots", [])
                    if r.get("path")
                    and Path(r["path"]).expanduser().resolve()
                    == Path(path).expanduser().resolve()
                ),
                None,
            )
            return {
                "granted": True,
                "path": (primary or {}).get("path", path),
                "writable": writable,
            }

        async def plan_approver(_args: dict, tool_call_id=None) -> dict:
            # The engine has already emitted PLAN_PROPOSED. Park, await the verdict.
            item = manager.inbox.add_plan(
                session_id,
                "Approve the plan?",
                body=str(_args.get("plan", "")),
                inbox=_route(),
                visibility=_visibility(),
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(
                await manager.inbox.wait(item.id)
            )  # {approved, mode, feedback}
            if not resp.get("approved"):
                return {
                    "approved": False,
                    "feedback": resp.get("feedback") or "the user rejected the plan",
                }
            return {"approved": True, "mode": resp.get("mode") or "interactive"}

        async def team_approver(_args: dict, tool_call_id=None) -> dict:
            # The staffing gate. The engine already emitted TEAM_PROPOSED; park an
            # Inbox item as the durable resolution vehicle, wait for the verdict, and
            # on approval PRE-SPAWN the team (create_team fails closed on non-worker
            # personas, so a bad roster reads as a rejection with the reason).
            members = _args.get("members") or []
            roster = "\n".join(
                f"- {m.get('persona', '?')}"
                + (f" · {m['model']}" if m.get("model") else "")
                + (f" — {m['reason']}" if m.get("reason") else "")
                for m in members
                if isinstance(m, dict)
            )
            item = manager.inbox.add_plan(
                session_id,
                "Create this team?",
                body=roster,
                inbox=_route(),
                visibility=_visibility(),
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(await manager.inbox.wait(item.id))
            if not resp.get("approved"):
                return {
                    "approved": False,
                    "feedback": resp.get("feedback") or "the user declined this roster",
                }
            # The gate checkbox is the USER's call: an explicit enable_chat in the
            # response overrides whatever the lead proposed.
            enable_chat = bool(
                resp["enable_chat"]
                if "enable_chat" in resp
                else _args.get("enable_chat", False)
            )
            return manager.create_team(
                session_id,
                [m for m in members if isinstance(m, dict)],
                enable_chat=enable_chat,
            )

        async def items_approver(_args: dict, tool_call_id=None) -> dict:
            # The decomposition gate. TEAMS-flavored sibling of plan_approver:
            # park a durable Inbox item, wait, and on approval create the items.
            items = _args.get("items") or []
            body = "\n".join(
                f"- {i.get('title', '?')} — Done when: {i.get('criteria', '?')}"
                for i in items
                if isinstance(i, dict)
            )
            item = manager.inbox.add_plan(
                session_id,
                "Approve the proposed work items?",
                body=body,
                inbox=_route(),
                visibility=_visibility(),
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(await manager.inbox.wait(item.id))
            if not resp.get("approved"):
                return {
                    "approved": False,
                    "feedback": resp.get("feedback") or "the user declined the split",
                }
            return manager.board_create_items(
                session_id, [i for i in items if isinstance(i, dict)]
            )

        async def _apply_model(model: Optional[str]) -> None:
            # Mid-session rebind is allowed (roadmap item 3, supersedes the 2026-07-04
            # lock): history is canonical and providers convert per call. A real switch
            # appends a persisted notice; broadcast it so live views render the marker
            # and update their header. Never rebind mid-turn — the running loop reads
            # `engine.model` per iteration and a mixed turn is exactly the breakage the
            # old lock existed to prevent.
            if not model or manager.is_running(session_id):
                return
            notice = engine.switch_model(model)
            if notice is None:  # same model, or first bind on a fresh session
                return
            manager.persist_session(session_id)
            await manager.broadcast_session(
                session_id,
                {"type": "model_changed", "data": {"model": model, "text": notice}},
            )

        def _resolve_pending(resolution: str) -> None:
            # Live WS responses resolve THE session's single pending prompt (one at a time, since the
            # agent blocks). Reconnect / Inbox resolve by id via REST instead.
            pend = manager.inbox.pending(session_id)
            if pend:
                manager.inbox.resolve(pend[0].id, resolution)

        workspace = ws.query_params.get("workspace")
        mcp_tools = await manager.prepare_mcp_tools(
            session_id, workspace=workspace, agent=agent
        )
        engine = manager.get_engine(
            session_id,
            workspace=workspace,
            agent=agent,
            approver=approver,
            extra_tools=mcp_tools,
            directory_requester=directory_requester,
            plan_approver=plan_approver,
            question_asker=question_asker,
            tool_requester=tool_requester,
            team_approver=team_approver,
            items_approver=items_approver,
        )
        if engine is None:
            await ws.send_json(
                {
                    "type": "error",
                    "data": {
                        "error": "no valid workspace — choose a project folder first"
                    },
                }
            )
            await ws.close()
            return
        # MCP servers that failed to start while preparing this session's tools:
        # leave a quiet, persistent notice instead of the session silently lacking
        # them (drill 2026-08-20: three silent startup failures in a row).
        for name, err in manager.pop_mcp_failures(session_id):
            detail = f": {err}" if err else ""
            # `server` makes the notice structured: the GUI renders one quiet line
            # with the full error behind a disclosure + an Open-Connectors action
            # (owner ruling 2026-08-21) instead of a wall of stderr.
            engine._append_notice(
                "mcp_error",
                f"MCP server “{name}” failed to start{detail}"[:500],
                server=name,
            )
        # Auto-compaction failure prompt (OPE-27): only an ATTENDED session may be asked
        # Retry/Trim — unattended runs auto-trim (the policy in engine._compact_now).
        engine.is_attended = lambda: _visibility() == VIS_INLINE
        await ws.send_json(
            {
                "type": "ready",
                "data": {
                    "session_id": session_id,
                    # A reconnect can land MID-TURN (sidebar revisit, app relaunch, WS
                    # drop). Without server truth the GUI never learns a turn is live —
                    # no Stop button, no waiting row (owner catch 2026-08-24).
                    "running": manager.is_running(session_id),
                    "agent": getattr(engine, "agent_name", "code"),
                    "model": engine.model,
                    "mode": engine.permissions.mode.value,
                    "workspace": (
                        str(getattr(engine, "executor").cwd)
                        if getattr(engine, "executor", None)
                        else None
                    ),
                    # UX-029: the GUI never shows a temporary folder's raw path — this flag
                    # is how it knows to say "Temporary folder" (and offer Save as project).
                    "temp_workspace": manager.is_temp_workspace(
                        str(getattr(engine, "executor").cwd)
                        if getattr(engine, "executor", None)
                        else None
                    ),
                    "command_trust": manager.workspace_command_trust(
                        str(getattr(engine, "audit_context", {}).get("workspace", ""))
                    ),
                },
            }
        )

        # Checkpoint events: persist mid-turn so a crash/quit can't eat the conversation.
        # turn_start = the user message just landed (a brand-new session gets its row here,
        # not at connect — empty never-used sessions shouldn't appear in Recents);
        # permission_required/directory_requested = parked indefinitely on the user;
        # iteration_end = a model response + its tool results completed.
        _CHECKPOINTS = {
            "turn_start",
            "permission_required",
            "directory_requested",
            "plan_proposed",
            "iteration_end",
        }

        async def run_turn(content, *, retry: bool = False, display=None) -> None:
            # The receive loop atomically claims this session before scheduling the task.
            # Keeping the claim outside prevents two back-to-back frames from both starting.
            try:
                events = (
                    engine.retry()
                    if retry
                    else engine.run(content, display=display)
                )
                async for event in events:
                    # Broadcast to every socket viewing this session (this socket included — it's a
                    # registered client), so a second view of the same session stays in sync too.
                    await manager.broadcast_session(
                        session_id, {"type": event.type.value, "data": event.data}
                    )
                    if event.type.value in _CHECKPOINTS:
                        manager.save(session_id, engine)
                    if event.type.value == "turn_start":
                        # Title on the user's words the moment they land — never behind
                        # a long agentic turn (owner catch 2026-08-24).
                        manager._maybe_autotitle(session_id)
            finally:
                manager.mark_idle(session_id)
                manager.save(session_id, engine)
                await manager.broadcast_session(
                    session_id, {"type": "turn_done", "data": {}}
                )

        # This socket is now a live view of the session; background turns (channel delivery,
        # self-wake, durable resume) broadcast here too, not just locally driven run_turns.
        manager.register_session_client(session_id, ws.send_json)
        if engine.permissions.mode is Mode.AUTO_APPROVE and not any(
            m.get("kind") == "mode_notice" for m in engine.messages
        ):
            from coworker.permissions import AUTO_APPROVE_NOTICE

            engine._append_notice(
                "mode_notice", AUTO_APPROVE_NOTICE, title="Auto-approve is on."
            )
            manager.save(session_id, engine, touch=False)  # migration ≠ activity
            await ws.send_json(
                {
                    "type": "mode_notice",
                    "data": {
                        "title": "Auto-approve is on.",
                        "text": AUTO_APPROVE_NOTICE,
                    },
                }
            )
        inbound_times: deque[float] = deque()

        async def reject_input(reason: str) -> None:
            # Input validation failures are not provider failures and must not offer "Retry"
            # or flush an in-progress assistant stream in the GUI.
            await ws.send_json({"type": "input_rejected", "data": {"error": reason}})

        async def claim_turn(*, retry: bool = False, content=None, display=None) -> None:
            if not manager.try_mark_running(session_id):
                await reject_input(
                    "This session is already running a turn. Wait for it to finish or stop it."
                )
                return
            asyncio.create_task(run_turn(content, retry=retry, display=display))

        try:
            while True:
                try:
                    message = await ws.receive_json()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    await reject_input("Invalid WebSocket message: expected JSON.")
                    continue

                now = asyncio.get_running_loop().time()
                while (
                    inbound_times
                    and now - inbound_times[0] > _WS_RATE_LIMIT_WINDOW_SECONDS
                ):
                    inbound_times.popleft()
                if len(inbound_times) >= _WS_RATE_LIMIT_COUNT:
                    await reject_input("Too many WebSocket messages; reconnect and try again.")
                    await ws.close(code=1008)
                    return
                inbound_times.append(now)

                if not isinstance(message, dict):
                    await reject_input("Invalid WebSocket message: expected an object.")
                    continue
                kind = message.get("type")
                if not isinstance(kind, str):
                    await reject_input("Invalid WebSocket message: missing string type.")
                    continue
                if kind == "approval":
                    _resolve_pending(message.get("decision", "deny"))
                elif kind == "directory_response":
                    _resolve_pending(
                        json.dumps(
                            {
                                "granted": bool(message.get("granted")),
                                "path": message.get("path", ""),
                                "writable": bool(message.get("writable", False)),
                            }
                        )
                    )
                elif kind == "tool_response":
                    _resolve_pending(
                        json.dumps({"approved": bool(message.get("approved"))})
                    )
                elif kind == "plan_response":
                    _resolve_pending(
                        json.dumps(
                            {
                                "approved": bool(message.get("approved")),
                                "mode": message.get("mode", "interactive"),
                                "feedback": message.get("feedback", ""),
                            }
                        )
                    )
                elif kind in ("team_response", "items_response"):
                    _resolve_pending(
                        json.dumps(
                            {
                                "approved": bool(message.get("approved")),
                                "feedback": message.get("feedback", ""),
                                **(
                                    {"enable_chat": bool(message.get("enable_chat"))}
                                    if "enable_chat" in message
                                    else {}
                                ),
                            }
                        )
                    )
                elif kind == "question_response":
                    _resolve_pending(str(message.get("answer", "")))
                elif kind == "allow_anyway":
                    # §8.4: the user clicked "Allow anyway" on a reviewer-denied tool card.
                    # Registers a ONE-SHOT exact-action approval on the engine; the GUI then
                    # sends its canned retry message through the normal user_message path,
                    # and the re-proposed identical action runs without the reviewer/card.
                    name = message.get("name")
                    arguments = message.get("arguments")
                    if not isinstance(name, str) or not name:
                        await reject_input("Invalid allow_anyway: missing tool name.")
                    elif arguments is not None and not isinstance(arguments, dict):
                        await reject_input("Invalid allow_anyway: arguments must be an object.")
                    else:
                        engine.approve_action_once(name, arguments or {})
                elif kind == "interrupt":
                    engine.request_interrupt()
                elif kind == "retry":
                    # Re-run after a provider error (engine guards on the error-notice
                    # tail, so a stray frame is a no-op that still ends with turn_done).
                    await claim_turn(retry=True)
                elif kind == "set_mode":
                    try:
                        new_mode = Mode(message.get("mode"))
                    except (TypeError, ValueError):
                        pass
                    else:
                        previous = engine.permissions.mode
                        engine.permissions.mode = new_mode
                        if previous is not new_mode:
                            manager.audit_autonomy_change(
                                session_id, "mode", previous.value, new_mode.value
                            )
                            # The transcript records which mode each exchange ran under
                            # (owner ruling 2026-08-24): full explainer the first time a
                            # session enters Auto-Approve, a one-line marker otherwise.
                            # Server-authored + persisted, so reloads show it in place
                            # exactly once instead of re-announcing on every restart.
                            from coworker.permissions import (
                                AUTO_APPROVE_NOTICE,
                                MODE_LABELS,
                            )

                            if new_mode is Mode.AUTO_APPROVE and not any(
                                m.get("kind") == "mode_notice"
                                for m in engine.messages
                            ):
                                engine._append_notice(
                                    "mode_notice",
                                    AUTO_APPROVE_NOTICE,
                                    title="Auto-approve is on.",
                                )
                                notice_data = {
                                    "title": "Auto-approve is on.",
                                    "text": AUTO_APPROVE_NOTICE,
                                }
                            else:
                                label = MODE_LABELS.get(
                                    new_mode.value, new_mode.value
                                )
                                engine._append_notice(
                                    "mode_switch", f"{label} is on."
                                )
                                notice_data = {"text": f"{label} is on."}
                            # A mode switch with no accompanying message is bookkeeping,
                            # not activity (owner ruling 2026-08-24): the transcript
                            # records it, Recents doesn't reorder. The next real turn's
                            # checkpoint save bumps recency as usual.
                            manager.save(session_id, engine, touch=False)
                            await manager.broadcast_session(
                                session_id,
                                {"type": "mode_notice", "data": notice_data},
                            )
                elif kind == "set_model":
                    model = message.get("model")
                    if model is not None and not isinstance(model, str):
                        await reject_input("Invalid model: expected a string.")
                    else:
                        await _apply_model(model)
                elif kind == "user_message":
                    raw_text = message.get("text")
                    if raw_text is None:
                        raw_text = ""
                    if not isinstance(raw_text, str):
                        await reject_input("Invalid message text: expected a string.")
                        continue
                    text = raw_text.strip()
                    raw_attachments = message.get("attachments")
                    attachments = [] if raw_attachments is None else raw_attachments
                    # Reject an oversized frame instead of buffering it into a turn. Send a
                    # visible error so the surface can tell the user, and drop the message.
                    if not isinstance(attachments, list):
                        await reject_input("Invalid attachments: expected a list.")
                        continue
                    reject = None
                    if len(text) > _MAX_MESSAGE_TEXT_CHARS:
                        reject = (
                            f"Message too long ({len(text)} chars; "
                            f"limit {_MAX_MESSAGE_TEXT_CHARS})."
                        )
                    elif len(attachments) > _MAX_ATTACHMENTS:
                        reject = (
                            f"Too many attachments ({len(attachments)}; "
                            f"limit {_MAX_ATTACHMENTS})."
                        )
                    elif any(not isinstance(a, dict) for a in attachments):
                        reject = "Invalid attachment: expected an object."
                    elif _json_value_size(attachments) > _MAX_ATTACHMENTS_BYTES:
                        reject = "Attachments too large (limit 15 MB per message)."
                    else:
                        for attachment in attachments:
                            attachment_kind = attachment.get("kind")
                            name = attachment.get("name")
                            mime = attachment.get("mime")
                            if attachment_kind not in {"image", "pdf", "text"}:
                                reject = "Invalid attachment kind."
                            elif name is not None and (
                                not isinstance(name, str) or len(name) > 1024
                            ):
                                reject = "Invalid attachment name."
                            elif mime is not None and (
                                not isinstance(mime, str) or len(mime) > 255
                            ):
                                reject = "Invalid attachment MIME type."
                            elif attachment_kind == "image":
                                data = attachment.get("data_url")
                                if (
                                    not isinstance(data, str)
                                    or not data.startswith("data:image/")
                                    or ";base64," not in data
                                    or len(data) > MAX_IMAGE_CHARS
                                ):
                                    reject = "Invalid or oversized image attachment."
                            elif attachment_kind == "pdf":
                                data = attachment.get("data_url")
                                if (
                                    not isinstance(data, str)
                                    or not data.startswith(
                                        "data:application/pdf;base64,"
                                    )
                                    or len(data) > MAX_PDF_CHARS
                                ):
                                    reject = "Invalid or oversized PDF attachment."
                            else:
                                body = attachment.get("text")
                                if (
                                    not isinstance(body, str)
                                    or len(body) > MAX_TEXT_CHARS
                                ):
                                    reject = "Invalid or oversized text attachment."
                            if reject is not None:
                                break
                    if reject is not None:
                        await reject_input(reject)
                        continue
                    # Soft steering (P2-9): a text message sent while this session is
                    # running joins the live turn at its next model request instead of
                    # being rejected. Attachments/skills start a new turn, and a pending
                    # Inbox gate keeps its reply semantics (gate answers win).
                    if manager.is_running(
                        session_id
                    ) and not manager.inbox.pending(session_id):
                        if attachments:
                            await reject_input(
                                "Mid-turn steering supports text only — wait for this "
                                "turn to finish before sending attachments."
                            )
                            continue
                        if message.get("skill"):
                            await reject_input(
                                "Mid-turn steering supports text only — a skill starts "
                                "a new turn."
                            )
                            continue
                        if not text:
                            await reject_input("Nothing to steer — the message is empty.")
                            continue
                        if manager.steer_session(session_id, text):
                            await ws.send_json(
                                {"type": "steering_queued", "data": {"text": text}}
                            )
                            continue
                    # The composer sends its visible model with every message — the FIRST
                    # one binds the session (race-proof across reconnects; see api.ts
                    # Session.userMessage), later ones may switch it (notice persisted).
                    model = message.get("model")
                    if model is not None and not isinstance(model, str):
                        await reject_input("Invalid model: expected a string.")
                        continue
                    # Force-run (SKILLS-SPEC §4.1 #3): the composer's `/skill` pick rides as a
                    # separate field. Validated against the session's effective menu — a muted
                    # or unknown skill is a visible error, never a silent no-op (§4.6 #15).
                    # The model-facing framing goes into `content`; the transcript shows the
                    # user's literal "/name …" line via the `_display` sidecar (one bubble).
                    skill = message.get("skill")
                    display = None
                    if skill is not None:
                        if not isinstance(skill, str) or not skill.strip():
                            await reject_input("Invalid skill: expected a name.")
                            continue
                        skill = skill.strip()
                        menu = manager.effective_skill_names(session_id, workspace)
                        if skill not in menu:
                            await reject_input(
                                f"Skill '{skill}' is not available in this session."
                            )
                            continue
                        display = f"/{skill}" + (f" {text}" if text else "")
                        text = (
                            f'Use the skill "{skill}" for this request: first call '
                            f'load_skill("{skill}") and follow its instructions.'
                            + (f"\n\n{text}" if text else "")
                        )
                    await _apply_model(model)
                    if text or attachments:
                        content = build_user_content(text, attachments)
                        await claim_turn(content=content, display=display)
                else:
                    await reject_input(f"Unknown WebSocket message type: {kind}.")
        except WebSocketDisconnect:
            pass
        finally:
            manager.unregister_session_client(session_id, ws.send_json)

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        """App-wide event stream (session-independent): the GUI keeps one open for
        pushes like automation_run_started (the UX-026 toast). Read-only — inbound
        frames are ignored; the receive loop just detects disconnect."""
        if not _websocket_authenticated(ws):
            await ws.close(code=1008)
            return
        if not _origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol="openworker" if api_token else None)
        manager.register_event_client(ws.send_json)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            manager.unregister_event_client(ws.send_json)

    return app


def _parse_json(s: str) -> dict[str, Any]:
    """Parse a structured Inbox resolution (directory/plan carry their reply as a JSON string)."""
    try:
        v = json.loads(s) if s else {}
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _openai_response(model: str, turn: AssistantTurn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in turn.tool_calls
        ]
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": turn.finish_reason or "stop",
            }
        ],
    }
