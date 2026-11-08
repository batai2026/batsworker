# 评测门禁接入 CI（eval CI gate）

## 背景

`matrix --case --check-baseline` 已能拦截基线漂移（退出码 3），但**没有 CI 接线**：
PR 里没有任何 job 跑它，真实模型矩阵也从未在 CI 跑过。1.0 前这是评测闭环的最后一环。

## 目标

1. 仓库内提交**确定性黄金用例** `evals/ci-case/`（`messages.jsonl` + `baseline.json`），
   基线由 `--stub` 跑出，不联网、不花钱，任何机器可复现。
2. 新增 `.github/workflows/eval.yml` 两个 job：
   - `eval-gate-stub`（push / PR）：跑 `matrix --case evals/ci-case --stub --check-baseline`，
     漂移即退出码 3 → job 失败；报告 JSON 作为 artifact 上传。
   - `eval-matrix-real`（仅 `workflow_dispatch`）：用仓库 secrets（`OPENAI_API_KEY` 等）
     跑真实模型矩阵并校验基线，参数化 case 路径 / models / harnesses / 字节阈值。
3. CLI 新增 `--write-baseline`：用本次**确定性 stub**运行重写 case 的 `baseline.json`，
   供有意改动（系统提示 / 工具目录 / harness）后刷新黄金基线。

## 判定与约束

- PR 门禁校验 `cell_error` / `missing_turn` / `bytes_drift`；stub 不发起 tool_call，
  `tool_drift` 在该模式下恒为通过（真实模型 job 才具备工具序列校验能力）。
- `<system-context>` 每轮注入当前时间与绝对工作区路径，因此黄金基线只做**相对字节阈值**
  （默认 10%）判定，不做逐字节断言。
- `--write-baseline` 语义：
  - 必须同时给 `--case` 与 `--stub`，否则返回 2；
  - 与 `--check-baseline` 互斥（同一次不能既写又校验），否则返回 2；
  - 本次运行必须恰好 1 个 cell（1 harness × 1 model），否则返回 2；
  - 写出 `<case>/baseline.json`，字段与 `capture` 基线一致（turn / model / bytes /
    requests / tool_calls / tools），`session_id` 沿用原基线或目录名。
- 真实模型 job 不阻塞 PR（需密钥与费用），失败仅影响该手动运行。

## 非目标

- 不在 PR 流水线跑真实模型（费用 + 密钥不可用）。
- 不做历史报告归档、不做跨模型自动择优。

## 验证

- 单测：`--write-baseline` 重写后 `--check-baseline` 通过；写模式参数互斥；多 cell 拒绝。
- 本地用 CI 同款命令复跑黄金用例，退出码 0。
- `ruff check coworker tests scripts` 干净；全量回归保持 2162 passed / 9 skipped 基线。
- 文档同步：`docs/design/eval-and-replay.md`、`docs/design/maturity-roadmap.md`、
  `docs/user-manual-zh.md` 20.2（新增 `--write-baseline`）。
