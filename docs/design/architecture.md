# Architecture

> Source of truth: the code under `coworker/` and `surfaces/gui/`. This doc is
> a map, not a substitute.

## The three moving parts

OpenWorker is a local-first desktop app. At runtime it is one native process
plus one Python child (and, on voice input, a mic session inside the native
process):

```
┌──────────────────────────────────────────────────────────────┐
│ Tauri desktop shell (surfaces/gui/src-tauri)                 │
│  • native window + system tray (close = hide; server lives)  │
│  • spawns & supervises the Python sidecar on a free port     │
│  • injects HTTP/WS base URLs + launch token into the webview │
│  • native commands: folder picker, autostart, keep-awake,    │
│    dictation bridge (ocw-stt), updater                       │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ React SPA (surfaces/gui/src)                           │  │
│  │  App.tsx ~2.4k lines; api.ts ~2.5k lines               │  │
│  │  REST for state, /ws/session/{id} for the live turn    │  │
│  └────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│ Python agent server — "the sidecar" (coworker/server)        │
│  • FastAPI + uvicorn, binds 127.0.0.1 only                   │
│  • SessionManager owns engines, sessions, persistence,       │
│    the automation scheduler, connector gateway, and the      │
│    cross-session Inbox                                      │
│  • TurnEngine (coworker/engine.py) runs agent turns          │
└──────────────────────────────────────────────────────────────┘
        │ files/terminal        │ 25+ connectors      │ any LLM provider
        ▼                       ▼                     ▼
   your machine            Slack, GitHub, …      OpenAI, Anthropic, GLM,
   (roots, shell,          via tokens you own    Gemini, Ollama, … with
   search)                 or brokered OAuth     your keys
```

The same server binary runs in two modes:

- **Desktop mode** — Tauri passes an in-memory launch token via environment;
  nothing secret is ever written to disk.
- **Standalone mode** — `openworker-server` writes a user-readable-only token
  file `<state-dir>/sidecar-<port>.token` (deleted at shutdown) so the Vite
  dev UI or curl can authenticate with the `X-OpenWorker-Token` header.

The browser UI and the desktop shell share one codebase: the SPA resolves its
backend via runtime globals injected by Tauri → Vite env vars → `127.0.0.1:8765`.

## Module map (`coworker/`)

**The turn core**

| Module | Role |
|---|---|
| `engine.py` | `TurnEngine` — the owned agent loop (see [agent-engine.md](agent-engine.md)) |
| `agent.py` | `build_engine()`: assembles Agent + tools + permissions + AGENTS.md + memory + skills + connectors + MCP into a `TurnEngine` |
| `agents/` | Declarative `Agent` dataclasses and built-in agents (`code`, `chat`, `cowork`, `myhelper`); `registry.py` resolves persona ids |
| `permissions.py` | `PermissionEngine`: modes, rules, grants, hard floors (see [permissions-and-approvals.md](permissions-and-approvals.md)) |
| `risk.py` | `RiskClass` classification of every tool call |
| `reviewer.py` | LLM reviewer behind Auto-Approve mode |
| `events.py` | Engine event enum (`assistant_delta`, `tool_proposed`, `permission_required`, …) streamed to the GUI |

**Context & history**

| Module | Role |
|---|---|
| `compaction.py` | Auto-compaction of long histories (OPE-27) |
| `sessions.py`, `conversations.py` | Persisted sessions and conversation threads |
| `memory/` | Long-lived memory store + injection (see [memory.md](memory.md)) |
| `session_facts.py` | Per-session facts block |

**Surfaces & coordination**

| Module | Role |
|---|---|
| `server/` | FastAPI app, `SessionManager`, launcher (see [server-and-auth.md](server-and-auth.md)) |
| `inbox.py`, `inbox_routing.py`, `unrouted.py` | The cross-session Inbox: parked approvals/questions and their routing |
| `unattended.py` | Per-session "unattended" flag that reroutes prompts to the Inbox |
| `mentions.py` | Slack/GitHub @-mention routing (§31 mention router) |
| `subscriptions.py` | Which sessions listen to which channels |
| `automation/` | Scheduler + scheduled tasks (see [automation.md](automation.md)) |
| `teams/` | Multi-agent board/journal/chat substrate (see [teams.md](teams.md)) |
| `selfwake.py` | Self-wake support so a session can resume on its own timer |

