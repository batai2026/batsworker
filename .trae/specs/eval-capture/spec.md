# P2-11 真实会话 → 评测输入一键生成（record → replay）

## 背景

P1-4 的评测矩阵（`coworker/eval/matrix.py`）已经能用录制会话重跑「模型 × harness」，
但输入包要靠人肉拼装：翻 `conversations/<sid>.jsonl`、对轨迹账本找 turn 边界、
再手工整理成可重放的序列。一条真实会话从「跑完」到「变成评测资产」之间缺一键导出。

## 目标

`capture <session-id> --out <dir>` 一键导出评测输入包；`matrix --case <dir>` 直接消费，
报告附与基线的 bytes/tools 对比；含密钥脱敏命中的会话默认拒绝导出。

## 设计

1. `coworker/eval/capture.py`
   - `capture_case(session_id, out_dir, *, conv_dir=None, trajectory_dir=None, force=False)`
     导出两个文件：
     - `messages.jsonl`：每轮一行 `{"turn": n, "input": <原始用户文本>}`。turn 边界复用
       `matrix.recorded_turns()`（账本 `turn_start.message_count` 映射，与 fork/replay 同一来源）。
     - `baseline.json`：`{"session_id", "captured_at", "turns": [{turn, model, bytes,
       requests, tool_calls, tools}]}`。`model/bytes/requests` 取自 `model_request` 摘要
       （bytes 求和、model 取最后一次），`tools` 为该轮 `tool_call` 名称序列。
       **基线只作参照，不作断言。**
   - 隐私门禁：导出前扫描轨迹原文，命中脱敏标记 `[redacted]` 即抛 `ValueError`，
     `force=True` 放行。消息体本身不脱敏（评测需要原始输入），所以门禁守的是
     「这个会话碰过密钥」这件事本身。
   - CLI：`python -m coworker.eval.capture <session-id> --out <dir> [--force]
     [--conv-dir] [--trajectory-dir]`；门禁拒绝时 stderr 提示并返回 2。
2. `matrix.py` CLI 新增 `--case <dir>`
   - 与 `--session` 互斥；从 `<dir>/messages.jsonl` 读 turns，从 `baseline.json` 读基线。
   - 新增 `compare_baseline(cell, baseline)`：逐 turn 给出
     `{turn, bytes, base_bytes, bytes_delta, tools, base_tools, tools_match}`，
     挂到报告每个 cell 的 `vs_baseline` 键下；CLI 打印每个 cell 的对比摘要。
3. `coworker/eval/__init__.py`：按既有 `__getattr__` 懒导出模式注册 capture。

## 非目标

- 不自动断言回归通过/失败（基线仅参照，偏差由人读报告判断）。
- 不动 GUI；不改 `run_matrix()` 库函数签名（对比只在 CLI 层做）。

## 验证

- 构造带轨迹的假会话 → capture 导出 → `matrix --case --stub` 跑通
- `messages.jsonl` / `baseline.json` 字段齐全；基线数值与账本一致
- 含 `[redacted]` 的会话被拒 + `--force` 放行
- `--case` 与 `--session` 互斥校验；`vs_baseline` 对比字段正确
- `ruff check coworker tests` + 全量回归（基线 2147 passed / 9 skipped）
