"""Turn-checkpoint capture/rollback round trips (coworker/checkpoints.py).

Semantics under test:
  - Only turns whose tracked write tools SUCCEEDED produce a checkpoint record
    (a no-write turn leaves no rollback point, and seqs have gaps).
  - ``rollback_to(K)`` restores every file to its state at the END of turn K:
    for each file, the pre-content captured at its FIRST post-K modification.
  - Files whose pre-content could not be captured (too large, unreadable) are
    reported as ``skipped`` and left untouched.
"""

from pathlib import Path

import pytest

from coworker.checkpoints import (
    CheckpointBusy,
    CheckpointNotFound,
    SessionCheckpoints,
)


def make_tracker(tmp_path: Path, session_id: str = "sess-abc-1") -> tuple[SessionCheckpoints, Path]:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    roots = [{"path": str(ws), "writable": True, "label": "workspace"}]
    tracker = SessionCheckpoints(
        session_id=session_id,
        state_dir=tmp_path / "state",
        roots_getter=lambda: roots,
        workspace_root=ws,
    )
    return tracker, ws


def test_capture_and_rollback_roundtrip(tmp_path):
    tracker, ws = make_tracker(tmp_path)
    a = ws / "a.txt"

    # Turn 1: create a.txt (pre-content: absent).
    tracker.begin_turn("create a")
    tracker.capture_before("write_file", {"path": "a.txt"})
    a.write_text("v1", encoding="utf-8")
    tracker.end_turn()

    # Turn 2: modify a.txt (pre-content: v1) and create b.txt (pre-content: absent).
    tracker.begin_turn("modify")
    tracker.capture_before("write_file", {"path": "a.txt"})
    a.write_text("v2", encoding="utf-8")
    tracker.capture_before("write_file", {"path": "b.txt"})
    b = ws / "b.txt"
    b.write_text("new", encoding="utf-8")
    tracker.end_turn()

    assert [t["seq"] for t in tracker.list_turns()] == [1, 2]

    # Rollback to 1: a.txt back to v1 (state at the end of turn 1), b.txt deleted.
    result = tracker.rollback_to(1)
    assert result["restored"] == 1
    assert result["deleted"] == 1
    assert a.read_text(encoding="utf-8") == "v1"
    assert not b.exists()
    # Turn list truncates to the rollback point.
    assert [t["seq"] for t in tracker.list_turns()] == [1]


