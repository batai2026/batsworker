import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { useRail } from "./useRail";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("useRail", () => {
  it("defaults to a hidden rail and persists the toggle", () => {
    const { result } = renderHook(() => useRail());
    expect(result.current.railHidden).toBe(true);

    act(() => result.current.setRailHiddenPersist(false));
    expect(result.current.railHidden).toBe(false);
    expect(localStorage.getItem("coworker:rail-hidden:v1")).toBe("0");
  });

  it("toggleNav flips the collapsed nav and persists it", () => {
    const { result } = renderHook(() => useRail());
    expect(result.current.navCollapsed).toBe(false);

    act(() => result.current.toggleNav());
    expect(result.current.navCollapsed).toBe(true);
    expect(localStorage.getItem("coworker:nav-collapsed:v1")).toBe("1");

    act(() => result.current.toggleNav());
    expect(result.current.navCollapsed).toBe(false);
  });

  it("artifact preview auto-collapses the nav then restores it", () => {
    const { result } = renderHook(() => useRail());

    act(() => result.current.onArtifactPreview(true));
    expect(result.current.navCollapsed).toBe(true);

    act(() => result.current.onArtifactPreview(false));
    expect(result.current.navCollapsed).toBe(false);
  });

  it("deep-link events un-hide the rail (artifact / board chips)", () => {
    const { result } = renderHook(() => useRail());

    act(() => {
      window.dispatchEvent(new CustomEvent("ocw-open-artifact"));
    });
    expect(result.current.railHidden).toBe(false);

    // Hide again, then the board chip un-hides + bumps its key.
    act(() => result.current.setRailHiddenPersist(true));
    const keyBefore = result.current.boardRailKey;
    act(() => {
      window.dispatchEvent(new CustomEvent("ocw-open-board"));
    });
    expect(result.current.railHidden).toBe(false);
    expect(result.current.boardRailKey).toBe(keyBefore + 1);
  });
});