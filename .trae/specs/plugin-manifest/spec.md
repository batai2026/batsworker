# Spec：统一 PluginManifest 适配层（建议路线 P1-3）

状态：待批准
范围：在 persona / skill / connector 插件 / MCP server 四类现有扩展之上加一层统一 `PluginManifest` 适配，复用 `personas/manifest.py` 的解析模式与 `personas/signing.py` 的 minisign 校验，安装/更新/卸载走同一套 consent diff。**只做适配层，不重写四套现有系统**。不做 Claude Code / 外部 MCP / 外部 SKILL 导入（留作后续）。

## 1. 背景与已确认事实

- 四类扩展各有独立的格式、加载器与生命周期（见上表）。Persona 是唯一有完整「签名 + consent_summary + capability_set diff + disabled-until-consent」的系统。
- `personas/manifest.py` 已有 `parse_manifest()`（YAML frontmatter + md body）、`PersonaManifest`、`ManifestError` 严格校验；`personas/signing.py` 已有 minisign 兼容的目录签名（`manifest.sha256` + `.minisign`）与 `verify_directory()`。
- `personas/loading.py` 已有 `consent_summary(m: PersonaManifest)` 与 `capability_set(m)`（扁平可比对集合，用于判断 update 是否能力增长 → 重新 consent）。
- `personas/registry.py` 的 `install_from_dir/zip/git` 已有 snapshot + 签名校验 + consent + `_replaces_of`（capabilities_grew 判定）模式。
- `skills/store.py` 有 stage→confirm 上传但无签名/consent/能力集；`skills/base.py` 的 `_parse_skill` 解析 SKILL.md（name/description/allowed-tools）。
- `connectors/marketplace.py` 有 community plugin 的 install/uninstall（JSON → `ConnectorDescriptor`），无签名/consent/能力集。
- `mcp/config.py` 的 `MCPServerDef` 是全局 + workspace JSON 合并，原生 CRUD（`put_global_server` 等），无签名/consent/能力集；`requires_approval` 是唯一的权限位。
- 工具 `catalog.py` 是平台封闭的能力注册表（第三方不可加），**不算扩展**，不在统一范围内。

## 2. 决策

1. **适配层而非重写**：新增 `coworker/plugins/` 包，定义统一 `PluginManifest` dataclass 与四个 adapter（`PersonaAdapter` / `SkillAdapter` / `ConnectorPluginAdapter` / `MCPAdapter`），每个 adapter 把原生对象映射成 `PluginManifest`。四套现有加载/存储代码**不动**，adapter 只做读侧投影 + 统一 install 委托。
2. **统一字段**：`PluginManifest(kind, id, name, version, deps, capability_set, permissions, signature, source, description)`。`kind ∈ {"persona","skill","connector","mcp"}`。`capability_set: set[str]` 是扁平可比对集合（同 persona 的 `capability_set` 思路）。`permissions: dict` 承载各 kind 的权限语义（persona: connectors grant + default_mode; skill: allowed_tools; connector: requires_approval; mcp: requires_approval + include/exclude_tools）。
3. **capability_set 命名空间**：`tool:<id>` / `mcp:<name>` / `connector:<name>` / `skill:<name>` / `messaging` / `team:<role>`。各 adapter 按 kind 填充；未知/不适用的不填。
4. **签名复用 signing.py**：四类扩展的 bundle（persona 目录、skill 目录、connector plugin 目录、MCP server 暂不签——它是 JSON 配置不是 bundle）都可用 `verify_directory()` 校验 `manifest.sha256` + `.minisig`。persona 已走这条路径；skill / connector plugin 新增可选签名；MCP 第一阶段不签（配置型，风险由 `requires_approval` + workspace trust gate 兜底）。
5. **统一 consent_summary**：`plugins.consent.consent_summary(pm: PluginManifest) -> dict`，按 kind 分支映射到统一输出（`id/name/kind/version/capability_set/risks/permissions/signature/source`）。UI 侧可复用 persona 的 consent 卡片。
6. **统一 install/update 门面**：`plugins.lifecycle.install(kind, source, ...) -> consent_summary` 与 `update(...)`。内部：解析 → adapter → `verify_directory`（若有签名）→ 计算 `capability_set` → 对已存在的同类 id 做 `capabilities_grew` diff → 委托给原生 store（persona→`registry.install_from_dir`、skill→`SkillStore.create/update`、connector→`marketplace.install_plugin`、mcp→`put_global_server`）。**更新若能力增长则返回 `requires_consent: true`，调用方（GUI/API）必须走 consent 后再落地**——与 persona 现有 `_replaces_of` 语义一致。
7. **不做**：Claude Code 插件导入、外部 MCP plugin 格式导入、外部 SKILL.md 生态格式导入、GUI 改造（后端 API 先出，UI 留作后续）、重写四套原生系统、为 MCP 加签名（配置型不适用）。
8. **fail-closed**：签名校验失败（pinned 安装）拒绝安装；能力集增长的更新不自动落地；未知 kind 拒绝。

## 3. 数据结构

```python
# coworker/plugins/manifest.py
@dataclass(frozen=True)
class PluginManifest:
    kind: str               # "persona" | "skill" | "connector" | "mcp"
    id: str
    name: str
    version: str = ""
    deps: list[str] = field(default_factory=list)      # 预留：依赖的其他插件 id
    capability_set: frozenset[str] = field(default_factory=frozenset)
    permissions: dict[str, Any] = field(default_factory=dict)
    signature: dict[str, Any] = field(default_factory=dict)  # signing.verify_directory 结果
    source: Optional[str] = None                       # path / url
    description: str = ""
```

