# Teams

> Source: `coworker/teams/` — `store.py`, `model.py`, `journal.py`,
> `chat.py`, `registry.py`, `tools.py`, `dialect.py`, `tokens.py`,
> `attachments.py`, `cli.py`, `mcp_server.py`.

Teams is the multi-agent orchestration substrate: a **lead** persona
decomposes work into board items, **workers** (each its own agent session)
claim and complete them, and the whole record is tamper-evident. The design
goal (OPE-100) was to expose the board as an *open surface* any harness —
including external coding agents — can join.

## The board is an event log

`store.py::TeamStore` (`teams.db`) keeps an **append-only, hash-chained event
log per space** (the workspace path is the space key). Board items are a
*projection*: `_apply`/`rebuild` fold events into the item list **in the same
transaction** as the append, so log and view can't diverge. Each space's
chain is verifiable (`verify_chain`) and carries a `taint` provenance flag.

Why event-sourced: agent-driven work benefits from an audit trail that can't
be quietly rewritten — a worker marking its own item done leaves the
assignment event right next to it, hash-locked.

## Model (`model.py`)

- `ItemState` machine: `open → in_progress → blocked | review → done |
  canceled`.
- `WORKER_TARGETS` — workers never mark items done; completion is the
  lead's/user's call.
- `Role` (`user` / `lead` / `worker` / `system`) and `Actor`.

## Journal & chat

- `journal.py::JournalStore` (`journal.db`) — case-keyed knowledge that
  **outlives boards**: kinds `finding` / `evidence` / `decision` / `note` /
  `raw`, body capped at 16k, its own hash chain. A grant table governs
  sharing ("sharing rides assignment").
- `chat.py::ChatStore` (`chat.db`) — one group chat per team; agent posts
  wake only @mentioned members (no idle-agent chatter).
- `attachments.py` — content-addressed blobs; the log carries
  `attachment://` refs, keeping the event log itself small.

## Orchestration

`registry.py::TeamRegistry` (`teams.json`) holds the team: lead persona
(builtin `swe-lead`, `devops-lead`, `devsecops-lead`, `triage-lead`) plus
workers. Flow: the lead decomposes work (`propose_work_items`), staffs
(`propose_team` — worker sessions are **pre-spawned but idle: zero tokens
until first assignment**), assigns board items. Wake plumbing then keeps
workers moving: the scheduler's `extra_tick`
([automation.md](automation.md)), a rolling per-hour wake budget, staleness
digests, and a check-in backstop in the manager — assigned-but-stale workers
get nudged rather than forgotten.

`tools.py` exposes board/journal verbs with **role-filtered sets**
(`LEAD_VERBS` include assign/link; `WORKER_VERBS` include claim), and the
store re-checks authority under the verbs — the tool layer is convenience,
the store is enforcement.

## The open surface

Three ways to touch the same board:

1. **`ocw` CLI** (`cli.py`; `ocw board …` / `ocw journal …`) — resolves
   remote → local-server → direct-SQLite backing automatically.
2. **`ocw board mcp`** (`mcp_server.py`) — serves the same surface as a
   stdio MCP server, so external coding agents (any MCP client) can join a
   team and claim items.
3. **REST `/v1/board`** — with **per-actor Bearer tokens**
   (`tokens.py::BoardTokens`, `owb_` join tokens stored sha256-hashed,
   binding actor + role).

`dialect.py` abstracts the backing: `LocalDialect` (direct SQLite) vs
`RemoteDialect` (HTTP over `/v1/board`) behind one `BoardDialect` interface.
Jira/Linear integrations are deliberately **mirrors, not dialects** — they
sync board state outward without ever becoming the system of record.
