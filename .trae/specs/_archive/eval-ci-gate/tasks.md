# Tasks

1. [ ] `coworker/eval/matrix.py`：新增 `--write-baseline`（c），参数校验 + 基线写出
2. [ ] 生成并提交黄金用例 `evals/ci-case/`（messages.jsonl + baseline.json）
3. [ ] 新增 `.github/workflows/eval.yml`（PR stub 门禁 + workflow_dispatch 真实矩阵）
4. [ ] `tests/test_eval_capture.py` 追加 `--write-baseline` 用例（重写后可过门禁、互斥、多 cell 拒绝）
5. [ ] 文档同步（eval-and-replay / maturity-roadmap / user-manual-zh 20.2）
6. [ ] ruff + 目标用例 + CI 同款命令本地复跑
7. [ ] 全量回归 + 提交
