import { beforeEach, describe, expect, it } from "vitest";
import { emptyUsage } from "../usage";
import { useRunStore } from "./runStore";

// Module-level singleton: reset every field so cases (and other files reusing the
// store) never see a previous test's transcript or turn flags.
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

describe("runStore", () => {
  it("starts idle with an empty transcript", () => {
    const state = useRunStore.getState();
    expect(state.running).toBe(false);
    expect(state.compacting).toBe(false);
    expect(state.streaming).toBe("");
    expect(state.reasoningStream).toBe("");
    expect(state.items).toEqual([]);
    expect(state.todo).toEqual([]);
    expect(state.usage).toEqual(emptyUsage());
  });

  it("accepts plain values for the Dispatch-shaped setters", () => {
    const s = useRunStore.getState();
    s.setRunning(true);
    s.setCompacting(true);
    s.setStreaming("hello");
    s.setReasoningStream("thinking");
    s.setItems([{ kind: "user", text: "hi", ts: 1 }] as any);
    s.setTodo([{ content: "step", status: "pending" }]);

    expect(useRunStore.getState()).toMatchObject({
      running: true,
      compacting: true,
      streaming: "hello",
      reasoningStream: "thinking",
      todo: [{ content: "step", status: "pending" }],
    });
    expect(useRunStore.getState().items).toHaveLength(1);
  });

  it("applies updater functions like React setState", () => {
    const s = useRunStore.getState();
    s.setStreaming((cur) => cur + "a");
    s.setStreaming((cur) => cur + "b"); // assistant_delta relies on the accumulator form
    s.setItems((prev) => [...prev, { kind: "notice", tone: "info", text: "x" } as any]);

    expect(useRunStore.getState().streaming).toBe("ab");
    expect(useRunStore.getState().items).toHaveLength(1);
  });

  it("keeps unrelated fields untouched", () => {
    useRunStore.getState().setRunning(true);

    expect(useRunStore.getState().streaming).toBe("");
    expect(useRunStore.getState().items).toEqual([]);
    expect(useRunStore.getState().usage).toEqual(emptyUsage());
  });
});
