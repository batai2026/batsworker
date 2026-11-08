# Tasks

1. [x] `surfaces/gui/src-tauri/src/lib.rs`：新增 `openshell_version` / `docker_host_networking`
   两个命令（`spawn_blocking` + `sidecar_env()` PATH），注册进 `invoke_handler`
2. [x] Rust 纯函数 `host_networking_from_settings()` + `#[cfg(test)]` 单元测试
3. [x] `surfaces/gui/src/tauri.ts`：导出类型与 `getOpenShellVersion()` / `getDockerHostNetworking()`
4. [x] `SecuritySection.tsx`：桌面专属「Desktop sandbox checks」卡片（仅 `isTauri()`）
5. [x] i18n：新增文案写入 `src/i18n/zh/cards.ts`
6. [x] `SecuritySection.test.tsx`：浏览器不渲染 / 桌面渲染两个用例
7. [x] 文档：`docs/design/egress-and-openshell.md`、`docs/design/maturity-roadmap.md`、
   `docs/design/openworker-design-zh.md` 同步
8. [x] `tsc --noEmit` + 目标 vitest + 全量 vitest（206/206）+ i18n（848 keys / 882 entries）
   + 全量 e2e（238 passed）；后端无改动，未重跑全量 pytest（沙箱外执行被拒绝）
9. [ ] 中文提交
