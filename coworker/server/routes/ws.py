"""The WebSocket surface: the session channel and the event stream.

The session socket carries the engine's event stream and every interactive prompt. All
four prompt kinds (approval / question / directory / plan) park as Inbox items and are
awaited through `inbox.wait`, so they survive a dropped socket (redelivered on
reconnect) and can be answered from any surface; `visibility` only decides where they
SHOW — Unattended goes to the cross-session Inbox, attended stays inline.

The loopback socket is reachable by any local process, so the handshake is gated twice
(token as a subprotocol, then Origin) and inbound traffic is bounded before it can
become model content: frame size, message text, attachments, and per-connection
request rate.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import origin_allowed, websocket_authenticated
from ..manager import SessionManager
from ..ws_turns import TurnRunner
from ..ws_prompts import PromptGateway
from ...attachments import (
    MAX_ATTACHMENTS as _MAX_ATTACHMENTS,
    MAX_IMAGE_CHARS,
    MAX_PDF_CHARS,
    MAX_TEXT_CHARS,
    build_user_content,
)
from ...inbox import VIS_INLINE
from ...permissions import Mode

# Caps on inbound WebSocket traffic. The loopback socket is unauthenticated (any local
# process can reach it), so bound frames, messages, and per-connection request rate before
# building model content or starting a turn.
WS_MAX_FRAME_BYTES = 16 * 1024 * 1024
WS_RATE_LIMIT_COUNT = 30
WS_RATE_LIMIT_WINDOW_SECONDS = 10.0
MAX_MESSAGE_TEXT_CHARS = 200_000
MAX_ATTACHMENTS_BYTES = 15_000_000  # leaves JSON overhead below the 16 MiB frame cap


def _json_value_size(value: Any) -> int:
    """Conservative UTF-8 size of parsed JSON without allocating another giant string."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, dict):
        return sum(_json_value_size(k) + _json_value_size(v) for k, v in value.items())
    if isinstance(value, list):
        return sum(_json_value_size(v) for v in value)
    return 8  # numbers, booleans, null, separators



def build_ws_router(manager: SessionManager, api_token: str) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/session/{session_id}")
    async def ws_session(ws: WebSocket, session_id: str) -> None:
        if not websocket_authenticated(ws, api_token):
            await ws.close(code=1008)
            return
        # CORS never gates WebSockets, so a cross-site page could otherwise open this socket
        # and drive the session into tool calls. Reject a disallowed browser Origin before
        # accepting the handshake (1008 = policy violation).
        if not origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol="openworker" if api_token else None)
        agent = ws.query_params.get("agent") or "code"
        # All prompt plumbing (the five askers + where each item shows) lives in
        # ws_prompts.PromptGateway; this socket owns the turn loop and the wire.
        prompts = PromptGateway(manager, ws, session_id, agent)

        # All four interactive prompts (approval / question / directory / plan) are parked as Inbox
        # items and awaited via inbox.wait — so they survive a dropped socket (redelivered on
        # reconnect) and can be resolved from any surface. `visibility` decides where they SHOW:
        # Unattended → the cross-session Inbox; attended → inline in this session only. The agent
        # stays blocked until the item is resolved (live WS response, REST, or a bound channel).
        workspace = ws.query_params.get("workspace")
        mcp_tools = await manager.prepare_mcp_tools(
            session_id, workspace=workspace, agent=agent
        )
        engine = manager.get_engine(
            session_id,
            workspace=workspace,
            agent=agent,
            approver=prompts.approval,
            extra_tools=mcp_tools,
            directory_requester=prompts.directory,
            plan_approver=prompts.plan,
            question_asker=prompts.question,
            tool_requester=prompts.tool_request,
            team_approver=prompts.team,
            items_approver=prompts.items,
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
        engine.is_attended = lambda: prompts.visibility() == VIS_INLINE
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

        # Turns live in ws_turns.TurnRunner: it owns the atomic claim, the event
        # broadcast, the checkpoint saves and the always-broadcast `turn_done`.
        turns = TurnRunner(manager, session_id, engine, reject=reject_input)

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
                    and now - inbound_times[0] > WS_RATE_LIMIT_WINDOW_SECONDS
                ):
                    inbound_times.popleft()
                if len(inbound_times) >= WS_RATE_LIMIT_COUNT:
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
                    turns.resolve_pending(message.get("decision", "deny"))
                elif kind == "directory_response":
                    turns.resolve_pending(
                        json.dumps(
                            {
                                "granted": bool(message.get("granted")),
                                "path": message.get("path", ""),
                                "writable": bool(message.get("writable", False)),
                            }
                        )
                    )
                elif kind == "tool_response":
                    turns.resolve_pending(
                        json.dumps({"approved": bool(message.get("approved"))})
                    )
                elif kind == "plan_response":
                    turns.resolve_pending(
                        json.dumps(
                            {
                                "approved": bool(message.get("approved")),
                                "mode": message.get("mode", "interactive"),
                                "feedback": message.get("feedback", ""),
                            }
                        )
                    )
                elif kind in ("team_response", "items_response"):
                    turns.resolve_pending(
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
                    turns.resolve_pending(str(message.get("answer", "")))
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
                    await turns.claim(retry=True)
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
                        await turns.apply_model(model)
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
                    if len(text) > MAX_MESSAGE_TEXT_CHARS:
                        reject = (
                            f"Message too long ({len(text)} chars; "
                            f"limit {MAX_MESSAGE_TEXT_CHARS})."
                        )
                    elif len(attachments) > _MAX_ATTACHMENTS:
                        reject = (
                            f"Too many attachments ({len(attachments)}; "
                            f"limit {_MAX_ATTACHMENTS})."
                        )
                    elif any(not isinstance(a, dict) for a in attachments):
                        reject = "Invalid attachment: expected an object."
                    elif _json_value_size(attachments) > MAX_ATTACHMENTS_BYTES:
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
                    await turns.apply_model(model)
                    if text or attachments:
                        content = build_user_content(text, attachments)
                        await turns.claim(content=content, display=display)
                else:
                    await reject_input(f"Unknown WebSocket message type: {kind}.")
        except WebSocketDisconnect:
            pass
        finally:
            manager.unregister_session_client(session_id, ws.send_json)

    @router.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        """App-wide event stream (session-independent): the GUI keeps one open for
        pushes like automation_run_started (the UX-026 toast). Read-only — inbound
        frames are ignored; the receive loop just detects disconnect."""
        if not websocket_authenticated(ws, api_token):
            await ws.close(code=1008)
            return
        if not origin_allowed(ws.headers.get("origin")):
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


    return router
