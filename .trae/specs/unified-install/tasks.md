# P2-7 任务清单

- [x] T1 `connectors/marketplace.py`：新增 `commit_plugin`，`install_plugin` 走 `lifecycle.install`
- [x] T2 `plugins/lifecycle.py`：connector 提交分支改调 `commit_plugin`（消除递归）
- [x] T3 `plugins/consent.py`：新增 `install_payload`（剥离 `manifest`）
- [x] T4 `mcp/config.py`：新增公开 `parse_server`
- [x] T5 `server/manager_connectors.py`：安装/卸载委托 `lifecycle`
- [x] T6 `server/manager_mcp.py`：`add_mcp` / `patch_mcp` 返回 `consent`
- [x] T7 新增 `tests/test_unified_install.py`（4 用例）
- [x] T8 `ruff check coworker tests` + 目标用例回归
- [x] T9 全量回归（2136 passed / 9 skipped）+ 提交
