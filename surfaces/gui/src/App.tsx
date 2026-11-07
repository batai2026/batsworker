import { useEffect, useRef, useState, type PointerEvent } from "react";
import {
  boardComment,
  fetchBoardAttachment,
  getBoardItem,
  getHealth,
  getRecentWorkspaces,
  announceMemoryChanged,
  deleteMemory,
  updateMemory,
  getSettings,
  saveSessionAsProject,
  type Session,
  type RecentWorkspace,
  type SurfaceVisibility,
  type WorkspaceCommandTrust,
} from "./api";
import type { Attachment, Item } from "./types";
import { fullPersonaName, isProjectScoped } from "./personaScope";
import { baseName } from "./paths";
import { emptyUsage } from "./usage";
import { streamMode } from "./streamGate";
import { InboxItemCard } from "./components/InboxItemCard";
import { chooseFolder, isTauri, platformOS, startWindowDrag } from "./tauri";
import { Icon } from "./components/Icon";
import { Sidebar } from "./components/Sidebar";
import { ThinkingBlock, Transcript } from "./components/Transcript";
import { Composer } from "./components/Composer";
import { Markdown } from "./components/Markdown";
import { SearchModal } from "./components/SearchModal";
import { SessionIntro } from "./components/SessionIntro";
import { FolderGate } from "./components/FolderGate";
import { SessionSetupRow } from "./components/SessionSetupRow";
import { SendFolderDialog } from "./components/SendFolderDialog";
import { Onboarding } from "./components/Onboarding";
import { UpdateBanner } from "./components/UpdateBanner";
import { ScheduledView } from "./components/ScheduledView";
import { RightRail } from "./components/RightRail";
import { IntegrationsView } from "./components/IntegrationsView";
import { SettingsView } from "./components/SettingsView";
import { PersonaView } from "./components/PersonaView";
import { AuditView } from "./components/AuditView";
import { InboxView } from "./components/InboxView";
import { ApprovalCard } from "./components/ApprovalCard";
import { ToolRequestCard } from "./components/ToolRequestCard";
import { DirectoryRequestCard } from "./components/DirectoryRequestCard";
import { PlanCard } from "./components/PlanCard";
import { BoardOverlay } from "./components/BoardPanel";
import { TeamRequestCard } from "./components/TeamRequestCard";
import { WorkItemsCard } from "./components/WorkItemsCard";
import { TeamChatView } from "./components/TeamChatView";
import { WorkspaceTrustPrompt } from "./components/WorkspaceTrustPrompt";
import { useI18n, type MessageKey } from "./i18n";
import { useAppNavigation } from "./hooks/useAppNavigation";
import { useRail } from "./hooks/useRail";
import { usePersonas } from "./hooks/usePersonas";
import { useSessions, rememberLastSession } from "./hooks/useSessions";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";
import { useArtifactCount } from "./hooks/useArtifactCount";
import { useBoard } from "./hooks/useBoard";
import { useSessionInbox } from "./hooks/useSessionInbox";
import { useComposerPrefill } from "./hooks/useComposerPrefill";
import { usePendingGates } from "./hooks/usePendingGates";
import { useGateResponders } from "./hooks/useGateResponders";
import { useBoot } from "./hooks/useBoot";
import { useSessionActions } from "./hooks/useSessionActions";
import { useAutomationToast } from "./hooks/useAutomationToast";
import { useSessionConnect } from "./hooks/useSessionConnect";
import { useSessionStream } from "./hooks/useSessionStream";
import { useSendGate } from "./hooks/useSendGate";
import { newSessionId as newId, useActiveSessionStore } from "./stores/activeSessionStore";
import { useRunStore } from "./stores/runStore";

const SUGGESTIONS = [
  { ico: "⚙", text: "Run the test suite and summarize any failures." },
  { ico: "✦", text: "Read the project and give me a 5-bullet overview." },
  { ico: "↻", text: "Find and fix the failing build." },
];

// Tools whose success means a new/changed file should show up under Artifacts right away.
// Fallback used only before the persona list loads (the in-component gatesWorkspace
// consults the real persona's requires_folder once available).
const gatesWorkspaceFallback = (a: string) => a === "code";

function fallbackWorkspace(current: string | null, projects: RecentWorkspace[]): string {
  if (current) return current;
  const existing = projects.find((p) => p.exists);
  return existing?.path || projects[0]?.path || "";
}

