"""Event triggers for standing goals ("watch X, act when Y") — the declarative
condition layer on top of the cron scheduler.

A ScheduledTask can carry an optional ``trigger``: an EventTrigger serialized to
a dict. Event tasks never cron-fire (``compute_next_run`` → None, ``due`` skips
them); instead the scheduler tick evaluates each condition and fires the task's
normal run machinery on a **rising edge** (condition transitions unmet → met),
re-firing at most once per ``cooldown_seconds`` while it stays met.

Trigger kinds (v1 — everything locally observable, no connector queries):
  - ``file``: a path inside the task's workspace exists / changed / contains a
    substring. ``changed`` baselines on first evaluation (recording the observed
    mtime) instead of firing immediately.
  - ``board_state``: the workspace's board has an item in the given state
    (blocked, review, …) — evaluated against the teams store via the injected
    context.
  - ``inbox``: any pending Inbox item (optionally filtered by kind) — "someone
    needs the agent" as a proactive surface (e.g. notify on Slack).

Safety invariants:
  - File paths resolve INSIDE the task's workspace (traversal/absolute-escape
    refused at parse time AND at evaluation time — both fail closed).
  - The condition detail that reaches the prompt is path/mode FACTS only, never
    file contents (content is data an attacker may control — the agent wakes and
    reads it through the normal permission system instead).
  - Cooldowns clamp to [MIN_COOLDOWN, MAX_COOLDOWN]; state persists on the task
    record (``trigger_state``) so restarts don't re-fire.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

EVENT_KINDS = ("file", "board_state", "inbox")
FILE_MODES = ("exists", "changed", "contains")
BOARD_STATES = ("open", "in_progress", "blocked", "review", "done", "canceled")
MIN_COOLDOWN = 300
MAX_COOLDOWN = 24 * 3600
DEFAULT_COOLDOWN = 3600


class TriggerError(ValueError):
    pass


@dataclass
class EventTrigger:
    kind: str
    params: Dict[str, Any] = field(default_factory=dict)
    cooldown_seconds: int = DEFAULT_COOLDOWN

    # -- parse / validate -----------------------------------------------------

    @classmethod
    def parse(cls, raw: Any) -> "EventTrigger":
        if not isinstance(raw, dict):
            raise TriggerError("trigger must be an object")
        kind = str(raw.get("kind", "")).strip()
        if kind not in EVENT_KINDS:
            raise TriggerError(f"unknown trigger kind: {kind!r} (expected one of {EVENT_KINDS})")
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            raise TriggerError("trigger params must be an object")
        trigger = cls(kind=kind, params=params)
        if kind == "file":
            trigger._validate_file()
        elif kind == "board_state":
            trigger._validate_board()
        try:
            cooldown = int(raw.get("cooldown_seconds") or DEFAULT_COOLDOWN)
        except (TypeError, ValueError):
            cooldown = DEFAULT_COOLDOWN
        trigger.cooldown_seconds = max(MIN_COOLDOWN, min(cooldown, MAX_COOLDOWN))
        return trigger

    def _validate_file(self) -> None:
        mode = str(self.params.get("mode", "exists")).strip()
        if mode not in FILE_MODES:
            raise TriggerError(f"file trigger mode must be one of {FILE_MODES}")
        self.params["mode"] = mode
        path = str(self.params.get("path", "")).strip()
        if not path:
            raise TriggerError("file trigger requires a path")
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise TriggerError("file trigger path must be relative to the task workspace")
        if mode == "contains" and not str(self.params.get("value", "")):
            raise TriggerError("contains mode requires a value substring")

    def _validate_board(self) -> None:
        state = str(self.params.get("state", "")).strip()
        if state not in BOARD_STATES:
            raise TriggerError(f"board_state must be one of {BOARD_STATES}")
        self.params["state"] = state
        try:
            self.params["min_minutes"] = max(0, int(self.params.get("min_minutes") or 0))
        except (TypeError, ValueError):
            self.params["min_minutes"] = 0

    # -- labels -----------------------------------------------------------------

    def human(self) -> str:
        """One-line label for the API/UI ('event: file build.log changed')."""
        p = self.params
        if self.kind == "file":
            mode = {"exists": "exists", "changed": "changed", "contains": "changes"}[p["mode"]]
            if p["mode"] == "contains":
                return f"event: file {p['path']} contains {p['value']!r}"
            return f"event: file {p['path']} {mode}"
        if self.kind == "board_state":
            minutes = f" for {p['min_minutes']}m" if p.get("min_minutes") else ""
            return f"event: a board item is {p['state']}{minutes}"
        kind = f" ({p['kind']})" if p.get("kind") else ""
        return f"event: pending inbox items{kind}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "params": dict(self.params),
            "cooldown_seconds": self.cooldown_seconds,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["EventTrigger"]:
        try:
            return cls.parse(raw)
        except TriggerError:
            return None


# -- evaluation -----------------------------------------------------------------
#
# ``ctx`` carries injected, locally-bound readers (board items, pending inbox) so
# evaluation stays a pure function over injected state and tests never touch the
# stores. Missing readers make the condition unmet with a detail explaining why —
# a standing goal must never crash the tick.


def evaluate(
    trigger: EventTrigger,
    *,
    task: Any,
    ctx: Dict[str, Any],
    state: Dict[str, Any],
    now: Optional[float] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Evaluate one trigger. Returns ``(met, detail, snapshot)`` — the snapshot is
    merged into the task's ``trigger_state`` by the caller (baselines for the
    ``changed`` mode live there). Never raises."""
    now = now if now is not None else time.time()
    try:
        if trigger.kind == "file":
            return _eval_file(trigger, task, state, now)
        if trigger.kind == "board_state":
            return _eval_board(trigger, task, ctx, state)
        if trigger.kind == "inbox":
            return _eval_inbox(trigger, ctx)
    except Exception as exc:  # a broken condition must never stall the tick
        return False, f"trigger evaluation error: {exc}", {}
    return False, f"unknown trigger kind {trigger.kind}", {}


