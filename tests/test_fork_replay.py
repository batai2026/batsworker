"""Session fork / replay tests（建议路线 P0-2）——fork 边界复制、回放、轨迹 diff。

测试隔离要点：
- autotitle 会消耗 scripted provider 的 turn（provider.complete 弹队列）， stub 掉；
- context_provider 每 turn 注入 `Now: %Y-%m-%d %H:%M` 与 roots 列表（含 per-session
  scratch 路径）——冻结时钟 + 共享 scratch，两分支的 outbound 才可字节级比对 digest。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import coworker.agent as agent_module
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app


class ScriptedProvider(ProviderClient):
    """Queued AssistantTurns; records every outbound payload for input assertions."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append(messages)
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text):
    return AssistantTurn(text=text, finish_reason="stop")


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = cls(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
        return fixed if tz is not None else fixed.astimezone()


def _mgr(tmp_path, monkeypatch, turns) -> SessionManager:
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider(turns))
    monkeypatch.setattr(manager, "_maybe_autotitle", lambda sid: None)
    scratch = tmp_path / "scratch-shared"
    scratch.mkdir(exist_ok=True)
    monkeypatch.setattr(manager, "_provision_scratch", lambda sid: str(scratch))
    monkeypatch.setattr(agent_module, "datetime", _FrozenDateTime)
    return manager


def _run_turn(manager, session_id, text):
    manager.get_engine(session_id, agent="chat")
    asyncio.run(manager.deliver_to_session(session_id, text))


def _replay_and_wait(manager, session_id, **kwargs):
    async def _go():
        result = await manager.replay_session(session_id, **kwargs)
        tasks = list(manager._replay_tasks)
        if tasks:
            await asyncio.gather(*tasks)
        return result

    return asyncio.run(_go())


def _turn_starts(manager, session_id):
    return [
        r
        for r in manager._trajectory_store().read(session_id, limit=10000)
        if r["kind"] == "turn_start"
    ]


# -- fork ------------------------------------------------------------------------


def test_tip_fork_copies_everything(tmp_path, monkeypatch):
    manager = _mgr(tmp_path, monkeypatch, [_text("a1"), _text("a2")])
    _run_turn(manager, "S", "q1")
    _run_turn(manager, "S", "q2")
    before = manager.session_store.load("S")

    result = manager.fork_session("S")

    assert result["ok"] is True
    assert result["fork"]["from_session"] == "S"
    assert result["fork"]["from_turn"] is None  # tip fork 无切割 turn
    assert result["fork"]["cut_messages"] == len(before.messages)
    forked = manager.session_store.load(result["session_id"])
    assert forked.messages == before.messages
    assert forked.workspace == before.workspace
    assert forked.model == before.model
    assert forked.mode == before.mode
    assert forked.agent == before.agent
    assert forked.title.endswith(" (fork)")
    # fork 对源会话只读不写
    assert manager.session_store.load("S").messages == before.messages


def test_turn_fork_cuts_at_boundary(tmp_path, monkeypatch):
    manager = _mgr(tmp_path, monkeypatch, [_text("a1"), _text("a2")])
    _run_turn(manager, "S", "q1")
    _run_turn(manager, "S", "q2")
    starts = _turn_starts(manager, "S")
    assert [s["turn"] for s in starts] == [1, 2]
    cut = starts[1]["data"]["message_count"] - 1  # turn 2 用户消息之前的所有消息

    result = manager.fork_session("S", turn=2)

    assert result["ok"] is True
    assert result["messages"] == cut
    assert "other_from_turn=2" in result["diff_hint"]
    source = manager.session_store.load("S")
    forked = manager.session_store.load(result["session_id"])
    assert forked.messages == source.messages[:cut]
    assert forked.fork == {"from_session": "S", "from_turn": 2, "cut_messages": cut}


def test_fork_errors(tmp_path, monkeypatch):
    manager = _mgr(tmp_path, monkeypatch, [_text("a1")])
    _run_turn(manager, "S", "q1")

    result = manager.fork_session("S", turn=99)
    assert result["ok"] is False and result["status"] == 400
    assert "turn 99" in result["detail"]

    manager._running_sessions.add("S")
    assert manager.fork_session("S")["status"] == 409
    manager._running_sessions.discard("S")

    assert manager.fork_session("__internal")["status"] == 403
    assert manager.fork_session("missing")["status"] == 404


