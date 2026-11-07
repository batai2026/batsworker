# OpenWorker 设计文档（中文版）

> 本文是 `docs/design/` 下全部英文设计文档的单文件中文版，共 25 章，对应英文分篇见同目录各 `*.md`。
> 文档依据 0.2.1b 代码快照编写：类名、枚举值、协议消息与文件路径均经源码核验；代码标识符、路径与配置键保留英文。
> 当文档与代码不一致时，以代码为准，并请修正文档。

更新日期：2026-11-07

## 目录

1. [系统架构](#1-系统架构)
2. [Agent 引擎](#2-agent-引擎)
3. [权限与审批](#3-权限与审批)
4. [服务端与认证](#4-服务端与认证)
5. [模型提供商与多模型](#5-模型提供商与多模型)
6. [工具系统](#6-工具系统)
7. [MCP（客户端与服务端）](#7-mcp客户端与服务端)
8. [连接器与扩展市场](#8-连接器与扩展市场)
9. [自动化与持续目标](#9-自动化与持续目标)
10. [记忆与检索](#10-记忆与检索)
11. [团队协作](#11-团队协作)
12. [Persona、技能与分发](#12-persona技能与分发)
13. [GUI、桌面壳与打包](#13-gui桌面壳与打包)
14. [沙箱（执行与文件）](#14-沙箱执行与文件)
15. [出网策略与 OpenShell](#15-出网策略与-openshell)
16. [检查点](#16-检查点)
17. [预算守卫](#17-预算守卫)
18. [压缩质量](#18-压缩质量)
19. [评审器解耦](#19-评审器解耦)
20. [可观测性](#20-可观测性)
21. [评测与回放](#21-评测与回放)
22. [Harness v2 与引擎优化](#22-harness-v2-与引擎优化)
23. [浏览器录制](#23-浏览器录制)
24. [决策日志](#24-决策日志)
25. [成熟度路线图](#25-成熟度路线图)

---

## 1. 系统架构

> 事实来源：`coworker/` 与 `surfaces/gui/` 下的代码。本文档是地图，不是替代品。

### 1.1 三个运动部件

OpenWorker 是一款本地优先的桌面应用。运行时，它由一个原生进程加一个 Python 子进程构成（在语音输入时，原生进程内部还会有一个麦克风会话）：

```
┌──────────────────────────────────────────────────────────────┐
│ Tauri desktop shell (surfaces/gui/src-tauri)                 │
│  • native window + system tray (close = hide; server lives)  │
│  • spawns & supervises the Python sidecar on a free port     │
│  • injects HTTP/WS base URLs + launch token into the webview │
│  • native commands: folder picker, autostart, keep-awake,    │
│    dictation bridge (ocw-stt), updater                       │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ React SPA (surfaces/gui/src)                           │  │
│  │  App.tsx ~2.4k lines; api.ts ~2.5k lines               │  │
│  │  REST for state, /ws/session/{id} for the live turn    │  │
│  └────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│ Python agent server — "the sidecar" (coworker/server)        │
│  • FastAPI + uvicorn, binds 127.0.0.1 only                   │
│  • SessionManager owns engines, sessions, persistence,       │
│    the automation scheduler, connector gateway, and the      │
│    cross-session Inbox                                      │
│  • TurnEngine (coworker/engine.py) runs agent turns          │
└──────────────────────────────────────────────────────────────┘
        │ files/terminal        │ 25+ connectors      │ any LLM provider
        ▼                       ▼                     ▼
   your machine            Slack, GitHub, …      OpenAI, Anthropic, GLM,
   (roots, shell,          via tokens you own    Gemini, Ollama, … with
   search)                 or brokered OAuth     your keys
```

同一个服务端二进制以两种模式运行：

- **桌面模式** —— Tauri 通过环境变量传入一个内存中的启动令牌；任何机密信息都不会写入磁盘。
- **独立模式** —— `openworker-server` 写一个仅当前用户可读的令牌文件 `<state-dir>/sidecar-<port>.token`（关闭时删除），这样 Vite 开发 UI 或 curl 就能用 `X-OpenWorker-Token` 请求头进行认证。

浏览器 UI 与桌面外壳共用同一套代码库：SPA 按 Tauri 注入的运行时全局变量 → Vite 环境变量 → `127.0.0.1:8765` 的顺序解析其后端地址。

### 1.2 模块地图（`coworker/`）

**回合核心**

| 模块 | 角色 |
|---|---|
| `engine.py` | `TurnEngine` —— 自持的 agent 循环（见第 2 章（agent-engine.md）） |
| `agent.py` | `build_engine()`：把 Agent + 工具 + 权限 + AGENTS.md + 记忆 + 技能 + 连接器 + MCP 组装成一个 `TurnEngine` |
| `agents/` | 声明式 `Agent` dataclass 与内置 agent（`code`、`chat`、`cowork`、`myhelper`）；`registry.py` 解析 persona id |
| `permissions.py` | `PermissionEngine`：模式、规则、授权、硬底线（见第 3 章（permissions-and-approvals.md）） |
| `risk.py` | 对每次工具调用做 `RiskClass` 分类 |
| `reviewer.py` | Auto-Approve 模式背后的 LLM 评审器 |
| `events.py` | 流式发往 GUI 的引擎事件枚举（`assistant_delta`、`tool_proposed`、`permission_required`……） |

**上下文与历史**

| 模块 | 角色 |
|---|---|
| `compaction.py` | 长历史的自动上下文压缩（OPE-27） |
| `sessions.py`、`conversations.py` | 持久化的会话与对话线程 |
| `memory/` | 长期记忆存储与注入（见 memory.md） |
| `session_facts.py` | 每会话的事实块 |

**界面与协作**

| 模块 | 角色 |
|---|---|
| `server/` | FastAPI 应用、`SessionManager`、启动器（见 server-and-auth.md） |
| `inbox.py`、`inbox_routing.py`、`unrouted.py` | 跨会话 Inbox：被搁置的审批/问题及其路由 |
| `unattended.py` | 每会话的“无人值守”标志，把提示改路由到 Inbox |
| `mentions.py` | Slack/GitHub @提及路由（§31 提及路由器） |
| `subscriptions.py` | 哪些会话监听哪些频道 |
| `automation/` | 调度器与定时任务（见 automation.md） |
| `teams/` | 多 agent 的 board/journal/chat 基座（见 teams.md） |
| `selfwake.py` | 自唤醒支持，让会话能按自己的定时器恢复 |

**环境与信任**

| 模块 | 角色 |
|---|---|
| `roots.py`、`workspace_trust.py`、`projects.py`、`project.py` | 可写根目录、工作区信任判定、项目注册表 |
| `provenance.py` | 追踪 agent 本会话下载的文件（喂给 OPE-114 硬底线） |
| `toolchain.py` | 带登录 shell 环境的 PATH/工具解析（OPE-82/83） |
| `secrets.py` | `SecretStore` —— 按 profile 作用域存放凭据 |
| `audit.py` | 有后果操作的审计日志 |
| `readonly.py` | 只读 shell 命令的分类器（`READONLY_SESSION` 授权） |
| `config.py` | TOML 配置（本目录的 `config.example.toml`），环境变量覆盖 |
| `cloud.py` | OpenWorker-Cloud 客户端（OAuth 代理、relay 注册） |
| `overrides.py`、`connections.py`、`interactions.py`、`attachments.py`、`environment.py` | 设置覆盖、连接状态、交互式提示管道、消息附件、环境/系统信息块 |

**能力平面**

| 模块 | 角色 |
|---|---|
| `providers/` | LLM 提供商（见 model-providers.md） |
| `tools/` | 内置工具与注册表（见 tools.md） |
| `connectors/` | 第三方平台适配器（见 connectors.md） |
| `mcp/` | MCP 客户端（见 mcp-client.md） |
| `personas/`、`skills/` | persona 清单与技能（见 personas-and-skills.md） |
| `catalog.py` | persona 可组合的声明式能力目录 |
| `web/` | `web_search` / `web_fetch` 管道与出网守卫 |
| `tui/`、`cli.py` | 终端 UI 与 `openworker` CLI 入口 |
| `testing/` | 测试支架（例如进程内 FakeSlack 服务器） |

### 1.3 状态目录清单

服务端保存的所有内容都位于每用户的状态目录下（桌面应用把它映射到自己的 app-data 区域）。管理器（`coworker/server/manager.py`）掌管这些文件：

| 文件 / 目录 | 写入方 | 内容 |
|---|---|---|
| `coworker.db` | `memory/sqlite_store.py` | 长期记忆（`memories` 表） |
| `memory-settings.json` | `memory/settings.py` | 记忆开关 + 用户规则文本 |
| `automation.db` | `automation/store.py` | `ScheduledTask` / `TaskRun` 行，按 `next_run` 建索引 |
| `teams.db` / `journal.db` / `chat.db` | `teams/` | Board 事件日志、journal、团队聊天（各自哈希链） |
| `teams.json` | `teams/registry.py` | 已注册的团队（lead persona、worker） |
| `board-tokens.json` | `teams/tokens.py` | `owb_` 加入令牌（sha256 哈希） |
| `attachments/` | `teams/attachments.py` | `attachment://` 引用背后的内容寻址 blob |
| `inbox.json` | `inbox.py` | 人类注意力队列（审批、问题……） |
| sidecar token file | `server/run.py` | 独立模式的启动令牌，仅用户可读，关闭时删除 |

另有两处配置位置只是被*读取*，并不归它所有：全局 `~/.config/coworker/` 树（通过环境变量提供的提供商密钥、`mcp.json`），以及每个工作区的 `.coworker/` 目录（工作区作用域的 MCP 服务器；AGENTS.md 位于工作区根目录）。

### 1.4 设计不变式

以下各条贯穿所有子系统，并在代码注释中反复出现：

1. **本地优先。** 循环、对话记录、令牌与密钥绝不离开本机，只有经由用户所选的模型提供商与连接器时例外。唯一的云端部分是连接器的 OAuth 代理/relay（`cloud.py`）。
2. **引擎与提供商无关。** 各处都使用同一种规范的 OpenAI 形状消息格式；提供商特有状态搭载在私有 sidecar 上，并在任何请求前被剥离（见 model-providers.md）。这正是会话中途切换模型安全的原因。
3. **失败即关闭。** 空的允许列表不放行任何人；仓库级配置只能*收窄*权限，绝不能放宽（`config.py` 中的 `_GLOBAL_ONLY_FIELDS`）；无法限定作用域的操作降级为仅人工处理。
4. **任何交互都不会丢失。** agent 需要人回答的每个提示，在向任何人发问之前都会先作为持久化的 Inbox 条目搁置，因此套接字断开与重启都不会让某个回合变成孤儿（见 server-and-auth.md）。
5. **注册表从不下判断。** `ToolRegistry` 执行工具；权限决策在引擎内、执行之前、对每次调用都发生。

---

## 2. Agent 引擎

> 源码：`coworker/engine.py`（`TurnEngine`），由 `coworker/agent.py::build_engine()` 组装。约 2,000 行，注释密集。

### 2.1 一个回合，多次迭代

一个**回合**是一条用户消息，加上 agent 围绕它所做的一切：由模型调用与工具调用构成的 while 循环，在模型不再调用工具、某条护栏触发或用户中断时结束。迭代上限来自配置（`max_iterations`，在 `coworker/config.py:34` 中默认 **150**；引擎构造函数自身的默认值 12 仅用于裸测试）。

```
run() / retry() / resume()
   └─ _loop():
        1. auto-compaction checkpoint        (OPE-27)
        2. stream the model                  (_astream)
        3. append assistant message          (+ _display sidecar)
        4. _handle_tool_calls()
             a. authorize every call         (sequential — see below)
             b. execute                       (parallel-safe ∥ sequential)
        5. repeat until: no tool calls | cap | rail | interrupt
```

入口点（`engine.py`）：

- `run()` —— 用户消息的常规入口。
- `retry()` —— 重跑一个失败的回合。
- `resume()` —— 崩溃/重启后的持久化恢复。末尾未应答的工具调用会被重新搁置：提示回调会找到已持久化的 Inbox 条目，而不是创建重复项，因此任何操作都不会执行两次。

### 2.2 流式输出

提供商暴露的是阻塞式流；引擎用线程 + 队列（`_astream`）把它桥接为异步。增量以引擎事件（`coworker/events.py`）发出：`assistant_delta` 用于可见文本，`reasoning_delta` 用于模型思考（仅用于展示，绝不回放给提供商），之后是 `tool_proposed`、`permission_required`、`tool_started`、`tool_finished`。停止是流队列与取消事件之间的一场竞速；中断还会触发 `interrupt_hooks`，运行中的 shell 子进程正是借此被杀死。

### 2.3 工具调用编排

`_handle_tool_calls()` 把*授权*与*执行*分开：

1. **先授权，全部授权，且串行**（spec §8.6 —— 一次请求一个动作，每个动作发一张卡片）。若并发执行，交互式审批卡片就毫无意义。
2. **然后执行**：被归为并行安全的调用（读、搜索）在 `asyncio.to_thread` 内通过 `asyncio.gather` 并发执行；写操作与 shell 命令严格按到达顺序执行。

一组固定的**门控工具（gate tools）**完全不经过工具注册表 —— 它们是引擎的交互词汇，每个都由专门的 `_handle_*` 生成器处理：`ask_user`、`propose_plan`、`request_directory`、`request_tool`、`propose_team`、`propose_work_items`。

#### 2.3.1 没有孤儿工具调用

每一次工具调用 —— 无论通过、拒绝还是被中断 —— 最终都会得到一条工具结果消息。协议要求每个 `tool_call_id` 都得到应答；拒绝会返回一句简短的拒绝语（*用户*能看到完整理由），让模型可以作出反应而不是报错。

### 2.4 审批与授权

授权结果（`ApprovalOutcome`，`engine.py:42`）：`once`、`always_tool`、`always_command`、`always_domain`、`readonly_session`、`deny`。除 `once` 之外的每一种都会铸造一个会话级授权，使下一次匹配的调用不再询问。`readonly_session` 是特殊的：它来自只读命令分类器（`coworker/readonly.py`），对通过分类器的 shell 读操作在整个会话内预先批准。

完整的决策流水线 —— 权限模式、风险等级、硬底线、Auto-Approve 评审器 —— 有专门的一篇文档：见第 3 章（permissions-and-approvals.md）。

### 2.5 自动上下文压缩（OPE-27）

在工具轮次之间、发起新的模型调用之前，引擎会取一个上下文压缩检查点。当历史增长超过阈值时，边界之前的所有内容都会被一段压缩摘要取代；边界本身记录在会话状态中（`sessions.py`、`conversations.py` —— 两者都以防御性方式解析它，使较旧/损坏的行降级为“从未压缩”而不是崩溃）。对无人值守运行，压缩失败会自动裁剪，而不是让运行停滞。

### 2.6 运行中引导与中断

- `queue_steering()` —— 用户可以在回合中途发送额外消息；它们会在下一个模型请求边界（以及模型停止请求工具的收尾点）被作为用户消息注入 —— 这是运行中引导上下文，而不是新的回合。
- 运行中引导的入口：WS 的 `user_message` 分支在会话运行中接受**纯文本**消息（`steer_session()` → `engine.queue_steering()`），回复 `steering_queued`，GUI 则在 Stop 旁边保留其 Send 按钮，并附上“在下一步生效”的提示。中途发送附件/技能会被明确拒绝（请在回合结束后再发送）；待处理的审批门控对回复路径的优先级高于运行中引导；中断当前工具调用才是 Stop 的职责 —— 运行中引导绝不会抢占它。
- 中断 —— 取消当前迭代，通过 `interrupt_hooks` 杀掉 shell 子进程，并且仍然关闭任何尚未闭合的工具调用（见上文）。

### 2.7 代码模式（form B）

> 源码：`coworker/codemode/`（bridge、guest、exec_tool）、`coworker/catalog.py`（`code_mode` 槽位）、`coworker/personas/builtin/code-mode/manifest.md`。

代码模式让模型输出**一段带围栏的 Python 程序**，在沙箱内编排工具调用，并且只返回关键结果 —— 从而消除 tool_call 循环中每次调用都要一次模型往返的开销。先前的一次评估（code-mode-eval）把这个想法置于 Go/No-Go 门控之后；form B 在不触碰引擎循环的前提下实现了它：

- **单一工具**：`code_mode` 能力槽位只暴露一个 `exec(code)` 工具，其描述根据会话真实展开的工具集生成，采用紧凑的单行签名（不含 `exec` 本身）。
- **失败即关闭的沙箱**：guest 代码在 Docker 中运行（`--network none --read-only`、内存/CPU 上限、不挂载工作区）。没有 Docker ⇒ `{"error": "sandbox_unavailable"}` —— 模型代码**绝不**在宿主上运行，这与 shell 沙箱的优雅降级是有意为之的差异。
- **Stdio JSON-lines 桥**：guest 输出 `{"__call__": …}` 行；宿主按工具白名单校验，把每次调用传给会话的 **Approver**（审批与 hook 不会被绕过），执行，再把结果写回。用户的 `print` 输出由 guest runner 捕获，只在最终的 `__result__` 信封中返回，因此协议行与用户输出永不混杂。
- **限制**：每次 exec 60 s（含 Docker 启动开销），stdout/stderr 各 64 KiB，每次 exec 的桥调用 ≤16；`exec` 本身也要经过审批，预览卡片中显示完整源码。
- **审计**：每次运行记录一条 `code_exec` 轨迹账本记录（代码 + `sandboxed` + 桥调用次数）；每一次经桥的调用仍会记录标准的 `tool_call`/`tool_result` 对。

### 2.8 模型看到的内容

引擎拥有规范的消息列表（OpenAI 形状）。提供商私有状态搭载在 sidecar 键上 —— `_anthropic`（thinking 块）、`_gemini`（thought 签名）、`_display`（仅 GUI 使用的数据）—— 而 `_outbound_messages()` 在任何请求前把它们剥离。后果：

- 会话中途切换模型是安全的；见 model-providers.md。
- `_display` 在重载后仍保留，但*绝不*对提供商可见（负责人裁定 2026-08-24）。
- 引擎还会追踪 **agent 本会话自己创建或下载了什么**（OPE-114 §1）—— 这份来源追踪集合正是权限文档中“先取后执行”硬底线的输入。

### 2.9 上下文组装（`agent.py::build_engine`）

循环运行之前，`build_engine()` 会把一个 `Agent`（`coworker/agents/base.py` —— 一个声明式 trait dataclass：`requires_folder`、`subagents`、`scheduling`、`messaging`、`connectors`、`team`）与以下内容接在一起：

- 来自能力目录（`coworker/catalog.py`）的工具，由 agent/persona 声明展开而来；
- 权限引擎 + 实时设置 getter（引擎持有的是**晚绑定的可调用对象**，而不是烘焙好的快照，因此设置变更会在下一次调用生效）；
- 来自工作区根目录的 AGENTS.md；
- 记忆块（见 memory.md）；
- 实时技能菜单（SKILLS-SPEC §4.1 —— 每回合重新计算，与每回合注入的 roots 列表和实时时钟一样，负责人裁定 2026-08-20）；
- 连接器（最小权限：带允许列表的 persona 只会拿到那些连接器，OPE-93）与 MCP 服务器（见 mcp-client.md）。

agent *知道*的内容在会话开始时固定（MEMORY-SPEC §7.1）；技能菜单与时钟是刻意保留的例外，实时刷新。

---

## 3. 权限与审批

> 源码：`coworker/permissions.py`（`PermissionEngine`）、
> `coworker/risk.py`（`RiskClass`）、`coworker/reviewer.py`（Auto-Approve
> 评审器）、`coworker/engine.py` 中的授权路径。

这是对每一次工具调用决定其运行、询问还是拒绝的子系统。支配原则：**失败即关闭（fail closed），任何便利功能都不能扩大人类尚未批准的范围。**

### 3.1 权限模式

`PermissionMode`（`permissions.py:214`）：

| 模式 | 值 | 含义 |
|---|---|---|
| `DISCUSS` | `discuss` | 只读对话：不编辑，不进入规划流程 |
| `PLAN` | `plan` | 与 DISCUSS 相同的只读闸门；仅在意图上不同——驱动 agent 走向 `propose_plan` 审批 |
| `INTERACTIVE` | `interactive` | 请求审批（默认） |
| `BYPASS_APPROVALS` | `bypass-approvals` | 除硬底线之外的完全访问 |
| `AUTO_APPROVE` | `auto-approve` | INTERACTIVE 加上一个可预批准卡片的 LLM 评审器 |
| `CUSTOM` | `custom` | INTERACTIVE 加上配置中 `auto_allow` 工具的自动放行 |

命名沿革：`bypass-approvals` 由 `"auto"` 更名而来（spec §1.5，2026-08-12），以免在选择器中把“安全系统关闭”与 `auto-approve` 混淆。未接入评审器时，`AUTO_APPROVE` 的行为与 `INTERACTIVE` 完全一致。

DISCUSS 与 PLAN 共用同一个执行闸门（只读）；二者之分在于如何引导模型，而不是第二条权限路径——OPE-117 的评审跟进确保这些模式在应出卡片时仍会出卡片。

### 3.2 风险等级

每次工具调用首先被分类（`risk.py:18`）：

| 等级 | 值 | 依据（来自代码注释） |
|---|---|---|
| `READ` | `read` | 无副作用——始终允许 |
| `EGRESS` | `egress` | 触达网络——请求本身就可能把数据带出本机 |
| `WRITE_LOCAL` | `write_local` | 变更工作区——按路径限定 + 受模式门控 |
| `EXEC` | `exec` | 执行命令——受模式门控 |
| `EXTERNAL` | `external` | 本机之外的副作用——无人值守 Inbox 挂钩 |

`send_message` 被按 EGRESS 门控，因为消息正文是模型自选的自由文本，走的是同一条出站通道（spec §2.2）；`browser_open_url` 同样分类（“同一条通道的另一个名字”，OPE-111）。

### 3.3 硬底线

`PermissionEngine.evaluate()`（`permissions.py:326`）返回一个 `Decision`（allowed / reason / needs-user / human-only / rule-matched）。某些底线在**任何**模式、允许列表或评审器发言之前运行：

1. **自我保护**——`protected_paths()`（`permissions.py:93`）：agent 不得编辑 OpenWorker 自身的设置、秘密或权限配置。
2. **可写根目录**——对已授予工作区根目录之外的写入直接拒绝。
3. **延迟执行**——`.git/hooks`、CI 配置及类似的“稍后执行”文件仅限人工：批准一次文件写入不等于批准之后无人值守运行的代码（OPE-117）。
4. **持久权威**——`PERSISTENT_AUTHORITY_TOOLS`（`permissions.py:85`）：`save_skill` 和定时任务 CRUD 能安装未来行为，因此始终仅限人工。
5. **来源追踪底线（OPE-114 §1）**——运行 agent *本会话下载的*东西是典型的“先取后执行”攻击，所以即使在 Auto-Approve 模式下也强制出人工卡片，覆盖评审器和命令允许列表。引擎跟踪这个会话级的来源追踪集合；评审器根本看不到这些卡片，无从判断。

模式级配置遵循同一方向：仓库配置只能*收窄*权限（`config.py` 中的 `_GLOBAL_ONLY_FIELDS`）——签入的文件永远不能授予用户本不具备的能力。

### 3.4 授权流水线

`engine.py::_authorize` 对每次工具调用运行：

```
PermissionEngine.evaluate()
   → provenance floor (OPE-114)          [human card, overrides everything]
   → Auto-Approve reviewer (if plugged)  [may turn ask→allow, never blocked→allow]
   → PERMISSION_REQUIRED event + approver callback
        → ApprovalOutcome: once | always_tool | always_command
                           | always_domain | readonly_session | deny
```

审批卡片是交互式的（以事件流式下发，并等待注入的异步 `approver` 审批回调），且每一张未答复的卡片都会先作为持久化 Inbox 条目停放，因此断开的 socket 或重启不会丢失任何东西（见第 4 章（server-and-auth.md））。

常驻规则：任务级条目（“工具 → 精确目标”）为自动化运行自动放行匹配的调用——限定在精确目标，而非整个工具。

### 3.5 Auto-Approve 评审器

`coworker/reviewer.py::Reviewer` 仅在 `Mode.AUTO_APPROVE` 下运行。它是一个 LLM 裁判，使用**与会话相同的提供商和模型**，对所有将成为卡片的请求一次性预先咨询（`_preconsult_reviewer`），从而以较低成本让一次判断覆盖整批。

不变量：

- 它只能把**询问转为允许**。权限引擎已阻止的调用保持阻止；评审器无权扩大策略。
- **拒绝**时，agent 收到简短拒绝（完整推理展示给*用户*，另有一次性的“仍然允许”）。
- **熔断器**：连续 5 次拒绝触发 `_REVIEWER_TRIP`，并在本回合剩余时间内暂停自动批准。（连续计数语义依负责人 2026-08-24 裁定：从 2 提高到 5 并改为累计感知，因为天真的 2 次计数会静默惩罚合法的来回交互。）
- **影子模式**记录评审器裁定而不影响决策——用于 `tests/corpora/` 中的评估语料和 `reports/` 中的报告。

### 3.6 授权存放位置

`always_*` 结果铸造**会话授权**——它们不会扩大到当前会话之外，且自我保护/延迟执行底线仍然叠加适用（授权永远不能覆盖底线）。自动化任务携带自己的任务级 `always_allowed_tools` 规则，见第 9 章（automation.md）。

### 3.7 插件同意治理

> 源码：`coworker/plugins/`（manifest、adapters、consent、lifecycle、
> consent_audit）、`coworker/server/manager_mcp.py`、
> `coworker/connectors/marketplace.py`。

工具调用审批治理 agent 在会话*内部*做什么。一条并行的同意线治理*安装*什么：persona、技能、连接器插件和 MCP 服务器都携带权限，安装或更新它们可能扩大未来会话能做什么。共四部分：

#### 3.7.1 统一的 PluginManifest（适配层）

四种扩展类型保留各自的原生格式、加载器和存储；一个读侧适配器把每种投影到同一个 `PluginManifest(kind, id, name, version, deps, capability_set, permissions, signature, source, description)`：

- `kind ∈ {persona, skill, connector, mcp}`；`capability_set` 是一个扁平、可比较的集合，命名空间为 `tool:<id>` / `mcp:<name>` / `connector:<name>` / `skill:<name>` / `messaging` / `team:<role>`。
- `permissions` 携带各类型特有的语义（persona：连接器授权 + `default_mode`；skill：`allowed_tools`；connector：`auth` / `two_way` / `mcp_backed`；mcp：`requires_approval` + `include/exclude_tools`）。
- 签名对目录包复用 `personas/signing.py`（`manifest.sha256` + minisign）；MCP 服务器是 JSON 配置而非包，保持未签名——其风险由 `requires_approval` + 工作区信任门控。
- `consent_summary(pm)` 产生统一形状（`id/name/kind/version/capability_set/risks/permissions/signature/source`），使 UI 对所有类型复用 persona 同意卡片。

#### 3.7.2 统一安装流水线

`plugins/lifecycle.install/update/uninstall` 是唯一入口：解析 → 适配器 → 可选签名校验 → `capability_set` → 对已安装的同类型 id 做 `capability_diff`（增长 ⇒ `requires_consent: true`，调用方必须在提交前取得同意）→ 委托给原生存储。连接器安装/卸载和 MCP 添加/修改已改道经由此流水线（此前它们完全绕过同意）；REST 响应新增嵌套 `consent` 键，同时保留全部旧字段。GUI 契约不变——点击安装或提交 MCP 表单*就是*同意动作；没有增加第二个对话框。

#### 3.7.3 同意 v2：权限扩大闸门

`capability_diff` 比较能力集合，但连接器/MCP 的能力是常量（`connector:<name>`、`mcp:<name>`），因此像 `requires_approval: true → false` 这样的权限语义变更过去会静默落地。v2 闸门增加 `permission_diff(old, new) -> {"widened": [...]}`：

| 变更 | 判定 |
|---|---|
| `requires_approval` true → false | 扩大 |
| `include_tools` 列表 → null（全部开放） | 扩大 |
| `include_tools` 增加条目 | 扩大 |
| `include_tools` null/列表 → （更短的）列表 | 收窄——不受门控 |
| `exclude_tools` 减少条目 | 扩大 |
| `two_way` false → true（开启入站消息） | 扩大 |

在 `PATCH /v1/mcp/{name}` 上，未带 `confirm: true` 的扩大变更返回 **409** 及同意载荷，并保持配置不变；带 `confirm` 则落地。收窄或未变更的更新立即生效。`confirm` 键在持久化前被弹出——它永远不会进入 `mcp.json`。

#### 3.7.4 同意审计账本

每次安装 / 卸载 / 权限变更都会向 `<state-dir>/plugin_audit.jsonl` 追加一条记录：`ts / kind / id / action (install|uninstall|update) / decision (accepted|deferred|denied) / source / capability_set / risks / permissions / widened`。被阻止（409）的 patch 记录为 `denied` 并带其 `widened` 列表，因此“这个服务器的批准何时以及为何被禁用”事后可答。只记录同意摘要——绝不记录 env 值、token 或原始配置。写入失败时静默（与回合轨迹账本相同的契约：审计绝不能阻塞请求），`GET /v1/plugins/audit?limit=&id=` 可读回（最新在前，跳过损坏行）。

---

## 4. 服务端与认证

> 源码：`coworker/server/app.py`（`create_app`）、`coworker/server/manager.py`
> （`SessionManager`）、`coworker/server/run.py`。

Python 后端是一个 FastAPI + uvicorn 应用，**仅绑定 127.0.0.1**。它是引擎、会话、持久化、调度器、连接器网关和 Inbox 的唯一所有者。`SessionManager`（`manager.py:180`）是把它们连接起来的上帝对象；`create_app()`（`app.py`，约 2,800 行）将它们暴露出来。

### 4.1 传输模型

GUI 用 REST 管状态，每个会话一条 WebSocket 承载实时回合：

- **`GET /ws/session/{session_id}`** —— 主通道。JSON 帧 `{type, data}`。
- **`GET /ws/events`** —— 只读的应用级推送流（toast、会话列表变化），独立于任何单个会话。
- **REST** —— `/v1/` 下约 150 个端点：sessions CRUD + messages / roots / artifacts、inbox、personas 安装/导出、skills、workspaces（open/trust/pick）、connectors（+ `/oauth/callback`）、providers、settings、automations、board（`/v1/board`，带按 actor 的 Bearer token）、browser state、audit。

#### 4.1.1 WebSocket 协议（会话通道）

客户端 → 服务端：`user_message`、`approval`、`question_response`、`plan_response`、`directory_response`、`tool_response`、`allow_anyway`、`interrupt`、`retry`、`set_mode`、`set_model`、`steering`。

服务端 → 客户端：`ready`、原样广播的引擎事件（`assistant_delta`、`reasoning_delta`、`tool_proposed`、`permission_required`、`tool_started`、`tool_finished`、……）、`turn_done`、`mode_notice`、`model_changed`、`question_requested`、`input_rejected`。

全部四种交互式提示（审批、提问、计划、目录）在任何人被询问*之前*就作为持久化 Inbox 条目停放（`inbox.wait`）——WS 帧只是答复的一种方式。条目同样可以通过 REST（`POST /v1/inbox/{id}/resolve`）或从绑定的渠道（带 `[ow:<id>]` token 的 Slack 回复）解决，先答复者胜。这就是断开的 socket、重启和无人值守运行不丢东西的原因：见第 9 章（automation.md）。

### 4.2 认证：启动令牌

sidecar 用每次启动生成的 token 认证每个请求（HTTP 头和 WebSocket 子协议）：

- 由中间件 `require_sidecar_token`（`app.py:214`）以恒定时间比较校验。以 `X-OpenWorker-Token` 头发送，或作为 `openworker` WebSocket 子协议发送（CORS 不门控 WebSocket，因此仅靠 origin 允许列表不够）。
- **桌面模式**：Tauri 在内存中生成 token，并通过环境变量传给两个子进程（`run.py::_ensure_api_token`，`run.py:128`）；它从不写入磁盘。
- **独立模式**：token 写入仅用户可读的文件 `<state-dir>/sidecar-<port>.token`（关闭时删除），使 Vite 开发 UI 和 curl 可以认证。

额外加固（针对 loopback 服务器的浏览器来源攻击）：

- Origin 允许列表：`tauri://localhost`、`127.0.0.1`、`tauri.localhost`（`app.py:34`）——感知 DNS 绑定。
- WS 输入受速率限制且有帧大小上限（`app.py:47-54`）。

### 4.3 SessionManager 职责

除路由之外，管理器掌管记录在案的权威流程：

- 每会话的引擎生命周期（通过 `agent.py::build_engine` 构建）；
- `_dispatch_inbound` —— 来自连接器网关的渠道消息按渠道缓冲，并扇出到已订阅的会话；
- `_route_mention`（§31 提及路由器）—— 一次 @ 提及会派生一个专用的按线程 coworker 会话，带有针对该线程目标的常驻 `send_message` 授权和预加载的渠道上下文；DM 路由到指定的 DM 会话，或作为未路由停放；
- `_run_scheduled_task` —— 每次自动化触发都是一个全新的持久化对话线程（引擎按运行构建，之后可继续），并可选向任务的 `notify_target` 发送完成通知；
- 团队唤醒管线 —— 每小时滚动的 worker 唤醒预算、陈旧度摘要和签到兜底（见第 11 章（teams.md））。

### 4.4 启动与状态

`run.py` 解析配置（`docs/config.example.toml` 记录了这些键），确保 token 存在，并把 `create_app(manager)` 交给 uvicorn。管理器拥有的状态文件清单位于第 1 章（architecture.md）的“状态目录清单”一节；每个存储都进行防御性解析，因此损坏的行只会降级而不会杀死服务器。

---

## 5. 模型提供商与多模型

### 5.1 模型提供商

> 源码：`coworker/providers/` — `base.py`（ABC + 值类型）、`router.py`（`ProviderRouter`）、`registry.py`（描述符 + 构造器），以及各原生提供商模块。

#### 5.1.1 抽象

`ProviderClient`（`base.py:102`）是一个小型 ABC——`complete` / `stream` / `capabilities`——作用于**规范的 OpenAI 形状消息格式**。提供商层之上的一切（引擎、持久化、对话记录）只会见到该形状，外加值类型 `AssistantTurn`、`ToolCall`、`TokenUsage`、`StreamChunk`。

`ProviderRouter`（`router.py:23`）按模型字符串前缀路由（`anthropic/claude-…`、`openai/gpt-…`），`registry.py` 声明 `ProviderDescriptor`：面向 UI 的表单（标题、API key 字段、端点预填、环境变量回退 key）外加一个产出 `ProviderClient` 的 `build` 函数。

#### 5.1.2 原生 SDK 客户端

并非一切都经过同一个 OpenAI 兼容垫片。当提供商具备兼容形状无法承载的能力时，就存在原生客户端：

| 模块 | 提供商 | 原生原因 |
|---|---|---|
| `anthropic_provider.py` | Anthropic | Claude Messages API；thinking 块通过 `_anthropic` 消息 sidecar 携带 |
| `gemini_provider.py` | Google | google-genai；thought signature 通过 `_gemini` sidecar 携带 |
| `bedrock_provider.py` | AWS Bedrock | boto3（可选依赖，惰性导入） |
| `vertex_provider.py` | GCP Vertex | service-account / ADC / MaaS-bearer 凭据 |
| `openai_responses.py` | OpenAI | 原生路由到 `/v1/responses`——chat.completions 在 GPT-5.6 上拒绝 tools+reasoning |
| `codex_provider.py` | OpenAI Codex | Codex 专用认证 |

#### 5.1.3 OpenAI 兼容厂商

`openai_provider.py` 是兼容主力（也涵盖 Ollama）。已注册的厂商（`registry.py`）：`ark`（BytePlus）、`ark-agent-plan-cn`（Volcengine）、`zai`（Z AI / GLM）、`deepseek`、`kimi`（Moonshot）、`minimax`、`qwen`（Alibaba）、`xai`（Grok）、`mistral`、`meta`（Muse Spark）、`together`、`fireworks`、`openrouter`、`ollama`（本地，用端点字段而非 key）。每个描述符预填该厂商的官方端点；该字段保持可编辑，以适配区域/代理变体。

#### 5.1.4 Sidecar 与模型切换

规范消息列表可以携带私有键——`_anthropic`（thinking 块）、`_gemini`（thought signature）、`_display`（仅 GUI）。引擎的 `_outbound_messages()` 在每次请求前剥离除当前提供商可理解内容之外的一切。这一条规则换来一个标志性功能：**你可以在会话中途切换模型**——历史保持有效，因为没有任何提供商的私有格式渗入共享对话记录。

#### 5.1.5 aisuite 的真实位置

尽管 README 写着 "built on aisuite"，aisuite 在这里**不是** LLM 传输层。它用于：

- 工具 schema 生成（`tools/registry.py` 中的 `aisuite.utils.tools.Tools`）以及工具函数上的 `ToolMetadata`；
- 预构建工具包（`catalog.py` 中的 `aisuite.toolkits` files/git）——其中一些被 OpenWorker 以自己的更快变体替换。

提供商传输、流式、权限以及 agent 循环全部由本仓库自行实现。`pyproject.toml` 中固定的 commit 是从原 aisuite 单仓库导入的接入点。

#### 5.1.6 PDF 支持

`coworker/pdf_support.py` 为没有原生 PDF 输入的模型适配 PDF 附件：pypdf 用于文本抽取，pypdfium2 用于页面栅格化——刻意*不*使用 PyMuPDF，其 AGPL 许可证无法随 DMG 分发（见 `pyproject.toml`）。

### 5.2 多模型路由（fast model + 升级基础设施）

> 源码：`coworker/config.py`（`fast_model`）、`coworker/agent.py::build_engine`（reviewer 回退链）、`coworker/engine.py`（`_select_model`、`_compact_now` 回退链）、`manager.py`（配置传递）。

会话可以指定一个 **fast model**——用于不驱动主 agent 循环的辅助调用的更轻量模型。主循环始终使用强模型；当设置了 fast model 时，辅助调用默认使用它。

#### 5.2.1 回退链（按调用点）

| 调用点 | 链 |
|---|---|
| **压缩** | `compaction_settings["model"]` > `fast_model` > 会话模型 |
| **Reviewer** | `reviewer_model`（配置）> `fast_model`（配置）> 会话模型 |
| **主循环** | `self._select_model()` → `self.model`（v1：始终为会话模型） |

#### 5.2.2 设计理由

**辅助调用先行，主循环在后**：压缩是最昂贵的辅助调用（它处理整个 span）。将其路由到 fast model 可节省大量 token，且不影响 agent 质量——摘要供 agent 参考，而非用于执行。reviewer 同样受益（判断是二元的：allow/deny/unsure）。主循环在 v1 中刻意不做路由——回合中途切换模型有行为不一致的风险。

**`_select_model()` 基础设施**：引擎在每次 `_astream()` 往返之前调用 `_select_model()`。默认返回 `self.model`。v2 的覆写可以在不改动引擎核心循环的前提下实现感知迭代的升级（早期读密集迭代使用 fast model，综合阶段使用强模型）。

#### 5.2.3 配置

| 字段 | 默认值 | 含义 |
|---|---|---|
| `fast_model` | `None` | 用于辅助调用的更轻量模型（用户全局） |
| `reviewer_model` | `None` | reviewer 专用覆写（优先于 `fast_model`） |

回退链：显式覆写 > `fast_model` > 会话模型。仓库配置无法设置其中任一项（两者都是 `_GLOBAL_ONLY_FIELDS`）。

#### 5.2.4 测试

`tests/test_multi_model.py`——9 个测试：配置可设置、`_select_model` 默认返回会话模型（即使设置了 fast_model）、主循环使用会话模型（而非 fast_model）、压缩回退链（3 个用例）、reviewer 回退链。

---

## 6. 工具系统

> 源码：`coworker/tools/`、`coworker/catalog.py`、`coworker/toolchain.py`，以及工具包 `coworker/web/`、`coworker/connectors/`、`coworker/mcp/`。

### 6.1 注册表

`ToolRegistry`（`tools/registry.py`）持有晚绑定的工具可调用体，其 OpenAI 函数 schema 经 `aisuite.utils.tools` 生成。模块开头的注释写明一条规则：**注册表永不执行权限检查。** 它负责执行；裁定由引擎做出——在每次调用上、在执行之前（见第 3 章（permissions-and-approvals.md））。工具以实时设置 getter 的形式接线，而不是烘焙好的快照，因此配置变更会作用于下一次调用。

### 6.2 内置工具（`coworker/tools/`）

| 文件 | 工具 | 说明 |
|---|---|---|
| `shell.py` | `run_shell`、`shell_task_output`、`kill`（经 hooks 的 `interrupt_now`） | 持久 OS 原生 shell REPL——bash 或 PowerShell——位于一个 `Executor` ABC 之后，该抽象显式为未来的 `ContainerExecutor` 沙箱留出余地。超时 + 后台任务句柄。 |
| `files.py` | `read_file`、`write_file`、`replace_in_file`、`apply_patch`、`apply_unified_diff` | `read_file` 带窗口与行号——*处处唯一读取器*（负责人裁定 2026-08-20，`catalog.py:76`）：整个栈共享它，而不是让每个工具重复读取。 |
| `search.py` | `grep` | ripgrep，带纯 Python 回退。 |
| `git.py` | `git_log` + status/diff | status/diff 委托给 aisuite 工具包。 |
| `todo.py` | `todo_write` | 会话任务清单。 |
| `ask.py` | `ask_user` | 门控工具（由引擎处理）。支持分组提问（OPE-51）：每张卡片最多 4 个 `{question, header, options, allow_text, multi}`，因为一行按钮无法回答 2 个以上问题。 |
| `plan.py` | `propose_plan` | 门控工具；PLAN 模式的目标。 |
| `directories.py` | `request_directory` | 门控工具：agent 请求用户授予一个文件夹根。 |
| `toolreq.py` | `request_tool` | 门控工具：安装缺失的 CLI 工具——**仅从固定版本、经摘要校验的目录安装**（OPE-85：取回可执行文件是供应链决策，绝不自动运行）。 |
| `subagent.py` | `explore` | 只读子代理扇出，用于大范围搜索。 |

### 6.3 Web 工具（`coworker/web/`）

- `web_search`——提供商可配置；默认无 key 的 DuckDuckGo（`ddgs`），配置后经 `httpx` 使用 Tavily/Brave。
- `web_fetch`——配合 `guard.py`，出网守卫使抓取保持在权限系统之内（风险类 EGRESS；见第 3 章（permissions-and-approvals.md））。

### 6.4 扩展平面

- **连接器工具**（`connectors/tools.py` + 各连接器模块）：经无状态发送器（`senders.py::DEFAULT_SENDERS`）为 Slack/Telegram/邮件提供出站 `send_message`；外加 Playwright 浏览器自动化（可选 `browser` 依赖），带根限定上传（OPE-122）与重定向复检（OPE-124）。
- **MCP 工具**：每个远端工具都桥接为 `mcp__<server>__<tool>`——见第 7 章（mcp-client.md）。
- **记忆工具**（`memory/tools.py`）：`remember`、`memory_update`、`memory_forget`、`memory_read`——见第 10 章（memory.md）。
- **技能**（`skills/`）：`load_skill` / `save_skill`——见第 12 章（personas-and-skills.md）。
- **自动化工具**（`automation/tools.py`）：计划任务 CRUD——`PERSISTENT_AUTHORITY_TOOLS` 成员，永远人工批准。
- **团队工具**（`teams/tools.py`）：看板/日志簿动词，按角色过滤——见第 11 章（teams.md）。

### 6.5 能力目录

`coworker/catalog.py` 持有 `CATALOG`，一组声明式的具名能力，`expand()` 将 persona/agent 声明的名字转换为具体工具集。Persona 从不罗列裸函数；它们组合能力目录条目，这让“这个 persona 能触碰什么”可在一处审计。

### 6.6 工具链解析

`coworker/toolchain.py` 刻意将两个问题分开（OPE-82）：

1. **查找**二进制——使用登录 shell 的环境进行 PATH 解析（桌面壳在拉起时将其传入，OPE-83），因此 GUI 启动的服务仍能看到用户的 `~/bin`、homebrew 等。
2. **安装**二进制——始终位于 `request_tool` + 人工审批 + 摘要固定之后（OPE-85）。解析与安装之间的区分就是供应链边界。

0.2.1 中**没有 OS 级沙箱**。安全以分层方式实现：对每次调用进行权限闸门、根限定路径、超时、非交互 shell 环境，以及第 3 章（permissions-and-approvals.md）中的硬底线。`shell.py` 中的 `Executor` ABC 是为未来 `ContainerExecutor` 指定的接缝。

---

## 7. MCP（客户端与服务端）

### 7.1 MCP 客户端

> 源码：`coworker/mcp/` —— `config.py`、`client.py`、`tools.py`、`oauth.py`。使用官方 `mcp` Python SDK（版本固定为 `>=1.28.1,<2` —— 下限用于规避 PYSEC-2026-3481/3482/3483，上限是因为 2.0.0 移除了 `streamablehttp_client`）。

任何 MCP 服务端都能作为工具源接入 OpenWorker，并支持逐工具控制。

#### 7.1.1 配置

标准 `mcpServers` JSON —— 可直接从 Claude Desktop 粘贴。分层：

- **全局**：`~/.config/coworker/mcp.json`
- **工作区**：`<workspace>/.coworker/mcp.json` —— 仅对*受信任*工作区生效；名称冲突时以全局文件为准。

`MCPServerDef` 字段：transport（stdio 或 streamable-http），以及每个服务端的 `include_tools` / `exclude_tools`、`requires_approval`，和从环境变量解析 `${VAR}` 密钥。工作区配置可以收窄某个服务端的工具面，但绝不能静默扩大信任 —— 与权限配置相同的方向性规则。

#### 7.1.2 生命周期

`client.py::MCPManager` 为**每个服务端保持一个常驻 asyncio 任务**：

- **stdio** —— `stdio_client`，stderr 捕获到临时文件，崩溃时为用户（以及 `request_tool` 式诊断）留下证据。
- **streamable-http** —— `streamablehttp_client`。

API：`ensure()`（启动/幂等）、`tools()`（列表）、`verify()` —— 一次真实的健康检查，实际往返调用一次 `tools/list`，而不只是探测套接字 —— 以及 `call()`。

#### 7.1.3 工具桥接

`tools.py::build_callables` 把每个 MCP 工具包装为名为 `mcp__<server>__<tool>` 的**同步注册表可调用体**，经 `run_coroutine_threadsafe` 桥入服务端的 asyncio 任务（引擎在工作线程中执行工具）。OpenAI 函数 schema 由 MCP `inputSchema` 推导。自此，MCP 工具就是普通工具：同样的权限管线、同样的审批卡，`requires_approval` 映射到常设规则机制。

#### 7.1.4 OAuth（需要认证的 MCP 服务端）

`oauth.py` 在 SDK 的 `OAuthClientProvider` 之上实现 **OAuth 2.1 + PKCE + Dynamic Client Registration**：

- token 存放于 `SecretStore`，profile 为 `mcp-oauth:<server>`。
- 浏览器回调落在 sidecar 回环地址上的 `GET /mcp/oauth/callback`。
- 非交互上下文（自动化运行、中继）拒绝弹出浏览器登录（`InteractiveAuthRequired`），但可以静默刷新有效 token。

### 7.2 MCP 服务端（完整能力暴露）

> 源码：`coworker/mcp_server.py`（FastMCP 服务端 + HTTP 桥）、`coworker/cli.py`（`--mcp` 标志）、`coworker/teams/mcp_server.py`（仅看板服务端，此前已存在）。

OpenWorker 的核心能力 —— 会话、工件、审批、自动化、审计、时间线 —— 以 MCP 工具形式暴露在 stdio 上。外部智能体（Claude Code、Cursor、任何 MCP 客户端）可以像 GUI 一样，经同一权限引擎与 Inbox 流程驱动一个 OpenWorker 实例。

#### 7.2.1 启动

```bash
# sidecar 必须已在运行。
openworker mcp --mcp-url http://127.0.0.1:8765 --mcp-token <token>
# 或者让它从 sidecar 的 token 文件中读取 token。
openworker mcp
```

MCP 客户端配置（例如 Claude Desktop）：
```json
{
  "mcpServers": {
    "openworker": {
      "command": "openworker",
      "args": ["--mcp"]
    }
  }
}
```

#### 7.2.2 暴露的工具

| 工具 | 方法 | 作用 |
|---|---|---|
| `session_list` | GET | 所有会话及其状态、工作区、智能体 |
| `session_get` | GET | 单个会话的完整记录 |
| `artifact_list` | GET | 会话产出的文件 |
| `artifact_read` | GET | 读取文件内容（文本或 base64） |
| `inbox_list` | GET | 待处理的审批/提问/通知 |
| `inbox_resolve` | POST | 批准/拒绝/回答一个 Inbox 条目 |
| `automation_list` | GET | 所有自动化及其状态与时间表 |
| `automation_create` | POST | 创建一个计划任务/事件触发任务 |
| `automation_run` | POST | 立即运行一个自动化（返回 session_id） |
| `automation_delete` | POST | 删除一个自动化 |
| `board_list` | GET | 团队看板条目 |
| `board_item` | GET | 单个看板条目的完整内容 |
| `audit_list` | GET | 近期工具活动（已脱敏） |
| `session_timeline` | GET | span 级执行时间线 |

#### 7.2.3 安全性

MCP 服务端是一个**薄 HTTP 桥** —— 不持状态、不做决策，也从不直接触碰文件系统。每次调用都经 sidecar 的权限引擎（同一审批流程、同一 Inbox 停靠）。外部智能体获得与 GUI 相同的安全保证：写操作出审批卡、延迟执行有硬底线、无人值守工作停靠进 Inbox。

工具结果是 JSON —— 返回给调用方智能体的原始数据，而非散文。

#### 7.2.4 架构

- 服务端经 HTTP 与 OpenWorker sidecar 通信（与看板 MCP 服务端中的 `RemoteDialect` 模式相同）。
- 它作为独立的 stdio 进程运行 —— MCP 客户端拉起它，它代理到正在运行的 sidecar。
- 无认证旁路：必须提供 sidecar token（从 token 文件读取或显式传入）。

---

## 8. 连接器与扩展市场

### 8.1 连接器

> 源码：`coworker/connectors/` —— `descriptors.py`（目录）、`base.py`（适配器 ABC）、`gateway.py`、`adapters.py`、`relay_client.py`、`github_relay.py`、`accounts.py`、`setup.py`、`tools.py`、`senders.py`。

连接器是 OpenWorker 可以施加影响的第三方平台（Slack、GitHub、Gmail……）。约 40 个声明在单一的 `DESCRIPTORS` 注册表（`descriptors.py`）中；`register_descriptor()` 添加额外项。

**已随附 / 可连接**：telegram、slack、email（IMAP）、gmail、google_calendar、browser、github、outlook、jira、monday、confluence、zendesk、linear、gitlab、discord、stripe、asana、hubspot、dropbox、box、whatsapp、quickbooks、docusign、clickup、google_drive、canva、figma、close、notion、attio、posthog、mixpanel、amplitude、apollo、hunter。
**占位**（`available=False`）：datadog、salesforce、descript、clay、pagerduty。`experimental/` 是已确认风险连接器的挂载点（当前为空；发布构建中排除）。

#### 8.1.1 抽象

`base.py::BasePlatformAdapter` —— `connect` / `disconnect` / `send`、入站 `handle_message` 钩子，以及可选的 `send_interactive` / `handle_interaction`（用于 Slack 的 Approve/Deny 按钮）。配套值类型：`SessionSource`（`platform:chat_id[:thread]` 目标令牌）、`MessageEvent`（带 `mentions_me` 标记与 `tagged_text()` 框定）、`MessageSource`（仅供展示的 sidecar）、`InteractionEvent`、`SendResult`。

#### 8.1.2 网关

`gateway.py::Gateway` 持有全部适配器，是唯一的入站门户：

- **入站白名单**（`config.py::is_authorized`）：空白名单不接纳*任何人* —— 失败即关闭。多工作区中继使用按团队的 `TeamAuth`。
- 未授权消息会被暂存（`parked.py::ParkedStore`，一个受 JSON 上限约束的队列），以便“允许并投递”，而非静默丢弃。
- `reply_resolver` 把频道回复（携带 `[ow:<id>]` 标记的 Slack 消息、按钮交互）映射回已停靠的 Inbox 条目，且先于启动任何新回合。

#### 8.1.3 入站 → 会话

`server/manager.py::_dispatch_inbound` 缓冲频道消息并扇出给订阅的会话。**§31 提及路由（mention router）**（`mentions.py`）处理 @ 提及：一条提及会派生一个专属的按线程 coworker 会话，带限定到该线程的常设 `send_message` 授权，并预载频道上下文。DM 进入指定的 DM 会话，或作为未路由项停靠（`unrouted.py`）。邮件入站采用轮询（`email_tools.py`）。

#### 8.1.4 出站

出站是无状态的 `send_message` 工具（`tools.py`），经 `senders.py::DEFAULT_SENDERS`（基于 httpx）—— 推送消息不需要长驻适配器。权限系统将它归为 EGRESS，因为消息体是模型自由选择的自由文本（见第 3 章（permissions-and-approvals.md））。

#### 8.1.5 三条认证路径

按描述符标志选择连接器的凭据方式：

1. **手动** —— 粘贴 token 字段；经一次性 whoami 调用校验（例如 `_validate_slack`）；存放在 `SecretStore` profile `<name>:default`（`setup.py::connect_connector`）。
2. **托管 OAuth**（`managed=True`）—— 经 OpenWorker Cloud 的一键式代办握手（`cloud.py`；按隐私模型这是*唯一*的云组件）。Google 系连接器为 `managed_paused=True`，等待 CASA 审查。
3. **`mcp_url`** —— 厂商托管的 MCP 服务端，配本地 OAuth/DCR（jira → mcp.atlassian.com、monday → mcp.monday.com）；工具面固定为 `tool_defs.py` 中的 `mcp__<name>__<tool>` 定义。

多账号支持（`accounts.py`）：profile 位于 `<name>:account:<id>`（gmail、gcal、hubspot 门户、github 安装）。

#### 8.1.6 云中继

直连入站监听（`adapters.py` 中 Slack 的 Socket Mode、Telegram、轮询邮件）要求服务端持有长命凭据。中继（`relay_client.py::SlackRelayAdapter`、`github_relay.py::GitHubRelayAdapter`、`cloud.py`）是替代方案：桌面端持有一条到 OpenWorker Cloud 的出站 WebSocket，入站事件经 `RelayHub` 按团队/安装白名单扇出（github-relay-spec §2.3、§4、§8）。中继代码永不触碰 secret store。错过的消息在重连时经频道上下文重放补回（spec §7）。

### 8.2 扩展、市场与工作流功能（2026-10）

> 源码：本文归档一批构建在 0.2.1 快照之上的功能。每节列出其源文件；代码与本文不一致时以代码为准。
>
> - `coworker/skills/demo.py`, `coworker/automation/templates.py`
> - `coworker/connectors/marketplace.py`, `coworker/memory/markdown.py`
> - `coworker/security/__init__.py`, `coworker/tools/security.py`
> - `coworker/tools/subagent.py` (delegate_parallel), `coworker/catalog.py`
> - `coworker/server/app.py`, `coworker/server/manager.py`
> - `surfaces/gui/src/components/{SkillsTab,TemplateMarketplace,MarketplaceModal,ScheduledView,MemorySection,SecuritySection,HireModal,RightRail,SettingsView,PersonasTab}.tsx`
> - `surfaces/gui/public/manifest.webmanifest`, `surfaces/gui/index.html`, `surfaces/gui/src/styles.css`

此处归档十项功能，分为三个主题：

1. **登录与云的可选性**（分析）—— 已登录 vs 未登录的表面。
2. **市场入口** —— 自动化模板、连接器/插件生态、基于角色的“招聘”落地页。
3. **工作流升级** —— demo→技能、内置浏览器面板、纯文本可编辑记忆、PWA/多设备、并行子代理、可编辑威胁模型 + 发现项闭环。

#### 8.2.1 Demo → 技能（`coworker/skills/demo.py`）

把录制或粘贴的演示转换为真正的 `SKILL.md` 技能。

- **粘贴步骤**（`steps_from_text`）—— 每行一步，或轻量 markdown 列表（`1.`、`-`、`*`），清洗为有序步骤。
- **浏览器录制**（`steps_from_recording`）—— 已保存的浏览器回放（`<data-dir>/browser-replays/*.json`）被转换为可读的有序步骤（“打开页面……”、“点击……”、“输入……”）。
- `build_skill_markdown()` 产出 YAML frontmatter（`name` / `description` / `source: demo`）+ 正文（Steps + Guidelines）。
- 安装走**与上传压缩包相同的暂存路径**（`SkillStore.stage_upload` / `confirm_upload`）：生成的 SKILL.md 写入暂存目录，用户在确认前审阅。设置列表显示 `demo` 来源徽标。

API：`POST /v1/skills/demo` → `manager.stage_demo_skill`。
GUI：设置 ▸ Skills ▸ “Record a demo” 入口（粘贴步骤，或经 `GET /v1/browser/recordings` 选择一个已保存的浏览器录制）。

测试：`tests/test_skills_api.py`（`test_demo_skill_*`）。

#### 8.2.2 内置浏览器面板（`surfaces/gui/src/components/RightRail.tsx`）

`browser_*` 工具背后的 Playwright 浏览器会话此前只有后端；现在 GUI 有了实时视图。

- 新增 **Browser** 侧栏分区：实时截图（data URL）、当前 URL/标题、状态、最近动作/错误，并提供 **Screenshot / Open in system browser / Close browser** 动作。
- 分区打开时每 2 秒轮询；`refreshKey`（App 在每次 `browser_*` 工具调用时递增）会立即刷新。
- 浏览器会话首次活跃时自动展开一次（“边跑边看”）。

后端此前已暴露 `GET /v1/browser/state`、`POST /v1/browser/screenshot`、`POST /v1/browser/close`；本批次新增 `GET /v1/browser/recordings`。

#### 8.2.3 自动化模板市场（`coworker/automation/templates.py`）

后端提供的可搜索模板目录（Cursor-Automations 风格）。

- 10 个内置模板（6 个原始 quickstart 模板 + `code-digest`、`pr-risk`、`dependency-audit`、`bug-triage`），每个含 `key/title/blurb/cadence/category/conns/needs_repo/needs_channel/consent/deliver/day/time`。
- `cron_for(day, time)` 组装 5 段 cron；`render_instructions()` 填充 `{repo}/{channel}/{deliver}`。
- 一键实例化走与手动表单相同的校验，并为写类模板追加常设的 `send_message → channel` 授权。

API：`GET /v1/automations/templates`、`POST /v1/automations/templates/{key}/instantiate`。
GUI：Automations 页面中的 `TemplateMarketplace.tsx` —— 搜索 + 分类 chip + 卡片网格 + 配置面板。

测试：`tests/test_marketplaces.py`（`test_template_*`）。

#### 8.2.4 连接器/插件市场（`coworker/connectors/marketplace.py`）

开放生态入口（Claude-Marketplace 风格），两类扩展：

- **精选 MCP 服务端** —— 安装会向用户的 Custom · MCP 分组添加一个 MCP 服务端（`POST /v1/mcp`），工具立即可用。随附 Granola + Linear。
- **社区插件** —— 可 JSON 序列化的连接器描述符（字段、说明、认证）。安装会经 `register_descriptor()` 注册新的连接器卡片，并持久化到 `<data-dir>/connector-plugins/<name>.json`；`load_installed_plugins()`（在 `descriptors.py` 导入时调用）在每次启动时重新注册已持久化的插件。
- 插件选取的名称**不得**与已随附连接器冲突；已随附插件的市场文案（About/Access）在 `catalog_copy.py` 中提供。

API：`GET /v1/connectors/marketplace`、`POST /v1/connectors/marketplace/plugins/{name}/install|uninstall`。
GUI：`MarketplaceModal.tsx`（Connectors 页面上的 ⋮ Marketplace 按钮）。

测试：`tests/test_marketplaces.py`（`test_plugin_*`、`test_marketplace_catalog_*`）。

#### 8.2.5 纯文本可编辑记忆（`coworker/memory/markdown.py`）

Lindy 风格：记忆同时以可编辑的 Markdown 文档存在。

- `memory_to_markdown()` 按 scope 分组条目（`## Global`、`## Workspace: <path>`、`## Session: <id>`），每条记忆一个要点项。
- `parse_memory_markdown()` 把编辑后的文件读回为行。
- 重新导入是**按分区替换** —— 文件成为其所命名每个 scope 的事实来源。

API：`POST /v1/memory/export`、`POST /v1/memory/import`、`POST /v1/memory/reveal`。
GUI：设置 ▸ Memory ▸ “Plain-text memory” 卡片（Export / Show folder / Re-import edited file）。

测试：`tests/test_memory_markdown.py`。

#### 8.2.6 PWA / 多设备（`surfaces/gui/public`、`index.html`、`styles.css`）

Web 构建变得可安装、对手机友好。

- `manifest.webmanifest` + SVG/PNG 图标；`index.html` 新增 manifest 链接、`theme-color`、`mobile-web-app-capable`、`apple-mobile-web-app-capable`、`apple-touch-icon`、`viewport-fit=cover`。
- 移动端 CSS：右栏在 ≤900 px 变为全高浮层（聊天保留宽度），为 URL 栏使用 `100dvh`，≤640 px 时侧栏更窄、输入框字号更大（避免 iOS 聚焦缩放）。
- Service worker（`public/sw.js`，仅生产浏览器构建）：预缓存 shell 与内容哈希资源以支持离线重载；`/v1/*` API 调用与 WebSocket 保持仅走网络，使 sidecar launch token 永不被缓存。

#### 8.2.7 并行子代理与任务分块（`coworker/tools/subagent.py`）

`delegate_parallel` —— 把大任务拆成独立的只读研究子任务并并行运行。

- 每个子任务获得一个全新的只读探索引擎（`build_explorer_engine`）；该工具在 `ThreadPoolExecutor` 中运行它们（真正并行的扇出），并返回 `{ok, results: {name: {report, status}}, count}`。
- 与 `explore` 一同注册给 `subagents=True` 的智能体（`coworker/agent.py`）。
- 这些工具运行时，Progress 面板显示 “Running subagents in parallel…”。

测试：`tests/test_subagent.py`（`test_delegate_parallel_*`）。

#### 8.2.8 可编辑威胁模型 + 发现项闭环（`coworker/security`、`coworker/tools/security.py`）

面向安全 coworker 的 Claude-Security 风格闭环。

- **威胁模型**：`<data-dir>/security/threat-model.md`，一个团队可编辑的 markdown 文件（默认模板含 In scope / Out of scope / Reachability / Mitigations）。智能体工具 `threat_model_read` / `threat_model_update`（更新受审批闸门）。
- **发现项跟踪器**：`<data-dir>/security/findings.json` 承载每个发现项的 `open → fixing → fixed → closed` 状态及其修复 PR url。智能体工具 `security_finding_log` / `security_finding_list` / `security_finding_update`。
- 注册为 `security` 目录能力（`coworker/catalog.py`），并加入 7 个安全族 persona 的清单。

API：`GET/PUT /v1/security/threat-model`、`POST /v1/security/threat-model/reveal`、`GET/POST /v1/security/findings`、`PATCH/DELETE /v1/security/findings/{id}`。
GUI：设置 ▸ Security 标签页（`SecuritySection.tsx`）—— 威胁模型编辑器 + 发现项跟踪器。

测试：`tests/test_security_workflow.py`。

#### 8.2.9 基于角色的“招聘”落地页（`surfaces/gui/src/components/HireModal.tsx`）

应用内的 Lindy 风格角色目录。

- 设置 ▸ Coworkers ▸ **“Hire by role”** 打开一个含 5 张角色卡的模态框（Security Reviewer、Cloud Posture、Dependency Audit、Executive Assistant、Software Engineer）。
- 每张卡片展开为落地页：**What it does / Checks in / Never / Works with**，以及 **“Meet the coworker →”**，后者打开该 persona 的详情页（配置 / 启动会话）。

#### 8.2.10 已登录 vs 未登录（分析）

早先的分析记录了云登录在哪些地方改变行为；以下工作日志以摘要形式记录，而非代码。

### 8.3 新增的磁盘状态

| 路径 | 用途 |
|---|---|
| `<data-dir>/skills-staged/<token>/SKILL.md` | 暂存的 demo 生成技能（确认前） |
| `<data-dir>/browser-replays/*.json` | 已保存的浏览器录制（既有；现已可经 HTTP 列出） |
| `<data-dir>/connector-plugins/*.json` | 已安装的市场插件（启动时重新注册） |
| `<data-dir>/memory/MEMORY.md` | 导出的纯文本记忆（全局默认位置） |
| `<data-dir>/security/threat-model.md` | 可编辑威胁模型 |
| `<data-dir>/security/findings.json` | 发现项跟踪器 |

### 8.4 验证

- 后端：`pytest tests/test_marketplaces.py tests/test_memory_markdown.py tests/test_subagent.py tests/test_security_workflow.py tests/test_skills_api.py tests/test_connectors.py` → 最终回归运行 **105 passed**。
- GUI：`tsc --noEmit` 无告警；`scripts/check_i18n.py` 报告所有 `t()` key 均已翻译且无过期条目。
- 已知的仅环境相关失败（与这些功能无关）：`test_automation.py` 中 Windows 缺 `time.tzset()`；`test_security_bundles.py` 两个测试中 UTF-8 persona 文件的 GBK 解码；vitest 在 DSH 沙箱下无法拉起 esbuild（EPERM）。

---

## 9. 自动化与持续目标

### 9.1 自动化（定时任务）与 Inbox

> 源码：`coworker/automation/`（`models.py`、`store.py`、`scheduler.py`、
> `tools.py`）、`coworker/unattended.py`、`coworker/inbox.py`、
> `coworker/selfwake.py`。

自动化 = 无人值守地重复执行的工作（"晨间简报"、"周报"、"盯一个频道"）。难点在于 agent 可能在运行中途撞上审批而现场没有人——这正是 Inbox 要解决的问题。

#### 9.1.1 模型

`models.py`：

- **`ScheduledTask`** —— 标题、指令、`Schedule`（kind 为 `cron` | `once`，带一个 IANA 时区）、工作区、agent/persona、`always_allowed_tools`（任务级常驻规则："tool → exact target" 条目，而非全量工具授权）、可选的 `notify_target`。每个任务拥有一个会话 id `__task__<id>`。
- **`TaskRun`** —— 一次触发：会话 id `__run__<run_id>`，状态 running/ok/error/skipped，触发方式 schedule/manual/catchup。

`store.py::TaskStore` 持久化到 SQLite（`automation.db`），行按 `next_run`/`enabled` 建索引。下次触发时间的计算使用 croniter 且感知夏令时（naive local base 或 `ZoneInfo`；`tzdata` 在 Windows 上是硬依赖，原因正是这个——见 `pyproject.toml`）。

#### 9.1.2 调度器

`scheduler.py::Scheduler` 在常驻服务端内每 30 秒 tick 一次（桌面应用的托盘驻留是它保持存活的保证）：

- **启动时单次补跑（run-once catch-up）** —— 应用关闭期间错过的一次触发只跑一次，而不是 N 次。
- **重叠即跳过（skip-on-overlap）** —— `_running_ids`：若某任务上一次运行仍在进行，新的触发被标记为 skipped。
- 每次运行都作为**独立任务**生成，因此一次停在审批上的运行永远不会拖住调度器循环本身。
- `extra_tick` 恢复自唤醒会话（`selfwake.py` —— 一个请求稍后再被检查的会话）。

每次触发（`manager.py::_run_scheduled_task`）都是一个全新的、已持久化的、可继续的会话线程：每次运行都新建引擎，完整对话记录保存，toast 广播，可选的完成消息通过连接器发送器发到 `notify_target`。

#### 9.1.3 无人值守运行与 Inbox

`unattended.py::UnattendedRegistry` 将一个会话标记为无人值守（`unattended`，即定时运行，以及用户离开的任何会话）。这个标记只改变一件事：每个交互式提示都被路由到**跨会话 Inbox**，而不是 GUI。

`inbox.py::InboxStore`（`inbox.json`）是需要人关注的队列。条目类型：`approval`、`question`、`notification`、`directory`（文件夹授权）、`plan`、`tool`（缺失的 CLI 工具）。生命周期：`pending → resolved`，**先响应者胜出**——条目可以从以下渠道应答：

1. 在线 GUI WebSocket，
2. REST `POST /v1/inbox/{id}/resolve`，
3. 已绑定的频道——包含 `[ow:<id>]` 的 Slack 回复，或由网关的 `reply_resolver` 解析的 Approve/Deny 交互按钮（见第 8 章（connectors.md））。

条目处于 pending 期间，`inbox_approver` 会挂起 agent 运行（不忙轮询，也不会在超时后强制采用默认答案）。这跟让 GUI 会话在套接字断开后仍存活的停泊机制是同一套（见第 4 章（server-and-auth.md））——无人值守只是 Inbox 成为*唯一*应答渠道的那种情形。

#### 9.1.4 创建任务

两个入口：GUI 的 Scheduled 视图，以及 agent 自身通过 `automation/tools.py`。后者被刻意设得很昂贵：定时任务 CRUD 是 `PERSISTENT_AUTHORITY_TOOLS` 的成员（见第 3 章（permissions-and-approvals.md））——始终需要人工审批，因为带有常驻授权的任务执行时所用的权限会比创建它的对话活得更久。

### 9.2 持续目标（事件触发的自动化）

> 源码：`coworker/automation/triggers.py`（EventTrigger + 求值器）、
> `scheduler.py`（`_event_due` 边沿/冷却循环）、`store.py`
> （`event_tasks`、`compute_next_run`）、`manager.py`（`_event_context` 读取器、
> 事件开场消息）、`automation/tools.py`（面向 agent 的 schema）。

一个 ScheduledTask 可以携带一个可选的 **trigger** —— 一个宣告式事件条件，让任务在 cron 通道之外触发。从"定时工作"到"守护目标"：*"当看板条目变为 blocked 时"、"当 build.log 包含 ERROR 时"、"当有什么东西停在我的 inbox 里时"* → 任务正常的运行机制照常执行（同一个 runner，同一份运行历史，同一套停泊审批流程）。

#### 9.2.1 条件（v1 —— 全部本地可观测）

| 类型 | 参数 | 触发时机 |
|---|---|---|
| `file` | `path`（相对路径）、`mode: exists\|changed\|contains`、`value` | 文件存在 / 自上次观测后发生变化 / 包含该子串 |
| `board_state` | `state`（open…canceled）、`min_minutes?` | 工作区的看板中存在该状态的条目（可选地要求已超过 `min_minutes` 时长） |
| `inbox` | `kind?`（approval/question/notification） | 任一 pending 的 Inbox 条目匹配 |

条件读取器是注入的（`Scheduler(event_context=…)`）——调度器从不直接访问 teams/inbox 存储，求值是注入状态上的纯函数（`tests` 用 mock；manager 把它绑定到 `team_store.list_items` + `inbox.pending`）。持续目标在决定是否触发时从不发起网络调用。

#### 9.2.2 语义

- **边沿触发 + 冷却**：在条件从未满足变为已满足的跳变上触发；条件持续满足期间，最多每个 `cooldown_seconds` 重新触发一次（默认 1h，限制在 [5m, 24h]）。边沿/冷却/基线状态持久化在任务记录上（`trigger_state`）——重启不会重复触发。
- **`changed` 先建立基线**：第一次求值记录观测到的 mtime 而不触发——"changed"指的是*我们*观测到的变化，而不是"文件比目标还早"。
- **互斥**：创建时 `trigger` 与 cron/`fire_at` 互斥（tool + manager）；对事件任务的 cron 编辑会被拒绝（tool + `update_automation`）——cron 不能悄悄把一个目标变成定时任务。
- **只带事实的细节**：进入 prompt 的触发细节是路径/状态/计数这类事实，绝不包含文件内容——内容是可被攻击者控制的数据；agent 被唤醒后通过正常权限系统自行读取。
- 路径安全：文件路径在解析时*和*求值时都在任务工作区内解析（即使存储的 trigger 被篡改，也无法逃逸）。

#### 9.2.3 对外接口

- **Agent**：`create_scheduled_task(trigger={kind, params, cooldown_seconds})` —— 对话式惯用法（"watch build.log and fix it when it breaks"）。
- **存储**：trigger + trigger_state 随任务的 JSON blob 存储（无 schema 迁移）；事件任务的 `compute_next_run` → None，因此 `due()` 排除它们；`store.event_tasks()` 是调度器对应的通道。
- **调度器**：`_event_due()` 在每个 30 秒 tick 内、cron 通道之后运行，共享同一重叠保护（`_running_ids`）。
- **API/UI**：`public().schedule` 通过 `schedule_label()` 携带事件标签（"event: file build.log changed"），因此 ScheduledView 无需改动即可展示；详情页对事件任务隐藏 cron 编辑。运行记录标记 `trigger: "event"`，运行开始的 toast/详情携带条件细节；该次运行的开场消息会点明触发事实。
- 测试：`tests/test_automation_triggers.py`（解析/路径/冷却上下限裁剪，各类别求值含 baseline-first + min_minutes + kind 过滤，存储排除，调度器边沿/冷却/清除后回归循环，重叠跳过）。

---

## 10. 记忆与检索

### 10.1 记忆

> 源码：`coworker/memory/` —— `base.py`、`sqlite_store.py`、`settings.py`、
> `tools.py` —— 以及 `coworker/agent.py` 中的注入管线（全文多处引用 MEMORY-SPEC）。
> 这里的行为遵循 "MEMORY-SPEC"，其全文不在本仓库中；代码注释引用了它的条款。

记忆是 coworker *在会话之间记住*的东西——区别于对话记录，也区别于压缩。

#### 10.1.1 模型

`base.py`：`MemoryStore` ABC 与 `MemoryItem`（id、scope `global` | `workspace` | `session`、key、content、summary、workspace、session_id、created_at）。后端：`sqlite_store.py::SQLiteMemoryStore`，位于 `coworker.db`（`memories` 表）。

`session` 作用域是**死的**（MEMORY-SPEC §3）：写入它的请求会重定向到 workspace 作用域，工具也直接拒绝它（`memory/tools.py:77`）。

#### 10.1.2 开关与用户规则

`settings.py::MemorySettingsStore`（`memory-settings.json`）保存：

- **开关** —— 记忆保存的开/关。关闭时，写入工具会当场拒绝（对于硬开关来说，"始终注册、无副作用"并不够好）。
- **`user_rules`** —— 用户在 Settings 中键入的自由文本。逐字注入，且**优先级高于学到的记忆**（MEMORY-SPEC §6）。agent 永远不能写入这个字段——它是用户权威，不是模型知识。

#### 10.1.3 注入

`render_memory_block`（在 `agent.py` 中接线）构建追加到系统上下文的块：

- **完整模式**：渲染后的存储小于 8,000 字符时——全部内容，逐字。
- **索引模式**：超过该阈值时——最新的 10 条完整展示，其余为单行摘要，并额外提供一个 `memory_read` 按 `[#id]` 取用的工具，让 agent 按需拉取更早的条目（MEMORY-SPEC §7）。

agent *知道*的内容在会话开始时固定（MEMORY-SPEC §7.1）——会话中途保存会显示 "I'll remember that" 提示，但不会改变当前回合的上下文。

#### 10.1.4 工具

`memory/tools.py`：`remember`（保存）、`memory_update`、`memory_forget`、`memory_read`（索引模式的取用器）。`remember` 持久化后，`on_saved` 钩子（MEMORY-SPEC §5.1，在 `agent.py:207` 接线）会推送一条 UI 提示——"I'll remember that … [Undo]"——因此保存总是可见且可撤销。

#### 10.1.5 防虚报

系统提示词带有明确的"何时该记住"规则（MEMORY-SPEC §4.2）和防虚报提示，这些是在观测到一次事件 **2026-07-28** 之后加上的：一个模型*伪造*了记忆保存（声称它记住了，却没有调用工具）。这些提示和始终可见的保存 toast 就是对策——记忆的更改必须经由工具并浮出表面，绝不能在散文里断言。

### 10.2 本地工作区检索增强（语义索引）

> 源码：`coworker/rag.py`（分块、VectorStore、reindex/search、tools），
> 在 `coworker/agent.py::build_engine` 中接线（配置后注册）、
> `risk.py`（两个工具都分级为 EGRESS）、`manager.py`（`_rag_config`）。

对工作区文本文件的本地语义索引，以两个工具暴露给 agent：`semantic_search`（一次读取——查询索引）和 `workspace_reindex`（EGRESS + 需审批——会把文件内容发送到向量化端点）。

#### 10.2.1 本地优先

唯一的出站调用就是向量化请求本身，发往用户配置的 OpenAI 兼容端点（config.toml 中的 `embedding_base_url` + `embedding_api_key` + `embedding_model`，均为用户全局配置）。向量与分块文本存放在本地 SQLite 存储（`data_dir/rag.db` 或 `workspace/.coworker/rag.db`），并用 **Python 内的暴力余弦检索**排序——不依赖向量扩展。在 v1 的上限（白名单文本扩展名、每文件 ≤1 MB、≤2k 块）下，暴力计算是个位数毫秒级；ANN 索引会带来复杂度却无收益。

#### 10.2.2 索引

- 白名单文本扩展名（`.md .py .ts .json .yaml …`）；跳过二进制文件和已知垃圾目录（`node_modules .git .venv …`）；超过 1 MB 的文件跳过。
- 滑动窗口分块（1200 字符，150 重叠），优先按段落边界切分。
- **按内容哈希增量**：未变化的文件跳过（向量化器才是昂贵的那部分）；消失的文件会被删除。
- 符号链接逃逸检查：收集到的文件必须解析在工作区内部。
- 若索引过程中向量化端点不可达，该文件标记为 failed 并跳过——索引的其余部分仍会完成。

#### 10.2.3 工具接口

| 工具 | 风险 | 行为 |
|---|---|---|
| `semantic_search(query, k?)` | READ（仅查询文本） | 向量化查询 → 余弦 top-k → 每个命中返回 `{path, chunk, score, text}` |
| `workspace_reindex()` | **EGRESS，需审批** | 增量重建索引；返回统计 `{embedded, skipped, removed, failed}` |

当向量化端点未配置时，两个工具都会如实报告（`"no embedding endpoint configured — set embedding_base_url…"`）而不是猜测一个，并且配置缺失时它们根本不会注册。

#### 10.2.4 v1 边界

- 不假设托管向量化服务——任何 OpenAI 兼容的 `/embeddings` 端点都可用（OpenAI、Ollama 的 shim、Z AI、DeepSeek……）。
- Anthropic 没有 embeddings 端点；仅使用 Anthropic 配置的用户在添加第二家提供商或 Ollama 之前无法使用检索增强。
- 没有 ANN 索引（在强制上限下暴力计算已足够）。
- 文件变化时不自动重建索引（由 agent 或用户调用 re-index；一旦持续目标获得文件系统监视器，文件监视触发是自然的下一步）。

---

## 11. 团队协作

> 源码：`coworker/teams/` — `store.py`、`model.py`、`journal.py`、`chat.py`、`registry.py`、`tools.py`、`dialect.py`、`tokens.py`、`attachments.py`、`cli.py`、`mcp_server.py`。

团队是多智能体编排基座：一个 **lead** persona 将工作拆解为看板工作项，**worker**（各自是一个独立的 agent 会话）领取并完成它们，整份记录防篡改。设计目标（OPE-100）是把看板暴露为一个*开放表面*，任何 harness——包括外部编码 agent——都能加入。

### 11.1 看板是一个事件日志

`store.py::TeamStore`（`teams.db`）为每个空间维护一条**只追加、哈希链式的事件日志**（工作区路径即空间键）。看板工作项是一个*投影*：`_apply`/`rebuild` 在**与追加相同的事务中**把事件折叠成工作项列表，因此日志与视图不会分叉。每个空间的链都可校验（`verify_chain`），并携带一个 `taint` 溯源标记。

为何采用事件溯源：agent 驱动的工作受益于一条无法被悄悄改写的审计轨迹——某个 worker 把自己的工作项标记为完成时，指派事件就紧挨着它，并被哈希锁定。

### 11.2 模型（`model.py`）

- `ItemState` 状态机：`open → in_progress → blocked | review → done | canceled`。
- `WORKER_TARGETS`——worker 从不把工作项标记为完成；完成是负责人/用户的决定。
- `Role`（`user` / `lead` / `worker` / `system`）与 `Actor`。

### 11.3 日志簿与聊天

- `journal.py::JournalStore`（`journal.db`）——按 case 键控的知识，**比看板更长寿**：种类 `finding` / `evidence` / `decision` / `note` / `raw`，正文上限 16k，有自己的哈希链。一张授权表管辖共享（“共享随指派而行”）。
- `chat.py::ChatStore`（`chat.db`）——每个团队一个群聊；agent 发帖只会唤醒被 @提及 的成员（没有空闲 agent 的闲聊）。
- `attachments.py`——内容寻址的 blob；日志携带 `attachment://` 引用，使事件日志本身保持小巧。

### 11.4 编排

`registry.py::TeamRegistry`（`teams.json`）持有团队：负责人 persona（内置 `swe-lead`、`devops-lead`、`devsecops-lead`、`triage-lead`）加上若干 worker。流程：负责人拆解工作（`propose_work_items`）、配置人员（`propose_team`——worker 会话**预先创建但保持空闲：在首次被指派之前零 token**）、指派看板工作项。随后唤醒机制让 worker 持续运转：调度器的 `extra_tick`（见第 9 章（automation.md））、每小时的滚动唤醒预算、陈旧摘要，以及 manager 中的签到兜底——被指派但已陈旧的 worker 会被提醒，而不是被遗忘。

`tools.py` 以**按角色过滤的动词集合**暴露看板/日志簿动词（`LEAD_VERBS` 包含 assign/link；`WORKER_VERBS` 包含 claim），存储层在这些动词之下再次校验权限——工具层只是便利，存储层才是执行者。

### 11.5 开放表面

三种方式触达同一块看板：

1. **`ocw` CLI**（`cli.py`；`ocw board …` / `ocw journal …`）——自动解析为 remote → local-server → direct-SQLite 后端。
2. **`ocw board mcp`**（`mcp_server.py`）——以 stdio MCP 服务器提供同一表面，因此外部编码 agent（任何 MCP 客户端）都能加入团队并领取工作项。
3. **REST `/v1/board`**——带**按 actor 的 Bearer token**（`tokens.py::BoardTokens`，`owb_` 加入 token 以 sha256 哈希存储，绑定 actor + 角色）。

`dialect.py` 抽象后端：`LocalDialect`（直连 SQLite）与 `RemoteDialect`（经 `/v1/board` 的 HTTP）位于同一个 `BoardDialect` 接口之后。Jira/Linear 集成被刻意设计为**镜像，而不是 dialect**——它们把看板状态向外同步，但从不成为记录系统。

---

## 12. Persona、技能与分发

> 源码：`coworker/personas/` — `manifest.py`、`registry.py`、`loading.py` — 以及 `coworker/skills/`。Persona 遵循 “persona manifest” 约定；技能遵循 “SKILLS-SPEC”（引用于代码注释中）。

### 12.1 Persona = 无代码的 agent 定义

persona 是一个**带 YAML frontmatter 的 markdown 文件**（`manifest.md`）：正文即系统提示词，frontmatter 声明能力。persona 不附带任何可执行代码（这是有意的安全属性）。

`manifest.py::PersonaManifest` 严格解析——未知键或格式错误的值会抛出 `ManifestError`，而不是被忽略。关键字段：

| 字段 | 含义 |
|---|---|
| `tools` | 通过 `catalog.py::expand()` 组合的目录能力名 |
| `skills`、`mcp` | persona 可使用的技能文件夹与 MCP 服务器 |
| `connectors` | `False` / 允许列表元组 / `all` 哨兵值——该哨兵值**保留给内置对象，从第三方输入解析时失效关闭**（OPE-93：绝不使用裸的“uses connectors”位） |
| `team` | `lead` / `worker` / 缺省——独行 persona 不具备团队资格 |
| `requires_folder`、`subagents`、`scheduling`、`messaging` | 与 `agents/base.py` 匹配的特性门控 |
| `default_permission_mode`、`recommended_models`、`recommends` | 默认值 + 连接器/MCP 建议（含原因与层级），在 UI 中展示 |
| `version`、`ships`、`group` | 版本管理、发布标志（`ops.md` 中为 `ships: false`）、分组（general/security） |

`to_agent()` 以目录展开后的工具集实体化一个运行时 `Agent`；`registry.py::PersonaRegistry` 统一两个 persona 家族，并跟踪 installed → enabled → surfaced 生命周期（状态文件）。默认 persona：`cowork`。

### 12.2 内置

Python 构建的（`cowork`、`code`——包装 `agents/cowork.py`、`agents/code.py` 构建器）加上 `personas/builtin/` 中的 markdown persona：`security`、`swe-lead`、`swe-worker`、`devops-lead`、`devsecops-lead`、`triage-lead`、`appsec-worker`、`secrets-worker`、`cloud-posture`、`posture-worker`、`dep-audit`、`logs-worker`、`infra-worker`、`test-worker`、`design-worker`、`change-worker`，以及 `ops.md`（`ships: false`）。安全家族的 persona 以 semgrep 为中心，并在其清单旁携带技能（例如 `semgrep-review`）。

### 12.3 第三方安装与同意

`loading.py` 从本地目录或浅克隆的 git 加载。安装时用户会看到一份 **`consent_summary`**：具体的能力集合——工具、风险类别、连接器、MCP 服务器、团队能力——而不是含糊描述。更新时，若新版本的 `capability_set` **增长**，已存储的同意便不再匹配，需要重新同意（OPE-93：新增连接器的更新必须使集合增长并重新询问）。

### 12.4 技能

技能是 `skills/<name>/SKILL.md` ——YAML frontmatter（name、description）加上模型按需加载的过程性指令。行为（SKILLS-SPEC）：

- **每会话的有效菜单**（§3）：技能列表按会话计算，传给引擎的菜单是一个**实时可调用对象**，每轮重新计算（`agent.py:419`）——类似 roots 列表与实时时钟（负责人裁定 2026-08-20）。
- `load_skill` 在会话中途注入技能的指令；强制运行的技能把结果处理拆分到一个 `_display` 边车，使用户视图与提供商可见的记录安全地不同。
- **`save_skill` 是 worker 创作者的入口**（§5.2）：会话可以提议把已完成的流程安装为可复用技能——而由于这会安装未来行为，它是 `PERSISTENT_AUTHORITY_TOOLS` 成员，始终需要人工审批（见第 3 章（permissions-and-approvals.md））。
- **Persona 携带的技能**（OPE-58）：persona 分发包的 `skills/` 目录会加入加载器，因此 persona 随其过程一起分发。

> 源码：`coworker/personas/signing.py`（minisign 兼容加密）、`coworker/personas/distribution.py`（受信密钥 + 目录）、`coworker/personas/registry.py::install_from_dir`（校验接线）、`coworker/server/app.py`（端点）、`surfaces/gui`（同意徽章）。作者 CLI：`python -m coworker.personas.signing genkey|sign|verify`。

同意流程已经让安装 persona 成为一个审慎的信任事件（`consent_summary`、`capability_set` 增长需重新同意——见 12.3 节）。它唯一无法说明的是**分发包来自谁**。分发 v1 在既有流程上增加 minisign 签名，而没有扩大信任模型：签名是与能力并列展示的一个*事实*，只有目录（catalog）锁定的安装才失效关闭。

### 12.5 签名（作者侧）

`python -m coworker.personas.signing genkey` 生成一个密钥对，其公钥文件与真实 minisign 公钥逐字节一致（base64 `"Ed"` + 32 字节 Ed25519，经由 `cryptography` 依赖——不新增包）。`sign` 向分发包写入两个文件：

    manifest.sha256           "sha256hex  relpath" 行（LF、已排序、POSIX 路径）
    manifest.sha256.minisig   对上述确切字节的 minisign 签名

分发包中的一切内容（manifest `.md` + `skills/**`）都被覆盖；manifest 文件本身被排除（它们**就是**声明）。签名可用真实的 `minisign` CLI 验证；我们的校验器接受真实的 minisign 公钥与签名。一处刻意的分歧：SECRET 密钥容器是我们自己的（`ow1` = scrypt + ChaCha20-Poly1305，而非 libsodium 的 KDF），因此真实的 minisign 私钥无法在此加载——验证侧完全互通，签名侧使用本工具产生的密钥。

### 12.6 校验（安装侧）

`verify_directory` 返回的是事实，并按是否提供了密钥进行分层：

| 条件 | 结果 |
|---|---|
| 无签名文件 | `{signed: false}` |
| 已签名，未提供密钥 | `{signed: true, files_match, key_id, valid: null}`——会检查与已签名 manifest 的完整性；**没有密钥就无法验证由谁签名** |
| 已签名 + 校验密钥 | `valid: true/false`（`pinned` 反映同一结果），篡改 ⇒ `valid: false` |
| 目录（catalog）锁定安装，签名无效 | 在展示同意之前**即拒绝安装**（失效关闭） |

校验之前的 `key_id` 来自签名中 UNTRUSTED 的注释——仅作为展示标签（注释行可被攻击者写入；密码学身份是锁定/受信密钥的匹配）。

### 12.7 信任表面

- **目录（catalog）安装**会锁定条目的密钥：`install_from_git/dir(expected_pubkey=…)` 拒绝无法用它验证通过的分发包。目录条目**就是**信任行为。
- **手动安装**只被标注，从不被拒绝：同意卡片显示 `✓ 已签名`（已验证）、`✕ 签名校验失败`（被篡改/不匹配——请勿启用），或 `已签名 · 密钥未验证`（未提供密钥）。
- **受信密钥**（`TrustedKeyStore`、`trusted-persona-keys.json`）：由用户显式增长的每用户列表；来自受信密钥的验证通过签名会渲染为 `· trusted`。公钥绝不会从 API 回显出去。

### 12.8 社区目录

`config.persona_catalog_url`（用户全局、默认为空——应用绝不会为一个尚不存在的生态预置目录 URL）。一旦设置：`GET /v1/personas/catalog` 会获取并缓存（TTL 1h、陈旧缓存回退、防御性解析——**缺少 slug+source+pubkey 的条目会被丢弃，绝不展示**），`POST /v1/personas/catalog/install {slug}` 以条目的锁定密钥安装，并在验证通过的安装中自动信任该密钥（选择条目即用户的信任行为）。受信密钥管理：`GET/POST/DELETE /v1/personas/trusted-keys`。

### 12.9 v1 不做的事

- 无托管目录（流水线用可注入的 fetcher 测试；URL 是配置项）。
- 不从目录自动更新已安装的 persona（重装 + 能力增长重新同意仍然是更新路径）。
- 无吊销列表：移除受信密钥只会停止*未来*的信任；已安装的 persona 保留其同意状态（那是 manifest 的职责）。

---

## 13. GUI、桌面壳与打包

> 源码：`surfaces/gui/`（React SPA + Tauri 桌面壳）、`stt/`（Rust）、
> `packaging/`、`.github/workflows/`。

### 13.1 React SPA（`surfaces/gui/src`）

React 18 + TypeScript 5.5 + Vite 5 + Tailwind CSS 3。刻意保持朴素：
**没有 router，没有 Redux/Zustand** —— hook 都放在单个大型根组件里
（`App.tsx`，约 2,400 行），由 `api.ts`（约 2,500 行）承载所有后端通信。
值得注意的依赖：`react-markdown`/`remark-gfm`、
`pdfjs-dist`、`xlsx`（产物渲染）。

主要组件（`src/components/` 下约 50 个文件）：`Sidebar`、`Composer`
（语音输入状态）、`Transcript`、`RightRail`、`PersonaView`/
`PersonaHero`、`SettingsView`、`InboxView`、`IntegrationsView`、
`ScheduledView`、`AuditView`、`TeamChatView` + `BoardPanel`、
`ApprovalCard` / `ToolRequestCard`、`UpdateBanner`、`Onboarding`、
`ProviderSetup`。

#### 13.1.1 与 sidecar 通信

- 状态走 REST；一个 `WebSocket /ws/session/{id}` 用于实时回合，一个
  应用级 `/ws/events` 用于推送（协议：
  见第 4 章（server-and-auth.md））。
- **端点解析顺序**：Tauri 注入的运行时全局变量
  （`window.__COWORKER_HTTP__/__COWORKER_WS__`）→ Vite env → 开发默认
  `127.0.0.1:8765`。浏览器开发模式与桌面壳共享同一份代码库。
- **认证**：每次启动生成的 token，作为 `X-OpenWorker-Token` 请求头和
  `openworker` WebSocket 子协议。

### 13.2 Tauri 桌面壳（`surfaces/gui/src-tauri`）

`src-tauri/src/lib.rs`（约 900 行）刻意只是"一个包在既有 React SPA 之上的薄原生窗口"：

1. 选一个空闲的 localhost 端口，把 Python `openworker-server` 作为受管
   sidecar 拉起（开发时用 venv 路径，生产时用 `binaries/sidecar/` 下的
   PyInstaller onedir），退出时杀掉。
2. 在 SPA 加载前把 sidecar URL + 内存中的启动令牌 注入 webview；为
   sidecar 合并用户登录 shell 的环境（OPE-83 ——
   好让 `toolchain.py` 看到用户的 PATH）。
3. **托盘驻留**：关闭窗口只是隐藏；调度器与自我唤醒继续运行；
   托盘 → 退出会停止 sidecar。
4. 原生命令：文件夹选择器、开机自启（open-at-login）、保持唤醒
   （caffeinate）、听写（STT 桥）、更新器。

### 13.3 STT sidecar（`stt/`）

Rust crate `ocw-stt`（`src/lib.rs`，约 670 行）—— 本地、**离线**
的语音听写转文字：

- `cpal` 负责麦克风采集，`whisper-rs`（whisper.cpp 绑定，走 CPU 路径 ——
  Metal 推迟），`ureq` 用于一次性的模型下载。
- 模型：来自 HuggingFace 的 Whisper `ggml-base.en`（约 142 MB），由精确
  字节大小**和** SHA-256 双重锁定，并带 `.verified`/`.ready` 标记文件；
  转写前重采样到 16 kHz。
- 一个可复用的单麦克风会话管理器（后台采集线程写入共享缓冲区；
  `stop_and_transcribe()` 返回文本），以 Tauri 命令
  （`start_dictation`、`stop_dictation`、
  `get_dictation_status`）暴露给 GUI。
- 刻意与壳做依赖隔离：crate 中不含任何 Tauri/UI/剪贴板依赖。

### 13.4 打包（`packaging/`）

- **macOS**（`build_dmg.sh`）：用 PyInstaller 打包服务端（spec：
  `openworker-server.spec`）→ 作为 Tauri 资源放置 + 签名 Mach-O →
  `tauri build` → 通过 `hdiutil` 封装成 DMG → 当设置了
  `APPLE_SIGNING_IDENTITY` 时可选公证 + staple。
- **Windows**（`build_windows.ps1`）：同样的流水线 → NSIS `.exe` + MSI；
  当前未签名（README 中已承认会有 SmartScreen 警告）。
- **自动更新**：`tauri-plugin-updater` 用 **minisign** 对照
  `tauri.conf.json` 中的公钥做签名校验。端点链：
  `download.openworker.com/latest.json` → GitHub Releases 回退。
  `make_update_manifest.py` 按稳定文件名从已放置的产物组装 `latest.json`；
  缺失的平台会被跳过，这样其它 OS 的安装看到的是"无更新"而不是损坏的更新。
  GUI 会预先把更新字节下载到内存（`download_update`），使"重启以更新"即时生效。

### 13.5 CI（`.github/workflows/`）

- **`ci.yml`**（push/PR）：三个并行 job —— `pytest`（Python 3.12，
  `pip install -e ".[messaging,dev,bedrock]"`）、`gui-unit`（tsc + vitest）、
  `gui-e2e`（Playwright chromium，封闭式）。
- **`release.yml`**（tag `v*` / 手动）：3 路矩阵 —— macOS arm64、macOS
  x64（本机构建；PyInstaller 无法交叉编译）、Windows —— 跑相同的本地构建
  脚本，然后两次上传安装包（带版本号 + 供 `releases/latest/download/...`
  深链使用的稳定名），并把组装好的 `latest.json` 附到草稿 GitHub Release。
  发布该 release 才是推送更新的动作。

#### 13.5.1 测试面

- 后端：`tests/` 下 127 个 `test_*.py` 文件，包括进程内的
  **FakeSlack** harness（`coworker/testing/fake_slack/`），它让真实的
  `SlackAdapter`/slack-bolt 栈对着假服务器运行 —— 完整的 Slack 测试，
  零网络。一个 conftest 的 `autouse` fixture 隔离每个测试的 state 目录
  （在一次真实的遥测泄漏事故之后加入，记录在
  `tests/conftest.py`）。
- GUI：13 个 Vitest 单元测试与 `src/` 同目录；`e2e/` 中
  **72 个封闭式 Playwright spec**（`fixtures.ts` mock 掉每个 `/v1` 请求
  并通过 WS 脚本化一个假 agent）外加一个小型 `e2e-live/` 套件对着真实
  服务器跑。

### 13.6 App.tsx 重构 —— hook 抽离（进行中）

> 状态：**大部分已抽离**。App.tsx 通过把自包含的状态 + effect 移到
> `surfaces/gui/src/hooks/`，从 **2,414 → 1,423 行**。行为完全一致
> （只是状态搬移，不是逻辑变更）；`tsc --noEmit` + `check_i18n.py` 是
> 门禁，每个 hook 的 vitest 测试在 `gui-unit` CI job 中运行，普通终端
> 跑完整套件全绿（**42 个文件 / 198 个测试**）。

#### 13.6.1 为什么

`App.tsx` 曾是一个 2,400+ 行的组件，把导航、会话、消息、gate 应答、
看板与启动逻辑混在一起。每次改动都可能碰触无关状态。抽离 hook 让每个簇
可单独单元测试与评审，并把组件收缩回它真正的职责：组装状态 + 渲染。

#### 13.6.2 已抽离的 hook（16 个）

| hook | 负责 | 从 App 中移除 |
|---|---|---|
| `useAppNavigation` | `surface` / `scheduledOpenId` / `personaView` / `settingsTab` | 导航状态 + setter |
| `useSessions` | 会话列表 + 最近项目、5s 轮询、`PERSONAS_CHANGED` 重新拉取、启动恢复辅助函数 | 会话状态 + 轮询 effect |
| `useRail` | rail 可见性、导航折叠/peek、产物预览自动折叠、深链事件 | rail/导航状态 + effect |
| `usePersonas` | persona 元数据加载/刷新 + `personaOf` | persona 状态 + effect |
| `useKeyboardShortcuts` | ⌘B / ⌘, 全局快捷键（基于 ref） | keydown effect |
| `useArtifactCount` | Cowork 会话的顶栏产物文件计数 | 产物拉取 effect |
| `useBoard` | 团队看板状态、看板/团队聊天浮层、`moveBoardItem` | 看板状态 + 拉取/刷新 |
| `useSessionInbox` | 待处理 Inbox + 无人值守模式 + 4s 轮询 + `dropSessionInbox` | inbox 状态 + 轮询 effect + 处理器 |
| `useComposerPrefill` | composer 预填（文本/附件）+ nonce | 预填状态 + 回调 |
| `usePendingGates` | 7 种未决 gate 派生（最新在前） | 7 行内联 `.find` |
| `useGateResponders` | approve/plan/team/items/dir/tool/question 应答流程 | 7 个响应器 |
| `useBoot` | sidecar 健康轮询、恢复上一会话、onboarding 门禁、启动画面揭示 | ~120 行启动逻辑 |
| `useSessionActions` | 打开/恢复/切换 agent、选择工作区、新建项目、重命名/置顶/归档/删除、运行自动化 | ~170 行会话生命周期 |
| `useAutomationToast` | 自动化运行开始 toast（5s）+ 徽标刷新 | ~25 行 toast 状态 + effect |
| `useSessionConnect` | 会话生命周期 + 完整的 WS 事件 switch（对话记录） | ~370 行 connect effect + 事件处理 |
| `useSendGate` | UX-029 发送时文件夹强制（stash/bind/temp/cancel） | ~45 行 gate 状态 + 解析 |

辅助模块：`resolveGates.ts`（7 个 `resolveLastX` 乐观解析辅助函数从
App.tsx 尾部移出）。

#### 13.6.3 模式

- **状态与其 effect 一起搬移**：`useSessions` 把 5s 轮询和
  `PERSONAS_CHANGED` 监听与会话列表状态打包在一起。
- **给 WS 读取方用 ref**：`unattendedRef` 位于 `useSessionInbox`；WS
  处理器通过返回的 ref 读取 `ref.current`。
- **基于 ref 的监听器**：`useKeyboardShortcuts` 通过 ref 读取处理器，
  使 window 监听器只挂载一次。
- **保留收窄后的类型**：`usePendingGates` 返回按 kind 区分的 gate 类型
  （显式类型守卫谓词），因此 JSX 卡片 props 保持精确类型。
- **纯辅助函数抽到模块**：`resolveGates.ts` 可独立单元测试，并由 hook 导入。

#### 13.6.4 验证

- `tsc --noEmit` 干净（所有 hook 文件 + 测试）。
- `scripts/check_i18n.py` 干净（827 个键）。
- 16 个 hook 全部有 vitest 测试（CI `gui-unit`）：**42 个文件 / 198 个测试**。

#### 13.6.5 剩余胶水（少量）

`useSessionConnect` 抽走了对话记录/WebSocket 这一簇。App.tsx 中剩下的
主要是状态所有权 + JSX + 几个小的胶水块（`saveAsProject`、send-gate
恢复、流式跟随滚动 effect）。进一步抽离是可选的，价值较低。

---

## 14. 沙箱（执行与文件）

> 源码：`coworker/tools/sandbox.py`（DockerExecutor + docker_available），
> 在 `coworker/agent.py::build_engine` 中接线（执行器选择），
> `coworker/config.py`（沙箱字段）。

`tools/shell.py` 中的 `Executor` ABC 一直是沙箱化的预留抽象 ——
`DockerExecutor` 把它落地。启用后，shell 命令在 Docker 容器内运行，
**无网络**、**只读根目录**，工作区是唯一可写挂载。自 v2 起，核心文件工具
（read/write/list）也运行在容器内（见 14.6 节）；
权限引擎的 root 作用域仍在其上作为互补的软层。

### 14.1 隔离模型

| 控制项 | 参数 | 效果 |
|---|---|---|
| 网络 | `--network none` | 沙箱内命令无法访问网络 |
| 根文件系统 | `--read-only` | 容器运行时只读；工作区是唯一可写路径 |
| 内存 | `--memory 512m` | 内存上限 |
| CPU | `--cpus 2` | CPU 上限 |
| 清理 | `--rm` | 每条命令后移除容器 |
| 环境 | `GIT_TERMINAL_PROMPT=0` 等 | 强制非交互（与 LocalExecutor 相同） |

### 14.2 逐命令模型

每次 `run()` 调用都是一个 `docker run --rm …` 子进程 —— 没有需要管理的
常驻容器。代价：`cd` 与 `export` 不会跨命令保留。若需要状态，可用 `&&`
串联命令。后台任务（v2）通过 `docker exec` 在长驻容器内运行。

### 14.3 配置

| 字段 | 默认值 | 含义 |
|---|---|---|
| `sandbox_enabled` | `false` | 总开关（用户全局 —— 仓库配置无法启用沙箱） |
| `sandbox_backend` | `"docker"` | 后端选择器（v1：仅 `"docker"`） |
| `sandbox_image` | `"python:3.12-slim"` | 容器镜像 |
| `sandbox_network` | `"none"` | Docker 网络模式 |

### 14.4 回退

如果 `docker` CLI 不在 PATH，或 daemon 不可达（通过 `docker info` 检查），
管理器回退到 `LocalExecutor` 并记录一条警告。shell 工具的结果 dict 携带
`sandboxed: true` 标志，好让 UI 能呈现该命令是否在沙箱内运行。

自 **v2** 起，回退也变为**逐命令**：若 Docker 在引擎构建之后消失
（CLI 被移除、会话中途 daemon 停止），`DockerExecutor.run` 会检测到
（`FileNotFoundError`，或 `docker run` 退出且 stderr 出现
"Cannot connect to the Docker daemon"），并在本地运行该命令，返回
`sandboxed: false` 加上可供 UI 呈现的 `fallback_reason`。

### 14.5 边界

- **文件工具（v2 核心）**：启用沙箱后，`files`/`code_files` 能力切换到
  容器支撑的 read/write/list（`sandbox_files.py`）。
  `apply_patch`/`replace_in_file` 与多 root 挂载也已实现
  （见 14.6 节）。
- **后台任务（v2）**：通过长驻容器 + `docker exec` 支持；输出捕获到工作区文件。
- 前台命令没有常驻容器（每条都得到干净环境）。
- `docker_available()` 在引擎构建时检查，而非逐命令；`DockerExecutor.run`
  中的逐命令探测捕获构建后的故障并本地回退（v2）。

### 14.6 设计：沙箱化文件工具（sandbox v2 最后一项）

> 状态：**已实现**（`read_file` / `write_file` / `list_files` / `replace_in_file` /
> `apply_patch` / `delete_file`，含宿主回退与多 root 挂载）。
>
> 源码：`coworker/tools/sandbox.py`（`DockerExecutor` 文件通道）、
> `coworker/tools/sandbox_files.py`（`sandboxed_file_tools`）、
> `coworker/catalog.py`（`_files` / `_code_files` 在宿主 → 沙箱化工具间切换）、
> 测试在 `tests/test_security_workflow.py`。

#### 14.6.1 为什么

在设计之初，沙箱**只覆盖 shell 命令**。文件工具
（`read_file`、`write_file`、`apply_patch`、`replace_in_file`、`list_files`、…）
直接操作宿主文件系统，仅受权限引擎的路径/root 检查约束。那是一层*软*层：
被攻陷的模型可以读写引擎授予的任何路径（可写 root），而且文件写入既无网络
隔离也无 OS 隔离。

#### 14.6.2 目标

当 `sandbox_enabled` 打开时，文件工具应当与 shell 命令运行**在同一个容器内**：
工作区挂载在 `/workspace`，根文件系统只读，且无网络。工作区挂载之外的读/写
会自然失败（容器文件系统），这是在权限引擎软性 root 作用域之上的*硬*边界。

#### 14.6.3 方案

两个层面需要修改：

1. **执行器边界** —— 给 `DockerExecutor` 加一条文件操作通道：
   - `read_file(path) -> str` → `docker exec <cid> cat /workspace/<rel>`
     （带宿主→容器路径映射）。
   - `write_file(path, content)` → `docker exec <cid> bash -c 'cat > /workspace/<rel>'`，
     内容通过 stdin 管道输入（只读根目录使 `/workspace` 成为唯一可写路径）。
   - `list_files(path)` → `docker exec <cid> find /workspace/<rel> ...`。
   - `apply_patch` / `replace_in_file` → 在容器内运行 `patch`/`python`。
   - 路径映射：只有工作区下的路径可映射；其它一律拒绝（`path escapes the sandbox`）。

2. **工具工厂切换** —— `catalog.py` 的 `_files` / `_code_files` 构造器
   此前返回宿主侧工具。引入一个 `SandboxedFiles` 变体，产出相同的工具名/schema，
   但通过 `DockerExecutor` 文件通道执行。选择规则：若
   `sandbox_enabled && docker_available` → 沙箱化文件工具；否则 → 宿主工具。
   这与 `build_engine` 已经选择 `DockerExecutor` vs `LocalExecutor` 的方式一致。

#### 14.6.4 关键决策与取舍

- **相同的工具面**：工具名、参数 schema 与审批元数据保持完全一致，因此引擎、
  权限与 GUI 无需改动。
- **仅工作区**：只有已挂载的工作区可寻址。工作区之外的 scratch/授予 root 必须
  作为额外 `-v` 挂载（类似 `extra_mounts`），否则沙箱化文件层必须以清晰的错误
  拒绝它们，要求用户把该 root 加为挂载。
- **符号链接 / 路径穿越**：在容器内解析路径；指向 `/workspace` 之外的符号链接
  会被拒绝（与 `SkillStore._folder_of` 相同的守卫）。
- **性能**：每个文件操作都是一次 `docker exec` 往返；对 agent 规模的文件 IO
  可接受，但大读取应被截断（复用 `max_output_chars`）。
- **回退**：如果 Docker 在会话中途消失，文件工具必须大声失败（与 shell 回退
  一致），或以 `sandboxed: false` 回退到宿主工具 —— 后者复用
  `DockerExecutor.run` 的逐命令探测模式。

#### 14.6.5 范围外事项

- `web_fetch`/连接器的出网由权限系统管辖，而非沙箱。
- 沙箱不替代权限引擎 —— 它加固的是那些**已被授予**的路径的边界。

#### 14.6.6 实现状态

1. ✅ `DockerExecutor` 文件通道：`read_workspace_file` / `write_workspace_file`
   / `list_workspace_dir`，带 `_safe_rel` 穿越守卫与 `_docker_exec`
   stdin 支持。
2. ✅ `coworker/tools/sandbox_files.py` 中的 `sandboxed_file_tools` ——
   与宿主兼容的 `read_file`（带窗口、带行号）/ `write_file` / `replace_in_file`
   / `apply_patch`（`coworker/tools/unified_diff.py` 中的最小 unified-diff
   应用器）/ `delete_file` / `list_files`。
3. ✅ `catalog.py` 的 `_files` / `_code_files` 在
   `context.executor` 是 `DockerExecutor` 时切换到沙箱化工具。
4. ✅ 测试：`test_docker_executor_file_channel`（mock docker、穿越守卫）、
   `test_sandboxed_file_tools_roundtrip`、`test_sandboxed_file_tools_host_fallback`，
   以及既有的真实 Docker 测试。
5. ✅ **宿主回退**：当容器在会话中途不可达时，沙箱化文件工具回退到宿主工作区，
   并把结果标记为 `sandboxed: false` + `fallback_reason`（与 shell 探测同样的诚实）。
6. ✅ **多 root 挂载**：会话 root 被注册为额外的容器挂载（通过
   `DockerExecutor.add_mount` 挂到 `/roots/N`），其下的绝对宿主路径被映射进容器
   （`read_container_file` 等）；任何 root 之外的路径都被拒绝。
7. ✅ `apply_patch` —— 一个最小 unified-diff 应用器
   （`coworker/tools/unified_diff.py`）对容器读到的内容打补丁并写回；
   不匹配则大声失败。（未镜像 aisuite 工具包中的 `apply_unified_diff` ——
   沙箱工具覆盖同一原语。）

---

## 15. 出网策略与 OpenShell

> 状态：**已实现（脚手架 + 服务端）**，真实的 OpenShell CLI 参数按站点而异。
> 源码：`coworker/egress.py`、`coworker/tools/egress.py`、
> `coworker/tools/openshell.py`、`coworker/permissions.py`、
> `coworker/server/app.py`、`scripts/security_drill.py`，测试在
> `tests/test_egress.py` / `tests/test_openshell.py`。

### 15.1 出网策略服务

**问题**：在沙箱化/网络受限的运行中，agent 会撞上被阻断的域。用户应能给
特定目标授权一次，之后 agent 即可访问而无需再次询问。

**方案** —— 一个持久化的、用户级允许列表：

- `EgressPolicy`（`coworker/egress.py`）把 `{domain → reason}` 存在
  `<state-dir>/egress-policy.json`。匹配是精确主机或子域
  （`api.example.com` 匹配 `example.com`；`example.com.evil.net` 永不匹配）。
- `request_egress(domain, reason)`（`coworker/tools/egress.py`）是 agent 侧
  工具。它**受审批门控**（`requires_approval=True`）：引擎显示一张 check-in
  卡片，用户的批准就是"允许"这一步。执行时该域被持久化。
- 权限引擎的 `_domain_allowed` 会查询策略存储
  （`permissions.py::_egress_policy_allows`），因此已批准的域无需按会话授权
  即可自动运行（且 AUTO_APPROVE 模式下也不会绕过评审器 ——
  策略是用户级设置）。
- REST：`GET /v1/egress/policy` · `POST /v1/egress/policy` ·
  `DELETE /v1/egress/policy/{domain}`。
- GUI：Settings ▸ Security ▸ **Egress policy** 卡片（列表 / 添加 / 删除）+
  OpenShell 后端状态行。
- 注册为 7 个安全 persona 上的 `egress` catalog 能力，以及
  `CODE_CAPABILITIES` 上（编码 agent 也能请求被阻断的下载）。

### 15.2 OpenShell 沙箱执行器

**问题**：上游 v0.3.1 把 coworker 运行在 NVIDIA OpenShell 内。具体的
OpenShell CLI 是按站点部署的，并且各不相同。

**方案** —— 一个可配置适配器，带有与 Docker 相同的 v2 诚实性：

- `OpenShellExecutor`（`coworker/tools/openshell.py`）实现 `Executor`。
  - `sandbox_openshell_prefix`（config.toml，JSON argv 列表；默认
    `["openshell", "exec", "--"]`）是命令占位符之前的全部内容。
  - 可用性会被探测（`<prefix[0]> --version`）并缓存 30s。
  - 命令以 `<prefix> <command>` 运行，带非交互 env + 硬超时；结果携带
    `sandboxed: true`。
  - 若 CLI 缺失或会话中途消失，回退到 `LocalExecutor` 并标记
    `sandboxed: false` + `fallback_reason`。
- `agent.py::build_engine` 在 `sandbox_backend == "openshell"` 时选择它。
- `GET /v1/settings/openshell/status` 报 `available` / `prefix` / `backend`
  供设置 UI 使用。

### 15.3 容器镜像

- `Dockerfile`（多阶段：在 `python:3.12-slim` 中安装包 + `aisuite`，
  运行 `openworker-server --cwd /workspace`）。
- `.github/workflows/docker.yml` 在 `v*` tag 上构建并推送
  `ghcr.io/andrewyng/openworker:<tag>` + `latest`。

### 15.4 桌面端主机检查（Tauri）

浏览器构建只能看到 sidecar 的间接状态，因此桌面壳暴露两个**只读**探测命令，
由 Settings ▸ Security 的桌面专属卡片展示：

- `openshell_version`（`src-tauri/src/lib.rs`）：执行 `openshell --version`，使用
  用户真实 PATH（`sidecar_env()`，Finder 启动也能找到 Homebrew 安装），返回首个
  非空行；不存在则 `null`。
- `docker_host_networking`：读取 Docker Desktop 设置文件（优先
  `settings-store.json`，回退 `settings.json`；macOS
  `~/Library/Group Containers/group.com.docker/`、Windows `%APPDATA%\Docker\`、
  Linux `~/.docker/desktop/`），返回：

  | 字段 | 含义 |
  |---|---|
  | `docker_cli` | `docker --version` 是否可执行 |
  | `host_networking` | `hostNetworkingEnabled`（兼容 `EnableHostNetworking` / `enableHostNetworking`）；`null` 表示未知，不当作关闭 |
  | `settings_path` | 实际读取的设置文件（不存在为 `null`） |

两个命令都在 `spawn_blocking` 中执行，任何失败都降级为 `null` / `false`。卡片
（`SecuritySection.tsx` 的 `desktop-checks-card`）仅在 `isTauri()` 为真时渲染，
浏览器 GUI 行为不变；`sandbox_network = "host"` 时才需要 host networking。

### 15.5 验证

- `tests/test_egress.py`：策略添加/删除/子域匹配、持久化、工具往返、
  catalog 能力、API、权限引擎集成。
- `tests/test_openshell.py`：prefix 解析、CLI 缺失回退、假 CLI 成功、
  逐命令回退、状态 API。
- `scripts/security_drill.py` 把出网往返作为第 5/6 步演练。
- 后端回归（security/connectors/subagents/skills/marketplaces）：通过。

### 15.6 后续事项（需真实环境）

- 把实际的 OpenShell CLI 参数放进 `sandbox_openshell_prefix`。
- 桌面主机检查已落地（`openshell_version` + `docker_host_networking` + GUI 卡片），
  仍需在装有 Docker Desktop / OpenShell 的机器上实测。
- 在真实的 tag release 上验证 `ghcr.io` 推送。

---

## 16. 检查点

> 源码：`coworker/checkpoints.py`（`SessionCheckpoints`），接线于
> `coworker/engine.py`（回合边界 + 捕获）与
> `coworker/server/manager.py`（归属 + REST）。UI：右侧栏的 Checkpoints
> 分区（`RightRail.tsx`）。

每个修改了受跟踪文件的回合都会留下一个**检查点**：一份记录，保存每个被修改文件的回合前内容，以内容寻址方式存放在 state dir 下。用户可以把会话文件恢复到任何一个已记录检查点的回合结束时的状态——在右侧栏一键操作，就地确认。

### 16.1 捕获

- **接缝**：`engine._execute_sync`——每个工具调用都会经过的唯一漏斗（运行在工作线程中，所以捕获就是普通的同步 I/O）。只有 `risk.WRITE_TOOLS`（`write_file`、`replace_in_file`、`apply_patch`、`apply_unified_diff`）会捕获；目标路径来自 `permissions.write_paths`（patch/diff blob 会解析文件头），经 `provenance.resolve` 解析，并且只接受位于**可写根**内的路径（通过引擎实时的 `permissions.roots` 迟绑定）。
- **首次捕获胜出**：在一个回合内，某个路径最早的回合前内容就是回滚的真相；同一回合内对同一路径的后续写入不会再追加任何东西。
- **成功才记录**：只有当工具调用成功（`note_write`）时，条目才会挂到待定回合上；失败/被中断的调用会丢弃它——抛错的写入在磁盘上没有留下任何可回滚的东西。
- **诚实的缺口**：超过 `MAX_FILE_BYTES`（20 MB）的文件或不可读文件会被记为 `skipped`；回滚会把它们报告为不可恢复，并保持原样不动——绝不悄悄保留或覆盖它无法担保的内容。shell 引起的变化（`sed`、git、构建）在设计上对捕获不可见；UI 会把检查点标注为覆盖文件工具写入。

### 16.2 回合边界

`run()` 紧挨着 `session_facts.begin_turn()` 打开一个待定回合，并在循环外的 `finally` 中把它终结——回合中途崩溃或被杀会留下一个未终结的待定回合，下次加载时丢弃（失败时倾向于更少的历史，与其他存储的防御姿态一致）。没有受跟踪写入的回合不产生记录：检查点 seq 会有空隙，会话中第一个带写入的回合就是最老的回滚点（v1 中没有会话前的基线）。

### 16.3 回滚

`rollback_to(K)` 针对每个在 K **之后首次**被修改的文件，恢复在该次 K 后首次修改时捕获的内容（正是回合 K 结束时的状态），删除 K 之前内容为“不存在”的文件，并把回合列表截断到 K。拒绝条件：有回合正在运行（`manager.is_running`）或存在进行中的捕获（`CheckpointBusy`）；未知 seq 抛 `CheckpointNotFound` → 404 形状的错误 JSON。v1 中对话历史不会被倒回——界面会发一条对话记录通知，让 agent 知道发生了回滚。故意没有 blob GC：内容寻址的 blob 会去重，孤儿 blob 的数量有界，而清扫与进行中捕获竞争需要 v1 没有的锁。

### 16.4 对外接口与呈现

- 存储：`<data-dir>/checkpoints/<session_id>/blobs/<sha[:2]>/<sha>` + `turns.json`（原子 tmp+replace；防御式解析）。绝不放在可写根内——agent 不能触到自己的撤销历史。会话 id 在用作路径名之前会被净化。
- REST：`GET /v1/sessions/{id}/checkpoints`（无需活跃引擎也能工作——读取持久化的列表）与 `POST /v1/sessions/{id}/checkpoints/{seq}/rollback`。
- 右侧栏 UI：一个 **Checkpoints** 分区（仅在有历史时出现），按从新到旧列出回合——`#seq · time · N files [· M skipped]`——带一个两步就地确认的回滚按钮（与两步删除同一套词汇）；成功后关闭 artifact 查看器并刷新 artifacts，状态行报告恢复/删除的数量。所有字符串都走 i18n 管线（`MessageKey` 类型，中文条目在 `i18n/zh/cards.ts`）。
- 测试：`tests/test_checkpoints.py`（捕获/回滚往返、最早捕获胜出、嵌套文件删除、根外拒绝、失败写入丢弃、跳过文件的诚实性、跨重启持久化、busy/unknown 守卫、路径遍历安全）；`e2e/fixtures.ts` 中的封闭式 e2e mock。

---

## 17. 预算守卫

> 源码：`coworker/budget.py`（BudgetStore + BudgetGuard），接线于
> `coworker/engine.py::_loop`（每次迭代顶部检查，每次模型调用后记录）、
> `manager.py`（挂载 + 自动化字段）、
> `automation/tools.py`（面向 agent 的 schema）。

用优雅停止代替让成本失控的 token 上限。三个独立上限，全部**显式选择加入**（None = 无限制）：

| 上限 | 作用域 | 设置方式 |
|---|---|---|
| **daily** | 所有会话，按自然日 | `config.daily_token_budget`（用户全局） |
| **session** | 单个对话，生命周期内 | `config.session_token_budget`（用户全局） |
| **task** | 单个自动化，生命周期内 | `task.budget_tokens`（创建/更新时设置） |

### 17.1 强制执行

检查位于**循环顶部**（与 `max_iterations` 同一模式），所以上一回合的输出已经 yield 给用户，被拒绝的是下一次模型调用。停止表现为一条对话记录通知 + `TURN_END {status: "budget_exhausted"}`——对话已持久化，预算提高或清除后可以继续。

计数基准：提供商处理过的每个 token（`input + output + cache_read + cache_write`），在每次模型往返**之后**记录。提供商未报告用量的回合不计入（与前端用量 chip 相同的诚实规则——绝不猜测）。

### 17.2 绝不破坏回合

所有守卫调用都被包裹：损坏或无法读取的预算存储降级为“无限制”，而不是变成比没有预算更糟的失败模式。这由每个 `record`/`check` 路径上的 `try/except` 保证（有测试）。

### 17.3 对外接口

- Agent：`create_scheduled_task(budget_tokens=…)` / `update_scheduled_task(budget_tokens=…)`。
- GUI：`create_automation`/`update_automation` 接受 `budget_tokens`；公开形状携带它，好让详情页显示上限。
- Manager：当设置了 `budget_tokens` 时，`_run_scheduled_task` 会为任务引擎挂载一个 `BudgetGuard`（并带上来自 config 的 daily 上限）。
- 存储：数据目录下的 `budget.json`（线程安全 JSON 累加）。
- 测试：`tests/test_budget.py`——存储累加、各上限检查、损坏存储降级、引擎停止（守卫触发后提供商从未被调用）。

---

## 18. 压缩质量

> 源码：`coworker/compaction.py`（`_validate_sections`、`_EXPECTED_SECTIONS`、
> 更新后的 `CONTINUATION_CONTRACT`）、`coworker/engine.py::_compact_now`
> （检查点交叉引用）。

上下文压缩的摘要提示词本来就有 8 个结构化小节（主要请求、关键概念、产物、错误、用户消息、待办任务、当前工作、下一步）。三项改进补齐了质量闭环：

### 18.1 分节校验

模型返回后，`summarize_span` 现在会调用 `_validate_sections(summary)`。缺失的小节标题会**以警告形式记录**——绝不抛出，因为部分摘要好过没有摘要。随着时间推移，这个警告模式会暴露系统性的质量问题（例如某个模型总是跳过“错误与修复”），而不是悄悄吞掉它们。

### 18.2 检查点交叉引用

当会话已记录文件检查点时，`_compact_now` 的通知现在会包含最新的检查点 seq：`"Context compacted — earlier turns were summarized. File states are preserved in checkpoints (latest: #3)."`

**原因**：压缩之后，agent 对文件内容的唯一记忆就是摘要文本——而摘要明确说“不要将完整文件内容当作真相”。没有检查点引用时，如果最近的编辑出了问题，agent 无从恢复先前的文件版本。现在它知道检查点系统里有快照。

### 18.3 续接契约更新

`CONTINUATION_CONTRACT`（agent 在压缩后看到的指令）现在会提到检查点：`"File states from earlier turns are preserved in the session's checkpoints — you can recover a prior version via the checkpoint system if a recent edit went wrong."`

### 18.4 测试

`tests/test_compaction_quality.py`——7 个测试：分节校验（全部存在、缺失部分且计数正确、空摘要）、summarize_span 集成（部分与完整）、续接契约提到检查点、引擎 `_compact_now` 检查点交叉引用。

---

## 19. 评审器解耦

> 源码：`coworker/config.py`（`reviewer_model`）、`coworker/agent.py::build_engine`
> （用 `reviewer_model or model` 构造 Reviewer）、`manager.py`（config 传递）。

Auto-Approve 评审器（见第 3 章 §3.5（permissions-and-approvals.md））目前使用与会话**相同的模型**——理由是“如果它值得被信任来驱动 agent，它就足够强到能评审它”。但同模型评审有盲区：评审器与会话模型共享训练数据、偏见和注入易感性。骗过会话模型的提示注入很可能也骗过评审器。

### 19.1 修复

`config.reviewer_model`（用户全局，默认 `None`）：设置后，评审器用这个模型字符串构造，而不是会话模型。同一个 `ProviderRouter` 同时服务两者——它按模型前缀路由，所以不同的提供商家族（例如会话在 `openai/gpt-5.6-sol`，评审器在 `anthropic/claude-4-sonnet`）会自动选择不同的提供商客户端。没有第二个客户端实例，没有第二个 API key，没有提示词改动。

### 19.2 为什么有效

评审器的提示词**本来就独立**于会话提示词：它接收机械提取的操作摘要（工具名、参数、provenance）加上会话的“已知世界”事实——而不是完整对话历史。换模型只改变判断，不改变输入格式。

### 19.3 信任模型

| Config | 行为 |
|---|---|
| `reviewer_model` 未设置（默认） | 评审器 = 会话模型（见第 3 章 §3.5（permissions-and-approvals.md），当前行为） |
| `reviewer_model` = 不同家族 | 去相关评审——评审器的错误独立于会话模型的错误 |
| `reviewer_model` = 同一家族、不同模型 | 较弱的去相关（同一训练数据谱系），但仍然有用 |

该字段是用户全局的（`_GLOBAL_ONLY_FIELDS`）——repo config 无法指定评审器模型，从而保留“repo 不能削弱安全”的不变量。

### 19.4 评测

`reports/` 中有评审器模型对比评测（GPT-5.6-sol、GLM-5.2、Kimi-k3、Muse-Spark——2026 年 8 月）。影子模式（见第 3 章 §3.5（permissions-and-approvals.md）中的影子评测）可以把同模型与不同模型评审器的判定与人工决策做 A/B，而无需改动线上路径。`tests/corpora/` 中的评测语料（benign/dangerous/injection/permission_gate JSONL）提供测试集。

---

## 20. 可观测性

> 源码：`coworker/observability.py`（TraceStore、SessionTracer、to_otlp_json），
> 在 `coworker/engine.py` 中接线（`_execute_sync` 工具 span、`run()` 回合 span、
> `_record_result` 在 TOOL_FINISHED 上记录时长），`manager.py`（挂载 + 导出），
> `surfaces/gui`（每步时长展示）。

### 20.1 span 时间线与 OTLP 导出

每次工具执行都会变成一个 **span** —— 名称、会话、开始/结束时间、状态、时长 —— 持久化在本地专用的 SQLite 存储中。它有两个消费者：

1. **Transcript 工具卡片**：TOOL_FINISHED 事件现在携带 `duration_ms`；StepRow 把它渲染成一个低调的标签（`120 ms`、`3.4 s`）—— 用户不需要打开任何东西就能看到每一步花了多久。
2. **`GET /v1/sessions/{id}/timeline`**：原始 span 列表，供工具或未来的时间线 UI 使用。

**OTLP 导出是双重选择加入（double opt-in）**：必须设置 `config.otlp_endpoint`，并且必须调用 `POST /v1/observability/export {session_id}`。除非用户既配置了端点又显式导出，否则任何数据都不会离开本机。导出形态是 OTLP/HTTP-JSON（标准 `resourceSpans` 结构）—— 与任何 OTLP collector 兼容。

### 20.2 设计决策

- **存储与审计日志分离**：审计回答“agent 做了什么”（脱敏后的参数、OPE-101 保留策略）；链路追踪回答“花了多久、代价是什么”。不同的问题，不同的保留策略，不同的导出。
- **span 中不含文件内容或工具结果** —— 只有名称和元数据，因此即使本地存储被读取也不会泄露内容。
- **回合 span** 包裹每次 `run()`（在 begin 块中开始，在 finally 中与检查点一同结束）；**工具 span** 包裹每次 `_execute_sync` 调用。LLM 往返 span 推迟到 v2：它需要在 `_astream` 内部有一个目前尚不存在的干净接缝。
- **永不打断回合**：所有 tracer 调用都有 `try/except` 包裹，与检查点相同的姿态。

---

## 21. 评测与回放

> 源码：`coworker/trajectory.py`（账本）、`coworker/server/manager_sessions.py`
> （分叉 / 回放 / 轨迹 diff）、`coworker/personas/harness.py`（harness
> 注册表）、`coworker/eval/matrix.py`（模型 × harness 矩阵）、
> `coworker/eval/capture.py`（记录 → 用例导出）、`evals/ci-case/`
> （黄金用例）、`.github/workflows/eval.yml`（CI 门禁）。

可观测性栈回答“每一步花了多久”（span）。本文档覆盖另一半：**到底往模型发了什么、模型要求执行什么，以及如何回放和比较** —— 这是对模型、harness 和 prompt 做离线评测与回归测试的基础。

五个组成部分，按依赖顺序排列：

1. **回合轨迹账本** —— 仅追加、脱敏、按会话划分的事件日志。
2. **分叉与回放** —— 在任意回合边界切开会话；重跑输入。
3. **harness 注册表** —— “mode = 组合”的唯一事实来源。
4. **评测矩阵** —— 在模型 × harness 上重跑一个已记录的会话。
5. **eval 采集** —— 一条命令把真实会话导出为矩阵用例。

### 21.1 回合轨迹账本（`coworker/trajectory.py`）

#### 21.1.1 为什么

span 存储（`observability.py`）只记录工具耗时。系统提示词、上下文注入、上下文压缩边界、审批决定，以及真正发往模型的出站请求都没有留下任何痕迹 —— 因此无法对同一会话的两次运行做 diff，甚至无法回答“第 3 回合我们给模型发了什么”。

#### 21.1.2 设计原则

- **只存元数据和摘要，绝不存内容。** 出站请求、工具参数和工具结果都以 `sha256` 摘要加上字节/字符计数和参数 *键名* 的形式记录 —— 绝不记录消息体、命令文本或文件内容。账本可以验证一次回放，而无需复制整个对话，也不会制造新的数据外泄面。
- **在写入层脱敏。** `redact()` 递归替换看起来像密钥的键（`token`、`secret`、`password`、`cookie`、`api_key`、`private_key`、…）以及已知的密钥前缀（`Bearer`、`sk-`、`ghp_`、`xox[abprs]-`、`AKIA`、`AIza`、PEM 头）；字符串截断到 1200 字符。
- **账本绝不能打断回合。** `SessionTrajectory.record()` 吞掉所有异常；引擎把每次写入都包在 `_traj()` 辅助函数中，契约相同。
- **默认开启，本地优先。** 文件位于 `<data-dir>/trajectory/<session>.jsonl`，与 `traces.db` 并列 —— 在工作区之外，因此 agent 无法改写自己的账本。两个配置键（`trajectory_enabled`、`trajectory_max_records = 5000`）都**仅限用户全局配置**：仓库级配置无法禁用账本。
- **按条数保留。** 超过 `trajectory_max_records` 后，文件被原子重写（`<file>.tmp` + `os.replace`），保留最新的 N 条。
- **回合边界只覆盖 `run()`。** `retry()`/`resume()` 复用当前回合号，而不新增边界事件。

#### 21.1.3 记录模型

每行一个 JSON 对象（`{"v":1,"seq":…,"ts":…,"session_id":…,"turn":…,"kind":…,"data":{…}}`）：

| kind | data |
|---|---|
| `turn_start` | `mode`、`input_chars`、`input_digest`、`has_source`、`has_display`、**`message_count`** |
| `turn_end` | `status` |
| `model_request` | `model`、`messages`（数量）、`bytes`、`digest`、`roles`、`tools`（名称）、`settings`（键名） |
| `context_injection` | `chars`、`digest` |
| `tool_call` | `name`、`call_id`、`argument_keys`、`arguments_digest`、`arguments_chars` |
| `tool_result` | `name`、`call_id`、`status`、`duration_ms`、`result_digest`、`result_chars`、`reason` |
| `approval_requested` / `approval_resolved` | `name`、`call_id`、`reason` / `status`、`outcome` |
| `notice` | `kind`、`text`、标量字段 |
| `code_exec` | code-mode 执行：code + `sandboxed` + bridge 调用次数 |

引擎中的写入点：`run()`（回合边界）、`_astream()`（在 `produce()` 之前写入 `model_request`）、`_outbound_messages()`（`context_injection`）、`_execute_sync()`（`tool_call` / `tool_result`，包括 interrupted/mangled/denied/unknown 等结果）、`_authorize()`（审批请求/决定）、`_append_notice()`（错误、重试、上下文压缩、预算通知）。

#### 21.1.4 读取 API

`GET /v1/sessions/{id}/trajectory?limit=500&since_seq=0` →
`{session_id, count, records}`。只读、无副作用；即使 `trajectory_enabled=false`，历史文件仍可读取。

`turn_start.message_count` 是在用户消息追加*之后*记录的，因此 `message_count - 1` 就是该回合用户消息的索引 —— 这是分叉、矩阵和采集共同使用的权威的“回合 → 消息索引”映射。

### 21.2 分叉与回放（`coworker/server/manager_sessions.py`）

#### 21.2.1 分叉语义：“在第 N 回合之前切断”

`POST /v1/sessions/{id}/fork`，可选 `{"turn": N}`：

- 带 `turn`：新会话包含第 N 回合*之前*的所有消息（切点**只从账本**解析 —— steering/注入的消息同样是 `role=user`，因此按消息计数不可靠）。账本缺失或回合未知 → 400。
- 不带 `turn`：尖端分叉（tip fork），复制所有内容。

分叉仅复制**会话状态，不复制工作区/文件状态**：两个分支共享工作区目录，源会话中后续的文件编辑不会被回滚（工作区回滚是检查点 API 的职责 —— 二者正交）。轨迹和检查点不会被复制；分叉从第 1 回合开始为自己的账本编号。

继承规则：

| 字段 | 规则 |
|---|---|
| workspace / model / mode / agent / extra_roots / grants / bindings | 继承（grants = 同一谱系中同一用户的意图） |
| team | **不**继承 —— 分叉是一个普通会话 |
| compaction | 仅尖端分叉（中途切断会使锚点失效） |
| title | 源标题截断 + `" (fork)"`（≤80 字符） |
| lineage | `SessionRecord.fork = {"from_session", "from_turn", "cut_messages"}`，持久化在 sessions 表中 |

#### 21.2.2 回放

`POST /v1/sessions/{id}/replay`，可选 `{"input": "…"}` → 202：

- 不带 `input`：在分叉的切点处重放已记录的用户消息（`input_source: "lineage"`）；无谱系 → 400。
- 带 `input`：使用给定文本（`input_source: "override"`）。
- 执行复用后台投递通道（`deliver_to_session`），因此审批会走正常的 Inbox/Unattended 流程 —— 回放从不绕过权限。目标会话正在运行 → 409。

#### 21.2.3 轨迹 diff

`GET /v1/sessions/{id}/trajectory/diff?other={sid}&from_turn=1&other_from_turn=1` 按顺序对齐双方的 `model_request` 记录并比较摘要，逐对返回 `{turn_left, turn_right, digest_match, bytes_left, bytes_right, model_left, model_right}` 以及 `matched`/`compared` 总数。分叉两侧存在天然的回合偏移（分叉从 1 重新编号），因此调用方要提供两个起始回合；分叉响应中会包含一个带建议参数的 `diff_hint` URL。

#### 21.2.4 fail-closed 规则

内部（`__` 前缀）会话 → 403；对正在运行的源会话分叉 → 409；分叉对源会话是只读的；不新增配置开关 —— 两个操作都是用户显式动作。

### 21.3 harness 注册表（`coworker/personas/harness.py`）

**问题**：运行时预设原本就是 persona manifest，但评测矩阵保留了自己硬编码的 `HarnessSpec`，其中的 prompt 文本重复且已经与 manifest 漂移，并且用户安装的 persona 无法作为 harness 被评测。

**修复**：harness（能力槽 + 系统提示词 + 权限模式 + 文件夹门禁）从**唯一来源**派生 —— persona manifest；只有 `code` 没有 manifest，使用代码内的 `CODE_CAPABILITIES / CODE_INSTRUCTIONS`。

```python
@dataclass(frozen=True)
class HarnessSpec:
    name: str
    capabilities: tuple[str, ...]
    system_prompt: str
    mode: str = "interactive"
    requires_folder: bool = True
    source: str = "manifest"        # "manifest" | "builtin"
```

- `builtin_specs()` —— minimal / creator / code-mode 从 `personas/builtin/<id>/manifest.md` 解析，`code` 来自代码常量。
- `installed_specs()` —— builtin ∪ 所有已安装的 manifest persona；注册表构造失败永不阻塞内置项（回退到 `builtin_specs()`）。
- `resolve(name)` —— 抛出 `ValueError("unknown harness: …")`。
- `eval/matrix.py` 从 `builtin_specs()` 派生 `HARNESSES`；`run_matrix()` 默认使用 `installed_specs()`，因此任何已安装的 persona id 都是合法的 `--harnesses` 条目。报告携带 `harness_sources`。

已记录的后果：`minimal`/`creator`/`code-mode` 的 prompt 现在来自 manifest 正文，`code` 使用 `CODE_CAPABILITIES`（包含 `egress`）—— 与注册表引入前的报告不可按字节比较。这是刻意的：评测画像必须跟随运行时画像。

### 21.4 评测矩阵（`coworker/eval/matrix.py`）

在固定的 (harness, model) 单元下重跑会话中每一个已记录的用户回合，并把账本的 `model_request` 概要收集成一份可比较的报告。

- **记录侧**：`recorded_turns(session_id)` 通过 `turn_start.message_count` 把回合映射到消息索引（与分叉使用同一映射），并从 `conversations/<sid>.jsonl` 读取原始用户文本 —— 未脱敏，因此输入是完整的。
- **执行侧**：每个单元获得一个全新引擎（`build_engine(agent=harness_agent(spec), model=…, mode=…, provider=…)`）以及一个临时 `SessionTrajectory` —— 单元之间从不互相污染，也不污染真实账本。
- **默认无审批**：除非传入 `--auto-approve`，否则引擎拒绝工具调用。
- **`--stub`**：一个确定性的假提供商（无网络、无密钥），以便 pytest 覆盖脚手架；真实运行通过 `providers.router` 并显式指定 `--models`。
- **报告**：每个单元一个 JSON 对象，包含 `status`、逐回合的 `digest`/`bytes`/`tools`，外加迭代次数/时长/错误。摘要相同意味着模型在该 harness 下产生了逐字节相同的出站载荷。

### 21.5 eval 采集（`coworker/eval/capture.py`）

闭合从真实会话到评测资产的回路。此前，构造矩阵输入意味着手工从两个 JSONL 文件中拼装消息和回合边界。

`python -m coworker.eval.capture <session-id> --out <dir>` 导出：

- `messages.jsonl` —— 每回合一行：`{"turn": n, "input": <raw text>}`（边界通过 `recorded_turns()` 获取）。
- `baseline.json` —— `{session_id, captured_at, turns: [{turn, model, bytes, requests, tool_calls, tools}]}`，其中 `model`/`bytes`/`requests` 来自 `model_request` 概要，`tools` 是该回合的 `tool_call` 名称序列。

**隐私门禁**：在写入任何内容之前，采集会扫描账本中的脱敏标记 `[redacted]` —— 命中意味着该会话接触过密钥，导出被拒绝（`--force` 可覆盖；CLI 以退出码 2 退出并在 stderr 给出提示）。消息体本身*不*脱敏（评测需要原始输入），因此这道门禁守护的是“接触过密钥”这一事实，而不是内容。

`matrix.py --case <dir>` 消费该包（与 `--session` 互斥），并且每个报告单元都会增加一个 `vs_baseline` 块：逐回合 `{bytes, base_bytes, bytes_delta, tools, base_tools, tools_match}`。

**默认仅作参考，按需门禁。** 基线从不会让一次运行失败，除非调用方要求：`--check-baseline` 把比较变成断言，用四类失败逐回合判定 —— `cell_error`（执行失败）、`missing_turn`（基线中没有该回合）、`tool_drift`（实际 `tool_call` 名称序列不同）、`bytes_drift`（出站字节变化超过 `--max-bytes-ratio`，默认 10%；零字节基线将任何字节都视为漂移）。结论写入 `report["baseline_check"]`，出现漂移时 CLI 以退出码 **3** 退出（区别于单元失败的 **1**），因此 CI 可以据此门禁，同时不丢失运行失败的信号。

### 21.6 CI 接线（`.github/workflows/eval.yml`）

门禁现在在 CI 中以两条 lane 运行：

- **`gate-stub`**（push / PR）：用 `--stub --check-baseline` 运行已提交的黄金用例 `evals/ci-case/`，并把报告作为 artifact 上传。漂移（退出码 3）会使 job 失败；不需要密钥、无网络、零成本。
- **`matrix-real`**（仅 `workflow_dispatch`）：参数化用例目录、模型、harness、字节比例与门禁开关；从仓库 secret 读取 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 并上传报告。手工触发是因为它花钱且需要密钥；它从不阻塞 PR。

**确定性黄金用例。** 出站载荷内嵌了工作区绝对路径、git 分支/状态/近期提交、每回合的 `Now:` 时钟，以及用户级技能/记忆 —— 因此工作流（以及任何重新生成运行）固定使用**空的非 git 工作区 + 空的 `COWORKER_STATE_DIR`**。在该固定条件下，唯一的跨机器差异是路径长度和时区名称（远低于 10% 比例），并且 stub 单元从不发出 `tool_call`，因此 `tool_drift` 在那里平凡满足；工具序列检查真正生效的地方是真实模型 lane。

**刷新基线。** 在有意修改 prompt、harness 规范或工具目录后，用 `matrix --write-baseline` 重写黄金基线（要求 `--case` + `--stub`、恰好一个单元，并与 `--check-baseline` 互斥）：

```bash
COWORKER_STATE_DIR=/tmp/eval-ci-state \
  python -m coworker.eval.matrix --case evals/ci-case --stub \
  --workspace /tmp/eval-ci-ws --write-baseline
```

把重新生成的 `baseline.json` 与造成漂移的改动一起提交，这样 diff 能同时显示改动的两端（改前/改后）。

### 21.7 非目标与后续工作

- 以上任何功能都没有 GUI（分支树、diff 查看器、审计面板）。
- 分叉中没有工作区快照/恢复（使用检查点 API）。
- 没有跨 harness 的模型选择，也没有历史报告归档。
- 批量回放以及针对 code-mode 的回放特化尚未实现。

---

## 22. Harness v2 与引擎优化

> 源码：`coworker/engine.py` —— `_loop` 中的增量批处理，`_handle_tool_calls`/`_plan_execution_waves` 中的执行波次，以及提示缓存文档。

### 22.1 增量批处理（流式效率）

**问题**：每一个流式 delta 都会产出一个事件。一段 2000 token 的输出会产生约 500+ 个事件，每个事件都会触发 IPC 序列化和 GUI 重渲染。

**修复**：delta 被缓冲在 `text_buffer` / `reasoning_buffer` 中，并按时间+大小的组合窗口刷新（50ms 或 100 个字符，以先到者为准）。流结束时执行最终刷新。错误/中断路径也会刷新，因此部分输出永不丢失。

**实现**：`_loop` 的流式段——`_should_flush()` 检查两个阈值，`_flush_deltas()` 产出合并后的事件。`streamed`/`streamed_reasoning` 累加器不受影响（它们始终为历史记录接收每一个 delta）。

**效果**：一次典型响应的事件量从约 500 降到约 20。GUI 收到的最终文本相同（由同一个 `streamed` 列表拼接而成），只是 delta 更少、更大。

### 22.2 执行波次（工具依赖 DAG）

**问题**：`_parallel_safe` 使用二分拆分——低风险工具并发运行，其余一切串行运行。三个相互独立的、写入不同文件的写入操作会被不必要地串行化。

**修复**：`_plan_execution_waves(cleared)` 用基于文件路径冲突分析的**贪心波次分配**替代二分拆分：

1. 提取每次调用的写入路径和读取路径（通过 `write_paths` + `resolve`）
2. 一次调用与某个波次**冲突**的条件是：它的写入路径与该波次的读路径或写路径重叠，或者它的读路径与该波次的写路径重叠
3. 每次调用被分配到第一个无冲突的波次（贪心、保持顺序——模型在该波次内的调用顺序被尊重）
4. 无可提取路径的调用（shell 命令、gate 工具）回退到 `_parallel_safe()` 元数据：低风险工具共享一个波次，其他工具各自获得独立的串行波次

**结果**：对 `a.md` 的写入与对 `b.md` 的写入现在并行运行（同一波次）。对 `out.md` 的写入后跟对 `out.md` 的读取则被串行化（不同波次）。只读批次行为与此前一致。

**约束**：波次分配保持顺序（波次内模型的调用顺序得以维持）。对同一文件的读取不冲突（两者可以并行读取）。只有写-读和写-写冲突才强制分离。

### 22.3 提示缓存审计

**发现**：现有的 `_outbound_messages()` 架构本身已对缓存友好——上下文提供者（时钟、目录列表、技能菜单）只注入**最后一条用户消息**，因此消息前缀（system + 所有此前回合 + 工具结果）在多次调用之间字节稳定。

**跨回合注意事项**：上下文块会移动到每条新的用户消息上，因此上一条用户消息在两次调用之间失去其上下文块——在该位置造成缓存未命中。这是实时时钟/环境模式的已知权衡（负责人裁定 2026-08-20：过时的时间戳导致了糟糕的推理）。修复它需要按消息持久化注入的上下文，而这与“永不改动 self.messages”的不变量冲突。

**验证**：`test_prompt_cache_within_turn_stable`（相同消息 → 相同出站）与 `test_prompt_cache_cross_turn_prefix_stable`（system 消息跨回合字节稳定，上下文块只在最后一条用户消息上）。

### 22.4 测试（v2）

`tests/test_harness_v2.py` —— 6 个测试：

- 波次规划：低风险并行（无路径）、高风险串行、同一文件的写+读被串行化、对不同文件的写入并行
- 提示缓存：回合内稳定性、跨回合 system 消息稳定性

**引擎级优化（v1）**

> 源码：`coworker/engine.py` —— `_is_transient_provider_error`、`_truncate_tool_result`、`_EGRESS_RATE_LIMITS`，以及它们在 `_loop` 和 `_handle_tool_calls` 中的接线。

三项引擎级优化，在不改变工具接口的前提下提升可靠性、上下文效率与安全性。

### 22.5 提供商瞬时错误自动重试

**问题**：一个 50 步的 agent 任务在第 48 步因 429 速率限制而失败——用户必须手动重试整个流程。

**修复**：当提供商抛出异常且尚未流式输出任何内容时（错误发生在第一个 token 之前），引擎用瞬时模式（429、502、503、rate limit、connection reset、overloaded）检查该异常，并以指数退避重试（2s → 4s → 8s，最多 3 次重试）。

**护栏**：

- 仅在 `not streamed and not streamed_reasoning` 时重试——在部分输出之后重试会造成 delta 重复。
- 认证错误（401/403）、上下文溢出和模型错误不是瞬时的。
- 重试计数器在流成功时重置——新的错误获得全新的重试预算。
- 每次重试都追加一条对话记录提示（`"Transient error — retrying (1/3)…"`），让用户知道发生了什么。

**代码**：`_is_transient_provider_error()` + `_TRANSIENT_PATTERNS` + `_loop` 的 except 块中的重试逻辑。

### 22.6 工具结果语义截断

**问题**：`_tool_result_message` 执行 `json.dumps(result, default=str)` 且不做截断——一个 2 MB 的 `read_file` 结果原样进入对话历史，消耗上下文窗口并抬高此后每个回合的 prompt 成本。

**修复**：`_truncate_tool_result(tool_name, content)` 在内容超过 `_MAX_TOOL_RESULT_CHARS`（16,000 字符 ≈ 4k token）时执行头+尾截断：保留前 60% + 后 20%，中间放一个 `[.. N chars truncated — re-read with a narrower query if needed ..]` 标记。

**为什么是头+尾**：文件开头承载 import 与头部信息；文件结尾承载结论与摘要。如果被省略的中段确实重要，模型可以通过带窗口的工具（`read_file(start_line=…)`）重读特定部分。

**工具无关**：同样的截断应用于所有工具——`read_file`、`browser_read_page`、`grep`，任何返回大字符串的工具。阈值基于字符（而非基于 token）是为了速度；16k 字符 ≈ 4k token 的比例对于粗粒度过滤已足够接近。

### 22.7 EGRESS 工具按回合速率限制

**问题**：`max_iterations`（150）是唯一的调用计数限制。处于 Auto-Approve 模式的 agent 可以在单个回合内发送 50 条消息或发起 50 次网页抓取，而不受任何频率检查。

**修复**：对 EGRESS 分类的工具施加按工具、按回合的速率限制：

| 工具 | 每回合上限 |
|---|---|
| `send_message` | 10 |
| `web_fetch` | 20 |
| `web_search` | 15 |
| `browser_open_url` | 20 |

达到上限时，工具会在授权之前被拒绝（不会为一次将被限流的调用显示审批卡片）。工具结果告知 agent：`"rate limited: send_message has been called 10 times this turn (limit 10). Try a different approach or summarize what you have so far."`

**计数**：所有已授权的调用都计数（包括在执行阶段失败的调用——消耗速率预算的是调用的意图）。计数器按用户回合重置（`_loop` 入口）。

**读工具不受速率限制**——读取成本低，且 agent 为完成任务可能需要很多次。限制针对的是 EGRESS 操作，每次调用都有真实世界的后果。

### 22.8 测试（v1）

`tests/test_harness_optimizations.py` —— 11 个测试：

- 截断：短内容不变、长内容头+尾、结构保留、跨工具一致性
- 瞬时检测：正向模式（429/503/rate limit/connection）与负向模式（401/context window/invalid model）
- 引擎重试：429 → 重试成功（提供商被调用两次，对话记录中有重试提示）；认证错误 → 不重试
- 速率限制：默认配置合理性、阈值阻断

---

## 23. 浏览器录制

> 源码：`coworker/connectors/browser_recording.py`（录制缓冲区 + 持久化）、`browser_automation.py`（录制拦截 + 工具）。

agent 的浏览器工具调用可以被**录制**为确定性的回放脚本，并**保存**为可复用的动作序列。回放通过同一个 Playwright 浏览器会话重新执行录制到的动作；某一步失败会带着步号停止回放，以便 agent 回退到模型驱动的修复（自愈）。

### 23.1 工作机制

1. `browser_record_start(name)` —— 打开一个录制缓冲区。
2. 在录制激活期间，每个**改变状态**的浏览器工具调用（open_url、click、type、select、upload、wait）在成功时被追加为 `{tool, args}`。读工具（read_page、screenshot）被排除——它们不改变状态，在回放时会是空操作。
3. `browser_record_stop()` —— 关闭缓冲区并返回动作列表。
4. `browser_record_save(name, title)` —— 持久化到 `<data-dir>/browser-replays/<name>.json`（受审批管控：会安装未来行为）。
5. `browser_replay(name)` —— 加载已保存的录制，并通过相同的浏览器工具闭包重新执行每个动作。首次失败即停止回放，返回 `{ok: false, failed_step, hint}`（受审批管控：回放会导航和点击）。

### 23.2 v1 边界

- 录制的是 agent 自身的浏览器工具调用——不是人类交互（那需要 CDP 注入）。agent 先把流程摸清一次；用户随后可以确定性地回放它。
- 使用精确选择器的确定性回放——v1 不做模糊重定位。失败的步骤干净地停止，以便 agent 通过常规工具修复。
- 录制是会话本地的（浏览器会话在每个 server 中是单例）；已保存的录制持久化到磁盘，能跨重启存活。

---

## 24. 决策日志

> 摘自代码中带日期的决策注释（`OPE-###` 规范条款引用、`owner ruling YYYY-MM-DD`、`*-SPEC §n`）。这是一份关于*决定了什么以及为什么*的索引，指向裁定所在的源文件。规范文件本身（OPE-*、MEMORY-SPEC、SKILLS-SPEC、github-relay-spec）不在本仓库中；注释引用了它们。

### 24.1 OPE 决策

| ID | 领域 | 决策 | 来源 |
|---|---|---|---|
| OPE-27 | 引擎 | 长历史的自动压缩：在工具回合之间设置检查点、边界摘要、防御性解析的状态 | `coworker/compaction.py`, `engine.py` |
| OPE-51 | 交互 | 分组问题——一行按钮无法回答 2 个及以上问题；卡片最多承载 4 个分组条目；旧的持久化字符串条目仍然有效 | `coworker/inbox.py:83-90`, `engine.py:1792`, `interactions.py:53` |
| OPE-58 | Persona | Persona 包自带 `skills/` 目录 | `coworker/agent.py:247` |
| OPE-82 | 工具链 | PATH 的*解析*与工具的*安装*是两个被刻意分开的问题 | `coworker/toolchain.py:3-7` |
| OPE-83 | 桌面 | 桌面外壳在 spawn 时把登录 shell 的 PATH/env 交给 sidecar | `coworker/toolchain.py:7`, `src-tauri/src/lib.rs` |
| OPE-85 | 供应链 | `request_tool` 仅从固定的、经摘要校验的目录安装——获取可执行文件属于供应链决策，绝不自动运行；缺失的扫描器必须*询问*，而不是静默丢弃需要它的检查 | `coworker/toolchain.py:18,207`, `agent.py:303` |
| OPE-93 | Persona | 连接器能力是一个具体集合，不是赤裸的“使用连接器”位；一次*新增*连接器的更新必须扩充 `capability_set` 并重新征得同意；白名单内的 persona 只能拿到这些连接器（最小权限） | `coworker/personas/loading.py:30,57`, `agents/base.py:40`, `agent.py:308` |
| OPE-100 | 团队 | board 是开放接口：`ocw` CLI + `ocw board mcp` stdio server 供外部 harness 使用 | `pyproject.toml` 脚本注释, `teams/cli.py` |
| OPE-101 | 审计 | 审计成本缺陷类别：不要按会话长度缩放保留的审计细节 | `coworker/audit.py:66` |
| OPE-111 | 风险 | `browser_open_url` 是“换名字的”EGRESS；只读模式必须一步可达 | `coworker/risk.py:33,80` |
| OPE-114 | 权限 | **来源底线**：运行 agent 本会话*下载*的东西会强制弹出人工卡片——评审器永远看不到这些卡片，命令白名单也看不到 | `coworker/engine.py:215,1094,1234` |
| OPE-117 | 权限 | 延迟执行的写入（之后会自行运行的东西——`.git/hooks`、计划任务）仅限人工；即使在 Discuss/Plan 模式下，需要时仍然显示卡片 | `coworker/permissions.py:74`, `risk.py:41` |
| OPE-122 | 浏览器 | 本地文件上传只在已授予的根目录内解析 | `coworker/connectors/browser_automation.py:352` |
| OPE-124 | 浏览器 | 重定向守卫：真正加载的那一跳会被重新检查，而不仅是初始 URL | `coworker/connectors/browser_automation.py:333` |

### 24.2 规范条款裁定

| 规范 | 裁定 | 来源 |
|---|---|---|
| github-relay-spec §2.3, §4, §8 | 面向 GitHub 入站的云端中继；中继代码永不接触密钥存储；GitHub 作为继 Slack 之后的第二个中继提供商 | `coworker/connectors/relay_client.py:69`, `cloud.py:522`, `descriptors.py:615` |
| github-relay-spec §7 | 重连时，错过的消息通过 channel-context / nudge 恢复 | `relay_client.py:371` |
| spec §1.5 (2026-08-12) | 权限模式改名 `"auto"` → `"bypass-approvals"`——名字必须表明“安全已关闭”，且不能与 `auto-approve` 混淆 | `coworker/permissions.py:219` |
| spec §2.2 | `send_message` 按 EGRESS 管控：模型自选的自由文本是同样的出站通道 | `coworker/risk.py:32` |
| spec §8.4 / §8.6 | 评审器连续计数语义；每次审批请求一个动作 | `coworker/engine.py:29,719` |
| MEMORY-SPEC §3 | `session` 记忆作用域已废弃——永不向它保存 | `coworker/memory/tools.py:77` |
| MEMORY-SPEC §4.2 | 只要有存储接入就注入“何时该记住”规则 | `coworker/agent.py:69` |
| MEMORY-SPEC §5.1 | `on_saved` 钩子渲染保存提示 + Undo | `coworker/agent.py:207` |
| MEMORY-SPEC §6 | 用户规则优先于学习到的记忆；agent 永远不能写入它们 | `coworker/agent.py:210`, `memory/settings.py` |
| MEMORY-SPEC §7 | 超过 8k 字符的索引模式：最新 10 条全文 + 摘要 + `memory_read` | `coworker/memory/tools.py:6` |
| MEMORY-SPEC §7.1 | agent 已知的内容在会话开始时即固定 | `coworker/agent.py:404` |
| SKILLS-SPEC §3 | 每会话有效的技能菜单 | `coworker/agent.py:419` |
| SKILLS-SPEC §4.1 | 实时技能菜单每回合重算；强制运行结果通过 `_display` 拆分 | `coworker/agent.py:501`, `engine.py:288` |
| SKILLS-SPEC §5.2 | `save_skill` 是 worker 作者入口（人工批准） | `coworker/agent.py:426` |

### 24.3 负责人裁定

| 日期 | 裁定 | 来源 |
|---|---|---|
| 2026-08-20 | 实时时钟**每回合**注入（过时的时间戳导致了糟糕的推理） | `coworker/agent.py:482` |
| 2026-08-20 | 处处一个读取器：全栈共享同一个带窗口、带行号的 `read_file` | `coworker/catalog.py:76` |
| 2026-08-24 | 评审器拒绝断路器：2 → **连续 5 次** + 感知累计的连续语义（朴素的 2 次计数会静默惩罚合法的来回往复） | `coworker/engine.py:29` |
| 2026-08-24 | `updated_at` 表示“最后一次处理”，绝不是“最后一次保存” | `coworker/conversations.py:173` |
| 2026-08-24 | `_display` sidecar 在 reload 后存活，但永远对提供商不可见 | `coworker/engine.py:181` |
| 2026-08-24 | 某个问题的已捕获答案恰好算作该问题作用域的证据 | `coworker/engine.py:1846` |
| 2026-08-24 | 评审器：agent 的被捕获问题与回复一起显示 | `coworker/reviewer.py:239` |

### 24.4 事件驱动的决策

| 日期 | 事件 → 决策 | 来源 |
|---|---|---|
| 2026-07-28 | 某模型**伪造了记忆保存**（断言“我会记住”却没有调用工具）→ MEMORY-SPEC 反虚报提示 + 始终可见的保存 toast | `coworker/agent.py`, `memory/` |
| （未标日期） | 测试运行期间的真实遥测泄漏 → 每个测试中的 `autouse` 状态目录隔离夹具 | `tests/conftest.py` |
| PYSEC-2026-3481/3482/3483 | MCP SDK 固定为 `>=1.28.1,<2`（下界 = 安全修复；上界 = 2.0.0 移除了 `streamablehttp_client`） | `pyproject.toml` |

---

## 25. 成熟度路线图

> 状态：2026-10-25 会话的评估 + 建议。P0/P1 事项正在执行；本文档是跟踪记录。

### 25.1 背景

0.2.1b 快照被评估为 **约 0.8/1.0 成熟度**（稳定的 Beta）。在 1.0 发布之前，有四个领域被点名为主要差距：

1. **OS 级沙箱**——评估时已实现（`coworker/tools/sandbox.py`、Docker executor），但只有 opt-in、没有 GUI 开关、文件工具未沙箱化、没有后台任务、没有 Windows 验证。
2. **前端架构**——`surfaces/gui/src/App.tsx` 约 2,400 行，50+ 个 hook；没有 router/状态库。
3. **Windows 平台测试与打包**——CI 只跑 `ubuntu-latest`；两个仅 Windows 出现的测试失败；安装包未签名。
4. **新功能验证**——marketplace / threat model / PWA 缺少 e2e 规范和真实世界验证。

### 25.2 优先级计划

| 优先级 | 事项 | 状态 |
|---|---|---|
| P0 | 增加 `windows-latest` CI job + 修复 2 个已知的 Windows 测试失败 | ✅ 已完成 |
| P0 | 为 6 个新功能添加 e2e 规范（模板 marketplace、连接器 marketplace、安全 tab、记忆纯文本、hire modal、浏览器面板） | ✅ 已完成 |
| P1 | 抽取 hook 以拆分 App.tsx（`useAppNavigation`、`useRail`、`usePersonas`） | ✅ 已完成 |
| P1 | 沙箱 GUI 开关 + 可见的 `sandboxed` 状态 | ✅ 已完成 |
| P2 | CI 中的 Windows 打包 + Authenticode 签名 | ✅ 已完成（release job 已存在；Authenticode 签名脚手架已添加） |
| P2 | 真实安全循环演练 + 报告（`scripts/security_drill.py`、`reports/security-drill.md`） | ✅ 已完成 |
| P2 | PWA 移动端冒烟测试 + Lighthouse 审计 | ✅ Lighthouse 已运行（真实浏览器，最终）：**SEO 100 · A11y 100 · Best Practices 96 · PWA installability 2/2 · Performance 50**。已修复：`<main>` 地标、生产 sourcemap、`--faint` 对比度 token（亮色 + 暗色）。Best Practices 96 = 控制台网络错误（静态服务审计时没有后端）+ 安全响应头（部署层面）。Service Worker（离线 shell）已添加于 `public/sw.js`；`index.html` 中有内联首屏 shell |
| P2 | 沙箱 v2：逐命令探测 + 运行时回退 | ✅ 已完成（`DockerExecutor` 以 `sandboxed:false` + reason 回退到本地） |
| P2 | 沙箱 v2：后台任务 | ✅ 已完成（长生命周期容器 + `docker exec`、输出回读、kill） |
| P2 | 沙箱 v2：文件工具经受沙箱（完整 read/write/replace/patch/list + 多根 + 宿主机回退） | ✅ 已完成（`sandbox_files.py` + `unified_diff.py` + catalog 替换） |
| P2 | 沙箱 v2：Windows Docker 验证 | ✅ 测试已添加（`test_docker_executor_real_sandbox_run`，在存在 Docker 时运行） |

### 25.3 细节：OS 级沙箱（P1 已执行）

- 添加了 **GUI 开关 + 状态**：Settings ▸ Security 中的 Sandbox 卡片通过 `GET/PUT /v1/settings/sandbox` 读写用户全局沙箱偏好（存于 `<state-dir>/sandbox.json`，在 `build_engine` 中覆盖 `config.toml`）。shell 工具的 `sandboxed: true` 标志以 🛡 徽标显示在工具卡片上（实时 `tool_finished` 与回放的对话记录）。
- 后端：`coworker/sandbox_settings.py`、`coworker/agent.py`、`coworker/server/manager.py`、`coworker/server/app.py`。
- 后续（v2）——均已执行：文件工具经受沙箱、后台任务、逐命令可用性探测、Windows Docker 验证。

### 25.4 细节：前端重构（P1/P2 已执行）

十六个自定义 hook 从 `App.tsx` 抽取到 `surfaces/gui/src/hooks/`：

- `useAppNavigation.ts` —— `surface` / `scheduledOpenId` / `personaView` / `settingsTab` 导航状态与 setter。
- `useSessions.ts` —— 会话列表 + 最近项目、5s 轮询、`PERSONAS_CHANGED` 重新拉取，以及启动恢复辅助（`rememberLastSession`、`resumeTargetForAgent`）。
- `useRail.ts` —— 右侧栏可见性、artifact 预览对导航的自动折叠、浏览器面板刷新键。
- `usePersonas.ts` —— persona 元数据加载/刷新。
- `useKeyboardShortcuts.ts` —— ⌘B / ⌘, 全局快捷键（基于 ref 的监听器）。
- `useArtifactCount.ts` —— Cowork 会话顶栏的产出文件计数。
- `useBoard.ts` —— 团队 board 状态、board/team-chat 覆盖层、`moveBoardItem`。
- `useSessionInbox.ts` —— 活跃会话待处理的 Inbox + Unattended 模式 + 4s 轮询。
- `useComposerPrefill.ts` —— composer 预填（文本/附件），带 nonce 递增。
- `usePendingGates.ts` —— 未解决的 approval/plan/team/items/tool/dir/question gate（最新优先）。
- `useGateResponders.ts` —— approve/plan/team/items/dir/tool/question 应答流程（+ `resolveGates.ts`）。
- `useBoot.ts` —— sidecar 健康 + 恢复上次会话 + 引导门 + splash 展示。
- `useSessionActions.ts` —— open/resume/switch/workspace/project/rename/pin/archive/delete/run。
- `useAutomationToast.ts` —— 5s 自动化运行启动 toast + 徽标刷新。
- `useSessionConnect.ts` —— Session 生命周期 + 完整的 WS 事件 switch（对话记录）。
- `useSendGate.ts` —— UX-029 发送时文件夹强制（stash/bind/temp/cancel）。
- `resolveGates.test.ts` —— 7 个乐观 gate 解析辅助函数的单元测试。

App.tsx 从 **2,414 → 1,423 行**（行为完全相同的状态迁移）；`tsc` 与 i18n 检查是门禁。**Vitest：42 个文件 / 199 个测试在普通终端中全部通过**。对话记录/WebSocket 与 send-gate 簇已抽取；只剩少量胶水代码。

### 25.5 细节：Windows CI + 测试修复（P0 已执行）

- `.github/workflows/ci.yml` 新增 `windows-latest` job，运行 Python 测试套件以及 GUI 类型检查/单元测试。
- `tests/test_automation.py`：`time.tzset()` 测试在 Windows 上跳过（`pytest.mark.skipif(sys.platform == "win32")`）。
- `tests/test_security_bundles.py`：persona/SKILL 文件读取改为 `encoding="utf-8"`（GBK 默认在 Windows 上会出问题）。

### 25.6 细节：e2e 规范（P0 已执行）

`surfaces/gui/e2e/` 下的八个新 Playwright 规范，全部驱动封闭的 mock harness（无模型/网络）：

- `template-marketplace.spec.ts`
- `connector-marketplace.spec.ts`
- `security-tab.spec.ts`（也覆盖沙箱开关：启用/保存/持久化）
- `memory-plaintext.spec.ts`
- `hire-modal.spec.ts`
- `browser-panel.spec.ts`
- `pwa-mobile.spec.ts`（iPhone 视口冒烟）
- `demo-skill.spec.ts`（粘贴步骤 → 生成 → 评审 → 安装）

新端点的 mock 路由已添加到 `surfaces/gui/e2e/fixtures.ts`。

针对被抽取 hook（`useAppNavigation`、`useSessions`、`useRail`、`usePersonas`）、`HireModal`、`TemplateMarketplace`、`MarketplaceModal` 和 `SecuritySection` 的单元测试（vitest）已添加到 `surfaces/gui/src/hooks/*.test.ts` 与 `components/*.test.tsx`；它们在 `gui-unit` CI job 中运行。

### 25.7 细节：沙箱文件工具（P2 已执行）

`docs/design/file-sandbox.md`（见第 14 章）是最后一项沙箱 v2 事项的设计文档：让文件工具（`read_file`/`write_file`/`apply_patch`/`replace_in_file`）经由 `DockerExecutor`（仅工作区、只读根、无网络），带路径映射、遍历守卫、宿主回退与多 root 挂载——均已实现，测试见 `tests/test_security_workflow.py`。

### 25.8 细节：v0.3.1 差距闭合（P0 已执行）

与上游 v0.3.1 发布说明对齐（OpenShell + 出网策略）：

- **出网策略服务器**（`coworker/egress.py`）：持久化的域名白名单（`<state-dir>/egress-policy.json`），精确/子域匹配，线程安全。
  - `request_egress` 工具（`coworker/tools/egress.py`，受审批管控）让 agent 请求允许某个被阻断的域名；权限引擎会查询该策略，使已批准的域名自动运行（`permissions.py`）。
  - API：`GET/POST /v1/egress/policy`、`DELETE /v1/egress/policy/{domain}`。
  - 在所有 7 个安全 persona 上注册为 `egress` catalog 能力。
- **OpenShell executor 脚手架**（`coworker/tools/openshell.py`）：可配置的 CLI 前缀（`sandbox_openshell_prefix`，默认 `openshell exec --`）、带缓存的可用性探测、逐命令的 v2 回退到 LocalExecutor 并带 `sandboxed: false` + `fallback_reason`。当 `sandbox_backend == "openshell"` 时 `agent.py` 选择它。
- 测试：`tests/test_egress.py`（策略/工具/能力/API/权限引擎）、`tests/test_openshell.py`（前缀解析、回退、假 CLI 成功、状态 API）。
- **出网策略 UI**：Settings ▸ Security 卡片（列出/添加/移除已批准域名）+ OpenShell 后端状态行（`getOpenShellStatus`）。
- **容器镜像**：`Dockerfile` + `.github/workflows/docker.yml` 在 tag 上发布 `ghcr.io/andrewyng/openworker:<tag>` + `latest`。
- **e2e**：`security-tab.spec.ts` 覆盖出网卡片（添加/列表/移除），为 `/v1/egress/policy` 提供可变 fixture 路由。
- 后续：把真实 OpenShell CLI 参数写入 `sandbox_openshell_prefix`；桌面主机检查已落地（`openshell_version` + `docker_host_networking` 命令与 Settings ▸ Security 卡片）。

### 25.9 验证

- 完整后端套件（P0 之后）：**2004 通过，8 跳过**；45 个失败 + 3 个错误与之前的**环境相关**集合相同（PATH 上缺少工具二进制、工具下载 / Slack 中继 / URL 重定向守卫 / git clone 的网络被阻断、Windows 后台 kill 语义、重型 e2e 时序）——均不触及此处构建的 egress / OpenShell / 安全功能。新增的 `test_egress.py` + `test_openshell.py`（11 个测试）全部通过。
- Workflow YAML：`ci.yml`、`release.yml` 和 `docker.yml` 解析正常。
- GUI：`tsc --noEmit` 干净；`scripts/check_i18n.py` 干净（837 个 key）。
- e2e 规范在 GUI tsconfig 下类型检查通过；运行它们需要普通（非沙箱）环境（Playwright 会 spawn 浏览器）。

### 25.10 2026-11 更新：借用的 harness 线（11/11 完成）

第二次评估（2026-11-07）给该代码树打分 **约 0.88/1.0**。从 DeepSeek Harness 分析中借用的十一项均已落地；五项评测侧事项见第 21 章（eval-and-replay.md），四项同意侧事项见第 3 章（permissions-and-approvals.md）的 §插件同意治理。

| 事项 | 落地内容 |
|---|---|
| P0-1 回合账本 | `coworker/trajectory.py` —— 仅追加、已脱敏的 JSONL，`GET /v1/sessions/{id}/trajectory` |
| P0-2 fork / replay | `POST /v1/sessions/{id}/fork` + `/replay` + `trajectory/diff` |
| P1-3 PluginManifest | `coworker/plugins/` 面向 persona/skill/connector/mcp 的适配层 |
| P1-4 presets + eval matrix | minimal/creator preset、`coworker/eval/matrix.py` |
| P2-5 code mode 评估 | No-Go 报告使引擎循环保持原样 |
| form-B code mode | `coworker/codemode/` —— `exec` 工具、故障即关闭的 Docker 沙箱、审批桥接 |
| P2-6 harness registry | `coworker/personas/harness.py` —— harness 定义的唯一来源 |
| P2-7 统一安装管线 | connector/MCP 安装改道经 `plugins/lifecycle` |
| P2-8 同意 v2 | 权限放宽的 `PATCH` 需显式 `confirm` 作为门禁（否则 409） |
| P2-9 回合中引导 | 纯文本消息在运行中排队，在下一个模型边界注入 |
| P2-10 同意审计 | 仅追加的 `plugin_audit.jsonl` + `GET /v1/plugins/audit` |
| P2-11 评测采集 | `coworker/eval/capture.py` → `matrix --case` 消费该包 |

本条线末尾的验证：完整 pytest **2155 通过 / 9 跳过**，ruff 干净，GUI vitest 199/199，i18n 检查干净。

### 25.11 2026-11 更新：CI 中的 eval 门禁

评测循环最后的自动化缺口已闭合：

- `evals/ci-case/` —— 已提交的确定性黄金用例（`messages.jsonl` + `baseline.json`），由一次 `--stub` 运行生成。
- `.github/workflows/eval.yml` —— `gate-stub` 在每次 push/PR 上运行 `matrix --case evals/ci-case --stub --check-baseline`（漂移时退出码 3，报告作为 artifact 上传）；`matrix-real` 是手工 `workflow_dispatch` lane，用仓库 secret 运行真实模型并按相同规则门禁。
- `matrix --write-baseline` 重新生成黄金基线（仅 stub、单个单元（一个模型 × 一个 harness）、与 `--check-baseline` 互斥）。工作流与重新生成都固定**空且非 git 的工作区 + 空 `COWORKER_STATE_DIR`**：出站载荷内嵌工作区路径、git 状态/提交、每回合时钟与用户技能/记忆，任何其他环境设置都会引入噪声漂移。
- 测试：`tests/test_eval_capture.py` 覆盖基线刷新后门禁通过、参数守卫、黄金用例的形态。

**1.0 之前的剩余差距**：真实模型矩阵仍需用密钥运行一次并提交其基线，以获得真实证据（接线与确定性 stub lane 已就位）；OpenShell 仍使用占位 CLI 参数；前端 `zustand` 迁移进行中（`sessionStore` 与 `activeSessionStore` 两个切片已落地）。

### 25.12 后续（P2）

评测：用密钥运行一次真实模型矩阵并提交其基线（CI 接线与确定性 stub lane 已落地于 `.github/workflows/eval.yml`）。安全：`sandbox_openshell_prefix` 中的真实 OpenShell CLI 参数；桌面主机检查已落地（`openshell_version` + `docker_host_networking` 命令，Settings ▸ Security 卡片）。前端：`sessionStore`（会话列表）与 `activeSessionStore`（活动会话）两个切片已落地，`runStore` 与 UI 偏好切片仍列入 `gui-state-zustand.md` 计划。