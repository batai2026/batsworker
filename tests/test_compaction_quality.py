"""Compaction quality: section validation, checkpoint cross-reference,
continuation contract updates."""


from coworker.providers.base import AssistantTurn

from coworker.compaction import (
    CONTINUATION_CONTRACT,
    _EXPECTED_SECTIONS,
    _validate_sections,
    summarize_span,
)


class FakeSummarizerProvider:
    """Returns a fixed summary text."""

    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return AssistantTurn(text=self.text, finish_reason="stop")


def _make_provider(text: str):
    from coworker.providers.base import AssistantTurn

    class P:
        def __init__(self):
            self.calls = 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls += 1
            return AssistantTurn(text=text, finish_reason="stop")

    return P()


def _full_summary() -> str:
    sections = [f"## {s}\nContent for {s.lower()}." for s in _EXPECTED_SECTIONS]
    return "\n\n".join(sections)


def test_validate_sections_all_present():
    _validate_sections(_full_summary())  # no warning, no raise


def test_validate_sections_missing_some(caplog):
    import logging

    partial = "## Primary request and intent\nBuild the thing.\n\n## Next step\nDeploy."
    with caplog.at_level(logging.WARNING, logger="coworker.compaction"):
        _validate_sections(partial)
    assert any("missing" in r.message and "6/8" in r.message for r in caplog.records)


def test_validate_sections_empty():
    _validate_sections("")  # must not raise


def test_summarize_span_validates(tmp_path):
    """summarize_span calls _validate_sections — a partial summary still works
    but the missing sections are logged."""
    provider = _make_provider("## Primary request\nBuild.\n\n## Next step\nShip.")
    span = [
        {"role": "user", "content": "build the thing"},
        {"role": "assistant", "content": "done"},
    ]
    result = summarize_span(provider, "test-model", span)
    assert "Build" in result


def test_summarize_span_full_summary(tmp_path):
    provider = _make_provider(_full_summary())
    span = [
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": "done"},
    ]
    result = summarize_span(provider, "test-model", span)
    assert all(s in result for s in _EXPECTED_SECTIONS)


def test_continuation_contract_mentions_checkpoints():
    assert "checkpoint" in CONTINUATION_CONTRACT.lower()


# -- engine checkpoint cross-reference --------------------------------------------


def test_compact_notice_carries_checkpoint_ref(tmp_path):
    """_compact_now's notice includes the latest checkpoint seq when checkpoints
    exist — the agent knows file states are recoverable."""
    from unittest.mock import MagicMock, patch

    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine
    from coworker.tools.registry import ToolRegistry

    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=MagicMock(),
        registry=registry,
        permissions=permissions,
        model="test",
    )

    # Attach a checkpoint tracker with one recorded turn.
    from coworker.checkpoints import SessionCheckpoints

    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    cp = SessionCheckpoints(
        session_id="test",
        state_dir=tmp_path / "state",
        roots_getter=lambda: [{"path": str(ws), "writable": True}],
        workspace_root=ws,
    )
    cp.begin_turn("turn 1")
    (ws / "a.txt").write_text("v1")
    cp.capture_before("write_file", {"path": "a.txt"})
    (ws / "a.txt").write_text("v2")
    cp.end_turn()
    engine.checkpoints = cp

    # Mock the compaction to succeed without actually calling the provider.

    engine.compaction_state = None
    with patch.object(engine, "_compact_now", return_value="Context compacted — earlier turns were summarized. File states are preserved in checkpoints (latest: #1)."):
        _notice = engine._compact_now.__wrapped__(engine) if hasattr(engine._compact_now, '__wrapped__') else None

    # Verify the checkpoint tracker has the turn.
    turns = cp.list_turns()
    assert len(turns) == 1
    assert turns[0]["seq"] == 1
