import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import {
  getHealth,
  getRecentWorkspaces,
  getSessionMessages,
  getSessions,
  getSettings,
  type RecentWorkspace,
} from "../api";
import type { Item, SessionInfo, SessionUsage } from "../types";
import { itemsFromMessages } from "../itemsFromMessages";
import { emptyUsage, usageFromMessages } from "../usage";
import { isTauri } from "../tauri";

// Boot / first-run gate logic extracted from App.tsx:
//  - `booting`: true until the sidecar health check + last-session resume settle.
//  - `onboarding`: desktop first-run setup wizard flag.
//  - `resumedExisting`: whether we resumed a prior conversation (splash wording).
//  - `uiReady`: latched reveal — the splash stays up until the restored session is
//    CONNECTED (or the folder gate is showing), plus a short safety-net timeout.

export interface UseBootDeps {
  agent: string;
  connected: boolean;
  showGate: boolean;
  setShowGate: Dispatch<SetStateAction<boolean>>;
  gatesWorkspace: (a: string) => boolean;
  loadSettings: () => void;
  loadPersonas: () => void;
  setSessions: Dispatch<SetStateAction<SessionInfo[]>>;
  setProjects: Dispatch<SetStateAction<RecentWorkspace[]>>;
  setItems: Dispatch<SetStateAction<Item[]>>;
  setUsage: Dispatch<SetStateAction<SessionUsage>>;
  setSessionId: Dispatch<SetStateAction<string>>;
  setAgent: Dispatch<SetStateAction<string>>;
  setModel: Dispatch<SetStateAction<string>>;
  setWorkspace: Dispatch<SetStateAction<string | null>>;
  setBranch: Dispatch<SetStateAction<string | null>>;
}

export function useBoot(deps: UseBootDeps) {
  const {
    agent,
    connected,
    showGate,
    setShowGate,
    gatesWorkspace,
    loadSettings,
    loadPersonas,
    setSessions,
    setProjects,
    setItems,
    setUsage,
    setSessionId,
    setAgent,
    setModel,
    setWorkspace,
    setBranch,
  } = deps;

  const [booting, setBooting] = useState(true);
  const [onboarding, setOnboarding] = useState(false);
  // True once we've resumed a prior conversation on boot (drives the splash wording).
  const [resumedExisting, setResumedExisting] = useState(false);
  // Latched: keep the boot splash up until the restored session is actually CONNECTED (not just
  // until `booting` clears), so an early click can't land on a session that's still settling.
  const [uiReady, setUiReady] = useState(false);

  // On boot with no seeded workspace, reopen the last thing the user had — most recent
  // conversation (restores its folder + agent + transcript), else the most recent project
  // folder. Only a true first run (nothing to resume) falls through to the folder gate.
  const resumeLastOrGate = async () => {
    let loadedSessions: SessionInfo[] = [];
    try {
      loadedSessions = (await getSessions()).filter((s) => s.session_id && !s.session_id.startsWith("__"));
      setSessions(loadedSessions);
      const sess = loadedSessions;
      const ts = (s: SessionInfo) => Date.parse(s.updated_at || "") || Number(s.updated_at) || 0;
      const last = [...sess].sort((a, b) => ts(b) - ts(a))[0];
      if (last) {
        setResumedExisting(true);
        if (last.agent) setAgent(last.agent);
        if (last.workspace) {
          setWorkspace(last.workspace);
          setBranch(null);
        }
        try {
          const messages = await getSessionMessages(last.session_id);
          setItems(itemsFromMessages(messages));
          setUsage(usageFromMessages(messages));
        } catch {
          setItems([]);
          setUsage(emptyUsage());
        }
        setSessionId(last.session_id);
        setShowGate(false);
        return;
      }
    } catch {
      /* fall through */
    }
    try {
      const recents = await getRecentWorkspaces();
      setProjects(recents);
      // Only auto-adopt a recent folder for gated surfaces (Code). Cowork starts orphan.
      if (gatesWorkspace(agent)) {
        const ws = recents.find((w) => w.exists) || recents[0];
        if (ws) {
          setWorkspace(ws.path);
          setShowGate(false);
          return;
        }
      }
    } catch {
      /* fall through */
    }
    setShowGate(gatesWorkspace(agent)); // only Code forces a first-run folder gate
  };

  // initial: adopt the server's seed workspace if any, else force the gate.
  // Retry health for a while: the desktop shell starts its sidecar in parallel, so the
  // server may not answer for a second or two. Only fall back to the gate once it's truly up.
  useEffect(() => {
    let cancelled = false;
    const attempt = (tries: number) => {
      getHealth()
        .then(async (h) => {
          if (cancelled) return;
          setModel(h.model);
          // First-run setup wizard (desktop): show until the user completes/dismisses it.
          if (isTauri()) {
            getSettings()
              .then((s) => !cancelled && !s.onboarded && setOnboarding(true))
              .catch(() => {});
          }
          // Settle the active session BEFORE clearing `booting` (which unblocks the connection
          // effect). resumeLastOrGate is async — if we cleared `booting` first, the throwaway
          // initial sessionId would connect against an empty/stale workspace and the server
          // would provision a junk per-conversation scratch dir for it before resume could
          // flip to the real session. Cowork ignores default_workspace (a Code concept).
          if (h.default_workspace && gatesWorkspace(agent)) setWorkspace(h.default_workspace);
          else await resumeLastOrGate();
          // The mount-time loadSettings races the sidecar boot and swallows its failure —
          // on a cold start that left "Loading models…" stuck until the user visited
          // Settings (owner-hit 2026-07-23). Health just answered, so this one lands.
          loadSettings();
          // Same race, same fix: the mount-time persona fetch loses to the sidecar boot in
          // the packaged app, and its only other trigger is PERSONAS_CHANGED — so the
          // composer's coworker picker stayed empty for the whole session while Settings
          // (mounted later) looked fine (owner-hit 2026-08-13).
          loadPersonas();
          if (!cancelled) setBooting(false);
        })
        .catch(() => {
          if (cancelled) return;
          if (tries <= 0) {
            setBooting(false);
            setShowGate(true);
          } else {
            setTimeout(() => attempt(tries - 1), 500);
          }
        });
    };
    attempt(40); // ~20s of 500ms retries
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reveal the UI once boot has settled AND the restored session is connected (or we're showing
  // the folder gate). Latched, so later reconnects never flash the splash again.
  useEffect(() => {
    if (uiReady || booting) return;
    if (connected || showGate) setUiReady(true);
  }, [uiReady, booting, connected, showGate]);
  // Safety net: if the restored session never reports connected (backend slow/unreachable), reveal
  // the UI anyway. Boot already passed the health check, so a live connect is sub-second; this only
  // bites in the failure case, so keep it short.
  useEffect(() => {
    if (uiReady || booting) return;
    const t = setTimeout(() => setUiReady(true), 1500);
    return () => clearTimeout(t);
  }, [uiReady, booting]);

  return { booting, onboarding, setOnboarding, resumedExisting, uiReady, setUiReady };
}