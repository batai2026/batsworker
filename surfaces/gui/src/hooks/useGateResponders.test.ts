import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";
import { useGateResponders } from "./useGateResponders";
import type { Session } from "../api";
import type { Item } from "../types";

function makeItem(kind: string, text: string): Item {
  return { kind, text, ts: 0 } as unknown as Item;
}

function setup() {
  const sessionRef = { current: null as Session | null };
  const setItems = vi.fn((updater) => updater);
  const dropSessionInbox = vi.fn();
  const setMode = vi.fn();
  const refreshBoard = vi.fn();
  const setReviewerPaused = vi.fn();
  const respondPlan = vi.fn();
  const respondTeam = vi.fn();
  const respondItems = vi.fn();
  const respondDirectory = vi.fn();
  const respondTool = vi.fn();
  const respondQuestion = vi.fn();
  const approve = vi.fn();
  sessionRef.current = {
    respondPlan,
    respondTeam,
    respondItems,
    respondDirectory,
    respondTool,
    respondQuestion,
    approve,
  } as unknown as Session;

  const { result } = renderHook(() =>
    useGateResponders({
      sessionRef,
      setItems,
      dropSessionInbox,
      setMode,
      refreshBoard,
      setReviewerPaused,
    }),
  );
  return { result, sessionRef, setItems, dropSessionInbox, setMode, refreshBoard, setReviewerPaused, respondPlan, respondTeam, respondItems, respondDirectory, respondTool, respondQuestion, approve };
}

afterEach(cleanup);

describe("useGateResponders", () => {
  it("approve resolves the last approval and drops its inbox mirror", () => {
    const { result, setItems, dropSessionInbox, approve } = setup();
    result.current.approve({ decision: "allow" } as any);
    const updated = setItems.mock.calls[0][0]([
      makeItem("approval", "x"),
    ]) as Item[];
    expect(updated[0]).toMatchObject({ resolved: { decision: "allow" } });
    expect(dropSessionInbox).toHaveBeenCalledWith("approval");
    expect(approve).toHaveBeenCalledWith({ decision: "allow" });
  });

  it("respondPlan flips the live mode when approved with a mode", () => {
    const { result, setMode, respondPlan } = setup();
    result.current.respondPlan(true, "fast");
    expect(setMode).toHaveBeenCalledWith("fast");
    expect(respondPlan).toHaveBeenCalledWith(true, "fast", undefined);
  });

  it("respondItemsReq refreshes the board after approval", async () => {
    const { result, respondItems, refreshBoard } = setup();
    result.current.respondItemsReq(true);
    expect(respondItems).toHaveBeenCalledWith(true, undefined);
    await new Promise((r) => setTimeout(r, 450));
    expect(refreshBoard).toHaveBeenCalled();
  });

  it("answerQuestion resets the reviewer streak and resolves the question", () => {
    const { result, setReviewerPaused, respondQuestion, setItems } = setup();
    result.current.answerQuestion("yes");
    expect(setReviewerPaused).toHaveBeenCalledWith(false);
    expect(respondQuestion).toHaveBeenCalledWith("yes");
    const updated = setItems.mock.calls[0][0]([makeItem("question", "q")]) as Item[];
    expect(updated[0]).toMatchObject({ resolved: "yes" });
  });
});