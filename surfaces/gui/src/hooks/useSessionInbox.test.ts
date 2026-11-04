import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { useSessionInbox } from "./useSessionInbox";
import {
  announceInboxUnlock,
  getInbox,
  getUnattended,
  resolveInboxItem,
  setUnattended,
} from "../api";

vi.mock("../api", () => ({
  getInbox: vi.fn(),
  getUnattended: vi.fn(),
  setUnattended: vi.fn(),
  resolveInboxItem: vi.fn(),
  announceInboxUnlock: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useSessionInbox", () => {
  it("loads pending inbox + unattended on mount", async () => {
    vi.mocked(getInbox).mockResolvedValue([{ id: "i1", kind: "approval" } as any]);
    vi.mocked(getUnattended).mockResolvedValue(true);
    const { result } = renderHook(() =>
      useSessionInbox("s1", "session", 0, vi.fn()),
    );
    await waitFor(() => expect(result.current.sessionInbox.length).toBe(1));
    await waitFor(() => expect(result.current.unattended).toBe(true));
    expect(result.current.unattendedRef.current).toBe(true);
  });

  it("toggleUnattended persists and unlocks the inbox", async () => {
    vi.mocked(getInbox).mockResolvedValue([]);
    vi.mocked(getUnattended).mockResolvedValue(false);
    vi.mocked(setUnattended).mockResolvedValue({ ok: true, unattended: true });
    const { result } = renderHook(() =>
      useSessionInbox("s1", "session", 0, vi.fn()),
    );
    await waitFor(() => expect(result.current.unattended).toBe(false));

    await result.current.toggleUnattended(true);
    expect(setUnattended).toHaveBeenCalledWith("s1", true);
    expect(announceInboxUnlock).toHaveBeenCalled();
    await waitFor(() => expect(result.current.unattended).toBe(true));
  });

  it("resolving an item refreshes the pending list and sessions", async () => {
    vi.mocked(getInbox).mockResolvedValue([]);
    vi.mocked(getUnattended).mockResolvedValue(false);
    vi.mocked(resolveInboxItem).mockResolvedValue({ ok: true });
    const refreshSessions = vi.fn();
    const { result } = renderHook(() =>
      useSessionInbox("s1", "session", 0, refreshSessions),
    );
    await result.current.resolveSessionInbox("i1", "allow");
    expect(resolveInboxItem).toHaveBeenCalledWith("i1", "allow");
    expect(getInbox).toHaveBeenCalledWith("s1", "pending");
    expect(refreshSessions).toHaveBeenCalled();
  });
});