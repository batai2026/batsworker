import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { useSessionStore } from "./sessionStore";
import { getRecentWorkspaces, getSessions } from "../api";

vi.mock("../api", () => ({
  getSessions: vi.fn(),
  getRecentWorkspaces: vi.fn(),
}));

const SESSIONS = [{ session_id: "s1", title: "t", agent: "cowork", workspace: "/ws" }] as any;
const PROJECTS = [{ path: "/ws", exists: true }] as any;

beforeEach(() => {
  useSessionStore.setState({ sessions: [], projects: [] });
  vi.clearAllMocks();
});

describe("sessionStore", () => {
  it("refreshSessions fills sessions + projects from the API", async () => {
    vi.mocked(getSessions).mockResolvedValue(SESSIONS);
    vi.mocked(getRecentWorkspaces).mockResolvedValue(PROJECTS);

    useSessionStore.getState().refreshSessions();

    await waitFor(() => expect(useSessionStore.getState().sessions).toEqual(SESSIONS));
    expect(useSessionStore.getState().projects).toEqual(PROJECTS);
  });

  it("refreshSessions clears the lists when the API rejects", async () => {
    useSessionStore.setState({ sessions: SESSIONS, projects: PROJECTS });
    vi.mocked(getSessions).mockRejectedValue(new Error("boom"));
    vi.mocked(getRecentWorkspaces).mockRejectedValue(new Error("boom"));

    useSessionStore.getState().refreshSessions();

    await waitFor(() => expect(useSessionStore.getState().sessions).toEqual([]));
    expect(useSessionStore.getState().projects).toEqual([]);
  });
});
