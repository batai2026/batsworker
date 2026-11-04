import { useEffect, useRef } from "react";

export interface KeyboardShortcuts {
  onToggleNav: () => void;
  onOpenSettings: () => void;
}

// Global keyboard shortcuts (extracted from App.tsx):
//   ⌘B / Ctrl+B — toggle the left nav collapse.
//   ⌘, / Ctrl+, — open the full-page Settings surface.
// Callbacks are read through a ref so the window listener is attached exactly once
// while always invoking the latest handlers (same behavior as the original
// [toggleNav]-dep effect).

export function useKeyboardShortcuts(handlers: KeyboardShortcuts) {
  const ref = useRef(handlers);
  ref.current = handlers;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        ref.current.onToggleNav();
      }
      // ⌘, — the platform Settings shortcut (advertised in the account menu, §26).
      if ((e.metaKey || e.ctrlKey) && e.key === ",") {
        e.preventDefault();
        ref.current.onOpenSettings();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}