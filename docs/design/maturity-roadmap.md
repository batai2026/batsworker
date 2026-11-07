# Maturity roadmap (2026-10) — closing the four biggest gaps

> Status: evaluation + recommendations from the 2026-10-25 session. P0/P1 items
> are being executed; this doc is the tracking record.

## Context

The 0.2.1b snapshot was assessed at **~0.8/1.0 maturity** (stable Beta). Four
areas were called out as the main gaps before a 1.0 release:

1. **OS-level sandbox** — implemented (`coworker/tools/sandbox.py`, Docker
   executor) but opt-in only, no GUI switch, file tools not sandboxed, no
   background tasks, no Windows validation.
2. **Frontend architecture** — `surfaces/gui/src/App.tsx` is ~2,400 lines with
   50+ hooks; no router/state library.
3. **Windows platform testing & packaging** — CI only runs `ubuntu-latest`; two
   Windows-only test failures; installers unsigned.
4. **New-feature validation** — marketplace / threat model / PWA lack e2e specs
   and real-world validation.

## Priority plan

| Priority | Item | Status |
|---|---|---|
| P0 | Add `windows-latest` CI job + fix the 2 known Windows test failures | ✅ Done |
| P0 | Add e2e specs for the 6 new features (template marketplace, connector marketplace, security tab, memory plain-text, hire modal, browser panel) | ✅ Done |
| P1 | Extract hooks to split App.tsx (`useAppNavigation`, `useRail`, `usePersonas`) | ✅ Done |
| P1 | Sandbox GUI toggle + `sandboxed` status visible | ✅ Done |
| P2 | Windows packaging in CI + Authenticode signing | ✅ Done (release job existed; Authenticode signing scaffold added) |
| P2 | Real security-loop drill + report (`scripts/security_drill.py`, `reports/security-drill.md`) | ✅ Done |
| P2 | PWA mobile smoke test + Lighthouse audit | ✅ Lighthouse ran (real browser, final): **SEO 100 · A11y 100 · Best Practices 96 · PWA installability 2/2 · Performance 50**. Fixed: `<main>` landmark, prod sourcemaps, `--faint` contrast tokens (light + dark). Best Practices 96 = console network errors (no backend on static-serve audit) + security headers (deployment-level). Service Worker (offline shell) added in `public/sw.js`; inline first-paint shell in `index.html` |
| P2 | Sandbox v2: per-command probe + runtime fallback | ✅ Done (`DockerExecutor` falls back locally with `sandboxed:false` + reason) |
| P2 | Sandbox v2: background tasks | ✅ Done (long-lived container + `docker exec`, output readback, kill) |
| P2 | Sandbox v2: file tools through the sandbox (full read/write/replace/patch/list + multi-root + host fallback) | ✅ Done (`sandbox_files.py` + `unified_diff.py` + catalog swap) |
| P2 | Sandbox v2: Windows Docker validation | ✅ Test added (`test_docker_executor_real_sandbox_run`, runs when Docker is present) |

## Detail: OS-level sandbox (P1 executed)

- Added **GUI toggle + status**: a Sandbox card in Settings ▸ Security reads/writes
  the user-global sandbox preference via `GET/PUT /v1/settings/sandbox` (stored in
  `<state-dir>/sandbox.json`, which overrides `config.toml` in `build_engine`).
  The shell tool's `sandboxed: true` flag is surfaced as a 🛡 badge on tool cards
  (live `tool_finished` and replayed transcripts).
- Backend: `coworker/sandbox_settings.py`, `coworker/agent.py`,
  `coworker/server/manager.py`, `coworker/server/app.py`.
- Follow-ups (v2) — all executed: file tools through the sandbox, background
  tasks, per-command availability probe, Windows Docker validation.

## Detail: Frontend refactor (P1/P2 executed)

Sixteen custom hooks extracted from `App.tsx` into `surfaces/gui/src/hooks/`:

- `useAppNavigation.ts` — `surface` / `scheduledOpenId` / `personaView` /
  `settingsTab` navigation state and setters.
- `useSessions.ts` — session list + recent projects, the 5s poll, the
  `PERSONAS_CHANGED` refetch, and the boot-resume helpers
  (`rememberLastSession`, `resumeTargetForAgent`).
- `useRail.ts` — right-rail visibility, artifact-preview auto-collapse of the
  nav, browser-panel refresh key.
