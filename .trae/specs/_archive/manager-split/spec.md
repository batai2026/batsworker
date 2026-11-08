# Spec：拆分 coworker/server/manager.py（建议路线第 3 条）

状态：待批准
范围：仅代码搬运与测试锚点同步；不改任何行为、签名、文档字符串；不改 app.py

## 1. 背景与已确认事实

- `coworker/server/manager.py`：**311 KB / ~6670 行 / 277 个方法**。`SessionManager`（L182-6570）独占其中，另有 12 个模块级 helper（L116-181、L6571-6670）。server 目录仅有 `app.py`(137KB)、`run.py`、`__init__.py`、`manager.py`。
- `__init__`（L183-363）登记约 40 个 `self.*` 属性（engines、task_store、scheduler、inbox、gateway、各 store），被所有职责簇交叉读写 —— 这是拆分方案选型的决定性约束。
- 外部引用面很窄：
  - coworker 内仅 3 处：`server/__init__.py:2`、`server/run.py:15`、`server/app.py:167`，均只导入 `SessionManager`。
  - scripts/ 与 surfaces/ 无 Python 级引用（GUI 走 HTTP）。
  - tests 大量 `from coworker.server.manager import SessionManager`（ mixin 拆分后零改动），仅 3 处直接导入私有 helper：
    - `tests/test_automation.py:263` → `_last_assistant_text`
    - `tests/test_readonly_grant.py:110` → `_grants_of`
    - `tests/test_inbox.py:120` → `_approval_body`
- **monkeypatch 锚点 10 处**，绑定的是 manager 模块命名空间里的名字；代码搬走后必须同步改指向新模块：

  | 位置 | 锚点 | 随哪个簇走 |
  |---|---|---|
  | tests/test_artifact_walk.py:37 | `coworker.server.manager.os.walk` | artifacts |
  | tests/test_server.py:1111 | `…manager.read_global` | mcp |
  | tests/test_server.py:1118 | `…manager.load_mcp_servers` | mcp |
  | tests/test_mcp.py:528/573/607 | `…manager.load_mcp_servers` | mcp |
  | tests/test_team_wake.py:203/246/314/481 | `…manager.get_agent` | teams |

- `tests/test_wake_resume.py:38-41` 以非绑定方式调 `SessionManager._wake_message(...)` —— mixin 继承下不受影响（沿用类属性查找）。
- 类属性级补丁（如 `SessionManager._ollama_alive`）在继承下行为不变（hasattr 沿 MRO 命中 mixin）。
- 本项目目录不是 git 仓库，回滚只能靠文件快照。
- 基线：ruff 0 errors；全量 pytest 2054 passed / 9 skipped（约 34 分钟）。

## 2. 决策

1. **Mixin 拆分，不做服务组合**。`SessionManager` 继承 13 个 mixin 类；app.py / run.py / 全部测试的 `manager.xxx()` 调用零改动，`self.*` 共享状态天然保留。组合方案需为 277 个方法写委托壳，manager.py 瘦不下来，违背本路线目标。
2. **平铺 `manager_<cluster>.py` 兄弟模块，不把 manager.py 转成 package**。更显式，无 `__init__` 再导出魔术。
3. **helpers 独立成 `manager_helpers.py`**，mixin 从它导入；manager.py 用冗余别名再导出测试在用的 3 个名字（`from .manager_helpers import _grants_of as _grants_of` 等），既保持 `from coworker.server.manager import _grants_of` 可用，又让 ruff F401 显式闭嘴。**禁止任何 mixin 从 manager.py 导入**（防循环）。
4. **代码逐字节搬运**：方法体不改逻辑、不改签名、不改注释/文档字符串；只允许新建文件的 import 块按所搬代码的实际引用组织，并从 manager.py 删去只被搬走代码使用的 import（ruff F401 把关）。行为等价由全量 pytest 背书。
5. **monkeypatch 锚点跟随代码走**：第 1 节表中 10 处测试补丁路径改指向新模块（绑定在谁名下就 patch 谁），属测试的机械同步，不改断言语义。
6. **一次搬一个簇，每步独立验证**：ruff 该簇文件 + 该簇 pytest 子集；全部簇搬完后跑全量回归对齐基线。
7. **开工先快照**：复制 manager.py 到 `.trae/specs/manager-split/manager.py.before`；全部验收通过后删除快照。
8. 不在本次：拆 app.py（另一条独立路线）、任何方法级重构/改名、`ruff format`、mypy、更新 docs/design 中对 manager.py 行号的引用（文档另行维护）。

