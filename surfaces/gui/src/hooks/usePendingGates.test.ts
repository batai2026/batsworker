import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { usePendingGates } from "./usePendingGates";
import type { Item } from "../types";

// Gate items are discriminated by kind; the type has no shared `id`, so the
// test distinguishes them by their kind-specific fields.
const gate = (kind: "approval" | "planreq" | "question", tag: string, resolved = false): Item => {
  const base = { resolved } as any;
  if (kind === "approval")
    return { ...base, kind, name: `tool-${tag}`, args: {}, reason: "needs review" };
  if (kind === "planreq")
    return { ...base, kind, plan: `plan-${tag}` };
  return {
    ...base,
    kind,
    question: `q-${tag}`,
    options: ["yes", "no"],
    allow_text: false,
    multi: false,
    header: "Ask",
    questions: [],
  };
};

describe("usePendingGates", () => {
  it("returns only unresolved gates, newest first", () => {
    const items = [
      gate("approval", "a1", true), // resolved → ignored
      gate("planreq", "p1", false),
      gate("approval", "a2", false),
    ];
    const { result } = renderHook(() => usePendingGates(items));
    expect((result.current.pendingApproval as any)?.name).toBe("tool-a2");
    expect((result.current.pendingPlan as any)?.plan).toBe("plan-p1");
    expect(result.current.pendingQuestion).toBeUndefined();
  });

  it("finds nothing when all gates are resolved", () => {
    const items = [gate("approval", "a1", true), gate("question", "q1", true)];
    const { result } = renderHook(() => usePendingGates(items));
    expect(result.current.pendingApproval).toBeUndefined();
    expect(result.current.pendingQuestion).toBeUndefined();
  });
});