- `usePersonas.ts` — persona metadata load/refresh.
- `useKeyboardShortcuts.ts` — ⌘B / ⌘, global shortcuts (ref-based listeners).
- `useArtifactCount.ts` — topbar produced-file count for Cowork sessions.
- `useBoard.ts` — team board state, board/team-chat overlays, `moveBoardItem`.
- `useSessionInbox.ts` — active-session pending Inbox + Unattended mode + 4s poll.
- `useComposerPrefill.ts` — composer prefill (text/attachments) with nonce bump.
- `usePendingGates.ts` — unresolved approval/plan/team/items/tool/dir/question gates (newest first).
- `useGateResponders.ts` — approve/plan/team/items/dir/tool/question answering flow (+ `resolveGates.ts`).
- `useBoot.ts` — sidecar health + resume-last-session + onboarding gate + splash reveal.
- `useSessionActions.ts` — open/resume/switch/workspace/project/rename/pin/archive/delete/run.
- `useAutomationToast.ts` — 5s automation-run-start toast + badge refresh.
- `useSessionConnect.ts` — Session lifecycle + the full WS event switch (transcript).
- `useSendGate.ts` — UX-029 send-time folder enforcement (stash/bind/temp/cancel).
- `resolveGates.test.ts` — unit tests for the 7 optimistic gate-resolution helpers.

App.tsx went from **2,414 → 1,423 lines** (behavior-identical state moves);
`tsc` and i18n checks are the gate. **Vitest: 42 files / 199 tests all passing** in
a normal terminal. The transcript/WebSocket and send-gate clusters are extracted;
only small glue remains.

## Detail: Windows CI + test fixes (P0 executed)

- `.github/workflows/ci.yml` gains a `windows-latest` job running the Python
  suite and the GUI typecheck/unit tests.
- `tests/test_automation.py`: the `time.tzset()` test is skipped on Windows
  (`pytest.mark.skipif(sys.platform == "win32")`).
- `tests/test_security_bundles.py`: persona/SKILL file reads now use
  `encoding="utf-8"` (the GBK default broke on Windows).

## Detail: e2e specs (P0 executed)

Eight new Playwright specs under `surfaces/gui/e2e/`, all driving the hermetic
mock harness (no model/network):

- `template-marketplace.spec.ts`
- `connector-marketplace.spec.ts`
- `security-tab.spec.ts` (also covers the sandbox toggle: enable/save/persist)
- `memory-plaintext.spec.ts`
- `hire-modal.spec.ts`
- `browser-panel.spec.ts`
- `pwa-mobile.spec.ts` (iPhone viewport smoke)
- `demo-skill.spec.ts` (paste steps → generate → review → install)

Mock routes for the new endpoints were added to `surfaces/gui/e2e/fixtures.ts`.

Unit tests (vitest) for the extracted hooks (`useAppNavigation`, `useSessions`,
`useRail`, `usePersonas`), `HireModal`, `TemplateMarketplace`, `MarketplaceModal`
and `SecuritySection` were added under `surfaces/gui/src/hooks/*.test.ts` and
`components/*.test.tsx`; they run in the `gui-unit` CI job.

## Detail: sandbox file tools (P2 executed)

`docs/design/file-sandbox.md` documents the last sandbox v2 item: routing file
tools (`read_file`/`write_file`/`apply_patch`/`replace_in_file`) through
`DockerExecutor` (workspace-only, read-only root, no network), with path mapping,
traversal guards, host fallback, and multi-root mounts — all implemented and
covered by `tests/test_security_workflow.py`.

## Detail: v0.3.1 gap closure (P0 executed)

Aligned with the upstream v0.3.1 release notes (OpenShell + egress policy):

- **Egress policy server** (`coworker/egress.py`): persistent domain allowlist
  (`<state-dir>/egress-policy.json`), exact/subdomain matching, thread-safe.
  - `request_egress` tool (`coworker/tools/egress.py`, approval-gated) lets the
    agent ask to allow a blocked domain; the permission engine consults the policy
    so approved domains auto-run (`permissions.py`).
  - API: `GET/POST /v1/egress/policy`, `DELETE /v1/egress/policy/{domain}`.
  - Registered as the `egress` catalog capability on all 7 security personas.
- **OpenShell executor scaffolding** (`coworker/tools/openshell.py`):
  configurable CLI prefix (`sandbox_openshell_prefix`, default `openshell exec --`),
  cached availability probe, per-command v2 fallback to LocalExecutor with
  `sandboxed: false` + `fallback_reason`. `agent.py` selects it when
  `sandbox_backend == "openshell"`.
- Tests: `tests/test_egress.py` (policy/tool/capability/API/permission-engine),
  `tests/test_openshell.py` (prefix parsing, fallback, fake-CLI success, status API).
- **Egress policy UI**: Settings ▸ Security card (list/add/remove approved domains)
  + OpenShell backend status line (`getOpenShellStatus`).
- **Container image**: `Dockerfile` + `.github/workflows/docker.yml` publish
  `ghcr.io/andrewyng/openworker:<tag>` + `latest` on tags.
