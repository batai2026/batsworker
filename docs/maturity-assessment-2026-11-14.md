# 项目成熟度评估报告 — 2026-11-14

> 评估对象：`batai2026/batsworker`（derived from `andrewyng/openworker` 0.2.1）
> 评估基线：远程 CI `3398710` 全绿 + 本地定向回归（shell/secrets/merge_pr/github_installs/codemode/sandbox 全部通过）
> 前次评估：2026-11-08 自评 **0.93 / 1.0**（见 [maturity-assessment-2026-11-08.md](maturity-assessment-2026-11-08.md)）

## 一、结论

**综合成熟度：0.95 / 1.0（Release Candidate）**

自 2026-11-08 评估以来，本轮完成了两项重要的可靠性/质量加固：

1. **修复 Windows GBK 解码崩溃**（F-2f26082f）：统一了产品代码与测试代码中全部 26 个 `subprocess` 文本模式调用点的编码策略，在 zh-CN Windows（代码页 936）上杜绝了 `UnicodeDecodeError` 杀死 reader 线程、导致命令输出丢失的故障。
2. **新增 GBK 字节回归测试**：在真实中文 Windows 环境验证 LocalExecutor 遇到非 UTF-8 子进程输出时不崩溃、会话存活。

核心功能已全部落地，CI 持续全绿，剩余缺口全部属于「真实环境验证」性质，无已知 P0/P1 缺陷。

## 二、本轮工作：GBK 解码崩溃修复（P1 可靠性）

### 背景与影响

在中文 Windows 上，`subprocess` 默认按 ANSI 代码页（GBK/936）解码子进程输出。但产品代码大量使用 `text=True` 且**未指定编码**，当子进程输出 UTF-8 字节（git 提交信息、rustc/编译器输出、含非 ASCII 文件名的 `dir` 列表）时，`for line in proc.stdout` 会抛出 `UnicodeDecodeError`，reader 线程崩溃，命令输出捕获中断，严重时拖垮整个会话。共影响 **26 个调用点**，横跨 11 个产品代码文件 + 3 个测试文件。

### 修复内容

- **统一编码策略**：所有 `text=True` 调用点补上 `encoding="utf-8", errors="replace"`。
  - `errors="replace"` 保证非法字节解码为 U+FFFD（�）而不是抛异常——一个乱码字节永远不会杀死会话。
  - 产品代码 11 个文件：`shell.py`、`openshell.py`、`sandbox.py`、`search.py`、`secrets.py`、`personas/loading.py`、`codemode/bridge.py`、`server/manager_helpers.py`、`server/manager_providers.py`、`scripts/check_sidecar_cli.py`、`projects.py`（原已带）。
  - 测试代码 3 个文件：`test_merge_pr_script.py`、`test_secrets.py`、`test_github_installs.py`。
  - `shell.py` 中新增常量 `UTF8_TEXT_IO = {"encoding": "utf-8", "errors": "replace"}` 复用。
- **新增回归测试**：`tests/test_shell.py::test_gbk_output_does_not_crash_reader`——在 Windows 上强制子进程以 GBK 编码（codepage 936）输出中文，验证：
  1. 命令正常跑完（exit_code == 0）；
  2. 输出被捕获（含 U+FFFD 替换符）；
  3. 会话在非 UTF-8 输出后仍然存活可用。

### 验证

| 套件 | 结果 |
|---|---|
| `tests/test_shell.py`（含新回归测试） | 12 passed |
| `tests/test_secrets.py` | 7 passed |
| `tests/test_merge_pr_script.py` | 7 passed |
| `tests/test_github_installs.py` + `tests/test_codemode.py` | 27 passed |
| `tests/test_sandbox.py` | 16 passed |
| ruff（全部改动文件） | All checks passed |

> 本机即 zh-CN Windows（GBK 默认代码页），因此新回归测试是**真实复现环境**，不是模拟。

## 三、已累积完成项（截至 2026-11-08，证据见前次报告）

### CI / 测试基线

| 项 | 状态 | 证据 |
|---|---|---|
| 双平台 CI（ubuntu + windows） | ✅ | `.github/workflows/ci.yml` |
| Ruff 静态检查 | ✅ | `pyproject.toml` select E4/E7/E9/F |
| 后端回归基线 | ✅ | 2170 passed / 9 skipped（2026-11-08） |
| GUI 单测 / Playwright e2e | ✅ | 46 文件 / 214 用例；238 用例全过 |
| i18n 校验 | ✅ | 848 keys / 882 entries |
| Eval gate（stub 黄金用例） | ✅ | `.github/workflows/eval.yml` `gate-stub` |

### 核心功能（P0/P1/P2 全部闭合）

- Windows CI job + 平台用例修复、6 项新功能 e2e specs、App.tsx 拆分（16 hooks）
- 沙箱 v2 全套：GUI 开关、文件工具入沙箱、后台任务、per-command probe + fallback、Windows Docker 验证
- Egress policy server + 工具 + API、OpenShell 真实 CLI 接入（生命周期 + 基线策略）、容器镜像发布
- Eval 闭环：确定性 stub 黄金用例 + CI gate、真实模型矩阵基线
- 2026-11 "borrowed harness" 11 项全部落地（turn ledger / fork-replay / PluginManifest / presets / code mode / harness registry / unified install / consent v2 / mid-turn steering / consent audit / eval capture）

## 四、剩余缺口（均非阻塞 1.0）

| 缺口 | 性质 | 建议 |
|---|---|---|
| OpenShell 在装有 NVIDIA OpenShell 的主机上实测 | 真实环境验证 | 当前为 fake-CLI 单测 + 架构正确；需在真实环境跑一轮 `sandbox create/exec/delete` |
| 真实模型矩阵在更多模型上跑通 | 真实环境验证 | 当前仅 deepseek:deepseek-v4.1 单模型；可扩展 gpt-4o / claude 等 |
| zustand UI-prefs slice | 文档判定低价值 | `gui-state-zustand.md` 标注「仅当出现非 React 消费方时再做」，可跳过 |

## 五、风险与建议

1. **真实环境验证仍是最大不确定性来源**：OpenShell 和真实模型矩阵均为单测/单模型验证，建议在 1.0 前安排一次真实环境集成演练。
2. **Windows 本机环境偶发 Winsock 异常**（getaddrinfo 崩溃 / WinError 10055），重启网络适配器可恢复；CI runner 不受影响。
3. **GitHub 网络间歇性阻断**：已沉淀 `.trae/skills/github-rest-push/` Skill，`github.com` 不通时可走 REST API 推送。
4. **编码策略已统一，但历史提交或第三方脚本仍可能产生非 UTF-8 输出**：`errors="replace"` 保证不崩溃，但乱码内容可能进入输出；如需保留原始字节，可考虑在需要精确回读的场景改用 `bytes` 模式 + 显式解码。

## 六、1.0 发布建议

满足以下条件即可发布 1.0：

- [ ] OpenShell 真实环境一轮跑通（非阻塞，可后置到 1.0.x）
- [x] CI 全绿 + 回归基线稳定
- [x] 文档归档完成
- [x] 无已知 P0/P1 缺陷
- [x] Windows 中文环境可靠性加固（GBK 解码崩溃已修复 + 回归测试）