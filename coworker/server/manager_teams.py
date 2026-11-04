"""Teams cluster split out of manager.py (manager-split refactor).

Mixin methods only; shared state lives on the SessionManager facade (self.*). Module-level
imports are limited to what the moved code references; everything else resolves via self.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ..agents import get_agent
from ..projects import resolve_board_space, resolve_memory_key
from ..sessions import SessionRecord
from ..teams import (
    Actor as TeamActor,
    BoardError as TeamsBoardError,
    Role as TeamRole,
    board_tools,
    journal_tools,
)
from ..teams.registry import TeamWorker

logger = logging.getLogger("coworker.manager")


class TeamsMixin:
    # ------------------------------------------------------------- agent teams (OPE-96)

    # ------------------------------------------------- project identity (pass 20)

    def _memory_key_for(self, record, ws: Optional[str]) -> Optional[str]:
        """Binding > git > path, with the one-time path→git re-key on the way."""
        binding = ((record.bindings if record else {}) or {}).get("memory")
        return resolve_memory_key(
            ws,
            binding=binding,
            names=self.session_store.names(),
            memory_store=self.memory_store,
        )

    def _space_for(self, record, ws: Optional[str]) -> Optional[str]:
        """Board-space twin of _memory_key_for — same ladder, board collision rule."""
        binding = ((record.bindings if record else {}) or {}).get("board")
        return resolve_board_space(
            ws,
            binding=binding,
            names=self.session_store.names(),
            team_store=self.team_store,
        )

    def _board_space(self, session_id: str) -> Optional[str]:
        record = self.session_store.load(session_id)
        workspace = (record.workspace if record else None) or self.default_workspace
        return self._space_for(record, workspace) if workspace else None

    def _user_actor(self) -> TeamActor:
        return TeamActor(id="user", role=TeamRole.USER)

    def board_item_detail(self, session_id: str, item_id: int) -> dict[str, Any]:
        """One item in full, with its TIMELINE — creations, assignments,
        transitions, and comments merged chronologically (the detail pane renders
        the item's whole story; the store is an event log, so this is just its
        honest projection). Acts as the user."""
        space = self._board_space(session_id)
        if space is None:
            return {"error": "no board for this session"}
        try:
            item = self.team_store.get_item(space, int(item_id))
        except TeamsBoardError as error:
            return {"error": str(error)}
        timeline: list[dict[str, Any]] = []
        for event in self.team_store.events(space, item_id=int(item_id)):
            payload = event.get("payload") or {}
            row: dict[str, Any] = {
                "seq": event["seq"],
                "ts": event["ts"],
                "actor": event["actor"],
            }
            if event["kind"] == "item_created":
                row["kind"] = "created"
            elif event["kind"] == "item_assigned":
                row["kind"] = "claimed" if payload.get("claimed") else "assigned"
                row["assignee"] = payload.get("assignee") or ""
            elif event["kind"] == "item_transitioned":
                row["kind"] = "moved"
                row["to"] = payload.get("to") or ""
                if payload.get("comment"):
                    row["body"] = payload["comment"]
                if payload.get("refs"):
                    row["refs"] = payload["refs"]
            elif event["kind"] == "item_commented":
                row["kind"] = "comment"
                row["body"] = payload.get("body") or ""
                if payload.get("refs"):
                    row["refs"] = payload["refs"]
            else:
                continue
            timeline.append(row)
        item["timeline"] = timeline
        return item

    def board_comment(self, session_id: str, item_id: int, body: str) -> dict[str, Any]:
        """A pure note from the user on an item — never changes state (owner
        doctrine 2026-08-17); the assignee hears it through its feed."""
        space = self._board_space(session_id)
        if space is None:
            return {"error": "no board for this session"}
        try:
            event = self.team_store.comment(
                space, self._user_actor(), int(item_id), body
            )
        except (TeamsBoardError, ValueError) as error:
            return {"error": str(error)}
        self.kick_team_tick()  # the assignee's feed has news
        return {"ok": True, "seq": event["seq"]}

    def session_board(self, session_id: str) -> dict[str, Any]:
        """The session's board: items grouped by the workspace-keyed space. Empty
        (space=None) when the workspace has no items — the rail hides itself."""
        space = self._board_space(session_id)
        if space is None:
            return {"space": None, "name": "", "items": []}
        items = self.team_store.list_items(space, self._user_actor())
        if not items:
            return {"space": None, "name": "", "items": []}
        # Blocked rows carry the blocker as a plain fact ("blocked: need tfvars") —
        # the latest blocked-transition comment, resolved here so the list stays
        # one round-trip.
        for item in items:
            if item["state"] != "blocked":
                continue
            for event in reversed(
                self.team_store.events(space, item_id=item["id"])
            ):
                payload = event.get("payload") or {}
                if event["kind"] == "item_transitioned" and payload.get("to") == "blocked":
                    if payload.get("comment"):
                        item["blocker"] = self._clamp(payload["comment"], 120)
                    break
        return {"space": space, "name": Path(space).name, "items": items}

    def board_transition(
        self, session_id: str, item: int, to: str, comment: str = ""
    ) -> dict[str, Any]:
        space = self._board_space(session_id)
        if space is None:
            return {"error": "this session has no board"}
        try:
            return self.team_store.transition(
                space, self._user_actor(), int(item), to, comment=comment
            )
        except (TeamsBoardError, ValueError) as error:
            return {"error": str(error)}

    def board_create_items(
        self, session_id: str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """The decomposition gate's approved action: create the proposed items as
        the LEAD (its identity is the creator; the user's approval is the gate that
        let this run). Validates everything up front so a bad batch creates nothing."""
        record = self.session_store.load(session_id)
        if record is None or not record.workspace:
            return {"approved": False, "error": "the session has no workspace"}
        space = self._space_for(record, record.workspace)
        actor = TeamActor(
            id=f"{record.agent}:{session_id[:8]}",
            role=TeamRole.LEAD,
            persona=record.agent,
            session_id=session_id,
        )
        for entry in items:
            if not str((entry or {}).get("title", "")).strip() or not str(
                (entry or {}).get("criteria", "")
            ).strip():
                return {
                    "approved": False,
                    "error": "every item needs a title and acceptance criteria",
                }
        created = []
        for entry in items:
            item = self.team_store.create_item(
                space,
                actor,
                title=str(entry["title"]),
                criteria=str(entry["criteria"]),
                description=str(entry.get("description", "")),
                case=str(entry.get("case", "")) or None,
            )
            created.append({"id": item["id"], "title": item["title"]})
        return {
            "approved": True,
            "items": created,
            "note": "items created on the board — staff and assign to start work",
        }

    def team_chat(self, team_id: str, *, mark_read: bool = True) -> dict[str, Any]:
        """The chat view's payload. Viewing IS reading for the user: the badge
        cursor advances on fetch."""
        team = self.teams.get(team_id)
        if team is None or not team.chat_enabled or not team.chat_group:
            return {"enabled": False, "messages": [], "members": []}
        group = self.chat_store.get_group(team.chat_group) or {"members": []}
        messages = self.chat_store.messages(team.chat_group)
        if mark_read and messages:
            self.chat_store.consume(team.chat_group, "user", messages[-1]["seq"])
        return {
            "enabled": True,
            "team_id": team_id,
            "members": group["members"],
            "messages": messages,
        }

    def post_team_chat(self, team_id: str, text: str) -> dict[str, Any]:
        team = self.teams.get(team_id)
        if team is None or not team.chat_enabled or not team.chat_group:
            return {"error": "chat is not enabled for this team"}
        try:
            message = self.chat_store.post(
                team.chat_group, "user", text, author_role="user"
            )
        except (TeamsBoardError, ValueError) as error:
            return {"error": str(error)}
        # A user post wakes every member — kick the drain rather than waiting a tick.
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.team_tick(), self._loop)
        return message

    def journal_overview(self) -> list[dict[str, Any]]:
        return self.journal_store.overview(self._user_actor())

    TEAM_WAKE_CAP_PER_HOUR = 60  # budget gate at the wake gate: silent server cap

    def _team_tools_for(
        self, session_id: str, agent: Any, record: Any, ws: Optional[str]
    ) -> list[Any]:
        """Board/journal verbs, gated by the persona `team:` trait. Leads get the
        coordination set (+ steer); workers get the worker set bound to their roster
        actor id. OPENWORKER_TEAM_BOARD=1 keeps the phase-1 any-session-as-lead dev
        mode."""
        role = getattr(agent, "team", None)
        if role is None and ws and os.environ.get("OPENWORKER_TEAM_BOARD") == "1":
            role = "lead"
        if role is None or not ws:
            return []
        space = self._space_for(record, ws)
        if role == "worker":
            info = (record.team if record is not None else {}) or {}
            actor = TeamActor(
                id=str(info.get("actor") or f"{agent.name}:{session_id[:8]}"),
                role=TeamRole.WORKER,
                persona=agent.name,
                session_id=session_id,
            )
            space = str(info.get("space") or space)
        else:
            actor = TeamActor(
                id=f"{agent.name}:{session_id[:8]}",
                role=TeamRole.LEAD,
                persona=agent.name,
                session_id=session_id,
            )
        tools = board_tools(
            self.team_store,
            space=space,
            actor=actor,
            attachments=self.attachment_store,
        ) + journal_tools(
            self.journal_store, actor=actor, space=space
        )
        if role == "lead":
            tools.append(self._steer_tool(session_id))
            tools.append(self._team_options_tool())
        # post_chat registers for every team persona; it resolves the group at call
        # time (the team may not exist yet at engine build) and fails gracefully
        # when chat is off.
        tools.append(self._post_chat_tool(session_id, role))
        return tools

    def _post_chat_tool(self, session_id: str, role: str) -> Any:
        import aisuite as ai

        manager = self

        def post_chat(text: str, record_on_item: Optional[int] = None) -> dict:
            """Post to # team chat. Mention teammates with @name to reach them —
            only mentioned members are woken (the user always sees it). Chat is for
            questions and consensus; status lives on the board. If your message
            answers something that matters, pass record_on_item to also record it
            as a comment on that work item."""
            team, handle, actor = manager._chat_identity(session_id, role)
            if team is None:
                return {"error": "this session is not part of a team"}
            if not team.chat_enabled or not team.chat_group:
                return {"error": "team chat is not enabled for this team"}
            try:
                message = manager.chat_store.post(
                    team.chat_group, handle, text, author_role=role
                )
            except (TeamsBoardError, ValueError) as error:
                return {"error": str(error)}
            result: dict[str, Any] = {
                "ok": True,
                "mentioned": message["mentions"],
            }
            if record_on_item is not None and actor is not None:
                try:
                    manager.team_store.comment(
                        team.space, actor, int(record_on_item), text
                    )
                    result["recorded_on"] = int(record_on_item)
                except (TeamsBoardError, ValueError) as error:
                    result["record_error"] = str(error)
            return result

        return ai.tool(
            post_chat,
            metadata=ai.ToolMetadata(
                category="team", risk_level="low", capabilities=["team"]
            ),
        )

    def _chat_identity(self, session_id: str, role: str):
        """(team, chat handle, board actor) for a team session — the lead's chat
        handle is "lead"; a worker's handle IS its board actor (the callname)."""
        if role == "lead":
            team = self.teams.for_lead_session(session_id)
            if team is None:
                return None, "", None
            record = self.session_store.load(session_id)
            actor = TeamActor(
                id=team.lead_actor,
                role=TeamRole.LEAD,
                persona=record.agent if record else "",
                session_id=session_id,
            )
            return team, "lead", actor
        found = self.teams.for_worker_session(session_id)
        if found is None:
            return None, "", None
        team, worker = found
        actor = TeamActor(
            id=worker.actor,
            role=TeamRole.WORKER,
            persona=worker.persona,
            session_id=session_id,
        )
        return team, worker.actor, actor

    def _team_options_tool(self) -> Any:
        """Registry-injected staffing knowledge: the lead's options come from the
        persona registry at call time — installing a worker coworker automatically
        widens what a lead can propose; nothing is hardcoded. Solo personas never
        appear (fail closed at the source AND at create_team)."""
        import aisuite as ai

        manager = self

        def team_options() -> dict:
            """List the worker coworkers available for staffing (call before
            propose_team). Only team-capable workers are listed — solo coworkers
            cannot join a team."""
            out = []
            # Registry entries directly — NOT list_all(), which applies the
            # ships:false visibility filter: a lead that is running (internal
            # build or user-enabled) must be able to staff its workers even
            # when those workers are hidden from the settings page.
            for pid in manager.personas.ids():
                entry = manager.personas.get(pid)
                m = getattr(entry, "manifest", None)
                if m is None or m.team != "worker":
                    continue
                if not manager.personas.is_enabled(pid):
                    continue
                out.append(
                    {
                        "persona": pid,
                        "name": m.name,
                        "tagline": m.tagline,
                        "recommended_models": list(m.recommended_models),
                    }
                )
            return {"workers": out}

        return ai.tool(
            team_options,
            metadata=ai.ToolMetadata(
                category="team", risk_level="low", capabilities=["team"]
            ),
        )

    def _steer_tool(self, lead_session_id: str) -> Any:
        """The lead's downward steering verb. Text lands in the worker's session
        attributed [Lead] — queued into a live turn, or a fresh background turn when
        idle. Strictly downward: no worker ever gets this tool."""
        import aisuite as ai

        manager = self

        def steer_worker(worker: str, message: str) -> dict:
            """Send steering text to one of your workers (by actor id). Use for
            exceptions — changed requirements, stop/redirect, unblock guidance;
            routine status flows through the board, not steering."""
            team = manager.teams.for_lead_session(lead_session_id)
            if team is None:
                return {"error": "no team yet — propose one with propose_team first"}
            match = next((w for w in team.workers if w.actor == worker), None)
            if match is None:
                return {
                    "error": f"no worker '{worker}' on this team",
                    "workers": [w.actor for w in team.workers],
                }
            if manager._loop is None:
                return {"error": "steering is unavailable in this surface"}
            asyncio.run_coroutine_threadsafe(
                manager.deliver_to_session(
                    match.session_id, f"[Lead] {message}".strip()
                ),
                manager._loop,
            )
            return {"ok": True, "delivered_to": worker}

        return ai.tool(
            steer_worker,
            metadata=ai.ToolMetadata(
                category="team", risk_level="medium", capabilities=["team"]
            ),
        )

    def create_team(
        self, session_id: str, members: list[dict[str, Any]], *, enable_chat: bool = False
    ) -> dict[str, Any]:
        """The staffing gate's approved action: PRE-SPAWN worker sessions (state on
        disk, zero tokens — the first model turn fires when the first assignment
        lands) and register the team. Fails closed on personas without `team: worker`."""
        record = self.session_store.load(session_id)
        if record is None or not record.workspace:
            return {"approved": False, "error": "the lead session has no workspace"}
        if self.teams.for_lead_session(session_id) is not None:
            return {"approved": False, "error": "this session already leads a team"}
        space = self._space_for(record, record.workspace)
        workers: list[TeamWorker] = []
        used: set[str] = {"lead", "user", "board"}  # reserved handles
        for member in members:
            pid = str((member or {}).get("persona", "")).strip()
            try:
                ag = get_agent(pid)
            except Exception:
                return {
                    "approved": False,
                    "error": f"unknown coworker '{pid}' — it must be installed and enabled",
                }
            if getattr(ag, "team", None) != "worker":
                # Fail closed: solo personas are not team-eligible — their prompts
                # are written at a human, not a lead.
                return {
                    "approved": False,
                    "error": f"'{pid}' is not team-capable (needs `team: worker`)",
                }
            # The lead-given callname is the HANDLE: board assignee, @mention target,
            # sidebar label. It must be mention-safe and unique on the team.
            name = str(member.get("name", "")).strip().lower()
            if name and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,23}", name):
                return {
                    "approved": False,
                    "error": f"'{name}' isn't a usable callname — letters/digits/._- only, max 24",
                }
            actor, n = name or pid, 2
            while actor in used:
                actor, n = f"{name or pid}-{n}", n + 1
            used.add(actor)
            worker_sid = uuid.uuid4().hex[:12]
            model = str(member.get("model") or record.model)
            self.session_store.save(
                SessionRecord(
                    session_id=worker_sid,
                    workspace=record.workspace,
                    model=model,
                    mode=record.mode,
                    messages=[],
                    agent=pid,
                )
            )
            # Written via the dedicated setter: the turn-save upsert never touches
            # `team`, so a worker's first turn can't detach it from its lead.
            self.session_store.set_team(
                worker_sid,
                {
                    "team_id": "",  # patched below once the team exists
                    "role": "worker",
                    "actor": actor,
                    "lead_session": session_id,
                    "space": space,
                },
            )
            workers.append(
                TeamWorker(
                    actor=actor,
                    persona=pid,
                    session_id=worker_sid,
                    model=model,
                    reason=str(member.get("reason", "")).strip(),
                )
            )
        chat_group = ""
        if enable_chat:
            group = self.chat_store.create_group(
                "team chat",
                [
                    *(
                        {"name": w.actor, "persona": w.persona, "role": "worker"}
                        for w in workers
                    ),
                    {"name": "lead", "persona": record.agent, "role": "lead"},
                ],
            )
            chat_group = group["group_id"]
        team = self.teams.create(
            space=space,
            lead_session=session_id,
            lead_actor=f"{record.agent}:{session_id[:8]}",
            workers=workers,
            chat_enabled=enable_chat,
            chat_group=chat_group,
        )
        for worker in workers:
            self.session_store.set_team(
                worker.session_id,
                {
                    "team_id": team.team_id,
                    "role": "worker",
                    "actor": worker.actor,
                    "lead_session": session_id,
                    "space": space,
                },
            )
            self._emit_session_created(worker.session_id, worker.persona)
        self.session_store.set_team(
            session_id,
            {
                "team_id": team.team_id,
                "role": "lead",
                "actor": team.lead_actor,
                "space": space,
            },
        )
        return {
            "approved": True,
            "team_id": team.team_id,
            "workers": [
                {"actor": w.actor, "persona": w.persona, "session_id": w.session_id}
                for w in workers
            ],
            "note": (
                "team created — workers are idle until you assign. Create work items"
                " and assign them to the actor ids above; review-state items are"
                " yours to verify."
            ),
        }

    def kick_team_tick(self) -> None:
        """Nudge the wake plumbing from outside the turn loop — e.g. after an
        external board client writes through the `/v1/board` API, so a review or a
        new filing reaches the lead now, not at the next 30s scheduler tick."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.team_tick(), self._loop)

    async def team_tick(self) -> int:
        """Drain team queues (called each scheduler tick + kicked after team turns).
        One wake consumes a burst as one digest; durable-until-consumed — the cursor
        advances only after the delivery turn is dispatched."""
        delivered = 0
        for team in self.teams.all():
            if team.paused:
                continue
            for worker in team.workers:
                delivered += await self._drain_team_member(
                    team,
                    session_id=worker.session_id,
                    actor=worker.actor,
                    is_lead=False,
                )
            delivered += await self._drain_team_member(
                team,
                session_id=team.lead_session,
                actor=team.lead_actor,
                is_lead=True,
            )
            delivered += await self._maybe_backstop_lead(team)
        return delivered

    # The lead owns its cadence (sleep_until, stretch-when-quiet); this backstop only
    # exists because prompts aren't guarantees. A forgotten timer must never orphan
    # a running team — and it de-facto covers a worker dying without a transition
    # (its item goes stale; the backstop wake surfaces it in the digest).
    TEAM_LEAD_BACKSTOP_SECS = 600

    def _lead_backstop_due(self, team) -> bool:
        sid = team.lead_session
        if self.is_running(sid) or sid in self._team_inflight:
            return False
        if self.wakes.pending(sid):
            return False  # a timer is set — the lead is on cadence, not forgotten
        # Restart-safe: the first observation starts the clock instead of waking.
        last = self._team_last_alive.setdefault(sid, time.time())
        if time.time() - last < self.TEAM_LEAD_BACKSTOP_SECS:
            return False
        try:
            items = self.team_store.list_items(team.space, self._user_actor())
        except Exception:
            return False
        return any(
            i["state"] in ("in_progress", "blocked", "review") for i in items
        )

    async def _maybe_backstop_lead(self, team) -> int:
        if not self._lead_backstop_due(team):
            return 0
        if not self.teams.count_wake(team.team_id, cap=self.TEAM_WAKE_CAP_PER_HOUR):
            return 0
        sid = team.lead_session
        self._team_last_alive[sid] = time.time()
        message = (
            "⏰ Backstop check — work is in flight but you had no check-in timer"
            " set.\n\n"
            + (self.team_staleness_digest(sid) or "Board state unavailable.")
            + "\n\nGlance, act only if something needs you, and set your next"
            " check-in with sleep_until (start 3–5 minutes out; stretch when quiet)."
        )
        self._team_inflight.add(sid)

        async def _deliver() -> None:
            try:
                await self.deliver_to_session(
                    sid, message, source=self._board_source(team, message)
                )
            finally:
                self._team_inflight.discard(sid)

        asyncio.create_task(_deliver())
        return 1

    async def _drain_team_member(
        self, team, *, session_id: str, actor: str, is_lead: bool
    ) -> int:
        # Interest follows the assignment relation: everyone's feed is the events
        # on their slice (assigned ∪ filed) — comments, moves, reassignments. The
        # lead additionally subscribes to the board-wide decision classes.
        directs = self.team_store.feed_for(team.space, actor)
        subs = (
            self.team_store.subscribed_events(team.space, actor) if is_lead else []
        )
        if subs:
            seen = {e["seq"] for e in subs}
            directs = [e for e in directs if e["seq"] not in seen]
        chat_handle = "lead" if is_lead else actor
        chats = (
            self.chat_store.unread_for(team.chat_group, chat_handle)
            if team.chat_enabled and team.chat_group
            else []
        )
        # Cancel is top-priority: an in-flight worker gets interrupted NOW; the
        # queued notice (delivered when the turn dies) tells it why. Only for the
        # item's ASSIGNEE — a filer merely hears about it.
        def _holds(event) -> bool:
            try:
                item = self.team_store.get_item(team.space, int(event["item_id"]))
            except Exception:
                return False
            return item["assignee"] == actor

        cancels = [
            e
            for e in directs
            if e["kind"] == "item_transitioned"
            and (e.get("payload") or {}).get("to") == "canceled"
            and _holds(e)
        ]
        if cancels and self.is_running(session_id):
            engine = self._engines.get(session_id)
            if engine is not None:
                engine.request_interrupt()
        if not directs and not subs and not chats:
            return 0
        if self.is_running(session_id) or session_id in self._team_inflight:
            return 0  # it will drain on its next turn end / next tick
        if not self.teams.count_wake(team.team_id, cap=self.TEAM_WAKE_CAP_PER_HOUR):
            logger.warning("team %s paused for budget this hour", team.team_id)
            return 0
        message, rows = self._team_digest(
            team, directs, subs, chats, is_lead=is_lead, reader=actor
        )
        self._team_inflight.add(session_id)
        source = self._board_source(team, message, rows=rows)

        async def _deliver() -> None:
            try:
                await self.deliver_to_session(session_id, message, source=source)
                # Consume only after the turn dispatched: a crash before this replays
                # the batch next tick (at-least-once, never silently lost).
                # The feed cursor advances past BOTH batches: a subs event deduped
                # out of directs must not replay as a direct next tick.
                delivered = [e["seq"] for e in directs] + [e["seq"] for e in subs]
                if delivered:
                    self.team_store.consume_feed(team.space, actor, max(delivered))
                if subs:
                    self.team_store.consume_subscription(
                        team.space, actor, subs[-1]["seq"]
                    )
                if chats:
                    self.chat_store.consume(
                        team.chat_group, chat_handle, chats[-1]["seq"]
                    )
            finally:
                self._team_inflight.discard(session_id)

        asyncio.create_task(_deliver())
        return 1

    # Long comment/hand-off bodies are already durable on the board — the wake
    # message's job is to say what needs DECISIONS, not to re-carry the evidence
    # into the recipient's context on every wake (owner ruling 2026-08-16). The
    # model text clamps hard; the UI sidecar rows clamp softer (the human gets a
    # bigger excerpt on click without re-inflating the lead's prompt).
    DIGEST_CLAMP_MODEL = 300
    DIGEST_CLAMP_UI = 600

    @staticmethod
    def _clamp(text: str, limit: int, *, suffix: str = "…") -> str:
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + suffix

    def _team_digest(
        self,
        team,
        directs: list[dict],
        subs: list[dict],
        chats: Optional[list[dict]] = None,
        *,
        is_lead: bool,
        reader: str = "",
    ) -> tuple[str, list[dict]]:
        """Coalesce one queue batch into one wake message. Deterministic, computed
        by code — the model does judgment, not arithmetic. Returns (model text,
        structured rows) — the rows ride the display sidecar so the GUI renders a
        collapsed BoardWakeCard instead of re-parsing prose."""
        clamp = lambda text: self._clamp(  # noqa: E731 — two-site local shorthand
            text, self.DIGEST_CLAMP_MODEL, suffix=" … (full text on the board)"
        )
        lines: list[str] = []
        rows: list[dict] = []
        for event in directs + subs:
            item_id = event.get("item_id")
            payload = event.get("payload") or {}
            item = None
            if item_id is not None:
                try:
                    item = self.team_store.get_item(team.space, int(item_id))
                except Exception:
                    item = None
            title = f"#{item_id} {item['title']}" if item else f"#{item_id}"
            row = {
                "item": item_id,
                "title": item["title"] if item else "",
                "actor": event.get("actor", ""),
            }
            if event["kind"] == "item_assigned":
                if item is None:
                    continue
                assignee = payload.get("assignee") or ""
                if payload.get("claimed"):
                    # A self-claim surfacing in the lead's subscription feed —
                    # supervision by exception, not an assignment to the reader.
                    lines.append(
                        f"{event['actor']} claimed {title} — it's theirs now;"
                        " reassign or cancel if that's wrong."
                    )
                    rows.append({**row, "kind": "claimed"})
                    continue
                if reader and payload.get("previous") == reader and assignee != reader:
                    # The reader just LOST this item — its interest ends here.
                    lines.append(
                        f"{title} was reassigned to {assignee} by {event['actor']}"
                        " — stop any work on it; hand off context via a comment"
                        " if useful."
                    )
                    rows.append({**row, "kind": "assigned", "assignee": assignee})
                    continue
                if reader and assignee != reader:
                    # Someone else's assignment surfacing in a broader feed.
                    lines.append(f"{title} assigned to {assignee} by {event['actor']}")
                    rows.append({**row, "kind": "assigned", "assignee": assignee})
                    continue
                lines.append(
                    f"You've been assigned work item {title}.\n"
                    f"  Done when: {item['criteria']}"
                    + (f"\n  Details: {item['description']}" if item["description"] else "")
                )
                rows.append({**row, "kind": "assigned", "assignee": assignee})
            elif event["kind"] == "item_transitioned":
                to = payload.get("to", "?")
                comment = clamp(payload.get("comment") or "")
                note = f" — “{comment}”" if comment else ""
                if to == "canceled" and not is_lead:
                    lines.append(
                        f"{title} was CANCELED by {event['actor']}{note} — stop any"
                        " work on it and pick up your other assignments."
                    )
                else:
                    lines.append(f"{title} moved to {to} by {event['actor']}{note}")
                rows.append(
                    {
                        **row,
                        "kind": "moved",
                        "to": to,
                        "note": self._clamp(
                            payload.get("comment") or "", self.DIGEST_CLAMP_UI
                        ),
                    }
                )
            elif event["kind"] == "item_created":
                lines.append(f"New item filed by {event['actor']}: {title}")
                rows.append({**row, "kind": "filed"})
            elif event["kind"] == "item_commented":
                lines.append(
                    f"Comment on {title} by {event['actor']}:"
                    f" {clamp(payload.get('body', ''))}"
                )
                rows.append(
                    {
                        **row,
                        "kind": "comment",
                        "note": self._clamp(
                            payload.get("body") or "", self.DIGEST_CLAMP_UI
                        ),
                    }
                )
        for chat in chats or []:
            who = chat["author"] if chat["author_role"] != "user" else "[User]"
            lines.append(f"# team chat — {who}: {clamp(chat['text'])}")
            rows.append(
                {
                    "kind": "chat",
                    "actor": who,
                    "note": self._clamp(chat["text"], self.DIGEST_CLAMP_UI),
                }
            )
        body = "\n".join(f"- {line}" for line in lines) or "- (no detail)"
        if is_lead:
            message = (
                "⏰ Board wake — your team needs decisions:\n"
                + body
                + "\n\nFull hand-off comments live on the board (get_item)."
                " Verify review items against their acceptance criteria (then"
                " done, or send back with a comment), unblock or reassign blocked"
                " items, and triage new filings. Steer only where needed."
            )
        else:
            message = (
                "[Lead] Board update:\n"
                + body
                + self._roster_note(team)
                + "\n\nMove your item to in_progress when you start; blocked (with a"
                " comment) if stuck; review with a hand-off comment when finished."
                " Journal evidence as you go."
            )
        return message, rows

    @staticmethod
    def _roster_note(team) -> str:
        """Teammate awareness as a mechanism: every worker digest carries the
        roster, so tagging teammates never depends on the lead remembering to
        introduce them."""
        if not team.workers:
            return ""
        mates = "; ".join(
            f"{w.actor} ({w.persona}" + (f" — {w.reason})" if w.reason else ")")
            for w in team.workers
        )
        reach = (
            " Reach them or the lead with @name in # team chat (post_chat)."
            if team.chat_enabled
            else " Coordinate through item comments; the lead reads the board."
        )
        return f"\n\nYour team: {mates}; lead (coordinator).{reach}"

    @staticmethod
    def _board_source(
        team, message: str, *, rows: Optional[list[dict]] = None
    ) -> dict[str, Any]:
        """Display-only MessageSource sidecar for board deliveries — the same
        mechanism connector messages use, so the GUI renders a structured card
        instead of a fake user bubble (owner ask 2026-08-16). The framed message
        stays the model-facing text; this only shapes presentation. `rows` are
        the digest's structured events — the BoardWakeCard renders those
        (collapsed to one line by default) instead of re-parsing the prose."""
        return {
            "connector": "board",
            "kind": "channel",
            "channel_id": team.space,
            "channel_name": "Team board",
            "sender_id": "board",
            "sender_name": "Board",
            "ts": time.time(),
            "text": message,
            "board": {"rows": rows or []},
        }

    def team_staleness_digest(self, session_id: str) -> str:
        """Attached to a lead's TIMER wakes: pure code over the board — a
        nothing's-wrong wake is one cheap glance, never a re-survey. Scoped by
        role membership: sessions with no team role get nothing."""
        team = self.teams.for_lead_session(session_id)
        if team is None:
            return ""
        try:
            items = self.team_store.list_items(team.space, self._user_actor())
        except Exception:
            return ""
        by_state: dict[str, int] = {}
        for item in items:
            by_state[item["state"]] = by_state.get(item["state"], 0) + 1
        unassigned = sum(
            1 for i in items if i["state"] == "open" and not i["assignee"]
        )
        parts = [f"{n} {state}" for state, n in sorted(by_state.items())]
        lines = [f"Board: {', '.join(parts) or 'empty'}."]
        if unassigned:
            lines.append(f"{unassigned} open item(s) have no assignee.")
        reviews = [i for i in items if i["state"] == "review"]
        if reviews:
            lines.append(
                "Awaiting your review: "
                + ", ".join(f"#{i['id']} {i['title']}" for i in reviews[:5])
            )
        blocked = [i for i in items if i["state"] == "blocked"]
        if blocked:
            lines.append(
                "Blocked: " + ", ".join(f"#{i['id']} {i['title']}" for i in blocked[:5])
            )
        return "\n".join(lines)
