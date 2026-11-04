import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { useAutomationToast } from "./useAutomationToast";
import { announceAutomationsChanged, connectEvents } from "../api";

vi.mock("../api", () => ({
  connectEvents: vi.fn(),
  announceAutomationsChanged: vi.fn(),
}));

const t = ((key: string) => key) as any;

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("useAutomationToast", () => {
  it("subscribes to automation_run_started and announces the change", async () => {
    let handler: ((msg: any) => void) | undefined;
    vi.mocked(connectEvents).mockImplementation((h: any) => {
      handler = h;
      return () => {};
    });
    const { result } = renderHook(() => useAutomationToast(t));

    expect(connectEvents).toHaveBeenCalled();
    handler?.({
      type: "automation_run_started",
      data: { task_title: "Weekly digest", session_id: "s-run", workspace: "/ws", agent: "cowork" },
    });
    await waitFor(() => expect(result.current.runToast?.title).toBe("Weekly digest"));
    expect(announceAutomationsChanged).toHaveBeenCalled();
  });

  it("auto-dismisses after 5 seconds", () => {
    vi.useFakeTimers();
    vi.mocked(connectEvents).mockImplementation(() => () => {});
    const { result } = renderHook(() => useAutomationToast(t));

    act(() =>
      result.current.setRunToast({
        title: "X",
        sessionId: "",
        workspace: "",
        agent: "cowork",
        time: "09:00",
      }),
    );
    expect(result.current.runToast).not.toBeNull();

    act(() => {
      vi.advanceTimersByTime(5001); // past the 5s auto-dismiss
    });
    expect(result.current.runToast).toBeNull();
  });
});