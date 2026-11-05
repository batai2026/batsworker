# Spec：Code mode 立项评估（建议路线 P2-5）

状态：待批准
范围：评估「模型直出可执行代码编排工具」是否值得引入，输出 Go/No-Go 决策；仅做评测侧 harness 与实验，**不动引擎主循环、不改 CATALOG、不引入新依赖**。

## 1. 背景与已确认事实

- 现状：引擎是 aisuite 风格 tool_call 循环（模型 → tool_call → 执行 → 结果回填 → 再决策），每次工具调用一次模型往返，中间结果全量占用上下文。
- Code mode 思想：模型直出一段沙箱可执行代码，代码内部编排工具调用并聚合，只把关键结果回传上下文 —— 往返次数与上下文字节数预期显著下降。
- 可复用基础设施：
  - Docker 沙箱执行器 `tools/sandbox.py`，不可用时 graceful fallback（`sandboxed=False + fallback_reason`，不抛错）；
  - 轨迹账本 `TrajectoryStore.read()`，`model_request.data` 含 `digest/bytes/model/tools`（[trajectory.py](file:///e:/tworkspace/openworker-0.2.1/coworker/trajectory.py#L157-L183)）；
  - 评测矩阵 `coworker/eval/matrix.py`：`HARNESSES` / `recorded_turns()` / `run_matrix()` / `--stub`（[matrix.py](file:///e:/tworkspace/openworker-0.2.1/coworker/eval/matrix.py#L58-L96)），新增 harness 只需加一项 `HarnessSpec`，零改引擎；
  - 录制回放：`POST /v1/sessions/{id}/replay` + fork 切割点（P0-2）。
- 权限模式四档：`discuss / plan / interactive / auto-approve`（[permissions.py](file:///e:/tworkspace/openworker-0.2.1/coworker/permissions.py#L226-L262)）；引擎默认 `approver=_deny_all`。
- 评测先例：P1-4 矩阵已验证 stub 离线可跑、真实跑必须显式 `--models`；`scripts/eval_reviewer.py` 同模式。

## 2. 决策

1. **评估形态**：本立项只交付评估报告 + Go/No-Go。三形态对比（A：独立 persona/harness；B：引擎新 mode；C：工具即代码 API）在报告内给结论，不预先锁定。
2. **试验 harness 走评测侧**：`code-mode` 作为 `HARNESSES` 第四档，系统提示注入「工具以代码 API 呈现 + 围栏代码块输出」约定；执行器仍走现有引擎（stub/真实模型都按普通 tool_call 循环跑，code mode 的差异只体现在提示与输出协议上）——**不为此改引擎**。若 Go 且选形态 B/C，另行立项实现。
3. **度量指标全部取自轨迹账本**：`model_request.bytes`（出站负载）、turn 数、`tool_calls` 数、总耗时；成功率由人工抽检 3 组会话判定。
4. **Go 阈值**：上下文字节数或 turn 数较 baseline 下降 <20%，或出现不可缓解的安全性退化 → **No-Go**；≥20% 且风险表全部有缓解 → Go。
5. **fail-closed**：真实模型跑批必须显式 `--models`；`--stub` 只验证脚手架；代码执行一律沙箱内，宿主裸跑模型代码不在评估范围。

## 3. 精确改动清单

### 3.1 调研笔记 `docs/code-mode-research.md`（或 spec 附录）

整理 DeepSeek Harness code mode 关键设计：输出协议（代码块格式）、沙箱 API 形状、错误回传策略、上下文回写规则。每条附出处链接/版本。

### 3.2 `coworker/eval/matrix.py`：新增 `code-mode` harness

```python
"code-mode": HarnessSpec(
    name="code-mode",
    capabilities=("code_files", "shell"),          # 与 minimal 同槽位，差异只在提示
    system_prompt=_CODE_MODE_PROMPT,               # 代码 API 约定 + 围栏输出协议
)
```

提示要点（写入 `_CODE_MODE_PROMPT`）：
- 可用工具以「代码 API」形式列出（名字 + 一行签名）；
- 模型输出必须是一个围栏代码块（```python），块内按约定调用 API；
- 只允许一个代码块，块外只放行结果陈述；
- 代码执行失败的 stderr 会在下一轮以工具错误形式回传，模型应修复后重试，最多两次。

### 3.3 实验输入

3 组录制会话（取自真实 trajectory 或手工构造，覆盖）：多文件修改、长检索聚合、多步 shell 编排。每组落盘为 conversations + trajectory JSONL，供 `recorded_turns()` 直接消费。

### 3.4 对比实验

```powershell
python -m coworker.eval.matrix --session <sid> --harnesses minimal,code-mode `
  --models <model1,model2> --workspace . --out report-<sid>.json
```

- baseline：`minimal`（tool_call）；试验：`code-mode`；
- `--stub` 只验证脚手架不产出有效结论；真实对比需显式 `--models`（由执行者提供 key，费用自担）；
- 产出：每组会话一份报告 JSON + 汇总表（bytes/turns/tool_calls/耗时 + 人工成功率）。

### 3.5 安全面评估表（报告附录）

逐项裁决：审批门（代码内工具调用是否仍过 `Approver`）、沙箱隔离（Docker fallback 契约）、egress（代码内网络访问约束）、审计（是否需新增 `kind: code_exec` 轨迹记录）、提示注入面变化。每项给出「现状/风险/缓解」三栏。

### 3.6 测试

- `tests/test_eval_matrix.py` 追加：`HARNESSES` 含 `code-mode`、其槽位与 minimal 一致、stub 跑通；
- 其余行为由既有矩阵测试覆盖，不新增框架。

## 4. 验证

1. `ruff check coworker/eval tests/test_eval_matrix.py` → 0 errors。
2. `pytest tests/test_eval_matrix.py -q` 全绿（含新断言）。
3. `python -m coworker.eval.matrix --help` 冒烟；`--stub --harnesses code-mode` 端到端跑通。
4. 全量 `pytest tests -q` ≥ 2113 passed / 9 skipped（新增用例另计）。

## 5. 风险与回滚

- 风险：提示协议不稳定导致模型不产出代码块 → 实验数据直接呈现失败率，作为 No-Go 证据之一；
- 风险：真实模型跑批产生费用 → 显式 `--models` 才跑，报告注明成本；
- 风险：评估结论被误当生产能力 → 报告首页注明「评测侧 harness，引擎未改」；
- 回滚：`code-mode` harness 是一行 `HarnessSpec` + 一个提示常量，删除即回退。

## 6. 验收标准

- `code-mode` harness 在评测矩阵中可选，`--stub` 离线跑通；
- 调研笔记覆盖输出协议 / 沙箱 API / 错误回传 / 回写规则四项；
- 3 组对比实验数据齐全（或明确记录因缺 key 只完成 stub 验证 + 实验方案）；
- 安全面评估表五项全部有裁决；
- 产出 Go/No-Go 决策；若 Go，附后续实现 spec 草案（落点：引擎可选执行路径或新 persona）。
- 全量回归无回退。
