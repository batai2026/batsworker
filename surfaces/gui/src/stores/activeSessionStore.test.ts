import { beforeEach, describe, expect, it } from "vitest";
import { newSessionId, useActiveSessionStore } from "./activeSessionStore";

// The store is a module-level singleton; reset every field so cases (and any other
// file reusing the store) never see a previous test's active session.
beforeEach(() => {
  useActiveSessionStore.setState({
    sessionId: "seed-session",
    workspace: null,
    branch: null,
    agent: "cowork",
    model: "gpt-5.6-sol",
  });
});

describe("activeSessionStore", () => {
  it("starts on a fresh cowork session with no folder", () => {
    const state = useActiveSessionStore.getState();
    expect(state.workspace).toBeNull();
    expect(state.branch).toBeNull();
    expect(state.agent).toBe("cowork");
    expect(state.model).toBe("gpt-5.6-sol");
    expect(state.sessionId).toBe("seed-session");
    expect(newSessionId()).toHaveLength(12);
  });

  it("accepts plain values for the Dispatch-shaped setters", () => {
    useActiveSessionStore.getState().setSessionId("s2");
    useActiveSessionStore.getState().setWorkspace("/ws/two");
    useActiveSessionStore.getState().setBranch("main");
    useActiveSessionStore.getState().setAgent("code");
    useActiveSessionStore.getState().setModel("gpt-5.5-sol");

    expect(useActiveSessionStore.getState()).toMatchObject({
      sessionId: "s2",
      workspace: "/ws/two",
      branch: "main",
      agent: "code",
      model: "gpt-5.5-sol",
    });
  });

  it("applies updater functions like React setState", () => {
    useActiveSessionStore.getState().setWorkspace(() => "/ws/from-updater");
    // `setWorkspace((cur) => cur || d.workspace)` in useSessionConnect relies on this.
    useActiveSessionStore.getState().setWorkspace((cur) => cur || "/ws/ignored");
    useActiveSessionStore.getState().setModel((cur) => `${cur}!`);

    expect(useActiveSessionStore.getState().workspace).toBe("/ws/from-updater");
    expect(useActiveSessionStore.getState().model).toBe("gpt-5.6-sol!");
  });

  it("keeps unrelated fields untouched", () => {
    useActiveSessionStore.getState().setAgent("chat");

    expect(useActiveSessionStore.getState().workspace).toBeNull();
    expect(useActiveSessionStore.getState().sessionId).toBe("seed-session");
    expect(useActiveSessionStore.getState().model).toBe("gpt-5.6-sol");
  });
});