**Environment & trust**

| Module | Role |
|---|---|
| `roots.py`, `workspace_trust.py`, `projects.py`, `project.py` | Writable roots, workspace trust decisions, project registry |
| `provenance.py` | Tracks files the agent downloaded this session (feeds the OPE-114 floor) |
| `toolchain.py` | PATH/tool resolution with login-shell env (OPE-82/83) |
| `secrets.py` | `SecretStore` — profile-scoped credential storage |
| `audit.py` | Audit log of consequential actions |
| `readonly.py` | Classifier for read-only shell commands (`READONLY_SESSION` grants) |
| `config.py` | TOML config (`config.example.toml` in this directory), env overrides |
| `cloud.py` | OpenWorker-Cloud client (OAuth brokering, relay registration) |
| `overrides.py`, `connections.py`, `interactions.py`, `attachments.py`, `environment.py` | Settings overrides, connection state, interactive prompt plumbing, message attachments, environment/system-info block |

**Capability planes**

| Module | Role |
|---|---|
| `providers/` | LLM providers (see [model-providers.md](model-providers.md)) |
| `tools/` | Built-in tools + registry (see [tools.md](tools.md)) |
| `connectors/` | Third-party platform adapters (see [connectors.md](connectors.md)) |
| `mcp/` | MCP client (see [mcp-client.md](mcp-client.md)) |
| `personas/`, `skills/` | Persona manifests & skills (see [personas-and-skills.md](personas-and-skills.md)) |
| `catalog.py` | Declarative capability catalog personas compose from |
| `web/` | `web_search` / `web_fetch` plumbing and the egress guard |
| `tui/`, `cli.py` | Terminal UI and `openworker` CLI entry points |
| `testing/` | Test harnesses (e.g. the in-process FakeSlack server) |

## State directory inventory

Everything the server keeps lives under a per-user state dir (the desktop app
maps it into its app-data area). The manager (`coworker/server/manager.py`)
owns these files:

| File / dir | Written by | Contents |
|---|---|---|
| `coworker.db` | `memory/sqlite_store.py` | Long-lived memories (`memories` table) |
| `memory-settings.json` | `memory/settings.py` | Memory on/off switch + user rules text |
| `automation.db` | `automation/store.py` | `ScheduledTask` / `TaskRun` rows, indexed on `next_run` |
| `teams.db` / `journal.db` / `chat.db` | `teams/` | Board event log, journal, team chat (each hash-chained) |
| `teams.json` | `teams/registry.py` | Registered teams (lead persona, workers) |
| `board-tokens.json` | `teams/tokens.py` | `owb_` join tokens (sha256-hashed) |
| `attachments/` | `teams/attachments.py` | Content-addressed blobs behind `attachment://` refs |
| `inbox.json` | `inbox.py` | The human-attention queue (approvals, questions, …) |
| sidecar token file | `server/run.py` | Standalone-mode launch token, user-only, deleted on shutdown |

Plus two config locations that are *read*, not owned: the global
`~/.config/coworker/` tree (provider keys via env, `mcp.json`) and per-workspace
`.coworker/` dirs (workspace-scoped MCP servers, AGENTS.md lives at workspace
root).

## Design invariants

These cut across every subsystem and show up repeatedly in code comments:

1. **Local-first.** The loop, transcripts, tokens, and keys never leave the
   machine except through the model provider and connectors the user chose. The
   only cloud piece is OAuth brokering / relay for connectors (`cloud.py`).
2. **The engine is provider-agnostic.** One canonical OpenAI-shaped message
   format everywhere; provider-specific state rides private sidecars and is
   stripped before any request (see [model-providers.md](model-providers.md)).
   This is what makes mid-session model switching safe.
3. **Fail closed.** Empty allowlists admit nobody; repo-level config can only
   *narrow* permissions, never widen them (`_GLOBAL_ONLY_FIELDS` in
   `config.py`); unscopable operations degrade to human-only.
4. **Nothing interactive is lost.** Every prompt the agent needs answered is
   parked as a persisted Inbox item before anyone is asked, so dropped
   sockets and restarts can't orphan a turn (see [server-and-auth.md](server-and-auth.md)).
5. **The registry never judges.** `ToolRegistry` executes tools; permission
   decisions happen in the engine, before execution, on every call.
