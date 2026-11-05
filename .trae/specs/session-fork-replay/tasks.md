# Tasks：会话分叉与回放（Session Fork/Replay）

依赖顺序执行；每步完成即更新状态。

- [ ] T1 `coworker/sessions.py` + `coworker/conversations.py`：`SessionRecord.fork` 字段；sessions 表 `fork TEXT` 列（ALTER 迁移模式）；save/load 序列化往返
- [ ] T2 `coworker/server/manager.py`：`__init__` 新增 `_replay_tasks` set（防 GC 引用）
- [ ] T3 `coworker/server/manager_sessions.py`：`fork_session()`（校验、账本边界解析、deepcopy、继承规则、血缘落盘、diff_hint）
- [ ] T4 `coworker/server/manager_sessions.py`：`replay_session()`（lineage 输入解析、create_task + retain、202 载荷）+ `trajectory_diff()`（model_request 按序对齐、from_turn 偏移、missing）
- [ ] T5 `coworker/server/app.py`：`POST /fork`、`POST /replay`（202）、`GET /trajectory/diff` 三路由 + 错误码映射
- [ ] T6 新建 `tests/test_fork_replay.py`：覆盖 spec §4.6 的 9 个用例
- [ ] T7 静态检查：`ruff check` 本次改动文件 → 0 errors
- [ ] T8 目标回归：`pytest tests/test_fork_replay.py tests/test_server.py tests/test_trajectory.py tests/test_conversations.py -q`（Windows 加 dangerouslyDisableSandbox）
- [ ] T9 全量回归：`pytest tests -q`（基线 2064 passed / 9 skipped）
- [ ] T10 冒烟与抽查：改动模块 import 冒烟；fork 后源 JSONL 字节数不变、fork JSONL 条数 == cut、coworker.db fork 列有血缘 JSON
