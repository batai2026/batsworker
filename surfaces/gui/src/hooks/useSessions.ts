import { useCallback, useEffect } from "react";
import { PERSONAS_CHANGED } from "../api";
import { useSessionStore } from "../stores/sessionStore";
import type { SessionInfo } from "../types";

const LAST_SESSION_KEY = "coworker:last-session-by-agent:v1";

export type LastSession = { sessionId: string; workspace: string; updatedAt: number };

function sessionTs(s: SessionInfo): number {
  return Date.parse(s.updated_at || "") || Number(s.updated_at) || 0;
}

export function readLastSessions(): Record<string, LastSession> {
  try {
    const raw = localStorage.getItem(LAST_SESSION_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function rememberLastSession(agent: string, sessionId: string, workspace: string | null) {
  if (!agent || !sessionId) return;
  try {
    const all = readLastSessions();
    all[agent] = { sessionId, workspace: workspace || "", updatedAt: Date.now() };
    localStorage.setItem(LAST_SESSION_KEY, JSON.stringify(all));
  } catch {
    /* localStorage may be unavailable; session restore is best effort. */
  }
}

export function resumeTargetForAgent(agent: string, sessions: SessionInfo[]): LastSession | null {
  const remembered = readLastSessions()[agent];
  if (remembered?.sessionId) {
    const live = sessions.find((s) => s.session_id === remembered.sessionId && s.agent === agent);
    if (live || remembered.workspace) {
      return {
        sessionId: remembered.sessionId,
        workspace: live?.workspace ?? remembered.workspace ?? "",
        updatedAt: live ? sessionTs(live) : remembered.updatedAt,
      };
    }
  }
  const recent = sessions
    .filter((s) => s.agent === agent && s.session_id && !s.session_id.startsWith("__"))
    .sort((a, b) => sessionTs(b) - sessionTs(a))[0];
  return recent ? { sessionId: recent.session_id, workspace: recent.workspace || "", updatedAt: sessionTs(recent) } : null;
}

// Session list + recent projects (the sidebar's grouping data), plus the standing
// refresh loop. The list itself lives in the zustand store (sessionStore) so
// non-component callbacks can read it; this hook keeps the mount/poll/event wiring
// and the same return shape App.tsx already consumes.

export function useSessions() {
  const sessions = useSessionStore((s) => s.sessions);
  const projects = useSessionStore((s) => s.projects);
  const setSessions = useSessionStore((s) => s.setSessions);
  const setProjects = useSessionStore((s) => s.setProjects);
  const refreshSessions = useSessionStore((s) => s.refreshSessions);
  const refresh = useCallback(() => refreshSessions(), [refreshSessions]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll the session list so the attention/liveness badges stay live and sessions created
  // out-of-band (unattended work, messaging, automations) appear without a manual refresh.
  useEffect(() => {
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  // Persona toggles can archive sessions server-side (disable-archives, §18): refetch on the
  // personas-changed event so the sidebar section disappears immediately, not on the next poll.
  useEffect(() => {
    const onPersonas = () => refresh();
    window.addEventListener(PERSONAS_CHANGED, onPersonas);
    return () => window.removeEventListener(PERSONAS_CHANGED, onPersonas);
  }, [refresh]);

  return { sessions, setSessions, projects, setProjects, refreshSessions: refresh };
}