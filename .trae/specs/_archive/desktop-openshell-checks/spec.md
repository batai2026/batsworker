# 桌面端沙箱能力检查（OpenShell 版本 + Docker Desktop host-networking）

## 背景

`docs/design/egress-and-openshell.md` 与 `maturity-roadmap.md` 的剩余跟进项写着：
「Desktop Tauri settings item: OpenShell version + Docker Desktop host-networking check
(Rust commands + GUI card)」。当前状态：

- 后端已有 `GET /v1/settings/openshell/status`（返回 `available` / `prefix` / `backend`），
  Settings ▸ Security 只显示一行状态文本；
- 但桌面端（Tauri）用户最需要的两项**本机能力**没有入口：
  1. 本机 OpenShell CLI 的实际版本（`openshell --version`）；
  2. Docker Desktop 的「Enable host networking」是否开启——OpenShell/Docker 沙箱用
     `sandbox_network = "host"` 连本机模型服务时，这项不开启会静默失败。

这两项只有桌面壳进程能直接探测（浏览器构建拿到的是 sidecar 的间接状态），因此按设计
文档用 Rust 命令 + GUI 卡片实现。

## 目标

1. 新增两个 Tauri 命令：
   - `openshell_version` → `Option<String>`：执行 `openshell --version`，成功时返回
     首行去空白文本；CLI 不存在或失败返回 `null`。
   - `docker_host_networking` → `DockerHostNetworking`：
     ```ts
     { docker_cli: boolean; host_networking: boolean | null; settings_path: string | null }
     ```
     - `docker_cli`：`docker --version` 是否可执行；
     - `host_networking`：读取 Docker Desktop 设置文件里的 host-networking 开关，
       读不到为 `null`（不是 `false`）；
     - `settings_path`：实际读到的设置文件路径（未找到为 `null`）。
2. Settings ▸ Security 新增「Desktop sandbox checks」卡片（仅 `isTauri()` 时渲染），
   展示上述两项并提供 Refresh 按钮；浏览器构建不渲染该卡片（行为不变）。
3. 两个探测都放到 `spawn_blocking` 里执行，避免子进程阻塞 UI 线程。

## Docker Desktop 设置文件位置

官方文档（`docs.docker.com/desktop/settings-and-maintenance/settings`）给出：

| 平台 | 路径 |
|---|---|
| macOS | `~/Library/Group Containers/group.com.docker/settings-store.json`（旧版 `settings.json`） |
| Windows | `%APPDATA%\Docker\settings-store.json`（旧版 `settings.json`） |
| Linux | `~/.docker/desktop/settings-store.json` |

host-networking 的键为 `hostNetworkingEnabled`（Docker 社区/官方设置参考一致）；解析时
额外容忍 `EnableHostNetworking` / `enableHostNetworking` 两种写法，取第一个 bool。
文件不存在或 JSON 解析失败 → `host_networking: null`，不当作关闭。

## 约束

- 只做**只读探测**：不修改 Docker Desktop 设置，不写任何文件。
- 命令使用 `sidecar_env()` 的 PATH（macOS 由 Finder 启动时 launchd 的 PATH 看不到
  Homebrew 安装的 CLI），且任何失败都返回 `null` / 默认结构，不抛异常。
- 浏览器构建不受影响：卡片只在 `isTauri()` 时渲染，`tauri.ts` 的 `invoke` 在无
  `__TAURI__` 时返回 `null`。
- 不改后端的 `/v1/settings/openshell/status`，不改 `sandbox.json` 读写字段。

## 验证

- Rust：`#[cfg(test)]` 覆盖 `host_networking_from_settings()`（三种键名、非 bool、
  坏 JSON、缺失键）；本机无 cargo，编译验证由 release workflow 承担。
- GUI：`SecuritySection.test.tsx` 新增用例——浏览器模式不渲染卡片；桌面模式
  （mock `isTauri()`）渲染 OpenShell 版本与 host-networking 状态。
- `tsc --noEmit`、全量 vitest、i18n 校验、全量 e2e、后端全量 pytest（无后端改动，
  基线需保持一致）。

## 非目标

- 不在 GUI 里编辑 `sandbox_openshell_prefix` / 切换后端（仍走 config.toml 或既有
  sandbox.json 字段）。
- 不实际运行 `docker run --network host` 做连通性验证（避免拉镜像、起容器）。
- 不新增后端 API。
