"""Approvals cluster split out of manager.py (manager-split refactor).

Mixin methods only; shared state lives on the SessionManager facade (self.*). Module-level
imports are limited to what the moved code references; everything else resolves via self.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..agent import build_engine
from ..agents import get_agent
from ..engine import ApprovalOutcome, TurnEngine
from ..permissions import Mode
from .manager_helpers import _approval_body, _grant_offered


class ApprovalsMixin:
    def list_audit(
        self,
        *,
        limit: int = 100,
        session_id: Optional[str] = None,
        connector: Optional[str] = None,
        tool: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return self.audit_store.list(
            limit=limit, session_id=session_id, connector=connector, tool=tool
        )

    # -- automation (scheduled tasks) -------------------------------------------
    def approval_prompt_data(self, session_id: str, request) -> dict[str, Any]:
        """Extra Inbox-item payload for a parked approval. Always carries the tool name +
        arguments so the GUI can render the same humanized card (§35) it shows live —
        without them a reopened session fell back to the raw 'Run `tool`?' treatment.
        Automation runs additionally carry the owning task + (when the call is eligible)
        the exact target a standing rule would pin: the GUI offers "Allow every time" only
        when both are present — in-app only, never on Slack-mirrored buttons (§25)."""
        from ..permissions import standing_rule_candidate

        data: dict[str, Any] = {
            "tool": request.tool_name,
            "arguments": getattr(request, "arguments", None) or {},
        }
        task = self.task_store.task_for_run_session(session_id)
        if task is None:
            return data
        data.update({"task_id": task.id, "task_title": task.title})
        target = standing_rule_candidate(
            request.tool_name,
            getattr(request, "arguments", None) or {},
            getattr(request, "metadata", None),
        )
        if target:
            data["standing_target"] = target
        return data

    def mint_task_rule(
        self, session_id: str, tool_name: str, arguments: Any, metadata: Any = None
    ) -> bool:
        """Persist a standing rule a human minted via "Allow every time" on a run's
        approval card (§25's retrofit path). Server-side validation, not trust in the
        card: the session must be an automation run and the call must be rule-eligible
        (external risk, declared target argument, non-empty target). Also applies the
        rule to the live engine so the run's next call auto-allows."""
        from ..permissions import standing_rule_candidate

        task = self.task_store.task_for_run_session(session_id)
        if task is None:
            return False
        target = standing_rule_candidate(tool_name, arguments or {}, metadata)
        if not target or not task.add_rule(tool_name, target):
            return False
        self.task_store.save(task)
        engine = self._engines.get(session_id)
        if engine is not None:
            engine.permissions.task_rules.setdefault(tool_name, set()).add(target)
        try:
            self.audit_store.append(
                {
                    "session_id": session_id,
                    "tool": tool_name,
                    "arguments": arguments or {},
                    "stage": "standing_rule_minted",
                    "status": "granted",
                    "reason": f"allow every time: {tool_name} → {target} (task {task.id})",
                }
            )
        except Exception:
            pass
        return True

    def approval_outcome(self, resolution: str, request, session_id: str):
        """Map an approval resolution (from any surface) to an ApprovalOutcome, handling
        the task-persistent "always_task" vocabulary alongside the session-scoped ones.

        Server-side validated, not trusted from the caller: a grant that no UI offers for
        this tool is downgraded to a one-time approval rather than honoured. The GUI already
        hides the broad "always allow" for run_shell / connectors / save_skill, and Slack
        mirrors only ever render approve/deny — but `POST /v1/inbox/{id}/resolve` takes a raw
        string, so without this check any local API caller could mint a session-wide
        any-argument shell grant. Same philosophy as mint_task_rule: validate here, don't
        trust the card.
        """

        if resolution == "always_task":
            minted = self.mint_task_rule(
                session_id,
                request.tool_name,
                getattr(request, "arguments", None),
                getattr(request, "metadata", None),
            )
            if not minted:
                self._audit_grant_refused(session_id, request, resolution)
            return ApprovalOutcome.ONCE
        try:
            outcome = ApprovalOutcome(resolution)
        except ValueError:
            if resolution == "allow":
                return ApprovalOutcome.ONCE
            if resolution == "always":
                outcome = ApprovalOutcome.ALWAYS_TOOL
            else:
                return ApprovalOutcome.DENY
        if outcome in (
            ApprovalOutcome.ALWAYS_TOOL,
            ApprovalOutcome.ALWAYS_COMMAND,
            ApprovalOutcome.ALWAYS_DOMAIN,
        ) and not _grant_offered(outcome, request):
            self._audit_grant_refused(session_id, request, resolution)
            return ApprovalOutcome.ONCE
        return outcome

    def audit_autonomy_change(
        self, session_id: str, kind: str, before: Any, after: Any
    ) -> None:
        """Record a change to how much the agent may do unsupervised — the permission mode,
        or the attended/unattended toggle. Without this, "who turned on auto mode, and when"
        is unanswerable from the audit store, which is at odds with the per-call trail the
        rest of the engine keeps. Raising autonomy is flagged so it can be filtered."""
        # AUTO_APPROVE sits above interactive (turning the reviewer on means fewer human
        # checks — that IS raising autonomy) and below bypass, which removes checks
        # entirely. "auto" is the legacy spelling of "bypass-approvals".
        order = {
            "discuss": 0,
            "plan": 1,
            "interactive": 2,
            "custom": 2,
            "auto-approve": 3,
            "auto": 4,
            "bypass-approvals": 4,
        }
        raised = (
            order.get(str(after), 0) > order.get(str(before), 0)
            if kind == "mode"
            else bool(after) and not bool(before)
        )
        try:
            self.audit_store.append(
                {
                    "session_id": session_id,
                    "tool": "",
                    "arguments": {},
                    "stage": f"{kind}_changed",
                    "status": "raised" if raised else "lowered",
                    "reason": f"{kind}: {before} → {after}",
                }
            )
        except Exception:
            pass

    def set_unattended(self, session_id: str, on: bool) -> dict[str, Any]:
        """Flip the attended/unattended toggle, with an audit row. Note this changes only
        WHERE the human is reached, never the autonomy ceiling (that's the mode) — but it is
        still worth recording, since an unattended session routes prompts away from the
        screen the user is looking at."""
        before = self.unattended.is_unattended(session_id)
        self.unattended.set(session_id, on)
        if before != on:
            self.audit_autonomy_change(session_id, "unattended", before, on)
        return {"ok": True, "session_id": session_id, "unattended": on}

    def _audit_grant_refused(self, session_id: str, request, resolution: str) -> None:
        try:
            self.audit_store.append(
                {
                    "session_id": session_id,
                    "tool": getattr(request, "tool_name", ""),
                    "arguments": getattr(request, "arguments", None) or {},
                    "stage": "grant_refused",
                    "status": "downgraded",
                    "reason": (
                        f"resolution {resolution!r} is not offered for this tool — "
                        "applied as a one-time approval"
                    ),
                }
            )
        except Exception:
            pass

    def _scheduled_approver(self, task, session_id: str):
        from ..permissions import WRITE_TOOLS

        name_allowed = task.name_allowed_tools()

        async def approver(request):
            # Unattended: auto-allow the deliverable writes (path-scoped to the task
            # workspace) + tools the task allows BY NAME (legacy entries). Target-bound
            # rules never reach here — the permission engine matched them already.
            if request.tool_name in WRITE_TOOLS or request.tool_name in name_allowed:
                return ApprovalOutcome.ONCE
            # Anything else parks in the Inbox and suspends the run (§25 graceful
            # degradation — an ungranted automation still works, it just asks). The item
            # carries the task binding so the in-app card can offer "Allow every time";
            # the Slack mirror renders only Approve/Deny buttons.
            item = self.inbox.add_approval(
                session_id,
                f"Run `{request.tool_name}`?",
                body=_approval_body(request),
                inbox=self.inbox_routing.route_for(session_id, task.agent),
                tool_call_id=getattr(request, "tool_call_id", None),
                data=self.approval_prompt_data(session_id, request),
            )
            if item.state == "pending":
                self.persist_session(session_id)
                await self.mirror_inbox_item(item)
            resolution = await self.inbox.wait(item.id)
            return self.approval_outcome(resolution, request, session_id)

        return approver

    def _seed_task_permissions(self, engine: TurnEngine, task) -> None:
        """Apply a task's standing allowances to an engine: target-bound rules feed the
        permission engine's matcher (connector tools included — the target binding is the
        safety); name-only legacy entries keep their session-allowlist behavior."""
        engine.permissions.task_rules = task.standing_rules()
        for tool in task.name_allowed_tools():
            engine.permissions.allow_tool_for_session(tool)

    def _build_task_engine(self, task, *, session_id: str) -> TurnEngine:
        ag = get_agent(task.agent)
        Path(task.workspace).mkdir(parents=True, exist_ok=True)
        engine = build_engine(
            agent=ag,
            workspace=task.workspace,
            model=task.model or self.model,
            mode=Mode.INTERACTIVE,
            approver=self._scheduled_approver(task, session_id),
            provider=self.provider,
            memory_store=self.memory_store,
            memory_workspace=self._memory_key_for(None, task.workspace),
            memory_off=not self.memory_settings.enabled,
            memory_saving_enabled=lambda: self.memory_settings.enabled,
            # Callable, not a snapshot: editing your instructions in Settings applies
            # to conversations already open (same reason as the saving switch).
            user_rules=lambda: self.memory_settings.user_rules,
            on_memory_saved=self._memory_saved_notifier(session_id),
            secrets=self.secrets,
            # No scheduling tools inside a scheduled run: the executing agent's job is to DO the
            # task, and instructions that mention timing ("every day at 5:32pm…") otherwise tempt
            # it to create another automation instead of running this one.
            task_store=None,
            session_id=session_id,
            audit_sink=self.audit_store.append,
            # Scheduled runs respect the same per-session connection hierarchy as live sessions:
            # expose only the persona's effective-enabled connectors' tools (§4.3).
            connector_filter=self.effective_connectors(session_id, task.agent),
            skill_filter=lambda sid=session_id, w=task.workspace, a=task.agent: (
                self.effective_skill_names(sid, w, agent=a)
            ),
            extra_skill_dirs=(
                [d] if (d := self.persona_skill_scope(task.agent)[0]) is not None else None
            ),
        )
        self._seed_task_permissions(engine, task)
        return engine
