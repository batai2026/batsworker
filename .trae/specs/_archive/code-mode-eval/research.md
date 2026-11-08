# 调研笔记：Code mode（programmatic tool calling）

日期：2026-11-06。四组来源：DeepSeek Harness 官网、MCP 客户端最佳实践（draft）、OpenClaw code mode 参考实现、Cloudflare 模式文 + philschmid 综述。

## 1. 输出协议

| 实现 | 模型可见面 | 代码形态 |
|---|---|---|
| DeepSeek Harness | Standard 全部能力，经 **Code Mode SDK** 暴露 | 一个 TypeScript 程序，多步操作合并在一次执行里 |
| MCP 最佳实践 | 模型不直接调工具，而是「写调工具的代码」 | 代码在沙箱执行，只回传最终结果 |
| OpenClaw | 只暴露 `exec` + `wait` + 少数 direct-only 工具 | `exec` 收 JSON `{code, language}`；隐藏目录经 `ALL_TOOLS` / `tools` 在 guest 内可查可调 |
| Codex Code Mode | 同上名字的 `exec` | freeform 原始 JS，可加 `// @exec: {...}` pragma 行，跑在 Deno/V8 |
| Cloudflare 模式 | 工具 schema 编译成紧凑 TS 声明（`declare namespace tools`），模型据此写代码 | TS 在临时 V8 isolate 执行 |

共性结论：
- **模型面坍缩**：从 N 个 JSON Schema 工具定义坍缩成 1~2 个控制工具（`exec[/wait]`），工具目录以「代码 API 声明」而非 schema 形式进上下文 —— 大目录时 token 收益主要来自这一步。
- **命名空间**：MCP 工具分组在 `MCP`（或 `tools.<server>.<tool>`）命名空间下；超大目录时再退化为全局 map 供代码内检索（`ALL_TOOLS` + `search_tools` 思路与 progressive discovery 正交可叠加）。
- **语言**：主流是 TypeScript/JavaScript（V8/QuickJS/Deno 生态）；Python 变体存在但非主线。

出处：
- https://www.deepseek.com/harness/en/（Multiple runtime modes 一节）
- https://modelcontextprotocol.io/docs/draft/develop/clients/client-best-practices
- https://docs.openclaw.ai/reference/code-mode
- https://www.agentic-patterns.com/patterns/code-first-tool-interface-pattern/
- https://www.philschmid.de/code-mode

## 2. 沙箱 API

- **隔离运行时**：QuickJS-WASI worker（OpenClaw）、V8 isolate（Cloudflare）、Deno/V8（Codex）。共同姿态：「model code is hostile」，默认无网络、无宿主文件句柄。
- **资源上限**（OpenClaw 配置面，可直接借鉴为本项目取值基线）：
  `timeoutMs: 10000`、`memoryLimitBytes: 64MiB`、`maxOutputBytes: 64KiB`、`maxPendingToolCalls: 16`、`maxSnapshotBytes: 10MiB`、`snapshotTtlSeconds: 900`、`searchDefaultLimit: 8`。
- **桥接**：guest 内调工具走**与正常 agent turn 相同的执行路径**——policy、审批、hooks、telemetry 全部照常生效（OpenClaw 明确列出）。这是安全模型的关键：code mode 只改编排面，不绕过治理面。
- **fail-closed**：运行时不存在时整个 run 失败，**绝不静默退回宽泛直连工具暴露**（OpenClaw 的 user-facing promise）。与本仓库 DockerExecutor 的 graceful fallback 语义不同——code mode 评估中应采纳 fail-closed（无沙箱即拒绝执行模型代码，而不是宿主裸跑）。

## 3. 错误回传

- **类型错误先行**：Pi codemode 在执行前先 type-check，类型错误直接返回且无副作用；运行时错误作为 tool error 返回。
- **suspend/resume**：嵌套工具调用未完成时 run 挂起，`wait` 恢复（OpenClaw）。
- **结果形态**：`return` 的值进 tool result；`print()`/`console.log()` 输出在返回值之前被捕获。超长结果在 UI 折叠中段。
- 对本项目的映射：代码执行失败应作为一条「工具错误」消息回注，让模型修复重试（spec 限定最多两次）；stderr 全文进轨迹账本但只摘要进后续模型请求。

出处：https://pi.dev/packages/@boozedog/pi-codemode、https://docs.openclaw.ai/reference/code-mode

## 4. 上下文回写规则

- **只回传显式输出**：philschmid 综述给出显式 helper——`text()/image()/audio()`，只有经 helper 的内容才进模型上下文窗口。
- **跨轮状态不进上下文**：Cloudflare 模式有 host 内存态 helper，跨 turn 持久化但不付 token 成本。
- **中间结果就地消化**：fan-out（100 封邮件、20 个并行 lookup）在沙箱内循环/并发完成，模型只见聚合结果——这是 bytes 收益的主要来源。
- **适用边界**（Cloudflare 模式文明确列出 avoid 项）：开放研究（每步依赖上步结果）、调用间需要 LLM 推理、一次性单工具调用——这些场景 code mode 无收益甚至负收益。本项目实验选「多文件修改 / 长检索聚合 / 多步 shell 编排」正是收益区。

## 5. 对本立项的直接推论

1. 评测侧 harness 的提示应模拟「工具以代码 API 声明呈现 + 单一代码块输出」，而非假装已有沙箱执行器（形态 A，零改引擎）。
2. Go 之后若选形态 B，执行器选型应 fail-closed；桥接必须复用现有 `Approver` 审批门，不旁路。
3. 度量收益看两个来源：**工具定义不进上下文**（bytes ↓）与**中间结果不进上下文**（bytes/turns ↓）——评测报告应分别归因。
4. 安全面五项里，「审批门不被代码旁路」是 Go 的硬条件（OpenClaw 与 MCP 实践一致）。
