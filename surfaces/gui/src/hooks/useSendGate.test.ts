import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { useSendGate, type SendGateDeps } from "./useSendGate";
import { createTempWorkspace, getRecentWorkspaces } from "../api";

vi.mock("../api", () => ({
  createTempWorkspace: vi.fn(),
  getRecentWorkspaces: vi.fn(),
}));

const t = ((k: string) => k) as any;

function makeDeps(overrides: Record<string, unknown> = {}): SendGateDeps & { [k: string]: any } {
  const deps = {
    model: "gpt-5.6-sol",
    setWorkspace: vi.fn(),
    setBranch: vi.fn(),
    setTempWorkspace: vi.fn(),
    setSessionId: vi.fn(),
    setItems: vi.fn((u: any) => u),
    setProjects: vi.fn(),
    pendingPromptRef: { current: null },
    newId: () => "new-1",
    prefillComposer: vi.fn(),
    t,
  } as unknown as SendGateDeps & { [k: string]: any };
  return { ...deps, ...overrides };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useSendGate", () => {
  it("stashes a gated send and resolves it by binding a real folder", () => {
    vi.mocked(getRecentWorkspaces).mockResolvedValue([]);
    const deps = makeDeps();
    const { result } = renderHook(() => useSendGate(deps));

    act(() => result.current.setSendGate({ text: "hello", skill: "code" }));
    act(() => result.current.resolveSendFolder("/ws/repo", "main"));

    expect(result.current.sendGate).toBeNull(); // gate cleared by the hook's own state
    expect(deps.setWorkspace).toHaveBeenCalledWith("/ws/repo");
    expect(deps.setBranch).toHaveBeenCalledWith("main");
    expect(deps.pendingPromptRef.current).toMatchObject({
      text: "hello",
      skill: "code",
      model: "gpt-5.6-sol",
    });
  });

  it("starts a temporary workspace and parks the prompt with a notice", async () => {
    vi.mocked(createTempWorkspace).mockResolvedValue({
      ok: true,
      path: "/tmp/scratch",
      git: true,
    } as any);
    const deps = makeDeps();
    const { result } = renderHook(() => useSendGate(deps));

    act(() => result.current.setSendGate({ text: "go", attachments: [] }));
    await result.current.startTempAndSend();

    expect(deps.setWorkspace).toHaveBeenCalledWith("/tmp/scratch");
    expect(deps.setTempWorkspace).toHaveBeenCalledWith(true);
    expect(deps.pendingPromptRef.current).toMatchObject({
      text: "go",
      model: "gpt-5.6-sol",
      notice: "Temporary folder created · git initialized",
    });
  });

  it("cancel restores the draft to the composer", () => {
    const deps = makeDeps();
    const { result } = renderHook(() => useSendGate(deps));

    act(() => result.current.setSendGate({ text: "draft", skill: "code" }));
    result.current.cancelSendGate();

    expect(deps.prefillComposer).toHaveBeenCalledWith("/code draft", undefined);
  });
});