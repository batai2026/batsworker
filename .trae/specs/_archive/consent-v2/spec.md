# P2-8 consent v2 —— 权限变更门禁

## 背景

P2-7 让 connector / MCP 安装走统一管线并返回 `consent`，但 PATCH 路径仍直接落地：

- `PATCH /v1/mcp/{name} {"requires_approval": false}` 会静默关闭该服务器所有工具的审批。
- `include_tools` 从 allowlist 变 `null`（全量开放）、或新增条目，都扩大了工具面。
- `exclude_tools` 删除条目同样扩大工具面。
- connector 的 `two_way: false → true` 开启入站消息。

这些变更的 `capability_set` 恒为 `mcp:<name>` / `connector:<name>`，`capability_diff` 永远
`grew=[]`，用户不会收到任何提示。

## 目标

更新时比较 **权限语义**，扩大即需显式 `confirm` 才落地；不扩大（收窄/无变化）的更新照常即时生效。

## 设计

`plugins/consent.py` 新增 `permission_diff(old_pm, new_pm) -> {"widened": [...]}`：

| 条件 | 判定 |
|---|---|
| `requires_approval` True → False | widened |
| `include_tools` 列表 → None | widened |
| `include_tools` 新增条目 | widened |
| `include_tools` None/list → 列表/更短列表 | 收窄，不拦 |
| `exclude_tools` 删除条目 | widened |
| `two_way` False → True | widened |

`lifecycle.update`：

- `requires_consent = bool(diff["grew"]) or bool(permission_diff["widened"])`
- 返回值新增 `permission_diff`（无 `existing_native` 时为 None，仍走保守 `requires_consent=True`）。

`server/manager_mcp.patch_mcp(name, changes, *, confirm=False)`：

- 先从 `changes` 弹出 `confirm`（绝不写入 `mcp.json`）。
- 服务不存在 → `{ok: False, name}`（原行为）。
- 否则构造新配置，`lifecycle.update(..., commit=False)` 比对；`widened` 非空且未 confirm →
  **不落地**，返回 `{ok: False, status: 409, name, requires_consent: True, consent: {...}}`。
- 通过则 `patch_global_server` 并返回 `{ok: True, name, consent}`。

`server/app.py` PATCH 路由：结果带 `status` 时用 `JSONResponse(status_code=...)` 返回。

## 非目标

- 不改 connector 安装（同名重装当前直接拒绝）。
- 不改 GUI：UI 只 PATCH `enabled` / `auth`，两字段都不在 widened 规则内。

## 验证

- 新增用例：关闭审批无 confirm → 409 且配置未变；confirm 后落地；`include_tools` 增条目被拦；
  收窄（None → 列表、新增 exclude）直接通过；`enabled` 切换不触发门禁；`confirm` 不落盘。
- `ruff check coworker tests` + 全量回归（基线 2136 passed / 9 skipped）。
