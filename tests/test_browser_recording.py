"""Browser recording → replay (connectors/browser_recording.py + wiring)."""



from coworker.connectors.browser_recording import (
    BrowserRecording,
    load_recording,
    list_recordings,
    save_recording,
)


# -- recording buffer ----------------------------------------------------------


def test_start_stop_roundtrip():
    rec = BrowserRecording()
    assert not rec.active
    rec.start("test-flow")
    assert rec.active
    rec.record("browser_open_url", {"url": "https://example.com"}, "ok")
    rec.record("browser_click", {"target": "#btn"}, "ok")
    actions = rec.stop()
    assert actions is not None
    assert len(actions) == 2
    assert actions[0]["tool"] == "browser_open_url"
    assert not rec.active


def test_record_skips_non_recorded_tools():
    rec = BrowserRecording()
    rec.start("t")
    rec.record("browser_read_page", {}, "ok")  # read tool — not recorded
    rec.record("browser_screenshot", {}, "ok")  # read tool
    rec.record("browser_click", {"target": "#x"}, "ok")  # action — recorded
    actions = rec.stop()
    assert len(actions) == 1
    assert actions[0]["tool"] == "browser_click"


def test_record_skips_failed_calls():
    rec = BrowserRecording()
    rec.start("t")
    rec.record("browser_click", {"target": "#x"}, "error")  # failed — skipped
    actions = rec.stop()
    assert actions == []


def test_stop_when_not_recording_returns_none():
    rec = BrowserRecording()
    assert rec.stop() is None


def test_restart_clears_buffer():
    rec = BrowserRecording()
    rec.start("first")
    rec.record("browser_click", {"target": "#x"}, "ok")
    rec.start("second")  # restart clears
    assert rec.action_count == 0


# -- persistence -----------------------------------------------------------------


def test_save_and_load(tmp_path):
    actions = [
        {"tool": "browser_open_url", "args": {"url": "https://x.com"}, "ts": 1.0},
        {"tool": "browser_click", "args": {"target": "#go"}, "ts": 2.0},
    ]
    save_recording(tmp_path, "my-flow", actions, title="My Flow")
    data = load_recording(tmp_path, "my-flow")
    assert data is not None
    assert data["title"] == "My Flow"
    assert len(data["actions"]) == 2
    assert data["actions"][0]["tool"] == "browser_open_url"


def test_load_nonexistent_returns_none(tmp_path):
    assert load_recording(tmp_path, "nope") is None


def test_list_recordings(tmp_path):
    save_recording(tmp_path, "flow-a", [{"tool": "x", "args": {}}], title="Flow A")
    save_recording(tmp_path, "flow-b", [{"tool": "y", "args": {}}], title="Flow B")
    recordings = list_recordings(tmp_path)
    assert len(recordings) == 2
    assert {r["name"] for r in recordings} == {"flow-a", "flow-b"}


def test_name_sanitized_for_path_safety(tmp_path):
    save_recording(tmp_path, "../../evil", [{"tool": "x", "args": {}}])
    # Nothing escaped the replays dir.
    assert (tmp_path / "browser-replays").is_dir()
    assert list((tmp_path / "browser-replays").glob("*.json"))
    assert not (tmp_path.parent / "evil.json").exists()


# -- replay logic (integration with mocked browser tools) -----------------------


def test_replay_via_tool_map(tmp_path):
    """Simulate the browser_replay tool: load a recording, re-execute each
    action through a tool map, verify success and failure stops."""
    actions = [
        {"tool": "browser_open_url", "args": {"url": "https://x.com"}, "ts": 1},
        {"tool": "browser_click", "args": {"target": "#go"}, "ts": 2},
        {"tool": "browser_type", "args": {"target": "#q", "text": "hello"}, "ts": 3},
    ]
    save_recording(tmp_path, "flow", actions)
    data = load_recording(tmp_path, "flow")

    executed: list[str] = []

    def browser_open_url(url, **kw):
        executed.append(f"open:{url}")
        return {"ok": True}

    def browser_click(target, **kw):
        executed.append(f"click:{target}")
        return {"ok": True}

    def browser_type(target, text, **kw):
        executed.append(f"type:{target}:{text}")
        return {"ok": True}

    tool_map = {
        "browser_open_url": browser_open_url,
        "browser_click": browser_click,
        "browser_type": browser_type,
    }

    for action in data["actions"]:
        fn = tool_map[action["tool"]]
        fn(**action["args"])

    assert executed == [
        "open:https://x.com",
        "click:#go",
        "type:#q:hello",
    ]


def test_replay_stops_on_failure(tmp_path):
    actions = [
        {"tool": "browser_open_url", "args": {"url": "https://x.com"}, "ts": 1},
        {"tool": "browser_click", "args": {"target": "#broken"}, "ts": 2},
        {"tool": "browser_type", "args": {"target": "#q", "text": "x"}, "ts": 3},
    ]
    save_recording(tmp_path, "flow", actions)
    data = load_recording(tmp_path, "flow")

    executed: list[str] = []

    def browser_open_url(url, **kw):
        executed.append("open")
        return {"ok": True}

    def browser_click(target, **kw):
        return {"error": "element not found"}

    def browser_type(target, text, **kw):
        executed.append("type")
        return {"ok": True}

    tool_map = {"browser_open_url": browser_open_url, "browser_click": browser_click, "browser_type": browser_type}

    results = []
    ok_all = True
    for i, action in enumerate(data["actions"]):
        fn = tool_map.get(action["tool"])
        result = fn(**action["args"])
        ok = isinstance(result, dict) and result.get("ok", not result.get("error"))
        results.append({"step": i, "status": "ok" if ok else "error"})
        if not ok:
            ok_all = False
            break

    assert not ok_all
    assert len(executed) == 1  # only the first step ran
    assert results[-1]["status"] == "error"
