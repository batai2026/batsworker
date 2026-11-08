# docs-harness-gaps — 设计文档补齐（DeepSeek Harness 借鉴清单 11 条）

## 背景

清单 11 条（P0-1 … P2-11）已全部落地并提交，但设计知识目前只存在于
`.trae/specs/<name>/` 的立项 spec 与代码注释中，`docs/design/` 没有对应沉淀。
成熟度评估（2026-11-07，~0.88/1.0）指出：剩余缺口之外，"已落地能力无权威文档"
本身是 1.0 前的治理债。本任务把这 11 条的关键设计写进 docs。

## 方案（用户已确认：方案 A，英文设计文档）

### 1. 新建 `docs/design/eval-and-replay.md`

承载"可复现与评测"线五条：

- P0-1 turn ledger（`coworker/trajectory.py`）：append-only 账本、事件种类、
  turn_start.message_count 的切割点语义
- P0-2 fork / replay（`coworker/server/manager_sessions.py`）：fork 切割点定义、
  replay 语义、API
- P1-4 presets + 模型×harness 评测矩阵（`coworker/eval/matrix.py`）：cell 隔离、
  --stub / --auto-approve、报告形态
- P2-6 harness 注册表（`coworker/personas/harness.py`）：builtin + installed 合并、
  单一来源原则
- P2-11 eval capture（`coworker/eval/capture.py`）：messages.jsonl + baseline.json、
  隐私门禁、tool_names 基线、matrix --case 消费

### 2. 增补 `docs/design/permissions-and-approvals.md`

consent 治理线四条（追加章节，不改既有内容语义）：

- P1-3 PluginManifest：权限声明来源
- P2-7 统一安装管线：connector/MCP lifecycle consent
- P2-8 consent v2：权限扩大显式确认
- P2-10 审计账本：append-only + GET /v1/plugins/audit

### 3. 增补 `docs/design/agent-engine.md`

- P2-9 mid-turn steering（软引导）：运行中文本入队、下一模型请求边界注入
- code mode：P2-5 评估 No-Go 结论一句 + 形态 B（exec 工具、fail-closed 沙箱、
  审批门桥接）

### 4. 更新 `docs/design/maturity-roadmap.md`

- 新增 2026-11 段落：清单 11 条完成状态、~0.88/1.0 评估、剩余缺口
  （评测闭环未自动化 / OpenShell 真实 flags / 前端 zustand 预案）

### 5. 更新 `docs/user-manual-zh.md`（中文）

- 新增 CLI 小节：`python -m coworker.eval.capture` 导出评测用例包、
  `python -m coworker.eval.matrix --case` 逐 turn 对比基线、常用参数

## 事实来源

`.trae/specs/{turn-ledger, session-fork-replay, plugin-manifest, presets-eval,
code-mode-eval, code-mode-impl, harness-registry, unified-install, consent-v2,
soft-steering, consent-audit, eval-capture}/spec.md` + 对应源码。
文档中的文件路径、符号名、API 路径、CLI 参数必须与代码一致。

## 验收

- 纯文档改动，不碰代码；`git status` 只含上述 5 个文件 + 本 spec
- 新文档遵循既有设计文档体例（标题 + `> Source:` 溯源行 + 章节）
- 手册新增小节为中文，与既有手册语言一致
- 无需全量 pytest；跑一次 `git diff --check` 防空白错误
