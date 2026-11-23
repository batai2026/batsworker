// Regression test for the silent-failure fix in tauri.ts.
//
// History: `invoke` used to `catch { return null }` with no trace, so a denied
// capability or an unregistered command looked exactly like "the feature isn't
// available" -- the owner clicked Choose folder and nothing happened, with no
// error anywhere (2026-11-21). These tests pin down the two behaviours that make
// the failure diagnosable: it logs, and it logs ONCE per command.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const g = globalThis as any;

async function loadModule() {
  // Fresh module per test: `invoke` keeps its own dedupe Set in module scope.
  return import("./tauri");
}

describe("tauri invoke failure reporting", () => {
  beforeEach(() => {
    vi.resetModules();
    g.__TAURI__ = { core: { invoke: vi.fn() } };
  });

  afterEach(() => {
    delete g.__TAURI__;
    delete g.__COWORKER_HTTP__;
    delete g.__COWORKER_API_TOKEN__;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("logs the real error when a command is rejected", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    g.__TAURI__.core.invoke.mockRejectedValue(
      new Error("pick_folder not allowed. Permissions associated with this command: dialog:allow-open"),
    );
    const { pickFolder } = await loadModule();

    await expect(pickFolder()).resolves.toBeNull();

    expect(spy).toHaveBeenCalledTimes(1);
    const [message, err] = spy.mock.calls[0];
    expect(String(message)).toContain("pick_folder");
    // The underlying rejection must survive to the log, not be swallowed.
    expect(String((err as Error).message)).toContain("not allowed");
  });

  it("keeps returning null so existing callers are unaffected", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    g.__TAURI__.core.invoke.mockRejectedValue(new Error("boom"));
    const { checkForUpdate } = await loadModule();

    await expect(checkForUpdate()).resolves.toBeNull();
  });

  it("does not stay silent on a second, DIFFERENT command", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    g.__TAURI__.core.invoke.mockRejectedValue(new Error("nope"));
    const { pickFolder, getAutostart } = await loadModule();

    await pickFolder();
    await getAutostart();

    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("logs only once per command under ~10Hz polling", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    g.__TAURI__.core.invoke.mockRejectedValue(new Error("nope"));
    const { getDictationLevel } = await loadModule();

    // dictation_level is polled at ~10Hz; 50 rejections must not be 50 log lines.
    for (let i = 0; i < 50; i += 1) await getDictationLevel();

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("stays quiet when the shell is absent (plain browser build)", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    delete g.__TAURI__;
    const { pickFolder } = await loadModule();

    await expect(pickFolder()).resolves.toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });

  it("does not log on success", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    g.__TAURI__.core.invoke.mockResolvedValue("/home/me/project");
    const { pickFolder } = await loadModule();

    await expect(pickFolder()).resolves.toBe("/home/me/project");
    expect(spy).not.toHaveBeenCalled();
  });
});

// A console.error is unreachable in the shipped app (release builds have no
// devtools), so the same first failure is also delivered to the sidecar, which
// appends it to a file. These tests pin the delivery's safety properties.
describe("sidecar delivery of webview failures", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.resetModules();
    vi.spyOn(console, "error").mockImplementation(() => {});
    g.__TAURI__ = { core: { invoke: vi.fn().mockRejectedValue(new Error("pick_folder not allowed")) } };
    g.__COWORKER_HTTP__ = "http://127.0.0.1:54321";
    g.__COWORKER_API_TOKEN__ = "tok-123";
    fetchMock.mockReset();
    fetchMock.mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    delete g.__TAURI__;
    delete g.__COWORKER_HTTP__;
    delete g.__COWORKER_API_TOKEN__;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("POSTs the failure to the sidecar log endpoint with auth", async () => {
    const { pickFolder } = await loadModule();
    await pickFolder();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://127.0.0.1:54321/v1/ui/log");
    expect(init.method).toBe("POST");
    expect(init.headers["X-OpenWorker-Token"]).toBe("tok-123");

    const body = JSON.parse(init.body);
    expect(body.events).toHaveLength(1);
    expect(body.events[0].command).toBe("pick_folder");
    expect(body.events[0].message).toContain("not allowed");
    expect(body.events[0].level).toBe("error");
  });

  it("never sends the token in the body", async () => {
    const { pickFolder } = await loadModule();
    await pickFolder();

    const body = fetchMock.mock.calls[0][1].body as string;
    expect(body).not.toContain("tok-123");
  });

  it("sends at most one entry per command, even under 10Hz polling", async () => {
    const { getDictationLevel } = await loadModule();
    for (let i = 0; i < 50; i += 1) await getDictationLevel();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("still resolves null when the POST fails", async () => {
    fetchMock.mockRejectedValue(new Error("sidecar down"));
    const { pickFolder } = await loadModule();

    await expect(pickFolder()).resolves.toBeNull();
  });

  it("does not recurse when the reporting call itself throws synchronously", async () => {
    // A `fetch` that throws synchronously is the shape that could loop: if the
    // reporter routed its own failure back through `invoke`, this would recurse.
    fetchMock.mockImplementation(() => {
      throw new Error("sync explosion");
    });
    const { pickFolder } = await loadModule();

    await expect(pickFolder()).resolves.toBeNull();
    // One attempt for pick_folder, none for the reporter's own failure.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(g.__TAURI__.core.invoke).toHaveBeenCalledTimes(1);
  });

  it("skips delivery when no sidecar endpoint was injected", async () => {
    delete g.__COWORKER_HTTP__;
    const { pickFolder } = await loadModule();

    await expect(pickFolder()).resolves.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
