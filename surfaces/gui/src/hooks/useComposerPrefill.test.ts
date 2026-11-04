import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useComposerPrefill } from "./useComposerPrefill";

describe("useComposerPrefill", () => {
  it("starts empty and bumps the nonce on each prefill", () => {
    const { result } = renderHook(() => useComposerPrefill());
    expect(result.current.composerPrefill).toBeUndefined();

    act(() => result.current.prefillComposer("hello"));
    expect(result.current.composerPrefill).toEqual({
      text: "hello",
      attachments: undefined,
      nonce: 1,
    });

    act(() => result.current.prefillComposer("/skill go", []));
    expect(result.current.composerPrefill?.text).toBe("/skill go");
    expect(result.current.composerPrefill?.nonce).toBe(2);
  });
});