def test_fork_inheritance_rules(tmp_path, monkeypatch):
    manager = _mgr(tmp_path, monkeypatch, [_text("a1"), _text("a2")])
    _run_turn(manager, "S", "q1")
    _run_turn(manager, "S", "q2")
    record = manager.session_store.load("S")
    record.grants = {"tools": ["bash"], "commands": ["ls"]}
    record.compaction = {"upto": 2, "summary": "condensed"}
    manager.session_store.save(record, touch=False)
    manager.session_store.set_team(
        "S",
        {"role": "worker", "team_id": "t1", "actor": "a", "lead_session": "L", "space": "s"},
    )

    tip = manager.session_store.load(manager.fork_session("S")["session_id"])
    assert tip.grants == record.grants
    assert tip.compaction == record.compaction  # tip fork 继承压缩锚点
    assert tip.team == {}  # team 不继承：fork 是普通会话
    assert tip.bindings == record.bindings
    assert tip.title.endswith(" (fork)") and len(tip.title) <= 80

    mid = manager.session_store.load(manager.fork_session("S", turn=2)["session_id"])
    assert mid.compaction == {}  # mid-cut 丢弃压缩锚点（对不齐截断的消息列表）
    assert mid.grants == record.grants
    assert mid.team == {}


# -- replay ------------------------------------------------------------------------


def test_replay_uses_lineage_input(tmp_path, monkeypatch):
    manager = _mgr(tmp_path, monkeypatch, [_text("a1"), _text("a2"), _text("a3")])
    _run_turn(manager, "S", "q1")
    _run_turn(manager, "S", "q2-second")
    fork_id = manager.fork_session("S", turn=2)["session_id"]

    result = _replay_and_wait(manager, fork_id)

    assert result["ok"] is True
    assert result["input_source"] == "lineage"
    assert result["input_chars"] == len("q2-second")
    outbound = manager.provider.calls[-1]
    assert outbound[-1]["role"] == "user"
    # <system-context> 块发送时拼接到最后一条 user 消息（不落盘）
    assert outbound[-1]["content"].startswith("q2-second\n\n<system-context>")
    forked = manager.session_store.load(fork_id)
    stored = forked.messages[-2]
    assert stored["role"] == "user"
    assert stored["content"] == "q2-second"  # ts 等 sidecar 不参与比对


def test_replay_override_and_errors(tmp_path, monkeypatch):
    manager = _mgr(
        tmp_path, monkeypatch, [_text("a1"), _text("a2"), _text("a3"), _text("a4")]
    )
    _run_turn(manager, "S", "q1")
    _run_turn(manager, "S", "q2")
    fork_id = manager.fork_session("S", turn=2)["session_id"]

    # 无血缘 + 无 input → 400
    result = asyncio.run(manager.replay_session("S"))
    assert result["ok"] is False and result["status"] == 400
    # tip fork 无 from_turn（没有"被切掉的那条输入"）→ 400
    tip_id = manager.fork_session("S")["session_id"]
    assert asyncio.run(manager.replay_session(tip_id))["status"] == 400
    # 运行中 → 409（replay 不 steering 插队）
    manager._running_sessions.add(fork_id)
    assert asyncio.run(manager.replay_session(fork_id, input="x"))["status"] == 409
    manager._running_sessions.discard(fork_id)

    result = _replay_and_wait(manager, fork_id, input="override-question")
    assert result["ok"] is True
    assert result["input_source"] == "override"
    assert manager.provider.calls[-1][-1]["content"].startswith("override-question")


# -- trajectory diff ------------------------------------------------------------------


