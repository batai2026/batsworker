# Spec：Code mode 形态 B 实现（exec 工具 + fail-closed 沙箱 + 审批门桥接）

状态：待批准
依据：[code-mode-eval/report.md](../code-mode-eval/report.md) 再进入条件。用户指令：「按这个方案推进形态 B 的实现」。

## 1. 背景与已确认事实

- 引擎是 aisuite tool_call 循环；`Approver` 逐工具审批，默认 deny（[engine.py](file:///e:/tworkspace/openworker-0.2.1/coworker/engine.py)）。
- CATALOG 封闭能力槽 `expand(ids, ctx)`；persona manifest `tools:` 选槽。
- DockerExecutor（`tools/sandbox.py`）一次性 `docker run` 执行命令；不可用时 graceful fallback——**code mode 不复用该语义，改为 fail-closed**。
- 轨迹账本支持任意 `kind` 记录；`model_request` 已有 digest/bytes。
- 调研基线（research.md §2/§3）：guest 内工具调用走与正常 turn 相同执行路径（审批/hooks 不旁路）；上限基线 timeout 10s、output 64KiB、pending calls 16；运行时缺失即失败，绝不静默退回。

## 2. 决策

1. **语言与运行时**：guest 代码为 **Python**（与本仓库栈一致），在 **Docker 容器**内执行；无 Docker → `exec` 返回结构化错误 `{"error": "sandbox_unavailable"}`，**宿主永不裸跑模型代码**。
2. **桥接（修订：stdio JSON-lines）**：guest 与宿主经 `docker run -i` 的 **stdin/stdout JSON-lines 协议**通信——guest 发出 `{"__call__": {...}}` 请求行，宿主写回响应行；用户 `print` 由 guest runner 捕获，只在最终信封 `{ "__result__": {...}}` 里回传。**容器保持 `--network none`，egress 零开放**（原 HTTP 桥接方案需开 bridge 网络，与安全评估冲突，已否决）。每次桥接调用照常过该会话的 `Approver`；名字白名单 + 单次 exec ≤16 次。
3. **工具面坍缩**：新能力槽 `code_mode` 只暴露一个 `exec(code)` 工具；可用工具 API 以紧凑签名注入 `exec` 的 description（声明来源 = 该会话真实展开的工具集，剔除 `exec` 自身）。
4. **审计**：新增轨迹 `kind: code_exec`（代码全文 + sandboxed + 桥接调用计数）；桥接的每次内部调用仍记标准 `tool_call`/`tool_result`。
5. **上限**：单次 exec timeout 60s（Docker 启动开销大于 QuickJS，调研 10s 基线放宽）、stdout/stderr 各 64KiB、单次 exec 内桥接调用 ≤16 次。
6. **exec 本身过审批**：代码全文进审批预览；`--auto-approve` 语义不变。
7. **不改引擎主循环**：一切经现有 `build_engine`/CATALOG/Approver 管线接入；新增 `coworker/codemode/` 包。

## 3. 精确改动清单

### 3.1 `coworker/codemode/__init__.py`
导出 `CodeModeBridge`、`guest_source()`、`exec_tool()`。

### 3.2 `coworker/codemode/bridge.py`
宿主侧协议循环（Popen stdin/stdout）：
- `run_guest(popen_args, code, *, tools, approver, call_limit=16, timeout=60)`：写 code → 循环读 guest stdout 行；`__call__` → 白名单校验 → `PermissionRequest` 过 `approver` → 执行 → 写回 `{ok, result|error}`；`__result__` → 收尾；超上限 → 回 `call_limit` 错误并继续（guest 自决）；
- 超时杀进程；guest 异常退出 → 返回 stderr。

### 3.3 `coworker/codemode/guest.py`
- `guest_source()` 返回内嵌 guest runner 源码：stdin 读用户代码 → `exec` 之，注入 `tools` 代理（`__getattr__` → 写 `__call__` 行、读响应行）；`print` 重定向进缓冲；结束写唯一 `__result__` 信封 `{stdout, error?}`；协议行与用户输出严格隔离（用户 print 不直接上 stdout）。
- docker 命令：`docker run --rm -i --network none --read-only --memory 512m --cpus 2 -e PYTHONUNBUFFERED=1 <image> python -c <runner>`（沿用 DockerExecutor 的镜像与资源上限约定；不挂工作区——文件操作全部走桥接工具）。

### 3.4 `coworker/codemode/exec_tool.py`
`exec_tool(context, *, bridge_factory)`：
- `context.executor` 非 DockerExecutor → 直接返回 `sandbox_unavailable`（fail-closed）；
- 起桥接 → 跑 guest → 截断 stdout/stderr 64KiB → 记录 `code_exec` → 返回 `{stdout, stderr, exit_code, bridge_calls}`；
- 审批：发起 `PermissionRequest`（kind=exec，preview=代码全文）。

### 3.5 `coworker/catalog.py`
新槽 `code_mode`：build 返回 `[exec_tool(...)]`；`requires=("workspace",)`；risk 含 `WRITE_LOCAL` + 执行语义注记。`exec` description 由会话工具集生成紧凑签名（`name(arg: type, ...) -> 一行说明`），排除 `exec` 自身。

### 3.6 `coworker/personas/builtin/code-mode/manifest.md`
`tools: [code_mode]` 单槽 preset，进入新会话选择器；提示复用评测版 `_CODE_MODE_PROMPT` 精神（单块编排、stderr 重试 ≤2 次）。

### 3.7 `coworker/trajectory.py`
`record_code_exec(code, *, sandboxed, bridge_calls, exit_code)`；digest 脱敏沿用现有管线（代码全文属本地落盘，与 conversations 同级处理）。

### 3.8 测试 `tests/test_codemode.py`
- 桥接：token 校验、白名单拒绝、审批门被调用（stub approver 记录）、16 次上限；
- exec：非 Docker executor → `sandbox_unavailable`（不跑 Docker）；mock executor 验证超时/截断/桥接计数；审批 preview 含代码全文；
- catalog/persona：`code_mode` 槽展开单 `exec` 工具；code-mode preset 注册进 registry；
- **全部测试不依赖真实 Docker**（Windows CI 无 Docker 是常态）。

### 3.9 评测接入（复用 P2-5 资产）
`coworker/eval/matrix.py` 的 `code-mode` harness 切到真槽位 `("code_mode",)`；验收实验方案沿用 `inputs/` 三组会话 + ≥20% 阈值，真实跑批需 `--models` + Docker，由执行者触发。

## 4. 验证

1. `ruff check` 0 errors。
2. `pytest tests/test_codemode.py tests/test_eval_matrix.py -q` 全绿（无 Docker 依赖）。
3. 冒烟：`python -c` 构造引擎 + stub provider，code-mode persona 下 `exec` 在无 Docker 时返回 `sandbox_unavailable`。
4. 若本机有 Docker：手动跑一次真实 guest（可选，记录结果）。
5. 全量 `pytest tests -q` ≥ 2115 passed / 9 skipped。

## 5. 风险与回滚

- 风险：stdio 协议被用户 print 污染 → guest runner 捕获 print，协议行与用户输出严格隔离；
- 风险：guest 死循环/挂起 → 60s 超时杀进程， stderr 回传；
- 风险：审批粒度过粗 → 代码全文进审批预览 + 桥接调用逐项仍过审批；
- 回滚：删除 `codemode/` 包 + catalog 槽 + preset 目录即完整回退，引擎零改动。

## 6. 验收标准

- code-mode persona 可选，`exec` 在无 Docker 环境 fail-closed；
- 桥接每次调用过 `Approver`（测试证明）；
- `code_exec` 轨迹记录代码全文与桥接计数；
- 上限（timeout/输出/调用数）生效且有测试；
- 全量回归无回退。
