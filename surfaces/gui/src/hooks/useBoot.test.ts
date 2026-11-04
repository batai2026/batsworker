import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { useBoot } from "./useBoot";
import {
  getHealth,
  getRecentWorkspaces,
  getSessionMessages,
  getSessions,
} from "../api";

vi.mock("../api", () => ({
  getHealth: vi.fn(),
  getSessions: vi.fn(),
  getSessionMessages: vi.fn(),
  getRecentWorkspaces: vi.fn(),
  getSettings: vi.fn(),
}));

function makeDeps(overrides: Record<string, unknown> = {}) {
  const deps = {
    agent: "cowork",
    connected: false,
    showGate: false,
    setShowGate: vi.fn(),
    gatesWorkspace: () => false,
    loadSettings: vi.fn(),
    loadPersonas: vi.fn(),
    setSessions: vi.fn(),
    setProjects: vi.fn(),
    setItems: vi.fn(),
    setUsage: vi.fn(),
    setSessionId: vi.fn(),
    setAgent: vi.fn(),
    setModel: vi.fn(),
    setWorkspace: vi.fn(),
    setBranch: vi.fn(),
  };
  return { ...deps, ...overrides } as any;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useBoot", () => {
  it("resumes the last session, then clears booting", async () => {
    const sessions = [
      {
        session_id: "s1",
        agent: "cowork",
        workspace: "/ws/launch",
        updated_at: "2026-10-25T00:00:00Z",
      },
    ] as any;
    vi.mocked(getHealth).mockResolvedValue({ model: "gpt-5.6-sol", default_workspace: null } as any);
    vi.mocked(getSessions).mockResolvedValue(sessions);
    vi.mocked(getSessionMessages).mockResolvedValue([]);
    vi.mocked(getRecentWorkspaces).mockResolvedValue([]);

    const deps = makeDeps();
    const { result } = renderHook(() => useBoot(deps));

    await waitFor(() => expect(result.current.booting).toBe(false));
    expect(result.current.resumedExisting).toBe(true);
    expect(deps.setSessionId).toHaveBeenCalledWith("s1");
    expect(deps.setWorkspace).toHaveBeenCalledWith("/ws/launch");
    expect(deps.setShowGate).toHaveBeenCalledWith(false);
  });

  it("falls through to the folder gate on a true first run", async () => {
    vi.mocked(getHealth).mockResolvedValue({ model: "gpt-5.6-sol", default_workspace: null } as any);
    vi.mocked(getSessions).mockResolvedValue([]);
    vi.mocked(getRecentWorkspaces).mockResolvedValue([]);

    const gatesWorkspace = vi.fn(() => true); // Code persona forces the gate
    const deps = makeDeps({ gatesWorkspace });
    const { result } = renderHook(() => useBoot(deps));

    await waitFor(() => expect(result.current.booting).toBe(false));
    expect(result.current.resumedExisting).toBe(false);
    expect(deps.setShowGate).toHaveBeenCalledWith(true);
  });
});