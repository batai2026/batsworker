import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { useSessions, rememberLastSession, resumeTargetForAgent, readLastSessions } from "./useSessions";
import { useSessionStore } from "../stores/sessionStore";
import { getRecentWorkspaces, getSessions, PERSONAS_CHANGED } from "../api";

vi.mock("../api", () => ({
  getSessions: vi.fn(),
  getRecentWorkspaces: vi.fn(),
  PERSONAS_CHANGED: "coworker:personas-changed",
}));

const SESSIONS = [
  {
    session_id: "s1",
    title: "Draft the launch note",
    agent: "cowork",
    workspace: "/ws/launch",
    updated_at: "2026-10-25T00:00:00Z",
  },
] as any;

const PROJECTS = [{ path: "/ws/launch", exists: true }] as any;

// store 是模块级单例：每个用例前清空，避免上一个用例的列表泄漏到下一条断言。
beforeEach(() => {
  useSessionStore.setState({ sessions: [], projects: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useSessions", () => {
  it("loads sessions + projects on mount", async () => {
    vi.mocked(getSessions).mockResolvedValue(SESSIONS);
    vi.mocked(getRecentWorkspaces).mockResolvedValue(PROJECTS);

    const { result } = renderHook(() => useSessions());

    await waitFor(() => expect(result.current.sessions.length).toBe(1));
    expect(result.current.sessions[0].session_id).toBe("s1");
    expect(result.current.projects[0].path).toBe("/ws/launch");
  });

  it("refreshes sessions on PERSONAS_CHANGED", async () => {
    vi.mocked(getSessions).mockResolvedValue([]);
    vi.mocked(getRecentWorkspaces).mockResolvedValue([]);
    const { result } = renderHook(() => useSessions());
    await waitFor(() => expect(result.current.sessions).toEqual([]));

    vi.mocked(getSessions).mockResolvedValue(SESSIONS);
    window.dispatchEvent(new CustomEvent(PERSONAS_CHANGED));
    await waitFor(() => expect(result.current.sessions.length).toBe(1));
  });

  it("exposes the boot-resume helpers", () => {
    localStorage.clear();
    // Nothing remembered → resume falls back to the most recent session.
    expect(resumeTargetForAgent("cowork", SESSIONS)?.sessionId).toBe("s1");

    // Remember a session → it wins over recency.
    rememberLastSession("cowork", "s9", "/ws/remembered");
    expect(resumeTargetForAgent("cowork", SESSIONS)?.sessionId).toBe("s9");
    expect(readLastSessions()["cowork"].workspace).toBe("/ws/remembered");
  });
});