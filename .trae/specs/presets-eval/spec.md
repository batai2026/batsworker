# Spec：Minimal / Creator preset + 模型×harness 评测（建议路线 P1-4）

状态：待批准
范围：两个内置 preset（Minimal / Creator）+ 录制轨迹驱动的「模型 × harness」离线评测脚本 + Creator 所需的能力槽只读 API。不做 GUI、不做 Code mode（建议 5 单独立项）。

## 1. 背景与已确认事实

- Preset 载体 = 内置 persona manifest：`coworker/personas/builtin/<id>/manifest.md`（YAML frontmatter + md body）。`ships: true`（默认）会进 release；`team != "worker"` 默认 surfaced，出现在新会话选择器（[registry.py](file:///e:/tworkspace/openworker-0.2.1/coworker/personas/registry.py#L205-L212)）。
- `PersonaManifest` 字段与语义见 [manifest.py](file:///e:/tworkspace/openworker-0.2.1/coworker/personas/manifest.py)：`id/name/tools/mcp/connectors/messaging/team/requires_folder/subagents/scheduling/recommended_models/default_permission_mode/description`。
- 能力槽 = 平台封闭的 `CATALOG`（[catalog.py](file:///e:/tworkspace/openworker-0.2.1/coworker/catalog.py#L146-L213)），现有 8 个 id：`code_files/files/git/search/shell/todo/security/egress`，每个带 name/description/risk/requires。`expand(ids, ctx)` 把 id 展开成具体工具。
- 权限模式：`discuss / plan / interactive / auto-approve`（[permissions.py](file:///e:/tworkspace/openworker-0.2.1/coworker/permissions.py#L226-L262)）。
- 引擎构建入口 `build_engine(agent=..., workspace=..., model=..., mode=..., provider=..., messages=...)`（[agent.py](file:///e:/tworkspace/openworker-0.2.1/coworker/agent.py#L190-L707)）；`engine.run(user_input)` 是 async iterator（[engine.py](file:///e:/tworkspace/openworker-0.2.1/coworker/engine.py#L365-L371)）。
- 轨迹账本 `TrajectoryStore(base_dir).read(session_id, limit=...)` 返回 `kind` 记录；`model_request.data` 含 `digest / bytes / model / tools / roles`（[trajectory.py](file:///e:/tworkspace/openworker-0.2.1/coworker/trajectory.py#L157-L183)）；`turn_start.data.message_count` 是 turn → 消息下标映射（fork/replay 已用它）。`engine.trajectory` 由 manager 注入，`build_engine` 不注入——评测需手动挂 `SessionTrajectory`。
- 会话消息落盘 `conversations/<sid>.jsonl`，`content_to_text` 可抽文本（[conversations.py](file:///e:/tworkspace/openworker-0.2.1/coworker/conversations.py#L51-L59)）。
- 评测脚本先例 `scripts/eval_reviewer.py`：`argparse` + `--stub`（无网络）+ 一个 pytest 只测脚手架。本次照抄该模式。
- 服务端测试模式：`SessionManager(workspace=tmp_path, provider=ScriptedProvider(turns))` + `TestClient(create_app(manager))`（tests/test_server.py）。

## 2. 决策

1. **Minimal = 最小可用面 + 只有 shell 与文件编辑**：tools 固定 `["code_files", "shell"]`，`requires_folder: true`，`subagents/scheduling/messaging/connectors` 全关，`team: null`。系统提示刻意最短（只讲工具用法与验证），避免 prompt 本身给模型额外策略，污染 harness 对比。`default_permission_mode: interactive`（不默认绕过审批）。
2. **Creator = 创作面**：tools `["code_files", "search", "shell", "todo"]`，`requires_folder: true`，`subagents: true`。系统提示给出创作流程：读现有 manifest/能力槽 → 写 bundle → **dry-run（consent 预览）** → 用户批准后再安装；强调能力槽是平台封闭的，第三方只能引用不能新增。
3. **能力槽只读 API 供 Creator 实时查看**：`GET /v1/plugins/slots` 返回 catalog 全部能力槽 + 四类已安装扩展的统一 `PluginManifest` 投影（复用 P1-3 的 adapter）。纯读，无副作用。
4. **dry-run 预览 API**：`POST /v1/plugins/preview` body `{"kind": "...", "source"|"name": "..."}`，内部走 `plugins.install(..., commit=False)`（不上盘），返回统一 consent 摘要 + `requires_consent`。persona/skill 用 `source` 目录；connector/mcp 用 `name` 查现有定义。
5. **评测矩阵 = 录制轨迹 × 模型 × harness**：
   - 录制侧：`recorded_turns()` 用 trajectory 的 `turn_start.message_count` 定位每 turn 的用户消息下标，从 `conversations/<sid>.jsonl` 取原始文本（**不脱敏**，保证输入完整）。
   - 执行侧：每 cell = `build_engine(agent=harness_agent(caps, prompt), model=..., mode=..., provider=...)` + 挂临时 `SessionTrajectory`，跑该 turn 的输入，收集 `status / model_request digest / bytes / 工具调用次数 / 迭代数 / 耗时 / error`。
   - harness 内置三档：`minimal`（`code_files+shell`）、`code`（`code_files+git+search+shell+todo`）、`creator`（`code_files+search+shell+todo`）。
   - `--stub` 用确定性假 provider（无网络、无密钥），供 CI 测试脚手架；真实跑按 `--models` 走现有 `providers.router`。
6. **输出可比较**：报告 JSON 每 cell 一行，`digest` 相同即「模型在 harness 下的出站负载逐字节一致」；矩阵按 `(harness, turn)` 分组，便于横向比较模型。
7. **不做**：GUI 面板、Code mode、自动批量回放（已有 replay API）、修改 CATALOG、为评测引入新依赖。
8. **fail-closed**：预览/槽位 API 拒绝 `__` 前缀内部会话；preview 的 `kind` 不在四类内 → 400；persona/skill 目录缺 manifest → 400；评测脚本无 key 且非 `--stub` → 明确报错退出，不静默跳过。

## 3. 精确改动清单

### 3.1 新增 `coworker/personas/builtin/minimal/manifest.md`

frontmatter：
```yaml
id: minimal
name: Minimal
icon: code
tagline: 最小 harness — 仅 shell 与文件编辑，用于模型基准对比
requires_folder: true
tools: [code_files, shell]
recommended_models: [anthropic:claude-opus-4-8, openai:gpt-5.6-sol]
default_permission_mode: interactive
description: 只保留 shell 与文件编辑的最小工作面，用于在同一 harness 下横向对比不同模型。
```
body：短提示——先用 read_file/grep 看清现状，改动最小，改完用 run_shell 跑最窄的验证；不要额外策略。

### 3.2 新增 `coworker/personas/builtin/creator/manifest.md`

frontmatter：
```yaml
id: creator
name: Creator
icon: wand
tagline: 创作与调试 coworker / skill / connector 插件
requires_folder: true
subagents: true
tools: [code_files, search, shell, todo]
recommended_models: [anthropic:claude-opus-4-8, openai:gpt-5.6-sol]
default_permission_mode: interactive
description: 创建一个 coworker、skill 或 connector 插件：先看能力槽与现有清单，再写 bundle，dry-run 预览后安装。
```
body：创作流程 + 能力槽封闭规则 + dry-run 先于 install + 不自我授权（不改 risk overrides）。

### 3.3 新增 `coworker/eval/__init__.py` + `coworker/eval/matrix.py`

```python
@dataclass(frozen=True)
class HarnessSpec:
    name: str
    capabilities: tuple[str, ...]
    system_prompt: str
    mode: str = "interactive"

HARNESSES: dict[str, HarnessSpec]   # minimal / code / creator

@dataclass
class RecordedTurn:
    turn: int
    input_text: str

def recorded_turns(conv_dir, trajectory_dir, session_id) -> list[RecordedTurn]
async def run_matrix(*, turns, models, harness_names, workspace, provider_factory, out_path=None) -> dict
def main(argv=None) -> int      # argparse: --session --conv-dir --trajectory-dir --models --harnesses --stub --out --max-turns
```

执行实现要点：
- `agent = Agent(name=h.name, title=h.name, system_prompt=h.system_prompt, tool_factory=lambda c: expand(list(h.capabilities), c), requires_folder=True)`
- `engine = build_engine(agent=agent, workspace=workspace, model=model, mode=Mode(h.mode), provider=provider)`
- 临时账本：`engine.trajectory = SessionTrajectory(TrajectoryStore(tmp), f"eval-{cell}")`
- `async for _ in engine.run(t.input_text): pass`，异常捕获进 `error`
- 从临时账本 `read()` 取 `kind == "model_request"` 记录拿 `digest/bytes/tools`
- `--stub` provider：按 harness 返回一条固定 assistant 文本（不调工具），确保同一 cell 的 digest 稳定

### 3.4 `coworker/server/manager_plugins.py`（新增 mixin）+ `manager.py` 挂载

```python
def plugin_slots(self) -> dict[str, Any]:
    # {"capabilities": [...catalog 槽位...], "plugins": {"persona": [...], "skill": [...],
    #  "connector": [...], "mcp": [...]}}  每项为 PluginManifest 投影 + consent_summary 子集
def preview_plugin(self, kind: str, *, source: str | None = None, name: str | None = None) -> dict[str, Any]:
    # 400/404 分支；内部 plugins.install(commit=False)
```

数据来源：`self.personas`（PersonaRegistry）、`self._skill_loader`/skills store、`connector_registry`/marketplace、`mcp.config` 的全局 servers。

### 3.5 `coworker/server/app.py`

紧跟既有 `/v1/plugins` 或无冲突位置加两条路由：
- `GET /v1/plugins/slots` → `manager.plugin_slots()`
- `POST /v1/plugins/preview`（Body JSON：`kind` + `source`|`name`）→ 错误映射 400/404

### 3.6 测试

- `tests/test_eval_matrix.py`：`recorded_turns` 用伪造 trajectory JSONL + conversations JSONL 验证切割点；`HARNESSES` 能力集正确；`run_matrix` 用 stub provider 跑 2 模型 × 1 harness，断言每 cell 有 `status/digest/bytes`，且同 stub 的 digest 稳定；缺 key 非 stub 抛错。
- `tests/test_plugin_slots.py`：`plugin_slots()` 返回 catalog 8 个槽位 + 四类插件数组；`preview_plugin` 对 persona 目录返回 `requires_consent=True`、未知 kind→400、缺目录→404；两条 HTTP 路由 happy path 与错误码。
- `tests/test_builtin_presets.py`（或在既有 personas 测试内追加）：`minimal` 加载后 tools == `["code_files","shell"]` 且 `subagents/messaging/connectors` 为假；`creator` 的 tools 与 surfaced 标记正确。

## 4. 验证

1. `ruff check coworker/eval coworker/server/manager_plugins.py coworker/server/app.py tests/test_eval_matrix.py tests/test_plugin_slots.py` → 0 errors。
2. 目标：`pytest tests/test_eval_matrix.py tests/test_plugin_slots.py tests/test_personas*.py tests/test_server.py -q`（Windows 加 `dangerouslyDisableSandbox`）。
3. `python -m coworker.eval.matrix --help` 冒烟；`--stub` 端到端跑通并生成报告 JSON。
4. 全量 `pytest tests -q` → 不低于 2098 passed / 9 skipped（新增用例另计）。

## 5. 风险与回滚

- 风险：评测跑真模型会产生费用与耗时——脚本默认不跑，必须显式 `--models`；`--stub` 免费可测。
- 风险：`preview` 对 persona/skill 目录做解析，恶意目录可能触发解析异常——统一 try/except → 400，不抛 500。
- 风险：`plugin_slots` 聚合四类数据源，某个源不可用时整体失败——逐类 try/except，失败项记 `error` 字段并继续。
- 回滚：两个 persona 目录 + `coworker/eval/` 包 + 两条只读路由，删除即回退；不改 CATALOG、不改引擎。

## 6. 验收标准

- `minimal` / `creator` 出现在新会话选择器（ships + surfaced），tools 与 manifest 一致。
- `GET /v1/plugins/slots` 返回 8 个能力槽 + 四类插件清单。
- `POST /v1/plugins/preview` 对 persona 目录返回统一 consent 摘要，不落盘。
- `python -m coworker.eval.matrix --stub --session <id>` 能对录制会话产出「模型 × harness」报告。
- 现有测试无回归，全量 ≥ 2098 passed / 9 skipped。
