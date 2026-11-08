# P2-6 Harness 注册表 — 「模式即组合」的单一来源

## 1. 背景与问题

平台已经有 persona manifest（`tools:` 引用 CATALOG 能力槽，body 即系统提示，`default_permission_mode`
声明权限模式），运行时 preset 就是这份 manifest。但评测侧另有一份硬编码：

- `coworker/eval/matrix.py` 的 `HARNESSES` 独立声明 `capabilities / system_prompt / mode`；
- `_MINIMAL_PROMPT`、`_CREATOR_PROMPT`、`_CODE_MODE_PROMPT` 与三份内置 manifest 的正文是同一语义的
  两份文本，已经漂移（minimal 正文多了「失败两次就停」与「说明未验证部分」两条规则）；
- 用户通过 manifest 定义的 persona 无法直接作为 harness 评测；
- 评测口径与运行时口径长期分叉，历史报告的可比性依赖人工对齐。

## 2. 目标

harness（能力槽集合 + 系统提示 + 权限模式 + 文件夹门）只从一个来源派生——persona manifest
（`code` 因无 manifest，取代码内建的 `CODE_CAPABILITIES / CODE_INSTRUCTIONS`）。评测矩阵、CLI
与后续「用户可组合 harness」共用同一注册表。

## 3. 方案

### 3.1 新模块 `coworker/personas/harness.py`

```python
@dataclass(frozen=True)
class HarnessSpec:
    name: str
    capabilities: tuple[str, ...]
    system_prompt: str
    mode: str = "interactive"
    requires_folder: bool = True
    source: str = "manifest"   # "manifest" | "builtin"


def from_manifest(m: PersonaManifest) -> HarnessSpec
def builtin_specs() -> dict[str, HarnessSpec]   # minimal / code / creator / code-mode
def installed_specs(registry=None) -> dict[str, HarnessSpec]  # 内置 + 所有已装 manifest persona
def resolve(name, *, specs=None) -> HarnessSpec  # 找不到时抛 ValueError("unknown harness: ...")
```

- `from_manifest`：`capabilities = tuple(m.tools)`、`system_prompt = m.system_prompt`、
  `mode = m.default_permission_mode`、`requires_folder = m.requires_folder`。
- 内置三档（minimal / creator / code-mode）从 `personas/builtin/<id>/manifest.md` 解析；
  `code` 取 `CODE_CAPABILITIES / CODE_INSTRUCTIONS`，`source="builtin"`。
- `installed_specs` 在 builtin 之上叠加所有 manifest 型 persona（代码内建 persona 无 manifest，跳过）。
  注册表构造失败不阻断内置 harness（回退到 `builtin_specs()`）。

### 3.2 `coworker/eval/matrix.py` 改为派生

- 删除 `_MINIMAL_PROMPT / _CREATOR_PROMPT / _CODE_MODE_PROMPT` 与手写 `HARNESSES`，
  `HARNESSES = builtin_specs()`（保持 4 个键，测试与 CLI 兼容）。
- `_harness_agent(spec)` 使用 `spec.requires_folder`，不再硬编码 `True`。
- `run_matrix(..., harness_specs=None)`：默认 `installed_specs()`（含用户已装 persona），
  也允许测试显式注入。未知 harness 报 `ValueError("unknown harness: ...")`。
- CLI `--harnesses` 校验同一注册表；报告新增 `harness_sources`（`{name: "manifest"|"builtin"}`）。
  帮助文案更新为「任意已安装 persona id」。

### 3.3 已知口径变化（记录在案）

- `minimal / creator / code-mode` 的提示文本改为 manifest 正文；与 P1-4 历史报告不可逐字节同比。
- `code` 的能力集改取 `CODE_CAPABILITIES`（当前含 `egress`，旧硬编码为 5 项）。
  这是刻意对齐运行时口径；测试 golden 以 `CODE_CAPABILITIES` 为准。
- `requires_folder` 改为按 manifest；四档内置均为 `true`，与旧行为等价。

## 4. 不做

- 不改引擎、不改安全语义、不改 GUI；
- 不动 `code_mode` 能力槽（形态 B 沙箱 + 审批门桥接保持原样）；
- 不引入「在 Settings 里自定义 harness」的 UI（后续条目）。

## 5. 测试

- `tests/test_harness_registry.py`
  - 四档内置：能力集 / 提示来源 / mode / requires_folder 正确；
  - `minimal` 提示等于 manifest 正文，`code` 提示等于 `CODE_INSTRUCTIONS`；
  - 临时装一个自定义 persona（`tools: [code_files, shell]`）→ `installed_specs` 含它且可解析；
  - `resolve("nope")` 抛错；`source` 标记正确；
  - 自定义 spec 注入 `run_matrix`，stub 跑通（`harness_names=["custom"]`）。
- `tests/test_eval_matrix.py` 调整：`HARNESSES` 仍为 4 档；code-mode 提示断言改为 manifest 语义
  （「ONE Python program / exec」），不再断言旧常量的围栏示例。

## 6. 验收

- `ruff check coworker tests` 全绿；
- 目标用例全绿；
- 全量回归 ≥ 2126 passed / 9 skipped（基线：形态 B 提交后）。

## 7. 回滚

删除 `personas/harness.py`，恢复 `matrix.py` 的常量与 `HarnessSpec` 定义即可（单文件级回滚）。
