"""Sessions cluster split out of manager.py (manager-split refactor).

Mixin methods only; shared state lives on the SessionManager facade (self.*). Module-level
imports are limited to what the moved code references; everything else resolves via self.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from ..engine import TurnEngine
from ..observability import to_otlp_json
from ..sessions import SessionRecord

logger = logging.getLogger("coworker.manager")


class SessionsMixin:
    def session_timeline(self, session_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        return self.trace_store.session_spans(session_id, limit=limit)

    def export_trace_otlp(self, session_id: str) -> dict[str, Any]:
        """Push the session's spans to the configured OTLP/HTTP-JSON endpoint.
        Double opt-in: the endpoint must be configured AND the export called."""
        from ..config import load_config

        endpoint = (load_config().otlp_endpoint or "").strip()
        if not endpoint:
            return {"ok": False, "error": "no OTLP endpoint configured (otlp_endpoint)"}
        spans = self.trace_store.session_spans(session_id)
        if not spans:
            return {"ok": False, "error": "no spans recorded for this session"}
        import httpx

        try:
            resp = httpx.post(
                endpoint.rstrip("/") + "/v1/traces",
                json=to_otlp_json(spans),
                headers={"Content-Type": "application/json"},
                timeout=20,
            )
            resp.raise_for_status()
        except Exception as exc:
            return {"ok": False, "error": f"OTLP export failed: {exc}"}
        return {"ok": True, "exported": len(spans)}

    def list_checkpoints(self, session_id: str) -> dict[str, Any]:
        engine = self._engines.get(session_id)
        if engine is not None and getattr(engine, "checkpoints", None) is not None:
            return {"turns": engine.checkpoints.list_turns()}
        # No live engine (restart, eviction): read the persisted turn list —
        # rollback/list never need a live engine, only capture does.
        return {"turns": self._checkpoints_reader(session_id).list_turns()}

    def rollback_checkpoint(self, session_id: str, seq: int) -> dict[str, Any]:
        if self.is_running(session_id):
            return {
                "ok": False,
                "error": "the session is running — wait for the turn to finish",
            }
        from ..checkpoints import CheckpointBusy, CheckpointNotFound

        engine = self._engines.get(session_id)
        if engine is not None and getattr(engine, "checkpoints", None) is not None:
            cp = engine.checkpoints
        else:
            cp = self._checkpoints_reader(session_id)
        try:
            result = cp.rollback_to(int(seq))
        except CheckpointBusy:
            return {"ok": False, "error": "a turn is mid-flight — wait for it to finish"}
        except CheckpointNotFound:
            return {"ok": False, "error": f"no checkpoint #{seq} for this session"}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **result}

    def _checkpoints_reader(self, session_id: str) -> "SessionCheckpoints":  # noqa: F821
        from ..checkpoints import SessionCheckpoints

        return SessionCheckpoints(
            session_id=session_id,
            state_dir=self.data_dir,
            roots_getter=lambda: [],
            workspace_root=Path("."),
        )

    def _emit_session_created(self, session_id: str, persona_id: str) -> None:
        """Phase 5 telemetry, fired once per brand-new session on a background thread
        (never blocks session start). cloud.emit_session_created is a hard no-op when
        signed out or opted out, and sends only content-free facts."""
        import threading

        from .. import cloud
        from ..config import load_config

        entry = self.personas.get(persona_id)
        # Wire fields kept stable; both now carry the workspace shape ("folder" = gated
        # primary folder, "scratch" = starts on the per-session scratch dir).
        kind = ("folder" if entry.requires_folder else "scratch") if entry else ""
        family = kind
        workspace_kind = kind

        def _send() -> None:
            try:
                cloud.emit_session_created(
                    self.secrets,
                    load_config(),
                    session_id=session_id,
                    persona_id=persona_id,
                    persona_family=family,
                    workspace_kind=workspace_kind,
                )
            except Exception:
                pass  # telemetry must never surface as a session error

        threading.Thread(target=_send, daemon=True).start()

    def persist_session(self, session_id: str) -> None:
        """Save the cached engine's thread (so a prompt's pending tool call survives a crash)."""
        engine = self._engines.get(session_id)
        if engine is not None:
            self.save(session_id, engine)

    def mark_running(self, session_id: str) -> None:
        self._running_sessions.add(session_id)

    def try_mark_running(self, session_id: str) -> bool:
        """Atomically claim an idle session for one turn on the server event loop."""
        if session_id in self._running_sessions:
            return False
        self._running_sessions.add(session_id)
        return True

    def mark_idle(self, session_id: str) -> None:
        self._running_sessions.discard(session_id)
        # Every turn path (WS, background delivery, durable resume) marks idle when it
        # finishes — the one shared post-turn moment, so auto-titling hooks in here and
        # can never add latency to the response itself.
        self._maybe_autotitle(session_id)
        # Team sessions: a finished turn is the moment new board events exist (an
        # assign, a review transition) — kick the queue drain now instead of waiting
        # for the next scheduler tick. Cheap no-op for teamless sessions.
        if self.teams.for_lead_session(session_id):
            self._team_last_alive[session_id] = time.time()
        if self._loop is not None and (
            self.teams.for_lead_session(session_id)
            or self.teams.for_worker_session(session_id)
        ):
            asyncio.run_coroutine_threadsafe(self.team_tick(), self._loop)
        if session_id in self._promotion_rebuild:
            # Promotion happened this turn: drop the cached engine so the next turn
            # rebuilds with the new primary (relative anchoring, env snapshot, git).
            self._promotion_rebuild.discard(session_id)
            self._engines.pop(session_id, None)

    def is_running(self, session_id: str) -> bool:
        return session_id in self._running_sessions

    # -- LLM auto-titles (FB-010) -------------------------------------------------
    _AUTOTITLE_PROMPT = (
        "You title chat sessions. Given the user's opening message(s) — and, when "
        "present, the assistant's first reply for context — reply with ONLY a 4-5 word "
        "title for the session, named after what the session is actually about — no "
        "quotes or punctuation wrapping it. If there is no topic at all ("
        '"hey", "how are you", "hi there" and a generic reply), reply with exactly: '
        "small-talk"
    )

    def _maybe_autotitle(self, session_id: str) -> None:
        """Kick off title generation after a turn completes, fire-and-forget. Only while
        the session has neither a manual rename nor a generated title, at most twice:
        attempt 1 rides turn 1, and the second window exists solely for the small-talk
        retry (with both openers). Attempts are counted in memory rather than derived
        from the user-message count — steering injections also land as role "user", and
        counting them would silently suppress titling on a steered first turn. A restart
        forgetting the counter is harmless: renamed/auto_title still gate re-titling."""
        if session_id.startswith("__"):
            return
        engine = self._engines.get(session_id)
        if engine is None or session_id in self._autotitle_inflight:
            return
        if self.task_store.task_for_run_session(session_id) is not None:
            return  # automation runs are titled by their task
        # Three windows, not two (owner ruling 2026-08-24): opener-only at turn 1 start,
        # opener+assistant-reply at turn 1 end (titles a "hey"-then-real-work session),
        # and both-openers at turn 2 start. The signature guard makes each fire at most
        # once; sessions with a meaty first message still title on attempt 1.
        if self._autotitle_attempts.get(session_id, 0) >= 3:
            return
        users = [m for m in engine.messages if m.get("role") == "user"]
        if not users:
            return
        state = self.session_store.title_state(session_id)
        if state is None or state["renamed"] or state["auto_title"]:
            return
        from ..attachments import content_to_text

        openers = [
            text
            for m in users
            if (text := content_to_text(m.get("content"), image_placeholder="").strip())
        ][:2]
        if not openers:
            return
        # The agent's first reply is fair evidence for a TITLE (unlike the reviewer,
        # naming a session is not a security boundary — owner ruling 2026-08-24): it is
        # what turns "hey" + a generic ask into "Semgrep security review".
        assistant = next(
            (
                text
                for m in engine.messages
                if m.get("role") == "assistant"
                and (
                    text := content_to_text(
                        m.get("content"), image_placeholder=""
                    ).strip()
                )
            ),
            "",
        )[:400]
        # Same evidence as the last attempt → nothing new to say; skip WITHOUT burning an
        # attempt (this is how the turn-start and turn-end triggers coexist).
        sig = (len(openers), bool(assistant))
        if self._autotitle_sig.get(session_id) == sig:
            return
        self._autotitle_sig[session_id] = sig
        self._autotitle_attempts[session_id] = (
            self._autotitle_attempts.get(session_id, 0) + 1
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop to ride (sync caller) — skip, never block
        self._autotitle_inflight.add(session_id)
        # Retain the task: the loop holds only a weak ref, and a GC'd task would both
        # kill the title mid-flight and strand the inflight guard.
        task = loop.create_task(
            self._generate_autotitle(session_id, engine, openers, assistant)
        )
        self._autotitle_tasks.add(task)
        task.add_done_callback(self._autotitle_tasks.discard)

    async def _generate_autotitle(
        self,
        session_id: str,
        engine: TurnEngine,
        openers: list[str],
        assistant: str = "",
    ) -> None:
        """One cheap non-streaming completion on the session's own provider/model. Every
        failure (provider error, empty, absurdly long) is swallowed — the title_from
        fallback stays; the small-talk sentinel leaves auto_title unset so the turn-2
        retry can run."""
        try:
            turn = await asyncio.to_thread(
                engine.provider.complete,
                model=engine.model,
                messages=[
                    {"role": "system", "content": self._AUTOTITLE_PROMPT},
                    {
                        "role": "user",
                        "content": "\n\n".join(openers)
                        + (
                            f"\n\n[the assistant's first reply]\n{assistant}"
                            if assistant
                            else ""
                        ),
                    },
                ],
                temperature=0.2,
                # Reasoning-routed models spend hidden tokens BEFORE emitting text; a
                # tight cap plus default effort yields an empty completion and a silent
                # no-op. Effort "none" reaches only the OpenAI-compat path (the native
                # providers whitelist their settings), and 64 leaves headroom either way.
                max_tokens=64,
                reasoning_effort="none",
            )
            raw = (getattr(turn, "text", None) or "").strip()
            # Sanitize: surrounding quotes off, whitespace collapsed, capped at 60.
            title = " ".join(raw.strip("\"'“”‘’`").split())
            # Sentinel tolerance: models riff on the exact token ("Small talk.", quoted,
            # trailing period) — normalize before comparing, else the riff becomes the title.
            if title.lower().strip(".!,;:'\"").replace(" ", "-").replace("_", "-") in (
                "small-talk",
                "smalltalk",
            ):
                return
            if not title or len(title) > 80:
                return
            if self.session_store.set_auto_title(session_id, title[:60]):
                # Best-effort nudge for any live viewer; the sidebar's poll and
                # post-turn refresh pick the new title up regardless.
                await self.broadcast_session(
                    session_id,
                    {
                        "type": "session_title",
                        "data": {"session_id": session_id, "title": title[:60]},
                    },
                )
        except Exception:
            # A failed title must never surface as a session error — but it must
            # not be invisible either (a silent provider 400 hid the max_tokens
            # rejection for a whole owner test pass, 2026-07-20).
            # warning, not debug: debug was invisible in packaged builds, which re-hid
            # exactly the class of failure this comment warns about (2026-08-24: the plan
            # backend 400-ing on max_output_tokens went unseen for a whole test pass).
            logger.warning("autotitle failed for %s", session_id, exc_info=True)
        finally:
            self._autotitle_inflight.discard(session_id)

    def session_messages(self, session_id: str) -> list[dict[str, Any]]:
        # A live engine's in-memory thread is authoritative: mid-turn it's ahead of the
        # persisted record — which may not even exist yet for a scheduled run's first turn
        # (opening a "running" automation showed a blank session; owner report 2026-07-04).
        engine = self._engines.get(session_id)
        if engine is not None:
            return list(engine.messages)
        record = self.session_store.load(session_id)
        return record.messages if record else []

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be renamed"}
        ok = self.session_store.rename(session_id, title)
        return {
            "ok": ok,
            "session_id": session_id,
            "title": " ".join((title or "").split())[:120],
        }

    def set_session_flags(
        self,
        session_id: str,
        *,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
    ) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be modified here"}
        ok = self.session_store.set_flags(session_id, pinned=pinned, archived=archived)
        return {"ok": ok, "session_id": session_id}

    def delete_session(self, session_id: str) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be deleted here"}
        engine = self._engines.pop(session_id, None)
        if engine is not None:
            try:
                # (was engine.interrupt() — a method that never existed; the AttributeError
                # was silently swallowed, so deleting a running session never stopped it.)
                engine.request_interrupt()
            except Exception:
                pass
        record = self.session_store.load(session_id)
        ok = self.session_store.delete(session_id)
        # Deleting a session is the one implicit unsubscribe (otherwise subscriptions are permanent).
        self.subscriptions.remove_session(session_id)
        # ...and releases any Slack threads it owned (§31): the next tag there spawns fresh.
        self.mention_sessions.remove_session(session_id)
        # ...and drops its per-session connector overrides (§4.2, like subscriptions).
        self.session_connections.remove_session(session_id)
        # ...and its per-session skill mutes (SKILLS-SPEC §3 — mutes die with the session).
        self.session_skills.remove_session(session_id)
        # ...and closes its pending Inbox items — an orphaned approval/question can never be
        # meaningfully answered (owner call, 2026-07-03).
        self.inbox.resolve_session(session_id)
        # ...and its scratch dir. STRICTLY scoped: only a directory inside scratch_base is
        # removed — a real project folder the user picked is never touched.
        if ok and record and record.workspace:
            scratch = self.scratch_base().resolve()
            ws = Path(record.workspace)
            try:
                resolved = ws.resolve()
                if (
                    resolved.is_relative_to(scratch)
                    and resolved != scratch
                    and resolved.is_dir()
                ):
                    shutil.rmtree(resolved)
            except OSError:
                pass  # a stale/foreign path must not fail the delete
        return {"ok": ok, "session_id": session_id}

    # -- read models ------------------------------------------------------------
    def list_sessions(self, workspace: Optional[str] = None) -> list[dict[str, Any]]:
        ws = self.resolve_workspace(workspace) if workspace else None
        return [
            {
                "session_id": r.session_id,
                "title": r.title or "New session",
                "workspace": r.workspace,
                "agent": r.agent,
                "model": r.model,
                "mode": r.mode,
                "updated_at": r.updated_at,
                "messages": r.message_count,
                "pinned": r.pinned,
                "archived": r.archived,
                # §31: non-user origin ("slack") + display label — drives the sidebar's
                # "From Slack" group and the row's platform icon.
                "origin": r.origin,
                "origin_label": r.origin_label,
                # Attention = Inbox items awaiting this session (the amber count that bubbles
                # session → persona → footer Inbox). Liveness = working (in-flight turn) /
                # sleeping (a self-wake is pending) / idle — a count-less dot that never bubbles.
                "attention": len(self.inbox.pending(session_id=r.session_id)),
                "liveness": self._session_liveness(r.session_id),
                # When sleeping: the next timer fire (ISO) — drives the "sleeping
                # until…" strip so a scheduled agent never reads as a dead one.
                "sleeping_until": self._sleeping_until(r.session_id),
                # Channels this session listens to (inbound subscriptions) — drives the per-session
                # "connections" indicator.
                "subscriptions": [
                    s.channel for s in self.subscriptions.for_session(r.session_id)
                ],
                # Agent teams: {} for plain sessions. Workers carry role/lead_session
                # (+ a computed current-item line); leads carry role/team_id — drives
                # the sidebar's ONE expandable team entry.
                "team": self._session_team_row(r),
            }
            for r in self.session_store.list(workspace=ws)
            if not r.session_id.startswith("__")  # hide internal threads
        ]

    def _session_team_row(self, record: SessionRecord) -> dict[str, Any]:
        info = record.team or {}
        if not info:
            return {}
        row = {
            "role": info.get("role", ""),
            "team_id": info.get("team_id", ""),
            "lead_session": info.get("lead_session", ""),
        }
        if info.get("role") == "lead":
            team = self.teams.get(str(info.get("team_id", "")))
            if team is not None and team.chat_enabled and team.chat_group:
                row["chat_enabled"] = True
                row["chat_unread"] = self.chat_store.unread_count(
                    team.chat_group, "user"
                )
        if info.get("role") == "worker" and info.get("space") and info.get("actor"):
            try:
                items = self.team_store.list_items(
                    str(info["space"]), self._user_actor(), assignee=str(info["actor"])
                )
            except Exception:
                items = []
            active = next(
                (
                    i
                    for state in ("blocked", "review", "in_progress", "open")
                    for i in items
                    if i["state"] == state
                ),
                None,
            )
            row["actor"] = info["actor"]
            row["current_item"] = (
                f"#{active['id']} {active['state'].replace('_', ' ')}" if active else "idle"
            )
            row["status"] = active["state"] if active else "idle"
        return row

    def _sleeping_until(self, session_id: str) -> Optional[str]:
        fires = [
            w.fire_at
            for w in self.wakes.pending(session_id)
            if w.kind == "timer" and w.fire_at
        ]
        return min(fires) if fires else None

    def _session_liveness(self, session_id: str) -> str:
        if self.is_running(session_id):
            return "working"
        if self.wakes.pending(session_id):
            return "sleeping"
        return "idle"
