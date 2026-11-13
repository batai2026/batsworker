"""「模型 × harness」矩阵评测。

用录制会话的每一轮用户输入，在固定的 harness（能力槽 + 系统提示 + 权限模式）下重跑，
把轨迹账本里的 model_request 摘要（digest / bytes / model / tools）收集成可横向比较的报告。

设计约束：
- 输入取自 conversations/<sid>.jsonl 的原始用户消息（不脱敏），turn 边界由轨迹账本
  turn_start.message_count 给出（与 fork/replay 用的是同一映射）。
- 每个 (harness, model) cell 用一个全新的引擎 + 临时轨迹账本，互不污染。
- 默认不执行任何工具审批（引擎默认 deny），需要无人值守跑工具时显式 --auto-approve。
- --stub 用确定性假 provider，不联网、不花钱，供 pytest 覆盖脚手架。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from ..agent import build_engine
from ..agents.base import Agent
from ..attachments import content_to_text
from ..catalog import expand
from ..permissions import Mode
from ..personas.harness import HarnessSpec, builtin_specs, installed_specs
from ..providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
)
from ..secrets import state_dir
from ..trajectory import SessionTrajectory, TrajectoryStore

# harness 定义只有一份来源：persona manifest（code 取代码内建定义）。
# 这里预展开四个内置档以保持既有 CLI/测试键名；用户装上的 persona 由
# installed_specs() 在运行时合并。
HARNESSES: dict[str, HarnessSpec] = builtin_specs()

# Per-turn wall clock (seconds). Short enough that an unreachable endpoint surfaces a
# `timeout` row in the report instead of an hour of silence; long enough for a normal agent
# turn with one tool round trip. The provider SDKs default to a 600s request timeout with 2
# retries, which is why a dead endpoint used to look like a silent job.
DEFAULT_TURN_TIMEOUT = 180.0


@dataclass
class RecordedTurn:
    """录制会话里的一轮用户输入。"""

    turn: int
    input_text: str


# -- 录制侧 -------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def recorded_turns(
    session_id: str,
    *,
    conv_dir: str | Path | None = None,
    trajectory_dir: str | Path | None = None,
) -> list[RecordedTurn]:
    """从落盘消息 + 轨迹账本重建每轮的用户输入。

    turn_start 在用户消息 append 之后记录，因此 `message_count - 1` 正是该轮用户消息下标
    （与 fork 的切割点定义一致）。
    """
    base = state_dir()
    conv_path = Path(conv_dir).expanduser() if conv_dir else base / "conversations"
    traj_path = Path(trajectory_dir).expanduser() if trajectory_dir else base / "trajectory"
    messages = _read_jsonl(conv_path / f"{session_id}.jsonl")
    rows = _read_jsonl(traj_path / f"{session_id}.jsonl")
    turns: list[RecordedTurn] = []
    for row in rows:
        if row.get("kind") != "turn_start":
            continue
        data = row.get("data") or {}
        try:
            index = int(data.get("message_count", 0)) - 1
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(messages):
            continue
        message = messages[index] or {}
        if message.get("role") != "user":
            continue
        text = content_to_text(message.get("content"), image_placeholder="").strip()
        if not text:
            continue
        try:
            turn_no = int(row.get("turn") or 0)
        except (TypeError, ValueError):
            turn_no = 0
        turns.append(RecordedTurn(turn=turn_no, input_text=text))
    return turns


# -- provider -------------------------------------------------------------------


class StubProvider(ProviderClient):
    """确定性假 provider：每次调用返回一句固定文本，不联网、不调工具。"""

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):  # noqa: ANN001
        self.calls += 1
        return AssistantTurn(text=self.reply, finish_reason="stop")

    def capabilities(self, model):  # noqa: ANN001
        return ModelCapabilities()


def _real_provider_factory() -> Callable[[str, HarnessSpec], ProviderClient]:
    from ..providers.router import ProviderRouter
    from ..secrets import SecretStore

    def factory(model: str, _spec: HarnessSpec) -> ProviderClient:
        return ProviderRouter(SecretStore())

    return factory


def _stub_provider_factory() -> Callable[[str, HarnessSpec], ProviderClient]:
    def factory(_model: str, _spec: HarnessSpec) -> ProviderClient:
        return StubProvider()

    return factory


async def _allow_once(_request):  # noqa: ANN001
    from ..engine import ApprovalOutcome

    return ApprovalOutcome.ONCE


# -- 执行侧 -------------------------------------------------------------------


def _harness_agent(spec: HarnessSpec) -> Agent:
    caps = list(spec.capabilities)
    return Agent(
        name=spec.name,
        title=spec.name,
        system_prompt=spec.system_prompt,
        tool_factory=lambda ctx: expand(caps, ctx),
        requires_folder=spec.requires_folder,
    )


def _slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-") or "model"


def _requests_by_turn(records: list[dict[str, Any]]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for row in records:
        if row.get("kind") != "model_request":
            continue
        try:
            turn = int(row.get("turn") or 0)
        except (TypeError, ValueError):
            turn = 0
        data = row.get("data") or {}
        slot = out.setdefault(turn, {"requests": 0, "bytes": 0})
        slot["requests"] += 1
        try:
            slot["bytes"] += int(data.get("bytes") or 0)
        except (TypeError, ValueError):
            pass
        # 保留最后一次（出站负载）的摘要，供逐字节比较。
        out[turn]["digest"] = str(data.get("digest") or "")
        out[turn]["model"] = str(data.get("model") or "")
        out[turn]["tools"] = list(data.get("tools") or [])
    return out


def _tool_calls_by_turn(records: list[dict[str, Any]]) -> dict[int, list[str]]:
    """每个 turn 实际发生的 tool_call 名称序列（按账本顺序）。"""
    out: dict[int, list[str]] = {}
    for row in records:
        if row.get("kind") != "tool_call":
            continue
        try:
            turn = int(row.get("turn") or 0)
        except (TypeError, ValueError):
            turn = 0
        name = str((row.get("data") or {}).get("name") or "")
        out.setdefault(turn, []).append(name)
    return out


async def _run_cell(
    spec: HarnessSpec,
    model: str,
    turns: list[RecordedTurn],
    *,
    workspace: Path,
    provider_factory: Callable[[str, HarnessSpec], ProviderClient],
    auto_approve: bool,
    work_dir: Path,
    timeout: float = DEFAULT_TURN_TIMEOUT,
) -> dict[str, Any]:
    provider = provider_factory(model, spec)
    agent = _harness_agent(spec)
    engine = build_engine(
        agent=agent,
        workspace=workspace,
        model=model,
        mode=Mode(spec.mode),
        provider=provider,
        approver=_allow_once if auto_approve else None,
    )
    session_id = f"eval-{spec.name}-{_slug(model)}-{uuid.uuid4().hex[:6]}"
    store = TrajectoryStore(work_dir)
    engine.trajectory = SessionTrajectory(store, session_id)
    if engine.codemode is not None:
        engine.codemode.trajectory = engine.trajectory

    per_turn: list[dict[str, Any]] = []
    error: Optional[str] = None
    for turn in turns:
        started = time.perf_counter()
        status = "ok"

        async def _drain() -> None:
            async for _event in engine.run(turn.input_text):
                pass

        try:
            # A turn is bounded on purpose. The provider SDKs default to a 600s request
            # timeout with 2 retries, so an unreachable endpoint (a China-hosted gateway
            # from a US runner, say) used to sit silent for ~30 minutes with no log line.
            # Cancelling here aborts the SDK call too: it is an await inside this task.
            await asyncio.wait_for(_drain(), timeout=timeout)
        except asyncio.TimeoutError:
            status = "timeout"
            error = f"turn {turn.turn} exceeded {timeout:g}s"
        except Exception as exc:  # 一个 cell 失败不影响矩阵其它 cell
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
        per_turn.append(
            {
                "turn": turn.turn,
                "status": status,
                "duration_s": round(time.perf_counter() - started, 3),
            }
        )
        if status in ("error", "timeout"):
            break
    records = store.read(session_id, limit=100000)
    requests = _requests_by_turn(records)
    tool_calls = _tool_calls_by_turn(records)
    for item in per_turn:
        data = requests.get(int(item["turn"]) or 0, {})
        item["digest"] = data.get("digest", "")
        item["bytes"] = data.get("bytes", 0)
        item["model"] = data.get("model", "")
        item["tools"] = data.get("tools", [])
        item["requests"] = data.get("requests", 0)
        names = tool_calls.get(int(item["turn"]) or 0, [])
        item["tool_calls"] = len(names)
        item["tool_names"] = names
    return {
        "harness": spec.name,
        "model": model,
        "session_id": session_id,
        "status": "error" if error else "ok",
        "error": error,
        "turns": per_turn,
    }


async def run_matrix(
    *,
    turns: list[RecordedTurn],
    models: list[str],
    harness_names: list[str],
    workspace: str | Path,
    provider_factory: Optional[Callable[[str, HarnessSpec], ProviderClient]] = None,
    auto_approve: bool = False,
    out_path: str | Path | None = None,
    work_dir: str | Path | None = None,
    harness_specs: Optional[dict[str, HarnessSpec]] = None,
    timeout: float = DEFAULT_TURN_TIMEOUT,
) -> dict[str, Any]:
    """跑完整矩阵，返回（并可选写出）报告字典。

    ``harness_specs`` 默认取 ``installed_specs()``（内置四档 + 用户已装 persona）；
    测试可显式注入``{name: HarnessSpec}``。
    """
    specs = harness_specs if harness_specs is not None else installed_specs()
    unknown = [n for n in harness_names if n not in specs]
    if unknown:
        raise ValueError(f"unknown harness: {', '.join(unknown)}")
    ws = Path(workspace).expanduser().resolve()
    factory = provider_factory or _real_provider_factory()
    base = Path(work_dir).expanduser() if work_dir else Path(_temp_dir())
    base.mkdir(parents=True, exist_ok=True)

    cells: list[dict[str, Any]] = []
    for name in harness_names:
        spec = specs[name]
        for model in models:
            cells.append(
                await _run_cell(
                    spec,
                    model,
                    turns,
                    workspace=ws,
                    provider_factory=factory,
                    auto_approve=auto_approve,
                    work_dir=base,
                    timeout=timeout,
                )
            )

    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "workspace": str(ws),
        "turns": [t.turn for t in turns],
        "models": models,
        "harnesses": harness_names,
        "harness_sources": {n: specs[n].source for n in harness_names},
        "cells": cells,
    }
    if out_path is not None:
        target = Path(out_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _temp_dir() -> Path:
    import tempfile

    return Path(tempfile.mkdtemp(prefix="coworker-eval-"))


# -- 基线对比（--case，P2-11） ---------------------------------------------------


DEFAULT_MAX_BYTES_RATIO = 0.10


def _in_git_worktree(path: Path) -> bool:
    """工作目录是否落在一个 git 仓库里（环境块会因此带上 branch/status/commits）。"""
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return True
    return False


def _resolve_cell_baseline(
    baseline: dict[str, Any], harness: str, model: str
) -> dict[str, Any] | None:
    """从一个基线里取出与该 cell 对应的逐 turn 记录。

    两种基线形态都认：

    * **扁平**（`turns` 列表）——stub 黄金用例，一个基线只对应一个确定性模型；
    * **按 cell**（`cells` 列表）——真实模型矩阵，一个文件里存多个模型各自的记录。

    真实模型之间的字节量差得很远（同一轮 9131 vs 5660），拿 stub 基线去卡真实模型
    只会得到必然失败的比较，所以真实模型必须按 (harness, model) 对齐。
    """
    cells = baseline.get("cells")
    if not isinstance(cells, list):
        return baseline
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        if str(cell.get("model") or "") != model:
            continue
        cell_harness = str(cell.get("harness") or "")
        if harness and cell_harness and cell_harness != harness:
            continue
        return cell
    return None


def compare_baseline(
    cell: dict[str, Any], baseline: dict[str, Any]
) -> list[dict[str, Any]]:
    """把一个 cell 的逐 turn 结果与 capture 基线对齐比较（仅参照，不作断言）。"""
    base_cell = _resolve_cell_baseline(
        baseline, str(cell.get("harness") or ""), str(cell.get("model") or "")
    ) or {}
    base_by_turn: dict[int, dict[str, Any]] = {}
    for item in base_cell.get("turns") or []:
        try:
            base_by_turn[int(item.get("turn") or 0)] = item
        except (TypeError, ValueError):
            continue
    diffs: list[dict[str, Any]] = []
    for item in cell.get("turns") or []:
        try:
            turn = int(item.get("turn") or 0)
        except (TypeError, ValueError):
            continue
        base = base_by_turn.get(turn) or {}
        base_tools = list(base.get("tools") or [])
        # 对比的是实际发生的 tool_call 名称序列（tool_names），不是模型面工具列表
        # （tools 由 harness 决定，与录制时不同不算偏差）。
        tools = list(item.get("tool_names") or [])
        base_bytes = int(base.get("bytes") or 0)
        bytes_now = int(item.get("bytes") or 0)
        diffs.append(
            {
                "turn": turn,
                "bytes": bytes_now,
                "base_bytes": base_bytes,
                "bytes_delta": bytes_now - base_bytes,
                "tool_calls": int(item.get("tool_calls") or 0),
                "base_tool_calls": int(base.get("tool_calls") or 0),
                "tools": tools,
                "base_tools": base_tools,
                "tools_match": tools == base_tools,
            }
        )
    return diffs


def check_baseline(
    report: dict[str, Any], *, max_bytes_ratio: float = DEFAULT_MAX_BYTES_RATIO
) -> dict[str, Any]:
    """把 vs_baseline 升级为可断言的回归门禁。

    默认行为不变（基线仅参照）；本函数只在调用方显式要求时使用。
    返回 ``{"ok", "max_bytes_ratio", "checked_turns", "failures"}``。
    """
    baseline = report.get("baseline") or {}
    per_cell = isinstance(baseline.get("cells"), list)

    def _base_turns(harness: str, model: str) -> tuple[set[int], bool]:
        """该 cell 在基线里出现的 turn 集合，以及基线里到底有没有这个 cell。"""
        base_cell = _resolve_cell_baseline(baseline, harness, model)
        if base_cell is None:
            return set(), False
        turns: set[int] = set()
        for item in base_cell.get("turns") or []:
            try:
                turns.add(int(item.get("turn") or 0))
            except (TypeError, ValueError):
                continue
        return turns, True

    failures: list[dict[str, Any]] = []
    checked = 0
    for cell in report.get("cells") or []:
        harness = cell.get("harness", "")
        model = cell.get("model", "")
        if cell.get("status") != "ok":
            failures.append(
                {
                    "harness": harness,
                    "model": model,
                    "turn": None,
                    "kind": "cell_error",
                    "detail": cell.get("error") or "",
                }
            )
            continue
        base_turns, has_cell = _base_turns(harness, model)
        if per_cell and not has_cell:
            # 基线是按模型存的，却没有这个模型：新模型必须先录一次基线，不能静默跳过。
            failures.append(
                {
                    "harness": harness,
                    "model": model,
                    "turn": None,
                    "kind": "missing_cell",
                    "detail": "model absent from this baseline",
                }
            )
            continue
        for diff in cell.get("vs_baseline") or []:
            turn = diff.get("turn")
            checked += 1
            if turn not in base_turns:
                failures.append(
                    {
                        "harness": harness,
                        "model": model,
                        "turn": turn,
                        "kind": "missing_turn",
                        "detail": "turn missing from baseline",
                    }
                )
                continue
            if not diff.get("tools_match"):
                failures.append(
                    {
                        "harness": harness,
                        "model": model,
                        "turn": turn,
                        "kind": "tool_drift",
                        "detail": {
                            "tools": diff.get("tools") or [],
                            "base_tools": diff.get("base_tools") or [],
                        },
                    }
                )
            base_bytes = int(diff.get("base_bytes") or 0)
            delta = int(diff.get("bytes_delta") or 0)
            if base_bytes > 0:
                ratio = abs(delta) / base_bytes
            else:
                ratio = float(abs(delta))
            if ratio > max_bytes_ratio:
                failures.append(
                    {
                        "harness": harness,
                        "model": model,
                        "turn": turn,
                        "kind": "bytes_drift",
                        "detail": {
                            "bytes": diff.get("bytes"),
                            "base_bytes": base_bytes,
                            "delta": delta,
                            "ratio": round(ratio, 4),
                        },
                    }
                )
    return {
        "ok": not failures,
        "max_bytes_ratio": max_bytes_ratio,
        "checked_turns": checked,
        "failures": failures,
    }


# -- CLI ----------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coworker.eval.matrix",
        description="用录制轨迹跑「模型 × harness」矩阵评测",
    )
    parser.add_argument("--session", help="录制的会话 id（conversations/<id>.jsonl）")
    parser.add_argument(
        "--case",
        help="capture 导出的评测输入包目录（messages.jsonl + baseline.json）；与 --session 互斥",
    )
    parser.add_argument("--conv-dir", help="会话消息目录（默认 <state>/conversations）")
    parser.add_argument("--trajectory-dir", help="轨迹账本目录（默认 <state>/trajectory）")
    parser.add_argument("--models", help="逗号分隔的模型 id，如 openai:gpt-5.6-sol,anthropic:claude-opus-4-8")
    parser.add_argument(
        "--harnesses",
        default="minimal",
        help="逗号分隔的 harness id（内置 minimal / code / creator / code-mode，或任意已装 persona）",
    )
    parser.add_argument("--workspace", default=".", help="评测用工作区（默认当前目录）")
    parser.add_argument("--stub", action="store_true", help="用确定性假 provider，不联网")
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="自动批准工具调用（无人值守跑工具时才开；默认引擎拒绝）",
    )
    parser.add_argument("--max-turns", type=int, default=0, help="最多跑前 N 轮（0 = 全部）")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TURN_TIMEOUT,
        help=(
            "单轮墙钟上限（秒，默认 "
            f"{DEFAULT_TURN_TIMEOUT:g}）。端点不可达时让该轮快速记为 timeout，"
            "而不是等 SDK 默认的 600s×3 重试"
        ),
    )
    parser.add_argument("--out", help="报告 JSON 输出路径")
    parser.add_argument(
        "--check-baseline",
        action="store_true",
        help="把 --case 的基线对比作为门禁：漂移即退出码 3（默认仅参照）",
    )
    parser.add_argument(
        "--baseline-file",
        default=None,
        help=(
            "指定基线文件（默认取 case 目录下的 baseline.json）。真实模型矩阵请指向 "
            "real-baseline.json：它是按 (harness, model) 存的，能各自比各自"
        ),
    )
    parser.add_argument(
        "--max-bytes-ratio",
        type=float,
        default=DEFAULT_MAX_BYTES_RATIO,
        help=f"check-baseline 允许的出站字节相对偏差（默认 {DEFAULT_MAX_BYTES_RATIO}）",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="用本次 --stub 运行重写 --case 的 baseline.json（刷新黄金基线，需单 cell）",
    )
    args = parser.parse_args(argv)

    harness_names = [h.strip() for h in args.harnesses.split(",") if h.strip()]
    specs = installed_specs()
    bad = [h for h in harness_names if h not in specs]
    if bad:
        print(f"unknown harness: {', '.join(bad)}", file=sys.stderr)
        return 2

    models = [m.strip() for m in (args.models or "").split(",") if m.strip()]
    if not models:
        if not args.stub:
            print("--models is required unless --stub is set", file=sys.stderr)
            return 2
        models = ["stub:model"]

    if args.session and args.case:
        print("--case and --session are mutually exclusive", file=sys.stderr)
        return 2
    if args.check_baseline and not args.case:
        print("--check-baseline requires --case", file=sys.stderr)
        return 2
    if args.max_bytes_ratio < 0:
        print("--max-bytes-ratio must be >= 0", file=sys.stderr)
        return 2
    if args.write_baseline or args.check_baseline:
        # 环境块里有 git 快照（branch / status / recent commits）。workspace 落在仓库里时，
        # 基线会把这几行一起拍进去，于是和 CI 的 `${{ runner.temp }}` 空目录差出几百字节 ——
        # 2026-11-13 就是这样把 push 触发的 golden gate 弄红的。
        ws_path = Path(args.workspace).expanduser().resolve()
        if _in_git_worktree(ws_path):
            print(
                f"warning: workspace {ws_path} is inside a git work tree — the environment "
                "block embeds the git snapshot (branch/status/recent commits), so the bytes "
                "will not match a run on an empty directory; use an empty non-git dir for "
                "baseline work (CI uses ${{ runner.temp }}/eval-ws)",
                file=sys.stderr,
            )
    if args.write_baseline:
        if not args.case or not args.stub:
            print("--write-baseline requires --case and --stub", file=sys.stderr)
            return 2
        if args.check_baseline:
            print(
                "--write-baseline and --check-baseline are mutually exclusive",
                file=sys.stderr,
            )
            return 2
        if len(models) * len(harness_names) != 1:
            print(
                "--write-baseline requires exactly one model × one harness",
                file=sys.stderr,
            )
            return 2

    baseline: Optional[dict[str, Any]] = None
    if args.case:
        from .capture import load_case  # 延迟导入：capture 顶层依赖本模块

        turns, baseline = load_case(args.case)
        if args.baseline_file:
            base_path = Path(args.baseline_file).expanduser()
            try:
                baseline = json.loads(base_path.read_text(encoding="utf-8"))
            except OSError as exc:
                print(f"cannot read --baseline-file {base_path}: {exc}", file=sys.stderr)
                return 2
            except json.JSONDecodeError as exc:
                print(
                    f"--baseline-file {base_path} is not valid JSON: {exc}", file=sys.stderr
                )
                return 2
        if not turns:
            print(
                f"no turns found in case {args.case!r} (missing messages.jsonl?)",
                file=sys.stderr,
            )
            return 2
        if args.check_baseline and baseline is None:
            print(
                f"no baseline.json found in case {args.case!r} (and no --baseline-file); "
                "cannot check baseline",
                file=sys.stderr,
            )
            return 2
    else:
        if not args.session:
            print("--session is required (recorded session id), or pass --case", file=sys.stderr)
            return 2
        turns = recorded_turns(
            args.session, conv_dir=args.conv_dir, trajectory_dir=args.trajectory_dir
        )
    if args.max_turns:
        turns = turns[: args.max_turns]
    if not turns:
        print(
            f"no recorded turns found for session {args.session!r} "
            "(check --conv-dir / --trajectory-dir)",
            file=sys.stderr,
        )
        return 2

    factory = _stub_provider_factory() if args.stub else _real_provider_factory()
    report = asyncio.run(
        run_matrix(
            turns=turns,
            models=models,
            harness_names=harness_names,
            workspace=args.workspace,
            provider_factory=factory,
            auto_approve=args.auto_approve,
            out_path=args.out,
            timeout=args.timeout,
        )
    )
    if args.write_baseline:
        from .capture import BASELINE_FILE, baseline_from_cell

        cell = report["cells"][0]
        if cell.get("status") != "ok":
            print(f"cell failed: {cell.get('error') or ''}", file=sys.stderr)
            return 1
        session_id = str((baseline or {}).get("session_id") or Path(args.case).name)
        target = Path(args.case).expanduser() / BASELINE_FILE
        target.write_text(
            json.dumps(
                baseline_from_cell(cell, session_id=session_id),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"baseline written: {target} ({len(cell['turns'])} turns)")
        return 0
    gate: Optional[dict[str, Any]] = None
    if baseline is not None:
        report["baseline"] = baseline
        for cell in report["cells"]:
            cell["vs_baseline"] = compare_baseline(cell, baseline)
        if args.check_baseline:
            gate = check_baseline(report, max_bytes_ratio=args.max_bytes_ratio)
            report["baseline_check"] = gate
        if args.out:  # run_matrix 已先行落盘，对比字段补上后需重写
            target = Path(args.out).expanduser()
            target.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    for cell in report["cells"]:
        digests = [t.get("digest", "")[:12] for t in cell["turns"]]
        print(
            f"{cell['harness']:<8} {cell['model']:<32} {cell['status']:<5} "
            f"turns={len(cell['turns'])} digests={digests}"
        )
        if cell.get("error"):
            print(f"  error: {cell['error']}")
        for diff in cell.get("vs_baseline") or []:
            mark = "ok" if diff["tools_match"] else "tools≠"
            print(
                f"  turn {diff['turn']}: bytes {diff['bytes']} vs base "
                f"{diff['base_bytes']} (Δ{diff['bytes_delta']:+d}), "
                f"tool_calls {diff['tool_calls']}/{diff['base_tool_calls']} [{mark}]"
            )
    if gate is not None:
        if gate["ok"]:
            print(f"baseline check: ok ({gate['checked_turns']} turns)")
        else:
            print(
                f"baseline check: {len(gate['failures'])} failure(s) "
                f"(max_bytes_ratio={gate['max_bytes_ratio']})",
                file=sys.stderr,
            )
            for failure in gate["failures"]:
                turn = "" if failure["turn"] is None else f" turn {failure['turn']}"
                print(
                    f"  {failure['harness']} {failure['model']}{turn}: {failure['kind']}",
                    file=sys.stderr,
                )
    if any(c["status"] != "ok" for c in report["cells"]):
        return 1
    if gate is not None and not gate["ok"]:
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
