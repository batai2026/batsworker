# 项目成熟度评估报告 — 2026-11-08

> 评估对象：`batai2026/batsworker`（derived from `andrewyng/openworker` 0.2.1）
> 评估基线：远端 CI `bee7e77` 全绿 + 本地回归 2170 passed / 9 skipped
> 前次评估：2026-11-07 自评 ~0.88/1.0（见 [maturity-roadmap.md](design/maturity-roadmap.md)）

## 一、结论

**综合成熟度：0.93 / 1.0（Beta → Release Candidate 边界）**

自 2026-11-07 评估以来，剩余的两项 P2 跟进（真实模型矩阵基线、OpenShell 真实 CLI 接入）已闭环，文档已归档，CI 持续全绿。当前已无阻塞 1.0 的功能缺口，仅余三项"真实环境验证"性质的待办。

## 二、已完成项（带证据锚点）

### A. CI / 测试基线（质量护栏）

| 项 | 状态 | 证据 |
|---|---|---|
| 双平台 CI（ubuntu + windows） | ✅ | `.github/workflows/ci.yml`，run `bee7e77` success |
| Ruff 静态检查（`coworker tests scripts`） | ✅ | `pyproject.toml` select E4/E7/E9/F，run `bee7e77` lint success |
| 后端回归基线 | ✅ | 2170 passed / 9 skipped（本地 45:59） |
| GUI 单测 | ✅ | 46 文件 / 214 用例全过 |
| GUI Playwright e2e | ✅ | 238 用例全过（hermetic mock，无真实模型/网络） |
| i18n 校验 | ✅ | 848 keys / 882 entries |
| Eval gate（stub 黄金用例） | ✅ | `.github/workflows/eval.yml` `gate-stub`，run `bee7e77` success |

### B. P0 / P1 核心缺口（roadmap 四件套）

| 项 | 状态 | 证据 |
|---|---|---|
| Windows CI job + 2 个平台用例修复 | ✅ | `tests/test_config.py`（POSIX-only 权限断言守卫）、`tests/test_security_workflow.py`（Docker `--read-only` Windows 守卫） |
| 6 项新功能 e2e specs | ✅ | `surfaces/gui/e2e/` 下 8 个 spec |
| App.tsx 拆分（16 hooks） | ✅ | `surfaces/gui/src/hooks/`，2414 → 1423 行 |
| 沙箱 GUI 开关 + `sandboxed` 徽章 | ✅ | `coworker/sandbox_settings.py` + Settings ▸ Security |

### C. P2 跟进项

| 项 | 状态 | 证据 |
|---|---|---|
| Windows 打包 + Authenticode 签名脚手架 | ✅ | `release.yml` 已有 release job |
| 安全闭环演练 + 报告 | ✅ | `scripts/security_drill.py`、`reports/security-drill.md` |
| PWA 移动烟测 + Lighthouse | ✅ | SEO 100 / A11y 100 / BP 96 |
| 沙箱 v2：per-command probe + fallback | ✅ | `DockerExecutor`、`OpenShellExecutor` `sandboxed:false + fallback_reason` |
| 沙箱 v2：后台任务 | ✅ | `run_background` / `background_output` / `background_kill` |
| 沙箱 v2：文件工具走沙箱 | ✅ | `sandbox_files.py`、`unified_diff.py` |
| 沙箱 v2：Windows Docker 验证 | ✅ | `test_docker_executor_real_sandbox_run` |

### D. v0.3.1 gap closure

| 项 | 状态 | 证据 |
|---|---|---|
| Egress policy server + 工具 + API | ✅ | `coworker/egress.py`、`coworker/tools/egress.py`、`tests/test_egress.py` |
| **OpenShell 真实 CLI 接入** | ✅ | `coworker/tools/openshell.py`（create/exec/delete 生命周期 + 内嵌基线 policy）、`tests/test_openshell.py` 11 tests |
| 容器镜像发布 | ✅ | `Dockerfile` + `.github/workflows/docker.yml` → `ghcr.io` |

### E. 2026-11 "borrowed harness" 11 项（11/11 done）

详见 [maturity-roadmap.md L176-189](design/maturity-roadmap.md#L176-L189)。涵盖 turn ledger、fork/replay、PluginManifest、presets、code mode（No-Go + 形态 B）、harness registry、unified install、consent v2、mid-turn steering、consent audit、eval capture。

### F. Eval 闭环

| 项 | 状态 | 证据 |
|---|---|---|
| 确定性 stub 黄金用例 + CI gate | ✅ | `evals/ci-case/`、`eval.yml` `gate-stub` |
| **真实模型矩阵基线** | ✅ | `evals/ci-case/real-baseline.json`（deepseek:deepseek-v4.1，run 42419210644，出站字节偏差 -1.5%/-2.7%） |

## 三、剩余缺口（均非阻塞 1.0）

| 缺口 | 性质 | 建议 |
|---|---|---|
| OpenShell 在装有 NVIDIA OpenShell 的主机上实测 | 真实环境验证 | 当前为 fake-CLI 单测 + 架构正确；需在真实环境跑一轮 `sandbox create/exec/delete` |
| 真实模型矩阵在更多模型上跑通 | 真实环境验证 | 当前仅 deepseek:deepseek-v4.1 单模型；可扩展 gpt-4o / claude 等 |
| zustand UI-prefs slice | 文档判定低价值 | `gui-state-zustand.md` 标注"仅当出现非 React 消费方时再做"，可跳过 |

## 四、文档归档清单

本次归档将已落地的规划记录移入归档目录，保持工作区整洁：

- `.trae/specs/_archive/`：22 个已完成 spec（`turn-ledger`、`session-fork-replay`、`plugin-manifest`、`presets-eval`、`code-mode-eval`、`code-mode-impl`、`harness-registry`、`unified-install`、`consent-v2`、`soft-steering`、`consent-audit`、`eval-capture`、`eval-baseline-gate`、`eval-ci-gate`、`gui-active-session-store`、`gui-run-store`、`gui-zustand-pilot`、`ci-ruff-lint`、`docs-harness-gaps`、`desktop-openshell-checks`、`openshell-prefix`、`manager-split`）
- `docs/archive/`：2 份工作日志（`worklog-2026-10-01.md`、`worklog-2026-10-25.md`），`docs/README.md` 链接已更新指向 `archive/`

## 五、风险与建议

1. **真实环境验证是当前最大的不确定性来源**：OpenShell 和真实模型矩阵均为单测/单模型验证，建议在 1.0 前安排一次真实环境集成演练。
2. **Windows 本机环境偶发 Winsock 异常**（本次会话出现 getaddrinfo 崩溃 / WinError 10055），重启网络适配器可恢复；CI runner 不受影响。
3. **GitHub 网络间歇性阻断**：已沉淀 `.trae/skills/github-rest-push/` Skill，`github.com` 不通时可走 REST API 推送。
4. **zustand UI-prefs slice** 按设计文档判定为低价值，建议不做，避免过度工程。

## 六、1.0 发布建议

满足以下条件即可发布 1.0：
- [ ] OpenShell 真实环境一轮跑通（非阻塞，可后置到 1.0.x）
- [x] CI 全绿 + 回归基线稳定
- [x] 文档归档完成
- [x] 无已知 P0/P1 缺陷
