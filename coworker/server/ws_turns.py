"""The turn lifecycle for one session socket: claim, stream, finish.

`routes/ws.py` keeps the wire (handshake gates, frames, rate limits) and hands the actual
turn to `TurnRunner`. Two rules live here and are easier to see in one place than in a
800-line closure:

* **Claim before scheduling.** `try_mark_running` is atomic, so two back-to-back frames
  from the same socket cannot both start a turn; the loser gets a visible rejection
  instead of a silent no-op.
* **Finish always.** The `finally` marks the session idle, saves, and broadcasts
  `turn_done` even if the provider exploded — a socket that never sees `turn_done` leaves
  the GUI stuck in a waiting state with no Stop button.

Also here: the two per-session knobs that ride the same socket — the mid-session model
rebind (never mid-turn) and resolving the session's single pending prompt.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from ..engine import TurnEngine
from .manager import SessionManager

# Checkpoint events: persist mid-turn so a crash/quit can't eat the conversation.
# turn_start = the user message just landed (a brand-new session gets its row here,
# not at connect — empty never-used sessions shouldn't appear in Recents);
# permission_required/directory_requested = parked indefinitely on the user;
# iteration_end = a model response + its tool results completed.
CHECKPOINTS = {
    "turn_start",
    "permission_required",
    "directory_requested",
    "plan_proposed",
    "iteration_end",
}


class TurnRunner:
    """One connection's turn executor. `reject` is how a refusal reaches the wire."""

    def __init__(
        self,
        manager: SessionManager,
        session_id: str,
        engine: TurnEngine,
        *,
        reject: Callable[[str], Awaitable[None]],
    ) -> None:
        self.manager = manager
        self.session_id = session_id
        self.engine = engine
        self.reject = reject

    async def claim(self, *, retry: bool = False, content=None, display=None) -> None:
        if not self.manager.try_mark_running(self.session_id):
            await self.reject(
                "This session is already running a turn. Wait for it to finish or stop it."
            )
            return
        asyncio.create_task(self.run(content, retry=retry, display=display))

    async def run(self, content, *, retry: bool = False, display=None) -> None:
        # The receive loop atomically claims this session before scheduling the task.
        # Keeping the claim outside prevents two back-to-back frames from both starting.
        try:
            events = (
                self.engine.retry()
                if retry
                else self.engine.run(content, display=display)
            )
            async for event in events:
                # Broadcast to every socket viewing this session (this socket included — it's a
                # registered client), so a second view of the same session stays in sync too.
                await self.manager.broadcast_session(
                    self.session_id, {"type": event.type.value, "data": event.data}
                )
                if event.type.value in CHECKPOINTS:
                    self.manager.save(self.session_id, self.engine)
                if event.type.value == "turn_start":
                    # Title on the user's words the moment they land — never behind
                    # a long agentic turn (owner catch 2026-08-24).
                    self.manager._maybe_autotitle(self.session_id)
        finally:
            self.manager.mark_idle(self.session_id)
            self.manager.save(self.session_id, self.engine)
            await self.manager.broadcast_session(
                self.session_id, {"type": "turn_done", "data": {}}
            )

    def resolve_pending(self, resolution: str) -> None:
        # Live WS responses resolve THE session's single pending prompt (one at a time, since the
        # agent blocks). Reconnect / Inbox resolve by id via REST instead.
        pend = self.manager.inbox.pending(self.session_id)
        if pend:
            self.manager.inbox.resolve(pend[0].id, resolution)

    async def apply_model(self, model: Optional[str]) -> None:
        # Mid-session rebind is allowed (roadmap item 3, supersedes the 2026-07-04
        # lock): history is canonical and providers convert per call. A real switch
        # appends a persisted notice; broadcast it so live views render the marker
        # and update their header. Never rebind mid-turn — the running loop reads
        # `engine.model` per iteration and a mixed turn is exactly the breakage the
        # old lock existed to prevent.
        if not model or self.manager.is_running(self.session_id):
            return
        notice = self.engine.switch_model(model)
        if notice is None:  # same model, or first bind on a fresh session
            return
        self.manager.persist_session(self.session_id)
        await self.manager.broadcast_session(
            self.session_id,
            {"type": "model_changed", "data": {"model": model, "text": notice}},
        )
