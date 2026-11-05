"""Turn ledger tests — store behaviour, redaction, retention, engine wiring, read API."""

from __future__ import annotations

import asyncio
import json

import aisuite as ai
from fastapi.testclient import TestClient

from coworker import trajectory
from coworker.config import Config
from coworker.engine import ApprovalOutcome, PermissionRequest, TurnEngine
from coworker.permissions import PermissionEngine
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ToolCall,
)
from coworker.server import SessionManager, create_app
from coworker.tools import ToolRegistry
from coworker.trajectory import SessionTrajectory, TrajectoryStore


# -- scripted provider (same shape as tests/test_engine.py) ------------------------


def _text_turn(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool_turn(name, args, call_id="call_1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, turns, *, approver=None):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    return TurnEngine(
        provider=ScriptedProvider(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=approver,
        max_iterations=12,
    )


def _run(engine, user_input):
    async def _collect():
        return [event async for event in engine.run(user_input)]

    return asyncio.run(_collect())


def _jsonl(base, session_id="s1") -> str:
    return (base / f"{session_id}.jsonl").read_text(encoding="utf-8")


# -- store ------------------------------------------------------------------------


def test_append_read_roundtrip(tmp_path):
    store = TrajectoryStore(tmp_path / "traj")
    writer = SessionTrajectory(store, "s1")
    writer.begin_turn()
    writer.record("model_request", model="gpt-5.5")
    writer.end_turn(status="completed")

    records = store.read("s1")
    assert [record["kind"] for record in records] == [
        "turn_start",
        "model_request",
        "turn_end",
    ]
    assert [record["seq"] for record in records] == [1, 2, 3]
    assert records[0]["turn"] == 1
    assert records[0]["v"] == trajectory.SCHEMA_VERSION
    assert store.count("s1") == 3
    assert [record["seq"] for record in store.read("s1", since_seq=2)] == [3]
    assert store.read("s1", limit=1) == records[-1:]
    # A reloaded recorder continues the sequence and turn counter.
    resumed = SessionTrajectory(TrajectoryStore(tmp_path / "traj"), "s1")
    assert resumed.turn == 1
    resumed.record("notice", kind="x")
    assert store.read("s1")[-1]["seq"] == 4


def test_write_redacts_secret_keys_and_values(tmp_path):
    secret = "sk-" + "a" * 20
    github = "ghp_" + "b" * 30
    store = TrajectoryStore(tmp_path / "traj")
    writer = SessionTrajectory(store, "s1")
    writer.record(
        "notice",
        kind="auth",
        text=f"Authorization: Bearer {secret} and token {github}",
        api_key=secret,
        headers={"Authorization": "Bearer abcdefghij"},
        nested={"password": "hunter2"},
    )

    raw = _jsonl(tmp_path / "traj")
    assert secret not in raw
    assert github not in raw
    assert "hunter2" not in raw
    assert "abcdefghij" not in raw
    assert "[redacted]" in raw


def test_retention_keeps_newest(tmp_path):
    store = TrajectoryStore(tmp_path / "traj", max_records=5)
    writer = SessionTrajectory(store, "s")
    for index in range(8):
        writer.record("tick", index=index)

    rows = store.read("s", limit=100)
    assert len(rows) == 5
    assert [row["seq"] for row in rows] == [4, 5, 6, 7, 8]
    assert store.count("s") == 5


def test_record_never_raises_when_store_is_unwritable(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    writer = SessionTrajectory(TrajectoryStore(blocker), "s")
    writer.begin_turn()
    writer.record("x", a=1)
    writer.end_turn(status="ok")  # no exception escapes


def test_outbound_snapshot_stable_and_content_free():
    messages = [
        {"role": "system", "content": "system prompt MARKER-SYS"},
        {"role": "user", "content": "hello MARKER-BODY"},
    ]
    first = trajectory.outbound_snapshot(
        messages, model="m", tool_names=["read_file"], settings_keys=["temperature"]
    )
    second = trajectory.outbound_snapshot(
        messages, model="m", tool_names=["read_file"], settings_keys=["temperature"]
    )
    assert first == second
    assert first["digest"] == second["digest"]
    assert first["roles"] == {"system": 1, "user": 1}
    assert first["messages"] == 2
    assert first["bytes"] > 0
    assert first["tools"] == ["read_file"]
    assert first["settings"] == ["temperature"]
    payload = json.dumps(first)
    assert "MARKER-SYS" not in payload
    assert "MARKER-BODY" not in payload


# -- engine wiring ------------------------------------------------------------------


def test_engine_turn_writes_ledger(tmp_path):
    marker = "USER-BODY-MARKER-UNIQUE"
    engine = _engine(tmp_path, [_text_turn("done")])
    store = TrajectoryStore(tmp_path / "traj")
    engine.trajectory = SessionTrajectory(store, "s1")

    events = _run(engine, marker)

    kinds = [record["kind"] for record in store.read("s1")]
    assert kinds[0] == "turn_start"
    assert "model_request" in kinds
    assert kinds[-1] == "turn_end"
    records = store.read("s1")
    assert records[-1]["data"]["status"] == "completed"
    assert events[-1].data["status"] == "completed"
    request = next(record for record in records if record["kind"] == "model_request")
    assert request["data"]["model"] == "gpt-5.5"
    assert request["data"]["messages"] >= 1
    # The ledger never stores the prompt/body text, only its digest/size.
    assert marker not in _jsonl(tmp_path / "traj")


def test_context_injection_recorded_once_per_request(tmp_path):
    context_text = "live directory listing MARKER-CONTEXT"
    engine = _engine(tmp_path, [_text_turn("done")])
    engine.context_provider = lambda: context_text
    store = TrajectoryStore(tmp_path / "traj")
    engine.trajectory = SessionTrajectory(store, "s1")

    _run(engine, "hi")

    injections = [
        record for record in store.read("s1") if record["kind"] == "context_injection"
    ]
    assert len(injections) == 1
    assert injections[0]["data"]["chars"] == len(context_text)
    assert "MARKER-CONTEXT" not in _jsonl(tmp_path / "traj")


def test_tool_turn_records_args_and_result_metadata(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine = _engine(
        tmp_path,
        [_tool_turn("read_file", {"path": "a.txt"}), _text_turn("read it")],
    )
    store = TrajectoryStore(tmp_path / "traj")
    engine.trajectory = SessionTrajectory(store, "s1")

    _run(engine, "read a.txt")

    records = store.read("s1")
    call = next(record for record in records if record["kind"] == "tool_call")
    result = next(record for record in records if record["kind"] == "tool_result")
    assert call["data"]["name"] == "read_file"
    assert call["data"]["call_id"] == "call_1"
    assert call["data"]["argument_keys"] == ["path"]
    assert result["data"]["status"] == "ok"
    assert result["data"]["result_chars"] > 0
    assert "duration_ms" in result["data"]
    # Argument values and result content stay out of the file.
    payload = json.dumps([call["data"], result["data"]])
    assert "a.txt" not in payload
    assert "hello" not in payload


def test_denied_tool_records_outcome(tmp_path):
    async def deny(_request: PermissionRequest):
        return ApprovalOutcome.DENY

    engine = _engine(
        tmp_path,
        [
            _tool_turn("write_file", {"path": "new.py", "content": "x"}),
            _text_turn("skipped"),
        ],
        approver=deny,
    )
    store = TrajectoryStore(tmp_path / "traj")
    engine.trajectory = SessionTrajectory(store, "s1")

    _run(engine, "create new.py")

    kinds = [record["kind"] for record in store.read("s1")]
    assert "approval_requested" in kinds
    assert "approval_resolved" in kinds
    assert "tool_result" in kinds
    resolved = next(
        record for record in store.read("s1") if record["kind"] == "approval_resolved"
    )
    assert resolved["data"]["status"] == "denied"
    assert "x" not in json.dumps(resolved["data"])


# -- manager / read API ---------------------------------------------------------------


def test_manager_attach_respects_config(tmp_path, monkeypatch):
    import coworker.config as config_module

    manager = SessionManager(
        data_dir=tmp_path / "data", provider=ScriptedProvider([])
    )

    def _engine_for_attach() -> TurnEngine:
        return TurnEngine(
            provider=ScriptedProvider([]),
            registry=ToolRegistry(),
            permissions=PermissionEngine(workspace_root=tmp_path),
            model="gpt-5.5",
        )

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda *args, **kwargs: Config(trajectory_enabled=False),
    )
    disabled = _engine_for_attach()
    manager._attach_checkpoints("off", disabled)
    assert getattr(disabled, "trajectory", None) is None

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda *args, **kwargs: Config(trajectory_enabled=True),
    )
    enabled = _engine_for_attach()
    manager._attach_checkpoints("on", enabled)
    assert enabled.trajectory is not None
    assert enabled.trajectory.session_id == "on"


def test_read_api_route(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    writer = SessionTrajectory(manager._trajectory_store(), "s1")
    writer.record("notice", kind="demo", text="hello")
    client = TestClient(create_app(manager))

    body = client.get("/v1/sessions/s1/trajectory").json()
    assert body["session_id"] == "s1"
    assert body["count"] == 1
    assert body["records"][0]["kind"] == "notice"

    after = client.get("/v1/sessions/s1/trajectory", params={"since_seq": 1}).json()
    assert after["records"] == []
