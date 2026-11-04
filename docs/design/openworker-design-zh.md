# OpenWorker 设计文档（中文版）

> 对应英文分篇文档见本目录各 `*.md`；本文为整合版，依据 0.2.1 代码快照逆向编写。
> 一切以代码为准：文中类名、枚举值、协议消息均经源码核验。代码标识符与路径保留英文。

## 目录

1. [系统架构](#1-系统架构)
2. [Agent 引擎](#2-agent-引擎)
3. [权限与审批](#3-权限与审批)
4. [服务端与认证](#4-服务端与认证)
5. [模型提供商](#5-模型提供商)
6. [工具系统](#6-工具系统)
7. [连接器](#7-连接器)
8. [MCP 客户端](#8-mcp-客户端)
9. [自动化与 Inbox](#9-自动化与-inbox)
10. [记忆](#10-记忆)
11. [团队（多 Agent 协作）](#11-团队多-agent-协作)
12. [Persona 与技能](#12-persona-与技能)
13. [桌面应用、STT、打包与 CI](#13-桌面应用stt打包与-ci)
14. [决策日志（精选）](#14-决策日志精选)

---

## 1. 系统架构

### 1.1 三个运行部件

OpenWorker 是本地优先（local-first）的桌面应用，运行时为一个原生进程 + 一个 Python 子进程：

```
┌──────────────────────────────────────────────────────────────┐
│ Tauri 桌面壳（surfaces/gui/src-tauri）                       │
│  • 原生窗口 + 系统托盘（关窗即隐藏，服务常驻）               │
│  • 在空闲端口拉起并监管 Python sidecar 子进程                │
│  • 向 webview 注入 HTTP/WS 地址 + 启动 token                 │
│  • 原生命令：目录选择、开机自启、防休眠、听写（STT）、更新器 │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ React SPA（surfaces/gui/src）                          │  │
│  │  App.tsx 约 2400 行；api.ts 约 2500 行                 │  │
│  │  REST 管状态，/ws/session/{id} 承载实时回合            │  │
│  └────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│ Python agent server —— "sidecar"（coworker/server）          │
│  • FastAPI + uvicorn，仅绑定 127.0.0.1                       │
│  • SessionManager 统管引擎、会话、持久化、自动化调度器、     │
│    连接器网关和跨会话 Inbox                                  │
│  • TurnEngine（coworker/engine.py）执行 agent 回合           │
└──────────────────────────────────────────────────────────────┘
        │ 文件/终端           │ 25+ 连接器          │ 任意 LLM 提供商
        ▼                    ▼                     ▼
     本机（roots、     Slack、GitHub……        OpenAI、Anthropic、GLM、
     shell、搜索）     用你自己的 token        Gemini、Ollama……用你自己的 key
```

同一服务端二进制有两种运行模式：

- **桌面模式**：Tauri 通过环境变量传入仅存内存的 launch token，任何秘密不落盘。
- **独立模式**：`openworker-server` 把 token 写到仅当前用户可读的 `<state-dir>/sidecar-<port>.token`（退出时删除），供 Vite 开发 UI 或 curl 以 `X-OpenWorker-Token` 头认证。

浏览器 UI 与桌面壳共用一套代码：SPA 按 Tauri 注入的运行时全局变量 → Vite 环境变量 → `127.0.0.1:8765` 的顺序解析后端地址。

### 1.2 模块地图（coworker/）

**回合核心**

| 模块 | 职责 |
|---|---|
| `engine.py` | `TurnEngine` —— 自研 agent 循环（见第 2 节） |
| `agent.py` | `build_engine()`：把 Agent + 工具 + 权限 + AGENTS.md + 记忆 + 技能 + 连接器 + MCP 组装成 TurnEngine |
| `agents/` | 声明式 `Agent` 数据类与内置 agent（`code`、`chat`、`cowork`、`myhelper`） |
| `permissions.py` | `PermissionEngine`：模式、规则、授权、硬底线（见第 3 节） |
| `risk.py` | 每次工具调用的 `RiskClass` 风险分级 |
| `reviewer.py` | Auto-Approve 模式背后的 LLM 评审员 |
| `events.py` | 引擎事件枚举（`assistant_delta`、`tool_proposed`、`permission_required`……），流式推给 GUI |

**上下文与历史**：`compaction.py`（长历史自动压缩，OPE-27）、`sessions.py`/`conversations.py`（会话持久化）、`memory/`（长期记忆，见第 10 节）、`session_facts.py`。

**表面与协调**：`server/`（FastAPI，见第 4 节）、`inbox.py`/`inbox_routing.py`/`unrouted.py`（跨会话 Inbox 及路由）、`unattended.py`（无人值守标记）、`mentions.py`（@ 提及路由，§31 mention router）、`subscriptions.py`（会话订阅频道）、`automation/`（见第 9 节）、`teams/`（见第 11 节）、`selfwake.py`（会话自唤醒）。

**环境与信任**：`roots.py`/`workspace_trust.py`/`projects.py`（可写根、工作区信任）、`provenance.py`（本回合 agent 下载过的文件，喂给 OPE-114 底线）、`toolchain.py`（PATH/工具解析，OPE-82/83）、`secrets.py`（SecretStore 凭据存储）、`audit.py`（审计日志）、`readonly.py`（只读 shell 命令分类器）、`config.py`（TOML 配置，见 `docs/config.example.toml`）、`cloud.py`（OpenWorker-Cloud 客户端）。

**能力平面**：`providers/`（见第 5 节）、`tools/`（见第 6 节）、`connectors/`（见第 7 节）、`mcp/`（见第 8 节）、`personas/`/`skills/`（见第 12 节）、`catalog.py`（声明式能力目录）、`web/`（web_search/web_fetch 与出网守卫）、`tui/`/`cli.py`（终端界面与 CLI）。

### 1.3 状态目录清单

| 文件/目录 | 归属 | 内容 |
|---|---|---|
| `coworker.db` | `memory/sqlite_store.py` | 长期记忆（`memories` 表） |
| `memory-settings.json` | `memory/settings.py` | 记忆开关 + 用户规则文本 |
| `automation.db` | `automation/store.py` | 计划任务/运行记录，按 `next_run` 索引 |
| `teams.db` / `journal.db` / `chat.db` | `teams/` | 看板事件日志、日志簿、团队聊天（各自哈希链） |
| `teams.json` | `teams/registry.py` | 已注册团队（lead persona、workers） |
| `board-tokens.json` | `teams/tokens.py` | `owb_` 加入令牌（sha256 存储） |
| `attachments/` | `teams/attachments.py` | 内容寻址二进制，事件日志中引用 `attachment://` |
| `inbox.json` | `inbox.py` | 人类注意力队列（审批、提问……） |
| `sidecar-<port>.token` | `server/run.py` | 独立模式启动 token，仅用户可读，退出即删 |

另有只读不写的配置位置：全局 `~/.config/coworker/`（含 `mcp.json`）与各工作区 `.coworker/` 目录（工作区级 MCP 服务器）。

### 1.4 设计不变量

1. **本地优先**：循环、转录、token、密钥只在用户选定的模型提供商与连接器之间出网；唯一的云组件是连接器的 OAuth 代办/中继（`cloud.py`）。
2. **引擎与提供商无关**：全局唯一的 OpenAI 形状消息格式，提供商私有状态走 sidecar，请求前剥离（见第 5 节）——这是会话中途换模型的根基。
3. **失败即关闭（fail closed）**：空白名单不放过任何人；仓库级配置只能收窄权限、永不放宽（`config.py::_GLOBAL_ONLY_FIELDS`）；无法界定范围的操作降级为 human-only。
4. **交互不丢失**：agent 需要回答的每个提问先落为持久化 Inbox 项，断线/重启不孤儿化回合（见第 4、9 节）。
5. **注册表不做裁决**：`ToolRegistry` 只执行工具；权限裁决在引擎、在每次调用之前。

---

## 2. Agent 引擎

> 源码：`coworker/engine.py`（`TurnEngine`，约 2000 行），由 `coworker/agent.py::build_engine()` 组装。

### 2.1 一个回合 = 多次迭代

**回合（turn）** 是一条用户消息加上 agent 为其做的全部工作：模型调用与工具调用的 while 循环，直到模型不再调工具、护栏触发或用户打断。迭代上限来自配置 `max_iterations`（`coworker/config.py:34`，默认 **150**；引擎构造函数自带的默认 12 只用于裸测试）。

```
run() / retry() / resume()
   └─ _loop():
        1. 自动压缩检查点               （OPE-27）
        2. 流式调用模型                  （_astream）
        3. 追加 assistant 消息           （含 _display sidecar）
        4. _handle_tool_calls()
             a. 逐个授权全部调用         （顺序执行，见下）
             b. 执行                     （并行安全 ∥ 顺序）
        5. 循环直到：无工具调用 | 达上限 | 护栏 | 打断
```

入口（`engine.py`）：`run()`（正常入口）、`retry()`（重跑失败回合）、`resume()`（崩溃/重启后的持久化恢复——末尾未应答的 tool-call 重新停靠，提示回调会找到已持久化的 Inbox 项而不是重复创建，因此不会二次执行）。

### 2.2 流式

提供商是阻塞流；引擎用线程 + 队列桥接为异步（`_astream`）。增量以引擎事件输出（`events.py`）：`assistant_delta`（可见文本）、`reasoning_delta`（模型思考，仅展示、永不回放给提供商）、`tool_proposed`、`permission_required`、`tool_started`、`tool_finished`。停止是流队列与取消事件之间的竞速；打断同时触发 `interrupt_hooks`——正在运行的 shell 子进程由此被杀掉。

### 2.3 工具调用编排

`_handle_tool_calls()` 把**授权**与**执行**分开：

1. **先授权，全部、顺序**（spec §8.6——每个请求一个动作，每动作一张卡）。并行的交互式审批卡毫无意义。
2. **再执行**：并行安全类（读、搜索）经 `asyncio.gather` + `asyncio.to_thread` 并发；写文件与 shell 命令严格按到达顺序执行。

一组固定的**门控工具**完全不走工具注册表——它们是引擎的交互词汇，各有专用 `_handle_*` 生成器：`ask_user`、`propose_plan`、`request_directory`、`request_tool`、`propose_team`、`propose_work_items`。

**无孤儿 tool-call**：每个工具调用——获批、被拒或被打断——最终都得到一条工具结果消息。协议要求每个 `tool_call_id` 都被应答；拒绝时给模型简短拒绝语（完整理由给*用户*看），模型可以反应而不是报错。

### 2.4 审批与授权

授权结果（`ApprovalOutcome`，`engine.py:42`）：`once`、`always_tool`、`always_command`、`always_domain`、`readonly_session`、`deny`。除 `once` 外都会铸造会话授权（session grant），下次匹配调用不再询问。`readonly_session` 特殊：来自只读命令分类器（`readonly.py`），一次性预批整个会话内分类通过的 shell 读命令。完整裁决管线见第 3 节。

### 2.5 自动压缩（OPE-27）

在工具回合之间、新模型调用之前取检查点。历史超过阈值后，边界之前的全部内容被压缩摘要取代；边界本身记入会话状态（`sessions.py`、`conversations.py` 均防御式解析，旧/坏行降级为"从未压缩"而非崩溃）。无人值守运行中压缩失败会自动修剪，不会卡死运行。

### 2.6 Steering 与打断

- `queue_steering()`：用户可在回合中追加消息，作为 steering 上下文在下一次迭代之间注入，不排队为新回合。
- 打断：取消当前迭代、经 `interrupt_hooks` 杀掉 shell 子进程，并照常闭合所有未完的工具调用。

### 2.7 模型看到什么

引擎持有 canonical 消息列表（OpenAI 形状）。提供商私有状态挂在 sidecar 键上——`_anthropic`（思考块）、`_gemini`（thought signature）、`_display`（仅 GUI）——`_outbound_messages()` 在每次请求前剥离。由此：会话中途换模型是安全的；`_display` 跨重载存活但*绝不*对提供商可见（owner ruling 2026-08-24）。引擎还追踪**本回合 agent 自己创建/下载过的内容**（OPE-114 §1）——该来源集合正是权限篇中"取回即执行"底线的输入。

### 2.8 上下文组装（agent.py::build_engine）

`build_engine()` 把 `Agent`（`agents/base.py`——声明式特质数据类：`requires_folder`、`subagents`、`scheduling`、`messaging`、`connectors`、`team`）与以下部件接线：能力目录展开的工具集；权限引擎 + 实时设置 getter（引擎持有**晚绑定可调用体**而非烘焙快照，设置改动下一次调用即生效）；工作区根的 AGENTS.md；记忆块（第 10 节）；实时技能菜单（SKILLS-SPEC §4.1——每回合重算，与 roots 列表、每回合注入的实时时钟同理，owner ruling 2026-08-20）；连接器（最小权限：带白名单的 persona 只获得白名单内连接器，OPE-93）与 MCP 服务器。agent *知道什么*在会话开始即固定（MEMORY-SPEC §7.1）；技能菜单与时钟是刻意的例外，实时刷新。

---

## 3. 权限与审批

> 源码：`permissions.py`、`risk.py`、`reviewer.py`、`engine.py` 的授权路径。
> 治理原则：**失败即关闭；任何便利特性都不得放宽人类未批准的范围。**

### 3.1 权限模式（PermissionMode，permissions.py:214）

| 模式 | 值 | 含义 |
|---|---|---|
| `DISCUSS` | `discuss` | 只读对话：不编辑、不走规划工作流 |
| `PLAN` | `plan` | 与 DISCUSS 同一只读闸门，仅意图不同——引导 agent 走向 `propose_plan` 审批 |
| `INTERACTIVE` | `interactive` | 请求审批（默认） |
| `BYPASS_APPROVALS` | `bypass-approvals` | 全权限，硬底线除外 |
| `AUTO_APPROVE` | `auto-approve` | INTERACTIVE + LLM 评审员可预批卡片 |
| `CUSTOM` | `custom` | INTERACTIVE + 自动放行配置的 `auto_allow` 工具 |

命名史：`bypass-approvals` 由 `"auto"` 改名（spec §1.5，2026-08-12），让"安全系统关闭"不会在选择器里与 `auto-approve` 混淆。未接评审员时 `AUTO_APPROVE` 行为与 `INTERACTIVE` 完全一致。DISCUSS 与 PLAN 共用一个执行闸门——拆分是为了引导模型，不是第二条权限路径；OPE-117 复审保证这些模式下该出卡的地方仍出卡。

### 3.2 风险分级（RiskClass，risk.py:18）

| 类 | 值 | 依据（代码注释） |
|---|---|---|
| `READ` | `read` | 无副作用——总是允许 |
| `EGRESS` | `egress` | 触网——请求本身即可把数据带离本机 |
| `WRITE_LOCAL` | `write_local` | 改动工作区——按路径限定 + 按模式闸门 |
| `EXEC` | `exec` | 执行命令——按模式闸门 |
| `EXTERNAL` | `external` | 机外副作用——无人值守 Inbox 钩子 |

`send_message` 按 EGRESS 闸门，因为消息体是模型自由文本走同一出网通道（spec §2.2）；`browser_open_url` 同理（"换个名字的同一通道"，OPE-111）。

### 3.3 硬底线

`PermissionEngine.evaluate()`（`permissions.py:326`）返回 `Decision`（allowed / reason / needs-user / human-only / rule-matched）。以下底线在**任何模式、白名单、评审员之前**运行：

1. **自我保护**——`protected_paths()`（`permissions.py:93`）：agent 不得改 OpenWorker 自己的设置、密钥、权限配置。
2. **可写根**——授予的工作区根之外的写一律拒绝。
3. **延迟执行**——`.git/hooks`、CI 配置等"以后才跑"的文件 human-only：批准一次文件写入不等于批准其中的代码日后自行运行（OPE-117）。
4. **持久权限**——`PERSISTENT_AUTHORITY_TOOLS`（`permissions.py:85`）：`save_skill` 与计划任务 CRUD 会安装未来行为，永远 human-only。
5. **来源底线（OPE-114 §1）**——运行 agent *本回合下载*的东西是经典"取回即执行"攻击，强制人卡——即使 Auto-Approve 模式也一样，同时压过评审员与命令白名单。评审员根本看不到这些卡。

模式级配置同向：仓库配置只能*收窄*权限（`config.py::_GLOBAL_ONLY_FIELDS`）——检入仓库的文件永远不能授予用户本来没有的能力。

### 3.4 授权管线

`engine.py::_authorize`，每次工具调用：

```
PermissionEngine.evaluate()
   → 来源底线（OPE-114）          [人卡，压过一切]
   → Auto-Approve 评审员（若接入） [只能把"询问"变"允许"，绝不能把"拦截"变"允许"]
   → PERMISSION_REQUIRED 事件 + approver 回调
        → ApprovalOutcome: once | always_tool | always_command
                           | always_domain | readonly_session | deny
```

审批卡是交互式的（以事件流式输出并等待注入的异步 `approver`），且每张未答的卡先落为持久化 Inbox 项，断线/重启零丢失（第 4 节）。常设规则：任务级条目（"工具 → 精确目标"）对自动化运行自动放行匹配调用——限定到精确目标，而非整个工具。

### 3.5 Auto-Approve 评审员

`reviewer.py::Reviewer` 仅在 `Mode.AUTO_APPROVE` 运行。它是与**会话同提供商同模型**的 LLM 裁判，一次性预审当回合全部待出卡项（`_preconsult_reviewer`），一次判断便宜地覆盖整批。不变量：

- 只能把**询问变允许**。权限引擎拦截的保持拦截；评审员无权放宽政策。
- **拒绝**时，agent 收到简短拒绝语（完整理由展示给*用户*，附一次性 "Allow anyway"）。
- **熔断**：连续 5 次拒绝触发 `_REVIEWER_TRIP`，本回合余下部分暂停自动审批（streak 语义按 owner ruling 2026-08-24：从 2 提到 5 并感知累计——朴素的 2 计数会无声惩罚正常的来回交互）。
- **影子模式**：只记录评审结论、不动决策——供 `tests/corpora/` 评测语料与 `reports/` 报告使用。

### 3.6 授权存放

`always_*` 结果铸造**会话授权**——不超出当前会话，且自我保护/延迟执行底线仍在其上叠加（授权永不压过底线）。自动化任务携带自己的任务级 `always_allowed_tools` 规则（第 9 节）。

---

## 4. 服务端与认证

> 源码：`server/app.py`（`create_app`）、`server/manager.py`（`SessionManager`）、`server/run.py`。

Python 后端是 FastAPI + uvicorn 应用，**只绑 127.0.0.1**，是引擎、会话、持久化、调度器、连接器网关与 Inbox 的唯一属主。`SessionManager`（`manager.py:180`）负责接线；`create_app()`（约 2800 行）负责暴露。

### 4.1 传输模型

GUI 用 REST 管状态、每会话一条 WebSocket 承载实时回合：

- **`GET /ws/session/{session_id}`**——主通道，JSON 帧 `{type, data}`。
- **`GET /ws/events`**——只读的全应用推送流（toast、会话列表变化）。
- **REST**——`/v1/` 下约 150 个端点：会话 CRUD 及消息/roots/工件、inbox、persona 安装导出、skills、工作区（open/trust/pick）、连接器（含 `/oauth/callback`）、providers、settings、automations、board（`/v1/board`，按 actor 的 Bearer token）、browser 状态、audit。

**WebSocket 协议（会话通道）**——客户端→服务端：`user_message`、`approval`、`question_response`、`plan_response`、`directory_response`、`tool_response`、`allow_anyway`、`interrupt`、`retry`、`set_mode`、`set_model`、`steering`。服务端→客户端：`ready`、引擎事件原样广播（`assistant_delta`、`reasoning_delta`、`tool_proposed`、`permission_required`、`tool_started`、`tool_finished` 等）、`turn_done`、`mode_notice`、`model_changed`、`question_requested`、`input_rejected`。

四个交互提示（审批、提问、计划、目录）都在任何人被询问*之前*先停靠为持久化 Inbox 项（`inbox.wait`）——WS 帧只是应答方式之一。同一项同样可经 REST（`POST /v1/inbox/{id}/resolve`）或绑定频道（携带 `[ow:<id>]` 的 Slack 回复）应答，先到先得。这就是断线、重启、无人值守零丢失的原因（第 9 节）。

### 4.2 认证：launch token

- 中间件 `require_sidecar_token`（`app.py:214`）以**常量时间比较**校验每个请求；token 经 `X-OpenWorker-Token` 头或 `openworker` WebSocket 子协议发送（CORS 管不住 WebSocket，所以只有源白名单不够）。
- **桌面模式**：Tauri 内存生成，经环境变量传给两个子进程（`run.py:128`），永不落盘。
- **独立模式**：写到仅用户可读的 `<state-dir>/sidecar-<port>.token`（退出删除），供 Vite 开发 UI 与 curl 认证。

针对回环服务器的浏览器源攻击的额外加固：源白名单 `tauri://localhost`、`127.0.0.1`、`tauri.localhost`（`app.py:34`，感知 DNS 绑定）；WS 输入限速 + 帧大小上限（`app.py:47-54`）。

### 4.3 SessionManager 职责

- 每会话引擎生命周期（经 `agent.py::build_engine` 构建）；
- `_dispatch_inbound`——连接器网关的频道消息按频道缓冲并扇出给订阅会话；
- `_route_mention`（§31 mention router）——@ 提及为其生成专属的按线程 coworker 会话，带限定该线程目标的常设 `send_message` 授权并预载频道上下文；DM 路由到指定 DM 会话或停为未路由；
- `_run_scheduled_task`——每次自动化触发都是一条全新的、持久化的、可续聊的会话线程（每次运行单独建引擎），可选向任务的 `notify_target` 发完成通知；
- 团队唤醒管线——按小时的滚动 worker 唤醒预算、陈旧度摘要、签到兜底（第 11 节）。

`run.py` 解析配置、确保 token，把 `create_app(manager)` 交给 uvicorn。各存储防御式解析，坏行降级而不致命。

---

## 5. 模型提供商

> 源码：`providers/base.py`、`router.py`、`registry.py` 及各原生 provider 模块。

### 5.1 抽象

`ProviderClient`（`base.py:102`）是小接口——`complete` / `stream` / `capabilities`——操作**canonical OpenAI 形状消息格式**。提供商层之上的一切（引擎、持久化、转录）只见该形状与值类型 `AssistantTurn`、`ToolCall`、`TokenUsage`、`StreamChunk`。`ProviderRouter`（`router.py:23`）按模型串前缀路由；`registry.py` 声明 `ProviderDescriptor`：UI 表单（标题、API key 字段、端点预填、环境变量回退）加一个产出 `ProviderClient` 的 `build` 函数。

### 5.2 原生 SDK 客户端

并非全部走 OpenAI 兼容壳。原生客户端存在于兼容形状装不下能力的场合：

| 模块 | 提供商 | 原生原因 |
|---|---|---|
| `anthropic_provider.py` | Anthropic | Claude Messages API；思考块走 `_anthropic` sidecar |
| `gemini_provider.py` | Google | google-genai；thought signature 走 `_gemini` sidecar |
| `bedrock_provider.py` | AWS Bedrock | boto3（可选依赖，懒加载） |
| `vertex_provider.py` | GCP Vertex | 服务账号 / ADC / MaaS bearer 凭据 |
| `openai_responses.py` | OpenAI | 原生走 `/v1/responses`——chat.completions 拒绝 GPT-5.6 的 tools+reasoning |
| `codex_provider.py` | OpenAI Codex | Codex 专属认证 |

### 5.3 OpenAI 兼容厂商

`openai_provider.py` 是兼容主力（含 Ollama）。已注册（`registry.py`）：`ark`（BytePlus）、`ark-agent-plan-cn`（Volcengine）、`zai`（Z AI/GLM）、`deepseek`、`kimi`（Moonshot）、`minimax`、`qwen`（Alibaba）、`xai`（Grok）、`mistral`、`meta`（Muse Spark）、`together`、`fireworks`、`openrouter`、`ollama`（本地，端点字段替代 key）。每个描述符预填厂商官方端点，字段可编辑以适配区域/代理变体。

### 5.4 Sidecar 与换模型

canonical 消息列表可携带私有键——`_anthropic`、`_gemini`、`_display`。引擎的 `_outbound_messages()` 在每次请求前剥离当前提供商不认识的一切。这一条规则买来标志性功能：**会话中途换模型**——历史始终有效，因为没有任何提供商的私有格式渗入共享转录。

### 5.5 aisuite 的真实位置

尽管 README 写着 "built on aisuite"，aisuite 在这里**不是** LLM 传输层。它用于：工具 schema 生成（`tools/registry.py` 用 `aisuite.utils.tools.Tools`）与函数上的 `ToolMetadata`；预置工具包（`catalog.py` 里的 files/git——部分已被本仓库更快的自有变体替换）。传输、流式、权限、agent 循环全部自研；`pyproject.toml` 里的固定 commit 是从原 aisuite 单仓迁移的导入点。

另外 `coworker/pdf_support.py` 为不支持原生 PDF 输入的模型做适配：pypdf 抽文本、pypdfium2 栅格化——刻意不用 PyMuPDF（AGPL 许可无法随 DMG 分发）。

---

## 6. 工具系统

> 源码：`tools/`、`catalog.py`、`toolchain.py`，及 `web/`、`connectors/`、`mcp/` 中的工具。

### 6.1 注册表

`ToolRegistry`（`tools/registry.py`）持有晚绑定工具可调用体，OpenAI 函数 schema 经 aisuite 生成。模块开头注释写明一条规则：**注册表永不做权限检查。** 它只执行；裁决在引擎、在每次调用之前。工具以实时设置 getter 接线而非烘焙快照，配置改动下一次调用即生效。

### 6.2 内置工具

| 文件 | 工具 | 说明 |
|---|---|---|
| `shell.py` | `run_shell`、`shell_task_output`、`kill`（经 hooks 的 `interrupt_now`） | 持久 OS 原生 shell REPL——bash 或 PowerShell——藏在 `Executor` 抽象之后，显式为未来 `ContainerExecutor` 沙箱留缝。超时 + 后台任务句柄。 |
| `files.py` | `read_file`、`write_file`、`replace_in_file`、`apply_patch`、`apply_unified_diff` | `read_file` 带窗口与行号——*全局唯一读取器*（owner ruling 2026-08-20，`catalog.py:76`）：整个栈共享它，而不是各工具自己重读。 |
| `search.py` | `grep` | ripgrep，纯 Python 回退。 |
| `git.py` | `git_log` + status/diff | 后者委托 aisuite 工具包。 |
| `todo.py` | `todo_write` | 会话任务清单。 |
| `ask.py` | `ask_user` | 门控工具（引擎处理）。支持分组提问（OPE-51）：每卡最多 4 个 `{question, header, options, allow_text, multi}`——一排按钮答不了两个以上的问题。 |
| `plan.py` | `propose_plan` | 门控工具；PLAN 模式的目标。 |
| `directories.py` | `request_directory` | 门控工具：agent 请求用户授予目录根。 |
| `toolreq.py` | `request_tool` | 门控工具：安装缺失 CLI 工具——**只从固定清单、经摘要校验的目录装**（OPE-85：取回可执行文件是供应链决策，永不自动运行）。 |
| `subagent.py` | `explore` | 只读子代理扇出，用于大范围搜索。 |

### 6.3 Web 工具（coworker/web/）

`web_search`——可配置提供商；默认无 key 的 DuckDuckGo（`ddgs`），可配 Tavily/Brave。`web_fetch`——配合 `guard.py` 出网守卫，把抓取留在权限系统之内（风险类 EGRESS）。

### 6.4 扩展平面

- **连接器工具**：无状态出站 `send_message`（`senders.py::DEFAULT_SENDERS`，Slack/Telegram/邮件）；Playwright 浏览器自动化（可选依赖）——上传只在授予根内解析（OPE-122）、重定向重新校验（OPE-124）。
- **MCP 工具**：每个远端工具桥接为 `mcp__<server>__<tool>`（第 8 节）。
- **记忆工具**：`remember`、`memory_update`、`memory_forget`、`memory_read`（第 10 节）。
- **技能**：`load_skill` / `save_skill`（第 12 节）。
- **自动化工具**：计划任务 CRUD——`PERSISTENT_AUTHORITY_TOOLS` 成员，永远人工批准。
- **团队工具**：看板/日志簿动词，按角色过滤（第 11 节）。

### 6.5 能力目录与工具链

`catalog.py` 持有声明式 `CATALOG`，`expand()` 把 persona/agent 声明的名字展开为具体工具集。Persona 永不罗列裸函数，只组合目录条目——"这个 persona 能摸什么"在一处即可审计。

`toolchain.py` 刻意分开两件事（OPE-82）：**找**二进制——用登录 shell 环境做 PATH 解析（桌面壳在拉起时传入，OPE-83），GUI 启动的服务也能看到用户的 `~/bin`、homebrew 等；**装**二进制——永远躲在 `request_tool` + 人工批准 + 摘要固定之后（OPE-85）。解析与安装的区分就是供应链边界。

0.2.1 **没有 OS 级沙箱**。安全靠分层：每次调用权限闸门、根限定路径、超时、非交互 shell 环境、以及第 3 节的硬底线。`shell.py` 的 `Executor` 抽象是未来 `ContainerExecutor` 的指定接缝。

---

## 7. 连接器

> 源码：`connectors/descriptors.py`、`base.py`、`gateway.py`、`adapters.py`、`relay_client.py`、`github_relay.py`、`accounts.py`、`setup.py`、`tools.py`、`senders.py`。

连接器是 OpenWorker 能施加影响的第三方平台。单一 `DESCRIPTORS` 注册表（`descriptors.py`）声明约 40 个；`register_descriptor()` 加扩展。

**已出货/可连接**：telegram、slack、email（IMAP）、gmail、google_calendar、browser、github、outlook、jira、monday、confluence、zendesk、linear、gitlab、discord、stripe、asana、hubspot、dropbox、box、whatsapp、quickbooks、docusign、clickup、google_drive、canva、figma、close、notion、attio、posthog、mixpanel、amplitude、apollo、hunter。**占位**（`available=False`）：datadog、salesforce、descript、clay、pagerduty。`experimental/` 是风险确认连接器的钩子（当前为空；发布构建排除）。

### 7.1 抽象

`base.py::BasePlatformAdapter`——`connect` / `disconnect` / `send`、入站 `handle_message` 钩子、可选 `send_interactive` / `handle_interaction`（Slack 批准/拒绝按钮）。值类型：`SessionSource`（`platform:chat_id[:thread]` 目标令牌）、`MessageEvent`（含 `mentions_me` 标记与 `tagged_text()` 框定）、`MessageSource`（仅展示 sidecar）、`InteractionEvent`、`SendResult`。

### 7.2 网关

`gateway.py::Gateway` 持有全部适配器，是唯一入站门户：

- **入站白名单**（`config.py::is_authorized`）：空白名单**不放过任何人**——失败即关闭。多工作区中继用按团队 `TeamAuth`。
- 未授权消息停靠（`parked.py::ParkedStore`，JSON 限量队列）待"批准并投递"，不静默丢弃。
- `reply_resolver` 把频道回复（带 `[ow:<id>]` 的 Slack 消息、按钮交互）映射回停靠的 Inbox 项，之后才考虑新回合。

**入站 → 会话**：`manager.py::_dispatch_inbound` 按频道缓冲并扇出给订阅会话。**§31 mention router**（`mentions.py`）处理 @ 提及：为提及生成专属按线程 coworker 会话，带限定该线程的常设 `send_message` 授权并预载频道上下文。DM 进指定 DM 会话或停为未路由。邮件入站轮询（`email_tools.py`）。

**出站**：无状态 `send_message` 工具（`tools.py`）经 `senders.py::DEFAULT_SENDERS`（httpx）——发消息无需长驻适配器。权限系统将其归为 EGRESS，因为消息体是模型自由文本。

### 7.3 三条认证路径

按描述符标志选择：1) **手动**——粘贴 token 字段，一次性 whoami 调用校验（如 `_validate_slack`），存 `SecretStore` profile `<name>:default`；2) **托管 OAuth**（`managed=True`）——经 OpenWorker Cloud 的一次点击代办握手（`cloud.py`；按隐私模型这是*唯一*的云组件；Google 系暂缓待 CASA 审查）；3) **`mcp_url`**——厂商托管 MCP 服务器 + 本地 OAuth/DCR（jira → mcp.atlassian.com、monday → mcp.monday.com），工具面固定为 `tool_defs.py` 里的 `mcp__<name>__<tool>` 定义。多账号（`accounts.py`）：profile 存于 `<name>:account:<id>`（gmail、gcal、hubspot 门户、github 安装）。

### 7.4 云中继

直连入站监听（Slack Socket Mode、Telegram、轮询邮件）要求服务端持有长命凭据。中继（`relay_client.py::SlackRelayAdapter`、`github_relay.py::GitHubRelayAdapter`）是替代：桌面持有一条到 OpenWorker Cloud 的出站 WebSocket，入站事件经 `RelayHub` 按团队/安装白名单扇出（github-relay-spec §2.3、§4、§8）。中继代码**永不触碰 secret store**。重连时按频道上下文重放补回错过的消息（spec §7）。

---

## 8. MCP 客户端

> 源码：`mcp/config.py`、`client.py`、`tools.py`、`oauth.py`。用官方 `mcp` SDK（钉 `>=1.28.1,<2`——下限避开 PYSEC-2026-3481/3482/3483，上限因 2.0.0 移除了 `streamablehttp_client`）。

任何 MCP 服务器都能作为工具源接入，且可逐工具控制。

### 8.1 配置

标准 `mcpServers` JSON——可直接粘贴 Claude Desktop 配置。分层：全局 `~/.config/coworker/mcp.json` + 工作区 `<workspace>/.coworker/mcp.json`（仅对*受信任*工作区生效；同名冲突全局优先）。`MCPServerDef` 字段：传输（stdio 或 streamable-http）、按服务器的 `include_tools`/`exclude_tools`、`requires_approval`、`${VAR}` 环境秘密解析。工作区配置可以收窄服务器工具面，但永不静默放宽信任——与权限配置同向。

### 8.2 生命周期

`client.py::MCPManager` 每服务器保持**一个常驻 asyncio 任务**：stdio——`stdio_client`，stderr 捕获到临时文件，崩溃时给用户留下证据；streamable-http——`streamablehttp_client`。API：`ensure()`（幂等启动）、`tools()`、`verify()`（真实健康检查，往返一次 `tools/list`，而非只探 socket）、`call()`。

### 8.3 工具桥接

`tools.py::build_callables` 把每个 MCP 工具包装为**同步注册表可调用体**，命名 `mcp__<server>__<tool>`，经 `run_coroutine_threadsafe` 桥入服务器的 asyncio 任务（引擎在工作线程执行工具）。OpenAI 函数 schema 由 MCP `inputSchema` 推导。此后 MCP 工具就是普通工具：同样的权限管线、同样的审批卡，`requires_approval` 映射到常设规则机制。

### 8.4 OAuth

`oauth.py` 在 SDK 的 `OAuthClientProvider` 之上实现 **OAuth 2.1 + PKCE + 动态客户端注册**：token 存 `SecretStore` profile `mcp-oauth:<server>`；浏览器回调落在 sidecar 回环的 `GET /mcp/oauth/callback`；非交互上下文（自动化运行、中继）拒绝弹浏览器登录（`InteractiveAuthRequired`），但可静默刷新有效 token。

---

## 9. 自动化与 Inbox

> 源码：`automation/`（`models.py`、`store.py`、`scheduler.py`、`tools.py`）、`unattended.py`、`inbox.py`、`selfwake.py`。

自动化 = 无人坐在桌前的周期性工作（"早报"、"周报"、"盯频道"）。难题是 agent 可能在运行中途撞上审批而没人在场——这正是 Inbox 解决的。

### 9.1 模型

- **`ScheduledTask`**——标题、指令、`Schedule`（kind `cron`|`once`，带 IANA 时区）、工作区、agent/persona、`always_allowed_tools`（任务级常设规则："工具 → 精确目标"条目，不是整工具放行）、可选 `notify_target`。每任务拥有会话 id `__task__<id>`。
- **`TaskRun`**——一次触发：会话 id `__run__<run_id>`，状态 running/ok/error/skipped，触发来源 schedule/manual/catchup。

`store.py::TaskStore` 持久化到 SQLite（`automation.db`），行按 `next_run`/`enabled` 索引。下次触发时间用 croniter 计算，感知夏令时（本地朴素基准或 `ZoneInfo`；`tzdata` 是 Windows 硬依赖，正为此）。

### 9.2 调度器

`scheduler.py::Scheduler` 在常驻服务内每 30 秒一跳（桌面托盘驻留保证它活着）：**启动时一次性补跑**（关机错过的触发只跑一次，不跑 N 次）；**重叠跳过**（`_running_ids`：上一跑未完，新触发记 skipped）；运行作为**独立任务**孵化，停靠在审批上的运行永远拖不死调度循环；`extra_tick` 恢复自唤醒会话（`selfwake.py`——请求"稍后再查"的会话）。每次触发（`manager.py::_run_scheduled_task`）都是全新、持久化、可续聊的会话线程：单次运行单独建引擎、完整转录保存、toast 广播、可选向 `notify_target` 发完成消息。

### 9.3 无人值守与 Inbox

`unattended.py::UnattendedRegistry` 给会话打无人值守标记（计划运行，以及用户离开的任意会话）。标记只改变一件事：所有交互提示路由到**跨会话 Inbox**而非 GUI。

`inbox.py::InboxStore`（`inbox.json`）是人类注意力队列。项种类：`approval`、`question`、`notification`、`directory`（目录授予）、`plan`、`tool`（缺失 CLI 工具）。生命周期 `pending → resolved`，**先应答者胜**——项可从三处回答：GUI 实时 WebSocket；REST `POST /v1/inbox/{id}/resolve`；绑定频道——带 `[ow:<id>]` 的 Slack 回复，或经网关 `reply_resolver` 解析的批准/拒绝按钮（第 7 节）。项待决期间 `inbox_approver` 挂起 agent 运行（不忙轮询、不超时强答默认）。这与 GUI 会话断线存活是同一套停靠机制——无人值守只是 Inbox 成为*唯一*应答通道的情形。

### 9.4 创建任务

两个入口：GUI 计划视图，以及 agent 自己经 `automation/tools.py`。后者刻意昂贵：计划任务 CRUD 是 `PERSISTENT_AUTHORITY_TOOLS` 成员（第 3 节）——永远人工批准，因为带常设授权的任务会以超出创建它的对话的权限执行。

---

## 10. 记忆

> 源码：`memory/`（`base.py`、`sqlite_store.py`、`settings.py`、`tools.py`）及 `agent.py` 的注入管线。行为遵循 "MEMORY-SPEC"（全文不在本仓库；代码注释引用其章节）。

记忆是 coworker *在会话之间*记住的东西——区别于会话转录与压缩。

### 10.1 模型

`MemoryStore` ABC 与 `MemoryItem`（id、scope `global`|`workspace`|`session`、key、content、summary、workspace、session_id、created_at）。后端 `sqlite_store.py::SQLiteMemoryStore`，库文件 `coworker.db`（`memories` 表）。`session` scope **已死**（MEMORY-SPEC §3）：写入重定向到 workspace，工具层直接拒绝。

### 10.2 开关与用户规则

`settings.py::MemorySettingsStore`（`memory-settings.json`）持有：**开关**——关闭时写工具当场拒绝（硬开关，"注册了也无害"不够）；**`user_rules`**——用户在设置里输入的自由文本，逐字注入并**凌驾于学到的记忆之上**（MEMORY-SPEC §6）。agent 永远写不了这个字段——它是用户权威，不是模型知识。

### 10.3 注入

`render_memory_block`（接线于 `agent.py`）构建追加到系统上下文的块：渲染后低于 8000 字符走**全量模式**（逐字全给）；超过走**索引模式**——最新 10 条全文 + 其余单行摘要 + `memory_read` 按 `[#id]` 拉取工具（MEMORY-SPEC §7）。agent *知道什么*在会话开始即固定（§7.1）——回合中保存会显示"我会记住"提示，但不改变当前回合上下文。

### 10.4 工具与防"假装保存"

工具：`remember`（保存）、`memory_update`、`memory_forget`、`memory_read`。`remember` 持久化后 `on_saved` 钩子（§5.1，接线于 `agent.py:207`）推送 UI 通知——"我会记住……[撤销]"——保存永远可见且可逆。

系统提示携带明确的何时记忆规则（§4.2）与防"假装保存"（anti-bluffing）声明，源于 **2026-07-28** 的一次观测事故：模型*伪造*了记忆保存（没调工具却宣称记住了）。对策即声明 + 永远可见的保存 toast——记忆变更必须经工具中介并被浮现，绝不能在散文里断言。

---

## 11. 团队（多 Agent 协作）

> 源码：`teams/`（`store.py`、`model.py`、`journal.py`、`chat.py`、`registry.py`、`tools.py`、`dialect.py`、`tokens.py`、`attachments.py`、`cli.py`、`mcp_server.py`）。

团队是多 agent 编排基底：**lead** persona 把工作分解为看板条目，**worker**（各自独立 agent 会话）认领并完成，全部记录防篡改。设计目标（OPE-100）：把看板暴露为任何 harness——包括外部编码 agent——都能加入的*开放表面*。

### 11.1 看板即事件日志

`store.py::TeamStore`（`teams.db`）按空间（工作区路径为空间键）保持**只追加、哈希链事件日志**。看板条目是*投影*：`_apply`/`rebuild` 在与追加**同一事务**内把事件折叠为条目列表，日志与视图不可能分叉。每空间链可验证（`verify_chain`），带 `taint` 来源标记。为何事件溯源：agent 驱动的工作受益于无法被悄悄改写审计轨迹——worker 把自己的条目标完成时，分派事件就在旁边，被哈希锁住。

### 11.2 模型

`ItemState` 状态机：`open → in_progress → blocked | review → done | canceled`。`WORKER_TARGETS`——worker 永不标完成；完成是 lead/用户的决定。`Role`（`user`/`lead`/`worker`/`system`）与 `Actor`。

### 11.3 日志簿与聊天

- `journal.py::JournalStore`（`journal.db`）——按键归档、**比看板活得久**的知识：种类 `finding`/`evidence`/`decision`/`note`/`raw`，正文上限 16k，自有哈希链。授权表管共享（"共享搭分派的便车"）。
- `chat.py::ChatStore`（`chat.db`）——每队一条群聊；agent 发帖只唤醒被 @ 的成员（无闲置 agent 闲聊）。
- `attachments.py`——内容寻址二进制；日志携带 `attachment://` 引用，事件日志本身保持小体积。

### 11.4 编排

`registry.py::TeamRegistry`（`teams.json`）持有团队：lead persona（内置 `swe-lead`、`devops-lead`、`devsecops-lead`、`triage-lead`）加 worker。流程：lead 分解工作（`propose_work_items`）、配员（`propose_team`——worker 会话**预孵化但空闲：首次分派前零 token**）、分派看板条目。唤醒管线随后推动 worker：调度器的 `extra_tick`（第 9 节）、按小时滚动唤醒预算、陈旧度摘要、manager 的签到兜底——已分派但停滞的 worker 会被轻推而不是被遗忘。

`tools.py` 以**角色过滤的动词集**暴露看板/日志簿动词（`LEAD_VERBS` 含 assign/link；`WORKER_VERBS` 含 claim），且 store 在动词之下重新校验权限——工具层是便利，store 是执法。

### 11.5 开放表面

同一看板的三种触达方式：1) **`ocw` CLI**（`cli.py`；`ocw board …`/`ocw journal …`）——自动解析 远程 → 本地服务器 → 直连 SQLite 后端；2) **`ocw board mcp`**（`mcp_server.py`）——同一表面作为 stdio MCP 服务器提供，外部编码 agent（任何 MCP 客户端）可加入团队认领条目；3) **REST `/v1/board`**——带**按 actor 的 Bearer token**（`tokens.py::BoardTokens`，`owb_` 加入令牌 sha256 存储，绑定 actor + 角色）。

`dialect.py` 抽象后端：`LocalDialect`（直连 SQLite）与 `RemoteDialect`（经 `/v1/board` 的 HTTP）藏在一个 `BoardDialect` 接口后。Jira/Linear 集成刻意做成**镜像而非方言**——向外同步看板状态，永不成为记录系统。

---

## 12. Persona 与技能

> 源码：`personas/`（`manifest.py`、`registry.py`、`loading.py`）与 `skills/`。

### 12.1 Persona = 无代码的 agent 定义

Persona 是**带 YAML frontmatter 的 markdown 文件**（`manifest.md`）：正文是系统提示，frontmatter 声明能力。persona 不随附可执行代码——这是刻意的安全属性。

`manifest.py::PersonaManifest` 严格解析——未知键或畸形值抛 `ManifestError` 而非忽略。关键字段：`tools`（经 `catalog.py::expand()` 组合的目录能力名）、`skills`/`mcp`、`connectors`（`False` / 白名单元组 / `all` 哨兵——哨兵**仅限内置，第三方输入解析时失败关闭**，OPE-93：绝没有裸的"用连接器"位）、`team`（`lead`/`worker`/无——单人 persona 不可入队）、`requires_folder`/`subagents`/`scheduling`/`messaging`（与 `agents/base.py` 对应的特质闸门）、`default_permission_mode`/`recommended_models`/`recommends`（默认值 + UI 中展示的连接器/MCP 推荐，含理由与层级）、`version`/`ships`/`group`。

`to_agent()` 物化为运行时 `Agent`；`registry.py::PersonaRegistry` 统一两族 persona 并跟踪 已安装 → 已启用 → 已浮现 生命周期（状态文件）。默认 persona：`cowork`。

### 12.2 内置

Python 构建（`cowork`、`code`）+ `personas/builtin/` 下的 markdown persona：`security`、`swe-lead`、`swe-worker`、`devops-lead`、`devsecops-lead`、`triage-lead`、`appsec-worker`、`secrets-worker`、`cloud-posture`、`posture-worker`、`dep-audit`、`logs-worker`、`infra-worker`、`test-worker`、`design-worker`、`change-worker`、`ops.md`（`ships: false`）。安全族 persona 以 semgrep 为中心，manifest 旁携带技能（如 `semgrep-review`）。

### 12.3 第三方安装与同意

`loading.py` 从本地目录或浅 git 克隆加载。安装时用户看到 **`consent_summary`**：具体能力集——工具、风险类、连接器、MCP 服务器、团队能力——绝无含糊描述。更新时若新版本的 `capability_set` **增长**，既有同意不再匹配，必须重新同意（OPE-93：加了连接器的更新必须使集合增长并重新询问）。

### 12.4 技能

技能是 `skills/<name>/SKILL.md`——YAML frontmatter（name、description）+ 模型按需加载的操作规程。行为（SKILLS-SPEC）：**每会话生效菜单**（§3）——技能列表按会话计算，传给引擎的是**每回合重算的活可调用体**（`agent.py:419`）——与 roots 列表、实时时钟同理（owner ruling 2026-08-20）。`load_skill` 会话中注入技能规程；强制运行的技能把结果处理拆进 `_display` sidecar，用户视图与提供商可见转录安全地分岔。**`save_skill` 是"worker 作者之门"**（§5.2）——会话可提议把完成的流程安装为可复用技能；因为它安装未来行为，属 `PERSISTENT_AUTHORITY_TOOLS`，永远人工批准。**Persona 携带技能**（OPE-58）：persona 包的 `skills/` 目录加入加载器。

---

## 13. 桌面应用、STT、打包与 CI

### 13.1 React SPA（surfaces/gui/src）

React 18 + TypeScript 5.5 + Vite 5 + Tailwind 3。刻意朴素：**无路由、无 Redux/Zustand**——单个大根组件 `App.tsx`（约 2400 行）里全用 hooks，`api.ts`（约 2500 行）独占后端通信。主要组件（约 50 个）：`Sidebar`、`Composer`（语音输入态）、`Transcript`、`RightRail`、`PersonaView`/`PersonaHero`、`SettingsView`、`InboxView`、`IntegrationsView`、`ScheduledView`、`AuditView`、`TeamChatView`+`BoardPanel`、`ApprovalCard`/`ToolRequestCard`、`UpdateBanner`、`Onboarding`、`ProviderSetup`。

与 sidecar 通信：REST 管状态；每会话 `WebSocket /ws/session/{id}` 承载实时回合，全应用 `/ws/events` 承载推送（协议见第 4 节）。端点解析顺序：Tauri 注入的运行时全局变量 → Vite env → 开发默认 `127.0.0.1:8765`。认证：launch token 作 `X-OpenWorker-Token` 头 + `openworker` WS 子协议。

### 13.2 Tauri 壳（surfaces/gui/src-tauri）

`lib.rs`（约 900 行）刻意做"现有 React SPA 之上的薄原生窗口"：1) 选空闲端口，把 Python `openworker-server` 作为受管 sidecar 拉起（开发用 venv 路径，生产用打包的 PyInstaller onedir `binaries/sidecar/`），退出时杀掉；2) SPA 加载前注入 sidecar 地址 + 内存中的 launch token；为 sidecar 合并用户登录 shell 环境（OPE-83——`toolchain.py` 才看得见用户 PATH）；3) **托盘驻留**：关窗即隐藏，调度器与自唤醒继续跑，托盘退出才停 sidecar；4) 原生命令：目录选择、开机自启、防休眠（caffeinate）、听写（STT 桥）、更新器。

### 13.3 STT sidecar（stt/）

Rust crate `ocw-stt`（约 670 行）——本地、**离线**的语音听写：`cpal` 采麦、`whisper-rs`（whisper.cpp 绑定，CPU 路径——Metal 因 macOS 兼容推迟）、`ureq` 一次性下载模型。模型：Whisper `ggml-base.en`（约 142 MB，HuggingFace），按**精确字节数 + SHA-256** 双重钉住，带 `.verified`/`.ready` 标记文件；转写前重采样到 16 kHz。可复用的单麦克风会话管理器（后台采集线程写入共享缓冲；`stop_and_transcribe()` 返回文本），以 Tauri 命令暴露（`start_dictation`、`stop_dictation`、`get_dictation_status`）。crate 刻意与壳做依赖隔离：不依赖 Tauri/UI/剪贴板。

### 13.4 打包（packaging/）

- **macOS**（`build_dmg.sh`）：PyInstaller 打包服务端（spec：`openworker-server.spec`）→ 作为 Tauri 资源暂存并签 Mach-O → `tauri build` → `hdiutil` 包 DMG → 设 `APPLE_SIGNING_IDENTITY` 时公证 + staple。
- **Windows**（`build_windows.ps1`）：同一管线产出 NSIS `.exe` + MSI；当前未签名（README 已说明 SmartScreen 告警）。
- **自动更新**：`tauri-plugin-updater` + **minisign** 签名验证（公钥在 `tauri.conf.json`）。端点链：`download.openworker.com/latest.json` → GitHub Releases 兜底。`make_update_manifest.py` 从暂存工件按稳定名组装 `latest.json`；缺平台跳过，他系统看到"无更新"而非坏更新。GUI 预下载更新字节进内存（`download_update`），"重启更新"瞬时完成。

### 13.5 CI（.github/workflows/）

- **`ci.yml`**（push/PR）：三并行 job——`pytest`（Python 3.12）、`gui-unit`（tsc + vitest）、`gui-e2e`（Playwright chromium，封闭）。
- **`release.yml`**（`v*` 标签/手动）：三路矩阵——macOS arm64、macOS x64（原生构建；PyInstaller 无法交叉编译）、Windows——跑同一组本地构建脚本，安装器上传两份（版本名 + 稳定名，供 `releases/latest/download/...` 深链），组装的 `latest.json` 附到草稿 GitHub Release。**发布 Release 即推送更新**。

测试面：后端 127 个测试文件，含进程内 **FakeSlack** 悬架（`coworker/testing/fake_slack/`）——把真实 `SlackAdapter`/slack-bolt 栈指向假服务器，零网络的全真 Slack 测试；conftest 的 `autouse` fixture 隔离每个测试的状态目录（源于一次真实遥测泄漏事故，记录于 `tests/conftest.py`）。GUI：13 个 Vitest 单测 + **72 个封闭 Playwright e2e**（`fixtures.ts` mock 全部 `/v1` 请求并在 WS 上脚本化假 agent）+ 小型 `e2e-live/` 真服务器套件。

---

## 14. 决策日志（精选）

> 收割自代码中带日期的决策注释（`OPE-###` 规格引用、`owner ruling YYYY-MM-DD`、`*-SPEC §n`）。规格全文（OPE-*、MEMORY-SPEC、SKILLS-SPEC、github-relay-spec）不在本仓库；注释引用了它们。

### 14.1 OPE 决策

| ID | 领域 | 决策 | 出处 |
|---|---|---|---|
| OPE-27 | 引擎 | 长历史自动压缩：工具回合间检查点、边界摘要、防御式状态解析 | `compaction.py`、`engine.py` |
| OPE-51 | 交互 | 分组提问——一排按钮答不了 2+ 个问题；卡片最多携带 4 个分组条目；旧持久化字符串项仍有效 | `inbox.py:83-90`、`engine.py:1792`、`interactions.py:53` |
| OPE-58 | Persona | persona 包自带 `skills/` 目录 | `agent.py:247` |
| OPE-82 | 工具链 | PATH *解析* 与工具 *安装* 是刻意分开的两个问题 | `toolchain.py:3-7` |
| OPE-83 | 桌面 | 桌面壳在拉起时把登录 shell 的 PATH/环境传给 sidecar | `toolchain.py:7`、`src-tauri/src/lib.rs` |
| OPE-85 | 供应链 | `request_tool` 只从钉住摘要的固定目录装——取回可执行文件是供应链决策，永不自动运行；缺扫描器要*问*，不能静默丢掉需要它的检查 | `toolchain.py:18,207`、`agent.py:303` |
| OPE-93 | Persona | 连接器能力是具体集合，绝无裸"用连接器"位；加连接器的更新必须使 `capability_set` 增长并重新同意；白名单 persona 只获得白名单内连接器（最小权限） | `loading.py:30,57`、`agents/base.py:40`、`agent.py:308` |
| OPE-100 | 团队 | 看板是开放表面：`ocw` CLI + `ocw board mcp` stdio 服务器供外部 harness | `pyproject.toml` 注释、`teams/cli.py` |
| OPE-101 | 审计 | 审计成本缺陷类：保留的审计细节不随会话时长缩放 | `audit.py:66` |
| OPE-111 | 风险 | `browser_open_url` 是"换个名字"的 EGRESS；只读模式须一步可达 | `risk.py:33,80` |
| OPE-114 | 权限 | **来源底线**：运行 agent 本回合*下载*的东西强制人卡——评审员看不到这些卡，命令白名单也算数不上 | `engine.py:215,1094,1234` |
| OPE-117 | 权限 | 延迟执行写入（日后自行运行的东西——`.git/hooks`、计划任务）human-only；Discuss/Plan 模式该出卡处仍出卡 | `permissions.py:74`、`risk.py:41` |
| OPE-122 | 浏览器 | 本地文件上传只在授予根内解析 | `browser_automation.py:352` |
| OPE-124 | 浏览器 | 重定向守卫：真正加载的跳变被重新校验，而非只查初始 URL | `browser_automation.py:333` |

### 14.2 规格条款裁决

| 规格 | 裁决 | 出处 |
|---|---|---|
| github-relay-spec §2.3、§4、§8 | GitHub 入站走云中继；中继代码永不触碰 secret store；GitHub 是 Slack 之后的第二中继提供商 | `relay_client.py:69`、`cloud.py:522`、`descriptors.py:615` |
| github-relay-spec §7 | 重连时按频道上下文/轻推补回错过的消息 | `relay_client.py:371` |
| spec §1.5（2026-08-12） | 权限模式 `"auto"` 改名 `"bypass-approvals"`——名字必须说"安全关闭"，不能与 `auto-approve` 混淆 | `permissions.py:219` |
| spec §2.2 | `send_message` 按 EGRESS 闸门：模型自由文本是同一出网通道 | `risk.py:32` |
| spec §8.4 / §8.6 | 评审员 streak 语义；每审批请求一个动作 | `engine.py:29,719` |
| MEMORY-SPEC §3 | `session` 记忆范围已死——永不写入 | `memory/tools.py:77` |
| MEMORY-SPEC §4.2 | 只要接了存储就注入"何时记忆"规则 | `agent.py:69` |
| MEMORY-SPEC §5.1 | `on_saved` 钩子渲染保存通知 + 撤销 | `agent.py:207` |
| MEMORY-SPEC §6 | 用户规则凌驾学到的记忆；agent 永不能写 | `agent.py:210`、`memory/settings.py` |
| MEMORY-SPEC §7 | 超 8k 走索引模式：最新 10 条全文 + 摘要 + `memory_read` | `memory/tools.py:6` |
| MEMORY-SPEC §7.1 | agent 知道什么在会话开始即固定 | `agent.py:404` |
| SKILLS-SPEC §3 | 每会话生效技能菜单 | `agent.py:419` |
| SKILLS-SPEC §4.1 | 活技能菜单每回合重算；强制运行结果经 `_display` 拆分 | `agent.py:501`、`engine.py:288` |
| SKILLS-SPEC §5.2 | `save_skill` 是 worker 作者之门（人工批准） | `agent.py:426` |

### 14.3 Owner 裁决

| 日期 | 裁决 | 出处 |
|---|---|---|
| 2026-08-20 | 实时时钟**每回合**注入（陈旧时间戳引发错误推理） | `agent.py:482` |
| 2026-08-20 | 全局唯一读取器：整个栈共享带窗口与行号的 `read_file` | `catalog.py:76` |
| 2026-08-24 | 评审员拒绝熔断：2 → **连续 5 次** + 感知累计的 streak 语义（朴素 2 计数会无声惩罚正常来回） | `engine.py:29` |
| 2026-08-24 | `updated_at` 意为"最后处理"，绝非"最后保存" | `conversations.py:173` |
| 2026-08-24 | `_display` sidecar 跨重载存活但绝不对提供商可见 | `engine.py:181` |
| 2026-08-24 | 提问捕获的回答只作为该提问范围内的证据 | `engine.py:1846` |
| 2026-08-24 | 评审员：agent 捕获的提问与回复一同展示 | `reviewer.py:239` |

### 14.4 事故驱动决策

| 日期 | 事故 → 决策 | 出处 |
|---|---|---|
| 2026-07-28 | 模型**伪造记忆保存**（未调工具却宣称记住）→ MEMORY-SPEC 防假装保存声明 + 永远可见的保存 toast | `agent.py`、`memory/` |
| （无日期） | 测试期间真实遥测泄漏 → 每个测试的 `autouse` 状态目录隔离 fixture | `tests/conftest.py` |
| PYSEC-2026-3481/3482/3483 | MCP SDK 钉 `>=1.28.1,<2`（下限=安全修复；上限=2.0.0 移除 `streamablehttp_client`） | `pyproject.toml` |
