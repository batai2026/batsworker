"""Session manager — owns engines (one per session), stores, and the provider.

Each session is bound to a workspace folder (Code requires one). Storage is a single DB
under a data dir (global for the real server, per-workspace for tests), so recents and
sessions span folders.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from ..agent import build_engine
from ..agents import get_agent
from ..connections import (
    PersonaConnectionStore,
    SessionConnectionStore,
)
from ..inbox import InboxStore
from ..inbox_routing import InboxRouting
from ..personas import PersonaRegistry
from ..personas.registry import set_registry as set_persona_registry
from ..selfwake import WakeStore
from ..mentions import MentionSessionStore
from ..subscriptions import ChannelBuffer, SubscriptionStore
from ..unrouted import UnroutedStore
from ..unattended import UnattendedRegistry
from ..audit import AuditStore
from ..config import load_config
from ..conversations import ConversationStore, title_from
from ..engine import Approver, TurnEngine
from ..workspace_trust import WorkspaceTrustStore
from ..automation import Scheduler, TaskStore
from ..connectors import Gateway
from ..connectors.parked import ParkedStore
from ..mcp import MCPManager
from ..memory import MemorySettingsStore, MemoryStore, SQLiteMemoryStore
from ..permissions import Mode
from ..agents import list_agents as _list_agents
from ..providers import (
    ProviderClient,
    ProviderRouter,
)
from ..budget import BudgetStore
from ..observability import TraceStore
from ..secrets import SecretStore, state_dir
from ..sessions import SessionRecord
from ..teams import JournalStore, TeamStore
from ..teams.chat import ChatStore
from ..teams.registry import TeamRegistry
from ..teams.attachments import AttachmentStore
from ..teams.tokens import BoardTokens
from ..skills import (
    SessionSkillStore,
    SkillStore,
)
# Split refactor: module-level helpers live in manager_helpers; the three redundant
# aliases re-export the names tests import from this module (test_readonly_grant /
# test_inbox / test_automation). Mixins import helpers from .manager_helpers directly.
from .manager_helpers import (
    _approval_body as _approval_body,
    _grants_of as _grants_of,
    _last_assistant_text as _last_assistant_text,
)
from .manager_workspaces import WorkspacesMixin
from .manager_sessions import SessionsMixin
from .manager_personas import PersonasMixin
from .manager_inbox import InboxMixin
from .manager_mcp import McpMixin
from .manager_connectors import ConnectorsMixin
from .manager_teams import TeamsMixin
from .manager_artifacts import ArtifactsMixin
from .manager_providers import ProvidersMixin
from .manager_settings import SettingsMixin
from .manager_approvals import ApprovalsMixin
from .manager_scheduler import SchedulerMixin
from .manager_memory import MemoryMixin
from .manager_skills import SkillsMixin
from .manager_plugins import PluginsMixin

logger = logging.getLogger("coworker.manager")


class SessionManager(
    WorkspacesMixin,
    SessionsMixin,
    PersonasMixin,
    InboxMixin,
    McpMixin,
    ConnectorsMixin,
    TeamsMixin,
    ArtifactsMixin,
    ProvidersMixin,
    SettingsMixin,
    ApprovalsMixin,
    SchedulerMixin,
    MemoryMixin,
    SkillsMixin,
    PluginsMixin,
):
    def __init__(
        self,
        *,
        workspace: Optional[str | Path] = None,  # default/seed workspace (e.g. --cwd)
        data_dir: Optional[str | Path] = None,
        model: str = "gpt-5.6-sol",
        mode: Mode = Mode.INTERACTIVE,
        provider: Optional[ProviderClient] = None,
    ) -> None:
        self.default_workspace = (
            str(Path(workspace).expanduser().resolve()) if workspace else None
        )
        self.model = model
        self.mode = mode
        self.provider = provider

        if data_dir is not None:
            base = Path(data_dir).expanduser()
        elif self.default_workspace is not None:
            base = Path(self.default_workspace) / ".coworker"
        else:
            base = state_dir()
        base.mkdir(parents=True, exist_ok=True)

        self.memory_store: MemoryStore = SQLiteMemoryStore(base / "coworker.db")
        # MEMORY-SPEC §4.3/§6: the on/off switch + the user's standing rules. Settings-
        # level, outside the memory table; read at engine build time.
        self.memory_settings = MemorySettingsStore(base / "memory-settings.json")
        self.audit_store = AuditStore(base / "coworker.db")
        self.session_store = ConversationStore(base)
        self.session_store.canonicalize_workspaces()  # collapse /tmp vs /private/tmp etc.
        if self.default_workspace:
            self.session_store.touch_workspace(self.default_workspace)
        self._engines: dict[str, TurnEngine] = {}
        # Turn-checkpoint blob store + turn records live under the same data dir
        # (never inside a writable root — the agent must not reach its own undo
        # history). See coworker/checkpoints.py.
        self.data_dir = base
        self.trace_store = TraceStore(base / "traces.db")
        # Turn ledger (trajectory.py): lazily built on first attach/read so config
        # (trajectory_max_records) is only read once per manager.
        self._trajectory_store_obj = None
        self.budget_store = BudgetStore(base / "budget.json")
        # Sessions whose workspace was promoted mid-turn (workspace-scratch-design.md §5):
        # evicted from the engine cache at the next mark_idle so the following turn
        # rebuilds fully anchored on the new workspace.
        self._promotion_rebuild: set[str] = set()
        self._running_sessions: set[str] = (
            set()
        )  # sessions with an in-flight turn (busy)
        # Sessions with an auto-title LLM call in flight (FB-010) — one call at a time.
        self._autotitle_inflight: set[str] = set()
        self._autotitle_tasks: set[asyncio.Task] = set()
        # 后台 replay turn 的 task 引用（防 GC，同 _autotitle_tasks 模式）
        self._replay_tasks: set[asyncio.Task] = set()
        self._autotitle_attempts: dict[str, int] = {}
        # Opener-count signature of the last attempt: titling fires at TURN START (owner
        # catch 2026-08-24 — waiting for an agentic turn to COMPLETE left sessions
        # untitled for however long the scan ran), and the completion hook still covers
        # background turns; this guard keeps the two trigger points from burning
        # duplicate attempts on the same openers.
        self._autotitle_sig: dict[str, int] = {}
        self.workspace_trust = WorkspaceTrustStore()
        self.secrets = SecretStore()
        # No explicit provider injected → route by the model's `provider:` prefix (OpenAI default,
        # Ollama, …). Tests inject a provider directly and bypass the router. The same router is
        # shared by every engine and the `/v1/chat/completions` proxy.
        if self.provider is None:
            self.provider = ProviderRouter(
                self.secrets, default_provider="openai", on_use=self._note_provider_use
            )
        self.mcp = MCPManager(secrets=self.secrets)
        # OAuth MCP servers with a sign-in in flight / their last connect error —
        # feeds list_mcp's status so the GUI can show "authorizing…" and failures.
        self._mcp_authorizing: set[str] = set()
        self._mcp_errors: dict[str, str] = {}
        # ChatGPT-subscription provider sign-in in flight / its last error — feeds
        # the providers list + status route so the GUI can show "authorizing…".
        self._codex_authorizing = False
        self._codex_error: Optional[str] = None
        # http servers whose anonymous connect came back 401/403 — the failure is
        # "needs sign-in", so the GUI offers the OAuth switch instead of a raw error.
        self._mcp_auth_hints: set[str] = set()
        # Servers that failed to connect while preparing a session's tools —
        # drained once by the WS handler to append a transcript notice.
        self._mcp_session_failures: dict[str, list[str]] = {}
        self.gateway: Optional[Gateway] = None
        self._data_base = base
        # Desktop/UI prefs (default model, onboarding state) — not secrets; a plain JSON file.
        self._prefs = self._load_prefs()
        if self._prefs.get("default_model"):
            self.model = self._prefs["default_model"]
        # Seed the PDF-fallback module global from prefs so engines see the user's
        # choice from the first turn (set_pdf_settings keeps it in sync after).
        from ..pdf_support import set_fallback_mode

        set_fallback_mode(self.pdf_settings()["pdf_fallback"])
        # Per-session live-view registry: every socket open on a session id gets the turn's events,
        # whoever drives the turn (foreground user_message, channel delivery, self-wake, resume).
        # Delivery itself is socket-independent — this only governs *live visibility*.
        self._session_clients: dict[str, set[Any]] = {}
        # App-wide event sockets (/ws/events): session-independent pushes — today the
        # automation-run-started toast (UX-026); badges could ride it later.
        self._event_clients: set[Any] = set()
        # Automation: scheduled tasks store + the tick scheduler (started in the lifespan).
        # The scheduler also resumes self-wake'd sessions each tick (extra_tick), and
        # evaluates standing-goal triggers via the readers below (board items by
        # workspace, pending inbox) — standing goals must never touch the network.
        self.task_store = TaskStore(base / "automation.db")
        self.scheduler = Scheduler(
            self.task_store,
            self._run_scheduled_task,
            extra_tick=self._scheduler_tick,
            event_context=self._event_context,
        )
        # Agent teams: two append-only stores, one record discipline. The journal is
        # case-keyed (knowledge outlives boards/teams); the board log is space-scoped,
        # and assignment feeds journal-case grants. Verbs register per-session behind
        # the persona's `team:` trait; the registry holds rosters (lead/worker
        # sessions per board) that the wake plumbing walks.
        self.journal_store = JournalStore(base / "journal.db")
        self.team_store = TeamStore(base / "teams.db", journal=self.journal_store)
        self.chat_store = ChatStore(base / "chat.db")
        self.teams = TeamRegistry(base / "teams.json")
        # External board clients (OPE-100): join tokens bind actor+role; the
        # `/v1/board` API resolves them and the store enforces authority.
        self.board_tokens = BoardTokens(base / "board-tokens.json")
        # Work-item attachments (OPE-105): content-addressed blobs next to the
        # board; the log carries only `attachment://` refs.
        self.attachment_store = AttachmentStore(base / "attachments")
        self._team_inflight: set[str] = set()
        # Lead-session last-turn timestamps for the check-in backstop (monotonic-ish
        # wall clock; restart resets the clock rather than firing a wake storm).
        self._team_last_alive: dict[str, float] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Personas: registry + lifecycle state under this manager's data dir. Installed as the
        # process singleton so agents.get_agent resolves persona ids (incl. third-party) here.
        self.personas = PersonaRegistry(state_path=base / "personas.json")
        set_persona_registry(self.personas)
        # Inbox (cross-session human-attention queue), routing (named inboxes + Slack/Telegram
        # bindings), the Unattended toggle, and self-wake records.
        self.inbox = InboxStore(base / "inbox.json")
        self.inbox_routing = InboxRouting(base / "inbox_routing.json")
        self.unattended = UnattendedRegistry(base / "unattended.json")
        self.wakes = WakeStore(base / "wakes.json")
        # Channel subscriptions (inbound): persisted (session_id, channel) records + a ring buffer
        # of recently-seen channel messages for get_channel_messages.
        self.subscriptions = SubscriptionStore(base / "subscriptions.json")
        self.channel_buffer = ChannelBuffer(state_path=base / "channels.json")
        # Mention router (§31): thread target → the session that owns that Slack thread.
        # Also the durable source of the thread's standing send_message grant (re-seeded
        # onto the engine in get_engine).
        self.mention_sessions = MentionSessionStore(base / "mention_threads.json")
        # Unauthorized inbound messages, parked instead of dropped (one-step allow-and-deliver).
        self.parked = ParkedStore(base / "parked.json")
        # People directory: "platform:user_id" → display name, noted from every inbound
        # (authorized or parked) so allow-list chips read "Rohit Prsad", not "U07JK…".
        self._people_path = base / "people.json"
        try:
            self._people: dict[str, str] = json.loads(self._people_path.read_text())
        except (OSError, ValueError):
            self._people = {}
        # Seed from already-parked messages (they carry resolved names) so an allow made from
        # an old parked item still gets a named chip.
        for it in self.parked.list():
            if it.get("user_name"):
                self._people.setdefault(
                    f"{it['platform']}:{it['user_id']}", it["user_name"]
                )
        # Connection hierarchy (UI-REFRESH §4): per-persona default connector on/off (seeded from the
        # manifest, then user-editable) + per-session overrides. Resolved into the session's effective
        # connector set, which gates inbound delivery and the engine's connector tools.
        self.persona_connections = PersonaConnectionStore(
            base / "persona_connections.json"
        )
        self.session_connections = SessionConnectionStore(
            base / "session_connections.json"
        )
        # Skills (SKILLS-SPEC §4): folder-backed CRUD + per-session mutes. The effective menu
        # gates the engine's skill catalog the same way effective_connectors gates connector
        # tools — one resolver feeds the catalog injection, the rail, and the composer popup.
        self.skill_store = SkillStore()
        self.session_skills = SessionSkillStore(base / "session_skills.json")
        # Dead-letter: inbound messages with no destination + background-turn failures, so neither
        # vanishes silently (a debugging/visibility surface, not a redelivery queue).
        self.unrouted = UnroutedStore(base / "unrouted.json")

    def get_engine(
        self,
        session_id: str,
        *,
        workspace: Optional[str] = None,
        agent: str = "code",
        approver: Optional[Approver] = None,
        extra_tools: Optional[list[Any]] = None,
        directory_requester: Optional[Any] = None,
        plan_approver: Optional[Any] = None,
        question_asker: Optional[Any] = None,
        tool_requester: Optional[Any] = None,
        team_approver: Optional[Any] = None,
        items_approver: Optional[Any] = None,
    ) -> Optional[TurnEngine]:
        engine = self._engines.get(session_id)
        if engine is not None:
            self._attach_checkpoints(session_id, engine)
            if approver is not None:
                engine.approver = approver
            if directory_requester is not None:
                engine.directory_requester = directory_requester
            if plan_approver is not None:
                engine.plan_approver = plan_approver
            if question_asker is not None:
                engine.question_asker = question_asker
            if tool_requester is not None:
                engine.tool_requester = tool_requester
            if team_approver is not None:
                engine.team_approver = team_approver
            if items_approver is not None:
                engine.items_approver = items_approver
            return engine

        record = self.session_store.load(session_id)
        is_new_session = record is None
        agent_name = (record.agent if record else agent) or "code"
        ag = get_agent(agent_name)

        if record:
            ws = record.workspace or None
            model, mode, messages = record.model, Mode(record.mode), record.messages
        else:
            ws = self.resolve_workspace(workspace)
            model, mode, messages = self.model, self.mode, None

        if not ws or not Path(ws).is_dir():
            # Sessions without a folder start "orphan": auto-provision a per-conversation
            # scratch directory (generalizes MyHelper's auto-workspace). Folder-gated
            # personas (requires_folder) still demand a real directory picked by the user.
            if not ag.requires_folder:
                ws = self._provision_scratch(session_id)
            else:
                return None

        if ws:
            self.session_store.touch_workspace(ws)
        # Universal scratch (workspace-scratch-design.md §4): EVERY session is multi-root
        # with a per-conversation scratch dir. Orphan sessions run ON their scratch
        # (ws == scratch, primary). Sessions on a real folder — gated personas, or a
        # temp-workspace pick that later became a project — keep that folder primary and
        # gain scratch as a second writable root, so deliverables/temp files have a home
        # that never dirties the user's repo. request_directory rides on roots, so it now
        # registers everywhere.
        roots = None
        if ws:
            extra = [
                r
                for r in ((record.extra_roots if record else []) or [])
                if Path(str(r.get("path", ""))).is_dir()
            ]
            if self.is_temp_workspace(ws):
                roots = [{"path": ws, "writable": True, "label": "scratch"}, *extra]
            elif self._SESSION_ID_RE.match(session_id or "") and session_id not in {".", ".."}:
                roots = [
                    {"path": ws, "writable": True, "label": "workspace"},
                    {
                        "path": self._provision_scratch(session_id),
                        "writable": True,
                        "label": "scratch",
                    },
                    *extra,
                ]
            else:
                # A session id we won't put in a filesystem path: primary root only.
                roots = [{"path": ws, "writable": True, "label": "workspace"}, *extra]
        engine = build_engine(
            agent=ag,
            workspace=ws,
            model=model,
            mode=mode,
            provider=self.provider,
            # Memory off (§4.3) = stop LEARNING, not amnesia: saved facts still inject
            # and stay usable, only the write tools go. Read at build time; running
            # sessions finish under the mode they started with.
            memory_store=self.memory_store,
            memory_workspace=self._memory_key_for(record, ws),
            memory_off=not self.memory_settings.enabled,
            # LIVE, not a snapshot: turning saving off mid-conversation must take
            # effect at once (owner-hit 2026-07-28 — a running session kept saving).
            memory_saving_enabled=lambda: self.memory_settings.enabled,
            # Callable, not a snapshot: editing your instructions in Settings applies
            # to conversations already open (same reason as the saving switch).
            user_rules=lambda: self.memory_settings.user_rules,
            on_memory_saved=self._memory_saved_notifier(session_id),
            messages=messages,
            extra_tools=[
                *(extra_tools or []),
                *self._team_tools_for(session_id, ag, record, ws),
            ]
            or None,
            secrets=self.secrets,
            task_store=self.task_store,
            wake_store=self.wakes,
            session_id=session_id,
            audit_sink=self.audit_store.append,
            roots=roots,
            # WS sessions pass mode-aware callbacks (attended → live prompt, unattended → Inbox).
            # Background / self-wake / durable-resume runs have no live socket → default to the
            # Inbox-based callbacks so a rebuilt engine can still get approvals/answers (and, on
            # resume, the already-resolved item returns immediately).
            approver=approver or self.inbox_approver(session_id, agent),
            directory_requester=directory_requester
            or self.inbox_directory_requester(session_id, agent),
            plan_approver=plan_approver or self.inbox_plan_approver(session_id, agent),
            question_asker=question_asker
            or self.inbox_question_asker(session_id, agent),
            tool_requester=tool_requester,
            team_approver=team_approver,
            items_approver=items_approver,
            subscription_store=self.subscriptions,
            channel_buffer=self.channel_buffer,
            routing_targets=self._routing_targets(session_id, agent),
            # Per-session connection hierarchy: expose only effective-enabled connectors' tools.
            connector_filter=self.effective_connectors(session_id, agent_name),
            # Per-session skill menu, LIVE (SKILLS-SPEC §3): a callable so load_skill sees
            # disables/new skills immediately; the catalog snapshot is taken at build.
            skill_filter=lambda sid=session_id, w=ws, a=agent_name: (
                self.effective_skill_names(sid, w, agent=a)
            ),
            # Persona-carried skills (OPE-58): the bundle's skills/ dir joins the loader
            # so its skills are readable, not just listed.
            extra_skill_dirs=(
                [d] if (d := self.persona_skill_scope(agent_name)[0]) is not None else None
            ),
            # Local RAG (rag.py): the embedding config read live at build — when the
            # user configures an endpoint, the tools register; when not, they don't.
            rag_config_getter=self._rag_config,
            rag_state_dir=self.data_dir,
            # Auto-Approve (spec §1.5): prefs-backed, so the Settings toggle takes effect on
            # the next session build without a config.toml edit.
            auto_approve=self.auto_approve(),
            auto_approve_shadow=self.auto_approve_shadow(),
            # Reviewer model decoupling: the reviewer can use a different model
            # (and provider family) than the session — decorrelated errors.
            reviewer_model=(load_config().reviewer_model or "").strip() or None,
            fast_model=(load_config().fast_model or "").strip() or None,
        )
        # An automation run rebuilt here (manual "Run now" over WS, durable resume) still
        # carries its task's standing allowances — the rules live on the task record.
        owning_task = self.task_store.task_for_run_session(session_id)
        if owning_task is not None:
            self._seed_task_permissions(engine, owning_task)
        # A mention-spawned session (§31) keeps its in-thread reply pre-approved across
        # rebuilds/restarts — the grant is re-derived from the durable thread map.
        for thread_target in self.mention_sessions.targets_for(session_id):
            engine.permissions.task_rules.setdefault("send_message", set()).add(
                thread_target
            )
        if record is not None and record.grants:
            self._apply_grants(engine, record.grants)
        # Auto-compaction (OPE-27): restore the persisted view boundary and wire the live
        # Settings getter — post-construction, so build_engine's signature stays put.
        if record is not None and record.compaction:
            from ..compaction import CompactionState

            engine.compaction_state = CompactionState.from_dict(record.compaction)
        engine.compaction_settings = self.compaction_settings
        self._attach_checkpoints(session_id, engine)
        self._engines[session_id] = engine
        if is_new_session:
            self._emit_session_created(session_id, agent_name)
        return engine

    def _attach_checkpoints(self, session_id: str, engine: TurnEngine) -> None:
        """Give the engine its turn-checkpoint tracker (idempotent — the tracker
        persists per session id under the data dir, so a rebuild keeps history)."""
        if getattr(engine, "checkpoints", None) is not None:
            return
        from ..checkpoints import SessionCheckpoints

        engine.checkpoints = SessionCheckpoints(
            session_id=session_id,
            state_dir=self.data_dir,
            roots_getter=lambda: getattr(engine.permissions, "roots", []),
            workspace_root=engine.permissions.workspace_root,
        )
        # Observability spans (observability.py): the engine only writes; reads and
        # exports go through the manager/REST.
        if getattr(engine, "traces", None) is None:
            from ..observability import SessionTracer

            engine.traces = SessionTracer(self.trace_store, session_id)
        # Turn ledger (trajectory.py): same attach-post-construction pattern. The
        # recorder is metadata-only and best-effort; `trajectory_enabled=false`
        # (user-global config) leaves the engine without one.
        if getattr(engine, "trajectory", None) is None:
            from ..config import load_config

            if load_config().trajectory_enabled:
                from ..trajectory import SessionTrajectory

                engine.trajectory = SessionTrajectory(
                    self._trajectory_store(), session_id
                )

    def _trajectory_store(self):
        """Lazy TrajectoryStore bound to <data-dir>/trajectory (config + cap read once)."""
        if self._trajectory_store_obj is None:
            from ..config import load_config
            from ..trajectory import TrajectoryStore

            cfg = load_config()
            self._trajectory_store_obj = TrajectoryStore(
                self.data_dir / "trajectory",
                max_records=int(cfg.trajectory_max_records),
            )
        return self._trajectory_store_obj

    def _event_context(self) -> dict[str, Any]:
        """Readers standing-goal triggers evaluate against — local stores only
        (a standing goal must never make a network call to decide whether to fire)."""

        def board_items(workspace: Optional[str]) -> list[dict[str, Any]]:
            if not workspace:
                return []
            try:
                return self.team_store.list_items(workspace, self._user_actor())
            except Exception:
                return []

        def inbox_pending(kind: Optional[str] = None) -> list[dict[str, Any]]:
            try:
                return [
                    {"kind": item.kind, "session_id": item.session_id}
                    for item in self.inbox.pending(None)
                    if kind is None or item.kind == kind
                ]
            except Exception:
                return []

        return {"board_items": board_items, "inbox_pending": inbox_pending}

    def _rag_config(self) -> Optional[dict[str, Any]]:
        from ..config import load_config

        cfg = load_config()
        if not (cfg.embedding_base_url and cfg.embedding_model):
            return None
        return {
            "embedding_base_url": cfg.embedding_base_url,
            "embedding_api_key": cfg.embedding_api_key,
            "embedding_model": cfg.embedding_model,
        }

    # -- per-session live view --------------------------------------------------
    def register_event_client(self, send_cb: Any) -> None:
        self._event_clients.add(send_cb)

    def unregister_event_client(self, send_cb: Any) -> None:
        self._event_clients.discard(send_cb)

    async def broadcast_event(self, message: dict) -> None:
        """Fan an app-wide event out to every /ws/events socket. Best-effort: a dead
        socket is dropped, never fatal to the caller."""
        for cb in list(self._event_clients):
            try:
                await cb(message)
            except Exception:
                self.unregister_event_client(cb)

    def register_session_client(self, session_id: str, send_cb: Any) -> None:
        self._session_clients.setdefault(session_id, set()).add(send_cb)

    def unregister_session_client(self, session_id: str, send_cb: Any) -> None:
        clients = self._session_clients.get(session_id)
        if clients is not None:
            clients.discard(send_cb)
            if not clients:
                self._session_clients.pop(session_id, None)

    async def broadcast_session(self, session_id: str, message: dict) -> None:
        """Fan a turn event out to every socket viewing this session. Best-effort: a dead socket
        is dropped, never fatal to the turn (delivery is socket-independent)."""
        for cb in list(self._session_clients.get(session_id, ())):
            try:
                await cb(message)
            except Exception:
                self.unregister_session_client(session_id, cb)

    async def aclose(self) -> None:
        await self.scheduler.stop()
        await self.stop_gateway()
        await self.mcp.aclose()
        self.audit_store.close()

    def save(self, session_id: str, engine: TurnEngine, touch: bool = True) -> None:
        executor = getattr(engine, "executor", None)
        workspace = os.path.realpath(str(executor.cwd)) if executor else ""
        self.session_store.save(
            SessionRecord(
                session_id=session_id,
                workspace=workspace,
                model=engine.model,
                mode=engine.permissions.mode.value,
                messages=engine.messages,
                title=title_from(engine.messages),
                agent=getattr(engine, "agent_name", "code"),
                extra_roots=self._extra_roots_of(engine, session_id),
                grants=_grants_of(engine),
                compaction=(
                    engine.compaction_state.as_dict()
                    if getattr(engine, "compaction_state", None)
                    else {}
                ),
            ),
            touch=touch,
        )

    @staticmethod
    def _apply_grants(engine: TurnEngine, grants: dict[str, Any]) -> None:
        """Re-apply a reloaded session's persisted "Always allow" approvals — they're
        session-scoped, and the session outlives the process (owner-hit 2026-07-22)."""
        for tool in grants.get("tools") or []:
            engine.permissions.allow_tool_for_session(str(tool))
        for command in grants.get("commands") or []:
            engine.permissions.allow_command_for_session(str(command))
        if grants.get("readonly"):
            engine.permissions.allow_readonly_for_session()

    def list_agents(self) -> list[dict[str, Any]]:
        return _list_agents()