Adapter 协议（`coworker/plugins/adapters/base.py`）：
```python
class PluginAdapter(Protocol):
    kind: str
    def to_manifest(self, native) -> PluginManifest: ...
    def capability_set(self, native) -> frozenset[str]: ...
    def permissions(self, native) -> dict: ...
```

四个 adapter 各自实现：
- `PersonaAdapter`：native = `PersonaManifest`；`capability_set` 直接复用 `personas.loading.capability_set()`；`permissions = {"connectors": ..., "default_mode": ..., "messaging": bool, "team": ...}`。
- `SkillAdapter`：native = `Skill`（来自 `_parse_skill`）；`capability_set = {f"skill:{name}"}`；`permissions = {"allowed_tools": [...]}`。
- `ConnectorPluginAdapter`：native = plugin dict（marketplace 格式）；`capability_set = {f"connector:{name}"}`；`permissions = {"auth": ..., "two_way": bool, "mcp_backed": bool}`。
- `MCPAdapter`：native = `MCPServerDef`；`capability_set = {f"mcp:{name}"}`；`permissions = {"requires_approval": bool, "include_tools": ..., "exclude_tools": ..., "transport": ...}`。

## 4. 统一 API 形态

```python
# coworker/plugins/lifecycle.py
def install(kind: str, source, *, expected_pubkey=None, **kwargs) -> dict:
    """返回 consent_summary + requires_consent（首次安装或能力增长时为 True）。
    落地交给原生 store；若 requires_consent 为 True，调用方须在用户批准后再调用 commit_install。"""

def update(kind: str, source, *, expected_pubkey=None, **kwargs) -> dict:
    """同 install，但对已存在 id 做 capability_set diff；能力增长 → requires_consent:True。"""

def uninstall(kind: str, id: str) -> dict:
    """委托给原生 store 的 uninstall/delete。"""

# coworker/plugins/consent.py
def consent_summary(pm: PluginManifest) -> dict:
    """统一 consent 输出（id/name/kind/version/capability_set/risks/permissions/signature/source）。"""

def capability_diff(old: PluginManifest, new: PluginManifest) -> dict:
    """{grew: set, shrank: set, same: set} — grew 非空即需重新 consent。"""
```

## 5. 精确改动清单

### 5.1 新增 `coworker/plugins/` 包

- `manifest.py`：`PluginManifest` dataclass + `PluginKind` 常量。
- `adapters/base.py`：`PluginAdapter` Protocol。
- `adapters/persona.py`：`PersonaAdapter`。
- `adapters/skill.py`：`SkillAdapter`。
- `adapters/connector.py`：`ConnectorPluginAdapter`。
- `adapters/mcp.py`：`MCPAdapter`。
- `consent.py`：`consent_summary(pm)`、`capability_diff(old, new)`。
- `lifecycle.py`：`install/update/uninstall` 门面（委托原生 store + 签名 + diff）。
- `__init__.py`：导出 `PluginManifest`、`consent_summary`、`install/update/uninstall`、各 adapter。

### 5.2 复用而非修改

- `personas/signing.py`：直接 `verify_directory()`，不改。
- `personas/loading.py`：`capability_set()` 被 `PersonaAdapter` 直接调用，不改。
- 四套原生 store（`PersonaRegistry` / `SkillStore` / `marketplace` / `mcp.config`）：`lifecycle.py` 只调用其现有 install/update/uninstall 方法，不改。

### 5.3 可选：skill / connector plugin 的签名接线

`lifecycle.install` 在调用原生 store 前，若 source 是目录且含 `manifest.sha256`，调用 `verify_directory()`，结果写入 `PluginManifest.signature`。persona 已在 `registry.install_from_dir` 内部做了；skill / connector plugin 由 `lifecycle` 统一做（不侵入原生 store）。

## 6. 验证

1. 静态：`ruff check coworker/plugins/` → 0 errors。
2. 单测 `tests/test_plugin_manifest.py`：
   - 四个 adapter 各把一个 native 对象映射成 `PluginManifest`，字段正确。
   - `capability_diff` 对 grew/shrank/same 的判定正确。
   - `consent_summary` 对四种 kind 输出结构一致。
   - `install` 首次 → `requires_consent:True`；同能力 `update` → `requires_consent:False`；能力增长 `update` → `requires_consent:True`。
   - 签名目录 `verify_directory` 结果写入 `signature` 字段。
3. 回归：现有 `tests/test_personas*.py`、`tests/test_skills*.py`、`tests/test_marketplace*.py`、`tests/test_mcp*.py` 不受影响（适配层只加不改）。
4. 全量 `pytest tests -q` → 不低于基线（2064/9 + 此前新增）。

## 7. 风险与回滚

- 风险：统一 `capability_set` 的命名空间可能漏掉某些 kind 的细粒度能力——第一阶段只覆盖最外层（`tool/mcp/connector/skill/messaging/team`），细粒度留后续迭代。
- 风险：`lifecycle` 委托原生 store 时，各 store 的「disabled-until-consent」语义不一致（persona 有，其余没有）——第一阶段 `requires_consent` 只作为返回信号，不强制阻塞原生落地（由调用方决定），避免破坏现有 skill/mcp 的即时安装体验。
- 回滚：`coworker/plugins/` 是新增包，删除即回退；不修改任何现有文件。

## 8. 验收标准

- `coworker/plugins/` 包可导入，四个 adapter 能把现有 native 对象映射成统一 `PluginManifest`。
- `consent_summary` / `capability_diff` 对四种 kind 行为正确。
- `install/update/uninstall` 门面能委托到四类原生 store 并正确返回 `requires_consent`。
- 现有四类扩展的测试全部通过（无回归）。
- 全量回归通过。
