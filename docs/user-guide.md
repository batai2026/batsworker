# OpenWorker 用户使用手册

适用版本：**1.0.2 及以上**（1.0.0 在 Windows 中文系统存在「提问无响应」缺陷，见第 7 节）

---

## 1. 安装

### Windows

安装包有两种，任选其一：

| 文件 | 说明 |
|---|---|
| `OpenWorker-windows-setup.exe` | NSIS 安装器（推荐），可按用户/自定义目录安装 |
| `OpenWorker-windows.msi` | MSI 安装包，适合企业批量分发 |

首次运行会出现 SmartScreen 提示（安装包未做代码签名）：

> **更多信息 → 仍要运行**

若安装时被杀毒软件拦截，选择「允许」或临时关闭实时防护后重试。

### macOS

| 文件 | 适用机型 |
|---|---|
| `OpenWorker-macos-arm64.dmg` | Apple Silicon（M1/M2/M3/M4） |
| `OpenWorker-macos-x64.dmg` | Intel Mac |

打开 dmg 后把 **OpenWorker.app** 拖到「应用程序」。首次启动若提示「无法验证开发者」：

```bash
xattr -cr /Applications/OpenWorker.app
```

然后在「系统设置 → 隐私与安全性」中点「仍要打开」。

---

## 2. 首次配置

### 2.1 配置模型（必须）

OpenWorker 本身不含模型，必须接一个模型提供方。

1. 打开左侧栏底部的 **Settings（设置）**
2. 进入 **Models** 子页
3. 选择提供方 → 填入 API Key → 点「验证 / Verify」
4. 验证通过后，该模型会出现在输入框下方的模型选择器里

支持的提供方：

| 提供方 | 需要密钥 | 环境变量 | 说明 |
|---|---|---|---|
| OpenAI | 是 | `OPENAI_API_KEY` | 可选自定义 base_url（Azure / vLLM / 兼容服务） |
| Claude (Anthropic) | 是 | `ANTHROPIC_API_KEY` | |
| Gemini (Google) | 是 | `GEMINI_API_KEY` | |
| DeepSeek | 是 | `DEEPSEEK_API_KEY` | |
| AWS Bedrock | 是 | —— | 支持 API Key / AWS Profile / IAM 三种方式 |
| Ollama | 否 | —— | 完全本地，填服务地址即可 |
| ChatGPT 订阅 | 否 | —— | 用 ChatGPT 订阅登录（OAuth），令牌只存本机 |

也支持直接读环境变量：设置后重启应用即可，无需在界面里再填。

> 密钥保存在本机 `secrets.json`（已做用户级 ACL 限制），不会上传。

### 2.2 关于「未登录」

左下角显示「未登录」指的是**云端账户**，只影响云端同步 / 团队能力。**本地使用（对话、执行、文件操作）不需要登录**，配好模型即可直接用。

### 2.3 选择项目（Workspace）

左侧「项目」区域选择一个工作目录，Agent 的读写和命令都在该目录内进行。首次进入新目录会询问「是否信任此工作区」——信任后才允许该目录的本地文件与命令操作。

---

## 3. 基本使用

1. 点 **+ 新会话**
2. 在输入框下方确认模型（如 `DeepSeek V4 Flash · DeepSeek`）
3. 输入任务，回车发送（`Shift+Enter` 换行）
4. 附件：直接拖拽文件到输入框，或粘贴图片
5. 语音输入：点麦克风按钮（首次需下载语音模型）

### 审批机制

工具调用（读写文件、执行命令等）默认需要确认，输入框下方可切换审批策略：

- **Ask for approval**：每次危险操作都询问（默认，推荐）
- 自动批准：仅在信任的工作区与已允许的命令范围内生效

审批卡会展示将要执行的命令或改动内容，点「允许 / 拒绝」即可。可勾选「记住」避免重复询问。

---

## 4. 功能模块

| 模块 | 入口 | 用途 |
|---|---|---|
| 人设（Personas） | 左侧「最近」分组 | Coworker / Code Mode / Creator / Security / Cloud Posture / Dependency Audit 等专用行为 |
| 自动化 | 左侧「自动化」 | 定时或事件触发任务 |
| 搜索 | 左侧「搜索」 | 搜索历史会话内容 |
| 安全 | Settings ▸ Security | 威胁模型编辑 + 安全问题跟踪 |
| 技能（Skills） | Settings ▸ Personas / Skills | 安装与启用可复用能力 |
| 记忆（Memory） | Settings ▸ Memory | 长期偏好与项目约定 |
| 集成（MCP / 连接器） | 左侧 Integrations | 外部工具与消息平台接入 |

