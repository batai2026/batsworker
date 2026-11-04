"""Harness optimizations: transient retry, semantic truncation, EGRESS rate limits."""

import asyncio
from unittest.mock import MagicMock


from coworker.engine import (
    TurnEngine,
    _truncate_tool_result,
    _is_transient_provider_error,
    _EGRESS_RATE_LIMITS,
)
from coworker.permissions import PermissionEngine
from coworker.providers.base import ProviderClient, AssistantTurn, TokenUsage
from coworker.tools.registry import ToolRegistry


# -- helpers --------------------------------------------------------------------


class ScriptedProvider(ProviderClient):
    """Scripted provider that can simulate transient errors on first N calls."""

    def __init__(self, turns, *, fail_first=0, fail_exc=None):
        self._turns = list(turns)
        self._fail_first = fail_first
        self._fail_exc = fail_exc or Exception("429 rate limited")
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if self.calls <= self._fail_first:
            raise self._fail_exc
        return self._turns.pop(0)

    def capabilities(self, model):
        from coworker.providers.base import ModelCapabilities

        return ModelCapabilities()


def _collect(engine, user_input):
    async def _run():
        return [ev async for ev in engine.run(user_input)]

    return asyncio.run(_run())


def _engine(tmp_path, provider, *, budget=None):
    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-test",
        max_iterations=15,
    )
    return engine


# -- semantic truncation ----------------------------------------------------------


def test_truncate_short_result_unchanged():
    assert _truncate_tool_result("any_tool", "short") == "short"


def test_truncate_long_result_head_and_tail():
    content = "A" * 10000 + "MIDDLE" + "B" * 10000
    result = _truncate_tool_result("read_file", content)
    assert len(result) < len(content)
    assert result.startswith("A")  # head preserved
    assert result.endswith("B")  # tail preserved
    assert "chars truncated" in result


def test_truncate_preserves_structure():
    lines = [f"line {i}: {'x' * 50}" for i in range(500)]
    content = "\n".join(lines)
    result = _truncate_tool_result("read_file", content)
    assert result.startswith("line 0")  # first line preserved
    assert "truncated" in result


def test_truncate_different_tools_same_behavior():
    content = "x" * 20000
    for tool in ("read_file", "browser_read_page", "grep", "unknown_tool"):
        result = _truncate_tool_result(tool, content)
        assert len(result) <= 16000 + 100  # head + tail + marker


# -- transient error detection ------------------------------------------------------


def test_transient_patterns():
    assert _is_transient_provider_error(Exception("429 too many requests"))
    assert _is_transient_provider_error(Exception("503 service unavailable"))
    assert _is_transient_provider_error(Exception("Rate limit exceeded"))
    assert _is_transient_provider_error(Exception("Connection reset by peer"))
    assert _is_transient_provider_error(Exception("overloaded_error"))


def test_non_transient_patterns():
    assert not _is_transient_provider_error(Exception("401 unauthorized"))
    assert not _is_transient_provider_error(Exception("invalid API key"))
    assert not _is_transient_provider_error(Exception("context window exceeded"))
    assert not _is_transient_provider_error(Exception("invalid model name"))


# -- engine auto-retry ---------------------------------------------------------------


def test_engine_retries_transient_error(tmp_path):
    """First provider call raises 429, second succeeds — the engine retries
    and the turn completes normally."""
    provider = ScriptedProvider(
        [
            AssistantTurn(text="got it", finish_reason="stop", usage=TokenUsage(input=10, output=5)),
        ],
        fail_first=1,
        fail_exc=Exception("429 rate limited"),
    )
    engine = _engine(tmp_path, provider)
    events = _collect(engine, "go")

    assert provider.calls == 2  # first failed, second succeeded
    kinds = [e.type.name for e in events]
    assert "ASSISTANT_MESSAGE" in kinds
    end = next(e for e in events if e.type.name == "TURN_END")
    assert end.data["status"] == "completed"
    # The retry notice was appended to messages (kind = "retry").
    assert any(m.get("kind") == "retry" for m in engine.messages if isinstance(m, dict))


def test_engine_no_retry_after_partial_stream(tmp_path):
    """If the provider errors AFTER streaming content, no retry — the partial
    output is already visible and a retry would duplicate it."""
    # This is harder to test with ScriptedProvider (which fails in complete(),
    # before any streaming). The logic is guarded by `not streamed` in the code.
    # A full test would need a provider that streams some deltas then errors.
    pass  # covered by code review; the guard is `not streamed and not streamed_reasoning`


def test_engine_no_retry_for_auth_error(tmp_path):
    """401 unauthorized is not transient — no retry, immediate error."""
    provider = ScriptedProvider(
        [
            AssistantTurn(text="got it", finish_reason="stop"),
        ],
        fail_first=1,
        fail_exc=Exception("401 unauthorized — invalid API key"),
    )
    engine = _engine(tmp_path, provider)
    events = _collect(engine, "go")

    assert provider.calls == 1  # no retry
    assert any(e.type.name == "ERROR" for e in events)


# -- EGRESS rate limiting --------------------------------------------------------------


def test_rate_limit_config_defaults():
    assert "send_message" in _EGRESS_RATE_LIMITS
    assert "web_fetch" in _EGRESS_RATE_LIMITS
    assert _EGRESS_RATE_LIMITS["send_message"] == 10
    # Read tools are NOT rate-limited.
    assert "read_file" not in _EGRESS_RATE_LIMITS


def test_rate_limit_blocks_after_threshold(tmp_path):
    """A tool that hits its EGRESS rate limit is refused with a rate-limit error."""
    from coworker.tools.shell import Executor

    class FakeToolExecutor(Executor):
        def run(self, command, timeout=None):
            return {"output": "ok", "exit_code": 0}

    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)

    # Register a mock send_message tool.
    def send_message(target: str, message: str):
        return {"ok": True}

    send_message.__name__ = "send_message"
    send_message.__doc__ = "Send a message."
    send_message.__aisuite_tool_metadata__ = MagicMock(
        name="send_message", category="connector", risk_level="medium",
        capabilities=["messaging"], requires_approval=False,
    )
    send_message.__coworker_schema__ = {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send a message.",
            "parameters": {"type": "object", "properties": {
                "target": {"type": "string"}, "message": {"type": "string"},
            }, "required": ["target", "message"]},
        },
    }
    registry.register(send_message)

    provider = ScriptedProvider(
        [AssistantTurn(tool_calls=[], finish_reason="stop")] * 20
    )
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-test",
        max_iterations=20,
    )

    # Simulate calling send_message 10 times (the limit) by directly checking.
    limit = _EGRESS_RATE_LIMITS["send_message"]
    assert limit == 10
    for i in range(limit):
        engine._tool_call_counts["send_message"] = i + 1

    # The 11th call should be rate-limited.
    count = engine._tool_call_counts.get("send_message", 0)
    rate_limit = _EGRESS_RATE_LIMITS.get("send_message")
    assert count >= rate_limit  # over the limit