export function App() {
  const { t } = useI18n();
  // Active session identity + workspace + persona/model. Shared by the boot, connect,
  // send-gate and session-action hooks, and readable outside React via
  // useActiveSessionStore.getState() (docs/design/gui-state-zustand.md).
  const {
    sessionId,
    workspace,
    branch,
    agent,
    model,
    setSessionId,
    setWorkspace,
    setBranch,
    setAgent,
    setModel,
  } = useActiveSessionStore();
  // UX-029: the active session runs in a temporary folder (never show its raw path —
  // the header says "Temporary folder" and offers Save as project…). Set locally when a
  // temp dir is created at send, corrected by every `ready` event (server truth).
  const [tempWorkspace, setTempWorkspace] = useState(false);
  // The draft folder came from the user's own chip pick (not boot-resume or scratch
  // adoption). Only such a pick survives a coworker change (owner catch 2026-08-24).
  const [draftFolderPicked, setDraftFolderPicked] = useState(false);
  // §8.4 breaker tripped this turn — the mode chip shows "· paused" until the turn ends
  // or an ask_user answer resets the reviewer's denial streak (engine semantics).
  const [reviewerPaused, setReviewerPaused] = useState(false);
  // UX-029 send-time folder enforcement — owned by the useSendGate hook (the stashed
  // message while the folder dialog is up; the dialog's resolutions live there too).
  // Bumped to force a socket rebuild on the SAME session id (Save as project… moves the
  // folder server-side; the engine rebinds on reconnect).
  const [connectNonce, setConnectNonce] = useState(0);
  const [showGate, setShowGate] = useState(false);
  const [workspaceTrustRequest, setWorkspaceTrustRequest] =
    useState<WorkspaceCommandTrust | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [modelLabels, setModelLabels] = useState<Record<string, string>>({});
  // {full model id → context window in tokens} from the curated matrix (verified only);
  // drives the composer usage chip's context-fill meter.
  const [modelContextWindows, setModelContextWindows] = useState<Record<string, number>>({});
  // Settings: show the composer's context-window fill bar. OFF by default (owner ask),
  // so an older backend without the field also shows the session total.
  const [contextBar, setContextBar] = useState(false);
  const [surfaces, setSurfaces] = useState<SurfaceVisibility>({ cowork: true, chat: false, code: false });
  const [mode, setMode] = useState("interactive");
  // Which session the current `mode` value is CONFIRMED for. On a session switch, `mode`
  // still holds the previous session's value until the server's `ready` event delivers the
  // real one — announcing anything in that window posts the old session's banner into the
  // new transcript (seen 2026-08-22: a fresh Ask-for-approval session opened with the
  // Auto-approve banner, then a stray "Ask for approval is on." marker when `ready` landed).
  const [connected, setConnected] = useState(false);
  // Per-session token usage (OPE-42), the transcript, streaming text and turn flags all live
  // in runStore now. The streaming/reasoning ref mirrors stay out of the store and live in
  // useSessionStream (hot path, must not re-render on every token).
  const {
    running,
    compacting,
    streaming,
    reasoningStream,
    items,
    todo,
    usage,
    setRunning,
    setCompacting,
    setItems,
    setTodo,
    setUsage,
  } = useRunStore();
  const { streamingRef, reasoningRef, setStreaming, setReasoningStream } = useSessionStream();
  // Automation-run context (§ owner ask 2026-07-04): which task an open __run__ session belongs
  // to, driving the banner + "Back to runs". Best-effort — a run session without context still
  // shows a generic banner (detected by its __run__ id).
  const [runContext, setRunContext] = useState<{ id: string; title: string } | null>(null);
  const [gateCreate, setGateCreate] = useState(false);
  // Whether the default model's provider is actually configured (any provider). Drives the
  // composer's "No model connected" chip. Default true so we don't flash the chip before settings
  // load; corrected by loadSettings.
  const [modelReady, setModelReady] = useState(true);
  const {
    scheduledOpenId,
    setScheduledOpenId,
    surface,
    setSurface,
    settingsTab,
    openSettings,
    personaViewId,
    personaViewReturn,
    openPersona,
  } = useAppNavigation();
  const [browserRefreshKey, setBrowserRefreshKey] = useState(0);
  // Agent teams (OPE-96): board for the current session's workspace space.
  const {
    board,
    boardOpen,
    setBoardOpen,
    boardDetailId,
    setBoardDetailId,
    chatTeam,
    setChatTeam,
    refreshBoard,
    moveBoardItem,
  } = useBoard(agent, surface, sessionId, browserRefreshKey, running);
  const {
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
  } = useRail();
  useKeyboardShortcuts({
    onToggleNav: toggleNav,
    onOpenSettings: () => setSurface("settings"),
  });
  // Count of files this Cowork conversation has produced — surfaces an "Artifacts (N)" button in
  // the topbar when the side panel is hidden, so produced files are never buried.
  // The command-palette search, openable from the collapsed-sidebar topbar cluster (§22). The
  // expanded sidebar owns its own instance; this one exists so search never disappears with it.
  const [searchOpen, setSearchOpen] = useState(false);
  // A pending composer prefill (text + attachments) pushed from the session start panel —
  // owned by the useComposerPrefill hook.
  const { composerPrefill, prefillComposer } = useComposerPrefill();

  // Persona metadata drives workspace behavior by FAMILY, not by hardcoded id (so a DevOps/SecOps
  // code-family persona gates a folder like Code, and a knowledge persona starts orphan like Cowork).
  const { personas, loadPersonas, personaOf } = usePersonas();

  // MUST pick a folder before starting — requires_folder personas (git-bound Code, the
  // security coworkers). Everything else starts orphan: the server auto-provisions a
  // per-conversation scratch dir and reports it in the `ready` event.
  const gatesWorkspace = (a: string) => {
    const p = personaOf(a);
    return p ? isProjectScoped(p) : gatesWorkspaceFallback(a);
  };

  // The desktop tray's "Settings" item dispatches this on the window.
  useEffect(() => {
    const open = () => openSettings("appearance");
    window.addEventListener("coworker:open-settings", open);
    return () => window.removeEventListener("coworker:open-settings", open);
  }, []);

  // "Run setup again" (from Settings) re-opens the wizard.
  useEffect(() => {
    const open = () => {
      setOnboarding(true);
    };
    window.addEventListener("coworker:open-onboarding", open);
    return () => window.removeEventListener("coworker:open-onboarding", open);
  }, []);

  const sessionRef = useRef<Session | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // A message to auto-send once the next session connects — "Run now" task prompts, and
  // UX-029's deferred first send (folder resolved at send time → reconnect → message goes).
  const pendingPromptRef = useRef<{
    text: string;
    attachments?: Attachment[];
    skill?: string;
    model?: string;
    notice?: string; // e.g. "Temporary folder created · git initialized", shown after the message
  } | null>(null);
  // The in-flight manual run to finalize after its first turn ({taskId, runId, sessionId}).
  const activeRunRef = useRef<{ taskId: string; runId: string; sessionId: string } | null>(null);

  // Session list + recent projects (sidebar grouping data), kept fresh by the hook.
  const { sessions, setSessions, projects, setProjects, refreshSessions } = useSessions();

  // Pending Inbox items for the ACTIVE session + Unattended mode (the WS handler reads
  // `unattendedRef` directly; the composer/JSX read the state).
  const {
    sessionInbox,
    unattended,
    unattendedRef,
    toggleUnattended,
    resolveSessionInbox,
    dropSessionInbox,
  } = useSessionInbox(sessionId, surface, browserRefreshKey, refreshSessions);

  // initial: adopt the server's seed workspace if any, else force the gate.
  // Retry health + resume last session — owned by the useBoot hook (booting /
  // onboarding / resumedExisting / uiReady).

  const loadSettings = () =>
    getSettings()
      .then((s) => {
        setModels(s.models || []);
        setModelLabels(s.model_labels || {});
        setModelContextWindows(s.model_context_windows || {});
        setContextBar(s.context_bar === true);
        setModelReady(s.model_ready);
        if (s.surfaces) setSurfaces(s.surfaces);
      })
      .catch(() => {});

  // Boot: sidecar health + resume last session + onboarding gate (owns booting /
  // onboarding / resumedExisting / uiReady and the splash-reveal effects).
  const { booting, onboarding, setOnboarding, resumedExisting, uiReady } = useBoot({
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
  });

  // Open Settings → Configure Models (from the composer's "No model connected" chip).
  const openModelSetup = () => openSettings("models");

  // Leaving the Settings page: pick up any model/surface changes for the composer (the modal used to
  // do this on close).
  useEffect(() => {
    if (surface !== "settings") loadSettings();
  }, [surface]);

  useEffect(() => {
    loadSettings(); // selectable models + which session surfaces are visible
  }, [refreshSessions]);

  // If the active persona is DISABLED (turned off in Settings, or a resumed session landed
  // on one), fall back to Cowork. This used to key on the legacy sidebar-visibility prefs
  // (show_chat/show_code) — with the composer picker shipped (UX-029), enablement is the
  // one visibility axis, and a deliberately picked coworker must never be reverted.
  useEffect(() => {
    const p = personaOf(agent);
    if (p && !p.enabled) switchAgent("cowork");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent, personas]);

  useEffect(() => {
    if (surface === "session") rememberLastSession(agent, sessionId, workspace);
  }, [surface, agent, sessionId, workspace]);

  // (re)connect when workspace, session, or agent changes — owned by the
  // useSessionConnect hook (Session lifecycle + the WS event switch).
  useSessionConnect({
    booting,
    agent,
    sessionId,
    workspace,
    connectNonce,
    t,
    streamingRef,
    reasoningRef,
    sessionRef,
    unattendedRef,
    activeRunRef,
    pendingPromptRef,
    setStreaming,
    setReasoningStream,
    setItems,
    setCompacting,
    setConnected,
    setModel,
    setMode,
    setWorkspaceTrustRequest,
    setWorkspace,
    setTempWorkspace,
    setRunning,
    setReviewerPaused,
    setTodo,
    setUsage,
    setBrowserRefreshKey,
    refreshSessions,
    gatesWorkspace,
    newId,
  });

  // Stream-following (FB-004): auto-scroll only while the user is AT the bottom, so scrolling
  // up to read during a streaming turn sticks. `atBottomRef` is the live truth (per scroll
  // event, no re-render); `following` mirrors it into state for the jump-to-latest pill.
  // Programmatic smooth-scrolls fire scroll events of their own — while one is in flight
  // (`autoScrollingRef`) they must not read as "the user scrolled up", or every stream tick
  // would disengage its OWN follow. The animation only moves down, so a decreasing scrollTop
  // mid-flight can only be the user taking over.
  const atBottomRef = useRef(true);
  const autoScrollingRef = useRef(false);
  const lastScrollTopRef = useRef(0);
  const [following, setFollowing] = useState(true);
  const scrollToBottom = () => {
    const el = scrollRef.current;
    if (!el) return;
    autoScrollingRef.current = true;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };
  const followLatest = () => {
    atBottomRef.current = true;
    setFollowing(true);
    scrollToBottom();
  };
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const top = el.scrollTop;
    const atBottom = el.scrollHeight - top - el.clientHeight < 48;
    if (autoScrollingRef.current) {
      if (atBottom) autoScrollingRef.current = false; // landed
      else if (top >= lastScrollTopRef.current) {
        lastScrollTopRef.current = top; // still animating down — not the user
        return;
      } else autoScrollingRef.current = false; // moved UP mid-flight — user takeover
    }
    lastScrollTopRef.current = top;
    atBottomRef.current = atBottom;
    setFollowing(atBottom);
  };
  // A different session is a fresh viewport — never inherit a scrolled-up state. Declared
  // BEFORE the auto-scroll effect: when a session switch and its hydrated items land in one
  // commit, the reset must run first or the stale ref would skip the initial bottom-scroll.
  useEffect(() => {
    atBottomRef.current = true;
    setFollowing(true);
  }, [sessionId]);

  useEffect(() => {
    if (atBottomRef.current) scrollToBottom();
  }, [items, streaming]);

  // Track produced-file count for the topbar "Artifacts" affordance (works even when the rail is
  // hidden, where the rail itself doesn't fetch). Cowork only; refreshes on file writes/turn end.
  const artifactCount = useArtifactCount(agent, surface, sessionId, browserRefreshKey);

  // Seventeenth pass: the drawer's Team panel — this session's staff (workers whose
  // lead is the current session). The sidebar shows ONE entry per team; members live here.
  const curSession = sessions.find((s) => s.session_id === sessionId);
  const teamMembers = sessions.filter(
    (s) => s.team?.role === "worker" && s.team.lead_session === sessionId,
  );

  // Keep the active session's pending Inbox items fresh (answer-in-context card) — handled by
  // the useSessionInbox hook (4s poll). The composer send path follows.

  const send = (text: string, attachments?: Attachment[], skill?: string) => {
    // UX-029: folder enforcement AT SEND. A code-family session with no folder has no
    // socket yet (the connect effect waits) — stash the message and ask where to work;
    // it goes out the moment the dialog resolves.
    if (gatesWorkspace(agent) && !workspace) {
      setSendGate({ text, attachments, skill });
      return;
    }
    // A typed message while a proposal gate is pending IS the answer: it resolves
    // the gate as decline-with-feedback, so "use gpt-5.6-sol for all workers"
    // reaches the lead instead of bouncing off a blocked composer (owner-hit
    // 2026-08-16). The card buttons stay the approve/plain-decline paths.
    if (!unattended && pendingTeam?.kind === "teamreq" && !pendingTeam.resolved) {
      setItems((p) => [...p, { kind: "user", text, ts: Date.now() / 1000 }]);
      respondTeam(false, text);
      return;
    }
    if (
      !unattended &&
      pendingItemsReq?.kind === "itemsreq" &&
      !pendingItemsReq.resolved
    ) {
      setItems((p) => [...p, { kind: "user", text, ts: Date.now() / 1000 }]);
      respondItemsReq(false, text);
      return;
    }
    // Force-run shows exactly what the user typed: "/name rest". Must match the server's
    // `display` sidecar formula so the turn_start dedupe recognizes the local echo.
    const shown = skill ? `/${skill}${text ? ` ${text}` : ""}` : text;
    setItems((p) => [...p, { kind: "user", text: shown, attachments, ts: Date.now() / 1000 }]);
    // The visible model rides along with the message (single source of truth per turn).
    sessionRef.current?.userMessage(text, attachments, model, skill);
    followLatest(); // sending always re-engages stream-following, wherever the user had scrolled
  };
  // Resolving a LIVE prompt also resolves its parked Inbox mirror server-side, but the polled
  // Resolving a LIVE prompt also resolves its parked Inbox mirror server-side — the
  // `dropSessionInbox` mirror-drop lives in the useSessionInbox hook.
  // §8.4 "Allow anyway" on a reviewer-denied tool: register the one-shot exact-action
  // approval, then send a visible user message so the agent retries. The engine runs the
  // identical re-proposal without the reviewer or a card; anything different still asks.
  const allowAnyway = (name: string, args: any) => {
    sessionRef.current?.allowAnyway(name, args);
    send(`I reviewed the blocked ${name} action — go ahead with it exactly as proposed.`);
  };
  // Live gate answering (approve / plan / team / items / directory / tool / question) —
  // owned by the useGateResponders hook.
  const { approve, respondPlan, respondTeam, respondItemsReq, respondDirectory, respondTool, answerQuestion } =
    useGateResponders({
      sessionRef,
      setItems,
      dropSessionInbox,
      setMode,
      refreshBoard,
      setReviewerPaused,
    });
  const interrupt = () => sessionRef.current?.interrupt();
  const retry = () => {
    // Optimistic running: turn_start confirms; a rejected retry still ends in turn_done.
    setRunning(true);
    sessionRef.current?.retry();
  };
  const changeMode = (m: string) => {
    setMode(m);
    sessionRef.current?.setMode(m);
  };
  const changeModel = (m: string) => {
    if (running) return; // the server refuses mid-turn rebinds — don't let the header lie
    setModel(m);
    sessionRef.current?.setModel(m);
  };

  const startNewSession = (forAgent?: string) => {
    const target = forAgent || agent;
    setSurface("session"); // return to the conversation view if we were on a sub-view
    setItems([]);
    setUsage(emptyUsage());
    setStreaming("");
    setTodo([]);
    setRunning(false);
    // "New session" under a browsed persona switches to it (expand≠switch: the header alone
    // doesn't switch; this explicit action does).
    if (target !== agent) {
      setAgent(target);
      if (gatesWorkspace(target)) {
        // Never inherit the previous persona's folder — it may be a scratch dir. Clearing
        // it also blocks the connection effect; the setup row's folder chip (or the
        // send-time dialog) provides the folder — no modal gate up front (UX-029).
        setWorkspace(null);
        setBranch(null);
      }
      setShowGate(false);
    }
    // Knowledge family: a new conversation starts fresh (orphan) — clear the workspace so the
    // server provisions a NEW scratch dir for the new session id. Code keeps its repo — but
    // never a TEMPORARY dir (per-conversation by definition; the next session picks anew).
    if (!gatesWorkspace(target) || tempWorkspace) {
      setWorkspace(null);
      setBranch(null);
    }
    setDraftFolderPicked(false);
    setTempWorkspace(false);
    setSessionId(newId());
  };
  // UX-029: re-target the DRAFT session (no messages yet) to another coworker. Unlike
  // switchAgent this never resumes that coworker's last conversation — the user is
  // composing a new one. A fresh id keeps knowledge families' per-conversation scratch
  // dirs clean and re-triggers the connection effect.
  const pickCoworker = (id: string) => {
    if (id === agent) return;
    setAgent(id);
    // An explicit draft folder pick survives a coworker change (owner catch
    // 2026-08-24). Anything inherited — boot-resume, scratch adoption, temp
    // dirs — still resets; the "never inherit" rule exists for those.
    if (!gatesWorkspace(id) || tempWorkspace || !draftFolderPicked || !workspace) {
      setWorkspace(null);
      setBranch(null);
    }
    setTempWorkspace(false);
    setShowGate(false);
    setSessionId(newId());
  };
  // UX-029: the setup row's folder chip — bind the draft to a folder before the first
  // message. A fresh id re-triggers the connection effect with the folder attached.
  const pickDraftFolder = (path: string, b?: string | null) => {
    setWorkspace(path);
    setBranch(b ?? null);
    setDraftFolderPicked(true);
    setTempWorkspace(false);
    setSessionId(newId());
    getRecentWorkspaces().then(setProjects).catch(() => {});
  };
  // UX-029 send-time folder enforcement — owned by the useSendGate hook (stash, bind
  // folder / temp workspace / cancel-and-restore-draft).
  const { sendGate, setSendGate, resolveSendFolder, startTempAndSend, cancelSendGate } = useSendGate({
    model,
    setWorkspace,
    setBranch,
    setTempWorkspace,
    setSessionId,
    setItems,
    setProjects,
    pendingPromptRef,
    newId,
    prefillComposer,
    t,
  });
  // UX-029 "Save as project…": move the temporary folder somewhere real, then reconnect
  // so the engine rebinds to the new path (same session id — the transcript stays).
  const saveAsProject = async () => {
    if (running) return;
    const dest = await chooseFolder();
    if (!dest) return;
    const res = await saveSessionAsProject(sessionId, dest);
    if (!res.ok || !res.path) {
      setItems((p) => [
        ...p,
        { kind: "notice", tone: "warn", text: res.error || t("Could not save as a project.") },
      ]);
      return;
    }
    const newPath = res.path;
    setWorkspace(newPath);
    setBranch(null);
    setTempWorkspace(false);
    setItems((p) => [
      ...p,
      { kind: "notice", tone: "info", text: t("Saved as a project — now working in {name}.", { name: baseName(newPath) }) },
    ]);
    setConnectNonce((n) => n + 1);
    refreshSessions();
  };
  // Inbox → session: the item carries its session's workspace/agent, so open it directly.
  // UX-026: 5s top-right toast when a SCHEDULED automation run starts — owned by the
  // useAutomationToast hook (rides /ws/events; View run opens the live session).
  const { runToast, setRunToast } = useAutomationToast(t);

  // MEMORY-SPEC §5.1: undo a write the transcript just announced. A new memory is
  // deleted; an EDIT is rolled back to its previous text (deleting there would throw
  // away whatever the memory already held). The notice confirms in place either way.
  const undoMemorySave = async (id: number, previous?: string) => {
    if (previous) await updateMemory(id, previous).catch(() => {});
    else await deleteMemory(id).catch(() => {});
    announceMemoryChanged();
    setItems((p) =>
      p.map((it) => (it.kind === "memory" && it.id === id ? { ...it, undone: true } : it)),
    );
  };

  // Session lifecycle (open/switch/workspace/project/rename/pin/archive/delete/run) —
  // owned by the useSessionActions hook.
  const {
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
  } = useSessionActions({
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
  });
  // Inbox → session: the item carries its session's workspace/agent, so open it directly.
  const openSessionFromInbox = (sid: string, ws: string, ag: string) => selectSession(sid, ws, ag);

  // `running` too: a mid-turn reconnect may land before any item is rebuilt — a live
  // session must show the transcript (waiting row, Stop), never the intro hero.
  const idle = items.length === 0 && !streaming && !running;
  const { pendingApproval, pendingDirReq, pendingToolReq, pendingPlan, pendingTeam, pendingItemsReq, pendingQuestion } = usePendingGates(items);
  // Facts subtitle (§22): the session's FIXED facts, not controls — model (+ the
  // workspace folder for project-scoped sessions). Renders only once the session has history;
  // until then the model is still choosable in the composer, so there's no locked fact to state.
  const hasHistory = items.length > 0;
  // Curated labels read "Claude Opus 4.8 · Anthropic" — the provider suffix is dropdown context,
  // noise in a facts line. Fall back to the raw id without its provider prefix.
  const modelDisplay =
    modelLabels[model]?.split(" · ")[0] ||
    (model.includes(":") ? model.split(":").slice(1).join(":") : model);
  // UX-029: with the coworker picker shipping, the coworker's name is a fixed fact again
  // (it was dropped 2026-07-22 while personas were hidden). For temporary folders the raw
  // path never shows — "Temporary folder" + the Save as project… affordance instead.
  const subtitleParts = [fullPersonaName(personaOf(agent)?.name, agent), modelDisplay];
  if (isProjectScoped(personaOf(agent)) && workspace)
    subtitleParts.push(tempWorkspace ? t("Temporary folder") : baseName(workspace));
  const showSaveAsProject = hasHistory && tempWorkspace && isProjectScoped(personaOf(agent));
  const activeInfo = sessions.find((s) => s.session_id === sessionId);
  const activeTitle = activeInfo?.title || t("New session");

  const desktop = isTauri();
  // Dev-only: `?overlay=1` simulates the desktop overlay layout in the browser (adds the
  // tauri-overlay class + draws fake traffic lights at the real position) so the top-left can be
  // tuned in the preview without a DMG build. Never active in the real app (isTauri() short-circuits).
  const simOverlay = !desktop && new URLSearchParams(window.location.search).has("overlay");
  // Overlay layout is macOS-ONLY: Windows/Linux keep the native title bar, so the mac
  // compensations (traffic-light insets, lowered top strips) must not apply there —
  // they rendered as misalignments under Windows' native bar (caught 2026-07-21).
  const overlay = (desktop && platformOS() === "macos") || simOverlay;
  const beginWindowDrag = (event: PointerEvent) => {
    if (!desktop || event.button !== 0) return;
    startWindowDrag();
  };

  if (booting || !uiReady) {
    return (
      <div className={"app boot-splash" + (overlay ? " tauri-overlay" : "")}>
        {/* overlay (not desktop): ?overlay=1 previews the splash's top-left in the browser
            too — the wordmark/traffic-light alignment is exactly what it exists to tune. */}
        {overlay && (
          <div className="titlebar-drag" data-tauri-drag-region>
            <span className="titlebar-brand brand-wordmark">
              <Icon name="logo" size={13} className="mark" /> OpenWorker<span className="beta-tag">BETA</span>
            </span>
          </div>
        )}
        {simOverlay && (
          <div className="sim-traffic-lights" aria-hidden="true">
            <span /><span /><span />
          </div>
        )}
        {/* The real OpenWorker mark (6-point star, same as the app/tray icon) — the old
            ✦ text glyph was a 4-point sparkle that read as another product's logo. */}
        <div className="boot-mark">
          <Icon name="logo" size={38} />
        </div>
        <div className="boot-text">
          {resumedExisting ? t("Restoring your session…") : t("Starting OpenWorker…")}
          <span className="beta-tag">BETA</span>
        </div>
      </div>
    );
  }

  return (
    <main
      className={
        "app" +
        (overlay ? " tauri-overlay" : "") +
        (navCollapsed ? " nav-collapsed" : "") +
        (navCollapsed && navPeek ? " nav-peek" : "")
      }
    >
      {/* Dev-only fake traffic lights so ?overlay=1 previews the real desktop top-left. */}
      {simOverlay && (
        <div className="sim-traffic-lights" aria-hidden="true">
          <span /><span /><span />
        </div>
      )}
      {/* Desktop-only auto-update prompt (15s after boot, then every 30 min; inert in browser). */}
      <UpdateBanner />
      {/* UX-026: automation-start toast — quiet panel, neutral dot/drain, accent only
          on the action (rev 2); auto-dismisses with the 5s drain bar. */}
      {runToast && (
        <div
          className="fixed top-3 right-3 z-[45] w-[290px] bg-panel border border-line rounded-xl shadow-lg px-3.5 pt-3 pb-2.5"
          data-testid="automation-toast"
        >
          <div className="flex items-center gap-2 text-[13px] font-semibold">
            <span className="w-[7px] h-[7px] rounded-full bg-faint toast-pulse" />
            {t("Automation started")}
          </div>
          <div className="text-[13px] text-muted mt-0.5 ml-[15px] truncate">
            {runToast.title} · {runToast.time} {t("run")}
          </div>
          <div className="flex items-center justify-between ml-[15px] mt-1.5">
            <button
              className="text-[13px] text-accent font-medium"
              data-testid="toast-view-run"
              onClick={() => {
                selectSession(runToast.sessionId, runToast.workspace, runToast.agent);
                setRunToast(null);
              }}
            >
              {t("View run ›")}
            </button>
            <button
              className="text-[12px] text-faint px-0.5"
              data-testid="toast-dismiss"
              title={t("Dismiss")}
              onClick={() => setRunToast(null)}
            >
              ✕
            </button>
          </div>
          <div className="absolute left-3 right-3 bottom-1 h-[2px] rounded bg-line overflow-hidden">
            <span className="block h-full bg-faint toast-drain" />
          </div>
        </div>
      )}
      {/* When collapsed, a thin left-edge zone peeks the nav back as a floating overlay. */}
      {navCollapsed && (
        <div
          className="nav-hover-zone"
          onMouseEnter={() => setNavPeek(true)}
          aria-hidden="true"
        />
      )}
      {/* Explicit reveal affordance while collapsed (alongside hover-peek + ⌘B) — on every
          surface EXCEPT the session view, whose topbar carries the [sidebar][+][search] cluster
          instead (§22; no duplicate reveal buttons). */}
      {navCollapsed && !navPeek && surface !== "session" && (
        <button
          className="nav-reveal-btn"
          onClick={toggleNav}
          onMouseEnter={() => setNavPeek(true)}
          title={t("Show sidebar (⌘B)")}
          aria-label={t("Show sidebar")}
        >
          <Icon name="sidebar" size={16} />
        </button>
      )}
      {onboarding && (
        <Onboarding
          onDone={(next) => {
            setOnboarding(false);
            getHealth().then((h) => setModel(h.model)).catch(() => {});
            loadSettings(); // pick up a model connected during setup (clears the composer chip)
            if (next === "gallery") {
              // The specialists tip: land on Settings ▸ Personas, where the Gallery link lives.
              openSettings("personas");
            } else if (next === "automations") {
              // "Create your first automation" (§29) lands on the Automations quickstart.
              setSurface("scheduled");
            } else if (next === "work") {
              // "Start working" teaches by landing (§24, §32): a fresh session with the rail's
              // Access section expanded. Bump after the session switch settles.
              startNewSession();
              setTimeout(openAccess, 80);
            }
          }}
        />
      )}
      <Sidebar
        agent={agent}
        workspace={workspace || ""}
        surfaces={surfaces}
        sessions={sessions}
        projects={projects}
        activeSession={sessionId}
        onSwitchAgent={switchAgent}
        onNewSession={startNewSession}
        onSelectSession={selectSession}
        onNewProject={newProject}
        onRenameSession={renameConversation}
        onDeleteSession={deleteConversation}
        onArchiveSession={toggleArchived}
        onTogglePin={togglePinned}
        onManage={() => openSettings("appearance")}
        onOpenPersona={(id) => {
          openPersona(id, "session");
        }}
        onOpenScheduled={() => setSurface("scheduled")}
        onOpenAutomation={(id) => {
          setScheduledOpenId(id);
          setSurface("scheduled");
        }}
        onOpenIntegrations={() => setSurface("integrations")}
        onOpenAudit={() => setSurface("audit")}
        onOpenInbox={() => setSurface("inbox")}
        scheduledActive={surface === "scheduled"}
        integrationsActive={surface === "integrations"}
        auditActive={surface === "audit"}
        inboxActive={surface === "inbox"}
        collapsed={navCollapsed}
        onCollapse={toggleNav}
        onPeekLeave={() => setNavPeek(false)}
      />
      {surface === "scheduled" ? (
        <ScheduledView
          onOpenRun={openRunSession}
          onRunNow={runTaskNow}
          initialOpenId={scheduledOpenId}
        />
      ) : surface === "integrations" ? (
        <IntegrationsView />
      ) : surface === "settings" ? (
        <SettingsView
          key={settingsTab}
          initialTab={settingsTab}
          onOpenPersona={(id) => openPersona(id, "settings")}
          onCreateSkill={(description) => {
            // The Skills doorway (SKILLS-SPEC §5.2): creation is a conversation. Fresh
            // session, description in the composer — the user reads and hits send. With
            // no description, the prefill invites them to finish the sentence there.
            startNewSession();
            prefillComposer(
              description
                ? `Build a new skill for me: ${description}`
                : "Build a new skill for me: (describe what the skill should do)",
            );
          }}
        />
      ) : surface === "audit" ? (
        <AuditView />
      ) : surface === "inbox" ? (
        <InboxView onOpenSession={openSessionFromInbox} />
      ) : surface === "persona" ? (
        <PersonaView
          personaId={personaViewId || agent}
          onBack={() =>
            personaViewReturn === "settings" ? openSettings("personas") : setSurface("session")
          }
          onOpenIntegrations={() => setSurface("integrations")}
        />
      ) : (
      <div className={"main" + (surface === "session" && agent !== "chat" && !railHidden ? " rail-open" : "")}>
        <div className="main-topbar">
          {/* Left: the contextual cluster — [sidebar] [+ new session] [search] — rendered ONLY
              while the sidebar is collapsed (§22; the expanded sidebar already owns those
              actions). Clicks must not start a window drag. */}
          <div className="main-topbar-side" onPointerDown={beginWindowDrag}>
            {navCollapsed && (
              <div
                className="flex items-center gap-1"
                data-testid="topbar-cluster"
                onPointerDown={(e) => e.stopPropagation()}
              >
                <button
                  className="topbar-icon-btn"
                  onClick={toggleNav}
                  aria-label={t("Show sidebar")}
                  title={t("Show sidebar (⌘B)")}
                >
                  <Icon name="sidebar" size={16} />
                </button>
                <button
                  className="topbar-icon-btn"
                  onClick={() => startNewSession()}
                  aria-label={t("New session")}
                  title={t("New session")}
                >
                  <Icon name="plus" size={16} />
                </button>
                <button
                  className="topbar-icon-btn"
                  onClick={() => setSearchOpen(true)}
                  aria-label={t("Search")}
                  title={t("Search")}
                >
                  <Icon name="search" size={16} />
                </button>
              </div>
            )}
            {/* §32: no session-settings row up here anymore — the §23 rest/hover/click glance
                machinery retired with the drawer. "What can this touch" lives permanently on
                the rail's Access section header; the panel toggle is the one entry. */}
          </div>
          {/* Center: title + facts subtitle (§22, amended: the ⋯ menu removed — the nav row's
              hover cluster owns pin/rename/archive/delete). The title stays: with the sidebar
              collapsed it is the only session identifier, and it anchors the subtitle. */}
          <div className="main-title" onPointerDown={beginWindowDrag}>
            <span
              className={"main-title-text" + (activeInfo ? "" : " title-ghost")}
              title={activeTitle}
            >
              {activeTitle}
            </span>
            {/* Plain facts, no affordance: the persona page it used to open is hidden for
                this release (owner ask 2026-07-22). */}
            {hasHistory && (
              <span className="title-sub" data-testid="session-subtitle">
                {subtitleParts.join(" · ")}
                {showSaveAsProject && (
                  <>
                    {" · "}
                    <button
                      className="text-accent hover:underline"
                      data-testid="save-as-project"
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={() => void saveAsProject()}
                    >
                      {t("Save as project…")}
                    </button>
                  </>
                )}
              </span>
            )}
          </div>
          {/* Right: session-settings icon (§23) + panel toggle. Model/mode/persona chrome is
              gone — the facts live in the subtitle, the controls in the composer (§22). */}
          <div className="main-topbar-side main-topbar-actions" onPointerDown={beginWindowDrag}>
            {railHidden && artifactCount > 0 && (
              <button
                className="topbar-artifacts-btn"
                onMouseDown={(e) => e.stopPropagation()}
                onClick={() => setRailHidden(false)}
                title={t("Show files this conversation produced")}
              >
                <Icon name="file" size={14} />
                <span>{t("Artifacts")}</span>
                <span className="topbar-artifacts-count">{artifactCount}</span>
              </button>
            )}
            {/* §32: the panel toggle is the ONE session-panel entry, for every non-chat persona
                (the rail now carries Access, so code-family gets it too). */}
            {agent !== "chat" && (
              <button
                className="topbar-icon-btn"
                onMouseDown={(e) => e.stopPropagation()}
                onClick={() => setRailHiddenPersist(!railHidden)}
                aria-label={railHidden ? t("Show side panel") : t("Hide side panel")}
                title={railHidden ? t("Show side panel") : t("Hide side panel")}
              >
                <Icon name="sidebarRight" size={16} />
              </button>
            )}
          </div>
        </div>
        {/* # team chat replaces the session view in place (owner ask 2026-08-16 —
            not a modal): the sidebar stays live, Esc/back returns to the session. */}
        {chatTeam && surface === "session" && (
          <TeamChatView teamId={chatTeam} onClose={() => setChatTeam(null)} />
        )}
        <div className={"main-workspace" + (railHidden ? " rail-hidden" : "")}>
          <div className="main-chat">
            {/* Automation-run context (owner ask 2026-07-04): a __run__ session looked like any
                other chat with no way back to the runs list. Lives INSIDE the chat column (which
                is padded to clear the absolute glass topbar — rendering above .main-workspace put
                it underneath the topbar; owner-reported CSS bug). */}
            {sessionId.startsWith("__run__") && (
              <div
                className="flex items-center gap-2 px-4 py-2 mb-1 rounded-lg text-[13px] border border-line bg-accentSoft/40"
                data-testid="run-banner"
              >
                <Icon name="clock" size={14} className="text-accent shrink-0" />
                <span className="truncate text-muted">
                  {t("Scheduled run")}
                  {runContext?.title ? (
                    <>
                      {" — "}
                      <span className="text-ink font-medium">{runContext.title}</span>
                    </>
                  ) : null}{" "}
                  {t("· started by an automation")}
                </span>
                <button
                  className="ml-auto shrink-0 text-accent font-medium hover:underline"
                  onClick={() => {
                    if (runContext) setScheduledOpenId(runContext.id);
                    setSurface("scheduled");
                  }}
                >
                  {t("← Back to runs")}
                </button>
              </div>
            )}
            <div className="main-scroll" ref={scrollRef} onScroll={handleScroll}>
              {idle ? (
                agent === "cowork" ? (
                  <SessionIntro
                    sessionId={sessionId}
                    onOpenSessionSettings={openAccess}
                    onPrefill={prefillComposer}
                  />
                ) : (
                  <div className="hero">
                    <h1 className="greeting">
                      <span className="mark">✦</span>
                      {agent === "chat" ? t("How can I help?") : t("Let's build something.")}
                    </h1>
                    {(
                      <div className="suggestions">
                        <div className="suggest-head">{t("Try a task")}</div>
                        {SUGGESTIONS.map((s, i) => (
                          <div className="suggest" key={i} onClick={() => workspace && send(t(s.text as MessageKey))}>
                            <span className="ico">{s.ico}</span>
                            {t(s.text as MessageKey)}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              ) : (
                <>
                  <Transcript
                    items={items}
                    onApprove={approve}
                    running={running}
                    onRetry={retry}
                    onOpenConnectors={() => setSurface("integrations")}
                    onAllowAnyway={allowAnyway}
                    onUndoMemory={(id, previous) => void undoMemorySave(id, previous)}
                    // §33 ref #3: sub-threshold streamed text renders INSIDE the live turn
                    // group (header when collapsed, quiet line when expanded) — never as a
                    // floating paragraph.
                    streamingText={streamMode(streaming, items, running) === "quiet" ? streaming : undefined}
                  />
                  {/* Live thinking (reasoning models): a quiet collapsed block that streams the
                      trace for anyone who expands it; folds into the answer's disclosure when
                      the message finalizes. */}
                  {running && reasoningStream && !streaming && (
                    <div className="transcript">
                      <ThinkingBlock text={reasoningStream} live />
                    </div>
                  )}
                  {/* Compaction runs between provider turns (nothing streams during it), so
                      the transient takes over the waiting slot with a specific label. */}
                  {running && compacting && <WaitingForAgent label={t("Compacting context…")} />}
                  {running &&
                    !compacting &&
                    !reasoningStream &&
                    (!streaming || streamMode(streaming, items, running) === "hold") &&
                    !lastItemIsAssistant(items) && <WaitingForAgent />}
                  {streaming && streamMode(streaming, items, running) === "answer" && (
                    <div className="transcript">
                      <div className="bubble-assistant">
                        <div className="who">{t("assistant")}</div>
                        <Markdown text={streaming} />
                        <span className="stream-cursor">▍</span>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Scrolled up while the transcript is still growing → offer the way back down.
                Zero-height strip keeps the pill floating over the scroll area, above the
                composer, without reserving layout space. */}
            {!following && (running || !!streaming) && (
              <div className="relative h-0 z-10">
                <button
                  className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-line bg-panel shadow-md text-[12px] text-muted hover:text-ink cursor-pointer whitespace-nowrap"
                  data-testid="jump-to-latest"
                  onClick={followLatest}
                >
                  <Icon name="chevronDown" size={13} />
                  Jump to latest
                </button>
              </div>
            )}

            {/* UX-029: per-session setup (coworker + folder) lives in its own quiet row
                above the composer — never inside the per-message control row. One-time
                pick: the whole row leaves after the first message; its facts move to the
                session header. */}
            {idle && !sessionId.startsWith("__run__") && (
              <SessionSetupRow
                personas={personas}
                agent={agent}
                showFolder
                folderName={workspace && !tempWorkspace ? baseName(workspace) : null}
                onPickCoworker={pickCoworker}
                onPickFolder={pickDraftFolder}
                onManage={() => openSettings("personas")}
                onImport={() => {
                  openSettings("personas");
                  // Give the Settings page a beat to mount, then spotlight the Add section.
                  window.setTimeout(
                    () => window.dispatchEvent(new CustomEvent("ocw-focus-import")),
                    250,
                  );
                }}
              />
            )}
            {/* A scheduled agent must never read as a dead one: while a self-wake is
                pending and no turn is running, say so and offer the obvious action. */}
            {activeInfo?.liveness === "sleeping" && !running && (
              <div className="sleep-strip" data-testid="sleep-strip">
                <span className="sleep-dot" />
                <span className="sleep-text">
                  {t("Sleeping")}
                  {activeInfo.sleeping_until
                    ? t(" until {time}", {
                        time: new Date(activeInfo.sleeping_until).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
                      })
                    : ""}
                  {activeInfo.team?.role === "lead"
                    ? t(" while the team works — it also wakes on board activity.")
                    : t(" — it wakes on its trigger.")}{" "}
                  {t("Talk to it anytime.")}
                </span>
                <button
                  className="btn sm"
                  data-testid="sleep-status-btn"
                  onClick={() =>
                    send(
                      "Quick status check, please — what's moving, what's blocked, and does anything need me?",
                    )
                  }
                >
                  {t("Ask for a status")}
                </button>
              </div>
            )}
            <Composer
              mode={mode}
              model={model}
              models={models}
              modelLabels={modelLabels}
              running={running}
              gateOpen={!unattended && (!!pendingTeam || !!pendingItemsReq)}
              connected={connected}
              modelReady={modelReady}
              onConnectModel={openModelSetup}
              onOpenMemory={() => openSettings("memory")}
              onConfigureVoiceInput={() => openSettings("voice")}
              onSend={send}
              onInterrupt={interrupt}
              onModeChange={changeMode}
              onModelChange={changeModel}
              sessionId={sessionId}
              workspace={workspace || ""}
              unattended={unattended}
              onUnattendedChange={agent !== "chat" ? toggleUnattended : undefined}
              prefill={composerPrefill}
              resetKey={sessionId}
              usage={usage}
              contextWindow={modelContextWindows[model]}
              contextBar={contextBar}
              reviewerPaused={reviewerPaused}
              placeholder={
                agent === "code"
                  ? "Ask the coder to build, fix, or explain…  (drop or paste files)"
                  : agent === "chat"
                    ? "Ask anything…  (drop or paste files)"
                    : "Ask the coworker…  (drop or paste files)"
              }
              approvalSlot={
                // Live inline cards are for ATTENDED sessions only; when Unattended the prompt is
                // parked in the Inbox and surfaced via the answer-in-context card below.
                !unattended && pendingPlan?.kind === "planreq" ? (
                  <PlanCard item={pendingPlan} onRespond={respondPlan} />
                ) : !unattended && pendingItemsReq?.kind === "itemsreq" ? (
                  <WorkItemsCard item={pendingItemsReq} onRespond={respondItemsReq} />
                ) : !unattended && pendingTeam?.kind === "teamreq" ? (
                  <TeamRequestCard item={pendingTeam} onRespond={respondTeam} />
                ) : !unattended && pendingToolReq?.kind === "toolreq" ? (
                  <ToolRequestCard item={pendingToolReq} onRespond={respondTool} />
                ) : !unattended && pendingDirReq?.kind === "dirreq" ? (
                  <DirectoryRequestCard item={pendingDirReq} onRespond={respondDirectory} />
                ) : !unattended && pendingApproval?.kind === "approval" ? (
                  <ApprovalCard
                    item={pendingApproval}
                    onApprove={approve}
                    runTask={runContext}
                    autoApprove={mode === "auto-approve"}
                    compact
                  />
                ) : !unattended && pendingQuestion?.kind === "question" ? (
                  // Live ask_user in an attended session — answer inline (reuses the Inbox card UI).
                  <InboxItemCard
                    item={{
                      id: "live-question",
                      session_id: sessionId,
                      kind: "question",
                      title: pendingQuestion.question,
                      body: "",
                      state: "pending",
                      resolution: null,
                      inbox: "default",
                      created_at: "",
                      resolved_at: null,
                      options: pendingQuestion.options,
                      allow_text: pendingQuestion.allow_text,
                      multi: pendingQuestion.multi,
                      header: pendingQuestion.header,
                      questions: pendingQuestion.questions,
                    }}
                    onResolve={(_id, answer) => answerQuestion(answer)}
                    compact
                  />
                ) : sessionInbox[0] ? (
                  // Unattended session blocked on an Inbox item — answer it in context.
                  <InboxItemCard item={sessionInbox[0]} onResolve={resolveSessionInbox} compact />
                ) : undefined
              }
            />
                  </div>
          <RightRail
            active={surface === "session" && agent !== "chat" && !railHidden}
            sessionId={sessionId}
            refreshKey={browserRefreshKey}
            toolNames={items.filter((i) => i.kind === "tool").map((i: any) => i.name)}
            todo={todo}
            running={running}
            onPreviewChange={onArtifactPreview}
            // Universal scratch (UX-036): every session has a scratch surface, so the
            // Artifacts section always shows — the server lists the scratch root only.
            showArtifacts
            personaId={agent}
            projectScoped={isProjectScoped(personaOf(agent))}
            workspace={workspace || undefined}
            branch={branch}
            scratchPrimary={tempWorkspace || !isProjectScoped(personaOf(agent))}
            openAccessKey={accessKey}
            onOpenIntegrations={() => setSurface("integrations")}
            board={board}
            onExpandBoard={() => setBoardOpen(true)}
            onOpenBoardItem={(id) => {
              setBoardDetailId(id);
              setBoardOpen(true);
            }}
            /* team serializes as {} for plain sessions — lead-ness needs an actual
               role, else every solo session loses its Progress panel (owner-hit
               2026-08-21: the rail showed nothing but "More"). */
            isLead={
              teamMembers.length > 0 ||
              (curSession?.team?.role != null && curSession.team.role !== "worker")
            }
            teamMembers={teamMembers}
            teamChatEnabled={!!curSession?.team?.chat_enabled}
            teamChatUnread={curSession?.team?.chat_unread || 0}
            onOpenTeamChat={() => setChatTeam(curSession?.team?.team_id || "")}
            onOpenWorker={(w) => void selectSession(w.session_id, w.workspace, w.agent)}
            openBoardKey={boardRailKey}
          />
          {boardOpen && board && board.space && (
            <BoardOverlay
              board={board}
              onClose={() => {
                setBoardOpen(false);
                setBoardDetailId(null);
              }}
              onTransition={moveBoardItem}
              onComment={(item, body) => boardComment(sessionId, item, body)}
              loadItem={(id) => getBoardItem(sessionId, id)}
              loadAttachment={(stored) => fetchBoardAttachment(sessionId, stored)}
              onOpenWorker={(actor) => {
                // The assignee is a team actor whose worker session the sidebar
                // already knows — jump straight into its transcript.
                const match =
                  sessions.find(
                    (s) =>
                      s.team?.role === "worker" &&
                      s.team?.actor === actor &&
                      s.workspace === board.space
                  ) ||
                  sessions.find(
                    (s) => s.team?.role === "worker" && s.team?.actor === actor
                  );
                if (!match) return;
                setBoardOpen(false);
                setBoardDetailId(null);
                void selectSession(match.session_id, match.workspace, match.agent);
              }}
              initialItem={boardDetailId}
            />
          )}
        </div>
      </div>
      )}

      {/* Search from the collapsed-sidebar topbar cluster (the sidebar's own instance is
          unreachable while it's collapsed). */}
      {searchOpen && (
        <SearchModal
          sessions={sessions}
          personas={personas ?? undefined}
          onSelect={(id, ws, ag) => {
            setSearchOpen(false);
            selectSession(id, ws, ag);
          }}
          onClose={() => setSearchOpen(false)}
        />
      )}

      {/* UX-029: the send-time folder dialog — the stashed message flies as soon as a
          choice lands; Escape/backdrop restores the draft to the composer. */}
      {sendGate && surface === "session" && (
        <SendFolderDialog
          coworkerName={fullPersonaName(personaOf(agent)?.name, agent)}
          onPick={resolveSendFolder}
          onTemp={() => void startTempAndSend()}
          onCancel={cancelSendGate}
        />
      )}
      {showGate && surface === "session" && gatesWorkspace(agent) && (
        <FolderGate
          create={gateCreate}
          onChoose={chooseWorkspace}
          onCancel={
            workspace
              ? () => {
                  setShowGate(false);
                  setGateCreate(false);
                }
              : undefined
          }
        />
      )}
      {workspaceTrustRequest && (
        <WorkspaceTrustPrompt
          request={workspaceTrustRequest}
          onClose={() => setWorkspaceTrustRequest(null)}
        />
      )}
    </main>
  );
}

function lastItemIsAssistant(items: Item[]): boolean {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind === "notice") continue;
    return item.kind === "assistant";
  }
  return false;
}

function WaitingForAgent({ label }: { label?: string }) {
  const { t } = useI18n();
  return (
    <div className="waiting-transcript">
      <div className="waiting-row" aria-live="polite">
        <span className="waiting-spinner" />
        <span>{label || t("Waiting for agent...")}</span>
      </div>
    </div>
  );
}