def _workspace_path(task: Any, raw: str) -> Optional[Path]:
    """Resolve a trigger path inside the task workspace, or None on any escape."""
    if not getattr(task, "workspace", None):
        return None
    root = Path(task.workspace)
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _eval_file(
    trigger: EventTrigger, task: Any, state: Dict[str, Any], now: float
) -> Tuple[bool, str, Dict[str, Any]]:
    p = trigger.params
    path = _workspace_path(task, str(p.get("path", "")))
    if path is None:
        return False, "file path escapes the task workspace", {}
    mode = p["mode"]
    if mode == "exists":
        met = path.is_file()
        return met, (f"file {p['path']} exists" if met else f"file {p['path']} does not exist yet"), {}
    if mode == "changed":
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return False, f"file {p['path']} does not exist yet", {}
        baseline = state.get("last_mtime")
        snapshot = {"last_mtime": mtime}
        if baseline is None:
            # First evaluation records the baseline instead of firing — "changed"
            # means a change WE observed, not "the file predates the goal".
            return False, f"baseline recorded for {p['path']}", snapshot
        met = mtime != float(baseline)
        return met, (f"file {p['path']} changed" if met else f"file {p['path']} unchanged"), snapshot
    # contains
    value = str(p.get("value", ""))
    try:
        met = value in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, f"file {p['path']} does not exist yet", {}
    return met, (f"file {p['path']} contains {value!r}" if met else f"file {p['path']} does not contain {value!r}"), {}


def _eval_board(
    trigger: EventTrigger, task: Any, ctx: Dict[str, Any], state: Dict[str, Any]
) -> Tuple[bool, str, Dict[str, Any]]:
    reader = ctx.get("board_items")
    if reader is None:
        return False, "board context unavailable", {}
    workspace = getattr(task, "workspace", None)
    items = reader(workspace) or []
    state_name = trigger.params["state"]
    min_minutes = int(trigger.params.get("min_minutes") or 0)
    now = time.time()
    matched = 0
    titles: list[str] = []
    for item in items:
        if item.get("state") != state_name:
            continue
        if min_minutes:
            updated = float(item.get("updated_at") or 0)
            if updated and (now - updated) < min_minutes * 60:
                continue
        matched += 1
        if len(titles) < 3:
            titles.append(str(item.get("title", f"#{item.get('id', '?')}")))
    detail = (
        f"{matched} board item(s) {state_name}"
        + (f" ({', '.join(titles)})" if titles else "")
    )
    return matched > 0, detail, {}


def _eval_inbox(
    trigger: EventTrigger, ctx: Dict[str, Any]
) -> Tuple[bool, str, Dict[str, Any]]:
    reader = ctx.get("inbox_pending")
    if reader is None:
        return False, "inbox context unavailable", {}
    kind = trigger.params.get("kind") or None
    items = reader(kind) or []
    met = len(items) > 0
    detail = f"{len(items)} pending inbox item(s)" + (f" ({kind})" if kind else "")
    return met, detail, {}
