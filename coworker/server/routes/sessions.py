"""Sessions — the transcript's REST surface, its files, and its time-travel tools.

List/rename/pin/archive/delete, the message log, multi-root scoping, artifacts (list, read
back through the sandbox guard, reveal in the OS file manager), turn checkpoints and their
rollback, the span timeline, the trajectory ledger, and fork/replay/diff — including the
project bindings (pass 20 / UX-044) that point a session at a memory/board project.

Two invariants worth naming because they live in the handlers' shape:

* fork/replay compute their HTTP status from the manager's `status` field (400/403/404/409)
  rather than raising — the caller is a UI that wants the reason.
* replay answers 202: it starts a background re-run and returns immediately.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..manager import SessionManager


def _fork_error(result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        {"detail": result.get("detail", "fork/replay failed")},
        status_code=int(result.get("status") or 400),
    )


def build_sessions_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/sessions")
    def sessions(workspace: str | None = None) -> dict[str, Any]:
        return {"sessions": manager.list_sessions(workspace)}

    @router.get("/v1/sessions/{session_id}/messages")
    def session_messages(session_id: str) -> dict[str, Any]:
        return {"messages": manager.session_messages(session_id)}

    @router.patch("/v1/sessions/{session_id}")
    def session_patch(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        if "pinned" in body or "archived" in body:
            return manager.set_session_flags(
                session_id,
                pinned=bool(body["pinned"]) if "pinned" in body else None,
                archived=bool(body["archived"]) if "archived" in body else None,
            )
        return manager.rename_session(session_id, str(body.get("title", "")))

    @router.delete("/v1/sessions/{session_id}")
    def session_delete(session_id: str) -> dict[str, Any]:
        return manager.delete_session(session_id)

    @router.get("/v1/sessions/{session_id}/roots")
    def session_roots(session_id: str) -> dict[str, Any]:
        return {"roots": manager.get_roots(session_id)}

    @router.post("/v1/sessions/{session_id}/roots")
    def session_add_root(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.add_root(
            session_id, str(body.get("path", "")), bool(body.get("writable", False))
        )

    @router.delete("/v1/sessions/{session_id}/roots")
    def session_remove_root(session_id: str, path: str) -> dict[str, Any]:
        return manager.remove_root(session_id, path)

    @router.get("/v1/sessions/{session_id}/artifacts")
    def session_artifacts(session_id: str) -> dict[str, Any]:
        return {"artifacts": manager.list_artifacts(session_id)}

    @router.get("/v1/sessions/{session_id}/artifacts/read")
    def session_artifact_read(session_id: str, path: str) -> dict[str, Any]:
        return manager.read_artifact(session_id, path)

    @router.post("/v1/sessions/{session_id}/artifacts/reveal")
    def session_artifact_reveal(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.reveal_artifact(
            session_id, str(body.get("path", "")), str(body.get("mode", "reveal"))
        )

    # Turn checkpoints ("time travel", coworker/checkpoints.py): the transcript's
    # per-turn file undo. Refused while a turn is running (manager + tracker both
    # guard); the surface posts a transcript notice on success.
    @router.get("/v1/sessions/{session_id}/checkpoints")
    def session_checkpoints(session_id: str) -> dict[str, Any]:
        return manager.list_checkpoints(session_id)

    @router.post("/v1/sessions/{session_id}/checkpoints/{seq}/rollback")
    def session_checkpoint_rollback(session_id: str, seq: int) -> dict[str, Any]:
        return manager.rollback_checkpoint(session_id, seq)

    # Observability (coworker/observability.py): span timeline + manual OTLP export
    # (double opt-in — the endpoint must be configured AND the export called).
    @router.get("/v1/sessions/{session_id}/timeline")
    def session_timeline(session_id: str, limit: int = 500) -> dict[str, Any]:
        return {"spans": manager.session_timeline(session_id, limit=limit)}

    # Turn ledger (coworker/trajectory.py): append-only, redacted metadata events.
    @router.get("/v1/sessions/{session_id}/trajectory")
    def session_trajectory(
        session_id: str, limit: int = 500, since_seq: int = 0
    ) -> dict[str, Any]:
        return manager.session_trajectory(session_id, limit=limit, since_seq=since_seq)

    # Session fork / replay（建议路线 P0-2）：turn 边界复制会话、后台重跑输入、轨迹对比。
    # 错误按 manager 返回的 status/detail 映射 HTTP 状态码（400/403/404/409）。
    @router.post("/v1/sessions/{session_id}/fork")
    def session_fork(session_id: str, body: Optional[dict] = None) -> Any:
        body = body or {}
        turn = body.get("turn")
        result = manager.fork_session(
            session_id, turn=int(turn) if turn is not None else None
        )
        if not result.get("ok"):
            return _fork_error(result)
        return result

    @router.post("/v1/sessions/{session_id}/replay")
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

    @router.get("/v1/sessions/{session_id}/trajectory/diff")
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

    # -- project bindings (pass 20 / UX-044) --------------------------------------
    @router.get("/v1/sessions/{session_id}/project-menu")
    def project_menu(session_id: str, kind: str = "memory") -> dict[str, Any]:
        return manager.project_menu(session_id, kind)

    @router.put("/v1/sessions/{session_id}/bindings")
    def put_binding(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        name = body.get("name")
        return manager.set_binding(
            session_id, str(body.get("kind", "")), str(name) if name else None
        )

    @router.post("/v1/sessions/{session_id}/project-name")
    def name_project(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.name_current_project(
            session_id, str(body.get("kind", "")), str(body.get("name", ""))
        )

    return router