## 3. 目标结构（模块映射表）

新文件均位于 `coworker/server/`，行号为搬迁前 manager.py 的区间（含段间注释/空行，实际以方法边界为准）：

| 新文件 | Mixin 类 | 方法来源（行区间） | 约规模 |
|---|---|---|---|
| `manager_helpers.py` | —（纯函数） | 116-181、6571-6670 | ~110 行 |
| `manager_workspaces.py` | `WorkspacesMixin` | 366-576（workspace 信任/recents/scratch/temp）、5362-5390（_extra_roots_of）、5534-5765（roots CRUD）、6078-6097（_scratch_workspace_error） | ~490 行 |
| `manager_sessions.py` | `SessionsMixin` | 781-849（checkpoints 读侧）、942-972（_emit_session_created）、1363-1368（persist_session）、4697-4731（running 标记）、5391-5533（autotitle）、5766-5839（session CRUD）、5851-5944（list_sessions/liveness） | ~450 行 |
| `manager_personas.py` | `PersonasMixin` | 888-941、973-1225（catalog/install/ trusted keys / connections 解析与视图） | ~310 行 |
| `manager_inbox.py` | `InboxMixin` | 1226-1396（4 类 inbox 交互回调 + resolve/_durable_resume）、3923-3954（set_inbox_binding）、4564-4670（mirror/_on_interaction/_resolve_inbox_reply） | ~420 行 |
| `manager_mcp.py` | `McpMixin` | 1397-1774（prepare_mcp_tools … reload_mcp） | ~380 行 |
| `manager_connectors.py` | `ConnectorsMixin` | 1775-1841、1854-1886（connector CRUD/browser/marketplace）、3800-3922（allow/disallow/slack owner）、3955-4126（_set_allowed/slack/github 状态）、4127-4275（gateway 生命周期/parked）、4741-4971（deliver/dispatch/mention） | ~1080 行 |
| `manager_teams.py` | `TeamsMixin` | 1887-2799（board/chat/tick/digest/backstop） | ~910 行 |
| `manager_artifacts.py` | `ArtifactsMixin` | 2800-3041（scan/list/read/reveal） | ~240 行 |
| `manager_providers.py` | `ProvidersMixin` | 3042-3362（web_search/providers/codex/verify）、3393-3490（ollama/curated/add/remove model）、5840-5850（provider_complete/_refresh_provider） | ~430 行 |
| `manager_settings.py` | `SettingsMixin` | 3363-3392（prefs/dm_session）、3491-3799（get_settings…set_scratch_base）、6468-6570（security store/threat/findings/sandbox 设置） | ~540 行 |
| `manager_approvals.py` | `ApprovalsMixin` | 1842-1853（list_audit）、4317-4563（approval prompt/outcome/unattended/task 授权播种） | ~260 行 |
| `manager_scheduler.py` | `SchedulerMixin` | 4671-4696（tick/resume_due_wakes）、4732-4740（_resume_wake）、4972-5327（run task/notify/automation CRUD/模板/手动运行） | ~490 行 |
| `manager_memory.py` | `MemoryMixin` | 6217-6467（memory 通知器/绑定/list/CRUD/导入导出） | ~250 行 |
| `manager_skills.py` | `SkillsMixin` | 5945-6077（list/reveal/scope/view）、6098-6216（CRUD/stage/confirm） | ~390 行 |

**manager.py 保留（核心门面，约 700-800 行）**：
- 模块 docstring；按核心代码所需裁剪后的 import 块；3 个 helper 冗余别名再导出。
- `class SessionManager(WorkspacesMixin, SessionsMixin, PersonasMixin, InboxMixin, McpMixin, ConnectorsMixin, TeamsMixin, ArtifactsMixin, ProvidersMixin, SettingsMixin, ApprovalsMixin, SchedulerMixin, MemoryMixin, SkillsMixin):`
- `__init__`（183-364）、`get_engine`（577-760）、`_attach_checkpoints`（761-780）、`_event_context`（850-873）、`_rag_config`（874-887）、WS 事件（4276-4309）、`aclose`（4310-4316）、`save`（5328-5351）、`_apply_grants`（5352-5361）。

mixin 之间互为平级、只经 `self` 互调，无 `super()` 链 → MRO 无陷阱。

## 4. 执行顺序与每步验证

严格一次一个簇（顺序即耦合度由低到高，便于早发现问题）：

