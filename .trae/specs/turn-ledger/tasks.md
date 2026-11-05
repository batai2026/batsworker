# Tasks：运行时轨迹账本（Turn Ledger）

依赖顺序执行；每步完成即更新状态。

- [x] T1 新建 `coworker/trajectory.py`：`redact`/`text_digest`/`value_digest`/`outbound_snapshot` + `TrajectoryStore`（JSONL append、seq/turn 状态缓存、按条数原子裁剪、容错读取）+ `SessionTrajectory`（`begin_turn`/`end_turn`/`record` 全吞异常）
- [x] T2 `coworker/config.py`：新增 `trajectory_enabled=True`、`trajectory_max_records=5000`，并加入 `_FIELDS` 与 `_GLOBAL_ONLY_FIELDS`
- [x] T3 `coworker/engine.py` 接线：`_traj` helper；`run()` 的 turn_start/turn_end（含 TURN_END status 采集）；`_astream()` 的 model_request；`_outbound_messages()` 的 context_injection
- [x] T4 `coworker/engine.py` 工具与审批写入点：`_execute_sync` 的 tool_call/tool_result；`_interrupted_tool`/`_mangled_tool`；`_authorize` 的 approval_requested/approval_resolved、reviewer 拒绝、普通拒绝、unknown tool；`_append_notice` 的 notice
- [x] T5 `coworker/server/manager.py`：`_trajectory_store_obj` 字段 + `_trajectory_store()` 懒加载 + `_attach_checkpoints` 内按 `trajectory_enabled` 挂 `SessionTrajectory`
- [x] T6 `coworker/server/manager_sessions.py`：新增 `session_trajectory(session_id, *, limit, since_seq)`
- [x] T7 `coworker/server/app.py`：新增 `GET /v1/sessions/{session_id}/trajectory`
- [x] T8 新建 `tests/test_trajectory.py`：覆盖 spec §4.7 的 9 个用例（读写、脱敏、裁剪、不抛异常、摘要稳定、engine turn、tool turn、配置开关、只读 API）
- [x] T9 静态检查：`ruff check` 本次改动文件 → 0 errors
- [x] T10 目标回归：`pytest tests/test_trajectory.py tests/test_engine.py tests/test_engine_stop.py tests/test_harness_v2.py tests/test_harness_optimizations.py tests/test_compaction_engine.py -q`（Windows 加 dangerouslyDisableSandbox）——10 + 53 passed
- [x] T11 服务端子集：`pytest tests/test_server.py tests/test_observability.py -q`——53 passed
- [x] T12 全量回归：`pytest tests -q` → **2064 passed / 9 skipped**（1983s；基线 2054 + 新增 10 个用例）；第 11 个 context_injection 用例因在全量启动后补加，单独补跑 `pytest tests/test_trajectory.py -q` → 11 passed
- [x] T13 冒烟与抽查：改动模块 `py_compile` 通过；pytest 目标子集已成功导入 engine/app/manager；沙箱内功能校验确认落盘无密钥原文、`seq` 连续（裁剪后为 [3,4,5]）
