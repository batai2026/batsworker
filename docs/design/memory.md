# Memory

> Source: `coworker/memory/` — `base.py`, `sqlite_store.py`, `settings.py`,
> `tools.py` — plus the injection plumbing in `coworker/agent.py` (MEMORY-SPEC
> references throughout). Behaviors here follow "MEMORY-SPEC", whose full text
> is not in this repo; the code comments quote its sections.

Memory is what the coworker *remembers between sessions* — distinct from the
conversation transcript and from compaction.

## Model

`base.py`: `MemoryStore` ABC and `MemoryItem` (id, scope `global` |
`workspace` | `session`, key, content, summary, workspace, session_id,
created_at). Backend: `sqlite_store.py::SQLiteMemoryStore` at `coworker.db`
(`memories` table).

The `session` scope is **dead** (MEMORY-SPEC §3): writes to it redirect to
workspace scope, and the tools refuse it outright (`memory/tools.py:77`).

## The on/off switch and user rules

`settings.py::MemorySettingsStore` (`memory-settings.json`) holds:

- **the switch** — memory saving on/off. When off, the write tools refuse
  live ("registered always, harmless" is not good enough for a hard switch).
- **`user_rules`** — free text the user types in Settings. Injected verbatim,
  and it **outranks learned memories** (MEMORY-SPEC §6). The agent can never
  write this field — it's user authority, not model knowledge.

## Injection

`render_memory_block` (wired in `agent.py`) builds the block appended to the
system context:

- **Full mode** when the rendered store is under 8,000 chars: everything,
  verbatim.
- **Index mode** above that: the 10 newest items in full, the rest as
  one-line summaries, plus a `memory_read` fetch-by-`[#id]` tool so the agent
  can pull an older item on demand (MEMORY-SPEC §7).

What the agent *knows* is fixed at session start (MEMORY-SPEC §7.1) — a
mid-session save shows the "I'll remember that" notice but doesn't mutate the
current turn's context.

## Tools

`memory/tools.py`: `remember` (save), `memory_update`, `memory_forget`,
`memory_read` (the index-mode fetcher). After `remember` persists, the
`on_saved` hook (MEMORY-SPEC §5.1, wired in `agent.py:207`) pushes a
UI notice — "I'll remember that … [Undo]" — so a save is always visible and
reversible.

## Anti-bluffing

The system prompt carries explicit when-to-remember rules (MEMORY-SPEC §4.2)
and anti-bluffing notices, added after an observed incident **2026-07-28**: a
model *faked* a memory save (claimed it remembered without calling the tool).
The notices and the always-visible save toast are the countermeasures —
memory changes must be tool-mediated and surfaced, never asserted in prose.
