# Token budget guard

> Source: `coworker/budget.py` (BudgetStore + BudgetGuard), wired in
> `coworker/engine.py::_loop` (check at the top of each iteration, record after
> each model call), `manager.py` (attach + automation fields),
> `automation/tools.py` (agent-facing schema).

Token ceilings that stop a turn gracefully instead of letting costs run away.
Three independent limits, all **explicit opt-in** (None = unlimited):

| Ceiling | Scope | Set via |
|---|---|---|
| **daily** | all sessions, per calendar day | `config.daily_token_budget` (user-global) |
| **session** | one conversation, lifetime | `config.session_token_budget` (user-global) |
| **task** | one automation, lifetime | `task.budget_tokens` (set at create/update) |

## Enforcement

The check lives at the **top of the loop** (same pattern as `max_iterations`),
so the previous turn's output was already yielded to the user and the next
model call is the thing being refused. The stop is a transcript notice +
`TURN_END {status: "budget_exhausted"}` — the conversation is persisted and
continuable once the budget is raised or cleared.

Counting basis: every token the provider processed (`input + output +
cache_read + cache_write`), recorded **after** each model round-trip. Turns
whose provider reported no usage don't count (same honesty rule as the
frontend's usage chip — never guessed).

## Never breaks the turn

All guard calls are wrapped: a broken or corrupt budget store degrades to
"unlimited" rather than becoming a worse failure mode than no budget. This is
enforced by `try/except` on every `record`/`check` path (tested).

## Surfaces

- Agent: `create_scheduled_task(budget_tokens=…)` / `update_scheduled_task(budget_tokens=…)`.
- GUI: `create_automation`/`update_automation` accept `budget_tokens`; the
  public shape carries it so the detail page can show the ceiling.
- Manager: `_run_scheduled_task` attaches a `BudgetGuard` to the task engine
  when `budget_tokens` is set (with the daily ceiling from config).
- Storage: `budget.json` under the data dir (thread-safe JSON accumulate).
- Tests: `tests/test_budget.py` — store accumulation, per-ceiling checks,
  corrupt-store degradation, engine stop (provider never called after the
  guard trips).
