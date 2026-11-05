# 评估报告：Code mode 立项评估（P2-5）

日期：2026-11-06 · 状态：完成 · **决策：No-Go（当前）**
范围声明：本次评估的 code-mode 是**评测侧 harness**（提示与输出协议模拟），引擎未改、代码不被真正执行。

## 1. 方法

- 调研：DeepSeek Harness 官网、MCP 客户端最佳实践（draft）、OpenClaw code mode 参考实现、Cloudflare 模式文、philschmid 综述（详见 [research.md](research.md)）。
- 试验：3 组录制输入（多文件修改 exp-edit / 长检索聚合 exp-search / 多步 shell 编排 exp-shell，见 `inputs/`），跑 `minimal`（baseline）vs `code-mode` 两档 harness。
- 度量：轨迹账本 `model_request.bytes / digest / requests / tool_calls`。

## 2. 实验数据（stub 脚手架，离线）

| 会话 | harness | 总 bytes | requests | tool_calls |
|---|---|---|---|---|
| exp-edit | minimal | 6319 | 2 | 0 |
| exp-edit | code-mode | 7325 | 2 | 0 |
| exp-search | minimal | 6348 | 2 | 0 |
| exp-search | code-mode | 7354 | 2 | 0 |
| exp-shell | minimal | 6281 | 2 | 0 |
| exp-shell | code-mode | 7287 | 2 | 0 |

真实模型跑批未执行（需显式 `--models` 与 key）；脚手架、输入管线、报告落盘均已验证可用（`report-*-stub.json`）。

## 3. 分析

1. **模拟形态下 code-mode bytes 不降反升 +16%**。原因直接：形态 A 不改引擎，工具 JSON Schema 仍在请求载荷里，代码 API 声明是纯增量提示。调研确认收益的两个来源——①工具定义不进上下文（面坍缩为 `exec`）、②中间结果在沙箱内消化不回传——**都只能在形态 B（真执行器）兑现**。
2. **形态 A 无法度量成功率**：代码不执行，需要真实改动的任务成功率为 0；turn 数即使下降也无意义（任务没完成）。因此「真实模型对比实验」在模拟形态下产出不了有效结论。
3. **要证明 ≥20% 收益，必须先建形态 B**（`exec` 工具 + fail-closed 沙箱 + 审批门桥接），而那本身就是待评估的引入对象——评估与实现无法解耦。
4. 适用边界（Cloudflare 模式文）：开放研究、调用间需 LLM 推理的场景 code mode 无收益；本项目会话以交互式逐步任务为主，收益面仅限 fan-out/聚合类。

按 spec 阈值（bytes 或 turns 下降 <20% 即 No-Go）：模拟实测 **-16%（回退）**，且有效收益不可在评测侧度量 → **No-Go**。

## 4. 安全面评估表

| 项 | 现状 | 风险 | 缓解（若未来做形态 B） |
|---|---|---|---|
| 审批门 | `Approver` 逐工具审批，默认 deny | 代码内拼接调用绕过逐工具审批 | 桥接走同一执行路径（OpenClaw 实证可行）；`exec` 本身按高风险工具过审批 |
| 沙箱隔离 | DockerExecutor + graceful fallback | 模型代码敌意；fallback 到宿主裸跑即事故 | code mode 必须 **fail-closed**：无沙箱即拒绝执行，不复用现有 fallback 语义 |
| egress | egress 槽位 + 默认策略 | 代码内直接网络访问绕过 egress 工具 | 沙箱网络默认关闭/白名单；上限基线：timeout 10s / mem 64MiB / output 64KiB / pending calls 16 |
| 审计 | 轨迹账本记 tool_call/tool_result/digest | 代码本体与代码内调用不可见，调试退化 | 新增 `kind: code_exec` 记代码全文；代码内每次工具调用仍记 tool_call |
| 注入面 | 逐工具审批=细粒度检查点 | 一段长代码聚合多操作，一次批准粒度变粗 | 代码全文进审批预览；单块操作数硬上限 |

裁决：五项均有缓解，无不可控项——安全面不是 No-Go 的原因；**不可度量收益才是**。

## 5. 决策与再进入条件

**No-Go（当前）**：不在 harness 中引入 code mode。

再进入条件（任一满足可重新立项，直接做形态 B 实现而非模拟）：
1. 有真实 fan-out/聚合类负载（connector 批量操作、MCP 大目录）成为主要场景；
2. 模型供应商对 code mode 的后训练支持成熟，且本仓库计划接入 MCP 大目录（工具定义占比触发 progressive discovery 阈值）；
3. 届时验收复用本次资产：`inputs/` 三组会话、`code-mode` harness、矩阵管线，Go 阈值不变（bytes 或 turns ↓ ≥20%），执行器 fail-closed。

## 6. 资产清单

- [research.md](research.md)：输出协议 / 沙箱 API / 错误回传 / 回写规则四项调研 + 出处
- [coworker/eval/matrix.py](file:///e:/tworkspace/openworker-0.2.1/coworker/eval/matrix.py)：`code-mode` harness（槽位同 minimal，仅提示差异）
- `inputs/`：3 组实验会话（conv + traj JSONL）
- `report-*-stub.json`：3 组 stub 对比报告
- tests/test_eval_matrix.py：8 用例全绿
