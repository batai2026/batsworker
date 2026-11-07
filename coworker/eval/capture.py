"""真实会话 → 评测输入包一键导出（record → replay，P2-11）。

从落盘的 ``conversations/<sid>.jsonl`` + 轨迹账本导出：

- ``messages.jsonl``：每轮一行 ``{"turn": n, "input": <原始用户文本>}``，turn 边界
  复用 ``matrix.recorded_turns()``（账本 ``turn_start.message_count`` 映射，
  与 fork/replay 同一来源）。
- ``baseline.json``：每轮参照摘要（model / bytes / requests / tool_calls / tools），
  仅供 ``matrix --case`` 对比，**不作断言**。

隐私门禁：消息体按原样导出（评测需要原始输入），因此导出前扫描轨迹原文，
命中脱敏标记 ``[redacted]``（说明该会话碰过密钥形数据）即拒绝，``--force`` 放行。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

from ..secrets import state_dir
from .matrix import _read_jsonl, recorded_turns

# 与 trajectory.redact() 的替换串一致（那边是模块私有常量，这里自持一份）。
REDACTION_MARKER = "[redacted]"

BASELINE_FILE = "baseline.json"
MESSAGES_FILE = "messages.jsonl"


def session_has_redactions(
    session_id: str, *, trajectory_dir: str | Path | None = None
) -> bool:
    """轨迹原文中是否出现脱敏标记（说明该会话有数据被判定为密钥形）。"""
    base = Path(trajectory_dir).expanduser() if trajectory_dir else state_dir() / "trajectory"
    try:
        return REDACTION_MARKER in (base / f"{session_id}.jsonl").read_text(encoding="utf-8")
    except OSError:
        return False


def _baseline_turns(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 turn 聚合账本摘要：model 取最后一次 model_request，bytes/requests 求和，
    tools 为该轮 tool_call 名称序列（按账本顺序）。"""
    per_turn: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for row in records:
        try:
            turn = int(row.get("turn") or 0)
        except (TypeError, ValueError):
            continue
        slot = per_turn.get(turn)
        if slot is None:
            slot = {"turn": turn, "model": "", "bytes": 0, "requests": 0, "tools": []}
            per_turn[turn] = slot
            order.append(turn)
        kind = row.get("kind")
        data = row.get("data") or {}
        if kind == "model_request":
            slot["requests"] += 1
            try:
                slot["bytes"] += int(data.get("bytes") or 0)
            except (TypeError, ValueError):
                pass
            slot["model"] = str(data.get("model") or slot["model"])
        elif kind == "tool_call":
            name = str(data.get("name") or "")
            if name:
                slot["tools"].append(name)
    return [
        {
            "turn": per_turn[t]["turn"],
            "model": per_turn[t]["model"],
            "bytes": per_turn[t]["bytes"],
            "requests": per_turn[t]["requests"],
            "tool_calls": len(per_turn[t]["tools"]),
            "tools": per_turn[t]["tools"],
        }
        for t in order
    ]


def capture_case(
    session_id: str,
    out_dir: str | Path,
    *,
    conv_dir: str | Path | None = None,
    trajectory_dir: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """导出评测输入包，返回写出摘要；轨迹含脱敏命中且未 force 时抛 ValueError。"""
    if not force and session_has_redactions(session_id, trajectory_dir=trajectory_dir):
        raise ValueError(
            f"session {session_id!r} contains redacted secret-shaped data in its "
            "trajectory — refusing to export (pass force=True / --force to override)"
        )
    turns = recorded_turns(session_id, conv_dir=conv_dir, trajectory_dir=trajectory_dir)
    if not turns:
        raise ValueError(f"no recorded turns found for session {session_id!r}")

    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    (out / MESSAGES_FILE).write_text(
        "".join(
            json.dumps({"turn": t.turn, "input": t.input_text}, ensure_ascii=False) + "\n"
            for t in turns
        ),
        encoding="utf-8",
    )
    base = (
        Path(trajectory_dir).expanduser() if trajectory_dir else state_dir() / "trajectory"
    )
    records = _read_jsonl(base / f"{session_id}.jsonl")
    baseline = {
        "session_id": session_id,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "turns": _baseline_turns(records),
    }
    (out / BASELINE_FILE).write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "session_id": session_id,
        "out": str(out),
        "turns": len(turns),
        "messages": str(out / MESSAGES_FILE),
        "baseline": str(out / BASELINE_FILE),
    }


def load_case(case_dir: str | Path) -> tuple[list, Optional[dict[str, Any]]]:
    """读取 capture 导出的输入包：(turns, baseline)；baseline 缺失时返回 None。"""
    from .matrix import RecordedTurn  # 延迟导入，保持包级懒导出契约

    case = Path(case_dir).expanduser()
    turns = []
    for row in _read_jsonl(case / MESSAGES_FILE):
        try:
            turn_no = int(row.get("turn") or 0)
        except (TypeError, ValueError):
            continue
        text = str(row.get("input") or "").strip()
        if text:
            turns.append(RecordedTurn(turn=turn_no, input_text=text))
    try:
        baseline = json.loads((case / BASELINE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        baseline = None
    return turns, baseline if isinstance(baseline, dict) else None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coworker.eval.capture",
        description="把真实会话一键导出为评测输入包（messages.jsonl + baseline.json）",
    )
    parser.add_argument("session", help="录制的会话 id（conversations/<id>.jsonl）")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--conv-dir", help="会话消息目录（默认 <state>/conversations）")
    parser.add_argument("--trajectory-dir", help="轨迹账本目录（默认 <state>/trajectory）")
    parser.add_argument(
        "--force",
        action="store_true",
        help="轨迹含脱敏命中（密钥形数据）时仍然导出",
    )
    args = parser.parse_args(argv)
    try:
        summary = capture_case(
            args.session,
            args.out,
            conv_dir=args.conv_dir,
            trajectory_dir=args.trajectory_dir,
            force=args.force,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"captured {summary['turns']} turns -> {summary['out']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
