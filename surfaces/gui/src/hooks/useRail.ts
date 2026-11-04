import { useCallback, useEffect, useRef, useState } from "react";

const RAIL_HIDDEN_KEY = "coworker:rail-hidden:v1";
const NAV_COLLAPSED_KEY = "coworker:nav-collapsed:v1";

// Right-rail + left-nav visibility state (extracted from App.tsx). Includes the
// per-device persistence, the artifact-preview auto-collapse of the nav, and the
// deep-link show events (artifact/board chips un-hide the rail transiently).

export function useRail() {
  // UX-038 follow-up (owner ruling 2026-08-21): the rail starts HIDDEN and the
  // topbar toggle persists per-device. Deep links (artifact/board chips, Access)
  // still force-show transiently — they never overwrite the stored preference.
  const [railHidden, setRailHidden] = useState<boolean>(() => {
    try {
      return localStorage.getItem(RAIL_HIDDEN_KEY) !== "0";
    } catch {
      return true;
    }
  });
  const setRailHiddenPersist = useCallback((v: boolean) => {
    setRailHidden(v);
    try {
      localStorage.setItem(RAIL_HIDDEN_KEY, v ? "1" : "0");
    } catch {
      /* best effort */
    }
  }, []);

  // Left-nav collapse (⌘B): when collapsed the sidebar leaves the grid so content
  // reclaims the width; hovering the left edge peeks it back as a floating overlay.
  const [navCollapsed, setNavCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(NAV_COLLAPSED_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [navPeek, setNavPeek] = useState(false);
  // While an artifact preview is open we auto-collapse the nav (#3). Remember the
  // pre-preview collapse state so we can restore it on close — unless the user
  // re-opened the nav meanwhile.
  const navBeforePreview = useRef<boolean | null>(null);
  const setNavCollapsedPersist = useCallback((v: boolean) => {
    setNavCollapsed(v);
    try {
      localStorage.setItem(NAV_COLLAPSED_KEY, v ? "1" : "0");
    } catch {
      /* best effort */
    }
  }, []);
  const toggleNav = useCallback(() => {
    setNavPeek(false);
    navBeforePreview.current = null; // a manual toggle takes control from the artifact auto-collapse
    setNavCollapsedPersist(!navCollapsed);
  }, [navCollapsed, setNavCollapsedPersist]);

  // STABLE identity (no deps): depending on navCollapsed changed this callback's
  // identity on every nav toggle, which re-ran the rail's notify effect with the
  // viewer still open and re-collapsed the nav the instant the user expanded it
  // (owner-hit 2026-08-21). The current collapse state is read through the
  // functional updater instead.
  const onArtifactPreview = useCallback((open: boolean) => {
    if (open) {
      setNavPeek(false);
      setNavCollapsed((cur) => {
        if (navBeforePreview.current === null) navBeforePreview.current = cur;
        return true;
      });
    } else if (navBeforePreview.current !== null) {
      setNavCollapsed(navBeforePreview.current);
      navBeforePreview.current = null;
    }
  }, []);

  // §34 (UX-016): clicking an artifact chip in the transcript must land somewhere
  // visible — RightRail opens the viewer; this just makes sure the rail isn't hidden.
  useEffect(() => {
    const show = () => setRailHidden(false);
    window.addEventListener("ocw-open-artifact", show);
    return () => window.removeEventListener("ocw-open-artifact", show);
  }, []);

  // §32 deep link into the rail's Access section: bumping the key expands the
  // section and scrolls it into view. Callers also un-hide the rail.
  const [accessKey, setAccessKey] = useState(0);
  const openAccess = useCallback(() => {
    setRailHidden(false);
    setAccessKey((k) => k + 1);
  }, []);

  // Seventeenth pass: the lead's one-time [Board · N items](board:) chip — un-hide
  // the rail and bump the key that expands its Board section.
  const [boardRailKey, setBoardRailKey] = useState(0);
  useEffect(() => {
    const show = () => {
      setRailHidden(false);
      setBoardRailKey((k) => k + 1);
    };
    window.addEventListener("ocw-open-board", show);
    return () => window.removeEventListener("ocw-open-board", show);
  }, []);

  return {
    railHidden,
    setRailHidden,
    setRailHiddenPersist,
    navCollapsed,
    navPeek,
    setNavPeek,
    toggleNav,
    onArtifactPreview,
    openAccess,
    accessKey,
    boardRailKey,
  };
}