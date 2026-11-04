import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useKeyboardShortcuts } from "./useKeyboardShortcuts";

function keydown(page: Window, key: string, ctrl = true) {
  act(() => {
    page.dispatchEvent(
      new KeyboardEvent("keydown", { key, ctrlKey: ctrl, metaKey: ctrl, cancelable: true }),
    );
  });
}

describe("useKeyboardShortcuts", () => {
  it("toggles the nav on ⌘B / Ctrl+B", () => {
    const onToggleNav = vi.fn();
    renderHook(() => useKeyboardShortcuts({ onToggleNav, onOpenSettings: vi.fn() }));
    keydown(window, "b");
    expect(onToggleNav).toHaveBeenCalledTimes(1);
  });

  it("opens settings on ⌘, / Ctrl+,", () => {
    const onOpenSettings = vi.fn();
    renderHook(() => useKeyboardShortcuts({ onToggleNav: vi.fn(), onOpenSettings }));
    keydown(window, ",");
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it("ignores plain keystrokes without the modifier", () => {
    const onToggleNav = vi.fn();
    renderHook(() => useKeyboardShortcuts({ onToggleNav, onOpenSettings: vi.fn() }));
    keydown(window, "b", false);
    expect(onToggleNav).not.toHaveBeenCalled();
  });
});