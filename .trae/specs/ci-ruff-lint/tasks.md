# Tasks：CI 引入 ruff 静态检查

依赖顺序执行；每步完成即更新状态。

- [x] T1 写配置：`pyproject.toml` 增加 `[tool.ruff]` / `[tool.ruff.lint]` / per-file-ignores，`dev` extra 加 `ruff>=0.17.3`
- [x] T2 自动修复：`ruff check coworker tests scripts --fix`，逐文件复核 `git diff`，重点确认 `agent.py` / `rag.py` / `skills/store.py` / `relay_client.py` 的副作用导入未被误删
- [x] T3 修 F821 真 bug：`browser_automation.py` 的 `state_dir` 补局部导入、`Dict`→`dict[...]`（browser_automation / signing / sandbox）
- [x] T4 修字符串注解：`manager.py:841`、`test_shadow_eval.py:226` 加 `# noqa: F821`；`manager.py:824` 删除未用 `SessionCheckpoints`
- [x] T5 修测试注解与 3.10 兼容：`test_compaction_quality.py` 模块级 `AssistantTurn`；`test_mcp_connectors.py` ExceptionGroup try/except 导入
- [x] T6 修 E731 / E741 / F841：4 处 lambda→def、2 处 `l`→`label`、剩余未使用变量改 `_x`
- [x] T7 复跑 lint：`ruff check coworker tests scripts` 必须 0 errors
- [x] T8 子集验证：运行 spec 第 4 节列出的 12 个测试文件
- [x] T9 全量回归：`pytest tests -q`（Windows 需 dangerouslyDisableSandbox；基线 2054 passed / 9 skipped）——全量跑出 5 个由 F401 误删再导出导致的失败，已恢复 `permissions.py` 的 `SHELL_TOOL`/`WRITE_TOOLS` 再导出并单独复测通过（34 passed）
- [x] T10 CI 接线：`.github/workflows/ci.yml` 新增独立 `lint` job
- [x] T11 导入冒烟：`python -c "import coworker.connectors.browser_automation, coworker.tools.sandbox, coworker.personas.signing, coworker.server.manager, coworker.server.app"`