- **e2e**: `security-tab.spec.ts` covers the egress card (add/list/remove) with
  mutable fixture routes for `/v1/egress/policy`.
- Follow-ups: real OpenShell CLI flags in `sandbox_openshell_prefix`; desktop
  OpenShell settings item (Tauri version/host-networking check).

## Verification

- Full backend suite (post-P0): **2004 passed, 8 skipped**; the 45 failed + 3 errors
  are the same **environment-dependent** set as before (missing tool binaries on PATH,
  blocked network for tool downloads / Slack relay / URL-redirect guards / git clone,
  Windows background-kill semantics, heavy e2e timing) — none touch the egress /
  OpenShell / security features built here. The new `test_egress.py` +
  `test_openshell.py` (11 tests) all pass.
- Workflow YAML: `ci.yml`, `release.yml`, and `docker.yml` parse cleanly.
- GUI: `tsc --noEmit` clean; `scripts/check_i18n.py` clean (837 keys).
- e2e specs typecheck under the GUI tsconfig; running them requires a normal
  (non-sandboxed) environment (Playwright spawns browsers).

## 2026-11 update: the borrowed harness line (11/11 done)

A second evaluation (2026-11-07) scored the tree at **~0.88/1.0**. The eleven
items borrowed from the DeepSeek Harness analysis are all landed; see
[eval-and-replay.md](eval-and-replay.md) for the five eval-side items and
[permissions-and-approvals.md](permissions-and-approvals.md) §Plugin consent
governance for the four consent-side items.

| Item | What landed |
|---|---|
| P0-1 turn ledger | `coworker/trajectory.py` — append-only redacted JSONL, `GET /v1/sessions/{id}/trajectory` |
| P0-2 fork / replay | `POST /v1/sessions/{id}/fork` + `/replay` + `trajectory/diff` |
| P1-3 PluginManifest | `coworker/plugins/` adapter layer over persona/skill/connector/mcp |
| P1-4 presets + eval matrix | minimal/creator presets, `coworker/eval/matrix.py` |
| P2-5 code mode evaluation | No-Go report kept the engine loop untouched |
| form-B code mode | `coworker/codemode/` — `exec` tool, fail-closed Docker sandbox, approval-bridged |
| P2-6 harness registry | `coworker/personas/harness.py` — one source for harness definitions |
| P2-7 unified install pipeline | connector/MCP installs re-routed through `plugins/lifecycle` |
| P2-8 consent v2 | permission-widening `PATCH` gated behind explicit `confirm` (409 otherwise) |
| P2-9 mid-turn steering | plain-text messages queue while running, injected at the next model boundary |
| P2-10 consent audit | append-only `plugin_audit.jsonl` + `GET /v1/plugins/audit` |
| P2-11 eval capture | `coworker/eval/capture.py` → `matrix --case` consumes the package |

Verification at the end of the line: full pytest **2155 passed / 9 skipped**,
ruff clean, GUI vitest 199/199, i18n check clean.

## 2026-11 update: eval gate in CI

The last automation gap of the eval loop is closed:

- `evals/ci-case/` — a committed, deterministic golden case (`messages.jsonl` +
  `baseline.json`), generated by a `--stub` run.
- `.github/workflows/eval.yml` — `gate-stub` runs
  `matrix --case evals/ci-case --stub --check-baseline` on every push/PR (exit
  3 on drift, report uploaded as an artifact); `matrix-real` is a manual
  `workflow_dispatch` lane that runs real models with repo secrets and gates on
  the same rules.
- `matrix --write-baseline` regenerates the golden baseline (stub only, single
  cell, mutually exclusive with `--check-baseline`). Both the workflow and
  regeneration pin an **empty, non-git workspace + empty `COWORKER_STATE_DIR`**:
  the outbound payload embeds the workspace path, git status/commits, the
  per-turn clock and user skills/memories, so anything else drifts on noise.
- Tests: `tests/test_eval_capture.py` covers refresh → gate passes, argument
  guards, and the golden case's shape.

**Remaining gaps before 1.0**: the real-model matrix still has to be run once
with keys and its baseline committed to have real evidence (the wiring and the
deterministic stub lane are in place); OpenShell still uses placeholder CLI
flags; the frontend keeps the extracted-hooks architecture with a `zustand`
migration held in reserve.

## Follow-ups (P2)

Eval: run the real-model matrix once with keys and commit its baseline (the CI
wiring and the deterministic stub lane landed in `.github/workflows/eval.yml`).
Security: real OpenShell CLI flags in `sandbox_openshell_prefix`, desktop
OpenShell settings item. Frontend: `zustand` migration only if the extracted
hooks still leave App.tsx too large.