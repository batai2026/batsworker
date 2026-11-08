# Tasks：统一 PluginManifest 适配层（P1-3）

依赖顺序执行；每步完成即更新状态。

- [x] T1 新建 `coworker/plugins/` 包骨架：`manifest.py`（`PluginManifest` + `PluginKind`）、`adapters/base.py`（`PluginAdapter` Protocol）、`__init__.py`
- [x] T2 四个 adapter：`persona.py` / `skill.py` / `connector.py` / `mcp.py`，各实现 `to_manifest()` + `capability_set()` + `permissions()`
- [x] T3 `consent.py`：`consent_summary(pm)`（按 kind 分支输出统一结构）+ `capability_diff(old, new)`（grew/shrank/same）
- [x] T4 `lifecycle.py`：`install/update/uninstall` 门面（签名校验 → capability diff → 委托原生 store → 返回 requires_consent）
- [x] T5 `tests/test_plugin_manifest.py`：四 adapter 映射、consent_summary、capability_diff、install/update requires_consent 判定、签名字段（24 passed）
- [x] T6 静态检查 `ruff check coworker/plugins/ tests/test_plugin_manifest.py` → 0 errors
- [x] T7 目标回归 `test_plugin_manifest.py` + personas/skills/marketplace/mcp/connector 相关测试 → 113 passed / 1 skipped
- [x] T8 全量回归 `pytest tests -q` → 2098 passed / 9 skipped（38:26）
