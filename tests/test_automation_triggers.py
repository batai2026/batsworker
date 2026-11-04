"""Standing goals: event-trigger parsing, evaluation, edge/cooldown scheduling
(automation/triggers.py + scheduler wiring)."""

import asyncio
import time
from pathlib import Path

import pytest

from coworker.automation.models import ScheduledTask, Schedule
from coworker.automation.scheduler import Scheduler
from coworker.automation.store import TaskStore
from coworker.automation.triggers import (
    EventTrigger,
    TriggerError,
    evaluate,
)


def make_task(tmp_path: Path, trigger: dict, **kwargs) -> ScheduledTask:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    task = ScheduledTask(
        title="watcher",
        instructions="do the thing",
        schedule=Schedule(kind="cron", cron="0 12 * * *"),
        workspace=str(ws),
        trigger=trigger,
        **kwargs,
    )
    return task


# -- parse / validate -----------------------------------------------------------


def test_parse_rejects_unknown_kind():
    with pytest.raises(TriggerError):
        EventTrigger.parse({"kind": "twitter"})


def test_parse_rejects_bad_file_params():
    with pytest.raises(TriggerError):
        EventTrigger.parse({"kind": "file", "params": {}})  # no path
    with pytest.raises(TriggerError):
        EventTrigger.parse({"kind": "file", "params": {"path": "x", "mode": "crypto"}})
    with pytest.raises(TriggerError):
        EventTrigger.parse({"kind": "file", "params": {"path": "x", "mode": "contains"}})


def test_parse_rejects_path_escape(tmp_path):
    with pytest.raises(TriggerError):
        EventTrigger.parse({"kind": "file", "params": {"path": "../outside.txt"}})
    with pytest.raises(TriggerError):
        EventTrigger.parse(
            {"kind": "file", "params": {"path": str(tmp_path / "abs.txt")}}
        )


def test_parse_cooldown_clamped():
    low = EventTrigger.parse(
        {"kind": "inbox", "params": {}, "cooldown_seconds": 10}
    )
    assert low.cooldown_seconds == 300  # min 5 minutes
    high = EventTrigger.parse(
        {"kind": "inbox", "params": {}, "cooldown_seconds": 999_999}
    )
    assert high.cooldown_seconds == 86400


def test_human_labels():
    t = EventTrigger.parse(
        {"kind": "file", "params": {"path": "build.log", "mode": "changed"}}
    )
    assert t.human() == "event: file build.log changed"
    b = EventTrigger.parse(
        {"kind": "board_state", "params": {"state": "blocked", "min_minutes": 120}}
    )
    assert b.human() == "event: a board item is blocked for 120m"


# -- evaluation -------------------------------------------------------------------


def test_file_exists(tmp_path):
    task = make_task(tmp_path, {"kind": "file", "params": {"path": "ready.txt", "mode": "exists"}})
    trigger = EventTrigger.parse(task.trigger)
    met, detail, _ = evaluate(trigger, task=task, ctx={}, state={}, now=0.0)
    assert not met
    (tmp_path / "ws" / "ready.txt").write_text("done")
    met, detail, _ = evaluate(trigger, task=task, ctx={}, state={}, now=1.0)
    assert met and "exists" in detail


def test_file_changed_baselines_then_fires(tmp_path):
    task = make_task(tmp_path, {"kind": "file", "params": {"path": "log.txt", "mode": "changed"}})
    trigger = EventTrigger.parse(task.trigger)
    log = tmp_path / "ws" / "log.txt"
    log.write_text("line")
    # First evaluation records the baseline instead of firing.
    met, _, snapshot = evaluate(trigger, task=task, ctx={}, state={}, now=0.0)
    assert not met and snapshot["last_mtime"] == log.stat().st_mtime
    # Same mtime → still not met.
    met, _, _ = evaluate(trigger, task=task, ctx={}, state=snapshot, now=1.0)
    assert not met
    # Touch → met (small sleep ensures mtime changes on coarse-granularity filesystems).
    time.sleep(0.05)
    log.write_text("line2")
    met, detail, _ = evaluate(trigger, task=task, ctx={}, state=snapshot, now=2.0)
    assert met and "changed" in detail


def test_file_contains(tmp_path):
    task = make_task(
        tmp_path,
        {"kind": "file", "params": {"path": "log.txt", "mode": "contains", "value": "ERROR"}},
    )
    trigger = EventTrigger.parse(task.trigger)
    log = tmp_path / "ws" / "log.txt"
    log.write_text("all fine")
    met, _, _ = evaluate(trigger, task=task, ctx={}, state={}, now=0.0)
    assert not met
    log.write_text("ERROR: boom")
    met, _, _ = evaluate(trigger, task=task, ctx={}, state={}, now=1.0)
    assert met


def test_file_path_escape_refused_at_evaluation(tmp_path):
    task = make_task(tmp_path, {"kind": "file", "params": {"path": "ok.txt", "mode": "exists"}})
    trigger = EventTrigger.parse(task.trigger)
    # The trigger record was tampered with post-validation (storage attack) —
    # evaluation still refuses to look outside the workspace.
    trigger.params["path"] = "../secret.txt"
    met, detail, _ = evaluate(trigger, task=task, ctx={}, state={}, now=0.0)
    assert not met and "escapes" in detail


