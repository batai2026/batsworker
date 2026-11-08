# GUI 运行态迁入 zustand（runStore + useSessionStream）

## 背景

`docs/design/gui-state-zustand.md` 的切片计划里，`runStore`（`running` / `compacting` /
`streaming` / `reasoningStream` / `items` / `todo` / `usage`）是第三个切片，但标注为
hot path，门禁条件是「先把 streaming 移入 hook」。当前 `App.tsx` 仍持有 7 个对应
`useState`，其中 `streaming` / `reasoningStream` 还各自维护一个时序敏感的 ref 镜像
（WS 回调闭包建一次，必须在事件到达时读到实时缓冲）。

## 目标

1. 新增 `src/stores/runStore.ts`：承载 7 个渲染态字段 + Dispatch 形状 setter。
2. 新增 `src/hooks/useSessionStream.ts`：把两个 ref 镜像与 setter 包装从 `App.tsx`
   移入 hook（满足切片门禁），hook 内部同步写入 store。
3. `App.tsx` 改成一次 store 订阅 + 一次 streaming hook 调用；所有 hook 入参与
   props 形状不变。

不改变任何用户可见行为：流式追加、思考流、停止按钮、compacting 提示、
用量统计、todo 面板、会话切换时的状态重置全部保持原语义。

## 约束

- ref 镜像不进 store：它们每个 token 都被读到，且不能触发重渲染。
- setter 保持 React `Dispatch<SetStateAction<T>>` 形状（含 updater 函数），
  `useBoot` / `useSessionConnect` / `useSessionActions` / `useSendGate` /
  `useGateResponders` 的依赖类型不变。
- 订阅粒度仍是整体订阅（与 7 个 `useState` 的渲染行为等价）。
- 单例 store 在测试间会泄漏：新测试文件必须在 `beforeEach` 重置。

## 交付物

- `src/stores/runStore.ts`（新增）：字段 + setter。
- `src/hooks/useSessionStream.ts`（新增）：`streamingRef` / `reasoningRef` +
  `setStreaming` / `setReasoningStream` 包装。
- `src/App.tsx`：7 个 `useState` 与本地 ref/包装函数改为 store 订阅 + hook。
- `src/stores/runStore.test.ts`（新增）：默认值、updater 语义、字段独立性。
- `src/hooks/useSessionStream.test.ts`（新增）：ref 镜像与 store 值同步、清空语义。
- `docs/design/gui-state-zustand.md`、`maturity-roadmap.md`、
  `openworker-design-zh.md`：切片状态同步。

## 验证

- `npx tsc --noEmit` 通过。
- `npm test`（全量 vitest）通过。
- `npm run e2e` 通过（流式渲染、停止、会话切换、用量/todo 面板路径）。
- `python surfaces/gui/scripts/check_i18n.py` 通过（无新增文案）。
- 后端无改动：说明未重跑全量 pytest。

## 非目标

- 不迁移 UI 偏好切片与 local-only 状态。
- 不删除 `App.tsx` 到各 hook 的 props 透传（第二遍再做）。
- 不改 WS 事件 switch 所在位置（仍在 `useSessionConnect`）。
