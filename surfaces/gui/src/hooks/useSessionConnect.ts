import { useEffect, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import { announceMemoryChanged, finalizeAutomationRun, Session, type MessageSource, type WorkspaceCommandTrust } from "../api";
import type { Item, SessionUsage, TodoItem, WsEvent } from "../types";
import type { TFunction } from "../i18n";
import { addTurnUsage } from "../usage";
import type { ActiveRunRef, PendingPrompt } from "./useSessionActions";

// The WebSocket connect effect + event switch, extracted from App.tsx. Owns the
// Session lifecycle for the active session and translates every engine event into
// transcript state updates (items/streaming/running/usage/todo/…) using the
// setters passed via deps. The event switch and the helper functions
// (`normalizeTodos`, `updateLastTool`, `FILE_WRITE_TOOLS`) live here so App.tsx
// only keeps the state and JSX.

const FILE_WRITE_TOOLS = new Set(["write_file", "apply_patch", "apply_unified_diff", "replace_in_file"]);

// Models sometimes pass todo items as bare strings instead of {content, status} objects (the
// backend tool normalizes them the same way; the GUI reads the raw proposal args, so mirror it).
function normalizeTodos(raw: unknown): TodoItem[] {
  if (!Array.isArray(raw)) return [];
  const statuses = new Set(["pending", "in_progress", "done"]);
  return raw.map((entry: any) => {
    if (entry && typeof entry === "object") {
      const status = entry.status === "completed" ? "done" : entry.status; // common model alias
      return {
        content: String(entry.content ?? ""),
        status: statuses.has(status) ? status : "pending",
      };
    }
    return { content: String(entry ?? ""), status: "pending" as const };
  });
}

function updateLastTool(
  items: Item[],
  name: string,
  status: string,
  preview?: string,
  hidden?: number,
  standingRule?: string,
  reviewerReason?: string,
  allowAnyway?: boolean,
  approvalOrigin?: string,
  approvalNote?: string,
  durationMs?: number,
): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "tool" && it.name === name && it.status === "…") {
      let sandboxed = false;
      if (typeof preview === "string" && preview) {
        try {
          sandboxed = JSON.parse(preview)?.sandboxed === true;
        } catch {
          /* preview is plain text — not a sandboxed shell result */
        }
      }
      copy[i] = {
        ...it,
        status,
        preview,
        ...(hidden ? { hidden } : {}),
        ...(sandboxed ? { sandboxed: true } : {}),
        ...(standingRule ? { standingRule } : {}),
        ...(reviewerReason ? { reviewerReason } : {}),
        ...(allowAnyway ? { allowAnyway } : {}),
        ...(approvalOrigin ? { approvalOrigin } : {}),
        ...(approvalNote ? { approvalNote } : {}),
        ...(durationMs != null ? { durationMs } : {}),
      };
      break;
    }
  }
  return copy;
}

export interface SessionConnectDeps {
  booting: boolean;
  agent: string;
  sessionId: string;
  workspace: string | null;
  connectNonce: number;
  t: TFunction;
  streamingRef: MutableRefObject<string>;
  reasoningRef: MutableRefObject<string>;
  sessionRef: MutableRefObject<Session | null>;
  unattendedRef: MutableRefObject<boolean>;
  activeRunRef: MutableRefObject<ActiveRunRef | null>;
  pendingPromptRef: MutableRefObject<PendingPrompt | null>;
  setStreaming: Dispatch<SetStateAction<string>>;
  setReasoningStream: (value: string) => void;
  setItems: Dispatch<SetStateAction<Item[]>>;
  setCompacting: Dispatch<SetStateAction<boolean>>;
  setConnected: Dispatch<SetStateAction<boolean>>;
  setModel: Dispatch<SetStateAction<string>>;
  setMode: Dispatch<SetStateAction<string>>;
  setWorkspaceTrustRequest: Dispatch<SetStateAction<WorkspaceCommandTrust | null>>;
  setWorkspace: Dispatch<SetStateAction<string | null>>;
  setTempWorkspace: Dispatch<SetStateAction<boolean>>;
  setRunning: Dispatch<SetStateAction<boolean>>;
  setReviewerPaused: Dispatch<SetStateAction<boolean>>;
  setTodo: Dispatch<SetStateAction<TodoItem[]>>;
  setUsage: Dispatch<SetStateAction<SessionUsage>>;
  setBrowserRefreshKey: Dispatch<SetStateAction<number>>;
  refreshSessions: () => void;
  gatesWorkspace: (a: string) => boolean;
  newId: () => string;
}

