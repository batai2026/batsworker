# GUI 活动会话状态迁入 zustand（activeSessionStore）

## 背景

`docs/design/gui-state-zustand.md` 的切片计划把 `activeSessionStore`
（`sessionId` / `workspace` / `branch` / `agent` / `model`）列为下一个候选：
这五个值被 boot、connect、sendGate、sessionActions 四个 hook 共享，且 websocket
回调等非组件代码需要随时读取当前活动会话。它们目前是 `App.tsx` 的 5 个
`useState`，只能通过 `SessionActionsDeps` 等依赖对象逐层透传。

## 目标

把这五个共享值迁入 `src/stores/activeSessionStore.ts`，`App.tsx` 改为订阅 store。
不改变任何用户可见行为，也不改动任何 hook 的入参/返回形状。

## 约束

- setter 保持 React `Dispatch<SetStateAction<T>>` 形状（含 updater 函数），
  `useBoot` / `useSessionConnect` / `useSendGate` / `useSessionActions` 的依赖类型不变。
- `tempWorkspace` / `draftFolderPicked` / `showGate` 等局部状态继续留在 `App.tsx`。
- `useRef` 与每 token 热路径状态（`streaming` / `reasoningStream` / `items`）不进 store。
- 单例 store 在测试间会泄漏：新测试文件必须在 `beforeEach` 重置。

## 交付物

- `src/stores/activeSessionStore.ts`（新增）：五个字段 + Dispatch 形状 setter，
  并把 `newSessionId()`（原 `App.tsx` 的 `newId`）移入，供初始值与新建会话共用。
- `src/App.tsx`：5 个 `useState` 改为一次 store 订阅；其余传参不动。
- `src/stores/activeSessionStore.test.ts`（新增）：默认值、updater 语义、字段独立性。
- `docs/design/gui-state-zustand.md` + `maturity-roadmap.md` +
  `openworker-design-zh.md`：状态表与剩余切片同步。

## 验证

- `npx tsc --noEmit` 通过。
- `npm test`（全量 vitest）通过。
- `npm run e2e` 通过（会话切换/新建/文件夹门禁路径依赖这些值）。
- `python surfaces/gui/scripts/check_i18n.py` 通过（无新增文案）。
- 后端无改动，仍按仓库约定跑一次全量 pytest 后提交。

## 非目标

- 不迁移 `runStore`（`running` / `items` / `streaming` 等热路径）与 UI 偏好切片。
- 不删除 `App.tsx` 到各 hook 的 props 透传（第二遍再做）。
- 不改变 store 的订阅粒度（`App.tsx` 仍整体订阅，与 5 个 `useState` 等价）。
