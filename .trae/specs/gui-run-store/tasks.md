# Tasks

1. [x] 新增 `src/stores/runStore.ts`（7 个字段 + Dispatch setter）
2. [x] 新增 `src/hooks/useSessionStream.ts`（ref 镜像 + store 同步 setter）
3. [x] `App.tsx` 改为 store 订阅 + streaming hook，hook 传参不变
4. [x] `runStore.test.ts` / `useSessionStream.test.ts`（含 `beforeEach` 重置）
5. [x] `docs/design/gui-state-zustand.md`、`maturity-roadmap.md`、`openworker-design-zh.md` 同步
6. [x] `npx tsc --noEmit` + 全量 vitest + 全量 e2e + i18n
7. [x] 修复 `selectSession` 历史加载覆盖实时 `items` 的竞态（store 同步提交让手动运行的
   auto-send 回显先到，e2e 约半数复现），并在 `useSessionActions.test.ts` 补单测
8. [x] 中文提交（后端无改动，说明未重跑 pytest）
