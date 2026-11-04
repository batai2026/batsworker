"""Budget guard (budget.py) — store accumulation, ceiling checks, engine stop."""

from pathlib import Path


from coworker.budget import BudgetGuard, BudgetStore
from coworker.engine import TurnEngine
from coworker.permissions import PermissionEngine
from coworker.providers.base import ProviderClient, TokenUsage, AssistantTurn
from coworker.tools.registry import ToolRegistry


class ScriptedProvider(ProviderClient):
    """Scripted chat provider reporting fixed token usage per call (inherits the
    base stream default: complete() → one final chunk)."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return self._turns.pop(0)

    def capabilities(self, model):
        from coworker.providers.base import ModelCapabilities

        return ModelCapabilities()


def make_guard(tmp_path: Path, **limits) -> tuple[BudgetGuard, BudgetStore]:
    store = BudgetStore(tmp_path / "budget.json")
    return (
        BudgetGuard(store, session_id="s1", **limits),
        store,
    )


def test_store_accumulates(tmp_path):
    store = BudgetStore(tmp_path / "budget.json")
    store.record(session_id="s1", task_id="t1", tokens=100)
    store.record(session_id="s1", task_id="t1", tokens=50)
    store.record(session_id="s2", task_id=None, tokens=10)
    assert store.session_total("s1") == 150
    assert store.task_total("t1") == 150
    assert store.day_total() == 160
    assert store.session_total("s2") == 10


def test_zero_and_negative_tokens_not_recorded(tmp_path):
    store = BudgetStore(tmp_path / "budget.json")
    store.record(session_id="s1", task_id=None, tokens=0)
    store.record(session_id="s1", task_id=None, tokens=-5)
    assert store.session_total("s1") == 0


def test_task_ceiling(tmp_path):
    guard, _ = make_guard(tmp_path, task_id="t1", task_limit=100)
    assert guard.check() is None
    guard.record(100)
    reason = guard.check()
    assert reason and "Task token budget" in reason


def test_session_ceiling(tmp_path):
    guard, _ = make_guard(tmp_path, session_limit=200)
    guard.record(200)
    reason = guard.check()
    assert reason and "Session token budget" in reason


def test_daily_ceiling(tmp_path):
    guard, _ = make_guard(tmp_path, daily_limit=300)
    guard.record(300)
    reason = guard.check()
    assert reason and "Daily token budget" in reason


def test_under_limit_returns_none(tmp_path):
    guard, _ = make_guard(
        tmp_path, task_id="t1", task_limit=1000, session_limit=1000, daily_limit=1000
    )
    guard.record(100)
    assert guard.check() is None


def test_broken_store_never_raises(tmp_path):
    guard, _ = make_guard(tmp_path)
    guard.store.path.write_text("NOT JSON", encoding="utf-8")
    guard.record(50)  # must not raise
    assert guard.check() is None  # corrupt file degrades to empty totals


# -- engine integration ----------------------------------------------------------


def _collect(engine, user_input):
    import asyncio

    async def _run():
        return [ev async for ev in engine.run(user_input)]

    return asyncio.run(_run())


def test_engine_stops_when_budget_exhausted(tmp_path):
    """Run 1 completes normally (100 tokens recorded, budget check passes because
    it's at the top of iteration 1 with 0 accrued). Run 2: the budget check at the
    top of the loop fires (100 ≥ 50) — TURN_END budget_exhausted, and the provider
    is never called for the second run."""
    provider = ScriptedProvider(
        [
            AssistantTurn(text="first", finish_reason="stop", usage=TokenUsage(input=60, output=40)),
            AssistantTurn(text="should never run", finish_reason="stop", usage=TokenUsage(input=60, output=40)),
        ]
    )
    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-test",
        max_iterations=10,
    )
    engine.budget = BudgetGuard(
        BudgetStore(tmp_path / "budget.json"), session_id="s", session_limit=50
    )

    # Run 1: completes normally — the budget check at the top of iteration 1 sees
    # 0 accrued (recording happens after the model call).
    events1 = _collect(engine, "go")
    assert [e.type.name for e in events1][-1] == "TURN_END"
    assert provider.calls == 1

    # Run 2 (same session id): the guard trips before the model call.
    events2 = _collect(engine, "go again")
    end = next(e for e in events2 if e.type.name == "TURN_END")
    assert end.data["status"] == "budget_exhausted"
    assert provider.calls == 1  # never called for run 2
