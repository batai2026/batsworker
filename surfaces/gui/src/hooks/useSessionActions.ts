import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import {
  deleteSession,
  getRecentWorkspaces,
  getSessionMessages,
  getSessions,
  renameSession,
  runAutomation,
  setSessionFlags,
  type RecentWorkspace,
} from "../api";
import type { Attachment, Item, SessionInfo, SessionUsage, TodoItem } from "../types";
import type { AppSurface } from "./useAppNavigation";
import { itemsFromMessages } from "../itemsFromMessages";
import { emptyUsage, usageFromMessages } from "../usage";
import { rememberLastSession, resumeTargetForAgent } from "./useSessions";

// Session lifecycle actions extracted from App.tsx: opening/resuming sessions,
// switching coworkers, picking a workspace / starting a project, renaming/pinning/
// archiving/deleting conversations, and starting an automation run live in the
// main view. Each action is a small orchestration of state setters + API calls —
// the same behavior as before, now unit-testable in isolation.

export interface PendingPrompt {
  text: string;
  attachments?: Attachment[];
  skill?: string;
  model?: string;
  notice?: string; // e.g. "Temporary folder created · git initialized", shown after the message
}

export interface ActiveRunRef {
  taskId: string;
  runId: string;
  sessionId: string;
}

export interface SessionActionsDeps {
  agent: string;
  sessionId: string;
  workspace: string | null;
  tempWorkspace: boolean;
  sessions: SessionInfo[];
  projects: RecentWorkspace[];
  setSurface: Dispatch<SetStateAction<AppSurface>>;
  setTodo: Dispatch<SetStateAction<TodoItem[]>>;
  setStreaming: Dispatch<SetStateAction<string>>;
  setRunning: Dispatch<SetStateAction<boolean>>;
  setAgent: Dispatch<SetStateAction<string>>;
  setReviewerPaused: Dispatch<SetStateAction<boolean>>;
  setDraftFolderPicked: Dispatch<SetStateAction<boolean>>;
  setTempWorkspace: Dispatch<SetStateAction<boolean>>;
  setShowGate: Dispatch<SetStateAction<boolean>>;
  setWorkspace: Dispatch<SetStateAction<string | null>>;
  setBranch: Dispatch<SetStateAction<string | null>>;
  setSessionId: Dispatch<SetStateAction<string>>;
  setItems: Dispatch<SetStateAction<Item[]>>;
  setUsage: Dispatch<SetStateAction<SessionUsage>>;
  setProjects: Dispatch<SetStateAction<RecentWorkspace[]>>;
  setGateCreate: Dispatch<SetStateAction<boolean>>;
  setRunContext: (ctx: { id: string; title: string } | null) => void;
  activeRunRef: MutableRefObject<ActiveRunRef | null>;
  pendingPromptRef: MutableRefObject<PendingPrompt | null>;
  refreshSessions: () => void;
  gatesWorkspace: (a: string) => boolean;
  newId: () => string;
  fallbackWorkspace: (current: string | null, projects: RecentWorkspace[]) => string;
}

