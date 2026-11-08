# P2-8 任务清单

- [ ] T1 `plugins/consent.py`：新增 `permission_diff`
- [ ] T2 `plugins/lifecycle.py`：`update` 并入权限 diff，返回 `permission_diff`
- [ ] T3 `server/manager_mcp.py`：`patch_mcp` 支持 `confirm` 与 409 拦截
- [ ] T4 `server/app.py`：PATCH 路由按 `status` 返回状态码
- [ ] T5 新增用例（`tests/test_unified_install.py`）覆盖拦截/确认/收窄/无门禁/不落盘
- [ ] T6 ruff + 目标用例回归
- [ ] T7 全量回归（基线 2136 passed / 9 skipped）+ 提交
