"""Scheduler cluster split out of manager.py (manager-split refactor).

Mixin methods only; shared state lives on the SessionManager facade (self.*). Module-level
imports are limited to what the moved code references; everything else resolves via self.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from ..automation import Schedule, ScheduledTask, TaskRun
from ..budget import BudgetGuard
from .manager_helpers import _epoch, _last_assistant_text, _recent_files

logger = logging.getLogger("coworker.manager")


class SchedulerMixin:
    # -- self-wake resumption ---------------------------------------------------
    async def _scheduler_tick(self) -> None:
        """The shared per-tick work: resume due self-wakes, then drain team queues.
        Team deliveries dispatch as tasks (a long worker turn must not stall the
        scheduler)."""
        await self.resume_due_wakes()
        try:
            await self.team_tick()
        except Exception:
            logger.exception("team tick failed")

    async def resume_due_wakes(self) -> int:
        """Resume sessions whose self-wakes are due (called each scheduler tick). A suspended
        agent (it called sleep_until / wake_on / wake_on_event and ended its turn) is re-invoked on
        its own session with a wake message so it continues where it left off. Returns the count.
        """
        resumed = 0
        for wake in self.wakes.due():
            try:
                await self._resume_wake(wake)
                resumed += 1
            except Exception:
                pass
            finally:
                self.wakes.mark_fired(wake.id)
        return resumed

    async def _resume_wake(self, wake) -> None:
        message = self._wake_message(wake)
        # A lead's timer wake carries the staleness digest — pure code over the
        # board, scoped by role membership (teamless sessions get a bare wake).
        digest = self.team_staleness_digest(wake.session_id)
        if digest:
            message = f"{message}\n\n{digest}"
        await self.deliver_to_session(wake.session_id, message)

    async def _run_scheduled_task(
        self, task, trigger: str, trigger_detail: str = ""
    ) -> TaskRun:
        run = TaskRun(
            task_id=task.id, trigger=trigger
        )  # __post_init__ sets run.session_id
        self.task_store.add_run(run)  # mark "running"
        # UX-026: tell every open app window a SCHEDULED run just started (the 5s
        # top-right toast). Manual runs never come through here — the user is
        # already watching those live.
        await self.broadcast_event(
            {
                "type": "automation_run_started",
                "data": {
                    "task_id": task.id,
                    "task_title": task.title,
                    "session_id": run.session_id,
                    "workspace": task.workspace,
                    "agent": task.agent,
                    "trigger": trigger,
                    "trigger_detail": trigger_detail,
                },
            }
        )
        # Each run is a real, persisted conversation thread: it runs the instructions under its
        # own session id, then saves the transcript. The user can reopen that session and ask a
        # follow-up — the scheduled agent is no longer fire-and-forget.
        engine = self._build_task_engine(task, session_id=run.session_id)
        # Budget guard (budget.py): an automation with a token ceiling stops
        # gracefully at a clean turn boundary instead of running away.
        if task.budget_tokens:
            from ..config import load_config

            engine.budget = BudgetGuard(
                self.budget_store,
                session_id=run.session_id,
                task_id=task.id,
                task_limit=int(task.budget_tokens),
                daily_limit=load_config().daily_token_budget,
            )
        # Register the live engine up-front: a parked approval persists the session
        # mid-run (durable suspend), and resolving from the Inbox must find this engine.
        self._engines[run.session_id] = engine
        # The first turn is the task itself. The framing matters: instructions often restate the
        # schedule ("every day at 5:32pm…"), so make explicit that the schedule already fired and
        # the job now is to execute, not to (re)schedule. Event-triggered runs say
        # WHICH condition fired (facts only — path/state, never untrusted contents)
        # so the agent knows why it woke.
        if trigger == "event":
            header = f"🎯 Event-triggered run — {task.title}"
            reason = f"\n\nTrigger: {trigger_detail}." if trigger_detail else ""
            opening = (
                f"{header}{reason}\n\n"
                "This standing goal just fired: carry out the task below immediately and "
                "produce the result. The goal already exists — do not create or modify any "
                f"scheduled tasks.\n\n{task.instructions}"
            )
        else:
            opening = (
                f"⏰ Scheduled run — {task.title}\n\n"
                "This automation is due now: carry out the task below immediately and produce the "
                "result. The schedule already exists — do not create or modify any scheduled tasks.\n\n"
                f"{task.instructions}"
            )
        try:
            async for _event in engine.run(opening):
                pass
            run.result_text = _last_assistant_text(engine.messages)
            run.artifacts = _recent_files(task.workspace, since=run.started_at)
            run.status = "ok"
            if task.notify_on_completion:
                await self._notify_task_done(task, run)
        except Exception as exc:
            run.status, run.error = "error", str(exc)
        finally:
            run.finished_at = _epoch()
            # Persist the run as a continuable session + keep the live engine for an immediate
            # follow-up; record the run (now carrying its session_id).
            try:
                self.save(run.session_id, engine)
                self._engines[run.session_id] = engine
            except Exception:
                pass
            self.task_store.add_run(run)
        return run

    async def _notify_task_done(self, task, run: TaskRun) -> None:
        summary = (run.result_text or "").strip()[:280]
        # Notify any socket viewing this scheduled run's session (it's a durable session of its own).
        await self.broadcast_session(
            run.session_id,
            {
                "type": "task_done",
                "data": {
                    "task": task.title,
                    "id": task.id,
                    "text": summary,
                    "run_id": run.run_id,
                },
            },
        )
        if task.notify_target:
            from ..connectors.base import parse_target
            from ..connectors.senders import DEFAULT_SENDERS

            try:
                platform, chat_id, thread = parse_target(task.notify_target)
                sender = DEFAULT_SENDERS.get(platform)
                creds = self.secrets.get(f"{platform}:default") or {}
                if sender and creds.get("bot_token"):
                    await asyncio.to_thread(
                        sender,
                        creds["bot_token"],
                        chat_id,
                        f"✓ {task.title}\n\n{summary}",
                        thread,
                    )
            except Exception:
                pass

    # -- automation REST --------------------------------------------------------
    def list_automations(self) -> dict[str, Any]:
        # Unseen = runs started after the task's seen mark (UX-023 sidebar badges).
        # `unseen_failed` tints the badge when the NEWEST unseen run errored.
        tasks = []
        for t in self.task_store.list():
            unseen = [
                r for r in self.task_store.runs(t.id) if r.started_at > t.seen_runs_at
            ]
            tasks.append(
                {
                    **t.public(),
                    "unseen_runs": len(unseen),
                    "unseen_failed": bool(unseen) and unseen[0].status == "error",
                }
            )
        return {"tasks": tasks}

    def mark_automation_seen(self, task_id: str) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        task.seen_runs_at = time.time()
        self.task_store.save(task)
        return {"ok": True}

    def get_automation(self, task_id: str) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"error": "not found"}
        return {
            "task": task.public(),
            "runs": [r.to_dict() for r in self.task_store.runs(task_id)],
        }

    def create_automation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an automation directly from the GUI (the "New automation" / template flow).
        Mirrors the agent-facing `create_scheduled_task` validation, but binds the task to a
        fresh per-task scratch workspace instead of an origin conversation's folder."""
        from croniter import croniter

        title = (payload.get("title") or "").strip()
        instructions = (payload.get("instructions") or "").strip()
        cron = (payload.get("cron") or "").strip() or None
        fire_at = (payload.get("fire_at") or "").strip() or None
        timezone = (payload.get("timezone") or "").strip() or "local"

        if not title:
            return {"ok": False, "error": "title is required"}
        if not instructions:
            return {"ok": False, "error": "instructions are required"}
        if not cron and not fire_at:
            return {
                "ok": False,
                "error": "provide a cron (recurring) or a fire_at ISO datetime (one-time)",
            }
        if cron and not croniter.is_valid(cron):
            return {"ok": False, "error": f"invalid cron expression: {cron}"}

        schedule = Schedule(
            kind="once" if (fire_at and not cron) else "cron",
            cron=cron,
            fire_at=fire_at,
            timezone=timezone,
        )
        from ..automation.models import grant_entries

        try:
            budget = int(payload["budget_tokens"]) if payload.get("budget_tokens") is not None else None
        except (TypeError, ValueError):
            return {"ok": False, "error": "budget_tokens must be a number"}
        task = ScheduledTask(
            title=title,
            instructions=instructions,
            schedule=schedule,
            workspace="",
            origin_surface="cowork",
            agent="cowork",
            # Human-driven path (GUI form / onboarding recipes): the creating surface
            # rendered the grants, the submit IS the consent. Same validation as the
            # agent tool — only target-bound write grants survive.
            always_allowed_tools=grant_entries(payload.get("permissions")),
            budget_tokens=budget,
        )
        task.workspace = self._provision_scratch(task.task_session_id)
        self.task_store.save(task)
        return {"ok": True, "task": task.public()}

    # -- automation template marketplace ------------------------------------------
    def list_automation_templates(self) -> dict[str, Any]:
        from ..automation.templates import template_catalog

        return {"templates": template_catalog()}

    def instantiate_automation_template(
        self, key: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Create an automation from a marketplace template.

        Renders the template's instructions with the user's configuration
        (repo/channel/deliver), assembles a cron from the chosen day/time, and
        routes through the same validation as the manual form. Write recipes
        carry a standing ``send_message`` grant to the chosen channel — the
        template card's consent line IS the user's consent, same as quickstart.
        """
        from ..automation.templates import (
            cron_for,
            find_template,
            render_instructions,
        )

        tmpl = find_template(key)
        if tmpl is None:
            return {"ok": False, "error": f"unknown template: {key}"}
        body = body or {}
        repo = str(body.get("repo") or "")
        channel = str(body.get("channel") or "")
        deliver = str(body.get("deliver") or "app")
        day = str(body.get("day") or tmpl.get("day") or "daily")
        time = str(body.get("time") or tmpl.get("time") or "09:00")

        if tmpl.get("needs_channel") and not channel:
            return {
                "ok": False,
                "error": "a Slack channel is required for this template",
            }
        instructions = render_instructions(
            tmpl, {"repo": repo, "channel": channel, "deliver": deliver}
        )
        permissions = None
        if tmpl.get("consent") and channel:
            permissions = [
                {"tool": "send_message", "target": channel, "access": "write"}
            ]
        return self.create_automation(
            {
                "title": tmpl["title"],
                "instructions": instructions,
                "cron": cron_for(day, time),
                "permissions": permissions,
            }
        )

    def update_automation(
        self, task_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        if "enabled" in changes:
            task.enabled = bool(changes["enabled"])
        if changes.get("instructions") is not None:
            task.instructions = changes["instructions"]
        if changes.get("title") is not None:
            task.title = changes["title"]
        if changes.get("cron") is not None:
            from croniter import croniter

            if task.trigger:
                return {
                    "ok": False,
                    "error": "this is an event-triggered automation — its condition can't be edited as a cron",
                }
            if not croniter.is_valid(changes["cron"]):
                return {"ok": False, "error": "invalid cron"}
            task.schedule.cron, task.schedule.kind = changes["cron"], "cron"
        if changes.get("budget_tokens") is not None:
            try:
                task.budget_tokens = int(changes["budget_tokens"])
            except (TypeError, ValueError):
                return {"ok": False, "error": "budget_tokens must be a number"}
        if changes.get("revoke"):
            # Revocation from the task detail page ("Allowed without asking … · Revoke").
            # Human-only, like minting; the agent-facing update tool has no such field.
            task.revoke_rule(str(changes["revoke"]))
        self.task_store.save(task)
        if changes.get("revoke"):
            # A live run engine may still hold the revoked rule — reseed from the record.
            for sid, engine in self._engines.items():
                owner = self.task_store.task_for_run_session(sid)
                if owner is not None and owner.id == task.id:
                    engine.permissions.task_rules = task.standing_rules()
        return {"ok": True, "task": task.public()}

    def delete_automation(self, task_id: str) -> dict[str, Any]:
        return {"ok": self.task_store.delete(task_id), "id": task_id}

    def prepare_manual_run(self, task_id: str) -> dict[str, Any]:
        """Create a 'running' manual run and return its session, so the GUI can open it and
        drive the task LIVE over the normal session WS (you watch the agent + follow up). The
        automatic scheduler path stays headless (`_run_scheduled_task`)."""
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        Path(task.workspace).mkdir(parents=True, exist_ok=True)
        run = TaskRun(
            task_id=task.id, trigger="manual"
        )  # status "running", session_id auto
        self.task_store.add_run(run)
        return {
            "ok": True,
            "run_id": run.run_id,
            "session_id": run.session_id,
            "workspace": task.workspace,
            "agent": task.agent,
            # Same execute-now framing as the headless path — manual runs ride a normal live
            # session whose engine DOES have scheduling tools, so be explicit.
            "prompt": (
                f"⏰ Running automation '{task.title}' now. Carry out these instructions "
                "immediately and produce the result. The schedule already exists — do not create "
                f"or modify any scheduled tasks.\n\n{task.instructions}"
            ),
        }

    def finalize_manual_run(self, task_id: str, run_id: str) -> dict[str, Any]:
        """Mark a manual run complete once its first turn finished (the WS already saved the
        session). Pulls result text + artifacts from the persisted transcript/workspace.
        """
        run = next(
            (r for r in self.task_store.runs(task_id) if r.run_id == run_id), None
        )
        task = self.task_store.get(task_id)
        if run is None or task is None:
            return {"ok": False, "error": "not found"}
        if run.status == "running":
            record = self.session_store.load(run.session_id)
            run.result_text = _last_assistant_text(record.messages) if record else None
            run.artifacts = _recent_files(task.workspace, since=run.started_at)
            run.status = "ok"
            run.finished_at = _epoch()
            self.task_store.add_run(run)
            task.last_run, task.last_status = run.finished_at, "ok"
            task.run_count += 1
            self.task_store.save(task)
        return {"ok": True, "run": run.to_dict()}