def test_board_state_via_injected_context(tmp_path):
    task = make_task(
        tmp_path,
        {"kind": "board_state", "params": {"state": "blocked", "min_minutes": 10}},
    )
    trigger = EventTrigger.parse(task.trigger)
    items = lambda ws: [  # noqa: E731
        {"id": 1, "state": "blocked", "title": "tfvars", "updated_at": time.time() - 3600},
        {"id": 2, "state": "open", "title": "other", "updated_at": time.time()},
    ]
    met, detail, _ = evaluate(trigger, task=task, ctx={"board_items": items}, state={}, now=time.time())
    assert met and "tfvars" in detail
    # A freshly-blocked item (younger than min_minutes) doesn't count.
    fresh = lambda ws: [  # noqa: E731
        {"id": 1, "state": "blocked", "title": "tfvars", "updated_at": time.time()},
    ]
    met, _, _ = evaluate(trigger, task=task, ctx={"board_items": fresh}, state={}, now=time.time())
    assert not met


def test_inbox_kind_filter(tmp_path):
    task = make_task(tmp_path, {"kind": "inbox", "params": {"kind": "approval"}})
    trigger = EventTrigger.parse(task.trigger)
    empty = lambda kind=None: []  # noqa: E731
    met, _, _ = evaluate(trigger, task=task, ctx={"inbox_pending": empty}, state={}, now=0.0)
    assert not met
    pending = lambda kind=None: [{"kind": kind or "question"}]  # noqa: E731
    met, detail, _ = evaluate(trigger, task=task, ctx={"inbox_pending": pending}, state={}, now=0.0)
    assert met and "approval" in detail
    questions_only = lambda kind=None: [  # noqa: E731
        {"kind": "question"}
    ] if kind == "question" else []
    met, _, _ = evaluate(trigger, task=task, ctx={"inbox_pending": questions_only}, state={}, now=0.0)
    assert not met


# -- store / scheduler wiring ------------------------------------------------------


def test_event_task_store_roundtrip_and_exclusion(tmp_path):
    store = TaskStore(tmp_path / "automation.db")
    task = make_task(tmp_path, {"kind": "inbox", "params": {}})
    store.save(task)
    # next_run stays None (never cron-due) and due() excludes it; event_tasks() finds it.
    assert task.next_run is None
    assert store.due() == []
    found = store.event_tasks()
    assert len(found) == 1 and found[0].trigger["kind"] == "inbox"


def test_scheduler_fires_on_edge_and_respects_cooldown(tmp_path):
    store = TaskStore(tmp_path / "automation.db")
    task = make_task(
        tmp_path,
        {"kind": "file", "params": {"path": "go.txt", "mode": "exists"}, "cooldown_seconds": 300},
    )
    store.save(task)
    ws = Path(task.workspace)
    ws.mkdir(parents=True, exist_ok=True)
    fired: list[str] = []

    async def runner(t, trigger, trigger_detail=""):
        fired.append(trigger_detail)
        return None

    ctx = lambda: {}  # noqa: E731 — file checks need no context readers
    scheduler = Scheduler(store, runner, event_context=ctx)

    # Pre-create the file (exists mode → first evaluation meets) and run two ticks.
    (ws / "go.txt").write_text("go")
    asyncio.run(scheduler._tick(trigger="schedule"))
    asyncio.run(scheduler._tick(trigger="schedule"))
    # Edge fired once; the second tick is inside the cooldown.
    assert len(fired) == 1

    # Cooldown elapses while STILL met → re-fires.
    saved = store.get(task.id)
    saved.trigger_state["last_fired_at"] = time.time() - 400
    store.save(saved)
    asyncio.run(scheduler._tick(trigger="schedule"))
    assert len(fired) == 2

    # Condition clears → no fire; state records unmet.
    (ws / "go.txt").unlink()
    asyncio.run(scheduler._tick(trigger="schedule"))
    assert len(fired) == 2
    assert store.get(task.id).trigger_state["met"] is False
    # Condition returns → rising edge again.
    (ws / "go.txt").write_text("go")
    asyncio.run(scheduler._tick(trigger="schedule"))
    assert len(fired) == 3


def test_scheduler_skips_event_task_while_its_run_is_live(tmp_path):
    store = TaskStore(tmp_path / "automation.db")
    task = make_task(tmp_path, {"kind": "inbox", "params": {}})
    store.save(task)
    fired: list[str] = []

    async def runner(t, trigger, trigger_detail=""):
        fired.append(trigger)
        return None

    scheduler = Scheduler(store, runner, event_context=lambda: {"inbox_pending": lambda kind=None: [{"kind": "question"}]})
    scheduler._running_ids.add(task.id)  # simulate an in-flight run
    asyncio.run(scheduler._tick(trigger="schedule"))
    assert fired == []


def test_scheduled_task_public_carries_trigger(tmp_path):
    task = make_task(
        tmp_path,
        {"kind": "file", "params": {"path": "ready.txt", "mode": "exists"}},
    )
    body = task.public()
    assert body["schedule"] == "event: file ready.txt exists"
    assert body["trigger"]["kind"] == "file"
