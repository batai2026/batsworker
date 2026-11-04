# Desktop app, STT, packaging & CI

> Source: `surfaces/gui/` (React SPA + Tauri shell), `stt/` (Rust),
> `packaging/`, `.github/workflows/`.

## React SPA (`surfaces/gui/src`)

React 18 + TypeScript 5.5 + Vite 5 + Tailwind CSS 3. Deliberately plain:
**no router, no Redux/Zustand** — hooks in a single large root component
(`App.tsx`, ~2,400 lines), with `api.ts` (~2,500 lines) owning all backend
communication. Notable deps: `react-markdown`/`remark-gfm`,
`pdfjs-dist`, `xlsx` (artifact rendering).

Major components (~50 files in `src/components/`): `Sidebar`, `Composer`
(voice-input states), `Transcript`, `RightRail`, `PersonaView`/
`PersonaHero`, `SettingsView`, `InboxView`, `IntegrationsView`,
`ScheduledView`, `AuditView`, `TeamChatView` + `BoardPanel`,
`ApprovalCard` / `ToolRequestCard`, `UpdateBanner`, `Onboarding`,
`ProviderSetup`.

### Talking to the sidecar

- REST for state; one `WebSocket /ws/session/{id}` for the live turn and one
  app-wide `/ws/events` for pushes (protocol:
  [server-and-auth.md](server-and-auth.md)).
- **Endpoint resolution order**: runtime globals injected by Tauri
  (`window.__COWORKER_HTTP__/__COWORKER_WS__`) → Vite env → dev default
  `127.0.0.1:8765`. Browser dev mode and the desktop shell share one codebase.
- **Auth**: per-launch token as `X-OpenWorker-Token` header and as the
  `openworker` WebSocket subprotocol.

## Tauri shell (`surfaces/gui/src-tauri`)

`src-tauri/src/lib.rs` (~900 lines) is deliberately "a thin native window
over the existing React SPA":

1. Picks a free localhost port, spawns the Python `openworker-server` as a
   managed sidecar (dev venv path, or the bundled PyInstaller onedir under
   `binaries/sidecar/` in production), kills it on exit.
2. Injects sidecar URLs + the in-memory launch token into the webview before
   the SPA loads; merges the user's login-shell env for the sidecar (OPE-83 —
   so `toolchain.py` sees the user's PATH).
3. **Tray residency**: closing the window hides it; the scheduler and
   self-wake keep running; tray → Quit stops the sidecar.
4. Native commands: folder picker, open-at-login autostart, keep-awake
   (caffeinate), dictation (STT bridge), updater.

## STT sidecar (`stt/`)

Rust crate `ocw-stt` (`src/lib.rs`, ~670 lines) — local, **offline**
speech-to-text for voice dictation:

- `cpal` for mic capture, `whisper-rs` (whisper.cpp bindings, CPU path —
  Metal deferred), `ureq` for the one-time model download.
- Model: Whisper `ggml-base.en` (~142 MB) from HuggingFace, pinned by exact
  byte size **and** SHA-256, with `.verified`/`.ready` marker files;
  16 kHz resample before transcription.
- A reusable single-microphone session manager (background capture thread
  into a shared buffer; `stop_and_transcribe()` returns the text), exposed to
  the GUI as Tauri commands (`start_dictation`, `stop_dictation`,
  `get_dictation_status`).
- Deliberately dependency-isolated from the shell: no Tauri/UI/clipboard
  deps in the crate.

## Packaging (`packaging/`)

- **macOS** (`build_dmg.sh`): PyInstaller-bundle the server (spec:
  `openworker-server.spec`) → stage as a Tauri resource + sign Mach-Os →
  `tauri build` → wrap in a DMG via `hdiutil` → optional notarization +
  staple when `APPLE_SIGNING_IDENTITY` is set.
- **Windows** (`build_windows.ps1`): same pipeline → NSIS `.exe` + MSI;
  currently unsigned (SmartScreen warning acknowledged in the README).
- **Auto-update**: `tauri-plugin-updater` with **minisign** signature
  verification against the pubkey in `tauri.conf.json`. Endpoint chain:
  `download.openworker.com/latest.json` → GitHub Releases fallback.
  `make_update_manifest.py` composes `latest.json` from staged artifacts by
  stable names; missing platforms are skipped so other-OS installs see "no
  update" rather than a broken one. The GUI pre-downloads update bytes into
  memory (`download_update`) so "Restart to update" is instant.

## CI (`.github/workflows/`)

- **`ci.yml`** (push/PR): three parallel jobs — `pytest` (Python 3.12,
  `pip install -e ".[messaging,dev,bedrock]"`), `gui-unit` (tsc + vitest),
  `gui-e2e` (Playwright chromium, hermetic).
- **`release.yml`** (tag `v*` / manual): 3-way matrix — macOS arm64, macOS
  x64 (native build; PyInstaller can't cross-compile), Windows — running the
  same local build scripts, then uploads installers twice (versioned +
  stable name for `releases/latest/download/...` deep links) and attaches the
  composed `latest.json` to a draft GitHub Release. Publishing the release is
  what pushes the update.

### Testing surface

- Backend: 127 `test_*.py` files under `tests/`, including the in-process
  **FakeSlack** harness (`coworker/testing/fake_slack/`) that drives the real
  `SlackAdapter`/slack-bolt stack against a fake server — full Slack tests
  with zero network. A conftest `autouse` fixture isolates every test's state
  dir (added after a real telemetry-leak incident, documented in
  `tests/conftest.py`).
- GUI: 13 Vitest units colocated in `src/`; **72 hermetic Playwright specs**
  in `e2e/` (`fixtures.ts` mocks every `/v1` request and scripts a fake agent
  over the WS) plus a small `e2e-live/` suite against a real server.
