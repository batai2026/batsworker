import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";
import { useSessionActions, type SessionActionsDeps } from "./useSessionActions";
import {
  deleteSession,
  getRecentWorkspaces,
  getSessionMessages,
  getSessions,
  runAutomation,
} from "../api";
import { resumeTargetForAgent } from "./useSessions";

vi.mock("../api", () => ({
  getSessions: vi.fn(),
  getSessionMessages: vi.fn(),
  getRecentWorkspaces: vi.fn(),
  renameSession: vi.fn(),
  setSessionFlags: vi.fn(),
  deleteSession: vi.fn(),
  runAutomation: vi.fn(),
}));

vi.mock("./useSessions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./useSessions")>();
  return {
    ...actual,
    rememberLastSession: vi.fn(),
    resumeTargetForAgent: vi.fn(),
  };
});

function makeDeps(overrides: Record<string, unknown> = {}): SessionActionsDeps {
  const deps = {
    agent: "cowork",
    sessionId: "s1",
    workspace: "/ws/a",
    tempWorkspace: false,
    sessions: [] as any[],
    projects: [],
    setSurface: vi.fn(),
    setTodo: vi.fn(),
    setStreaming: vi.fn(),
    setRunning: vi.fn(),
    setAgent: vi.fn(),
    setReviewerPaused: vi.fn(),
    setDraftFolderPicked: vi.fn(),
    setTempWorkspace: vi.fn(),
    setShowGate: vi.fn(),
    setWorkspace: vi.fn(),
    setBranch: vi.fn(),
    setSessionId: vi.fn(),
    setItems: vi.fn((u: any) => u),
    setUsage: vi.fn(),
    setProjects: vi.fn(),
    setGateCreate: vi.fn(),
    setRunContext: vi.fn(),
    activeRunRef: { current: null },
    pendingPromptRef: { current: null },
    refreshSessions: vi.fn(),
    gatesWorkspace: () => false,
    newId: () => "new-1",
    fallbackWorkspace: () => "/ws/fallback",
  } as unknown as SessionActionsDeps;
  return { ...deps, ...overrides } as SessionActionsDeps;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useSessionActions", () => {
  it("selectSession loads the session's transcript", async () => {
    vi.mocked(getSessionMessages).mockResolvedValue([]);
    const deps = makeDeps();
    const { result } = renderHook(() => useSessionActions(deps));
    await result.current.selectSession("s9", "/ws/nine", "code");

    expect(deps.setSurface).toHaveBeenCalledWith("session");
    expect(deps.setSessionId).toHaveBeenCalledWith("s9");
    expect(deps.setWorkspace).toHaveBeenCalledWith("/ws/nine");
    expect(getSessionMessages).toHaveBeenCalledWith("s9");
  });

  it("switchAgent resumes a remembered session when one exists", async () => {
    const target = { sessionId: "old", workspace: "/ws/old", agent: "security" };
    vi.mocked(resumeTargetForAgent).mockReturnValue(target as any);
    vi.mocked(getSessions).mockResolvedValue([]);
    vi.mocked(getRecentWorkspaces).mockResolvedValue([]);
    vi.mocked(getSessionMessages).mockResolvedValue([]);
    const deps = makeDeps();
    const { result } = renderHook(() => useSessionActions(deps));
    await result.current.switchAgent("security");

    expect(deps.setAgent).toHaveBeenCalledWith("security");
    expect(deps.setSessionId).toHaveBeenCalledWith("old");
    expect(deps.setShowGate).toHaveBeenCalledWith(false);
  });

  it("deleteConversation resets the view when deleting the active session", async () => {
    vi.mocked(deleteSession).mockResolvedValue({ ok: true } as any);
    const deps = makeDeps();
    const { result } = renderHook(() => useSessionActions(deps));
    await result.current.deleteConversation("s1"); // sessionId === deps.sessionId

    expect(deleteSession).toHaveBeenCalledWith("s1");
    expect(deps.refreshSessions).toHaveBeenCalled();
    expect(deps.setSessionId).toHaveBeenCalledWith("new-1");
  });

  it("runTaskNow opens the run session and parks the prompt", async () => {
    vi.mocked(runAutomation).mockResolvedValue({
      ok: true,
      run_id: "r1",
      session_id: "s-run",
      workspace: "/ws/run",
      agent: "code",
      prompt: "do the thing",
    } as any);
    vi.mocked(getSessionMessages).mockResolvedValue([]);
    const deps = makeDeps();
    const { result } = renderHook(() => useSessionActions(deps));
    await result.current.runTaskNow("t1", "Digest");

    expect(deps.pendingPromptRef.current).toEqual({ text: "do the thing" });
    expect(deps.activeRunRef.current).toEqual({ taskId: "t1", runId: "r1", sessionId: "s-run" });
    expect(deps.setRunContext).toHaveBeenCalledWith({ id: "t1", title: "Digest" });
  });
});