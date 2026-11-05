# Spec：会话分叉与回放（Session Fork/Replay，建议路线 P0-2）

状态：待批准
范围：fork API（turn 边界复制会话）+ replay API（后台重跑输入）+ 轨迹 diff 只读 API；不做 GUI、不做工作区快照、不做批量回放、不做 code mode

## 1. 背景与已确认事实

- P0-1 轨迹账本已落地：`coworker/trajectory.py` 的 `turn_start` 记录含 `message_count`（[engine.py](file:///e:/tworkspace/openworker-0.2.1/coworker/engine.py#L392-L402)，在用户消息 append **之后**记录），这就是 turn 号 → 消息下标的权威映射：turn N 的切割点 = `turn_start(N).data.message_count - 1`（turn N 用户消息之前的所有消息）。
- 会话消息持久化：`conversations/<id>.jsonl` append-only + SQLite `sessions` 索引（[conversations.py](file:///e:/tworkspace/openworker-0.2.1/coworker/conversations.py#L62-L167)）；新列用既有 `ALTER TABLE … except OperationalError` 迁移模式（L86-L105）。
- `SessionRecord`（[sessions.py](file:///e:/tworkspace/openworker-0.2.1/coworker/sessions.py)）携带 workspace/model/mode/agent/extra_roots/grants/compaction/bindings/team 等元数据；`save()` 对新 id 自动建 JSONL。
- 会话 id 生成惯例：`uuid.uuid4().hex[:12]`（cli.py L75、manager_teams.py L478）。
- turn 只能经 WS `/ws/session/{id}` 触发；但服务端已有后台 turn 通道 `deliver_to_session()`（[manager_connectors.py](file:///e:/tworkspace/openworker-0.2.1/coworker/server/manager_connectors.py#L581-L623)）：`try_mark_running` 认领 → `engine.run()` → 事件经 `broadcast_session` 推送 → `save` → `mark_idle`。replay 直接复用，审批走既有 Inbox/Unattended 管线。
- live engine 的内存消息领先于持久化记录，`session_messages()`（manager_sessions.py L329-L337）已封装"live 优先"读取，fork 读取消息时沿用。
- checkpoints（`checkpoints/<sid>/turns.json`）的 seq 属于源会话血缘，fork 不复制；fork 从自己的 turn 1 重新编号（trajectory 同理，新会话新文件从 seq 1 开始）。
- 服务端测试模式：`SessionManager(workspace=tmp_path, provider=ScriptedProvider(turns))` + `TestClient(create_app(manager))`（tests/test_server.py L40-L41）。
- 测试基线：2064 passed / 9 skipped；Windows 跑 pytest 必须 `dangerouslyDisableSandbox`。

## 2. 决策

1. **fork 语义 = "在 turn N 之前切开"**：`POST /v1/sessions/{id}/fork` body `{"turn": N}`（可选）。带 turn → 新会话含 turn N 之前的全部消息（可随即回放 turn N）；不带 turn → tip fork，整体复制。切割点只从 trajectory 账本解析（消息列表里 steering/注入也是 role=user，无法可靠数 turn）；账本缺失或无该 turn → 400。
2. **fork 只复制会话状态，不复制工作区/文件状态**：两分支共享同一 workspace 目录，源会话后续 turn 对文件的改动不回滚（工作区回滚走源会话既有 checkpoint API，两件事正交）。trajectory/checkpoints 均不复制——新会话按新 id 天然隔离、各自从 1 编号。
3. **血缘落 `SessionRecord.fork` 字段**：`{"from_session", "from_turn", "cut_messages"}`（tip fork 的 `from_turn` 为 null）。sessions 表加 `fork TEXT` 列（既有 ALTER 模式）；`origin`/`origin_label` 不动（fork 不是外部来源，不改变 sidebar 分组）；`list_sessions` 行内暴露 `fork` 供后续 UI 使用。
4. **元数据继承规则**：workspace/model/mode/agent/extra_roots/grants/bindings 全部继承（grants 是同一用户在同一血缘里的授权意图）；**team 不继承**（fork 是普通会话，不带团队身份）；**compaction 仅 tip fork 继承**（mid-cut 后压缩锚点对不齐截断的消息列表，引擎按需重新压缩）。title = 源标题截断 + " (fork)"，≤80 字符。
5. **replay 复用后台 turn 通道**：`POST /v1/sessions/{id}/replay` body `{"input": "..."}` 可选。省略 input → 按血缘取源会话切割点处那条 role=user 消息的文本（`content_to_text` 抽取，附件占位）作为输入；取不到或无血缘 → 400。执行 = 后台 `asyncio.create_task(deliver_to_session(...))`，task 引用挂 manager 防 GC（同 `_autotitle_tasks` 模式），路由立即返回 202 + `input_source`（lineage/override）+ `input_chars`。目标会话运行中 → 409（replay 不 steering 插队，保持语义干净）。
6. **diff = 逐条比对 model_request 摘要**：`GET /v1/sessions/{id}/trajectory/diff?other={sid}&from_turn=1&other_from_turn=1`。两侧各取 `kind=="model_request"` 且 `turn >= from_turn` 的记录，按序对齐逐一比 `digest`；返回 pairs（turn/bytes/model/digest_match）+ `matched`/`compared` 汇总。fork 两侧 turn 编号有天然偏移（fork 从 1 重编），故由调用方给两侧起始 turn；fork 响应里附建议的 diff 查询参数。缺侧记 `missing`。
7. **fail-closed**：fork/replay 拒绝 `__` 前缀内部会话；fork 拒绝运行中的源会话（409，防拷到半 turn 状态）；fork 对源会话只读不写；replay 的 input 与 WS 输入同管线，不绕过任何权限/审批；无新增配置项（fork/replay 都是显式用户动作，无需开关）。
8. **不做**：GUI（分支树/对比视图）、工作区快照与恢复、自动批量回放、retry/resume 路径的 replay 特化、code mode、docs 更新。

## 3. API 形态

```
POST /v1/sessions/{id}/fork        {"turn": 2}            # turn 可选
→ 200 {"ok": true, "session_id": "ab12cd34ef56",
       "fork": {"from_session": "…", "from_turn": 2, "cut_messages": 7},
       "messages": 7, "title": "… (fork)",
       "diff_hint": "/v1/sessions/ab12cd34ef56/trajectory/diff?other=…&from_turn=1&other_from_turn=2"}
→ 400 {"detail": "turn 9 not found in trajectory records"} / 404 / 409 / 403

POST /v1/sessions/{id}/replay      {"input": "…"}          # input 可选
→ 202 {"ok": true, "session_id": "…", "input_source": "lineage", "input_chars": 42}
→ 400 {"detail": "no recorded input for the forked turn"} / 404 / 409

GET  /v1/sessions/{id}/trajectory/diff?other={sid}&from_turn=1&other_from_turn=2
→ 200 {"left": "…", "right": "…",
       "pairs": [{"i": 0, "turn_left": 1, "turn_right": 2,
                  "digest_match": true, "bytes_left": 812, "bytes_right": 812,
                  "model_left": "gpt-5.5", "model_right": "gpt-5.5"}, …],
       "matched": 3, "compared": 4}
```

## 4. 精确改动清单

### 4.1 `coworker/sessions.py`

`SessionRecord` 新增字段（含中文注释）：

```python
    # 会话分叉血缘（fork/replay）：{} = 非 fork 会话。
    # {"from_session": 源会话 id, "from_turn": int | None, "cut_messages": int}
    fork: dict[str, Any] = field(default_factory=dict)
```

### 4.2 `coworker/conversations.py`

- `__init__` 的 ALTER 列表加 `"ALTER TABLE sessions ADD COLUMN fork TEXT"`。
- `save()`：INSERT/UPDATE 的列清单与参数加 `fork = json.dumps(record.fork or {})`。
- `load()`：构造 `SessionRecord` 时加 `fork=_load_grants(row["fork"])`（复用现有的 dict 解析 helper，坏 JSON → {}）。

### 4.3 `coworker/server/manager.py`

`__init__` 在 `self._autotitle_tasks` 附近加：

```python
self._replay_tasks: set[asyncio.Task] = set()  # 后台 replay turn 的 task 引用（防 GC）
```

### 4.4 `coworker/server/manager_sessions.py`

三个新方法（SessionsMixin）：

```python
def fork_session(self, session_id: str, *, turn: Optional[int] = None) -> dict[str, Any]:
    # 1) 校验：__ 前缀 → 403；record 不存在 → 404；is_running → 409
    # 2) 解析切割点：turn is None → cut = len(msgs)
    #    否则读 trajectory：找 turn==N 的 turn_start → cut = data.message_count - 1
    #    找不到 → 400（"turn N not found in trajectory records"）
    # 3) msgs = self.session_messages(session_id)（live 优先）；cut 越界保护
    # 4) 组装新 SessionRecord：新 id（uuid4().hex[:12]）、messages=copy.deepcopy(msgs[:cut])、
    #    继承规则按决策 4、title=源标题[:60] + " (fork)"（≤80）、
    #    fork={"from_session": session_id, "from_turn": turn, "cut_messages": cut}
    # 5) self.session_store.save(record)；返回 200 载荷（含 diff_hint）

async def replay_session(self, session_id: str, *, input: Optional[str] = None) -> dict[str, Any]:
    # 1) 校验：__ 前缀 → 403；record 不存在 → 404；is_running → 409
    # 2) input 省略：需 record.fork["from_turn"] 非 None，
    #    src_msgs = self.session_messages(record.fork["from_session"])，
    #    取 src_msgs[record.fork["cut_messages"]]（应即源 turn N 的 user 消息），
    #    content_to_text 抽取文本；越界/非 user/空文本 → 400
    # 3) task = asyncio.get_running_loop().create_task(self.deliver_to_session(session_id, text))
    #    self._replay_tasks.add(task); task.add_done_callback(self._replay_tasks.discard)
    # 4) 返回 202 载荷（input_source / input_chars）

def trajectory_diff(self, session_id: str, other: str, *,
                    from_turn: int = 1, other_from_turn: int = 1) -> dict[str, Any]:
    # 两侧 store.read(limit=100000) 取全量，筛 kind=="model_request" 且 turn>=起始，
    # 按序 zip 对齐比对 digest；任一侧耗尽后剩余侧记 missing 不进入 compared
```

### 4.5 `coworker/server/app.py`

紧跟 trajectory 路由（L839-L848）之后加三个路由：fork（`Body` 解析可选 JSON）、replay（async，202 用 `status_code=202`）、diff（GET，query 参数）。错误分支按 manager 返回的 `error` 映射 HTTPException（400/403/404/409）。

### 4.6 新文件 `tests/test_fork_replay.py`

复用 test_server.py 的 `ScriptedProvider`/`SessionManager`/`TestClient` 模式与 test_engine.py 的 `_text_turn`/`_tool_turn`：

1. `test_tip_fork_copies_everything`：跑 2 turn → tip fork → 消息数/元数据/grants/compaction 一致，血缘 `from_turn` 为 None，源会话消息不变。
2. `test_turn_fork_cuts_at_boundary`：跑 2 turn（账本开启）→ fork turn=2 → 新会话消息 = turn 1 产出（= turn 2 的 turn_start.message_count - 1 条）。
3. `test_fork_errors`：turn=99 → 400；源会话运行中 → 409；`__x` 会话 → 403；不存在 → 404。
4. `test_fork_inheritance_rules`：mid-cut fork 丢弃 compaction、tip fork 保留；grants 继承；team 不继承；title 带 " (fork)"。
5. `test_replay_uses_lineage_input`：fork turn=2 后 replay（无 input）→ 新会话跑一个 turn，provider 收到的用户文本 == 源会话 turn 2 的用户输入；`input_source=="lineage"`。
6. `test_replay_override_and_errors`：带 input 覆盖生效；无血缘 + 无 input → 400；运行中 → 409。
7. `test_trajectory_diff`：源会话与 fork 各跑相同 ScriptedProvider 脚本 → 首个 model_request digest 一致（`digest_match` true）；换脚本 → false；`from_turn`/`other_from_turn` 偏移生效；缺侧记 missing。
8. `test_fork_independence`：fork 后再跑新 turn → 源会话消息数不变、fork 的 trajectory 文件独立（turn 从 1 起）。
9. `test_routes`：TestClient 三端点 happy path + 错误码（400/403/404/409/202）。

## 5. 验证

1. 静态：`ruff check coworker/sessions.py coworker/conversations.py coworker/server/manager.py coworker/server/manager_sessions.py coworker/server/app.py tests/test_fork_replay.py` → 0 errors。
2. 目标单测（`dangerouslyDisableSandbox`）：`pytest tests/test_fork_replay.py tests/test_server.py tests/test_trajectory.py tests/test_conversations.py -q`（test_conversations.py 若存在）。
3. 引擎子集回归：`tests/test_engine.py tests/test_engine_stop.py tests/test_harness_v2.py`。
4. 全量 `pytest tests -q`（`dangerouslyDisableSandbox`，约 34 分钟）→ 不低于基线 **2064 passed / 9 skipped**（新增用例另计）。
5. 冒烟：`python -c "import coworker.sessions, coworker.conversations, coworker.server.app, coworker.server.manager"`。
6. 手工抽查：fork 后源 `<sid>.jsonl` 字节数不变；fork 的新 JSONL 条数 == cut；`coworker.db` 的 fork 列有血缘 JSON。

## 6. 风险与回滚

- 风险：fork 深拷贝消息（大会话内存峰值）—— 单会话消息量级 MB 内，可接受；copy.deepcopy 防两分支共享可变 dict。
- 风险：replay 后台 turn 的审批无人值守时堆积 Inbox —— 与既有 background delivery 行为一致，不新增面。
- 风险：diff 的 `store.read` 全量读取（默认 limit=500 不够）—— diff 内部用大 limit 显式读全量；单文件 ≤5000 条（账本自带裁剪），内存可控。
- 回滚：三个路由 + 两个 mixin 方法独立，删除即回退；`fork` 列存在但恒 `{}` 时行为与现状完全一致。
- 不做：GUI、工作区快照、批量回放、code mode、docs 更新。

## 7. 验收标准

- fork 在任意 turn 边界切分准确（以账本 message_count 为准），源会话零写入。
- replay 无 input 时精确重放源 turn 的用户输入；有 input 时用 input；全程走既有权限/审批管线。
- diff 能对齐两会话的 model_request 序列并给出逐条 digest_match；from_turn 偏移正确。
- fork 血缘（from_session/from_turn/cut_messages）持久化并可经 list_sessions 读取。
- 全量 pytest 不低于基线；ruff 0 errors。
