# Tasks：Code mode 形态 B 实现

依赖顺序执行；每步完成即更新状态。前置：P2-5 全量回归通过并已提交。

- [ ] T1 `coworker/codemode/bridge.py`：per-session 桥接 HTTP 服务（token + 白名单 + Approver 门 + 16 次上限）
- [ ] T2 `coworker/codemode/guest.py`：guest runner（tools 代理、print 收集、错误→stderr）+ docker run 管线
- [ ] T3 `coworker/codemode/exec_tool.py`：exec(code) 工具，fail-closed + 上限 + code_exec 轨迹
- [ ] T4 `coworker/catalog.py`：`code_mode` 槽 + exec description 从会话工具集生成签名
- [ ] T5 `coworker/personas/builtin/code-mode/manifest.md` preset
- [ ] T6 `coworker/trajectory.py`：record_code_exec
- [ ] T7 `tests/test_codemode.py`（无 Docker 依赖）+ preset/槽位断言更新
- [ ] T8 ruff 0 errors + 目标回归
- [ ] T9 冒烟：stub 引擎下 exec fail-closed；可选真实 Docker 验证
- [ ] T10 全量回归（基线 2115 passed / 9 skipped）+ 提交
