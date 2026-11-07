# GUI 状态 zustand 迁移（预案 + 试点）

## 背景

`App.tsx`（1423 行、40+ `useState`）在抽出 15 个 `use*` hooks 后仍然过大，跨组件状态
只能靠 props 与 hook 依赖对象透传。非组件代码（websocket 回调、导航工具）无法直接
读取当前会话列表。

## 目标

1. 产出可执行的切片计划（哪些状态进 store、哪些保持局部、风险与回滚）。
2. 落地一个低风险试点：会话列表 + 最近项目（`sessions` / `projects`）迁入 zustand，
   hook 返回形状不变，不改任何组件与 `App.tsx`。

## 约束

- 试点不改变任何用户可见行为；`useSessions` 的返回值、刷新时机（挂载、5s 轮询、
  `PERSONAS_CHANGED`）保持原样。
- setter 保持 React `Dispatch<SetStateAction<T>>` 形状，避免改动 `App.tsx` props 类型。
- `streaming` / `items` 等每 token 热路径状态与所有 `useRef` 不进 store（避免额外重渲染）。
- 单例 store 在测试间会泄漏，测试文件必须在 `beforeEach` 重置。

## 交付物

- `src/stores/sessionStore.ts`：`sessions` / `projects` / setter / `refreshSessions`。
- `src/hooks/useSessions.ts`：改为读取与调用 store，返回结构不变。
- `src/stores/sessionStore.test.ts`（新增）+ `useSessions.test.ts` 增加 store 重置。
- `docs/design/gui-state-zustand.md`：状态清单、切片计划、风险与回滚。
- `surfaces/gui/package.json` + lock：新增 `zustand` 依赖。

## 验证

- `npx tsc --noEmit` 通过。
- `npm test`（全量 vitest）通过。
- `npm run e2e`（238 用例）通过；重点覆盖侧边栏会话分组与轮询。
- `python surfaces/gui/scripts/check_i18n.py` 通过（无新增用户可见文案）。

## 非目标

- 不迁移 `activeSessionStore` / `runStore` / UI 偏好切片（仅列入计划）。
- 不删除 `App.tsx` 的任何 props 透传。
