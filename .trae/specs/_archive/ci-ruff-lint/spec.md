# Spec：CI 引入 ruff 静态检查（建议路线第 2 条）

状态：待批准
范围：仅 lint 配置、lint 违规修复、CI 接线；不改业务行为，不做 `ruff format`

## 1. 背景与已确认事实

- 仓库当前**没有任何 ruff 配置**（无 `ruff.toml` / `.ruff.toml` / `setup.cfg` / `tox.ini`），CI 也**没有 lint 步骤**。
- ruff 已装入 `.venv`（0.17.3）。按项目 `requires-python = ">=3.10"`，`target-version` 取 `py310`。
- 显式默认规则集 `ruff check coworker tests scripts --select E4,E7,E9,F`：**135 errors，98 可自动修复**：

| 规则 | 数量 | 说明 |
|---|---|---|
| F401 | 86 | unused-import，绝大多数可安全自动删除 |
| E402 | 14 | 集中在 engine.py / permissions.py / app.py，属有意延迟导入 |
| F811 | 11 | 函数内重复导入，可自动删顶部冗余导入 |
| F821 | 10 | 4 处真问题需人工修，其余为字符串注解 + 局部导入 |
| F841 | 8 | 未使用局部变量；`[*]` 的自动修，其余改名 `_x` |
| E731 | 4 | 测试内 lambda 赋值，改 `def` |
| E741 | 2 | 变量名 `l`，改 `label` |

- 已逐处核实的关键结论：
  - `coworker/connectors/browser_automation.py:727` 的 `state_dir` 是**真实运行时 NameError**（同文件 688/698 均在函数内局部导入，唯独此处缺失）。
  - `browser_automation.py:639`、`personas/signing.py:295`、`tools/sandbox.py:452` 的 `Dict` 未导入；三文件都有 `from __future__ import annotations`，注解惰性求值**不会运行时崩**，但 `typing.get_type_hints` 会失败，属于应修的类型卫生问题。
  - `coworker/server/manager.py:841`、`tests/test_shadow_eval.py:226` 是**字符串注解 + 函数内导入**，整改后加 `# noqa: F821`，不改运行结构。
  - `tests/test_mcp_connectors.py:250/273` 直接用 3.11+ 内建 `ExceptionGroup`，而 `requires-python` 为 3.10。
- `.github/workflows/ci.yml` 现有 5 个 job：`pytest`、`pytest-windows`、`gui-unit`、`gui-unit-windows`、`gui-e2e`；新增独立 `lint` job。

## 2. 决策

1. **规则集从窄**：只启用 `E4/E7/E9/F`，不引入 SIM/B/RUF/S/PLR 等扩展集（那会产生 1753 条，属另一个独立工程）。
2. **不做 `ruff format --check`**：全仓格式化会产生大面积无关 diff，破坏可审阅性；若需要，另开 spec。
3. **E402 用 per-file-ignore，不搬 import**：三处均为模块级常量/延迟导入布局，搬动有循环导入风险；waiver 仅限这三个文件，其余文件 E402 仍然报错。
4. **字符串注解的 F821 用行内 `# noqa: F821`**，并写明原因；不为此调整导入结构。
5. **F401 自动修复的副作用风险由全量 pytest 兜底**：`coworker/agent.py:47`、`rag.py:296`、`skills/store.py:31` 等导入可能带注册副作用，必须用全量测试验证，不能只靠 ruff 退出码。
6. **CI 独立 job**：`lint` 不安装项目依赖（ruff 不 import 代码），保持秒级反馈。

## 3. 精确改动清单

### 3.1 `pyproject.toml`

在 `[project.optional-dependencies]` 的 `dev` 数组追加 ruff：

```toml
dev = ["pytest>=8", "pytest-asyncio", "httpx", "ruff>=0.17.3"]
```

在 `[tool.pytest.ini_options]` 之前新增：

```toml
[tool.ruff]
# Ruff's pyflakes default set is pinned explicitly so a future ruff release that
# widens its defaults doesn't turn CI red for unrelated style rules.
target-version = "py310"

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F"]

[tool.ruff.lint.per-file-ignores]
# Deliberate late imports: these modules place imports after module-level
# constants (and permissions.py imports .risk after its helpers) to avoid
# import cycles. Moving them up changes import order; waived instead.
"coworker/engine.py" = ["E402"]
"coworker/permissions.py" = ["E402"]
"coworker/server/app.py" = ["E402"]
```

### 3.2 自动修复（98 条）

执行：

```
.\.venv\Scripts\ruff.exe check coworker tests scripts --fix
```

覆盖 F401（86）、F811（11，删重复导入）、可修的 F841。执行后逐文件 `git diff` 复核，重点检查：`coworker/agent.py`、`coworker/rag.py`、`coworker/skills/store.py`、`coworker/connectors/relay_client.py`（疑似注册/副作用导入）。

### 3.3 人工修复（37 条）

#### A. 真 bug / 类型卫生（4 处 F821）

| 文件 | 位置 | 改动 |
|---|---|---|
| `coworker/connectors/browser_automation.py` | L639 | 返回注解 `Dict[str, Any]` → `dict[str, Any]` |
| `coworker/connectors/browser_automation.py` | L726-727 | 函数体首行补 `from ..secrets import state_dir`，与 L688/L698 写法一致 |
| `coworker/personas/signing.py` | L44 | `from typing import Optional` → `from typing import Any, Optional` |
| `coworker/personas/signing.py` | L295 | `out: Dict[str, Any]` → `out: dict[str, Any]` |
| `coworker/tools/sandbox.py` | L452 | `result: Dict[str, Any]` → `result: dict[str, Any]` |

