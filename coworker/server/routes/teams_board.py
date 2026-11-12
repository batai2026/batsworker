"""Agent teams and the open board surface — `/v1/sessions/{id}/board`, `/v1/teams/*`,
`/v1/board/*`.

Two audiences, one store:

* The in-app side (`/v1/sessions/{id}/board`, `/v1/teams/*`) rides the sidecar token and
  acts as the USER — the human half of the gates.
* The open surface (`/v1/board/*`, OPE-100) is token-authenticated per request: identity
  is the TOKEN (actor + role bound at mint, never client-asserted) and authority is the
  STORE, i.e. the same double gate in-app agents get. This is the one wire protocol every
  external front door rides — the `ocw` CLI, the team-board MCP server, headless
  instances. Tokens are required even on loopback because they carry identity, not just
  access; `/v1/board/*` is the one path prefix exempt from the sidecar header gate for
  that reason.

`_board` is the whole error contract in one place: 401 for no/unknown token, 403 for
authority, 400 for board/domain errors.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..manager import SessionManager
from ...teams.model import AuthorityError as TeamsAuthorityError
from ...teams.model import BoardError as TeamsBoardError


def _board_actor(manager: SessionManager, request: Request):
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    return manager.board_tokens.resolve(token)


def _board(manager: SessionManager, request: Request, handler):
    actor = _board_actor(manager, request)
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


def build_teams_board_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    # Agent teams (OPE-96): the session's board (workspace-keyed space) + journal
    # overview. Mutations act as the USER — the human side of the gates.
    @router.get("/v1/sessions/{session_id}/board")
    def session_board(session_id: str) -> dict[str, Any]:
        return manager.session_board(session_id)

    @router.get("/v1/sessions/{session_id}/board/item")
    def session_board_item(session_id: str, id: int) -> dict[str, Any]:
        return manager.board_item_detail(session_id, int(id))

    @router.get("/v1/sessions/{session_id}/board/attachment")
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

    @router.post("/v1/sessions/{session_id}/board/comment")
    def session_board_comment(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.board_comment(
            session_id, int(body.get("item", 0)), str(body.get("body", ""))
        )

    @router.post("/v1/sessions/{session_id}/board/transition")
    def session_board_transition(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.board_transition(
            session_id,
            int(body.get("item", 0)),
            str(body.get("to", "")),
            comment=str(body.get("comment", "")),
        )

    @router.get("/v1/teams/{team_id}/chat")
    def team_chat(team_id: str) -> dict[str, Any]:
        return manager.team_chat(team_id)

    @router.post("/v1/teams/{team_id}/chat")
    def team_chat_post(team_id: str, body: dict) -> dict[str, Any]:
        return manager.post_team_chat(team_id, str((body or {}).get("text", "")))

    @router.get("/v1/teams/journal")
    def teams_journal() -> dict[str, Any]:
        return {"cases": manager.journal_overview()}

    # ---- The open board surface (OPE-100): token-authenticated `/v1/board` API.
    # Identity is the TOKEN (actor+role bound at mint, resolved per request, never
    # client-asserted); authority is the STORE — the same double gate in-app agents
    # get. This is the one wire protocol every external front door rides:
    # RemoteDialect (the `ocw` CLI, the team-board MCP server, headless instances)
    # today, a hosted board service later. Tokens are required even on loopback —
    # they carry identity, not just access.

    @router.get("/v1/board/whoami")
    def board_whoami(request: Request):
        return _board(
            manager,
            request,
            lambda actor: {"actor": actor.id, "role": actor.role.value},
        )

    @router.get("/v1/board/spaces")
    def board_spaces(request: Request):
        return _board(
            manager, request, lambda actor: {"spaces": manager.team_store.spaces()}
        )

    @router.get("/v1/board/items")
    def board_list_items(
        request: Request, space: str, state: str = "", assignee: str = ""
    ):
        return _board(
            manager,
            request,
            lambda actor: {
                "items": manager.team_store.list_items(
                    space, actor, state=state or None, assignee=assignee or None
                )
            },
        )

    @router.get("/v1/board/item")
    def board_get_item(request: Request, space: str, id: int):
        return _board(
            manager,
            request,
            lambda actor: manager.team_store.get_item(space, int(id)),
        )

    @router.post("/v1/board/items")
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

        return _board(manager, request, run)

    @router.post("/v1/board/items/transition")
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

        return _board(manager, request, run)

    @router.post("/v1/board/items/comment")
    def board_comment_item(request: Request, body: dict):
        body = body or {}
        return _board(
            manager,
            request,
            lambda actor: manager.team_store.comment(
                str(body.get("space", "")),
                actor,
                int(body.get("id", 0)),
                str(body.get("body", "")),
                refs=[str(ref) for ref in body.get("refs") or []],
            ),
        )

    @router.post("/v1/board/items/assign")
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

        return _board(manager, request, run)

    @router.post("/v1/board/items/claim")
    def board_claim_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            item = manager.team_store.claim(
                str(body.get("space", "")), actor, int(body.get("id", 0))
            )
            manager.kick_team_tick()  # claims land in the lead's feed
            return item

        return _board(manager, request, run)

    @router.post("/v1/board/link")
    def board_link_items(request: Request, body: dict):
        body = body or {}
        return _board(
            manager,
            request,
            lambda actor: manager.team_store.link(
                str(body.get("space", "")),
                actor,
                int(body.get("src", 0)),
                str(body.get("kind", "")),
                int(body.get("dst", 0)),
            ),
        )

    @router.post("/v1/board/items/attach")
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

        return _board(manager, request, run)

    @router.get("/v1/board/attachment")
    def board_attachment(request: Request, name: str):
        def run(actor):
            from fastapi.responses import Response

            path = manager.attachment_store.path_for(name)
            return Response(
                content=path.read_bytes(),
                media_type=manager.attachment_store.mime_for(name),
            )

        return _board(manager, request, run)

    @router.get("/v1/board/policy")
    def board_get_policy(request: Request, space: str):
        return _board(
            manager, request, lambda actor: manager.team_store.policy(space)
        )

    @router.post("/v1/board/policy")
    def board_set_policy(request: Request, body: dict):
        body = body or {}
        return _board(
            manager,
            request,
            lambda actor: manager.team_store.set_policy(
                str(body.get("space", "")), actor, claims=str(body.get("claims", ""))
            ),
        )

    @router.get("/v1/board/pending")
    def board_pending(request: Request, space: str, limit: int = 200):
        # The actor's FEED: events on its slice since its cursor — interest
        # follows the assignment relation, same projection in-app workers use.
        return _board(
            manager,
            request,
            lambda actor: {
                "events": manager.team_store.feed_for(
                    space, actor.id, limit=int(limit)
                )
            },
        )

    @router.post("/v1/board/consume")
    def board_consume(request: Request, body: dict):
        body = body or {}

        def run(actor):
            manager.team_store.consume_feed(
                str(body.get("space", "")), actor.id, int(body.get("upto_seq", 0))
            )
            return {"ok": True}

        return _board(manager, request, run)

    @router.get("/v1/board/journal/cases")
    def board_journal_cases(request: Request):
        return _board(
            manager,
            request,
            lambda actor: {"cases": manager.journal_store.overview(actor)},
        )

    @router.get("/v1/board/journal")
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
            manager,
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

    @router.post("/v1/board/journal")
    def board_journal_append(request: Request, body: dict):
        body = body or {}
        return _board(
            manager,
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

    return router
