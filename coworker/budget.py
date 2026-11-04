"""Token budget guard (v1) — ceilings that stop a turn before costs run away.

Three independent ceilings, checked in one place:
  - **daily**: ALL sessions on this machine, per calendar day
    (``config.daily_token_budget``).
  - **session**: one conversation, lifetime (``config.session_token_budget``).
  - **task**: one automation, lifetime (``task.budget_tokens`` on the
    ScheduledTask — set at creation or update; the run's opening message and
    the stop notice say so).

Enforcement lives in the engine's `_loop`: after each model round-trip the
engine records the turn's usage through :class:`BudgetGuard.record` and calls
:meth:`BudgetGuard.check`; a non-None reason string ends the turn gracefully
(notice + TURN_END "budget_exhausted") — mirroring the max-iterations rail, so
a parked approval or in-flight tool still finishes cleanly. The guard NEVER
raises into the turn: a broken budget store must not be a worse failure mode
than no budget at all.

Counting basis: every token the provider processed for the turn
(``input + output + cache_read + cache_write``) — billed-or-processed, never
estimated. Turns whose provider reported no usage simply don't count (the
frontend's usage chip has the same honesty rule).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional


def _today() -> str:
    return time.strftime("%Y-%m-%d")


class BudgetStore:
    """Thread-safe JSON accumulator: {daily: {date: tokens}, sessions: {sid: n},
    tasks: {tid: n}} under the state dir."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def record(self, *, session_id: str, task_id: Optional[str], tokens: int) -> None:
        if tokens <= 0:
            return
        with self._lock:
            data = self._load()
            day = _today()
            data.setdefault("daily", {})
            data["daily"][day] = int(data["daily"].get(day, 0)) + tokens
            data.setdefault("sessions", {})
            data["sessions"][session_id] = int(data["sessions"].get(session_id, 0)) + tokens
            if task_id:
                data.setdefault("tasks", {})
                data["tasks"][task_id] = int(data["tasks"].get(task_id, 0)) + tokens
            self._save(data)

    def day_total(self, day: Optional[str] = None) -> int:
        data = self._load()
        return int(data.get("daily", {}).get(day or _today(), 0))

    def session_total(self, session_id: str) -> int:
        return int(self._load().get("sessions", {}).get(session_id, 0))

    def task_total(self, task_id: str) -> int:
        return int(self._load().get("tasks", {}).get(task_id, 0))


class BudgetGuard:
    """The engine-facing object: records turn usage and returns a stop reason
    when a ceiling is exceeded. Attach post-construction (``engine.budget``),
    same pattern as checkpoints/compaction."""

    def __init__(
        self,
        store: BudgetStore,
        *,
        session_id: str,
        session_limit: Optional[int] = None,
        task_id: Optional[str] = None,
        task_limit: Optional[int] = None,
        daily_limit: Optional[int] = None,
    ) -> None:
        self.store = store
        self.session_id = session_id
        self.session_limit = session_limit
        self.task_id = task_id
        self.task_limit = task_limit
        self.daily_limit = daily_limit

    def record(self, tokens: int) -> None:
        try:
            self.store.record(
                session_id=self.session_id, task_id=self.task_id, tokens=tokens
            )
        except Exception:
            pass  # a broken budget store must never break the turn

    def check(self) -> Optional[str]:
        """A human-readable stop reason when a ceiling is exceeded, else None."""
        try:
            if self.task_limit and self.task_id:
                if self.store.task_total(self.task_id) >= self.task_limit:
                    return (
                        f"Task token budget reached ({self.task_limit} tokens for this "
                        "automation)."
                    )
            if self.session_limit and self.store.session_total(self.session_id) >= self.session_limit:
                return f"Session token budget reached ({self.session_limit} tokens)."
            if self.daily_limit and self.store.day_total() >= self.daily_limit:
                return f"Daily token budget reached ({self.daily_limit} tokens today)."
        except Exception:
            return None
        return None
