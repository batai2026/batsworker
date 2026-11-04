import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { useArtifactCount } from "./useArtifactCount";
import { getArtifacts } from "../api";

vi.mock("../api", () => ({
  getArtifacts: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useArtifactCount", () => {
  it("returns 0 for non-Cowork agents or non-session surfaces", () => {
    vi.mocked(getArtifacts).mockResolvedValue([]);
    const { result } = renderHook(() =>
      useArtifactCount("code", "session", "s1", 0),
    );
    expect(result.current).toBe(0);
    expect(getArtifacts).not.toHaveBeenCalled();
  });

  it("fetches the artifact count for Cowork sessions", async () => {
    vi.mocked(getArtifacts).mockResolvedValue([{ path: "a.md" }, { path: "b.md" }] as any);
    const { result } = renderHook(() =>
      useArtifactCount("cowork", "session", "s1", 0),
    );
    await waitFor(() => expect(result.current).toBe(2));
    expect(getArtifacts).toHaveBeenCalledWith("s1");
  });
});