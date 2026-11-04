# App.tsx refactor — hook extraction (in progress)

> Status: **mostly extracted**. App.tsx went from **2,414 → 1,423 lines** by moving
> self-contained state + effects into `surfaces/gui/src/hooks/`. Behavior is
> identical (state moves, not logic changes); `tsc --noEmit` + `check_i18n.py` are
> the gate, vitest tests for each hook run in the `gui-unit` CI job, and a normal
> terminal runs the full suite green (**42 files / 198 tests**).

## Why

`App.tsx` was a 2,400+ line component mixing navigation, sessions, messaging,
gate answering, board, and boot logic. Every change risked touching unrelated
state. Extracting hooks makes each cluster unit-testable and reviewable in
isolation, and shrinks the component to its real job: composing state + rendering.

## Extracted hooks (16)

| Hook | Owns | Removed from App |
|---|---|---|
| `useAppNavigation` | `surface` / `scheduledOpenId` / `personaView` / `settingsTab` | navigation state + setters |
| `useSessions` | session list + recent projects, 5s poll, `PERSONAS_CHANGED` refetch, boot-resume helpers | session state + polling effects |
| `useRail` | rail visibility, nav collapse/peek, artifact-preview auto-collapse, deep-link events | rail/nav state + effects |
| `usePersonas` | persona metadata load/refresh + `personaOf` | persona state + effects |
| `useKeyboardShortcuts` | ⌘B / ⌘, global shortcuts (ref-based) | keydown effect |
| `useArtifactCount` | topbar produced-file count for Cowork sessions | artifact fetch effect |
| `useBoard` | team board state, board/team-chat overlays, `moveBoardItem` | board state + fetch/refresh |
| `useSessionInbox` | pending Inbox + Unattended mode + 4s poll + `dropSessionInbox` | inbox state + poll effect + handlers |
| `useComposerPrefill` | composer prefill (text/attachments) + nonce | prefill state + callback |
| `usePendingGates` | 7 unresolved gate derivations (newest first) | 7 inline `.find` lines |
| `useGateResponders` | approve/plan/team/items/dir/tool/question answering flow | 7 responders |
| `useBoot` | sidecar health poll, resume-last-session, onboarding gate, splash reveal | ~120 lines of boot logic |
| `useSessionActions` | open/resume/switch agent, choose workspace, new project, rename/pin/archive/delete, run automation | ~170 lines of session lifecycle |
| `useAutomationToast` | automation-run-start toast (5s) + badge refresh | ~25 lines of toast state + effects |
| `useSessionConnect` | Session lifecycle + the full WS event switch (transcript) | ~370 lines of connect effect + event handling |
| `useSendGate` | UX-029 send-time folder enforcement (stash/bind/temp/cancel) | ~45 lines of gate state + resolutions |

Supporting module: `resolveGates.ts` (the 7 `resolveLastX` optimistic-resolution
helpers moved out of App.tsx's tail).

## Patterns

- **State + its effects move together**: `useSessions` bundles the 5s poll and
  the `PERSONAS_CHANGED` listener with the session list state.
- **Refs for WS readers**: `unattendedRef` lives in `useSessionInbox`; the WS
  handler reads `ref.current` through the returned ref.
- **Ref-based listeners**: `useKeyboardShortcuts` reads handlers through a ref so
  the window listener attaches exactly once.
- **Narrowed types preserved**: `usePendingGates` returns per-kind gate types
  (explicit type-guard predicates), so JSX card props keep their exact types.
- **Pure helpers extracted to modules**: `resolveGates.ts` is unit-testable on
  its own and imported by the hook.

## Verification

- `tsc --noEmit` clean (all hook files + tests).
- `scripts/check_i18n.py` clean (827 keys).
- vitest tests for all 16 hooks (CI `gui-unit`): **42 files / 198 tests**.

## Remaining glue (small)

`useSessionConnect` extracted the transcript/WebSocket cluster. What remains in
App.tsx is mostly state ownership + JSX + a few small glue blocks
(`saveAsProject`, send-gate recovery, stream-following scroll effects). Further
extraction is optional and lower value.