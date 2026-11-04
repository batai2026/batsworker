import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { useSessionConnect, type SessionConnectDeps } from "./useSessionConnect";
import { Session } from "../api";

vi.mock("../api", () => ({
  Session: class {
    constructor(
      public sessionId: string,
      public workspace: string,
      public agent: string,
      public opts: any,
    ) {}
    close() {}
    userMessage = vi.fn();
  },
  announceMemoryChanged: vi.fn(),
  finalizeAutomationRun: vi.fn(),
}));

function makeDeps(overrides: Record<string, unknown> = {}): SessionConnectDeps & {
  setItems: ReturnType<typeof vi.fn>;
  sessionRef: { current: Session | null };
} {
  const deps = {
    booting: false,
    agent: "cowork",
    sessionId: "s1",
    workspace: "/ws",
    connectNonce: 0,
    t: ((k: string) => k) as any,
    streamingRef: { current: "" },
    reasoningRef: { current: "" },
    sessionRef: { current: null as Session | null },
    unattendedRef: { current: false },
    activeRunRef: { current: null },
    pendingPromptRef: { current: null },
    setStreaming: vi.fn(),
    setReasoningStream: vi.fn(),
    setItems: vi.fn((u: any) => u),
    setCompacting: vi.fn(),
    setConnected: vi.fn(),
    setModel: vi.fn(),
    setMode: vi.fn(),
    setWorkspaceTrustRequest: vi.fn(),
    setWorkspace: vi.fn(),
    setTempWorkspace: vi.fn(),
    setRunning: vi.fn(),
    setReviewerPaused: vi.fn(),
    setTodo: vi.fn(),
    setUsage: vi.fn(),
    setBrowserRefreshKey: vi.fn(),
    refreshSessions: vi.fn(),
    gatesWorkspace: () => false,
    newId: () => "t-1",
  } as unknown as SessionConnectDeps & { setItems: ReturnType<typeof vi.fn>; sessionRef: { current: Session | null } };
  return { ...deps, ...overrides } as any;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useSessionConnect", () => {
  it("creates a session and dispatches assistant_message into items", () => {
    const deps = makeDeps();
    renderHook(() => useSessionConnect(deps));
    const session = deps.sessionRef.current as any;

    act(() => {
      session.opts.onEvent({ type: "assistant_message", data: { text: "hello" } });
    });
    const updater = deps.setItems.mock.calls[0][0];
    const items = updater([]);
    expect(items[0]).toMatchObject({ kind: "assistant", text: "hello" });
    expect(deps.setStreaming).toHaveBeenCalledWith("");
  });

  it("ready event adopts the workspace and marks connected", () => {
    const deps = makeDeps();
    renderHook(() => useSessionConnect(deps));
    const session = deps.sessionRef.current as any;

    act(() => {
      session.opts.onEvent({ type: "ready", data: { workspace: "/ws/new", running: true } });
    });
    expect(deps.setConnected).toHaveBeenCalledWith(true);
    const wsUpdater = (deps as any).setWorkspace.mock.calls[0][0];
    expect(wsUpdater(null)).toBe("/ws/new");
    expect(deps.setRunning).toHaveBeenCalledWith(true);
  });

  it("turn_done stops running and refreshes sessions", () => {
    const deps = makeDeps();
    renderHook(() => useSessionConnect(deps));
    const session = deps.sessionRef.current as any;

    act(() => {
      session.opts.onEvent({ type: "turn_done", data: {} });
    });
    expect(deps.setRunning).toHaveBeenCalledWith(false);
    expect(deps.refreshSessions).toHaveBeenCalled();
    expect(deps.setBrowserRefreshKey).toHaveBeenCalled();
  });

  it("onOpen auto-sends the pending prompt and calls userMessage", () => {
    const deps = makeDeps();
    deps.pendingPromptRef.current = { text: "do it" };
    renderHook(() => useSessionConnect(deps));
    const session = deps.sessionRef.current as any;

    act(() => {
      session.opts.onOpen();
    });
    expect(deps.pendingPromptRef.current).toBeNull();
    const updater = deps.setItems.mock.calls[0][0];
    const items = updater([]);
    expect(items[0]).toMatchObject({ kind: "user", text: "do it" });
    expect(session.userMessage).toHaveBeenCalledWith("do it", undefined, undefined, undefined);
  });
});