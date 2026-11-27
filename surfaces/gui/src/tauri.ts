// Thin bridge to the Tauri desktop shell. In the browser these are inert (isTauri() === false),
// so the SPA stays a single codebase. We use the injected `window.__TAURI__` global (the shell
// sets `withGlobalTauri`) instead of the @tauri-apps npm packages, so the browser build needs
// no Tauri dependencies.

export const isTauri = (): boolean =>
  typeof (globalThis as any).__TAURI__ !== "undefined";

// "macos" | "windows" | "linux" — injected by the shell (std::env::consts::OS) before the
// SPA loads; userAgent fallback covers browser dev. The macOS overlay-titlebar layout (and
// its traffic-light compensations) must NEVER apply on Windows, which keeps its native
// title bar (alignment bug, caught on Windows 2026-07-21).
export const platformOS = (): string => {
  const injected = (globalThis as any).__OCW_PLATFORM__;
  if (typeof injected === "string" && injected) return injected;
  return /mac/i.test(navigator.userAgent) ? "macos" : /win/i.test(navigator.userAgent) ? "windows" : "linux";
};

export type DictationStatus = {
  recording: boolean;
  model_installed: boolean;
  model_verified: boolean;
  test_passed: boolean;
  download_in_progress: boolean;
  model_name: string;
  model_bytes: number;
  supported: boolean;
  device_summary: string;
  compatibility_reason: string | null;
};

export type DictationDownloadProgress = {
  downloaded_bytes: number;
  total_bytes: number;
};

// A rejected `invoke` is the ONLY signal that a capability is missing, a command is
// unregistered, or the IPC bridge is broken -- without it the UI just does nothing
// (owner report 2026-11-21: "clicked Choose folder, no reaction, no error"). Log the
// first failure per command, then stay quiet: `dictation_level` polls at ~10Hz and
// would otherwise flood the console with the same rejection.
//
// The console alone is not enough: release builds compile no devtools
// (src-tauri/Cargo.toml enables only `tray-icon`), so nobody can open devtools in
// the shipped app. The same first failure is therefore also POSTed to the sidecar,
// which appends it to `ui_events.jsonl` in the state dir -- a file a user CAN find
// and attach to a bug report.
const reportedFailures = new Set<string>();

const UI_LOG_ENDPOINT = "/v1/ui/log";

/** Send one diagnostic to the sidecar. Deliberately fire-and-forget.
 *
 *  Must never throw and never recurse: this runs from `invoke`'s failure path, so a
 *  throw here would surface as an unhandled rejection, and routing it back through
 *  `invoke`-style reporting would loop. `globalThis.fetch` is used directly rather
 *  than the ./api wrapper because importing ./api from here would be a cycle
 *  (api.ts is statically imported by App.tsx, which imports tauri.ts). */
function reportToSidecar(payload: Record<string, unknown>): void {
  try {
    const base = (globalThis as any).__COWORKER_HTTP__;
    const token = (globalThis as any).__COWORKER_API_TOKEN__;
    // Without the injected endpoint we are not inside the desktop shell; nothing to
    // deliver to, and guessing a port would be worse than dropping the entry.
    if (!base || typeof globalThis.fetch !== "function") return;
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers["X-OpenWorker-Token"] = String(token);
    void globalThis
      .fetch(`${String(base).replace(/\/$/, "")}${UI_LOG_ENDPOINT}`, {
        method: "POST",
        headers,
        body: JSON.stringify({ events: [payload] }),
      })
      .catch(() => {
        /* the log is an observer; its failure must stay invisible */
      });
  } catch {
    /* never let diagnostics break the caller */
  }
}

const invoke = async <T>(cmd: string, args?: Record<string, unknown>): Promise<T | null> => {
  const tauri = (globalThis as any).__TAURI__;
  if (!tauri?.core?.invoke) return null;
  try {
    return (await tauri.core.invoke(cmd, args)) as T;
  } catch (err) {
    if (!reportedFailures.has(cmd)) {
      reportedFailures.add(cmd);
      console.error(`[tauri] invoke("${cmd}") failed; treating it as unavailable`, err);
      reportToSidecar({
        command: cmd,
        level: "error",
        message:
          err instanceof Error
            ? err.message
            : typeof err === "string"
              ? err
              : JSON.stringify(err),
        stack: err instanceof Error ? err.stack ?? undefined : undefined,
        origin: typeof location !== "undefined" ? location.origin : undefined,
        platform: platformOS(),
      });
    }
    return null;
  }
};

const invokeStrict = async <T>(cmd: string, args?: Record<string, unknown>): Promise<T> => {
  const tauri = (globalThis as any).__TAURI__;
  if (!tauri?.core?.invoke) throw new Error("This feature is available in the desktop app.");
  return (await tauri.core.invoke(cmd, args)) as T;
};

/** Open the native macOS folder picker (Tauri only). Returns the chosen path, or null. */
export async function pickFolder(): Promise<string | null> {
  const path = await invoke<string>("pick_folder");
  return typeof path === "string" && path ? path : null;
}

/** The folder picker that works EVERYWHERE: Tauri's native dialog in the desktop shell, else the
 * sidecar-opened OS dialog (the sidecar is local, so the browser GUI still gets a real picker —
 * owner report 2026-07-04: "Browse" was desktop-only and the browser had paste-a-path only). */
