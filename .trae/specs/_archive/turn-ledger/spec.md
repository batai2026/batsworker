# Spec：运行时轨迹账本（Turn Ledger，建议路线 P0-1）

状态：待批准
范围：新增 append-only 事件账本 + 引擎三个写入点 + 只读 API + 配置；不做 GUI、不做 fork/replay、不做 code mode

## 1. 背景与已确认事实

- 会话消息已由 `coworker/conversations.py` 以 append-only JSONL 持久化到 `conversations/<id>.jsonl`；`SessionRecord` 在 `coworker/sessions.py`。
- 现有可观测性只有 `coworker/observability.py` 的 `TraceStore`/`SessionTracer`，**只记 tool span**（name/耗时/状态）；系统提示、上下文注入、压缩边界、审批决策、实际 outbound 请求均不留痕。
- 引擎唯一 provider 出口是 [engine.py](file:///e:/tworkspace/openworker-0.2.1/coworker/engine.py#L2313-L2412) 的 `_outbound_messages()`，由 `_astream()`（L867-L877）调用；`_astream` 已在此处拿到最终的 `model / messages / settings`。
- 工具执行唯一入口是 `_execute_sync()`（L1645-L1711，工作线程），结果汇总在 `_record_result()`（L1788-L1860，事件循环）。
- 审批：`PERMISSION_REQUIRED` 在 L1512-L1547，结果分支在 L1554-L1610；reviewer 拒绝在 L1476-L1500；普通拒绝在 L1612-L1630。
- `_append_notice()`（L486-L495）是所有 `notice`（error/interrupted/compacted/retry/budget）的唯一落点，适合集中记录“关键事件”。
- `run()`（L363-L429）是正常 user turn 的唯一入口；`retry()`/`resume()`（L497-L521）直接调 `_loop()`，本次**不**为它们补 turn 边界（见决策 6）。
- Manager 的 `_attach_checkpoints()`（[manager.py](file:///e:/tworkspace/openworker-0.2.1/coworker/server/manager.py#L469-L487)）已按 `attach post-construction` 模式给引擎挂 `checkpoints` 与 `traces`，本次沿用同一模式挂 `trajectory`。
- 配置为分层 TOML：`coworker/config.py` 的 `Config` 数据类 + `_FIELDS` / `_GLOBAL_ONLY_FIELDS` 白名单。
- 只读路由在 [app.py](file:///e:/tworkspace/openworker-0.2.1/coworker/server/app.py#L832-L840)，会话类方法在 `coworker/server/manager_sessions.py`（SessionsMixin），实例状态在 `coworker/server/manager.py`。
- 测试基线：2054 passed / 9 skipped；Windows 跑 pytest 必须 `dangerouslyDisableSandbox`（沙箱内 asyncio 建 socket 报 WinError 10038）。

## 2. 决策

1. **只存元数据与摘要，不存内容**：outbound 请求、工具参数、工具结果一律只落 `sha256` 摘要、字节/字符数、参数键名；不落消息正文、命令全文、文件内容。这样账本能用于 diff/回放校验，又不复制会话内容、不引入新的泄密面。
2. **脱敏在写入层统一做**：`redact()` 递归处理，按敏感键名（token/secret/password/cookie/authorization/api_key/private_key/credential 等）整值替换，按已知密钥前缀（`Bearer`、`sk-`、`ghp_`、`xox[abprs]-`、`AKIA`、`AIza`、PEM 头）正则替换，超长字符串截断到 1200 字符。
3. **失败绝不影响 turn**：`SessionTrajectory.record()` 内部 `try/except Exception: pass`；引擎侧再包一层 `_traj()` 兜底；store 的裁剪也独立 try。
4. **默认开启、本地优先**：`trajectory_enabled = true`；账本落在 `<data-dir>/trajectory/<session>.jsonl`，与 `traces.db` 同级，不进入工作区（agent 不可写自己的账本）。两配置项均为 **user-global only**，仓库配置不能关闭账本。
5. **保留策略按条数**：`trajectory_max_records = 5000` / 会话文件；超出时原子重写（`<file>.tmp` + `os.replace`）保留最新 N 条。
6. **turn 边界只覆盖 `run()`**：`retry()`/`resume()` 产生的记录沿用当前 turn 号，不新增边界事件，避免为两条路径加 try/finally。
7. **读取 API 只读、无副作用**：`GET /v1/sessions/{id}/trajectory`，支持 `limit` / `since_seq`；即使 `trajectory_enabled=false` 也能读已存在的历史文件。
8. **不做 UI、不做 fork/replay、不改 docs**：留给后续 spec。

## 3. 数据模型

每行一条记录（UTF-8 JSONL）：

```json
{"v":1,"seq":12,"ts":1780000000.123,"session_id":"s1","turn":3,"kind":"model_request","data":{...}}
```

`kind` 与 `data` 字段：

| kind | data |
|---|---|
| `turn_start` | `mode`（`user`）、`input_chars`、`input_digest`、`has_source`、`has_display`、`message_count` |
| `turn_end` | `status` |
| `model_request` | `model`、`messages`（条数）、`bytes`、`digest`、`roles`（role→条数）、`tools`（工具名列表）、`settings`（设置键名列表） |
| `context_injection` | `chars`、`digest` |
| `tool_call` | `name`、`call_id`、`argument_keys`、`arguments_digest`、`arguments_chars` |
| `tool_result` | `name`、`call_id`、`status`、`duration_ms`（可缺省）、`result_digest`、`result_chars`、`reason`（可缺省） |
| `approval_requested` | `name`、`call_id`、`reason` |
| `approval_resolved` | `name`、`call_id`、`status`（approved/denied）、`outcome`（ApprovalOutcome 值） |
| `notice` | `kind`、`text`、其余标量字段 |

`model_request.roles` 只统计 `role` 出现次数；`model_request.settings` 只列键名（`model_settings` 可能含敏感值，绝不下发值）。

## 4. 精确改动清单

### 4.1 新文件 `coworker/trajectory.py`

```python
"""Turn ledger (trajectory) — append-only, redacted, local per-session event log."""

SCHEMA_VERSION = 1
DEFAULT_MAX_RECORDS = 5000
MAX_STRING_CHARS = 1200

def text_digest(text: str) -> str          # sha256(utf-8) 十六进制
def value_digest(value) -> str            # 规范化 JSON（sort_keys/separators/ensure_ascii=False/default=str）后 sha256；序列化失败退回 repr
def redact(value) -> Any                  # 递归脱敏 + 截断
def outbound_snapshot(messages, *, model, tool_names, settings_keys) -> dict
    # {"model", "messages", "bytes", "digest", "roles", "tools", "settings"}

class TrajectoryStore:
    def __init__(self, base_dir: str | Path, *, max_records: int = DEFAULT_MAX_RECORDS)
    def state(self, session_id: str) -> dict[str, int]   # 缓存 {"seq": n, "turn": n}，首次从文件解析
    def append(self, session_id: str, kind: str, data: dict, *, state: dict) -> dict
    def read(self, session_id: str, *, limit: int = 500, since_seq: int = 0) -> list[dict]
    def count(self, session_id: str) -> int

class SessionTrajectory:
    def __init__(self, store: TrajectoryStore, session_id: str)
    @property
    def turn(self) -> int
    def begin_turn(self, *, mode: str = "user", input: Any = None, **fields) -> None
    def end_turn(self, *, status: str = "ok") -> None
    def record(self, kind: str, **data) -> None   # 绝不抛异常
```

实现要点：
- 文件路径 `base_dir / f"{_safe_id(session_id)}.jsonl"`，`_safe_id` 把非 `[A-Za-z0-9_.-]` 替换为 `_`。
- `append`：`RLock` 内递增 `state["seq"]`，组装记录（`v/seq/ts/session_id/turn/kind/data=redact(data)`），`json.dumps(ensure_ascii=False)` 单行追加 + `flush`；`count` 超 `max_records` 时 `try: _trim()`。
- `_trim`：读全部行 → 保留最后 `max_records` 行 → 写 `.tmp` → `os.replace`；失败只 `pass`。
- `read`：逐行 `json.loads`，坏行跳过；`since_seq` 过滤；返回尾部 `limit` 条。
- `state`：缓存命中直接返回；否则解析文件得到末条 `seq` 与 `turn`（无文件则为 `{"seq": 0, "turn": 0}`）。
- `begin_turn`：`state["turn"] += 1` 后 `record("turn_start", mode=…, input_chars=len(str(input)), input_digest=value_digest(input), …)`。
- `redact`：键名归一化（小写、`-`→`_`）后命中 `_SECRET_KEYS`（`authorization`、`proxy_authorization`、`api_key`、`apikey`、`access_token`、`refresh_token`、`id_token`、`token`、`secret`、`client_secret`、`password`、`passwd`、`cookie`/`set_cookie`、`x_api_key`、`private_key`、`credential`/`credentials`）→ `"[redacted]"`；字符串值先做密钥前缀正则替换，再截断；`dict/list` 递归、最大深度 6。

### 4.2 `coworker/engine.py`

新增私有 helper（放在 `_outbound_messages` 之前）：

```python
def _traj(self, kind: str, **data: Any) -> None:
    tj = getattr(self, "trajectory", None)
    if tj is None:
        return
    try:
        tj.record(kind, **data)
    except Exception:
        pass  # 账本永远不能打断 turn
```

写入点：

1. `run()` L387 之后：`self._traj("turn_start", ...)`（若已挂 `SessionTrajectory`，改调 `tj.begin_turn(...)` 以推进 turn 号；否则直接 `_traj`）。实现：在 `run()` 内 `tj = getattr(self, "trajectory", None)`，`mode="user"`，`input=user_input`，记录 `has_source/has_display/message_count`。
2. `run()` 末尾 `try/finally`：`status = "ok"` 初始化；循环中若 `event.type is EventType.TURN_END` 则记下 `event.data.get("status")`；`finally` 里 `tj.end_turn(status=status)`。
3. `_astream()` L873-L877：拿到 `model/messages/settings` 后、`produce()` 之前：
   ```python
   self._traj("model_request",
              **trajectory.outbound_snapshot(
                  messages,
                  model=model,
                  tool_names=[s.get("function", {}).get("name", "") for s in (tools or [])],
                  settings_keys=sorted(settings.keys()),
              ))
   ```
   `tools` 变量已是 `registry.schemas()` 的结果。
4. `_outbound_messages()` L2405-L2410：`context` 非空时 `self._traj("context_injection", chars=len(context), digest=trajectory.text_digest(context))`。
5. `_execute_sync()`：
   - `started` 计时后、`registry.execute` 前：`self._traj("tool_call", name=…, call_id=…, argument_keys=sorted((tool_call.arguments or {}).keys()), arguments_digest=trajectory.value_digest(tool_call.arguments), arguments_chars=len(json.dumps(...)))`。
   - 异常返回前：`self._traj("tool_result", name=…, call_id=…, status="error", duration_ms=duration, result_digest=trajectory.text_digest(str(exc)), result_chars=len(str(exc)))`。
   - 成功返回前：`status="ok"` + `result_digest=value_digest(result)` + `result_chars=len(json.dumps(result, default=str))`。
6. `_record_result()`：不再重复记录（执行态已由 5 覆盖）。
7. `_interrupted_tool()` / `_mangled_tool()`：各补一条 `tool_result`（`status="interrupted"` / `"error"`，`reason` 固定短语）。
8. `_authorize()`：
   - reviewer 拒绝分支 L1476 附近：`tool_result(status="denied", reason="blocked by the safety reviewer")`。
   - 审批卡 L1548-L1553 之后：`approval_requested(name/call_id/reason)`。
   - 审批结果两个 `_audit(..., stage="approval_resolved")` 之后：`approval_resolved(name/call_id/status/outcome)`。
   - 普通拒绝 L1624-L1629：`tool_result(status="denied", reason=reason)`。
   - unknown tool L1632-L1639：`tool_result(status="error", reason="unknown tool")`。
9. `_append_notice()` L495 之后：`self._traj("notice", kind=kind, text=text, **fields)`（`fields` 只含标量，`redact` 兜底）。

### 4.3 `coworker/config.py`

```python
    # 运行时轨迹账本（turn ledger）：本地 append-only 元数据日志，默认开启。
    # 只记录摘要/计数，不记录正文；user-global only —— 仓库配置不能关闭账本。
    trajectory_enabled: bool = True
    trajectory_max_records: int = 5000
```

`_FIELDS` 与 `_GLOBAL_ONLY_FIELDS` 各加 `trajectory_enabled`、`trajectory_max_records`。

### 4.4 `coworker/server/manager.py`

在 `_attach_checkpoints()` 的 traces 块之后追加：

```python
if getattr(engine, "trajectory", None) is None:
    from ..config import load_config
    if load_config().trajectory_enabled:
        from ..trajectory import SessionTrajectory
        engine.trajectory = SessionTrajectory(self._trajectory_store(), session_id)
```

在 `__init__` 中 `self.trace_store = TraceStore(...)` 附近新增 `self._trajectory_store_obj = None`，并新增方法：

```python
def _trajectory_store(self):
    """Lazy TrajectoryStore bound to <data-dir>/trajectory (config read once)."""
    if self._trajectory_store_obj is None:
        from ..config import load_config
        from ..trajectory import TrajectoryStore
        cfg = load_config()
        self._trajectory_store_obj = TrajectoryStore(
            self.data_dir / "trajectory",
            max_records=int(cfg.trajectory_max_records),
        )
    return self._trajectory_store_obj
```

### 4.5 `coworker/server/manager_sessions.py`

```python
def session_trajectory(self, session_id: str, *, limit: int = 500, since_seq: int = 0) -> dict[str, Any]:
    store = self._trajectory_store()
    records = store.read(session_id, limit=limit, since_seq=since_seq)
    return {"session_id": session_id, "count": store.count(session_id), "records": records}
```

### 4.6 `coworker/server/app.py`

紧跟 timeline 路由（L834-L836）之后：

```python
@app.get("/v1/sessions/{session_id}/trajectory")
def session_trajectory(session_id: str, limit: int = 500, since_seq: int = 0) -> dict[str, Any]:
    return manager.session_trajectory(session_id, limit=limit, since_seq=since_seq)
```

### 4.7 新文件 `tests/test_trajectory.py`

覆盖：
1. `test_append_read_roundtrip`：写入 3 条 → `read` 顺序、`seq` 递增、`count` 正确。
2. `test_write_redacts_secret_keys_and_values`：含 `api_key`/`Authorization`/`Bearer sk-…`/`ghp_…` 的 data 落盘后原文不在文件里，出现 `[redacted]`。
3. `test_retention_keeps_newest`：`max_records=5` 写 8 条 → 文件 5 行且是最后 5 个 `seq`。
4. `test_record_never_raises`：指向不可写路径（`base_dir` 为普通文件）→ `record()` 不抛。
5. `test_outbound_snapshot_stable_and_content_free`：同内容两次摘要相等；快照文本不含消息正文。
6. `test_engine_turn_writes_ledger`：复用 `tests/test_engine.py` 的 `ScriptedProvider`/`ToolRegistry` 构造方式，挂真实 `SessionTrajectory`，跑一个 `_text_turn` turn → 记录序列包含 `turn_start`/`model_request`/`turn_end`，`turn_end.status == "completed"`（以 `TURN_END` 事件值为准）。
7. `test_tool_turn_records_args_and_result_metadata`：`_tool_turn("write_file", {...})` → 有 `tool_call`/`tool_result`，且 `data` 中无写入正文（仅 digest/keys）。
8. `test_manager_attach_respects_config`：`trajectory_enabled=false` 时 `_attach_checkpoints` 不挂 `engine.trajectory`（monkeypatch `coworker.config.load_config`）。
9. `test_read_api_route`：`app` 的 `GET /v1/sessions/{id}/trajectory` 返回 `records`（可复用现有 app fixture；若成本高，改为直接调 `manager.session_trajectory`）。

## 5. 验证

1. 静态：`.\.venv\Scripts\ruff.exe check coworker/trajectory.py tests/test_trajectory.py coworker/engine.py coworker/config.py coworker/server/manager.py coworker/server/manager_sessions.py coworker/server/app.py` → 0 errors。
2. 目标单测（Windows 必须 `dangerouslyDisableSandbox`）：
   `.\.venv\Scripts\python.exe -m pytest tests/test_trajectory.py tests/test_engine.py tests/test_engine_stop.py tests/test_harness_v2.py tests/test_harness_optimizations.py tests/test_compaction_engine.py -q`
3. 涉及 manager/server 的子集：`tests/test_server.py tests/test_observability.py tests/test_sessions.py`（若 `test_sessions.py` 不存在则跳过）。
4. 全量 `pytest tests -q`（Windows 加 `dangerouslyDisableSandbox`，约 34 分钟）→ 期望不低于基线 **2054 passed / 9 skipped**（新增用例数另计）。
5. 冒烟：`python -c "import coworker.trajectory, coworker.engine, coworker.server.app, coworker.server.manager"`。
6. 手工抽查：跑一个测试会话后查看 `<tmp>/trajectory/<sid>.jsonl`，确认无正文、无密钥、`seq` 连续。

## 6. 风险与回滚

- 风险：`_outbound_messages` / `_execute_sync` 增加同步 IO 会带来每次模型调用/工具调用一次 append（小文件、flush 不 fsync）→ 延迟可忽略；若磁盘慢，`record` 吞异常。
- 风险：`value_digest` 对不可 JSON 序列化结果回退 `repr`，不同对象可能撞摘要 —— 仅用于稳定性比对，可接受。
- 风险：多线程（工具 worker + 事件循环）同时 append → `RLock` 串行，行完整性有保证。
- 回滚：删除 `engine.trajectory` 挂载 + 配置默认改 `false` 即可一键关闭；`coworker/trajectory.py` 独立，无其他模块反向依赖。
- 不做：GUI 时间线、fork/replay、code mode、docs 更新。

## 7. 验收标准

- 新账本在真实 turn 中稳定写入 `turn_start → model_request → tool_call/tool_result → turn_end`，任何写入异常都不改变原有事件序列。
- 落盘文件不含消息正文、工具参数值、工具结果内容或任何密钥原文。
- 超过 `trajectory_max_records` 时文件被原子裁剪到最新 N 条，进程不崩。
- `GET /v1/sessions/{id}/trajectory` 返回 `{session_id, count, records}`，支持 `limit`/`since_seq`。
- 全量 pytest 不低于基线；ruff 0 errors。