#### B. 字符串注解（2 处 F821）

| 文件 | 位置 | 改动 |
|---|---|---|
| `coworker/server/manager.py` | L841 | 行尾加 `# noqa: F821`（注解后局部导入，运行安全） |
| `coworker/server/manager.py` | L824 | 从 `from ..checkpoints import ...` 中删除未使用的 `SessionCheckpoints` |
| `tests/test_shadow_eval.py` | L226 | 行尾加 `# noqa: F821`（同上，函数内导入） |

#### C. 测试注解 / 3.10 兼容（2 处 F821 + F401）

**`tests/test_compaction_quality.py`**
- 顶部导入区补 `from coworker.providers.base import AssistantTurn`。
- 删除 L99 测试函数内的 `AssistantTurn` 局部导入（模块级导入已覆盖）。
- L134 `notice = ...` → `_notice = ...`（调用有副作用，保留表达式）。

**`tests/test_mcp_connectors.py`**（stdlib 导入区之后插入）

```python
try:  # Python 3.10 has no builtin ExceptionGroup; pytest installs the backport there.
    from builtins import ExceptionGroup
except ImportError:  # pragma: no cover - CI runs on 3.11+
    from exceptiongroup import ExceptionGroup
```

#### D. E402（14 处）

仅由 3.1 的 per-file-ignores 覆盖，不改代码。

#### E. E731（4 处 → `def`）

| 文件:行 | 改法 |
|---|---|
| `tests/test_code_tools.py:80` | `run = lambda *a: subprocess.run([...])` → `def run(*a): return subprocess.run([...])`（参数与调用体原样保留） |
| `tests/test_environment.py:14` | 同上 |
| `tests/test_permissions_risk.py:51` | `relax = lambda n: RiskClass.READ if n in {"write_file", "mcp_tool"} else None` → `def relax(n): return ...` |
| `tests/test_permissions_risk.py:62` | `tighten = lambda n: RiskClass.EXEC if n == "write_file" else None` → `def tighten(n): return ...` |

`tests/test_personas_signing.py:220` 已有 `# noqa: E731`，保持不变。

#### F. E741（2 处）

- `coworker/connectors/gmail_accounts.py:162`：集合推导变量 `l` → `label`。
- `coworker/connectors/integration_tools.py:283`：字典推导变量 `l` → `label`。

#### G. F841（8 处）

| 文件:行 | 改法 |
|---|---|
| `coworker/personas/signing.py:142` | `mem` → `_mem`（保留解析代码，标注 KDF hint 未使用） |
| `coworker/server/manager.py:6465` | 交给 `--fix` |
| `tests/test_compaction_quality.py:134` | 见 C 节 `_notice` |
| `tests/test_personas_signing.py:224` | `data` → `_data` |
| `tests/test_plan_mode.py:198` | `events` → `_events` |
| `tests/test_reviewer_decoupling.py:99` | `registry` → `_registry`（该测试只验证 reviewer model 传递，保留构造） |
| `tests/test_reviewer_decoupling.py:100` | `permissions` → `_permissions` |
| `tests/test_shadow_eval.py:162` | `events` → `_events` |

### 3.4 `.github/workflows/ci.yml`

在 `jobs:` 下第一个 job（`pytest`）之前新增：

```yaml
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install ruff
        run: pip install "ruff>=0.17.3"
      - name: Ruff
        run: ruff check coworker tests scripts
```

## 4. 验证

1. `.\.venv\Scripts\ruff.exe check coworker tests scripts` → **0 errors**（`Found 0 errors`）。
2. 快速子集（先跑，避免直接等 34 分钟）：改动涉及的测试文件
   `tests/test_compaction_quality.py tests/test_mcp_connectors.py tests/test_shadow_eval.py tests/test_code_tools.py tests/test_environment.py tests/test_permissions_risk.py tests/test_personas_signing.py tests/test_plan_mode.py tests/test_reviewer_decoupling.py tests/test_harness_v2.py tests/test_mcp_server.py tests/test_browser_recording.py`
   → 全绿。
3. 全量 `pytest tests -q`（Windows 必须 `dangerouslyDisableSandbox`，约 34 分钟）→ 期望 **2054 passed / 9 skipped**，与改动前基线一致。
4. 冒烟：`python -c "import coworker.connectors.browser_automation, coworker.tools.sandbox, coworker.personas.signing, coworker.server.manager, coworker.server.app"` → 无异常。
5. 复核 `git diff --stat`：除 `pyproject.toml`、`ci.yml`、本 spec 外，只应有 lint 修复文件。

## 5. 风险与回滚

- **F401 自动删除可能移除带注册副作用的导入** → 以全量 pytest 为门禁；若出现失败，逐文件恢复该导入并加 `# noqa: F401`。
- E402 per-file-ignore 只覆盖三个文件，不扩大范围。
- 回滚粒度：`pyproject.toml` + `ci.yml` 单独回滚即可关闭 lint 门禁；代码修复独立于业务逻辑，可 `git revert` 对应提交。
- 不在本次引入 `ruff format`、mypy、扩展规则集。

## 6. 验收标准

- ruff 本地 0 错误；CI `lint` job 在 push/PR 上可独立通过。
- 全量 pytest 结果不低于基线（2054 passed / 9 skipped）。
- `pyproject.toml` 的 `dev` extra 可复现安装 ruff（`pip install -e ".[dev]"`）。
