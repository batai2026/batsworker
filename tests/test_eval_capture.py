"""真实会话 → 评测输入包（record → replay，P2-11）的单元测试。

覆盖：capture 导出 messages.jsonl + baseline.json（字段与账本一致）、隐私门禁
（脱敏命中默认拒绝、--force 放行）、matrix --case 消费跑通并给出 vs_baseline 对比、
基线门禁（--check-baseline 的判定规则与退出码）、CLI 互斥校验。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from coworker.eval import matrix
from coworker.eval.capture import (
    BASELINE_FILE,
    MESSAGES_FILE,
    capture_case,
    load_case,
    session_has_redactions,
)
from coworker.eval.matrix import check_baseline, compare_baseline, run_matrix


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


# -- 基线门禁（--check-baseline） ------------------------------------------------


def _report_with_cell(**turn_fields):
    """构造最小报告：一个 cell，一轮，字段可覆盖。"""
    turn = {
        "turn": 1,
        "status": "ok",
        "digest": "d" * 64,
        "bytes": 1000,
        "model": "stub:model",
        "tools": ["read_file"],
        "requests": 1,
        "tool_calls": 1,
        "tool_names": ["read_file"],
        **(turn_fields or {}),
    }
    return {
        "baseline": {"turns": [{"turn": 1, "bytes": 1000, "tools": ["read_file"]}]},
        "cells": [
            {
                "harness": "minimal",
                "model": "stub:model",
                "status": "ok",
                "error": None,
                "turns": [turn],
                "vs_baseline": [
                    {
                        "turn": 1,
                        "bytes": turn["bytes"],
                        "base_bytes": 1000,
                        "bytes_delta": turn["bytes"] - 1000,
                        "tool_calls": turn["tool_calls"],
                        "base_tool_calls": 1,
                        "tools": turn["tool_names"],
                        "base_tools": ["read_file"],
                        "tools_match": turn["tool_names"] == ["read_file"],
                    }
                ],
            }
        ],
    }


def test_check_baseline_passes_on_match():
    result = check_baseline(_report_with_cell())
    assert result["ok"] is True
    assert result["checked_turns"] == 1
    assert result["failures"] == []
    assert result["max_bytes_ratio"] == matrix.DEFAULT_MAX_BYTES_RATIO


def test_check_baseline_flags_tool_drift():
    result = check_baseline(
        _report_with_cell(tool_calls=0, tool_names=[], bytes=1000)
    )
    assert result["ok"] is False
    assert [f["kind"] for f in result["failures"]] == ["tool_drift"]
    assert result["failures"][0]["detail"]["base_tools"] == ["read_file"]


def test_check_baseline_bytes_threshold():
    # 1000 → 1200 是 20% 偏差：默认 10% 拦下，放宽到 0.25 放行
    result = check_baseline(_report_with_cell(bytes=1200))
    assert result["ok"] is False
    (failure,) = result["failures"]
    assert failure["kind"] == "bytes_drift"
    assert failure["detail"]["ratio"] == 0.2
    assert check_baseline(_report_with_cell(bytes=1200), max_bytes_ratio=0.25)["ok"]
    # 恰好等于阈值不判失败（用严格大于）
    assert check_baseline(_report_with_cell(bytes=1100), max_bytes_ratio=0.1)["ok"]


def test_check_baseline_flags_missing_turn_and_cell_error():
    missing = _report_with_cell()
    missing["baseline"] = {"turns": []}
    result = check_baseline(missing)
    assert [f["kind"] for f in result["failures"]] == ["missing_turn"]

    broken = _report_with_cell()
    broken["cells"][0]["status"] = "error"
    broken["cells"][0]["error"] = "RuntimeError: boom"
    result = check_baseline(broken)
    assert [f["kind"] for f in result["failures"]] == ["cell_error"]
    assert result["checked_turns"] == 0


def test_cli_check_baseline_requires_case(tmp_path):
    assert matrix.main(["--session", "s1", "--stub", "--check-baseline"]) == 2
    assert (
        matrix.main(
            ["--case", str(tmp_path / "case"), "--stub", "--max-bytes-ratio", "-1"]
        )
        == 2
    )


def test_cli_check_baseline_requires_baseline_file(recorded_session, tmp_path):
    conv_dir, traj_dir = recorded_session
    out = tmp_path / "case"
    capture_case("s1", out, conv_dir=conv_dir, trajectory_dir=traj_dir)
    (out / BASELINE_FILE).unlink()
    assert matrix.main(["--case", str(out), "--stub", "--check-baseline"]) == 2


def _baseline_from_cell(cell, session_id="s1"):
    """把一次确定性 stub 跑的 cell 结果转成 capture 基线格式。"""
    return {
        "session_id": session_id,
        "turns": [
            {
                "turn": t["turn"],
                "model": t["model"],
                "bytes": t["bytes"],
                "requests": t["requests"],
                "tool_calls": t["tool_calls"],
                "tools": t["tool_names"],
            }
            for t in cell["turns"]
        ],
    }


def test_cli_check_baseline_end_to_end(recorded_session, tmp_path, capsys):
    conv_dir, traj_dir = recorded_session
    out = tmp_path / "case"
    capture_case("s1", out, conv_dir=conv_dir, trajectory_dir=traj_dir)
    turns, _ = load_case(out)

    # 先跑一次确定性 stub，用其结果覆盖捕获基线 → 再跑应完全一致
    seed = asyncio.run(
        run_matrix(
            turns=turns,
            models=["stub:model"],
            harness_names=["minimal"],
            workspace=tmp_path,
            provider_factory=matrix._stub_provider_factory(),
            work_dir=tmp_path / "seed",
        )
    )
    reproducible = _baseline_from_cell(seed["cells"][0])
    (out / BASELINE_FILE).write_text(
        json.dumps(reproducible, ensure_ascii=False), encoding="utf-8"
    )

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
            "--check-baseline",
        ]
    )
    assert rc == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["baseline_check"]["ok"] is True
    assert report["baseline_check"]["checked_turns"] == 2
    assert "baseline check: ok" in capsys.readouterr().out


def test_cli_check_baseline_drift_exit_code(recorded_session, tmp_path, capsys):
    conv_dir, traj_dir = recorded_session
    out = tmp_path / "case"
    capture_case("s1", out, conv_dir=conv_dir, trajectory_dir=traj_dir)

    # 捕获基线含 read_file，stub 不调工具 → 工具序列漂移必然失败
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
            "--check-baseline",
        ]
    )
    assert rc == 3
    report = json.loads(report_path.read_text(encoding="utf-8"))
    kinds = {f["kind"] for f in report["baseline_check"]["failures"]}
    assert "tool_drift" in kinds
    assert "failure(s)" in capsys.readouterr().err


# -- 黄金基线刷新（--write-baseline）与 CI 用例 ---------------------------------


def test_cli_write_baseline_refreshes_case(recorded_session, tmp_path):
    conv_dir, traj_dir = recorded_session
    out = tmp_path / "case"
    capture_case("s1", out, conv_dir=conv_dir, trajectory_dir=traj_dir)
    rc = matrix.main(
        ["--case", str(out), "--stub", "--workspace", str(tmp_path), "--write-baseline"]
    )
    assert rc == 0
    baseline = json.loads((out / BASELINE_FILE).read_text(encoding="utf-8"))
    assert baseline["session_id"] == "s1"
    assert [t["turn"] for t in baseline["turns"]] == [1, 2]
    # stub 不发起 tool_call → 重写后的基线工具序列为空
    assert [t["tools"] for t in baseline["turns"]] == [[], []]
    assert all(t["requests"] == 1 for t in baseline["turns"])
    # 重写后同一命令即可通过门禁
    assert (
        matrix.main(
            ["--case", str(out), "--stub", "--workspace", str(tmp_path), "--check-baseline"]
        )
        == 0
    )


def test_cli_write_baseline_rejects_bad_args(recorded_session, tmp_path, capsys):
    conv_dir, traj_dir = recorded_session
    out = tmp_path / "case"
    capture_case("s1", out, conv_dir=conv_dir, trajectory_dir=traj_dir)
    case_args = ["--case", str(out)]
    # 必须同时给 --stub（先于单 cell 校验命中 --models 缺失，同样是 rc=2）
    assert matrix.main([*case_args, "--write-baseline"]) == 2
    # 必须给 --case
    assert matrix.main(["--session", "s1", "--stub", "--write-baseline"]) == 2
    # 写与校验互斥
    assert (
        matrix.main([*case_args, "--stub", "--write-baseline", "--check-baseline"]) == 2
    )
    assert "mutually exclusive" in capsys.readouterr().err
    # 只允许单 cell（1 harness × 1 model）
    assert matrix.main([*case_args, "--stub", "--write-baseline", "--models", "a,b"]) == 2
    assert (
        matrix.main(
            [*case_args, "--stub", "--write-baseline", "--harnesses", "minimal,code"]
        )
        == 2
    )
    assert "exactly one model" in capsys.readouterr().err


def test_golden_ci_case_shape():
    root = Path(__file__).resolve().parents[1] / "evals" / "ci-case"
    assert (root / MESSAGES_FILE).is_file()
    assert (root / BASELINE_FILE).is_file()
    baseline = json.loads((root / BASELINE_FILE).read_text(encoding="utf-8"))
    assert baseline["session_id"] == "ci-case"
    assert [t["turn"] for t in baseline["turns"]] == [1, 2]
    assert [t["tools"] for t in baseline["turns"]] == [[], []]
