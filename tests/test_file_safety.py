"""File editing safety triangle: read-before-write, staleness detection,
write_file similarity suggestion (provenance.py + engine._execute_sync)."""


from coworker.provenance import SessionFiles


def make_sf(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return SessionFiles(workspace_root=ws)


def test_read_tracking_roundtrip(tmp_path):
    sf = make_sf(tmp_path)
    ws = tmp_path / "ws"
    f = ws / "a.txt"
    f.write_text("hello")
    assert not sf.was_read(str(f))
    sf.record_read(str(f), f.stat().st_mtime)
    assert sf.was_read(str(f))
    assert not sf.is_stale(str(f))  # file hasn't changed


def test_staleness_detected_on_modify(tmp_path):
    sf = make_sf(tmp_path)
    f = tmp_path / "ws" / "a.txt"
    f.write_text("v1")
    sf.record_read(str(f), f.stat().st_mtime)
    assert not sf.is_stale(str(f))
    import time
    time.sleep(0.05)
    f.write_text("v2")
    assert sf.is_stale(str(f))


def test_staleness_not_tracked_for_unread(tmp_path):
    sf = make_sf(tmp_path)
    assert not sf.is_stale(str(tmp_path / "ws" / "never_read.txt"))


def test_staleness_missing_file_not_crash(tmp_path):
    sf = make_sf(tmp_path)
    sf.record_read(str(tmp_path / "ws" / "gone.txt"), 12345.0)
    (tmp_path / "ws" / "gone.txt").unlink(missing_ok=True)
    assert not sf.is_stale(str(tmp_path / "ws" / "gone.txt"))  # OSError → not stale


def test_record_read_path_resolution(tmp_path):
    sf = make_sf(tmp_path)
    ws = tmp_path / "ws"
    f = ws / "a.txt"
    f.write_text("hi")
    # Record with relative path — should resolve to same key as absolute.
    sf.record_read("a.txt", f.stat().st_mtime)
    assert sf.was_read(str(f))  # found via absolute path


# -- similarity suggestion (engine-level, tested via direct method call) -----------


def test_write_similarity_detected(tmp_path):
    """write_file with >90% similar content should trigger a suggestion."""
    from unittest.mock import MagicMock

    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine
    from coworker.providers.base import ToolCall
    from coworker.tools.registry import ToolRegistry

    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=MagicMock(), registry=registry, permissions=permissions, model="test"
    )
    f = tmp_path / "a.txt"
    old_content = "line1\nline2\n" + "\n".join(f"line{i}" for i in range(3, 50))
    f.write_text(old_content)

    # New content: same but with one line changed at the end — >90% similar.
    new_content = old_content.replace("line49", "line49 MODIFIED")
    tc = ToolCall(id="t1", name="write_file", arguments={"path": str(f), "content": new_content})

    hint = engine._check_write_similarity(tc)
    assert hint is not None
    assert "replace_in_file" in hint


def test_write_similarity_not_triggered_for_new_content(tmp_path):
    from unittest.mock import MagicMock

    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine
    from coworker.providers.base import ToolCall
    from coworker.tools.registry import ToolRegistry

    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=MagicMock(), registry=registry, permissions=permissions, model="test"
    )
    f = tmp_path / "a.txt"
    old = "completely different original content"
    f.write_text(old)
    new = "entirely new and unrelated text that shares nothing"
    tc = ToolCall(id="t1", name="write_file", arguments={"path": str(f), "content": new})
    hint = engine._check_write_similarity(tc)
    assert hint is None  # below 90% threshold


# -- file safety warnings (engine-level) ---------------------------------------------


def test_unread_file_warning(tmp_path):
    from unittest.mock import MagicMock

    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine
    from coworker.providers.base import ToolCall
    from coworker.tools.registry import ToolRegistry

    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=MagicMock(), registry=registry, permissions=permissions, model="test"
    )
    f = tmp_path / "existing.txt"
    f.write_text("content")
    tc = ToolCall(id="t1", name="write_file", arguments={"path": str(f), "content": "new"})
    warnings = engine._file_safety_warnings(tc)
    assert len(warnings) == 1
    assert "has not been read" in warnings[0]


def test_new_file_no_warning(tmp_path):
    from unittest.mock import MagicMock

    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine
    from coworker.providers.base import ToolCall
    from coworker.tools.registry import ToolRegistry

    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=MagicMock(), registry=registry, permissions=permissions, model="test"
    )
    f = tmp_path / "new_file.txt"
    tc = ToolCall(id="t1", name="write_file", arguments={"path": str(f), "content": "new"})
    warnings = engine._file_safety_warnings(tc)
    assert warnings == []  # new file — no read needed
