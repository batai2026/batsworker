# P2-7 统一安装管线（connector / MCP 收口）

## 背景

`plugins/lifecycle.py` 已是四种扩展的统一安装入口（adapter → consent 摘要 → 原生 store），
但 connector 与 MCP 的历史路径绕过它：

- `connectors/marketplace.install_plugin`：直接 `register_descriptor + save_plugin`，无 consent。
- `server/manager_mcp.add_mcp` / `patch_mcp`：直接 `put/patch_global_server`，无 consent。

P1-3 已留下 `ConnectorPluginAdapter`、`MCPAdapter` 与 `_commit_install` 分支，因此本次只补接线。

## 目标

1. connector 安装/卸载、MCP 添加/修改都经 `lifecycle` 的 adapter + `consent_summary`。
2. REST 响应新增嵌套 `consent` 键（纯增量），保留原有 `ok` / `plugin` / `name` 字段。
3. 不改变 GUI 交互：marketplace 点击安装、MCP 表单提交仍是一次调用即落地。

## 非目标

- 不做「首次安装必须二次确认」：现有点击/表单提交即 consent 动作，两步会破坏 GUI。
- 不加权限变更门禁：`capability_diff` 只比较能力集，connector/MCP 的能力恒为
  `connector:<name>` / `mcp:<name>`，更新永不 `grew`；扩 diff 语义另列条目。
- 不改 MCP 删除路径：`delete_mcp` 还负责连接关闭、OAuth token 清理、错误缓存，
  统一卸载会丢失这些语义。

## 设计

- `connectors/marketplace.commit_plugin(data)`：只做落地（register + save）；
  `install_plugin(name)` 负责查找/冲突校验，然后 `lifecycle.install(KIND_CONNECTOR, data, commit=True)`。
  本地 import lifecycle，避免与 lifecycle 顶部的 marketplace import 循环。
- `lifecycle._commit_install` 的 connector 分支改调 `marketplace.commit_plugin(native)`（原为
  `marketplace.install_plugin`，会递归）。
- `mcp/config.parse_server(name, raw, secrets=None)`：`_parse` 的公开包装，供 consent 构造。
- `plugins/consent.install_payload(result)`：去掉不可 JSON 序列化的 `manifest`，供 REST 返回。
- `manager.install_connector_plugin` / `uninstall_connector_plugin`：委托 `lifecycle`，
  卸载结果补回 `name` 保持旧字段。
- `manager.add_mcp`：`lifecycle.install(KIND_MCP, parse_server(...), commit=True,
  commit_payload=(name, config))`，返回 `{ok, name, consent}`。
- `manager.patch_mcp`：先 `patch_global_server`，成功后用 `lifecycle.update`（旧/新 def 比较）
  生成 `consent`（含 `diff`），返回 `{ok, name, consent}`。

## 兼容性

- 响应体变大（新增 `consent`），旧字段与错误文案不变。
- connector 安装现在会经过 adapter 校验：畸形 plugin dict 会抛错（测试覆盖正常路径即可）。

## 验证

- 新用例：connector 安装返回 `connector:` 能力与 `connectors` 风险；卸载字段；
  MCP 添加返回 `mcp:` 能力与 transport/approval 权限；MCP patch 返回 consent 且 `grew` 为空。
- `ruff check coworker tests` + 全量 `pytest tests -q`（基线 2132 passed / 9 skipped）。
