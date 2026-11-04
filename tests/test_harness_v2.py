"""Harness optimizations v2: delta batching, execution waves, prompt cache."""

import json
from unittest.mock import MagicMock


from coworker.permissions import PermissionEngine
from coworker.providers.base import ProviderClient, ToolCall
from coworker.tools.registry import ToolRegistry


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        from coworker.providers.base import ModelCapabilities

        return ModelCapabilities()


def _collect(engine, user_input):
    import asyncio

    async def _run():
        return [ev async for ev in engine.run(user_input)]

    return asyncio.run(_run())


# -- execution wave planning ------------------------------------------------------


def _engine_with_tools(tmp_path, registered_tools):
    from coworker.engine import TurnEngine

    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    for fn, meta in registered_tools:
        registry.register(fn, metadata=meta)
    engine = TurnEngine(
        provider=MagicMock(), registry=registry, permissions=permissions, model="test"
    )
    return engine


def test_waves_parallel_low_risk_no_paths(tmp_path):
    """Two low-risk tools with no file paths share a wave (parallel-safe metadata)."""
    import aisuite as ai

    low = ai.ToolMetadata(category="search", risk_level="low", requires_approval=False)

    def tool_a():
        pass

    def tool_b():
        pass

    engine = _engine_with_tools(tmp_path, [(tool_a, low), (tool_b, low)])
    tc_a = ToolCall(id="a", name="tool_a", arguments={})
    tc_b = ToolCall(id="b", name="tool_b", arguments={})
    waves = engine._plan_execution_waves([tc_a, tc_b])
    assert len(waves) == 1 and len(waves[0]) == 2  # same wave → parallel


def test_waves_serial_high_risk(tmp_path):
    """A high-risk tool (shell command) gets its own serial wave."""
    import aisuite as ai

    low = ai.ToolMetadata(category="search", risk_level="low", requires_approval=False)

    def tool_a() -> dict:
        return {}

    def run_shell(command: str) -> dict:
        return {}

    run_shell.__name__ = "run_shell"
    engine = _engine_with_tools(
        tmp_path,
        [(tool_a, low), (run_shell, ai.ToolMetadata(category="shell", risk_level="high", requires_approval=True))],
    )
    low_tc = ToolCall(id="a", name="tool_a", arguments={})
    shell_tc = ToolCall(id="b", name="run_shell", arguments={"command": "ls"})
    waves = engine._plan_execution_waves([low_tc, shell_tc])
    assert len(waves) == 2  # separate waves
    assert waves[0][0].name == "tool_a"
    assert waves[1][0].name == "run_shell"


def test_waves_write_read_same_file_serialized(tmp_path):
    """A write and a read of the same file are placed in different waves."""
    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine

    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=MagicMock(), registry=registry, permissions=permissions, model="test"
    )
    write_tc = ToolCall(id="w", name="write_file", arguments={"path": "out.md", "content": "hi"})
    read_tc = ToolCall(id="r", name="read_file", arguments={"path": "out.md"})
    waves = engine._plan_execution_waves([write_tc, read_tc])
    assert len(waves) == 2  # write in wave 0, read in wave 1


def test_waves_write_read_different_files_parallel(tmp_path):
    """Writes to different files can run in parallel."""
    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine

    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=MagicMock(), registry=registry, permissions=permissions, model="test"
    )
    w1 = ToolCall(id="w1", name="write_file", arguments={"path": "a.md", "content": "x"})
    w2 = ToolCall(id="w2", name="write_file", arguments={"path": "b.md", "content": "y"})
    waves = engine._plan_execution_waves([w1, w2])
    assert len(waves) == 1  # same wave — different files, no conflict


# -- prompt cache stability (within a turn) -----------------------------------------


def test_prompt_cache_within_turn_stable(tmp_path):
    """Within a single turn, calling _outbound_messages() twice (no new messages)
    produces the same output — the prefix is byte-stable for prompt caching."""
    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine

    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    clock_values = iter(["12:00", "12:00"])  # same clock within a turn

    def context_provider():
        return next(clock_values)

    engine = TurnEngine(
        provider=MagicMock(),
        registry=registry,
        permissions=permissions,
        model="test",
        context_provider=context_provider,
    )
    engine.messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hello"},
    ]
    out1 = engine._outbound_messages()
    out2 = engine._outbound_messages()
    assert json.dumps(out1, sort_keys=True) == json.dumps(out2, sort_keys=True)


def test_prompt_cache_cross_turn_prefix_stable(tmp_path):
    """Cross-turn: the SYSTEM message (the cacheable prefix) must be stable. The
    context injection into the last user message is a known cache-miss cost —
    documented as a trade-off of the live-clock/environment pattern."""
    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine

    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)

    clock_values = iter(["12:00", "12:01"])

    def context_provider():
        return next(clock_values)

    engine = TurnEngine(
        provider=MagicMock(),
        registry=registry,
        permissions=permissions,
        model="test",
        context_provider=context_provider,
    )
    engine.messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hello"},
    ]
    out1 = engine._outbound_messages()
    engine.messages.append({"role": "user", "content": "more"})
    out2 = engine._outbound_messages()

    # The system message is byte-identical — that's the cacheable prefix root.
    assert out1[0]["content"] == out2[0]["content"]
    # The context block is always on the LAST user message only.
    assert "system-context" not in json.dumps(out2[0])
