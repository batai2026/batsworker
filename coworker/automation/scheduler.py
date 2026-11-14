"""The scheduler loop — runs in the always-on server.

Policy (agreed): **run-once-catch-up** for runs missed while down (due tasks fire once on
startup, then resume), and **skip-on-overlap** (don't stack a run if the previous is still
going). The actual execution is injected as `runner(task, trigger) -> TaskRun` so this stays
independent of the engine/manager.

Standing goals (triggers.py): an enabled task carrying a ``trigger`` never appears in
``due()``; each tick evaluates its condition (via the injected ``event_context`` readers)
and fires it on a RISING EDGE, re-firing at most once per cooldown while the condition
stays met. Edge/cooldown/baseline state persists on the task's ``trigger_state``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from .models import ScheduledTask, TaskRun
from .store import TaskStore
from .triggers import EventTrigger, evaluate

logger = logging.getLogger("coworker.automation")

Runner = Callable[[ScheduledTask, str], Awaitable[TaskRun]]
EventContext = Callable[[], dict]


class Scheduler:
    def __init__(
        self,
        store: TaskStore,
        runner: Runner,
        *,
        tick_seconds: float = 30.0,
        extra_tick: Optional[Callable[[], Awaitable[None]]] = None,
        event_context: Optional[EventContext] = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.tick_seconds = tick_seconds
        # An extra per-tick coroutine (self-wake resumption: resume sessions whose wakes are due).
        self.extra_tick = extra_tick
        # Readers for event-trigger conditions ({board_items, inbox_pending}) — injected by
        # the manager so this module never touches the teams/inbox stores directly.
        self.event_context = event_context
        self._task: Optional[asyncio.Task] = None
        self._running_ids: set[str] = set()  # overlap guard
        self._spawned: set[asyncio.Task] = set()  # keep spawned runs referenced

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # In-flight runs died with the loop before they were spawned; keep that shutdown
        # contract now that they're independent tasks (a suspended run must not outlive us).
        for spawned in list(self._spawned):
            spawned.cancel()
            try:
                await spawned
            except asyncio.CancelledError:
                pass
        self._spawned.clear()

    async def _loop(self) -> None:
        # First pass = run-once-catch-up for anything missed while the server was down.
        try:
            await self._tick(trigger="catchup")
        # Loop boundary: one failed catch-up pass must not stop the scheduler from running the
        # next tick; the error is logged and the loop continues.
        except Exception:
            logger.exception("scheduler catch-up failed")
        while True:
            await asyncio.sleep(self.tick_seconds)
            try:
                await self._tick(trigger="schedule")
            except Exception:
                logger.exception("scheduler tick failed")

    async def _tick(self, *, trigger: str) -> None:
        for task in self.store.due():
            # Spawn, don't await: a run can suspend on a parked approval (standing
            # scoped approvals, §25) and one blocked automation must never stall the
            # scheduler loop, other due tasks, or self-wake resumption. Overlap is
            # still guarded inside run_task via _running_ids.
            spawned = asyncio.create_task(self.run_task(task, trigger=trigger))
            self._spawned.add(spawned)
            spawned.add_done_callback(self._spawned.discard)
        for task, detail in self._event_due():
            spawned = asyncio.create_task(
                self.run_task(task, trigger="event", trigger_detail=detail)
            )
            self._spawned.add(spawned)
            spawned.add_done_callback(self._spawned.discard)
        if self.extra_tick is not None:
            try:
                await self.extra_tick()
            except Exception:
                logger.exception("scheduler extra_tick (wake resume) failed")

    def _event_due(self) -> list[tuple[ScheduledTask, str]]:
        """Evaluate every standing goal; return (task, detail) for the ones that
        should fire NOW (rising edge, or cooldown elapsed while still met). The
        edge/cooldown/baseline bookkeeping persists on each task's record whether
        or not it fires."""
        if self.event_context is None:
            return []
        try:
            ctx = self.event_context() or {}
        # Callback boundary: the host supplies the event context; a broken provider means "no
        # events this tick", not "stop the scheduler".
        except Exception:
            logger.exception("event context provider failed")
            return []
        out: list[tuple[ScheduledTask, str]] = []
        now = time.time()
        for task in self.store.event_tasks():
            if task.id in self._running_ids:  # skip-on-overlap, same as cron
                continue
            try:
                trigger = EventTrigger.from_dict(task.trigger)
                if trigger is None:
                    continue
                met, detail, snapshot = evaluate(
                    trigger, task=task, ctx=ctx, state=dict(task.trigger_state or {}), now=now
                )
            # Per-task boundary: one standing goal with a broken trigger expression must not
            # stop the other tasks from being evaluated this tick.
            except Exception:
                logger.exception("event evaluation failed for %s", task.id)
                continue
            state = dict(task.trigger_state or {})
            state.update(snapshot)
            prev_met = bool(state.get("met"))
            last_fired = float(state.get("last_fired_at") or 0.0)
            should_fire = met and ((not prev_met) or (now - last_fired >= trigger.cooldown_seconds))
            state["met"] = met
            state["last_detail"] = detail
            if should_fire:
                state["last_fired_at"] = now
            task.trigger_state = state
            self.store.save(task)  # persists edge/cooldown even when not firing
            if should_fire:
                out.append((task, detail))
        return out

    async def run_task(
        self, task: ScheduledTask, *, trigger: str, trigger_detail: str = ""
    ) -> Optional[TaskRun]:
        if task.id in self._running_ids:  # skip-on-overlap
            logger.info("skipping %s — previous run still going", task.id)
            return None
        # Re-check due-ness against the CURRENT row: `_tick` snapshots due tasks
        # and spawns them; if the previous run finished inside that same tick
        # window (and advanced next_run), the queued spawn is stale and must not
        # fire a duplicate run. Manual runs and event-triggered tasks are exempt
        # (their firing isn't gated by next_run).
        if trigger in ("schedule", "catchup") and task.id not in {
            t.id for t in self.store.due()
        }:
            logger.info("skipping %s — no longer due", task.id)
            return None
        self._running_ids.add(task.id)
        try:
            run = await self.runner(task, trigger, trigger_detail=trigger_detail)
        # Task boundary: the runner starts a whole agent turn — providers, tools, approvals. Its
        # failure is recorded as this run's error; swallowing the type here is what keeps one
        # bad task from taking the scheduler with it.
        except Exception as exc:
            logger.exception("task %s run failed", task.id)
            run = TaskRun(
                task_id=task.id, status="error", error=str(exc), trigger=trigger
            )
            self.store.add_run(run)
        finally:
            self._running_ids.discard(task.id)
        # advance the task (run_count/last_run) → save recomputes next_run.
        fresh = self.store.get(task.id)
        if fresh is not None:
            fresh.run_count += 1
            fresh.last_run = run.started_at if run else None
            fresh.last_status = run.status if run else "error"
            self.store.save(fresh)
        return run