export function useSessionActions(deps: SessionActionsDeps) {
  const {
    agent,
    sessionId,
    workspace,
    tempWorkspace,
    sessions,
    projects,
    setSurface,
    setTodo,
    setStreaming,
    setRunning,
    setAgent,
    setReviewerPaused,
    setDraftFolderPicked,
    setTempWorkspace,
    setShowGate,
    setWorkspace,
    setBranch,
    setSessionId,
    setItems,
    setUsage,
    setProjects,
    setGateCreate,
    setRunContext,
    activeRunRef,
    pendingPromptRef,
    refreshSessions,
    gatesWorkspace,
    newId,
    fallbackWorkspace,
  } = deps;

  const selectSession = async (id: string, ws: string, ag: string) => {
    setSurface("session"); // selecting a conversation always returns to the conversation view
    setTodo([]);
    setStreaming("");
    setRunning(false);
    if (ag) setAgent(ag);
    setReviewerPaused(false);
    setDraftFolderPicked(false); // a resumed session's folder is inherited, not a pick
    setTempWorkspace(false); // the `ready` event restores the truth for temp sessions
    if (!gatesWorkspace(ag)) setShowGate(false);
    if (ws && ws !== workspace) {
      setWorkspace(ws); // switch project to the session's folder
      setBranch(null);
    }
    setSessionId(id);
    try {
      const messages = await getSessionMessages(id);
      setItems(itemsFromMessages(messages));
      setUsage(usageFromMessages(messages));
    } catch {
      setItems([]);
      setUsage(emptyUsage());
    }
  };

  const switchAgent = async (name: string) => {
    setSurface("session");
    if (name === agent) return;
    setDraftFolderPicked(false); // leaving the draft — any pick belonged to it
    rememberLastSession(agent, sessionId, workspace);
    const knownSessions = sessions.length ? sessions : await getSessions().catch(() => []);
    const knownProjects = projects.length ? projects : await getRecentWorkspaces().catch(() => []);
    const target = resumeTargetForAgent(name, knownSessions);

    setAgent(name);
    setItems([]);
    setUsage(emptyUsage());
    setStreaming("");
    setTodo([]);
    setRunning(false);

    // The live workspace is only a valid fallback for a gated persona if it came from
    // another gated persona — a knowledge persona's workspace is a scratch dir, and a
    // code-family session must never adopt one. Same for a code session's TEMPORARY dir:
    // per-conversation, never inherited. (`agent` is still the previous persona here.)
    const inheritable = gatesWorkspace(agent) && !tempWorkspace ? workspace : null;

    if (target) {
      // Code falls back to a recent folder; Cowork resumes its scratch (target.workspace) or
      // starts orphan ("" → server provisions). Chat has no workspace.
      const targetWorkspace = gatesWorkspace(name)
        ? target.workspace || fallbackWorkspace(inheritable, knownProjects)
        : target.workspace || "";
      if (targetWorkspace && targetWorkspace !== workspace) {
        setWorkspace(targetWorkspace);
        setBranch(null);
      } else if (!targetWorkspace) {
        setWorkspace(null); // orphan cowork: clear so the next `ready` adopts a fresh scratch
      }
      if (!gatesWorkspace(name)) setShowGate(false);
      else if (targetWorkspace) setShowGate(false);
      else setShowGate(true);
      setSessionId(target.sessionId);
      try {
        const messages = await getSessionMessages(target.sessionId);
        setItems(itemsFromMessages(messages));
        setUsage(usageFromMessages(messages));
      } catch {
        setItems([]);
        setUsage(emptyUsage());
      }
      return;
    }

    const id = newId();
    const fallback = gatesWorkspace(name) ? fallbackWorkspace(inheritable, knownProjects) : "";
    if (fallback && fallback !== workspace) {
      setWorkspace(fallback);
      setBranch(null);
    } else if (!fallback) {
      setWorkspace(null); // orphan cowork: server provisions a fresh scratch on connect
    }
    setSessionId(id);
    rememberLastSession(name, id, fallback);
    if (!gatesWorkspace(name)) setShowGate(false);
    else setShowGate(!fallback);
  };

  const chooseWorkspace = (path: string, b?: string | null) => {
    setWorkspace(path);
    setBranch(b ?? null);
    setShowGate(false);
    setGateCreate(false);
    setItems([]);
    setUsage(emptyUsage());
    setStreaming("");
    setTodo([]);
    setSessionId(newId());
    getRecentWorkspaces().then(setProjects).catch(() => {});
  };

  // "New project" lives under a project-scoped persona's accordion. Switch to that persona, start a
  // fresh session with no folder yet, and open the gate in create mode — so the gate's
  // surface==="session" && gatesWorkspace(agent) guard passes even if the active session was Chat/Cowork.
  const newProject = (forAgent?: string) => {
    const target = forAgent || agent;
    setSurface("session");
    setItems([]);
    setUsage(emptyUsage());
    setStreaming("");
    setTodo([]);
    setRunning(false);
    if (target !== agent) setAgent(target);
    setWorkspace(null);
    setBranch(null);
    setSessionId(newId());
    setGateCreate(true);
    setShowGate(true);
  };

  const renameConversation = async (id: string, title: string) => {
    const res = await renameSession(id, title);
    if (res.ok) refreshSessions();
  };

  const togglePinned = async (id: string, pinned: boolean) => {
    await setSessionFlags(id, { pinned });
    refreshSessions();
  };

  const toggleArchived = async (id: string, archived: boolean) => {
    await setSessionFlags(id, { archived });
    refreshSessions();
    // Archiving the open chat: leave it and start fresh (it moves to the Archived section).
    if (archived && id === sessionId) {
      setItems([]);
      setUsage(emptyUsage());
      setStreaming("");
      setTodo([]);
      setRunning(false);
      setSessionId(newId());
    }
  };

  const deleteConversation = async (id: string) => {
    const res = await deleteSession(id);
    if (!res.ok) return;
    refreshSessions();
    if (id === sessionId) {
      setItems([]);
      setUsage(emptyUsage());
      setStreaming("");
      setTodo([]);
      setRunning(false);
      setSessionId(newId());
    }
  };

  // "Run now": prepare a manual run, open its session, and auto-send the task so the agent
  // runs LIVE in the main view; finalize it in history once the first turn finishes.
  const openRunSession = (
    sessionId: string,
    ws: string,
    ag: string,
    task?: { id: string; title: string },
  ) => {
    setRunContext(task ?? null);
    setSurface("session");
    setShowGate(false);
    selectSession(sessionId, ws, ag);
  };

  const runTaskNow = async (taskId: string, title?: string) => {
    const r = await runAutomation(taskId);
    if (!r || !r.ok) return;
    pendingPromptRef.current = { text: r.prompt };
    activeRunRef.current = { taskId, runId: r.run_id, sessionId: r.session_id };
    openRunSession(r.session_id, r.workspace, r.agent, { id: taskId, title: title || "" });
  };

  return {
    selectSession,
    switchAgent,
    chooseWorkspace,
    newProject,
    renameConversation,
    togglePinned,
    toggleArchived,
    deleteConversation,
    openRunSession,
    runTaskNow,
  };
}