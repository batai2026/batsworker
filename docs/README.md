# OpenWorker design docs

This directory holds the design specs and decision logs behind OpenWorker's
subsystems. The docs were written against the **0.2.1b** snapshot by reading the
code: file paths, class names, enum values, and protocol messages quoted here
are the ones the shipped code actually uses. When a doc and the code disagree,
the code wins — then please fix the doc.

The code itself carries a second, finer-grained layer of documentation: dated
decision comments (`OPE-###` spec references, `owner ruling YYYY-MM-DD`,
`*-SPEC §n`). The curated index of those lives in
[design/decisions.md](design/decisions.md).

Also in this directory: [config.example.toml](config.example.toml) — a
commented example of the server-side config file (`coworker/config.py` parses
it; every key is optional).

## Reading order

New to the codebase? Read in roughly this order:

1. [**Architecture**](design/architecture.md) — processes, layers, module map, on-disk state.
2. [**Agent engine**](design/agent-engine.md) — the turn loop that drives everything.
3. [**Permissions & approvals**](design/permissions-and-approvals.md) — the safety model.
4. [**Server & auth**](design/server-and-auth.md) — how the GUI talks to the engine.

## Index

| Doc | What it covers |
|---|---|
| [architecture.md](design/architecture.md) | System overview, process model, module map, state-directory inventory |
| [agent-engine.md](design/agent-engine.md) | `TurnEngine` loop, streaming, tool-call orchestration, compaction, durable resume, steering |
| [permissions-and-approvals.md](design/permissions-and-approvals.md) | Permission modes, risk classes, hard floors, approval cards, the Auto-Approve reviewer |
| [server-and-auth.md](design/server-and-auth.md) | FastAPI sidecar, WebSocket protocol, REST surface, launch-token auth, Inbox parking |
| [model-providers.md](design/model-providers.md) | `ProviderClient` abstraction, canonical message format, provider registry, mid-session model switching |
| [tools.md](design/tools.md) | Tool registry, built-in tools, capability catalog, `request_tool` supply-chain gate, subagents |
| [connectors.md](design/connectors.md) | Connector descriptors, gateway & inbound allowlist, OAuth paths, Slack mention router, cloud relay |
| [mcp-client.md](design/mcp-client.md) | `mcpServers` config layering, server lifecycle, MCP tool bridging, OAuth 2.1 + DCR |
| [automation.md](design/automation.md) | Scheduler, scheduled tasks & runs, catch-up semantics, unattended runs and the Inbox |
| [standing-goals.md](design/standing-goals.md) | Event-triggered automations ("standing goals"): file/board/inbox conditions, edge + cooldown firing |
| [budget-guard.md](design/budget-guard.md) | Token budget guard: daily/session/task ceilings, graceful stop, opt-in |
| [observability.md](design/observability.md) | Span-level traces (tool durations), OTLP/HTTP-JSON export, local-first |
| [rag.md](design/rag.md) | Local workspace RAG: chunking, embeddings, cosine search, incremental re-index |
| [browser-recording.md](design/browser-recording.md) | Browser recording → deterministic replay: capture agent tool calls, save as replay scripts |
| [sandbox.md](design/sandbox.md) | Docker-backed sandbox executor: isolation model, config, fallback, v1 boundaries |
| [harness-optimizations.md](design/harness-optimizations.md) | Engine-level optimizations: transient retry, tool result truncation, EGRESS rate limits |
| [harness-v2.md](design/harness-v2.md) | Delta batching, execution waves (file-path DAG), prompt cache stability audit |
| [reviewer-decoupling.md](design/reviewer-decoupling.md) | Reviewer model decoupling: config-driven second model for decorrelated Auto-Approve review |
| [compaction-quality.md](design/compaction-quality.md) | Compaction quality: section validation, checkpoint cross-reference, continuation contract |
| [mcp-server.md](design/mcp-server.md) | Full MCP server: sessions, artifacts, approvals, automations exposed to external agents |
| [multi-model.md](design/multi-model.md) | Multi-model routing: fast_model for utility calls, fallback chains, _select_model infrastructure |
| [memory.md](design/memory.md) | Memory store, injection modes, user rules, memory tools |
| [teams.md](design/teams.md) | Hash-chained board event log, journal, team chat, worker wake plumbing, `ocw` CLI & MCP server |
| [personas-and-skills.md](design/personas-and-skills.md) | Persona manifests, install/consent lifecycle, skills |
| [checkpoints.md](design/checkpoints.md) | Turn-level file checkpoints ("time travel"), rollback semantics, storage layout |
| [persona-distribution.md](design/persona-distribution.md) | minisign-compatible bundle signing, install-time verification, trusted keys, community catalog |
| [desktop-app.md](design/desktop-app.md) | React SPA, Tauri shell, STT sidecar, packaging, auto-update, CI |
| [decisions.md](design/decisions.md) | Curated decision log harvested from in-code `OPE-###` / spec-reference comments |
| [openworker-design-zh.md](design/openworker-design-zh.md) | 整合版中文设计文档（上述全部内容的单文件中文版） |
| [user-manual-zh.md](user-manual-zh.md) | 用户手册（中文）：从快速上手到全部功能的操作指南 |
| [worklog-2026-10-01.md](archive/worklog-2026-10-01.md) | 工作总结：项目分析 · 设计文档 · GUI 中文化 · 质量护栏 · 本地编译（2026-09-27 → 10-01） |
| [worklog-2026-10-25.md](archive/worklog-2026-10-25.md) | 工作总结：登录差异 · 演示生成技能 · 浏览器面板 · 模板/插件市场 · 明文记忆 · PWA · 并行子代理 · 威胁模型闭环 · 雇佣落地页 |
| [extensions-and-marketplace.md](design/extensions-and-marketplace.md) | Extensions & marketplaces: demo→skill, browser panel, template/plugin marketplaces, plain-text memory, PWA, parallel subagents, threat model + finding closure, hire landing pages |
| [maturity-roadmap.md](design/maturity-roadmap.md) | Maturity roadmap: sandbox GUI, App.tsx split, Windows CI/test fixes, new-feature e2e, P2 follow-ups |
| [file-sandbox.md](design/file-sandbox.md) | Design proposal: routing file tools through the Docker sandbox (workspace-only, read-only root, no network) |
| [app-tsx-refactor.md](design/app-tsx-refactor.md) | App.tsx refactor: 16 extracted hooks, patterns, verification, remaining clusters |
| [egress-and-openshell.md](design/egress-and-openshell.md) | Egress policy server + OpenShell sandbox executor + container image (v0.3.1 alignment) |
| [maturity-assessment-2026-11-08.md](maturity-assessment-2026-11-08.md) | 项目成熟度评估报告（2026-11-08）：0.93/1.0，已完成项清单 + 剩余缺口 + 归档记录 |

## Related material elsewhere in the repo

- `reports/` — reviewer-model evaluation reports (Auto-Approve persona comparisons).
- `tests/corpora/` + `scripts/` — permission/risk decision-matrix eval corpora and tooling.
- `surfaces/gui/e2e/README.md` — the hermetic GUI end-to-end harness.
