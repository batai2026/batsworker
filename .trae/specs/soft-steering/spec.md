# P2-9 运行中软引导（soft steering）

## 背景与现状（重要修正）

引擎**已经有引导能力**：`TurnEngine.queue_steering(text, source)` 入队，
`_inject_steering()` 在每轮模型请求前与「模型不再请求工具」的收尾点注入为 user 消息
（[engine.py](file:///e:/tworkspace/openworker-0.2.1/coworker/engine.py#L359-L362)）。
连接器消息（`manager_connectors.feed_session`）已在用它。

缺口只在**交互入口**：

- GUI 在 `running` 时用 Stop 按钮替代发送按钮，且 `submit()` 里 `(running && !gateOpen)` 直接 return；
- 服务端 `claim_turn` 对运行中的 `user_message` 一律回 `input_rejected`，消息丢失。

## 目标

turn 运行中用户提交的**纯文本**消息入队，在下一个模型请求边界生效；GUI 允许边跑边发并给出排队提示。

## 设计

### 服务端
1. `manager_sessions.steer_session(session_id, text) -> bool`：引擎存在且会话在跑时
   `engine.queue_steering(text)` 并返回 True，否则 False。
2. `app.py` WS `user_message` 分支：在现有校验之后、
   `turn`（模型/技能/附件）处理之前插入：

   ```
   if manager.is_running(session_id) and not 有待决 Inbox 门:
       if attachments or skill:  -> input_rejected("中途引导只支持纯文本…")
       if manager.steer_session(session_id, text): -> {"type": "steering_queued", "data": {"text": text}}
       else: 维持现有 input_rejected
   ```
   - 有 `manager.inbox.pending(session_id)` 时不入队，保持现有拒绝——审批门回复优先。
   - `model` 字段随每条消息发送；引导期间忽略它（不切模型、不做首轮绑定）。
   - 空文本 + 无附件维持现有「无内容」拒绝。

### GUI
3. `Composer.tsx`：running 时同时渲染 Stop 与 Send；`submit()` 去掉 running 拦截；
   运行中发送按钮 title 改为「作为中途引导（下一步生效）」。
4. `useSessionConnect.ts` + `types.ts`：新增 `steering_queued` 事件 → notice
   「已排队，将在下一步生效」。
5. `i18n/zh/app.ts`：补两条中文翻译，保证 `check_i18n.py` 通过。

## 非目标

- 不支持中途带附件/技能（会明确拒绝并提示 turn 结束后再发）
- 不打断当前工具调用（那是 Stop 的语义）
- 不新增引擎逻辑、不新增队列上限（WS 已有速率限制；连接器路径同样无上限）

## 验证

- `tests/test_server.py`：monkeypatch `is_running/steer_session` → 运行中纯文本收到
  `steering_queued` 且文本入队；带附件 → `input_rejected`；空闲路径不受影响。
- 新用例：`steer_session` 在运行中写入引擎 `_steering`，空闲/无引擎返回 False。
- GUI `npm test`（Composer 行为改动需覆盖）+ `check_i18n.py`。
- `ruff check coworker tests` + 全量 pytest（基线以 P2-8 回归结果为准）。