### 沙箱

Settings 中可开启命令沙箱，后端支持两种（未安装时自动回退到本地执行，并在结果中标注 `sandboxed:false` 及原因）：

- **Docker**：容器内执行，默认断网、根只读
- **OpenShell**：NVIDIA OpenShell 沙箱，自动建/用/删沙箱实例，默认断网 + 只挂载工作区可写

---

## 5. 配置与数据位置

### Windows

```
%APPDATA%\coworker\
├── config.toml               # 高级配置（可不存在）
├── secrets.json              # API 密钥
├── prefs.json                # 界面偏好、默认模型、最近使用
├── conversations\            # 会话记录（jsonl）
├── coworker.db               # 主数据
├── *.db                      # 自动化 / 追踪 / 团队等
└── logs\openworker-server.log # 后端日志（排障首选）
```

### macOS / Linux

```
~/.config/coworker/
```

### 高级配置（config.toml，可选）

全局配置位于上述目录的 `config.toml`；工作区级配置位于工作区的 `.coworker/config.toml`。

常用项：

| 键 | 说明 |
|---|---|
| `model` | 默认模型，如 `deepseek:deepseek-v4-flash` |
| `max_iterations` | 单轮最大工具循环次数（默认 150） |
| `mode` | `interactive` / `plan` |
| `allowed_commands` | 免审批的命令白名单（安全敏感，仅全局配置生效） |
| `auto_allow` | 免审批的工具名列表（仅全局配置生效） |
| `port` | 后端监听端口 |

> 安全约束：**工作区级配置不能自行授予权限**（`allowed_commands` / `auto_allow` 以全局配置为准），只有受信任的工作区才能追加命令白名单。

修改配置后需重启应用。

---

## 6. 更新

- **应用内**：Settings 中有「检查更新 / 安装更新」
- **手动**：从 Releases 页面下载新版本覆盖安装

Docker 镜像用户：`docker pull ghcr.io/batai2026/batsworker:latest`

---

## 7. 常见问题（排障）

### 提问后没有任何响应

1. 查看后端日志：
   `%APPDATA%\coworker\logs\openworker-server.log`
2. 确认后端在跑（任务管理器中有 `openworker-server`），或访问 `http://127.0.0.1:54581/v1/health` 应返回 `{"status":"ok"}`
3. 日志若有 `UnicodeDecodeError: 'gbk' codec` 与 `AttributeError: 'NoneType' object has no attribute 'strip'`，说明是 **1.0.0 的已知缺陷**（Windows 中文系统解码 UTF-8 的 git 输出失败），升级到 **1.0.2+** 即可修复。

### 模型调用失败

- 检查密钥是否有效、是否已过期（Settings ▸ Models 可重新验证）
- 检查网络与代理；自定义 base_url 的填写是否完整
- DeepSeek 等服务的模型 id 需与提供方一致

### 首次打开很慢 / 卡在启动

后端以独立进程启动，首次启动需要解包运行环境。若持续超过 1 分钟，检查是否有安全软件拦截，或查看上述日志。

### 沙箱不生效

日志中若出现 `sandboxed:false`，先确认 Docker Desktop 或 OpenShell CLI 是否安装并可执行；未安装时属于预期的本地回退行为。

---

## 8. 注意事项

1. **安装包未签名**：Windows 会有 SmartScreen 提示，macOS 需 `xattr -cr`，属正常现象。
2. **数据边界**：对话内容只经你选择的模型提供方和已授权的外部集成离开本机；本地模型（Ollama）全程不出网。
3. **命令执行**：Agent 执行的命令在所选工作区内进行；请保持「Ask for approval」策略，尤其是首次使用未熟悉的工作区。
4. **密钥安全**：不要将 `secrets.json` 提交到版本库或共享。
5. **升级前**：关闭正在运行的 OpenWorker 再安装，避免文件占用导致安装失败。
6. **日志排障**：反馈问题前，先附上 `logs\openworker-server.log` 的相关片段（注意其中可能含路径信息）。
