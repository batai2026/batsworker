# P2-10 权限变更审计（consent audit）

## 背景

P2-7 统一了安装管线并返回 `consent`；P2-8 让「权限扩大」必须显式 `confirm`。但**决策没有留痕**：
一次 `PATCH /v1/mcp/fs {"requires_approval": false, "confirm": true}` 通过后，无法事后回答
「何时、因为什么放开了这个服务器的审批」。

## 目标

安装 / 卸载 / 权限变更各留一条 append-only 记录；提供只读查询接口；审计永不阻塞请求。

## 设计

1. `plugins/consent_audit.py`
   - `record(kind, id, action, decision, consent=None, source="api")` 追加写
     `state_dir()/plugin_audit.jsonl`，字段：
     `ts / kind / id / action(install|uninstall|update) / decision(accepted|deferred|denied) /
      source / capability_set / risks / permissions / widened`
   - 故障静默（与 trajectory 同一契约）：写盘异常不得冒泡到安装请求。
   - `read(limit=100, id=None)`：倒序、按 id 过滤、`limit` 夹在 1..500，单行损坏跳过。
2. 接线
   - connector install / uninstall → `accepted`（kind=`connector`）
   - MCP add → `accepted`（kind=`mcp`）
   - MCP patch：拦截（409）→ `denied`（带 `widened`）；confirm 通过 → `accepted`
   - MCP delete → `uninstall`
3. 接口 `GET /v1/plugins/audit?limit=100&id=` → `{"events": [...]}`。
4. 只记录 consent 摘要，**不记录** env / token / 原始 config。

## 非目标

- 不给 GUI 加审计面板（`/v1/audit` 是会话工具审计，另一条线）。
- 不做日志轮转。

## 验证

- 安装/卸载留痕；409 记 `denied` 且带 `widened`；confirm 记 `accepted`；删除记 `uninstall`
- 不泄漏 `env` 中的密钥；`limit`/`id` 过滤；损坏行不影响其余记录
- `ruff check coworker tests` + 全量回归（基线以 P2-9 最终回归为准）
