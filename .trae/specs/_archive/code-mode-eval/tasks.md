# Tasks：Code mode 立项评估（P2-5）

依赖顺序执行；每步完成即更新状态。

- [ ] T1 调研笔记：DeepSeek Harness code mode 的输出协议 / 沙箱 API / 错误回传 / 回写规则
- [ ] T2 `coworker/eval/matrix.py`：新增 `code-mode` harness（槽位同 minimal，仅提示差异）
- [ ] T3 `tests/test_eval_matrix.py` 追加 code-mode 断言；ruff 0 errors
- [ ] T4 准备 3 组实验输入（多文件修改 / 长检索聚合 / 多步 shell 编排）
- [ ] T5 对比实验：baseline(minimal) vs code-mode；`--stub` 脚手架先行，真实跑显式 `--models`
- [ ] T6 安全面评估表：审批 / 沙箱 / egress / 审计 / 注入面，逐项三栏裁决
- [ ] T7 评估报告 + Go/No-Go 决策；若 Go 附实现 spec 草案
- [ ] T8 目标回归（test_eval_matrix）
- [ ] T9 `python -m coworker.eval.matrix --stub --harnesses code-mode` 端到端冒烟
- [ ] T10 全量回归 `pytest tests -q`（基线 2113 passed / 9 skipped）
