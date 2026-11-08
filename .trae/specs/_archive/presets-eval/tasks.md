# Tasks：Minimal / Creator preset + 模型×harness 评测（P1-4）

依赖顺序执行；每步完成即更新状态。

- [x] T1 新增 `coworker/personas/builtin/minimal/manifest.md`（tools: code_files+shell，最短提示）
- [x] T2 新增 `coworker/personas/builtin/creator/manifest.md`（创作流程 + 能力槽封闭 + dry-run 后安装）
- [x] T3 新增 `coworker/eval/` 包：`HarnessSpec` / `HARNESSES` / `recorded_turns()` / `run_matrix()` / `main()`（含 `--stub`）
- [x] T4 新增 `coworker/server/manager_plugins.py`：`plugin_slots()` + `preview_plugin()`，并在 `manager.py` 挂到 facade
- [x] T5 `coworker/server/app.py`：`GET /v1/plugins/slots`、`POST /v1/plugins/preview` 两路由 + 错误映射
- [x] T6 测试：`tests/test_eval_matrix.py`、`tests/test_plugin_slots.py`、内置 preset 断言
- [x] T7 静态检查 ruff → 0 errors
- [x] T8 目标回归（eval_matrix + plugin_slots + personas + server）
- [x] T9 `python -m coworker.eval.matrix --help` 与 `--stub` 端到端冒烟
- [ ] T10 全量回归 `pytest tests -q`（基线 2098 passed / 9 skipped）