def test_rollback_deletes_files_created_after_checkpoint(tmp_path):
    tracker, ws = make_tracker(tmp_path)
    # Turn 1 creates keep.txt THROUGH the write tool (capture: absent).
    tracker.begin_turn("t1")
    tracker.capture_before("write_file", {"path": "keep.txt"})
    (ws / "keep.txt").write_text("keep", encoding="utf-8")
    tracker.end_turn()
    # Turn 2 creates a nested file only.
    tracker.begin_turn("t2")
    nested = ws / "sub" / "dir" / "new.txt"
    tracker.capture_before("write_file", {"path": "sub/dir/new.txt"})
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("data", encoding="utf-8")
    tracker.end_turn()

    result = tracker.rollback_to(1)
    assert result["deleted"] == 1
    assert not nested.exists()
    assert (ws / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_earliest_capture_wins_for_rollback(tmp_path):
    """A file modified in turns 2 AND 3 rolls back, on rollback_to(1), to the
    pre-content captured at turn 2 (state at end of turn 1) — never to a later
    capture."""
    tracker, ws = make_tracker(tmp_path)
    a = ws / "a.txt"

    tracker.begin_turn("t1")
    tracker.capture_before("write_file", {"path": "a.txt"})  # pre: absent
    a.write_text("v1", encoding="utf-8")
    tracker.end_turn()
    tracker.begin_turn("t2")
    tracker.capture_before("write_file", {"path": "a.txt"})  # pre: v1
    a.write_text("v2", encoding="utf-8")
    tracker.end_turn()
    tracker.begin_turn("t3")
    tracker.capture_before("write_file", {"path": "a.txt"})  # pre: v2
    a.write_text("v3", encoding="utf-8")
    tracker.end_turn()

    assert [t["seq"] for t in tracker.list_turns()] == [1, 2, 3]
    tracker.rollback_to(1)
    assert a.read_text(encoding="utf-8") == "v1"


def test_rollback_to_middle_checkpoint(tmp_path):
    """rollback_to(K) restores the state at the end of turn K when later turns
    exist — here, state at end of turn 2 (v2), with turn 3 undone."""
    tracker, ws = make_tracker(tmp_path)
    a = ws / "a.txt"
    for seq, content in ((1, "v1"), (2, "v2"), (3, "v3")):
        tracker.begin_turn(f"t{seq}")
        tracker.capture_before("write_file", {"path": "a.txt"})
        a.write_text(content, encoding="utf-8")
        tracker.end_turn()
    tracker.rollback_to(2)
    assert a.read_text(encoding="utf-8") == "v2"
    assert [t["seq"] for t in tracker.list_turns()] == [1, 2]


def test_outside_writable_root_is_not_captured(tmp_path):
    tracker, ws = make_tracker(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    tracker.begin_turn("t1")
    entry = tracker.capture_before("write_file", {"path": str(outside)})
    assert entry is None
    tracker.end_turn()
    assert tracker.list_turns() == []


def test_failed_write_discard(tmp_path):
    tracker, ws = make_tracker(tmp_path)
    a = ws / "a.txt"
    tracker.begin_turn("t1")
    entry = tracker.capture_before("write_file", {"path": "a.txt"})
    assert entry is not None  # pre-content (absent) captured before the call
    tracker.discard(entry)  # ...but the call failed
    tracker.end_turn()
    assert tracker.list_turns() == []  # nothing tracked ⇒ no checkpoint
    assert not a.exists()


def test_skipped_oversized_file_is_reported_not_restored(tmp_path, monkeypatch):
    from coworker import checkpoints as cp_mod

    tracker, ws = make_tracker(tmp_path)
    big = ws / "big.bin"
    monkeypatch.setattr(cp_mod, "MAX_FILE_BYTES", 50)

    # Two turns: turn 1 creates the file (pre-content = absent, capturable);
    # turn 2 modifies it at 100 bytes (> the patched cap) — pre-content can't
    # be captured, so its record carries a skipped entry.
    for seq, payload in ((1, b"x" * 100), (2, b"y" * 100)):
        tracker.begin_turn(f"t{seq}")
        entry = tracker.capture_before("write_file", {"path": "big.bin"})
        if seq == 1:
            assert entry["blob"] is None  # absent before the first write
        else:
            assert entry["skipped"] == "too large"
        big.write_bytes(payload)
        tracker.end_turn()

    result = tracker.rollback_to(1)
    assert result["skipped"] == 1 and result["restored"] == 0
    assert big.read_bytes() == b"y" * 100  # left untouched, honestly reported


def test_persistence_across_tracker_instances(tmp_path):
    tracker, ws = make_tracker(tmp_path)
    a = ws / "a.txt"
    tracker.begin_turn("t1")
    tracker.capture_before("write_file", {"path": "a.txt"})  # pre: absent
    a.write_text("v1", encoding="utf-8")
    tracker.end_turn()
    tracker.begin_turn("t2")
    tracker.capture_before("write_file", {"path": "a.txt"})  # pre: v1
    a.write_text("v2", encoding="utf-8")
    tracker.end_turn()

    # A fresh instance (server restart, engine eviction) reads the same history
    # and can roll back without a live engine.
    again = SessionCheckpoints(
        session_id="sess-abc-1",
        state_dir=tmp_path / "state",
        roots_getter=lambda: [],
        workspace_root=ws,
    )
    assert [t["seq"] for t in again.list_turns()] == [1, 2]
    result = again.rollback_to(1)
    assert result["restored"] == 1
    assert a.read_text(encoding="utf-8") == "v1"


def test_rollback_guards(tmp_path):
    tracker, ws = make_tracker(tmp_path)
    a = ws / "a.txt"
    tracker.begin_turn("t1")
    tracker.capture_before("write_file", {"path": "a.txt"})
    # Mid-turn (captures pending): rollback refuses.
    with pytest.raises(CheckpointBusy):
        tracker.rollback_to(1)
    a.write_text("v", encoding="utf-8")
    tracker.end_turn()
    # Unknown seq: refuses.
    with pytest.raises(CheckpointNotFound):
        tracker.rollback_to(99)
    # After end_turn, the pending state is clear and rollback works.
    tracker.rollback_to(1)


def test_session_id_never_traverses(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    roots = [{"path": str(ws), "writable": True}]
    evil = SessionCheckpoints(
        session_id="../../evil",
        state_dir=tmp_path / "state",
        roots_getter=lambda: roots,
        workspace_root=ws,
    )
    evil.begin_turn("t1")
    (ws / "a.txt").write_text("v", encoding="utf-8")
    evil.capture_before("write_file", {"path": "a.txt"})
    evil.end_turn()
    # The store stayed inside the state dir; nothing escaped upward.
    assert (tmp_path / "state" / "checkpoints").is_dir()
    assert not (tmp_path / "state" / "evil").exists()
