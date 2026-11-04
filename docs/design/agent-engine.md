# The agent engine

> Source: `coworker/engine.py` (`TurnEngine`), assembled by
> `coworker/agent.py::build_engine()`. Roughly 2,000 lines, heavily commented.

## One turn, many iterations

A **turn** is one user message plus everything the agent does about it: a
while-loop of model calls and tool calls that ends when the model stops
calling tools, a rail trips, or the user interrupts. The iteration cap comes
from config (`max_iterations`, default **150** in `coworker/config.py:34`;
the engine constructor's own default of 12 is only for bare tests).

```
run() / retry() / resume()
   └─ _loop():
        1. auto-compaction checkpoint        (OPE-27)
        2. stream the model                  (_astream)
        3. append assistant message          (+ _display sidecar)
        4. _handle_tool_calls()
             a. authorize every call         (sequential — see below)
             b. execute                       (parallel-safe ∥ sequential)
        5. repeat until: no tool calls | cap | rail | interrupt
```

Entry points (`engine.py`):

- `run()` — normal entry for a user message.
- `retry()` — re-run a failed turn.
- `resume()` — durable resume after a crash/restart. Trailing unanswered
  tool-calls are re-parked: prompt callbacks find the already-persisted Inbox
  item instead of creating a duplicate, so nothing executes twice.

## Streaming

Providers expose a blocking stream; the engine bridges it to async with a
thread + queue (`_astream`). Deltas go out as engine events (`coworker/events.py`):
`assistant_delta` for visible text, `reasoning_delta` for model thinking
(display-only, never replayed to a provider), later `tool_proposed`,
`permission_required`, `tool_started`, `tool_finished`. Stop is a race between
the stream queue and a cancel-event; interrupts also fire `interrupt_hooks`,
which is how a running shell child gets killed.

## Tool-call orchestration

`_handle_tool_calls()` separates *authorization* from *execution*:

1. **Authorize first, all of them, sequentially** (spec §8.6 — one action per
   request, sent as one card per action). Interactive approval cards would be
   meaningless if executed concurrently.
2. **Then execute**: calls classified parallel-safe (reads, searches) run
   concurrently via `asyncio.gather` inside `asyncio.to_thread`; writes and
   shell commands run strictly in arrival order.

A fixed set of **gate tools** never touch the tool registry at all — they are
the engine's interactive vocabulary, each handled by a dedicated `_handle_*`
generator: `ask_user`, `propose_plan`, `request_directory`, `request_tool`,
`propose_team`, `propose_work_items`.

### No orphan tool calls

Every tool call — approved, denied, or interrupted — eventually gets a tool
result message. The protocol requires every `tool_call_id` to be answered;
denials return a terse refusal (the *user* sees the full reason) so the model
can react instead of erroring.

## Approvals and grants

Authorization outcomes (`ApprovalOutcome`, `engine.py:42`): `once`,
`always_tool`, `always_command`, `always_domain`, `readonly_session`, `deny`.
Everything except `once` mints a session grant so the next matching call
doesn't ask again. `readonly_session` is special: it comes from the read-only
command classifier (`coworker/readonly.py`) and pre-approves classifier-passing
shell reads for the whole session.

The full decision pipeline — permission modes, risk classes, hard floors, the
Auto-Approve reviewer — is its own doc:
[permissions-and-approvals.md](permissions-and-approvals.md).

## Auto-compaction (OPE-27)

Between tool turns and before a new model call, the engine takes a compaction
checkpoint. When history grows past the threshold, everything before a boundary
is replaced by a compaction summary; the boundary itself is recorded in session
state (`sessions.py`, `conversations.py` — both parse it defensively so an
older/corrupt row degrades to "never compacted" instead of crashing). For
unattended runs, a failed compaction auto-trims rather than stalls the run.

## Steering and interruption

- `queue_steering()` — the user can send additional mid-turn messages; they are
  injected between loop iterations as steering context, not queued as new turns.
- Interrupt — cancels the current iteration, kills shell children via
  `interrupt_hooks`, and still closes out any open tool calls (see above).

## What the model sees

The engine owns the canonical message list (OpenAI shape). Provider-private
state rides sidecar keys — `_anthropic` (thinking blocks), `_gemini` (thought
signatures), `_display` (GUI-only data) — and `_outbound_messages()` strips
them before any request. Consequences:

- Mid-session model switching is safe; see [model-providers.md](model-providers.md).
- `_display` survives reloads but is *never* provider-visible (owner ruling
  2026-08-24).
- The engine also tracks **what the agent itself created or downloaded this
  session** (OPE-114 §1) — that provenance set is what the fetch-then-execute
  floor in the permissions doc feeds on.

## Context assembly (`agent.py::build_engine`)

Before the loop can run, `build_engine()` wires an `Agent`
(`coworker/agents/base.py` — a declarative dataclass of traits:
`requires_folder`, `subagents`, `scheduling`, `messaging`, `connectors`,
`team`) together with:

- tools from the capability catalog (`coworker/catalog.py`), expanded from the
  agent/persona declaration;
- the permission engine + live settings getters (engines hold **late-bound
  callables**, not baked snapshots, so a settings change applies next call);
- AGENTS.md from the workspace root;
- the memory block (see [memory.md](memory.md));
- the live skill menu (SKILLS-SPEC §4.1 — recomputed every turn, like the
  roots list and the live clock injected per-turn, owner ruling 2026-08-20);
- connectors (least-privilege: a persona with an allowlist gets only those
  connectors, OPE-93) and MCP servers (see [mcp-client.md](mcp-client.md)).

What the agent *knows* is fixed at session start (MEMORY-SPEC §7.1); the menu
of skills and the clock are the deliberate exceptions, refreshed live.
