# GUI state: zustand migration plan (pilot landed)

Status: pilot landed (`src/stores/sessionStore.ts`); the remaining slices are a plan,
not scheduled. Trigger for further work: `App.tsx` is still ~1420 lines with 40+
`useState` calls even after 15 `use*` hooks were extracted — the roadmap's condition
"only if the extracted hooks still leave App.tsx too large" is now met.

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
| `activeSessionStore` | `sessionId`, `workspace`, `branch`, `agent`, `model` (+ switch action) | candidate next — highest fan-out, needs `useSessionActions` deps rework |
| `runStore` | `running`, `compacting`, `streaming`, `reasoningStream`, `items`, `todo`, `usage` | candidate — hot path; only after streaming is moved into a hook |
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

## Risks and mitigations

- **Singleton across tests**: reset the store in `beforeEach`
  (`useSessionStore.setState({ sessions: [], projects: [] })`). Any new store test
  file must do the same.
- **Server push + polling race**: `refreshSessions` is fire-and-forget; the poll (5s)
  and `PERSONAS_CHANGED` can overlap. Last response wins — acceptable for a list view,
  but do not move `sessionId`/`items` into a store without a request generation guard.
- **Prop-type drift**: migrate slices by keeping the hook's return shape stable first,
  then delete props in a second pass; `tsc --noEmit` is the gate.
- **Rollback**: the pilot is one file plus a hook body; reverting the commit restores
  `useState` without touching consumers.

## Verification

- `npx tsc --noEmit` clean.
- `npx vitest run` (all files) — the pilot adds 2 tests, `useSessions` keeps 3.
- `npm run e2e` — the sidebar list/poll behaviour is covered by existing session specs;
  no new fixtures required.