export async function chooseFolder(): Promise<string | null> {
  if (isTauri()) return pickFolder();
  const { pickFolderViaServer } = await import("./api");
  return pickFolderViaServer();
}

/** Save the diagnostics text through the OS save dialog (desktop only).
 *
 *  Returns the chosen path, or null when the user cancelled -- cancelling is a
 *  normal outcome, not an error, so it must not look like a failure in the UI.
 *  The bytes are produced by the sidecar (`/v1/ui/log/export`); this command only
 *  picks a destination, which is why it can never write somewhere the user did
 *  not approve. */
export async function saveDiagnostics(contents: string, filename: string): Promise<string | null> {
  const path = await invoke<string>("save_diagnostics", { contents, filename });
  return typeof path === "string" && path ? path : null;
}

/** Open-at-login (macOS LaunchAgent). */
export const getAutostart = () => invoke<boolean>("get_autostart");
export const setAutostart = (enabled: boolean) => invoke<boolean>("set_autostart", { enabled });

/** Keep this system awake so scheduled tasks fire while idle (caffeinate on macOS,
 * SetThreadExecutionState on Windows). Persists across restarts. */
export const getKeepAwake = () => invoke<boolean>("get_keep_awake");
export const setKeepAwake = (enabled: boolean) => invoke<boolean>("set_keep_awake", { enabled });

/** Begin native window dragging from a custom title/header region. */
export const startWindowDrag = () => invoke<boolean>("start_window_drag");

// Sandbox capability checks (Settings ▸ Security). Desktop-only read-only probes:
// OpenShell's CLI version and Docker Desktop's host-networking preference.
export type DockerHostNetworkingStatus = {
  docker_cli: boolean;
  host_networking: boolean | null;
  settings_path: string | null;
};

/** `openshell --version` first line, or null when the CLI is absent. */
export const getOpenShellVersion = () => invoke<string | null>("openshell_version");

/** Whether Docker Desktop's "Enable host networking" toggle is on (null = unknown). */
export const getDockerHostNetworking = () =>
  invoke<DockerHostNetworkingStatus>("docker_host_networking");

// Local dictation is native-only. The browser build deliberately keeps this unavailable rather
// than silently sending microphone audio to a server.
export const getDictationStatus = () => invoke<DictationStatus>("get_dictation_status");
/** Instantaneous mic loudness 0..1 while recording (0 otherwise) — drives the composer's
 * live waveform. Cheap; poll at ~10Hz. */
export const getDictationLevel = () => invoke<number>("dictation_level");
export const startDictation = () => invokeStrict<DictationStatus>("start_dictation");
export const stopDictation = () => invokeStrict<string>("stop_dictation");
export const cancelDictation = () => invokeStrict<void>("cancel_dictation");
export const downloadDictationModel = () => invokeStrict<DictationStatus>("download_dictation_model");
export const cancelDictationModelDownload = () => invokeStrict<void>("cancel_dictation_model_download");
export const verifyDictationModel = () => invokeStrict<DictationStatus>("verify_dictation_model");
export const markDictationTestPassed = () => invokeStrict<DictationStatus>("mark_dictation_test_passed");
export const deleteDictationModel = () => invokeStrict<DictationStatus>("delete_dictation_model");

export async function listenDictationDownloadProgress(
  handler: (progress: DictationDownloadProgress) => void,
): Promise<() => void> {
  const listen = (globalThis as any).__TAURI__?.event?.listen;
  if (!listen) return () => {};
  return (await listen("dictation-download-progress", (event: { payload: DictationDownloadProgress }) => {
    handler(event.payload);
  })) as () => void;
}

// --- Auto-update (desktop only; browser builds see null / throw) -----------------

export type UpdateInfo = { version: string; notes: string };

/** Ask the shell whether a newer release exists (verified manifest; see lib.rs).
 * null = up to date, unreachable endpoint, or not the desktop app. */
export const checkForUpdate = () => invoke<UpdateInfo | null>("check_for_update");

/** Pre-fetch + verify the update bytes in the background so the install is instant.
 * The shell caches them keyed by version; calling again for the same version is a no-op.
 * Rejects when there is no update or the download fails — callers fall back to the
 * download-on-install path. */
export const downloadUpdate = () => invokeStrict<void>("download_update");

/** Drop the pre-fetched update bundle (freed on "Later" so a dismissed release
 * doesn't pin tens of MB for a weeks-long app run). */
export const clearPendingUpdate = () => invokeStrict<void>("clear_pending_update");

/** Install the update (pre-fetched bytes when available, else download + verify now),
 * then relaunch. Resolves only on failure paths (success restarts the process on macOS;
 * Windows hands off to the installer). */
export const installUpdate = () => invokeStrict<void>("install_update");

/** Best-effort open a URL in the user's browser. Uses the Tauri opener plugin if present, else
 * `window.open`. The caller should also render the raw URL so it stays copyable if both no-op
 * (the desktop webview has no opener plugin wired yet). */
export function openExternal(url: string): void {
  const opener = (globalThis as any).__TAURI__?.opener;
  if (opener?.openUrl) {
    opener.openUrl(url).catch(() => window.open(url, "_blank", "noopener,noreferrer"));
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}
