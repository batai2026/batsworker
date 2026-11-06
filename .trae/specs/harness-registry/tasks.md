# P2-6 Harness 注册表 — 任务清单

- [x] T1 新增 `coworker/personas/harness.py`：`HarnessSpec` + `from_manifest` + `builtin_specs` + `installed_specs` + `resolve`
- [x] T2 `coworker/eval/matrix.py` 改为从注册表派生：删除三个提示常量与手写 `HARNESSES`；`HARNESSES = builtin_specs()`
- [x] T3 `run_matrix` 支持任意已装 persona（`harness_specs` 可注入）+ 报告写 `harness_sources`；CLI 校验走注册表
- [x] T4 `_harness_agent` 使用 `spec.requires_folder`
- [x] T5 新增 `tests/test_harness_registry.py`（四档 golden、来源/提示、自定义 persona 派生、resolve 失败、stub 跑通）
- [x] T6 调整 `tests/test_eval_matrix.py`（code-mode 提示断言改为 manifest 语义）
- [x] T7 `ruff check coworker tests` + 目标用例回归（33 passed）
- [x] T8 全量回归（2132 passed / 9 skipped）+ 提交