export function useSessionConnect(deps: SessionConnectDeps) {
  const {
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
  } = deps;

  // (re)connect when workspace, session, or agent changes
  useEffect(() => {
    if (booting) return; // wait until boot/resume settles the session before connecting
    if (gatesWorkspace(agent) && !workspace) return; // Code needs a folder (gate handles it)
    const handleEvent = (ev: WsEvent) => {
      const d = ev.data || {};
      // An interrupted/errored turn never emits assistant_message, so its streamed partial
      // would otherwise live only in the ephemeral buffer until the next turn_start wipes it
      // (owner-hit 2026-07-22). Promote it to a durable transcript item — the engine persists
      // the same text server-side, so the live view and a session reload now agree.
      const flushPartialStream = () => {
        const partial = streamingRef.current;
        const thinking = reasoningRef.current;
        if (!partial && !thinking) return;
        setStreaming("");
        setReasoningStream("");
        setItems((p) => [
          ...p,
          {
            kind: "assistant",
            text: partial,
            ts: Date.now() / 1000,
            ...(thinking ? { reasoning: thinking } : {}),
          },
        ]);
      };
      // Any engine event after `compacting` means the summarizer finished (compacted /
      // silent no-op / failure prompt) — the transient must never outlive it.
      if (ev.type !== "compacting") setCompacting(false);
      switch (ev.type) {
        case "ready":
          setConnected(true);
          if (d.model) setModel(d.model);
          if (d.mode) setMode(d.mode);
          if (d.command_trust?.required) setWorkspaceTrustRequest(d.command_trust);
          // Cowork: adopt the server-provisioned scratch dir (only when we don't already have one).
          if (d.workspace) setWorkspace((cur) => cur || d.workspace);
          // UX-029: server truth on whether this session runs in a temporary folder.
          if (typeof d.temp_workspace === "boolean") setTempWorkspace(d.temp_workspace);
          // Server truth on a live turn: a reconnect mid-turn never sees turn_start, so
          // without this the Stop button and waiting row vanish (owner catch 2026-08-24).
          if (typeof d.running === "boolean") setRunning(d.running);
          break;
        case "turn_start":
          setRunning(true);
          setReviewerPaused(false); // a fresh user message resets the denial streak
          setStreaming("");
          setReasoningStream("");
          // Background-delivered turns (channel message, self-wake, durable resume) have no local
          // send(), so the triggering message isn't in `items` yet — surface it. A connector message
          // carries a structured `source` (§3.1) → render the rich card; otherwise a plain user item.
          // Foreground turns already appended it in send(); skip the duplicate.
          if (d.source?.connector) {
            const src = d.source as MessageSource;
            setItems((p) => {
              const last = p[p.length - 1];
              return last && last.kind === "connector" && last.source.ts === src.ts && last.source.text === src.text
                ? p
                : [...p, { kind: "connector", source: src }];
            });
          } else if (typeof d.input === "string" && d.input) {
            // `display` (force-run) is the user's literal "/name …" line; the framed
            // `input` is model-facing. Surface/dedupe on what the user actually sees.
            const shown = (typeof d.display === "string" && d.display) || (d.input as string);
            setItems((p) => {
              // Look past trailing notices — the UX-029 "Temporary folder created" line
              // sits between the local echo and this event's arrival.
              let i = p.length - 1;
              while (i >= 0 && p[i].kind === "notice") i--;
              const last = p[i];
              return last && last.kind === "user" && last.text === shown
                ? p
                : [...p, { kind: "user", text: shown, ts: Date.now() / 1000 }];
            });
          }
          break;
        case "assistant_delta":
          setStreaming((s) => s + (d.text || ""));
          break;
        case "reasoning_delta":
          setReasoningStream(reasoningRef.current + (d.text || ""));
          break;
        case "assistant_message": {
          if (d.usage) setUsage((u) => addTurnUsage(u, d.usage));
          // The event's reasoning is authoritative (covers background-delivered turns);
          // the local buffer is the fallback for older servers.
          const reasoning = d.reasoning || reasoningRef.current;
          if (d.text || reasoning)
            setItems((p) => [
              ...p,
              {
                kind: "assistant",
                text: d.text || "",
                ts: Date.now() / 1000,
                ...(reasoning ? { reasoning } : {}),
              },
            ]);
          setStreaming(""); // finalized into items (or empty tool-only turn)
          setReasoningStream("");
          break;
        }
        case "tool_proposed":
          if (d.name === "todo_write" && (d.arguments?.todos || d.arguments?.items))
            setTodo(normalizeTodos(d.arguments.todos ?? d.arguments.items));
          setItems((p) => [
            ...p,
            { kind: "tool", id: newId(), name: d.name, args: d.arguments, status: "…" },
          ]);
          break;
        case "permission_required":
          // Unattended → the backend parked it in the Inbox; don't also surface a live card.
          if (unattendedRef.current) break;
          setItems((p) => [
            ...p,
            {
              kind: "approval",
              name: d.name,
              args: d.arguments,
              reason: d.reason,
              category: d.category,
              standingTarget: d.standing_target || undefined,
              searchProvider: d.search_provider || undefined,
              provenance: d.provenance || undefined,
              reviewerUnsure: d.reviewer_unsure || undefined,
              readonlyOk: !!d.readonly_ok,
            },
          ]);
          break;
        case "directory_requested":
          if (unattendedRef.current) break;
          setItems((p) => [
            ...p,
            { kind: "dirreq", reason: d.reason || "", path: d.path || "", writable: !!d.writable, primary: !!d.primary },
          ]);
          break;
        case "tool_requested":
          if (unattendedRef.current) break;
          setItems((p) => [
            ...p,
            {
              kind: "toolreq",
              tool: d.name || "",
              reason: d.reason || "",
              // Fail CLOSED: only offer Install when the event says a pinned build exists.
              installable: d.installable === true,
              version: d.version || "",
              summary: d.summary || "",
              source: d.source || "",
            },
          ]);
          break;
        case "plan_proposed":
          if (unattendedRef.current) break;
          setItems((p) => [...p, { kind: "planreq", plan: d.plan || "" }]);
          break;
        case "team_proposed":
          // The staffing gate (agent teams) — approval pre-spawns the worker sessions.
          if (unattendedRef.current) break;
          setItems((p) => [
            ...p,
            {
              kind: "teamreq",
              members: Array.isArray(d.members) ? d.members : [],
              enable_chat: !!d.enable_chat,
              note: d.note || "",
            },
          ]);
          break;
        case "items_proposed":
          // The decomposition gate — approval creates the items on the board.
          if (unattendedRef.current) break;
          setItems((p) => [
            ...p,
            {
              kind: "itemsreq",
              items: Array.isArray(d.items) ? d.items : [],
              note: d.note || "",
            },
          ]);
          break;
        case "question_requested":
          // ask_user in an attended session — answered inline (not routed to the Inbox).
          setItems((p) => [
            ...p,
            {
              kind: "question",
              question: d.question || "",
              options: d.options || [],
              allow_text: d.allow_text !== false,
              multi: !!d.multi,
              header: d.header || "",
              questions: d.questions || [],
            },
          ]);
          break;
        case "tool_finished":
          setItems((p) =>
            updateLastTool(
              p,
              d.name,
              d.status,
              d.result_preview || d.reason,
              d.display?.hidden_by_filters,
              d.standing_rule,
              d.reviewer_reason,
              d.allow_anyway,
              d.approval_origin,
              d.approval_note,
              d.duration_ms,
            ),
          );
          // §8.4 breaker: the reviewer paused itself for the rest of the turn — say so
          // where the user is looking (persisted server-side for reloads) and on the
          // composer's mode chip.
          if (d.reviewer_paused) {
            setReviewerPaused(true);
            setItems((p) => [...p, { kind: "notice", tone: "info", text: String(d.reviewer_paused) }]);
          }
          // Refresh the right rail when something it shows may have changed: browser state, or a
          // file write that should appear under Artifacts immediately (not only after the turn).
          if (String(d.name || "").startsWith("browser_") || FILE_WRITE_TOOLS.has(d.name)) {
            setBrowserRefreshKey((k) => k + 1);
          }
          break;
        case "turn_end":
          if (d.status === "max_iterations_exceeded")
            setItems((p) => [...p, { kind: "notice", tone: "warn", text: t("Stopped: max iterations reached.") }]);
          break;
        case "mode_notice":
          // Server-authored + persisted (owner ruling 2026-08-24): the Auto-Approve
          // explainer once per session ever, one-line markers for later switches.
          setItems((p) => [
            ...p,
            { kind: "notice", tone: "info", ...(d.title ? { title: d.title } : {}), text: d.text || "" },
          ]);
          break;
        case "model_changed":
          // Mid-session switch (server-applied): update the header fact and drop the
          // persisted marker into the live transcript (replay renders it from history).
          if (d.model) setModel(d.model);
          setItems((p) => [...p, { kind: "notice", tone: "info", text: d.text || t("Model switched") }]);
          break;
        case "memory_saved":
          // §5.1 save notice — inline in the transcript, where the user is already
          // looking and where it keeps until they act (a corner toast disappeared
          // before it could be read or undone — owner-hit 2026-07-28). Summary is the
          // friendly one-liner; content is the fallback when the model skipped it.
          setItems((p) => [
            ...p,
            {
              kind: "memory",
              id: Number(d.id),
              text: String(d.summary || d.content || ""),
              // Present when an existing memory was edited rather than added — the
              // notice says so, and Undo restores this text instead of deleting.
              ...(d.previous ? { previous: String(d.previous) } : {}),
            },
          ]);
          announceMemoryChanged(); // Settings ▸ Memory, if open, is now stale
          break;
        case "compacting":
          setCompacting(true);
          break;
        case "compacted":
          // Auto-compaction marker (OPE-27): outbound-only — the transcript stays intact,
          // this divider just shows where the model's memory was summarized.
          setItems((p) => [...p, { kind: "notice", tone: "info", text: d.text || t("Context compacted") }]);
          break;
        case "interrupted":
          flushPartialStream();
          setItems((p) => [...p, { kind: "notice", tone: "warn", text: t("Interrupted.") }]);
          break;
        case "error":
          flushPartialStream();
          setItems((p) => [
            ...p,
            { kind: "notice", tone: "warn", text: t("Error: {msg}", { msg: d.error || "unknown" }), retriable: true },
          ]);
          break;
        case "input_rejected":
          setItems((p) => [
            ...p,
            { kind: "notice", tone: "warn", text: d.error || t("That message was rejected.") },
          ]);
          break;
        case "steering_queued":
          // P2-9: the message joins the running turn at its next model request.
          setItems((p) => [
            ...p,
            { kind: "notice", tone: "info", text: t("Queued — it applies at the next step.") },
          ]);
          break;
        case "turn_done":
          setRunning(false);
          setReviewerPaused(false); // the pause is scoped to the turn
          refreshSessions();
          // Catch-all artifact refresh: files created via shell or on a brand-new session (whose
          // record only exists after the first save) appear once the turn completes.
          setBrowserRefreshKey((k) => k + 1);
          // Finalize a manual run after its first turn completes (mark it ok in history).
          {
            const ar = activeRunRef.current;
            if (ar && ar.sessionId === sessionId) {
              activeRunRef.current = null;
              finalizeAutomationRun(ar.taskId, ar.runId).catch(() => {});
            }
          }
          break;
      }
    };

    const session = new Session(sessionId, workspace || "", agent, {
      onEvent: handleEvent,
      onOpen: () => {
        setConnected(true);
        // Auto-send the pending message once the session connects ("Run now" prompts and
        // UX-029's deferred first send).
        const p = pendingPromptRef.current;
        if (p) {
          pendingPromptRef.current = null;
          const shown = p.skill ? `/${p.skill}${p.text ? ` ${p.text}` : ""}` : p.text;
          setItems((prev) => [
            ...prev,
            { kind: "user", text: shown, attachments: p.attachments, ts: Date.now() / 1000 },
            ...(p.notice
              ? [{ kind: "notice", tone: "info", text: p.notice } as Item]
              : []),
          ]);
          sessionRef.current?.userMessage(p.text, p.attachments, p.model, p.skill);
        }
      },
      onClose: () => setConnected(false),
    });
    sessionRef.current = session;
    return () => session.close();
    // NOTE: `workspace` is intentionally NOT a dependency. Every real workspace change
    // (pick folder, select/switch session, new session) is paired with a `sessionId`
    // change, so the socket still reconnects when it should. The one workspace-only change
    // is the `ready` handler adopting the server's provisioned Cowork scratch dir — listing
    // `workspace` here made that adoption tear down and rebuild the socket immediately after
    // first connect, dropping the user's first message (the "send twice" bug). The scratch
    // dir is deterministic from `sessionId` server-side, so skipping that reconnect is safe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booting, sessionId, agent, refreshSessions, connectNonce]);
}