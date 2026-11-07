import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { emptyUsage } from "../usage";
import { useRunStore } from "../stores/runStore";
import { useSessionStream } from "./useSessionStream";

beforeEach(() => {
  useRunStore.setState({
    running: false,
    compacting: false,
    streaming: "",
    reasoningStream: "",
    items: [],
    todo: [],
    usage: emptyUsage(),
  });
});

afterEach(cleanup);

describe("useSessionStream", () => {
  it("keeps the streaming ref mirror in sync with the store", () => {
    const { result } = renderHook(() => useSessionStream());

    act(() => result.current.setStreaming((s) => s + "tok"));
    expect(result.current.streamingRef.current).toBe("tok");
    expect(useRunStore.getState().streaming).toBe("tok");

    // The second append reads the mirror, not a stale render-time value.
    act(() => result.current.setStreaming((s) => s + "en"));
    expect(result.current.streamingRef.current).toBe("token");
    expect(useRunStore.getState().streaming).toBe("token");
  });

  it("clears the mirror and the store together", () => {
    const { result } = renderHook(() => useSessionStream());

    act(() => result.current.setStreaming("partial answer"));
    act(() => result.current.setStreaming(""));

    expect(result.current.streamingRef.current).toBe("");
    expect(useRunStore.getState().streaming).toBe("");
  });

  it("mirrors the reasoning stream", () => {
    const { result } = renderHook(() => useSessionStream());

    act(() => result.current.setReasoningStream("plan"));
    expect(result.current.reasoningRef.current).toBe("plan");
    expect(useRunStore.getState().reasoningStream).toBe("plan");

    // reasoning_delta appends via the ref, so consecutive tokens accumulate.
    act(() => result.current.setReasoningStream(result.current.reasoningRef.current + "ning"));
    expect(useRunStore.getState().reasoningStream).toBe("planning");
  });
});
