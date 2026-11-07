# GUI state: zustand migration plan (three slices landed)

Status: `sessionStore`, `activeSessionStore` and `runStore` landed; only the UI-prefs
slice remains a plan, not scheduled. Trigger for further work: `App.tsx` is still
~1420 lines with 40+ `useState` calls even after 15 `use*` hooks were extracted — the
roadmap's condition "only if the extracted hooks still leave App.tsx too large" is met.

## Why a store at all

Every shared value in the GUI currently travels through `App.tsx` props or hook
argument objects (`SessionActionsDeps`, `PendingGatesDeps`, …). Two costs follow:

1. Non-component code (event callbacks, websocket handlers, navigation helpers)
   cannot read the current session list / active session without capturing props.
2. Adding a consumer to a shared value means threading it through `App.tsx`,
   which is why the file keeps growing despite the hook extraction.

zustand removes both: `useSessionStore.getState()` works outside React, and
components select only the fields they render.

## State inventory (App.tsx)

| Group | State | Lines | Share |
|---|---|---|---|
| Workspace | `workspace`, `branch`, `tempWorkspace`, `draftFolderPicked`, `commandTrust` | 98–117 | Sidebar + header + composer |
| Persona/model | `agent`, `model`, `models`, `modelLabels`, `modelContextWindows`, `modelReady` | 118–173 | Header + settings + session switch |
| View prefs | `surfaces`, `mode`, `contextBar`, `following`, `searchOpen`, `showGate` | 127–219 | Rail + sidebar + main |
| Session runtime | `sessionId`, `items`, `streaming`, `reasoningStream`, `todo`, `usage`, `running`, `compacting`, `connected` | 130–169 | Composer + thread + rail |
| Gates | `showGate`, `gateCreate`, `pendingGates` (hook) | 115/169 | Composer + thread |
| Refs | `streamingRef`, `reasoningRef`, `sessionRef`, `scrollRef`, `pendingPromptRef`, `activeRunRef`, `atBottomRef`, `autoScrollingRef`, `lastScrollTopRef` | 150–389 | hot-path only; keep refs |

Refs stay out of the store: they are read on every stream token and must not
trigger re-renders.

## Slice plan

| Slice | Contents | Status |
|---|---|---|
| `sessionStore` | `sessions`, `projects`, `setSessions`, `setProjects`, `refreshSessions` | **landed** |
| `activeSessionStore` | `sessionId`, `workspace`, `branch`, `agent`, `model` (+ switch action) | **landed** — hook deps and `App.tsx` props unchanged |
| `runStore` | `running`, `compacting`, `streaming`, `reasoningStream`, `items`, `todo`, `usage` | **landed** — streaming ref mirrors live in `useSessionStream`; `App.tsx` subscribes once |
| UI prefs | `surfaces`, `mode`, `contextBar`, `following`, `searchOpen` | lowest value; only if a non-React consumer appears |
| Local-only | `tempWorkspace`, `draftFolderPicked`, `commandTrust`, `showGate`, `gateCreate`, `modelReady`, `browserRefreshKey`, `connectNonce` | keep `useState` |

## Pilot: what changed

- `src/stores/sessionStore.ts`: sessions/projects + setters (typed as React's
  `Dispatch<SetStateAction<T>>` so existing props need no change) + `refreshSessions`.
- `src/hooks/useSessions.ts`: keeps the same return shape; mount/poll/`PERSONAS_CHANGED`
  wiring moved onto the store action. No component or `App.tsx` edit.
- Tests: `src/stores/sessionStore.test.ts` (fill + error-clears); `useSessions.test.ts`
  resets the singleton store in `beforeEach` (module-level state otherwise leaks
  between cases).

## activeSessionStore slice: what changed

- `src/stores/activeSessionStore.ts`: `sessionId` / `workspace` / `branch` / `agent` /
  `model` with `Dispatch`-shaped setters (updater functions included — `useSessionConnect`
  relies on `setWorkspace((cur) => cur || d.workspace)`). `newSessionId()` moved here from
  `App.tsx` so the initial session id and every "new session" share one generator.
- `src/App.tsx`: the five `useState` declarations became one store subscription; every
  hook dependency object (`useBoot`, `useSessionConnect`, `useSendGate`,
  `useSessionActions`) and every prop stays exactly as it was. Whole-store subscription
  matches the old `useState` re-render behaviour.
- Non-React code can now read the active session without capturing props:
  `useActiveSessionStore.getState().sessionId` / `.workspace` / `.agent`.
- Tests: `src/stores/activeSessionStore.test.ts` (defaults, plain/updater setters,
  field isolation) with the same `beforeEach` reset.

The second pass — dropping the now-redundant `sessionId` / `workspace` / `agent` /
`model` entries from `SessionActionsDeps` and reading the store inside
`useSessionActions` — is intentionally not done here, so the slice stays revertible
without touching that hook's tests.

## runStore slice: what changed

- `src/stores/runStore.ts`: the seven run-time fields with `Dispatch`-shaped setters
  (`items` / `todo` updaters included — every WS handler appends through them).
- `src/hooks/useSessionStream.ts`: the slice plan's gate — the `streaming` /
  `reasoningStream` ref mirrors and the setters that keep them in sync moved out of
  `App.tsx` into a hook; the rendered values still come from the store.
- `src/App.tsx`: seven `useState` calls became one store subscription plus one hook
  call; every hook dependency object (`useBoot`, `useSessionConnect`,
  `useSessionActions`, `useSendGate`, `useGateResponders`) is untouched.
- Tests: `src/stores/runStore.test.ts` (defaults / updaters / field isolation) and
  `src/hooks/useSessionStream.test.ts` (mirror and store stay in sync on append/clear),
  both resetting the singleton store in `beforeEach`.

## Risks and mitigations

- **Singleton across tests**: reset the store in `beforeEach`
  (`useSessionStore.setState({ sessions: [], projects: [] })`,
  `useActiveSessionStore.setState({ sessionId, workspace, branch, agent, model })`).
  Any new store test file must do the same.
- **Server push + polling race**: `refreshSessions` is fire-and-forget; the poll (5s)
  and `PERSONAS_CHANGED` can overlap. Last response wins — acceptable for a list view.
  `items` is safe in `runStore`: it comes from the single per-session WebSocket, not
  from polling, and the socket is rebuilt on every session switch.
- **History hydrate vs a live turn**: `selectSession` awaits `getSessionMessages` while
  the new socket can already deliver a turn (a manual run's auto-sent prompt is echoed
  immediately). Store updates commit synchronously, so live items can land before the
  fetch resolves — and hydrating the (usually empty) history over them wiped the turn
  (caught by `standing-approvals.spec.ts`, ~half the runs). `useSessionActions` now
  applies the loaded history only when nothing has touched `items` since the request
  started (identity check) and `useSessionActions.test.ts` pins that case.
- **Prop-type drift**: migrate slices by keeping the hook's return shape stable first,
  then delete props in a second pass; `tsc --noEmit` is the gate.
- **Rollback**: each landed slice is one store file plus a `useState`/hook-body swap;
  reverting the commit restores `useState` without touching consumers.

## Verification

- `npx tsc --noEmit` clean.
- `npx vitest run` (all files) — the three stores plus `useSessionStream` add 13 tests,
  `useSessions` keeps 3.
- `npm run e2e` — the sidebar list/poll behaviour, session switch/new-session flows,
  streaming/stop and the usage/todo panels are covered by existing specs; no new
  fixtures required.
