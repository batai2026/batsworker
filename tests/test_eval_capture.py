"""真实会话 → 评测输入包（record → replay，P2-11）的单元测试。

覆盖：capture 导出 messages.jsonl + baseline.json（字段与账本一致）、隐私门禁
（脱敏命中默认拒绝、--force 放行）、matrix --case 消费跑通并给出 vs_baseline 对比、
CLI 互斥校验。
"""

from __future__ import annotations

import json

import pytest

from coworker.eval import matrix
from coworker.eval.capture import (
    BASELINE_FILE,
    MESSAGES_FILE,
    capture_case,
    load_case,
    session_has_redactions,
)
from coworker.eval.matrix import compare_baseline, run_matrix


def _write_jsonl(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


@pytest.fixture
def recorded_session(tmp_path):
    """两轮会话，账本带 model_request / tool_call 摘要，供基线聚合。"""
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
            {
                "seq": 2,
                "turn": 1,
                "kind": "model_request",
                "data": {"model": "openai:gpt-x", "bytes": 1000, "digest": "a" * 64},
            },
            {
                "seq": 3,
                "turn": 1,
                "kind": "tool_call",
                "data": {"name": "read_file", "argument_keys": ["path"]},
            },
            {"seq": 4, "turn": 1, "kind": "turn_end", "data": {"status": "ok"}},
            {"seq": 5, "turn": 2, "kind": "turn_start", "data": {"message_count": 3}},
            {
                "seq": 6,
                "turn": 2,
                "kind": "model_request",
                "data": {"model": "openai:gpt-x", "bytes": 2000, "digest": "b" * 64},
            },
        ],
    )
    return conv_dir, traj_dir


def test_capture_exports_messages_and_baseline(recorded_session, tmp_path):
    conv_dir, traj_dir = recorded_session
    out = tmp_path / "case"
    summary = capture_case("s1", out, conv_dir=conv_dir, trajectory_dir=traj_dir)
    assert summary["turns"] == 2

    messages = [
        json.loads(line)
        for line in (out / MESSAGES_FILE).read_text(encoding="utf-8").splitlines()
    ]
    assert messages == [
        {"turn": 1, "input": "list the files"},
        {"turn": 2, "input": "fix the bug"},
    ]

    baseline = json.loads((out / BASELINE_FILE).read_text(encoding="utf-8"))
    assert baseline["session_id"] == "s1"
    (t1, t2) = baseline["turns"]
    assert t1 == {
        "turn": 1,
        "model": "openai:gpt-x",
        "bytes": 1000,
        "requests": 1,
        "tool_calls": 1,
        "tools": ["read_file"],
    }
    assert t2["turn"] == 2
    assert t2["bytes"] == 2000
    assert t2["tool_calls"] == 0
    assert t2["tools"] == []


def test_capture_refuses_redacted_session(recorded_session, tmp_path):
    conv_dir, traj_dir = recorded_session
    # 账本里出现脱敏标记（说明有数据被判定为密钥形）→ 默认拒绝导出
    with (traj_dir / "s1.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"seq": 7, "turn": 2, "kind": "tool_call",
                 "data": {"name": "run_shell", "authorization": "[redacted]"}}
            )
            + "\n"
        )
    assert session_has_redactions("s1", trajectory_dir=traj_dir)
    with pytest.raises(ValueError, match="redacted"):
        capture_case("s1", tmp_path / "case", conv_dir=conv_dir, trajectory_dir=traj_dir)
    # force 放行
    summary = capture_case(
        "s1", tmp_path / "case", conv_dir=conv_dir, trajectory_dir=traj_dir, force=True
    )
    assert summary["turns"] == 2


def test_capture_rejects_session_without_turns(tmp_path):
    with pytest.raises(ValueError, match="no recorded turns"):
        capture_case(
            "ghost",
            tmp_path / "case",
            conv_dir=tmp_path / "conv",
            trajectory_dir=tmp_path / "traj",
        )


def test_load_case_roundtrip_and_missing_baseline(recorded_session, tmp_path):
    conv_dir, traj_dir = recorded_session
    out = tmp_path / "case"
    capture_case("s1", out, conv_dir=conv_dir, trajectory_dir=traj_dir)
    turns, baseline = load_case(out)
    assert [t.turn for t in turns] == [1, 2]
    assert baseline["turns"][0]["bytes"] == 1000

    (out / BASELINE_FILE).unlink()
    turns, baseline = load_case(out)
    assert [t.turn for t in turns] == [1, 2]
    assert baseline is None


@pytest.mark.asyncio
async def test_matrix_case_runs_and_compares_baseline(recorded_session, tmp_path):
    conv_dir, traj_dir = recorded_session
    out = tmp_path / "case"
    capture_case("s1", out, conv_dir=conv_dir, trajectory_dir=traj_dir)
    turns, baseline = load_case(out)

    report = await run_matrix(
        turns=turns,
        models=["stub:model"],
        harness_names=["minimal"],
        workspace=tmp_path,
        provider_factory=matrix._stub_provider_factory(),
        work_dir=tmp_path / "work",
    )
    (cell,) = report["cells"]
    assert cell["status"] == "ok"
    diffs = compare_baseline(cell, baseline)
    assert [d["turn"] for d in diffs] == [1, 2]
    d1 = diffs[0]
    assert d1["base_bytes"] == 1000
    assert d1["bytes"] > 0
    assert d1["bytes_delta"] == d1["bytes"] - 1000
    # stub provider 不调工具 → 与基线的 tool_call 序列必然不同，能看出来
    assert d1["tool_calls"] == 0
    assert d1["base_tool_calls"] == 1
    assert d1["tools_match"] is False
    d2 = diffs[1]
    assert d2["base_tools"] == []
    assert d2["tools_match"] is True


def test_cli_case_mutually_exclusive_with_session(recorded_session, tmp_path):
    conv_dir, traj_dir = recorded_session
    out = tmp_path / "case"
    capture_case("s1", out, conv_dir=conv_dir, trajectory_dir=traj_dir)
    assert (
        matrix.main(["--session", "s1", "--case", str(out), "--stub"])
        == 2
    )


def test_cli_case_stub_end_to_end(recorded_session, tmp_path, capsys):
    conv_dir, traj_dir = recorded_session
    out = tmp_path / "case"
    capture_case("s1", out, conv_dir=conv_dir, trajectory_dir=traj_dir)
    report_path = tmp_path / "report.json"
    rc = matrix.main(
        [
            "--case",
            str(out),
            "--stub",
            "--workspace",
            str(tmp_path),
            "--out",
            str(report_path),
        ]
    )
    assert rc == 0
    printed = capsys.readouterr().out
    assert "vs base" in printed  # 基线对比摘要已打印
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["baseline"]["session_id"] == "s1"
    (cell,) = report["cells"]
    assert [d["turn"] for d in cell["vs_baseline"]] == [1, 2]


def test_cli_case_missing_messages(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert matrix.main(["--case", str(empty), "--stub"]) == 2
