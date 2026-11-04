# Standing goals (event-triggered automations)

> Source: `coworker/automation/triggers.py` (EventTrigger + evaluator),
> `scheduler.py` (`_event_due` edge/cooldown loop), `store.py`
> (`event_tasks`, `compute_next_run`), `manager.py` (`_event_context` readers,
> event opening message), `automation/tools.py` (agent-facing schema).

A ScheduledTask can carry an optional **trigger** — a declarative event
condition that fires the task outside the cron lane. From "timed work" to
"guarding a goal": *"when a board item goes blocked", "when build.log contains
ERROR", "when something parks in my inbox"* → the task's normal run machinery
executes (same runner, same run history, same parked-approval flow).

## Conditions (v1 — all locally observable)

| Kind | Params | Fires when |
|---|---|---|
| `file` | `path` (relative), `mode: exists\|changed\|contains`, `value` | the file exists / changed since last observation / contains the substring |
| `board_state` | `state` (open…canceled), `min_minutes?` | the workspace's board has an item in that state (optionally aged past `min_minutes`) |
| `inbox` | `kind?` (approval/question/notification) | any pending Inbox item matches |

Condition readers are injected (`Scheduler(event_context=…)`) — the scheduler
never touches the teams/inbox stores directly, and evaluation is a pure
function over injected state (`tests` mock it; the manager binds it to
`team_store.list_items` + `inbox.pending`). A standing goal never makes a
network call to decide whether to fire.

## Semantics

- **Edge-triggered + cooldown**: fires on unmet→met transition; while a
  condition stays met it re-fires at most once per `cooldown_seconds`
  (default 1h, clamped to [5m, 24h]). Edge/cooldown/baseline state persists on
  the task record (`trigger_state`) — restarts don't re-fire.
- **`changed` baselines first**: the first evaluation records the observed
  mtime instead of firing — "changed" means a change WE observed, not "the
  file predates the goal".
- **Mutual exclusion**: `trigger` and cron/`fire_at` are mutually exclusive at
  creation (tool + manager); cron edits on an event task are refused
  (tool + `update_automation`) — a cron can't silently convert a goal into a
  timed task.
- **Facts-only detail**: the trigger detail reaching the prompt is
  path/state/count facts, never file contents — contents are attacker-
  controllable data; the agent wakes and reads them through the normal
  permission system instead.
- Path safety: file paths resolve inside the task workspace at parse time AND
  at evaluation time (a tampered stored trigger still can't escape).

## Surfaces

- **Agent**: `create_scheduled_task(trigger={kind, params, cooldown_seconds})` —
  the conversational idiom ("watch build.log and fix it when it breaks").
- **Storage**: trigger + trigger_state ride the task's JSON blob (no schema
  migration); `compute_next_run` → None for event tasks, so `due()` excludes
  them; `store.event_tasks()` is the scheduler's lane.
- **Scheduler**: `_event_due()` runs inside every 30s tick, after the cron lane,
  with the overlap guard shared (`_running_ids`).
- **API/UI**: `public().schedule` carries the event label ("event: file
  build.log changed") via `schedule_label()`, so ScheduledView displays it with
  no changes; the detail page hides cron editing for event tasks. Runs record
  `trigger: "event"`, and the run-start toast/detail carries the condition
  detail; the run's opening message names the firing fact.
- Tests: `tests/test_automation_triggers.py` (parse/paths/cooldown clamps,
  per-kind evaluation incl. baseline-first + min_minutes + kind filters,
  store exclusion, scheduler edge/cooldown/clear-return loop, overlap skip).
