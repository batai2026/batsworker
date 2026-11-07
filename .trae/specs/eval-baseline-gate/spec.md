# P2-11 后续：评测基线门禁（baseline gate）

## 背景

`matrix --case` 已给出 `vs_baseline` 逐轮对比，但基线只作参照：CLI 在基线漂移时仍返回 0，
CI 无法据此拦截「工具序列变了 / 出站负载暴涨」的回归。

## 目标

基线对比可选地升级为**显式门禁**：

- `--check-baseline`（必须配合 `--case`）：逐轮判定，任一失败即非零退出。
- `--max-bytes-ratio`（默认 0.10）：允许的出站字节相对偏差；超出即判漂移。
- 不开启该选项时行为完全不变（基线仍是参照，退出码语义不变）。

## 判定规则

对每个 cell 的每一轮：

| 失败类型 | 条件 |
|---|---|
| `cell_error` | cell `status != "ok"`（执行异常） |
| `missing_turn` | cell 有该轮，但 baseline 无对应 turn |
| `tool_drift` | `tools_match == False`（实际 tool_call 名称序列不一致） |
| `bytes_drift` | `base_bytes > 0` 且 `abs(bytes_delta) / base_bytes > max_bytes_ratio`；或 `base_bytes == 0` 且 `bytes > 0` |

## 接口

```python
DEFAULT_MAX_BYTES_RATIO = 0.10

def check_baseline(report: dict, *, max_bytes_ratio: float = DEFAULT_MAX_BYTES_RATIO) -> dict:
    """返回 {"ok", "max_bytes_ratio", "checked_turns", "failures": [...]}。

    failures 每项含 harness / model / turn / kind / 必要明细。
    要求 report 已带 "baseline" 与各 cell 的 "vs_baseline"。
    """
```

CLI：

- 新增 `--check-baseline`、`--max-bytes-ratio`。
- `--check-baseline` 未配合 `--case` → stderr 提示，返回 2。
- `--case` 但没有 `baseline.json` → stderr 提示，返回 2。
- `--max-bytes-ratio` 为负 → 返回 2。
- 报告新增顶层 `baseline_check` 键（与 `baseline` 并列），落盘 JSON 一并包含。
- 退出码：`0` 通过；`1` 矩阵执行有 cell 报错（既有语义）；`3` 基线门禁失败。

## 非目标

- 不做跨模型/跨 harness 的自动选择，不做历史报告归档。
- 不改变默认的「基线仅参照」行为。

## 验证

- `tests/test_eval_capture.py` 追加：
  - 与基线一致（同工具序列、字节在阈值内）→ `ok=True`，CLI 返回 0；
  - 工具序列漂移 → `tool_drift` 失败，CLI 返回 3；
  - 字节偏差超阈值 → `bytes_drift`；调大阈值后通过；
  - baseline 缺轮 → `missing_turn`；
  - `--check-baseline` 无 `--case` → 2；负阈值 → 2。
- `ruff check coworker tests` + 全量回归（基线 2155 passed / 9 skipped）。