| # | 步骤 | 额外改动 | 子集验证（pytest） |
|---|---|---|---|
| 1 | helpers → manager_helpers.py；manager.py 再导出 ×3 | — | test_inbox.py test_readonly_grant.py test_automation.py |
| 2 | settings(+security) | — | test_auto_approve_settings.py test_security_bundles.py test_security_workflow.py |
| 3 | memory | — | test_memory_api.py test_memory_markdown.py |
| 4 | skills | — | test_persona_skills.py -k skill |
| 5 | artifacts | test_artifact_walk.py:37 锚点改 manager_artifacts | test_artifact_walk.py |
| 6 | workspaces(+roots) | — | test_multiroot.py test_project_bindings.py |
| 7 | sessions(+checkpoints/autotitle/running) | — | test_autotitle.py test_session_events.py test_durable_resume.py |
| 8 | personas(+connections) | — | test_persona_connections.py test_connections.py |
| 9 | providers(+models/codex) | — | test_providers.py test_provider_router.py test_codex_provider.py |
| 10 | mcp | test_mcp.py ×3 + test_server.py ×2 锚点改 manager_mcp | test_mcp.py test_mcp_oauth.py test_mcp_connectors.py test_server.py |
| 11 | inbox(+bindings) | — | test_inbox.py test_interactions.py test_dm_routing.py |
| 12 | approvals(+list_audit) | — | test_approval_integrity.py test_readonly_grant.py test_ask_user_upgrades.py |
| 13 | scheduler(+automations) | — | test_automation.py test_automation_create.py test_wake_resume.py |
| 14 | teams | test_team_wake.py ×4 锚点改 manager_teams | test_team_wake.py -k team、-k board、-k journal |
| 15 | connectors(+gateway/dispatch/slack/github) | — | test_connectors.py test_connectors_allowlist.py test_mention_router.py test_message_source.py test_egress.py test_github_installs.py test_hubspot_portals.py test_openshell.py |
| 16 | 核心清理 + 门面校验 | 删除快照前完成 §5 全部验收 | 全量回归 |

每步通用动作：
1. 新建 `manager_<x>.py`：模块 docstring 一行 + 该簇所需 import + mixin 类 + 逐字节搬运的方法。
2. 从 manager.py 删除对应方法段；把 mixin 加入 `SessionManager` 基类列表；删 manager.py 中只为搬走代码服务的 import。
3. `ruff check coworker/server` → 0 errors（F401 抓多余 import，F821 抓漏 import）。
4. 跑该步子集测试（Windows 须 `dangerouslyDisableSandbox`）。

## 5. 最终验证（全量）

1. `ruff check coworker tests scripts` → 0 errors。
2. 冒烟：`python -c "import coworker.server.app, coworker.server.run; from coworker.server.manager import SessionManager, _grants_of, _approval_body, _last_assistant_text"` 无异常（兼证无循环导入）。
3. **方法守恒**：对比快照与现状，SessionManager 可见方法集合完全一致（含私有方法；脚本见 tasks.md 收尾步）。
4. 全量 `pytest tests -q`（dangerouslyDisableSandbox）→ **2054 passed / 9 skipped**，与基线一致。
5. 行数确认：manager.py ≤ ~1200 行；13 个 manager_*.py + manager_helpers.py 就位。
6. 删除 `.trae/specs/manager-split/manager.py.before`。

## 6. 风险与回滚

- **搬运遗漏/重复** → 方法守恒检查（§5.3）+ ruff F811/F401 双保险。
- **补丁锚点漏改** → 对应子集测试立刻红，不会静默漏过。
- **循环导入** → 决策 3 禁止 mixin 反向导入 manager；冒烟 import（§5.2）兜底。
- **隐式属性依赖断裂**（mixin 用了 __init__ 未建的属性）→ 属行为变更范畴，本次不引入；全量回归背书。
- **回滚**：任何一步失败，删除已建的新模块、用快照还原 manager.py 即可回到起点；每步独立，无需级联回滚。

## 7. 验收标准

- manager.py 降为纯门面 + 核心装配（≤ ~1200 行），13 个职责模块 + 1 个 helpers 模块就位。
- `from coworker.server.manager import SessionManager`、`from coworker.server import SessionManager` 及 3 个私有 helper 的导入路径全部不变。
- ruff 0 errors；全量 pytest 不低于基线（2054 passed / 9 skipped）。
- 除上表 10 处锚点外，tests/ 零改动。
