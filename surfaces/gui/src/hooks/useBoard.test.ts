import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { useBoard } from "./useBoard";
import { boardTransition, getBoard } from "../api";

vi.mock("../api", () => ({
  getBoard: vi.fn(),
  boardTransition: vi.fn(),
}));

const BOARD = {
  space: "/Users/test/OpenWorker/launch-note",
  name: "launch-note",
  items: [
    {
      id: 1,
      title: "Fix auth",
      description: "",
      criteria: "auth fixed",
      state: "open",
      assignee: "",
      creator: "lead",
      refs: [],
      links: [],
    },
  ],
} as any;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useBoard", () => {
  it("clears the board outside session/Chat surfaces", () => {
    vi.mocked(getBoard).mockResolvedValue(BOARD);
    const { result } = renderHook(() =>
      useBoard("chat", "session", "s1", 0, false),
    );
    expect(result.current.board).toBeNull();
    expect(getBoard).not.toHaveBeenCalled();
  });

  it("loads the board for a session and moves items", async () => {
    vi.mocked(getBoard).mockResolvedValue(BOARD);
    vi.mocked(boardTransition).mockResolvedValue({ ok: true } as any);
    const { result } = renderHook(() =>
      useBoard("cowork", "session", "s1", 0, false),
    );
    await waitFor(() => expect(result.current.board?.name).toBe("launch-note"));

    await result.current.moveBoardItem(1, "done", "verified");
    expect(boardTransition).toHaveBeenCalledWith("s1", 1, "done", "verified");
    // moveBoardItem refreshes the board after the transition.
    expect(getBoard).toHaveBeenCalledWith("s1");
  });
});