# Automation (scheduled tasks) & the Inbox

> Source: `coworker/automation/` (`models.py`, `store.py`, `scheduler.py`,
> `tools.py`), `coworker/unattended.py`, `coworker/inbox.py`,
> `coworker/selfwake.py`.

Automation = recurring work ("morning brief", "weekly report", "watch a
channel") without a human at the desk. The hard problem is that the agent may
hit an approval mid-run with nobody there — that's what the Inbox solves.

## Model

`models.py`:

- **`ScheduledTask`** — title, instructions, `Schedule` (kind `cron` | `once`,
  with an IANA timezone), workspace, agent/persona, `always_allowed_tools`
  (task-scoped standing rules: "tool → exact target" entries, not blanket
  tool grants), optional `notify_target`. Each task owns a conversation id
  `__task__<id>`.
- **`TaskRun`** — one fire: session id `__run__<run_id>`, status
  running/ok/error/skipped, trigger schedule/manual/catchup.

`store.py::TaskStore` persists to SQLite (`automation.db`), rows indexed on
`next_run`/`enabled`. Next-fire math uses croniter and is DST-aware (naive
local base or `ZoneInfo`; `tzdata` is a hard Windows dependency for exactly
this reason — see `pyproject.toml`).

## Scheduler

`scheduler.py::Scheduler` ticks every 30 s inside the always-on server (the
desktop app's tray residency is what keeps it alive):

- **Run-once catch-up** on startup — a missed fire while the app was closed
  runs once, not N times.
- **Skip-on-overlap** — `_running_ids`: if the previous run of a task is
  still going, the new fire is marked skipped.
- Runs are spawned as **independent tasks**, so a run parked on an approval
  can never stall the scheduler loop itself.
- `extra_tick` resumes self-woken sessions (`selfwake.py` — a session that
  asked to be re-checked later).

Each fire (`manager.py::_run_scheduled_task`) is a fresh, persisted,
continuable conversation thread: engine built per run, full transcript saved,
toast broadcast, optional completion message to `notify_target` via the
connector senders.

## Unattended runs & the Inbox

`unattended.py::UnattendedRegistry` flags a session as unattended (scheduled
runs, and any session the user leaves). The flag changes one thing: every
interactive prompt is routed to the **cross-session Inbox** instead of the
GUI.

`inbox.py::InboxStore` (`inbox.json`) is the human-attention queue. Item
kinds: `approval`, `question`, `notification`, `directory` (folder grant),
`plan`, `tool` (missing CLI tool). Lifecycle: `pending → resolved`, **first
responder wins** — an item can be answered from:

1. the live GUI WebSocket,
2. REST `POST /v1/inbox/{id}/resolve`,
3. a bound channel — a Slack reply containing `[ow:<id>]`, or an
   Approve/Deny interaction button resolved by the gateway's
   `reply_resolver` ([connectors.md](connectors.md)).

While an item is pending, `inbox_approver` suspends the agent run (no busy
polling, no timeout-forced default answer). This is the same parking
mechanism that makes GUI sessions survive dropped sockets
([server-and-auth.md](server-and-auth.md)) — unattended is just the case
where the Inbox is the *only* answer channel.

## Creating tasks

Two doors: the GUI's Scheduled view, and the agent itself via
`automation/tools.py`. The latter is deliberately expensive: scheduled-task
CRUD is a `PERSISTENT_AUTHORITY_TOOLS` member
([permissions-and-approvals.md](permissions-and-approvals.md)) — always
human-approved, because a task with standing grants executes with authority
that outlives the conversation that created it.