def test_trajectory_diff(tmp_path, monkeypatch):
    # 字节比对的对象是两个 fork：fork 继承源的 extra_roots（含源的 scratch 目录），
    # 两侧 roots 天然不同，源↔fork 的 outbound 不可能字节一致；同源的两个 fork
    # roots 完全一致，相同输入的 replay 才能产出相同 digest（探针验证）。
    manager = _mgr(
        tmp_path,
        monkeypatch,
        [_text("a1"), _text("a2"), _text("a3"), _text("a4"), _text("a5")],
    )
    _run_turn(manager, "S", "q1")
    _run_turn(manager, "S", "q2")
    fork_a = manager.fork_session("S", turn=2)["session_id"]
    fork_b = manager.fork_session("S", turn=2)["session_id"]
    _replay_and_wait(manager, fork_a)  # fork A turn 1 == 源会话 turn 2 的输入
    _replay_and_wait(manager, fork_b)  # fork B turn 1 同上

    # 对齐（A turn 1 ↔ B turn 1）：相同上下文 + 相同输入 → digest 一致
    aligned = manager.trajectory_diff(fork_a, fork_b)
    assert aligned["compared"] == 1
    assert aligned["matched"] == 1
    pair = aligned["pairs"][0]
    assert pair["digest_match"] is True
    assert pair["turn_left"] == 1 and pair["turn_right"] == 1
    assert pair["bytes_left"] == pair["bytes_right"]
    assert pair["model_left"] == pair["model_right"]

    # 换输入：fork B 再跑一个不同输入的 turn → digest 不同 + 缺侧
    _run_turn(manager, fork_b, "B 专属输入")
    diff = manager.trajectory_diff(fork_a, fork_b)
    assert diff["compared"] == 1
    extra = diff["pairs"][-1]
    assert extra["missing"] == "left"
    assert extra["turn_left"] is None and extra["turn_right"] == 2

    # 不同输入对齐比（A turn 1 ↔ B turn 2）→ digest_match False
    mismatched = manager.trajectory_diff(fork_a, fork_b, other_from_turn=2)
    assert mismatched["compared"] == 1
    assert mismatched["matched"] == 0
    assert mismatched["pairs"][0]["digest_match"] is False

    # from_turn 过滤：两侧都跳过全部 turn → 无可比记录
    empty = manager.trajectory_diff(fork_a, fork_b, from_turn=3, other_from_turn=3)
    assert empty["compared"] == 0 and empty["pairs"] == []


def test_fork_independence(tmp_path, monkeypatch):
    manager = _mgr(tmp_path, monkeypatch, [_text("a1"), _text("a2")])
    _run_turn(manager, "S", "q1")
    fork_id = manager.fork_session("S")["session_id"]
    src_msgs = manager.session_store.load("S").messages
    src_traj_count = manager._trajectory_store().count("S")

    _run_turn(manager, fork_id, "brand new")

    assert manager.session_store.load("S").messages == src_msgs
    assert manager._trajectory_store().count("S") == src_traj_count
    # fork 的 trajectory 独立：新文件、seq/turn 均从 1 起
    records = manager._trajectory_store().read(fork_id, limit=10000)
    assert records and records[0]["seq"] == 1
    assert all(r["turn"] == 1 for r in records)
    assert (manager.data_dir / "conversations" / f"{fork_id}.jsonl").exists()


# -- routes ------------------------------------------------------------------------


def test_routes(tmp_path, monkeypatch):
    manager = _mgr(
        tmp_path, monkeypatch, [_text("a1"), _text("a2"), _text("a3"), _text("a4")]
    )
    _run_turn(manager, "S", "q1")
    _run_turn(manager, "S", "q2")
    client = TestClient(create_app(manager))

    resp = client.post("/v1/sessions/S/fork", json={"turn": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["fork"] == {
        "from_session": "S",
        "from_turn": 2,
        "cut_messages": body["messages"],
    }
    assert "diff_hint" in body
    fork_id = body["session_id"]

    assert client.post("/v1/sessions/S/fork", json={"turn": 99}).status_code == 400
    assert client.post("/v1/sessions/__x/fork", json={}).status_code == 403
    assert client.post("/v1/sessions/missing/fork", json={}).status_code == 404
    manager._running_sessions.add("S")
    assert client.post("/v1/sessions/S/fork", json={}).status_code == 409
    manager._running_sessions.discard("S")

    def wait_idle(session_id):
        deadline = time.time() + 10
        while manager.is_running(session_id) and time.time() < deadline:
            time.sleep(0.05)
        assert not manager.is_running(session_id)

    resp = client.post(f"/v1/sessions/{fork_id}/replay", json={})
    assert resp.status_code == 202
    assert resp.json()["input_source"] == "lineage"
    wait_idle(fork_id)

    # 无血缘会话 replay 无 input → 400；运行中 → 409
    assert client.post("/v1/sessions/S/replay", json={}).status_code == 400
    manager._running_sessions.add(fork_id)
    assert client.post(f"/v1/sessions/{fork_id}/replay", json={}).status_code == 409
    manager._running_sessions.discard(fork_id)

    # 第二个 fork + 同样的 lineage replay：两侧 outbound 字节一致（详见 test_trajectory_diff）
    fork_b = client.post("/v1/sessions/S/fork", json={"turn": 2}).json()["session_id"]
    resp = client.post(f"/v1/sessions/{fork_b}/replay", json={})
    assert resp.status_code == 202
    wait_idle(fork_b)

    resp = client.get(
        f"/v1/sessions/{fork_id}/trajectory/diff",
        params={"other": fork_b},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["left"] == fork_id and body["right"] == fork_b
    assert body["compared"] == 1
    assert body["matched"] == 1
    assert body["pairs"][0]["digest_match"] is True
