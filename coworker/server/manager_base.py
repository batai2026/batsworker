"""Contract of the SessionManager facade: the shared state and the cross-mixin
methods every mixin may rely on.

manager.py composes 15 mixins into one SessionManager. Each mixin reaches for attributes
and methods that live on *other* mixins or on the facade itself, so on its own a mixin
looks like it is using names that do not exist -- which is exactly what a type checker
reports, several hundred times over.

This class states that contract once. Nothing here is implemented: every method body
raises NotImplementedError, and each one is overridden by a real mixin (or by
SessionManager itself) at runtime. tests/test_manager_contract.py asserts that, so the
contract cannot silently drift from the mixins. If a mixin changes its shared state, this
file has to move with it -- that is the point.

Why `raise NotImplementedError` rather than `...`: a bare `...` body is an *empty* body,
which mypy reports as [empty-body] on every non-None-returning method here (24 errors).
Raising is a real body, so it type-checks clean, and it also fails loudly if a method is
ever declared here but not actually wired up -- instead of quietly returning None.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    # Type-only imports: at runtime the mixins import each other's modules, so pulling
    # these in for real would create import cycles. With `from __future__ import
    # annotations` the annotations are strings, so mypy resolves them here and Python
    # never evaluates them.
    from ..agent import TurnEngine
    from ..engine import Approver
    from ..audit import AuditStore
    from ..automation import Scheduler, TaskStore
    from ..budget import BudgetStore
    from ..connections import PersonaConnectionStore, SessionConnectionStore
    from ..connectors import Gateway
    from ..connectors.parked import ParkedStore
    from ..conversations import ConversationStore
    from ..inbox import InboxStore
    from ..inbox_routing import InboxRouting
    from ..mcp import MCPManager
    from ..mentions import MentionSessionStore
    from ..memory import MemorySettingsStore, MemoryStore
    from ..observability import TraceStore
    from ..permissions import Mode
    from ..personas import PersonaRegistry
    from ..providers import ProviderClient
    from ..secrets import SecretStore
    from ..selfwake import WakeStore
    from ..skills import SessionSkillStore, SkillStore
    from ..subscriptions import ChannelBuffer, SubscriptionStore
    from ..teams import JournalStore, TeamStore
    from ..teams.attachments import AttachmentStore
    from ..teams.chat import ChatStore
    from ..teams.registry import TeamRegistry
    from ..teams.tokens import BoardTokens
    from ..unattended import UnattendedRegistry
    from ..unrouted import UnroutedStore
    from ..workspace_trust import WorkspaceTrustStore
    from ..teams import Actor as TeamActor


class ManagerBase:
    """Declaration-only contract shared by all SessionManager mixins."""

    # -- shared state, assigned in SessionManager.__init__ ---------------------------
    DEFAULT_SCRATCH_BASE: str
    _SESSION_ID_RE: re.Pattern[str]
    _autotitle_attempts: dict[str, int]
    _autotitle_inflight: set[str]
    _autotitle_sig: dict[str, int]
    _autotitle_tasks: set[asyncio.Task]
    _codex_authorizing: bool
    _codex_error: Optional[str]
    _data_base: Path
    _event_clients: set[Any]
    _engines: dict[str, TurnEngine]
    _loop: Optional[asyncio.AbstractEventLoop]
    _mcp_auth_hints: set[str]
    _mcp_authorizing: set[str]
    _mcp_errors: dict[str, str]
    _mcp_session_failures: dict[str, list[str]]
    _people: dict[str, str]
    _people_path: Path
    _prefs: dict[str, Any]
    _promotion_rebuild: set[str]
    _replay_tasks: set[asyncio.Task]
    _session_clients: dict[str, set[Any]]
    _running_sessions: set[str]
    _team_inflight: set[str]
    _team_last_alive: dict[str, float]
    _trajectory_store_obj: Any
    attachment_store: AttachmentStore
    audit_store: AuditStore
    board_tokens: BoardTokens
    budget_store: BudgetStore
    channel_buffer: ChannelBuffer
    chat_store: ChatStore
    data_dir: Path
    default_workspace: Optional[str]
    gateway: Optional[Gateway]
    inbox: InboxStore
    inbox_routing: InboxRouting
    journal_store: JournalStore
    mcp: MCPManager
    memory_settings: MemorySettingsStore
    memory_store: MemoryStore
    mention_sessions: MentionSessionStore
    mode: Mode
    model: str
    parked: ParkedStore
    persona_connections: PersonaConnectionStore
    personas: PersonaRegistry
    provider: Optional[ProviderClient]
    scheduler: Scheduler
    secrets: SecretStore
    session_connections: SessionConnectionStore
    session_skills: SessionSkillStore
    session_store: ConversationStore
    skill_store: SkillStore
    subscriptions: SubscriptionStore
    task_store: TaskStore
    team_store: TeamStore
    teams: TeamRegistry
    trace_store: TraceStore
    unattended: UnattendedRegistry
    unrouted: UnroutedStore
    wakes: WakeStore
    workspace_trust: WorkspaceTrustStore

    # -- methods owned by another mixin but called across the boundary ---------------
    def _build_task_engine(self, task, *, session_id: str) -> TurnEngine:
        raise NotImplementedError

    # -- inbox <-> approvals <-> workspaces (mypy: cross-mixin, previously undeclared) --
    def add_root(self, session_id: str, path: str, writable: bool = False) -> dict[str, Any]:
        raise NotImplementedError

    def approval_outcome(self, resolution: str, request, session_id: str) -> Any:
        raise NotImplementedError

    def approval_prompt_data(self, session_id: str, request) -> dict[str, Any]:
        raise NotImplementedError

    async def mirror_inbox_item(self, item) -> None:
        raise NotImplementedError

    def promote_workspace(self, session_id: str, path: str) -> dict[str, Any]:
        raise NotImplementedError

    def _curated_models(self) -> list[str]:
        raise NotImplementedError

    def _emit_session_created(self, session_id: str, persona_id: str) -> None:
        raise NotImplementedError

    def _inbound_connector_allowed(self, session_id: str, connector: str) -> bool:
        raise NotImplementedError

    def _mcp_workspace_trusted(self, workspace: Optional[str | Path]) -> bool:
        raise NotImplementedError

    def _memory_key_for(self, record, ws: Optional[str]) -> Optional[str]:
        raise NotImplementedError

    def _memory_saved_notifier(self, session_id: str) -> Any:
        raise NotImplementedError

    def _model_provider(self, model: str) -> str:
        raise NotImplementedError

    def _ollama_alive(self) -> bool:
        raise NotImplementedError

    async def _on_interaction(self, event) -> None:
        raise NotImplementedError

    def _persona_of(self, session_id: str, persona_id: Optional[str] = None) -> str:
        raise NotImplementedError

    def _provider_configured(self, name: str) -> bool:
        raise NotImplementedError

    def _provision_scratch(self, session_id: str) -> str:
        raise NotImplementedError

    def _refresh_provider(self, name: Optional[str] = None) -> None:
        raise NotImplementedError

    def _resolve_inbox_reply(self, event) -> bool:
        raise NotImplementedError

    def _save_prefs(self) -> None:
        raise NotImplementedError

    def _scratch_workspace_error(self, workspace: Any) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    def _slack_actor_owns_item(
        self, item, *, actor_id: str, chat_id: str, team_id: Optional[str]
    ) -> bool:
        raise NotImplementedError

    def _trajectory_store(self) -> Any:
        raise NotImplementedError

    def _user_actor(self) -> TeamActor:
        raise NotImplementedError

    @staticmethod
    def _wake_message(wake) -> str:
        raise NotImplementedError

    async def broadcast_event(self, message: dict) -> None:
        raise NotImplementedError

    async def broadcast_session(self, session_id: str, message: dict) -> None:
        raise NotImplementedError

    async def deliver_to_session(
        self, session_id: str, message: str, *, source: Optional[dict[str, Any]] = None
    ) -> None:
        raise NotImplementedError

    def dm_session(self) -> Optional[str]:
        raise NotImplementedError

    def effective_connectors(self, session_id: str, persona_id: Optional[str] = None) -> set[str]:
        raise NotImplementedError

    def effective_skill_names(
        self,
        session_id: str,
        workspace: Optional[str | Path] = None,
        agent: Optional[str] = None,
    ) -> set[str]:
        raise NotImplementedError

    def engine_workspace(
        self, session_id: str, *, workspace: Optional[str] = None, agent: str = "code"
    ) -> Optional[str]:
        raise NotImplementedError

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
        raise NotImplementedError

    def get_settings(self) -> dict[str, Any]:
        raise NotImplementedError

    def is_running(self, session_id: str) -> bool:
        raise NotImplementedError

    def is_temp_workspace(self, path: Optional[str]) -> bool:
        raise NotImplementedError

    def list_connectors(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def mark_idle(self, session_id: str) -> None:
        raise NotImplementedError

    def mark_running(self, session_id: str) -> None:
        raise NotImplementedError

    def persist_session(self, session_id: str) -> None:
        raise NotImplementedError

    def persona_mcp_scope(self, persona_id: str) -> Optional[set[str]]:
        raise NotImplementedError

    def persona_skill_scope(self, persona_id: str) -> tuple[Optional[Path], Optional[set[str]]]:
        raise NotImplementedError

    def resolve_workspace(self, requested: Optional[str]) -> Optional[str]:
        raise NotImplementedError

    def save(self, session_id: str, engine: TurnEngine, touch: bool = True) -> None:
        raise NotImplementedError

    def scratch_base(self) -> Path:
        raise NotImplementedError

    def set_default_model(self, model: str) -> dict[str, Any]:
        raise NotImplementedError

    def slack_approval_owner_ids(self, team_id: Optional[str] = None) -> set[str]:
        raise NotImplementedError

    def team_staleness_digest(self, session_id: str) -> str:
        raise NotImplementedError

    async def team_tick(self) -> int:
        raise NotImplementedError

    def try_mark_running(self, session_id: str) -> bool:
        raise NotImplementedError
