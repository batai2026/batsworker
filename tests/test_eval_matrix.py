"""「模型 × harness」矩阵评测脚手架（P1-4）的单元测试。

覆盖：从录制会话重建每轮用户输入、stub provider 的离线矩阵跑通、未知 harness 拒绝、
以及 CLI 的参数校验（不联网）。
"""

from __future__ import annotations

import json

import pytest

from coworker.eval import matrix
from coworker.eval.matrix import HARNESSES, RecordedTurn, recorded_turns, run_matrix


def _write_jsonl(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


@pytest.fixture
def recorded_session(tmp_path):
    """两轮会话：turn_start.message_count 指向该轮用户消息 append 之后的下标。"""
    conv_dir = tmp_path / "conv"
    traj_dir = tmp_path / "traj"
    _write_jsonl(
        conv_dir / "s1.jsonl",
        [
            {"role": "user", "content": "list the files"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "fix the bug"},
        ],
    )
    _write_jsonl(
        traj_dir / "s1.jsonl",
        [
            {"seq": 1, "turn": 1, "kind": "turn_start", "data": {"message_count": 1}},
            {"seq": 2, "turn": 1, "kind": "turn_end", "data": {"status": "ok"}},
            {"seq": 3, "turn": 2, "kind": "turn_start", "data": {"message_count": 3}},
        ],
    )
    return conv_dir, traj_dir


def test_recorded_turns_maps_message_index_to_user_text(recorded_session):
    conv_dir, traj_dir = recorded_session
    turns = recorded_turns("s1", conv_dir=conv_dir, trajectory_dir=traj_dir)
    assert [t.turn for t in turns] == [1, 2]
    assert [t.input_text for t in turns] == ["list the files", "fix the bug"]


def test_recorded_turns_skips_out_of_range_and_non_user(tmp_path):
    conv_dir = tmp_path / "conv"
    traj_dir = tmp_path / "traj"
    _write_jsonl(
        conv_dir / "s1.jsonl",
        [{"role": "assistant", "content": "not a user turn"}],
    )
    _write_jsonl(
        traj_dir / "s1.jsonl",
        [
            {"seq": 1, "turn": 1, "kind": "turn_start", "data": {"message_count": 1}},
            {"seq": 2, "turn": 2, "kind": "turn_start", "data": {"message_count": 9}},
        ],
    )
    assert recorded_turns("s1", conv_dir=conv_dir, trajectory_dir=traj_dir) == []


def test_runs_are_declared_with_minimal_and_creator():
    assert set(HARNESSES) == {"minimal", "code", "creator", "code-mode"}
    assert HARNESSES["minimal"].capabilities == ("code_files", "shell")
    assert "todo" in HARNESSES["creator"].capabilities


def test_code_mode_harness_collapses_to_exec_slot():
    cm = HARNESSES["code-mode"]
    assert cm.capabilities == ("code_mode",)  # 工具面坍缩：模型只见 exec
    # 提示取自内置 manifest 正文（单一来源），保留单程序编排协议。
    assert "ONE Python program" in cm.system_prompt
    assert "exec" in cm.system_prompt
    assert cm.source == "manifest"


@pytest.mark.asyncio
async def test_run_matrix_stub_runs_code_mode(tmp_path):
    report = await run_matrix(
        turns=[RecordedTurn(turn=1, input_text="hello")],
        models=["stub:model"],
        harness_names=["code-mode"],
        workspace=tmp_path,
        provider_factory=matrix._stub_provider_factory(),
        work_dir=tmp_path / "work",
    )
    assert report["harnesses"] == ["code-mode"]
    (cell,) = report["cells"]
    assert cell["status"] == "ok"
    (turn,) = cell["turns"]
    assert turn["status"] == "ok"
    assert len(turn["digest"]) == 64
    assert "exec" in turn["tools"]
    assert "read_file" not in turn["tools"]  # 域工具坍缩进桥接，不再上模型面
    assert "run_shell" not in turn["tools"]


@pytest.mark.asyncio
async def test_run_matrix_records_a_timeout_instead_of_hanging(tmp_path):
    """An unreachable endpoint must land as a `timeout` row, not as silent minutes.

    The provider SDKs default to a 600s request timeout with two retries, so before this
    guard a dead endpoint looked like a hung job: no output, no failure, no end. The turn is
    now bounded by `--timeout`, and cancelling the await aborts the SDK call with it.
    """
    import time

    from coworker.eval import matrix
    from coworker.personas.harness import builtin_specs
    from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient

    class _SlowProvider(ProviderClient):
        # The engine calls `complete` inside `asyncio.to_thread`, so the blocking sleep is
        # what a hung HTTP call looks like from the turn's point of view. It is kept short:
        # the timeout fires at 0.1s and the thread finishes a second later on its own.
        def complete(self, *, model, messages, tools=None, **settings):  # noqa: ANN001
            time.sleep(1.0)
            return AssistantTurn(text="too late", finish_reason="stop")

        def capabilities(self, model):  # noqa: ANN001
            return ModelCapabilities()

    report = await matrix.run_matrix(
        turns=[
            matrix.RecordedTurn(turn=1, input_text="hello"),
            matrix.RecordedTurn(turn=2, input_text="again"),
        ],
        models=["stub:model"],
        harness_names=["minimal"],
        workspace=tmp_path,
        provider_factory=lambda _model, _spec: _SlowProvider(),
        work_dir=tmp_path / "work",
        harness_specs=builtin_specs(),
        timeout=0.1,
    )
    (cell,) = report["cells"]
    assert cell["status"] == "error"
    assert "exceeded 0.1s" in (cell["error"] or "")
    # The first turn times out and the cell stops there instead of trying the second one.
    (turn,) = cell["turns"]
    assert turn["status"] == "timeout"
    assert turn["turn"] == 1


@pytest.mark.asyncio
async def test_run_matrix_stub_is_offline_and_reports_digests(tmp_path):
    report = await run_matrix(
        turns=[RecordedTurn(turn=1, input_text="hello")],
        models=["stub:model"],
        harness_names=["minimal"],
        workspace=tmp_path,
        provider_factory=matrix._stub_provider_factory(),
        work_dir=tmp_path / "work",
    )
    assert report["turns"] == [1]
    assert report["harnesses"] == ["minimal"]
    (cell,) = report["cells"]
    assert cell["status"] == "ok"
    assert cell["model"] == "stub:model"
    (turn,) = cell["turns"]
    assert turn["status"] == "ok"
    assert turn["requests"] == 1
    assert len(turn["digest"]) == 64
    assert turn["bytes"] > 0
    assert "read_file" in turn["tools"]  # minimal harness 的 code_files 槽展开
    assert "grep" not in turn["tools"]  # 没有 search 槽


@pytest.mark.asyncio
async def test_run_matrix_rejects_unknown_harness(tmp_path):
    with pytest.raises(ValueError, match="unknown harness"):
        await run_matrix(
            turns=[RecordedTurn(turn=1, input_text="hi")],
            models=["stub:model"],
            harness_names=["nope"],
            workspace=tmp_path,
            provider_factory=matrix._stub_provider_factory(),
            work_dir=tmp_path / "work",
        )


def test_cli_requires_models_unless_stub():
    assert matrix.main(["--session", "s1"]) == 2
    assert matrix.main(["--session", "s1", "--harnesses", "nope"]) == 2
    assert matrix.main(["--models", "stub:model"]) == 2
