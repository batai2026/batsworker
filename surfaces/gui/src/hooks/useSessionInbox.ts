import { useCallback, useEffect, useRef, useState } from "react";
import {
  announceInboxUnlock,
  getInbox,
  getUnattended,
  resolveInboxItem,
  setUnattended,
  type InboxItem,
} from "../api";

// Pending Inbox items for the ACTIVE session — surfaced inline above the composer
// so an unattended session's blocking question/approval can be answered in
// context (resolving the same item the Inbox shows; first responder wins). Also
// owns the Unattended mode flag (a ref mirrors it for the WS handler). Extracted
// from App.tsx.

export function useSessionInbox(
  sessionId: string,
  surface: string,
  browserRefreshKey: number,
  refreshSessions: () => void,
) {
  const [sessionInbox, setSessionInbox] = useState<InboxItem[]>([]);
  // Whether the active session is Unattended — when true, the agent's prompts route to the Inbox,
  // so we suppress the inline live cards (the Inbox / answer-in-context path shows them instead).
  const [unattended, setUnattendedState] = useState(false);
  // A ref too, because the WS event handler closes over stale state.
  const unattendedRef = useRef(false);

  const markUnattended = useCallback((on: boolean) => {
    unattendedRef.current = on;
    setUnattendedState(on);
  }, []);

  // The Mode menu's "Send approvals to Inbox" toggle (§22 — the old InboxControl, folded in).
  const toggleUnattended = async (on: boolean) => {
    await setUnattended(sessionId, on);
    markUnattended(on);
    // First Unattended enable = Inbox machinery engaged → the account row's chip unlocks (§26).
    if (on) announceInboxUnlock();
  };

  const resolveSessionInbox = async (id: string, resolution: string) => {
    await resolveInboxItem(id, resolution);
    getInbox(sessionId, "pending")
      .then(setSessionInbox)
      .catch(() => setSessionInbox([]));
    refreshSessions(); // attention badge should drop right away
  };

  // Keep the active session's pending Inbox items fresh (answer-in-context card). Loads on session
  // change + after each turn, plus a slow poll so an unattended agent's new question surfaces.
  useEffect(() => {
    if (surface !== "session") return;
    const load = () => {
      getInbox(sessionId, "pending")
        .then(setSessionInbox)
        .catch(() => setSessionInbox([]));
      getUnattended(sessionId)
        .then(markUnattended)
        .catch(() => markUnattended(false));
    };
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [surface, sessionId, browserRefreshKey, markUnattended]);

  // Resolving a LIVE prompt also resolves its parked Inbox mirror server-side, but the polled
  // `sessionInbox` copy stays "pending" for up to a poll cycle — long enough for the docked
  // answer-in-context card to flash the SAME request again right after the user answered it
  // (tester catch 2026-07-12: a Slack send "asked twice"). Drop the mirror optimistically;
  // the 4s poll restores anything genuinely still pending.
  const dropSessionInbox = (kind: string) =>
    setSessionInbox((cur) => cur.filter((it) => it.kind !== kind));

  return {
    sessionInbox,
    setSessionInbox,
    unattended,
    unattendedRef,
    markUnattended,
    toggleUnattended,
    resolveSessionInbox,
    dropSessionInbox,
  };
}